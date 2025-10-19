# app/ui/routes/control_page.py
from __future__ import annotations
from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.templating import Jinja2Templates

router = APIRouter(tags=["ui-control"])

# Resolve the templates folder robustly from this file’s location
# …/app/ui/routes/control_page.py  →  …/app/ui/templates
_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

@router.get("/ui/control/", response_class=HTMLResponse, include_in_schema=False)
@router.get("/ui/control",  response_class=HTMLResponse, include_in_schema=False)
def control_page(request: Request):
    # Renders app/ui/templates/control.html
    return templates.TemplateResponse("control.html", {"request": request})