"""Windows release gate: pinned native dependencies and real packaged OCR.

Uses only generated images and an isolated, new data directory. No AI calls.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import uuid

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "backend"))
from app.services.ocr_runtime import check_tesseract, prepare_windows_runtime, verify_runtime


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--app", type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    if os.name != "nt" or not str(args.work_root.resolve()).isascii():
        parser.error("Use Windows and an ASCII work directory on the workspace drive")
    work = args.work_root / uuid.uuid4().hex
    work.mkdir(parents=True)
    temporary = work / "tmp"
    temporary.mkdir()
    os.environ["TEMP"] = os.environ["TMP"] = str(temporary)
    # Exercise the compatibility path even when another developer's checkout
    # has an entirely ASCII name. All copied binaries stay inside this run.
    if args.app and str(args.app.resolve()).isascii():
        installed = work / "中文运行包"
        shutil.copytree(args.app.parent, installed)
        args.app = installed / args.app.name
        args.runtime = installed / "Tesseract-OCR"
    manifest = json.loads((PROJECT / "packaging/tesseract-runtime.json").read_text(encoding="utf-8"))
    verify_runtime(args.runtime, manifest)
    executable = prepare_windows_runtime(args.runtime / "tesseract.exe", work / "cache with spaces")
    check_tesseract(executable)
    result = {"runtime_files_verified": len(manifest), "language_check": "passed", "work": str(work)}
    if args.app:
        # Reproduce the reported failure: EXE remains but DLLs/languages are gone.
        # Delete only this newly created isolated cache, never an installed file.
        cache = executable.parent
        assert cache.is_relative_to(work) and cache != args.runtime.resolve(), "App smoke requires a non-ASCII installation path"
        for name in manifest:
            if name != "tesseract.exe":
                (cache / name).unlink()
        result.update(check_application(args.app, work, cache, manifest))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


def check_application(app: Path, work: Path, cache: Path, manifest: dict) -> dict:
    import httpx
    from PIL import Image, ImageDraw, ImageFont
    from app.config import APP_VERSION

    sample = Image.new("RGB", (1800, 1500), "white")
    draw = ImageDraw.Draw(sample)
    font = ImageFont.truetype(str(Path(os.environ["WINDIR"]) / "Fonts/arial.ttf"), 45)
    lines = ["OCR RELEASE CHECK", "Member Account No.: 123456789",
             "Name: TEST CLIENT", "Scheme Name Test Scheme", "Total Balance (HKD) 123,456.78",
             "Net Contributions & Transfer-in Amount (HKD) 100,000.00",
             "Investment gain (loss) 23,456.78", "As of 30/06/2026", "My Current Holdings"]
    for index, line in enumerate(lines):
        draw.text((65, 70 + 100 * index), line, font=font, fill="black")
    for suffix in ("png", "jpg", "pdf"):
        sample.save(work / f"synthetic.{suffix}")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    environment = dict(os.environ, FINANCIAL_PORT=str(port), FINANCIAL_HOST="127.0.0.1",
                       FINANCIAL_NO_BROWSER="1", FINANCIAL_DATA_ROOT=str(work / "data"),
                       FINANCIAL_CODEX_HOME=str(work / "ai"), FINANCIAL_OCR_CACHE_ROOT=str(work / "cache with spaces"))
    for key in ("FINANCIAL_TESSERACT_CMD", "TESSERACT_CMD", "TESSDATA_PREFIX"):
        environment.pop(key, None)
    process = subprocess.Popen([str(app.resolve())], cwd=app.parent, env=environment,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=90,
                         headers={"X-Financial-System-Request": "1"})
    try:
        deadline = time.monotonic() + 60
        while True:
            if process.poll() is not None:
                raise RuntimeError(f"Packaged app exited: {process.returncode}")
            try:
                health = client.get("/api/health").raise_for_status().json()
                break
            except httpx.TransportError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Packaged app startup timed out")
                time.sleep(0.25)
        assert health["version"] == APP_VERSION, health
        assert Path(client.get("/api/system-info").json()["data_root"]) == work / "data"
        checked = []
        for suffix, mime in (("png", "image/png"), ("jpg", "image/jpeg"), ("pdf", "application/pdf")):
            with (work / f"synthetic.{suffix}").open("rb") as stream:
                record = client.post("/api/statement-imports", files={"file": (f"synthetic.{suffix}", stream, mime)}).raise_for_status().json()
            assert record["status"] == "NEEDS_REVIEW", record
            with sqlite3.connect((work / "data/database/financial_system.sqlite3").as_uri() + "?mode=ro", uri=True) as connection:
                raw_text = connection.execute("SELECT raw_text FROM statement_imports WHERE id=?", (record["id"],)).fetchone()[0]
            assert "OCR RELEASE CHECK" in raw_text, record
            extracted = record["extracted"]
            assert extracted["document_type"] == "empf_account_page", record
            assert extracted["total_balance"] == "123456.78", record
            assert extracted["account_number"] == "123456789", record
            assert extracted["as_of_date"] == "2026-06-30", record
            assert record["confirmed_snapshot_id"] is None, record
            verify_runtime(cache, manifest)
            checked.append(suffix)
            # Another upload must repair a cache damaged since its first use.
            (cache / "libarchive-13.dll").unlink()
        # The same previously uploaded original can be retried without duplicates.
        retry = client.post(f"/api/statement-imports/{record['id']}/reparse").raise_for_status().json()
        assert retry["extracted"]["total_balance"] == "123456.78"
        verify_runtime(cache, manifest)
        return {"app_version": health["version"], "real_ocr_formats": checked,
                "cache_path_with_spaces": True,
                "exe_only_cache_repaired": True, "later_damage_repaired": True,
                "original_reparse": "passed", "financial_write": False, "ai_calls": 0}
    finally:
        if process.poll() is None:
            try:
                client.post("/api/shutdown").raise_for_status()
                process.wait(timeout=20)
            except (httpx.HTTPError, subprocess.TimeoutExpired):
                process.terminate()
                process.wait(timeout=10)
        client.close()


if __name__ == "__main__":
    main()
