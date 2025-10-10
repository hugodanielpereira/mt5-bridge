# app/services/mt5/diag.py
from __future__ import annotations
from typing import Any, Dict
import MetaTrader5 as MT5

def diag(session) -> Dict[str, Any]:
    try:
        session.ensure_up()
    except Exception:
        pass

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

    return {"ok": ok, "terminal": terminal, "account": account, "last_error": last_error}