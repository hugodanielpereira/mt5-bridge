# app/ui/routes/api_dash.py
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

router = APIRouter(prefix="/ui/api", tags=["ui-dash"])

# ---------------------------------------------------
# Helpers
# ---------------------------------------------------
def _apps_root() -> Path:
    # 1) .env oficial (ideal: PROJECT_DIR = .../apps/ml-strategy-lab)
    env = os.getenv("PROJECT_DIR")
    if env:
        return Path(env)
    # 2) compat legado
    env2 = os.getenv("MLSL_APPS_DIR")
    if env2:
        return Path(env2)
    # 3) heurística (quando tudo falha)
    here = Path(__file__).resolve()
    # .../bridges/mt5-bridge/app/ui/routes/api_dash.py
    # queremos .../apps/ml-strategy-lab
    bridges_dir = here.parents[4]  # mt5-bridge
    root = bridges_dir.parent      # bridges
    return root.parent / "apps" / "ml-strategy-lab"

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
    # ⚠️ Estes logs são os do LAB (não os da raiz ~/Trading/logs)
    env = os.getenv("LAB_LOGS_DIR")
    if env:
        return Path(env)
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

def _portfolio_file() -> Path:
    env = os.getenv("PORTFOLIO_FILE")
    if env:
        return Path(env)
    return _apps_root() / "outputs" / "live" / "portfolio" / "top10.yaml"

def _retrain_yaml_file() -> Path:
    return _schedule_file()

def _read_yaml_safe(p: Path) -> dict:
    try:
        import yaml  # type: ignore
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}

# --- State files (governor/promoter) ---
def _state_dir() -> Path:
    return _apps_root() / "outputs" / "live" / "state"

def _read_json_safe(p: Path) -> dict:
    try:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:
        pass
    return {}

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
    elif r == "emitter":
        cands += [
            PID_DIR_PIDS / "mlsl-emitter.pid",
            PID_DIR_RUNS / "emitter.pid",
            PID_DIR_RUNS / "mlsl-emitter.pid",
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

# === util partilhado: join seguro ==================================
def _safe_join(base: Path, name: str) -> Path:
    """
    Join `name` to `base` safely (no traversal). Only basename is honored.
    """
    p = (base / Path(name).name).resolve()
    base_res = base.resolve()
    if not str(p).startswith(str(base_res)):
        raise HTTPException(status_code=400, detail="invalid path")
    return p

# ================== MÉTRICAS via CSV (proc_monitor) =================

def _metrics_csv() -> Path:
    env = os.getenv("PROC_METRICS_FILE")
    if env:
        return Path(env)
    return _apps_root() / "outputs" / "live" / "metrics" / "proc_metrics.csv"

def _last_metrics_for(service: str) -> Dict[str, Any]:
    """
    Lê o último registo de métricas para um dado 'service' a partir do
    proc_metrics.csv gerado pelo tools.proc_monitor.
    """
    path = _metrics_csv()
    if not path.exists():
        return {}
    try:
        import csv  # lazy import para não falhar se faltar
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
LOGS_DIR  = _logs_dir()

def _log_age_min(path: Path) -> Optional[float]:
    try:
        if not path.exists():
            return None
        return round((time.time() - path.stat().st_mtime)/60.0, 1)
    except Exception:
        return None

def _log_age_min_multi(base: Path, names: list[str]) -> tuple[Optional[float], Optional[str]]:
    """
    Devolve (age_min, file_used) do primeiro ficheiro existente em 'names'
    (ordem de prioridade) calculado a partir de 'base'. Se nenhum existir,
    devolve (None, None).
    """
    for name in names:
        p = base / name
        age = _log_age_min(p)
        if age is not None:
            return age, str(p)
    return None, None

STATE_DIR = _state_dir()

def _read_state_json(name: str) -> dict:
    """Lê um ficheiro de estado JSON do outputs/live/state (ex: promoter-state.json)."""
    try:
        p = STATE_DIR / name
        if not p.exists():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}

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

    # tenta primeiro por pidfile; cai para cmdline; promove se for wrapper
    bridge_pid = _read_pid("mlsl-bridge.pid")
    bridge_needles = [
        "uvicorn app.main:app",
        "uvicorn app.main",
        "-m uvicorn",
        "app.main:app",
        "app\\main.py",
    ]
    bp = _locate_proc(bridge_pid, bridge_needles) or _find_proc(bridge_needles)

    def _pmet(p): return _proc_metrics(p) if p else {"pid": None, "cpu_s": None, "mem_mb": None, "uptime_s": None}
    bmet = _pmet(bp)

    # CSV metrics override (proc_monitor)
    bcsv = _last_metrics_for("bridge")
    if bcsv:
        bmet["pid"] = bcsv.get("pid") or bmet.get("pid")
        # não temos cpu_s no CSV, mas temos cpu_pct se quiseres usar no futuro
        bmet["mem_mb"] = bcsv.get("mem_mb") or bmet.get("mem_mb")
        bmet["uptime_s"] = bcsv.get("uptime_s") or bmet.get("uptime_s")

    # --- EXECUTOR ---
    exec_pid = _read_pid("mlsl-executor.pid") or _read_pid_multi("executor")
    exec_needles = ["services.executor.loop", "executor.loop", "run_execute_live_mt5"]
    pe = _locate_proc(exec_pid, exec_needles)
    exec_info = _pmet(pe)
    exec_csv = _last_metrics_for("executor")
    if exec_csv:
        exec_info["pid"] = exec_csv.get("pid") or exec_info.get("pid")
        exec_info["mem_mb"] = exec_csv.get("mem_mb") or exec_info.get("mem_mb")
        exec_info["uptime_s"] = exec_csv.get("uptime_s") or exec_info.get("uptime_s")
    exec_age, exec_file = _log_age_min_multi(
        LOGS_DIR,
        ["executor.log", "executor.stdout.log", "executor.stderr.log"]
    )
    exec_fresh = (exec_age is not None) and (exec_age <= float(os.getenv("EXEC_FRESH_MAX_AGE_MIN","5")))

    # --- RETRAIN ---
    retr_pid = _read_pid("mlsl-retrain.pid") or _read_pid_multi("retrain")
    retr_needles = [
        "services.retrain",
        "services.scheduler_retrain",
        "scheduler_retrain",
        "tools.retrain_incremental",
    ]
    pr = _locate_proc(retr_pid, retr_needles)
    retr_info = _pmet(pr)
    retr_age, retr_file = _log_age_min_multi(
        _logs_dir(),
        ["retrain.log", "scheduler_retrain.log", "scheduler_retrain.stdout.log", "scheduler_retrain.stderr.log"]
    )
    retr_fresh = (retr_age is not None) and (retr_age <= float(os.getenv("SCHED_FRESH_MAX_AGE_MIN","10")))
    retr_info = _pmet(pr)
    retr_csv = _last_metrics_for("retrain")
    if retr_csv:
        retr_info["pid"] = retr_csv.get("pid") or retr_info.get("pid")
        retr_info["mem_mb"] = retr_csv.get("mem_mb") or retr_info.get("mem_mb")
        retr_info["uptime_s"] = retr_csv.get("uptime_s") or retr_info.get("uptime_s")
    retr_age, retr_file = _log_age_min_multi(
        LOGS_DIR,
        ["scheduler_retrain.log", "scheduler_retrain.stdout.log", "scheduler_retrain.stderr.log"]
    )
    retr_fresh = (retr_age is not None) and (retr_age <= float(os.getenv("SCHED_FRESH_MAX_AGE_MIN","10")))

    # --- WATCHER ---
    watch_pid = _read_pid_multi("watcher")
    watch_needles = ["tools.watch_signals", "watch_signals.py", "watcher"]
    wp = _locate_proc(watch_pid, watch_needles) or _find_proc(watch_needles)
    wmet = _pmet(wp)
    wcsv = _last_metrics_for("watcher")
    if wcsv:
        wmet["pid"] = wcsv.get("pid") or wmet.get("pid")
        wmet["mem_mb"] = wcsv.get("mem_mb") or wmet.get("mem_mb")
        wmet["uptime_s"] = wcsv.get("uptime_s") or wmet.get("uptime_s")
    watch_age, watch_file = _log_age_min_multi(
        LOGS_DIR,
        ["watch_signals.log", "watch_signals.stdout.log", "watch_signals.stderr.log"]
    )
    watch_fresh = bool(wmet.get("pid")) or (watch_age is not None and watch_age <= int(os.getenv("WATCH_FRESH_MAX_AGE_MIN","5")))

    # --- EMITTER ---
    emit_pid = _read_pid("mlsl-emitter.pid") or _read_pid_multi("emitter")
    emit_needles = ["services.emitter.loop", "emitter.loop"]
    ep = _locate_proc(emit_pid, emit_needles) or _find_proc(emit_needles)
    emet = _pmet(ep)
    ecsv = _last_metrics_for("emitter")
    if ecsv:
        emet["pid"] = ecsv.get("pid") or emet.get("pid")
        emet["mem_mb"] = ecsv.get("mem_mb") or emet.get("mem_mb")
        emet["uptime_s"] = ecsv.get("uptime_s") or emet.get("uptime_s")
    emitter_age, emitter_file = _log_age_min_multi(
        LOGS_DIR,
        ["emitter.log", "emitter.stdout.log", "emitter.stderr.log"]
    )
    emitter_fresh = bool(emet.get("pid")) or (emitter_age is not None and emitter_age <= int(os.getenv("EMITTER_FRESH_MAX_AGE_MIN","5")))

    # --- GOVERNOR ---
    gov_pid = _read_pid("mlsl-governor.pid")
    gov_needles = ["tools.asset_governor", "asset_governor.py", "governor loop"]
    pg = _locate_proc(gov_pid, gov_needles) or _find_proc(gov_needles)
    gov_info = _pmet(pg)
    gcsv = _last_metrics_for("governor")
    if gcsv:
        gov_info["pid"] = gcsv.get("pid") or gov_info.get("pid")
        gov_info["mem_mb"] = gcsv.get("mem_mb") or gov_info.get("mem_mb")
        gov_info["uptime_s"] = gcsv.get("uptime_s") or gov_info.get("uptime_s")
    gov_age, gov_file = _log_age_min_multi(
        LOGS_DIR, ["governor.log","asset_governor.log","governor.stdout.log","governor.stderr.log"]
    )
    gov_fresh = bool(gov_info.get("pid")) or (gov_age is not None and gov_age <= int(os.getenv("GOV_FRESH_MAX_AGE_MIN","15")))

    # ler governor-state.json (min_pf/top_k/itens)
    gov_state = _read_json_safe(_state_dir() / "governor-state.json")
    gov_items = gov_state.get("items") or []
    gov_selected = len(gov_items)
    gov_topk = gov_state.get("top_k") or gov_state.get("selection",{}).get("top_k")
    gov_min_pf = (gov_state.get("selection") or {}).get("min_pf")

    # --- PROMOTER ---
    pro_pid = _read_pid("mlsl-promoter.pid")
    pro_needles = ["tools.promote_model","promote_model.py","promoter loop"]
    pp = _locate_proc(pro_pid, pro_needles) or _find_proc(pro_needles)
    pro_info = _pmet(pp)
    pcsv = _last_metrics_for("promoter")
    if pcsv:
        pro_info["pid"] = pcsv.get("pid") or pro_info.get("pid")
        pro_info["mem_mb"] = pcsv.get("mem_mb") or pro_info.get("mem_mb")
        pro_info["uptime_s"] = pcsv.get("uptime_s") or pro_info.get("uptime_s")
    pro_age, pro_file = _log_age_min_multi(
        LOGS_DIR, ["promoter.log","promote.log","promoter.stdout.log","promoter.stderr.log"]
    )
    pro_fresh = bool(pro_info.get("pid")) or (pro_age is not None and pro_age <= int(os.getenv("PROMO_FRESH_MAX_AGE_MIN","30")))

    # ler promoter-state.json (scan_every_min/require_model_ok)
    pro_state = _read_json_safe(_state_dir() / "promoter-state.json")
    scan_every_min = pro_state.get("scan_every_min")
    require_model_ok = pro_state.get("require_model_ok")

    # --- POSITION MANAGER (novo no status principal) ---
    pm_csv = _last_metrics_for("position_manager")
    pm_pid = pm_csv.get("pid") if pm_csv else None
    pm_info = {
        "pid": pm_pid,
        "cpu_s": None,  # não temos cpu_s no CSV
        "mem_mb": pm_csv.get("mem_mb") if pm_csv else None,
        "uptime_s": pm_csv.get("uptime_s") if pm_csv else None,
        "log_age_min": None,
        "fresh": bool(pm_pid),
        "log_file": None,
    }

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
        "governor": {
            "pid": gov_info.get("pid"),
            "cpu_s": gov_info.get("cpu_s"),
            "mem_mb": gov_info.get("mem_mb"),
            "uptime_s": gov_info.get("uptime_s"),
            "log_age_min": gov_age,
            "fresh": gov_fresh,
            "log_file": gov_file,
            # novos:
            "selected_assets": gov_items,
            "top_k": gov_topk,
            "min_pf": gov_min_pf,
        },
        "promoter": {
            "pid": pro_info.get("pid"),
            "cpu_s": pro_info.get("cpu_s"),
            "mem_mb": pro_info.get("mem_mb"),
            "uptime_s": pro_info.get("uptime_s"),
            "log_age_min": pro_age,
            "fresh": pro_fresh,
            "log_file": pro_file,
            # novos:
            "scan_every_min": scan_every_min,
            "require_model_ok": require_model_ok,
        },
        "position_manager": pm_info,
    }

# --- colar no app/ui/routes/api_dash.py (substitui o def strategies) ---
def _configs_dir() -> Path:
    env = os.getenv("CONFIGS_DIR")
    if env:
        return Path(env)
    return _apps_root() / "outputs" / "live" / "configs"

def _yaml_safe(p: Path):
    try:
        import yaml
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}

@router.get("/strategies")
def strategies(include: str = Query("schedule", description="schedule|configs|both")):
    include_set = {s.strip().lower() for s in include.split(",") if s.strip()}
    if not include_set:
        include_set = {"schedule"}

    path = _schedule_file()
    rows: list[dict[str, Any]] = []
    scheduled_keys: set[str] = set()

    # 1) Sempre: ler o schedule
    if path.exists():
        try:
            import yaml
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for j in (data or {}).get("jobs", []):
                sym = j.get("symbol") or j.get("mt5_symbol") or j.get("instrument")
                tf  = j.get("timeframe")
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

    # 2) Opcional: incluir órfãos do diretório de configs
    if "configs" in include_set or "both" in include_set:
        cfg_dir = _configs_dir()
        for p in sorted(cfg_dir.glob("*_config.yaml")):
            # inferir symbol/timeframe a partir do nome
            stem = p.stem  # ex: XAUUSD_M15_config
            base = stem[:-7] if stem.endswith("_config") else stem
            if "_" not in base:
                continue
            sym, tf = base.split("_", 1)
            key = _norm_key(sym, tf)
            if key in scheduled_keys:
                continue  # já está no schedule
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

    # 3) Enriquecer last_run/next_in para os do schedule
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
                        (f.stat().st_mtime for f in latest_dir.rglob('*') if f.is_file()),
                        default=None
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

@router.get("/executor_state")
def executor_state():
    # fresh por idade de log (com fallbacks)
    fresh_min = int(os.getenv("EXEC_FRESH_MAX_AGE_MIN", "5"))
    age_min, used = _log_age_min_multi(
        LOGS_DIR,
        ["executor.log", "executor.stdout.log", "executor.stderr.log"]
    )
    fresh = (age_min is not None) and (age_min <= fresh_min)

    # métricas a partir do CSV (proc_monitor)
    m = _last_metrics_for("executor")
    pid = m.get("pid")
    return {
        "fresh": fresh if age_min is not None else bool(pid),
        "age_min": age_min,
        "log_file": used,
        "last_signal_ts": None,
        "pid": pid,
        "cpu_s": None,  # não temos cpu_s no CSV
        "mem_mb": m.get("mem_mb"),
        "uptime_s": m.get("uptime_s"),
    }

@router.get("/scheduler_state")
def scheduler_state():
    """
    Estado do scheduler/retrain, usando:
      - idade dos logs (retrain.log / scheduler_retrain.log)
      - últimas métricas vistas no proc_metrics.csv (serviço 'retrain')
    """
    fresh_min = int(os.getenv("SCHED_FRESH_MAX_AGE_MIN", "10"))

    # Considera tanto retrain.log como os nomes antigos de scheduler
    age_min, used = _log_age_min_multi(
        LOGS_DIR,
        [
            "retrain.log",                 # novo serviço services.retrain
            "scheduler_retrain.log",       # nomes antigos, por compat
            "scheduler_retrain.stdout.log",
            "scheduler_retrain.stderr.log",
        ]
    )

    # Últimas métricas do proc_monitor para o serviço "retrain"
    m = _last_metrics_for("retrain")  # ex: {"pid": 1234, "mem_mb": 50.1, "uptime_s": 120.0, ...}
    pid = m.get("pid")

    # locks (se existirem)
    locks: list[str] = []
    for lf in [
        _apps_root() / "outputs" / "live" / "locks" / "scheduler_retrain.lock",
        _apps_root() / "outputs" / "live" / "scheduler_retrain.lock",
    ]:
        if lf.exists():
            locks.append(str(lf))

    # fresh: se tiver log recente usa idade; se não tiver, cai para "há PID vivo?"
    fresh = (age_min is not None and age_min <= fresh_min) or bool(pid)

    return {
        "fresh": fresh,
        "age_min": age_min,
        "log_file": used,
        "locks": locks,
        "pid": pid,
        "cpu_s": None,                      # não temos CPU em segundos no CSV, por isso fica None
        "mem_mb": m.get("mem_mb"),
        "uptime_s": m.get("uptime_s"),
    }

@router.get("/watcher_state")
def watcher_state():
    m = _last_metrics_for("watcher")
    pid = m.get("pid")
    age_min, used = _log_age_min_multi(
        LOGS_DIR,
        ["watch_signals.log", "watch_signals.stdout.log", "watch_signals.stderr.log"]
    )
    fresh = bool(pid) or (age_min is not None and age_min <= int(os.getenv("WATCH_FRESH_MAX_AGE_MIN","5")))
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
    m = _last_metrics_for("emitter")
    pid = m.get("pid")
    age_min, used = _log_age_min_multi(
        LOGS_DIR,
        ["emitter.log", "emitter.stdout.log", "emitter.stderr.log"]
    )
    fresh = bool(pid) or (age_min is not None and age_min <= int(os.getenv("EMITTER_FRESH_MAX_AGE_MIN","5")))
    return {
        "fresh": fresh,
        "age_min": age_min,
        "log_file": used,
        "pid": pid,
        "cpu_s": None,
        "mem_mb": m.get("mem_mb"),
        "uptime_s": m.get("uptime_s"),
    }

@router.get("/portfolio")
def get_portfolio():
    """JSON para a aba Portfolio na UI."""
    p = _portfolio_file()
    return _read_yaml_safe(p) if p.exists() else {"version": 1, "top": []}

@router.get("/governor_status")
def governor_status():
    """Resumo de jobs enabled/disabled a partir do retrain.yaml."""
    doc = _read_yaml_safe(_retrain_yaml_file())
    jobs = doc.get("jobs") or []
    enabled = sum(1 for j in jobs if j.get("enabled"))
    disabled = sum(1 for j in jobs if not j.get("enabled"))
    return {"enabled": enabled, "disabled": disabled, "total": len(jobs), "ts": time.time()}

@router.post("/jobs/{job_id}/toggle")
def toggle_job(job_id: str, payload: Dict[str, Any]):
    """Enable/disable manual de um job no retrain.yaml."""
    p = _retrain_yaml_file()
    if not p.exists():
        raise HTTPException(status_code=404, detail="retrain.yaml não encontrado")
    import yaml  # type: ignore
    doc = _read_yaml_safe(p)
    jobs = doc.get("jobs") or []
    found = False
    en = bool(payload.get("enabled", True))
    reason = (payload.get("reason") or "manual toggle").strip()
    for j in jobs:
        if str(j.get("id")) == job_id:
            j["enabled"] = en
            if not en:
                j["disabled_reason"] = reason
                j["disabled_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail="job não encontrado")
    p.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return {"ok": True}

# === Logs & Failed Signals =========================================
LOG_DIR    = LOGS_DIR
FAILED_DIR = _apps_root() / "outputs" / "live" / "signals" / "failed"

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
    p = _safe_join(LOG_DIR, name)
    return PlainTextResponse(_tail_text(p, n))

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

@router.get("/failed/list")
def failed_list(limit: int = 50):
    items: list[dict[str, Any]] = []
    if not FAILED_DIR.exists():
        return {"dir": str(FAILED_DIR), "items": items}

    cand = list(FAILED_DIR.glob("*.json.work"))
    cand.sort(key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True)
    for f in cand[: max(1, int(limit))]:
        try:
            st = f.stat()
        except Exception:
            continue
        err = FAILED_DIR / (f.name + ".error.txt")
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
        if dst.exists():
            dst.unlink(missing_ok=True)  # type: ignore[arg-type]
        src.replace(dst)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"falha a mover para inbox: {e}")

    err = _safe_join(FAILED_DIR, name + ".error.txt")
    if err.exists():
        arch = _signals_dir() / "archive"
        arch.mkdir(parents=True, exist_ok=True)
        try:
            err.replace(arch / err.name)
        except Exception:
            pass

    return {"ok": True, "moved_to": str(dst)}

# --- NOVO: abrir o retrain.yaml diretamente (usado pelo botão "Ver Schedule")
@router.get("/view_schedule")
def view_schedule():
    p = _schedule_file()
    if not p.exists():
        return PlainTextResponse("retrain.yaml não encontrado", status_code=404)
    return PlainTextResponse(p.read_text(encoding="utf-8"))

# --- NOVO: abrir um config YAML arbitrário (usado pelo link "Ver cfg" na tabela)
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