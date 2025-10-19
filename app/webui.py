# app/webui.py
from flask import Flask, render_template, send_from_directory, Response
from pathlib import Path
import os

BASE_DIR   = Path(__file__).resolve().parent
UI_DIR     = BASE_DIR / "ui"
STATIC_DIR = UI_DIR / "static"

def _apps_root():
    env = os.getenv("PROJECT_DIR") or os.getenv("MLSL_APPS_DIR")
    if env:
        return Path(env)
    # heurística (opcional) – ajusta à tua árvore se precisares
    return BASE_DIR.parent / "apps" / "ml-strategy-lab"

app = Flask(
    __name__,
    static_folder=str(STATIC_DIR),
    template_folder=str(UI_DIR),
)

@app.route("/")
@app.route("/ui/")
def index():
    return render_template("index.html")

@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)

# <<< NOVO: compat legado para evitar 404 em /ui/retrain.yaml >>>
@app.route("/ui/retrain.yaml")
def retrain_yaml_legacy():
    path = _apps_root() / "outputs" / "live" / "schedules" / "retrain.yaml"
    if not path.exists():
        return Response("retrain.yaml não encontrado", status=404, mimetype="text/plain; charset=utf-8")
    return Response(path.read_text(encoding="utf-8", errors="replace"),
                    mimetype="text/plain; charset=utf-8")

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)