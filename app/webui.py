from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

import requests
from flask import Flask, Response, render_template, request, send_from_directory

BASE_DIR = Path(__file__).resolve().parent
UI_DIR = BASE_DIR / "ui"
TEMPLATES_DIR = UI_DIR / "templates"
STATIC_DIR = UI_DIR / "static"


# ---------------------------------------------------------------------
# ✅ Instance/env normalization (para UI)
# ---------------------------------------------------------------------
def _normalize_instance_env() -> None:
    iid = (
        (os.getenv("MLSL_INSTANCE_ID") or "").strip()
        or (os.getenv("INSTANCE_ID") or "").strip()
        or (os.getenv("BRIDGE_ID") or "").strip()
        or (os.getenv("IID") or "").strip()
    )

    if iid:
        os.environ.setdefault("MLSL_INSTANCE_ID", iid)
        os.environ.setdefault("INSTANCE_ID", iid)
        os.environ.setdefault("IID", iid)
        os.environ.setdefault("BRIDGE_ID", iid)

    ld = (os.getenv("LOG_DIR") or "").strip()
    li = (os.getenv("LOGS_INST") or "").strip()

    if ld and not li:
        os.environ["LOGS_INST"] = ld
        li = ld

    if li and not ld:
        os.environ["LOG_DIR"] = li

    if li:
        pids_dir = str(Path(li) / "pids")
        os.environ.setdefault("PIDFILES_DIR", pids_dir)
        os.environ.setdefault("PIDS_DIR", pids_dir)


_normalize_instance_env()


# ---------------------------------------------------------------------
# Roots / paths
# ---------------------------------------------------------------------
def _trading_root() -> Path:
    env = (os.getenv("TRADING_ROOT") or os.getenv("PROJECT_ROOT") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return BASE_DIR.parent


def _apps_root() -> Path:
    env = (os.getenv("PROJECT_DIR") or os.getenv("MLSL_APPS_DIR") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return _trading_root() / "apps" / "ml-strategy-lab"


def _iid() -> str:
    return (os.getenv("MLSL_INSTANCE_ID") or os.getenv("INSTANCE_ID") or os.getenv("BRIDGE_ID") or "default").strip()


def _live_root() -> Path:
    env = (os.getenv("LIVE_DIR") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return _apps_root() / "outputs" / "live"


def _instance_dir() -> Path:
    env = (os.getenv("INSTANCE_DIR") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return _live_root() / "instances" / _iid()


# ---------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------
app = Flask(
    __name__,
    static_folder=str(STATIC_DIR),
    template_folder=str(TEMPLATES_DIR),
)

# ---------------------------------------------------------------------
# Backend alvo (Bridge FastAPI)
# ---------------------------------------------------------------------
API_BASE = (os.getenv("BRIDGE_URL") or os.getenv("BRIDGE_API_BASE") or "http://127.0.0.1:5035").rstrip("/")
PROXY_TIMEOUT = float(os.getenv("PROXY_TIMEOUT", "30").strip() or "30")


def _current_api_key() -> str:
    return (os.getenv("BRIDGE_API_KEY") or os.getenv("X_API_KEY") or os.getenv("API_KEY") or "").strip().strip('"').strip("'")


def _proxy_headers() -> Dict[str, str]:
    h: Dict[str, str] = {}
    for k, v in request.headers.items():
        lk = k.lower()
        # não reenviar headers que podem causar inconsistências
        if lk in ("host", "content-length", "connection", "accept-encoding"):
            continue
        h[k] = v

    key = _current_api_key()
    if key and "x-api-key" not in {kk.lower() for kk in h.keys()}:
        h["X-API-Key"] = key

    # útil para logs do backend
    try:
        h.setdefault("X-Forwarded-For", request.remote_addr or "")
        h.setdefault("X-Forwarded-Proto", request.scheme or "http")
    except Exception:
        pass

    return h


def _cors_preflight_response() -> Response:
    # Resposta simples para OPTIONS (preflight)
    resp = Response("", status=204)
    resp.headers["Access-Control-Allow-Origin"] = request.headers.get("Origin", "*")
    resp.headers["Access-Control-Allow-Methods"] = request.headers.get("Access-Control-Request-Method", "GET,POST,PUT,PATCH,DELETE,OPTIONS")
    resp.headers["Access-Control-Allow-Headers"] = request.headers.get("Access-Control-Request-Headers", "Content-Type, X-API-Key, Authorization")
    resp.headers["Access-Control-Allow-Credentials"] = "true"
    resp.headers["Vary"] = "Origin"
    return resp


def _forward(url: str) -> Response:
    # Se for preflight, responde aqui (evita depender do backend para CORS)
    if request.method == "OPTIONS":
        return _cors_preflight_response()

    params = request.args
    body = request.get_data()

    try:
        resp = requests.request(
            method=request.method,
            url=url,
            params=params,
            data=body,
            headers=_proxy_headers(),
            cookies=request.cookies,
            allow_redirects=False,
            timeout=PROXY_TIMEOUT,
        )
    except requests.RequestException as e:
        return Response(f"proxy error: {e}", status=502, mimetype="text/plain; charset=utf-8")

    excluded = {"content-encoding", "transfer-encoding", "connection", "content-length"}
    headers = [(k, v) for k, v in resp.headers.items() if k.lower() not in excluded]
    return Response(resp.content, status=resp.status_code, headers=headers)


# ---------------------------------------------------------------------
# Pages UI
# ---------------------------------------------------------------------
@app.route("/")
@app.route("/ui/")
def index():
    return render_template("index.html")


@app.route("/ui/ops/")
def ops():
    return render_template("ops.html")


@app.route("/ui/control/")
def control():
    return render_template("control.html")


@app.route("/static/<path:filename>")
def static_files(filename: str):
    return send_from_directory(STATIC_DIR, filename)


# ---------------------------------------------------------------------
# ✅ Retrain YAML (legacy endpoint) — agora instance-aware
# ---------------------------------------------------------------------
@app.route("/ui/retrain.yaml")
def retrain_yaml_legacy():
    p1 = _instance_dir() / "schedules" / "retrain.yaml"
    if p1.exists():
        return Response(p1.read_text(encoding="utf-8", errors="replace"), mimetype="text/plain; charset=utf-8")

    p2 = _apps_root() / "outputs" / "live" / "schedules" / "retrain.yaml"
    if p2.exists():
        return Response(p2.read_text(encoding="utf-8", errors="replace"), mimetype="text/plain; charset=utf-8")

    return Response(
        f"retrain.yaml não encontrado (iid={_iid()})\n"
        f"tentado:\n- {p1}\n- {p2}\n",
        status=404,
        mimetype="text/plain; charset=utf-8",
    )


# ---------------------------------------------------------------------
# ✅ PROXY CORRETO para o dashboard: /ui/api/*  ->  {API_BASE}/ui/api/*
# ---------------------------------------------------------------------
@app.route("/ui/api/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def proxy_ui_api(path: str):
    url = f"{API_BASE}/ui/api/{path}"
    return _forward(url)


# ---------------------------------------------------------------------
# Proxy antigo (legacy): /api/*
# ---------------------------------------------------------------------
@app.route("/api/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def proxy_api(path: str):
    url = f"{API_BASE}/{path}"
    return _forward(url)


if __name__ == "__main__":
    host = (os.getenv("UI_HOST") or "127.0.0.1").strip()
    port = int(os.getenv("UI_PORT", "5000"))
    debug = (os.getenv("UI_DEBUG", "0").strip().lower() in ("1", "true", "yes", "on", "y"))
    app.run(host=host, port=port, debug=debug)