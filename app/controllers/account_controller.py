from typing import Optional
from fastapi import APIRouter, Header

from app.shareweb import require_api_key
from app.services.mt5 import MT5Service

router = APIRouter(tags=["account"])

@router.get("/account")
def account(x_api_key: Optional[str] = Header(None)):
    require_api_key(x_api_key)
    svc = MT5Service()
    svc.ensure_up()
    return svc.account_info()

@router.get("/diag")
def diag(x_api_key: Optional[str] = Header(None)):
    require_api_key(x_api_key)
    svc = MT5Service()
    return svc.diag()