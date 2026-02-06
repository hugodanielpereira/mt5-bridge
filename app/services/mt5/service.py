# app/services/mt5/service.py
from __future__ import annotations

import os
import re
import inspect
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.common.env_loader import load_instance_env

load_instance_env(override=False)

from .session import MT5Session
from .marketdata import MarketData
from .orders import Orders
from .history import History
from .diag import diag as _diag


def _env_str(name: str, default: str = "") -> str:
    v = os.getenv(name, "")
    v = v.strip()
    return v if v else default


def _as_bool(v: Optional[str], default: bool = False) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on", "y")


def _split_exe_args(raw: str) -> tuple[str, str]:
    """
    Aceita:
      - "C:\\Program Files\\MetaTrader 5\\terminal64.exe /portable"
      - "terminal64.exe" etc
    """
    s = (raw or "").strip()
    if not s:
        return "", ""
    m = re.match(r'^\s*"?(.*?\.exe)"?\s*(.*)$', s, flags=re.IGNORECASE)
    if not m:
        return s, ""
    exe, extra = m.group(1), (m.group(2) or "")
    return exe, extra.strip()


class MT5Service:
    """
    Fachada MT5: market + orders + history

    MODOS:
      - MT5_MODE=webrequest (default recomendado no Bottles):
          O bridge NÃO controla o MT5 via MetaTrader5 Python API.
          O EA comunica com o bridge via HTTP WebRequest.
      - MT5_MODE=native:
          (legado) usa MetaTrader5 Python package.
    """

    def __init__(self, *args, **kwargs):
        # ---- mode ----------------------------------------------------------
        self.mode = _env_str("MT5_MODE", "webrequest").lower().strip()
        self.webrequest_mode = self.mode in ("webrequest", "http", "ea", "bridge")

        # ambiente live/prod vs dev/demo (mantém a tua lógica)
        env = (_env_str("ENVIRONMENT", _env_str("BRIDGE_ENV", "demo"))).lower()
        self.is_live = env in ("live", "prod", "production")

        # terminal path (ainda mantemos parsing, mas em webrequest não é usado)
        raw_path = (
            os.getenv("MT5_TERMINAL_PATH")
            or (os.getenv("MT5_TERMINAL_PATH_LIVE") if self.is_live else os.getenv("MT5_TERMINAL_PATH_DEMO"))
            or ""
        )

        exe, inline_args = _split_exe_args(raw_path)
        extra_args = _env_str("MT5_TERMINAL_ARGS", "")
        args_joined = " ".join(x for x in (inline_args, extra_args) if x).strip()

        self.require_portable: bool = _as_bool(os.getenv("REQUIRE_PORTABLE"), False)
        self.kill_non_portable: bool = _as_bool(
            os.getenv("KILL_NON_PORTABLE_LIVE" if self.is_live else "KILL_NON_PORTABLE_DEMO"), False
        )

        if self.require_portable and "/portable" not in args_joined.lower():
            args_joined = (args_joined + " /portable").strip()

        # Em webrequest não precisamos do exe, mas mantemos um default seguro
        if not exe:
            exe = "terminal64.exe"

        self.exe_path: str = exe
        self.exe_args: str = args_joined

        # creds (podem continuar a existir, mas em webrequest o bridge não faz login)
        self.login: Optional[str] = os.getenv("MT5_LOGIN_LIVE" if self.is_live else "MT5_LOGIN_DEMO")
        self.password: Optional[str] = os.getenv("MT5_PASSWORD_LIVE" if self.is_live else "MT5_PASSWORD_DEMO")
        self.server: Optional[str] = os.getenv("MT5_SERVER_LIVE" if self.is_live else "MT5_SERVER_DEMO")

        # ---- session -------------------------------------------------------
        session_kwargs: Dict[str, Any] = {
            "exe_path": Path(self.exe_path),
            "exe": self.exe_path,
            "login": self.login,
            "password": self.password,
            "server": self.server,
            "terminal_args": self.exe_args,
            "args": self.exe_args,
            "require_portable": self.require_portable,
            "kill_non_portable": self.kill_non_portable,
        }

        # cria MT5Session (agora é webrequest-first)
        try:
            sig = inspect.signature(MT5Session)  # type: ignore[arg-type]
            allowed = set(sig.parameters.keys())
            filtered = {k: v for k, v in session_kwargs.items() if k in allowed}
            self.session = MT5Session(**filtered)  # type: ignore[call-arg]
        except TypeError:
            self.session = MT5Session(Path(self.exe_path), self.login, self.password, self.server)

        # push attrs compat (não rebenta se não existirem)
        for k, v in session_kwargs.items():
            if hasattr(self.session, k):
                try:
                    setattr(self.session, k, v)
                except Exception:
                    pass

        # ---- sub-services --------------------------------------------------
        # NOTA: se estes módulos ainda estiverem a importar MetaTrader5,
        # vais ter de os converter também para webrequest.
        self.market = MarketData(self.session)
        self.history = History(self.session)
        self.orders = Orders(self.session)

        self.connected: bool = False

        # ---- native MT5 module (lazy) -------------------------------------
        self._mt5 = None  # carregado só se MT5_MODE=native

    # ----------------------------------------------------------------------
    # Native loader (legado) - só quando MT5_MODE=native
    # ----------------------------------------------------------------------
    def _load_native_mt5(self):
        if self._mt5 is not None:
            return self._mt5
        try:
            import MetaTrader5 as MT5  # type: ignore
        except Exception as e:
            raise RuntimeError(f"MetaTrader5 import failed (MT5_MODE=native): {e!r}")
        self._mt5 = MT5
        return MT5

    # ---- lifecycle --------------------------------------------------------
    def initialize(self) -> bool:
        """
        webrequest:
          - não faz shutdown/login
          - apenas marca como conectado (o “up” real vem do EA chamar o bridge)
        native:
          - comportamento antigo (initialize/login)
        """
        try:
            # WebRequest mode: NO-OP (não tocar em MT5 python package)
            if self.webrequest_mode:
                # aqui podes opcionalmente validar alguma coisa (ex: env API key)
                self.session.ensure_up()
                self.connected = True
                print("[MT5] webrequest mode: ready (EA -> HTTP WebRequest)")
                return True

            # Native mode (legado)
            MT5 = self._load_native_mt5()

            try:
                MT5.shutdown()
            except Exception:
                pass

            self.session.ensure_up()

            # login opcional
            if self.session.login and self.session.password and self.session.server:
                if not MT5.login(int(self.session.login), password=self.session.password, server=self.session.server):
                    code, msg = MT5.last_error()
                    print(f"[MT5][ERR] login failed: {code} {msg}")
                    self.connected = False
                    return False

            print("[MT5] ligado com sucesso (native)")
            self.connected = True
            return True

        except Exception as e:
            print(f"[MT5][EXC] initialize: {e}")
            self.connected = False
            return False

    def ensure_up(self) -> None:
        self.session.ensure_up()

    # ---- market façade ----------------------------------------------------
    def account_info(self) -> Dict[str, Any]:
        return self.market.account_info()

    def list_symbols(self) -> List[Dict[str, Any]]:
        return self.market.list_symbols()

    def positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.market.positions(symbol=symbol)

    def ohlcv(self, *args, **kwargs) -> List[Dict[str, Any]]:
        return self.market.ohlcv(*args, **kwargs)

    def symbol_info(self, symbol: str):
        return self.market.symbol_info(symbol)

    def quote(self, symbol: str):
        return self.market.quote(symbol)

    # ---- orders façade ----------------------------------------------------
    def order_market(self, *args, **kwargs) -> Dict[str, Any]:
        return self.orders.order_market(*args, **kwargs)

    def modify_position(self, ticket: int, sl: float | None = None, tp: float | None = None) -> Dict[str, Any]:
        return self.orders.modify_position(ticket=ticket, sl=sl, tp=tp)

    def close_symbol(self, symbol: str):
        return self.orders.close_symbol(symbol)

    def close_ticket(self, ticket: int, volume: float | None = None):
        return self.orders.close_ticket(ticket=ticket, volume=volume)

    # ---- history / diag ---------------------------------------------------
    def diag(self) -> Dict[str, Any]:
        return _diag(self.session)

    def history_deals_get(self, frm, to):
        return self.history.history_deals_get(frm, to)

    def deals_recent(self, days: int = 1):
        return self.history.deals_recent(days)

    def orders_recent(self, days: int = 1):
        return self.history.orders_recent(days)