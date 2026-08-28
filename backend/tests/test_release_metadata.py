from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path

from app.config import APP_VERSION


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_TEMPLATE_SHA256 = "16A1197C6C2C843F58F766EC42041439D063FBBD4F6FF6D9002A947035871145"
EXPECTED_TESSERACT_SHA256 = "C66F0F12ED76F6AA455DAC97684BBC86756D6A732380BEE09122454CFDA3F420"


def test_release_version_metadata_matches_application_version() -> None:
    version_parts = tuple(int(part) for part in APP_VERSION.split("."))
    assert len(version_parts) == 3

    frontend_package = json.loads(
        (PROJECT_ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
    )
    assert frontend_package["version"] == APP_VERSION

    version_info = (PROJECT_ROOT / "packaging" / "version_info.txt").read_text(
        encoding="utf-8"
    )
    expected_tuple = (*version_parts, 0)
    for field in ("filevers", "prodvers"):
        match = re.search(rf"{field}=\(([^)]+)\)", version_info)
        assert match is not None
        actual_tuple = tuple(int(value.strip()) for value in match.group(1).split(","))
        assert actual_tuple == expected_tuple
    assert f"StringStruct('FileVersion', '{APP_VERSION}.0')" in version_info
    assert f"StringStruct('ProductVersion', '{APP_VERSION}')" in version_info

    build_script = (PROJECT_ROOT / "scripts" / "build-windows.ps1").read_text(
        encoding="utf-8"
    )
    assert "Windows ProductVersion：$AppVersion" in build_script
    assert "Windows FileVersion：$ExpectedFileVersion" in build_script
    assert EXPECTED_TEMPLATE_SHA256 in build_script
    assert EXPECTED_TESSERACT_SHA256 in build_script

    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    project_manual = (PROJECT_ROOT / "docs" / "项目说明书.md").read_text(encoding="utf-8")
    release_notes = (PROJECT_ROOT / "packaging" / "发布说明.txt").read_text(encoding="utf-8")
    build_manifest = (PROJECT_ROOT / "packaging" / "构建清单.txt").read_text(encoding="utf-8")
    assert f"当前源码版本为 **{APP_VERSION}**" in readme
    assert f"> 当前源码版本：`{APP_VERSION}`" in project_manual
    assert release_notes.startswith(f"金融计划收费计算系统 MVP {APP_VERSION}\n")
    assert build_manifest.startswith(
        f"金融计划收费计算系统 MVP {APP_VERSION} Windows运行版构建清单\n"
    )

    template_digest = hashlib.sha256(
        (PROJECT_ROOT / "新收费计划计算纯净版模板.xlsx").read_bytes()
    ).hexdigest().upper()
    assert template_digest == EXPECTED_TEMPLATE_SHA256
