-- =========================================
-- drop-knowledge-base.sql
-- =========================================

-- 0) Views first
DROP VIEW IF EXISTS <SCHEMA>.active_datasources;
DROP VIEW IF EXISTS <SCHEMA>.expired_datasources;

-- 1) Triggers
DROP TRIGGER IF EXISTS trg_<SCHEMA>_update_search_vector ON <SCHEMA>.retrieval_segment;

-- 2) Retrieval Segment indexes
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_rs_ext_exp;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_rs_ext_mod;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_rs_ext_pub;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_rs_ext_provider;

DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_rs_provider_created;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_rs_provider_resource;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_rs_provider;

DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_rs_resource_created;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_rs_created_at;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_rs_tags;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_rs_resource;

DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_rs_entity_values;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_rs_entities_gin;

DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_rs_embedding;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_rs_search_vector;

-- 3) Datasource indexes
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_ds_mod_text;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_ds_pub_text;

DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_ds_cache_lookup;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_ds_expired;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_ds_expiration;

DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_ds_provider_type;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_ds_provider;

DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_ds_metadata;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_ds_created_at;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_ds_status;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_ds_id_version;

-- 4) Content hash indexes
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_ch_name;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_ch_value;

-- 5) Event indexes
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_events_timestamp;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_events_service_metadata;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_events_entity;

-- 6) Functions (new + existing)
DROP FUNCTION IF EXISTS <SCHEMA>.cleanup_expired_data_<SCHEMA>();
DROP FUNCTION IF EXISTS <SCHEMA>.is_datasource_expired_<SCHEMA>(TIMESTAMPTZ);
DROP FUNCTION IF EXISTS <SCHEMA>.extract_entity_values_<SCHEMA>(JSONB);
DROP FUNCTION IF EXISTS <SCHEMA>.update_search_vector_<SCHEMA>();

-- legacy cleanups (if any linger)
DROP FUNCTION IF EXISTS <SCHEMA>.generate_retrieval_segment_rn();
DROP FUNCTION IF EXISTS <SCHEMA>.generate_datasource_rn();

-- 7) Tables (FK order: child → parent)
DROP TABLE IF EXISTS <SCHEMA>.retrieval_segment CASCADE;
DROP TABLE IF EXISTS <SCHEMA>.content_hash CASCADE;
DROP TABLE IF EXISTS <SCHEMA>.datasource CASCADE;
DROP TABLE IF EXISTS <SCHEMA>.events CASCADE;

-- 8) Schema last (should be empty now)
DROP SCHEMA IF EXISTS <SCHEMA>;
