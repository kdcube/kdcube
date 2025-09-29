-- =========================================
-- deploy-conversation-history.sql
-- =========================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS <SCHEMA>;

CREATE TABLE IF NOT EXISTS <SCHEMA>.conv_messages (
                                                      id               BIGSERIAL PRIMARY KEY,
                                                      user_id          TEXT NOT NULL,
                                                      bundle_id        TEXT,
                                                      conversation_id  TEXT NOT NULL,
                                                      message_id       TEXT,                           -- ConversationStore id; present for artifacts
                                                      role             TEXT NOT NULL,                  -- 'user' | 'assistant' | 'artifact'
                                                      text             TEXT NOT NULL,
                                                      s3_uri           TEXT NOT NULL,
                                                      ts               TIMESTAMPTZ NOT NULL DEFAULT now(),
    ttl_days         INT NOT NULL DEFAULT 365,
    user_type        TEXT NOT NULL DEFAULT 'anonymous',
    tags             TEXT[] NOT NULL DEFAULT '{}',
    embedding        VECTOR(1536),
    track_id         TEXT,
    turn_id          TEXT
    );

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_conv_user_conversation_ts
  ON <SCHEMA>.conv_messages (user_id, conversation_id, ts DESC);

-- CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_conv_user_conv_track_ts
--   ON <SCHEMA>.conv_messages (user_id, conversation_id, track_id, ts DESC);

-- 0.2) Helpful indexes
CREATE INDEX IF NOT EXISTS conv_messages_bundle_id_idx
  ON <SCHEMA>.conv_messages (bundle_id);

-- If you often combine with user & conversation scope:
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
  (user_id, conversation_id, track_id, role, ts DESC);

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_cm_text_trgm
ON <SCHEMA>.conv_messages USING gin (text gin_trgm_ops);

-- ANN (embeddings)
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_conv_embedding
  ON <SCHEMA>.conv_messages USING ivfflat (embedding vector_cosine_ops) WITH (lists=100);

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

-- ---------- Artifact dependency edges (you already join this in ConvIndex.search_context) ----------
CREATE TABLE IF NOT EXISTS <SCHEMA>.conv_artifact_edges (
                                                            from_id   BIGINT NOT NULL REFERENCES <SCHEMA>.conv_messages(id) ON DELETE CASCADE,
    to_id     BIGINT NOT NULL REFERENCES <SCHEMA>.conv_messages(id) ON DELETE CASCADE,
    policy    TEXT NOT NULL DEFAULT 'none',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (from_id, to_id)
    );
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_edges_to_id    ON <SCHEMA>.conv_artifact_edges (to_id);
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_edges_policy   ON <SCHEMA>.conv_artifact_edges (policy);

-- ---------- User memory (stable, consented facts/preferences) ----------
CREATE TABLE IF NOT EXISTS <SCHEMA>.user_memory (
                                                    id            BIGSERIAL PRIMARY KEY,
                                                    user_id       TEXT NOT NULL,
                                                    fact          TEXT NOT NULL,
                                                    source        TEXT NOT NULL DEFAULT 'user_said',        -- 'user_said' | 'system' | 'import' | ...
                                                    strength      REAL NOT NULL DEFAULT 0.90,               -- 0..1, decay/refresh over time
                                                    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ,
    embedding     VECTOR(1536),
    tags          TEXT[] NOT NULL DEFAULT '{}'
    );
-- fast filters
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_um_user_created   ON <SCHEMA>.user_memory (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_um_user_lastseen  ON <SCHEMA>.user_memory (user_id, last_seen_at DESC);
-- hybrid search
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_um_fact_tsv       ON <SCHEMA>.user_memory USING gin (to_tsvector('simple', fact));
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_um_tags_gin       ON <SCHEMA>.user_memory USING gin (tags);
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_um_emb_ivf        ON <SCHEMA>.user_memory USING ivfflat (embedding vector_cosine_ops) WITH (lists=50);

-- ---------- Turn-level preferences extracted from NL (assertions + exceptions) ----------
CREATE TABLE IF NOT EXISTS <SCHEMA>.conv_prefs (
                                                   id               BIGSERIAL PRIMARY KEY,
                                                   user_id          TEXT NOT NULL,
                                                   conversation_id  TEXT NOT NULL,
                                                   turn_id          TEXT,
                                                   key              TEXT NOT NULL,                 -- dotted key, e.g., 'focus.edr'
                                                   value_json       JSONB,                         -- arbitrary JSON
                                                   desired          BOOLEAN NOT NULL DEFAULT TRUE, -- positive/negative rule
                                                   scope            TEXT NOT NULL DEFAULT 'conversation', -- 'conversation' | 'user'
                                                   confidence       REAL NOT NULL DEFAULT 0.60,
                                                   reason           TEXT NOT NULL DEFAULT 'nl-extracted',
                                                   ts               TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at       TIMESTAMPTZ,
    tags             TEXT[] NOT NULL DEFAULT '{}'
    );
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_cp_user_conv_key_ts
  ON <SCHEMA>.conv_prefs (user_id, conversation_id, key, ts DESC);
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_cp_scope_ts
  ON <SCHEMA>.conv_prefs (scope, ts DESC);
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_cp_value_gin
  ON <SCHEMA>.conv_prefs USING gin (value_json);
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_cp_tags_gin
  ON <SCHEMA>.conv_prefs USING gin (tags);

CREATE TABLE IF NOT EXISTS <SCHEMA>.conv_pref_exceptions (
                                                             id               BIGSERIAL PRIMARY KEY,
                                                             user_id          TEXT NOT NULL,
                                                             conversation_id  TEXT NOT NULL,
                                                             turn_id          TEXT,
                                                             rule_key         TEXT NOT NULL,                -- which assertion this carves out
                                                             value_json       JSONB,
                                                             scope            TEXT NOT NULL DEFAULT 'conversation',
                                                             confidence       REAL NOT NULL DEFAULT 0.60,
                                                             reason           TEXT NOT NULL DEFAULT 'nl-extracted',
                                                             ts               TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at       TIMESTAMPTZ
    );
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_cpe_user_conv_rule_ts
  ON <SCHEMA>.conv_pref_exceptions (user_id, conversation_id, rule_key, ts DESC);
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_cpe_value_gin
  ON <SCHEMA>.conv_pref_exceptions USING gin (value_json);

-- ---------- RAG index (hybrid: vector + BM25 + filters) ----------
CREATE TABLE IF NOT EXISTS <SCHEMA>.rag_chunks (
                                                   id          BIGSERIAL PRIMARY KEY,
                                                   corpus      TEXT NOT NULL,                 -- logical collection id (e.g., 'docs', 'faq', 'tickets')
                                                   source_id   TEXT,                          -- per-document/source identifier
                                                   chunk       TEXT NOT NULL,
                                                   chunk_sha1  TEXT NOT NULL,
                                                   metadata    JSONB NOT NULL DEFAULT '{}',   -- {url, title, product_id, section, ...}
                                                   created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ,
    embedding   VECTOR(1536)
    );
CREATE UNIQUE INDEX IF NOT EXISTS idx_<SCHEMA>_rag_uni
  ON <SCHEMA>.rag_chunks (corpus, source_id, chunk_sha1);
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_rag_corpus_created
  ON <SCHEMA>.rag_chunks (corpus, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_rag_meta_gin
  ON <SCHEMA>.rag_chunks USING gin (metadata);
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_rag_chunk_tsv
  ON <SCHEMA>.rag_chunks USING gin (to_tsvector('english', chunk));
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_rag_emb_ivf
  ON <SCHEMA>.rag_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists=500);
CREATE OR REPLACE VIEW <SCHEMA>.rag_chunks_active AS
SELECT * FROM <SCHEMA>.rag_chunks
WHERE expires_at IS NULL OR expires_at > now();

CREATE TABLE IF NOT EXISTS <SCHEMA>.conv_track_programs (
                                                            program_id      TEXT PRIMARY KEY,
                                                            track_id        TEXT NOT NULL,
                                                            user_id         TEXT NOT NULL,
                                                            conversation_id TEXT NOT NULL,
                                                            title           TEXT NOT NULL,
                                                            language        TEXT NOT NULL DEFAULT 'python',
                                                            code            TEXT NOT NULL,
                                                            params          JSONB NOT NULL DEFAULT '{}'::jsonb,
                                                            deliverables    JSONB NOT NULL DEFAULT '{}'::jsonb,
                                                            status          TEXT NOT NULL DEFAULT 'active',
                                                            revision        INT  NOT NULL DEFAULT 1,
                                                            last_run_at     TIMESTAMPTZ,
                                                            last_run_meta   JSONB NOT NULL DEFAULT '{}'::jsonb,
                                                            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    );
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_programs_track_updated
  ON <SCHEMA>.conv_track_programs (track_id, updated_at DESC);


CREATE TABLE IF NOT EXISTS <SCHEMA>.conv_track_tickets (
   ticket_id   TEXT PRIMARY KEY,
   track_id    TEXT NOT NULL,
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
    data JSONB NOT NULL DEFAULT '{}'::jsonb
    );
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_tickets_track
  ON <SCHEMA>.conv_track_tickets (track_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_tickets_status
  ON <SCHEMA>.conv_track_tickets (status, priority DESC);
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_tickets_embedding
  ON <SCHEMA>.conv_track_tickets USING ivfflat (embedding vector_cosine_ops) WITH (lists=50);

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_tickets_conv_user
  ON <SCHEMA>.conv_track_tickets (conversation_id, user_id, updated_at DESC);  -- NEW

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_tickets_turn
  ON <SCHEMA>.conv_track_tickets (turn_id);