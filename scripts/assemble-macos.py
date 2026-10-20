"""Create one DMG containing both native runtimes and a universal launcher."""
import argparse
import plistlib
import re
import shutil
import subprocess
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--runtimes", required=True, type=Path)
p.add_argument("--output", required=True, type=Path)
args = p.parse_args()
root = Path(__file__).resolve().parents[1]
version = re.search(r'APP_VERSION = "([^"]+)"', (root / "backend/app/config.py").read_text()).group(1)
stage = args.output / "dmg"
bundle = stage / "金融计划收费系统.app"
contents = bundle / "Contents"
(contents / "MacOS").mkdir(parents=True)
for arch in ("arm64", "x86_64"):
    shutil.copytree(args.runtimes / arch / "FinancialFeeSystem",
                    contents / "Resources" / f"runtime-{arch}", symlinks=True)
launcher = contents / "MacOS/FinancialFeeSystem"
subprocess.run(["clang", "-arch", "arm64", "-arch", "x86_64", "-mmacosx-version-min=15.0",
                str(root / "packaging/macos-launcher.c"), "-o", str(launcher)], check=True)
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
shutil.copyfile(root / "packaging/Mac安装说明.txt", stage / "安装说明.txt")
subprocess.run(["hdiutil", "create", "-volname", "金融计划收费系统", "-srcfolder", str(stage),
               "-ov", "-format", "UDZO", str(args.output / f"FinancialFeeSystem-{version}-Mac.dmg")], check=True)
