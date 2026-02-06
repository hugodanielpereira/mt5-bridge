# app/controllers/debug_controller.py
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, HTTPException

from app.common.service_registry import get_service
from app.services.mt5.utils import sanitize_comment, nt_to_dict, ensure_symbol

router = APIRouter(tags=["debug"])


# -----------------------------------------------------------------------------
# Mode helpers
# -----------------------------------------------------------------------------
def _mt5_mode() -> str:
    return (os.getenv("MT5_MODE", "webrequest") or "webrequest").strip().lower()


def _is_webrequest_mode() -> bool:
    return _mt5_mode() in ("webrequest", "http", "ea", "bridge")


def _is_native_mode() -> bool:
    return _mt5_mode() in ("native", "mt5", "metatrader5")


def _require_native(feature: str):
    if _is_webrequest_mode() or not _is_native_mode():
        raise HTTPException(
            status_code=501,
            detail=(
                f"{feature} requires MT5_MODE=native (MetaTrader5 Python API). "
                f"Current MT5_MODE={_mt5_mode()!r}. In webrequest mode the EA talks to the bridge via HTTP."
            ),
        )


def _mt5():
    """
    Importa MetaTrader5 apenas quando estamos em native.
    """
    _require_native("debug endpoints")
    try:
        import MetaTrader5 as mt5  # type: ignore
        return mt5
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "MetaTrader5 import failed (native required)",
                "exc": repr(e),
                "mt5_mode": _mt5_mode(),
            },
        )


@router.post("/trade_check")
def trade_check(payload: dict):
    """
    Faz order_check para várias filling modes (debug).
    NATIVE ONLY: precisa de MetaTrader5 Python API.
    """
    mt5 = _mt5()

    svc = get_service()
    svc.ensure_up()

    symbol = payload.get("symbol")
    side = (payload.get("side") or "").lower()
    try:
        volume = float(payload.get("volume") or 0)
    except Exception:
        volume = 0.0

    if not symbol or side not in ("buy", "sell") or volume <= 0:
        raise HTTPException(status_code=400, detail="need symbol, side in {buy|sell}, volume>0")

    # garante símbolo visível/existente (native)
    try:
        ensure_symbol(symbol)
    except Exception as e:
        # ensure_symbol já usa MT5 internamente; mas pode falhar por symbol inexistente
        raise HTTPException(status_code=400, detail={"error": "ensure_symbol failed", "exc": str(e), "symbol": symbol})

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        code, msg = mt5.last_error()
        raise HTTPException(status_code=500, detail={"error": "symbol_info_tick None", "last_error": [code, msg]})

    price = float(tick.ask if side == "buy" else tick.bid)
    otype = mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL

    info = mt5.symbol_info(symbol)
    cands = []
    if info and hasattr(info, "filling_mode"):
        try:
            cands.append(int(info.filling_mode))
        except Exception:
            pass

    for fm in (
        getattr(mt5, "ORDER_FILLING_FOK", None),
        getattr(mt5, "ORDER_FILLING_IOC", None),
        getattr(mt5, "ORDER_FILLING_RETURN", None),
    ):
        if fm is not None and int(fm) not in cands:
            cands.append(int(fm))

    base_req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": otype,
        "price": price,
        "type_time": mt5.ORDER_TIME_GTC,
        "magic": int(payload.get("magic", 2025)),
        "deviation": int(payload.get("deviation", 100)),
        "comment": sanitize_comment(payload.get("comment", "smoke")),
    }

    results = []
    for fm in cands or [None]:
        req = dict(base_req)
        if fm is not None:
            req["type_filling"] = fm
        chk = mt5.order_check(req)
        code, msg = mt5.last_error()
        results.append(
            {
                "filling": fm,
                "check": nt_to_dict(chk) if chk else None,
                "last_error": [code, msg],
            }
        )

    return {"mt5_mode": _mt5_mode(), "candidates": cands, "checks": results}