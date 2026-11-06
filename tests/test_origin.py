import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.main import app


def test_other_websites_cannot_connect():
    with pytest.raises(WebSocketDisconnect) as refused:
        with TestClient(app).websocket_connect("/ws/transcribe", headers={"origin": "https://example.com"}):
            pass
    assert refused.value.code == 1008
