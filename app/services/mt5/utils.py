# app/services/mt5/utils.py
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------
# Mode helpers
# ---------------------------------------------------------------------
def _mt5_mode() -> str:
    return (os.getenv("MT5_MODE", "webrequest") or "webrequest").strip().lower()


def _is_webrequest_mode() -> bool:
    return _mt5_mode() in ("webrequest", "http", "ea", "bridge")


def _load_mt5_native():
    """
    Lazy import do MetaTrader5 package.
    Só deve ser usado quando MT5_MODE=native.
    """
    try:
        import MetaTrader5 as MT5  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError(f"MetaTrader5 import failed (MT5_MODE=native): {e!r}")
    return MT5


# ---------------------------------------------------------------------
# Timeframes (map canónico para o projeto)
# ---------------------------------------------------------------------
# Em webrequest não precisamos dos enums MT5 (só strings canónicas).
# Em native, tf_enum() devolve o enum certo do package.
TF_MAP: Dict[str, str] = {
    "M1": "M1",
    "M5": "M5",
    "M15": "M15",
    "M30": "M30",
    "H1": "H1",
    "H2": "H2",
    "H3": "H3",
    "H4": "H4",
    "H6": "H6",
    "H8": "H8",
    "H12": "H12",
    "D1": "D1",
    "W1": "W1",
    "MN1": "MN1",
}


def tf_enum(tf: str):
    """
    Converte timeframe canónico (ex: 'H1') para enum do MetaTrader5.
    - webrequest: levanta erro (porque não há MetaTrader5 API)
    - native: devolve MT5.TIMEFRAME_*
    """
    t = (tf or "").upper().strip()
    if not t:
        raise RuntimeError("invalid tf ''")

    if _is_webrequest_mode():
        raise RuntimeError("tf_enum requires MT5_MODE=native (MetaTrader5 API not available in webrequest mode)")

    MT5 = _load_mt5_native()

    m = {
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
    if t not in m:
        raise RuntimeError(f"invalid tf '{t}' (use one of {list(m)})")
    return m[t]


# ---------------------------------------------------------------------
# Helpers genéricos
# ---------------------------------------------------------------------
def nt_to_dict(x) -> Dict[str, Any]:
    """Converte namedtuple-like (ou dict-like) para dict normal."""
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
# Helpers específicos de MT5 (native-only)
# ---------------------------------------------------------------------
def ensure_symbol(symbol: str) -> None:
    """Garante que o símbolo existe e está visível no Market Watch (MT5_MODE=native)."""
    if _is_webrequest_mode():
        # Em webrequest o bridge não valida símbolos via API.
        return

    MT5 = _load_mt5_native()

    info = MT5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"symbol '{symbol}' not found")
    if not getattr(info, "visible", False):
        if not MT5.symbol_select(symbol, True):
            raise RuntimeError(f"symbol_select failed for '{symbol}'")