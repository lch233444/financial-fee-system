"""Opt-in live acceptance check for the fixed Luna statement recognizer.

This script sends the selected image to OpenAI through the current Windows
user's ChatGPT-managed Codex login. It never accepts an API key and prints no
account email or authentication material.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.services.codex_app_server import (  # noqa: E402
    FIXED_AI_MODEL,
    CodexAppServerClient,
    compare_ocr_and_ai,
)
from app.services.statement_parser import parse_empf_statement  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("statement", type=Path)
    parser.add_argument(
        "--consent-cloud-upload",
        action="store_true",
        help="Confirm that this statement may be sent to OpenAI for the test.",
    )
    parser.add_argument(
        "--controls-only",
        action="store_true",
        help="Check login and extraction-only controls without sending the image.",
    )
    args = parser.parse_args()
    if not args.controls_only and not args.consent_cloud_upload:
        parser.error("需要显式提供 --consent-cloud-upload")
    source = args.statement.resolve()
    if not source.is_file():
        parser.error(f"文件不存在：{source}")

    client = CodexAppServerClient()
    try:
        status = client.status()
        public_status = {
            "status": status.get("status"),
            "authenticated": status.get("authenticated"),
            "model": status.get("model"),
            "model_available": status.get("model_available"),
            "message": status.get("message"),
        }
        print(json.dumps({"assistant": public_status}, ensure_ascii=False))
        if status.get("status") != "ready":
            return 2

        config_result = client._rpc("config/read", {"includeLayers": False}, 10)  # noqa: SLF001
        effective = config_result.get("config") if isinstance(config_result.get("config"), dict) else {}
        features = effective.get("features") if isinstance(effective.get("features"), dict) else {}
        apps = effective.get("apps") if isinstance(effective.get("apps"), dict) else {}
        apps_default = apps.get("_default") if isinstance(apps.get("_default"), dict) else {}
        mcp_servers = effective.get("mcp_servers") if isinstance(effective.get("mcp_servers"), dict) else {}
        hooks = effective.get("hooks") if isinstance(effective.get("hooks"), dict) else {}
        plugins = effective.get("plugins") if isinstance(effective.get("plugins"), dict) else {}
        extraction_controls = {
            "web_search": effective.get("web_search"),
            "apps_default_enabled": apps_default.get("enabled"),
            "agents_enabled": (effective.get("agents") or {}).get("enabled")
            if isinstance(effective.get("agents"), dict)
            else None,
            "shell_tool_enabled": features.get("shell_tool"),
            "unified_exec_enabled": features.get("unified_exec"),
            "configured_mcp_server_count": len(mcp_servers),
            "active_mcp_server_count": sum(
                1 for value in mcp_servers.values()
                if not isinstance(value, dict) or value.get("enabled") is not False
            ),
            "configured_hook_event_count": len(hooks),
            "active_hook_event_count": sum(1 for value in hooks.values() if value),
            "configured_plugin_count": len(plugins),
            "active_plugin_count": sum(
                1 for value in plugins.values()
                if not isinstance(value, dict) or value.get("enabled") is not False
            ),
            "project_doc_max_bytes": effective.get("project_doc_max_bytes"),
            "project_doc_fallback_filenames": effective.get("project_doc_fallback_filenames"),
        }
        print(json.dumps({"extraction_controls": extraction_controls}, ensure_ascii=False))
        if args.controls_only:
            return 0

        local_values = parse_empf_statement(source).extracted_dict()
        ai_values = client.recognize_statement(source)
        comparison = compare_ocr_and_ai(local_values, ai_values)
        result = {
            "model": FIXED_AI_MODEL,
            "comparison_status": comparison["status"],
            "conflicts": comparison["conflicts"],
            "uncorroborated": comparison["uncorroborated"],
            "ai_values": ai_values,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if comparison["status"] == "AGREED" else 3
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
