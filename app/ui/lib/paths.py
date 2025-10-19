# app/ui/lib/paths.py
from __future__ import annotations
import os
from pathlib import Path

def project_root() -> Path:
    env = os.getenv("PROJECT_DIR")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for up in (here.parents[3],):  # .../app/ui/lib -> sobe 3
        cand = up / "outputs" / "live"
        if cand.exists():
            return up
    return Path.cwd()

PROJECT = project_root()
LIVE     = PROJECT / "outputs" / "live"
LOGS     = LIVE / "logs"
PIDS     = LIVE / "pids"
CONFIGS  = Path(os.getenv("CONFIGS_DIR", str(LIVE / "configs")))
SCHED_LOG= LOGS / "scheduler_retrain.log"
EXEC_LOG = LOGS / "executor.log"
EXEC_PID = PIDS / "mlsl-executor.pid"
RETRAIN_PID = PIDS / "mlsl-retrain.pid"
SCHEDULE_YAML = LIVE / "schedules" / "retrain.yaml"
SIGNALS_INBOX = LIVE / "signals" / "inbox"
SIGNALS_DONE  = LIVE / "signals" / "_done"