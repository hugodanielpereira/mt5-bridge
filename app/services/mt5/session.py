# app/services/mt5/session.py
from __future__ import annotations
from pathlib import Path
from typing import Optional, Dict
import os

try:
    import MetaTrader5 as MT5
except Exception as e:
    raise RuntimeError(f"MetaTrader5 import failed: {e!r}")

from .terminal import (
    ensure_terminal_running as _ensure_terminal_running,
    attach_specific as _attach_specific,
)


class MT5Session:
    """
    Gere a sessão com o terminal MT5 e resolve símbolos respeitando:
      - Mapa de aliases (chaves em UPPER; valores preservam o case real do MT5)
      - Correspondência case-insensitive no terminal
      - Heurística comum USDT -> USD
    """

    def __init__(self, exe_path: Path, login: Optional[str], password: Optional[str], server: Optional[str]) -> None:
        self.exe_path = exe_path
        self.base_dir = exe_path.parent
        self.login = login
        self.password = password
        self.server = server
        self._symmap: Optional[Dict[str, str]] = None  # {UPPER: "ValorExactoDoMT5"}

    # ---------- symbol map ----------
    def _load_symbol_map(self) -> Dict[str, str]:
        """
        Ordem de resolução:
          1) env SYMBOLS_MAP (absolute ou relative; YAML/JSON)
          2) PROJECT_DIR/outputs/live/symbols_map.yaml
          3) outputs/live/symbols_map.yaml (relativo ao CWD)
        As CHAVES são normalizadas em UPPER, os VALORES mantêm-se tal como definidos (case-sensitive).
        """
        if self._symmap is not None:
            return self._symmap

        import json
        try:
            import yaml  # type: ignore
        except Exception:
            yaml = None  # se não houver, só JSON

        candidates = []
        env_map = os.getenv("SYMBOLS_MAP", "").strip()
        if env_map:
            candidates.append(Path(env_map))

        proj = os.getenv("PROJECT_DIR", "").strip()
        if proj:
            candidates.append(Path(proj) / "outputs" / "live" / "symbols_map.yaml")

        candidates.append(Path("outputs") / "live" / "symbols_map.yaml")

        mp: Dict[str, str] = {}
        for p in candidates:
            try:
                if not p.exists():
                    continue
                if p.suffix.lower() in (".yaml", ".yml"):
                    if yaml is None:
                        continue
                    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                else:
                    data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    # chaves canónicas em UPPER; valores com case preservado
                    mp = {str(k).upper(): str(v).strip() for k, v in data.items() if str(k).strip()}
                    break
            except Exception:
                # tenta próximo candidato
                pass

        self._symmap = mp
        return mp

    # ---------- symbol resolution ----------
    def resolve_symbol(self, symbol: str) -> str:
        """
        Recebe um nome pedido (potencialmente em qualquer case/alias) e devolve
        o nome **exato** conhecido pelo MT5, se possível.
        Estrutura:
          1) aplica alias do mapa (se existir)
          2) tenta tal-qual no terminal
          3) tenta match case-insensitive no terminal
          4) heurística USDT -> USD
          5) devolve o pedido (poderá falhar depois em ensure_symbol)
        """
        raw = (symbol or "").strip()
        if not raw:
            return raw

        # 1) alias do mapa (chave UPPER → valor preservado)
        m = self._load_symbol_map()
        alias = m.get(raw.upper())
        if alias:
            raw = alias

        # 2) existe tal-qual?
        try:
            if MT5.symbol_info(raw) is not None:
                return raw
        except Exception:
            pass

        # 3) match case-insensitive no terminal
        try:
            infos = MT5.symbols_get("*") or []
            low = raw.lower()
            for i in infos:
                n = getattr(i, "name", None)
                if n and n.lower() == low:
                    return n  # devolve com case correto do terminal
        except Exception:
            pass

        # 4) heurística comum: USDT → USD
        if raw.upper().endswith("USDT"):
            cand = raw[:-4] + "USD"
            try:
                if MT5.symbol_info(cand) is not None:
                    return cand
            except Exception:
                pass

        # 5) fallback
        return raw

    # ---------- terminal control wrappers ----------
    def ensure_terminal_running(self, max_wait_sec: int = 60) -> None:
        _ensure_terminal_running(self.exe_path, self.base_dir, max_wait_sec=max_wait_sec)

    def attach_specific(self, max_wait_sec: int = 60) -> None:
        _attach_specific(self.exe_path, self.base_dir, max_wait_sec=max_wait_sec)

    # ---------- lifecycle ----------
    def ensure_up(self) -> None:
        try:
            ti = MT5.terminal_info()
            if ti:
                return
        except Exception:
            pass

        try:
            MT5.shutdown()
        except Exception:
            pass

        self.ensure_terminal_running()
        self.attach_specific()

    # ---------- symbols ----------
    def ensure_symbol(self, symbol: str) -> str:
        """
        Garante que o símbolo (já resolvido) existe e está visível no Market Watch.
        Retorna o próprio símbolo, para encadear de forma conveniente.
        """
        info = MT5.symbol_info(symbol)
        if info is None:
            raise RuntimeError(f"symbol '{symbol}' not found")
        if not info.visible:
            if not MT5.symbol_select(symbol, True):
                raise RuntimeError(f"symbol_select failed for '{symbol}'")
        return symbol