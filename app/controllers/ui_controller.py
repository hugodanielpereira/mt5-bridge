# app/controllers/ui_controller.py
from __future__ import annotations

import os
import time
import json
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

router = APIRouter(prefix="/ui", tags=["ui"])

# -------------------------
# Helpers de paths/env
# -------------------------
def _project_root() -> Path:
    env = os.getenv("PROJECT_DIR")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    # tenta achar "outputs/live" para subir à raiz do repo
    for up in (here.parents[2], here.parents[3] if len(here.parents) > 3 else here.parents[2]):
        cand = up / "outputs" / "live"
        if cand.exists():
            return up
    return Path.cwd()

PROJECT = _project_root()
LIVE = PROJECT / "outputs" / "live"
LOGS = LIVE / "logs"
PIDS = LIVE / "pids"
SIGNALS_INBOX = LIVE / "signals" / "inbox"
SIGNALS_DONE = LIVE / "signals" / "_done"
SCHED_LOG = LOGS / "scheduler_retrain.log"
EXEC_LOG = LOGS / "executor.log"
EXEC_PID = PIDS / "mlsl-executor.pid"
SCHEDULE_YAML = LIVE / "schedules" / "retrain.yaml"
CONFIGS_DIR = Path(os.getenv("CONFIGS_DIR", str(LIVE / "configs")))

def _file_age_minutes(p: Path) -> Optional[float]:
    try:
        if not p.exists():
            return None
        mtime = p.stat().st_mtime
        return max(0.0, (time.time() - mtime) / 60.0)
    except Exception:
        return None

def _read_pid(p: Path) -> Optional[int]:
    try:
        if not p.exists():
            return None
        txt = p.read_text(encoding="utf-8").strip()
        return int(txt) if txt.isdigit() else None
    except Exception:
        return None

def _pid_alive(pid: Optional[int]) -> Optional[bool]:
    if not pid:
        return None
    try:
        import psutil  # type: ignore
        return psutil.pid_exists(pid)
    except Exception:
        return None

def _count_pending_signals(inbox: Path) -> int:
    try:
        if not inbox.exists():
            return 0
        return sum(1 for p in inbox.glob("*.json") if p.is_file())
    except Exception:
        return 0

def _latest_signal_name() -> Optional[str]:
    candidates = []
    for base in (SIGNALS_INBOX, SIGNALS_DONE):
        if not base.exists():
            continue
        for f in base.glob("*.json"):
            try:
                candidates.append((f.stat().st_mtime, f))
            except Exception:
                pass
    if not candidates:
        return None
    _, f = max(candidates, key=lambda t: t[0])
    return f.name

def _tf_to_minutes(tf: str) -> int:
    if not tf:
        return 30
    t = str(tf).strip().upper()
    mapping = {
        "M1": 1, "M5": 5, "M15": 15, "M30": 30,
        "H1": 60, "H2": 120, "H3": 180, "H4": 240,
        "H6": 360, "H8": 480, "H12": 720,
        "D1": 1440, "W1": 10080,
    }
    if t in mapping:
        return mapping[t]
    import re
    m = re.match(r"^(?P<num>\d+)\s*(?P<unit>[MHDW])$", t)
    if m:
        n = int(m.group("num"))
        u = m.group("unit")
        if u == "M":
            return n
        if u == "H":
            return n * 60
        if u == "D":
            return n * 1440
        if u == "W":
            return n * 10080
    return 30

# -------------------------
# UI: HTML
# -------------------------
@router.get("/", response_class=HTMLResponse)
def ui_index() -> HTMLResponse:
    html = """
<!DOCTYPE html>
<html lang="pt">
<head>
  <meta charset="utf-8"/>
  <title>MLSL Bridge — UI</title>
  <style>
    body { font-family: system-ui, Segoe UI, Roboto, sans-serif; margin: 22px; }
    .wrap { display:grid; gap:16px; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }
    .card { border:1px solid #e5e7eb; border-radius:10px; padding:14px 16px; box-shadow: 0 1px 2px rgba(0,0,0,.04); }
    .title { font-size:20px; font-weight:700; margin-bottom:8px; }
    .kvs div { display:flex; justify-content:space-between; padding:4px 0; border-bottom: 1px dashed #eee; }
    .kvs div:last-child { border-bottom: none; }
    code, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    .muted { color:#6b7280; font-size:12px; }
    .ok { color:#059669; font-weight:600; }
    .warn { color:#d97706; font-weight:600; }
    .bad { color:#dc2626; font-weight:600; }
    button { padding:8px 12px; border-radius:8px; border:1px solid #e5e7eb; background:#f9fafb; cursor:pointer; }
    button:hover { background:#f3f4f6; }
    .hl { background:#f3f4f6; padding:2px 6px; border-radius:6px; }
    table { width:100%; border-collapse:collapse }
    th, td { padding:6px 4px; text-align:left; vertical-align: middle; }
    thead tr { border-bottom:1px solid #eee; }
    tbody tr { border-bottom:1px solid #f2f2f2; }
    .tag { padding:2px 6px; border-radius:6px; background:#eef2ff; color:#3730a3; font-size:12px; }
    .toolbar { display:flex; gap:8px; flex-wrap: wrap; margin: 10px 0 16px; }
    .actions { display:flex; gap:6px; }
    .btn-small { padding:4px 8px; font-size:12px; border-radius:6px; }
    .pre { white-space: pre; background:#0b1020; color:#e9eefc; padding:8px; border-radius:8px; overflow:auto; max-height: 60vh; }
  </style>
</head>
<body>
  <h1>MLSL Bridge — UI</h1>
<div class="toolbar">
  <button id="refreshBtn">Atualizar</button>
  <button id="runDueBtn">Run Retrain (due only)</button>
  <button id="runAllBtn">Run Retrain (ALL)</button>
  <button id="rebuildBtn">Rebuild Schedule</button>
  <button id="viewScheduleBtn">Ver retrain.yaml</button>

  <span style="margin-left:8px"></span>
  <select id="procTarget">
    <option value="all">all</option>
    <option value="bridge">bridge</option>
    <option value="executor">executor</option>
    <option value="retrain">retrain</option>
  </select>
  <button id="procUpBtn">Up</button>
  <button id="procDownBtn">Down</button>
  <button id="procRestartBtn">Restart</button>

  <span class="muted" id="ts"></span>
</div>
  <div id="healthLine" class="muted mono"></div>

  <div class="wrap" style="margin-top:14px">
    <div class="card">
      <div class="title">Bridge</div>
      <div class="kvs" id="bridgeCard"></div>
    </div>
    <div class="card">
      <div class="title">Executor</div>
      <div class="kvs" id="executorCard"></div>
    </div>
    <div class="card">
      <div class="title">Retrain (scheduler)</div>
      <div class="kvs" id="retrainCard"></div>
    </div>
  </div>

  <div class="card" style="margin-top:16px">
    <div class="title">Estratégias & Retrain</div>
    <div class="muted" style="margin-bottom:6px">
      Baseado em <code>outputs/live/schedules/retrain.yaml</code> (ou diretório de configs).
      <span id="configsHint"></span>
    </div>
    <div id="strategies"></div>
  </div>

  <div class="card" style="margin-top:16px">
    <div class="title">Output</div>
    <div id="out" class="pre"></div>
  </div>

<script>

async function procRun(kind){
  const target = document.getElementById('procTarget').value;
  setOut(`${kind.toUpperCase()} ${target} ...`);
  try{
    const res = await api(`/ui/api/proc/${kind}?target=${encodeURIComponent(target)}`, {method:'POST'});
    appendOut(res.stdout || '');
    if (res.stderr) appendOut('STDERR:\n' + res.stderr);
  }catch(e){ appendOut(String(e)); }
  await load();
}

document.getElementById('procUpBtn').addEventListener('click', ()=>procRun('up'));
document.getElementById('procDownBtn').addEventListener('click', ()=>procRun('down'));
document.getElementById('procRestartBtn').addEventListener('click', ()=>procRun('restart'));

const cls = (v) => v === true ? 'ok' : (v === false ? 'bad' : 'warn');

function fmtNum(x) {
  if (x === null || x === undefined || x === '-') return '-';
  const n = Number(x);
  if (!isFinite(n)) return '-';
  return n % 1 === 0 ? String(n) : n.toFixed(1);
}
function setOut(s){ document.getElementById('out').textContent = s || ''; }
function appendOut(s){ const el = document.getElementById('out'); el.textContent += (s || '') + "\\n"; el.scrollTop = el.scrollHeight; }

async function api(path, opts={}) {
  const resp = await fetch(path, Object.assign({headers:{'Content-Type':'application/json'}}, opts));
  if (!resp.ok) throw new Error('HTTP ' + resp.status);
  const ct = resp.headers.get('content-type') || '';
  if (ct.includes('application/json')) return await resp.json();
  return await resp.text();
}

async function load() {
  const r = await fetch('/ui/api/health');
  const data = await r.json();
  document.getElementById('ts').textContent = 'Atualizado: ' + new Date().toLocaleString();

  // linha de topo (raw health do bridge)
  const bridgeOk = data.bridge && data.bridge.ok === true;
  document.getElementById('healthLine').textContent =
    'Health: ' + (bridgeOk ? 'OK' : 'FAIL') + ' — ' + JSON.stringify(data.bridge || {});

  // Bridge card
  const b = data.bridge || {};
  const bBuild = b?.diag?.terminal?.build ?? '-';
  const bLogin = b?.diag?.account?.login ?? '-';
  const bServer = b?.diag?.account?.server ?? '-';
  const bBalance = b?.diag?.account?.balance ?? '-';
  document.getElementById('bridgeCard').innerHTML = `
    <div><span>Status</span><span class="${cls(b.ok)}">${b.ok ? 'OK':'FAIL'}</span></div>
    <div><span>Build</span><span class="mono">${bBuild}</span></div>
    <div><span>Conta</span><span class="mono">${bLogin} @ ${bServer}</span></div>
    <div><span>Saldo</span><span class="mono">${bBalance}</span></div>
  `;

  // Executor card
  const e = data.executor || {};
  const eAlive = e.alive;
  const eFresh = e.log_fresh;
  const eAge = (e.age_min ?? '-');
  const ePend = (e.pending ?? 0);
  const eLast = (e.last_signal ?? '-');
  document.getElementById('executorCard').innerHTML = `
    <div><span>Status</span><span class="${cls(eAlive)}">${eAlive===true?'ALIVE':(eAlive===false?'DOWN':'N/A')}</span></div>
    <div><span>Log fresco</span><span class="${cls(eFresh)}">${eFresh===true?'Sim':(eFresh===false?'Não':'N/A')}</span></div>
    <div><span>Idade do log (min)</span><span class="mono">${fmtNum(eAge)}</span></div>
    <div><span>Pendentes</span><span class="mono">${ePend}</span></div>
    <div><span>Último sinal</span><span class="mono hl">${eLast}</span></div>
  `;

  // Retrain (scheduler) card
  const rtr = data.retrain || {};
  const rAlive = rtr.alive;
  const rFresh = rtr.fresh;
  const rAge = (rtr.age_min ?? '-');
  const rWin = (rtr.window_min ?? '-');
  const rTf = (rtr.tf ?? '-');
  const rBars = (rtr.bars ?? '-');
  const rLocks = (rtr.locks ?? 'none');
  document.getElementById('retrainCard').innerHTML = `
    <div><span>Status</span><span class="${cls(rAlive)}">${rAlive===true?'ALIVE':(rAlive===false?'DOWN':'N/A')}</span></div>
    <div><span>Fresh</span><span class="${cls(rFresh)}">${rFresh===true?'Sim':(rFresh===false?'Não':'N/A')}</span></div>
    <div><span>Idade do log (min)</span><span class="mono">${fmtNum(rAge)}</span></div>
    <div><span>Window (min)</span><span class="mono">${fmtNum(rWin)}</span></div>
    <div><span>TF/Bars</span><span class="mono">${rTf} / ${rBars}</span></div>
    <div><span>Locks</span><span class="mono">${rLocks}</span></div>
  `;

  // Estratégias + retrain por TF
  const s = data.strategies || [];
  document.getElementById('configsHint').textContent = (s.length === 0 ? 'Sem entradas na schedule nem configs.' : '');
  const rows = s.map(row => {
    const due = row.due === true ? 'Sim' : (row.due === false ? 'Não' : '-');
    const dueCls = cls(row.due);
    const nextIn = row.next_in_min !== undefined && row.next_in_min !== null
                  ? fmtNum(row.next_in_min) : '-';
    const jobId = (row.symbol + '_' + row.tf).toUpperCase();
    const cfgPath = row.config_file || '';
    return `
      <tr>
        <td><span class="hl mono">${row.symbol}</span></td>
        <td><span class="hl mono">${row.tf}</span></td>
        <td><span class="tag">${row.source}</span></td>
        <td class="mono">${fmtNum(row.window_min)}</td>
        <td class="mono">${fmtNum(row.since_last_min)}</td>
        <td class="${dueCls}">${due}</td>
        <td class="mono">${nextIn}</td>
        <td><code>${cfgPath || '-'}</code></td>
        <td>
          <div class="actions">
            <button class="btn-small" onclick="runOne('${jobId}')">Run</button>
            ${cfgPath ? `<button class="btn-small" onclick="viewCfg('${encodeURIComponent(cfgPath)}')">Ver cfg</button>` : ''}
          </div>
        </td>
      </tr>
    `;
  }).join('');
  document.getElementById('strategies').innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Symbol</th><th>TF</th><th>Fonte</th>
          <th>Window (min)</th><th>Last run (min)</th><th>Due?</th><th>Next in (min)</th>
          <th>Config</th><th>Ações</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

async function runDue(){
  setOut('Running retrain (due only)...');
  try{
    const res = await api('/ui/api/retrain/run', {method:'POST'});
    appendOut(JSON.stringify(res, null, 2));
  }catch(e){ appendOut(String(e)); }
  await load();
}
async function runAll(){
  setOut('Running retrain (ALL/force)...');
  try{
    const res = await api('/ui/api/retrain/run?force=1', {method:'POST'});
    appendOut(JSON.stringify(res, null, 2));
  }catch(e){ appendOut(String(e)); }
  await load();
}
async function rebuild(){
  setOut('Rebuilding schedule...');
  try{
    const res = await api('/ui/api/retrain/rebuild', {method:'POST'});
    appendOut(JSON.stringify(res, null, 2));
  }catch(e){ appendOut(String(e)); }
  await load();
}
async function runOne(jobId){
  setOut('Running job ' + jobId + ' ...');
  try{
    const res = await api('/ui/api/retrain/run_one?id=' + encodeURIComponent(jobId), {method:'POST'});
    appendOut(JSON.stringify(res, null, 2));
  }catch(e){ appendOut(String(e)); }
  await load();
}
async function viewCfg(pathEnc){
  setOut('Loading config...');
  try{
    const txt = await api('/ui/api/config?path=' + pathEnc);
    setOut(txt);
  }catch(e){ appendOut(String(e)); }
}

document.getElementById('refreshBtn').addEventListener('click', load);
document.getElementById('runDueBtn').addEventListener('click', runDue);
document.getElementById('runAllBtn').addEventListener('click', runAll);
document.getElementById('rebuildBtn').addEventListener('click', rebuild);
document.getElementById('viewScheduleBtn').addEventListener('click', async () => {
  try{ setOut(await api('/ui/api/schedule')); }catch(e){ setOut(String(e)); }
});
load();
</script>
</body>
</html>
    """.strip()
    return HTMLResponse(html)

# -------------------------
# API consolidada (health)
# -------------------------
@router.get("/api/health", response_class=JSONResponse)
def ui_health() -> JSONResponse:
    # --- Bridge health ------------------------------------------------------
    try:
        from app.main import health as _bridge_health  # type: ignore
        bridge = _bridge_health(verbose=1)
    except Exception as e:
        bridge = {"ok": False, "reason": f"bridge_health_failed: {e}"}

    # --- Executor -----------------------------------------------------------
    exec_pid = _read_pid(EXEC_PID)
    alive = _pid_alive(exec_pid)
    age_min = _file_age_minutes(EXEC_LOG)
    log_fresh = (age_min is not None and age_min < 5.0)
    pending = _count_pending_signals(SIGNALS_INBOX)
    last_signal = _latest_signal_name()
    executor = {
        "alive": alive,
        "log_fresh": log_fresh if age_min is not None else None,
        "age_min": round(age_min, 1) if isinstance(age_min, (int, float)) else None,
        "pending": pending,
        "last_signal": last_signal,
        "pid": exec_pid,
        "log": str(EXEC_LOG),
        "inbox": str(SIGNALS_INBOX),
    }

    # --- Retrain (scheduler heartbeat) -------------------------------------
    sched_age = _file_age_minutes(SCHED_LOG)
    retrain = {
        "alive": None,  # se quiseres, podes ler PID/lock do scheduler aqui
        "fresh": (sched_age is not None and sched_age < 60.0),
        "age_min": round(sched_age, 1) if isinstance(sched_age, (int, float)) else None,
        "window_min": None,
        "tf": None,
        "bars": None,
        "locks": "none",
    }

    # --- Estratégias + cálculo de due/next_in ------------------------------
    grace = float(os.getenv("RETRAIN_GRACE_MIN", "10"))
    now = time.time()
    strategies: list[dict] = []

    def _add_row(
        symbol: str,
        tf: str,
        src: str,
        cfg_file: str | None,
        window_min: float | None,
        last_run_ts: float | None,
    ) -> None:
        since_min: float | None = None
        due: bool | None = None
        next_in: float | None = None
        overdue: float | None = None

        if last_run_ts:
            try:
                since_min = max(0.0, (now - float(last_run_ts)) / 60.0)
            except Exception:
                since_min = None

        w = None
        if window_min:
            try:
                w = float(window_min)
            except Exception:
                w = None

        if (since_min is not None) and (w is not None):
            raw = w - since_min  # +: falta; -: atraso
            due = since_min >= max(0.0, w - grace)
            if raw >= 0:
                next_in = raw
                overdue = 0.0
            else:
                next_in = 0.0
                overdue = -raw

        strategies.append({
            "symbol": symbol,
            "tf": tf,
            "source": src,
            "config_file": cfg_file,
            "window_min": round(w, 1) if isinstance(w, (int, float)) else window_min,
            "since_last_min": round(since_min, 1) if isinstance(since_min, (int, float)) else since_min,
            "due": due,
            "next_in_min": round(next_in, 1) if isinstance(next_in, (int, float)) else next_in,
            "overdue_min": round(overdue, 1) if isinstance(overdue, (int, float)) else overdue,
        })

    # 1) Entradas a partir do schedule (se existir)
    jobs_in_schedule: set[str] = set()
    if SCHEDULE_YAML.exists():
        try:
            try:
                import yaml  # type: ignore
                data = yaml.safe_load(SCHEDULE_YAML.read_text(encoding="utf-8")) or {}
            except Exception:
                data = {}
            for job in (data.get("jobs") or []):
                symbol = job.get("symbol") or job.get("mt5_symbol") or job.get("instrument") or "-"
                tf = str(job.get("timeframe") or "-").upper()
                cfg_file = job.get("config_file")
                window_min = job.get("retrain_every_minutes")
                last_run_ts = job.get("last_run_ts")
                jobs_in_schedule.add(f"{symbol}_{tf}")
                _add_row(symbol, tf, "schedule", cfg_file, window_min, last_run_ts)
        except Exception:
            pass

    # 2) Completar com configs que não estejam no schedule
    try:
        if CONFIGS_DIR.exists():
            for f in sorted(CONFIGS_DIR.glob("*_config.yaml")):
                name = f.stem  # ex.: BNBUSDT_H2_config -> stem sem .yaml
                parts = name.split("_")
                symbol = parts[0]
                tf = (parts[1] if len(parts) > 1 else "M30").upper()
                key = f"{symbol}_{tf}"
                if key in jobs_in_schedule:
                    continue
                window_min = _tf_to_minutes(tf)  # default: 1 bar por janela
                _add_row(symbol, tf, "config", str(f), window_min, None)
    except Exception:
        pass

    payload = {
        "bridge": bridge,
        "executor": executor,
        "retrain": retrain,
        "strategies": strategies,
        "updated_at": int(now),
    }
    return JSONResponse(payload)

# -------------------------
# API: ações retrain
# -------------------------
def _run_py_module(mod: str, args: List[str]) -> Dict[str, Any]:
    """
    Executa `python -m <mod> <args>` no diretório do PROJECT.
    Força UTF-8 na IO para evitar crashes de decoding no Windows.
    """
    py = os.getenv("VENV_PY") or os.getenv("PYTHON") or "python"
    cmd = [py, "-m", mod] + list(args or [])
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"  # stdout/stderr do filho em UTF-8

    try:
        p = subprocess.run(
            cmd,
            cwd=str(PROJECT),
            capture_output=True,
            text=True,
            encoding="utf-8",   # <- obrigatório
            errors="replace",   # <- obrigatório
            env=env,
            timeout=60 * 30,
        )
        out = (p.stdout or "")[-8000:]
        err = (p.stderr or "")[-8000:]
        return {"ok": (p.returncode == 0), "rc": p.returncode, "cmd": cmd, "stdout": out, "stderr": err}
    except Exception as e:
        return {"ok": False, "rc": -1, "cmd": cmd, "error": str(e)}

def _run_ps1(script: Path, *ps_args: str) -> Dict[str, Any]:
    """
    Executa um .ps1 via PowerShell, forçando UTF-8 para não rebentar o decoder.
    Ex.: _run_ps1(PROJECT/'scripts/mlsl.ps1', 'status', 'all')
    """
    ps = os.getenv("POWERSHELL", "powershell")
    prelude = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
        "$OutputEncoding=[Console]::OutputEncoding; "
        "$PSStyle.OutputRendering='PlainText'; "
    )
    s = str(script)
    args_str = " ".join(f"'{a}'" for a in ps_args)
    full_cmd = f"{prelude}& '{s}' {args_str}"

    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")

    try:
        p = subprocess.run(
            [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", full_cmd],
            cwd=str(PROJECT),
            capture_output=True,
            text=True,
            encoding="utf-8",   # <- obrigatório
            errors="replace",   # <- obrigatório
            env=env,
            timeout=60 * 15,
        )
        out = (p.stdout or "")[-8000:]
        err = (p.stderr or "")[-8000:]
        return {"ok": (p.returncode == 0), "rc": p.returncode, "cmd": full_cmd, "stdout": out, "stderr": err}
    except Exception as e:
        return {"ok": False, "rc": -1, "cmd": full_cmd, "error": str(e)}

MLS_PS = PROJECT / "scripts" / "mlsl.ps1"

@router.get("/api/status", response_class=JSONResponse)
def api_status(target: str = "all") -> JSONResponse:
    if not MLS_PS.exists():
        return JSONResponse({"ok": False, "error": f"script not found: {MLS_PS}"}, status_code=404)
    res = _run_ps1(MLS_PS, "status", target, "-tail", "0")
    return JSONResponse(res)

@router.post("/api/restart", response_class=JSONResponse)
def api_restart(target: str = "all") -> JSONResponse:
    if not MLS_PS.exists():
        return JSONResponse({"ok": False, "error": f"script not found: {MLS_PS}"}, status_code=404)
    res = _run_ps1(MLS_PS, "restart", target)
    return JSONResponse(res)

@router.post("/api/up", response_class=JSONResponse)
def api_up(target: str = "all") -> JSONResponse:
    if not MLS_PS.exists():
        return JSONResponse({"ok": False, "error": f"script not found: {MLS_PS}"}, status_code=404)
    res = _run_ps1(MLS_PS, "up", target)
    return JSONResponse(res)

@router.post("/api/down", response_class=JSONResponse)
def api_down(target: str = "all") -> JSONResponse:
    if not MLS_PS.exists():
        return JSONResponse({"ok": False, "error": f"script not found: {MLS_PS}"}, status_code=404)
    res = _run_ps1(MLS_PS, "down", target)
    return JSONResponse(res)

@router.post("/api/retrain/run", response_class=JSONResponse)
def api_retrain_run(force: Optional[int] = Query(default=0)) -> JSONResponse:
    args: List[str] = []
    if force:
        args.append("--force")
    res = _run_py_module("tools.retrain_incremental", args)
    return JSONResponse(res)

@router.post("/api/retrain/run_one", response_class=JSONResponse)
def api_retrain_run_one(id: str = Query(..., description="JOB_ID, ex: BNBUSDT_H2")) -> JSONResponse:
    job_id = (id or "").strip()
    if not job_id:
        return JSONResponse({"ok": False, "error": "missing id"}, status_code=400)
    res = _run_py_module("tools.retrain_incremental", ["--only", job_id])
    return JSONResponse(res)

@router.post("/api/retrain/rebuild", response_class=JSONResponse)
def api_retrain_rebuild() -> JSONResponse:
    res = _run_py_module("tools.retrain_incremental", ["--rebuild-schedule"])
    return JSONResponse(res)

# -------------------------
# API: ver configs / schedule
# -------------------------
@router.get("/api/configs", response_class=JSONResponse)
def api_configs() -> JSONResponse:
    cfgs: List[Dict[str, Any]] = []
    base = CONFIGS_DIR
    try:
        for f in sorted(base.glob("*_config.yaml")):
            name = f.stem
            parts = name.split("_")
            symbol = parts[0]
            tf = parts[1] if len(parts) > 1 else "M30"
            cfgs.append({"symbol": symbol, "tf": tf, "path": str(f)})
    except Exception:
        pass
    return JSONResponse({"dir": str(base), "items": cfgs})

def _is_inside(child: Path, root: Path) -> bool:
    try:
        child = child.resolve()
        root = root.resolve()
        return str(child).startswith(str(root))
    except Exception:
        return False

@router.get("/api/config", response_class=PlainTextResponse)
def api_config(path: str = Query(...)) -> PlainTextResponse:
    # permite absoluto ou relativo a CONFIGS_DIR; mas tem de viver dentro de CONFIGS_DIR
    p = Path(path)
    if not p.is_absolute():
        p = CONFIGS_DIR / p
    if not _is_inside(p, CONFIGS_DIR):
        return PlainTextResponse("denied", status_code=403)
    if not p.exists():
        return PlainTextResponse("not found", status_code=404)
    try:
        return PlainTextResponse(p.read_text(encoding="utf-8"))
    except Exception as e:
        return PlainTextResponse(f"error: {e}", status_code=500)

@router.get("/api/schedule", response_class=PlainTextResponse)
def api_schedule() -> PlainTextResponse:
    if not SCHEDULE_YAML.exists():
        return PlainTextResponse("# retrain.yaml não encontrado", status_code=404)
    try:
        return PlainTextResponse(SCHEDULE_YAML.read_text(encoding="utf-8"))
    except Exception as e:
        return PlainTextResponse(f"error: {e}", status_code=500)

# --- PowerShell helpers (usar mlsl.ps1) -------------------------------------
def _ps1_run(args: list[str], timeout_sec: int = 180) -> dict[str, Any]:
    """
    Executa PowerShell com o scripts\mlsl.ps1 do PROJECT.
    """
    ps1 = PROJECT / "scripts" / "mlsl.ps1"
    if not ps1.exists():
        return {"ok": False, "error": f"mlsl.ps1 not found at {ps1}"}

    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", str(ps1),
    ] + args

    try:
        p = subprocess.run(
            cmd, cwd=str(PROJECT),
            capture_output=True, text=True,
            timeout=timeout_sec
        )
        out = (p.stdout or "")[-8000:]
        err = (p.stderr or "")[-8000:]
        return {"ok": p.returncode == 0, "rc": p.returncode, "cmd": cmd, "stdout": out, "stderr": err}
    except Exception as e:
        return {"ok": False, "rc": -1, "error": str(e)}

@router.post("/api/proc/up", response_class=JSONResponse)
def api_proc_up(target: str = Query("all", pattern="^(all|bridge|executor|retrain)$")) -> JSONResponse:
    return JSONResponse(_ps1_run(["up", target]))

@router.post("/api/proc/down", response_class=JSONResponse)
def api_proc_down(target: str = Query("all", pattern="^(all|bridge|executor|retrain)$")) -> JSONResponse:
    return JSONResponse(_ps1_run(["down", target]))

@router.post("/api/proc/restart", response_class=JSONResponse)
def api_proc_restart(target: str = Query("all", pattern="^(all|bridge|executor|retrain)$")) -> JSONResponse:
    return JSONResponse(_ps1_run(["restart", target]))

@router.get("/api/proc/status", response_class=JSONResponse)
def api_proc_status(target: str = Query("all", pattern="^(all|bridge|executor|retrain)$"),
                    tail: int = Query(15, ge=0, le=500)) -> JSONResponse:
    return JSONResponse(_ps1_run(["status", target, "-tail", str(tail)], timeout_sec=90))

@router.get("/api/proc/tail", response_class=PlainTextResponse)
def api_proc_tail(target: str = Query(..., pattern="^(bridge|executor|retrain)$"),
                  lines: int = Query(100, ge=1, le=1000)) -> PlainTextResponse:
    # Tail lendo diretamente os logs (mais rápido que powershell).
    LOG_MAP = {
        "bridge": Path(LOGS / "bridge.log"),
        "executor": Path(LOGS / "executor.log"),
        "retrain": Path(LOGS / "retrain.log"),
    }
    p = LOG_MAP.get(target)
    if not p or not p.exists():
        return PlainTextResponse(f"no log for {target}", status_code=404)
    try:
        txt = p.read_text(encoding="utf-8", errors="ignore").splitlines()[-lines:]
        return PlainTextResponse("\n".join(txt))
    except Exception as e:
        return PlainTextResponse(f"error: {e}", status_code=500)