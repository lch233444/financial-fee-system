from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
import pymupdf as fitz
from PIL import Image

import app.services.codex_app_server as codex_app_server_module
from app.config import get_settings

from app.services.codex_app_server import (
    AI_PARSER_VERSION,
    CODEX_EXTRACTION_CONFIG,
    CODEX_RECOGNITION_ROOT_MARKER,
    FIXED_AI_MODEL,
    CodexAppServerClient,
    CodexModelReroutedError,
    CodexProtocolError,
    CodexRecognitionError,
    CodexUnsafeConfigurationError,
    CodexUnavailableError,
    _active_extension_overrides,
    _assert_extraction_config_safe,
    _assert_no_instruction_sources,
    _codex_app_server_command,
    _codex_subprocess_environment,
    _image_for_codex,
    _images_for_codex,
    _recognition_workspace,
    _validate_bundled_codex_runtime,
    build_ai_review_result,
    compare_ocr_and_ai,
    empf_output_schema,
)


AI_VALUES = {
    "document_type": "empf_account_page",
    "client_name": "SAMPLE CLIENT",
    "account_number": "24681357",
    "scheme_name": "BCT (MPF) Pro Choice",
    "trustee": "Bank Consortium Trust Company Limited",
    "currency": "HKD",
    "as_of_date": "2026-05-20",
    "total_balance": "9736.57",
    "lifetime_net_contributions": "9735.04",
    "lifetime_gain_loss": "1.53",
    "holdings": [],
    "uncertain_fields": [],
    "warnings": [],
}


def test_fixed_sol_contract_and_parser_version() -> None:
    assert FIXED_AI_MODEL == "gpt-5.6-sol"
    assert AI_PARSER_VERSION == "CODEX_APP_SERVER_EMPF_1.2"


def _write_test_jpeg(path: Path) -> None:
    Image.new("RGB", (8, 8), "white").save(path, format="JPEG")


def _safe_effective_config(
    *,
    mcp_servers: dict[str, Any] | None = None,
    hooks: dict[str, Any] | None = None,
    plugins: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "web_search": "disabled",
        "apps": {"_default": {"enabled": False}},
        "agents": {"enabled": False},
        "features": {"shell_tool": False, "unified_exec": False},
        "mcp_servers": mcp_servers or {},
        "hooks": hooks or {},
        "plugins": plugins or {},
        "project_doc_max_bytes": 0,
        "project_doc_fallback_filenames": [],
    }


def test_extraction_subprocess_disables_agent_and_external_tools() -> None:
    assert 'web_search="disabled"' in CODEX_EXTRACTION_CONFIG
    assert "apps._default.enabled=false" in CODEX_EXTRACTION_CONFIG
    assert "agents.enabled=false" in CODEX_EXTRACTION_CONFIG
    assert "features.shell_tool=false" in CODEX_EXTRACTION_CONFIG
    assert "features.unified_exec=false" in CODEX_EXTRACTION_CONFIG
    assert "mcp_servers={}" in CODEX_EXTRACTION_CONFIG
    assert "hooks={}" in CODEX_EXTRACTION_CONFIG
    assert "plugins={}" in CODEX_EXTRACTION_CONFIG
    assert "project_doc_max_bytes=0" in CODEX_EXTRACTION_CONFIG
    assert "project_doc_fallback_filenames=[]" in CODEX_EXTRACTION_CONFIG
    assert f'project_root_markers=["{CODEX_RECOGNITION_ROOT_MARKER}"]' in CODEX_EXTRACTION_CONFIG


def test_app_server_command_keeps_all_overrides_without_strict_config() -> None:
    executable = Path("C:/FinancialFeeSystem/Codex/codex.exe")

    command = _codex_app_server_command(executable, CODEX_EXTRACTION_CONFIG)

    assert command[0] == str(executable)
    assert command[-1] == "app-server"
    assert "--strict-config" not in command
    assert [command[index + 1] for index, value in enumerate(command) if value == "-c"] == list(
        CODEX_EXTRACTION_CONFIG
    )


def test_codex_subprocess_environment_scrubs_api_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    expected_codex_home = get_settings().codex_home.resolve()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "unsafe-user-codex-home"))
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-codex")
    monkeypatch.setenv("OPENAI_ORG_ID", "must-not-reach-codex")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "must-not-reach-codex")
    monkeypatch.setenv("OPENAI_CUSTOM_AUTH_TOKEN", "must-not-reach-codex")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "must-not-reach-codex")
    monkeypatch.setenv("CODEX_API_KEY", "must-not-reach-codex")
    monkeypatch.setenv("FINANCIAL_SAFE_SENTINEL", "preserved")

    child_env = _codex_subprocess_environment()

    assert not any(key.upper().startswith("OPENAI_") for key in child_env)
    assert "AZURE_OPENAI_API_KEY" not in child_env
    assert "CODEX_API_KEY" not in child_env
    assert child_env["FINANCIAL_SAFE_SENTINEL"] == "preserved"
    assert child_env["CODEX_HOME"] == str(expected_codex_home)


def test_codex_subprocess_environment_ignores_explicit_user_codex_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    explicit = tmp_path / "explicit-codex-home"
    monkeypatch.setenv("CODEX_HOME", str(explicit))

    child_env = _codex_subprocess_environment()

    assert child_env["CODEX_HOME"] == str(get_settings().codex_home.resolve())
    assert child_env["CODEX_HOME"] != str(explicit)


def test_bundled_codex_runtime_requires_code_and_manifest_hashes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = tmp_path / "Codex"
    runtime.mkdir()
    files = {
        "codex.exe": b"codex-test-runtime",
        "codex-code-mode-host.exe": b"host-test-runtime",
    }
    hashes: dict[str, str] = {}
    for filename, content in files.items():
        target = runtime / filename
        target.write_bytes(content)
        hashes[filename] = codex_app_server_module._file_sha256(target)
    (runtime / "VERSION.txt").write_text(
        "\n".join(f"{digest}  {filename}" for filename, digest in hashes.items()),
        encoding="utf-8",
    )
    monkeypatch.setattr(codex_app_server_module, "BUNDLED_CODEX_HASHES", hashes)

    _validate_bundled_codex_runtime(runtime / "codex.exe")
    (runtime / "codex-code-mode-host.exe").write_bytes(b"tampered")
    with pytest.raises(CodexUnavailableError):
        _validate_bundled_codex_runtime(runtime / "codex.exe")


def test_frozen_executable_discovery_ignores_override_and_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundled = tmp_path / "Codex" / "codex.exe"
    bundled.parent.mkdir()
    bundled.write_bytes(b"mocked-after-integrity-layer")
    monkeypatch.setattr(codex_app_server_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(codex_app_server_module, "installation_root", lambda: tmp_path)
    monkeypatch.setattr(
        codex_app_server_module,
        "_validate_bundled_codex_runtime",
        lambda path: None if path == bundled.resolve() else pytest.fail("wrong bundled path"),
    )
    monkeypatch.setattr(
        codex_app_server_module.shutil,
        "which",
        lambda _name: pytest.fail("frozen discovery must not inspect PATH"),
    )

    assert CodexAppServerClient._find_executable() == bundled.resolve()


def test_inherited_mcp_and_hooks_get_process_only_disable_overrides() -> None:
    config = _safe_effective_config(
        mcp_servers={
            "anysearch": {"command": "private"},
            "already-off": {"enabled": False},
        },
        hooks={"SessionStart": [{"command": "private"}], "AfterAgent": []},
        plugins={
            "codex-app-tools@openai-bundled": {"enabled": True},
            "disabled-plugin": {"enabled": False},
        },
    )

    assert _active_extension_overrides(config) == (
        "mcp_servers.anysearch.enabled=false",
        "hooks.SessionStart=[]",
        'plugins={"codex-app-tools@openai-bundled"={enabled=false},'
        '"disabled-plugin"={enabled=false}}',
    )


def test_special_mcp_id_fails_closed_instead_of_serializing_its_config() -> None:
    with pytest.raises(CodexUnsafeConfigurationError):
        _active_extension_overrides(
            _safe_effective_config(
                mcp_servers={"https://private.example/mcp": {"enabled": True}}
            )
        )


def test_effective_extraction_config_fails_closed_when_capability_remains_active() -> None:
    _assert_extraction_config_safe(
        _safe_effective_config(
            mcp_servers={"off": {"enabled": False}}, hooks={"SessionStart": []}
        )
    )

    unsafe_web = _safe_effective_config()
    unsafe_web["web_search"] = "live"
    with pytest.raises(CodexUnsafeConfigurationError):
        _assert_extraction_config_safe(unsafe_web)

    with pytest.raises(CodexUnsafeConfigurationError) as exc_info:
        _assert_extraction_config_safe(
            _safe_effective_config(mcp_servers={"private-id": {"enabled": True}})
        )
    assert "private-id" not in str(exc_info.value)


def test_effective_config_allows_unknown_desktop_metadata_but_still_checks_controls() -> None:
    compatible = _safe_effective_config()
    compatible["computer_use"] = {"source": "newer-desktop"}

    _assert_extraction_config_safe(compatible)

    compatible["features"]["shell_tool"] = True
    with pytest.raises(CodexUnsafeConfigurationError):
        _assert_extraction_config_safe(compatible)


def test_startup_restarts_once_to_disable_inherited_extensions(monkeypatch) -> None:
    client = CodexAppServerClient()
    starts: list[tuple[str, ...]] = []
    effective_configs = iter(
        [
            _safe_effective_config(
                mcp_servers={"private-id": {"enabled": True}},
                hooks={"SessionStart": [{"command": "private"}]},
                plugins={"connector-plugin": {"enabled": True}},
            ),
            _safe_effective_config(
                mcp_servers={"private-id": {"enabled": False}},
                hooks={"SessionStart": []},
                plugins={"connector-plugin": {"enabled": False}},
            ),
        ]
    )
    monkeypatch.setattr(client, "_find_executable", lambda: Path("codex-test.exe"))
    monkeypatch.setattr(
        client,
        "_start_process",
        lambda _executable, overrides: starts.append(overrides),
    )
    monkeypatch.setattr(client, "_read_effective_config", lambda: next(effective_configs))
    monkeypatch.setattr(client, "close", lambda: None)

    client._ensure_started()

    assert starts[0] == CODEX_EXTRACTION_CONFIG
    assert starts[1] == (
        *CODEX_EXTRACTION_CONFIG,
        "mcp_servers.private-id.enabled=false",
        "hooks.SessionStart=[]",
        'plugins={"connector-plugin"={enabled=false}}',
    )


def test_startup_fails_closed_if_extension_is_still_active_after_restart(monkeypatch) -> None:
    client = CodexAppServerClient()
    unsafe = _safe_effective_config(mcp_servers={"private-id": {"enabled": True}})
    starts: list[tuple[str, ...]] = []
    monkeypatch.setattr(client, "_find_executable", lambda: Path("codex-test.exe"))
    monkeypatch.setattr(
        client,
        "_start_process",
        lambda _executable, overrides: starts.append(overrides),
    )
    monkeypatch.setattr(client, "_read_effective_config", lambda: unsafe)
    monkeypatch.setattr(client, "close", lambda: None)

    with pytest.raises(CodexUnsafeConfigurationError) as exc_info:
        client._ensure_started()

    assert len(starts) == 2
    assert "private-id" not in str(exc_info.value)


def test_instruction_sources_fail_closed_without_exposing_paths() -> None:
    _assert_no_instruction_sources({"instructionSources": []})
    with pytest.raises(CodexUnsafeConfigurationError) as exc_info:
        _assert_no_instruction_sources(
            {"thread": {"instructionSources": ["D:/private/AGENTS.md"]}}
        )
    assert "private" not in str(exc_info.value)


def test_empf_output_schema_is_strict_and_requires_all_fields() -> None:
    schema = empf_output_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "document_type",
        "client_name",
        "account_number",
        "scheme_name",
        "trustee",
        "currency",
        "as_of_date",
        "total_balance",
        "lifetime_net_contributions",
        "lifetime_gain_loss",
        "holdings",
        "uncertain_fields",
        "warnings",
    }
    holding_schema = schema["$defs"]["AIHolding"]
    assert holding_schema["additionalProperties"] is False
    assert set(schema["properties"]["document_type"]["enum"]) == {
        "empf_account_page",
        "contribution_record",
        "contribution_asset_transfer_record",
        "unknown",
    }


def test_comparison_requires_four_critical_agreements_for_auto_prefill() -> None:
    result = compare_ocr_and_ai(AI_VALUES, AI_VALUES)
    assert result["status"] == "AGREED"
    assert result["automatic_prefill_allowed"] is True
    assert result["requires_financial_confirmation"] is True

    conflicting = {**AI_VALUES, "total_balance": "9736.58"}
    result = compare_ocr_and_ai(AI_VALUES, conflicting)
    assert result["status"] == "CONFLICT"
    assert result["automatic_prefill_allowed"] is False
    assert result["conflict_requires_human_review"] is True
    assert [value["field"] for value in result["conflicts"]] == ["total_balance"]

    uncorroborated = {**AI_VALUES, "client_name": None}
    result = compare_ocr_and_ai(AI_VALUES, uncorroborated)
    assert result["status"] == "INCOMPLETE"
    assert result["uncorroborated_critical_fields"] == ["client_name"]

    unknown_document = {**AI_VALUES, "document_type": "unknown"}
    result = compare_ocr_and_ai(AI_VALUES, unknown_document)
    assert result["status"] == "CONFLICT"
    assert result["conflicts"][0]["field"] == "document_type"
    assert result["unsupported_document_type"] is True
    assert result["automatic_prefill_allowed"] is False
    assert result["recognition_requires_human_review"] is True


def test_holding_numeric_formats_compare_equal_and_conflicts_disable_prefill() -> None:
    ocr_holding = {
        "fund_name": "BCT Fund",
        "market_value": "9736.57",
        "investment_gain_loss": "1.53",
        "portfolio_percent": "100.00",
        "units": "7697.509290",
        "unit_price": "1.2649",
        "mandatory_contributions": None,
        "voluntary_contributions": None,
        "balance_as_of": "2026-05-20",
    }
    ocr = {**AI_VALUES, "holdings": [ocr_holding]}
    ai = {
        **ocr,
        "holdings": [{**ocr_holding, "portfolio_percent": "100.0%", "units": "7697.50929"}],
    }
    result = compare_ocr_and_ai(ocr, ai)
    assert result["status"] == "AGREED"
    assert result["automatic_prefill_allowed"] is True

    ai["holdings"][0]["unit_price"] = "1.2650"
    result = compare_ocr_and_ai(ocr, ai)
    assert result["status"] == "CONFLICT"
    assert result["automatic_prefill_allowed"] is False
    assert result["conflicts"][0]["field"] == "holdings[0].unit_price"


def test_holding_comparison_matches_funds_independent_of_row_order() -> None:
    first = {
        "fund_name": "Manulife MPF Conservative Fund",
        "market_value": "900.00",
        "investment_gain_loss": None,
        "portfolio_percent": "90.0%",
        "units": None,
        "unit_price": None,
        "mandatory_contributions": None,
        "voluntary_contributions": None,
        "balance_as_of": "2025-12-31",
    }
    second = {
        **first,
        "fund_name": "Manulife MPF Age 65 Plus Fund",
        "market_value": "100.00",
        "portfolio_percent": "10.0%",
    }
    ocr = {**AI_VALUES, "holdings": [first, second]}
    ai = {**ocr, "holdings": [second, first]}

    result = compare_ocr_and_ai(ocr, ai)

    assert result["status"] == "AGREED"
    assert result["conflicts"] == []
    assert "holdings[0].market_value" in result["agreements"]
    assert "holdings[1].market_value" in result["agreements"]


def test_one_sided_holding_value_and_any_uncertainty_are_incomplete() -> None:
    ocr_holding = {
        "fund_name": "BCT Fund",
        "market_value": "9736.57",
        "investment_gain_loss": "1.53",
        "portfolio_percent": "100.00",
        "units": "7697.50929",
        "unit_price": "1.2649",
        "mandatory_contributions": None,
        "voluntary_contributions": None,
        "balance_as_of": None,
    }
    ocr = {**AI_VALUES, "holdings": [ocr_holding]}
    ai = {
        **ocr,
        "holdings": [{**ocr_holding, "voluntary_contributions": "0.00"}],
    }

    result = compare_ocr_and_ai(ocr, ai)
    assert result["status"] == "INCOMPLETE"
    assert result["automatic_prefill_allowed"] is False
    assert result["recognition_requires_human_review"] is True
    assert result["conflict_requires_human_review"] is False
    assert result["uncorroborated"][0]["field"] == "holdings[0].voluntary_contributions"

    uncertain = {**ocr, "uncertain_fields": ["holdings[0].fund_name"]}
    result = compare_ocr_and_ai(ocr, uncertain)
    assert result["status"] == "INCOMPLETE"
    assert result["recognition_requires_human_review"] is True


def test_uncertain_critical_and_arithmetic_mismatch_force_manual_review() -> None:
    uncertain = {**AI_VALUES, "uncertain_fields": ["total_balance"]}
    comparison = compare_ocr_and_ai(AI_VALUES, uncertain)
    assert comparison["automatic_prefill_allowed"] is False
    assert comparison["recognition_requires_human_review"] is True
    assert comparison["uncertain_critical_fields"] == ["total_balance"]

    holding = {
        "fund_name": "BCT Fund",
        "market_value": "9730.00",
        "investment_gain_loss": "2.53",
        "portfolio_percent": "100.0%",
        "units": "7697.50929",
        "unit_price": "1.2649",
        "mandatory_contributions": None,
        "voluntary_contributions": None,
        "balance_as_of": "2026-05-20",
    }
    inconsistent = {**AI_VALUES, "lifetime_gain_loss": "2.53", "holdings": [holding]}
    review = build_ai_review_result(inconsistent, inconsistent)
    assert review["status"] == "CONFLICT"
    assert review["automatic_prefill_allowed"] is False
    assert set(review["validation_failures"]) == {
        "total_equals_lifetime_net_plus_gain_loss",
        "total_equals_sum_of_holding_market_values",
    }


def test_status_accepts_nonliteral_pro_plan_when_chatgpt_and_sol_ready(monkeypatch) -> None:
    client = CodexAppServerClient()
    monkeypatch.setattr(client, "_ensure_started", lambda: None)
    monkeypatch.setattr(
        client,
        "_account",
        lambda: {"type": "chatgpt", "email": "finance@example.com", "planType": "prolite"},
    )
    monkeypatch.setattr(
        client,
        "_fixed_model",
        lambda: {"model": FIXED_AI_MODEL, "inputModalities": ["text", "image"]},
    )
    monkeypatch.setattr(client, "_rate_limits", lambda: ({"buckets": []}, False))

    status = client.status()
    assert status["status"] == "ready"
    assert status["authenticated"] is True
    assert status["plan_type"] == "prolite"
    assert status["model"] == FIXED_AI_MODEL


def test_status_marks_exhausted_quota_for_manual_review(monkeypatch) -> None:
    client = CodexAppServerClient()
    monkeypatch.setattr(client, "_ensure_started", lambda: None)
    monkeypatch.setattr(client, "_account", lambda: {"type": "chatgpt", "planType": "prolite"})
    monkeypatch.setattr(client, "_fixed_model", lambda: {"model": FIXED_AI_MODEL})
    monkeypatch.setattr(
        client,
        "_rate_limits",
        lambda: ({"buckets": [{"primary": {"used_percent": 100.0}}]}, True),
    )

    status = client.status()
    assert status["status"] == "quota_exhausted"
    assert status["model_available"] is True


def test_model_availability_uses_visible_catalog_only(monkeypatch) -> None:
    client = CodexAppServerClient()
    captured: dict[str, Any] = {}

    def fake_rpc(method: str, params: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
        captured["method"] = method
        captured["params"] = params
        return {
            "data": [
                {
                    "id": FIXED_AI_MODEL,
                    "model": FIXED_AI_MODEL,
                    "hidden": True,
                    "inputModalities": ["text", "image"],
                }
            ]
        }

    monkeypatch.setattr(client, "_rpc", fake_rpc)
    assert client._fixed_model() is None
    assert captured == {
        "method": "model/list",
        "params": {"limit": 100, "includeHidden": False},
    }


def test_customer_image_is_reencoded_to_temporary_png_without_exif(tmp_path: Path) -> None:
    source = tmp_path / "customer.jpg"
    image = Image.new("RGB", (8, 8), "white")
    exif = Image.Exif()
    exif[270] = "private statement metadata"
    image.save(source, format="JPEG", exif=exif)

    with _image_for_codex(source) as transport:
        assert transport != source.resolve()
        assert transport.suffix == ".png"
        assert transport.is_file()
        with Image.open(transport) as normalized:
            assert not normalized.getexif()
            assert "exif" not in normalized.info
    assert not transport.exists()


def test_recognition_workspace_is_private_project_root() -> None:
    workspace = _recognition_workspace()

    assert workspace == get_settings().codex_recognition_workspace.resolve()
    assert (workspace / CODEX_RECOGNITION_ROOT_MARKER).is_file()
    assert workspace != get_settings().data_root.resolve()


def test_pdf_all_pages_are_reencoded_for_sol_and_removed(tmp_path: Path) -> None:
    source = tmp_path / "statement.pdf"
    with fitz.open() as document:
        for page_number in (1, 2):
            page = document.new_page()
            page.insert_text((72, 72), f"Statement page {page_number}")
        document.save(source)

    with _images_for_codex(source) as transports:
        assert len(transports) == 2
        assert all(path.is_file() and path.suffix == ".png" for path in transports)
        assert all(path != source.resolve() for path in transports)
        with Image.open(transports[1]) as second_page:
            assert second_page.width > 100
            assert second_page.height > 100
    assert all(not path.exists() for path in transports)


def test_login_rejects_non_https_auth_url(monkeypatch) -> None:
    client = CodexAppServerClient()
    monkeypatch.setattr(client, "_ensure_started", lambda: None)
    monkeypatch.setattr(client, "_account", lambda: None)
    monkeypatch.setattr(
        client,
        "_rpc",
        lambda _method, _params, _timeout: {
            "type": "chatgpt",
            "authUrl": "http://example.invalid/sign-in",
        },
    )

    with pytest.raises(CodexProtocolError) as exc_info:
        client.login()
    assert getattr(exc_info.value, "code", None) == "CODEX_PROTOCOL_ERROR"


class _FakeRecognitionClient(CodexAppServerClient):
    def __init__(
        self,
        *,
        reroute: bool = False,
        forbidden_event: dict[str, Any] | None = None,
        instruction_sources: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.reroute = reroute
        self.forbidden_event = forbidden_event
        self.instruction_sources = instruction_sources
        self.requests: list[tuple[str, dict[str, Any] | None]] = []
        self.writes: list[dict[str, Any]] = []

    def _require_ready(self) -> None:
        return None

    def _write(self, message: dict[str, Any]) -> None:
        self.writes.append(message)

    def _rpc(self, method: str, params: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
        self.requests.append((method, params))
        if method == "thread/start":
            return {
                "thread": {
                    "id": "thread-finance-test",
                    "instructionSources": self.instruction_sources or [],
                }
            }
        if method == "turn/start":
            assert params is not None
            if self.forbidden_event:
                event = {
                    **self.forbidden_event,
                    "params": {
                        "threadId": "thread-finance-test",
                        "turnId": "turn-finance-test",
                        **(self.forbidden_event.get("params") or {}),
                    },
                }
                self._notifications.put(event)
            elif self.reroute:
                self._notifications.put(
                    {
                        "method": "model/rerouted",
                        "params": {
                            "threadId": "thread-finance-test",
                            "turnId": "turn-finance-test",
                            "fromModel": FIXED_AI_MODEL,
                            "toModel": "gpt-5.6-terra",
                        },
                    }
                )
            else:
                self._notifications.put(
                    {
                        "method": "item/completed",
                        "params": {
                            "threadId": "thread-finance-test",
                            "turnId": "turn-finance-test",
                            "item": {
                                "type": "agentMessage",
                                "text": json.dumps(AI_VALUES),
                            },
                        },
                    }
                )
            self._notifications.put(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "thread-finance-test",
                        "turnId": "turn-finance-test",
                        "turn": {
                            "id": "turn-finance-test",
                            "status": "completed",
                            "items": [],
                        },
                    },
                }
            )
            return {"turn": {"id": "turn-finance-test"}}
        if method in {"turn/interrupt", "thread/delete"}:
            return {}
        raise AssertionError(f"unexpected method: {method}")


def test_recognition_uses_local_image_schema_and_fixed_sol_only(tmp_path: Path) -> None:
    image = tmp_path / "statement.jpg"
    _write_test_jpeg(image)
    client = _FakeRecognitionClient()

    assert client.recognize_statement(image)["account_number"] == "24681357"
    thread_params = next(params for method, params in client.requests if method == "thread/start")
    turn_params = next(params for method, params in client.requests if method == "turn/start")
    assert thread_params and thread_params["model"] == FIXED_AI_MODEL
    assert thread_params["sandbox"] == "read-only"
    assert turn_params and turn_params["model"] == FIXED_AI_MODEL
    assert turn_params["effort"] == "low"
    assert turn_params["sandboxPolicy"] == {"type": "readOnly"}
    assert turn_params["outputSchema"]["additionalProperties"] is False
    transported_path = Path(turn_params["input"][1]["path"])
    assert turn_params["input"][1]["type"] == "localImage"
    assert transported_path.suffix == ".png"
    assert transported_path != image.resolve()
    assert not transported_path.exists()
    expected_cwd = str(get_settings().codex_recognition_workspace.resolve())
    assert thread_params["cwd"] == expected_cwd
    assert turn_params["cwd"] == expected_cwd
    assert [params["model"] for method, params in client.requests if method in {"thread/start", "turn/start"}] == [
        FIXED_AI_MODEL,
        FIXED_AI_MODEL,
    ]
    assert any(method == "thread/delete" for method, _params in client.requests)


def test_pdf_recognition_sends_every_page_as_local_image(tmp_path: Path) -> None:
    source = tmp_path / "statement.pdf"
    with fitz.open() as document:
        document.new_page()
        document.new_page()
        document.save(source)
    client = _FakeRecognitionClient()

    client.recognize_statement(source)

    turn_params = next(params for method, params in client.requests if method == "turn/start")
    assert turn_params is not None
    local_images = [item for item in turn_params["input"] if item["type"] == "localImage"]
    assert len(local_images) == 2
    assert all(Path(item["path"]).suffix == ".png" for item in local_images)
    assert all(not Path(item["path"]).exists() for item in local_images)


def test_model_reroute_is_rejected_without_fallback(tmp_path: Path) -> None:
    image = tmp_path / "statement.jpg"
    _write_test_jpeg(image)
    client = _FakeRecognitionClient(reroute=True)

    with pytest.raises(CodexModelReroutedError):
        client.recognize_statement(image)
    assert not any(
        params and params.get("model") != FIXED_AI_MODEL
        for method, params in client.requests
        if method in {"thread/start", "turn/start"}
    )
    assert any(method == "turn/interrupt" for method, _params in client.requests)
    assert any(method == "thread/delete" for method, _params in client.requests)


def test_loaded_project_instruction_source_rejects_turn_and_deletes_exact_thread(
    tmp_path: Path,
) -> None:
    image = tmp_path / "statement.jpg"
    _write_test_jpeg(image)
    client = _FakeRecognitionClient(instruction_sources=["D:/private/AGENTS.md"])

    with pytest.raises(CodexUnsafeConfigurationError):
        client.recognize_statement(image)

    assert not any(method == "turn/start" for method, _params in client.requests)
    assert (
        "thread/delete",
        {"threadId": "thread-finance-test"},
    ) in client.requests


@pytest.mark.parametrize(
    "method",
    [
        "item/tool/call",
        "item/tool/requestUserInput",
        "item/commandExecution/requestApproval",
        "future/toolCapability/start",
        "future/requestApprovalV2",
    ],
)
def test_server_tool_or_approval_request_is_rejected_immediately(
    tmp_path: Path, method: str
) -> None:
    image = tmp_path / "statement.jpg"
    _write_test_jpeg(image)
    client = _FakeRecognitionClient(
        forbidden_event={"id": 81, "method": method, "params": {}}
    )

    with pytest.raises(CodexRecognitionError):
        client.recognize_statement(image)

    assert client.writes == [
        {
            "id": 81,
            "error": {
                "code": -32601,
                "message": "This extraction client does not allow tools or approvals",
            },
        }
    ]
    assert any(method == "turn/interrupt" for method, _params in client.requests)
    assert any(method == "thread/delete" for method, _params in client.requests)


@pytest.mark.parametrize(
    "item_type",
    [
        "commandExecution",
        "fileChange",
        "mcpToolCall",
        "dynamicToolCall",
        "collabToolCall",
        "webSearch",
    ],
)
def test_forbidden_item_started_is_interrupted_before_completion(
    tmp_path: Path, item_type: str
) -> None:
    image = tmp_path / "statement.jpg"
    _write_test_jpeg(image)
    client = _FakeRecognitionClient(
        forbidden_event={
            "method": "item/started",
            "params": {"item": {"type": item_type}},
        }
    )

    with pytest.raises(CodexRecognitionError):
        client.recognize_statement(image)

    assert client.writes == []
    assert any(method == "turn/interrupt" for method, _params in client.requests)
    assert any(method == "thread/delete" for method, _params in client.requests)
