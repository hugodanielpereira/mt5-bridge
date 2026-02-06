# app/controllers/account_controller.py
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.common.service_registry import get_service

router = APIRouter(tags=["account"])
log = logging.getLogger("bridge")


@router.get("/account")
def account():
    try:
        svc = get_service()
        svc.ensure_up()
        return svc.account_info()
    except Exception as e:
        log.exception("account failed")
        raise HTTPException(status_code=500, detail={"error": "account crashed", "exc": str(e)})


@router.get("/diag")
def diag():
    try:
        svc = get_service()
        svc.ensure_up()
        return svc.diag()
    except Exception as e:
        log.exception("diag failed")
        raise HTTPException(status_code=500, detail={"error": "diag crashed", "exc": str(e)})