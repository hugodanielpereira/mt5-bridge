# app/controllers/trade_controller.py
from __future__ import annotations

from typing import Optional, Any, Tuple, Dict
import os
import traceback
import time

from fastapi import APIRouter, Query, Depends, HTTPException

from app.common.auth import require_api_key
from app.services.mt5 import MT5Service
from app.services.mt5.state_store import STORE
from app.services.mt5.command_store import CommandStore
from app.services.events.publisher import EventsPublisher

router = APIRouter(tags=["trade"])

COMMANDS = CommandStore.from_env()
PUBLISHER = EventsPublisher()


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _now_ms() -> int:
    return int(time.time() * 1000)


def _default_scope() -> str:
    return (
        (os.getenv("IID") or "").strip()
        or (os.getenv("INSTANCE_ID") or "").strip()
        or (os.getenv("BRIDGE_ID") or "").strip()
        or "default"
    )


def _norm_scope(scope: Optional[str]) -> str:
    sc = (scope or "").strip()
    return sc or _default_scope()



def _is_webrequest_mode(svc: MT5Service | None = None) -> bool:
    try:
        if svc is not None and hasattr(svc, "webrequest_mode"):
            return bool(getattr(svc, "webrequest_mode"))
    except Exception:
        pass
    mode = (os.getenv("MT5_MODE", "webrequest") or "webrequest").strip().lower()
    return mode in ("webrequest", "http", "ea", "bridge")


def _native_last_error() -> Optional[Tuple[Any, Any]]:
    try:
        mode = (os.getenv("MT5_MODE", "webrequest") or "webrequest").strip().lower()
        if mode in ("webrequest", "http", "ea", "bridge"):
            return None
        import MetaTrader5 as mt5  # type: ignore
        code, msg = mt5.last_error()
        return (code, msg)
    except Exception:
        return None


def _ea_meta() -> Dict[str, Any]:
    st = STORE.snapshot()
    ttl = int(os.getenv("EA_TTL_MS", "5000"))
    return {
        "ea_connected": st.is_ea_connected(ttl_ms=ttl),
        "last_heartbeat_ms": st.last_heartbeat_ms,
        "bridge_instance_id": getattr(st, "bridge_instance_id", None),
    }


def _badreq(msg: str) -> HTTPException:
    return HTTPException(status_code=400, detail=msg)


def _require_fields(payload: dict, fields: list[str]) -> None:
    for f in fields:
        if payload.get(f, None) is None:
            raise _badreq(f"missing field '{f}'")


def _norm_side(v: Any) -> str:
    s = str(v or "").strip().upper()
    if s in ("BUY", "LONG"):
        return "BUY"
    if s in ("SELL", "SHORT"):
        return "SELL"
    raise _badreq("field 'side' must be BUY/SELL")


def _norm_symbol(v: Any) -> str:
    s = str(v or "").strip().upper()
    if not s:
        raise _badreq("field 'symbol' is required")
    return s


def _norm_float(v: Any, field: str) -> float:
    try:
        return float(v)
    except Exception:
        raise _badreq(f"field '{field}' must be a number")


def _norm_int(v: Any, field: str) -> int:
    try:
        return int(v)
    except Exception:
        raise _badreq(f"field '{field}' must be int")


def _enqueue_cmd(cmd: Dict[str, Any], *, scope: Optional[str] = None) -> Dict[str, Any]:
    """
    Enfileira no Redis (CommandStore) e devolve cmd_id imediatamente.
    """
    sc = _norm_scope(scope)

    c = dict(cmd or {})
    c.setdefault("scope", sc)
    c.setdefault("ts_ms", _now_ms())

    meta = COMMANDS.enqueue(c, scope=str(c.get("scope") or sc))

    # --- publish stream event (best-effort) ---
    try:
        iid = (os.getenv("IID") or os.getenv("BRIDGE_ID") or "unknown").strip()
        PUBLISHER.publish(
            iid=iid,
            scope=str(c.get("scope") or sc),
            event_type="cmd.queued",
            payload={
                "id": meta.id,
                "type": meta.type,
                "status": meta.status,
                "scope": meta.scope,
                "ts_ms": meta.ts_ms,
                "cmd": c,
            },
        )
    except Exception:
        pass

    out: Dict[str, Any] = {
        "ok": True,
        "mode": "webrequest",
        "queued": True,
        "cmd_id": meta.id,
        "cmd_meta": {
            "id": meta.id,
            "type": meta.type,
            "status": meta.status,
            "ts_ms": meta.ts_ms,
            "scope": meta.scope,
        },
        "cmd": c,
        **_ea_meta(),
    }
    if not out["ea_connected"]:
        out["warning"] = "ea_not_connected_yet (command queued anyway)"
    return out


# ====================== ACCOUNT / INFO ======================

@router.get("/account")
@router.get("/account_info")
def account_info(_: None = Depends(require_api_key)):
    svc = MT5Service()

    if _is_webrequest_mode(svc):
        st = STORE.snapshot()
        return {
            "ok": True,
            "mode": "webrequest",
            "ea_connected": st.is_ea_connected(ttl_ms=int(os.getenv("EA_TTL_MS", "5000"))),
            "last_heartbeat_ms": st.last_heartbeat_ms,
            **(st.account or {}),
        }

    svc.ensure_up()
    return svc.account_info()


# ====================== POSITIONS ===========================

@router.get("/positions")
def positions(symbol: Optional[str] = Query(None), _: None = Depends(require_api_key)):
    svc = MT5Service()

    if _is_webrequest_mode(svc):
        st = STORE.snapshot()
        items = st.positions or []
        if symbol:
            sym = symbol.upper().strip()
            items = [p for p in items if str(p.get("symbol", "")).upper() == sym]
        return {
            "ok": True,
            "mode": "webrequest",
            "positions": items,
            **_ea_meta(),
        }

    svc.ensure_up()
    return svc.positions(symbol)


# ====================== MARKET ORDERS =======================

@router.post("/order_market")
def order_market(payload: dict, _: None = Depends(require_api_key)):
    svc = MT5Service()

    if _is_webrequest_mode(svc):
        _require_fields(payload, ["symbol", "side", "volume"])

        sym = _norm_symbol(payload.get("symbol"))
        side = _norm_side(payload.get("side"))
        vol = _norm_float(payload.get("volume"), "volume")
        if vol <= 0:
            raise _badreq("field 'volume' must be > 0")

        sl = payload.get("sl", None)
        tp = payload.get("tp", None)
        sl_f = None if sl is None else _norm_float(sl, "sl")
        tp_f = None if tp is None else _norm_float(tp, "tp")

        cmd: Dict[str, Any] = {
            "type": "order_market",
            "symbol": sym,
            "side": side,
            "volume": vol,
        }
        if sl_f is not None:
            cmd["sl"] = sl_f
        if tp_f is not None:
            cmd["tp"] = tp_f

        for k in ("magic", "comment", "deviation", "filling", "time_type", "scope"):
            if payload.get(k) is not None:
                cmd[k] = payload.get(k)

        return _enqueue_cmd(cmd, scope=str(cmd.get("scope") or _default_scope()))

    # native
    try:
        svc.ensure_up()
        return svc.order_market(payload)
    except HTTPException:
        raise
    except Exception as e:
        le = _native_last_error()
        raise HTTPException(
            status_code=500,
            detail={
                "error": "order_market crashed",
                "exc": str(e),
                "last_error": list(le) if le else None,
                "trace": traceback.format_exc().splitlines()[-12:],
            },
        )


@router.post("/order_modify")
def order_modify(payload: dict, _: None = Depends(require_api_key)):
    svc = MT5Service()

    ticket = payload.get("ticket", None)
    if ticket is None:
        ticket = payload.get("position", None)
    if ticket is None:
        raise _badreq("field 'ticket' is required (or 'position' alias)")

    ticket_i = _norm_int(ticket, "ticket")
    sl = payload.get("sl", None)
    tp = payload.get("tp", None)
    if sl is None and tp is None:
        raise _badreq("at least one of 'sl' or 'tp' must be provided")

    if _is_webrequest_mode(svc):
        cmd: Dict[str, Any] = {"type": "order_modify", "ticket": ticket_i}

        if sl is not None:
            cmd["sl"] = _norm_float(sl, "sl")
        if tp is not None:
            cmd["tp"] = _norm_float(tp, "tp")

        for k in ("symbol", "comment", "magic", "scope"):
            if payload.get(k) is not None:
                cmd[k] = payload.get(k)

        return _enqueue_cmd(cmd, scope=str(cmd.get("scope") or _default_scope()))

    # native
    try:
        svc.ensure_up()
        return svc.modify_position(ticket=ticket_i, sl=sl, tp=tp)
    except HTTPException:
        raise
    except Exception as e:
        le = _native_last_error()
        raise HTTPException(
            status_code=500,
            detail={
                "error": "order_modify crashed",
                "exc": str(e),
                "last_error": list(le) if le else None,
                "trace": traceback.format_exc().splitlines()[-12:],
            },
        )


@router.post("/order_close")
def order_close(payload: dict, _: None = Depends(require_api_key)):
    svc = MT5Service()

    ticket = payload.get("ticket", None)
    if ticket is None:
        raise _badreq("field 'ticket' is required")
    ticket_i = _norm_int(ticket, "ticket")

    volume = payload.get("volume", None)
    volume_f: Optional[float] = None
    if volume is not None:
        volume_f = _norm_float(volume, "volume")
        if volume_f <= 0:
            raise _badreq("field 'volume' must be > 0 if provided")

    if _is_webrequest_mode(svc):
        cmd: Dict[str, Any] = {"type": "order_close", "ticket": ticket_i}
        if volume_f is not None:
            cmd["volume"] = volume_f

        for k in ("symbol", "comment", "magic", "scope"):
            if payload.get(k) is not None:
                cmd[k] = payload.get(k)

        return _enqueue_cmd(cmd, scope=str(cmd.get("scope") or _default_scope()))

    # native
    try:
        svc.ensure_up()
        return svc.close_ticket(ticket_i, volume_f)
    except HTTPException:
        raise
    except Exception as e:
        le = _native_last_error()
        raise HTTPException(
            status_code=500,
            detail={
                "error": "order_close crashed",
                "exc": str(e),
                "last_error": list(le) if le else None,
                "trace": traceback.format_exc().splitlines()[-12:],
            },
        )


# ====================== COMPAT (LEGACY) =====================

@router.post("/close_symbol")
def close_symbol(symbol: str, _: None = Depends(require_api_key)):
    svc = MT5Service()

    if _is_webrequest_mode(svc):
        sym = _norm_symbol(symbol)
        return _enqueue_cmd({"type": "close_symbol", "symbol": sym})

    svc.ensure_up()
    return svc.close_symbol(symbol)


@router.post("/close_ticket")
def close_ticket(ticket: int, _: None = Depends(require_api_key)):
    svc = MT5Service()

    if _is_webrequest_mode(svc):
        return _enqueue_cmd({"type": "close_ticket", "ticket": int(ticket)})

    svc.ensure_up()
    return svc.close_ticket(ticket)


# ====================== DEALS RECENT ========================

@router.get("/deals_recent")
def deals_recent(days: int = Query(1, ge=0, le=30), _: None = Depends(require_api_key)):
    svc = MT5Service()

    if _is_webrequest_mode(svc):
        # Serve from EA-pushed store (deals history is published by EA)
        from app.services.mt5.state_store import STORE
        st = STORE.snapshot()
        deals = list(st.deals_history or [])
        deals.sort(key=lambda x: (x.get("time_msc") or 0))
        return {"ok": True, "mode": "webrequest", "count": len(deals), "data": deals}

    svc.ensure_up()
    return svc.deals_recent(days=days)


# ====================== OHLCV / MARKET DATA =================

# ====================== MARKET DATA (ALIASES REQUIRED BY LIVE) =================

def _get_ohlcv_rows_webrequest(symbol: str, tf: str, limit: int) -> list:
    st = STORE.snapshot()
    sym = symbol.upper().strip()
    t = tf.upper().strip()
    key = f"{sym}:{t}"
    rows = (st.ohlcv or {}).get(key) or []
    if rows and limit:
        rows = rows[-int(limit):]
    return rows

def _ohlcv_payload(symbol: str, tf: str, rows: list) -> dict:
    return {
        "ok": True,
        "mode": "webrequest",
        "symbol": symbol.upper().strip(),
        "tf": tf.upper().strip(),
        "rows": rows,
        "count": len(rows),
        **_ea_meta(),
    }



@router.get("/bars")
def bars(
    symbol: str = Query(...),
    tf: str = Query("H1"),
    limit: int = Query(1000, ge=1, le=5000),
    _: None = Depends(require_api_key),
):
    svc = MT5Service()
    if _is_webrequest_mode(svc):
        rows = _get_ohlcv_rows_webrequest(symbol, tf, limit)
        out = _ohlcv_payload(symbol, tf, rows)
        out["bars"] = out["rows"]  # opcional
        return out

    svc.ensure_up()
    return svc.ohlcv(symbol=symbol, tf=tf, limit=limit)


@router.get("/quote")
def quote(symbol: str = Query(...), _: None = Depends(require_api_key)):
    svc = MT5Service()

    if _is_webrequest_mode(svc):
        st = STORE.snapshot()
        # Preserve exact broker casing (e.g. "SpotCrude", "NatGas").
        # Uppercasing breaks mixed-case symbols — use case-insensitive fallback instead.
        sym = symbol.strip()
        quotes = st.quotes or {}
        q = quotes.get(sym)
        if q is None:
            sym_up = sym.upper()
            for k, v in quotes.items():
                if k.upper() == sym_up:
                    q = v
                    break
        if not q:
            return {"ok": False, "mode": "webrequest", "symbol": sym, "hint": "EA ainda não publicou quote", **_ea_meta()}
        return {"ok": True, "mode": "webrequest", **q, **_ea_meta()}

    svc.ensure_up()
    return svc.quote(symbol)


@router.get("/symbol_info")
def symbol_info(symbol: str = Query(...), _: None = Depends(require_api_key)):
    svc = MT5Service()

    if _is_webrequest_mode(svc):
        st = STORE.snapshot()
        # Preserve exact broker casing — same reason as /quote above.
        sym = symbol.strip()
        sym_info = st.symbol_info or {}
        info = sym_info.get(sym)
        if info is None:
            sym_up = sym.upper()
            for k, v in sym_info.items():
                if k.upper() == sym_up:
                    info = v
                    break
        if not info:
            return {"ok": False, "mode": "webrequest", "symbol": sym, "hint": "EA ainda não publicou symbol_info", **_ea_meta()}
        return {"ok": True, "mode": "webrequest", **info, **_ea_meta()}

    svc.ensure_up()
    return svc.symbol_info(symbol)


# ====================== COMMAND STATUS / RESULTS (REDIS) =====

def _commands_get_status(cmd_id: str, *, scope: str) -> Optional[Dict[str, Any]]:
    return COMMANDS.get_command(cmd_id, scope=scope)

def _commands_get_result(cmd_id: str, *, scope: str) -> Optional[Dict[str, Any]]:
    return COMMANDS.get_result(cmd_id, scope=scope)

@router.get("/command_status/{cmd_id}")
def command_status(
    cmd_id: str,
    scope: str = Query("", description="scope override (default: IID/INSTANCE_ID)"),
    _: None = Depends(require_api_key),
):
    meta = _ea_meta()
    sc = _norm_scope(scope)

    cmd = _commands_get_status(cmd_id, scope=sc)
    if not cmd:
        return {
            "ok": False,
            "cmd_id": cmd_id,
            "scope": sc,
            "found": False,
            **meta,
            "hint": "cmd_id não encontrado no Redis (ou expirou por TTL, ou scope errado).",
        }

    return {"ok": True, "cmd_id": cmd_id, "scope": sc, "found": True, "cmd": cmd, **meta}

@router.get("/command_result/{cmd_id}")
def command_result(
    cmd_id: str,
    scope: str = Query("", description="scope override (default: IID/INSTANCE_ID)"),
    _: None = Depends(require_api_key),
):
    meta = _ea_meta()
    sc = _norm_scope(scope)

    r = _commands_get_result(cmd_id, scope=sc)
    if not r:
        return {"ok": False, "cmd_id": cmd_id, "scope": sc, "found": False, **meta}

    return {"ok": True, "cmd_id": cmd_id, "scope": sc, "found": True, "result": r, **meta}

