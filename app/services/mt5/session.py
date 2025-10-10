# app/services/mt5/session.py

from __future__ import annotations
from pathlib import Path
from typing import Optional

try:
    import MetaTrader5 as MT5
except Exception as e:
    raise RuntimeError(f"MetaTrader5 import failed: {e!r}")

from .terminal import ensure_terminal_running as _ensure_terminal_running, attach_specific as _attach_specific

class MT5Session:
    def __init__(self, exe_path: Path, login: Optional[str], password: Optional[str], server: Optional[str]) -> None:
        self.exe_path = exe_path
        self.base_dir = exe_path.parent
        self.login = login
        self.password = password
        self.server = server

    # --- wrappers para manter compatibilidade com service.py ---
    def ensure_terminal_running(self, max_wait_sec: int = 45) -> None:
        _ensure_terminal_running(self.exe_path, self.base_dir, max_wait_sec=max_wait_sec)

    def attach_specific(self, max_wait_sec: int = 45) -> None:
        _attach_specific(self.exe_path, self.base_dir, max_wait_sec=max_wait_sec)

    def ensure_up(self) -> None:
        # pequeno “ping” ao MT5; se caiu, reanexa
        try:
            ti = MT5.terminal_info()
            if ti:
                return
        except Exception:
            pass
        try:
            MT5.shutdown()
        except Exception:
            pass
        self.ensure_terminal_running()
        self.attach_specific()

    def ensure_symbol(self, symbol: str) -> None:
        info = MT5.symbol_info(symbol)
        if info is None:
            raise RuntimeError(f"symbol '{symbol}' not found")
        if not info.visible:
            if not MT5.symbol_select(symbol, True):
                raise RuntimeError(f"symbol_select failed for '{symbol}'")