from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import APP_VERSION, LOOPBACK_HOSTS, get_settings
from .database import init_db
from .routes import ai_assistant, attachments, browser_sessions, invoices, master, settlements, statements, system
from .services.browser_sessions import BrowserSessions
from .services.backup import apply_pending_restore
from .services.codex_app_server import get_codex_app_server
from .services.statement_delete_recovery import reconcile_statement_delete_pending_files
from .services.shutdown import get_shutdown_coordinator


@asynccontextmanager
async def lifespan(_app: FastAPI):
    apply_pending_restore()
    init_db()
    reconcile_statement_delete_pending_files()
    sessions = BrowserSessions(get_shutdown_coordinator())
    _app.state.browser_sessions = sessions
    try:
        yield
    finally:
        await sessions.close()
        get_codex_app_server().close()


settings = get_settings()


class _RestoreMutationGate:
    """Allow normal concurrent writes, but make restore exclusive with all of them."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._active_mutations = 0
        self._restore_active = False

    def try_enter_mutation(self) -> bool:
        with self._lock:
            if self._restore_active:
                return False
            self._active_mutations += 1
            return True

    def leave_mutation(self) -> None:
        with self._lock:
            self._active_mutations -= 1
            if self._active_mutations < 0:
                raise RuntimeError("财务写入门闩计数无效")

    def try_enter_restore(self) -> bool:
        with self._lock:
            if self._restore_active or self._active_mutations:
                return False
            self._restore_active = True
            return True

    def leave_restore(self) -> None:
        with self._lock:
            if not self._restore_active:
                raise RuntimeError("恢复门闩状态无效")
            self._restore_active = False


_restore_mutation_gate = _RestoreMutationGate()
app = FastAPI(
    title=settings.app_name,
    version=APP_VERSION,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["*"] ,
    allow_headers=["*"] ,
)
trusted_hosts = [*sorted(LOOPBACK_HOSTS), "[::1]"]
if settings.testing:
    trusted_hosts.append("testserver")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)


@app.middleware("http")
async def require_financial_write_marker(request: Request, call_next):
    """Reject blind cross-site writes to every API mutation endpoint."""

    is_api_mutation = (
        request.url.path.startswith("/api/")
        and request.method.upper() not in {"GET", "HEAD", "OPTIONS"}
    )
    if is_api_mutation:
        if request.headers.get(ai_assistant.FINANCIAL_REQUEST_HEADER) != "1":
            return JSONResponse(status_code=403, content={"detail": "缺少本地系统请求标记"})
        if request.url.path == "/api/shutdown":
            return await call_next(request)

        pending_restore_marker = get_settings().data_root / "pending_restore.json"
        if pending_restore_marker.exists():
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "数据包导入已排队，系统正在安全退出；为避免导入后丢失新数据，已停止接受财务写入"
                },
            )

        is_restore_request = request.url.path == "/api/backups/restore"
        if is_restore_request:
            if not _restore_mutation_gate.try_enter_restore():
                return JSONResponse(
                    status_code=409,
                    content={"detail": "系统仍有财务写入或另一份数据包正在导入，请稍后重试"},
                )
        elif not _restore_mutation_gate.try_enter_mutation():
            return JSONResponse(
                status_code=409,
                content={"detail": "数据包导入正在校验，已暂停新的财务写入"},
            )
        try:
            return await call_next(request)
        finally:
            if is_restore_request:
                _restore_mutation_gate.leave_restore()
            else:
                _restore_mutation_gate.leave_mutation()
    return await call_next(request)

app.include_router(master.router)
app.include_router(statements.router)
app.include_router(settlements.router)
app.include_router(invoices.router)
app.include_router(invoices.correction_router)
app.include_router(attachments.router)
app.include_router(system.router)
app.include_router(browser_sessions.router)
app.include_router(ai_assistant.router)


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": app.version,
        "local_only": settings.is_loopback,
    }


frontend_dist = settings.frontend_dist
assets_dir = frontend_dist / "assets"
if assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


@app.get("/{requested_path:path}", include_in_schema=False)
def spa_fallback(requested_path: str):
    if requested_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="API route not found")
    candidate = (frontend_dist / requested_path).resolve()
    if candidate.is_file() and frontend_dist.resolve() in candidate.parents:
        headers = {"Cache-Control": "no-store, max-age=0"} if candidate.name == "index.html" else None
        return FileResponse(candidate, headers=headers)
    index = frontend_dist / "index.html"
    if index.exists():
        # The program is upgraded in place while keeping the same localhost URL.
        # Never let an old application shell point at a previous release's assets.
        return FileResponse(index, headers={"Cache-Control": "no-store, max-age=0"})
    return {
        "message": "Frontend尚未构建。开发模式请启动Vite，生产模式请先运行build-windows.ps1。",
        "api_docs": "/api/docs",
    }
