from __future__ import annotations

import ntpath
from pathlib import Path

from app.services.invoice_archive import (
    invoice_archive_paths,
    invoice_archive_stem,
    invoice_download_filename,
    invoice_recovery_path_sets,
    legacy_invoice_archive_paths,
)


def test_all_new_archives_use_deterministic_hash_and_recovery_keeps_safe_legacy_paths() -> None:
    legacy = "AAA-TW-20260828-1"
    legacy_stem = invoice_archive_stem(legacy)
    assert legacy_stem.startswith("invoice-")
    assert len(legacy_stem) == len("invoice-") + 64

    unsafe = "Alpha/香港: Advisory Limited-TW-20260828-1"
    first = invoice_archive_stem(unsafe)
    assert first == invoice_archive_stem(unsafe)
    assert first.startswith("invoice-")
    assert len(first) == len("invoice-") + 64
    assert "/" not in first and ":" not in first

    paths = invoice_archive_paths(unsafe, Path("F:/synthetic/pdf"))
    assert set(paths) == {"zh", "en"}
    assert all(path.parent == Path("F:/synthetic/pdf") for path in paths.values())
    assert len(invoice_recovery_path_sets(unsafe, Path("F:/synthetic/pdf"))) == 1

    legacy_paths = legacy_invoice_archive_paths(legacy, Path("F:/synthetic/pdf"))
    assert legacy_paths is not None
    recovery_sets = invoice_recovery_path_sets(legacy, Path("F:/synthetic/pdf"))
    assert recovery_sets == (invoice_archive_paths(legacy, Path("F:/synthetic/pdf")), legacy_paths)


def test_hash_archive_names_do_not_collide_on_case_insensitive_windows_paths() -> None:
    lower = invoice_archive_stem("Alpha Advisory-TW-20260828-1")
    upper = invoice_archive_stem("ALPHA ADVISORY-TW-20260828-1")
    assert lower != upper
    assert ntpath.normcase(lower) != ntpath.normcase(upper)


def test_download_name_is_windows_safe_and_bounded_without_changing_business_number() -> None:
    number = f"{'长公司名称' * 35}/A:B*?-TW-20260828-123"
    filename = invoice_download_filename(number, 42, "zh")
    assert filename.endswith("-record-42_zh.pdf")
    assert len(filename) <= 180
    assert all(character not in filename for character in '<>:"/\\|?*')

    short = invoice_download_filename("Alpha Advisory-TW-20260828-1", 7, "en")
    assert short == "Alpha Advisory-TW-20260828-1-record-7_en.pdf"
