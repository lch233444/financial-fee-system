"""Build one native runtime. Assemble the two architectures in a separate job."""
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
arch = platform.machine()
assert sys.platform == "darwin" and arch in ("arm64", "x86_64")
out = ROOT / "release"
brew = Path(subprocess.check_output(["brew", "--prefix"], text=True).strip())
args = [
    sys.executable, "-m", "PyInstaller", "--onedir", "--name", "FinancialFeeSystem",
    "--distpath", str(out), "--workpath", str(out / "build"), "--specpath", str(out),
    "--paths", str(ROOT / "backend"), "--collect-submodules", "uvicorn",
    "--collect-data", "reportlab", "--collect-data", "alembic",
]
for source, destination in [
    (ROOT / "frontend/dist", "frontend/dist"), (ROOT / "新收费计划计算纯净版模板.xlsx", "."),
    (ROOT / "backend/alembic.ini", "."), (ROOT / "backend/alembic", "alembic"),
    (ROOT / "fonts", "fonts"), (brew / "share/tessdata/eng.traineddata", "Tesseract-OCR/tessdata"),
]:
    args.extend(["--add-data", f"{source}:{destination}"])
args.extend(["--add-binary", f"{brew / 'bin/tesseract'}:Tesseract-OCR",
             str(ROOT / "backend/run_financial_system.py")])
subprocess.run(args, check=True)
runtime = out / "FinancialFeeSystem"
shutil.copytree(ROOT / "tools/Codex", runtime / "Codex")
shutil.copytree(ROOT / "docs", runtime / "docs")
# Reject forgotten absolute Homebrew/library paths in every bundled Mach-O.
checked = 0
for path in runtime.rglob("*"):
    if not path.is_file() or path.is_symlink():
        continue
    description = subprocess.check_output(["file", "-b", str(path)], text=True)
    if "Mach-O" not in description:
        continue
    checked += 1
    libraries = subprocess.check_output(["otool", "-L", str(path)], text=True).splitlines()[1:]
    for line in libraries:
        dep = line.strip().split(" (", 1)[0]
        assert not dep.startswith(("/opt/homebrew/", "/usr/local/", "/Library/Frameworks/")), (path, dep)
    subprocess.run(["codesign", "--verify", str(path)], check=True, capture_output=True)
(out / "native-build.json").write_text(json.dumps({
    "architecture": arch, "macos": platform.mac_ver()[0], "python": sys.version,
    "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "checked_native_files": checked,
    "tesseract": subprocess.check_output([str(runtime / "_internal/Tesseract-OCR/tesseract"), "--version"], text=True),
}, indent=2))
