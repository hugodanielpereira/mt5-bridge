from __future__ import annotations

import os
import logging
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from pathlib import Path
import json
import time
from datetime import datetime, timezone

from fastapi import FastAPI, Query, Depends
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware

from app.common.env_loader import load_instance_env
from app.common.service_registry import set_service, clear_service
from app.common.auth import require_api_key
from app.services.mt5 import MT5Service
from app.services.mt5.state_store import STORE

# Routers core (API)
from app.controllers.marketdata_controller import router as marketdata_router
from app.controllers.trade_controller import router as trade_router
from app.controllers.ea_controller import router as ea_router
from app.controllers.data_controller import router as data_router
from app.controllers.account_controller import router as account_router
from app.controllers.history_controller import router as history_router
from app.controllers.debug_controller import router as debug_router
from app.controllers.meta_controller import router as meta_router
from app.controllers.reporting_controller import router as reporting_router

# APIs do painel
from app.ui.routes.api_dash import router as api_dash_router
from app.ui.routes.api_procs import router as api_procs_router
from app.ui.routes.control import router as ops_router
from app.ui.routes.health_api import router as ui_health_router
from app.ui.routes.legacy import router as ui_legacy_router


# -----------------------------------------------------------------------------
# Helpers (ops / snapshots)
# -----------------------------------------------------------------------------

def _resolve_ops_dir() -> Path:
    """
    Preferência:
      1) OPS_DIR (se existir)
      2) INSTANCE_DIR/ops
      3) LOGS_INST/ops
      4) ./ops (fallback)
    """
    env_ops = (os.getenv("OPS_DIR") or "").strip()
    if env_ops:
        return Path(env_ops)

    inst = (os.getenv("INSTANCE_DIR") or "").strip()
    if inst:
        return Path(inst) / "ops"

    li = (os.getenv("LOGS_INST") or os.getenv("LOG_DIR") or "").strip()
    if li:
        return Path(li) / "ops"

    return Path("./ops")


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _write_mt5_attach_snapshot(svc: "MT5Service", ok: bool) -> None:
    """
    WebRequest-first snapshot:
    - NÃO tenta ler MetaTrader5.terminal_info / last_error
    - Só regista a config e o estado "ok" do serviço.
    """
    try:
        ops_dir = _resolve_ops_dir()
        iid = (os.getenv("IID") or os.getenv("BRIDGE_ID") or os.getenv("INSTANCE_ID") or "").strip()
        now = datetime.now(timezone.utc).isoformat()

        # compat: tenta obter flags do session sem depender de coisas antigas
        running = None  # em webrequest o bridge não sabe se o MT5 está realmente aberto
        portable = None
        try:
            portable = bool(getattr(svc.session, "_wants_portable", lambda: None)())
        except Exception:
            portable = None

        payload = {
            "ts": now,
            "service": "bridge_backend",
            "mode": "webrequest",
            "iid": iid or None,
            "ok": bool(ok),
            "mt5": {
                "running": running,
                "portable": portable,
                "exe_path": getattr(svc, "exe_path", None),
                "exe_args": getattr(svc, "exe_args", None),
                "login": getattr(svc, "login", None),
                "server": getattr(svc, "server", None),
                # terminal_info/last_error só existem no modo MetaTrader5 API — removido de propósito
                "terminal_info": None,
                "last_error": None,
            },
        }

        _atomic_write_json(ops_dir / "mt5_attach.json", payload)
    except Exception as e:
        logging.getLogger("bridge").warning("failed to write ops/mt5_attach.json: %r", e)


def _is_webrequest_mode(svc: object | None = None) -> bool:
    """
    Determina se estamos em modo webrequest (EA->bridge via HTTP WebRequest).

    - Preferimos ler do MT5Service (svc.webrequest_mode) quando disponível
    - Fallback: MT5_MODE env
    """
    try:
        if svc is not None and hasattr(svc, "webrequest_mode"):
            return bool(getattr(svc, "webrequest_mode"))
    except Exception:
        pass

    mode = (os.getenv("MT5_MODE", "webrequest") or "webrequest").strip().lower()
    return mode in ("webrequest", "http", "ea", "bridge")


# -----------------------------------------------------------------------------
# Environment normalization (instance / logs / pids)
# -----------------------------------------------------------------------------

def _normalize_instance_env() -> None:
    """
    Normaliza o ambiente para garantir:
      - instance_id consistente (MLSL_INSTANCE_ID / IID / BRIDGE_ID / INSTANCE_ID)
      - logs por instância (LOGS_INST)
      - pidfiles canónicos em LOGS_INST/pids
    """

    # ------------------------------------------------------------------
    # 1) Resolver INSTANCE_ID canónico
    # ------------------------------------------------------------------
    iid = (
        (os.getenv("MLSL_INSTANCE_ID") or "").strip()
        or (os.getenv("INSTANCE_ID") or "").strip()
        or (os.getenv("BRIDGE_ID") or "").strip()
        or (os.getenv("IID") or "").strip()
    )

    if iid:
        os.environ.setdefault("MLSL_INSTANCE_ID", iid)
        os.environ.setdefault("INSTANCE_ID", iid)
        os.environ.setdefault("IID", iid)
        os.environ.setdefault("BRIDGE_ID", iid)

    # ------------------------------------------------------------------
    # 2) Normalizar LOGS_INST / LOG_DIR
    # ------------------------------------------------------------------
    ld = (os.getenv("LOG_DIR") or "").strip()
    if ld and not (os.getenv("LOGS_INST") or "").strip():
        os.environ["LOGS_INST"] = ld

    li = (os.getenv("LOGS_INST") or "").strip()
    if li and not (os.getenv("LOG_DIR") or "").strip():
        os.environ["LOG_DIR"] = li

    # ------------------------------------------------------------------
    # 3) PIDFILES canónico = <LOGS_INST>/pids
    # ------------------------------------------------------------------
    if li:
        pids_dir = str(Path(li) / "pids")
        os.environ.setdefault("PIDFILES_DIR", pids_dir)
        os.environ.setdefault("PIDS_DIR", pids_dir)  # compat legado


# -----------------------------------------------------------------------------
# carregar .env explícito (ENV_BRIDGE_FILE / ENV_LAB_FILE)
# -----------------------------------------------------------------------------
loaded = load_instance_env(override=True)

# -----------------------------------------------------------------------------
# aplicar normalização FINAL
# -----------------------------------------------------------------------------
_normalize_instance_env()

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------

def _setup_logging_for_bridge() -> None:
    lvl = os.getenv("LOG_LEVEL", "INFO").upper()
    fmt = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"

    root = logging.getLogger()
    if root.handlers:
        return

    root.setLevel(lvl)

    log_dir = Path(os.getenv("LOG_DIR", "./logs"))
    log_dir.mkdir(parents=True, exist_ok=True)

    iid = (os.getenv("IID") or os.getenv("BRIDGE_ID") or "").strip()
    logfile = log_dir / (f"bridge_{iid}.log" if iid else "bridge.log")

    rotate_mode = os.getenv("LOG_ROTATE", "size").lower()
    if rotate_mode == "time":
        fh = TimedRotatingFileHandler(
            str(logfile),
            when=os.getenv("LOG_WHEN", "midnight"),
            interval=1,
            backupCount=int(os.getenv("LOG_BACKUPS", "14")),
            encoding="utf-8",
            utc=True,
        )
    else:
        fh = RotatingFileHandler(
            str(logfile),
            maxBytes=int(os.getenv("LOG_MAX_BYTES", "5000000")),
            backupCount=int(os.getenv("LOG_BACKUPS", "3")),
            encoding="utf-8",
        )

    fh.setFormatter(logging.Formatter(fmt))
    fh.setLevel(lvl)
    root.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter(fmt))
    ch.setLevel(lvl)
    root.addHandler(ch)

    logging.getLogger("bridge").info(
        "logger ready (file=%s, level=%s, rotate=%s, env_loaded=%s)",
        str(logfile),
        lvl,
        rotate_mode,
        str(loaded) if loaded else "-",
    )


_setup_logging_for_bridge()
log = logging.getLogger("bridge")

# -----------------------------------------------------------------------------
# FastAPI app
# -----------------------------------------------------------------------------

app = FastAPI(
    title="MT5 Bridge MVC",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
    openapi_url="/openapi.json",
)

# -----------------------------------------------------------------------------
# CORS
# -----------------------------------------------------------------------------
cors_origins = os.getenv("CORS_ALLOW_ORIGINS", "http://127.0.0.1:5000,http://localhost:5000")
allow_origins = [x.strip() for x in cors_origins.split(",") if x.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# -----------------------------------------------------------------------------
# Redirects
# -----------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")


@app.get("/index.html", include_in_schema=False)
def root_index():
    return RedirectResponse(url="/docs")


# -----------------------------------------------------------------------------
# Routers core (API) — protegidos por API key
# -----------------------------------------------------------------------------
core_deps = [Depends(require_api_key)]

app.include_router(marketdata_router, dependencies=core_deps)
app.include_router(trade_router, dependencies=core_deps)
app.include_router(ea_router, dependencies=core_deps)
app.include_router(data_router, dependencies=core_deps)
app.include_router(account_router, dependencies=core_deps)
app.include_router(history_router, dependencies=core_deps)
app.include_router(debug_router, dependencies=core_deps)
app.include_router(meta_router, dependencies=core_deps)
app.include_router(reporting_router, dependencies=core_deps)

# -----------------------------------------------------------------------------
# Routers UI
# -----------------------------------------------------------------------------
protect_ui = os.getenv("PROTECT_UI_API", "0").strip().lower() in ("1", "true", "yes", "on", "y")
if protect_ui:
    app.include_router(ui_health_router, dependencies=core_deps)
    app.include_router(api_dash_router, dependencies=core_deps)
    app.include_router(api_procs_router, dependencies=core_deps)
    app.include_router(ops_router, dependencies=core_deps)
    app.include_router(ui_legacy_router, dependencies=core_deps)
else:
    app.include_router(ui_health_router)
    app.include_router(api_dash_router)
    app.include_router(api_procs_router)
    app.include_router(ops_router)
    app.include_router(ui_legacy_router)

# -----------------------------------------------------------------------------
# Health & ping
# -----------------------------------------------------------------------------
@app.get("/ping")
def ping():
    mode = (os.getenv("MT5_MODE", "webrequest") or "webrequest").strip().lower()
    return {"ok": True, "mode": mode}


@app.get("/health")
def health(verbose: int = Query(0, ge=0, le=1)):
    svc = getattr(app.state, "mt5", None)
    if not svc:
        return {"ok": False, "reason": "svc_missing"}

    try:
        info = svc.diag()
    except Exception as e:
        return {"ok": False, "reason": f"diag_failed: {e}"}

    st = STORE.snapshot()
    ttl = int(os.getenv("EA_TTL_MS", "15000"))
    now_ms = int(time.time() * 1000)
    age_ms = (now_ms - int(st.last_heartbeat_ms or 0)) if int(st.last_heartbeat_ms or 0) > 0 else None

    out = {
        "ok": True,
        "diag": info,
        "mode": "webrequest" if _is_webrequest_mode(svc) else "native",
        "ea": {
            "connected": st.is_ea_connected(ttl_ms=ttl),
            "last_heartbeat_ms": st.last_heartbeat_ms,
            "heartbeat_age_ms": age_ms,
            "ttl_ms": ttl,
            "identity": {
                "terminal_id": st.terminal_id,
                "account_login": st.account_login,
                "server": st.server,
                "build": st.build,
                "bridge_instance_id": st.bridge_instance_id,
            },
        },
    }

    if verbose:
        out["ea"]["positions_count"] = len(st.positions or [])
        out["ea"]["quotes_count"] = len(st.quotes or {})
        out["ea"]["symbol_info_count"] = len(st.symbol_info or {})
        out["ea"]["ohlcv_keys_count"] = len(st.ohlcv or {})

    return out

# -----------------------------------------------------------------------------
# Lifecycle
# -----------------------------------------------------------------------------
@app.on_event("startup")
def startup():
    """
    WebRequest-first:
      - Não tenta usar MetaTrader5 Python API.
      - O serviço arranca sempre.
      - Estado real do MT5/EA vem via WebRequest (endpoints que o EA chama).
    """
    svc = MT5Service()

    # Para não rebentar se o MT5Service ainda tiver restos de "initialize" antigo,
    # chamamos de forma segura (e não bloqueamos o arranque).
    ok = True
    try:
        ok = bool(getattr(svc, "initialize", lambda: True)())
    except Exception as e:
        ok = False
        log.error("MT5Service.initialize() falhou (webrequest mode, ignorado): %r", e)

    app.state.mt5 = svc
    set_service(svc)

    _write_mt5_attach_snapshot(svc, ok)

    log.info(
        "bridge startup ok=%s | mode=webrequest | exe=%s | args=%s | login=%s | server=%s",
        ok,
        getattr(svc, "exe_path", None),
        getattr(svc, "exe_args", None),
        getattr(svc, "login", None),
        getattr(svc, "server", None),
    )


@app.on_event("shutdown")
def shutdown():
    # em webrequest não há MT5.shutdown() aqui
    clear_service()
    log.info("bridge shutdown complete")