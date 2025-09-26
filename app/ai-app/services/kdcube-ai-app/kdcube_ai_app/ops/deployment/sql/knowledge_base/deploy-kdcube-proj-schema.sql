-- =========================================
-- deploy-knowledge-base.sql (CLEAN IF NOT EXISTS VERSION)
-- =========================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- For fuzzy text search
CREATE EXTENSION IF NOT EXISTS btree_gin; -- For composite indexes

CREATE SCHEMA IF NOT EXISTS <SCHEMA>;

-------------------------------------------------------------------------------
-- 1) Event Log
-------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS <SCHEMA>.events (
                                               event_id        BIGSERIAL PRIMARY KEY,
                                               entity_type     TEXT NOT NULL,
                                               entity_id       TEXT NOT NULL,
                                               version         INT  NOT NULL,
                                               event_type      TEXT NOT NULL,
                                               actor_id        TEXT,
                                               timestamp       TIMESTAMPTZ NOT NULL DEFAULT now(),
    event           JSONB,
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

-- NEW: Provider-related indexes
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_ds_provider
    ON <SCHEMA>.datasource (provider);

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_ds_provider_type
    ON <SCHEMA>.datasource (provider, source_type);

-- NEW: Expiration management indexes
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_ds_expiration
    ON <SCHEMA>.datasource (expiration) WHERE expiration IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_ds_expired
    ON <SCHEMA>.datasource (expiration, status) WHERE expiration IS NOT NULL;

-- NEW: Combined index for cache queries
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_ds_cache_lookup
    ON <SCHEMA>.datasource (provider, source_type, status, expiration);

-------------------------------------------------------------------------------
-- 3) Content Hash
-------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS <SCHEMA>.content_hash (
    id              BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    value           TEXT NOT NULL UNIQUE,
    type            TEXT NOT NULL,
    provider        TEXT,
    creation_time   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Content hash indexes (schema-specific names)
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_ch_value
    ON <SCHEMA>.content_hash (value);

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_ch_name
    ON <SCHEMA>.content_hash (name);

COMMENT ON COLUMN <SCHEMA>.content_hash.value IS 'Content hash value (e.g., SHA256, MD5, etc.)';
COMMENT ON COLUMN <SCHEMA>.content_hash.type IS 'Type of hash algorithm used (e.g., SHA256, MD5, SHA1)';
COMMENT ON COLUMN <SCHEMA>.content_hash.name IS 'Name or identifier of the object being hashed';

-------------------------------------------------------------------------------
-- 4) Versioned Data Source Retrieval Segment
-------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS <SCHEMA>.retrieval_segment (
                                                          id                  TEXT NOT NULL,
                                                          version             INT  NOT NULL,
                                                          rn                  TEXT UNIQUE,
                                                          provider            TEXT,

    -- Link to datasource (version is same as segment version)
                                                          resource_id         TEXT NOT NULL,  -- matches datasource.id

    -- Core content
                                                          content             TEXT NOT NULL,
                                                          summary             TEXT,
                                                          title               TEXT,

    -- Extracted entities and metadata from processing
                                                          entities            JSONB NOT NULL DEFAULT '[]'::jsonb, -- [{"key": "domain", "value": "LLM hallucination prevention"}, ...]

    -- Additional metadata
                                                          tags                TEXT[] NOT NULL DEFAULT '{}',
                                                          word_count          INT,
                                                          sentence_count      INT,

                                                          processed_at        TIMESTAMPTZ,

    -- Search vectors
                                                          search_vector       TSVECTOR,          -- For keyword search
                                                          embedding           VECTOR(1536),      -- For semantic search (adjustable dimension)

-- Temporal
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Lineage (tracks source)
    lineage             JSONB NOT NULL DEFAULT '{}'::jsonb, -- {"resource_id": "file|doc.pdf", "segment_index": 2}
    extensions          JSONB DEFAULT '{}'::jsonb,

    PRIMARY KEY (id, version),
    FOREIGN KEY (resource_id, version) REFERENCES <SCHEMA>.datasource(id, version) ON DELETE CASCADE
    );

-- Comment to document the purpose
COMMENT ON COLUMN <SCHEMA>.retrieval_segment.extensions IS 'Non-indexed JSONB field for arbitrary extension data and metadata';
COMMENT ON COLUMN <SCHEMA>.datasource.provider IS 'Provider identifier for data source (e.g., news_api, reuters, internal_docs, web_scraper)';
COMMENT ON COLUMN <SCHEMA>.datasource.expiration IS 'Expiration timestamp for cached data. NULL means never expires';
COMMENT ON COLUMN <SCHEMA>.retrieval_segment.provider IS 'Provider identifier inherited from datasource for faster filtering';

-------------------------------------------------------------------------------
-- 5) Functions (CREATE OR REPLACE handles existence automatically)
-------------------------------------------------------------------------------

-- Enhanced search vector with metadata (schema-specific function name)
CREATE OR REPLACE FUNCTION <SCHEMA>.update_search_vector_<SCHEMA>()
    RETURNS TRIGGER AS
$$
DECLARE
    entities_text TEXT := '';
    entity_item JSONB;
BEGIN
    -- Extract text from entities for search indexing
    FOR entity_item IN SELECT jsonb_array_elements(NEW.entities)
    LOOP
        entities_text := entities_text || ' ' || (entity_item->>'key') || ' ' || (entity_item->>'value');
    END LOOP;

    -- Build search vector with weighted content
    NEW.search_vector :=
        setweight(to_tsvector('english', COALESCE(NEW.title, '')), 'A') ||
        setweight(to_tsvector('english', COALESCE(NEW.content, '')), 'B') ||
        setweight(to_tsvector('english', COALESCE(entities_text, '')), 'A') ||  -- Boost entity terms
        setweight(to_tsvector('english', array_to_string(NEW.tags, ' ')), 'B');  -- Boost tags

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Function to extract entity values for indexing (schema-specific function name)
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

-- Function to check if datasource is expired
CREATE OR REPLACE FUNCTION <SCHEMA>.is_datasource_expired_<SCHEMA>(expiration_ts TIMESTAMPTZ)
    RETURNS BOOLEAN
AS $$
BEGIN
  RETURN expiration_ts IS NOT NULL AND expiration_ts <= now();
END;
$$ LANGUAGE plpgsql STABLE STRICT PARALLEL SAFE;

-- NEW: Function to clean up expired datasources and segments
CREATE OR REPLACE FUNCTION <SCHEMA>.cleanup_expired_data_<SCHEMA>()
    RETURNS TABLE(
        datasources_deleted INT,
        segments_deleted INT
    ) AS
$$
DECLARE
    ds_count INT := 0;
    seg_count INT := 0;
BEGIN
    -- Delete segments from expired datasources
    DELETE FROM <SCHEMA>.retrieval_segment rs
    WHERE EXISTS (
        SELECT 1 FROM <SCHEMA>.datasource ds
        WHERE ds.id = rs.resource_id
        AND ds.version = rs.version
        AND <SCHEMA>.is_datasource_expired_<SCHEMA>(ds.expiration)
    );

    GET DIAGNOSTICS seg_count = ROW_COUNT;

    -- Delete expired datasources
    DELETE FROM <SCHEMA>.datasource ds
    WHERE <SCHEMA>.is_datasource_expired_<SCHEMA>(ds.expiration);

    GET DIAGNOSTICS ds_count = ROW_COUNT;

    RETURN QUERY SELECT ds_count, seg_count;
END;
$$ LANGUAGE plpgsql;

-- ✅ index on ISO text (immutable expression)
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_ds_pub_text
  ON <SCHEMA>.datasource ((metadata->'metadata'->>'published_time_iso'))
  WHERE (metadata->'metadata'->>'published_time_iso') IS NOT NULL
    AND metadata->'metadata'->>'published_time_iso' <> '';

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_ds_mod_text
  ON <SCHEMA>.datasource ((metadata->'metadata'->>'modified_time_iso'))
  WHERE (metadata->'metadata'->>'modified_time_iso') IS NOT NULL
    AND metadata->'metadata'->>'modified_time_iso' <> '';

-- ✅ combined with provider (still TEXT, still immutable)
-- only if you filter on these often
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_rs_ext_provider
  ON <SCHEMA>.retrieval_segment ( ( (extensions->'datasource'->>'provider') ) );

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_rs_ext_pub
  ON <SCHEMA>.retrieval_segment ( ( NULLIF(extensions->'datasource'->>'published_time_iso','') ) );

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_rs_ext_mod
  ON <SCHEMA>.retrieval_segment ( ( NULLIF(extensions->'datasource'->>'modified_time_iso','') ) );

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_rs_ext_exp
  ON <SCHEMA>.retrieval_segment ( ( NULLIF(extensions->'datasource'->>'expiration','') ) );

-------------------------------------------------------------------------------
-- 6) Trigger (with clean IF NOT EXISTS logic)
-------------------------------------------------------------------------------

-- Create trigger only if it doesn't exist
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

-------------------------------------------------------------------------------
-- 7) Indexes (all support IF NOT EXISTS)
-------------------------------------------------------------------------------

-- Primary search index (schema-specific name)
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_rs_search_vector
    ON <SCHEMA>.retrieval_segment USING GIN (search_vector);

-- Semantic similarity index (schema-specific name)
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_rs_embedding
    ON <SCHEMA>.retrieval_segment USING ivfflat (embedding vector_cosine_ops)
WITH (lists=100);

-- Entity search indices (schema-specific names)
CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_rs_entities_gin
    ON <SCHEMA>.retrieval_segment USING GIN (entities jsonb_ops);

CREATE INDEX IF NOT EXISTS idx_<SCHEMA>_rs_entity_values
    ON <SCHEMA>.retrieval_segment
    USING GIN (<SCHEMA>.extract_entity_values_<SCHEMA>(entities));

-- Resource linkage index (critical for cleanup operations) (schema-specific name)
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

-- Entity value search index (with conditional creation)
DO $$
BEGIN
    -- Check if index exists
    IF NOT EXISTS (
        SELECT 1 FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relname = 'idx_<SCHEMA>_rs_entity_values'
          AND n.nspname = '<SCHEMA>'
    ) THEN
        -- Create the functional index
        EXECUTE 'CREATE INDEX idx_<SCHEMA>_rs_entity_values
                ON <SCHEMA>.retrieval_segment USING GIN (<SCHEMA>.extract_entity_values_<SCHEMA>(entities))';
    END IF;
END $$;

-------------------------------------------------------------------------------
-- 8) Views for Common Queries - NEW
-------------------------------------------------------------------------------

-- View for active (non-expired) data sources
CREATE OR REPLACE VIEW <SCHEMA>.active_datasources AS
SELECT *
FROM <SCHEMA>.datasource
WHERE expiration IS NULL OR expiration > now();

-- View for expired data sources
CREATE OR REPLACE VIEW <SCHEMA>.expired_datasources AS
SELECT *
FROM <SCHEMA>.datasource
WHERE expiration IS NOT NULL AND expiration <= now();

