from __future__ import annotations

import os
import hashlib
from typing import Any, Dict, Optional


def bridge_instance_id() -> str:
    """
    ID estável desta instância (terminal/bridge).

    Ordem:
      1) BRIDGE_INSTANCE_ID (recomendado, explícito por terminal)
      2) BRIDGE_ID (compat)
      3) fallback: hash estável de login|server|data_path (se existirem)
      4) fallback final: "default"
    """
    v = (os.getenv("BRIDGE_INSTANCE_ID") or "").strip()
    if v:
        return v

    v2 = (os.getenv("BRIDGE_ID") or "").strip()
    if v2:
        return v2

    base = (
        (os.getenv("MT5_LOGIN") or "").strip()
        + "|"
        + (os.getenv("MT5_SERVER") or "").strip()
        + "|"
        + (os.getenv("MT5_DATA_PATH") or "").strip()
    ).strip()

    if base.strip("|"):
        return "bridge_" + hashlib.sha1(base.encode("utf-8")).hexdigest()[:10]

    return "default"


def meta_snapshot(extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Snapshot de identidade/config mínima (bom para responses e para ledger/reporting).
    """
    d: Dict[str, Any] = {
        "bridge_instance_id": bridge_instance_id(),
        "account_login": os.getenv("MT5_LOGIN"),
        "server": os.getenv("MT5_SERVER"),
        # opcional (útil p/ debug multi-terminal)
        "mt5_data_path": os.getenv("MT5_DATA_PATH"),
        "mt5_terminal_path": os.getenv("MT5_TERMINAL_PATH") or os.getenv("MT5_PATH"),
    }
    if extra:
        d.update(extra)
    return d