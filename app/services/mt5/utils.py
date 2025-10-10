# app/services/mt5/utils.py
from __future__ import annotations
import os, re
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import MetaTrader5 as MT5
except Exception as e:  # pragma: no cover
    raise RuntimeError(f"MetaTrader5 import failed: {e!r}")

# ---------------------------------------------------------------------
# Timeframes (map canónico para o projeto)
# ---------------------------------------------------------------------
TF_MAP = {
    "M1":  MT5.TIMEFRAME_M1,
    "M5":  MT5.TIMEFRAME_M5,
    "M15": MT5.TIMEFRAME_M15,
    "M30": MT5.TIMEFRAME_M30,
    "H1":  MT5.TIMEFRAME_H1,
    "H2":  MT5.TIMEFRAME_H2,
    "H3":  MT5.TIMEFRAME_H3,
    "H4":  MT5.TIMEFRAME_H4,
    "H6":  MT5.TIMEFRAME_H6,
    "H8":  MT5.TIMEFRAME_H8,
    "H12": MT5.TIMEFRAME_H12,
    "D1":  MT5.TIMEFRAME_D1,
    "W1":  MT5.TIMEFRAME_W1,
    "MN1": MT5.TIMEFRAME_MN1,
}

# ---------------------------------------------------------------------
# Helpers genéricos
# ---------------------------------------------------------------------
def nt_to_dict(x) -> Dict[str, Any]:
    """Converte namedtuple-like de MT5 para dict normal."""
    if x is None:
        return {}
    return x._asdict() if hasattr(x, "_asdict") else dict(x)

def sanitize_comment(val: Optional[str], fallback: str = "mlsl-exec") -> str:
    """
    MT5 é sensível ao 'comment': ASCII imprimível, sem quebras, <= 31 chars.
    Remove quotes e separadores problemáticos.
    """
    if val is None:
        s = fallback
    else:
        try:
            s = str(val)
        except Exception:
            s = fallback
    s = s.replace("\r", " ").replace("\n", " ").strip()
    # ASCII imprimível
    s = re.sub(r"[^ -~]", "", s)
    # remover caracteres chatos
    for ch in ("'", '"', ";", "/"):
        s = s.replace(ch, "")
    s = s[:31]
    return s or fallback

def pick_env(key: str, env_suffix: str) -> Optional[str]:
    """Lê variáveis com sufixo de ambiente (ex: _LIVE/_DEMO) e fallback para genérica."""
    return os.getenv(f"{key}{env_suffix}") or os.getenv(key)

def samefile_case_insensitive(a: Path, b: Path) -> bool:
    """Compara paths de forma robusta mesmo quando samefile não está disponível/lança exceção."""
    try:
        return a.resolve().samefile(b.resolve())
    except Exception:
        return str(a).strip().lower() == str(b).strip().lower()

# ---------------------------------------------------------------------
# Helpers específicos de MT5
# ---------------------------------------------------------------------
def ensure_symbol(symbol: str) -> None:
    """Garante que o símbolo existe e está visível no Market Watch."""
    info = MT5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"symbol '{symbol}' not found")
    if not info.visible:
        if not MT5.symbol_select(symbol, True):
            raise RuntimeError(f"symbol_select failed for '{symbol}'")