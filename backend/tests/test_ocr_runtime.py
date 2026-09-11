from __future__ import annotations

import hashlib
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import ocr_runtime, statement_parser


def make_runtime(tmp_path: Path) -> Path:
    source = tmp_path / "中文程序" / "Tesseract-OCR"
    files = {
        "tesseract.exe": b"exe", "libtesseract-5.dll": b"tesseract",
        "libarchive-13.dll": b"archive", "libleptonica-6.dll": b"leptonica",
        "libother.dll": b"transitive", "tessdata/eng.traineddata": b"english",
        "tessdata/configs/tsv": b"tsv",
    }
    for name, value in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    manifest = {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}
    (source / "runtime-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return source / "tesseract.exe"


def test_repairs_exe_only_and_same_size_corrupt_cache(tmp_path):
    executable = make_runtime(tmp_path)
    cache_root = tmp_path / "cache"
    identity = hashlib.sha256(str(executable.resolve().parent).encode()).hexdigest()[:16]
    cache = cache_root / identity
    cache.mkdir(parents=True)
    (cache / "tesseract.exe").write_bytes(b"exe")
    (cache / "libarchive-13.dll").write_bytes(b"xxxxxxx")
    result = ocr_runtime.prepare_windows_runtime(executable, cache_root)
    assert result.parent == cache
    manifest = ocr_runtime.verify_runtime(executable.parent)
    assert ocr_runtime.verify_runtime(cache, manifest) == manifest
    assert (cache / "tessdata/configs/tsv").read_bytes() == b"tsv"
    # A removed dependency after first use must be repaired on the next use.
    (cache / "libother.dll").unlink()
    ocr_runtime.prepare_windows_runtime(executable, cache_root)
    assert (cache / "libother.dll").read_bytes() == b"transitive"


@pytest.mark.parametrize("missing", ["libtesseract-5.dll", "libarchive-13.dll", "libother.dll", "tessdata/eng.traineddata"])
def test_rejects_incomplete_installed_source(tmp_path, missing):
    executable = make_runtime(tmp_path)
    (executable.parent / missing).unlink()
    with pytest.raises(ocr_runtime.OcrRuntimeError, match=missing):
        ocr_runtime.prepare_windows_runtime(executable, tmp_path / "cache")


def test_interrupted_copy_is_repairable_and_not_published(tmp_path, monkeypatch):
    executable = make_runtime(tmp_path)
    copyfile = ocr_runtime.shutil.copyfile

    def interrupted(source, destination):
        Path(destination).write_bytes(b"partial")
        raise OSError("copy interrupted")

    monkeypatch.setattr(ocr_runtime.shutil, "copyfile", interrupted)
    with pytest.raises(OSError, match="copy interrupted"):
        ocr_runtime.prepare_windows_runtime(executable, tmp_path / "cache")
    assert not list((tmp_path / "cache").rglob("*.dll"))
    monkeypatch.setattr(ocr_runtime.shutil, "copyfile", copyfile)
    result = ocr_runtime.prepare_windows_runtime(executable, tmp_path / "cache")
    assert result.is_file()


def test_concurrent_requests_only_use_complete_cache(tmp_path):
    executable = make_runtime(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: ocr_runtime.prepare_windows_runtime(executable, tmp_path / "cache"), range(4)))
    assert len(set(results)) == 1
    ocr_runtime.verify_runtime(results[0].parent, ocr_runtime.verify_runtime(executable.parent))


@pytest.mark.parametrize("failure", ["dll", "timeout", "language"])
def test_component_failure_is_a_clear_page_warning(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(ocr_runtime, "suppress_native_error_dialogs", lambda: None)

    def run(*args, **kwargs):
        assert kwargs["timeout"] == 10
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args[0], 10)
        return subprocess.CompletedProcess(args[0], 0xC0000135 if failure == "dll" else 0, b"", b"")

    monkeypatch.setattr(ocr_runtime.subprocess, "run", run)
    monkeypatch.setattr(statement_parser, "_configure_tesseract", lambda: ocr_runtime.check_tesseract(tmp_path / "tesseract.exe"))
    result = statement_parser.parse_empf_statement(tmp_path / "statement.png")
    assert result.total_balance is None
    assert result.document_type == "unknown"
    assert "本地OCR组件" in result.warnings[0]
    assert "本次未生成余额快照" in result.warnings[1]


def test_windows_error_mode_is_inherited_without_modal_dialogs(monkeypatch):
    calls = []
    class Kernel:
        def GetErrorMode(self): return 0x100
        def SetErrorMode(self, value): calls.append(value)
    class Windll:
        kernel32 = Kernel()
    monkeypatch.setattr(ocr_runtime.ctypes, "windll", Windll(), raising=False)
    if ocr_runtime.os.name == "nt":
        ocr_runtime.suppress_native_error_dialogs()
        assert calls == [0x8103]


@pytest.mark.skipif(ocr_runtime.os.name != "nt", reason="Windows native configuration")
def test_language_directory_with_spaces_uses_environment_not_quoted_config(tmp_path, monkeypatch):
    executable = make_runtime(tmp_path)
    prepared = tmp_path / "runtime with spaces" / "tesseract.exe"
    monkeypatch.setenv("TESSDATA_PREFIX", "obsolete-directory")
    monkeypatch.setattr(statement_parser, "get_settings", lambda: SimpleNamespace(
        tesseract_cmd=str(executable), ocr_cache_root=tmp_path / "cache"))
    monkeypatch.setattr(statement_parser, "prepare_windows_runtime", lambda *_: prepared)
    monkeypatch.setattr(statement_parser, "check_tesseract", lambda *_: None)
    monkeypatch.setattr(statement_parser.pytesseract.pytesseract, "tesseract_cmd", "before")
    assert statement_parser._configure_tesseract() == str(prepared)
    assert statement_parser.os.environ["TESSDATA_PREFIX"] == str(prepared.parent / "tessdata")
    monkeypatch.setattr(statement_parser.pytesseract, "get_languages", lambda: ["eng"])
    def recognize(*args, **kwargs):
        assert "tessdata" not in kwargs["config"]
        assert kwargs["timeout"] == 30
        return {"text": [], "conf": []} if "output_type" in kwargs else "recognized"
    monkeypatch.setattr(statement_parser.pytesseract, "image_to_data", recognize)
    monkeypatch.setattr(statement_parser.pytesseract, "image_to_string", recognize)
    assert statement_parser._ocr(None)[0] == "recognized"
