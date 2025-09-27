# app.py
from __future__ import annotations
import os
from datetime import datetime
from typing import Optional, List, Dict, Any

import pandas as pd
from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# ---- env / constants ---------------------------------------------------------
load_dotenv()
API_KEY = os.getenv("API_KEY", "change-me")
ALLOW_CORS = os.getenv("ALLOW_CORS", "0") in ("1", "true", "yes", "on")

# MetaTrader5 só existe “oficialmente” no Windows
try:
    import MetaTrader5 as MT5
except Exception as e:
    raise RuntimeError(
        "Falha a importar MetaTrader5. Este bridge deve correr em Windows "
        "com o MetaTrader 5 instalado. Erro original: %r" % (e,)
    )

# ---- FastAPI -----------------------------------------------------------------
app = FastAPI(title="MT5 Bridge", version="0.2.0")

if ALLOW_CORS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# ---- helpers -----------------------------------------------------------------
def _auth(x_api_key: Optional[str]):
    """API key simples via header X-API-Key."""
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="unauthorized")

def _mt5_initialize():
    """Inicializa MT5 + (opcional) login com credenciais do .env."""
    term = os.getenv("MT5_TERMINAL_PATH")  # opcional
    ok = MT5.initialize(term) if term else MT5.initialize()
    if not ok:
        raise RuntimeError(f"MT5.initialize failed: {MT5.last_error()}")

    login = os.getenv("MT5_LOGIN")
    pwd   = os.getenv("MT5_PASSWORD")
    srv   = os.getenv("MT5_SERVER")

    # Se não forneces credenciais, assume terminal já logado.
    if login and pwd and srv:
        if not MT5.login(int(login), password=pwd, server=srv):
            raise RuntimeError(f"MT5.login failed: {MT5.last_error()}")

def _ensure_up():
    """Reinicializa MT5 se a sessão tiver caído."""
    if not MT5.terminal_info():
        try:
            MT5.shutdown()
        except Exception:
            pass
        _mt5_initialize()

def _ensure_symbol(symbol: str):
    info = MT5.symbol_info(symbol)
    if info is None:
        raise HTTPException(400, f"symbol '{symbol}' not found")
    if not info.visible:
        if not MT5.symbol_select(symbol, True):
            raise HTTPException(500, f"symbol_select failed for '{symbol}'")

def _namedtuple_to_dict(obj) -> Dict[str, Any]:
    return obj._asdict() if hasattr(obj, "_asdict") else dict(obj)

def _df_from_rates(rates) -> pd.DataFrame:
    if rates is None or len(rates) == 0:
        return pd.DataFrame(columns=["timestamp","open","high","low","close","volume"])
    df = pd.DataFrame(rates)
    # “time” vem em segundos epoch (UTC)
    df["timestamp"] = pd.to_datetime(df["time"], unit="s", utc=True)
    # MT5 chama volume de tick_volume
    if "tick_volume" in df.columns:
        df.rename(columns={"tick_volume":"volume"}, inplace=True)
    # ordena e seleciona
    cols = [c for c in ["timestamp","open","high","low","close","volume"] if c in df.columns]
    df = df[cols].drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    return df

# Mapa TF string -> enum MT5
TF_MAP = {
    "M1": MT5.TIMEFRAME_M1, "M5": MT5.TIMEFRAME_M5, "M15": MT5.TIMEFRAME_M15,
    "M30": MT5.TIMEFRAME_M30, "H1": MT5.TIMEFRAME_H1, "H4": MT5.TIMEFRAME_H4,
    "D1": MT5.TIMEFRAME_D1,
}

# ---- lifecycle ---------------------------------------------------------------
@app.on_event("startup")
def _startup():
    _mt5_initialize()

@app.on_event("shutdown")
def _shutdown():
    try:
        MT5.shutdown()
    except Exception:
        pass

# ---- schemas -----------------------------------------------------------------
class MarketOrder(BaseModel):
    symbol: str
    side: str            # "buy" | "sell"
    volume: float        # lotes (depende do símbolo/conta)
    magic: int = 2025
    sl: Optional[float] = None
    tp: Optional[float] = None
    comment: Optional[str] = "mt5-bridge"

# ---- endpoints ---------------------------------------------------------------
@app.get("/ping")
def ping(x_api_key: Optional[str] = Header(None)):
    _auth(x_api_key)
    return {"ok": True, "ts": datetime.utcnow().isoformat() + "Z"}

@app.get("/account")
def account(x_api_key: Optional[str] = Header(None)):
    _auth(x_api_key)
    _ensure_up()
    info = MT5.account_info()
    if info is None:
        raise HTTPException(500, "no account info (not logged in?)")
    d = _namedtuple_to_dict(info)
    # torna JSON-serializável
    for k, v in list(d.items()):
        if isinstance(v, (pd.Timestamp, )):
            d[k] = str(v)
    return d

@app.get("/positions")
def positions(x_api_key: Optional[str] = Header(None)):
    _auth(x_api_key)
    _ensure_up()
    pos = MT5.positions_get()
    return [_namedtuple_to_dict(p) for p in (pos or [])]

@app.get("/ohlcv")
def ohlcv(
    symbol: str,
    tf: str = Query("H1"),          # validaremos abaixo
    start: Optional[str] = None,    # ISO: "2024-01-01 00:00:00+00:00" ou "2024-01-01"
    end:   Optional[str] = None,
    limit: int = 1000,
    x_api_key: Optional[str] = Header(None),
):
    _auth(x_api_key)
    _ensure_up()

    tf = tf.upper()
    if tf not in TF_MAP:
        raise HTTPException(400, f"invalid tf '{tf}' (use one of {list(TF_MAP)})")

    _ensure_symbol(symbol)
    tf_enum = TF_MAP[tf]

    # range por tempo (preferível), senão últimos N
    if start or end:
        if not (start and end):
            raise HTTPException(400, "provide both 'start' and 'end' for range mode")
        t0 = pd.Timestamp(start, tz="UTC").to_pydatetime()
        t1 = pd.Timestamp(end,   tz="UTC").to_pydatetime()
        rates = MT5.copy_rates_range(symbol, tf_enum, t0, t1)
    else:
        rates = MT5.copy_rates_from_pos(symbol, tf_enum, 0, int(limit))

    df = _df_from_rates(rates)
    if df.empty:
        return []
    df = df.assign(symbol=symbol, timeframe=tf)
    # devolve como lista de dicts
    return df.to_dict(orient="records")

@app.post("/order_market")
def order_market(req: MarketOrder, x_api_key: Optional[str] = Header(None)):
    _auth(x_api_key)
    _ensure_up()

    side = req.side.lower().strip()
    if side not in ("buy","sell"):
        raise HTTPException(400, "side must be 'buy' or 'sell'")

    _ensure_symbol(req.symbol)
    tick = MT5.symbol_info_tick(req.symbol)
    if tick is None:
        raise HTTPException(500, "symbol_info_tick returned None")

    price = tick.ask if side == "buy" else tick.bid
    order_type = MT5.ORDER_TYPE_BUY if side == "buy" else MT5.ORDER_TYPE_SELL

    request = {
        "action": MT5.TRADE_ACTION_DEAL,
        "symbol": req.symbol,
        "volume": float(req.volume),
        "type": order_type,
        "price": float(price),
        "magic": int(req.magic),
        "deviation": 20,
        "comment": req.comment or "mt5-bridge",
        "type_time": MT5.ORDER_TIME_GTC,
        "type_filling": MT5.ORDER_FILLING_IOC,
    }
    if req.sl is not None: request["sl"] = float(req.sl)
    if req.tp is not None: request["tp"] = float(req.tp)

    res = MT5.order_send(request)
    if res is None:
        raise HTTPException(500, "order_send returned None")
    d = _namedtuple_to_dict(res)
    if d.get("retcode") != MT5.TRADE_RETCODE_DONE:
        # devolve tudo o que MT5 reportar para debug
        raise HTTPException(500, {"error": "order failed", "mt5": d})
    return d