# app/controllers/history_controller.py
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
import os

from fastapi import APIRouter, Depends, HTTPException, Query

from app.common.auth import require_api_key
from app.common.service_registry import get_service
from app.common.meta import bridge_instance_id
from app.services.mt5.state_store import STORE

router = APIRouter(tags=["history"])


# -----------------------------------------------------------------------------
# Helpers (mode / errors)
# -----------------------------------------------------------------------------
def _is_webrequest_mode() -> bool:
    mode = (os.getenv("MT5_MODE", "webrequest") or "webrequest").strip().lower()
    return mode in ("webrequest", "http", "ea", "bridge")


def _require_native_or_501(endpoint: str) -> None:
    if _is_webrequest_mode():
        raise HTTPException(
            status_code=501,
            detail={
                "error": "native_only_endpoint",
                "endpoint": endpoint,
                "mt5_mode": (os.getenv("MT5_MODE", "webrequest") or "webrequest"),
                "hint": "Este endpoint requer MT5_MODE=native (MetaTrader5 python package). "
                        "Em MT5_MODE=webrequest o bridge recebe dados do EA via WebRequest (EA->HTTP).",
            },
        )


def _native_last_error() -> Optional[Tuple[Any, Any]]:
    try:
        if _is_webrequest_mode():
            return None
        import MetaTrader5 as mt5  # type: ignore
        code, msg = mt5.last_error()
        return (code, msg)
    except Exception:
        return None


# -----------------------------------------------------------------------------
# Helpers (time parsing / formatting)  [no pandas]
# -----------------------------------------------------------------------------
def _iso_utc(v: Any) -> Optional[str]:
    """Converte epoch seconds / datetime-like / string -> ISO-8601 UTC."""
    if v is None:
        return None

    # epoch seconds
    if isinstance(v, (int, float)):
        try:
            dt = datetime.fromtimestamp(float(v), tz=timezone.utc)
            return dt.isoformat()
        except Exception:
            return None

    # already a string
    if isinstance(v, str):
        return v

    # datetime-like
    if hasattr(v, "isoformat"):
        try:
            dt = v  # type: ignore[assignment]
            if getattr(dt, "tzinfo", None) is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt.isoformat()
        except Exception:
            return None

    return None


def _parse_since(since: Optional[str]) -> Optional[datetime]:
    """
    since: ISO-8601 (idealmente com Z). Ex: 2025-12-13T00:00:00Z
    """
    if not since:
        return None
    s = since.strip()
    try:
        # suporta "...Z"
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"

        dt = datetime.fromisoformat(s)

        # se vier sem timezone, assumimos UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)

        return dt
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="invalid 'since' (use ISO-8601, ex: 2025-01-01T00:00:00Z)",
        )


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _range_from_since_or_days(since_dt: Optional[datetime], days: int) -> Tuple[datetime, datetime]:
    end = _now_utc()
    if since_dt is not None:
        start = since_dt
    else:
        start = end - timedelta(days=int(days))
    return start, end


def _filter_symbol(items: List[Dict[str, Any]], symbol: Optional[str]) -> List[Dict[str, Any]]:
    if not symbol:
        return items
    symu = symbol.upper()
    return [x for x in items if str(x.get("symbol", "")).upper() == symu]


def _filter_magic(items: List[Dict[str, Any]], magic: Optional[int]) -> List[Dict[str, Any]]:
    if magic is None:
        return items
    try:
        mi = int(magic)
    except Exception:
        return items
    return [x for x in items if int(x.get("magic", -1) or -1) == mi]


# -----------------------------------------------------------------------------
# Row normalizers
# -----------------------------------------------------------------------------
def _deal_row(d: Any) -> Dict[str, Any]:
    time_sec = getattr(d, "time", None)
    time_msc = getattr(d, "time_msc", None)

    deal_id = getattr(d, "deal", None)
    if deal_id is None:
        deal_id = getattr(d, "ticket", None)

    return {
        "time": _iso_utc(time_sec),
        "time_msc": int(time_msc) if isinstance(time_msc, (int, float)) else None,
        "deal": deal_id,
        "order": getattr(d, "order", None),
        "position_id": getattr(d, "position_id", None),
        "symbol": getattr(d, "symbol", ""),
        "type": getattr(d, "type", None),
        "entry": getattr(d, "entry", None),
        "price": getattr(d, "price", None),
        "volume": getattr(d, "volume", None),
        "profit": getattr(d, "profit", None),
        "commission": getattr(d, "commission", None),
        "swap": getattr(d, "swap", None),
        "fee": getattr(d, "fee", None),
        "magic": getattr(d, "magic", None),
        "comment": getattr(d, "comment", ""),
    }


def _order_row(o: Any) -> Dict[str, Any]:
    time_setup = getattr(o, "time_setup", None)
    time_done = getattr(o, "time_done", None)
    time_setup_msc = getattr(o, "time_setup_msc", None)
    time_done_msc = getattr(o, "time_done_msc", None)

    return {
        "time_setup": _iso_utc(time_setup),
        "time_done": _iso_utc(time_done),
        "time_setup_msc": int(time_setup_msc) if isinstance(time_setup_msc, (int, float)) else None,
        "time_done_msc": int(time_done_msc) if isinstance(time_done_msc, (int, float)) else None,
        "order": getattr(o, "order", None),
        "position_id": getattr(o, "position_id", None),
        "symbol": getattr(o, "symbol", ""),
        "type": getattr(o, "type", None),
        "price_open": getattr(o, "price_open", None),
        "volume_initial": getattr(o, "volume_initial", None),
        "volume_current": getattr(o, "volume_current", None),
        "magic": getattr(o, "magic", None),
        "comment": getattr(o, "comment", ""),
        "state": getattr(o, "state", None),
        "reason": getattr(o, "reason", None),
    }


# -----------------------------------------------------------------------------
# Deals / Orders (histórico)
# -----------------------------------------------------------------------------
def _deals_from_store(symbol: Optional[str], magic: Optional[int]) -> List[Dict[str, Any]]:
    """Read deals history from in-memory store (pushed by EA in webrequest mode)."""
    st = STORE.snapshot()
    out = list(st.deals_history or [])
    out = _filter_symbol(out, symbol)
    out = _filter_magic(out, magic)
    out.sort(key=lambda x: (x.get("time_msc") or 0, x.get("time") or ""))
    return out


def _orders_from_store(symbol: Optional[str], magic: Optional[int]) -> List[Dict[str, Any]]:
    """Read orders history from in-memory store (pushed by EA in webrequest mode)."""
    st = STORE.snapshot()
    out = list(st.orders_history or [])
    out = _filter_symbol(out, symbol)
    out = _filter_magic(out, magic)
    out.sort(key=lambda x: (x.get("time_done_msc") or 0, x.get("time_done") or x.get("time_setup") or ""))
    return out


@router.get("/deals_history")
def deals_history(
    days: int = Query(7, ge=1, le=365),
    symbol: Optional[str] = Query(default=None),
    magic: Optional[int] = Query(default=None),
    _: None = Depends(require_api_key),
):
    # Webrequest mode: serve from EA-pushed store
    if _is_webrequest_mode():
        out = _deals_from_store(symbol, magic)
        return {"ok": True, "bridge_instance_id": bridge_instance_id(),
                "mode": "webrequest", "count": len(out), "data": out}

    svc = get_service()
    svc.ensure_up()

    start, end = _range_from_since_or_days(None, days)
    try:
        deals = svc.history_deals_get(start, end) or []
    except Exception as e:
        le = _native_last_error()
        raise HTTPException(
            status_code=500,
            detail={"error": "history_deals_get failed", "exc": str(e), "last_error": list(le) if le else None},
        )

    out = [_deal_row(d) for d in deals]
    out = _filter_symbol(out, symbol)
    out = _filter_magic(out, magic)
    out.sort(key=lambda x: (x.get("time_msc") or 0, x.get("time") or ""))

    return {"ok": True, "bridge_instance_id": bridge_instance_id(), "count": len(out), "data": out}


@router.get("/orders_history")
def orders_history(
    days: int = Query(7, ge=1, le=365),
    symbol: Optional[str] = Query(default=None),
    magic: Optional[int] = Query(default=None),
    _: None = Depends(require_api_key),
):
    # Webrequest mode: serve from EA-pushed store
    if _is_webrequest_mode():
        out = _orders_from_store(symbol, magic)
        return {"ok": True, "bridge_instance_id": bridge_instance_id(),
                "mode": "webrequest", "count": len(out), "data": out}

    svc = get_service()
    svc.ensure_up()

    start, end = _range_from_since_or_days(None, days)

    try:
        orders = svc.history.orders_recent(days=days)  # type: ignore[attr-defined]
        try:
            orders = svc.history.history_orders_get(start, end)  # type: ignore[attr-defined]
        except Exception:
            pass
        orders = orders or []
    except Exception as e:
        le = _native_last_error()
        raise HTTPException(
            status_code=500,
            detail={"error": "history_orders_get failed", "exc": str(e), "last_error": list(le) if le else None},
        )

    out = [_order_row(o) for o in orders]
    out = _filter_symbol(out, symbol)
    out = _filter_magic(out, magic)
    out.sort(key=lambda x: (x.get("time_done_msc") or 0, x.get("time_done") or x.get("time_setup") or ""))

    return {"ok": True, "bridge_instance_id": bridge_instance_id(), "count": len(out), "data": out}


@router.get("/history")
def history(
    days: int = Query(7, ge=1, le=365),
    symbol: Optional[str] = Query(default=None),
    magic: Optional[int] = Query(default=None),
    _: None = Depends(require_api_key),
):
    deals = deals_history(days=days, symbol=symbol, magic=magic, _=None)
    orders = orders_history(days=days, symbol=symbol, magic=magic, _=None)
    return {
        "ok": True,
        "bridge_instance_id": bridge_instance_id(),
        "orders": orders["data"],
        "deals": deals["data"],
    }


# -----------------------------------------------------------------------------
# NOVO: Deals “since” para ledger (cursor-based)
# -----------------------------------------------------------------------------
@router.get("/deals_since")
def deals_since(
    since: Optional[str] = Query(default=None, description="ISO-8601 UTC (ex: 2025-01-01T00:00:00Z)"),
    since_msc: Optional[int] = Query(default=None, description="cursor opcional (epoch ms) - preferível quando disponível"),
    symbol: Optional[str] = Query(default=None),
    magic: Optional[int] = Query(default=None),
    limit: int = Query(2000, ge=1, le=5000),
    _: None = Depends(require_api_key),
):
    if since_msc is not None and since_msc < 0:
        raise HTTPException(status_code=400, detail="'since_msc' must be >= 0")

    # Webrequest mode: filter from EA-pushed store
    if _is_webrequest_mode():
        out = _deals_from_store(symbol, magic)
        if since_msc is not None:
            out = [x for x in out if (x.get("time_msc") or 0) > int(since_msc)]
        out.sort(key=lambda x: (x.get("time_msc") or 0, x.get("time") or ""))
        if len(out) > limit:
            out = out[:limit]
        next_cursor = max((x.get("time_msc") or 0) for x in out) if out else None
        return {"ok": True, "bridge_instance_id": bridge_instance_id(),
                "mode": "webrequest", "count": len(out),
                "next_since_msc": next_cursor, "data": out}

    svc = get_service()
    svc.ensure_up()

    if since_msc is not None:
        since_dt = datetime.fromtimestamp(int(since_msc) / 1000.0, tz=timezone.utc)
        start = since_dt - timedelta(days=2)  # janela de segurança
        end = _now_utc()
    else:
        since_dt = _parse_since(since)
        start, end = _range_from_since_or_days(since_dt, days=7)

    try:
        deals = svc.history_deals_get(start, end) or []
    except Exception as e:
        le = _native_last_error()
        raise HTTPException(
            status_code=500,
            detail={"error": "history_deals_get failed", "exc": str(e), "last_error": list(le) if le else None},
        )

    out = [_deal_row(d) for d in deals]
    out = _filter_symbol(out, symbol)
    out = _filter_magic(out, magic)

    if since_msc is not None:
        out = [x for x in out if (x.get("time_msc") or 0) > int(since_msc)]

    out.sort(key=lambda x: (x.get("time_msc") or 0, x.get("time") or ""))

    if len(out) > limit:
        out = out[:limit]

    next_cursor = None
    if out:
        next_cursor = max((x.get("time_msc") or 0) for x in out) or None

    return {
        "ok": True,
        "bridge_instance_id": bridge_instance_id(),
        "count": len(out),
        "next_since_msc": next_cursor,
        "data": out,
    }