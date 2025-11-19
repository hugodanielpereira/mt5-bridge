# app/ui/routes/health_api.py
from __future__ import annotations
import os, sys, json, time, socket, subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/ui/api", tags=["ui-health"])

# ---------- paths & env ----------
BASE = Path(os.getenv("PROJECT_DIR", r"C:\Trading\apps\ml-strategy-lab"))
BRIDGE = Path(os.getenv("BRIDGE_DIR", r"C:\Trading\bridges\mt5-bridge"))
LOGS = BASE / "outputs" / "live" / "logs"
PIDS = BASE / "outputs" / "live" / "pids"
SIGNALS_DIR = Path(os.getenv("SIGNALS_DIR", str(BASE / "outputs" / "live" / "signals")))
BRIDGE_HOST = os.getenv("BRIDGE_HOST", "127.0.0.1")
BRIDGE_PORT = int(os.getenv("BRIDGE_PORT", "5005"))
_PAT_GOV   = ["tools.asset_governor", "asset_governor.py"]
_PAT_PRO   = ["tools.promote_model", "promote_model.py"]

# ---------- helpers ----------
def _age_min(p: Path) -> Optional[float]:
    try:
        if not p.exists(): return None
        return max(0.0, (time.time() - p.stat().st_mtime) / 60.0)
    except Exception:
        return None

def _latest_file(base: Path, pattern: str="*.json") -> Tuple[Optional[str], Optional[float]]:
    try:
        if not base.exists(): return (None, None)
        cand = [(f.stat().st_mtime, f) for f in base.glob(pattern) if f.is_file()]
        if not cand: return (None, None)
        ts, f = max(cand, key=lambda t: t[0])
        return (f.name, ts)
    except Exception:
        return (None, None)

def _tcp_listening(host: str, port: int, timeout: float=0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False

# ---------- process discovery (psutil opcional) ----------
def _try_psutil() -> Optional[Any]:
    try:
        import psutil  # type: ignore
        return psutil
    except Exception:
        return None

def _proc_info_psutil(patterns: List[str]) -> List[Dict[str, Any]]:
    psutil = _try_psutil()
    if not psutil:
        return []
    out: List[Dict[str, Any]] = []
    for p in psutil.process_iter(attrs=["pid","name","cmdline","create_time","memory_info","cpu_times"]):
        try:
            cmd = " ".join(p.info.get("cmdline") or [])
            if not cmd:
                continue
            if any(s.lower() in cmd.lower() for s in patterns):
                mem = (p.info.get("memory_info").rss / (1024*1024)) if p.info.get("memory_info") else None
                cpu_times = p.info.get("cpu_times")
                cpu_total = (cpu_times.user + cpu_times.system) if cpu_times else None
                create_time = p.info.get("create_time")
                uptime_s = (time.time() - float(create_time)) if create_time else None
                out.append({
                    "pid": p.info["pid"],
                    "cmd": cmd,
                    "mem_mb": round(mem, 1) if mem is not None else None,
                    "cpu_s": round(cpu_total, 1) if cpu_total is not None else None,
                    "uptime_s": round(uptime_s, 1) if uptime_s is not None else None,
                })
        except Exception:
            continue
    return out

def _proc_info_powershell(patterns: List[str]) -> List[Dict[str, Any]]:
    # Fallback sem psutil (Windows): usa CIM
    try:
        cmd = [
            "powershell", "-NoProfile", "-Command",
            "Get-CimInstance Win32_Process | Select-Object ProcessId,CommandLine,CreationDate,WorkingSetSize | ConvertTo-Json -Depth 3"
        ]
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8)
        if p.returncode != 0 or not p.stdout.strip():
            return []
        arr = json.loads(p.stdout)
        if isinstance(arr, dict): arr = [arr]
        out: List[Dict[str, Any]] = []
        for it in arr:
            cmdline = (it.get("CommandLine") or "") if isinstance(it, dict) else ""
            if not cmdline:
                continue
            if not any(s.lower() in cmdline.lower() for s in patterns):
                continue
            pid = it.get("ProcessId")
            wss = it.get("WorkingSetSize") or 0
            mem_mb = round(float(wss)/(1024*1024), 1) if isinstance(wss,(int,float)) else None
            # CreationDate vem em CIM datetime; não vamos parsear — uptime ficará None aqui
            out.append({"pid": pid, "cmd": cmdline, "mem_mb": mem_mb, "cpu_s": None, "uptime_s": None})
        return out
    except Exception:
        return []

def _proc_info(patterns: List[str]) -> List[Dict[str, Any]]:
    ps = _try_psutil()
    if ps:
        res = _proc_info_psutil(patterns)
        if res:
            return res
    # fallback Windows (PowerShell)
    return _proc_info_powershell(patterns)

def _first_or_none(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return rows[0] if rows else None

# ---------- mapping por role ----------
_PAT_BRIDGE = ["uvicorn app.main", "app.main:app", "uvicorn", "fastapi"]
_PAT_EXEC   = ["services.executor.loop", "executor.loop", "run_execute_live_mt5.py"]
_PAT_RETR   = ["services.scheduler_retrain", "scheduler_retrain", "tools.retrain_incremental --schedule-loop"]
_PAT_WATCH  = ["tools.watch_signals"]

def _service_status() -> Dict[str, Any]:
    # Bridge
    bridge_procs = _proc_info(_PAT_BRIDGE)
    bridge = _first_or_none(bridge_procs) or {}
    # Health HTTP rápido
    http_ok = _tcp_listening(BRIDGE_HOST, BRIDGE_PORT)

    # Executor
    exec_procs = _proc_info(_PAT_EXEC)
    executor = _first_or_none(exec_procs) or {}

    # Retrain/scheduler
    retr_procs = _proc_info(_PAT_RETR)
    retrain = _first_or_none(retr_procs) or {}

    # Watcher  (signals)
    watch_procs = _proc_info(_PAT_WATCH)
    watcher = _first_or_none(watch_procs) or {}

    # Governor
    gov_procs = _proc_info(_PAT_GOV)
    governor = _first_or_none(gov_procs) or {}

    # Promoter
    pro_procs = _proc_info(_PAT_PRO)
    promoter = _first_or_none(pro_procs) or {}

    # Logs & sinais
    exec_log = LOGS / "executor.log"
    retr_log = LOGS / "scheduler_retrain.log"
    retrain_log = LOGS / "retrain.log"  # às vezes é este que mexe
    exec_age = _age_min(exec_log)
    retr_age = _age_min(retr_log) or _age_min(retrain_log)

    inbox = SIGNALS_DIR / "inbox"
    outbox = SIGNALS_DIR / "outbox"
    done = SIGNALS_DIR / "_done"
    cnt_inbox = len(list(inbox.glob("*.json"))) if inbox.exists() else 0
    cnt_outbox = len(list(outbox.glob("*.json"))) if outbox.exists() else 0
    cnt_done = len(list(done.glob("*.json"))) if done.exists() else 0
    latest_name, latest_ts = _latest_file(outbox)

    return {
        "bridge": {
            "http_ok": bool(http_ok),
            "pid": bridge.get("pid"),
            "cpu_s": bridge.get("cpu_s"),
            "mem_mb": bridge.get("mem_mb"),
            "uptime_s": bridge.get("uptime_s"),
            "port": BRIDGE_PORT,
        },
        "executor": {
            "pid": executor.get("pid"),
            "cpu_s": executor.get("cpu_s"),
            "mem_mb": executor.get("mem_mb"),
            "uptime_s": executor.get("uptime_s"),
            "log_age_min": exec_age,
        },
        "retrain": {
            "pid": retrain.get("pid"),
            "cpu_s": retrain.get("cpu_s"),
            "mem_mb": retrain.get("mem_mb"),
            "uptime_s": retrain.get("uptime_s"),
            "log_age_min": retr_age,
        },
        "watcher": {
            "pid": watcher.get("pid"),
            "cpu_s": watcher.get("cpu_s"),
            "mem_mb": watcher.get("mem_mb"),
            "uptime_s": watcher.get("uptime_s"),
        },
        "signals": {
            "inbox": cnt_inbox,
            "outbox": cnt_outbox,
            "done": cnt_done,
            "latest_file": latest_name,
            "latest_ts": int(latest_ts) if latest_ts else None,
        },
        "governor": {
            "pid": governor.get("pid"), "cpu_s": governor.get("cpu_s"),
            "mem_mb": governor.get("mem_mb"), "uptime_s": governor.get("uptime_s"),
        },
        "promoter": {
            "pid": promoter.get("pid"), "cpu_s": promoter.get("cpu_s"),
            "mem_mb": promoter.get("mem_mb"), "uptime_s": promoter.get("uptime_s"),
        },
        "logs": {
            "executor": str(exec_log),
            "scheduler": str(retr_log),
            "retrain": str(retrain_log),
        }
    }

@router.get("/health")
def ui_health_extended():
    s = _service_status()
    # “ok” geral: bridge HTTP ok + executor log mexeu nos últimos 10 min + retrain log < 120 min
    exec_fresh = (s["executor"]["log_age_min"] is not None) and (s["executor"]["log_age_min"] < 10)
    retr_fresh = (s["retrain"]["log_age_min"] is not None) and (s["retrain"]["log_age_min"] < 120)
    ok = bool(s["bridge"]["http_ok"]) and exec_fresh and retr_fresh
    return JSONResponse({"ok": ok, **s})