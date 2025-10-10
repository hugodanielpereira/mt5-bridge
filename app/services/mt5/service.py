# app/services/mt5/service.py
from __future__ import annotations
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---- import the CLASS-based modules you already have
from .session import MT5Session
from .marketdata import MarketData
from .trade import Trade
from .history import History
from .diag import diag as _diag  # keep it a function if you prefer

from dotenv import load_dotenv
load_dotenv()

try:
    import MetaTrader5 as MT5
except Exception as e:
    raise RuntimeError(f"MetaTrader5 import failed: {e!r}")

class MT5Service:
    """Façade simples a compor os módulos mt5/*."""

    def __init__(self) -> None:
        env = (os.getenv("ENVIRONMENT", "demo") or "demo").lower()
        suf = "_LIVE" if env == "live" else "_DEMO"

        def pick(key: str) -> Optional[str]:
            return os.getenv(f"{key}{suf}") or os.getenv(key)

        term = pick("MT5_TERMINAL_PATH")
        self.login = pick("MT5_LOGIN")
        self.password = pick("MT5_PASSWORD")
        self.server = pick("MT5_SERVER")

        if not term or not Path(term).exists():
            raise RuntimeError(f"MT5_TERMINAL_PATH inválido: {term!r}")

        self.exe_path = Path(term).resolve()
        self.connected = False

        # sessão principal
        self.session = MT5Session(
            exe_path=self.exe_path,
            login=self.login,
            password=self.password,
            server=self.server,
        )

        # módulos (1x cada)
        self.market = MarketData(self.session)
        self.trade = Trade(self.session)
        self.history = History(self.session)

    # ---- lifecycle ----------------------------------------------------------
    def initialize(self) -> bool:
        try:
            try:
                MT5.shutdown()
            except Exception:
                pass

            self.session.ensure_terminal_running()
            self.session.attach_specific()

            if self.session.login and self.session.password and self.session.server:
                if not MT5.login(int(self.session.login), password=self.session.password, server=self.session.server):
                    code, msg = MT5.last_error()
                    print(f"[MT5][ERR] login failed: {code} {msg}")
                    self.connected = False
                    return False

            print("[MT5] ligado com sucesso")
            self.connected = True
            return True
        except Exception as e:
            print(f"[MT5][EXC] initialize: {e}")
            self.connected = False
            return False

    def ensure_up(self) -> None:
        self.session.ensure_up()

    # ---- façade API (delegates) --------------------------------------------
    def account_info(self) -> Dict[str, Any]:
        return self.market.account_info()

    def list_symbols(self) -> List[Dict[str, Any]]:
        return self.market.list_symbols()

    def positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.market.positions(symbol=symbol)

    def ohlcv(self, *args, **kwargs) -> List[Dict[str, Any]]:
        return self.market.ohlcv(*args, **kwargs)

    def order_market(self, *args, **kwargs) -> Dict[str, Any]:
        return self.trade.order_market(*args, **kwargs)

    def close_symbol(self, symbol: str):
        return self.trade.close_symbol(symbol)

    def close_ticket(self, ticket: int):
        return self.trade.close_ticket(ticket)

    def diag(self) -> Dict[str, Any]:
        return _diag(self.session)

    def history_deals_get(self, frm, to):
        return self.history.history_deals_get(frm, to)

    def deals_recent(self, days: int = 1):
        return self.history.deals_recent(days)

    def orders_recent(self, days: int = 1):
        return self.history.orders_recent(days)