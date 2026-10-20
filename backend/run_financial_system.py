from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

from app.launcher import main
from app.platform_paths import is_macos, macos_state_root


def _report_startup_error(message: str, details: str = "") -> None:
    log_root = Path(os.getenv("LOCALAPPDATA", Path.home())) / "FinancialFeeSystem"
    if is_macos():
        log_root = macos_state_root()
    try:
        log_root.mkdir(parents=True, exist_ok=True)
        (log_root / "launcher-error.log").write_text(
            f"{datetime.now().isoformat()}\n{message}\n{details}",
            encoding="utf-8",
        )
    except OSError:
        pass
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(0, message, "金融计划收费计算系统", 0x10)
    elif is_macos():
        subprocess.run(
            ["/usr/bin/osascript", "-e",
             'on run argv\n display alert "金融计划收费系统" message (item 1 of argv) as critical\nend run',
             message], check=False,
        )
    else:
        print(message, file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except SystemExit as exc:
        if exc.code not in (None, 0):
            _report_startup_error(str(exc.code))
        raise
    except Exception as exc:  # packaged GUI must surface otherwise invisible failures
        _report_startup_error(f"系统启动失败：{exc}", traceback.format_exc())
        raise
