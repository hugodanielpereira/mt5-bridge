#!/usr/bin/env bash
# ===============================================
# Run script para MT5-Bridge (Linux / macOS)
# ===============================================

set -euo pipefail

# Ativar virtualenv se existir
if [[ -f ".venv/bin/activate" ]]; then
  source .venv/bin/activate
fi

# Carregar variáveis de ambiente do .env (se existir)
if [[ -f ".env" ]]; then
  echo "[INFO] A carregar .env..."
  export $(grep -v '^#' .env | xargs)
fi

# Definir host/porta padrão se não vierem do .env
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-5005}"

echo "[INFO] A iniciar MT5-Bridge em http://${HOST}:${PORT}"
exec uvicorn app:app --host "${HOST}" --port "${PORT}" --reload