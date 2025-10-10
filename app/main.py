# app/main.py
from __future__ import annotations
import os
import logging
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, Query
from dotenv import load_dotenv

# importa a façade, que reexporta MT5Service a partir de app/services/mt5/__init__.py
from app.services.mt5 import MT5Service
from app.controllers.trade_controller import router as trade_router
from app.controllers.data_controller import router as data_router
from app.controllers.account_controller import router as account_router
from app.controllers.history_controller import router as history_router
from app.controllers.debug_controller import router as debug_router

load_dotenv()

def _setup_logging_for_bridge() -> None:
    lvl = os.getenv("LOG_LEVEL", "INFO").upper()
    fmt = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"

    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(lvl)

    log_dir = Path(os.getenv("LOG_DIR", "./logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    logfile = log_dir / "bridge.log"

    rotate_mode = os.getenv("LOG_ROTATE", "size").lower()
    if rotate_mode == "time":
        when = os.getenv("LOG_WHEN", "midnight")
        backups = int(os.getenv("LOG_BACKUPS", "14"))
        fh = TimedRotatingFileHandler(str(logfile), when=when, interval=1, backupCount=backups, encoding="utf-8", utc=True)
    else:
        max_bytes = int(os.getenv("LOG_MAX_BYTES", str(5_000_000)))
        backups = int(os.getenv("LOG_BACKUPS", "3"))
        fh = RotatingFileHandler(str(logfile), maxBytes=max_bytes, backupCount=backups, encoding="utf-8")

    fh.setFormatter(logging.Formatter(fmt))
    fh.setLevel(lvl)
    root.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter(fmt))
    ch.setLevel(lvl)
    root.addHandler(ch)

    logging.getLogger("bridge").info(f"logger ready (file={logfile}, level={lvl}, rotate={rotate_mode})")

_setup_logging_for_bridge()

app = FastAPI(
    title="MT5 Bridge MVC",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
    openapi_url="/openapi.json",
)

@app.on_event("startup")
def startup():
    svc = MT5Service()
    ok = bool(svc.initialize())
    app.state.mt5 = svc
    log = logging.getLogger("bridge")
    strict = os.getenv("BRIDGE_STRICT_STARTUP","0").lower() in ("1","true","yes","on")
    if ok:
        log.info("[MT5] ligado com sucesso")
    else:
        msg = "[MT5] falha ao ligar — a API arranca na mesma; usa /health?verbose=1 para ver estado."
        if strict:
            raise RuntimeError(msg)
        log.error(msg)

@app.get("/ping")
def ping():
    return {"ok": True}

@app.get("/health")
def health(verbose: int = Query(0, ge=0, le=1)):
    svc = getattr(app.state, "mt5", None)
    if not svc:
        return {"ok": False, "reason": "svc_missing"}
    try:
        info = svc.diag()
    except Exception as e:
        return {"ok": False, "reason": f"diag_failed: {e}"}

    if not info.get("ok"):
        return {"ok": False, "diag": info}

    if verbose:
        try:
            pos = svc.positions()
            info["open_positions"] = len(pos)
        except Exception:
            info["open_positions"] = None
    return {"ok": True, "diag": info}

# Routers
app.include_router(trade_router)
app.include_router(data_router)
app.include_router(account_router)
app.include_router(history_router)
app.include_router(debug_router)