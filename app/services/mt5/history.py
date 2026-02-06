# app/services/mt5/history.py
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from typing import Any


def _mt5_mode() -> str:
    return (os.getenv("MT5_MODE", "webrequest") or "webrequest").strip().lower()


def _is_webrequest_mode() -> bool:
    return _mt5_mode() in ("webrequest", "http", "ea", "bridge")


def _is_native_mode() -> bool:
    return _mt5_mode() in ("native", "mt5", "metatrader5")


class History:
    """
    History facade.

    - MT5_MODE=webrequest (default): NOT supported here (bridge is EA->HTTP first).
    - MT5_MODE=native: uses MetaTrader5 Python API (legacy).
    """

    def __init__(self, session: Any):
        self.session = session
        self._mt5_mod = None  # lazy native-only

    # ----------------------------------------------------------------------
    # Native loader (legado) - só quando MT5_MODE=native
    # ----------------------------------------------------------------------
    def _mt5(self):
        if self._mt5_mod is not None:
            return self._mt5_mod
        try:
            import MetaTrader5 as MT5  # type: ignore
        except Exception as e:
            raise RuntimeError(
                f"MetaTrader5 import failed (MT5_MODE=native). "
                f"Install it in the python env being used, or switch MT5_MODE=webrequest. "
                f"err={e!r}"
            )
        self._mt5_mod = MT5
        return MT5

    def _require_native(self, what: str) -> None:
        mode = _mt5_mode()
        if _is_webrequest_mode():
            raise RuntimeError(
                f"{what} not supported in MT5_MODE={mode}. "
                f"Bridge is WebRequest-first (EA talks to bridge). "
                f"Set MT5_MODE=native to use MetaTrader5 Python API."
            )
        # se alguém meter lixo tipo MT5_MODE=foo, também bloqueia (fail-fast)
        if not _is_native_mode():
            raise RuntimeError(
                f"{what} requires MT5_MODE=native (got MT5_MODE={mode!r})."
            )

    # ----------------------------------------------------------------------
    # API
    # ----------------------------------------------------------------------
    def history_deals_get(self, frm: datetime, to: datetime) -> Any:
        self._require_native("history_deals_get")
        MT5 = self._mt5()

        if hasattr(self.session, "ensure_up"):
            self.session.ensure_up()

        return MT5.history_deals_get(frm, to)

    def deals_recent(self, days: int = 1) -> Any:
        self._require_native("deals_recent")
        end = datetime.now(tz=timezone.utc)
        start = end - timedelta(days=int(days))
        return self.history_deals_get(start, end)

    def orders_recent(self, days: int = 1) -> Any:
        self._require_native("orders_recent")
        MT5 = self._mt5()

        end = datetime.now(tz=timezone.utc)
        start = end - timedelta(days=int(days))

        if hasattr(self.session, "ensure_up"):
            self.session.ensure_up()

        return MT5.history_orders_get(start, end)