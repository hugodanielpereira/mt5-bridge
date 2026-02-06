#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.common.env_loader

Carrega .env explícito por instância (bridge).
Evita python-dotenv apanhar .env "legacy" por heurística.
"""

from __future__ import annotations
import os
from pathlib import Path
from typing import Optional

_LOADED = False

def _norm_path(p: str) -> Path:
    p = p.strip()
    # paths absolutos ficam iguais
    if os.path.isabs(p):
        return Path(p)
    # relativo: resolve a partir do CWD atual
    return Path.cwd() / p

def load_instance_env(*, prefer: Optional[str] = None, override: bool = True) -> Optional[Path]:
    """
    Ordem:
      1) prefer (argumento)
      2) ENV_BRIDGE_FILE
      3) ENV_FILE
    """
    global _LOADED
    if _LOADED:
        return None

    cand = (prefer or "").strip() or (os.getenv("ENV_BRIDGE_FILE") or "").strip() or (os.getenv("ENV_FILE") or "").strip()
    _LOADED = True

    if not cand:
        return None

    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:
        return None

    p = _norm_path(cand)
    if not p.exists():
        return None

    load_dotenv(dotenv_path=str(p), override=override)
    return p