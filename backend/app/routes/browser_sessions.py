from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..config import LOOPBACK_HOSTS


router = APIRouter()


def _is_local_page(websocket: WebSocket) -> bool:
    if websocket.client is None:
        return False
    try:
        if not ipaddress.ip_address(websocket.client.host.strip("[]")).is_loopback:
            return False
        origin = urlsplit(websocket.headers.get("origin", ""))
        origin_port = origin.port or 80
        request_port = websocket.url.port or 80
    except ValueError:
        return False
    return (
        origin.scheme == "http"
        and origin.hostname in LOOPBACK_HOSTS
        and origin_port in {request_port, 5173}
        and not origin.username
        and not origin.password
        and not origin.path
        and not origin.query
        and not origin.fragment
        and websocket.headers.get("sec-fetch-site", "").casefold() != "cross-site"
    )


@router.websocket("/api/browser-session")
async def browser_session(websocket: WebSocket) -> None:
    # A cross-site page must never acquire control over the process lifetime.
    if not _is_local_page(websocket):
        await websocket.close(code=1008)
        return
    sessions = websocket.app.state.browser_sessions
    page = object()
    if not sessions.connect(page):
        await websocket.close(code=1012)
        return
    try:
        await websocket.accept()
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        sessions.disconnect(page)
