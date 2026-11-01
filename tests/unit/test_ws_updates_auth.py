"""C3/H4 (#2368): the /ws/updates WebSocket must require the engine key on the desktop
(DEPLOYMENT_MODE=LOCAL) — a browser cannot send the X-Engine-Key header, so a bare accept()
left the live engine stream cross-origin readable + hijackable."""

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import core.engine.api_routes as api_routes


def _client():
    return TestClient(api_routes.app)


def test_ws_rejected_without_or_wrong_key_on_local():
    with patch.dict(
        os.environ, {"DEPLOYMENT_MODE": "LOCAL", "ENGINE_API_KEY": "K"}, clear=False
    ):
        with pytest.raises(WebSocketDisconnect):
            with _client().websocket_connect("/ws/updates?key=WRONG") as ws:
                ws.receive_text()


def test_ws_accepts_with_correct_key_on_local():
    with patch.dict(
        os.environ, {"DEPLOYMENT_MODE": "LOCAL", "ENGINE_API_KEY": "K"}, clear=False
    ):
        with patch.object(api_routes, "engine"):
            with _client().websocket_connect("/ws/updates?key=K") as ws:
                msg = ws.receive_json()
    assert msg["type"] == "log"
