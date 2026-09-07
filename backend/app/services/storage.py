from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
from pathlib import Path
from typing import BinaryIO


SAFE_NAME = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+")
STATEMENT_FILE_SIGNATURES = (
    (b"\xff\xd8\xff", ".jpg", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", ".png", "image/png"),
    (b"%PDF-", ".pdf", "application/pdf"),
)


def detect_statement_format(data: bytes) -> tuple[str, str] | None:
    """Return canonical suffix/MIME from trusted bytes, never user metadata."""

    for signature, suffix, mime_type in STATEMENT_FILE_SIGNATURES:
        if data.startswith(signature):
            return suffix, mime_type
    return None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    buffer = bytearray(64 * 1024)
    view = memoryview(buffer)
    with path.open("rb") as stream:
        while True:
            size = stream.readinto(buffer)
            if not size:
                break
            digest.update(view[:size])
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_filename(name: str) -> str:
    clean = SAFE_NAME.sub("_", Path(name).name).strip("._")
    return clean or "file"


def store_bytes(*, data: bytes, original_name: str, directory: Path, prefix: str = "") -> tuple[Path, str]:
    directory.mkdir(parents=True, exist_ok=True)
    digest = sha256_bytes(data)
    suffix = Path(original_name).suffix.lower()
    target = directory / f"{prefix}{digest}{suffix}"
    if not target.exists():
        target.write_bytes(data)
    return target, digest


def copy_with_hash(source: Path, target: Path) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return sha256_file(target)


def store_stream(*, stream: BinaryIO, directory: Path, max_bytes: int, suffix: str = "") -> Path:
    """Spool uploads with bounded memory and remove incomplete files on any failure."""
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=directory, prefix="restore_", suffix=suffix, delete=False) as output:
        target = Path(output.name)
        try:
            total = 0
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError("上传文件超过大小限制")
                output.write(chunk)
            output.flush()
        except BaseException:
            output.close()
            target.unlink(missing_ok=True)
            raise
    return target


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
