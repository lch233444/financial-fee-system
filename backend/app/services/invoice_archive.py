from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re


_LEGACY_SAFE_NUMBER = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
_WINDOWS_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def invoice_archive_stem(invoice_number: str) -> str:
    """Return a case-safe deterministic stem without changing the business number.

    Windows paths are case-insensitive, while Invoice numbers are not.  Every new
    archive therefore uses a SHA-256-derived internal name, including Invoice
    numbers that would otherwise be safe as a literal filename.
    """

    digest = sha256(invoice_number.encode("utf-8")).hexdigest()
    return f"invoice-{digest}"


def invoice_archive_paths(invoice_number: str, pdf_root: Path) -> dict[str, Path]:
    stem = invoice_archive_stem(invoice_number)
    return {language: pdf_root / f"{stem}_{language}.pdf" for language in ("zh", "en")}


def legacy_invoice_archive_paths(invoice_number: str, pdf_root: Path) -> dict[str, Path] | None:
    """Return the pre-0.2.12 literal paths when the old filename was safe.

    These paths are for recovery and cleanup only.  Callers that create a new
    archive must use :func:`invoice_archive_paths`.
    """

    if not _LEGACY_SAFE_NUMBER.fullmatch(invoice_number):
        return None
    return {language: pdf_root / f"{invoice_number}_{language}.pdf" for language in ("zh", "en")}


def invoice_recovery_path_sets(invoice_number: str, pdf_root: Path) -> tuple[dict[str, Path], ...]:
    """Return canonical and compatible legacy path sets for ISSUING recovery."""

    canonical = invoice_archive_paths(invoice_number, pdf_root)
    legacy = legacy_invoice_archive_paths(invoice_number, pdf_root)
    if legacy is None:
        return (canonical,)
    return canonical, legacy


def invoice_download_filename(invoice_number: str, invoice_id: int, language: str) -> str:
    """Build a readable Windows-safe download name; the PDF keeps the exact number."""

    safe_number = _WINDOWS_UNSAFE_FILENAME.sub("_", invoice_number).strip(" .")
    if not safe_number:
        safe_number = "Invoice"
    suffix = f"-record-{invoice_id}_{language}.pdf"
    max_prefix_length = 180 - len(suffix)
    if len(safe_number) > max_prefix_length:
        digest = sha256(invoice_number.encode("utf-8")).hexdigest()[:12]
        readable_length = max(max_prefix_length - len(digest) - 1, 1)
        safe_number = f"{safe_number[:readable_length].rstrip(' .')}-{digest}"
    return f"{safe_number}{suffix}"
