# app/services/mt5/service.py
from __future__ import annotations

import os
import re
import inspect
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from .session import MT5Session
from .marketdata import MarketData
from .trade import Trade          # mantém para compatibilidade com código antigo
from .orders import Orders        # <- NOVO: usa esta para as ordens via API
from .history import History
from .diag import diag as _diag  # função existente

# Em DEV queremos que o .env tenha precedência sobre o ambiente/sistema
load_dotenv(override=True)

try:
    import MetaTrader5 as MT5
except Exception as e:
    raise RuntimeError(f"MetaTrader5 import failed: {e!r}")


def _split_exe_args(raw: str) -> tuple[str, str]:
    """
    Aceita:
      C:\\...\terminal64.exe /portable
      "C:\\...\terminal64.exe" /portable
    Devolve (exe, args_inline)
    """
    s = (raw or "").strip()
    if not s:
        return "", ""
    m = re.match(r'^\s*"?(.*?\.exe)"?\s*(.*)$', s, flags=re.IGNORECASE)
    if not m:
        return s, ""
    exe, extra = m.group(1), (m.group(2) or "")
    return exe, extra.strip()


def _as_bool(v: Optional[str], default: bool = False) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on", "y")


class MT5Service:
    """
    Serviço de alto nível para o MT5, responsável por:
      - Resolver DEMO/LIVE a partir de ENVIRONMENT/BRIDGE_ENV
      - Montar caminho do terminal + args (inclui /portable se requerido)
      - Instanciar MT5Session com kwargs compatíveis (via introspeção)
      - Expor uma façade (market/trade/history) usada pelos controladores
    Env relevantes:
      ENVIRONMENT=demo|live
      MT5_TERMINAL_PATH_DEMO, MT5_TERMINAL_PATH_LIVE ou MT5_TERMINAL_PATH
      MT5_TERMINAL_ARGS
      REQUIRE_PORTABLE=0|1
      KILL_NON_PORTABLE_DEMO=0|1, KILL_NON_PORTABLE_LIVE=0|1
      MT5_LOGIN_DEMO, MT5_PASSWORD_DEMO, MT5_SERVER_DEMO
      MT5_LOGIN_LIVE, MT5_PASSWORD_LIVE, MT5_SERVER_LIVE
    """
    def __init__(self, *args, **kwargs):
        # --- ambiente -------------------------------------------------------
        env = (os.getenv("ENVIRONMENT") or os.getenv("BRIDGE_ENV") or "demo").strip().lower()
        self.is_live: bool = (env == "live")

        # --- caminhos / argumentos -----------------------------------------
        raw_path = (
            os.getenv("MT5_TERMINAL_PATH_LIVE") if self.is_live else os.getenv("MT5_TERMINAL_PATH_DEMO")
        ) or os.getenv("MT5_TERMINAL_PATH") or ""
        exe, inline_args = _split_exe_args(raw_path)
        extra_args = (os.getenv("MT5_TERMINAL_ARGS") or "").strip()
        args_joined = " ".join(x for x in (inline_args, extra_args) if x).strip()

        # --- flags ----------------------------------------------------------
        self.require_portable: bool = _as_bool(os.getenv("REQUIRE_PORTABLE"), False)
        self.kill_non_portable: bool = _as_bool(
            os.getenv("KILL_NON_PORTABLE_LIVE" if self.is_live else "KILL_NON_PORTABLE_DEMO"),
            False,
        )

        if self.require_portable and "/portable" not in args_joined.lower():
            args_joined = (args_joined + " /portable").strip()

        # --- validações -----------------------------------------------------
        if not exe or not Path(exe).exists():
            raise RuntimeError(f"MT5_TERMINAL_PATH inválido: {raw_path!r}")

        # guardas para diagnóstico
        self.exe_path: str = exe                      # string do .env (mantemos para logging)
        self.exe_args: str = args_joined

        # --- credenciais por ambiente --------------------------------------
        self.login: Optional[str] = os.getenv("MT5_LOGIN_LIVE" if self.is_live else "MT5_LOGIN_DEMO")
        self.password: Optional[str] = os.getenv("MT5_PASSWORD_LIVE" if self.is_live else "MT5_PASSWORD_DEMO")
        self.server: Optional[str] = os.getenv("MT5_SERVER_LIVE" if self.is_live else "MT5_SERVER_DEMO")

        # --- construir kwargs para MT5Session -------------------------------
        session_kwargs: Dict[str, Any] = {
            # preferimos Path para exe_path; mantemos alias "exe" por compatibilidade
            "exe_path": Path(self.exe_path),
            "exe": self.exe_path,
            # credenciais
            "login": self.login,
            "password": self.password,
            "server": self.server,
            # extras comuns (só entram se o __init__ aceitar)
            "terminal_args": self.exe_args,
            "args": self.exe_args,
            "require_portable": self.require_portable,
            "kill_non_portable": self.kill_non_portable,
        }

        # filtra só o que o __init__ realmente aceita
        try:
            sig = inspect.signature(MT5Session)  # type: ignore[arg-type]
            allowed = set(sig.parameters.keys())
            filtered = {k: v for k, v in session_kwargs.items() if k in allowed}
            self.session = MT5Session(**filtered)  # type: ignore[call-arg]
        except TypeError:
            # fallback mínimo (ordem posicional legacy): exe_path, login, password, server
            self.session = MT5Session(self.exe_path, self.login, self.password, self.server)  # type: ignore[misc]

        # fallback universal: se a classe tiver atributos, setta-os
        for k, v in session_kwargs.items():
            if hasattr(self.session, k):
                try:
                    setattr(self.session, k, v)
                except Exception:
                    pass

        # módulos da façade
        self.market = MarketData(self.session)
        self.trade = Trade(self.session)      # <- legado (caso algum código ainda use)
        self.orders = Orders(self.session)    # <- NOVO: API consolidada para ordens
        self.history = History(self.session)
        self.connected: bool = False

    # ---- lifecycle ----------------------------------------------------------
    def initialize(self) -> bool:
        try:
            try:
                MT5.shutdown()
            except Exception:
                pass

            # arranca terminal e conecta
            self.session.ensure_terminal_running()
            self.session.attach_specific()

            if self.session.login and self.session.password and self.session.server:
                if not MT5.login(int(self.session.login), password=self.session.password, server=self.session.server):
                    code, msg = MT5.last_error()
                    print(f"[MT5][ERR] login failed: {code} {msg}")
                    self.connected = False
                    return False

            print("[MT5] ligado com sucesso")
            self.connected = True
            return True
        except Exception as e:
            print(f"[MT5][EXC] initialize: {e}")
            self.connected = False
            return False

    def ensure_up(self) -> None:
        self.session.ensure_up()

    # ---- façade API ---------------------------------------------------------
    def account_info(self) -> Dict[str, Any]:
        return self.market.account_info()

    def list_symbols(self) -> List[Dict[str, Any]]:
        return self.market.list_symbols()

    def positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.market.positions(symbol=symbol)

    def ohlcv(self, *args, **kwargs) -> List[Dict[str, Any]]:
        return self.market.ohlcv(*args, **kwargs)

    # ==== ORDERS / TRADE (via Orders) =======================================
    def order_market(self, *args, **kwargs) -> Dict[str, Any]:
        """
        Envia ordem de mercado robusta (via Orders.order_market).
        """
        return self.orders.order_market(*args, **kwargs)

    def modify_position(self, ticket: int, sl: float | None = None, tp: float | None = None) -> Dict[str, Any]:
        """
        Atualiza SL/TP de uma posição existente (via Orders.modify_position).
        """
        return self.orders.modify_position(ticket=ticket, sl=sl, tp=tp)

    def close_symbol(self, symbol: str):
        """
        Fecha todas as posições de um símbolo (via Orders.close_symbol).
        """
        return self.orders.close_symbol(symbol)

    def close_ticket(self, ticket: int, volume: float | None = None):
        """Fecho total ou parcial de uma posição."""
        return self.trade.close_ticket(ticket, volume)

    def modify_position(self, ticket: int, sl: float | None = None, tp: float | None = None):
        """Atualiza SL/TP de uma posição existente."""
        return self.trade.modify_position(ticket, sl=sl, tp=tp)

    # ==== HISTORY / DIAG =====================================================
    def diag(self) -> Dict[str, Any]:
        return _diag(self.session)

    def history_deals_get(self, frm, to):
        return self.history.history_deals_get(frm, to)

    def deals_recent(self, days: int = 1):
        return self.history.deals_recent(days)

    def orders_recent(self, days: int = 1):
        return self.history.orders_recent(days)

    def symbol_info(self, symbol: str):
        return self.market.symbol_info(symbol)

    def quote(self, symbol: str):
        return self.market.quote(symbol)