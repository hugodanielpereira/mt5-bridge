# app/services/mt5/trade.py
from __future__ import annotations
from typing import Any, Dict, List, Optional

try:
    import MetaTrader5 as MT5
except Exception as e:  # pragma: no cover
    raise RuntimeError(f"MetaTrader5 import failed: {e!r}")

from .session import MT5Session
from .utils import nt_to_dict, sanitize_comment
from decimal import Decimal, ROUND_FLOOR


class Trade:
    def __init__(self, session: MT5Session) -> None:
        self.s = session

    # -------- helper: normalização de volume --------
    def _normalize_volume(self, info, vol_in: float) -> float:
        """
        Ajusta volume aos limites do símbolo:
          - >= volume_min
          - múltiplo de volume_step (a partir de volume_min)
          - <= volume_max
        """
        try:
            vmin  = float(getattr(info, "volume_min", 0) or 0)
            vstep = float(getattr(info, "volume_step", 0) or 0)
            vmax  = float(getattr(info, "volume_max", 0) or 0)
        except Exception:
            vmin, vstep, vmax = 0.0, 0.0, 0.0

        v = float(vol_in)

        if vmin > 0 and v < vmin:
            v = vmin

        if vstep and vstep > 0:
            dv   = Decimal(str(v))
            dmin = Decimal(str(vmin))
            dstep= Decimal(str(vstep))
            steps = ((dv - dmin) / dstep).quantize(Decimal("1"), rounding=ROUND_FLOOR)
            dv_ok = dmin + steps * dstep
            if dv_ok < dmin:
                dv_ok = dmin
            v = float(dv_ok)

        if vmax and vmax > 0 and v > vmax:
            v = vmax

        if v <= 0:
            v = vmin if vmin > 0 else (vstep if vstep > 0 else 0.01)

        if vstep and vstep > 0:
            decs = max(0, len(str(vstep).split(".")[-1]) if "." in str(vstep) else 0)
            v = round(v, decs)
        return v

    def order_market(
        self,
        req: Optional[Dict[str, Any]] = None,
        *,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        volume: Optional[float] = None,
        magic: int = 2025,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        comment: Optional[str] = "mlsl-exec",
        position: Optional[int] = None,
    ) -> Dict[str, Any]:
        self.s.ensure_up()

        # merge pedido
        if req is not None:
            if hasattr(req, "dict"):
                data = req.dict()
            elif isinstance(req, dict):
                data = dict(req)
            else:
                raise RuntimeError("invalid request object for order_market")
        else:
            data = {
                "symbol": symbol, "side": side, "volume": volume, "magic": magic,
                "sl": sl, "tp": tp, "comment": comment, "position": position,
            }

        for key in ("symbol", "side", "volume"):
            if data.get(key) in (None, ""):
                raise RuntimeError(f"missing field '{key}'")

        sym_raw = str(data["symbol"])
        side_s  = str(data["side"]).lower().strip()
        vol     = float(data["volume"])
        mg      = int(data.get("magic", 2025))
        cm      = sanitize_comment(data.get("comment", "mlsl-exec"), fallback="mlsl-exec")
        slv     = data.get("sl"); tpv = data.get("tp"); pos = data.get("position")

        if side_s not in ("buy", "sell"):
            raise RuntimeError("side must be 'buy' or 'sell'")

        # === RESOLVE & ENSURE ===
        real_sym = self.s.resolve_symbol(sym_raw)
        self.s.ensure_symbol(real_sym)

        info = MT5.symbol_info(real_sym)
        if info is None:
            raise RuntimeError("symbol_info returned None")

        tick = MT5.symbol_info_tick(real_sym)
        if tick is None:
            code, msg = MT5.last_error()
            raise RuntimeError(f"symbol_info_tick None ({code},{msg})")

        # normalizar volume (resolve o retcode 10014 / Invalid volume)
        vol = self._normalize_volume(info, vol)

        price = float(tick.ask if side_s == "buy" else tick.bid)
        order_type = MT5.ORDER_TYPE_BUY if side_s == "buy" else MT5.ORDER_TYPE_SELL

        base_req: Dict[str, Any] = {
            "action": MT5.TRADE_ACTION_DEAL,
            "symbol": real_sym,
            "volume": float(vol),
            "type": order_type,
            "price": price,
            "magic": mg,
            "deviation": 100,
            "type_time": MT5.ORDER_TIME_GTC,
        }
        if cm: base_req["comment"] = cm
        if slv is not None: base_req["sl"] = float(slv)
        if tpv is not None: base_req["tp"] = float(tpv)
        if pos is not None: base_req["position"] = int(pos)

        # fillings preferidos
        candidates: List[int] = []
        for fm in (
            getattr(MT5, "ORDER_FILLING_IOC", None),
            getattr(MT5, "ORDER_FILLING_RETURN", None),
            getattr(MT5, "ORDER_FILLING_FOK", None),
        ):
            if isinstance(fm, int):
                candidates.append(fm)
        try:
            if hasattr(info, "filling_mode"):
                fm_sym = int(info.filling_mode)
                if fm_sym in candidates: candidates.remove(fm_sym)
                candidates.insert(0, fm_sym)
        except Exception:
            pass

        def try_with_filling(payload: Dict[str, Any], fm: Optional[int]) -> Optional[Dict[str, Any]]:
            req_payload = dict(payload)
            if fm is not None: req_payload["type_filling"] = int(fm)
            else: req_payload.pop("type_filling", None)

            chk = MT5.order_check(req_payload)
            if chk is not None and getattr(chk, "retcode", None) not in (MT5.TRADE_RETCODE_DONE, 0):
                if getattr(chk, "retcode", None) == 10030:
                    return None
                code, msg = MT5.last_error()
                raise RuntimeError(
                    f"order_check failed retcode={getattr(chk,'retcode',None)} "
                    f"comment={getattr(chk,'comment',None)} last_error=({code},{msg})"
                )

            res = MT5.order_send(req_payload)
            if res is None:
                code, msg = MT5.last_error()
                if msg and 'Invalid "comment" argument' in str(msg):
                    raise ValueError("invalid-comment")
                return None
            d = nt_to_dict(res)
            if d.get("retcode") == MT5.TRADE_RETCODE_DONE:
                return d
            return None

        try:
            for fm in candidates:
                ok = try_with_filling(base_req, fm)
                if ok: return ok
            ok = try_with_filling(base_req, None)
            if ok: return ok
        except ValueError as ve:
            if str(ve) == "invalid-comment":
                base2 = dict(base_req); base2.pop("comment", None)
                for fm in candidates:
                    ok = try_with_filling(base2, fm)
                    if ok: return ok
                ok = try_with_filling(base2, None)
                if ok: return ok
            else:
                raise

        code, msg = MT5.last_error()
        raise RuntimeError(f"order_send failed for all fillings; last_error=({code},{msg})")

    def close_symbol(self, symbol: str) -> List[Dict[str, Any]]:
        self.s.ensure_up()
        self.s.ensure_symbol(symbol)
        positions = MT5.positions_get(symbol=symbol) or []
        out: List[Dict[str, Any]] = []
        for p in positions:
            d = nt_to_dict(p)
            ticket = d.get("ticket")
            vol = float(d.get("volume") or 0)
            if not ticket or vol <= 0:
                continue
            is_buy = (d.get("type") == MT5.ORDER_TYPE_BUY)
            side_close = MT5.ORDER_TYPE_SELL if is_buy else MT5.ORDER_TYPE_BUY
            tick = MT5.symbol_info_tick(symbol)
            price = tick.bid if side_close == MT5.ORDER_TYPE_SELL else tick.ask
            req = {
                "action": MT5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": vol,
                "type": side_close,
                "price": float(price),
                "magic": int(d.get("magic") or 2025),
                "deviation": 20,
                "comment": "close_symbol",
                "type_time": MT5.ORDER_TIME_GTC,
                "type_filling": MT5.ORDER_FILLING_IOC,
                "position": int(ticket),
            }
            r = MT5.order_send(req)
            out.append({"ticket": ticket, "retcode": getattr(r, "retcode", None), "raw": nt_to_dict(r) if r else None})
        return out

    def close_ticket(self, ticket: int) -> Dict[str, Any]:
        self.s.ensure_up()
        p = next((pp for pp in (MT5.positions_get() or []) if getattr(pp, "ticket", None) == ticket), None)
        if not p:
            raise RuntimeError(f"position {ticket} not found")
        is_buy = (p.type == MT5.ORDER_TYPE_BUY)
        side_close = MT5.ORDER_TYPE_SELL if is_buy else MT5.ORDER_TYPE_BUY
        tick = MT5.symbol_info_tick(p.symbol)
        price = tick.bid if side_close == MT5.ORDER_TYPE_SELL else tick.ask
        req = {
            "action": MT5.TRADE_ACTION_DEAL,
            "symbol": p.symbol,
            "volume": float(p.volume),
            "type": side_close,
            "price": float(price),
            "deviation": 20,
            "magic": int(getattr(p, "magic", 2025) or 2025),
            "comment": "close_ticket",
            "type_time": MT5.ORDER_TIME_GTC,
            "type_filling": MT5.ORDER_FILLING_IOC,
            "position": int(p.ticket),
        }
        r = MT5.order_send(req)
        d = nt_to_dict(r) if r else None
        if not r or d.get("retcode") != MT5.TRADE_RETCODE_DONE:
            raise RuntimeError(f"close failed: {d}")
        return d