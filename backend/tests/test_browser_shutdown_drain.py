from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen

from websockets.sync.client import connect


def test_last_browser_close_allows_inflight_request_to_finish(tmp_path):
    """Exercise the real launcher, WebSocket disconnect and Uvicorn drain."""
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    backend = Path(__file__).resolve().parents[1]
    child = tmp_path/'server.py'
    started = tmp_path/'request-started'
    child.write_text('''import os, time
from pathlib import Path
from app.main import app
from app.launcher import main
@app.post('/api/inflight-probe')
def inflight_probe():
    Path(os.environ['PROBE_STARTED']).write_text('started')
    time.sleep(4)
    return {'completed': True}
# Register this test-only route ahead of the application's SPA catch-all.
app.router.routes.insert(0, app.router.routes.pop())
main()
''', encoding='utf-8')
    env = os.environ | {
        'PYTHONPATH': str(backend), 'FINANCIAL_HOST': '127.0.0.1', 'FINANCIAL_PORT': str(port),
        'FINANCIAL_DATA_ROOT': str(tmp_path/'data'), 'FINANCIAL_CODEX_HOME': str(tmp_path/'ai'),
        'FINANCIAL_NO_BROWSER': '1', 'PROBE_STARTED': str(started), 'OPENBLAS_NUM_THREADS': '1',
    }
    base = f'http://127.0.0.1:{port}'
    def get(path):
        request = Request(base+path, data=b'{}', headers={'X-Financial-System-Request': '1'}) if path == '/api/inflight-probe' else base+path
        with urlopen(request, timeout=10) as response:
            return json.load(response)

    process = subprocess.Popen([sys.executable, str(child)], env=env, cwd=backend,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        deadline = time.monotonic()+20
        while True:
            assert process.poll() is None, 'Launcher exited before browser connected'
            try:
                assert get('/api/health')['status'] == 'ok'
                break
            except OSError:
                assert time.monotonic() < deadline, 'Startup timed out'
                time.sleep(0.05)
        with connect(f'ws://127.0.0.1:{port}/api/browser-session', origin=base) as page:
            with ThreadPoolExecutor(max_workers=1) as pool:
                request = pool.submit(get, '/api/inflight-probe')
                deadline = time.monotonic()+5
                while not started.exists():
                    if request.done():
                        request.result()
                        raise AssertionError('Probe returned before entering the request handler')
                    assert time.monotonic() < deadline, 'Request never started'
                    time.sleep(0.01)
                page.close()
                assert request.result(timeout=10) == {'completed': True}
        assert process.wait(timeout=10) == 0
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', port))
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
