# app/services/mt5/orders.py
from __future__ import annotations

import math
import os
import logging
from decimal import Decimal, ROUND_FLOOR
from typing import Any, Dict, List, Optional, Union

from app.services.mt5.session import MT5Session
from app.services.mt5.utils import nt_to_dict, sanitize_comment
from app.common.meta import bridge_instance_id

log = logging.getLogger("mt5.orders")

FORCE_IOC = str(os.getenv("MT5_FORCE_IOC", "0")).strip().lower() in ("1", "true", "yes", "on", "y")


def _mt5_mode() -> str:
    return (os.getenv("MT5_MODE", "webrequest") or "webrequest").strip().lower()


def _is_webrequest_mode() -> bool:
    return _mt5_mode() in ("webrequest", "http", "ea", "bridge")


def _fmt_filling(MT5: Any, fm: Optional[int]) -> str:
    if fm is None:
        return "NONE"
    m = {
        getattr(MT5, "ORDER_FILLING_FOK", -9999): "FOK",
        getattr(MT5, "ORDER_FILLING_IOC", -9998): "IOC",
        getattr(MT5, "ORDER_FILLING_RETURN", -9997): "RETURN",
    }
    try:
        return m.get(int(fm), str(int(fm)))
    except Exception:
        return "NONE"


def _clean_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        fv = float(v)
        if not math.isfinite(fv):
            return None
        return fv
    except Exception:
        return None


def _deviation() -> int:
    try:
        return int(os.getenv("MT5_DEVIATION", "100"))
    except Exception:
        return 100


class Orders:
    """
    Execução e gestão de ordens no MT5.

    NOTA IMPORTANTE (Bottles / WebRequest-first):
      - Em MT5_MODE=webrequest, o bridge NÃO usa a lib MetaTrader5 para executar ordens.
        (a execução deve ser via EA <-> bridge, mas isso é outro mecanismo).
      - Para já, este módulo NÃO rebenta ao importar; mas chamadas de trade
        em webrequest mode levantam erro claro.
      - Em MT5_MODE=native, mantém o comportamento antigo (MetaTrader5 API).
    """

    def __init__(self, session: MT5Session) -> None:
        self.s = session
        self._mt5_mod = None  # lazy (native-only)

    # ----------------------------------------------------------------------
    # Native loader (legado) - só quando MT5_MODE=native
    # ----------------------------------------------------------------------
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
                f"Bridge is WebRequest-first (EA executes trades). "
                f"Set MT5_MODE=native to use MetaTrader5 Python API."
            )

    # -------------------------------------------------------------------------
    # Volume normalization
    # -------------------------------------------------------------------------
    def _normalize_volume(self, info: Any, vol_in: float) -> float:
        try:
            vmin = float(getattr(info, "volume_min", 0) or 0)
            vstep = float(getattr(info, "volume_step", 0) or 0)
            vmax = float(getattr(info, "volume_max", 0) or 0)
        except Exception:
            vmin, vstep, vmax = 0.0, 0.0, 0.0

        v = float(vol_in)

        if vmin > 0 and v < vmin:
            v = vmin

        if vstep and vstep > 0:
            dv = Decimal(str(v))
            dmin = Decimal(str(vmin))
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

    # -------------------------------------------------------------------------
    # Helpers: filling candidates
    # -------------------------------------------------------------------------
    def _filling_candidates_from_symbol(self, MT5: Any, info: Any) -> List[int]:
        candidates: List[int] = []
        if FORCE_IOC:
            fm_ioc = getattr(MT5, "ORDER_FILLING_IOC", None)
            if fm_ioc is not None:
                candidates.append(int(fm_ioc))
            return candidates

        try:
            if hasattr(info, "filling_mode") and info.filling_mode is not None:
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

        return candidates

    # -------------------------------------------------------------------------
    # Market order
    # -------------------------------------------------------------------------
    def order_market(
        self,
        req: Union[Dict[str, Any], Any, None] = None,
        *,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        volume: Optional[float] = None,
        magic: int = 2025,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        comment: Optional[str] = "mlsl-exec",
        position: Optional[int] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Abre ordem de mercado BUY/SELL.

        webrequest: não suportado (EA deve executar).
        native: usa MetaTrader5.order_send.
        """
        self._require_native("order_market")
        MT5 = self._mt5()

        self.s.ensure_up()

        if req is not None:
            if hasattr(req, "dict"):
                data = req.dict()
            elif isinstance(req, dict):
                data = dict(req)
            else:
                raise ValueError("invalid request object for order_market")
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
                "correlation_id": correlation_id,
            }

        for key in ("symbol", "side", "volume"):
            if data.get(key) in (None, ""):
                raise ValueError(f"missing field '{key}'")

        sym_req = str(data["symbol"]).strip()
        side_s = str(data["side"]).lower().strip()
        vol_in = float(data["volume"])
        mg = int(data.get("magic", 2025))
        slv = data.get("sl")
        tpv = data.get("tp")
        pos = data.get("position")
        corr = (data.get("correlation_id") or "").strip() or None

        if side_s not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")

        cm_raw = (data.get("comment") or "mlsl-exec")
        if corr:
            cm_raw = f"mlsl-{corr[:12]}"
        cm = sanitize_comment(cm_raw, fallback="mlsl-exec")

        real_sym = self.s.resolve_symbol(sym_req)
        self.s.ensure_symbol(real_sym)

        info = MT5.symbol_info(real_sym)
        if info is None:
            raise RuntimeError("symbol_info returned None")

        tick = MT5.symbol_info_tick(real_sym)
        if tick is None:
            code, msg = MT5.last_error()
            raise RuntimeError(f"symbol_info_tick None ({code},{msg})")

        vol = self._normalize_volume(info, vol_in)

        price = float(tick.ask if side_s == "buy" else tick.bid)
        order_type = MT5.ORDER_TYPE_BUY if side_s == "buy" else MT5.ORDER_TYPE_SELL

        slv = _clean_float(slv)
        tpv = _clean_float(tpv)

        point = float(getattr(info, "point", 0.0) or 0.0)
        stops_level = int(getattr(info, "stops_level", 0) or 0)
        min_dist = stops_level * point if point > 0 else 0.0

        issues: List[str] = []
        sl_before, tp_before = slv, tpv

        if slv is not None:
            if side_s == "buy" and slv >= price:
                issues.append("sl>=price")
                slv = None
            elif side_s == "sell" and slv <= price:
                issues.append("sl<=price")
                slv = None

        if tpv is not None:
            if side_s == "buy" and tpv <= price:
                issues.append("tp<=price")
                tpv = None
            elif side_s == "sell" and tpv >= price:
                issues.append("tp>=price")
                tpv = None

        if min_dist > 0:
            if slv is not None and abs(price - slv) < min_dist:
                issues.append("sl_too_close")
                slv = None
            if tpv is not None and abs(price - tpv) < min_dist:
                issues.append("tp_too_close")
                tpv = None

        stops_sanitized = {
            "price": price,
            "sl_before": sl_before,
            "tp_before": tp_before,
            "sl_after": slv,
            "tp_after": tpv,
            "min_dist": min_dist,
            "issues": issues,
        }

        if issues:
            log.warning("order_market.sanitize_stops symbol=%s side=%s %s", real_sym, side_s, stops_sanitized)

        base_req: Dict[str, Any] = {
            "action": MT5.TRADE_ACTION_DEAL,
            "symbol": real_sym,
            "volume": float(vol),
            "type": order_type,
            "price": price,
            "magic": mg,
            "deviation": _deviation(),
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

        candidates = self._filling_candidates_from_symbol(MT5, info)

        log.info(
            "order_market.fill_candidates symbol=%s side=%s candidates=%s",
            real_sym,
            side_s,
            [_fmt_filling(MT5, x) for x in candidates] or ["NONE"],
        )

        unsupported_fill_codes: set[int] = {10030}
        if hasattr(MT5, "TRADE_RETCODE_INVALID_FILL"):
            unsupported_fill_codes.add(int(MT5.TRADE_RETCODE_INVALID_FILL))

        invalid_stops_codes: set[int] = {130}
        if hasattr(MT5, "TRADE_RETCODE_INVALID_STOPS"):
            invalid_stops_codes.add(int(MT5.TRADE_RETCODE_INVALID_STOPS))

        if candidates:
            check_req = dict(base_req)
            check_req["type_filling"] = candidates[0]
            chk = MT5.order_check(check_req)
            ret = getattr(chk, "retcode", None) if chk is not None else None
            cmt = getattr(chk, "comment", None) if chk is not None else None

            is_unsupported_fill = (ret in unsupported_fill_codes) or (
                isinstance(cmt, str) and "Unsupported filling mode" in cmt
            )

            if chk is not None and ret not in (MT5.TRADE_RETCODE_DONE, 0):
                if is_unsupported_fill:
                    log.warning(
                        "order_market.check_unsupported_fill symbol=%s filling=%s retcode=%s comment=%s",
                        real_sym, _fmt_filling(MT5, candidates[0]), ret, cmt
                    )
                    candidates = candidates[1:]
                elif ret in invalid_stops_codes:
                    code, msg = MT5.last_error()
                    raise RuntimeError(
                        f"order_check invalid_stops retcode={ret} comment={cmt} last_error=({code},{msg}) "
                        f"sl={base_req.get('sl')} tp={base_req.get('tp')}"
                    )
                else:
                    code, msg = MT5.last_error()
                    raise RuntimeError(f"order_check failed retcode={ret} comment={cmt} last_error=({code},{msg})")

        tries: List[Dict[str, Any]] = []
        for fm in candidates or [None]:
            req_payload = dict(base_req)
            if fm is not None:
                req_payload["type_filling"] = int(fm)

            res = MT5.order_send(req_payload)
            if res is None:
                code, msg = MT5.last_error()
                tries.append({"filling": _fmt_filling(MT5, fm), "retcode": None, "last_error": [code, msg]})
                log.warning(
                    "order_market.send_none symbol=%s side=%s filling=%s last_error=%s",
                    real_sym, side_s, _fmt_filling(MT5, fm), [code, msg],
                )
                continue

            d = nt_to_dict(res)
            retcode = d.get("retcode")

            log.info(
                "order_market.send_result symbol=%s side=%s filling=%s retcode=%s deal=%s order=%s comment=%s",
                real_sym, side_s, _fmt_filling(MT5, fm), retcode, d.get("deal"), d.get("order"), d.get("comment")
            )

            if retcode == MT5.TRADE_RETCODE_DONE:
                d["bridge_instance_id"] = bridge_instance_id()
                d["symbol_requested"] = sym_req
                d["symbol_mt5"] = real_sym
                d["side"] = side_s.upper()
                d["volume_requested"] = vol_in
                d["volume_sent"] = vol
                d["price_sent"] = price
                d["comment_sent"] = cm
                d["correlation_id"] = corr
                d["stops_sanitized"] = stops_sanitized
                return d

            tries.append({"filling": _fmt_filling(MT5, fm), "retcode": retcode, "raw": d})

        code, msg = MT5.last_error()
        log.warning(
            "order_market.fail symbol=%s side=%s volume=%s tries=%s last_error=%s",
            real_sym, side_s, vol, tries, [code, msg]
        )
        raise RuntimeError(f"order_send failed (tries={len(tries)}) last_error=({code},{msg}) details={tries}")

    # -------------------------------------------------------------------------
    # Close by ticket
    # -------------------------------------------------------------------------
    def close_ticket(
        self,
        *,
        ticket: int,
        volume: Optional[float] = None,
        deviation: Optional[int] = None,
        comment: str = "mlsl-close",
    ) -> Dict[str, Any]:
        """
        Fecha (total ou parcial) uma posição existente por ticket.
        """
        self._require_native("close_ticket")
        MT5 = self._mt5()

        self.s.ensure_up()

        t = int(ticket)
        pos = MT5.positions_get(ticket=t)
        if not pos:
            allp = MT5.positions_get()
            if allp:
                for p in allp:
                    if int(getattr(p, "ticket", 0) or 0) == t:
                        pos = [p]
                        break

        if not pos:
            code, msg = MT5.last_error()
            raise RuntimeError(f"positions_get(ticket={t}) empty last_error=({code},{msg})")

        p0 = pos[0]
        sym = str(getattr(p0, "symbol", "") or "").strip()
        if not sym:
            raise RuntimeError("position symbol missing")

        real_sym = self.s.resolve_symbol(sym)
        self.s.ensure_symbol(real_sym)

        info = MT5.symbol_info(real_sym)
        if info is None:
            raise RuntimeError("symbol_info returned None")

        tick = MT5.symbol_info_tick(real_sym)
        if tick is None:
            code, msg = MT5.last_error()
            raise RuntimeError(f"symbol_info_tick None ({code},{msg})")

        ptype = int(getattr(p0, "type", 0) or 0)
        pos_vol = float(getattr(p0, "volume", 0.0) or 0.0)
        if pos_vol <= 0:
            raise RuntimeError("position volume <= 0")

        vol_req = pos_vol if volume is None else float(volume)
        if vol_req <= 0:
            raise ValueError("volume must be > 0")

        vol = self._normalize_volume(info, min(vol_req, pos_vol))

        if ptype == 0:
            order_type = MT5.ORDER_TYPE_SELL
            price = float(tick.bid)
        else:
            order_type = MT5.ORDER_TYPE_BUY
            price = float(tick.ask)

        cm = sanitize_comment(comment, fallback="mlsl-close")
        req: Dict[str, Any] = {
            "action": MT5.TRADE_ACTION_DEAL,
            "symbol": real_sym,
            "position": t,
            "type": order_type,
            "volume": float(vol),
            "price": price,
            "deviation": int(deviation) if deviation is not None else _deviation(),
            "type_time": MT5.ORDER_TIME_GTC,
        }
        if cm:
            req["comment"] = cm

        candidates = self._filling_candidates_from_symbol(MT5, info)
        tries: List[Dict[str, Any]] = []

        for fm in candidates or [None]:
            payload = dict(req)
            if fm is not None:
                payload["type_filling"] = int(fm)

            res = MT5.order_send(payload)
            if res is None:
                code, msg = MT5.last_error()
                tries.append({"filling": _fmt_filling(MT5, fm), "retcode": None, "last_error": [code, msg]})
                continue

            d = nt_to_dict(res)
            retcode = d.get("retcode")

            log.info(
                "close_ticket.send_result ticket=%s symbol=%s filling=%s retcode=%s deal=%s order=%s comment=%s",
                t, real_sym, _fmt_filling(MT5, fm), retcode, d.get("deal"), d.get("order"), d.get("comment")
            )

            if retcode == MT5.TRADE_RETCODE_DONE:
                d["bridge_instance_id"] = bridge_instance_id()
                d["ticket"] = t
                d["symbol_mt5"] = real_sym
                d["volume_requested"] = vol_req
                d["volume_sent"] = vol
                d["price_sent"] = price
                d["comment_sent"] = cm
                return d

            tries.append({"filling": _fmt_filling(MT5, fm), "retcode": retcode, "raw": d})

        code, msg = MT5.last_error()
        raise RuntimeError(f"close_ticket failed last_error=({code},{msg}) details={tries}")

    # -------------------------------------------------------------------------
    # Modify SL/TP by ticket
    # -------------------------------------------------------------------------
    def modify_ticket(
        self,
        *,
        ticket: int,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Modifica SL/TP de uma posição por ticket.
        MT5 usa TRADE_ACTION_SLTP com "position".
        """
        self._require_native("modify_ticket")
        MT5 = self._mt5()

        self.s.ensure_up()

        t = int(ticket)
        slv = _clean_float(sl)
        tpv = _clean_float(tp)

        if slv is None and tpv is None:
            raise ValueError("sl or tp required")

        pos = MT5.positions_get(ticket=t)
        if not pos:
            code, msg = MT5.last_error()
            raise RuntimeError(f"positions_get(ticket={t}) empty last_error=({code},{msg})")
        p0 = pos[0]
        sym = str(getattr(p0, "symbol", "") or "").strip()
        if not sym:
            raise RuntimeError("position symbol missing")

        real_sym = self.s.resolve_symbol(sym)
        self.s.ensure_symbol(real_sym)

        req: Dict[str, Any] = {
            "action": MT5.TRADE_ACTION_SLTP,
            "symbol": real_sym,
            "position": t,
        }
        if slv is not None:
            req["sl"] = float(slv)
        if tpv is not None:
            req["tp"] = float(tpv)

        res = MT5.order_send(req)
        if res is None:
            code, msg = MT5.last_error()
            raise RuntimeError(f"modify_ticket order_send None last_error=({code},{msg})")

        d = nt_to_dict(res)
        d["bridge_instance_id"] = bridge_instance_id()
        d["ticket"] = t
        d["symbol_mt5"] = real_sym
        d["sl_sent"] = slv
        d["tp_sent"] = tpv

        retcode = d.get("retcode")
        if retcode != MT5.TRADE_RETCODE_DONE:
            code, msg = MT5.last_error()
            raise RuntimeError(f"modify_ticket failed retcode={retcode} last_error=({code},{msg}) details={d}")

        return d

    # compat: alguns controllers chamam modify_position
    def modify_position(self, ticket: int, sl: float | None = None, tp: float | None = None) -> Dict[str, Any]:
        return self.modify_ticket(ticket=ticket, sl=sl, tp=tp)