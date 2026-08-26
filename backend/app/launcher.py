from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn


def _application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _config_paths() -> list[Path]:
    local_app_data = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return [
        _application_root() / "financial_system_config.json",
        local_app_data / "FinancialFeeSystem" / "config.json",
    ]


def _choose_data_root() -> Path | None:
    if os.name != "nt":
        return _application_root() / "data"
    script = r"""
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = '请选择金融计划收费系统的数据保存目录（数据库、客人账单、Excel、PDF及备份）'
$dialog.ShowNewFolderButton = $true
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    Write-Output $dialog.SelectedPath
}
"""
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-STA", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        creationflags=creation_flags,
    )
    selected = completed.stdout.strip()
    return Path(selected) if selected else None


def _prepare_data_root() -> None:
    if os.getenv("FINANCIAL_DATA_ROOT"):
        return
    for config_path in _config_paths():
        if not config_path.exists():
            continue
        try:
            configured = Path(json.loads(config_path.read_text(encoding="utf-8"))["data_root"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        os.environ["FINANCIAL_DATA_ROOT"] = str(configured)
        return

    selected = _choose_data_root()
    if not selected:
        raise SystemExit("首次启动需要选择数据保存目录；当前操作已取消。")
    selected.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"data_root": str(selected)}, ensure_ascii=False, indent=2)
    saved = False
    for config_path in _config_paths():
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(payload, encoding="utf-8")
            saved = True
            break
        except OSError:
            continue
    if not saved:
        raise SystemExit("无法保存数据目录配置，请把程序移动到可写目录后重试。")
    os.environ["FINANCIAL_DATA_ROOT"] = str(selected)


def _port_available(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def main() -> None:
    _prepare_data_root()
    # Import only after FINANCIAL_DATA_ROOT is known: database.py creates the
    # SQLite engine during import and must bind to the selected directory.
    from .config import get_settings
    from .main import app
    from .services.shutdown import get_shutdown_coordinator

    settings = get_settings()
    if not _port_available(settings.host, settings.port):
        raise SystemExit(f"端口{settings.port}已被占用，请关闭已运行的系统后重试。")
    browser_host = f"[{settings.host}]" if ":" in settings.host else settings.host
    if os.getenv("FINANCIAL_NO_BROWSER") != "1":
        threading.Timer(
            1.2, lambda: webbrowser.open(f"http://{browser_host}:{settings.port}")
        ).start()
    # PyInstaller's windowed mode intentionally has no stdout/stderr stream.
    # Uvicorn's default colour formatter probes isatty() on that missing
    # stream, so packaged builds must run without the console log config.
    server = uvicorn.Server(uvicorn.Config(
        app=app,
        host=settings.host,
        port=settings.port,
        log_level="info",
        log_config=None,
        access_log=False,
    ))
    shutdown_coordinator = get_shutdown_coordinator()
    shutdown_coordinator.register_server_stop_hook(lambda: setattr(server, "should_exit", True))
    try:
        server.run()
    finally:
        shutdown_coordinator.register_server_stop_hook(None)


if __name__ == "__main__":
    main()
