# app/services/mt5/orders.py
from __future__ import annotations
from typing import Any, Dict, List, Optional, Union
from fastapi import HTTPException
import os

try:
    import MetaTrader5 as MT5
except Exception as e:
    raise RuntimeError(f"MetaTrader5 import failed: {e!r}")

from .session import MT5Session
from .utils import nt_to_dict

import logging
log = logging.getLogger("mt5.orders")

FORCE_IOC = str(os.getenv("MT5_FORCE_IOC", "0")).strip().lower() in ("1", "true", "yes", "y")


def _sanitize_comment_mt5(comment: Any, fallback: str = "mlsl-exec", max_len: int = 31) -> str:
    """
    Sanitização mínima, *compatível* com a lib MetaTrader5:

    - se vier vazio → usa fallback
    - força para str
    - remove caracteres não-ASCII imprimíveis
    - corta para max_len (por defeito 31 chars)
    """
    s = str(comment or "").strip()
    if not s:
        s = fallback

    safe_chars = []
    for ch in s:
        code = ord(ch)
        if 32 <= code < 127:  # espaço até ~
            safe_chars.append(ch)
        else:
            safe_chars.append("_")

    safe = "".join(safe_chars)
    if len(safe) > max_len:
        safe = safe[:max_len]
    return safe


def _fmt_filling(fm: Optional[int]) -> str:
    """Converte o código numérico de filling num nome legível."""
    if fm is None:
        return "NONE"
    try:
        fm_i = int(fm)
    except Exception:
        return f"UNKNOWN({fm})"

    name = None
    if hasattr(MT5, "ORDER_FILLING_FOK") and fm_i == int(MT5.ORDER_FILLING_FOK):
        name = "FOK"
    elif hasattr(MT5, "ORDER_FILLING_IOC") and fm_i == int(MT5.ORDER_FILLING_IOC):
        name = "IOC"
    elif hasattr(MT5, "ORDER_FILLING_RETURN") and fm_i == int(MT5.ORDER_FILLING_RETURN):
        name = "RETURN"

    return f"{name or 'UNKNOWN'}({fm_i})"


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
        """
        Envio robusto com:
          - normalização do payload
          - comentário seguro (evita Invalid \"comment\" argument)
          - sanity check básico de SL/TP
          - order_check com tratamento de:
              * Unsupported filling mode
              * Invalid stops
          - fallback de filling_mode (symbol_info.filling_mode -> FOK -> IOC -> RETURN)
        """
        self.s.ensure_up()

        # ----------------- Normalização -----------------
        if req is not None:
            if hasattr(req, "dict"):
                data = req.dict()
            elif isinstance(req, dict):
                data = dict(req)
            else:
                raise HTTPException(status_code=400, detail="invalid request object for order_market")
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
                raise HTTPException(status_code=400, detail=f"missing field '{key}'")

        sym = str(data["symbol"])
        s   = str(data["side"]).lower().strip()
        vol = float(data["volume"])
        mg  = int(data.get("magic", 2025))
        raw_comment = data.get("comment")
        slv = data.get("sl")
        tpv = data.get("tp")
        pos = data.get("position")

        if s not in ("buy", "sell"):
            raise HTTPException(status_code=400, detail="side must be 'buy' or 'sell'")

        # ----------------- Comentário seguro -----------------
        cm_base = "mlsl-exec"
        if not isinstance(raw_comment, str):
            raw_comment = ""
        extra_chars: List[str] = []
        for ch in raw_comment.strip():
            if ch.isalnum() or ch in ("_", "-", "."):
                extra_chars.append(ch)
        if extra_chars:
            cm_full = f"{cm_base}-{''.join(extra_chars)}"
        else:
            cm_full = cm_base

        cm = _sanitize_comment_mt5(cm_full, fallback=cm_base, max_len=31)

        # ----------------- Info do símbolo / preço -----------------
        self.s.ensure_symbol(sym)
        info = MT5.symbol_info(sym)
        if info is None:
            raise HTTPException(status_code=500, detail="symbol_info returned None")

        tick = MT5.symbol_info_tick(sym)
        if tick is None:
            code, msg = MT5.last_error()
            raise HTTPException(
                status_code=500,
                detail={"error": "symbol_info_tick None", "last_error": [code, msg]},
            )

        price = float(tick.ask if s == "buy" else tick.bid)
        order_type = MT5.ORDER_TYPE_BUY if s == "buy" else MT5.ORDER_TYPE_SELL

        # ----------------- Sanity check stops (SL/TP) -----------------
        import math

        def _clean_stop(v):
            """Converte para float finito ou devolve None."""
            try:
                if v is None:
                    return None
                fv = float(v)
                if not math.isfinite(fv):
                    return None
                return fv
            except Exception:
                return None

        slv = _clean_stop(slv)
        tpv = _clean_stop(tpv)

        point = float(getattr(info, "point", 0.0) or 0.0)
        stops_level = int(getattr(info, "stops_level", 0) or 0)
        min_dist = stops_level * point if point > 0 else 0.0

        issues: List[str] = []

        # relação SL/TP vs preço
        if slv is not None:
            if s == "buy" and slv >= price:
                issues.append(f"sl>=price({slv} >= {price})")
                slv = None
            elif s == "sell" and slv <= price:
                issues.append(f"sl<=price({slv} <= {price})")
                slv = None

        if tpv is not None:
            if s == "buy" and tpv <= price:
                issues.append(f"tp<=price({tpv} <= {price})")
                tpv = None
            elif s == "sell" and tpv >= price:
                issues.append(f"tp>=price({tpv} >= {price})")
                tpv = None

        # distância mínima (stops_level)
        if min_dist > 0:
            if slv is not None and abs(price - slv) < min_dist:
                issues.append(f"sl_too_close({abs(price - slv)} < {min_dist})")
                slv = None
            if tpv is not None and abs(price - tpv) < min_dist:
                issues.append(f"tp_too_close({abs(price - tpv)} < {min_dist})")
                tpv = None

        if issues:
            log.warning(
                "order_market.sanitize_stops symbol=%s side=%s price=%.5f sl_after=%s tp_after=%s issues=%s",
                sym, s, price, slv, tpv, issues,
            )

        # ----------------- Pedido base para MT5 -----------------
        base_req: Dict[str, Any] = {
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
        if slv is not None:
            base_req["sl"] = float(slv)
        if tpv is not None:
            base_req["tp"] = float(tpv)
        if pos is not None:
            base_req["position"] = int(pos)

        # ----------------- Filling modes (lista de candidatos) -----------------
        candidates: List[int] = []

        if FORCE_IOC:
            # modo “limpo”: só IOC, sem tentativas de RETURN/FOK
            fm_ioc = getattr(MT5, "ORDER_FILLING_IOC", None)
            if fm_ioc is not None:
                candidates.append(int(fm_ioc))
        else:
            # modo “auto”: começa pelo filling_mode do símbolo, depois FOK/IOC/RETURN
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

        log.info(
            "order_market.fill_candidates symbol=%s side=%s candidates=%s",
            sym, s, [ _fmt_filling(fm) for fm in candidates ] or ["NONE"],
        )

        # ----------------- order_check no 1º filling (se existir) ---------------
        if candidates:
            check_req = dict(base_req)
            check_req["type_filling"] = candidates[0]
            chk = MT5.order_check(check_req)

            ret = getattr(chk, "retcode", None) if chk is not None else None
            comment_chk = getattr(chk, "comment", None) if chk is not None else None

            unsupported_fill_codes: set[int] = set()
            if hasattr(MT5, "TRADE_RETCODE_INVALID_FILL"):
                unsupported_fill_codes.add(int(MT5.TRADE_RETCODE_INVALID_FILL))
            unsupported_fill_codes.add(10030)  # típico para invalid/unsupported filling

            invalid_stops_codes: set[int] = set()
            if hasattr(MT5, "TRADE_RETCODE_INVALID_STOPS"):
                invalid_stops_codes.add(int(MT5.TRADE_RETCODE_INVALID_STOPS))
            invalid_stops_codes.add(130)  # clássico "invalid stops"

            is_unsupported_fill = (
                ret in unsupported_fill_codes
                or (isinstance(comment_chk, str) and "Unsupported filling mode" in comment_chk)
            )

            if chk is not None and ret not in (MT5.TRADE_RETCODE_DONE, 0):
                if is_unsupported_fill:
                    log.warning(
                        "order_market.check_unsupported_fill symbol=%s filling=%s retcode=%s comment=%s",
                        sym, _fmt_filling(candidates[0]), ret, comment_chk,
                    )
                    # drop do 1º filling e deixamos os restantes para o loop de order_send
                    candidates = candidates[1:]
                elif ret in invalid_stops_codes:
                    code, msg = MT5.last_error()
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error": "order_check invalid_stops",
                            "retcode": ret,
                            "comment": comment_chk,
                            "last_error": [code, msg],
                            "sl": base_req.get("sl"),
                            "tp": base_req.get("tp"),
                        },
                    )
                else:
                    code, msg = MT5.last_error()
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error": "order_check failed",
                            "retcode": ret,
                            "comment": comment_chk,
                            "last_error": [code, msg],
                            "tried_filling": candidates[:1],
                        },
                    )

        # ----------------- Envio: tentar todos os fillings -----------------
        errs: List[Dict[str, Any]] = []

        for fm in candidates or [None]:
            req_payload = dict(base_req)
            if fm is not None:
                req_payload["type_filling"] = fm

            res = MT5.order_send(req_payload)
            if res is None:
                code, msg = MT5.last_error()
                errs.append({"filling": fm, "error": "order_send None", "last_error": [code, msg]})
                log.warning(
                    "order_market.order_send_result symbol=%s side=%s filling=%s retcode=None last_error=%s",
                    sym, s, _fmt_filling(fm), [code, msg],
                )
                continue

            d = nt_to_dict(res)
            retcode = d.get("retcode")
            log.info(
                "order_market.order_send_result symbol=%s side=%s filling=%s retcode=%s deal=%s order=%s comment=%s",
                sym, s, _fmt_filling(fm), retcode, d.get("deal"), d.get("order"), d.get("comment"),
            )

            if retcode == MT5.TRADE_RETCODE_DONE:
                log.info(
                    "order_market.ok symbol=%s side=%s volume=%s filling=%s price=%.5f sl=%s tp=%s comment=%s",
                    sym, s, vol, _fmt_filling(fm), price,
                    base_req.get("sl"), base_req.get("tp"), cm,
                )
                return d

            errs.append({"filling": fm, "retcode": retcode, "raw": d})

        code, msg = MT5.last_error()
        log.warning(
            "order_market.fail symbol=%s side=%s volume=%s errs=%s last_error=%s",
            sym, s, vol, errs, [code, msg],
        )
        raise HTTPException(
            status_code=400,
            detail={"error": "order_send failed", "tries": errs, "last_error": [code, msg]},
        )

    # ------------------------------------------------------------------
    # RESTO DA CLASSE (close_symbol, close_ticket, modify_position)
    # ------------------------------------------------------------------

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