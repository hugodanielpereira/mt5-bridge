from __future__ import annotations
from typing import Any, Dict, List, Optional, Union

from .session import MT5Session
from .orders import Orders
from .marketdata import MarketData
from .history import History
from .diag import Diag

class MT5Service:
    """
    Fachada compatível com a versão antiga:
    mantém os mesmos métodos que os controllers já chamam.
    """
    def __init__(self) -> None:
        self.session = MT5Session()
        self.orders = Orders(self.session)
        self.market = MarketData(self.session)
        self.history = History(self.session)
        self._diag = Diag(self.session)

    # lifecycle
    def initialize(self) -> bool: return self.session.initialize()
    def ensure_up(self) -> None:   return self.session.ensure_up()
    def _ensure_symbol(self, symbol: str) -> None: return self.session.ensure_symbol(symbol)

    # diag
    def diag(self) -> Dict[str, Any]: return self._diag.diag()

    # marketdata
    def account_info(self) -> Dict[str, Any]: return self.market.account_info()
    def list_symbols(self) -> List[Dict[str, Any]]: return self.market.list_symbols()
    def positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]: return self.market.positions(symbol)
    def ohlcv(self, symbol: str, tf: str = "H1",
              start: Optional[str] = None, end: Optional[str] = None, limit: int = 1000) -> List[Dict[str, Any]]:
        return self.market.ohlcv(symbol, tf, start, end, limit)

    # orders
    def order_market(self, *args, **kwargs) -> Dict[str, Any]: return self.orders.order_market(*args, **kwargs)
    def close_symbol(self, symbol: str): return self.orders.close_symbol(symbol)
    def close_ticket(self, ticket: int): return self.orders.close_ticket(ticket)

    # history
    def history_deals_get(self, frm, to): return self.history.history_deals_get(frm, to)
    def deals_recent(self, days: int = 1): return self.history.deals_recent(days)
    def orders_recent(self, days: int = 1): return self.history.orders_recent(days)