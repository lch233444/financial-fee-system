"""Install and exercise the actual NSIS payload without machine integration."""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--installer", type=Path, required=True)
p.add_argument("--runtime", type=Path, required=True)
p.add_argument("--work", type=Path, required=True)
args = p.parse_args()
assert os.name == "nt"
work = args.work.resolve()
assert str(work).isascii() and not work.exists()
work.mkdir(parents=True)
(work / "tmp").mkdir()
install = work / "installed"
env = dict(os.environ, TEMP=str(work / "tmp"), TMP=str(work / "tmp"), PYTHONUTF8="1")
subprocess.run([str(args.installer.resolve()), "/S", "/NOINTEGRATION", f"/D={install}"],
               env=env, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
payload_files = sorted(p for p in args.runtime.resolve().rglob("*") if p.is_file())
for source in payload_files:
    relative = source.relative_to(args.runtime.resolve())
    actual = install / relative
    assert actual.is_file(), relative
    with source.open("rb") as expected_stream, actual.open("rb") as actual_stream:
        assert hashlib.file_digest(expected_stream, "sha256").digest() == hashlib.file_digest(actual_stream, "sha256").digest(), relative
subprocess.run([sys.executable, str(Path(__file__).with_name("check-distribution.py")),
                "--executable", str(install / "FinancialFeeSystem.exe"), "--work", str(work / "accept")],
               env=env, check=True)
extra = install / "user-data/keep.txt"
extra.parent.mkdir()
extra.write_text("SYNTHETIC USER DATA MUST SURVIVE UNINSTALL")
config = install / "financial_system_config.json"
config.write_text(json.dumps({"data_root": str(extra.parent)}))
subprocess.run([str(install / "卸载程序.exe"), "/S"], env=env, check=True,
               creationflags=subprocess.CREATE_NO_WINDOW)
deadline = time.monotonic() + 60
while (install / "FinancialFeeSystem.exe").exists() or (install / "卸载程序.exe").exists():
    assert time.monotonic() < deadline, "Uninstaller did not remove program"
    time.sleep(.25)
remaining_programs = [p.relative_to(install).as_posix() for p in install.rglob("*")
                      if p.is_file() and p not in (extra, config)]
assert not remaining_programs, remaining_programs
assert extra.read_text() == "SYNTHETIC USER DATA MUST SURVIVE UNINSTALL"
assert config.is_file()
(work / "installation.json").write_text(json.dumps({
    "status": "passed", "payload_files_verified": len(payload_files),
    "installed_application_acceptance": True, "uninstalled": True,
    "user_files_preserved": True, "integration": "disabled for isolated acceptance",
}, indent=2))
