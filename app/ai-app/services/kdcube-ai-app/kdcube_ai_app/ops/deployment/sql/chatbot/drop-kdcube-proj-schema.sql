-- =========================================
-- drop-conversation-history.sql
-- Companion to deploy-conversation-history.sql
-- =========================================

-- ---------- Views ----------
DROP VIEW IF EXISTS <SCHEMA>.rag_chunks_active;
DROP VIEW IF EXISTS <SCHEMA>.conv_messages_expired;

-- ---------- Indexes: conv_messages ----------
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_conv_embedding;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_conv_tags;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_conv_user_conversation_ts;
-- DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_conv_user_conv_track_ts;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_conv_user_conv_turn;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_conv_user_type_ts;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_cm_scope_time;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_cm_text_trgm;

DROP INDEX IF EXISTS <SCHEMA>.conv_messages_bundle_id_idx;
DROP INDEX IF EXISTS <SCHEMA>.conv_messages_user_conv_bundle_ts_idx;

-- ---------- Indexes: conv_artifact_edges ----------
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_edge_from_id;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_edge_to_id;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_edges_to_id;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_edges_policy;

-- ---------- Indexes: user_memory ----------
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_um_user_created;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_um_user_lastseen;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_um_fact_tsv;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_um_tags_gin;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_um_emb_ivf;

-- ---------- Indexes: conv_prefs ----------
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_cp_user_conv_key_ts;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_cp_scope_ts;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_cp_value_gin;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_cp_tags_gin;

-- ---------- Indexes: rag_chunks ----------
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_rag_uni;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_rag_corpus_created;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_rag_meta_gin;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_rag_chunk_tsv;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_rag_emb_ivf;

-- ---------- Indexes: conv_track_programs ----------
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_programs_track_updated;

-- ---------- Indexes: conv_track_tickets ----------
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_tickets_track;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_tickets_status;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_tickets_embedding;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_tickets_conv_user;
DROP INDEX IF EXISTS <SCHEMA>.idx_<SCHEMA>_tickets_turn;

-- ---------- Tables ----------
DROP TABLE IF EXISTS <SCHEMA>.conv_artifact_edges CASCADE;
DROP TABLE IF EXISTS <SCHEMA>.conv_track_tickets CASCADE;
DROP TABLE IF EXISTS <SCHEMA>.conv_track_programs CASCADE;
DROP TABLE IF EXISTS <SCHEMA>.rag_chunks CASCADE;
DROP TABLE IF EXISTS <SCHEMA>.conv_pref_exceptions CASCADE;
DROP TABLE IF EXISTS <SCHEMA>.conv_prefs CASCADE;
DROP TABLE IF EXISTS <SCHEMA>.user_memory CASCADE;
DROP TABLE IF EXISTS <SCHEMA>.conv_messages CASCADE;

-- ---------- (Optional) Drop schema ----------
-- Note: Extensions (pg_trgm, vector, pgcrypto) are shared and intentionally not dropped.
-- Uncomment the line below if you want to remove the schema entirely.
-- DROP SCHEMA IF EXISTS <SCHEMA> CASCADE;

-- Drop schema (only if empty)
-- DROP SCHEMA IF EXISTS <SCHEMA>;