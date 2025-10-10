# app/controllers/history_controller.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pandas import Timestamp, Timedelta, to_datetime
import MetaTrader5 as mt5

from app.services.mt5 import MT5Service

router = APIRouter(tags=["history"])

# --- helpers ---------------------------------------------------------------

def _require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    import os
    expected = os.getenv("X_API_KEY") or os.getenv("BRIDGE_API_KEY") or os.getenv("API_KEY")
    if expected:
        if not x_api_key or x_api_key != expected:
            raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")

def _iso(v: Any) -> Optional[str]:
    """Converte timestamps (epoch/MT5) em ISO-8601 UTC."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        try:
            return to_datetime(v, unit="s", utc=True).isoformat()
        except Exception:
            return None
    # strings já ISO passam
    if isinstance(v, str):
        return v
    # datetime → ISO
    if hasattr(v, "isoformat"):
        try:
            # força timezone UTC se vier “naive”
            if getattr(v, "tzinfo", None) is None:
                v = v.replace(tzinfo=timezone.utc)
            return v.isoformat()
        except Exception:
            return None
    return None

def _filter_symbol(items: List[Dict[str, Any]], symbol: Optional[str]) -> List[Dict[str, Any]]:
    if not symbol:
        return items
    symu = symbol.upper()
    return [x for x in items if str(x.get("symbol", "")).upper() == symu]

def _filter_magic(items: List[Dict[str, Any]], magic: Optional[int]) -> List[Dict[str, Any]]:
    if magic is None:
        return items
    return [x for x in items if int(x.get("magic", -1)) == int(magic)]

# --- rotas ----------------------------------------------------------------

@router.get("/deals_history")
def deals_history(
    days: int = Query(7, ge=1, le=365),
    symbol: Optional[str] = Query(default=None),
    magic: Optional[int] = Query(default=None),
    _: None = Depends(_require_api_key),
):
    """Trades fechadas (deals) dos últimos N dias (ISO UTC)."""
    svc = MT5Service()
    end = Timestamp.utcnow().to_pydatetime()
    start = (Timestamp.utcnow() - Timedelta(days=days)).to_pydatetime()
    try:
        deals = svc.history_deals_get(start, end) or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"history_deals_get failed: {e}")

    out: List[Dict[str, Any]] = []
    for d in deals:
        row = {
            "time": _iso(getattr(d, "time", None)),
            "symbol": getattr(d, "symbol", ""),
            "entry": getattr(d, "entry", None),
            "price": getattr(d, "price", None),
            "volume": getattr(d, "volume", None),
            "profit": getattr(d, "profit", None),
            "magic": getattr(d, "magic", None),
            "comment": getattr(d, "comment", ""),
            "ticket": getattr(d, "ticket", None),
            "position_id": getattr(d, "position_id", None),
        }
        out.append(row)

    out = _filter_symbol(out, symbol)
    out = _filter_magic(out, magic)

    # ordena cronologicamente por time
    out.sort(key=lambda x: x["time"] or "")
    return out


@router.get("/orders_history")
def orders_history(
    days: int = Query(7, ge=1, le=365),
    symbol: Optional[str] = Query(default=None),
    magic: Optional[int] = Query(default=None),
    _: None = Depends(_require_api_key),
):
    """Orders históricas (ordens emitidas) dos últimos N dias (ISO UTC)."""
    end = Timestamp.utcnow().to_pydatetime()
    start = (Timestamp.utcnow() - Timedelta(days=days)).to_pydatetime()
    try:
        orders = mt5.history_orders_get(start, end) or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"history_orders_get failed: {e}")

    out: List[Dict[str, Any]] = []
    for o in orders:
        row = {
            "time_setup": _iso(getattr(o, "time_setup", None)),
            "time_done":  _iso(getattr(o, "time_done", None)),
            "symbol": getattr(o, "symbol", ""),
            "type": getattr(o, "type", None),
            "price_open": getattr(o, "price_open", None),
            "volume_initial": getattr(o, "volume_initial", None),
            "volume_current": getattr(o, "volume_current", None),
            "magic": getattr(o, "magic", None),
            "comment": getattr(o, "comment", ""),
            "order": getattr(o, "order", None),
        }
        out.append(row)

    out = _filter_symbol(out, symbol)
    out = _filter_magic(out, magic)

    # ordena por time_done, depois time_setup
    out.sort(key=lambda x: (x["time_done"] or x["time_setup"] or ""))
    return out


@router.get("/history")
def history(
    days: int = Query(7, ge=1, le=365),
    symbol: Optional[str] = Query(default=None),
    magic: Optional[int] = Query(default=None),
    _: None = Depends(_require_api_key),
):
    """
    Resumo: orders + deals dos últimos N dias (ISO UTC) com filtros opcionais:
    - ?symbol=BTCUSD
    - ?magic=7701
    """
    end = Timestamp.utcnow().to_pydatetime()
    start = (Timestamp.utcnow() - Timedelta(days=days)).to_pydatetime()

    # Orders
    try:
        orders_raw = mt5.history_orders_get(start, end) or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"history_orders_get failed: {e}")

    orders = []
    for o in orders_raw:
        row = {
            "time_setup": _iso(getattr(o, "time_setup", None)),
            "time_done":  _iso(getattr(o, "time_done", None)),
            "symbol": getattr(o, "symbol", ""),
            "type": getattr(o, "type", None),
            "price_open": getattr(o, "price_open", None),
            "volume_initial": getattr(o, "volume_initial", None),
            "volume_current": getattr(o, "volume_current", None),
            "magic": getattr(o, "magic", None),
            "comment": getattr(o, "comment", ""),
            "order": getattr(o, "order", None),
        }
        orders.append(row)

    orders = _filter_symbol(orders, symbol)
    orders = _filter_magic(orders, magic)
    orders.sort(key=lambda x: (x["time_done"] or x["time_setup"] or ""))

    # Deals
    svc = MT5Service()
    try:
        deals_raw = svc.history_deals_get(start, end) or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"history_deals_get failed: {e}")

    deals = []
    for d in deals_raw:
        row = {
            "time": _iso(getattr(d, "time", None)),
            "symbol": getattr(d, "symbol", ""),
            "entry": getattr(d, "entry", None),
            "price": getattr(d, "price", None),
            "volume": getattr(d, "volume", None),
            "profit": getattr(d, "profit", None),
            "magic": getattr(d, "magic", None),
            "comment": getattr(d, "comment", ""),
            "ticket": getattr(d, "ticket", None),
            "position_id": getattr(d, "position_id", None),
        }
        deals.append(row)

    deals = _filter_symbol(deals, symbol)
    deals = _filter_magic(deals, magic)
    deals.sort(key=lambda x: x["time"] or "")

    return {"orders": orders, "deals": deals}