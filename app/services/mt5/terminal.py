from __future__ import annotations
import os, time, subprocess
from pathlib import Path
from typing import List
import psutil

KILL_NON_PORTABLE_DEMO = os.getenv("KILL_NON_PORTABLE_DEMO", "1").lower() in ("1","true","yes","on")
REQUIRE_PORTABLE       = os.getenv("REQUIRE_PORTABLE", "1").lower() in ("1","true","yes","on")

try:
    import MetaTrader5 as MT5
except Exception as e:
    raise RuntimeError(f"MetaTrader5 import failed: {e!r}")

def _our_proc_list(exe_path: Path) -> List[psutil.Process]:
    out = []
    for p in psutil.process_iter(["pid","name","exe","cmdline"]):
        try:
            name_ok = (p.info.get("name") or "").lower() in ("terminal64.exe","terminal.exe")
            exep = p.info.get("exe")
            exe_ok = False
            if exep:
                try:
                    exe_ok = Path(exep).resolve().samefile(exe_path)
                except Exception:
                    exe_ok = (str(exep).strip().lower() == str(exe_path).strip().lower())
            if name_ok and exe_ok:
                out.append(p)
        except Exception:
            continue
    return out

def ensure_terminal_running(exe_path: Path, base_dir: Path, max_wait_sec: int = 45) -> None:
    if _our_proc_list(exe_path):
        return
    # opcional: fechar instâncias não-portable do mesmo exe
    if REQUIRE_PORTABLE and KILL_NON_PORTABLE_DEMO:
        for p in _our_proc_list(exe_path):
            try:
                cmd = " ".join(p.cmdline() or []).lower()
            except Exception:
                cmd = ""
            if "/portable" not in cmd:
                try:
                    p.terminate()
                    p.wait(5)
                except psutil.TimeoutExpired:
                    p.kill()
                except Exception:
                    pass
    # arrancar
    try:
        if REQUIRE_PORTABLE:
            try:
                (base_dir / "portable").touch(exist_ok=True)
            except Exception:
                pass
        args = [str(exe_path)] + (["/portable"] if REQUIRE_PORTABLE else [])
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(base_dir))
    except Exception as e:
        raise RuntimeError(f"failed to launch terminal: {e}")

    t0 = time.time()
    while time.time() - t0 < max_wait_sec:
        if _our_proc_list(exe_path):
            return
        time.sleep(0.3)
    raise RuntimeError("timeout launching MT5 terminal")

def attach_specific(exe_path: Path, base_dir: Path, max_wait_sec: int = 45) -> None:
    t0 = time.time()
    attempt = 0
    while True:
        attempt += 1
        if MT5.initialize(path=str(exe_path)):
            break
        code, msg = MT5.last_error()
        print(f"[MT5] initialize(path=...) failed (try {attempt}): {code} {msg}")
        if time.time() - t0 > max_wait_sec:
            raise RuntimeError(f"MT5.initialize(path) failed after {attempt} tries: {code} {msg}")
        time.sleep(1.0)

    ti = MT5.terminal_info()
    if ti is None:
        code, msg = MT5.last_error()
        raise RuntimeError(f"terminal_info None: {code} {msg}")

    dp = str(getattr(ti, "data_path", "") or "")
    if not dp.strip().lower().rstrip("\\/").startswith(str(base_dir).strip().lower().rstrip("\\/")):
        MT5.shutdown()
        if REQUIRE_PORTABLE:
            raise RuntimeError(f"[MT5] datapath mismatch (require portable). dp={dp}, base={base_dir}")
        raise RuntimeError(f"[MT5] datapath mismatch. dp={dp}, base={base_dir}")

def ensure_up() -> None:
    try:
        MT5.terminal_info()
        return
    except Exception:
        pass
    try:
        MT5.shutdown()
    except Exception:
        pass