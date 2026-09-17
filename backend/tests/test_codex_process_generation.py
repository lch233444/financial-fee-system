import json
import queue
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services.codex_app_server import CodexAppServerClient, CodexUnavailableError


class _Input:
    def __init__(self):
        self.messages = queue.Queue()

    def write(self, message):
        self.messages.put(json.loads(message))

    def flush(self):
        pass


class _Process:
    def __init__(self, stdout):
        self.stdout = stdout
        self.stdin = _Input()

    def poll(self):
        return None


@pytest.mark.parametrize("old_message", [None, {"id": 1, "result": {"wrong": True}}, {"method": "old/notification"}])
def test_late_old_reader_cannot_fail_or_answer_new_process_rpc(old_message):
    captured, release = threading.Event(), threading.Event()

    def delayed_old_output():
        captured.set()
        assert release.wait(5)
        if old_message is not None:
            yield json.dumps(old_message)

    client = CodexAppServerClient()
    old = _Process(delayed_old_output())
    client._process = old
    reader = threading.Thread(target=client._reader_loop)
    reader.start()
    assert captured.wait(5)
    new = _Process(None)
    client._process = new
    client._process_failure = None
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(client._rpc, "initialize", {}, 5)
        message = new.stdin.messages.get(timeout=5)
        try:
            release.set()
            reader.join(timeout=5)
            assert not reader.is_alive()
            assert client._process_failure is None
            assert client._notifications.empty()
            # Deliver a real response through the current reader, not directly
            # to the waiter. The old response must not have filled its queue.
            new.stdout = iter([json.dumps({"id": message["id"], "result": {"current": True}})])
            client._reader_loop()
            assert result.result(timeout=5) == {"current": True}
        finally:
            release.set()
            reader.join(timeout=5)


def test_current_reader_eof_fails_its_pending_rpc_promptly():
    client = CodexAppServerClient()
    current = _Process(iter(()))
    client._process = current
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(client._rpc, "initialize", {}, 5)
        current.stdin.messages.get(timeout=5)
        client._reader_loop()
        with pytest.raises(CodexUnavailableError):
            result.result(timeout=2)
