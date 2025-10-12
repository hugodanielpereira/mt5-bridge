from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Depends
import logging

from app.shareweb import require_api_key, get_service

router = APIRouter(tags=["data"])
log = logging.getLogger("bridge")

@router.get("/symbols")
def symbols(_=Depends(require_api_key)):
    try:
        svc = get_service()
        svc.ensure_up()
        return svc.list_symbols()
    except Exception as e:
        log.exception("symbols failed")
        raise HTTPException(status_code=500, detail={"error": "symbols crashed", "exc": str(e)})

@router.get("/ohlcv")
def ohlcv(
    symbol: str,
    tf: str = Query("H1"),
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: int = 1000,
    _=Depends(require_api_key),
):
    try:
        svc = get_service()
        svc.ensure_up()
        return svc.ohlcv(symbol=symbol, tf=tf, start=start, end=end, limit=limit)
    except Exception as e:
        log.exception("ohlcv failed")
        raise HTTPException(status_code=500, detail={"error": "ohlcv crashed", "exc": str(e)})

@router.get("/symbol_info")
def symbol_info(symbol: str = Query(..., min_length=1), _=Depends(require_api_key)):
    try:
        svc = get_service()
        return svc.symbol_info(symbol)
    except Exception as e:
        code, msg = (None, None)
        try:
            import MetaTrader5 as MT5  # type: ignore
            code, msg = MT5.last_error()
        except Exception:
            pass
        log.exception("symbol_info failed")
        raise HTTPException(status_code=500, detail={
            "error": "symbol_info crashed",
            "exc": str(e),
            "last_error": [code, msg],
        })

@router.get("/quote")
def quote(symbol: str = Query(..., min_length=1), _=Depends(require_api_key)):
    try:
        svc = get_service()
        return svc.quote(symbol)
    except Exception as e:
        code, msg = (None, None)
        try:
            import MetaTrader5 as MT5  # type: ignore
            code, msg = MT5.last_error()
        except Exception:
            pass
        log.exception("quote failed")
        raise HTTPException(status_code=500, detail={
            "error": "quote crashed",
            "exc": str(e),
            "last_error": [code, msg],
        })