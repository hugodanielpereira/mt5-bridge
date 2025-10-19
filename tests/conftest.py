# tests/conftest.py
from __future__ import annotations
import os
import sys
from pathlib import Path

# Raiz do projeto do bridge (contém a pasta 'app')
BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))  # permite "from app.main import app"

# Raiz do MLSL (onde vivem "tools" e "services")
# Estrutura real no teu caso:
#   C:\Trading\bridges\mt5-bridge      (este repo)
#   C:\Trading\apps\ml-strategy-lab    (MLSL)
DEFAULT_MLSL = BRIDGE_ROOT.parent.parent / "apps" / "ml-strategy-lab"
APPS_ROOT = Path(os.getenv("MLSL_APPS_DIR", str(DEFAULT_MLSL)))

# Assegura que existe e mete no sys.path
if APPS_ROOT.exists():
    if str(APPS_ROOT) not in sys.path:
        sys.path.insert(0, str(APPS_ROOT))
else:
    # Deixa uma pista clara se o caminho falhar
    raise RuntimeError(f"MLSL_APPS_DIR inválido. Esperado em: {APPS_ROOT}")

# Variáveis “seguras” para testes (evitar enviar ordens)
os.environ.setdefault("MLSL_RETRAIN_NO_EMIT", "1")
os.environ.setdefault("BRIDGE_STRICT_STARTUP", "0")

# Diretórios defaults para o retrainer durante testes
os.environ.setdefault("CONFIGS_DIR", str(APPS_ROOT / "outputs" / "live" / "configs"))
os.environ.setdefault("BRIDGE_URL", "http://127.0.0.1:5005")