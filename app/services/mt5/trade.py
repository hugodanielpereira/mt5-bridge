# app/services/mt5/trade.py
from __future__ import annotations
from typing import Any, Dict, List, Optional
from decimal import Decimal, ROUND_FLOOR
import logging

try:
    import MetaTrader5 as MT5
except Exception as e:  # pragma: no cover
    raise RuntimeError(f"MetaTrader5 import failed: {e!r}")

from .session import MT5Session
from .utils import nt_to_dict, sanitize_comment

log = logging.getLogger("mt5.trade")


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
            dv    = Decimal(str(v))
            dmin  = Decimal(str(vmin))
            dstep = Decimal(str(vstep))
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

    # ------------------------------------------------------------------ #
    # ORDEM DE MERCADO
    # ------------------------------------------------------------------ #
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
        """
        Envia ordem de mercado (BUY/SELL).

        Em caso de falha em todos os fillings, levanta RuntimeError com
        retcode + comment da última tentativa e MT5.last_error().
        """
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
                "symbol": symbol,
                "side": side,
                "volume": volume,
                "magic": magic,
                "sl": sl,
                "tp": tp,
                "comment": comment,
                "position": position,
            }

        for key in ("symbol", "side", "volume"):
            if data.get(key) in (None, ""):
                raise RuntimeError(f"missing field '{key}'")

        sym_raw = str(data["symbol"])
        side_s  = str(data["side"]).lower().strip()
        vol     = float(data["volume"])
        mg      = int(data.get("magic", 2025))
        cm      = sanitize_comment(data.get("comment", "mlsl-exec"), fallback="mlsl-exec")
        slv     = data.get("sl")
        tpv     = data.get("tp")
        pos     = data.get("position")

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

        price      = float(tick.ask if side_s == "buy" else tick.bid)
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
        if cm:
            base_req["comment"] = cm
        if slv is not None:
            base_req["sl"] = float(slv)
        if tpv is not None:
            base_req["tp"] = float(tpv)
        if pos is not None:
            base_req["position"] = int(pos)

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
                if fm_sym in candidates:
                    candidates.remove(fm_sym)
                candidates.insert(0, fm_sym)
        except Exception:
            pass

        last_send: Optional[Dict[str, Any]] = None  # último resultado de order_send()

        def try_with_filling(payload: Dict[str, Any], fm: Optional[int]) -> Optional[Dict[str, Any]]:
            nonlocal last_send

            req_payload = dict(payload)
            if fm is not None:
                req_payload["type_filling"] = int(fm)
            else:
                req_payload.pop("type_filling", None)

            chk = MT5.order_check(req_payload)
            if chk is not None and getattr(chk, "retcode", None) not in (
                MT5.TRADE_RETCODE_DONE,
                0,
            ):
                # se for "invalid filling mode" (10030) tentamos outro filling
                if getattr(chk, "retcode", None) == 10030:
                    log.warning(
                        "order_check.invalid_filling symbol=%s side=%s volume=%s fm=%s comment=%s",
                        req_payload.get("symbol"),
                        side_s,
                        req_payload.get("volume"),
                        fm,
                        getattr(chk, "comment", None),
                    )
                    return None

                code, msg = MT5.last_error()
                raise RuntimeError(
                    "order_check failed retcode={retcode} comment={comment} last_error=({code},{msg})".format(
                        retcode=getattr(chk, "retcode", None),
                        comment=getattr(chk, "comment", None),
                        code=code,
                        msg=msg,
                    )
                )

            res = MT5.order_send(req_payload)
            if res is None:
                code, msg = MT5.last_error()
                last_send = None
                if msg and 'Invalid "comment" argument' in str(msg):
                    raise ValueError("invalid-comment")
                log.warning(
                    'order_send.none symbol=%s side=%s volume=%s fm=%s last_error=(%s,%s)',
                    req_payload.get("symbol"),
                    side_s,
                    req_payload.get("volume"),
                    fm,
                    code,
                    msg,
                )
                return None

            d = nt_to_dict(res)
            last_send = d

            if d.get("retcode") == MT5.TRADE_RETCODE_DONE:
                log.info(
                    "order_market.ok symbol=%s side=%s volume=%s fm=%s ticket=%s",
                    req_payload.get("symbol"),
                    side_s,
                    req_payload.get("volume"),
                    fm,
                    d.get("order") or d.get("deal"),
                )
                return d

            # falhou esta tentativa; fica registado em last_send
            log.warning(
                "order_market.retcode_fail symbol=%s side=%s volume=%s fm=%s retcode=%s comment=%s",
                req_payload.get("symbol"),
                side_s,
                req_payload.get("volume"),
                fm,
                d.get("retcode"),
                d.get("comment"),
            )
            return None

        # --- tentativas com os vários fillings ---
        try:
            for fm in candidates:
                ok = try_with_filling(base_req, fm)
                if ok:
                    return ok
            ok = try_with_filling(base_req, None)
            if ok:
                return ok
        except ValueError as ve:
            if str(ve) == "invalid-comment":
                # tentar novamente sem comment
                base2 = dict(base_req)
                base2.pop("comment", None)
                for fm in candidates:
                    ok = try_with_filling(base2, fm)
                    if ok:
                        return ok
                ok = try_with_filling(base2, None)
                if ok:
                    return ok
            else:
                raise

        # --- se chegou aqui, todas as tentativas falharam ---
        code, msg = MT5.last_error()
        retcode = last_send.get("retcode") if isinstance(last_send, dict) else None
        comment = last_send.get("comment") if isinstance(last_send, dict) else None

        log.error(
            "order_market.failed_all symbol=%s side=%s volume=%s retcode=%s comment=%s last_error=(%s,%s)",
            real_sym,
            side_s,
            vol,
            retcode,
            comment,
            code,
            msg,
        )

        raise RuntimeError(
            f"order_send failed for all fillings; "
            f"retcode={retcode}, comment={comment!r}, last_error=({code},{msg})"
        )

    # ------------------------------------------------------------------ #
    # FECHAR POSIÇÕES (TOTAL OU PARCIAL)
    # ------------------------------------------------------------------ #
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
            is_buy = d.get("type") == MT5.ORDER_TYPE_BUY
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
            out.append(
                {
                    "ticket": ticket,
                    "retcode": getattr(r, "retcode", None),
                    "raw": nt_to_dict(r) if r else None,
                }
            )
        return out

    def close_ticket(self, ticket: int, volume: Optional[float] = None) -> Dict[str, Any]:
        """
        Fecha uma posição:
          - se volume for None ou >= volume da posição → fecha tudo
          - se volume for > 0 e < volume posição        → FECHO PARCIAL
        """
        self.s.ensure_up()
        p = next(
            (
                pp
                for pp in (MT5.positions_get() or [])
                if getattr(pp, "ticket", None) == ticket
            ),
            None,
        )
        if not p:
            raise RuntimeError(f"position {ticket} not found")

        pos_vol = float(getattr(p, "volume", 0.0) or 0.0)
        if pos_vol <= 0:
            raise RuntimeError(f"position {ticket} has non-positive volume")

        # volume a fechar: se None ou inválido → fecha tudo
        try:
            vol_req = float(volume) if volume is not None else pos_vol
        except Exception:
            vol_req = pos_vol

        if vol_req <= 0 or vol_req >= pos_vol:
            vol_close = pos_vol
        else:
            vol_close = vol_req

        is_buy = p.type == MT5.ORDER_TYPE_BUY
        side_close = MT5.ORDER_TYPE_SELL if is_buy else MT5.ORDER_TYPE_BUY
        tick = MT5.symbol_info_tick(p.symbol)
        if tick is None:
            code, msg = MT5.last_error()
            raise RuntimeError(f"symbol_info_tick None ({code},{msg})")
        price = tick.bid if side_close == MT5.ORDER_TYPE_SELL else tick.ask

        req = {
            "action": MT5.TRADE_ACTION_DEAL,
            "symbol": p.symbol,
            "volume": float(vol_close),
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

    # ------------------------------------------------------------------ #
    # MODIFICAR SL / TP
    # ------------------------------------------------------------------ #
    def modify_position(
        self,
        ticket: int,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Atualiza SL/TP de uma posição existente (por ticket).
        Se sl ou tp vierem como None, mantém o valor atual.

        Usa TRADE_ACTION_SLTP do MT5.
        """
        self.s.ensure_up()
        p = next(
            (
                pp
                for pp in (MT5.positions_get() or [])
                if getattr(pp, "ticket", None) == ticket
            ),
            None,
        )
        if not p:
            raise RuntimeError(f"position {ticket} not found")

        sym = str(p.symbol)
        self.s.ensure_symbol(sym)

        cur_sl = float(getattr(p, "sl", 0.0) or 0.0)
        cur_tp = float(getattr(p, "tp", 0.0) or 0.0)

        new_sl = float(sl) if sl is not None else cur_sl
        new_tp = float(tp) if tp is not None else cur_tp

        req: Dict[str, Any] = {
            "action": MT5.TRADE_ACTION_SLTP,
            "symbol": sym,
            "position": int(p.ticket),
            "sl": new_sl,
            "tp": new_tp,
        }

        res = MT5.order_send(req)
        d = nt_to_dict(res) if res else None
        if not res or d.get("retcode") != MT5.TRADE_RETCODE_DONE:
            raise RuntimeError(f"modify_failed: {d}")
        return d