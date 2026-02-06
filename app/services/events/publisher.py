# app/services/events/publisher.py
from __future__ import annotations

import json
import os
import time
import logging
from typing import Any, Dict, Optional

try:
    from redis import Redis  # type: ignore
except Exception:  # pragma: no cover
    Redis = None  # type: ignore

log = logging.getLogger("events-publisher")


def _now_ms() -> int:
    return int(time.time() * 1000)


class EventsPublisher:
    """
    Publica eventos num Redis Stream (XADD).
    Não deve rebentar requests caso Redis falhe: só loga warning.
    """

    def __init__(
        self,
        redis_url: Optional[str] = None,
        stream: Optional[str] = None,
        maxlen: Optional[int] = None,
        enabled: Optional[bool] = None,
    ):
        self.redis_url = (redis_url or os.getenv("REDIS_URL") or "").strip()
        self.stream = (stream or os.getenv("EVENTS_STREAM_KEY") or "mt5:events").strip()
        self.maxlen = int(os.getenv("EVENTS_MAXLEN", str(maxlen or 10000)))
        self.enabled = (
            enabled
            if enabled is not None
            else (os.getenv("EVENTS_ENABLE", "1").strip().lower() not in ("0", "false", "no"))
        )

        self._r: Optional["Redis"] = None
        self._init_error: Optional[str] = None

        if not self.enabled:
            return

        if not self.redis_url:
            self._init_error = "REDIS_URL missing"
            return

        if Redis is None:
            self._init_error = "python package 'redis' not installed"
            return

        try:
            self._r = Redis.from_url(self.redis_url, decode_responses=True)
            # sanity ping (não demasiado caro)
            self._r.ping()
        except Exception as e:
            self._init_error = f"redis connect failed: {e!r}"
            self._r = None

        if self._init_error:
            log.warning("EventsPublisher disabled: %s", self._init_error)

    @property
    def ok(self) -> bool:
        return self.enabled and self._r is not None

    def publish(self, *, iid: str, scope: str, event_type: str, payload: Dict[str, Any]) -> None:
        if not self.ok:
            return

        ev = {
            "ts_ms": _now_ms(),
            "iid": iid,
            "scope": scope,
            "event_type": event_type,
            "payload": payload,
        }

        try:
            # guard: stream fields têm de ser strings
            fields = {"json": json.dumps(ev, ensure_ascii=False, separators=(",", ":"))}

            # approximate trimming (mais barato)
            self._r.xadd(self.stream, fields, maxlen=self.maxlen, approximate=True)  # type: ignore[union-attr]
        except Exception as e:
            log.warning("publish failed: stream=%s type=%s err=%r", self.stream, event_type, e)