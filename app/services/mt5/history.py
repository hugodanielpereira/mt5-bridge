# app/services/mt5/history.py
from __future__ import annotations
from typing import Any, Dict, List
import pandas as pd
import MetaTrader5 as MT5
from .session import MT5Session
from .utils import nt_to_dict

class History:
    def __init__(self, session: MT5Session) -> None:
        self.s = session

    def history_deals_get(self, frm, to):
        self.s.ensure_up()
        deals = MT5.history_deals_get(frm, to)
        return deals or []

    def orders_recent(self, days: int = 1) -> List[Dict[str, Any]]:
        self.s.ensure_up()
        end = pd.Timestamp.utcnow().to_pydatetime()
        start = (pd.Timestamp.utcnow() - pd.Timedelta(days=days)).to_pydatetime()
        orders = MT5.history_orders_get(start, end) or []
        out: List[Dict[str, Any]] = []
        for o in orders:
            dd = nt_to_dict(o)
            for k in ("time_setup", "time_done"):
                v = dd.get(k)
                if isinstance(v, (int, float)):
                    dd[k] = pd.to_datetime(v, unit="s", utc=True).isoformat()
            out.append({
                "time_setup": dd.get("time_setup"),
                "time_done": dd.get("time_done"),
                "symbol": dd.get("symbol"),
                "type": dd.get("type"),
                "price_open": dd.get("price_open"),
                "volume_initial": dd.get("volume_initial"),
                "volume_current": dd.get("volume_current"),
                "magic": dd.get("magic"),
                "comment": dd.get("comment"),
                "order": dd.get("order"),
            })
        return sorted(out, key=lambda x: (x["time_done"] or x["time_setup"] or ""))

    def deals_recent(self, days: int = 1) -> List[Dict[str, Any]]:
        self.s.ensure_up()
        end = pd.Timestamp.utcnow().to_pydatetime()
        start = (pd.Timestamp.utcnow() - pd.Timedelta(days=days)).to_pydatetime()
        deals = MT5.history_deals_get(start, end) or []
        out: List[Dict[str, Any]] = []
        for d in deals:
            dd = nt_to_dict(d)
            t = dd.get("time")
            if isinstance(t, (int, float)):
                t = pd.to_datetime(t, unit="s", utc=True).isoformat()
            out.append({
                "time": t,
                "symbol": dd.get("symbol"),
                "type": dd.get("type"),
                "entry": dd.get("entry"),
                "volume": dd.get("volume"),
                "price": dd.get("price"),
                "profit": dd.get("profit"),
                "magic": dd.get("magic"),
                "comment": dd.get("comment"),
                "order": dd.get("order"),
                "deal": dd.get("deal"),
            })
        return sorted(out, key=lambda x: x["time"] or "")