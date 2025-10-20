# app/services/mt5/marketdata.py
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

        # <<< RESOLVE AQUI >>>
        symbol_mt5 = self.s.resolve_symbol(symbol)
        self.s.ensure_symbol(symbol_mt5)

        tf_enum = TF_MAP[tf]
        if start or end:
            if not (start and end):
                raise RuntimeError("provide both 'start' and 'end'")
            t0 = pd.Timestamp(start, tz="UTC").to_pydatetime()
            t1 = pd.Timestamp(end, tz="UTC").to_pydatetime()
            rates = MT5.copy_rates_range(symbol_mt5, tf_enum, t0, t1)
        else:
            rates = MT5.copy_rates_from_pos(symbol_mt5, tf_enum, 0, int(limit))

        if rates is None or len(rates) == 0:
            return []
        df = pd.DataFrame(rates)
        if "time" in df.columns:
            df["timestamp"] = pd.to_datetime(df["time"], unit="s", utc=True)
        if "tick_volume" in df.columns:
            df.rename(columns={"tick_volume": "volume"}, inplace=True)

        cols = [c for c in ["timestamp", "open", "high", "low", "close", "volume"] if c in df.columns]
        df = df[cols].drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
        # devolve com o símbolo ORIGINAL pedido e tf original, para consistência do bridge
        df = df.assign(symbol=symbol, timeframe=tf)
        return df.to_dict(orient="records")

    def symbol_info(self, symbol: str):
        self.s.ensure_up()
        symbol_mt5 = self.s.resolve_symbol(symbol)
        self.s.ensure_symbol(symbol_mt5)

        inf = MT5.symbol_info(symbol_mt5)
        if inf is None:
            raise RuntimeError("symbol_info returned None")
        d = nt_to_dict(inf)

        return {
            "symbol": d.get("name") or symbol_mt5,
            "path": d.get("path"),
            "trade_mode": d.get("trade_mode"),
            "digits": d.get("digits"),
            "point": d.get("point"),
            "trade_contract_size": d.get("trade_contract_size") or d.get("contract_size"),
            "trade_tick_value": d.get("trade_tick_value") or d.get("tick_value"),
            "trade_tick_size": d.get("trade_tick_size") or d.get("tick_size"),
            "margin_initial": d.get("margin_initial"),
            "margin_maintenance": d.get("margin_maintenance"),
            "volume_min": d.get("volume_min"),
            "volume_max": d.get("volume_max"),
            "volume_step": d.get("volume_step"),
            "spreads": {
                "spread": d.get("spread"),
                "spread_float": d.get("spread_float"),
            },
            "session_deals": d.get("session_deals"),
            "session_buy_orders": d.get("session_buy_orders"),
            "session_sell_orders": d.get("session_sell_orders"),
            # também devolvemos o símbolo pedido, para UI
            "requested_symbol": symbol,
        }

    def quote(self, symbol: str):
        self.s.ensure_up()
        symbol_mt5 = self.s.resolve_symbol(symbol)
        self.s.ensure_symbol(symbol_mt5)

        t = MT5.symbol_info_tick(symbol_mt5)
        if t is None:
            code, msg = MT5.last_error()
            raise RuntimeError(f"symbol_info_tick None ({code},{msg})")
        td = nt_to_dict(t)

        inf = MT5.symbol_info(symbol_mt5)
        meta = nt_to_dict(inf) if inf else {}

        return {
            "symbol": symbol,  # devolve o pedido
            "time": td.get("time"),
            "bid": float(td.get("bid", 0) or 0),
            "ask": float(td.get("ask", 0) or 0),
            "last": float(td.get("last", 0) or 0),
            "volume": td.get("volume"),
            "flags": td.get("flags"),
            "point": meta.get("point"),
            "digits": meta.get("digits"),
        }