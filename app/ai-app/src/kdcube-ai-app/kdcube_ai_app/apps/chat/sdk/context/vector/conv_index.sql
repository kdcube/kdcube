-- =========================================
-- deploy-conversation-history.sql
-- =========================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE SCHEMA IF NOT EXISTS <SCHEMA>;

CREATE TABLE IF NOT EXISTS <SCHEMA>.conv_messages (
                                                      id               BIGSERIAL PRIMARY KEY,
                                                      user_id          TEXT NOT NULL,
                                                      bundle_id        TEXT,
                                                      agent_id         TEXT,                           -- owning agent; NULL when no agent_id is provided
                                                      conversation_id  TEXT NOT NULL,
                                                      message_id       TEXT,                           -- ConversationStore id; present for artifacts
                                                      role             TEXT NOT NULL,                  -- 'user' | 'assistant' | 'artifact'
                                                      text             TEXT NOT NULL,
                                                      hosted_uri       TEXT NOT NULL,
                                                      ts               TIMESTAMPTZ NOT NULL DEFAULT now(),
    ttl_days         INT NOT NULL DEFAULT 365,
    user_type        TEXT NOT NULL DEFAULT 'anonymous',
    tags             TEXT[] NOT NULL DEFAULT '{}',
    embedding        VECTOR(1536),
    turn_id          TEXT,
    anchors_text     TEXT NOT NULL DEFAULT '',
    search_tsv       TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('simple',  coalesce(anchors_text, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(text,         '')), 'B')
    ) STORED
    );

-- Keep this focused bootstrap usable against an older conversation-only
-- schema as well as a fresh database. Full deployments still use the project
-- schema provisioner; direct SDK hosts need only these conversation tables.
ALTER TABLE <SCHEMA>.conv_messages
  ADD COLUMN IF NOT EXISTS bundle_id TEXT,
  ADD COLUMN IF NOT EXISTS agent_id TEXT,
  ADD COLUMN IF NOT EXISTS turn_id TEXT,
  ADD COLUMN IF NOT EXISTS anchors_text TEXT NOT NULL DEFAULT '';

ALTER TABLE <SCHEMA>.conv_messages
  ADD COLUMN IF NOT EXISTS search_tsv TSVECTOR GENERATED ALWAYS AS (
      setweight(to_tsvector('simple',  coalesce(anchors_text, '')), 'A') ||
      setweight(to_tsvector('english', coalesce(text,         '')), 'B')
  ) STORED;

-- Handle historical rename of table column s3_uri -> hosted_uri
DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = '<SCHEMA>'
      AND table_name = 'conv_messages'
      AND column_name = 's3_uri'
  ) AND NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = '<SCHEMA>'
      AND table_name = 'conv_messages'
      AND column_name = 'hosted_uri'
  ) THEN
    ALTER TABLE <SCHEMA>.conv_messages
      RENAME COLUMN s3_uri TO hosted_uri;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_conv_user_conversation_ts
  ON <SCHEMA>.conv_messages (user_id, conversation_id, ts DESC);

CREATE INDEX IF NOT EXISTS conv_messages_bundle_id_idx
  ON <SCHEMA>.conv_messages (bundle_id);

CREATE INDEX IF NOT EXISTS conv_messages_user_conv_bundle_ts_idx
  ON <SCHEMA>.conv_messages (user_id, conversation_id, bundle_id, ts DESC);

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_conv_user_conv_turn
  ON <SCHEMA>.conv_messages (user_id, conversation_id, turn_id);

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_conv_user_type_ts
  ON <SCHEMA>.conv_messages (user_type, ts DESC);

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_conv_tags
  ON <SCHEMA>.conv_messages USING GIN (tags);

-- speed up recency & scope
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_cm_scope_time ON <SCHEMA>.conv_messages
  (user_id, conversation_id, role, ts DESC);

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_cm_text_trgm
ON <SCHEMA>.conv_messages USING gin (text gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_cm_search_tsv
ON <SCHEMA>.conv_messages USING gin (search_tsv);

-- ANN (embeddings)
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_conv_embedding
  ON <SCHEMA>.conv_messages USING ivfflat (embedding vector_cosine_ops) WITH (lists=100);

-- Handle historical rename of view column s3_uri -> hosted_uri
DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = '<SCHEMA>'
      AND table_name = 'conv_messages_expired'
      AND column_name = 's3_uri'
  ) THEN
    ALTER VIEW <SCHEMA>.conv_messages_expired
      RENAME COLUMN s3_uri TO hosted_uri;
  END IF;
END $$;

CREATE OR REPLACE VIEW <SCHEMA>.conv_messages_expired AS
SELECT * FROM <SCHEMA>.conv_messages
WHERE ts + (ttl_days || ' days')::interval < now();


CREATE TABLE IF NOT EXISTS <SCHEMA>.conv_artifact_edges (
                                                            from_id    BIGINT NOT NULL REFERENCES <SCHEMA>.conv_messages(id) ON DELETE CASCADE,
    to_id      BIGINT NOT NULL REFERENCES <SCHEMA>.conv_messages(id) ON DELETE CASCADE,
    policy     TEXT   NOT NULL DEFAULT 'none',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (from_id, to_id)
    );

-- (edges)
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_edge_from_id ON <SCHEMA>.conv_artifact_edges (from_id);
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_edge_to_id   ON <SCHEMA>.conv_artifact_edges (to_id);

-- === tracks (first-class) ===
-- === per-conversation tickets ===
CREATE TABLE IF NOT EXISTS <SCHEMA>.conv_track_tickets (
                                                           ticket_id   TEXT PRIMARY KEY,
                                                           user_id     TEXT NOT NULL,
                                                           conversation_id TEXT NOT NULL,
                                                           turn_id     TEXT,
                                                           title       TEXT NOT NULL,
                                                           description TEXT NOT NULL DEFAULT '',
                                                           status      TEXT NOT NULL DEFAULT 'open',
                                                           priority    SMALLINT NOT NULL DEFAULT 3,
                                                           assignee    TEXT,
                                                           tags        TEXT[] NOT NULL DEFAULT '{}',
                                                           created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    embedding   VECTOR(1536),
    data        JSONB NOT NULL DEFAULT '{}'::jsonb
    );
ALTER TABLE <SCHEMA>.conv_track_tickets
  ADD COLUMN IF NOT EXISTS turn_id TEXT,
  ADD COLUMN IF NOT EXISTS data JSONB NOT NULL DEFAULT '{}'::jsonb;
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_tickets_status
  ON <SCHEMA>.conv_track_tickets (status, priority DESC);
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_tickets_embedding
  ON <SCHEMA>.conv_track_tickets USING ivfflat (embedding vector_cosine_ops) WITH (lists=50);
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_tickets_conv_user
  ON <SCHEMA>.conv_track_tickets (conversation_id, user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_tickets_turn
  ON <SCHEMA>.conv_track_tickets (turn_id);

-- === cold tier ledger ===
-- One row per archived batch. Hot rows are deleted only after the batch is
-- read back and matches its manifest (state 'verified' -> 'pruned').
CREATE TABLE IF NOT EXISTS <SCHEMA>.conv_archive_batches (
    batch_id     TEXT PRIMARY KEY,
    day          DATE NOT NULL,
    row_count    INTEGER NOT NULL,
    min_id       BIGINT NOT NULL,
    max_id       BIGINT NOT NULL,
    min_ts       TIMESTAMPTZ,
    max_ts       TIMESTAMPTZ,
    part_key     TEXT NOT NULL,
    manifest_key TEXT NOT NULL,
    sha256       TEXT,
    state        TEXT NOT NULL CHECK (state IN ('written', 'verified', 'pruned', 'retired')),
    error        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    );
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_archive_state ON <SCHEMA>.conv_archive_batches (state, day);

-- Which conversations each archived batch holds, so listing archived
-- conversations reads only this table and opening one reads only the parts
-- that hold it. Rows of a batch count only while the batch is 'pruned'.
CREATE TABLE IF NOT EXISTS <SCHEMA>.conv_archive_conversations (
    batch_id        TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    bundle_id       TEXT NOT NULL DEFAULT '',
    agent_id        TEXT NOT NULL DEFAULT '',
    row_count       INTEGER NOT NULL,
    min_ts          TIMESTAMPTZ NOT NULL,
    max_ts          TIMESTAMPTZ NOT NULL,
    expires_at      TIMESTAMPTZ,
    start_min_ts    TIMESTAMPTZ,
    start_last_ts   TIMESTAMPTZ,
    start_last_text TEXT,
    PRIMARY KEY (batch_id, user_id, conversation_id, bundle_id, agent_id)
    );
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_archive_conv_user ON <SCHEMA>.conv_archive_conversations (user_id, max_ts);
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_archive_conv_conversation ON <SCHEMA>.conv_archive_conversations (user_id, conversation_id);
-- Each conversation start with its own expiry (a scope can mix TTLs), so the
-- list never shows an expired start. Added after the table first shipped.
ALTER TABLE <SCHEMA>.conv_archive_conversations ADD COLUMN IF NOT EXISTS start_ts_list      TIMESTAMPTZ[];
ALTER TABLE <SCHEMA>.conv_archive_conversations ADD COLUMN IF NOT EXISTS start_expires_list TIMESTAMPTZ[];
ALTER TABLE <SCHEMA>.conv_archive_conversations ADD COLUMN IF NOT EXISTS start_text_list    TEXT[];

-- Every explicit conversation deletion: who, when, what and how much. The row
-- is written before anything is deleted and finishes 'completed' or 'failed'.
CREATE TABLE IF NOT EXISTS <SCHEMA>.conv_archive_deletions (
    deletion_id  TEXT PRIMARY KEY,
    actor        TEXT NOT NULL,
    reason       TEXT,
    scope        JSONB NOT NULL,
    state        TEXT NOT NULL DEFAULT 'started' CHECK (state IN ('started', 'completed', 'failed')),
    hot_rows     INTEGER NOT NULL DEFAULT 0,
    body_objects INTEGER NOT NULL DEFAULT 0,
    cold_rows    INTEGER NOT NULL DEFAULT 0,
    error        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
    );
