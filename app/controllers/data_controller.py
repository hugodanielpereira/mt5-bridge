# app/controllers/data_controller.py
from __future__ import annotations

import logging
import os
from typing import Optional, Tuple

from fastapi import APIRouter, HTTPException, Query

from app.common.service_registry import get_service

router = APIRouter(tags=["data"])
log = logging.getLogger("bridge")


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _mt5_mode() -> str:
    return (os.getenv("MT5_MODE", "webrequest") or "webrequest").strip().lower()


def _is_native_mode() -> bool:
    return _mt5_mode() in ("native", "mt5", "metatrader5")


def _safe_mt5_last_error() -> Tuple[Optional[int], Optional[str]]:
    """
    Só tenta ler MT5.last_error quando estamos em MT5_MODE=native.
    Em webrequest mode isto deve devolver (None, None) e NÃO tentar imports.
    """
    if not _is_native_mode():
        return (None, None)
    try:
        import MetaTrader5 as MT5  # type: ignore
        code, msg = MT5.last_error()
        return (code, msg)
    except Exception:
        return (None, None)


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@router.get("/symbols")
def symbols():
    try:
        svc = get_service()
        svc.ensure_up()
        return svc.list_symbols()
    except Exception as e:
        log.exception("symbols failed")
        raise HTTPException(status_code=500, detail={"error": "symbols crashed", "exc": str(e)})


@router.get("/ohlcv")
def ohlcv(
    symbol: str = Query(..., min_length=1),
    tf: str = Query("H1"),
    start: Optional[str] = Query(default=None),
    end: Optional[str] = Query(default=None),
    limit: int = Query(1000, ge=1, le=5000),
):
    try:
        svc = get_service()
        svc.ensure_up()
        return svc.ohlcv(symbol=symbol, tf=tf, start=start, end=end, limit=limit)
    except Exception as e:
        log.exception("ohlcv failed")
        raise HTTPException(status_code=500, detail={"error": "ohlcv crashed", "exc": str(e)})


@router.get("/symbol_info")
def symbol_info(symbol: str = Query(..., min_length=1)):
    try:
        svc = get_service()
        svc.ensure_up()
        return svc.symbol_info(symbol)
    except Exception as e:
        code, msg = _safe_mt5_last_error()
        log.exception("symbol_info failed")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "symbol_info crashed",
                "exc": str(e),
                "last_error": [code, msg] if (code is not None or msg is not None) else None,
                "mt5_mode": _mt5_mode(),
            },
        )


@router.get("/quote")
def quote(symbol: str = Query(..., min_length=1)):
    try:
        svc = get_service()
        svc.ensure_up()
        return svc.quote(symbol)
    except Exception as e:
        code, msg = _safe_mt5_last_error()
        log.exception("quote failed")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "quote crashed",
                "exc": str(e),
                "last_error": [code, msg] if (code is not None or msg is not None) else None,
                "mt5_mode": _mt5_mode(),
            },
        )