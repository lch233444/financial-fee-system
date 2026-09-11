"""Prepare the complete native OCR runtime before starting any OCR process."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from threading import RLock


_runtime_lock = RLock()


class OcrRuntimeError(RuntimeError):
    pass


def suppress_native_error_dialogs() -> None:
    if os.name == "nt":
        # Missing DLLs must return an error, not block a background request on
        # a Windows modal dialog. Child processes inherit this error mode.
        kernel32 = ctypes.windll.kernel32
        kernel32.SetErrorMode(kernel32.GetErrorMode() | 0x0001 | 0x0002 | 0x8000)


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def runtime_files(directory: Path) -> list[Path]:
    return sorted([
        directory / "tesseract.exe",
        *directory.glob("*.dll"),
        *(path for path in (directory / "tessdata").rglob("*") if path.is_file()),
    ])


def verify_runtime(directory: Path, manifest: dict[str, str] | None = None) -> dict[str, str]:
    required = ("tesseract.exe", "libtesseract-5.dll", "libarchive-13.dll",
                "libleptonica-6.dll", "tessdata/eng.traineddata")
    for name in required:
        if not (directory / name).is_file():
            raise OcrRuntimeError(f"本地OCR组件不完整，缺少 {name}；请恢复完整运行包后重新识别。")
    manifest_path = directory / "runtime-manifest.json"
    if manifest is None and manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest is not None:
        for name, expected in manifest.items():
            path = directory / name
            if not path.is_file() or file_digest(path) != expected:
                raise OcrRuntimeError(f"本地OCR组件校验失败：{name}；请恢复完整运行包后重新识别。")
        return manifest
    return {path.relative_to(directory).as_posix(): file_digest(path)
            for path in runtime_files(directory)}


def prepare_windows_runtime(executable: Path, cache_root: Path) -> Path:
    """Repair incomplete/obsolete cache files from the verified installed copy."""
    with _runtime_lock:
        source = executable.resolve().parent
        manifest = verify_runtime(source)
        if str(executable).isascii():
            return executable
        if not str(cache_root).isascii():
            raise OcrRuntimeError("本地OCR缓存路径须为英文路径，请配置FINANCIAL_OCR_CACHE_ROOT后重启。")
        # Separate installed versions; never execute the
        # old unverified shared TEMP/FinancialFeeSystem-OCR directory.
        identity = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:16]
        target = cache_root / identity
        target.mkdir(parents=True, exist_ok=True)
        for name, expected in manifest.items():
            destination = target / name
            if destination.is_file() and file_digest(destination) == expected:
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            # An interrupted copy must not publish a half-written DLL/EXE.
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
                staged = Path(temporary.name)
            try:
                shutil.copyfile(source / name, staged)
                if file_digest(staged) != expected:
                    raise OcrRuntimeError(f"本地OCR组件复制校验失败：{name}，请重新识别。")
                staged.replace(destination)
            finally:
                staged.unlink(missing_ok=True)
        verify_runtime(target, manifest)
        return target / executable.name


def check_tesseract(executable: Path) -> None:
    suppress_native_error_dialogs()
    try:
        completed = subprocess.run(
            [str(executable), "--list-langs", "--tessdata-dir", str(executable.parent / "tessdata")],
            capture_output=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OcrRuntimeError("本地OCR组件无法启动或检查超时；原件会保留，请修复组件后重新识别。") from exc
    languages = completed.stdout.decode("utf-8", errors="replace").splitlines()
    if completed.returncode or "eng" not in {line.strip() for line in languages}:
        raise OcrRuntimeError(
            f"本地OCR组件无法加载或缺少英文语言数据（退出码{completed.returncode}）；"
            "原件会保留，请修复组件后重新识别。"
        )
