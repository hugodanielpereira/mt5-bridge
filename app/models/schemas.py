# app/models/schemas.py
from __future__ import annotations
from typing import Optional, List, Literal, Dict, Any
from pydantic import BaseModel, Field

# Úteis para validação de inputs onde fizer sentido
Timeframe = Literal["M1", "M5", "M15", "M30", "H1", "H2", "H3", "H4", "H6", "H12", "D1"]

class Ping(BaseModel):
    ok: bool
    ts: str

class SymbolInfo(BaseModel):
    name: str
    path: Optional[str] = None
    trade_mode: Optional[int] = None

class OHLCVBar(BaseModel):
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    symbol: Optional[str] = None
    timeframe: Optional[Timeframe] = None

class Position(BaseModel):
    ticket: int
    symbol: str
    type: int = Field(..., description="0=BUY, 1=SELL")
    volume: float
    price_open: float
    price_current: float
    sl: Optional[float] = None
    tp: Optional[float] = None
    magic: Optional[int] = None
    comment: Optional[str] = None

class TradeResult(BaseModel):
    retcode: int
    deal: Optional[int] = None
    order: Optional[int] = None
    volume: Optional[float] = None
    price: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    comment: Optional[str] = None
    request_id: Optional[int] = None
    retcode_external: Optional[int] = None
    request: Optional[Dict[str, Any]] = None

class AccountInfo(BaseModel):
    login: Optional[int] = None
    trade_mode: Optional[int] = None
    leverage: Optional[int] = None
    limit_orders: Optional[int] = None
    margin_so_mode: Optional[int] = None
    trade_allowed: Optional[bool] = None
    trade_expert: Optional[bool] = None
    margin_mode: Optional[int] = None
    currency_digits: Optional[int] = None
    fifo_close: Optional[bool] = None
    balance: Optional[float] = None
    credit: Optional[float] = None
    profit: Optional[float] = None
    equity: Optional[float] = None
    margin: Optional[float] = None
    margin_free: Optional[float] = None
    margin_level: Optional[float] = None
    margin_so_call: Optional[float] = None
    margin_so_so: Optional[float] = None
    margin_initial: Optional[float] = None
    margin_maintenance: Optional[float] = None
    assets: Optional[float] = None
    liabilities: Optional[float] = None
    commission_blocked: Optional[float] = None
    name: Optional[str] = None
    server: Optional[str] = None
    currency: Optional[str] = None
    company: Optional[str] = None

class Diag(BaseModel):
    terminal_info: Optional[Dict[str, Any]] = None
    account_info: Optional[AccountInfo] = None
    last_error: Optional[List[Any]] = None