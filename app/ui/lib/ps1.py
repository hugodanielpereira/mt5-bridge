# app/ui/lib/ps1.py
from __future__ import annotations
import os
import subprocess
from typing import Any, Dict, List
from .paths import PROJECT

def run_ps1(args: List[str], timeout_sec: int = 180) -> Dict[str, Any]:
    ps1 = PROJECT / "scripts" / "mlsl.ps1"
    if not ps1.exists():
        return {"ok": False, "error": f"mlsl.ps1 not found at {ps1}"}

    # compatível WinPS5 (sem $PSStyle)
    prelude = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
        "$OutputEncoding=[Console]::OutputEncoding; "
    )

    def _q(a: str) -> str:
        if a.startswith("-"):
            return a
        try:
            float(a); return a
        except Exception:
            return "'" + a.replace("'", "''") + "'"

    args_str = " ".join(_q(a) for a in args)
    full_cmd = f"{prelude}& '{ps1}' {args_str}"

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    p = subprocess.run(
        ["powershell.exe","-NoProfile","-ExecutionPolicy","Bypass","-NoLogo","-Command", full_cmd],
        cwd=str(PROJECT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout_sec, env=env
    )
    return {
        "ok": p.returncode == 0, "rc": p.returncode, "cmd": full_cmd,
        "stdout": (p.stdout or "")[-10000:], "stderr": (p.stderr or "")[-10000:]
    }