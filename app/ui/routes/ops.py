# app/ui/routes/ops.py
from __future__ import annotations
from fastapi import APIRouter, Request
from starlette.templating import Jinja2Templates
from fastapi.responses import HTMLResponse

router = APIRouter()
templates = Jinja2Templates(directory="app/ui/templates")

@router.get("/ops/", response_class=HTMLResponse, include_in_schema=False)
def ops_page(request: Request):
    return templates.TemplateResponse("ops.html", {"request": request})