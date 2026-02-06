# app/controllers/marketdata_controller.py
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, HTTPException

from app.common.auth import require_api_key
from app.common.service_registry import get_service
from app.services.mt5.state_store import STORE
from app.services.mt5 import MT5Service

router = APIRouter(tags=["marketdata"])


# ----------------------------
# Helpers
# ----------------------------
def _mt5_mode() -> str:
    return (os.getenv("MT5_MODE", "webrequest") or "webrequest").strip().lower()


def _is_webrequest_mode(svc: Optional[MT5Service] = None) -> bool:
    try:
        if svc is not None and hasattr(svc, "webrequest_mode"):
            return bool(getattr(svc, "webrequest_mode"))
    except Exception:
        pass
    return _mt5_mode() in ("webrequest", "http", "ea", "bridge")


def _ea_meta() -> Dict[str, Any]:
    st = STORE.snapshot()
    ttl = int(os.getenv("EA_TTL_MS", "5000"))
    return {
        "ea_connected": st.is_ea_connected(ttl_ms=ttl),
        "last_heartbeat_ms": st.last_heartbeat_ms,
        "bridge_instance_id": getattr(st, "bridge_instance_id", None),
    }


def _get_rows_webrequest(symbol: str, tf: str, limit: int) -> List[Dict[str, Any]]:
    st = STORE.snapshot()
    sym = symbol.upper().strip()
    t = tf.upper().strip()
    key = f"{sym}:{t}"
    rows = (st.ohlcv or {}).get(key) or []
    if limit:
        rows = rows[-int(limit):]
    return rows


def _get_rows_native(symbol: str, tf: str, limit: int, start: Optional[str], end: Optional[str]) -> Dict[str, Any]:
    svc = get_service()
    svc.ensure_up()
    return svc.ohlcv(symbol=symbol, tf=tf, start=start, end=end, limit=limit)


def _resp_ok(symbol: str, tf: str, rows: List[Dict[str, Any]], *, mode: str) -> Dict[str, Any]:
    return {
        "ok": True,
        "mode": mode,
        "symbol": symbol.upper().strip(),
        "tf": tf.upper().strip(),
        "count": len(rows),
        "rows": rows,
        **_ea_meta(),
    }


# ----------------------------
# Canonical endpoint
# ----------------------------
@router.get("/ohlcv")
def ohlcv(
    symbol: str = Query(..., min_length=1),
    tf: str = Query("H1"),
    limit: int = Query(1000, ge=1, le=5000),
    start: Optional[str] = Query(default=None),
    end: Optional[str] = Query(default=None),
    _: None = Depends(require_api_key),
):
    svc = None
    try:
        svc = get_service()
    except Exception:
        svc = None

    if _is_webrequest_mode(svc):
        rows = _get_rows_webrequest(symbol, tf, limit)
        return _resp_ok(symbol, tf, rows, mode="webrequest")

    # native
    out = _get_rows_native(symbol, tf, limit, start, end)
    return out


# ----------------------------
# Endpoints exigidos pelo LAB (OFICIAIS)
# ----------------------------
@router.get("/candles")
def candles(
    symbol: str = Query(..., min_length=1),
    tf: str = Query("H1"),
    limit: int = Query(120, ge=1, le=5000),
    _: None = Depends(require_api_key),
):
    # Mesma semântica: devolve OHLCV
    svc = None
    try:
        svc = get_service()
    except Exception:
        svc = None

    if _is_webrequest_mode(svc):
        rows = _get_rows_webrequest(symbol, tf, limit)
        return _resp_ok(symbol, tf, rows, mode="webrequest")

    out = _get_rows_native(symbol, tf, limit, None, None)
    return out


@router.get("/bars")
def bars(
    symbol: str = Query(..., min_length=1),
    tf: str = Query("H1"),
    limit: int = Query(120, ge=1, le=5000),
    _: None = Depends(require_api_key),
):
    svc = None
    try:
        svc = get_service()
    except Exception:
        svc = None

    if _is_webrequest_mode(svc):
        rows = _get_rows_webrequest(symbol, tf, limit)
        return _resp_ok(symbol, tf, rows, mode="webrequest")

    out = _get_rows_native(symbol, tf, limit, None, None)
    return out


@router.get("/ohlc")
def ohlc(
    symbol: str = Query(..., min_length=1),
    timeframe: str = Query("H1"),
    limit: int = Query(120, ge=1, le=5000),
    _: None = Depends(require_api_key),
):
    # compat: timeframe=H1 (não tf)
    tf = timeframe
    svc = None
    try:
        svc = get_service()
    except Exception:
        svc = None

    if _is_webrequest_mode(svc):
        rows = _get_rows_webrequest(symbol, tf, limit)
        return _resp_ok(symbol, tf, rows, mode="webrequest")

    out = _get_rows_native(symbol, tf, limit, None, None)
    return out


@router.get("/history")
def history(
    symbol: str = Query(..., min_length=1),
    timeframe: str = Query("H1"),
    limit: int = Query(300, ge=1, le=5000),
    _: None = Depends(require_api_key),
):
    # Este /history do LAB é marketdata (não o native-only history_controller)
    tf = timeframe
    svc = None
    try:
        svc = get_service()
    except Exception:
        svc = None

    if _is_webrequest_mode(svc):
        rows = _get_rows_webrequest(symbol, tf, limit)
        return _resp_ok(symbol, tf, rows, mode="webrequest")

    out = _get_rows_native(symbol, tf, limit, None, None)
    return out


@router.get("/api/ohlcv")
def api_ohlcv(
    symbol: str = Query(..., min_length=1),
    tf: str = Query("H1"),
    limit: int = Query(1000, ge=1, le=5000),
    _: None = Depends(require_api_key),
):
    # compat endpoint visto nos logs
    svc = None
    try:
        svc = get_service()
    except Exception:
        svc = None

    if _is_webrequest_mode(svc):
        rows = _get_rows_webrequest(symbol, tf, limit)
        return _resp_ok(symbol, tf, rows, mode="webrequest")

    out = _get_rows_native(symbol, tf, limit, None, None)
    return out


@router.get("/mt5/ohlcv")
def mt5_ohlcv(
    symbol: str = Query(..., min_length=1),
    tf: str = Query("H1"),
    limit: int = Query(1000, ge=1, le=5000),
    _: None = Depends(require_api_key),
):
    # compat endpoint visto nos logs
    svc = None
    try:
        svc = get_service()
    except Exception:
        svc = None

    if _is_webrequest_mode(svc):
        rows = _get_rows_webrequest(symbol, tf, limit)
        return _resp_ok(symbol, tf, rows, mode="webrequest")

    out = _get_rows_native(symbol, tf, limit, None, None)
    return out