# app/ui/routes/legacy.py
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.ui.lib.paths import SCHEDULE_YAML

router = APIRouter(prefix="/ui", tags=["ui-legacy"])


@router.get("/retrain.yaml", include_in_schema=False)
def retrain_yaml_legacy():
    """
    Compat legado para o dashboard antigo:
      dashboard.js -> fetch('/ui/retrain.yaml')

    Agora servido pelo FastAPI (bridge), sem precisar de webui.py.
    """
    path = SCHEDULE_YAML
    if not path.exists():
        return PlainTextResponse("retrain.yaml não encontrado", status_code=404)

    return PlainTextResponse(path.read_text(encoding="utf-8", errors="replace"))