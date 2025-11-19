# app/ui/routes/api_procs.py
from __future__ import annotations
import os, shlex, subprocess, shutil
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/ui/api", tags=["ui-procs"])

# -------------------------
# Helpers de caminho
# -------------------------
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
    bridges_dir = here.parents[4]   # .../bridges/mt5-bridge
    root = bridges_dir.parent       # .../C:\Trading
    return root / "apps" / "ml-strategy-lab"

def _mlsl_ps1() -> Path:
    return _apps_root() / "scripts" / "mlsl.ps1"

def _is_windows() -> bool:
    return os.name == "nt"

def _pwsh_exe() -> str:
    env = os.getenv("PWSH_EXE")
    if env:
        return env
    if shutil.which("pwsh"):
        return "pwsh"
    return "powershell"

# -------------------------
# Execução (exportada p/ testes)
# -------------------------
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

# -------------------------
# Mapeamento de comandos
# -------------------------
def _cmd_for(target: str, action: str, *, visible: bool) -> tuple[list[str] | str, Path, Dict[str, str]]:
    tgt = (target or "").strip().lower()
    act = (action or "").strip().lower()

    # ⬇️ acrescenta governor e promoter
    valid = {"all","bridge","executor","retrain","scheduler","watcher","emitter","governor","promoter"}
    if tgt not in valid:
        raise HTTPException(status_code=400, detail=f"target inválido: {target}")
    if act not in ("start", "stop", "restart"):
        raise HTTPException(status_code=400, detail=f"action inválida: {action}")

    if tgt == "scheduler":
        tgt = "retrain"

    mlsl = _mlsl_ps1()
    allow_missing = bool(os.getenv("PYTEST_CURRENT_TEST")) or (
        os.getenv("MLSL_ALLOW_MISSING_MLSL", "0").lower() in ("1","true","yes","on")
    )
    if not mlsl.exists() and not allow_missing:
        raise HTTPException(
            status_code=500,
            detail=f"mlsl.ps1 não encontrado em {mlsl}. Define PROJECT_DIR (ou MLSL_APPS_DIR) no .env."
        )

    verb = {"start":"up","stop":"down","restart":"restart"}[act]

    env = os.environ.copy()
    if visible:
        env["MLSL_VISIBLE"] = "on"

    if _is_windows():
        ps = _pwsh_exe()
        cmd = [ps, "-ExecutionPolicy", "Bypass", "-File", str(mlsl), verb, tgt]  # ⬅️ tgt já inclui governor/promoter
        return cmd, mlsl.parent, env
    else:
        return (["bash", str(mlsl), verb, tgt], mlsl.parent, env)

# -------------------------
# Rotas
# -------------------------
@router.post("/proc_run")
def proc_run(payload: Dict[str, Any]):
    """
    Body JSON:
      {
        "target": "executor|retrain|scheduler|watcher|emitter|bridge|all",
        "action": "start|stop|restart",
        "visible": true|false   # opcional, abre janelas (pwsh) se suportado
      }
    Retorna SEMPRE 200 com ok=True/False e detalhes (mesmo em erro de validação).
    """
    target = str(payload.get("target") or "")
    action = str(payload.get("action") or "")
    visible = bool(payload.get("visible", False))

    try:
        cmd, cwd, env = _cmd_for(target, action, visible=visible)
    except HTTPException as e:
        return JSONResponse(
            {
                "ok": False,
                "error": e.detail,
                "hint": "Confirma PROJECT_DIR no .env e existência de scripts/mlsl.ps1. Sem PowerShell 7, define PWSH_EXE=powershell.",
                "target": target, "action": action,
            },
            status_code=200,
        )

    res = _run(cmd, cwd=cwd, env=env)
    rc = int(res.get("rc", -1))
    cmd_str = cmd if isinstance(cmd, str) else " ".join(shlex.quote(c) for c in cmd)
    return JSONResponse({
        "ok": (rc == 0),
        "cmd": cmd_str,
        "cwd": str(cwd),
        "rc": rc,
        "stdout": res.get("stdout", ""),
        "stderr": res.get("stderr", ""),
        "hint": "Se rc!=0, corre o comando no terminal para ver o erro exacto (paths/permissões).",
    })