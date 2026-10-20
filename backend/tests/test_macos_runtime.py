from pathlib import Path

import pytest

from app import config, launcher
from app.services import codex_app_server as codex, statement_parser


def test_macos_state_survives_app_replacement(monkeypatch, tmp_path):
    root = tmp_path / "Library" / "Application Support" / "FinancialFeeSystem"
    for module in (launcher, config):
        monkeypatch.setattr(module, "is_macos", lambda: True)
        monkeypatch.setattr(module, "macos_state_root", lambda: root)
    monkeypatch.delenv("FINANCIAL_DATA_ROOT", raising=False)
    monkeypatch.setattr(launcher, "_application_root", lambda: tmp_path / "read-only.app")
    launcher._prepare_data_root()
    assert (root / "config.json").is_file()
    assert (root / "data").is_dir()
    assert not (tmp_path / "read-only.app").exists()
    assert config.default_ai_codex_home() == root / "LunaCodexHome"
    # A second installation reuses the configured business data.
    monkeypatch.delenv("FINANCIAL_DATA_ROOT")
    monkeypatch.setattr(launcher, "_choose_data_root", lambda: pytest.fail("must reuse config"))
    launcher._prepare_data_root()
    assert Path(launcher.os.environ["FINANCIAL_DATA_ROOT"]) == root / "data"


@pytest.mark.parametrize("arch", ["arm64", "x86_64"])
def test_macos_codex_uses_verified_bundle_and_rejects_tampering(monkeypatch, tmp_path, arch):
    monkeypatch.setattr(codex, "is_macos", lambda: True)
    monkeypatch.setattr(codex.platform, "machine", lambda: arch)
    monkeypatch.setattr(codex.sys, "frozen", True, raising=False)
    monkeypatch.setattr(codex, "installation_root", lambda: tmp_path)
    monkeypatch.setattr(codex.shutil, "which", lambda _: pytest.fail("must not inspect PATH"))
    runtime = tmp_path / "Codex"
    runtime.mkdir()
    hashes = {}
    for name in ("codex", "codex-code-mode-host"):
        path = runtime / name
        path.write_bytes((arch + name).encode())
        hashes[name] = codex._file_sha256(path)
    monkeypatch.setattr(codex, "MAC_CODEX_HASHES", {arch: hashes})
    (runtime / "VERSION.txt").write_text("\n".join(f"{v}  {k}" for k, v in hashes.items()))
    assert codex.CodexAppServerClient._find_executable() == runtime / "codex"
    (runtime / "codex-code-mode-host").write_bytes(b"tampered")
    with pytest.raises(codex.CodexUnavailableError, match="完整性"):
        codex.CodexAppServerClient._find_executable()


def test_frozen_macos_requires_bundled_ocr(monkeypatch, tmp_path):
    monkeypatch.setattr(statement_parser, "is_macos", lambda: True)
    monkeypatch.setattr(statement_parser.sys, "frozen", True, raising=False)
    monkeypatch.setattr(statement_parser, "application_root", lambda: tmp_path)
    monkeypatch.setattr(statement_parser.shutil, "which", lambda _: pytest.fail("must not use Homebrew"))
    with pytest.raises(statement_parser.OcrRuntimeError, match="组件缺失"):
        statement_parser._configure_tesseract()
