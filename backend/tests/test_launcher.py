from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from app import launcher
from app.config import APP_VERSION


@pytest.mark.parametrize("different", [None, "version", "root", "app", "malformed"])
def test_existing_service_requires_same_identity_and_data_directory(tmp_path, different):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = {"app": "金融计划收费计算系统", "version": APP_VERSION, "status": "ok", "local_only": True} if self.path == '/api/health' else {"data_root": str(tmp_path), "local_only": True}
            if different == "version": body["version"] = "0.0.0"
            if different == "root": body["data_root"] = str(tmp_path/'other')
            if different == "app": body["app"] = "Other app"
            payload = b"invalid" if different == "malformed" else json.dumps(body).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_):
            pass

    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        worker = Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01})
        worker.start()
        try:
            url = f'http://127.0.0.1:{server.server_port}'
            assert launcher._is_same_running_system(url, version=APP_VERSION, data_root=tmp_path) is (different is None)
        finally:
            server.shutdown()
            worker.join(timeout=2)
        assert not worker.is_alive()


@pytest.mark.parametrize("same_system", [False, True])
def test_repeated_launch_reopens_matching_service_only(monkeypatch, same_system):
    opened = []
    monkeypatch.setattr(launcher, '_prepare_data_root', lambda: None)
    monkeypatch.setattr(launcher, '_port_available', lambda *_: False)
    monkeypatch.setattr(launcher, '_is_same_running_system', lambda *_, **__: same_system)
    monkeypatch.setattr(launcher.webbrowser, 'open', opened.append)
    monkeypatch.delenv('FINANCIAL_NO_BROWSER', raising=False)
    if same_system:
        launcher.main()
        assert opened == ['http://127.0.0.1:8000']
    else:
        with pytest.raises(SystemExit, match='不同版本或不同数据目录'):
            launcher.main()
        assert opened == []
