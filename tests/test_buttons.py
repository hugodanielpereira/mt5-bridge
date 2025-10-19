# tests/test_buttons.py
import json
from fastapi.testclient import TestClient
from app.main import app

def test_proc_run_start_executor(monkeypatch):
    from app.ui.routes.api_procs import _run

    def fake_run(cmd, cwd=None, env=None):
        # valida o mapeamento do botão "Executor Up" -> start executor
        cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
        assert "mlsl.ps1" in cmd_str.lower()
        assert "up" in cmd_str.lower()
        assert "executor" in cmd_str.lower()
        return {"rc": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr("app.ui.routes.api_procs._run", fake_run)
    client = TestClient(app)
    r = client.post("/ui/api/proc_run", json={"target": "executor", "action": "start"})
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    assert j["rc"] == 0

def test_proc_run_restart_scheduler(monkeypatch):
    def fake_run(cmd, cwd=None, env=None):
        cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
        assert "mlsl.ps1" in cmd_str.lower()
        assert "restart" in cmd_str.lower()
        # scheduler é mapeado para "retrain" no mlsl.ps1
        assert "retrain" in cmd_str.lower()
        return {"rc": 0, "stdout": "ok", "stderr": ""}

    from app.ui.routes.api_procs import _run
    import app.ui.routes.api_procs as api
    monkeypatch.setattr("app.ui.routes.api_procs._run", fake_run)
    client = TestClient(app)
    r = client.post("/ui/api/proc_run", json={"target": "scheduler", "action": "restart"})
    assert r.status_code == 200
    assert r.json()["ok"] is True

def test_retrain_run_all_noemit(monkeypatch):
    # garante que o retrain ALL não emite sinais quando chamado pela UI
    from app.ui.routes.api_dash import _run

    def fake_run(cmd, cwd=None, env=None):
        cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
        # deve incluir --no-emit OU o env MLSL_RETRAIN_NO_EMIT=1
        ok_flag = ("--no-emit" in cmd_str) or (env and env.get("MLSL_RETRAIN_NO_EMIT") in ("1","true","yes","on"))
        assert ok_flag, f"no-emit ausente: {cmd_str} env={env}"
        return {"rc": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr("app.ui.routes.api_dash._run", fake_run)
    client = TestClient(app)
    r = client.post("/ui/api/retrain_run_all")
    assert r.status_code == 200
    assert r.json()["ok"] is True