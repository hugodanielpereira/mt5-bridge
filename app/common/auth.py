#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from typing import Optional, Set

from fastapi import Depends, HTTPException, Request, status

def _split_keys(raw: str) -> Set[str]:
    # aceita "k1,k2,k3" ou "k1; k2"
    parts = []
    for chunk in raw.replace(";", ",").split(","):
        k = chunk.strip().strip('"').strip("'")
        if k:
            parts.append(k)
    return set(parts)

def _expected_keys() -> Set[str]:
    # prioridade: BRIDGE_API_KEY, depois X_API_KEY, depois API_KEY
    keys: Set[str] = set()
    for name in ("BRIDGE_API_KEY", "X_API_KEY", "API_KEY"):
        v = os.getenv(name, "") or ""
        if v.strip():
            keys |= _split_keys(v)
    return keys

def _get_presented_key(req: Request) -> Optional[str]:
    # 1) Header X-API-Key (case-insensitive)
    x = req.headers.get("X-API-Key")
    if x and x.strip():
        return x.strip()

    # 2) Authorization: Bearer <token>
    auth = req.headers.get("Authorization")
    if auth:
        a = auth.strip()
        if a.lower().startswith("bearer "):
            token = a[7:].strip()
            if token:
                return token
    return None

def require_api_key(req: Request) -> None:
    expected = _expected_keys()
    presented = _get_presented_key(req)

    if not expected:
        # se não tens nenhuma key definida, isso é erro de config
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server misconfigured: missing API key env (BRIDGE_API_KEY/X_API_KEY/API_KEY)",
        )

    if not presented or presented not in expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )