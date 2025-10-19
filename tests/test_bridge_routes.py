import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_ui_ops_route_exists():
    """Check that /ui/ops/run exists and returns JSON."""
    r = client.post("/ui/ops/run", json={"target": "executor", "action": "start"})
    # We just care that the endpoint is reachable
    assert r.status_code in (200, 400)
    j = r.json()
    assert isinstance(j, dict)
    assert "ok" in j
    assert "rc" in j

def test_static_control_js_served():
    """Control JS must exist under /ui/static/js/control.js"""
    r = client.get("/ui/static/js/control.js")
    assert r.status_code == 200
    assert "fetch(\"/ui/ops/run\"" in r.text