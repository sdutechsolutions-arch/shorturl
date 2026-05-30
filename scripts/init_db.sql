CREATE TABLE IF NOT EXISTS links (
    id              BIGSERIAL PRIMARY KEY,
    slug            TEXT UNIQUE NOT NULL,
    target_url      TEXT NOT NULL,
    title           TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    expires_at      TIMESTAMPTZ,
    click_count     BIGINT NOT NULL DEFAULT 0,
    last_clicked_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_links_slug_active ON links(slug) WHERE is_active;
CREATE INDEX IF NOT EXISTS idx_links_created_at  ON links(created_at DESC);

CREATE OR REPLACE FUNCTION links_touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_links_updated_at ON links;
CREATE TRIGGER trg_links_updated_at
    BEFORE UPDATE ON links
    FOR EACH ROW
    EXECUTE FUNCTION links_touch_updated_at();
