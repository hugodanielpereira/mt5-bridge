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
    # Symbol: do NOT uppercase — the EA stores keys with the exact broker casing
    # (e.g. "SpotCrude", "NatGas").  state_store.set_ohlcv() preserves case on
    # the write side, so we must match exactly here.  Uppercasing breaks any broker
    # symbol that uses mixed case.
    sym = symbol.strip()
    t = tf.upper().strip()
    key = f"{sym}:{t}"
    rows = (st.ohlcv or {}).get(key) or []
    if not rows:
        # Fallback: try uppercase in case the EA stored with a different convention
        key_upper = f"{sym.upper()}:{t}"
        rows = (st.ohlcv or {}).get(key_upper) or []
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
        "symbol": symbol.strip(),        # preserve broker casing (e.g. SpotCrude)
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


# ----------------------------
# Symbol info endpoint (webrequest-mode safe)
# ----------------------------

# Static tick specs for common symbols — used as fallback when EA hasn't pushed symbol_info.
# tick_value is approximate (EUR account, ~2026 mid-rates).  Accurate to ±10% as rates drift,
# which is far better than the 100-200× overstatement from the default 0.00001/1.0 fallback.
_STATIC_SYMBOL_SPECS: Dict[str, Dict[str, Any]] = {
    # Standard 5-decimal forex (account EUR ~1.08 USD)
    "EURUSD":   {"tick_size": 0.00001, "point": 0.00001, "digits": 5, "tick_value": 0.93, "volume_min": 0.01, "volume_step": 0.01},
    "GBPUSD":   {"tick_size": 0.00001, "point": 0.00001, "digits": 5, "tick_value": 0.93, "volume_min": 0.01, "volume_step": 0.01},
    "AUDUSD":   {"tick_size": 0.00001, "point": 0.00001, "digits": 5, "tick_value": 0.93, "volume_min": 0.01, "volume_step": 0.01},
    "NZDUSD":   {"tick_size": 0.00001, "point": 0.00001, "digits": 5, "tick_value": 0.93, "volume_min": 0.01, "volume_step": 0.01},
    "USDCAD":   {"tick_size": 0.00001, "point": 0.00001, "digits": 5, "tick_value": 0.68, "volume_min": 0.01, "volume_step": 0.01},
    "USDCHF":   {"tick_size": 0.00001, "point": 0.00001, "digits": 5, "tick_value": 1.04, "volume_min": 0.01, "volume_step": 0.01},
    "EURCAD":   {"tick_size": 0.00001, "point": 0.00001, "digits": 5, "tick_value": 0.68, "volume_min": 0.01, "volume_step": 0.01},
    "EURGBP":   {"tick_size": 0.00001, "point": 0.00001, "digits": 5, "tick_value": 1.17, "volume_min": 0.01, "volume_step": 0.01},
    "EURAUD":   {"tick_size": 0.00001, "point": 0.00001, "digits": 5, "tick_value": 0.60, "volume_min": 0.01, "volume_step": 0.01},
    "GBPCHF":   {"tick_size": 0.00001, "point": 0.00001, "digits": 5, "tick_value": 1.04, "volume_min": 0.01, "volume_step": 0.01},
    "AUDCAD":   {"tick_size": 0.00001, "point": 0.00001, "digits": 5, "tick_value": 0.68, "volume_min": 0.01, "volume_step": 0.01},
    "EURNZD":   {"tick_size": 0.00001, "point": 0.00001, "digits": 5, "tick_value": 0.56, "volume_min": 0.01, "volume_step": 0.01},
    "GBPAUD":   {"tick_size": 0.00001, "point": 0.00001, "digits": 5, "tick_value": 0.60, "volume_min": 0.01, "volume_step": 0.01},
    "AUDNZD":   {"tick_size": 0.00001, "point": 0.00001, "digits": 5, "tick_value": 0.56, "volume_min": 0.01, "volume_step": 0.01},
    "GBPNZD":   {"tick_size": 0.00001, "point": 0.00001, "digits": 5, "tick_value": 0.56, "volume_min": 0.01, "volume_step": 0.01},
    # JPY pairs (3-decimal — tick_size=0.001; tick_value per lot ≈ 100k/EURJPY)
    "USDJPY":   {"tick_size": 0.001, "point": 0.001, "digits": 3, "tick_value": 0.62, "volume_min": 0.01, "volume_step": 0.01},
    "EURJPY":   {"tick_size": 0.001, "point": 0.001, "digits": 3, "tick_value": 0.62, "volume_min": 0.01, "volume_step": 0.01},
    "GBPJPY":   {"tick_size": 0.001, "point": 0.001, "digits": 3, "tick_value": 0.62, "volume_min": 0.01, "volume_step": 0.01},
    "AUDJPY":   {"tick_size": 0.001, "point": 0.001, "digits": 3, "tick_value": 0.62, "volume_min": 0.01, "volume_step": 0.01},
    "NZDJPY":   {"tick_size": 0.001, "point": 0.001, "digits": 3, "tick_value": 0.62, "volume_min": 0.01, "volume_step": 0.01},
    "CADJPY":   {"tick_size": 0.001, "point": 0.001, "digits": 3, "tick_value": 0.62, "volume_min": 0.01, "volume_step": 0.01},
    "CHFJPY":   {"tick_size": 0.001, "point": 0.001, "digits": 3, "tick_value": 0.62, "volume_min": 0.01, "volume_step": 0.01},
    # Metals
    "XAUUSD":   {"tick_size": 0.01, "point": 0.01, "digits": 2, "tick_value": 0.93, "volume_min": 0.01, "volume_step": 0.01},
    "GOLD":     {"tick_size": 0.01, "point": 0.01, "digits": 2, "tick_value": 0.93, "volume_min": 0.01, "volume_step": 0.01},
    "XAGUSD":   {"tick_size": 0.001, "point": 0.001, "digits": 3, "tick_value": 0.93, "volume_min": 0.01, "volume_step": 0.01},
    # Energy
    "USOIL":    {"tick_size": 0.001, "point": 0.001, "digits": 3, "tick_value": 0.93, "volume_min": 0.01, "volume_step": 0.01},
    "XTIUSD":   {"tick_size": 0.001, "point": 0.001, "digits": 3, "tick_value": 0.93, "volume_min": 0.01, "volume_step": 0.01},
    "XBRUSD":   {"tick_size": 0.001, "point": 0.001, "digits": 3, "tick_value": 0.93, "volume_min": 0.01, "volume_step": 0.01},
    "SpotCrude":{"tick_size": 0.001, "point": 0.001, "digits": 3, "tick_value": 0.93, "volume_min": 0.01, "volume_step": 0.01},
    "NatGas":   {"tick_size": 0.001, "point": 0.001, "digits": 3, "tick_value": 0.93, "volume_min": 0.01, "volume_step": 0.01},
}


@router.get("/symbol_info")
def get_symbol_info(
    symbol: str = Query(..., min_length=1),
    _: None = Depends(require_api_key),
):
    """Return symbol specs (tick_size, tick_value, digits, volume_min, volume_step).

    webrequest mode: reads EA-pushed STORE.symbol_info; falls back to _STATIC_SYMBOL_SPECS.
    native mode: calls MT5Service.symbol_info().
    """
    svc = None
    try:
        svc = get_service()
    except Exception:
        svc = None

    if _is_webrequest_mode(svc):
        st = STORE.snapshot()
        sym = symbol.strip()

        # 1. EA-pushed symbol info (most accurate — reflects broker's actual values)
        stored = (st.symbol_info or {}).get(sym) or (st.symbol_info or {}).get(sym.upper())
        if stored:
            return {"ok": True, "symbol": sym, "source": "ea_push", "info": stored, **_ea_meta()}

        # 2. Static fallback (covers common symbols — accurate tick_size, approximate tick_value)
        static = _STATIC_SYMBOL_SPECS.get(sym) or _STATIC_SYMBOL_SPECS.get(sym.upper())
        if static:
            return {"ok": True, "symbol": sym, "source": "static", "info": dict(static), **_ea_meta()}

        raise HTTPException(status_code=404, detail=f"No symbol info for {sym!r} (EA not connected?)")

    # native mode
    try:
        info = svc.symbol_info(symbol)
        return {"ok": True, "symbol": symbol, "source": "native", "info": info}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


# ----------------------------
# Live price endpoint
# ----------------------------
@router.get("/price/{symbol}")
def get_price(
    symbol: str,
    _: None = Depends(require_api_key),
):
    """Return live bid/ask/mid for a symbol.

    webrequest mode: reads EA-pushed quotes from STORE; falls back to last M1 bar close.
    native mode: calls symbol_info_tick via MT5Service.
    """
    svc = None
    try:
        svc = get_service()
    except Exception:
        svc = None

    if _is_webrequest_mode(svc):
        st = STORE.snapshot()
        sym = symbol.strip()

        # 1. Try EA-pushed quote (has live bid/ask)
        quote = (st.quotes or {}).get(sym) or (st.quotes or {}).get(sym.upper())
        if quote:
            bid = float(quote.get("bid") or 0)
            ask = float(quote.get("ask") or 0)
            mid = round((bid + ask) / 2, 8) if bid and ask else (bid or ask)
            return {"ok": True, "symbol": sym, "bid": bid, "ask": ask, "mid": mid, "source": "quote", **_ea_meta()}

        # 2. Fall back to last M1 bar close
        rows = _get_rows_webrequest(sym, "M1", 1)
        if not rows:
            # Try H1 as last resort
            rows = _get_rows_webrequest(sym, "H1", 1)
        if rows:
            close = float(rows[-1].get("close") or 0)
            return {"ok": True, "symbol": sym, "bid": close, "ask": close, "mid": close, "source": "bar", **_ea_meta()}

        raise HTTPException(status_code=503, detail=f"No price available for {sym}")

    # native mode
    try:
        q = svc.quote(symbol)
        bid = float(q.get("bid") or 0)
        ask = float(q.get("ask") or 0)
        mid = round((bid + ask) / 2, 8) if bid and ask else (bid or ask)
        return {"ok": True, "symbol": symbol, "bid": bid, "ask": ask, "mid": mid, "source": "tick"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))