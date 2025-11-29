import os
from pathlib import Path

# Lista dos ficheiros que enviaste
TARGETS = {
    "diag.py",
    "history.py",
    "init.py",
    "marketdata.py",
    "orders.py",
    "service.py",
    "session.py",
    "terminal.py",
    "trade.py",
    "utils.py",
}

# Caminho base onde tens o MT5-Bridge
BASE = Path(".").resolve()  # <--- podes ajustar, mas normalmente basta executar no diretório certo

found = {}

for root, dirs, files in os.walk(BASE):
    for f in files:
        if f in TARGETS:
            p = Path(root) / f
            found[f] = str(p)

print("\n=== RESULTADOS DA VARRIDURA ===\n")
if not found:
    print("Nenhum dos ficheiros-alvo foi encontrado!")

for name, fullpath in found.items():
    rel = os.path.relpath(fullpath, BASE)
    print(f"{name:15s} →  {rel}")

print("\n=== FIM ===")