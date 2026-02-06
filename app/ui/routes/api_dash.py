# bridges/mt5-bridge/app/ui/routes/api_dash.py
from __future__ import annotations

import os
import json
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional, List, Tuple

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse

from app.common.service_registry import get_service
from app.common.path_utils import norm_path, project_root, apps_root, logs_dir as logs_root

router = APIRouter(prefix="/ui/api", tags=["ui-dash"])

# ===================================================
# Instance + Paths (RUNTIME, instance-aware)
# ===================================================

def _iid() -> str:
    return (
        os.getenv("MLSL_INSTANCE_ID")
        or os.getenv("IID")
        or os.getenv("BRIDGE_ID")
        or "default"
    ).strip()

def _python_exe() -> str:
    return os.getenv("VENV_PY") or "python"

def _apps_root() -> Path:
    # raiz do MLSL dentro do mono-repo
    return apps_root()

def _live_root() -> Path:
    env = (os.getenv("LIVE_DIR") or "").strip()
    if env:
        return norm_path(env, base=_apps_root())
    return _apps_root() / "outputs" / "live"

def _instance_dir() -> Path:
    env = (os.getenv("INSTANCE_DIR") or "").strip()
    if env:
        return norm_path(env, base=_live_root())
    return _live_root() / "instances" / _iid()

def _logs_inst_dir() -> Path:
    """
    Preferir logs por instância: <Trading>/logs/instances/<IID>
    Se LOGS_INST vier no env, usa.
    """
    env = (os.getenv("LOGS_INST") or "").strip()
    if env:
        return norm_path(env, base=project_root())
    return logs_root() / "instances" / _iid()

def _logs_dir() -> Path:
    """
    Diretório de logs (por instância).
    """
    # aceita também LOGS_DIR/LAB_LOGS_DIR por compat
    env = (os.getenv("LOGS_INST") or os.getenv("LOGS_DIR") or os.getenv("LAB_LOGS_DIR") or "").strip()
    if env:
        return norm_path(env, base=project_root())
    return _logs_inst_dir()

def _pids_dir() -> Path:
    env = (os.getenv("PIDS_DIR") or os.getenv("PIDFILES_DIR") or "").strip()
    if env:
        p = norm_path(env, base=project_root())
        p.mkdir(parents=True, exist_ok=True)
        return p

    li = (os.getenv("LOGS_INST") or "").strip()
    if li:
        p = norm_path(li, base=project_root()) / "pids"
        p.mkdir(parents=True, exist_ok=True)
        return p

    raise HTTPException(status_code=500, detail="PID dir não resolvido: define PIDS_DIR (ou LOGS_INST).")

def _configs_dir() -> Path:
    env = (os.getenv("CONFIGS_DIR") or "").strip()
    if env:
        return norm_path(env, base=_instance_dir())
    return _instance_dir() / "configs"

def _models_root() -> Path:
    env = (os.getenv("MODELS_ROOT") or "").strip()
    if env:
        return norm_path(env, base=_instance_dir())
    return _instance_dir() / "models"

def _models_latest_dir(symbol: str | None, timeframe: str | None) -> Path:
    s = (symbol or "").strip().upper()
    tf = (timeframe or "").strip().upper()
    return _models_root() / s / tf / "latest"

def _signals_dir() -> Path:
    env = (os.getenv("SIGNALS_DIR") or "").strip()
    if env:
        return norm_path(env, base=_instance_dir())
    return _instance_dir() / "signals"

def _metrics_dir() -> Path:
    env = (os.getenv("METRICS_DIR") or "").strip()
    if env:
        return norm_path(env, base=_instance_dir())
    return _instance_dir() / "metrics"

def _schedules_dir() -> Path:
    env = (os.getenv("SCHEDULES_DIR") or "").strip()
    if env:
        return norm_path(env, base=_instance_dir())
    return _instance_dir() / "schedules"

def _policies_dir() -> Path:
    env = (os.getenv("POLICIES_DIR") or "").strip()
    if env:
        return norm_path(env, base=_instance_dir())
    return _instance_dir() / "policies"

def _schedule_file() -> Path:
    env = (os.getenv("RETRAIN_SCHEDULE_FILE") or "").strip()
    if env:
        return norm_path(env, base=_instance_dir())
    return _schedules_dir() / "retrain.yaml"

def _timestamps_file() -> Path:
    env = (os.getenv("RETRAIN_TIMESTAMPS_FILE") or "").strip()
    if env:
        return norm_path(env, base=_instance_dir())
    return _schedules_dir() / "retrain.timestamps.json"

def _portfolio_file() -> Path:
    env = (os.getenv("PORTFOLIO_FILE") or "").strip()
    if env:
        return norm_path(env, base=_instance_dir())
    return _instance_dir() / "portfolio" / "top10.yaml"

def _norm_key(symbol: str | None, timeframe: str | None) -> str:
    s = (symbol or "").strip().upper()
    tf = (timeframe or "").strip().upper()
    return f"{s}:{tf}" if s and tf else ""

def _instance_paths() -> Dict[str, str]:
    bridge_url = (os.getenv("BRIDGE_URL") or os.getenv("BRIDGE_REAL") or os.getenv("BRIDGE_ENV") or "").strip()
    ui_url = (os.getenv("UI_URL") or "").strip()
    return {
        "iid": _iid(),
        "bridge_url": bridge_url,
        "ui_url": ui_url,
        "project_root": str(project_root()),
        "apps_root": str(_apps_root()),
        "live_root": str(_live_root()),
        "instance_dir": str(_instance_dir()),
        "configs_dir": str(_configs_dir()),
        "models_root": str(_models_root()),
        "signals_dir": str(_signals_dir()),
        "pids_dir": str(_pids_dir()),
        "metrics_dir": str(_metrics_dir()),
        "schedules_dir": str(_schedules_dir()),
        "policies_dir": str(_policies_dir()),
        "logs_dir": str(_logs_dir()),
    }

# ===================================================
# Helpers gerais
# ===================================================

def _run(cmd: list[str] | str, cwd: Optional[Path] = None, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    shell = isinstance(cmd, str)
    _env = (env or os.environ).copy()
    _env.setdefault("PYTHONUTF8", "1")
    _env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        p = subprocess.run(
            cmd if shell else [*cmd],
            cwd=str(cwd) if cwd else None,
            env=_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=shell,
        )
        return {"rc": p.returncode, "stdout": p.stdout or "", "stderr": p.stderr or ""}
    except Exception as e:
        return {"rc": -1, "stdout": "", "stderr": f"{type(e).__name__}: {e}"}

def _read_yaml_safe(p: Path) -> dict:
    try:
        import yaml  # type: ignore
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}

def _safe_join(base: Path, name: str) -> Path:
    p = (base / Path(name).name).resolve()
    base_res = base.resolve()
    if not str(p).startswith(str(base_res)):
        raise HTTPException(status_code=400, detail="invalid path")
    return p

def _load_last_runs() -> dict[str, float]:
    p = _timestamps_file()
    try:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            out: dict[str, float] = {}
            for k, v in (data or {}).items():
                if isinstance(v, (int, float)):
                    out[str(k).strip().upper()] = float(v)
            return out
    except Exception:
        pass
    return {}

# ===================================================
# psutil process helpers
# ===================================================

def _ps():
    try:
        import psutil  # type: ignore
        return psutil
    except Exception:
        return None

def _read_pid_any(names: List[str]) -> Optional[int]:
    base = _pids_dir()
    for name in names:
        p = base / name
        try:
            if p.exists():
                t = p.read_text(encoding="utf-8", errors="replace").strip()
                if t.isdigit():
                    return int(t)
        except Exception:
            pass
    return None

def _proc_from_pid(pid: Optional[int]):
    ps = _ps()
    if not ps or not pid:
        return None
    try:
        return ps.Process(pid)
    except Exception:
        return None

def _find_proc(needles: List[str]):
    ps = _ps()
    if not ps:
        return None
    needles = [n.lower() for n in needles if n]
    for p in ps.process_iter(attrs=["pid", "name", "cmdline", "create_time"]):
        try:
            cl = " ".join(p.info.get("cmdline") or []) or (p.info.get("name") or "")
            txt = cl.lower()
            if any(n in txt for n in needles):
                return p
        except Exception:
            continue
    return None

def _is_wrapper_name(name: str | None) -> bool:
    if not name:
        return False
    n = name.lower()
    return any(x in n for x in ("pwsh", "powershell", "cmd.exe", "cmd", "conhost", "start.exe", "wine"))

def _promote_to_child_with_needles(p, needles: List[str]):
    ps = _ps()
    if not ps or not p:
        return p
    try:
        name = p.name()
    except Exception:
        name = None
    if not _is_wrapper_name(name):
        return p

    needles_lc = [n.lower() for n in needles if n]
    try:
        for ch in p.children(recursive=True):
            try:
                cl = " ".join(ch.cmdline()) if hasattr(ch, "cmdline") else ""
                if any(n in cl.lower() for n in needles_lc):
                    return ch
            except Exception:
                continue
    except Exception:
        pass
    return p

def _locate_proc(pid: Optional[int], needles: List[str]):
    p = _proc_from_pid(pid)
    if not p:
        p = _find_proc(needles)
    if p:
        p = _promote_to_child_with_needles(p, needles)
    return p

def _proc_metrics(p) -> Dict[str, Any]:
    ps = _ps()
    if not ps or not p:
        return {"pid": None, "cpu_s": None, "mem_mb": None, "uptime_s": None}
    try:
        ct = p.cpu_times()
        mem = p.memory_info()
        return {
            "pid": p.pid,
            "cpu_s": round((ct.user + ct.system), 1) if ct else None,
            "mem_mb": round(mem.rss / (1024 * 1024), 1) if mem else None,
            "uptime_s": max(0.0, time.time() - (p.create_time() or time.time())),
        }
    except Exception:
        return {"pid": None, "cpu_s": None, "mem_mb": None, "uptime_s": None}

# ===================================================
# Metrics CSV (proc_monitor)
# ===================================================

def _metrics_csv() -> Path:
    env = (os.getenv("PROC_METRICS_FILE") or "").strip()
    if env:
        return norm_path(env, base=_metrics_dir())
    return _metrics_dir() / "proc_metrics.csv"

def _last_metrics_for(service: str) -> Dict[str, Any]:
    path = _metrics_csv()
    if not path.exists():
        return {}
    try:
        import csv
        with path.open("r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            return {}
        for row in reversed(rows):
            if (row.get("service") or "").strip().lower() != service.strip().lower():
                continue

            def _to_int(key: str) -> Optional[int]:
                v = (row.get(key) or "").strip()
                if not v:
                    return None
                try:
                    return int(float(v))
                except Exception:
                    return None

            def _to_float(key: str) -> Optional[float]:
                v = (row.get(key) or "").strip()
                if not v:
                    return None
                try:
                    return float(v)
                except Exception:
                    return None

            return {
                "pid": _to_int("pid"),
                "cpu_pct": _to_float("cpu_pct"),
                "mem_mb": _to_float("rss_mb"),
                "uptime_s": _to_float("uptime_s"),
                "threads": _to_int("threads"),
                "sys_cpu_pct": _to_float("sys_cpu_pct"),
                "sys_mem_used_mb": _to_float("sys_mem_used_mb"),
                "sys_mem_total_mb": _to_float("sys_mem_total_mb"),
            }
    except Exception:
        return {}

# ===================================================
# Retrain controlado pela UI (NO-EMIT)
# ===================================================

def _retrain_cmd_args(extra: list[str] | None = None) -> list[str]:
    py = _python_exe()
    args = [py, "-m", "tools.retrain_incremental"]
    if extra:
        args.extend(extra)
    return args

def _ui_no_emit_env(base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = dict(base or os.environ)
    env["MLSL_RETRAIN_NO_EMIT"] = os.getenv("MLSL_RETRAIN_NO_EMIT", "0")
    env["MLSL_RETRAIN_NO_EMIT_UI"] = os.getenv("MLSL_RETRAIN_NO_EMIT_UI", "1")

    # garantir instância correta
    env.setdefault("INSTANCE_ID", _iid())
    env.setdefault("MLSL_INSTANCE_ID", _iid())
    env.setdefault("INSTANCE_DIR", str(_instance_dir()))
    env.setdefault("CONFIGS_DIR", str(_configs_dir()))
    env.setdefault("MODELS_ROOT", str(_models_root()))
    env.setdefault("SIGNALS_DIR", str(_signals_dir()))
    env.setdefault("RETRAIN_SCHEDULE_FILE", str(_schedule_file()))
    env.setdefault("RETRAIN_TIMESTAMPS_FILE", str(_timestamps_file()))
    return env

# ===================================================
# API
# ===================================================

@router.get("/env")
def env_info():
    return _instance_paths()

@router.post("/retrain_run_all")
def retrain_run_all():
    cmd = _retrain_cmd_args(["--force", "--no-emit"])
    res = _run(cmd, cwd=_apps_root(), env=_ui_no_emit_env())
    rc = int(res.get("rc", -1))
    return JSONResponse({
        "ok": (rc == 0),
        "cmd": " ".join(shlex.quote(c) for c in cmd),
        "cwd": str(_apps_root()),
        "rc": rc,
        "stdout": res.get("stdout", ""),
        "stderr": res.get("stderr", ""),
    })

@router.post("/retrain_run_due")
def retrain_run_due():
    cmd = _retrain_cmd_args(["--no-emit"])
    res = _run(cmd, cwd=_apps_root(), env=_ui_no_emit_env())
    rc = int(res.get("rc", -1))
    return JSONResponse({
        "ok": (rc == 0),
        "cmd": " ".join(shlex.quote(c) for c in cmd),
        "cwd": str(_apps_root()),
        "rc": rc,
        "stdout": res.get("stdout", ""),
        "stderr": res.get("stderr", ""),
    })

@router.post("/retrain_one")
def retrain_one(payload: Dict[str, Any]):
    cfg = str(payload.get("config") or "").strip()
    if not cfg:
        raise HTTPException(status_code=400, detail="config obrigatório")
    cmd = _retrain_cmd_args(["--no-emit", "--config", cfg])
    res = _run(cmd, cwd=_apps_root(), env=_ui_no_emit_env())
    rc = int(res.get("rc", -1))
    return JSONResponse({
        "ok": (rc == 0),
        "cmd": " ".join(shlex.quote(c) for c in cmd),
        "cwd": str(_apps_root()),
        "rc": rc,
        "stdout": res.get("stdout", ""),
        "stderr": res.get("stderr", ""),
    })

@router.post("/retrain_rebuild")
def retrain_rebuild():
    cmd = _retrain_cmd_args(["--rebuild-schedule"])
    res = _run(cmd, cwd=_apps_root())
    rc = int(res.get("rc", -1))
    return JSONResponse({
        "ok": (rc == 0),
        "cmd": " ".join(shlex.quote(c) for c in cmd),
        "cwd": str(_apps_root()),
        "rc": rc,
        "stdout": res.get("stdout", ""),
        "stderr": res.get("stderr", ""),
    })

# ---------------------------------------------------
# Dashboard helpers
# ---------------------------------------------------

def _log_age_min(path: Path) -> Optional[float]:
    try:
        if not path.exists():
            return None
        return round((time.time() - path.stat().st_mtime) / 60.0, 1)
    except Exception:
        return None

def _log_age_min_multi(base: Path, names: list[str]) -> tuple[Optional[float], Optional[str]]:
    for name in names:
        p = base / name
        age = _log_age_min(p)
        if age is not None:
            return age, str(p)
    return None, None

@router.get("/status")
def status():
    # ---------- BRIDGE ----------
    bridge_ok = False
    bridge_diag: Dict[str, Any] | None = None
    try:
        svc = get_service()
        diag = svc.diag() if svc else {"ok": False, "reason": "svc_missing"}
        bridge_diag = diag
        bridge_ok = bool(diag.get("ok"))
    except Exception as e:
        bridge_diag = {"ok": False, "reason": f"diag_failed: {e}"}

    logs_dir = _logs_dir()

    # ---------- BRIDGE backend (uvicorn) ----------
    bridge_pid = _read_pid_any(["bridge_backend.pid"])
    bridge_needles = [
        "uvicorn app.main:app",
        "uvicorn app.main",
        "-m uvicorn",
        "app.main:app",
        "app\\main.py",
    ]
    bp = _locate_proc(bridge_pid, bridge_needles) or _find_proc(bridge_needles)
    bmet = _proc_metrics(bp)

    # ---------- BRIDGE UI (se existir) ----------
    ui_pid = _read_pid_any(["bridge_ui.pid"])
    ui_needles = ["bridge_ui", "ui_server", "app.ui"]
    up = _locate_proc(ui_pid, ui_needles) or _find_proc(ui_needles)
    umet = _proc_metrics(up)

    # ---------- STRATEGY ENGINE ----------
    se_pid = _read_pid_any(["strategy_engine.pid"])
    se_needles = ["services.strategy_engine", "strategy_engine", "strategy-engine"]
    sp = _locate_proc(se_pid, se_needles) or _find_proc(se_needles)
    smet = _proc_metrics(sp)
    se_age, se_file = _log_age_min_multi(
        logs_dir,
        ["strategy_engine.log", "strategy_engine.stdout.log", "strategy_engine.stderr.log"],
    )
    se_fresh = bool(smet.get("pid")) or (se_age is not None and se_age <= 5)

    # ---------- EXECUTOR ----------
    exec_pid = _read_pid_any(["executor.pid"])
    exec_needles = ["services.executor.loop", "executor.loop", "run_execute_live_mt5"]
    pe = _locate_proc(exec_pid, exec_needles) or _find_proc(exec_needles)
    exec_info = _proc_metrics(pe)
    exec_age, exec_file = _log_age_min_multi(
        logs_dir,
        ["executor.log", "executor.stdout.log", "executor.stderr.log"],
    )
    exec_fresh = bool(exec_info.get("pid")) or (
        exec_age is not None and exec_age <= float(os.getenv("EXEC_FRESH_MAX_AGE_MIN", "5"))
    )

    # ---------- RETRAIN ----------
    retr_pid = _read_pid_any(["retrain.pid"])
    retr_needles = ["services.retrain", "scheduler_retrain", "tools.retrain_incremental"]
    pr = _locate_proc(retr_pid, retr_needles) or _find_proc(retr_needles)
    retr_info = _proc_metrics(pr)
    retr_age, retr_file = _log_age_min_multi(
        logs_dir,
        ["retrain.log", "scheduler_retrain.log", "scheduler_retrain.stdout.log", "scheduler_retrain.stderr.log"],
    )
    retr_fresh = bool(retr_info.get("pid")) or (
        retr_age is not None and retr_age <= float(os.getenv("SCHED_FRESH_MAX_AGE_MIN", "10"))
    )

    # ---------- WATCHER ----------
    watch_pid = _read_pid_any(["watcher.pid"])
    watch_needles = [
        "services.monitoring.watch_signals",
        "-m services.monitoring.watch_signals",
        "watch_signals",
    ]
    wp = _locate_proc(watch_pid, watch_needles) or _find_proc(watch_needles)
    wmet = _proc_metrics(wp)
    watch_age, watch_file = _log_age_min_multi(
        logs_dir,
        ["watch_signals.log", "watch_signals.stdout.log", "watch_signals.stderr.log"],
    )
    watch_fresh = bool(wmet.get("pid")) or (
        watch_age is not None and watch_age <= int(os.getenv("WATCH_FRESH_MAX_AGE_MIN", "5"))
    )

    # ---------- EMITTER ----------
    emit_pid = _read_pid_any(["emitter.pid"])
    emit_needles = [
        "services.emitter.loop",
        "-m services.emitter.loop",
        "emitter/loop.py",
        "emitter.loop",
    ]
    ep = _locate_proc(emit_pid, emit_needles) or _find_proc(emit_needles)
    emet = _proc_metrics(ep)
    emitter_age, emitter_file = _log_age_min_multi(
        logs_dir,
        ["emitter.log", "emitter.stdout.log", "emitter.stderr.log"],
    )
    emitter_fresh = bool(emet.get("pid")) or (
        emitter_age is not None and emitter_age <= int(os.getenv("EMITTER_FRESH_MAX_AGE_MIN", "5"))
    )

    # ---------- MONITOR ----------
    mon_pid = _read_pid_any(["monitor.pid"])
    mon_needles = ["services.monitor", "proc_monitor", "monitor.loop"]
    mp = _locate_proc(mon_pid, mon_needles) or _find_proc(mon_needles)
    mmet = _proc_metrics(mp)
    mon_age, mon_file = _log_age_min_multi(
        logs_dir,
        ["monitor.log", "proc_monitor.log", "proc_monitor.stdout.log", "proc_monitor.stderr.log"],
    )
    mon_fresh = bool(mmet.get("pid")) or (mon_age is not None and mon_age <= 5)

    # ---------- POSITION MANAGER (sem CSV) ----------
    pm_pid = _read_pid_any(["position_manager.pid"])
    pm_needles = ["services.risk.position_manager", "-m services.risk.position_manager", "position_manager.py"]
    pp = _locate_proc(pm_pid, pm_needles) or _find_proc(pm_needles)
    pm_met = _proc_metrics(pp)

    pm_age, pm_file = _log_age_min_multi(
        logs_dir,
        ["position_manager.log", "position_manager.stdout.log", "position_manager.stderr.log"],
    )
    pm_fresh = bool(pm_met.get("pid")) or (pm_age is not None and pm_age <= 5)

    pm_info = {
        "pid": pm_met.get("pid"),
        "cpu_s": pm_met.get("cpu_s"),
        "mem_mb": pm_met.get("mem_mb"),
        "uptime_s": pm_met.get("uptime_s"),
        "log_age_min": pm_age,
        "log_file": pm_file,
        "fresh": pm_fresh,
    }

    return {
        "env": _instance_paths(),
        "bridge": {
            "ok": bridge_ok,
            "diag": bridge_diag,
            "pid": bmet.get("pid"),
            "cpu_s": bmet.get("cpu_s"),
            "mem_mb": bmet.get("mem_mb"),
            "uptime_s": bmet.get("uptime_s"),
        },
        "bridge_ui": {
            "pid": umet.get("pid"),
            "cpu_s": umet.get("cpu_s"),
            "mem_mb": umet.get("mem_mb"),
            "uptime_s": umet.get("uptime_s"),
        },
        "strategy_engine": {
            "pid": smet.get("pid"),
            "cpu_s": smet.get("cpu_s"),
            "mem_mb": smet.get("mem_mb"),
            "uptime_s": smet.get("uptime_s"),
            "log_age_min": se_age,
            "log_file": se_file,
            "fresh": se_fresh,
        },
        "executor": {
            "pid": exec_info.get("pid"),
            "cpu_s": exec_info.get("cpu_s"),
            "mem_mb": exec_info.get("mem_mb"),
            "uptime_s": exec_info.get("uptime_s"),
            "log_age_min": exec_age,
            "fresh": exec_fresh,
            "log_file": exec_file,
        },
        "retrain": {
            "pid": retr_info.get("pid"),
            "cpu_s": retr_info.get("cpu_s"),
            "mem_mb": retr_info.get("mem_mb"),
            "uptime_s": retr_info.get("uptime_s"),
            "log_age_min": retr_age,
            "fresh": retr_fresh,
            "log_file": retr_file,
            "locks": [],
        },
        "watcher": {
            "pid": wmet.get("pid"),
            "cpu_s": wmet.get("cpu_s"),
            "mem_mb": wmet.get("mem_mb"),
            "uptime_s": wmet.get("uptime_s"),
            "log_age_min": watch_age,
            "fresh": watch_fresh,
            "log_file": watch_file,
        },
        "emitter": {
            "pid": emet.get("pid"),
            "cpu_s": emet.get("cpu_s"),
            "mem_mb": emet.get("mem_mb"),
            "uptime_s": emet.get("uptime_s"),
            "log_age_min": emitter_age,
            "fresh": emitter_fresh,
            "log_file": emitter_file,
        },
        "monitor": {
            "pid": mmet.get("pid"),
            "cpu_s": mmet.get("cpu_s"),
            "mem_mb": mmet.get("mem_mb"),
            "uptime_s": mmet.get("uptime_s"),
            "log_age_min": mon_age,
            "log_file": mon_file,
            "fresh": mon_fresh,
        },
        "position_manager": pm_info,
    }

# ---------------------------------------------------
# Strategies (schedule + configs)  <-- (isto é o que te faltou na UI)
# ---------------------------------------------------

@router.get("/strategies")
def strategies(include: str = Query("schedule", description="schedule|configs|both")):
    include_set = {s.strip().lower() for s in include.split(",") if s.strip()}
    if not include_set:
        include_set = {"schedule"}

    path = _schedule_file()
    rows: list[dict[str, Any]] = []
    scheduled_keys: set[str] = set()

    # 1) schedule (jobs)
    if path.exists():
        try:
            data = _read_yaml_safe(path) or {}
            for j in (data or {}).get("jobs", []):
                sym = j.get("symbol") or j.get("mt5_symbol") or j.get("instrument")
                tf = j.get("timeframe")
                win = j.get("retrain_every_minutes")
                key = _norm_key(sym, tf)
                scheduled_keys.add(key)
                rows.append({
                    "symbol": sym,
                    "timeframe": tf,
                    "source": "schedule",
                    "window_min": win,
                    "last_run_min": None,
                    "due": None,
                    "next_in_min": None,
                    "config_file": j.get("config_file"),
                    "source_url": None,
                })
        except Exception:
            rows = []

    # 2) configs órfãos
    if "configs" in include_set or "both" in include_set:
        cfg_dir = _configs_dir()
        for p in sorted(cfg_dir.glob("*_config.yaml")):
            stem = p.stem  # XAUUSD_H1_config
            base = stem[:-7] if stem.endswith("_config") else stem
            if "_" not in base:
                continue
            sym, tf = base.split("_", 1)
            key = _norm_key(sym, tf)
            if key in scheduled_keys:
                continue
            rows.append({
                "symbol": sym,
                "timeframe": tf,
                "source": "configs",
                "window_min": None,
                "last_run_min": None,
                "due": None,
                "next_in_min": None,
                "config_file": str(p),
                "source_url": None,
            })

    # 3) last_run / due / next_in
    last_map = _load_last_runs()
    now = time.time()
    for r in rows:
        if r["source"] != "schedule":
            continue
        key = _norm_key(r.get("symbol"), r.get("timeframe"))
        last_ts = last_map.get(key)

        if not last_ts:
            latest_dir = _models_latest_dir(r.get("symbol"), r.get("timeframe"))
            if latest_dir.exists():
                try:
                    last_ts = max(
                        (f.stat().st_mtime for f in latest_dir.rglob("*") if f.is_file()),
                        default=None,
                    )
                except Exception:
                    last_ts = None

        if last_ts:
            last_min = int((now - float(last_ts)) // 60)
            r["last_run_min"] = max(0, last_min)
            win = r.get("window_min")
            if isinstance(win, (int, float)) and win is not None:
                next_in = int(max(0, float(win) - last_min))
                r["next_in_min"] = next_in
                r["due"] = next_in == 0

    return {"rows": rows}

@router.get("/schedule_yaml")
def schedule_yaml():
    p = _schedule_file()
    if not p.exists():
        return PlainTextResponse("retrain.yaml não encontrado", status_code=404)
    return PlainTextResponse(p.read_text(encoding="utf-8"))

@router.get("/view_schedule")
def view_schedule():
    p = _schedule_file()
    if not p.exists():
        return PlainTextResponse("retrain.yaml não encontrado", status_code=404)
    return PlainTextResponse(p.read_text(encoding="utf-8"))

@router.get("/view_config")
def view_config(file: str):
    base = _configs_dir()
    p = _safe_join(base, file)
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="config não encontrado")
    try:
        txt = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        txt = p.read_text(encoding="latin-1", errors="replace")
    return PlainTextResponse(txt)

# ---------------------------------------------------
# State endpoints (legacy UI calls)
# ---------------------------------------------------

@router.get("/executor_state")
def executor_state():
    logs = _logs_dir()
    fresh_min = int(os.getenv("EXEC_FRESH_MAX_AGE_MIN", "5"))
    age_min, used = _log_age_min_multi(logs, ["executor.log", "executor.stdout.log", "executor.stderr.log"])
    m = _last_metrics_for("executor")
    return {
        "fresh": (age_min is not None and age_min <= fresh_min) if age_min is not None else bool(m.get("pid")),
        "age_min": age_min,
        "log_file": used,
        "last_signal_ts": None,
        "pid": m.get("pid"),
        "cpu_s": None,
        "mem_mb": m.get("mem_mb"),
        "uptime_s": m.get("uptime_s"),
    }

@router.get("/scheduler_state")
def scheduler_state():
    logs = _logs_dir()
    fresh_min = int(os.getenv("SCHED_FRESH_MAX_AGE_MIN", "10"))
    age_min, used = _log_age_min_multi(logs, ["retrain.log", "scheduler_retrain.log", "scheduler_retrain.stdout.log", "scheduler_retrain.stderr.log"])
    m = _last_metrics_for("retrain")
    fresh = (age_min is not None and age_min <= fresh_min) or bool(m.get("pid"))
    return {
        "fresh": fresh,
        "age_min": age_min,
        "log_file": used,
        "locks": [],
        "pid": m.get("pid"),
        "cpu_s": None,
        "mem_mb": m.get("mem_mb"),
        "uptime_s": m.get("uptime_s"),
    }

@router.get("/watcher_state")
def watcher_state():
    logs = _logs_dir()
    m = _last_metrics_for("watcher")
    pid = m.get("pid")
    age_min, used = _log_age_min_multi(logs, ["watch_signals.log", "watch_signals.stdout.log", "watch_signals.stderr.log"])
    fresh = bool(pid) or (age_min is not None and age_min <= int(os.getenv("WATCH_FRESH_MAX_AGE_MIN", "5")))
    return {
        "fresh": fresh,
        "age_min": age_min,
        "log_file": used,
        "pid": pid,
        "cpu_s": None,
        "mem_mb": m.get("mem_mb"),
        "uptime_s": m.get("uptime_s"),
    }

@router.get("/emitter_state")
def emitter_state():
    logs = _logs_dir()
    m = _last_metrics_for("emitter")
    pid = m.get("pid")
    age_min, used = _log_age_min_multi(logs, ["emitter.log", "emitter.stdout.log", "emitter.stderr.log"])
    fresh = bool(pid) or (age_min is not None and age_min <= int(os.getenv("EMITTER_FRESH_MAX_AGE_MIN", "5")))
    return {
        "fresh": fresh,
        "age_min": age_min,
        "log_file": used,
        "pid": pid,
        "cpu_s": None,
        "mem_mb": m.get("mem_mb"),
        "uptime_s": m.get("uptime_s"),
    }

# ---------------------------------------------------
# Portfolio
# ---------------------------------------------------

@router.get("/portfolio")
def get_portfolio():
    p = _portfolio_file()
    return _read_yaml_safe(p) if p.exists() else {"version": 1, "top": []}

# ---------------------------------------------------
# Logs & Failed signals
# ---------------------------------------------------

def _tail_text(path: Path, n: int = 200) -> str:
    try:
        if not path.exists():
            return f"[{path}] não encontrado."
        txt = path.read_text(encoding="utf-8", errors="replace")
        lines = txt.splitlines()
        n = max(1, int(n))
        return "\n".join(lines[-n:])
    except Exception as e:
        return f"Erro a ler {path}: {e}"

@router.get("/logs/list")
def logs_list():
    base = _logs_dir()

    def item(p: Path):
        try:
            st = p.stat()
            return {
                "name": p.name,
                "path": str(p),
                "size": st.st_size,
                "mtime": st.st_mtime,
                "age_min": round((time.time() - st.st_mtime) / 60.0, 1),
            }
        except Exception:
            return {"name": p.name, "path": str(p), "size": None, "mtime": None, "age_min": None}

    files: list[Path] = []
    if base.exists():
        for pat in ("*.log", "*.log.*", "*.txt"):
            files.extend(base.glob(pat))

    files.sort(key=lambda p: (p.name, p.stat().st_mtime if p.exists() else 0), reverse=True)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for p in files:
        if p.name in seen:
            continue
        seen.add(p.name)
        out.append(item(p))

    return {"dir": str(base), "files": out}

@router.get("/logs/get")
def logs_get(name: str = "executor.log", n: int = 200):
    base = _logs_dir()
    p = _safe_join(base, name)
    return PlainTextResponse(_tail_text(p, n))

FAILED_DIR_NAME = "failed"

@router.get("/failed/list")
def failed_list(limit: int = 50):
    failed_dir = _signals_dir() / FAILED_DIR_NAME
    items: list[dict[str, Any]] = []
    if not failed_dir.exists():
        return {"dir": str(failed_dir), "items": items}

    cand = list(failed_dir.glob("*.json.work"))
    cand.sort(key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True)
    for f in cand[: max(1, int(limit))]:
        try:
            st = f.stat()
        except Exception:
            continue
        err = failed_dir / (f.name + ".error.txt")
        rec: dict[str, Any] = {
            "file": f.name,
            "mtime": st.st_mtime,
            "age_min": round((time.time() - st.st_mtime) / 60.0, 1),
            "size": st.st_size,
            "has_error": err.exists(),
            "error_name": err.name if err.exists() else None,
        }
        if err.exists():
            try:
                preview = err.read_text(encoding="utf-8", errors="replace").splitlines()[:5]
                rec["error_preview"] = "\n".join(preview)
            except Exception:
                rec["error_preview"] = None
        items.append(rec)

    return {"dir": str(failed_dir), "items": items}

@router.get("/failed/get")
def failed_get(file: str):
    failed_dir = _signals_dir() / FAILED_DIR_NAME
    p = _safe_join(failed_dir, file)
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="ficheiro não encontrado")
    try:
        txt = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        txt = p.read_text(encoding="latin-1", errors="replace")
    return PlainTextResponse(txt)

@router.post("/failed/retry")
def failed_retry(payload: Dict[str, Any]):
    name = str(payload.get("file") or "")
    if not name.endswith(".json.work"):
        raise HTTPException(status_code=400, detail="esperava *.json.work")

    failed_dir = _signals_dir() / FAILED_DIR_NAME
    src = _safe_join(failed_dir, name)
    if not src.exists():
        raise HTTPException(status_code=404, detail="work file não existe")

    inbox = _signals_dir() / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)

    dst = inbox / name.replace(".json.work", ".json")
    try:
        if dst.exists():
            dst.unlink(missing_ok=True)  # type: ignore[arg-type]
        src.replace(dst)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"falha a mover para inbox: {e}")

    err = _safe_join(failed_dir, name + ".error.txt")
    if err.exists():
        arch = _signals_dir() / "archive"
        arch.mkdir(parents=True, exist_ok=True)
        try:
            err.replace(arch / err.name)
        except Exception:
            pass

    return {"ok": True, "moved_to": str(dst)}