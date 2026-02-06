# app/services/events_worker/worker.py
from __future__ import annotations

import os
import json
import time
import signal
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from redis import Redis
import psycopg

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL)
log = logging.getLogger("events-worker")

STOP = False


def _on_sigterm(*_):
    global STOP
    STOP = True
    log.warning("SIGTERM received -> stopping...")


def env(name: str, default: str | None = None) -> str:
    v = os.getenv(name, default)
    if v is None or str(v).strip() == "":
        raise RuntimeError(f"Missing env: {name}")
    return v


def _find_schema_path() -> Path:
    """
    Resolve schema.sql dentro do container.
    Preferência:
      1) DB_SCHEMA_PATH (env)
      2) /app/db/schema.sql (típico no teu Dockerfile COPY -> /app)
      3) relativo a este ficheiro (fallback dev)
    """
    p = os.getenv("DB_SCHEMA_PATH", "").strip()
    if p:
        return Path(p)

    candidates = [
        Path("/app/db/schema.sql"),
        # fallback dev: .../app/services/events_worker/worker.py -> .../app/db/schema.sql
        Path(__file__).resolve().parents[2] / "db" / "schema.sql",
        # fallback antigo, se a tua tree for diferente
        Path(__file__).resolve().parents[3] / "db" / "schema.sql",
    ]
    for c in candidates:
        if c.exists():
            return c
    # devolve o mais provável para ajudar no erro
    return candidates[0]


def ensure_schema(conn: psycopg.Connection):
    schema_path = _find_schema_path()
    if not schema_path.exists():
        raise RuntimeError(f"schema.sql not found at {schema_path}")

    sql = schema_path.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
    log.info("DB schema ensured (ok) path=%s", schema_path)


def ensure_consumer_group(r: Redis, stream: str, group: str):
    try:
        r.xgroup_create(name=stream, groupname=group, id="0-0", mkstream=True)
        log.info("Created consumer group: stream=%s group=%s", stream, group)
    except Exception as e:
        msg = str(e)
        if "BUSYGROUP" in msg:
            log.info("Consumer group exists: stream=%s group=%s", stream, group)
        else:
            raise


def _now_ms() -> int:
    return int(time.time() * 1000)


def parse_event(fields: Dict[str, str]) -> Dict[str, Any]:
    """
    Suporta 2 formatos:

    A) NOVO (recomendado): fields = {"json": "<json>"}
       onde json contém:
         {ts_ms, iid, scope, event_type, payload}

    B) LEGADO: fields com keys planas (type/event_type, payload, ts_ms, iid, scope...)
    """
    # --- formato novo ---
    raw_json = fields.get("json")
    if raw_json:
        try:
            ev = json.loads(raw_json)
            if isinstance(ev, dict):
                # normaliza mínimos
                ev.setdefault("ts_ms", _now_ms())
                ev.setdefault("event_type", ev.get("type") or "unknown")
                ev.setdefault("payload", {})
                return {
                    "ts_ms": int(ev.get("ts_ms") or 0) or None,
                    "iid": ev.get("iid"),
                    "scope": ev.get("scope"),
                    "event_type": ev.get("event_type") or ev.get("type") or "unknown",
                    "payload": ev.get("payload") if isinstance(ev.get("payload"), (dict, list)) else {"_raw": ev.get("payload")},
                }
        except Exception:
            # se falhar, cai no legado
            pass

    # --- formato legado ---
    event_type = fields.get("type") or fields.get("event_type") or "unknown"
    ts_ms = int(fields.get("ts_ms") or fields.get("timestamp_ms") or 0) or None
    iid = fields.get("iid") or fields.get("IID") or fields.get("bridge_id")
    scope = fields.get("scope")

    payload_raw = fields.get("payload")
    if payload_raw:
        try:
            payload = json.loads(payload_raw)
        except Exception:
            payload = {"_raw": payload_raw}
    else:
        payload = {k: v for k, v in fields.items() if k not in ("type", "event_type", "ts_ms", "timestamp_ms", "iid", "IID", "scope", "json")}

    return {
        "ts_ms": ts_ms,
        "iid": iid,
        "scope": scope,
        "event_type": event_type,
        "payload": payload,
    }


def insert_event(conn: psycopg.Connection, stream: str, redis_id: str, ev: Dict[str, Any]):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO bridge_events (ts_ms, iid, scope, event_type, payload, redis_stream, redis_id)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)
            ON CONFLICT (redis_id) DO NOTHING
            """,
            (
                ev.get("ts_ms"),
                ev.get("iid"),
                ev.get("scope"),
                ev.get("event_type"),
                json.dumps(ev.get("payload") or {}, ensure_ascii=False),
                stream,
                redis_id,
            ),
        )


def main():
    signal.signal(signal.SIGTERM, _on_sigterm)
    signal.signal(signal.SIGINT, _on_sigterm)

    iid = env("IID")
    redis_url = env("REDIS_URL")
    db_url = env("DATABASE_URL")

    stream = os.getenv("EVENTS_STREAM_KEY", "mt5:events")
    group = os.getenv("EVENTS_CONSUMER_GROUP", "events_worker")
    consumer = os.getenv("EVENTS_CONSUMER_NAME", iid.lower())

    block_ms = int(os.getenv("EVENTS_BLOCK_MS", "2000"))
    count = int(os.getenv("EVENTS_READ_COUNT", "50"))

    # batch commits (muito importante para performance)
    commit_every = int(os.getenv("EVENTS_COMMIT_EVERY", "50"))  # commit a cada N inserts
    commit_max_ms = int(os.getenv("EVENTS_COMMIT_MAX_MS", "2000"))  # ou a cada X ms

    log.info("events-worker starting: IID=%s stream=%s group=%s consumer=%s", iid, stream, group, consumer)
    log.info("REDIS_URL=%s", redis_url)
    log.info("DATABASE_URL=%s", db_url)

    r = Redis.from_url(redis_url, decode_responses=True)

    with psycopg.connect(db_url) as conn:
        ensure_schema(conn)
        ensure_consumer_group(r, stream, group)

        pending_inserts = 0
        last_commit_ms = _now_ms()

        while not STOP:
            try:
                resp = r.xreadgroup(
                    groupname=group,
                    consumername=consumer,
                    streams={stream: ">"},
                    count=count,
                    block=block_ms,
                )
                if not resp:
                    # flush por tempo
                    now = _now_ms()
                    if pending_inserts > 0 and (now - last_commit_ms) >= commit_max_ms:
                        conn.commit()
                        log.info("Committed (timer) inserts=%s", pending_inserts)
                        pending_inserts = 0
                        last_commit_ms = now
                    continue

                for stream_name, msgs in resp:
                    for redis_id, fields in msgs:
                        try:
                            ev = parse_event(fields)
                            insert_event(conn, stream_name, redis_id, ev)
                            r.xack(stream_name, group, redis_id)
                            pending_inserts += 1

                            now = _now_ms()
                            if pending_inserts >= commit_every or (now - last_commit_ms) >= commit_max_ms:
                                conn.commit()
                                log.info("Committed inserts=%s", pending_inserts)
                                pending_inserts = 0
                                last_commit_ms = now

                        except Exception:
                            log.exception("Failed to process event redis_id=%s fields=%s", redis_id, fields)
                            conn.rollback()
                            # não ACK -> fica pending e dá retry
                            time.sleep(0.1)

            except Exception:
                log.exception("Worker loop error; sleeping...")
                time.sleep(1.0)

        # flush final
        try:
            if pending_inserts > 0:
                conn.commit()
        except Exception:
            pass

    log.info("events-worker stopped")


if __name__ == "__main__":
    main()