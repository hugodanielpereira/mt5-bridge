# app/services/mt5/trade.py
from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    import MetaTrader5 as MT5
except Exception as e:  # pragma: no cover
    raise RuntimeError(f"MetaTrader5 import failed: {e!r}")

from .session import MT5Session
from .utils import nt_to_dict, sanitize_comment


class Trade:
    """Trading ops (market orders, close by symbol/ticket)."""

    def __init__(self, session: MT5Session) -> None:
        self.s = session

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
        Ordem a mercado com fallback de filling e fallback extra:
        - se broker recusar `comment`, reenvia **sem** o campo `comment`.
        """
        self.s.ensure_up()

        # --- request merge ---
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

        sym = str(data["symbol"])
        s = str(data["side"]).lower().strip()
        vol = float(data["volume"])
        mg = int(data.get("magic", 2025))
        cm = sanitize_comment(data.get("comment", "mlsl-exec"), fallback="mlsl-exec")
        slv = data.get("sl")
        tpv = data.get("tp")
        pos = data.get("position")

        if s not in ("buy", "sell"):
            raise RuntimeError("side must be 'buy' or 'sell'")

        # --- símbolo & tick ---
        self.s.ensure_symbol(sym)  # <-- FIX: era self._ensure_symbol(sym)
        info = MT5.symbol_info(sym)
        if info is None:
            raise RuntimeError("symbol_info returned None")

        tick = MT5.symbol_info_tick(sym)
        if tick is None:
            code, msg = MT5.last_error()
            raise RuntimeError(f"symbol_info_tick None ({code},{msg})")

        price = float(tick.ask if s == "buy" else tick.bid)
        order_type = MT5.ORDER_TYPE_BUY if s == "buy" else MT5.ORDER_TYPE_SELL

        # --- base request ---
        base_req: Dict[str, Any] = {
            "action": MT5.TRADE_ACTION_DEAL,
            "symbol": sym,
            "volume": float(vol),
            "type": order_type,
            "price": price,
            "magic": mg,
            "deviation": 100,
            "type_time": MT5.ORDER_TIME_GTC,
        }
        # comenta inicialmente (vamos poder removê-lo se der erro)
        if cm:
            base_req["comment"] = cm
        if slv is not None:
            base_req["sl"] = float(slv)
        if tpv is not None:
            base_req["tp"] = float(tpv)
        if pos is not None:
            base_req["position"] = int(pos)

        # --- fillings candidates (prioridade mais compatível: IOC -> RETURN -> FOK) ---
        candidates: List[int] = []
        for fm in (
            getattr(MT5, "ORDER_FILLING_IOC", None),
            getattr(MT5, "ORDER_FILLING_RETURN", None),
            getattr(MT5, "ORDER_FILLING_FOK", None),
        ):
            if isinstance(fm, int):
                candidates.append(fm)

        # se o símbolo expõe filling_mode válido, mete-o no topo sem duplicar
        try:
            if hasattr(info, "filling_mode"):
                fm_sym = int(info.filling_mode)
                if fm_sym in candidates:
                    candidates.remove(fm_sym)
                candidates.insert(0, fm_sym)
        except Exception:
            pass

        # --- função utilitária para tentar (order_check -> order_send) para um dado filling ---
        def try_with_filling(payload: Dict[str, Any], fm: Optional[int]) -> Optional[Dict[str, Any]]:
            req_payload = dict(payload)
            if fm is not None:
                req_payload["type_filling"] = int(fm)
            else:
                # garantir que não herdamos algum valor anterior
                req_payload.pop("type_filling", None)

            # 1) order_check (se falhar com 10030, saltamos para próximo filling)
            chk = MT5.order_check(req_payload)
            if chk is not None and getattr(chk, "retcode", None) not in (MT5.TRADE_RETCODE_DONE, 0):
                # Unsupported filling mode → tenta próximo
                if getattr(chk, "retcode", None) == 10030:
                    return None
                # outros erros de check: propaga
                code, msg = MT5.last_error()
                raise RuntimeError(
                    f"order_check failed retcode={getattr(chk,'retcode',None)} "
                    f"comment={getattr(chk,'comment',None)} last_error=({code},{msg})"
                )

            # 2) order_send
            res = MT5.order_send(req_payload)
            if res is None:
                code, msg = MT5.last_error()
                # se for queixa de comment inválido, sinalizamos para o chamador
                if msg and 'Invalid "comment" argument' in str(msg):
                    raise ValueError("invalid-comment")
                return None

            d = nt_to_dict(res)
            if d.get("retcode") == MT5.TRADE_RETCODE_DONE:
                return d
            return None

        # --- 1ª tentativa: com comment normal ---
        try:
            # percorre fillings conhecidos
            for fm in candidates:
                ok = try_with_filling(base_req, fm)
                if ok:
                    return ok
            # última tentativa: sem especificar filling (deixa o servidor decidir)
            ok = try_with_filling(base_req, None)
            if ok:
                return ok

        except ValueError as ve:
            # 'invalid-comment' → refazemos sem comment
            if str(ve) == "invalid-comment":
                base2 = dict(base_req)
                base2.pop("comment", None)
                # repete o ciclo com base2
                for fm in candidates:
                    ok = try_with_filling(base2, fm)
                    if ok:
                        return ok
                ok = try_with_filling(base2, None)
                if ok:
                    return ok
                # se ainda falhar, cai no bloco final abaixo
            else:
                raise

        # --- se chegou aqui, recolhe último erro e reporta ---
        code, msg = MT5.last_error()
        raise RuntimeError(f"order_send failed for all fillings; last_error=({code},{msg})")

        # --- order_check (opcional, com o 1º filling) ---
        check_req = dict(base_req)
        check_req["type_filling"] = candidates[0]
        chk = MT5.order_check(check_req)
        if chk is not None and getattr(chk, "retcode", None) not in (MT5.TRADE_RETCODE_DONE, 0):
            code, msg = MT5.last_error()
            raise RuntimeError(
                f"order_check failed retcode={getattr(chk,'retcode',None)} "
                f"comment={getattr(chk,'comment',None)} last_error=({code},{msg})"
            )

        # --- tenta enviar; se der "Invalid \"comment\" argument", tenta sem comment ---
        def try_send(payload: Dict[str, Any]) -> Dict[str, Any]:
            errs: List[Dict[str, Any]] = []
            for fm in candidates:
                req_payload = dict(payload)
                req_payload["type_filling"] = fm
                res = MT5.order_send(req_payload)
                if res is None:
                    code, msg = MT5.last_error()
                    errs.append({"filling": fm, "error": "order_send None", "last_error": [code, msg]})
                    if msg and "Invalid \"comment\" argument" in str(msg):
                        raise ValueError("invalid-comment")
                    continue
                d = nt_to_dict(res)
                if d.get("retcode") == MT5.TRADE_RETCODE_DONE:
                    return d
                errs.append({"filling": fm, "retcode": d.get("retcode"), "raw": d})
            code, msg = MT5.last_error()
            if msg and "Invalid \"comment\" argument" in str(msg):
                raise ValueError("invalid-comment")
            raise RuntimeError(f"order_send failed tries={errs} last_error=({code},{msg})")

        try:
            return try_send(base_req)
        except ValueError as ve:
            if str(ve) == "invalid-comment":
                payload2 = dict(base_req)
                payload2.pop("comment", None)
                return try_send(payload2)
            raise

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