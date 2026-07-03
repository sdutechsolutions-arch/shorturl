-- 002_analytics.sql — per-hit event log + persisted QR design.
-- Idempotent; safe to re-run.

-- ---------- per-hit event log ----------
CREATE TABLE IF NOT EXISTS click_events (
    id           BIGSERIAL PRIMARY KEY,
    link_id      BIGINT NOT NULL REFERENCES links(id) ON DELETE CASCADE,
    ts           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source       TEXT NOT NULL,             -- 'qr' | 'link' | 'direct'
    ip           INET,                      -- raw client IP
    user_agent   TEXT,
    device       TEXT,                      -- 'mobile' | 'tablet' | 'pc' | 'bot' | 'other'
    browser      TEXT,
    os           TEXT,
    is_bot       BOOLEAN NOT NULL DEFAULT FALSE,
    referrer     TEXT,
    country      TEXT,                      -- ISO code
    country_name TEXT,
    city         TEXT
);

CREATE INDEX IF NOT EXISTS idx_click_events_link_ts     ON click_events(link_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_click_events_link_source ON click_events(link_id, source);
CREATE INDEX IF NOT EXISTS idx_click_events_ts          ON click_events(ts DESC);

-- ---------- persisted QR design (one QR per link) ----------
ALTER TABLE links ADD COLUMN IF NOT EXISTS qr_fg   TEXT    NOT NULL DEFAULT '#000000';
ALTER TABLE links ADD COLUMN IF NOT EXISTS qr_bg   TEXT    NOT NULL DEFAULT '#FFFFFF';
ALTER TABLE links ADD COLUMN IF NOT EXISTS qr_size INT     NOT NULL DEFAULT 512;
ALTER TABLE links ADD COLUMN IF NOT EXISTS qr_ec   TEXT    NOT NULL DEFAULT 'M';    -- L|M|Q|H
ALTER TABLE links ADD COLUMN IF NOT EXISTS qr_logo BOOLEAN NOT NULL DEFAULT FALSE;
