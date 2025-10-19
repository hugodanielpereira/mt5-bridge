# app/ui/routes/api_dash.py
from __future__ import annotations
import os
import json
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional, List
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse

router = APIRouter(prefix="/ui/api", tags=["ui-dash"])

# ---------------------------------------------------
# Helpers
# ---------------------------------------------------
def _apps_root() -> Path:
    # 1) .env oficial
    env = os.getenv("PROJECT_DIR")
    if env:
        return Path(env)
    # 2) compat legado
    env2 = os.getenv("MLSL_APPS_DIR")
    if env2:
        return Path(env2)
    # 3) heurística
    here = Path(__file__).resolve()
    bridges_dir = here.parents[4]
    root = bridges_dir.parent
    return root / "apps" / "ml-strategy-lab"

def _python_exe() -> str:
    return os.getenv("VENV_PY") or "python"

def _schedule_file() -> Path:
    env = os.getenv("RETRAIN_SCHEDULE_FILE")
    if env:
        return Path(env)
    return _apps_root() / "outputs" / "live" / "schedules" / "retrain.yaml"

def _timestamps_file() -> Path:
    env = os.getenv("RETRAIN_TIMESTAMPS_FILE")
    if env:
        return Path(env)
    return _apps_root() / "outputs" / "live" / "schedules" / "retrain.timestamps.json"

def _models_latest_dir(symbol: str | None, timeframe: str | None) -> Path:
    s = (symbol or "").strip().upper()
    tf = (timeframe or "").strip().upper()
    return _apps_root() / "outputs" / "live" / "models" / s / tf / "latest"

def _signals_dir() -> Path:
    env = os.getenv("SIGNALS_DIR")
    if env:
        return Path(env)
    return _apps_root() / "outputs" / "live" / "signals"

def _logs_dir() -> Path:
    return _apps_root() / "outputs" / "live" / "logs"

def _norm_key(symbol: str | None, timeframe: str | None) -> str:
    s = (symbol or "").strip().upper()
    tf = (timeframe or "").strip().upper()
    return f"{s}:{tf}" if s and tf else ""

def _load_last_runs() -> dict[str, float]:
    """Lê timestamps por (symbol,timeframe) de JSON: { "BTCUSDT:H2": 1729099200, ... }"""
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

def _run(cmd: list[str] | str, cwd: Optional[Path] = None, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    shell = isinstance(cmd, str)
    _env = (env or os.environ).copy()
    # Força UTF-8 (Windows) para não estragar acentos
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

# ================== Helpers de processos (psutil) ==================
def _ps():
    try:
        import psutil  # type: ignore
        return psutil
    except Exception:
        return None

def _file_age_min(path: Path) -> Optional[float]:
    try:
        return round((time.time() - path.stat().st_mtime) / 60.0, 1)
    except Exception:
        return None

# PID dirs e candidatos (pids/ e runs/)
PID_DIR_PIDS = _apps_root() / "outputs" / "live" / "pids"
PID_DIR_RUNS = _apps_root() / "outputs" / "live" / "runs"

# --- PID readers simples (compat com nomes .pid) -------------------
def _read_pid(name: str) -> Optional[int]:
    """
    Lê um PID por nome de ficheiro (ex.: 'mlsl-executor.pid') procurando
    em outputs/live/pids e outputs/live/runs.
    """
    for base in (PID_DIR_PIDS, PID_DIR_RUNS):
        p = base / name
        try:
            if p.exists():
                t = p.read_text(encoding="utf-8").strip()
                if t.isdigit():
                    return int(t)
        except Exception:
            pass
    return None

# --- métricas de um PID (forma curta usada no /status) -------------
def _proc_info(pid: Optional[int]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"pid": None, "cpu_s": None, "mem_mb": None, "uptime_s": None}
    if not pid:
        return out
    ps = _ps()
    if not ps:
        return out
    try:
        p = ps.Process(pid)
        with p.oneshot():
            ct = p.cpu_times()
            mem = p.memory_info()
            out["pid"] = pid
            out["cpu_s"] = round((ct.user + ct.system), 1) if ct else None
            out["mem_mb"] = round(mem.rss / (1024*1024), 1) if mem else None
            out["uptime_s"] = max(0.0, time.time() - (p.create_time() or time.time()))
    except Exception:
        pass
    return out

def _pid_candidates(role: str) -> List[Path]:
    r = role.strip().lower()
    cands: List[Path] = []
    if r == "executor":
        cands += [
            PID_DIR_PIDS / "mlsl-executor.pid",
            PID_DIR_RUNS / "executor.pid",
            PID_DIR_RUNS / "mlsl-executor.pid",
            PID_DIR_RUNS / "services.executor.pid",
        ]
    elif r in ("retrain", "scheduler", "scheduler_retrain"):
        cands += [
            PID_DIR_PIDS / "mlsl-retrain.pid",
            PID_DIR_RUNS / "retrain.pid",
            PID_DIR_RUNS / "scheduler_retrain.pid",
            PID_DIR_RUNS / "mlsl-retrain.pid",
        ]
    elif r == "watcher":
        cands += [
            PID_DIR_PIDS / "mlsl-watcher.pid",
            PID_DIR_RUNS / "watcher.pid",
            PID_DIR_RUNS / "mlsl-watcher.pid",
        ]
    return cands

def _read_pid_multi(role: str) -> Optional[int]:
    for p in _pid_candidates(role):
        try:
            if p.exists():
                t = p.read_text(encoding="utf-8").strip()
                if t.isdigit():
                    return int(t)
        except Exception:
            continue
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
    """Procura por cmdline se não houver pidfile."""
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

def _proc_metrics(p) -> Dict[str, Any]:
    ps = _ps()
    if not ps or not p:
        return {}
    try:
        ct = p.cpu_times()
        mem = p.memory_info()
        return {
            "pid": p.pid,
            "cpu_s": round((ct.user + ct.system), 1) if ct else None,
            "mem_mb": round(mem.rss / (1024*1024), 1) if mem else None,
            "uptime_s": max(0.0, time.time() - (p.create_time() or time.time())),
        }
    except Exception:
        return {}

def _fresh_from_log_or_pid(age_min: Optional[float], max_age_min: float, has_pid: bool) -> bool:
    # Se há log recente, usa-o. Se não há log, considera vivo se houver PID.
    if age_min is not None:
        return age_min <= max_age_min
    return has_pid

def _is_wrapper_name(name: str | None) -> bool:
    if not name:
        return False
    n = name.lower()
    return any(x in n for x in ("pwsh", "powershell", "cmd.exe", "cmd", "conhost"))

def _promote_to_child_with_needles(p, needles: List[str]):
    """Se p for um wrapper (pwsh/powershell/cmd), procura um filho Python que
    contenha alguma das 'needles' na cmdline. Caso encontre, devolve o filho."""
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
        # procura recursivamente: primeiro nível costuma ser suficiente
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
    """
    Tenta localizar o processo real:
    1) pelo PID (pidfile);
    2) se não existir, por cmdline (needles);
    3) se for um wrapper (pwsh/powershell/cmd), promove para o filho Python.
    """
    p = _proc_from_pid(pid)
    if not p:
        p = _find_proc(needles)
    if p:
        p = _promote_to_child_with_needles(p, needles)
    return p

# ---------------------------------------------------
# Retrain controlado pela UI (NO-EMIT)
# ---------------------------------------------------
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
    return env

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
# Dados p/ Dashboard
# ---------------------------------------------------
LOGS_DIR  = _apps_root() / "outputs" / "live" / "logs"

def _log_age_min(path: Path) -> Optional[float]:
    try:
        if not path.exists(): 
            return None
        return round((time.time() - path.stat().st_mtime)/60.0, 1)
    except Exception:
        return None

def _bridge_proc():
    # procura o uvicorn do bridge
    return _find_proc([
        "uvicorn app.main:app",   # forma que estás a lançar
        "uvicorn app.main",       # fallback
        "app.main:app",           # extra
    ])

@router.get("/status")
def status():
    # ---------- BRIDGE ----------
    bridge_ok = False
    bridge_diag: Dict[str, Any] | None = None
    try:
        from app.shareweb import get_service
        svc = get_service()
        diag = svc.diag() if svc else {"ok": False, "reason": "svc_missing"}
        bridge_diag = diag
        bridge_ok = bool(diag.get("ok"))
    except Exception as e:
        bridge_diag = {"ok": False, "reason": f"diag_failed: {e}"}

    # métricas do processo do bridge (uvicorn)
    bp = _find_proc([
        "uvicorn app.main:app",
        "uvicorn app.main",
        "app.main:app",
    ])
    bmet = _proc_metrics(bp) if bp else {"pid": None, "cpu_s": None, "mem_mb": None, "uptime_s": None}

    # --- EXECUTOR ---
    exec_pid = _read_pid("mlsl-executor.pid") or _read_pid_multi("executor")
    exec_needles = ["services.executor.loop", "executor.loop", "run_execute_live_mt5"]
    pe = _locate_proc(exec_pid, exec_needles)
    exec_info = _proc_metrics(pe) if pe else {"pid": None, "cpu_s": None, "mem_mb": None, "uptime_s": None}
    exec_log_age = _log_age_min(LOGS_DIR / "executor.log")
    exec_fresh = (exec_log_age is not None) and (exec_log_age <= float(os.getenv("EXEC_FRESH_MAX_AGE_MIN","5")))

    # --- RETRAIN ---
    retr_pid = _read_pid("mlsl-retrain.pid") or _read_pid_multi("retrain")
    retr_needles = ["services.scheduler_retrain", "scheduler_retrain", "tools.retrain_incremental"]
    pr = _locate_proc(retr_pid, retr_needles)
    retr_info = _proc_metrics(pr) if pr else {"pid": None, "cpu_s": None, "mem_mb": None, "uptime_s": None}
    retr_log_age = _log_age_min(LOGS_DIR / "scheduler_retrain.log")
    retr_fresh = (retr_log_age is not None) and (retr_log_age <= float(os.getenv("SCHED_FRESH_MAX_AGE_MIN","10")))

    # --- WATCHER ---
    watch_pid = _read_pid_multi("watcher")
    watch_needles = ["tools.watch_signals", "watch_signals.py", "watcher"]
    wp = _locate_proc(watch_pid, watch_needles) or _find_proc(watch_needles)
    wmet = _proc_metrics(wp) if wp else {"pid": None, "cpu_s": None, "mem_mb": None, "uptime_s": None}
    watch_log_age = _file_age_min(_logs_dir() / "watch_signals.log")
    watch_fresh = bool(wmet.get("pid")) or (watch_log_age is not None and watch_log_age <= 5)

    return {
        "bridge": {
            "ok": bridge_ok,
            "diag": bridge_diag,
            "pid": bmet.get("pid"),
            "cpu_s": bmet.get("cpu_s"),
            "mem_mb": bmet.get("mem_mb"),
            "uptime_s": bmet.get("uptime_s"),
        },
        "executor": {
            "pid": exec_info.get("pid"), "cpu_s": exec_info.get("cpu_s"),
            "mem_mb": exec_info.get("mem_mb"), "uptime_s": exec_info.get("uptime_s"),
            "log_age_min": exec_log_age, "fresh": exec_fresh
        },
        "retrain": {
            "pid": retr_info.get("pid"), "cpu_s": retr_info.get("cpu_s"),
            "mem_mb": retr_info.get("mem_mb"), "uptime_s": retr_info.get("uptime_s"),
            "log_age_min": retr_log_age, "fresh": retr_fresh, "locks": []
        },
        "watcher": {
            "pid": wmet.get("pid"),
            "cpu_s": wmet.get("cpu_s"),
            "mem_mb": wmet.get("mem_mb"),
            "uptime_s": wmet.get("uptime_s"),
            "log_age_min": watch_log_age,
            "fresh": watch_fresh,
        },
    }

@router.get("/strategies")
def strategies():
    path = _schedule_file()
    rows: list[dict[str, Any]] = []

    # 1) Lê schedule YAML
    if path.exists():
        try:
            import yaml  # opcional
            data = yaml.safe_load(path.read_text(encoding="utf-8")) if yaml else None
            for j in (data or {}).get("jobs", []):
                sym = j.get("symbol") or j.get("mt5_symbol") or j.get("instrument")
                tf  = j.get("timeframe")
                win = j.get("retrain_every_minutes")
                rows.append({
                    "symbol": sym,
                    "timeframe": tf,
                    "source": "schedule",
                    "window_min": win,
                    "last_run_min": None,   # preenchido no passo 2
                    "due": None,            # preenchido no passo 2
                    "next_in_min": None,    # preenchido no passo 2
                    "config_file": j.get("config_file"),
                    "source_url": None,
                })
        except Exception:
            rows = []

    # 2) Enriquecer com timestamps → fallback para mtime do ficheiro MAIS RECENTE
    last_map = _load_last_runs()
    now = time.time()
    for r in rows:
        key = _norm_key(r.get("symbol"), r.get("timeframe"))
        last_ts = last_map.get(key)

        if not last_ts:
            latest_dir = _models_latest_dir(r.get("symbol"), r.get("timeframe"))
            if latest_dir.exists():
                try:
                    most_recent = max(
                        (f.stat().st_mtime for f in latest_dir.rglob('*') if f.is_file()),
                        default=None
                    )
                    last_ts = most_recent
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
            else:
                r["due"] = None
        else:
            r["last_run_min"] = None
            r["next_in_min"] = None
            r["due"] = None

    return {"rows": rows}

@router.get("/schedule_yaml")
def schedule_yaml():
    p = _schedule_file()
    if not p.exists():
        return PlainTextResponse("retrain.yaml não encontrado", status_code=404)
    return PlainTextResponse(p.read_text(encoding="utf-8"))

@router.get("/executor_state")
def executor_state():
    fresh_min = int(os.getenv("EXEC_FRESH_MAX_AGE_MIN", "5"))
    log = _logs_dir() / "executor.log"
    age_min = _file_age_min(log)
    fresh = (age_min is not None) and (age_min <= fresh_min)

    pid = _read_pid_multi("executor")
    p = _proc_from_pid(pid) or _find_proc([
        "services.executor.loop", "executor.loop", "run_execute_live_mt5"
    ])
    met = _proc_metrics(p) if p else {}

    return {
        "fresh": fresh if age_min is not None else bool(met.get("pid")),
        "age_min": age_min,
        "last_signal_ts": None,
        "pid": met.get("pid"),
        "cpu_s": met.get("cpu_s"),
        "mem_mb": met.get("mem_mb"),
        "uptime_s": met.get("uptime_s"),
    }

@router.get("/scheduler_state")
def scheduler_state():
    fresh_min = int(os.getenv("SCHED_FRESH_MAX_AGE_MIN", "10"))
    log = _logs_dir() / "scheduler_retrain.log"
    age_min = _file_age_min(log)
    fresh = (age_min is not None) and (age_min <= fresh_min)

    pid = _read_pid_multi("retrain")
    p = _proc_from_pid(pid) or _find_proc([
        "services.scheduler_retrain", "scheduler_retrain", "tools.retrain_incremental"
    ])
    met = _proc_metrics(p) if p else {}

    # locks (se existirem)
    locks: list[str] = []
    for lf in [
        _apps_root() / "outputs" / "live" / "locks" / "scheduler_retrain.lock",
        _apps_root() / "outputs" / "live" / "scheduler_retrain.lock",
    ]:
        if lf.exists():
            locks.append(str(lf))

    return {
        "fresh": fresh if age_min is not None else bool(met.get("pid")),
        "age_min": age_min,
        "locks": locks,
        "pid": met.get("pid"),
        "cpu_s": met.get("cpu_s"),
        "mem_mb": met.get("mem_mb"),
        "uptime_s": met.get("uptime_s"),
    }

@router.get("/watcher_state")
def watcher_state():
    p = _find_proc(["tools.watch_signals", "watch_signals.py", "watcher"])
    met = _proc_metrics(p) if p else {}
    log = _logs_dir() / "watch_signals.log"
    age_min = _file_age_min(log)
    fresh = bool(met.get("pid")) or (age_min is not None and age_min <= 5)
    return {
        "fresh": fresh,
        "age_min": age_min,
        "pid": met.get("pid"),
        "cpu_s": met.get("cpu_s"),
        "mem_mb": met.get("mem_mb"),
        "uptime_s": met.get("uptime_s"),
    }

# === Logs & Failed Signals =========================================
LOG_DIR    = _apps_root() / "outputs" / "live" / "logs"
FAILED_DIR = _apps_root() / "outputs" / "live" / "signals" / "failed"

def _safe_join(base: Path, name: str) -> Path:
    """
    Join `name` to `base` safely (no traversal). Only basename is honored.
    """
    p = (base / Path(name).name).resolve()
    base_res = base.resolve()
    if not str(p).startswith(str(base_res)):
        raise HTTPException(status_code=400, detail="invalid path")
    return p

def _tail_text(path: Path, n: int = 200) -> str:
    """
    Simple tail for small/medium logs. Returns last N lines.
    """
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
    if LOG_DIR.exists():
        # include main and rotated variants
        for pat in ("*.log", "*.log.*", "*.txt"):
            files.extend(LOG_DIR.glob(pat))

    # sort newest first and de-dup by file name (keep first/newest)
    files.sort(key=lambda p: (p.name, p.stat().st_mtime if p.exists() else 0), reverse=True)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for p in files:
        if p.name in seen:
            continue
        seen.add(p.name)
        out.append(item(p))

    return {"dir": str(LOG_DIR), "files": out}

@router.get("/logs/get")
def logs_get(name: str = "executor.log", n: int = 200):
    from fastapi.responses import PlainTextResponse
    p = _safe_join(LOG_DIR, name)
    return PlainTextResponse(_tail_text(p, n))

@router.get("/failed/list")
def failed_list(limit: int = 50):
    items: list[dict[str, Any]] = []
    if not FAILED_DIR.exists():
        return {"dir": str(FAILED_DIR), "items": items}

    # list only *.json.work; pair with possible *.error.txt
    cand = list(FAILED_DIR.glob("*.json.work"))
    # newest first
    cand.sort(key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True)
    for f in cand[: max(1, int(limit))]:
        try:
            st = f.stat()
        except Exception:
            continue
        err = FAILED_DIR / (f.name + ".error.txt")  # e.g., *.json.work.error.txt
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

    return {"dir": str(FAILED_DIR), "items": items}

@router.get("/failed/get")
def failed_get(file: str):
    from fastapi.responses import PlainTextResponse
    # Allow reading both *.json.work and its *.error.txt companion
    p = _safe_join(FAILED_DIR, file)
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="ficheiro não encontrado")
    try:
        txt = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        txt = p.read_text(encoding="latin-1", errors="replace")
    return PlainTextResponse(txt)

@router.post("/failed/retry")
def failed_retry(payload: Dict[str, Any]):
    """
    Reenvia um *.json.work de FAILED → signals/inbox como *.json (remove .work).
    Move o erro associado (*.json.work.error.txt) para signals/archive/.
    """
    name = str(payload.get("file") or "")
    if not name.endswith(".json.work"):
        raise HTTPException(status_code=400, detail="esperava *.json.work")

    src = _safe_join(FAILED_DIR, name)
    if not src.exists():
        raise HTTPException(status_code=404, detail="work file não existe")

    inbox = _signals_dir() / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)

    dst = inbox / name.replace(".json.work", ".json")
    try:
        # overwrite if exists (idempotent retry)
        if dst.exists():
            dst.unlink(missing_ok=True)  # type: ignore[arg-type]
        src.replace(dst)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"falha a mover para inbox: {e}")

    # move the paired error to archive (if present)
    err = _safe_join(FAILED_DIR, name + ".error.txt")
    if err.exists():
        arch = _signals_dir() / "archive"
        arch.mkdir(parents=True, exist_ok=True)
        try:
            err.replace(arch / err.name)
        except Exception:
            # best-effort: ignore
            pass

    return {"ok": True, "moved_to": str(dst)}

# === Viewers (Schedule & Configs) ===========================================
CONFIGS_DIR = _apps_root() / "outputs" / "live" / "configs"

def _safe_join(base: Path, name: str) -> Path:
    """
    Join `name` to `base` safely (no traversal). Only basename is honored.
    """
    p = (base / Path(name).name).resolve()
    base_res = base.resolve()
    if not str(p).startswith(str(base_res)):
        raise HTTPException(status_code=400, detail="invalid path")
    return p

@router.get("/view_schedule")
def view_schedule():
    """
    Mostra o retrain.yaml (texto puro). Usado pelo botão 'Ver Schedule'.
    """
    p = _schedule_file()
    if not p.exists():
        return PlainTextResponse("retrain.yaml não encontrado", status_code=404)
    return PlainTextResponse(p.read_text(encoding="utf-8", errors="replace"))

@router.get("/view_config")
def view_config(file: str):
    """
    Mostra um ficheiro de config individual a partir de outputs/live/configs.
    Ex.: ?file=BTCUSDT_H2.yaml
    """
    if not CONFIGS_DIR.exists():
        raise HTTPException(status_code=404, detail="configs dir não encontrado")
    path = _safe_join(CONFIGS_DIR, file)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="config não encontrado")
    try:
        txt = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        txt = path.read_text(encoding="latin-1", errors="replace")
    return PlainTextResponse(txt)