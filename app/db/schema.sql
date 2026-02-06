CREATE TABLE IF NOT EXISTS bridge_events (
  id            BIGSERIAL PRIMARY KEY,
  ts_ms         BIGINT,
  iid           TEXT,
  scope         TEXT,
  event_type    TEXT,
  payload       JSONB,
  redis_stream  TEXT,
  redis_id      TEXT UNIQUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_bridge_events_created_at ON bridge_events(created_at);
CREATE INDEX IF NOT EXISTS ix_bridge_events_iid ON bridge_events(iid);
CREATE INDEX IF NOT EXISTS ix_bridge_events_event_type ON bridge_events(event_type);