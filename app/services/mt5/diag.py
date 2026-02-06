# app/services/mt5/diag.py
from __future__ import annotations

from typing import Any, Dict
import os


def _mt5_mode() -> str:
    return (os.getenv("MT5_MODE", "webrequest") or "webrequest").strip().lower()


def _is_webrequest_mode() -> bool:
    return _mt5_mode() in ("webrequest", "http", "ea", "bridge")


def _load_mt5_native():
    try:
        import MetaTrader5 as MT5  # type: ignore
    except Exception as e:
        raise RuntimeError(f"MetaTrader5 import failed (MT5_MODE=native): {e!r}")
    return MT5


def diag(session) -> Dict[str, Any]:
    # tenta "ensure_up" (em webrequest é no-op; em native pode fazer attach)
    try:
        if hasattr(session, "ensure_up"):
            session.ensure_up()
    except Exception:
        pass

    # -------------------------
    # WebRequest mode (Bottles)
    # -------------------------
    if _is_webrequest_mode():
        # aqui o bridge não tem visibilidade do terminal via Python package
        return {
            "ok": True,  # ok = "bridge up" (não "mt5 connected")
            "mode": "webrequest",
            "terminal": None,
            "account": None,
            "last_error": None,
            "hint": "EA->HTTP WebRequest is the source of truth (MT5 Python API disabled).",
        }

    # -------------------------
    # Native mode (legado)
    # -------------------------
    MT5 = _load_mt5_native()

    try:
        ti = MT5.terminal_info()
    except Exception:
        ti = None
    try:
        ai = MT5.account_info()
    except Exception:
        ai = None
    try:
        le = MT5.last_error()
    except Exception:
        le = None

    terminal = None
    if ti:
        terminal = {
            "connected": getattr(ti, "connected", None),
            "trade_allowed": getattr(ti, "trade_allowed", None),
            "build": getattr(ti, "build", None),
            "data_path": getattr(ti, "data_path", None),
            "community_connection": getattr(ti, "community_connection", None),
        }

    account = None
    if ai:
        account = {
            "login": getattr(ai, "login", None),
            "server": getattr(ai, "server", None),
            "currency": getattr(ai, "currency", None),
            "balance": getattr(ai, "balance", None),
            "equity": getattr(ai, "equity", None),
            "company": getattr(ai, "company", None),
        }

    last_error = {"code": le[0], "message": le[1]} if isinstance(le, (list, tuple)) and len(le) >= 2 else None
    ok = bool(terminal and terminal.get("connected")) and bool(account and account.get("login"))

    return {
        "ok": ok,
        "mode": "native",
        "terminal": terminal,
        "account": account,
        "last_error": last_error,
    }