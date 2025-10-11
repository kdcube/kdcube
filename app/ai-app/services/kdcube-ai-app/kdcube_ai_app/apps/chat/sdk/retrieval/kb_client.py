# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/retrieval/kb_client.py
import traceback, os
import asyncpg, json, re, unicodedata, logging
from typing import List, Dict, Any, Optional, Union
from datetime import datetime, timezone, timedelta
import kdcube_ai_app.apps.utils.sql_dt_utils as dt_utils

from kdcube_ai_app.apps.knowledge_base.db.data_models import HybridSearchParams
from kdcube_ai_app.infra.embedding.embedding import convert_embedding_to_string
from kdcube_ai_app.apps.chat.sdk.config import get_settings

logger = logging.getLogger(__name__)

def _day_bounds_utc(day_str: str) -> tuple[str, str]:
    """
    For YYYY-MM-DD, return [start, next_day) bounds as ISO UTC text.
    """
    d = datetime.fromisoformat(day_str).date()
    start = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=timezone.utc)
    end   = start + timedelta(days=1)
    return (
        start.isoformat().replace("+00:00", "Z"),
        end.isoformat().replace("+00:00", "Z"),
    )

class KBClient:
    """
    Queries your KB schema:
      - <SCHEMA>.retrieval_segment with (search_vector TSVECTOR, embedding VECTOR(1536))
      - <SCHEMA>.datasource for expiration
    """
    def __init__(self,
                 pool: Optional[asyncpg.Pool] = None):

        self._pool: Optional[asyncpg.Pool] = pool
        self._settings = get_settings()

        tenant = self._settings.TENANT.replace("-", "_").replace(" ", "_")
        project = self._settings.PROJECT.replace("-", "_").replace(" ", "_")

        schema_name = f"{tenant}_{project}"
        if schema_name and not schema_name.startswith("kdcube_"):
            schema_name = f"kdcube_{schema_name}"

        self.schema = schema_name

    async def init(self):
        # import ssl
        # ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        # ctx.check_hostname = False
        # ctx.verify_mode = ssl.CERT_NONE
        async def _init_conn(conn: asyncpg.Connection):
            # Encode/decode json & jsonb as Python dicts automatically
            await conn.set_type_codec('json',  encoder=json.dumps, decoder=json.loads, schema='pg_catalog')
            await conn.set_type_codec('jsonb', encoder=json.dumps, decoder=json.loads, schema='pg_catalog')
            await conn.execute("SET TIME ZONE 'UTC'; SET datestyle = ISO, YMD;")

        if not self._pool:
            self._pool = await asyncpg.create_pool(
                host=self._settings.PGHOST,
                port=self._settings.PGPORT,
                user=self._settings.PGUSER,
                password=self._settings.PGPASSWORD,
                database=self._settings.PGDATABASE,
                ssl=self._settings.PGSSL,
                max_inactive_connection_lifetime=300.0,
                min_size=int(os.getenv("PGPOOL_MIN_SIZE", "0")),   # 0 so idle workers release conns
                max_size=int(os.getenv("PGPOOL_MAX_SIZE", "2")),   # keep this SMALL in child runtimes
                init=_init_conn,
                server_settings={"application_name": "kdcube-kb-client"},
            )

    async def close(self):
        if self._pool: await self._pool.close()

    async def hybrid_search(
            self, *, query:str, embedding:list[float] | None,
            top_n:int=8, include_expired:bool=False,
            providers: list[str] | None = None,         # NEW
    ) -> List[Dict[str,Any]]:
        # --- use websearch_to_tsquery ---
        fts_query = query.strip()
        use_fts = bool(fts_query)

        facet = ""
        if not include_expired:
            facet += f""" AND EXISTS (SELECT 1 FROM {self.schema}.datasource ds
                           WHERE ds.id=resource_id AND ds.version=rs.version
                           AND (ds.expiration IS NULL OR ds.expiration > now()))"""

        provider_filter = ""
        params = []
        if providers:
            provider_filter = " AND provider = ANY($2)"
            params.append(providers)

        async with self._pool.acquire() as con:
            bm25_rows = []
            if use_fts:
                q = (
                    f"""SELECT id, resource_id, version, provider, content, title, entities, tags, created_at
                        FROM {self.schema}.retrieval_segment rs
                        WHERE search_vector @@ websearch_to_tsquery('english', $1) {facet} {provider_filter}
                        ORDER BY ts_rank_cd(search_vector, websearch_to_tsquery('english', $1), 32) DESC
                        LIMIT {int(top_n*4)}"""
                )
                args = [fts_query] + params
                bm25_rows = await con.fetch(q, *args)

            ann_rows = []
            if embedding is not None:
                embedding = convert_embedding_to_string(embedding)
                q = (
                    f"""SELECT id, resource_id, version, provider, content, title, entities, tags, created_at,
                               (1.0 - (embedding <=> $1)) AS semantic_score
                        FROM {self.schema}.retrieval_segment rs
                        WHERE embedding IS NOT NULL {facet} {provider_filter}
                        ORDER BY embedding <=> $1
                        LIMIT {int(top_n*4)}"""
                )
                args = [embedding] + params
                ann_rows = await con.fetch(q, *args)

            out: Dict[str,Dict[str,Any]] = {}
            for r in bm25_rows:
                out[str(r["id"])] = dict(r) | {"bm25": 1.0, "semantic_score": 0.0}
            for r in ann_rows:
                cur = out.get(str(r["id"]))
                if cur:
                    cur["semantic_score"] = max(cur.get("semantic_score",0.0), float(r["semantic_score"]))
                else:
                    out[str(r["id"])] = dict(r) | {"bm25": 0.0}
            merged = list(out.values())
            merged.sort(key=lambda x: (x.get("semantic_score", 0.0), x.get("bm25", 0.0), x.get("created_at")), reverse=True)
            return merged[:top_n]

    async def hybrid_pipeline_search(
            self,
            params: Union[HybridSearchParams, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Two-stage retrieval (BM25 + ANN), then semantic scoring and optional rerank.
        Stage 1 & 2: retrieval_segment only (no join).
        Stage 3: JOIN datasource for provider/expiry/publication/modified filters on the *candidate set*.
        """

        # -------- normalize incoming params (allow dict) --------
        if isinstance(params, dict):
            params = HybridSearchParams(**params)

        rs = f"{self.schema}.retrieval_segment"
        ds = f"{self.schema}.datasource"

        # --- define TEXT JSON paths once (index-friendly; no ::timestamptz casts) ---
        # pub_text = "(ds.metadata->'metadata'->>'published_time_iso')"
        pub_col = "ds.published_at"
        # mod_text = "(ds.metadata->'metadata'->>'modified_time_iso')"
        mod_col = "ds.modified_at"

        def normalize_query(q: str) -> str:
            nfkd = unicodedata.normalize('NFKD', q or "")
            cleaned = re.sub(r'[^0-9A-Za-z\s]', ' ', nfkd)
            return cleaned.lower().strip()

        def _build_entity_group(entity_filters, use_and: bool, sink: list) -> str:
            if not entity_filters:
                return ""
            parts = []
            for ent in entity_filters:
                parts.append("entities @> $%s::jsonb")
                sink.append(json.dumps([{"key": ent.key, "value": ent.value}]))
            joiner = " AND " if use_and else " OR "
            # We don’t know the $N numbers yet; we’ll backfill below.
            return "(" + joiner.join(parts) + ")"

        # -------- tokens for BM25 prefix query --------
        raw_query = params.query or ""
        q_norm = normalize_query(raw_query)
        terms = [t for t in q_norm.split() if len(t) > 3]
        prefix_tsquery = ' & '.join(f"{t}:*" for t in terms) if terms else ""

        # -------- FACETS that live on retrieval_segment (no join) --------
        rs_clauses: List[str] = []
        rs_params: List[Any] = []

        # resource_ids facet (fast with =ANY($n))
        if params.resource_ids:
            rs_clauses.append(f"resource_id = ANY($%s::text[])")
            rs_params.append(params.resource_ids)

        # entities (JSONB)
        entities_match_all = getattr(params, "entities_match_all", params.match_all)
        ent_params: List[Any] = []
        ent_clause_template = _build_entity_group(getattr(params, "entity_filters", None),
                                                  bool(entities_match_all), ent_params)
        if ent_clause_template:
            # patch $%s with correct indices after we know current param count
            base = len(rs_params) + 1
            patched = ent_clause_template
            for i in range(len(ent_params)):
                patched = patched.replace("$%s", f"${base+i}", 1)
            rs_clauses.append(patched)
            rs_params.extend(ent_params)

        rs_facets_sql = " AND ".join(rs_clauses) if rs_clauses else ""

        # -------- RECALL (query + tags) with match_all --------
        recall_clauses: List[str] = []
        recall_params: List[Any] = []

        if prefix_tsquery:
            recall_clauses.append("search_vector @@ to_tsquery('english', $%s)")
            recall_params.append(prefix_tsquery)

        if params.tags:
            if params.match_all:
                recall_clauses.append("tags @> $%s::text[]")
            else:
                recall_clauses.append("tags && $%s::text[]")
            recall_params.append(params.tags)

        if recall_clauses:
            # backfill $N in recall template
            base = len(rs_params) + 1
            patched_parts = []
            for i, tmpl in enumerate(recall_clauses):
                patched_parts.append(tmpl.replace("$%s", f"${base+i}"))
            recall_sql = "(" + (" AND " if params.match_all else " OR ").join(patched_parts) + ")"
            rs_params.extend(recall_params)
        else:
            recall_sql = ""

        # -------- Stage 1: BM25 over rs only --------
        # bind WHERE first
        where_parts = []
        if rs_facets_sql: where_parts.append(f"({rs_facets_sql})")
        if recall_sql:     where_parts.append(recall_sql)
        bm25_where = " AND ".join(where_parts) if where_parts else "TRUE"

        order_params: List[Any] = []  # <<< keep ORDER BY params separate
        if prefix_tsquery:
            # reference a NEW placeholder and push the param
            bm25_order = f"ts_rank_cd(search_vector, to_tsquery('english', ${len(rs_params)+1}), 32) DESC"
            order_params.append(prefix_tsquery)
        else:
            bm25_order = "created_at DESC"

        bm25_k = int(getattr(params, "bm25_k", 100))
        bm25_sql = f"""
            SELECT id AS segment_id
            FROM {rs}
            WHERE {bm25_where}
            ORDER BY {bm25_order}
            LIMIT {bm25_k}
        """

        async with self._pool.acquire() as con:
            # <<< pass rs_params + order_params
            bm25_rows = await con.fetch(bm25_sql, *(rs_params + order_params))
            bm25_ids = [r["segment_id"] for r in bm25_rows]

            # -------- Stage 2: ANN fallback (rs only) --------
            if params.embedding is None:
                return []

            # We pass vector literal as text; server casts during <=> comparison.
            from kdcube_ai_app.infra.embedding.embedding import convert_embedding_to_string
            emb_lit = convert_embedding_to_string(params.embedding)  # e.g., "[0.1,0.2,...]"
            fallback_k = int(getattr(params, "fallback_k", params.top_n))

            ann_clauses = ["embedding IS NOT NULL"]
            ann_params: List[Any] = []

            if rs_facets_sql:
                # We must reproduce the same rs facets with new $N numbering
                # Re-generate the facet SQL with fresh numbering
                # (simple approach: rebuild like above)
                ann_rs_clauses: List[str] = []
                ann_rs_params: List[Any] = []

                if params.resource_ids:
                    ann_rs_clauses.append(f"resource_id = ANY($%s::text[])")
                    ann_rs_params.append(params.resource_ids)

                ent_params2: List[Any] = []
                ent_clause2 = _build_entity_group(getattr(params, "entity_filters", None),
                                                  bool(entities_match_all), ent_params2)
                if ent_clause2:
                    base = len(ann_rs_params) + 1
                    patched = ent_clause2
                    for i in range(len(ent_params2)):
                        patched = patched.replace("$%s", f"${base+i}", 1)
                    ann_rs_clauses.append(patched)
                    ann_rs_params.extend(ent_params2)

                if ann_rs_clauses:
                    ann_clauses.append("(" + " AND ".join(ann_rs_clauses) + ")")
                    ann_params.extend(ann_rs_params)

            # <<< cast embedding param to ::vector for asyncpg
            ann_sql = f"""
                SELECT id AS segment_id
                FROM {rs}
                WHERE {" AND ".join(ann_clauses)}
                ORDER BY embedding <=> ${len(ann_params)+1}::vector
                LIMIT {fallback_k}
            """
            ann_rows = await con.fetch(ann_sql, *(ann_params + [emb_lit]))
            ann_ids = [r["segment_id"] for r in ann_rows]

            # -------- Stage 3: semantic scoring on union + JOIN ds (post-filter) --------
            candidate_ids = list(dict.fromkeys(bm25_ids + ann_ids))
            if not candidate_ids:
                return []

            # Build ds filters (providers/expiry/publication/modified) AFTER candidates known
            ds_filters: List[str] = []
            ds_params: List[Any] = []

            # ---------- provider facet
            if params.providers:
                ds_filters.append(f"ds.provider = ANY($%s::text[])")
                ds_params.append(params.providers)

            # ---------- expiry facet
            if not getattr(params, "include_expired", True):
                ds_filters.append("(ds.expiration IS NULL OR ds.expiration > now())")

            # ---------- publication time (TEXT, index-friendly)
            pub_clauses, pub_vals = dt_utils.build_temporal_filters(
                # col_expr=pub_text, mode="text",
                col_expr=pub_col, mode="timestamptz",
                on=params.published_on, after=params.published_after, before=params.published_before
            )
            # --- modified time filters ---
            mod_clauses, mod_vals = dt_utils.build_temporal_filters(
                # col_expr=mod_text, mode="text",
                col_expr=mod_col, mode="timestamptz",
                on=params.modified_on,  after=params.modified_after,  before=params.modified_before
            )
            ds_filters.extend(pub_clauses + mod_clauses)
            ds_params.extend(pub_vals + mod_vals)

            # Patch $%s placeholders with actual indexes (after the first two params)
            # Order of params: 1) emb_lit, 2) candidate_ids array, then ds_params...
            base = 2  # $1=embedding vector, $2=candidate_ids
            patched_ds_filters, _ = dt_utils.patch_placeholders(ds_filters, start_index=base)
            ds_filter_sql = (" AND " + " AND ".join(patched_ds_filters)) if patched_ds_filters else ""

            # <<< cast $1 to ::vector; cast $2 to ::text[]
            semantic_sql = f"""
                SELECT
                  rs.id            AS segment_id,
                  rs.resource_id,
                  rs.version,
                  rs.provider,
                  rs.content,
                  rs.title,
                  rs.lineage,
                  rs.entities,
                  rs.extensions,
                  rs.tags,
                  rs.created_at,
                  ds.event_ts,
                  ds.published_at,
                  ds.modified_at,
                  (1.0 - (rs.embedding <=> $1::vector)) AS semantic_score,
                  CASE WHEN rs.embedding IS NOT NULL THEN 1.0 ELSE 0.0 END AS has_embedding,
                  jsonb_build_object(
                    'id', rs.resource_id,
                    'version', rs.version,
                    'rn', ds.rn,
                    'provider', ds.provider,
                    'title', ds.title,
                    'uri', ds.uri,
                    'system_uri', ds.system_uri,
                    'metadata', ds.metadata,
                    'active', (ds.expiration IS NULL OR ds.expiration > now()),
--                     'published_time_iso', ds.metadata->'metadata'->>'published_time_iso',
--                     'modified_time_iso',  ds.metadata->'metadata'->>'modified_time_iso'
                    'published_at', to_char((ds.published_at AT TIME ZONE 'UTC'), 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                    'modified_at',  to_char((ds.modified_at  AT TIME ZONE 'UTC'), 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                    'event_ts',     to_char((ds.event_ts     AT TIME ZONE 'UTC'), 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
                  ) AS datasource
                FROM {rs} rs
                JOIN {ds} ds
                  ON ds.id = rs.resource_id
                 AND ds.version = rs.version
                WHERE rs.id = ANY($2::text[])
                {ds_filter_sql}
            """
            ds_placeholders = sum(1 for _ in re.finditer(r"\$(\d+)", ds_filter_sql))
            # It counts all $N in DS part; we expect it to equal len(ds_params) plus
            # the two earlier placeholders in the main query ($1, $2) are outside ds_filter_sql.
            logger.debug("DS SQL: %s", ds_filter_sql)
            logger.debug("DS params (%d): %r", len(ds_params), ds_params)
            sem_rows = await con.fetch(semantic_sql, emb_lit, candidate_ids, *ds_params)

        # -------- local filtering/sorting + optional rerank --------
        thresh = params.min_similarity or 0.0
        filtered = [dict(r) for r in sem_rows if float(r.get("semantic_score", 0.0)) >= thresh]

        # for r in filtered:
        #     dsj = (r.get("datasource") or {})
        #     pub = ((dsj.get("metadata") or {}).get("metadata") or {}).get("published_time_iso") or dsj.get("published_time_iso")
        #     r["_published_ts"] = pub
        for r in filtered:
            # prefer event_ts, fallback to created_at
            r["_event_ts"] = r.get("event_ts") or r.get("created_at")

        # filtered.sort(
        #     key=lambda r: (float(r.get("semantic_score", 0.0)),
        #                    float(r.get("has_embedding", 0.0)),
        #                    dt_utils.parse_ts_safe(r.get("_published_ts"))),
        #     reverse=True
        # )
        filtered.sort(
            key=lambda r: (float(r.get("semantic_score", 0.0)),
                           float(r.get("has_embedding", 0.0)),
                           dt_utils.parse_ts_safe(r.get("_event_ts"))),
            reverse=True
        )
        top_sem = filtered[: params.top_n]

        # Optional cross-encoder rerank
        if params.should_rerank:
            try:
                from kdcube_ai_app.infra.rerank.rerank import cross_encoder_rerank
                if top_sem:
                    reranked = cross_encoder_rerank(raw_query or q_norm, top_sem, 'content')
                    if params.rerank_threshold is not None and len(reranked) > (params.rerank_top_k or params.top_n) * 2:
                       reranked = [r for r in reranked if r.get("rerank_score", 0.0) >= params.rerank_threshold]
                    top_sem = reranked[: (params.rerank_top_k or params.top_n)]
            except Exception:
                # Reranker unavailable — keep semantic order
                logger.error(traceback.format_exc())

        # strip helper field
        # for r in top_sem:
        #     r.pop("_published_ts", None)
        for r in top_sem:
            r.pop("_event_ts", None)
        return top_sem

    async def hybrid_pipeline_search_nojoin(
            self,
            params: Union[HybridSearchParams, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Two-stage retrieval over retrieval_segment only (NO JOIN).
        Stage 1: BM25/prefix recall (rs only)
        Stage 2: ANN fallback (rs only)
        Stage 3: semantic scoring + post-filters that read from rs.extensions->'datasource'

        Date filter semantics:
          - published_on / modified_on:  day-closed range  [day, day+1)
          - published_after / modified_after:  >= instant (if date-only, start-of-day)
          - published_before / modified_before: < instant (if date-only, start-of-day)
        """
        # Normalize param container
        if isinstance(params, dict):
            params = HybridSearchParams(**params)

        rs = f"{self.schema}.retrieval_segment"

        # JSON text paths inside extensions.datasource
        ds_node     = "(rs.extensions->'datasource')"
        ds_rn       = f"({ds_node}->>'rn')"
        ds_uri      = f"({ds_node}->>'uri')"
        ds_mime     = f"({ds_node}->>'mime')"
        ds_title    = f"({ds_node}->>'title')"
        ds_exp_text = f"({ds_node}->>'expiration')"
        ds_src_type = f"({ds_node}->>'source_type')"
        ds_pub_text = f"({ds_node}->>'published_time_iso')"
        ds_mod_text = f"({ds_node}->>'modified_time_iso')"
        ds_event_text = f"({ds_node}->>'event_ts')"

        def normalize_query(q: str) -> str:
            nfkd = unicodedata.normalize('NFKD', q or "")
            cleaned = re.sub(r'[^0-9A-Za-z\s]', ' ', nfkd)
            return cleaned.lower().strip()

        def _build_entity_group(entity_filters, use_and: bool, sink: list) -> str:
            if not entity_filters:
                return ""
            parts = []
            for ent in entity_filters:
                parts.append("entities @> $%s::jsonb")
                sink.append(json.dumps([{"key": ent.key, "value": ent.value}]))
            return "(" + (" AND " if use_and else " OR ").join(parts) + ")"

        def _is_date_only(x: Any) -> bool:
            return isinstance(x, str) and ("T" not in x) and len(x) >= 10 and x[4] == "-" and x[7] == "-"

        # --- Tokens for BM25 prefix recall
        raw_query = params.query or ""
        q_norm = normalize_query(raw_query)
        terms = [t for t in q_norm.split() if len(t) > 3]
        prefix_tsquery = ' & '.join(f"{t}:*" for t in terms) if terms else ""

        # --- Facets on retrieval_segment
        rs_clauses: List[str] = []
        rs_params: List[Any] = []

        if params.resource_ids:
            rs_clauses.append("resource_id = ANY($%s::text[])")
            rs_params.append(params.resource_ids)

        entities_match_all = getattr(params, "entities_match_all", params.match_all)
        ent_params: List[Any] = []
        ent_clause_template = _build_entity_group(getattr(params, "entity_filters", None),
                                                  bool(entities_match_all), ent_params)
        if ent_clause_template:
            base = len(rs_params) + 1
            patched = ent_clause_template
            for i in range(len(ent_params)):
                patched = patched.replace("$%s", f"${base+i}", 1)
            rs_clauses.append(patched)
            rs_params.extend(ent_params)

        rs_facets_sql = " AND ".join(rs_clauses) if rs_clauses else ""

        # --- Recall (query + tags)
        recall_clauses: List[str] = []
        recall_params: List[Any] = []

        if prefix_tsquery:
            recall_clauses.append("search_vector @@ to_tsquery('english', $%s)")
            recall_params.append(prefix_tsquery)

        if params.tags:
            if params.match_all:
                recall_clauses.append("tags @> $%s::text[]")
            else:
                recall_clauses.append("tags && $%s::text[]")
            recall_params.append(params.tags)

        if recall_clauses:
            base = len(rs_params) + 1
            patched_parts = []
            for i, tmpl in enumerate(recall_clauses):
                patched_parts.append(tmpl.replace("$%s", f"${base+i}"))
            recall_sql = "(" + (" AND " if params.match_all else " OR ").join(patched_parts) + ")"
            rs_params.extend(recall_params)
        else:
            recall_sql = ""

        # --- Stage 1: BM25 over rs only
        where_parts = []
        if rs_facets_sql: where_parts.append(f"({rs_facets_sql})")
        if recall_sql:     where_parts.append(recall_sql)
        bm25_where = " AND ".join(where_parts) if where_parts else "TRUE"

        order_params: List[Any] = []
        if prefix_tsquery:
            bm25_order = f"ts_rank_cd(search_vector, to_tsquery('english', ${len(rs_params)+1}), 32) DESC"
            order_params.append(prefix_tsquery)
        else:
            bm25_order = "created_at DESC"

        bm25_k = int(getattr(params, "bm25_k", 100))
        bm25_sql = f"""
            SELECT id AS segment_id
            FROM {rs}
            WHERE {bm25_where}
            ORDER BY {bm25_order}
            LIMIT {bm25_k}
        """

        async with self._pool.acquire() as con:
            bm25_rows = await con.fetch(bm25_sql, *(rs_params + order_params))
            bm25_ids = [r["segment_id"] for r in bm25_rows]

            # --- Stage 2: ANN fallback over rs only
            if params.embedding is None:
                return []

            emb_lit = convert_embedding_to_string(params.embedding)
            fallback_k = int(getattr(params, "fallback_k", params.top_n))

            ann_clauses = ["embedding IS NOT NULL"]
            ann_params: List[Any] = []

            if rs_facets_sql:
                ann_rs_clauses: List[str] = []
                ann_rs_params: List[Any] = []

                if params.resource_ids:
                    ann_rs_clauses.append("resource_id = ANY($%s::text[])")
                    ann_rs_params.append(params.resource_ids)

                ent_params2: List[Any] = []
                ent_clause2 = _build_entity_group(getattr(params, "entity_filters", None),
                                                  bool(entities_match_all), ent_params2)
                if ent_clause2:
                    base = len(ann_rs_params) + 1
                    patched = ent_clause2
                    for i in range(len(ent_params2)):
                        patched = patched.replace("$%s", f"${base+i}", 1)
                    ann_rs_clauses.append(patched)
                    ann_rs_params.extend(ent_params2)

                if ann_rs_clauses:
                    ann_clauses.append("(" + " AND ".join(ann_rs_clauses) + ")")
                    ann_params.extend(ann_rs_params)

            ann_sql = f"""
                SELECT id AS segment_id
                FROM {rs}
                WHERE {" AND ".join(ann_clauses)}
                ORDER BY embedding <=> ${len(ann_params)+1}::vector
                LIMIT {fallback_k}
            """
            ann_rows = await con.fetch(ann_sql, *(ann_params + [emb_lit]))
            ann_ids = [r["segment_id"] for r in ann_rows]

            # --- Stage 3: semantic scoring + post-filters (NO JOIN)
            candidate_ids = list(dict.fromkeys(bm25_ids + ann_ids))
            if not candidate_ids:
                return []

            seg_filters: List[str] = []
            seg_params: List[Any] = []

            # provider facet (rs.provider)
            if params.providers:
                seg_filters.append("rs.provider = ANY($%s::text[])")
                seg_params.append(params.providers)

            # expiration facet (extensions.datasource.expiration TEXT, compare as timestamptz)
            if not getattr(params, "include_expired", True):
                now_dt = datetime.now(timezone.utc)
                seg_filters.append(
                    f"({ds_exp_text} IS NULL OR {ds_exp_text} = '' OR {ds_exp_text}::timestamptz > $%s::timestamptz)"
                )
                seg_params.append(now_dt)

            # Published / Modified (TEXT mode)
            pub_clauses, pub_vals = dt_utils.build_temporal_filters(
                col_expr=ds_pub_text, mode="text",
                on=params.published_on, after=params.published_after, before=params.published_before
            )
            mod_clauses, mod_vals = dt_utils.build_temporal_filters(
                col_expr=ds_mod_text, mode="text",
                on=params.modified_on,  after=params.modified_after,  before=params.modified_before
            )
            seg_filters.extend(pub_clauses + mod_clauses)
            seg_params.extend(pub_vals + mod_vals)

            # Always include $3 for "now" even if include_expired=True? Only if we referenced it.
            patched_seg_filters, last_idx = dt_utils.patch_placeholders(seg_filters, start_index=2)
            seg_filter_sql = (" AND " + " AND ".join(patched_seg_filters)) if patched_seg_filters else ""

            sem_sql = f"""
                SELECT
                  rs.id            AS segment_id,
                  rs.resource_id,
                  rs.version,
                  rs.provider,
                  rs.content,
                  rs.title,
                  rs.lineage,
                  rs.entities,
                  rs.extensions,
                  rs.tags,
                  rs.created_at,
                  (1.0 - (rs.embedding <=> $1::vector)) AS semantic_score,
                  CASE WHEN rs.embedding IS NOT NULL THEN 1.0 ELSE 0.0 END AS has_embedding,
                  jsonb_build_object(
                    'rn',                {ds_rn},
                    'uri',               {ds_uri},
                    'mime',              {ds_mime},
                    'title',             {ds_title},
                    'source_type',       {ds_src_type},
                    'modified_time_iso', {ds_mod_text},
                    'published_time_iso',{ds_pub_text},
                    'expiration',        {ds_exp_text},
                    'event_ts',          {ds_event_text}
                  ) AS datasource
                FROM {rs} rs
                WHERE rs.id = ANY($2::text[])
                {seg_filter_sql}
            """

            # Args order matches: $1 embedding, $2 candidate_ids, then seg_params
            args = [emb_lit, candidate_ids] + seg_params
            sem_rows = await con.fetch(sem_sql, *args)

        # --- local filter/sort + rerank
        thresh = params.min_similarity or 0.0
        filtered = [dict(r) for r in sem_rows if float(r.get("semantic_score", 0.0)) >= thresh]

        # for r in filtered:
        #     ds = r.get("datasource") or {}
        #     r["_published_ts"] = ds.get("published_time_iso")
        for r in filtered:
            ds = r.get("datasource") or {}
            r["_event_ts"] = ds.get("event_ts") or ds.get("published_time_iso")
        filtered.sort(
            key=lambda r: (float(r.get("semantic_score", 0.0)),
                           float(r.get("has_embedding", 0.0)),
                           # dt_utils.parse_ts_safe(r.get("_published_ts"))),
                           dt_utils.parse_ts_safe(r.get("_event_ts"))),
            reverse=True
        )

        top_sem = filtered[: params.top_n]

        if params.should_rerank:
            try:
                from kdcube_ai_app.infra.rerank.rerank import cross_encoder_rerank
                if top_sem:
                    reranked = cross_encoder_rerank(raw_query or q_norm, top_sem, 'content')
                    if params.rerank_threshold is not None and len(reranked) > (params.rerank_top_k or params.top_n) * 2:
                        reranked = [r for r in reranked if r.get("rerank_score", 0.0) >= params.rerank_threshold]
                    top_sem = reranked[: (params.rerank_top_k or params.top_n)]
            except Exception:
                logger.error(traceback.format_exc())

        # for r in top_sem:
        #     r.pop("_published_ts", None)
        for r in top_sem:
            r.pop("_event_ts", None)
        return top_sem


    async def hybrid_pipeline_search_nojoin_blend(
            self,
            params: Union[HybridSearchParams, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Two-stage retrieval over retrieval_segment only (NO JOIN).
        Stage 1: BM25/prefix recall (rs only)
        Stage 2: ANN fallback (rs only)
        Stage 3: semantic scoring + post-filters that read from rs.extensions->'datasource'

        Date filter semantics:
          - published_on / modified_on:  day-closed range  [day, day+1)
          - published_after / modified_after:  >= instant (if date-only, start-of-day)
          - published_before / modified_before: < instant (if date-only, start-of-day)
        """
        # Normalize param container
        if isinstance(params, dict):
            params = HybridSearchParams(**params)

        rs = f"{self.schema}.retrieval_segment"

        # JSON text paths inside extensions.datasource
        ds_node     = "(rs.extensions->'datasource')"
        ds_rn       = f"({ds_node}->>'rn')"
        ds_uri      = f"({ds_node}->>'uri')"
        ds_mime     = f"({ds_node}->>'mime')"
        ds_title    = f"({ds_node}->>'title')"
        ds_exp_text = f"({ds_node}->>'expiration')"
        ds_src_type = f"({ds_node}->>'source_type')"
        ds_pub_text = f"({ds_node}->>'published_time_iso')"
        ds_mod_text = f"({ds_node}->>'modified_time_iso')"
        ds_event_text = f"({ds_node}->>'event_ts')"

        def normalize_query(q: str) -> str:
            nfkd = unicodedata.normalize('NFKD', q or "")
            cleaned = re.sub(r'[^0-9A-Za-z\s]', ' ', nfkd)
            return cleaned.lower().strip()

        def _build_entity_group(entity_filters, use_and: bool, sink: list) -> str:
            if not entity_filters:
                return ""
            parts = []
            for ent in entity_filters:
                parts.append("entities @> $%s::jsonb")
                sink.append(json.dumps([{"key": ent.key, "value": ent.value}]))
            return "(" + (" AND " if use_and else " OR ").join(parts) + ")"

        def _is_date_only(x: Any) -> bool:
            return isinstance(x, str) and ("T" not in x) and len(x) >= 10 and x[4] == "-" and x[7] == "-"

        # --- Tokens for BM25 prefix recall
        raw_query = params.query or ""
        q_norm = normalize_query(raw_query)
        terms = [t for t in q_norm.split() if len(t) > 3]
        prefix_tsquery = ' & '.join(f"{t}:*" for t in terms) if terms else ""

        # --- Facets on retrieval_segment
        rs_clauses: List[str] = []
        rs_params: List[Any] = []

        if params.resource_ids:
            rs_clauses.append("resource_id = ANY($%s::text[])")
            rs_params.append(params.resource_ids)

        entities_match_all = getattr(params, "entities_match_all", params.match_all)
        ent_params: List[Any] = []
        ent_clause_template = _build_entity_group(getattr(params, "entity_filters", None),
                                                  bool(entities_match_all), ent_params)
        if ent_clause_template:
            base = len(rs_params) + 1
            patched = ent_clause_template
            for i in range(len(ent_params)):
                patched = patched.replace("$%s", f"${base+i}", 1)
            rs_clauses.append(patched)
            rs_params.extend(ent_params)

        rs_facets_sql = " AND ".join(rs_clauses) if rs_clauses else ""

        # --- Recall (query + tags)
        recall_clauses: List[str] = []
        recall_params: List[Any] = []

        if prefix_tsquery:
            recall_clauses.append("search_vector @@ to_tsquery('english', $%s)")
            recall_params.append(prefix_tsquery)

        if params.tags:
            if params.match_all:
                recall_clauses.append("tags @> $%s::text[]")
            else:
                recall_clauses.append("tags && $%s::text[]")
            recall_params.append(params.tags)

        if recall_clauses:
            base = len(rs_params) + 1
            patched_parts = []
            for i, tmpl in enumerate(recall_clauses):
                patched_parts.append(tmpl.replace("$%s", f"${base+i}"))
            recall_sql = "(" + (" AND " if params.match_all else " OR ").join(patched_parts) + ")"
            rs_params.extend(recall_params)
        else:
            recall_sql = ""

        # --- Stage 1: BM25 over rs only
        where_parts = []
        if rs_facets_sql: where_parts.append(f"({rs_facets_sql})")
        if recall_sql:     where_parts.append(recall_sql)
        bm25_where = " AND ".join(where_parts) if where_parts else "TRUE"

        order_params: List[Any] = []
        if prefix_tsquery:
            bm25_order = f"ts_rank_cd(search_vector, to_tsquery('english', ${len(rs_params)+1}), 32) DESC"
            order_params.append(prefix_tsquery)
        else:
            bm25_order = "created_at DESC"

        bm25_k = int(getattr(params, "bm25_k", 100))
        bm25_sql = f"""
            SELECT id AS segment_id
            FROM {rs}
            WHERE {bm25_where}
            ORDER BY {bm25_order}
            LIMIT {bm25_k}
        """

        async with self._pool.acquire() as con:
            bm25_rows = await con.fetch(bm25_sql, *(rs_params + order_params))
            bm25_ids = [r["segment_id"] for r in bm25_rows]

            # --- Stage 2: ANN fallback over rs only
            if params.embedding is None:
                return []

            emb_lit = convert_embedding_to_string(params.embedding)
            fallback_k = int(getattr(params, "fallback_k", params.top_n))

            ann_clauses = ["embedding IS NOT NULL"]
            ann_params: List[Any] = []

            if rs_facets_sql:
                ann_rs_clauses: List[str] = []
                ann_rs_params: List[Any] = []

                if params.resource_ids:
                    ann_rs_clauses.append("resource_id = ANY($%s::text[])")
                    ann_rs_params.append(params.resource_ids)

                ent_params2: List[Any] = []
                ent_clause2 = _build_entity_group(getattr(params, "entity_filters", None),
                                                  bool(entities_match_all), ent_params2)
                if ent_clause2:
                    base = len(ann_rs_params) + 1
                    patched = ent_clause2
                    for i in range(len(ent_params2)):
                        patched = patched.replace("$%s", f"${base+i}", 1)
                    ann_rs_clauses.append(patched)
                    ann_rs_params.extend(ent_params2)

                if ann_rs_clauses:
                    ann_clauses.append("(" + " AND ".join(ann_rs_clauses) + ")")
                    ann_params.extend(ann_rs_params)

            ann_sql = f"""
                SELECT id AS segment_id
                FROM {rs}
                WHERE {" AND ".join(ann_clauses)}
                ORDER BY embedding <=> ${len(ann_params)+1}::vector
                LIMIT {fallback_k}
            """
            ann_rows = await con.fetch(ann_sql, *(ann_params + [emb_lit]))
            ann_ids = [r["segment_id"] for r in ann_rows]

            # --- Stage 3: semantic scoring + post-filters (NO JOIN)
            candidate_ids = list(dict.fromkeys(bm25_ids + ann_ids))
            if not candidate_ids:
                return []

            seg_filters: List[str] = []
            seg_params: List[Any] = []

            # provider facet (rs.provider)
            if params.providers:
                seg_filters.append("rs.provider = ANY($%s::text[])")
                seg_params.append(params.providers)

            # expiration facet (extensions.datasource.expiration TEXT, compare as timestamptz)
            if not getattr(params, "include_expired", True):
                now_dt = datetime.now(timezone.utc)
                seg_filters.append(
                    f"({ds_exp_text} IS NULL OR {ds_exp_text} = '' OR {ds_exp_text}::timestamptz > $%s::timestamptz)"
                )
                seg_params.append(now_dt)

            # Published / Modified (TEXT mode)
            pub_clauses, pub_vals = dt_utils.build_temporal_filters(
                col_expr=ds_pub_text, mode="text",
                on=params.published_on, after=params.published_after, before=params.published_before
            )
            mod_clauses, mod_vals = dt_utils.build_temporal_filters(
                col_expr=ds_mod_text, mode="text",
                on=params.modified_on,  after=params.modified_after,  before=params.modified_before
            )
            seg_filters.extend(pub_clauses + mod_clauses)
            seg_params.extend(pub_vals + mod_vals)

            # Always include $3 for "now" even if include_expired=True? Only if we referenced it.
            patched_seg_filters, last_idx = dt_utils.patch_placeholders(seg_filters, start_index=2)
            seg_filter_sql = (" AND " + " AND ".join(patched_seg_filters)) if patched_seg_filters else ""

            sem_sql = f"""
                SELECT
                  rs.id            AS segment_id,
                  rs.resource_id,
                  rs.version,
                  rs.provider,
                  rs.content,
                  rs.title,
                  rs.lineage,
                  rs.entities,
                  rs.extensions,
                  rs.tags,
                  rs.created_at,
                  (1.0 - (rs.embedding <=> $1::vector)) AS semantic_score,
                  CASE WHEN rs.embedding IS NOT NULL THEN 1.0 ELSE 0.0 END AS has_embedding,
                  jsonb_build_object(
                    'rn',                {ds_rn},
                    'uri',               {ds_uri},
                    'mime',              {ds_mime},
                    'title',             {ds_title},
                    'source_type',       {ds_src_type},
                    'modified_time_iso', {ds_mod_text},
                    'published_time_iso',{ds_pub_text},
                    'expiration',        {ds_exp_text},
                    'event_ts',          {ds_event_text}
                  ) AS datasource
                FROM {rs} rs
                WHERE rs.id = ANY($2::text[])
                {seg_filter_sql}
            """

            # Args order matches: $1 embedding, $2 candidate_ids, then seg_params
            args = [emb_lit, candidate_ids] + seg_params
            sem_rows = await con.fetch(sem_sql, *args)

        # ---------- post-SQL local scoring & filters ----------
        # Build a set of BM25 seeds so we can award a small text signal.
        bm25_seed_ids = set(map(str, bm25_ids))

        # Extract routing/scoring knobs (all optional)
        weights = (getattr(params, "weights", None) or
                   params.get("weights") if isinstance(params, dict) else None) or {}
        w_sem  = float(weights.get("w_sem", 1.0))
        w_bm25 = float(weights.get("w_bm25", 0.0))
        w_rec  = float(weights.get("w_rec", 0.0))
        w_auth = float(weights.get("w_auth", 0.0))

        # Half-life in hours; if 0 → disable recency
        default_half_life_h = float(params.get("recency_half_life_hours", 0.0)
                                    if isinstance(params, dict)
                                    else getattr(params, "recency_half_life_hours", 0.0))

        # Provider authority map (0..1); overrideable
        provider_authority = (params.get("provider_authority", {})
                              if isinstance(params, dict)
                              else getattr(params, "provider_authority", {})) or {}

        # Diversity controls
        per_provider_cap = int(params.get("per_provider_cap", 0)
                               if isinstance(params, dict)
                               else getattr(params, "per_provider_cap", 0))  # 0 = no cap
        do_dedup = bool(params.get("dedup", True)
                        if isinstance(params, dict)
                        else getattr(params, "dedup", True))

        # Threshold
        thresh = float(params.get("min_similarity", 0.0)
                       if isinstance(params, dict)
                       else (params.min_similarity or 0.0))

        now = datetime.now(timezone.utc)

        # 1) build enriched dicts + compute recency/authority/bm25 flags
        enriched = []
        for row in sem_rows:
            r = dict(row)
            seg_id = str(r.get("segment_id") or r.get("id") or "")
            prov   = r.get("provider") or ((r.get("datasource") or {}).get("provider")) or ""
            sem    = float(r.get("semantic_score", 0.0))
            if sem < thresh:
                continue

            # bm25 flag (1.0 if segment appeared in BM25 candidate set)
            bm25_flag = 1.0 if seg_id in bm25_seed_ids else 0.0

            # choose event_ts/published/modified as "when"
            # (already set above for your earlier ordering; reuse it)
            ds = r.get("datasource") or {}
            when_iso = (ds.get("event_ts") or
                        ds.get("published_time_iso") or
                        ds.get("modified_time_iso") or
                        r.get("created_at"))
            when_dt = dt_utils.parse_ts_safe(when_iso)

            # recency bonus
            rec_bonus = 0.0
            hl_h = default_half_life_h
            if isinstance(provider_authority, dict):
                # Allow providers to carry a custom half-life via pseudo-key "__half_life_hours"
                hl_h = float(provider_authority.get("__half_life_hours", hl_h)) if hl_h <= 0 else hl_h
            # If you want per-provider half-life, allow params.provider_half_lives = {prov: hours}
            provider_half_lives = (params.get("provider_half_lives", {})
                                   if isinstance(params, dict)
                                   else getattr(params, "provider_half_lives", {})) or {}
            if prov in provider_half_lives:
                hl_h = float(provider_half_lives[prov])

            if w_rec > 0 and hl_h > 0 and when_dt:
                age_h = max(0.0, (now - when_dt).total_seconds() / 3600.0)
                rec_bonus = pow(2.718281828, -age_h / hl_h)  # exp(-age / half_life)

            # authority weight (0..1)
            auth = float(provider_authority.get(prov, 1.0)) if isinstance(provider_authority, dict) else 1.0

            # final blended score
            final_score = (w_sem * sem) + (w_bm25 * bm25_flag) + (w_rec * rec_bonus) + (w_auth * auth)

            r.update({
                "bm25_flag": bm25_flag,
                "recency_bonus": rec_bonus,
                "authority": auth,
                "final_score": final_score,
            })
            enriched.append(r)

        # 2) lightweight dedup (resource_id + canonical url + near-identical titles)
        def _norm_title(t):
            if not t:
                return ""
            t = t.lower()
            t = re.sub(r"[^a-z0-9]+", " ", t).strip()
            return t

        seen_keys = set()
        seen_titles = set()
        deduped = []
        for r in sorted(enriched, key=lambda x: x["final_score"], reverse=True):
            ds = r.get("datasource") or {}
            rid = f"{r.get('resource_id') or ''}:{r.get('version') or ''}"
            url = r.get("url") or ds.get("uri") or ds.get("system_uri") or ""
            k = (rid, url)
            tnorm = _norm_title(r.get("title") or ds.get("title") or "")
            if do_dedup:
                if k in seen_keys:
                    continue
                # collapse near-duplicates on same normalized title
                if tnorm and tnorm in seen_titles:
                    continue
            seen_keys.add(k)
            if tnorm:
                seen_titles.add(tnorm)
            deduped.append(r)

        # 3) diversity cap per provider
        if per_provider_cap > 0:
            taken_by = {}
            diversified = []
            for r in deduped:
                prov = r.get("provider") or ((r.get("datasource") or {}).get("provider")) or ""
                c = taken_by.get(prov, 0)
                if c < per_provider_cap:
                    diversified.append(r)
                    taken_by[prov] = c + 1
            deduped = diversified

        # 4) take top_n and (optionally) rerank
        top_n = int(params.get("top_n", 10) if isinstance(params, dict) else params.top_n)
        top_sem = deduped[: top_n]

        if params.should_rerank:
            try:
                from kdcube_ai_app.infra.rerank.rerank import cross_encoder_rerank
                if top_sem:
                    reranked = cross_encoder_rerank(raw_query or q_norm, top_sem, 'content')
                    # keep the old blended score as tiebreaker; store rerank_score
                    for rr, src in zip(reranked, top_sem):
                        rr["final_score_pre_rerank"] = src.get("final_score", 0.0)
                    # optional threshold / cut
                    if params.rerank_threshold is not None and len(reranked) > (params.rerank_top_k or top_n) * 2:
                        reranked = [r for r in reranked if r.get("rerank_score", 0.0) >= params.rerank_threshold]
                    top_sem = reranked[: (params.rerank_top_k or top_n)]
            except Exception:
                logger.error(traceback.format_exc())

        for r in top_sem:
            r.pop("_event_ts", None)

        return top_sem