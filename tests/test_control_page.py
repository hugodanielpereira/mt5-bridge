# tests/test_control_page.py
from fastapi.testclient import TestClient
from app.main import app

def test_control_page_served():
    c = TestClient(app)
    r = c.get("/ui/control/")
    assert r.status_code == 200
    assert b"id=\"ops-output\"" in r.content  # textarea marker