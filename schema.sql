-- ============================================================================
-- Developer Freebies Aggregation System — PostgreSQL Schema (Phase 2)
-- ----------------------------------------------------------------------------
-- Target: PostgreSQL 14+ on Heroku Postgres / AWS RDS / local Docker.
--
-- ⚠️  pgvector note:
--     Heroku Postgres does NOT ship the `vector` extension. Embeddings are
--     therefore stored as REAL[] and cosine similarity is computed app-side
--     (numpy brute-force over the last 90 days is sub-millisecond at
--     single-user scale, <10k offers). If you ever move to RDS (PG15+) or
--     Supabase, apply the optional pgvector upgrade at the bottom.
--
-- Apply with:  psql $DATABASE_URL -f schema.sql
-- ============================================================================

BEGIN;

-- 1. SOURCES ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sources (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,              -- 'telegram:cc-freebies', 'twitter:devrel'
    kind        TEXT NOT NULL CHECK (kind IN ('telegram','twitter','instagram','facebook','rss','web','manual')),
    config      JSONB NOT NULL DEFAULT '{}',       -- platform-specific adapter config
    rate_budget JSONB NOT NULL DEFAULT '{}',       -- {rpm, daily_cap, backoff_s}
    creds_ref   TEXT,                              -- env var / vault key; NEVER store secrets here
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    health      JSONB NOT NULL DEFAULT '{}',       -- {success_rate, last_error, breaker_open}
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2. RAW ITEMS (write-first capture; nothing is ever silently dropped) --------
CREATE TABLE IF NOT EXISTS raw_items (
    id          BIGSERIAL PRIMARY KEY,
    source_id   BIGINT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    external_id TEXT NOT NULL,                     -- platform-native id (msg_id, tweet_id, post_id)
    raw_payload JSONB NOT NULL,                    -- full original payload, pre-normalization
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, external_id)
);

-- Attempt tracking / dead-lettering (crawler/pipeline.py). Without these, any
-- raw_item that never produces an offers row (prefiltered, dead link, llm_rejected,
-- dup, or errored) is re-fetched and re-run through the LLM on EVERY future run,
-- burning API budget on known junk forever. mark_raw_item_attempt() flips
-- permanently_rejected once a verdict is terminal or MAX_ATTEMPTS is reached.
-- Idempotent: safe to re-run `psql $DATABASE_URL -f schema.sql`.
ALTER TABLE raw_items ADD COLUMN IF NOT EXISTS attempts INT NOT NULL DEFAULT 0;
ALTER TABLE raw_items ADD COLUMN IF NOT EXISTS last_attempted_at TIMESTAMPTZ;
ALTER TABLE raw_items ADD COLUMN IF NOT EXISTS last_reject_reason TEXT;
ALTER TABLE raw_items ADD COLUMN IF NOT EXISTS permanently_rejected BOOLEAN NOT NULL DEFAULT FALSE;

-- Keeps the unprocessed scan cheap as the dead-letter pile grows.
CREATE INDEX IF NOT EXISTS idx_raw_items_pending
    ON raw_items (fetched_at DESC) WHERE NOT permanently_rejected;

-- 3. OFFERS (canonical, verified records) --------------------------------------
CREATE TABLE IF NOT EXISTS offers (
    id                  BIGSERIAL PRIMARY KEY,
    raw_item_id         BIGINT REFERENCES raw_items(id),
    url                 TEXT NOT NULL,
    canonical_url       TEXT NOT NULL,             -- tracking params stripped, redirects resolved
    title               TEXT NOT NULL,
    description         TEXT,
    -- Source of truth for this list: crawler/categories.py (CATEGORY_DEFS).
    -- Postgres can't import Python, so it's mirrored here; tests/test_categories.py
    -- asserts the two stay in sync.
    category            TEXT NOT NULL DEFAULT 'other'
                        CHECK (category IN ('cloud','llm','hosting','domain','tools','student','course','coupon',
                                            'ai_tools','coding_agents',
                                            'open_source_repo','llm_api_drop','student_pack','saas_deal',
                                            'other')),
    offer_type          TEXT NOT NULL DEFAULT 'other'
                        CHECK (offer_type IN ('credit','grant','vps','domain','license','subscription','code','giveaway','other')),
    value               NUMERIC(12,2),
    currency            TEXT,
    expires_at          TIMESTAMPTZ,
    requirements        JSONB NOT NULL DEFAULT '[]',   -- ["student email", "18+", ...]
    author_handle       TEXT,                          -- telegram channel / twitter handle
    engagement          JSONB NOT NULL DEFAULT '{}',   -- {views, likes, forwards} if available
    verification_status TEXT NOT NULL DEFAULT 'unconfirmed'
                        CHECK (verification_status IN ('unconfirmed','verified','live','expired','dead','reported')),
    confidence          REAL,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    first_seen          TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen           TIMESTAMPTZ NOT NULL DEFAULT now(),
    popularity          INTEGER NOT NULL DEFAULT 0,
    quality_flags       JSONB NOT NULL DEFAULT '[]',
    raw                 JSONB NOT NULL DEFAULT '{}',   -- full normalized payload (audit)
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_offers_active_cat  ON offers (is_active, category);
CREATE INDEX IF NOT EXISTS idx_offers_expiry      ON offers (expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_offers_canonical   ON offers (canonical_url);
CREATE INDEX IF NOT EXISTS idx_offers_status      ON offers (verification_status) WHERE is_active;

-- 4. DEDUP FINGERPRINTS --------------------------------------------------------
CREATE TABLE IF NOT EXISTS offer_fingerprints (
    offer_id   BIGINT PRIMARY KEY REFERENCES offers(id) ON DELETE CASCADE,
    url_hash   TEXT NOT NULL UNIQUE,                -- sha256(canonical_url)  -> exact dedup
    title_hash TEXT NOT NULL,                       -- sha256(normalized title)
    embedding  REAL[] NOT NULL DEFAULT '{}',        -- 384-dim sentence-transformers vector
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 5. DISPATCH LOG (idempotent, worker-safe delivery) ----------------------------
CREATE TABLE IF NOT EXISTS dispatches (
    id          BIGSERIAL PRIMARY KEY,
    offer_id    BIGINT NOT NULL REFERENCES offers(id) ON DELETE CASCADE,
    channel     TEXT NOT NULL CHECK (channel IN ('discord','email','web')),
    status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sent','failed')),
    attempts    INT NOT NULL DEFAULT 0,
    last_error  TEXT,
    message_meta JSONB NOT NULL DEFAULT '{}',       -- discord message_id, etc.
    claimed_at  TIMESTAMPTZ,                        -- lease for concurrent workers
    sent_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (offer_id, channel)                      -- re-run is a no-op once 'sent'
);
CREATE INDEX IF NOT EXISTS idx_dispatches_queue ON dispatches (channel, status, created_at);

-- 6. CLICKS / SUBSCRIBERS / MEDIA / RUNS ----------------------------------------
CREATE TABLE IF NOT EXISTS clicks (
    id          BIGSERIAL PRIMARY KEY,
    dispatch_id BIGINT REFERENCES dispatches(id) ON DELETE SET NULL,
    clicked_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS subscribers (
    id          BIGSERIAL PRIMARY KEY,
    channel     TEXT NOT NULL,                      -- 'discord' | 'email'
    channel_id  TEXT NOT NULL UNIQUE,
    categories  TEXT[] NOT NULL DEFAULT '{}',
    quiet_start SMALLINT, quiet_end SMALLINT,       -- local quiet hours
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS media (
    id          BIGSERIAL PRIMARY KEY,
    offer_id    BIGINT NOT NULL REFERENCES offers(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL DEFAULT 'image',      -- image | video | thumbnail
    url         TEXT NOT NULL,
    local_path  TEXT,                               -- if archived to S3/MinIO
    width       INT, height INT,
    UNIQUE (offer_id, url)
);

CREATE TABLE IF NOT EXISTS runs (
    id          BIGSERIAL PRIMARY KEY,
    flow_key    TEXT NOT NULL,                      -- 'pipeline', 'verify', 'dispatch'
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status      TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running','success','failed','partial')),
    stats       JSONB NOT NULL DEFAULT '{}'         -- {raw_ingested, offers_created, dupes, sent}
);

-- 7. TELEGRAM SCOPED TABLES ------------------------------------------------------
CREATE TABLE IF NOT EXISTS channel_cursors (       -- incremental watermark per channel
    id               BIGSERIAL PRIMARY KEY,
    source_id        BIGINT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    channel_username TEXT NOT NULL,
    last_message_id  BIGINT NOT NULL DEFAULT 0,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, channel_username)
);

CREATE TABLE IF NOT EXISTS discovered_channels (   -- auto-join discovery registry
    id               BIGSERIAL PRIMARY KEY,
    source_id        BIGINT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    channel_username TEXT NOT NULL UNIQUE,
    title            TEXT,
    member_count     INT,
    status           TEXT NOT NULL DEFAULT 'new'
                     CHECK (status IN ('new','approved','joined','failed','dropped')),
    discovered_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_check       TIMESTAMPTZ
);

-- 8. updated_at TRIGGER -----------------------------------------------------------
CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_offers_touch ON offers;
CREATE TRIGGER trg_offers_touch BEFORE UPDATE ON offers
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- 9. VIEWS -------------------------------------------------------------------------
-- Offers that still deserve a Discord broadcast (verified, active, never sent).
CREATE OR REPLACE VIEW v_dispatch_queue AS
SELECT o.*, s.name AS source_name
FROM offers o
JOIN raw_items r  ON r.id = o.raw_item_id
JOIN sources  s  ON s.id = r.source_id
WHERE o.verification_status IN ('verified','live')
  AND o.is_active
  AND NOT EXISTS (
      SELECT 1 FROM dispatches d
      WHERE d.offer_id = o.id AND d.channel = 'discord' AND d.status = 'sent'
  );

COMMIT;

-- ============================================================================
-- OPTIONAL pgvector upgrade (RDS PG15+ / Supabase / self-hosted only):
--     CREATE EXTENSION IF NOT EXISTS vector;
--     ALTER TABLE offer_fingerprints
--         ADD COLUMN IF NOT EXISTS embedding_vec vector(384);
--     UPDATE offer_fingerprints SET embedding_vec = embedding::vector
--         WHERE embedding_vec IS NULL AND cardinality(embedding) = 384;
--     CREATE INDEX IF NOT EXISTS idx_fp_hnsw ON offer_fingerprints
--         USING hnsw (embedding_vec vector_cosine_ops);
-- ============================================================================