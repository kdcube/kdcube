-- =========================================
-- deploy-knowledge-base.sql (CLEAN IF NOT EXISTS VERSION)
-- =========================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- For fuzzy text search
CREATE EXTENSION IF NOT EXISTS btree_gin; -- For composite indexes
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE SCHEMA IF NOT EXISTS <SCHEMA>;

-------------------------------------------------------------------------------
-- 1) Event Log
-------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS <SCHEMA>.events (
  event_id         BIGSERIAL PRIMARY KEY,
  entity_type      TEXT NOT NULL,
  entity_id        TEXT NOT NULL,
  version          INT  NOT NULL,
  event_type       TEXT NOT NULL,
  actor_id         TEXT,
  timestamp        TIMESTAMPTZ NOT NULL DEFAULT now(),
  event            JSONB,
  service_metadata JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_events_entity
    ON <SCHEMA>.events(entity_type, entity_id);

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_events_service_metadata
    ON <SCHEMA>.events USING GIN (service_metadata jsonb_ops);

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_events_timestamp
    ON <SCHEMA>.events (timestamp DESC);

-------------------------------------------------------------------------------
-- 2) Data Source (All Versions)
-------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS <SCHEMA>.datasource (
    id              TEXT NOT NULL,
    version         INT  NOT NULL,
    rn              TEXT UNIQUE,
    source_type     TEXT,
    provider        TEXT,          -- Provider identifier (e.g., 'news_api', 'reuters', 'internal_docs')

    -- Core metadata
    title           TEXT NOT NULL,
    uri             TEXT NOT NULL,  -- Original source URI
    system_uri      TEXT,          -- S3 URI when rehosted

    -- Content metadata
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Processing status
    status          TEXT NOT NULL DEFAULT 'pending', -- 'pending', 'processing', 'completed', 'failed'
    segment_count   INT DEFAULT 0,

    -- Cache/Expiration management - NEW
    expiration      TIMESTAMPTZ,   -- NEW: When this data expires (NULL = never expires)

    -- Temporal
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Derived timestamps (maintained by trigger)
    published_at    TIMESTAMPTZ,   -- parsed from metadata->'metadata'->>'published_time_iso'
    modified_at     TIMESTAMPTZ,   -- parsed from metadata->'metadata'->>'modified_time_iso'
    event_ts        TIMESTAMPTZ,   -- COALESCE(modified_at, published_at, created_at)

    PRIMARY KEY (id, version)
    );

-- Datasource indexes (schema-specific names)
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_ds_id_version
    ON <SCHEMA>.datasource (id, version DESC);

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_ds_status
    ON <SCHEMA>.datasource (status);

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_ds_created_at
    ON <SCHEMA>.datasource (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_ds_metadata
    ON <SCHEMA>.datasource USING GIN (metadata jsonb_ops);

-- Provider-related
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_ds_provider
    ON <SCHEMA>.datasource (provider);

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_ds_provider_type
    ON <SCHEMA>.datasource (provider, source_type);

-- Expiration management
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_ds_expiration
    ON <SCHEMA>.datasource (expiration) WHERE expiration IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_ds_expired
    ON <SCHEMA>.datasource (expiration, status) WHERE expiration IS NOT NULL;

-- Combined cache queries
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_ds_cache_lookup
    ON <SCHEMA>.datasource (provider, source_type, status, expiration);

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_ds_published_at
  ON <SCHEMA>.datasource (published_at);

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_ds_modified_at
  ON <SCHEMA>.datasource (modified_at);

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_ds_event_ts
  ON <SCHEMA>.datasource (event_ts DESC);
-------------------------------------------------------------------------------
-- 3) Content Hash
-------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS <SCHEMA>.content_hash (
  id            BIGSERIAL PRIMARY KEY,
  name          TEXT NOT NULL,
  value         TEXT NOT NULL UNIQUE,
  type          TEXT NOT NULL,
  provider      TEXT,
  creation_time TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Content hash indexes (schema-specific names)
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_ch_value
  ON <SCHEMA>.content_hash (value);

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_ch_name
  ON <SCHEMA>.content_hash (name);

COMMENT ON COLUMN <SCHEMA>.content_hash.value IS 'Content hash value (e.g., SHA256, MD5, etc.)';
COMMENT ON COLUMN <SCHEMA>.content_hash.type  IS 'Type of hash algorithm used (e.g., SHA256, MD5, SHA1)';
COMMENT ON COLUMN <SCHEMA>.content_hash.name  IS 'Name or identifier of the object being hashed';

-------------------------------------------------------------------------------
-- 4) Versioned Data Source Retrieval Segment
-------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS <SCHEMA>.retrieval_segment (
  id               TEXT NOT NULL,
  version          INT  NOT NULL,
  rn               TEXT UNIQUE,
  provider         TEXT,

  -- Link to datasource (version equals segment version)
  resource_id      TEXT NOT NULL,     -- matches datasource.id

  -- Core content
  content          TEXT NOT NULL,
  summary          TEXT,
  title            TEXT,

  -- Extracted entities and metadata from processing
  entities         JSONB NOT NULL DEFAULT '[]'::jsonb,

  -- Additional metadata
  tags             TEXT[] NOT NULL DEFAULT '{}',
  word_count       INT,
  sentence_count   INT,

  processed_at     TIMESTAMPTZ,

  -- Search vectors
  search_vector    TSVECTOR,          -- For keyword search
  embedding        VECTOR(1536),      -- For semantic search (adjust dimension if needed)

  -- Temporal
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  event_ts         TIMESTAMPTZ,       -- DENORMALIZED recency from datasource

  -- Lineage (tracks source)
  lineage          JSONB NOT NULL DEFAULT '{}'::jsonb,
  extensions       JSONB DEFAULT '{}'::jsonb,

  PRIMARY KEY (id, version),
  FOREIGN KEY (resource_id, version) REFERENCES <SCHEMA>.datasource(id, version) ON DELETE CASCADE
);

COMMENT ON COLUMN <SCHEMA>.retrieval_segment.extensions IS 'Non-indexed JSONB field for arbitrary extension data and metadata';
COMMENT ON COLUMN <SCHEMA>.datasource.provider IS 'Provider identifier for data source (e.g., news_api, reuters, internal_docs, web_scraper)';
COMMENT ON COLUMN <SCHEMA>.datasource.expiration IS 'Expiration timestamp for cached data. NULL means never expires';
COMMENT ON COLUMN <SCHEMA>.retrieval_segment.provider IS 'Provider identifier inherited from datasource for faster filtering';
COMMENT ON COLUMN <SCHEMA>.retrieval_segment.event_ts IS 'Recency timestamp denormalized from datasource (GREATEST(modified_at, published_at, created_at))';

-------------------------------------------------------------------------------
-- 5) Functions
-------------------------------------------------------------------------------

-- Search vector refresher
CREATE OR REPLACE FUNCTION <SCHEMA>.update_search_vector_<SCHEMA>()
RETURNS TRIGGER AS
$$
DECLARE
  tag text;
  tags_text_raw   text := '';  -- "topic.vendor risk"
  tags_text_split text := '';  -- "topic vendor risk"
  tags_keys       text := '';  -- "topic"
  tags_vals       text := '';  -- "vendor risk"
BEGIN
  -- Expand tags into multiple token streams
  IF NEW.tags IS NOT NULL THEN
    FOREACH tag IN ARRAY NEW.tags LOOP
      tags_text_raw   := tags_text_raw   || ' ' || tag;
      tags_text_split := tags_text_split || ' ' || replace(tag, '.', ' ');
      IF position('.' IN tag) > 0 THEN
        tags_keys := tags_keys || ' ' || split_part(tag, '.', 1);
        tags_vals := tags_vals || ' ' || split_part(tag, '.', 2);
      END IF;
    END LOOP;
  END IF;

  NEW.search_vector :=
      -- Titles and contextual tags get top weight
      setweight(to_tsvector('english', COALESCE(NEW.title, '')),        'A')
    || setweight(to_tsvector('english', COALESCE(tags_text_raw, '')),   'A')  -- "key.value"
    || setweight(to_tsvector('english', COALESCE(tags_text_split, '')), 'A')  -- "key value"

      -- Key alone has context, medium weight
    || setweight(to_tsvector('english', COALESCE(tags_keys, '')),       'B')

      -- Value alone is weak context; set to low weight OR comment out to disable
    || setweight(to_tsvector('english', COALESCE(tags_vals, '')),       'C')

      -- Body content remains background signal
    || setweight(to_tsvector('english', COALESCE(NEW.content, '')),     'B');

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Extract entity values (for GIN functional index)
CREATE OR REPLACE FUNCTION <SCHEMA>.extract_entity_values_<SCHEMA>(entities_json JSONB)
    RETURNS JSONB
AS $$
BEGIN
  RETURN (
    SELECT jsonb_agg(item->>'value')
    FROM jsonb_array_elements(entities_json) item
  );
END;
$$ LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE;

-- Expiration check
CREATE OR REPLACE FUNCTION <SCHEMA>.is_datasource_expired_<SCHEMA>(expiration_ts TIMESTAMPTZ)
    RETURNS BOOLEAN
AS $$
BEGIN
  RETURN expiration_ts IS NOT NULL AND expiration_ts <= now();
END;
$$ LANGUAGE plpgsql STABLE STRICT PARALLEL SAFE;

-- Clean up expired datasources and segments
CREATE OR REPLACE FUNCTION <SCHEMA>.cleanup_expired_data_<SCHEMA>()
RETURNS TABLE(datasources_deleted INT, segments_deleted INT)
AS $$
DECLARE
  ds_count INT := 0;
  seg_count INT := 0;
BEGIN
  DELETE FROM <SCHEMA>.retrieval_segment rs
  WHERE EXISTS (
    SELECT 1
    FROM <SCHEMA>.datasource ds
    WHERE ds.id = rs.resource_id
      AND ds.version = rs.version
      AND <SCHEMA>.is_datasource_expired_<SCHEMA>(ds.expiration)
  );
  GET DIAGNOSTICS seg_count = ROW_COUNT;

  DELETE FROM <SCHEMA>.datasource ds
  WHERE <SCHEMA>.is_datasource_expired_<SCHEMA>(ds.expiration);
  GET DIAGNOSTICS ds_count = ROW_COUNT;

  RETURN QUERY SELECT ds_count, seg_count;
END;
$$ LANGUAGE plpgsql;

-- Keep datasource.published_at / modified_at / event_ts in sync with metadata
CREATE OR REPLACE FUNCTION <SCHEMA>.sync_ds_times_<SCHEMA>()
RETURNS TRIGGER AS
$$
DECLARE
  pub_ts TIMESTAMPTZ;
  mod_ts TIMESTAMPTZ;
BEGIN
  -- Parse ISO strings if present ('' -> NULL)
  pub_ts := NULLIF(NEW.metadata->'metadata'->>'published_time_iso','')::timestamptz;
  mod_ts := NULLIF(NEW.metadata->'metadata'->>'modified_time_iso','')::timestamptz;

  -- Only backfill when columns are NULL
  IF NEW.published_at IS NULL THEN
    NEW.published_at := pub_ts;
  END IF;

  IF NEW.modified_at IS NULL THEN
    NEW.modified_at := mod_ts;
  END IF;

  -- event_ts prefers explicit value; else derived
  NEW.event_ts := COALESCE(NEW.event_ts, NEW.modified_at, NEW.published_at, NEW.created_at);

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Denormalize recency into retrieval_segment on insert/update
CREATE OR REPLACE FUNCTION <SCHEMA>.rs_set_denorms_<SCHEMA>()
RETURNS TRIGGER AS
$$
DECLARE
  ds_rec RECORD;
BEGIN
  SELECT provider,
         GREATEST(
           COALESCE(modified_at,  TIMESTAMPTZ '-infinity'),
           COALESCE(published_at, TIMESTAMPTZ '-infinity'),
           COALESCE(created_at,   TIMESTAMPTZ '-infinity')
         ) AS ds_event_ts
    INTO ds_rec
  FROM <SCHEMA>.datasource
  WHERE id = NEW.resource_id
    AND version = NEW.version;

  IF ds_rec.provider IS NOT NULL THEN
    NEW.provider := COALESCE(NEW.provider, ds_rec.provider);
  END IF;

  NEW.event_ts := COALESCE(ds_rec.ds_event_ts, NEW.created_at, now());
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- When datasource times change, broadcast new event_ts to its segments
CREATE OR REPLACE FUNCTION <SCHEMA>.ds_broadcast_event_ts_<SCHEMA>()
RETURNS TRIGGER AS
$$
DECLARE
  new_event_ts TIMESTAMPTZ;
BEGIN
  IF NOT (TG_OP = 'UPDATE' AND (
        NEW.modified_at IS DISTINCT FROM OLD.modified_at OR
        NEW.published_at IS DISTINCT FROM OLD.published_at OR
        NEW.created_at  IS DISTINCT FROM OLD.created_at))
  THEN
    RETURN NULL;
  END IF;

  new_event_ts :=
    GREATEST(
      COALESCE(NEW.modified_at,  TIMESTAMPTZ '-infinity'),
      COALESCE(NEW.published_at, TIMESTAMPTZ '-infinity'),
      COALESCE(NEW.created_at,   TIMESTAMPTZ '-infinity')
    );

  UPDATE <SCHEMA>.retrieval_segment rs
     SET event_ts = COALESCE(new_event_ts, rs.created_at, now())
   WHERE rs.resource_id = NEW.id
     AND rs.version     = NEW.version;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-------------------------------------------------------------------------------
-- 6) Triggers
-------------------------------------------------------------------------------

-- Search vector trigger
DO $$
BEGIN
    -- Check if trigger exists
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_<SCHEMA>_update_search_vector'
          AND tgrelid = '<SCHEMA>.retrieval_segment'::regclass
    ) THEN
        -- Create the trigger
        EXECUTE 'CREATE TRIGGER trg_<SCHEMA>_update_search_vector
                    BEFORE INSERT OR UPDATE ON <SCHEMA>.retrieval_segment
                    FOR EACH ROW
                    EXECUTE PROCEDURE <SCHEMA>.update_search_vector_<SCHEMA>()';
    END IF;
END $$;

-- Datasource time sync trigger (metadata → published_at/modified_at/event_ts)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger
    WHERE tgname = 'trg_<SCHEMA>_sync_ds_times'
      AND tgrelid = '<SCHEMA>.datasource'::regclass
  ) THEN
    EXECUTE 'CREATE TRIGGER trg_<SCHEMA>_sync_ds_times
               BEFORE INSERT OR UPDATE OF metadata, created_at
               ON <SCHEMA>.datasource
               FOR EACH ROW
               EXECUTE PROCEDURE <SCHEMA>.sync_ds_times_<SCHEMA>()';
  END IF;
END $$;

-- Retrieval segment denorm trigger (copy provider/event_ts from datasource)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger
    WHERE tgname = 'trg_<SCHEMA>_rs_set_denorms'
      AND tgrelid = '<SCHEMA>.retrieval_segment'::regclass
  ) THEN
    EXECUTE 'CREATE TRIGGER trg_<SCHEMA>_rs_set_denorms
               BEFORE INSERT OR UPDATE OF resource_id, version, provider
               ON <SCHEMA>.retrieval_segment
               FOR EACH ROW
               EXECUTE PROCEDURE <SCHEMA>.rs_set_denorms_<SCHEMA>()';
  END IF;
END $$;

-- Datasource → segments event_ts broadcaster
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger
    WHERE tgname = 'trg_<SCHEMA>_ds_broadcast_event_ts'
      AND tgrelid = '<SCHEMA>.datasource'::regclass
  ) THEN
    EXECUTE 'CREATE TRIGGER trg_<SCHEMA>_ds_broadcast_event_ts
               AFTER UPDATE OF modified_at, published_at, created_at
               ON <SCHEMA>.datasource
               FOR EACH ROW
               EXECUTE PROCEDURE <SCHEMA>.ds_broadcast_event_ts_<SCHEMA>()';
  END IF;
END $$;

-------------------------------------------------------------------------------
-- 7) Indexes (retrieval_segment)
-------------------------------------------------------------------------------

-- Primary search index
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_rs_search_vector
    ON <SCHEMA>.retrieval_segment USING GIN (search_vector);

-- Semantic similarity index
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_rs_embedding_hnsw
  ON <SCHEMA>.retrieval_segment
  USING hnsw (embedding vector_cosine_ops);

-- Entities
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_rs_entities_gin
  ON <SCHEMA>.retrieval_segment USING GIN (entities jsonb_ops);

-- Functional GIN on entity values
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relname = 'idx_<SCHEMA>_rs_entity_values'
      AND n.nspname = '<SCHEMA>'
  ) THEN
    EXECUTE 'CREATE INDEX idx_<SCHEMA>_rs_entity_values
              ON <SCHEMA>.retrieval_segment USING GIN (<SCHEMA>.extract_entity_values_<SCHEMA>(entities))';
  END IF;
END $$;

-- Resource linkage / tags / temporal / provider
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_rs_resource
  ON <SCHEMA>.retrieval_segment (resource_id, version);

-- Tags index (schema-specific name)
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_rs_tags
    ON <SCHEMA>.retrieval_segment USING GIN (tags);

-- Temporal index (schema-specific name)
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_rs_created_at
    ON <SCHEMA>.retrieval_segment (created_at DESC);

-- Composite index for common queries (schema-specific name)
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_rs_resource_created
    ON <SCHEMA>.retrieval_segment (resource_id, created_at DESC);

-- Provider-related indexes for retrieval segments
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_rs_provider
    ON <SCHEMA>.retrieval_segment (provider);

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_rs_provider_resource
    ON <SCHEMA>.retrieval_segment (provider, resource_id);

-- Combined provider search index
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_rs_provider_created
    ON <SCHEMA>.retrieval_segment (provider, created_at DESC);

-- Freshness-aware indexes
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_rs_event_ts_desc
  ON <SCHEMA>.retrieval_segment (event_ts DESC);

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_rs_provider_event_ts
  ON <SCHEMA>.retrieval_segment (provider, event_ts DESC);

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_rs_resver_event_ts
  ON <SCHEMA>.retrieval_segment (resource_id, version, event_ts DESC);

-------------------------------------------------------------------------------
-- 8) Views
-------------------------------------------------------------------------------

-- Active (non-expired) datasources
CREATE OR REPLACE VIEW <SCHEMA>.active_datasources AS
SELECT *
FROM <SCHEMA>.datasource
WHERE expiration IS NULL OR expiration > now();

-- Expired datasources
CREATE OR REPLACE VIEW <SCHEMA>.expired_datasources AS
SELECT *
FROM <SCHEMA>.datasource
WHERE expiration IS NOT NULL AND expiration <= now();

-- Active retrieval segments (through active datasource)
CREATE OR REPLACE VIEW <SCHEMA>.active_retrieval_segments AS
SELECT rs.*
FROM <SCHEMA>.retrieval_segment rs
JOIN <SCHEMA>.datasource ds
  ON ds.id = rs.resource_id
 AND ds.version = rs.version
WHERE ds.expiration IS NULL OR ds.expiration > now();


