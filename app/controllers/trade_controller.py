# app/controllers/trade_controller.py
from __future__ import annotations

from typing import Optional
import os

from fastapi import APIRouter, Header, Query, Depends, HTTPException
# add at top
import traceback
import MetaTrader5 as mt5

from app.shareweb import require_api_key
from app.services.mt5 import MT5Service
from app.models.market_order import MarketOrder

router = APIRouter(tags=["trade"])

# Light auth (same pattern as other controllers)
def _require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    expected = os.getenv("X_API_KEY") or os.getenv("BRIDGE_API_KEY") or os.getenv("API_KEY")
    if expected and (not x_api_key or x_api_key != expected):
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")

@router.get("/positions")
def positions(symbol: Optional[str] = Query(None), x_api_key: Optional[str] = Header(None)):
    require_api_key(x_api_key)
    svc = MT5Service()
    svc.ensure_up()
    return svc.positions(symbol)

@router.post("/order_market")
def order_market(payload: dict, _: None = Depends(_require_api_key)):
    try:
        svc = MT5Service()
        res = svc.order_market(payload)
        return res
    except HTTPException as e:
        # re-raise clean 4xx with detail already prepared by service
        raise
    except Exception as e:
        code, msg = mt5.last_error()
        raise HTTPException(
            status_code=400,
            detail={
                "error": "order_market crashed",
                "exc": str(e),
                "last_error": [code, msg],
                "trace": traceback.format_exc().splitlines()[-8:],  # tail
            },
        )

@router.post("/close_symbol")
def close_symbol(symbol: str, x_api_key: Optional[str] = Header(None)):
    require_api_key(x_api_key)
    svc = MT5Service()
    svc.ensure_up()
    return svc.close_symbol(symbol)

@router.post("/close_ticket")
def close_ticket(ticket: int, x_api_key: Optional[str] = Header(None)):
    require_api_key(x_api_key)
    svc = MT5Service()
    svc.ensure_up()
    return svc.close_ticket(ticket)

@router.get("/deals_recent")
def deals_recent(days: int = Query(1, ge=0, le=30), x_api_key: Optional[str] = Header(None)):
    require_api_key(x_api_key)
    svc = MT5Service()
    svc.ensure_up()
    return svc.deals_recent(days=days)