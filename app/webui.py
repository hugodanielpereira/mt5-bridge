# app/webui.py
from __future__ import annotations
from flask import Flask, render_template, send_from_directory, Response
from pathlib import Path
import os

BASE_DIR    = Path(__file__).resolve().parent
UI_DIR      = BASE_DIR / "ui"
TEMPLATES_DIR = UI_DIR / "templates"
STATIC_DIR  = UI_DIR / "static"

def _apps_root():
    env = os.getenv("PROJECT_DIR") or os.getenv("MLSL_APPS_DIR")
    if env:
        return Path(env)
    # heurística – ajusta se a tua árvore for diferente
    return BASE_DIR.parent / "apps" / "ml-strategy-lab"

app = Flask(
    __name__,
    static_folder=str(STATIC_DIR),
    template_folder=str(TEMPLATES_DIR),
)

# ----------------- Rotas UI -----------------

@app.route("/")
@app.route("/ui/")
def index():
    # dashboard principal
    return render_template("index.html")

@app.route("/ui/ops/")
def ops():
    return render_template("ops.html")

@app.route("/ui/control/")
def control():
    return render_template("control.html")

# ----------------- Static -----------------

@app.route("/static/<path:filename>")
def static_files(filename: str):
    return send_from_directory(STATIC_DIR, filename)

# --- compat legado para /ui/retrain.yaml ---
@app.route("/ui/retrain.yaml")
def retrain_yaml_legacy():
    path = _apps_root() / "outputs" / "live" / "schedules" / "retrain.yaml"
    if not path.exists():
        return Response("retrain.yaml não encontrado",
                        status=404,
                        mimetype="text/plain; charset=utf-8")
    return Response(
        path.read_text(encoding="utf-8", errors="replace"),
        mimetype="text/plain; charset=utf-8",
    )

if __name__ == "__main__":
    # UI em 5000 por defeito
    app.run(host="127.0.0.1", port=5000, debug=True)