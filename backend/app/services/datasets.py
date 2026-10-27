"""Independent local datasets. Publication and switching are exclusive operations."""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from threading import Lock
from uuid import uuid4

from sqlalchemy import create_engine, event
from starlette.responses import JSONResponse

from .. import database
from ..config import get_settings, using_settings
from . import backup
from .settlement_cycle import read_cycle, CYCLES


class Datasets:
    def __init__(self, root: Path):
        if root.is_symlink() or root.is_junction():
            raise RuntimeError("数据根不能使用链接")
        self.root = root.resolve()
        self.catalog_path = self.root / "datasets.json"
        self.entries = [{"id": "primary", "name": "原有数据"}]
        self.active = "primary"
        self.generation = uuid4().hex
        self.lock = Lock()
        self.requests = 0
        self.exclusive = False
        self.managed = False
        if self.catalog_path.exists():
            if self.catalog_path.is_symlink():
                raise RuntimeError("数据包目录清单不能是链接")
            data = json.loads(self.catalog_path.read_text(encoding="utf-8"))
            entries = data.get("datasets")
            if data.get("version") != 1 or not isinstance(entries, list) or not entries:
                raise RuntimeError("数据包目录清单无效，已停止启动")
            ids, names = set(), set()
            for entry in entries:
                if not isinstance(entry, dict) or set(entry) != {"id", "name"}:
                    raise RuntimeError("数据包目录清单无效")
                identity, name = entry["id"], entry["name"]
                self.path(identity)
                self.check_name(name)
                if identity in ids or name.casefold() in names:
                    raise RuntimeError("数据包目录清单重复")
                ids.add(identity)
                names.add(name.casefold())
            if "primary" not in ids or data.get("active") not in ids:
                raise RuntimeError("当前数据包不存在，已停止启动")
            self.entries, self.active, self.managed = entries, data["active"], True

    def path(self, identity: str) -> Path:
        if identity == "primary":
            target = self.root
            self.check_components(target)
            return target
        if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{32}", identity):
            raise ValueError("数据包标识无效")
        parent = self.root / "datasets"
        target = parent / identity
        if any(p.is_symlink() or p.is_junction() for p in (self.root, parent, target)):
            raise ValueError("数据包目录不能使用链接")
        if target.resolve().parent != parent.resolve():
            raise ValueError("数据包目录超出允许范围")
        self.check_components(target)
        return target

    @staticmethod
    def check_components(target: Path) -> None:
        for component in (*backup.RESTORE_COMPONENTS, "backups", "tmp", "database/financial_system.sqlite3"):
            path = target / component
            if path.is_symlink() or path.is_junction():
                raise ValueError("数据包业务目录或数据库不能使用链接")

    @staticmethod
    def check_name(name: str) -> str:
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 60:
            raise ValueError("数据包名称须为1至60个字符")
        if any(ord(c) < 32 for c in name):
            raise ValueError("数据包名称不能包含控制字符")
        return name.strip()

    def snapshot(self) -> dict:
        entries = [{**entry, "settlement_cycle": read_cycle(self.path(entry["id"]) / "database/financial_system.sqlite3")} for entry in self.entries]
        return {"datasets": entries, "active_id": self.active, "token": self.generation,
                "settlement_cycle": next(entry["settlement_cycle"] for entry in entries if entry["id"] == self.active)}

    def save(self, entries: list[dict], active: str) -> None:
        temporary = self.root / f".datasets-{uuid4().hex}.tmp"
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump({"version": 1, "active": active, "datasets": entries}, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.catalog_path)
        finally:
            temporary.unlink(missing_ok=True)

    def settings_for(self, identity: str):
        return get_settings().model_copy(update={"data_root": self.path(identity)})

    @staticmethod
    def engine_for(settings):
        engine = create_engine(settings.database_url, connect_args={"check_same_thread": False}, pool_pre_ping=True)
        event.listen(engine, "connect", database._sqlite_pragmas)
        return engine

    def create(self, name: str, package: Path | None = None, *, settlement_cycle: str = "QUARTERLY") -> dict:
        if settlement_cycle not in CYCLES:
            raise ValueError("请选择季度或自然年度结算")
        name = self.check_name(name)
        if any(e["name"].casefold() == name.casefold() for e in self.entries):
            raise ValueError("数据包名称已存在，请使用不同名称")
        identity = uuid4().hex
        settings = self.settings_for(identity)
        root = settings.data_root
        root.mkdir(parents=True, exist_ok=False)
        target_engine = None
        try:
            with using_settings(settings):
                if package is not None:
                    (root / "backups").mkdir()
                    original = root / "backups" / "import-original.zip"
                    shutil.copyfile(package, original)
                    extracted = root / "tmp" / "import"
                    manifest = backup.validate_backup(original, extracted, allow_upgrade=True)
                    if manifest["format"] != backup.BACKUP_FORMAT:
                        raise ValueError("请使用带有系统版本的完整数据包；旧备份请先用对应程序转换")
                    backup._rebase_data_package_database(extracted, root, manifest, allow_upgrade=True)
                    for component in backup.RESTORE_COMPONENTS:
                        source = extracted / component
                        if source.exists():
                            source.rename(root / component)
                settings.ensure_directories()
                target_engine = self.engine_for(settings)
                database.init_db(target_settings=settings, target_engine=target_engine)
                if package is None and settlement_cycle == "ANNUAL":
                    with target_engine.begin() as connection:
                        connection.exec_driver_sql("UPDATE app_settings SET value='ANNUAL' WHERE key='settlement_cycle'")
                # Export validates migrated schema, protected records and every file.
                # The untouched original ZIP remains separately available for rollback.
                backup.create_backup()
            entry = {"id": identity, "name": name}
            entries = [*self.entries, entry]
            self.save(entries, self.active)
            self.entries, self.managed = entries, True
            return entry
        except Exception:
            (root / "import-failed.txt").write_text("创建或导入未完成；未加入数据包清单，原有数据未替换。请保留此目录供核查。", encoding="utf-8")
            raise
        finally:
            if target_engine is not None:
                target_engine.dispose()

    def bind(self, identity: str, engine) -> None:
        get_settings().data_root = self.path(identity)
        database.settings = get_settings()
        database.engine = engine
        database.SessionLocal.configure(bind=engine)
        self.active = identity
        self.generation = uuid4().hex

    def startup(self) -> None:
        selected = self.settings_for(self.active)
        if self.managed and not selected.database_path.is_file():
            raise RuntimeError("已选数据包的数据库缺失，已停止启动；不会新建空库替代")
        if self.active != "primary":
            database.engine.dispose()
            self.bind(self.active, self.engine_for(selected))

    def switch(self, identity: str) -> None:
        if not any(entry["id"] == identity for entry in self.entries):
            raise ValueError("数据包不存在")
        settings = self.settings_for(identity)
        if (settings.data_root / "pending_restore.json").exists():
            raise ValueError("目标数据包有待完成的恢复，请先重启完成恢复")
        if not settings.database_path.is_file():
            raise ValueError("数据包数据库缺失，不能切换")
        from .dataset_upgrade import upgrade_registered_dataset
        # The exclusive request gate prevents writes while the inactive target
        # is backed up, migrated in isolation and restored atomically.
        upgrade_registered_dataset(settings, self.engine_for)
        target_engine = self.engine_for(settings)
        try:
            with target_engine.connect() as connection:
                backup.validate_database_structure(connection.connection.driver_connection)
                revision = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
                if revision != backup.CURRENT_DATABASE_REVISION:
                    raise ValueError("数据包需要先升级，请导出后作为新数据包导入")
            # No request can cross this boundary: the ASGI gate includes streams
            # and response background tasks, not just route execution.
            from .codex_app_server import get_codex_app_server
            get_codex_app_server().close()
            get_codex_app_server.cache_clear()
            database.engine.dispose()
            self.save(self.entries, identity)
            self.bind(identity, target_engine)
            self.managed = True
        except Exception:
            target_engine.dispose()
            raise


_manager: Datasets | None = None


def get_datasets() -> Datasets:
    global _manager
    if _manager is None:
        _manager = Datasets(get_settings().data_root)
    return _manager


class DatasetGate:
    """Hold a dataset lease until the entire HTTP response has finished."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not path.startswith("/api/") or path == "/api/health":
            return await self.app(scope, receive, send)
        manager = get_datasets()
        headers = dict(scope.get("headers", []))
        from urllib.parse import parse_qs
        token = headers.get(b"x-financial-dataset", b"").decode()
        token = token or parse_qs(scope.get("query_string", b"").decode()).get("dataset_token", [""])[0]
        bootstrap = path == "/api/datasets" and scope["method"] == "GET"
        exclusive = scope["method"] == "POST" and path in {"/api/datasets", "/api/datasets/import", "/api/datasets/switch"}
        error = None
        with manager.lock:
            if manager.exclusive or (exclusive and manager.requests):
                error = "系统正在处理请求，请稍后再切换或管理数据包"
            elif not bootstrap and ((token and token != manager.generation) or (manager.managed and not token)):
                error = "当前数据包已变化，请刷新页面后操作"
            elif exclusive:
                manager.exclusive = True
            else:
                manager.requests += 1
        if error:
            return await JSONResponse({"detail": error, "dataset_changed": "已变化" in error}, status_code=409)(scope, receive, send)
        try:
            await self.app(scope, receive, send)
        finally:
            with manager.lock:
                if exclusive:
                    manager.exclusive = False
                else:
                    manager.requests -= 1
