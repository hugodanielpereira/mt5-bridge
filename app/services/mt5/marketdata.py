from __future__ import annotations
from typing import Any, Dict, List, Optional
import pandas as pd

try:
    import MetaTrader5 as MT5
except Exception as e:
    raise RuntimeError(f"MetaTrader5 import failed: {e!r}")

from .session import MT5Session
from .utils import TF_MAP, nt_to_dict

class MarketData:
    def __init__(self, session: MT5Session) -> None:
        self.s = session

    def account_info(self) -> Dict[str, Any]:
        self.s.ensure_up()
        info = MT5.account_info()
        if info is None:
            raise RuntimeError("no account info (not logged in?)")
        d = nt_to_dict(info)
        for k, v in list(d.items()):
            if isinstance(v, pd.Timestamp):
                d[k] = str(v)
        return d

    def list_symbols(self) -> List[Dict[str, Any]]:
        self.s.ensure_up()
        infos = MT5.symbols_get() or []
        out: List[Dict[str, Any]] = []
        for inf in infos:
            d = nt_to_dict(inf)
            if d.get("visible"):
                out.append({"name": d.get("name"), "path": d.get("path"), "trade_mode": d.get("trade_mode")})
        return sorted(out, key=lambda x: x["name"] or "")

    def positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        self.s.ensure_up()
        pos = MT5.positions_get(symbol=symbol) if symbol else MT5.positions_get()
        out: List[Dict[str, Any]] = []
        for p in (pos or []):
            d = nt_to_dict(p)
            out.append({
                "ticket": d.get("ticket"),
                "symbol": d.get("symbol"),
                "type": d.get("type"),
                "volume": d.get("volume"),
                "price_open": d.get("price_open"),
                "price_current": d.get("price_current"),
                "sl": d.get("sl"),
                "tp": d.get("tp"),
                "magic": d.get("magic"),
                "comment": d.get("comment"),
            })
        return out

    def ohlcv(self, symbol: str, tf: str = "H1",
              start: Optional[str] = None, end: Optional[str] = None, limit: int = 1000) -> List[Dict[str, Any]]:
        self.s.ensure_up()
        tf = (tf or "H1").upper()
        if tf not in TF_MAP:
            raise RuntimeError(f"invalid tf '{tf}' (use one of {list(TF_MAP)})")
        self.s.ensure_symbol(symbol)
        tf_enum = TF_MAP[tf]
        if start or end:
            if not (start and end):
                raise RuntimeError("provide both 'start' and 'end'")
            t0 = pd.Timestamp(start, tz="UTC").to_pydatetime()
            t1 = pd.Timestamp(end, tz="UTC").to_pydatetime()
            rates = MT5.copy_rates_range(symbol, tf_enum, t0, t1)
        else:
            rates = MT5.copy_rates_from_pos(symbol, tf_enum, 0, int(limit))
        if rates is None or len(rates) == 0:
            return []
        df = pd.DataFrame(rates)
        if "time" in df.columns:
            df["timestamp"] = pd.to_datetime(df["time"], unit="s", utc=True)
        if "tick_volume" in df.columns:
            df.rename(columns={"tick_volume": "volume"}, inplace=True)
        cols = [c for c in ["timestamp", "open", "high", "low", "close", "volume"] if c in df.columns]
        df = df[cols].drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
        df = df.assign(symbol=symbol, timeframe=tf)
        return df.to_dict(orient="records")