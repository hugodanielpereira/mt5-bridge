# app/services/mt5/orders.py
from __future__ import annotations
from typing import Any, Dict, List, Optional, Union
from fastapi import HTTPException

try:
    import MetaTrader5 as MT5
except Exception as e:
    raise RuntimeError(f"MetaTrader5 import failed: {e!r}")

from .session import MT5Session
from .utils import nt_to_dict, sanitize_comment

class Orders:
    def __init__(self, session: MT5Session) -> None:
        self.s = session

    def order_market(
        self,
        req: Union[Dict[str, Any], Any, None] = None, *,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        volume: Optional[float] = None,
        magic: int = 2025,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        comment: Optional[str] = "mlsl-exec",
        position: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Envio robusto com fallback de filling + order_check."""
        self.s.ensure_up()

        # normalização
        if req is not None:
            if hasattr(req, "dict"):
                data = req.dict()
            elif isinstance(req, dict):
                data = dict(req)
            else:
                raise HTTPException(status_code=400, detail="invalid request object for order_market")
        else:
            data = {"symbol": symbol, "side": side, "volume": volume,
                    "magic": magic, "sl": sl, "tp": tp, "comment": comment, "position": position}

        for key in ("symbol", "side", "volume"):
            if data.get(key) in (None, ""):
                raise HTTPException(status_code=400, detail=f"missing field '{key}'")

        sym = str(data["symbol"])
        s   = str(data["side"]).lower().strip()
        vol = float(data["volume"])
        mg  = int(data.get("magic", 2025))
        cm  = sanitize_comment(data.get("comment"), "mlsl-exec")
        slv = data.get("sl")
        tpv = data.get("tp")
        pos = data.get("position")

        if s not in ("buy", "sell"):
            raise HTTPException(status_code=400, detail="side must be 'buy' or 'sell'")

        # símbolo & tick
        self.s.ensure_symbol(sym)
        info = MT5.symbol_info(sym)
        if info is None:
            raise HTTPException(status_code=500, detail="symbol_info returned None")
        tick = MT5.symbol_info_tick(sym)
        if tick is None:
            code, msg = MT5.last_error()
            raise HTTPException(status_code=500, detail={"error": "symbol_info_tick None", "last_error": [code, msg]})

        price = float(tick.ask if s == "buy" else tick.bid)
        order_type = MT5.ORDER_TYPE_BUY if s == "buy" else MT5.ORDER_TYPE_SELL

        base_req = {
            "action": MT5.TRADE_ACTION_DEAL,
            "symbol": sym,
            "volume": float(vol),
            "type": order_type,
            "price": price,
            "magic": mg,
            "deviation": 100,
            "comment": cm,
            "type_time": MT5.ORDER_TIME_GTC,
        }
        if slv is not None: base_req["sl"] = float(slv)
        if tpv is not None: base_req["tp"] = float(tpv)
        if pos is not None: base_req["position"] = int(pos)

        # lista de fillings: símbolo -> FOK -> IOC -> RETURN
        candidates: List[int] = []
        try:
            if hasattr(info, "filling_mode"):
                candidates.append(int(info.filling_mode))
        except Exception:
            pass
        for fm in (
            getattr(MT5, "ORDER_FILLING_FOK", None),
            getattr(MT5, "ORDER_FILLING_IOC", None),
            getattr(MT5, "ORDER_FILLING_RETURN", None),
        ):
            if fm is not None and int(fm) not in candidates:
                candidates.append(int(fm))

        # order_check no 1º filling (se existir)
        check_req = dict(base_req)
        if candidates:
            check_req["type_filling"] = candidates[0]
        chk = MT5.order_check(check_req)
        if chk is not None and getattr(chk, "retcode", None) not in (MT5.TRADE_RETCODE_DONE, 0):
            code, msg = MT5.last_error()
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "order_check failed",
                    "retcode": getattr(chk, "retcode", None),
                    "comment": getattr(chk, "comment", None),
                    "last_error": [code, msg],
                    "tried_filling": candidates[:1],
                },
            )

        # tenta todos os fillings
        errs: List[Dict[str, Any]] = []
        for fm in candidates or [None]:
            req_payload = dict(base_req)
            if fm is not None:
                req_payload["type_filling"] = fm
            res = MT5.order_send(req_payload)
            if res is None:
                code, msg = MT5.last_error()
                errs.append({"filling": fm, "error": "order_send None", "last_error": [code, msg]})
                continue
            d = nt_to_dict(res)
            if d.get("retcode") == MT5.TRADE_RETCODE_DONE:
                return d
            errs.append({"filling": fm, "retcode": d.get("retcode"), "raw": d})

        code, msg = MT5.last_error()
        raise HTTPException(status_code=400, detail={"error": "order_send failed", "tries": errs, "last_error": [code, msg]})

    def close_symbol(self, symbol: str):
        self.s.ensure_up()
        self.s.ensure_symbol(symbol)
        positions = MT5.positions_get(symbol=symbol) or []
        results: List[Dict[str, Any]] = []
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
                "action": MT5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": vol, "type": side_close,
                "price": float(price), "magic": int(d.get("magic") or 2025), "deviation": 20,
                "comment": "close_symbol", "type_time": MT5.ORDER_TIME_GTC, "type_filling": MT5.ORDER_FILLING_IOC,
                "position": int(ticket),
            }
            r = MT5.order_send(req)
            results.append({"ticket": ticket, "retcode": getattr(r, "retcode", None), "raw": nt_to_dict(r) if r else None})
        return results

    def close_ticket(self, ticket: int):
        self.s.ensure_up()
        p = next((pp for pp in (MT5.positions_get() or []) if getattr(pp, "ticket", None) == ticket), None)
        if not p:
            raise RuntimeError(f"position {ticket} not found")
        is_buy = (p.type == MT5.ORDER_TYPE_BUY)
        side_close = MT5.ORDER_TYPE_SELL if is_buy else MT5.ORDER_TYPE_BUY
        tick = MT5.symbol_info_tick(p.symbol)
        price = tick.bid if side_close == MT5.ORDER_TYPE_SELL else tick.ask
        req = {
            "action": MT5.TRADE_ACTION_DEAL, "symbol": p.symbol, "volume": float(p.volume),
            "type": side_close, "price": float(price), "deviation": 20,
            "magic": int(getattr(p, "magic", 2025) or 2025), "comment": "close_ticket",
            "type_time": MT5.ORDER_TIME_GTC, "type_filling": MT5.ORDER_FILLING_IOC, "position": int(p.ticket),
        }
        r = MT5.order_send(req)
        d = nt_to_dict(r) if r else None
        if not r or d.get("retcode") != MT5.TRADE_RETCODE_DONE:
            raise RuntimeError(f"close failed: {d}")
        return d

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

        # pelo menos um dos dois tem de ser fornecido ou existir na posição
        if sl is None and tp is None:
            raise HTTPException(status_code=400, detail="sl or tp required")

        p = next(
            (pp for pp in (MT5.positions_get() or []) if getattr(pp, "ticket", None) == ticket),
            None,
        )
        if not p:
            raise HTTPException(status_code=404, detail=f"position {ticket} not found")

        sym = str(p.symbol)
        self.s.ensure_symbol(sym)

        cur_sl = float(getattr(p, "sl", 0.0) or 0.0)
        cur_tp = float(getattr(p, "tp", 0.0) or 0.0)

        new_sl = float(sl) if sl is not None else cur_sl
        new_tp = float(tp) if tp is not None else cur_tp

        req = {
            "action": MT5.TRADE_ACTION_SLTP,
            "symbol": sym,
            "position": int(p.ticket),
            "sl": new_sl,
            "tp": new_tp,
        }

        res = MT5.order_send(req)
        d = nt_to_dict(res) if res else None
        if not res or d.get("retcode") != MT5.TRADE_RETCODE_DONE:
            raise HTTPException(status_code=400, detail={"error": "modify_failed", "raw": d})

        return d