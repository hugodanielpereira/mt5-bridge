# app/services/mt5/marketdata.py
from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
import os
import json

from .session import MT5Session
from .utils import TF_MAP, nt_to_dict


def _mt5_mode() -> str:
    return (os.getenv("MT5_MODE", "webrequest") or "webrequest").strip().lower()


def _is_webrequest_mode() -> bool:
    return _mt5_mode() in ("webrequest", "http", "ea", "bridge")


def _load_symbols_map() -> Dict[str, str]:
    """
    Lê SYMBOLS_MAP (yaml ou json), devolve dict {CHAVE_EM_UPPER: valor_preservado}.
    Não altera o case dos valores, porque no MT5 o case é sensível.
    """
    path = os.getenv("SYMBOLS_MAP", "").strip()
    if not path:
        return {}
    try:
        data: Dict[str, Any]
        if path.lower().endswith((".yaml", ".yml")):
            try:
                import yaml  # type: ignore
            except Exception:
                return {}
            data = yaml.safe_load(open(path, "r", encoding="utf-8")) or {}
        else:
            data = json.load(open(path, "r", encoding="utf-8"))
        out: Dict[str, str] = {}
        for k, v in (data or {}).items():
            if not k:
                continue
            out[str(k).upper()] = str(v).strip()
        return out
    except Exception:
        return {}


def _parse_iso_utc(s: str) -> datetime:
    ss = (s or "").strip()
    if not ss:
        raise ValueError("empty datetime")
    if ss.endswith("Z"):
        ss = ss[:-1] + "+00:00"
    dt = datetime.fromisoformat(ss)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


class MarketData:
    """
    Serviço de dados de mercado.

    webrequest:
      - não usa MetaTrader5 Python package

    native:
      - usa MetaTrader5 Python API
    """

    def __init__(self, session: MT5Session) -> None:
        self.s = session
        self._symmap = _load_symbols_map()
        self._mt5_mod = None  # lazy

    def _mt5(self):
        if self._mt5_mod is not None:
            return self._mt5_mod
        try:
            import MetaTrader5 as MT5  # type: ignore
        except Exception as e:
            raise RuntimeError(f"MetaTrader5 import failed (MT5_MODE=native): {e!r}")
        self._mt5_mod = MT5
        return MT5

    def _require_native(self, what: str) -> None:
        if _is_webrequest_mode():
            raise RuntimeError(
                f"{what} not supported in MT5_MODE=webrequest. "
                f"Bridge is WebRequest-first (EA talks to bridge). "
                f"Set MT5_MODE=native to use MetaTrader5 Python API."
            )

    # ----------------------------------------------------------------------
    # helpers (native)
    # ----------------------------------------------------------------------
    def _try_variants_in_terminal(self, MT5: Any, base: str) -> Optional[str]:
        infos = MT5.symbols_get() or []
        names = [nt_to_dict(x).get("name", "") for x in infos]
        names = [n for n in names if n]

        for n in names:
            if n.upper() == base.upper():
                return n

        suff_raw = os.getenv("MT5_SYMBOL_SUFFIXES", "")
        suffixes = [s.strip() for s in suff_raw.split(",") if s.strip()]
        for sfx in suffixes:
            cand = f"{base}{sfx}"
            for n in names:
                if n.upper() == cand.upper():
                    return n

        up = base.upper()
        for n in names:
            if up in n.upper():
                return n

        return None

    def _resolve_symbol_robust(self, MT5: Any, requested: str) -> str:
        req = (requested or "").strip()
        if not req:
            raise RuntimeError("símbolo vazio")

        try:
            mt5_sym = self.s.resolve_symbol(req)
            if mt5_sym and MT5.symbol_info(mt5_sym) is not None:
                return mt5_sym
        except Exception:
            pass

        alias = self._symmap.get(req.upper())
        if alias:
            found = self._try_variants_in_terminal(MT5, alias) or alias
            if MT5.symbol_info(found) is not None:
                return found

        if req.upper().endswith("USDT"):
            base_usd = req[:-4] + "USD"
            found = self._try_variants_in_terminal(MT5, base_usd) or base_usd
            if MT5.symbol_info(found) is not None:
                return found

        found = self._try_variants_in_terminal(MT5, req)
        if found and MT5.symbol_info(found) is not None:
            return found

        raise RuntimeError(
            f"symbol '{requested}' not found (configure alias em SYMBOLS_MAP "
            f"ou defina sufixos em MT5_SYMBOL_SUFFIXES)"
        )

    # ----------------------------------------------------------------------
    # public API (native-only)
    # ----------------------------------------------------------------------
    def account_info(self) -> Dict[str, Any]:
        self._require_native("account_info")
        MT5 = self._mt5()

        self.s.ensure_up()
        info = MT5.account_info()
        if info is None:
            raise RuntimeError("no account info (not logged in?)")

        d = nt_to_dict(info)

        # Normaliza datetimes se aparecerem
        for k, v in list(d.items()):
            if hasattr(v, "isoformat"):
                try:
                    d[k] = str(v)
                except Exception:
                    pass

        return d

    def list_symbols(self) -> List[Dict[str, Any]]:
        self._require_native("list_symbols")
        MT5 = self._mt5()

        self.s.ensure_up()
        infos = MT5.symbols_get() or []
        out: List[Dict[str, Any]] = []
        for inf in infos:
            d = nt_to_dict(inf)
            if d.get("visible"):
                out.append({"name": d.get("name"), "path": d.get("path"), "trade_mode": d.get("trade_mode")})
        return sorted(out, key=lambda x: x["name"] or "")

    def positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        self._require_native("positions")
        MT5 = self._mt5()

        self.s.ensure_up()

        symbol_mt5 = None
        if symbol:
            symbol_mt5 = self._resolve_symbol_robust(MT5, symbol)
            self.s.ensure_symbol(symbol_mt5)

        pos = MT5.positions_get(symbol=symbol_mt5) if symbol_mt5 else MT5.positions_get()

        sym_meta: Dict[str, Dict[str, Any]] = {}
        out: List[Dict[str, Any]] = []

        for p in (pos or []):
            d = nt_to_dict(p)
            sym = str(d.get("symbol") or "").strip()

            if sym and sym not in sym_meta:
                try:
                    inf = MT5.symbol_info(sym)
                    if inf is None:
                        sym_meta[sym] = {"volume_min": None, "volume_step": None, "volume_max": None}
                    else:
                        di = nt_to_dict(inf)
                        sym_meta[sym] = {
                            "volume_min": di.get("volume_min"),
                            "volume_step": di.get("volume_step"),
                            "volume_max": di.get("volume_max"),
                        }
                except Exception:
                    sym_meta[sym] = {"volume_min": None, "volume_step": None, "volume_max": None}

            meta = sym_meta.get(sym) or {"volume_min": None, "volume_step": None, "volume_max": None}

            out.append(
                {
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
                    "volume_min": meta.get("volume_min"),
                    "volume_step": meta.get("volume_step"),
                    "volume_max": meta.get("volume_max"),
                }
            )

        return out

    def ohlcv(
        self,
        symbol: str,
        tf: str = "H1",
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        self._require_native("ohlcv")
        MT5 = self._mt5()

        self.s.ensure_up()
        tf = (tf or "H1").upper()
        if tf not in TF_MAP:
            raise RuntimeError(f"invalid tf '{tf}' (use one of {list(TF_MAP)})")

        symbol_mt5 = self._resolve_symbol_robust(MT5, symbol)
        self.s.ensure_symbol(symbol_mt5)

        tf_enum = TF_MAP[tf]

        if start or end:
            if not (start and end):
                raise RuntimeError("provide both 'start' and 'end'")
            t0 = _parse_iso_utc(start)
            t1 = _parse_iso_utc(end)
            rates = MT5.copy_rates_range(symbol_mt5, tf_enum, t0, t1)
        else:
            rates = MT5.copy_rates_from_pos(symbol_mt5, tf_enum, 0, int(limit))

        if rates is None or len(rates) == 0:
            return []

        # MT5 devolve array/records com campos: time, open, high, low, close, tick_volume, ...
        rows: List[Dict[str, Any]] = []
        seen_ts = set()

        for r in rates:
            # r pode ser tuple-like ou dict-like; usamos index por key se existir
            try:
                t_sec = r["time"]  # type: ignore[index]
                o = r["open"]      # type: ignore[index]
                h = r["high"]      # type: ignore[index]
                l = r["low"]       # type: ignore[index]
                c = r["close"]     # type: ignore[index]
                v = r.get("tick_volume", r.get("volume", None))  # type: ignore[attr-defined]
            except Exception:
                # fallback tuple order: time, open, high, low, close, tick_volume, spread, real_volume
                t_sec = r[0]
                o, h, l, c = r[1], r[2], r[3], r[4]
                v = r[5] if len(r) > 5 else None

            ts = datetime.fromtimestamp(int(t_sec), tz=timezone.utc).isoformat()

            if ts in seen_ts:
                continue
            seen_ts.add(ts)

            rows.append(
                {
                    "timestamp": ts,
                    "open": float(o),
                    "high": float(h),
                    "low": float(l),
                    "close": float(c),
                    "volume": v,
                    "symbol": symbol,   # mantém consistência com a stack (símbolo pedido)
                    "timeframe": tf,
                }
            )

        rows.sort(key=lambda x: x["timestamp"])
        return rows

    def symbol_info(self, symbol: str) -> Dict[str, Any]:
        self._require_native("symbol_info")
        MT5 = self._mt5()

        self.s.ensure_up()
        symbol_mt5 = self._resolve_symbol_robust(MT5, symbol)
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
            "spreads": {"spread": d.get("spread"), "spread_float": d.get("spread_float")},
            "session_deals": d.get("session_deals"),
            "session_buy_orders": d.get("session_buy_orders"),
            "session_sell_orders": d.get("session_sell_orders"),
            "requested_symbol": symbol,
        }

    def quote(self, symbol: str) -> Dict[str, Any]:
        self._require_native("quote")
        MT5 = self._mt5()

        self.s.ensure_up()
        symbol_mt5 = self._resolve_symbol_robust(MT5, symbol)
        self.s.ensure_symbol(symbol_mt5)

        t = MT5.symbol_info_tick(symbol_mt5)
        if t is None:
            code, msg = MT5.last_error()
            raise RuntimeError(f"symbol_info_tick None ({code},{msg})")
        td = nt_to_dict(t)

        inf = MT5.symbol_info(symbol_mt5)
        meta = nt_to_dict(inf) if inf else {}

        return {
            "symbol": symbol,
            "time": td.get("time"),
            "bid": float(td.get("bid", 0) or 0),
            "ask": float(td.get("ask", 0) or 0),
            "last": float(td.get("last", 0) or 0),
            "volume": td.get("volume"),
            "flags": td.get("flags"),
            "point": meta.get("point"),
            "digits": meta.get("digits"),
        }