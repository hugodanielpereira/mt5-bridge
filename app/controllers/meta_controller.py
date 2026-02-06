# app/controllers/meta_controller.py
from __future__ import annotations

import os
from typing import Any, Dict

from fastapi import APIRouter, Depends

from app.common.auth import require_api_key
from app.common.meta import bridge_instance_id, meta_snapshot

router = APIRouter(tags=["meta"])


# -----------------------------------------------------------------------------
# Mode helpers
# -----------------------------------------------------------------------------
def _mt5_mode() -> str:
    return (os.getenv("MT5_MODE", "webrequest") or "webrequest").strip().lower()


def _is_webrequest_mode() -> bool:
    return _mt5_mode() in ("webrequest", "http", "ea", "bridge")


def _is_native_mode() -> bool:
    return _mt5_mode() in ("native", "mt5", "metatrader5")


def _try_import_mt5():
    """
    Importa MetaTrader5 *apenas* em MT5_MODE=native.
    Em webrequest, retorna None.
    """
    if _is_webrequest_mode():
        return None
    if not _is_native_mode():
        # modo inválido -> trata como webrequest (fail-safe)
        return None
    try:
        import MetaTrader5 as mt5  # type: ignore
        return mt5
    except Exception:
        return None


# -----------------------------------------------------------------------------
# Normalization helpers
# -----------------------------------------------------------------------------
def _nt_get(obj: Any, key: str, default=None):
    try:
        return getattr(obj, key, default)
    except Exception:
        return default


def _account_row(ai: Any) -> Dict[str, Any]:
    if not ai:
        return {"connected": False}

    return {
        "connected": True,
        "login": _nt_get(ai, "login"),
        "name": _nt_get(ai, "name"),
        "server": _nt_get(ai, "server"),
        "currency": _nt_get(ai, "currency"),
        "balance": _nt_get(ai, "balance"),
        "equity": _nt_get(ai, "equity"),
        "profit": _nt_get(ai, "profit"),
        "margin": _nt_get(ai, "margin"),
        "margin_free": _nt_get(ai, "margin_free"),
        "margin_level": _nt_get(ai, "margin_level"),
        "leverage": _nt_get(ai, "leverage"),
        "company": _nt_get(ai, "company"),
    }


def _terminal_row(ti: Any) -> Dict[str, Any]:
    if not ti:
        return {"connected": False}

    return {
        "connected": True,
        "terminal_build": _nt_get(ti, "build"),
        "terminal_data_path": _nt_get(ti, "data_path"),
        "terminal_common_path": _nt_get(ti, "commondata_path"),
        "terminal_name": _nt_get(ti, "name"),
        "company": _nt_get(ti, "company"),
        "community_account": _nt_get(ti, "community_account"),
        "maxbars": _nt_get(ti, "maxbars"),
        "trade_allowed": _nt_get(ti, "trade_allowed"),
    }


# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------
@router.get("/meta")
def meta(_: None = Depends(require_api_key)):
    """
    Meta completo para multi-terminal/multi-broker:
      - bridge_instance_id
      - account_info (se MT5_MODE=native e MetaTrader5 estiver disponível)
      - terminal_info (idem)
      - meta_snapshot (ENV baseline) — sempre disponível
    """
    bid = bridge_instance_id()
    snap = meta_snapshot()  # sempre

    mt5 = _try_import_mt5()

    ti = None
    ai = None
    if mt5 is not None:
        try:
            ti = mt5.terminal_info()
        except Exception:
            ti = None
        try:
            ai = mt5.account_info()
        except Exception:
            ai = None

    # normaliza campos-chave (preferimos MT5 quando existir; senão snapshot)
    account_login = (_nt_get(ai, "login") if ai else None) or snap.get("account_login") or snap.get("mt5_login")
    server = (_nt_get(ai, "server") if ai else None) or snap.get("server") or snap.get("mt5_server")

    return {
        "ok": True,
        "mode": _mt5_mode(),
        "bridge_instance_id": bid,
        "account_login": account_login,
        "server": server,
        "account": _account_row(ai),
        "terminal": _terminal_row(ti),
        "snapshot": snap,
    }


@router.get("/meta/instance")
def instance(_: None = Depends(require_api_key)):
    """
    Endpoint legado/compat: mantém a forma “terminal-focused”,
    mas já inclui account_login/server para ajudar o LAB.

    Em MT5_MODE=webrequest devolve connected=False e campos None, mas não falha.
    """
    bid = bridge_instance_id()
    mt5 = _try_import_mt5()

    ti = None
    ai = None
    if mt5 is not None:
        try:
            ti = mt5.terminal_info()
        except Exception:
            ti = None
        try:
            ai = mt5.account_info()
        except Exception:
            ai = None

    return {
        "mode": _mt5_mode(),
        "bridge_instance_id": bid,
        "connected": bool(ti),
        "account_login": _nt_get(ai, "login") if ai else None,
        "server": _nt_get(ai, "server") if ai else None,
        "terminal_build": _nt_get(ti, "build") if ti else None,
        "terminal_data_path": _nt_get(ti, "data_path") if ti else None,
        "terminal_common_path": _nt_get(ti, "commondata_path") if ti else None,
        "terminal_name": _nt_get(ti, "name") if ti else None,
        "company": _nt_get(ti, "company") if ti else None,
    }