"""Acceptance of an actual frozen runtime using only generated test records."""
import argparse
from contextlib import closing
import io
import json
import os
import platform
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
from PIL import Image, ImageDraw
from pypdf import PdfReader

p = argparse.ArgumentParser()
p.add_argument("--executable", type=Path, required=True)
p.add_argument("--work", type=Path, required=True)
args = p.parse_args()
exe, work = args.executable.resolve(), args.work.resolve()
assert not work.exists(), "Acceptance requires a fresh synthetic directory"
work.mkdir(parents=True)
(work / "tmp").mkdir()
root = Path(__file__).resolve().parents[1]
os.environ.update(FINANCIAL_DATA_ROOT=str(work / "helper-data"), FINANCIAL_CODEX_HOME=str(work / "helper-login"))
sys.path[:0] = [str(root / "backend"), str(root / "backend/tests")]
from app.config import APP_VERSION
from test_invoice_aggregation import _group, _finalized_settlement, _draft

with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
env = {k: v for k, v in os.environ.items() if not k.startswith("FINANCIAL_")}
env.update(FINANCIAL_DATA_ROOT=str(work / "data"), FINANCIAL_CODEX_HOME=str(work / "login"),
           FINANCIAL_OCR_CACHE_ROOT=str(work / "ocr-cache"),
           FINANCIAL_HOST="127.0.0.1", FINANCIAL_PORT=str(port), FINANCIAL_NO_BROWSER="1",
           TEMP=str(work / "tmp"), TMP=str(work / "tmp"), TMPDIR=str(work / "tmp"), PYTHONUTF8="1")
if sys.platform == "darwin":
    env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
client = httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=120,
                      headers={"X-Financial-System-Request": "1"})
process = None
log = (work / "app.log").open("w", encoding="utf-8")
report = {"version": APP_VERSION, "platform": sys.platform, "status": "failed", "checks": []}


def start():
    global process
    process = subprocess.Popen([str(exe)], cwd=exe.parent, env=env, stdout=log, stderr=subprocess.STDOUT,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for _ in range(200):
        if process.poll() is not None:
            raise RuntimeError(f"App exited: {process.returncode}")
        try:
            health = client.get("/api/health", timeout=1).raise_for_status().json()
            assert health["version"] == APP_VERSION and health["local_only"]
            state = client.get("/api/datasets").raise_for_status().json()
            client.headers["X-Financial-Dataset"] = state["token"]
            return
        except httpx.TransportError:
            time.sleep(.2)
    raise RuntimeError("App startup timeout")


def stop():
    client.post("/api/shutdown").raise_for_status()
    process.wait(timeout=30)
    assert process.returncode == 0


try:
    start()
    info = client.get("/api/system-info").raise_for_status().json()
    assert Path(info["data_root"]).resolve() == work / "data" and info["template_exists"]
    for name in ["companies", "fcs", "platforms", "fee-plans", "clients", "accounts",
                 "settlements", "invoices", "statement-imports", "attachments"]:
        assert client.get(f"/api/{name}").raise_for_status().json() == [], name
    assert client.get("/api/openapi.json").raise_for_status().json()["paths"]
    assert "<html" in client.get("/").raise_for_status().text.lower()
    report["checks"].append("blank/startup/static/template/loopback")
    ai = client.get("/api/ai-assistant/status").raise_for_status().json()
    assert ai["available"] and ai["status"] == "signed_out", ai
    report["checks"].append("bundled-Codex-app-server/signed-out")
    data = _group(client, "DIST", platform_count=1, company_name="香港合成验收公司")
    settlement = _finalized_settlement(client, data, 0, year=2026, quarter=1, closing="900.00")
    draft = _draft(client, data, year=2026, quarter=1).raise_for_status().json()
    issued = client.post(f"/api/invoices/{draft['id']}/issue",
                         json={"issue_date": "2026-04-05", "language": "zh"}).raise_for_status().json()
    assert issued["payment_status"] == "NOT_REQUIRED" and issued["amount"] == "0.00"
    for lang in ("zh", "en"):
        pdf = client.post(f"/api/invoices/{issued['id']}/pdf?language={lang}").raise_for_status().content
        (work / f"notice-{lang}.pdf").write_bytes(pdf)
        reader = PdfReader(io.BytesIO(pdf))
        text = "\n".join(page.extract_text() for page in reader.pages)
        assert "香港合成验收公司" in text and "HKD 0.00" in text
        assert ("無需付款" if lang == "zh" else "No payment") in text
        if sys.platform == "darwin":
            font_names = {
                str(font.get_object()["/BaseFont"])
                for page in reader.pages
                for font in page["/Resources"]["/Font"].get_object().values()
            }
            assert any(name.endswith("NotoSansSC-Regular") for name in font_names), font_names
            assert any(name.endswith("NotoSansSC-Bold") for name in font_names), font_names
    report["checks"].append("calculate/finalize/zero-invoice/bilingual-PDF")
    picture = Image.new("RGB", (1200, 450), "white")
    draw = ImageDraw.Draw(picture)
    for n, text in enumerate(["SYNTHETIC TEST DOCUMENT", "Account: TEST123456", "Balance: HKD 900.00"]):
        draw.text((40, 40 + 95 * n), text, fill="black", font_size=45)
    png = work / "ocr.png"
    picture.save(png)
    runtime = exe.parent
    if exe.parent.name == "MacOS" and exe.parent.parent.name == "Contents":
        runtime = exe.parent.parent / "Resources" / f"runtime-{platform.machine()}"
    ocr = runtime / ("Tesseract-OCR/tesseract.exe" if os.name == "nt" else "_internal/Tesseract-OCR/tesseract")
    if os.name == "nt":
        from app.services.ocr_runtime import prepare_windows_runtime
        ocr = prepare_windows_runtime(ocr, work / "ocr-cache")
    ocr_env = dict(env, TESSDATA_PREFIX=str(ocr.parent / "tessdata"))
    recognized = subprocess.check_output([str(ocr), str(png), "stdout"], env=ocr_env,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), text=True)
    assert "TEST123456" in recognized and "900.00" in recognized
    imported = client.post("/api/statement-imports",
                           files={"file": ("synthetic.png", png.read_bytes(), "image/png")}).raise_for_status().json()
    assert imported["id"]
    with closing(sqlite3.connect(work / "data/database/financial_system.sqlite3")) as db:
        raw = db.execute("SELECT raw_text FROM statement_imports WHERE id = ?", (imported["id"],)).fetchone()[0]
    assert "TEST123456" in raw and "900.00" in raw
    report["checks"].append("native-OCR/generated-image/import")
    backup = client.post("/api/backups?year=2026&quarter=1").raise_for_status().content
    assert backup.startswith(b"PK")
    restored = client.post("/api/backups/restore",
                           files={"file": ("synthetic.zip", backup, "application/zip")}).raise_for_status().json()
    assert restored["staged"] and restored["restart_required"]
    process.wait(timeout=30)
    start()
    invoices = client.get("/api/invoices").raise_for_status().json()
    assert len(invoices) == 1 and invoices[0]["invoice_number"] == issued["invoice_number"]
    report["checks"].append("backup/restore/restart/persistence")
    original_token = client.headers["X-Financial-Dataset"]
    other = client.post("/api/datasets", json={"name": "Independent synthetic B"}).raise_for_status().json()
    state = client.post("/api/datasets/switch", json={"id": other["id"]}).raise_for_status().json()
    client.headers["X-Financial-Dataset"] = state["token"]
    for name in ["clients", "accounts", "settlements", "invoices", "attachments", "statement-imports"]:
        assert client.get(f"/api/{name}").raise_for_status().json() == [], name
    assert client.get("/api/invoices", headers={"X-Financial-Dataset": original_token}).status_code == 409
    customer = client.post("/api/clients", json={"name": "Independent B customer"}).raise_for_status().json()
    package_b = client.post("/api/backups").raise_for_status().content
    clone = client.post("/api/datasets/import", data={"name": "Independent clone C"},
                        files={"file": ("b.zip", package_b, "application/zip")}).raise_for_status().json()
    state = client.post("/api/datasets/switch", json={"id": "primary"}).raise_for_status().json()
    client.headers["X-Financial-Dataset"] = state["token"]
    assert client.get("/api/invoices").raise_for_status().json()[0]["invoice_number"] == issued["invoice_number"]
    state = client.post("/api/datasets/switch", json={"id": clone["id"]}).raise_for_status().json()
    client.headers["X-Financial-Dataset"] = state["token"]
    assert client.get("/api/clients").raise_for_status().json()[0]["name"] == customer["name"]
    stop()
    start()
    assert client.get("/api/datasets").raise_for_status().json()["active_id"] == clone["id"]
    assert client.get("/api/clients").raise_for_status().json()[0]["name"] == customer["name"]
    assert client.get("/api/invoices").raise_for_status().json() == []
    report["checks"].append("independent-datasets/create/import/switch/stale-request/restart")
    stop()
    report["checks"].append("safe-shutdown")
    report["status"] = "passed"
finally:
    if process and process.poll() is None:
        process.terminate()
        process.wait(timeout=15)
    client.close()
    log.close()
    (work / "acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=True))
