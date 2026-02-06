# app/controllers/ea_controller.py
from __future__ import annotations

import os
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Any, Dict

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