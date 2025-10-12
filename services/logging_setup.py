from __future__ import annotations
import logging, os, sys
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from pathlib import Path
from typing import Optional

def _ensure_stdout_utf8() -> None:
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

def setup_logging(
    name: str = "bridge",
    level: str = "INFO",
    fmt: str = "text",
    log_dir: Optional[Path] = None,
) -> logging.Logger:
    """
    Configura logging com rotação por 'size' ou 'time' via env:
      LOG_ROTATE    = "size" (default) | "time"
      LOG_WHEN      = "midnight" | "H" | "D" ... (se time)
      LOG_BACKUPS   = nº de ficheiros a reter (default 14 time / 3 size)
      LOG_MAX_BYTES = bytes p/ size (default 5_000_000)
      LOG_DIR       = pasta (default ./logs)
    """
    _ensure_stdout_utf8()

    if log_dir is None:
        log_dir = Path(os.getenv("LOG_DIR", "./logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    logfile = log_dir / f"{name}.log"

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # já configurado

    logger.setLevel(level.upper())
    fmt_str = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    formatter = logging.Formatter(fmt_str)

    rotate_mode = os.getenv("LOG_ROTATE", "size").lower()
    if rotate_mode == "time":
        when = os.getenv("LOG_WHEN", "midnight")
        backups = int(os.getenv("LOG_BACKUPS", "14"))
        fh = TimedRotatingFileHandler(
            filename=str(logfile),
            when=when,
            interval=1,
            backupCount=backups,
            encoding="utf-8",
            utc=True,
        )
    else:
        max_bytes = int(os.getenv("LOG_MAX_BYTES", str(5_000_000)))
        backups = int(os.getenv("LOG_BACKUPS", "3"))
        fh = RotatingFileHandler(
            filename=str(logfile),
            maxBytes=max_bytes,
            backupCount=backups,
            encoding="utf-8",
        )

    fh.setLevel(level.upper())
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    # Console
    ch = logging.StreamHandler()
    ch.setLevel(level.upper())
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    logger.info(
        f"logger ready (file={logfile}, level={level.upper()}, fmt={fmt}, rotate={rotate_mode})"
    )
    return logger

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)