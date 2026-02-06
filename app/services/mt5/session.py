# app/services/mt5/session.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional
import os


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name, "")
    if v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


def _env_str(name: str, default: str = "") -> str:
    v = os.getenv(name, "")
    return v.strip() if v.strip() else default


@dataclass
class MT5Session:
    """
    WebRequest-first session (Bottles friendly).

    O bridge NÃO controla o MT5 via MetaTrader5 Python API.
    Em vez disso:
      - O EA no MT5 comunica com o bridge via HTTP WebRequest.
      - O bridge apenas mantém estado / validações leves.

    Se no futuro quiseres voltar ao modo MetaTrader5 API, fazemos isso
    como um backend opcional separado — mas por agora fica 100% HTTP.
    """

    exe_path: Path
    login: Optional[str]
    password: Optional[str]
    server: Optional[str]

    # setados externamente (compat com o teu service)
    require_portable: bool = False
    terminal_args: str = ""

    # cache
    _symmap: Optional[Dict[str, str]] = None

    def __init__(
        self,
        exe_path: Path,
        login: Optional[str],
        password: Optional[str],
        server: Optional[str],
        **kwargs,
    ) -> None:
        self.exe_path = Path(exe_path)
        self.login = login
        self.password = password
        self.server = server
        self._symmap = None

        # compat com o teu wiring atual
        self.require_portable = bool(kwargs.get("require_portable", False))
        self.terminal_args = str(
            kwargs.get("terminal_args", "") or kwargs.get("args", "") or ""
        ).strip()

    # -------------------------
    # Portable / args (mantido só por compat)
    # -------------------------
    def _wants_portable(self) -> bool:
        if self.require_portable:
            return True
        a = (self.terminal_args or "").lower()
        return "/portable" in a

    # -------------------------
    # Symbol map (local)
    # -------------------------
    def _load_symbol_map(self) -> Dict[str, str]:
        if self._symmap is not None:
            return self._symmap

        import json

        try:
            import yaml  # type: ignore
        except Exception:
            yaml = None  # type: ignore

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
                    mp = {
                        str(k).upper(): str(v).strip()
                        for k, v in data.items()
                        if str(k).strip()
                    }
                    break
            except Exception:
                pass

        self._symmap = mp
        return mp

    def resolve_symbol(self, symbol: str) -> str:
        """
        Em modo WebRequest, o bridge NÃO consegue validar se o símbolo existe
        no terminal via API. Fazemos só alias mapping e devolvemos.
        """
        raw = (symbol or "").strip()
        if not raw:
            return raw

        m = self._load_symbol_map()
        alias = m.get(raw.upper())
        return alias if alias else raw

    # -------------------------
    # Lifecycle (NO-OP em WebRequest)
    # -------------------------
    def ensure_up(self) -> None:
        """
        WebRequest mode: não faz initialize/attach/spawn.
        Assumimos que:
          - MT5 já está aberto (via Bottles)
          - EA está a fazer WebRequest para o bridge
        """
        # opcional: se quiseres obrigar "heartbeat" do EA, isso é noutra camada
        return

    def ensure_symbol(self, symbol: str) -> str:
        """
        WebRequest mode: não valida visibilidade/existência no terminal.
        Só resolve alias e devolve.
        """
        return self.resolve_symbol(symbol)