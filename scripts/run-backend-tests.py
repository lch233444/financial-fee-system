"""Run backup tests first, then isolated modules with short temporary paths."""
import argparse
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--work", type=Path, required=True)
args = p.parse_args()
root = Path(__file__).resolve().parents[1]
work = args.work.resolve()
assert str(work).isascii() and len(str(work)) < 45, work
work.mkdir(parents=True, exist_ok=True)
env = dict(os.environ, TEMP=str(work), TMP=str(work), TMPDIR=str(work),
           OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", GOMAXPROCS="2", PYTHONUTF8="1")
# Include the longest backup staging components plus a conservative test suffix.
deep = work / ("financial-fee-system-tests-" + "x" * 12) / "tmp" / ("restore_apply_" + "x" * 32) / "data" / "attachments" / ("x" * 64 + ".pdf")
assert len(str(deep)) < 240, deep
deep.parent.mkdir(parents=True)
deep.write_bytes(b"path-preflight")
deep.unlink()
tests = root / "backend/tests"
ordered = [tests / "test_system_backup.py"] + sorted(t for t in tests.glob("test_*.py") if t.name != "test_system_backup.py")
failed = []
for i, test in enumerate(ordered):
    log = work / f"{test.stem}.log"
    with log.open("w", encoding="utf-8") as out:
        result = subprocess.run([sys.executable, "-m", "pytest", str(test), "-q", "-p", "no:cacheprovider",
                                 "--basetemp", str(work / f"p{i}"), "--junitxml", str(work / f"{test.stem}.xml")],
                                cwd=root, env=env, stdout=out, stderr=subprocess.STDOUT)
    print(test.name, result.returncode, flush=True)
    if result.returncode:
        failed.append(test.name)
        print(log.read_text(encoding="utf-8"), flush=True)
        if i == 0:
            break
if failed:
    raise SystemExit(f"Failed: {failed}")
totals = {key: 0 for key in ("tests", "failures", "errors", "skipped")}
for path in work.glob("test_*.xml"):
    for suite in ET.parse(path).getroot().iter("testsuite"):
        for key in totals:
            totals[key] += int(suite.attrib.get(key, 0))
print(totals)
