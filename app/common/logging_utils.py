# app/common/logging_utils.py
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from logging.handlers import TimedRotatingFileHandler
from typing import Optional, Dict, Any

from app.common.path_utils import logs_dir

def _ensure_stdout_utf8() -> None:
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

def _default_context() -> Dict[str, Any]:
    return {
        "instance": os.getenv("SERVICE_INSTANCE", ""),
        "terminal": os.getenv("TERMINAL_ID", ""),
        "broker": os.getenv("BROKER", ""),
        "run_id": os.getenv("RUN_ID", ""),
        "env": os.getenv("ENV", ""),
    }

def _effective_log_dir(service: str) -> Path:
    """
    Decide a pasta final de logs.
    Base: <Trading>/logs (via logs_dir()).

    Opcional (sem mexer no código):
      - LOG_SUBDIR=1 -> cria subdir por serviço
      - LOG_INSTANCE_SUBDIR=1 -> cria subdir com SERVICE_INSTANCE/TERMINAL_ID/BROKER/RUN_ID
    """
    base = logs_dir()
    base.mkdir(parents=True, exist_ok=True)

    subdir = base

    if os.getenv("LOG_INSTANCE_SUBDIR", "0").strip().lower() in ("1","true","yes","on","y"):
        parts = []
        for k in ("RUN_ID", "SERVICE_INSTANCE", "TERMINAL_ID", "BROKER"):
            v = (os.getenv(k) or "").strip()
            if v:
                safe = "".join(ch if ch.isalnum() or ch in ("-","_","@") else "_" for ch in v)
                parts.append(safe)
        if parts:
            subdir = subdir / "_".join(parts)

    if os.getenv("LOG_SUBDIR", "0").strip().lower() in ("1","true","yes","on","y"):
        subdir = subdir / service

    subdir.mkdir(parents=True, exist_ok=True)
    return subdir

class ContextAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        extra = kwargs.get("extra") or {}
        if not isinstance(extra, dict):
            extra = {"extra": extra}
        merged = dict(self.extra)
        merged.update(extra)
        kwargs["extra"] = merged
        return msg, kwargs

def setup_logging(
    service: str,
    *,
    level: Optional[str] = None,
    filename: Optional[str] = None,
) -> logging.Logger:
    _ensure_stdout_utf8()

    lvl = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    logger = logging.getLogger(service)
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, lvl, logging.INFO))
    logger.propagate = False

    log_dir = _effective_log_dir(service)
    log_path = log_dir / (filename or f"{service}.log")

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    fh = TimedRotatingFileHandler(
        str(log_path),
        when=os.getenv("LOG_WHEN", "midnight"),
        interval=1,
        backupCount=int(os.getenv("LOG_BACKUPS", "14")),
        encoding="utf-8",
        utc=True,
        delay=True,
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    logger.info("logger.ready file=%s level=%s", str(log_path), lvl)
    return logger

def get_logger(service: str, *, level: Optional[str] = None, filename: Optional[str] = None) -> logging.Logger:
    return setup_logging(service, level=level, filename=filename)

def get_service_logger(service: str, **context: Any) -> logging.LoggerAdapter:
    base = _default_context()
    base.update({k: v for k, v in context.items() if v is not None})
    lg = setup_logging(service)
    return ContextAdapter(lg, base)