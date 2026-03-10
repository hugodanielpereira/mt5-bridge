# app/controllers/ea_controller.py
from __future__ import annotations

import os
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Any, Dict, List

from app.common.auth import require_api_key
from app.services.mt5.state_store import STORE
from app.services.mt5.command_store import CommandStore
from app.services.events.publisher import EventsPublisher

router = APIRouter(tags=["ea"])

COMMANDS = CommandStore.from_env()
PUBLISHER = EventsPublisher()


def _default_scope() -> str:
    return (
        (os.getenv("IID") or "").strip()
        or (os.getenv("INSTANCE_ID") or "").strip()
        or (os.getenv("BRIDGE_ID") or "").strip()
        or "default"
    )


def _norm_scope(scope: str) -> str:
    sc = (scope or "").strip()
    if not sc:
        sc = _default_scope()
    return sc


# ---------------------------------------------------------------------
# EA -> Bridge (publish / snapshots)  [MEMORY STORE]
# ---------------------------------------------------------------------

@router.post("/ea/heartbeat")
def ea_heartbeat(payload: Dict[str, Any], _: None = Depends(require_api_key)):
    STORE.update_heartbeat(payload or {})
    return {"ok": True}


@router.post("/ea/account")
def ea_account(payload: Dict[str, Any], _: None = Depends(require_api_key)):
    STORE.set_account(payload or {})
    return {"ok": True}


@router.post("/ea/positions")
def ea_positions(payload: Dict[str, Any], _: None = Depends(require_api_key)):
    if isinstance(payload, dict) and isinstance(payload.get("positions"), list):
        positions = payload["positions"]
    else:
        raise HTTPException(status_code=400, detail="expected {'positions':[...]}")

    STORE.set_positions(positions)
    return {"ok": True, "count": len(positions)}


@router.post("/ea/quote")
def ea_quote(payload: Dict[str, Any], _: None = Depends(require_api_key)):
    symbol = (payload.get("symbol") or "").strip()
    if not symbol:
        raise HTTPException(status_code=400, detail="missing symbol")
    STORE.set_quote(symbol, payload)
    return {"ok": True}


@router.post("/ea/symbol_info")
def ea_symbol_info(payload: Dict[str, Any], _: None = Depends(require_api_key)):
    symbol = (payload.get("symbol") or "").strip()
    if not symbol:
        raise HTTPException(status_code=400, detail="missing symbol")
    STORE.set_symbol_info(symbol, payload)
    return {"ok": True}


@router.post("/ea/quotes")
def ea_quotes_batch(payload: Dict[str, Any], _: None = Depends(require_api_key)):
    """Batch quote update: {"quotes":[{symbol, bid, ask, ...}, ...]}"""
    quotes = payload.get("quotes")
    if not isinstance(quotes, list):
        raise HTTPException(status_code=400, detail="expected {'quotes':[...]}")
    count = 0
    for q in quotes:
        if not isinstance(q, dict):
            continue
        sym = (q.get("symbol") or "").strip()
        if not sym:
            continue
        STORE.set_quote(sym, q)
        count += 1
    return {"ok": True, "count": count}


@router.post("/ea/symbol_info_batch")
def ea_symbol_info_batch(payload: Dict[str, Any], _: None = Depends(require_api_key)):
    """Batch symbol info: {"symbols":[{symbol, digits, ...}, ...]}"""
    symbols = payload.get("symbols")
    if not isinstance(symbols, list):
        raise HTTPException(status_code=400, detail="expected {'symbols':[...]}")
    count = 0
    for s in symbols:
        if not isinstance(s, dict):
            continue
        sym = (s.get("symbol") or "").strip()
        if not sym:
            continue
        STORE.set_symbol_info(sym, s)
        count += 1
    return {"ok": True, "count": count}


@router.post("/ea/ohlcv_batch")
def ea_ohlcv_batch(payload: Dict[str, Any], _: None = Depends(require_api_key)):
    """Batch OHLCV: {"batch":[{symbol, tf, rows:[...]}, ...]}"""
    batch = payload.get("batch")
    if not isinstance(batch, list):
        raise HTTPException(status_code=400, detail="expected {'batch':[...]}")
    count = 0
    for item in batch:
        if not isinstance(item, dict):
            continue
        sym = (item.get("symbol") or "").strip()
        tf = (item.get("tf") or item.get("timeframe") or "").upper().strip()
        rows = item.get("rows") or item.get("ohlcv") or item.get("bars")
        if not sym or not tf or not isinstance(rows, list):
            continue
        STORE.set_ohlcv(sym, tf, rows)
        count += 1
    return {"ok": True, "count": count}


@router.post("/ea/deals")
def ea_deals(payload: Dict[str, Any], _: None = Depends(require_api_key)):
    deals = payload.get("deals")
    if not isinstance(deals, list):
        raise HTTPException(status_code=400, detail="expected {'deals':[...]}")
    STORE.set_deals_history(deals)
    return {"ok": True, "count": len(deals)}


@router.post("/ea/orders_history")
def ea_orders_history(payload: Dict[str, Any], _: None = Depends(require_api_key)):
    orders = payload.get("orders")
    if not isinstance(orders, list):
        raise HTTPException(status_code=400, detail="expected {'orders':[...]}")
    STORE.set_orders_history(orders)
    return {"ok": True, "count": len(orders)}


@router.post("/ea/ohlcv")
def ea_ohlcv(payload: Dict[str, Any], _: None = Depends(require_api_key)):
    symbol = (payload.get("symbol") or "").strip()
    tf = (payload.get("tf") or payload.get("timeframe") or "").upper().strip()
    rows = payload.get("rows") or payload.get("ohlcv") or payload.get("bars")
    if not symbol or not tf or not isinstance(rows, list):
        raise HTTPException(status_code=400, detail="expected {symbol, tf, rows:[...]}")
    STORE.set_ohlcv(symbol, tf, rows)
    return {"ok": True, "count": len(rows)}


# ---------------------------------------------------------------------
# Bridge -> EA (commands)  [REDIS COMMAND STORE]
# ---------------------------------------------------------------------

@router.get("/ea/commands")
def ea_commands(
    max_n: int = Query(20, ge=1, le=200),
    scope: str = Query("", description="instance scope (default: IID/INSTANCE_ID)"),
    _: None = Depends(require_api_key),
):
    sc = _norm_scope(scope)
    cmds = COMMANDS.pop_for_ea(scope=sc, max_n=max_n)
    return {"ok": True, "scope": sc, "count": len(cmds), "commands": cmds}


@router.get("/ea/commands_count")
def ea_commands_count(
    scope: str = Query("", description="instance scope (default: IID/INSTANCE_ID)"),
    _: None = Depends(require_api_key),
):
    sc = _norm_scope(scope)
    return {"ok": True, "scope": sc, "pending": COMMANDS.pending_count(scope=sc)}


# ---------------------------------------------------------------------
# EA -> Bridge (results / acks)  [REDIS COMMAND STORE]
# ---------------------------------------------------------------------

@router.post("/ea/result")
def ea_result(
    payload: Dict[str, Any],
    scope: str = Query("", description="optional scope override (fallback if payload has no scope)"),
    _: None = Depends(require_api_key),
):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="expected JSON object")

    # compat: aceitar cmd_id como alias de id
    cid = str(payload.get("id") or payload.get("cmd_id") or "").strip()
    if not cid:
        raise HTTPException(status_code=400, detail="missing field 'id' (or 'cmd_id')")

    sc = _norm_scope(str(payload.get("scope") or scope or ""))

    # força scope canonical no payload (para ficar consistente no registry)
    payload = dict(payload)
    payload["id"] = cid
    payload["scope"] = sc

    COMMANDS.set_result(cid, payload, scope=sc)

    try:
        iid = (os.getenv("IID") or os.getenv("BRIDGE_ID") or "unknown").strip()
        PUBLISHER.publish(
            iid=iid,
            scope=sc,
            event_type="cmd.result",
            payload=payload,   # já tem id/scope/retcode/ticket/msg/ts_ms/etc.
        )
    except Exception:
        pass

    return {"ok": True, "id": cid, "scope": sc}


@router.get("/ea/result")
def get_ea_result(
    id: str = Query(..., min_length=6),
    scope: str = Query("", description="instance scope (default: IID/INSTANCE_ID)"),
    _: None = Depends(require_api_key),
):
    sc = _norm_scope(scope)
    r = COMMANDS.get_result(id, scope=sc)
    if not r:
        return {"ok": False, "id": id, "scope": sc, "found": False}
    return {"ok": True, "id": id, "scope": sc, "found": True, "result": r}


# ---------------------------------------------------------------------
# Debug / Ops
# ---------------------------------------------------------------------

@router.get("/ea/command/{id}")
def get_ea_command(
    id: str,
    scope: str = Query("", description="instance scope (default: IID/INSTANCE_ID)"),
    _: None = Depends(require_api_key),
):
    """
    Debug: ver o registry do comando (status + timestamps + result embed se existir).
    """
    sc = _norm_scope(scope)
    c = COMMANDS.get_command(id, scope=sc)
    if not c:
        return {"ok": False, "id": id, "scope": sc, "found": False}
    return {"ok": True, "id": id, "scope": sc, "found": True, "cmd": c}


@router.get("/ea/commands_health")
def commands_health(_: None = Depends(require_api_key)):
    return COMMANDS.health()


@router.get("/ea/ohlcv-inventory")
def ea_ohlcv_inventory(_: None = Depends(require_api_key)):
    """
    List all OHLCV symbol:TF keys currently in the in-memory store, with bar counts.
    Useful to confirm what the EA is actually pushing (e.g. if H1 is missing the EA
    has not been configured to push that TF).
    """
    st = STORE.snapshot()
    ohlcv = st.ohlcv or {}
    by_symbol: Dict[str, Dict[str, int]] = {}
    for key, rows in ohlcv.items():
        parts = key.split(":", 1)
        sym = parts[0] if len(parts) == 2 else key
        tf  = parts[1] if len(parts) == 2 else "?"
        by_symbol.setdefault(sym, {})[tf] = len(rows)
    return {
        "ok": True,
        "total_keys": len(ohlcv),
        "by_symbol": by_symbol,
    }