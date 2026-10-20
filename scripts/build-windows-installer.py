"""Wrap a validated blank Windows runtime in a per-user NSIS installer."""
import argparse
import json
import re
import subprocess
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--runtime", type=Path, required=True)
p.add_argument("--compiler", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
args = p.parse_args()
root = Path(__file__).resolve().parents[1]
runtime = args.runtime.resolve()
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=True)
version = re.search(r'APP_VERSION = "([^"]+)"', (root / "backend/app/config.py").read_text()).group(1)
files = sorted(p for p in runtime.rglob("*") if p.is_file())
assert files and (runtime / "FinancialFeeSystem.exe").is_file()
for path in files:
    name = path.relative_to(runtime).as_posix().lower()
    assert not path.is_symlink()
    assert not name.endswith((".sqlite3", ".sqlite", ".sqlite3-wal", ".sqlite3-shm")), name
    assert path.name.lower() not in {"auth.json", "financial_system_config.json", "config.toml"}, name
    assert not any(x in path.relative_to(runtime).parts for x in ("data", "test-data", "login", "test-login", "backups")), name


def quoted(text):
    return str(text).replace("$", "$$").replace('"', '$\\"')


install = []
uninstall = []
for path in files:
    rel = path.relative_to(runtime)
    install += [f'SetOutPath "$INSTDIR\\{quoted(rel.parent)}"',
                f'File "{quoted(path)}"']
    uninstall.append(f'Delete "$INSTDIR\\{quoted(rel)}"')
dirs = sorted({parent for p in files for parent in p.parents if parent.is_relative_to(runtime)},
              key=lambda x: len(x.parts), reverse=True)
for directory in dirs:
    if directory != runtime:
        uninstall.append(f'RMDir "$INSTDIR\\{quoted(directory.relative_to(runtime))}"')
nsi = (root / "packaging/windows-installer.nsi").read_text(encoding="utf-8")
nsi = nsi.replace("@VERSION@", version).replace("@OUTPUT@", quoted(output / f"FinancialFeeSystem-{version}-Windows-Setup.exe"))
nsi = nsi.replace("@INSTALL@", "\n".join(install)).replace("@UNINSTALL@", "\n".join(uninstall))
source = output / "installer.nsi"
source.write_text(nsi, encoding="utf-8-sig")
subprocess.run([str(args.compiler.resolve()), "/V2", str(source)], check=True)
(output / "installer-files.json").write_text(json.dumps([p.relative_to(runtime).as_posix() for p in files], indent=2))
