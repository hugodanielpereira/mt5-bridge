# app/ui/routes/control.py
from __future__ import annotations
import os, sys, subprocess, shlex, shutil
from pathlib import Path
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(prefix="/ui/ops", tags=["ui-ops"])

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]

def _bridges_root() -> Path:
    return _repo_root().parent

def _win() -> bool:
    return os.name == "nt"

def _pwsh_exe() -> str:
    env = os.getenv("PWSH_EXE")
    if env:
        return env
    if shutil.which("pwsh"):
        return "pwsh"
    return "powershell"

def _always_json(ok: bool, cmd: str, rc: int, stdout: str, stderr: str, cwd: str) -> JSONResponse:
    return JSONResponse(
        {"ok": bool(ok), "cmd": cmd, "rc": int(rc), "stdout": stdout, "stderr": stderr, "cwd": cwd}
    )

def _run(cmd: list[str] | str, cwd: Optional[Path] = None, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    shell = isinstance(cmd, str)
    _env = (env or os.environ).copy()
    # Força UTF-8 para evitar "concluÃ­do" em Windows
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

def _bridge_scripts_dir() -> Path:
    env_dir = os.getenv("BRIDGE_DIR")
    return Path(env_dir) if env_dir else (_bridges_root() / "mt5-bridge")

def _apps_root() -> Path:
    env = os.getenv("PROJECT_DIR")
    if env:
        return Path(env)
    env2 = os.getenv("MLSL_APPS_DIR")
    if env2:
        return Path(env2)
    return _bridges_root().parent / "apps" / "ml-strategy-lab"

def _cmd_for(target: str, action: str, *, visible: bool) -> tuple[list[str] | str, Path, dict]:
    tgt = target.lower().strip()
    act = action.lower().strip()
    valid_targets = {"bridge","executor","scheduler","retrainer","watcher","emitter","all"}
    if tgt not in valid_targets:
        raise HTTPException(status_code=400, detail=f"target inválido: {target}")
    if act not in ("start", "stop", "restart"):
        raise HTTPException(status_code=400, detail=f"action inválida: {action}")

    # mapear nomes para o mlsl.ps1
    map_target = {
        "bridge":"bridge",
        "executor":"executor",
        "scheduler":"retrain",
        "retrainer":"retrain",
        "watcher":"watcher",
        "emitter":"emitter",
        "all":"all",
    }[tgt]
    verb = {"start":"up","stop":"down","restart":"restart"}[act]

    scripts_root = _apps_root() / "scripts"
    mlsl = scripts_root / "mlsl.ps1"
    if not mlsl.exists():
        raise HTTPException(status_code=500, detail=f"mlsl.ps1 não encontrado em {mlsl}")

    env = os.environ.copy()
    if visible:
        env["MLSL_VISIBLE"] = "on"

    if _win():
        ps = _pwsh_exe()
        return ([ps, "-ExecutionPolicy", "Bypass", "-File", str(mlsl), verb, map_target], scripts_root, env)
    else:
        return (["bash", str(mlsl), verb, map_target], scripts_root, env)

@router.post("/run")
def run_op(payload: Dict[str, Any]):
    target = str(payload.get("target") or "").strip().lower()
    action = str(payload.get("action") or "").strip().lower()
    visible = bool(payload.get("visible", False))
    try:
        cmd, cwd, env = _cmd_for(target, action, visible=visible)
    except HTTPException as e:
        return _always_json(False, cmd=f"{target} {action}", rc=1, stdout="", stderr=e.detail, cwd=str(Path.cwd()))

    res = _run(cmd, cwd=cwd, env=env)  # <<<<< agora passa o env (MLSL_VISIBLE)
    rc, stdout, stderr = int(res.get("rc", -1)), res.get("stdout", ""), res.get("stderr", "")
    cmd_str = cmd if isinstance(cmd, str) else " ".join(shlex.quote(c) for c in cmd)
    return _always_json(ok=(rc == 0), cmd=cmd_str, rc=rc, stdout=stdout, stderr=stderr, cwd=str(cwd))

# --------- Retrain API (enforces NO-EMIT when you want) ---------
class RetrainReq(BaseModel):
    mode: str              # "all" | "due" | "rebuild" | "show"
    emit: bool = False     # default: DO NOT emit for UI-triggered retrain

@router.post("/api/retrain")
def api_retrain(req: RetrainReq):
    apps_dir = _apps_root()
    py = os.getenv("VENV_PY") or sys.executable
    schedule_file = os.getenv("RETRAIN_SCHEDULE_FILE") or str(apps_dir / "outputs" / "live" / "schedules" / "retrain.yaml")

    args: list[str] = ["-m", "tools.retrain_incremental"]
    mode = req.mode.strip().lower()

    if mode == "all":
        args.append("--force")
    elif mode == "rebuild":
        args.append("--rebuild-schedule")
    elif mode == "show":
        try:
            txt = Path(schedule_file).read_text(encoding="utf-8")
        except Exception as e:
            return _always_json(False, f"type {schedule_file}", 1, "", f"{type(e).__name__}: {e}", str(apps_dir))
        return _always_json(True, f"type {schedule_file}", 0, txt, "", str(apps_dir))
    elif mode == "due":
        pass
    else:
        return _always_json(False, "invalid mode", 1, "", f"modo inválido: {req.mode}", str(apps_dir))

    if not req.emit:
        args.append("--no-emit")

    env = os.environ.copy()
    env["MLSL_RETRAIN_NO_EMIT"] = "0" if req.emit else "1"

    res = _run([py, *args], cwd=apps_dir, env=env)
    rc, stdout, stderr = int(res.get("rc", -1)), res.get("stdout", ""), res.get("stderr", "")
    cmd_str = " ".join(shlex.quote(c) for c in [py, *args])
    return _always_json(ok=(rc == 0), cmd=cmd_str, rc=rc, stdout=stdout, stderr=stderr, cwd=str(apps_dir))