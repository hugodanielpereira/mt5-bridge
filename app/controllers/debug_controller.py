from fastapi import APIRouter, Depends, Header, HTTPException
from typing import Optional, List, Dict, Any
import MetaTrader5 as mt5
from app.services.mt5 import MT5Service

router = APIRouter(tags=["debug"])

def _require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    import os
    expect = os.getenv("X_API_KEY") or os.getenv("BRIDGE_API_KEY") or os.getenv("API_KEY")
    if expect and (not x_api_key or x_api_key != expect):
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")

@router.post("/trade_check")
def trade_check(payload: dict, _: None = Depends(_require_api_key)):
    svc = MT5Service()
    svc.ensure_up()

    symbol = payload.get("symbol")
    side   = (payload.get("side") or "").lower()
    volume = float(payload.get("volume") or 0)
    if not symbol or side not in ("buy","sell") or volume <= 0:
        raise HTTPException(status_code=400, detail="need symbol, side in {buy|sell}, volume>0")

    from app.services.mt5.utils import sanitize_comment
    from app.services.mt5.utils import nt_to_dict as _nt_to_dict

    # price / type
    svc.ensure_up()
    from app.services.mt5.utils import ensure_symbol
    ensure_symbol(symbol)
    tick = mt5.symbol_info_tick(symbol)
    price = float(tick.ask if side=="buy" else tick.bid)
    otype = mt5.ORDER_TYPE_BUY if side=="buy" else mt5.ORDER_TYPE_SELL

    # candidates
    info = mt5.symbol_info(symbol)
    cands = []
    if hasattr(info, "filling_mode"):
        cands.append(int(info.filling_mode))
    for fm in (getattr(mt5,"ORDER_FILLING_FOK",None),
               getattr(mt5,"ORDER_FILLING_IOC",None),
               getattr(mt5,"ORDER_FILLING_RETURN",None)):
        if fm is not None and int(fm) not in cands:
            cands.append(int(fm))

    base_req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": otype,
        "price": price,
        "type_time": mt5.ORDER_TIME_GTC,
        "magic": int(payload.get("magic", 2025)),
        "deviation": int(payload.get("deviation", 100)),
        "comment": sanitize_comment(payload.get("comment","smoke")),
    }

    results = []
    for fm in cands or [None]:
        req = dict(base_req)
        if fm is not None:
            req["type_filling"] = fm
        chk = mt5.order_check(req)
        res = _nt_to_dict(chk) if chk else None
        code, msg = mt5.last_error()
        results.append({"filling": fm, "check": res, "last_error": [code, msg]})
    return {"candidates": cands, "checks": results}