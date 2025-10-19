# app/services/mt5/session.py
from __future__ import annotations
from pathlib import Path
from typing import Optional, Dict
import os

try:
    import MetaTrader5 as MT5
except Exception as e:
    raise RuntimeError(f"MetaTrader5 import failed: {e!r}")

# helpers do módulo terminal (já existiam no projeto)
from .terminal import (
    ensure_terminal_running as _ensure_terminal_running,
    attach_specific as _attach_specific,
)


class MT5Session:
    def __init__(self, exe_path: Path, login: Optional[str], password: Optional[str], server: Optional[str]) -> None:
        self.exe_path = exe_path
        self.base_dir = exe_path.parent
        self.login = login
        self.password = password
        self.server = server
        self._symmap: Optional[Dict[str, str]] = None

    # ---------- symbol map ----------
    def _load_symbol_map(self) -> Dict[str, str]:
        if self._symmap is not None:
            return self._symmap
        p = Path(os.getenv("SYMBOLS_MAP") or "outputs/live/symbols_map.yaml")
        m: Dict[str, str] = {}
        try:
            import yaml  # type: ignore
            if p.exists():
                data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                if isinstance(data, dict):
                    m = {str(k).upper(): str(v).upper() for k, v in data.items()}
        except Exception:
            m = {}
        self._symmap = m
        return m

    def resolve_symbol(self, symbol: str) -> str:
        s = (symbol or "").upper().strip()
        if not s:
            return s
        # se já existir no terminal, usa-o tal como está
        try:
            info = MT5.symbol_info(s)
            if info is not None:
                return s
        except Exception:
            pass
        # senão, tenta mapear
        return self._load_symbol_map().get(s, s)

    # ---------- terminal control wrappers ----------
    def ensure_terminal_running(self, max_wait_sec: int = 60) -> None:
        """Garante que o terminal (portable) desta instância está de pé."""
        _ensure_terminal_running(self.exe_path, self.base_dir, max_wait_sec=max_wait_sec)

    def attach_specific(self, max_wait_sec: int = 60) -> None:
        """
        Faz MT5.initialize/initialize_ex (conforme a tua implementação em .terminal)
        e espera pelo IPC do terminal correto (o da base_dir desta sessão).
        """
        _attach_specific(self.exe_path, self.base_dir, max_wait_sec=max_wait_sec)

    # ---------- lifecycle ----------
    def ensure_up(self) -> None:
        """Se o handle do MT5 tiver caído, reata a sessão ao terminal correto."""
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

        # religa ao terminal desta sessão
        self.ensure_terminal_running()
        self.attach_specific()

    # ---------- symbols ----------
    def ensure_symbol(self, symbol: str) -> None:
        info = MT5.symbol_info(symbol)
        if info is None:
            raise RuntimeError(f"symbol '{symbol}' not found")
        if not info.visible:
            if not MT5.symbol_select(symbol, True):
                raise RuntimeError(f"symbol_select failed for '{symbol}'")