import os
from fastapi.testclient import TestClient
from app.main import app

def test_ops_run_route_exists():
    client = TestClient(app)
    r = client.post("/ui/ops/run", json={"target":"executor","action":"start"})
    # We don't care if the command actually runs here; existence + JSON is enough
    assert r.status_code in (200, 400)  # 400 if missing script etc., but route exists
    assert isinstance(r.json(), dict)

def test_static_control_js():
    client = TestClient(app)
    r = client.get("/ui/static/js/control.js")
    assert r.status_code == 200
    assert "fetch(\"/ui/ops/run\"" in r.text