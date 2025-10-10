from typing import Optional
from fastapi import APIRouter, Header, HTTPException, Query

from app.services.auth_service import require_api_key
from app.services.mt5 import MT5Service

router = APIRouter(tags=["data"])

@router.get("/symbols")
def symbols(x_api_key: Optional[str] = Header(None)):
    require_api_key(x_api_key)
    svc = MT5Service()
    svc.ensure_up()
    return svc.list_symbols()

@router.get("/ohlcv")
def ohlcv(
    symbol: str,
    tf: str = Query("H1"),
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: int = 1000,
    x_api_key: Optional[str] = Header(None),
):
    require_api_key(x_api_key)
    svc = MT5Service()
    svc.ensure_up()
    return svc.ohlcv(symbol=symbol, tf=tf, start=start, end=end, limit=limit)