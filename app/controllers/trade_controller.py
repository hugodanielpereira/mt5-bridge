# app/controllers/trade_controller.py
from __future__ import annotations

from typing import Optional
import os

from fastapi import APIRouter, Header, Query, Depends, HTTPException
import traceback
import MetaTrader5 as mt5

from app.shareweb import require_api_key
from app.services.mt5 import MT5Service
from app.models.market_order import MarketOrder  # se ainda usares noutros sítios

router = APIRouter(tags=["trade"])

# Light auth (same pattern as other controllers)
def _require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    expected = os.getenv("X_API_KEY") or os.getenv("BRIDGE_API_KEY") or os.getenv("API_KEY")
    if expected and (not x_api_key or x_api_key != expected):
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


# ====================== ACCOUNT / INFO ======================

@router.get("/account")
@router.get("/account_info")
def account_info(_: None = Depends(_require_api_key)):
    """
    Devolve info da conta atual (saldo, equity, margem, etc.)
    """
    svc = MT5Service()
    svc.ensure_up()
    try:
        return svc.account_info()
    except Exception as e:
        code, msg = mt5.last_error()
        raise HTTPException(
            status_code=500,
            detail={
                "error": "account_info failed",
                "exc": str(e),
                "last_error": [code, msg],
            },
        )


# ====================== POSITIONS ===========================

@router.get("/positions")
def positions(symbol: Optional[str] = Query(None), x_api_key: Optional[str] = Header(None)):
    require_api_key(x_api_key)
    svc = MT5Service()
    svc.ensure_up()
    return svc.positions(symbol)


# ====================== MARKET ORDERS =======================

@router.post("/order_market")
def order_market(payload: dict, _: None = Depends(_require_api_key)):
    """
    Abre ordem de mercado (BUY/SELL) usando MT5Service.order_market
    Payload típico:
      {
        "symbol": "EURUSD",
        "side": "BUY",
        "volume": 0.01,
        "sl": 1.0800,
        "tp": 1.0900,
        "comment": "mlsl-exec"
      }
    """
    try:
        svc = MT5Service()
        res = svc.order_market(payload)
        return res
    except HTTPException:
        # re-raise clean 4xx com detail preparado pelo service
        raise
    except Exception as e:
        code, msg = mt5.last_error()
        raise HTTPException(
            status_code=400,
            detail={
                "error": "order_market crashed",
                "exc": str(e),
                "last_error": [code, msg],
                "trace": traceback.format_exc().splitlines()[-8:],  # tail
            },
        )


# ====================== MODIFY / CLOSE ======================

@router.post("/order_modify")
def order_modify(payload: dict, _: None = Depends(_require_api_key)):
    """
    Modifica SL/TP de uma posição aberta (por ticket).
    Payload:
      {
        "ticket": 12345678,
        "sl": 1.0800,   # opcional
        "tp": 1.0900    # opcional
      }
    """
    ticket = payload.get("ticket")
    if ticket is None:
        raise HTTPException(status_code=400, detail="field 'ticket' is required")

    try:
        # permitir ticket como string ou int
        ticket_i = int(ticket)
    except Exception:
        raise HTTPException(status_code=400, detail="field 'ticket' must be int")

    sl = payload.get("sl")
    tp = payload.get("tp")

    # deixar None passar para “mantém valor actual”
    try:
        svc = MT5Service()
        svc.ensure_up()
        res = svc.modify_position(ticket=ticket_i, sl=sl, tp=tp)
        return res
    except HTTPException:
        raise
    except Exception as e:
        code, msg = mt5.last_error()
        raise HTTPException(
            status_code=400,
            detail={
                "error": "order_modify crashed",
                "exc": str(e),
                "last_error": [code, msg],
                "trace": traceback.format_exc().splitlines()[-8:],
            },
        )


@router.post("/order_close")
def order_close(payload: dict, _: None = Depends(_require_api_key)):
    """
    Fecha uma posição pelo ticket.

    Suporta fecho total ou parcial:
      {
        "ticket": 12345678,
        "volume": 0.02,        # opcional; se omitido ou >= volume da posição → fecha tudo
        "symbol": "EURUSD",    # opcional (ignorado aqui, usado só para logging no caller)
        "side": "BUY"          # opcional (ignorado aqui)
      }

    É o endpoint que o PositionManager deve usar.
    """
    ticket = payload.get("ticket")
    if ticket is None:
        raise HTTPException(status_code=400, detail="field 'ticket' is required")

    try:
        ticket_i = int(ticket)
    except Exception:
        raise HTTPException(status_code=400, detail="field 'ticket' must be int")

    volume = payload.get("volume")

    # volume pode ser None → fecho total; se vier, tem de ser float válido
    volume_f: Optional[float]
    if volume is None:
        volume_f = None
    else:
        try:
            volume_f = float(volume)
        except Exception:
            raise HTTPException(status_code=400, detail="field 'volume' must be float if provided")

    try:
        svc = MT5Service()
        svc.ensure_up()
        return svc.close_ticket(ticket_i, volume_f)
    except HTTPException:
        raise
    except Exception as e:
        code, msg = mt5.last_error()
        raise HTTPException(
            status_code=400,
            detail={
                "error": "order_close crashed",
                "exc": str(e),
                "last_error": [code, msg],
                "trace": traceback.format_exc().splitlines()[-8:],
            },
        )


@router.post("/close_symbol")
def close_symbol(symbol: str, x_api_key: Optional[str] = Header(None)):
    """
    Fecha todas as posições de um símbolo (mantido para compatibilidade).
    """
    require_api_key(x_api_key)
    svc = MT5Service()
    svc.ensure_up()
    return svc.close_symbol(symbol)


@router.post("/close_ticket")
def close_ticket(ticket: int, x_api_key: Optional[str] = Header(None)):
    """
    Fecha uma posição por ticket (mantido para compatibilidade).

    Aqui é sempre fecho total; para fecho parcial usa /order_close.
    """
    require_api_key(x_api_key)
    svc = MT5Service()
    svc.ensure_up()
    return svc.close_ticket(ticket)


# ====================== OHLCV / MARKET DATA =================

@router.get("/ohlcv")
def ohlcv(
    symbol: str = Query(..., description="símbolo pedido (ex.: EURUSD, XAUUSD, BTCUSD)"),
    tf: str = Query("H1", description="timeframe MT5 (M1,M5,M15,M30,H1,H4,D1,...)"),
    start: Optional[str] = Query(
        default=None,
        description="início (ISO-8601 UTC, ex.: 2025-01-01T00:00:00Z). Se usado, precisa também de 'end'.",
    ),
    end: Optional[str] = Query(
        default=None,
        description="fim (ISO-8601 UTC). Se usado, precisa também de 'start'.",
    ),
    limit: int = Query(1000, ge=1, le=5000),
    _: None = Depends(_require_api_key),
):
    """
    OHLCV direto do MT5, resolvendo sufixos / aliases via MarketData.
    Se 'start' e 'end' forem fornecidos → usa copy_rates_range.
    Caso contrário → copy_rates_from_pos com 'limit' barras.
    """
    svc = MT5Service()
    svc.ensure_up()
    try:
        data = svc.ohlcv(symbol=symbol, tf=tf, start=start, end=end, limit=limit)
        return data
    except Exception as e:
        code, msg = mt5.last_error()
        raise HTTPException(
            status_code=400,
            detail={
                "error": "ohlcv failed",
                "exc": str(e),
                "last_error": [code, msg],
            },
        )


# ====================== DEALS RECENT ========================

@router.get("/deals_recent")
def deals_recent(days: int = Query(1, ge=0, le=30), x_api_key: Optional[str] = Header(None)):
    require_api_key(x_api_key)
    svc = MT5Service()
    svc.ensure_up()
    return svc.deals_recent(days=days)