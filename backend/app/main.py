from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import APP_VERSION, LOOPBACK_HOSTS, get_settings
from .database import init_db
from .routes import ai_assistant, attachments, invoices, master, settlements, statements, system
from .services.backup import apply_pending_restore
from .services.codex_app_server import get_codex_app_server


@asynccontextmanager
async def lifespan(_app: FastAPI):
    apply_pending_restore()
    init_db()
    try:
        yield
    finally:
        get_codex_app_server().close()


settings = get_settings()
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

    if (
        request.url.path.startswith("/api/")
        and request.method.upper() not in {"GET", "HEAD", "OPTIONS"}
        and request.headers.get(ai_assistant.FINANCIAL_REQUEST_HEADER) != "1"
    ):
        return JSONResponse(status_code=403, content={"detail": "缺少本地系统请求标记"})
    return await call_next(request)

app.include_router(master.router)
app.include_router(statements.router)
app.include_router(settlements.router)
app.include_router(invoices.router)
app.include_router(attachments.router)
app.include_router(system.router)
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
