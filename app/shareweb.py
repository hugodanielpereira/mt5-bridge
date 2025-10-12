# app/shareweb.py
from __future__ import annotations
from fastapi import Header, HTTPException
from typing import Optional
import os
import logging

log = logging.getLogger("auth")

# ---- auth (como já tinhas) ----
def _get_expected_api_key() -> str:
    return os.getenv("X_API_KEY") or os.getenv("BRIDGE_API_KEY") or os.getenv("API_KEY") or ""

def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    expected = _get_expected_api_key()
    if not expected:
        log.debug("No API key set; skipping auth (dev mode).")
        return
    if not x_api_key or x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")

# ---- service registry ----
# notamos: tipagem frouxa para evitar import circular
_svc = None

def set_service(svc) -> None:
    """Regista a instância global do MT5Service criada no startup da app."""
    global _svc
    _svc = svc

def get_service():
    """Devolve a instância global. Lança erro se ainda não existir."""
    if _svc is None:
        raise RuntimeError("MT5Service not initialized yet (startup not finished?)")
    return _svc