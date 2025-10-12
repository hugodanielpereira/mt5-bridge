from pydantic import BaseModel
from typing import Optional

class MarketOrder(BaseModel):
    symbol: str
    side: str
    volume: float
    magic: int = 2025
    sl: Optional[float] = None
    tp: Optional[float] = None
    comment: Optional[str] = "mt5-bridge"
    position: Optional[int] = None