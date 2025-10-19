import os
import types
import tools.retrain_incremental as retr

def test_no_emit_env_blocks(monkeypatch, tmp_path):
    """When MLSL_RETRAIN_NO_EMIT=1, emit_signal should never be called."""
    monkeypatch.setenv("MLSL_RETRAIN_NO_EMIT", "1")
    calls = []

    def fake_emit(*a, **k):
        calls.append((a, k))
        return tmp_path / "dummy.json"

    monkeypatch.setattr(retr, "emit_signal", fake_emit)

    # Minimal fake job
    job = {"symbol": "TESTUSD", "timeframe": "M15"}

    retr.retrain_one(job)

    # simulate guarded emission branch from main()
    if not retr.NO_EMIT:
        retr.emit_signal("/dev/null", symbol="TESTUSD", side="buy")

    # because NO_EMIT==True, we expect zero calls
    assert calls == []