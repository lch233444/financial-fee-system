"""Create a DMG for one Mac architecture from its verified native runtime."""
import argparse
import json
import plistlib
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--runtimes", required=True, type=Path)
p.add_argument("--archives", type=Path)
p.add_argument("--output", required=True, type=Path)
p.add_argument("--arch", required=True, choices=("arm64", "x86_64"))
args = p.parse_args()
root = Path(__file__).resolve().parents[1]
version = re.search(r'APP_VERSION = "([^"]+)"', (root / "backend/app/config.py").read_text()).group(1)
stage = args.output / f"dmg-{args.arch}"
bundle = stage / "金融计划收费系统.app"
contents = bundle / "Contents"
(contents / "MacOS").mkdir(parents=True)
for arch in (args.arch,):
    if args.archives:
        destination = args.runtimes / arch
        destination.mkdir(parents=True)
        with tarfile.open(args.archives / f"runtime-{arch}.tar.gz") as archive:
            archive.extractall(destination, filter="data")
    metadata = json.loads((args.runtimes / arch / "native-build.json").read_text())
    assert metadata["architecture"] == arch
    assert re.fullmatch(r"[0-9a-f]{40}", metadata["commit"])
    # A packaging/documentation change may reuse native artifacts only when every
    # runtime source, asset pin, dependency and native acceptance input matches.
    subprocess.run([
        "git", "diff", "--exit-code", metadata["commit"], "HEAD", "--",
        "backend", "frontend", "packaging/macos-assets.json", "packaging/licenses",
        "scripts/build-macos.py", "scripts/prepare-macos-assets.py",
        "scripts/check-distribution.py", "scripts/run-backend-tests.py",
        "新收费计划计算纯净版模板.xlsx",
    ], cwd=root, check=True)
    shutil.copytree(args.runtimes / arch / "FinancialFeeSystem",
                    contents / "Resources" / f"runtime-{arch}", symlinks=True)
    for path in (contents / "Resources" / f"runtime-{arch}").rglob("*"):
        assert path.name.lower() not in {
            "auth.json", "financial_system_config.json", "config.toml", ".codex",
            "lunacodexhome", "backups", "attachments", "statement_imports",
        }, path
        assert not path.name.lower().endswith((".sqlite", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm")), path
    # Native code is immutable; replace only the existing Markdown documents.
    docs = contents / "Resources" / f"runtime-{arch}" / "docs"
    for document in (root / "docs").rglob("*"):
        if document.is_file():
            target = docs / document.relative_to(root / "docs")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(document, target)
    runtime = contents / "Resources" / f"runtime-{arch}"
    for document in ("README.md", "AGENTS.md"):
        shutil.copyfile(root / document, runtime / document)
    for document in ("发布说明.txt", "构建清单.txt"):
        shutil.copyfile(root / "packaging" / document, runtime / document)
launcher = contents / "MacOS/FinancialFeeSystem"
subprocess.run(["clang", "-arch", args.arch, "-mmacosx-version-min=15.0",
                str(root / "packaging/macos-launcher.c"), "-o", str(launcher)], check=True)
assert subprocess.check_output(["lipo", "-archs", str(launcher)], text=True).strip() == args.arch
assert sorted(p.name for p in (contents / "Resources").iterdir()) == [f"runtime-{args.arch}"]
info = {
    "CFBundleExecutable": "FinancialFeeSystem", "CFBundleName": "金融计划收费系统",
    "CFBundleDisplayName": "金融计划收费系统", "CFBundleIdentifier": "com.financialfeesystem.desktop",
    "CFBundlePackageType": "APPL", "CFBundleShortVersionString": version,
    "CFBundleVersion": version, "LSMinimumSystemVersion": "15.0",
    "NSHighResolutionCapable": True,
}
(contents / "Info.plist").write_bytes(plistlib.dumps(info))
# Ad-hoc integrity signature only; no claim of Developer ID signing/notarization.
subprocess.run(["codesign", "--force", "--sign", "-", str(bundle)], check=True)
subprocess.run(["codesign", "--verify", str(bundle)], check=True)
(stage / "Applications").symlink_to("/Applications")
label = "Apple芯片（arm64）" if args.arch == "arm64" else "Intel处理器（x86_64）"
instructions = (root / "packaging/Mac安装说明.txt").read_text(encoding="utf-8")
assert "{{ARCH_LABEL}}" in instructions
(stage / "安装说明.txt").write_text(instructions.replace("{{ARCH_LABEL}}", label), encoding="utf-8")
subprocess.run(["hdiutil", "create", "-volname", "金融计划收费系统", "-srcfolder", str(stage),
               "-ov", "-format", "UDZO", str(args.output / f"FinancialFeeSystem-{version}-Mac-{args.arch}.dmg")], check=True)
