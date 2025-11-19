# bridges/mt5-bridge/services/logging_setup.py
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

def _safe_file_handler(logfile: Path, rotate_mode: str, *, level: str) -> tuple[logging.Handler, Path]:
    logfile.parent.mkdir(parents=True, exist_ok=True)
    def _make(f: Path):
        if rotate_mode == "time":
            when = os.getenv("LOG_WHEN", "midnight")
            backups = int(os.getenv("LOG_BACKUPS", "14"))
            return TimedRotatingFileHandler(
                filename=str(f),
                when=when,
                interval=1,
                backupCount=backups,
                encoding="utf-8",
                utc=True,
                delay=True,
            )
        else:
            max_bytes = int(os.getenv("LOG_MAX_BYTES", str(5_000_000)))
            backups = int(os.getenv("LOG_BACKUPS", "3"))
            return RotatingFileHandler(
                filename=str(f),
                maxBytes=max_bytes,
                backupCount=backups,
                encoding="utf-8",
                delay=True,
            )
    try:
        fh = _make(logfile)
        return fh, logfile
    except PermissionError:
        alt = logfile.with_name(f"{logfile.name}.{os.getpid()}.log")
        fh = _make(alt)
        return fh, alt

def setup_logging(
    name: str = "bridge",
    level: str = "INFO",
    fmt: str = "text",
    log_dir: Optional[Path] = None,
) -> logging.Logger:
    _ensure_stdout_utf8()

    if log_dir is None:
        log_dir = Path(os.getenv("LOG_DIR", "./logs"))
    logfile = log_dir / f"{name}.log"

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(level.upper())
    fmt_str = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    formatter = logging.Formatter(fmt_str)

    rotate_mode = os.getenv("LOG_ROTATE", "size").lower()
    fh, effective_file = _safe_file_handler(logfile, rotate_mode, level=level)
    fh.setLevel(level.upper()); fh.setFormatter(formatter)
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setLevel(level.upper()); ch.setFormatter(formatter)
    logger.addHandler(ch)

    logger.info(
        "logger ready (file=%s, level=%s, fmt=%s, rotate=%s)",
        effective_file, level.upper(), fmt, rotate_mode
    )
    return logger

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)