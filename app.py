# app.py
from __future__ import annotations
import os, time, subprocess
from datetime import datetime
from typing import Optional, Dict, Any, List

import pandas as pd
from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# -----------------------------------------------------------------------------
# Env / constants
# -----------------------------------------------------------------------------
load_dotenv()
API_KEY   = os.getenv("API_KEY", "change-me")  # <-- vem do .env
ALLOW_CORS = os.getenv("ALLOW_CORS", "0").lower() in ("1", "true", "yes", "on")

# MetaTrader5 (só Windows)
try:
    import MetaTrader5 as MT5
except Exception as e:
    raise RuntimeError(
        "Falha a importar MetaTrader5. Corre este bridge no Windows com MT5 instalado. "
        f"Erro original: {e!r}"
    )

# -----------------------------------------------------------------------------
# FastAPI app
# -----------------------------------------------------------------------------
app = FastAPI(title="MT5 Bridge", version="0.3.0")

if ALLOW_CORS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"], allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"],
    )

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _auth(x_api_key: Optional[str]):
    """API key simples via header X-API-Key."""
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="unauthorized")

def _mt5_initialize(max_wait_sec: int = 45) -> None:
    """
    Inicializa MT5 e (se houver credenciais no .env) faz login.
    Tenta arrancar o terminal em /portable antes de initialize() e faz retries.
    """
    term  = os.getenv("MT5_TERMINAL_PATH")   # ex.: C:\Trading\mt5\MT5_ICMarkets\terminal64.exe
    login = os.getenv("MT5_LOGIN")
    pwd   = os.getenv("MT5_PASSWORD")
    srv   = os.getenv("MT5_SERVER")

    # fecha sessão antiga
    try: MT5.shutdown()
    except Exception: pass

    # tenta arrancar o terminal (idempotente)
    if term and os.path.exists(term):
        try:
            subprocess.Popen([term, "/portable"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f"[MT5] Aviso: não consegui arrancar o terminal: {e}")

    # initialize com retries (mitiga IPC timeout)
    t0 = time.time()
    attempt = 0
    while True:
        attempt += 1
        ok = MT5.initialize(path=term) if term else MT5.initialize()
        if ok:
            break
        code, msg = MT5.last_error()
        print(f"[MT5] initialize falhou (tentativa {attempt}): {code} {msg}")
        if time.time() - t0 > max_wait_sec:
            raise RuntimeError(f"MT5.initialize failed after {attempt} attempts: {code} {msg}")
        time.sleep(2.0)

    # login (se credenciais fornecidas)
    if login and pwd and srv:
        if not MT5.login(int(login), password=pwd, server=srv):
            code, msg = MT5.last_error()
            raise RuntimeError(f"MT5.login falhou: {code} {msg}")

    ti = MT5.terminal_info()
    if ti is None:
        code, msg = MT5.last_error()
        raise RuntimeError(f"terminal_info None: {code} {msg}")
    print("[MT5] terminal_info:", ti)

def _ensure_up():
    """Reinicializa MT5 se a sessão tiver caído."""
    if not MT5.terminal_info():
        try: MT5.shutdown()
        except Exception: pass
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
    df["timestamp"] = pd.to_datetime(df["time"], unit="s", utc=True)
    if "tick_volume" in df.columns:
        df.rename(columns={"tick_volume":"volume"}, inplace=True)
    cols = [c for c in ["timestamp","open","high","low","close","volume"] if c in df.columns]
    return df[cols].drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)

# TF string -> enum MT5
TF_MAP = {
    "M1": MT5.TIMEFRAME_M1, "M5": MT5.TIMEFRAME_M5, "M15": MT5.TIMEFRAME_M15,
    "M30": MT5.TIMEFRAME_M30, "H1": MT5.TIMEFRAME_H1, "H4": MT5.TIMEFRAME_H4,
    "D1": MT5.TIMEFRAME_D1,
}

def _close_positions_by_symbol(symbol: str) -> List[Dict[str, Any]]:
    """Fecha todas as posições de um símbolo (hedging: fecha por ticket)."""
    _ensure_symbol(symbol)
    resps: List[Dict[str, Any]] = []
    positions = MT5.positions_get(symbol=symbol) or []
    for p in positions:
        d = _namedtuple_to_dict(p)
        ticket = d.get("ticket")
        volume = float(d.get("volume", 0) or 0)
        if not ticket or volume <= 0:
            continue
        side_close = MT5.ORDER_TYPE_SELL if d.get("position_type") == 0 else MT5.ORDER_TYPE_BUY
        tick = MT5.symbol_info_tick(symbol)
        price = tick.bid if side_close == MT5.ORDER_TYPE_SELL else tick.ask
        req = {
            "action": MT5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": side_close,
            "price": float(price),
            "magic": 2025,
            "deviation": 20,
            "comment": "close_symbol",
            "type_time": MT5.ORDER_TIME_GTC,
            "type_filling": MT5.ORDER_FILLING_IOC,
            "position": int(ticket),  # chave para fechar por ticket
        }
        r = MT5.order_send(req)
        resps.append(_namedtuple_to_dict(r) if r else {"error": "order_send returned None", "ticket": ticket})
    return resps

# -----------------------------------------------------------------------------
# Lifecycle
# -----------------------------------------------------------------------------
@app.on_event("startup")
def _startup():
    _mt5_initialize(max_wait_sec=45)

@app.on_event("shutdown")
def _shutdown():
    try: MT5.shutdown()
    except Exception: pass

# -----------------------------------------------------------------------------
# Schemas
# -----------------------------------------------------------------------------
class MarketOrder(BaseModel):
    symbol: str
    side: str            # "buy" | "sell"
    volume: float        # lotes
    magic: int = 2025
    sl: Optional[float] = None
    tp: Optional[float] = None
    comment: Optional[str] = "mt5-bridge"
    position: Optional[int] = None   # ticket (para fechar em hedging)

# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------
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
    for k, v in list(d.items()):
        if isinstance(v, (pd.Timestamp,)):
            d[k] = str(v)
    return d

@app.get("/positions")
def positions(symbol: str | None = Query(None), x_api_key: Optional[str] = Header(None)):
    _auth(x_api_key)
    _ensure_up()
    # se vier symbol -> o próprio MT5 filtra; caso contrário devolve todas
    pos = MT5.positions_get(symbol=symbol) if symbol else MT5.positions_get()
    out = []
    for p in (pos or []):
        d = _namedtuple_to_dict(p)
        # normaliza chave para JSON limpinho
        out.append({
            "ticket": d.get("ticket"),
            "symbol": d.get("symbol"),
            "type": d.get("type"),               # 0=BUY,1=SELL
            "volume": d.get("volume"),
            "price_open": d.get("price_open"),
            "price_current": d.get("price_current"),
            "sl": d.get("sl"),
            "tp": d.get("tp"),
            "magic": d.get("magic"),
            "comment": d.get("comment"),
        })
    return out

@app.get("/symbols")
def symbols(x_api_key: Optional[str] = Header(None)):
    _auth(x_api_key)
    _ensure_up()
    infos = MT5.symbols_get()
    out = []
    for inf in infos or []:
        d = _namedtuple_to_dict(inf)
        if d.get("visible"):
            out.append({"name": d["name"], "path": d.get("path"), "trade_mode": d.get("trade_mode")})
    return sorted(out, key=lambda x: x["name"])

@app.get("/ohlcv")
def ohlcv(
    symbol: str,
    tf: str = Query("H1"),
    start: Optional[str] = None,   # "2024-01-01 00:00:00+00:00"
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
    return df.to_dict(orient="records")

@app.post("/order_market")
def order_market(req: MarketOrder, x_api_key: Optional[str] = Header(None)):
    """
    Envia uma ordem de mercado.
    - side: "buy" | "sell"
    - volume: lotes
    - position: (opcional) ticket da posição a fechar em contas hedging
    """
    _auth(x_api_key)
    _ensure_up()

    side = req.side.lower().strip()
    if side not in ("buy", "sell"):
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
    # SL/TP opcionais
    if req.sl is not None:
        request["sl"] = float(req.sl)
    if req.tp is not None:
        request["tp"] = float(req.tp)

    # ESSENCIAL para contas hedging quando queres fechar uma posição específica
    if req.position is not None:
        request["position"] = int(req.position)

    res = MT5.order_send(request)
    if res is None:
        raise HTTPException(500, "order_send returned None")

    d = _namedtuple_to_dict(res)
    if d.get("retcode") != MT5.TRADE_RETCODE_DONE:
        # devolve o payload completo do MT5 para debugging (inclui retcode, comment, etc.)
        raise HTTPException(500, {"error": "order failed", "mt5": d})

    return d

@app.get("/diag")
def diag(x_api_key: Optional[str] = Header(None)):
    _auth(x_api_key)
    info = MT5.terminal_info()
    acct = MT5.account_info()
    le   = MT5.last_error()
    return {
        "terminal_info": _namedtuple_to_dict(info) if info else None,
        "account_info":  _namedtuple_to_dict(acct) if acct else None,
        "last_error":    le,
    }

@app.post("/close_symbol")
def close_symbol(symbol: str, x_api_key: Optional[str] = Header(None)):
    _auth(x_api_key); _ensure_up(); _ensure_symbol(symbol)
    pos = MT5.positions_get(symbol=symbol) or []
    results = []
    for p in pos:
        side = "sell" if p.type == MT5.ORDER_TYPE_BUY else "buy"
        req = {
            "action": MT5.TRADE_ACTION_DEAL,
            "symbol": p.symbol,
            "volume": float(p.volume),
            "type": MT5.ORDER_TYPE_SELL if side=="sell" else MT5.ORDER_TYPE_BUY,
            "price": float(MT5.symbol_info_tick(p.symbol).bid if side=="sell" else MT5.symbol_info_tick(p.symbol).ask),
            "deviation": 20, "magic": int(p.magic or 2025), "comment": "close_symbol",
            "type_time": MT5.ORDER_TIME_GTC, "type_filling": MT5.ORDER_FILLING_IOC,
            "position": int(p.ticket),
        }
        r = MT5.order_send(req)
        results.append({"ticket": p.ticket, "retcode": getattr(r,"retcode",None), "raw": _namedtuple_to_dict(r) if r else None})
    return {"closed": results}

@app.post("/close_ticket")
def close_ticket(ticket: int, x_api_key: Optional[str] = Header(None)):
    _auth(x_api_key); _ensure_up()
    p = next((pp for pp in (MT5.positions_get() or []) if pp.ticket==ticket), None)
    if not p: raise HTTPException(404, f"position {ticket} not found")
    side = "sell" if p.type == MT5.ORDER_TYPE_BUY else "buy"
    tick = MT5.symbol_info_tick(p.symbol)
    price = tick.bid if side=="sell" else tick.ask
    req = {"action": MT5.TRADE_ACTION_DEAL,"symbol": p.symbol,"volume": float(p.volume),
           "type": MT5.ORDER_TYPE_SELL if side=="sell" else MT5.ORDER_TYPE_BUY,
           "price": float(price),"deviation":20,"magic": int(p.magic or 2025),
           "comment":"close_ticket","type_time": MT5.ORDER_TIME_GTC,
           "type_filling": MT5.ORDER_FILLING_IOC,"position": int(p.ticket)}
    r = MT5.order_send(req)
    d = _namedtuple_to_dict(r) if r else None
    if not r or d.get("retcode")!=MT5.TRADE_RETCODE_DONE:
        raise HTTPException(500, {"error":"close failed", "mt5": d})
    return d