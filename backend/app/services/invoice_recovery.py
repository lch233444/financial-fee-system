"""Shared archive verification for ISSUING recovery and its UI eligibility."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import stat

from pypdf import PdfReader
from pypdf.errors import PyPdfError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ExportRecord, Invoice
from .invoice_archive import invoice_recovery_path_sets
from .storage import is_within


def validated_recovery_archives(
    invoice: Invoice, pdf_root: Path, db: Session | None,
) -> tuple[dict[str, Path], dict[str, str]]:
    """Return the only usable bilingual pair, hashing the bytes we actually parse."""
    if not invoice.invoice_number or db is None:
        raise ValueError("ISSUING Invoice缺少已预留编号或归档记录，不能完成签发")
    path_sets = invoice_recovery_path_sets(invoice.invoice_number, pdf_root)
    if any(not is_within(path, pdf_root) for paths in path_sets for path in paths.values()):
        raise ValueError("Invoice归档路径不安全，不能自动完成签发")
    # A damaged pair must not make a second, untracked pair authoritative.
    if sum(all(path.exists() for path in paths.values()) for paths in path_sets) > 1:
        raise ValueError("同时发现哈希归档和旧版原名双语归档，不能自动完成签发；请人工核对后仅保留一组")
    records = db.scalars(select(ExportRecord).where(
        ExportRecord.export_type == "PDF_INVOICE", ExportRecord.entity_type == "INVOICE",
        ExportRecord.entity_id == invoice.id,
    )).all()
    known_hashes: dict[tuple[Path, str], set[str]] = {}
    for record in records:
        key = (Path(record.stored_path).resolve(), record.language)
        known_hashes.setdefault(key, set()).add(record.sha256.casefold())

    complete = []
    for paths in path_sets:
        hashes = {}
        for language, path in paths.items():
            try:
                if not stat.S_ISREG(path.lstat().st_mode):
                    break
                content = path.read_bytes()
                if not content:
                    break
                document = PdfReader(BytesIO(content), strict=True)
                if document.is_encrypted or not len(document.pages):
                    break
                digest = sha256(content).hexdigest()
                expected = known_hashes.get((path.resolve(), language), set())
                if expected and expected != {digest}:
                    break
                hashes[language] = digest
            except (OSError, PyPdfError, ValueError, TypeError, KeyError):
                break
        if len(hashes) == len(paths):
            complete.append((paths, hashes))
    if not complete:
        raise ValueError("中文和英文Invoice PDF必须都完整存在于安全归档目录、可读取且与已有摘要一致")
    if len(complete) > 1:
        raise ValueError("同时发现哈希归档和旧版原名双语归档，不能自动完成签发；请人工核对后仅保留一组")
    return complete[0]
