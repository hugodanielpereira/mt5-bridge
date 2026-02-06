# app/common/service_registry.py
from __future__ import annotations

from typing import Any, Optional
import threading

_lock = threading.RLock()
_svc: Any = None


def set_service(svc: Any) -> None:
    """Regista uma instância global (ex.: MT5Service) criada no startup."""
    global _svc
    with _lock:
        _svc = svc


def get_service() -> Any:
    """Devolve a instância global. Lança erro se ainda não existir."""
    with _lock:
        if _svc is None:
            raise RuntimeError("Service not initialized yet (startup not finished?)")
        return _svc


def clear_service() -> None:
    """(Opcional) Limpa a instância global. Útil para testes/reloads controlados."""
    global _svc
    with _lock:
        _svc = None


def try_get_service() -> Optional[Any]:
    """(Opcional) Devolve serviço ou None."""
    with _lock:
        return _svc