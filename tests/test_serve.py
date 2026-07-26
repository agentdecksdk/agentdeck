"""The HTTP surface must answer coherently before the lifespan has started the App."""

import pytest

pytest.importorskip("fastapi")


def test_endpoints_503_before_startup():
    from fastapi.testclient import TestClient

    from agentdeck.serve import create_app

    client = TestClient(create_app())  # no `with`: lifespan never runs, so state.deck stays None

    health = client.get("/health")
    assert health.status_code == 503
    assert health.json() == {"status": "starting"}

    chat = client.post("/agents/Greeter/chat", json={"session_id": "s1", "message": "hi"})
    assert chat.status_code == 503
    assert client.post("/workflows/HelloFlow", json={}).status_code == 503
