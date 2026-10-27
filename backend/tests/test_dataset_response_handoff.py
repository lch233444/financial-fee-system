"""The HTTP client can start its next request before ASGI cleanup completes."""
import asyncio

import pytest

from app.services import datasets


@pytest.mark.parametrize("first_path,next_path,next_method", [
    ("/api/clients", "/api/datasets/switch", "POST"),
    ("/api/datasets", "/api/datasets/switch", "POST"),
    ("/api/datasets/switch", "/api/clients", "GET"),
])
@pytest.mark.parametrize("framing", ["final-body", "content-length", "empty"])
def test_completed_response_hands_off_after_cleanup(tmp_path, monkeypatch, first_path, next_path, next_method, framing):
    manager = datasets.Datasets(tmp_path)
    monkeypatch.setattr(datasets, "_manager", manager)

    async def run():
        responded, cleanup = asyncio.Event(), asyncio.Event()
        calls = []

        async def endpoint(scope, receive, send):
            calls.append(scope["path"])
            headers = [(b"content-length", b"2" if framing == "content-length" else b"0")] if framing != "final-body" else []
            await send({"type": "http.response.start", "status": 200, "headers": headers})
            if framing != "empty":
                await send({"type": "http.response.body", "body": b"[]", "more_body": framing == "content-length"})
            if len(calls) == 1:
                responded.set()
                await cleanup.wait()
            if framing != "final-body":
                await send({"type": "http.response.body", "body": b""})

        gate = datasets.DatasetGate(endpoint)

        async def request(path, method):
            messages = []
            async def send(message):
                messages.append(message)
            async def receive():
                return {"type": "http.request", "body": b""}
            await gate({"type": "http", "path": path, "method": method,
                        "headers": [], "query_string": b""}, receive, send)
            return messages[0]["status"]

        first = asyncio.create_task(request(first_path, "GET" if first_path == "/api/clients" else "POST"))
        await responded.wait()
        following = asyncio.create_task(request(next_path, next_method))
        await asyncio.sleep(0)  # Deliver the next HTTP request while cleanup is held.
        assert calls == [first_path], "Switch must not enter before cleanup finishes"
        cleanup.set()
        assert await first == 200
        assert await following == 200
        assert calls == [first_path, next_path]

    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["timeout", "cancel", "exception", "token-change", "competing-switches"])
def test_handoff_keeps_lease_and_rechecks_admission(tmp_path, monkeypatch, outcome):
    manager = datasets.Datasets(tmp_path)
    monkeypatch.setattr(datasets, "_manager", manager)
    monkeypatch.setattr(datasets.DatasetGate, "response_drain_timeout", .05)

    async def run():
        responded, cleanup = asyncio.Event(), asyncio.Event()
        switches = []

        async def endpoint(scope, receive, send):
            if scope["path"] == "/api/clients":
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"[]"})
                responded.set()
                await cleanup.wait()
                if outcome == "exception":
                    raise RuntimeError("synthetic cleanup failure")
            else:
                switches.append(scope["path"])
                manager.generation = "new-generation"
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"{}"})

        gate = datasets.DatasetGate(endpoint)
        original_token = manager.generation

        async def request(path, token=original_token):
            messages = []
            async def send(message):
                messages.append(message)
            async def receive():
                return {"type": "http.request", "body": b""}
            await gate({"type": "http", "path": path, "method": "POST",
                        "headers": [(b"x-financial-dataset", token.encode())], "query_string": b""}, receive, send)
            return messages[0]["status"]

        first = asyncio.create_task(request("/api/clients"))
        await responded.wait()
        waiting = asyncio.create_task(request("/api/datasets/switch"))
        await asyncio.sleep(0)
        assert not waiting.done() and not switches
        if outcome == "timeout":
            assert await waiting == 409
            assert manager.requests and not manager.exclusive
        elif outcome == "cancel":
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
            assert manager.requests and not manager.exclusive
        elif outcome == "token-change":
            manager.generation = "changed-while-waiting"
        elif outcome == "competing-switches":
            rival = asyncio.create_task(request("/api/datasets/switch"))
            await asyncio.sleep(0)
        cleanup.set()
        if outcome == "exception":
            with pytest.raises(RuntimeError, match="synthetic"):
                await first
        else:
            await first
        if outcome == "token-change":
            assert await waiting == 409 and not switches
        elif outcome == "competing-switches":
            assert sorted([await waiting, await rival]) == [200, 409]
            assert len(switches) == 1
        elif outcome == "exception":
            assert await waiting == 200
        else:
            assert await request("/api/datasets/switch") == 200
        assert not manager.requests and manager.exclusive is None

    asyncio.run(run())
