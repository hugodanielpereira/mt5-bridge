# app/ui/lib/paths.py
from __future__ import annotations

import os
from pathlib import Path


def _env_path(*keys: str) -> Path | None:
    for k in keys:
        v = (os.getenv(k) or "").strip()
        if v:
            return Path(v)
    return None


def project_root() -> Path:
    """
    Resolve o PROJECT root (onde existe outputs/live).

    Ordem:
      1) PROJECT_DIR (se definido)
      2) MLSL_APPS_DIR (se definido; assume .../apps/ml-strategy-lab)
      3) procura para cima a partir deste ficheiro (até 8 níveis) por outputs/live
      4) fallback: CWD
    """
    p = _env_path("PROJECT_DIR")
    if p:
        return p

    apps = _env_path("MLSL_APPS_DIR")
    if apps:
        cand = apps / "ml-strategy-lab"
        if (cand / "outputs").exists() or (cand / "outputs" / "live").exists():
            return cand

    here = Path(__file__).resolve()
    for up in here.parents[:8]:
        if (up / "outputs" / "live").exists() or (up / "outputs").exists():
            return up

    return Path.cwd()


PROJECT = project_root()
LIVE = PROJECT / "outputs" / "live"
LOGS = LIVE / "logs"
PIDS = LIVE / "pids"

CONFIGS = Path(os.getenv("CONFIGS_DIR", str(LIVE / "configs")))

SCHED_LOG = LOGS / "scheduler_retrain.log"
EXEC_LOG = LOGS / "executor.log"
EXEC_PID = PIDS / "mlsl-executor.pid"
RETRAIN_PID = PIDS / "mlsl-retrain.pid"

SCHEDULE_YAML = LIVE / "schedules" / "retrain.yaml"

SIGNALS_INBOX = LIVE / "signals" / "inbox"
SIGNALS_DONE = LIVE / "signals" / "_done"