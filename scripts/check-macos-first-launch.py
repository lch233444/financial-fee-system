"""Verify Finder-style default state locations with an isolated HOME."""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

p = argparse.ArgumentParser()
p.add_argument("--app", required=True, type=Path)
p.add_argument("--work", required=True, type=Path)
args = p.parse_args()
assert sys.platform == "darwin"
work, bundle = args.work.resolve(), args.app.resolve()
assert not work.exists()
profile = work / "user"
profile.mkdir(parents=True)
state = profile / "Library/Application Support/FinancialFeeSystem"
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
env = {k: v for k, v in os.environ.items() if not k.startswith("FINANCIAL_")}
env.update(HOME=str(profile), FINANCIAL_PORT=str(port), FINANCIAL_HOST="127.0.0.1",
           FINANCIAL_NO_BROWSER="1", PATH="/usr/bin:/bin:/usr/sbin:/sbin")
with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=10,
                  headers={"X-Financial-System-Request": "1"}) as client:
    for number in (1, 2):
        with (work / f"launch-{number}.log").open("w") as log:
            process = subprocess.Popen([str(bundle / "Contents/MacOS/FinancialFeeSystem")],
                                       env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                for _ in range(100):
                    assert process.poll() is None, "Default app launch exited early"
                    try:
                        client.get("/api/health", timeout=1).raise_for_status()
                        break
                    except httpx.TransportError:
                        time.sleep(.2)
                else:
                    raise RuntimeError("Default app launch timeout")
                info = client.get("/api/system-info").raise_for_status().json()
                assert Path(info["data_root"]) == state / "data"
                assert Path(json.loads((state / "config.json").read_text())["data_root"]) == state / "data"
                assert (state / "LunaCodexHome").is_dir()
                assert (state / "data/database/financial_system.sqlite3").is_file()
                assert client.get("/api/clients").raise_for_status().json() == []
                client.post("/api/shutdown").raise_for_status()
                assert process.wait(timeout=30) == 0
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=15)
subprocess.run(["codesign", "--verify", str(bundle)], check=True)
(work / "first-launch.json").write_text(json.dumps({
    "status": "passed", "default_location": str(state), "launches": 2,
    "app_bundle_unchanged": True, "blank": True, "separate_login": True,
}, indent=2))
