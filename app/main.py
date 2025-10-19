# app/main.py
from __future__ import annotations
import os, logging
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Query, Depends
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware

from app.shareweb import set_service, require_api_key
from app.services.mt5 import MT5Service

# Routers core (API) — protegidos com X-API-Key
from app.controllers.trade_controller import router as trade_router
from app.controllers.data_controller import router as data_router
from app.controllers.account_controller import router as account_router
from app.controllers.history_controller import router as history_router
from app.controllers.debug_controller import router as debug_router

# APIs do painel (usam prefixo /ui/api DENTRO dos ficheiros)
from app.ui.routes.api_dash import router as api_dash_router
from app.ui.routes.api_procs import router as api_procs_router
from app.ui.routes.control import router as ops_router   # atenção: este usa prefixo /ui/ops
from app.ui.routes import health_api
from app.ui.routes.health_api import router as ui_health_router


load_dotenv()

# -----------------------------
# Logging
# -----------------------------
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
    fh = TimedRotatingFileHandler(
      str(logfile), when=os.getenv("LOG_WHEN","midnight"),
      interval=1, backupCount=int(os.getenv("LOG_BACKUPS","14")),
      encoding="utf-8", utc=True
    )
  else:
    fh = RotatingFileHandler(
      str(logfile),
      maxBytes=int(os.getenv("LOG_MAX_BYTES","5000000")),
      backupCount=int(os.getenv("LOG_BACKUPS","3")),
      encoding="utf-8"
    )
  fh.setFormatter(logging.Formatter(fmt)); fh.setLevel(lvl); root.addHandler(fh)

  ch = logging.StreamHandler()
  ch.setFormatter(logging.Formatter(fmt)); ch.setLevel(lvl); root.addHandler(ch)

  logging.getLogger("bridge").info("logger ready (file=%s, level=%s, rotate=%s)", logfile, lvl, rotate_mode)

_setup_logging_for_bridge()

app = FastAPI(
  title="MT5 Bridge MVC",
  version="1.0.0",
  docs_url="/docs",
  redoc_url=None,
  openapi_url="/openapi.json",
)

# -----------------------------
# CORS (UI em Flask:5000)
# -----------------------------
app.add_middleware(
  CORSMiddleware,
  allow_origins=["http://127.0.0.1:5000", "http://localhost:5000"],
  allow_credentials=True,
  allow_methods=["*"],    # inclui OPTIONS
  allow_headers=["*"],    # ex: X-API-Key, Content-Type
  expose_headers=["*"],
)

# ⚠️ IMPORTANTE:
# NADA de app.mount("/ui", ...) nem app.mount("/ui/static", ...)
# A UI é servida pelo Flask. Aqui só ficam as APIs.

# -----------------------------
# Redirects convenientes / legados (opcional)
# -----------------------------
@app.get("/", include_in_schema=False)
def root():
  # Se preferires, podes devolver {"ok": True} em vez de redirect
  return RedirectResponse(url="/docs")

@app.get("/index.html", include_in_schema=False)
def root_index():
  return RedirectResponse(url="/docs")

@app.get("/ops", include_in_schema=False)
@app.get("/ops/", include_in_schema=False)
def legacy_ops_redirect():
  # Mantém para compat; UI corre em :5000.
  return RedirectResponse(url="/docs", status_code=307)

@app.get("/ops/{subpath:path}", include_in_schema=False)
def legacy_ops_subpaths(subpath: str):
  return RedirectResponse(url="/docs", status_code=307)

@app.get("/control", include_in_schema=False)
@app.get("/control/", include_in_schema=False)
def legacy_control_redirect():
  return RedirectResponse(url="/docs", status_code=307)

@app.get("/control/{subpath:path}", include_in_schema=False)
def legacy_control_subpaths(subpath: str):
  return RedirectResponse(url="/docs", status_code=307)

# -----------------------------
# Routers de API (X-API-Key)
# -----------------------------
app.include_router(trade_router,   dependencies=[Depends(require_api_key)])
app.include_router(data_router,    dependencies=[Depends(require_api_key)])
app.include_router(account_router, dependencies=[Depends(require_api_key)])
app.include_router(history_router, dependencies=[Depends(require_api_key)])
app.include_router(debug_router,   dependencies=[Depends(require_api_key)])

# -----------------------------
# APIs do painel (prefixos estão definidos nos próprios routers)
# -----------------------------
# api_dash_router  -> prefix="/ui/api"     (tem /strategies, /schedule_yaml, /executor_state, /scheduler_state, retrain_*)
# api_procs_router -> prefix="/ui/api"     (proc_run)
# ops_router       -> prefix="/ui/ops"     (ops/run)
app.include_router(ui_health_router)
app.include_router(api_dash_router)
app.include_router(api_procs_router)
app.include_router(ops_router)

# -----------------------------
# Health & ping
# -----------------------------
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

# -----------------------------
# Lifecycle MT5
# -----------------------------
@app.on_event("startup")
def startup():
  svc = MT5Service()
  ok = bool(svc.initialize())
  app.state.mt5 = svc
  set_service(svc)

  strict = os.getenv("BRIDGE_STRICT_STARTUP","0").lower() in ("1","true","yes","on")
  try:
    import MetaTrader5 as MT5  # type: ignore
    ti = MT5.terminal_info()
    build = getattr(ti, "build", None) if ti else None
    data_path = getattr(ti, "data_path", None) if ti else None
  except Exception:
    build = data_path = None
  logging.getLogger("bridge").info(
    "[MT5] env check: exe=%s | args=%s | login=%s | server=%s | build=%s | data_path=%s | require_portable=%s",
    getattr(svc, "exe_path", None), getattr(svc, "exe_args", None),
    getattr(svc, "login", None), getattr(svc, "server", None),
    build, data_path, os.getenv("REQUIRE_PORTABLE", "0"),
  )
  if ok:
    logging.getLogger("bridge").info("[MT5] ligado com sucesso")
  else:
    msg = "[MT5] falha ao ligar — a API arranca na mesma; usa /health?verbose=1 para ver estado."
    if strict:
      raise RuntimeError(msg)
    logging.getLogger("bridge").error(msg)

@app.on_event("shutdown")
def shutdown():
  try:
    import MetaTrader5 as MT5  # type: ignore
    MT5.shutdown()
    logging.getLogger("bridge").info("[MT5] shutdown() chamado")
  except Exception:
    pass