# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# providers/knowledge_base_search.py

from typing import Optional, List, Dict, Any
import json

from kdcube_ai_app.ops.deployment.sql.db_deployment import SYSTEM_SCHEMA, PROJECT_DEFAULT_SCHEMA
from kdcube_ai_app.infra.relational.psql.psql_base import PostgreSqlDbMgr
from kdcube_ai_app.infra.relational.psql.utilities import (
    transactional, to_pgvector_str
)

# Import the data models
from kdcube_ai_app.apps.knowledge_base.db.data_models import (
    EntityItem, HybridSearchParams
)
import kdcube_ai_app.apps.utils.sql_dt_utils as dt_utils

class KnowledgeBaseSearch:
    """
    Search functionality for Knowledge Base with provider and expiration support.
    Includes text search, semantic search, and hybrid search with cache-aware filtering.
    """

    def __init__(self,
                 tenant: str,
                 schema_name: Optional[str] = None,
                 system_schema_name: Optional[str] = None,
                 config=None):
        self.dbmgr = PostgreSqlDbMgr()

        self.tenant = tenant
        if schema_name and not schema_name.startswith(tenant):
            schema_name = f"{tenant}_{schema_name}"
        if schema_name and not schema_name.startswith("kdcube_"):
            schema_name = f"kdcube_{schema_name}"

        self.schema = schema_name or PROJECT_DEFAULT_SCHEMA
        self.system_schema = system_schema_name or SYSTEM_SCHEMA

    # ================================
    # Search Operations
    # ================================

    @transactional
    def hybrid_search(self,
                      query: str,
                      resource_id: Optional[str] = None,
                      provider: Optional[str] = None,  # NEW
                      include_expired: bool = False,  # NEW
                      top_k: int = 5,
                      relevance_threshold: float = 0.0,
                      conn=None) -> List[Dict[str, Any]]:
        """
        Perform hybrid search - updated for new schema (no heading/subheading).
        """
        where_clauses = ["search_vector @@ plainto_tsquery('english', %s)"]
        params = [query, query]  # query used twice for ranking

        if resource_id:
            where_clauses.append("resource_id = %s")
            params.append(resource_id)

        if provider:  # NEW
            where_clauses.append("provider = %s")
            params.append(provider)

        if not include_expired:  # NEW - filter out expired segments
            where_clauses.append("""
                EXISTS (
                    SELECT 1 FROM {schema}.datasource ds 
                    WHERE ds.id = resource_id AND ds.version = version
                    AND (ds.expiration IS NULL OR ds.expiration > now())
                )
            """.format(schema=self.schema))

        where_clause = " AND ".join(where_clauses)

        # Updated SQL - removed heading/subheading
        sql = f"""
        SELECT 
            id as segment_id,
            resource_id,
            version,
            provider,
            content,
            title,
            lineage,
            entities,
            extensions,
            ts_rank(search_vector, plainto_tsquery('english', %s)) as text_score,
            CASE 
                WHEN embedding IS NOT NULL THEN 1.0
                ELSE 0.0 
            END as has_embedding
        FROM {self.schema}.retrieval_segment
        WHERE {where_clause}
        ORDER BY 
            text_score DESC,
            has_embedding DESC,
            created_at DESC
        LIMIT %s
        """

        final_params = params + [top_k]

        with conn.cursor() as cur:
            cur.execute(sql, final_params)
            columns = [desc[0] for desc in cur.description]
            results = []

            for row in cur.fetchall():
                result_dict = dict(zip(columns, row))

                # Fix: Convert decimal.Decimal to float
                text_score = float(result_dict.get("text_score", 0.0) or 0.0)
                has_embedding = float(result_dict.get("has_embedding", 0.0) or 0.0)
                relevance_score = text_score + (0.1 * has_embedding)

                if relevance_score >= relevance_threshold:
                    result_dict["relevance_score"] = relevance_score
                    results.append(result_dict)

            return results

    @transactional
    def advanced_hybrid_search(self,
                               params: HybridSearchParams,
                               conn=None) -> List[Dict[str, Any]]:
        """
        Advanced hybrid search with correct boolean logic:

        - FACETS (always AND): providers, resource_ids, include_expired, entity_filters group
          * The entity_filters group itself ANDs with other facets; inside the group you can
            AND/OR individual entity items (controlled by entities_match_all or match_all).
        - RECALL (combined by match_all): query + tags
          * match_all=True  -> AND
          * match_all=False -> OR

        Scoring:
          text_score     = ts_rank_cd(search_vector, websearch_to_tsquery('english', %s), 33)
          semantic_score = 1 - (embedding <=> %s)  (when embedding present)
          relevance      = norm_tw * text_score + norm_sw * semantic_score
        """
        import json

        # ---------- 1) Per-row scoring fragments ----------
        if params.query:
            # coverage-density rank (33 == 1|32) using websearch_to_tsquery
            text_score_sql = "ts_rank_cd(search_vector, websearch_to_tsquery('english', %s), 33)"
        else:
            text_score_sql = "0.0"

        if params.embedding:
            embedding_str = to_pgvector_str(params.embedding)
            semantic_score_sql = (
                "CASE WHEN embedding IS NOT NULL "
                "THEN (1.0 - (embedding <=> %s)) ELSE 0.0 END"
            )
        else:
            semantic_score_sql = "0.0"

        # ---------- 2) Normalize weights ----------
        tw = params.text_weight or 0.0
        sw = params.semantic_weight or 0.0
        total = tw + sw
        if total > 0:
            norm_tw = tw / total
            norm_sw = sw / total
        else:
            norm_tw = norm_sw = 0.5

        # ---------- 3) Build FACETS (ALWAYS AND) ----------
        facet_clauses: list[str] = []
        facet_params: list[Any] = []

        # resource_ids facet
        if params.resource_ids:
            ph = ",".join(["%s"] * len(params.resource_ids))
            facet_clauses.append(f"resource_id IN ({ph})")
            facet_params.extend(params.resource_ids)

        # providers facet (supports multiple)
        providers = getattr(params, "providers", None)
        if providers:
            ph = ",".join(["%s"] * len(providers))
            facet_clauses.append(f"provider IN ({ph})")
            facet_params.extend(providers)

        # include_expired facet (joins datasource)
        if not getattr(params, "include_expired", True):
            facet_clauses.append(f"""
                EXISTS (
                    SELECT 1 FROM {self.schema}.datasource ds
                    WHERE ds.id = resource_id
                      AND ds.version = version
                      AND (ds.expiration IS NULL OR ds.expiration > now())
                )
            """)

        # entity_filters facet group (internal AND/OR, group ANDed with other facets)
        entity_filters = getattr(params, "entity_filters", None)
        if entity_filters:
            entities_match_all = getattr(params, "entities_match_all", params.match_all)
            ent_parts = []
            for ent in entity_filters:
                ent_parts.append("entities @> %s")
                facet_params.append(json.dumps([{"key": ent.key, "value": ent.value}], ensure_ascii=False))
            joiner = " AND " if entities_match_all else " OR "
            facet_clauses.append("(" + joiner.join(ent_parts) + ")")

        facet_sql = " AND ".join(facet_clauses) if facet_clauses else ""

        # ---------- 4) Build RECALL (query + tags) by match_all ----------
        recall_clauses: list[str] = []
        recall_params: list[Any] = []

        if params.query:
            # match search_vector with the same websearch_to_tsquery used for ranking
            recall_clauses.append("search_vector @@ websearch_to_tsquery('english', %s)")
            recall_params.append(params.query)

        if params.tags:
            # match_all=True -> require all tags; else any overlap
            if params.match_all:
                recall_clauses.append("tags @> %s")
            else:
                recall_clauses.append("tags && %s")
            recall_params.append(params.tags)

        if recall_clauses:
            recall_joiner = " AND " if params.match_all else " OR "
            recall_sql = "(" + recall_joiner.join(recall_clauses) + ")"
        else:
            recall_sql = ""

        # ---------- 5) Combine WHERE (FACETS AND (RECALL)) ----------
        where_parts = []
        if facet_sql:
            where_parts.append(f"({facet_sql})")
        if recall_sql:
            where_parts.append(recall_sql)

        where_clause = " AND ".join(where_parts) if where_parts else "TRUE"

        # ---------- 6) Full SQL with CTE scoring ----------
        sql = f"""
        WITH scored_segments AS (
          SELECT
            id             AS segment_id,
            resource_id,
            version,
            provider,
            content,
            title,
            lineage,
            entities,
            extensions,
            tags,
            created_at,
            {text_score_sql}     AS text_score,
            {semantic_score_sql} AS semantic_score,
            (
              ({text_score_sql}) * %s
              +
              ({semantic_score_sql}) * %s
            )                   AS relevance_score,
            CASE WHEN embedding IS NOT NULL THEN 1.0 ELSE 0.0 END AS has_embedding
          FROM {self.schema}.retrieval_segment
          WHERE {where_clause}
        )
        SELECT *
        FROM scored_segments
        WHERE relevance_score >= %s
        ORDER BY
          relevance_score DESC,
          has_embedding   DESC,
          created_at      DESC
        LIMIT %s
        """

        # ---------- 7) Assemble parameters in exact placeholder order ----------
        final_params: list[Any] = []

        # text_score alias
        if params.query:
            final_params.append(params.query)  # for text_score (websearch_to_tsquery)

        # semantic_score alias
        if params.embedding:
            final_params.append(embedding_str)  # for semantic_score

        # text_score inside relevance
        if params.query:
            final_params.append(params.query)

        # normalized text weight
        final_params.append(norm_tw)

        # semantic_score inside relevance
        if params.embedding:
            final_params.append(embedding_str)

        # normalized semantic weight
        final_params.append(norm_sw)

        # WHERE params: facets first, then recall (order matches where_clause construction)
        final_params.extend(facet_params)
        final_params.extend(recall_params)

        # threshold and limit
        final_params.append(params.min_similarity or 0.0)
        final_params.append(params.top_n)

        # ---------- 8) Execute and post-process ----------
        with conn.cursor() as cur:
            cur.execute(sql, final_params)
            cols = [desc[0] for desc in cur.description]
            results = []
            for row in cur.fetchall():
                rec = dict(zip(cols, row))
                rec["text_score"]      = float(rec.get("text_score")      or 0.0)
                rec["semantic_score"]  = float(rec.get("semantic_score")  or 0.0)
                rec["relevance_score"] = float(rec.get("relevance_score") or 0.0)
                rec["has_embedding"]   = float(rec.get("has_embedding")   or 0.0)
                results.append(rec)

        return results


    @transactional
    def entity_search(self,
                      entity_filters: List[EntityItem],
                      match_all: bool = False,
                      resource_ids: Optional[List[str]] = None,
                      providers: Optional[List[str]] = None,  # NEW
                      include_expired: bool = False,  # NEW
                      top_k: int = 10,
                      conn=None) -> List[Dict[str, Any]]:
        """
        Pure entity-based search
        """
        if not entity_filters:
            return []

        where_clauses = []
        params = []

        # Build entity matching conditions
        if match_all:
            # All entities must be present (AND condition)
            for entity in entity_filters:
                where_clauses.append("entities @> %s")
                params.append(json.dumps([{"key": entity.key, "value": entity.value}], ensure_ascii=False))
        else:
            # Any entity can be present (OR condition)
            entity_conditions = []
            for entity in entity_filters:
                entity_conditions.append("entities @> %s")
                params.append(json.dumps([{"key": entity.key, "value": entity.value}], ensure_ascii=False))
            where_clauses.append(f"({' OR '.join(entity_conditions)})")

        # Add resource filter if specified
        if resource_ids:
            placeholders = ",".join(["%s"] * len(resource_ids))
            where_clauses.append(f"resource_id IN ({placeholders})")
            params.extend(resource_ids)

        #Add provider filter if specified
        if providers:
            placeholders = ",".join(["%s"] * len(providers))
            where_clauses.append(f"provider IN ({placeholders})")
            params.extend(providers)

        # Add expiration filter
        if not include_expired:
            where_clauses.append(f"""
                EXISTS (
                    SELECT 1 FROM {self.schema}.datasource ds 
                    WHERE ds.id = resource_id AND ds.version = version
                    AND (ds.expiration IS NULL OR ds.expiration > now())
                )
            """)

        where_clause = " AND ".join(where_clauses)

        sql = f"""
        SELECT 
            id as segment_id,
            resource_id,
            version,
            provider,
            content,
            title,
            lineage,
            entities,
            extensions,
            1.0 as relevance_score  -- Perfect match for entity-based search
        FROM {self.schema}.retrieval_segment
        WHERE {where_clause}
        ORDER BY created_at DESC
        LIMIT %s
        """

        params.append(top_k)

        with conn.cursor() as cur:
            cur.execute(sql, params)
            columns = [desc[0] for desc in cur.description]
            results = []

            for row in cur.fetchall():
                result_dict = dict(zip(columns, row))
                results.append(result_dict)

            return results

    @transactional
    def semantic_search_only(self,
                             embedding: List[float],
                             distance_type: str = "cosine",
                             resource_ids: Optional[List[str]] = None,
                             providers: Optional[List[str]] = None,
                             include_expired: bool = False,
                             min_similarity: float = 0.0,
                             top_k: int = 10,
                             conn=None) -> List[Dict[str, Any]]:
        """
        Pure semantic search using vector similarity.

        Args:
            embedding: Query embedding vector
            distance_type: Distance metric ('cosine', 'l2', 'ip')
            resource_ids: Optional resource filter
            min_similarity: Minimum similarity threshold
            top_k: Maximum results

        Returns:
            List of semantically similar segments
        """
        where_clauses = ["embedding IS NOT NULL"]
        params = []

        # Add resource filter
        if resource_ids:
            placeholders = ",".join(["%s"] * len(resource_ids))
            where_clauses.append(f"resource_id IN ({placeholders})")
            params.extend(resource_ids)

        # Add provider filter
        if providers:
            placeholders = ",".join(["%s"] * len(providers))
            where_clauses.append(f"provider IN ({placeholders})")
            params.extend(providers)

        # Add expiration filter
        if not include_expired:
            where_clauses.append(f"""
                EXISTS (
                    SELECT 1 FROM {self.schema}.datasource ds 
                    WHERE ds.id = resource_id AND ds.version = version
                    AND (ds.expiration IS NULL OR ds.expiration > now())
                )
            """)

        # Convert embedding and build similarity expression
        embedding_str = to_pgvector_str(embedding)

        if distance_type == "cosine":
            similarity_sql = f"(1.0 - (embedding <=> %s))"
            order_sql = f"embedding <=> %s"
        elif distance_type == "l2":
            similarity_sql = f"(1.0 / (1.0 + (embedding <-> %s)))"
            order_sql = f"embedding <-> %s"
        else:  # inner product
            similarity_sql = f"(embedding <#> %s)"
            order_sql = f"embedding <#> %s DESC"

        where_clause = " AND ".join(where_clauses)

        sql = f"""
        SELECT 
            id as segment_id,
            resource_id,
            version,
            provider,
            content,
            title,
            lineage,
            entities,
            extensions,
            {similarity_sql} as relevance_score,
            (embedding <=> %s) as distance
        FROM {self.schema}.retrieval_segment
        WHERE {where_clause}
        AND {similarity_sql} >= %s
        ORDER BY {order_sql}
        LIMIT %s
        """

        # Parameters: embedding for similarity calc, resource/provider filters, embedding for distance, similarity threshold, embedding for ordering, limit
        final_params = [embedding_str] + params + [embedding_str, embedding_str, min_similarity, embedding_str, top_k]

        with conn.cursor() as cur:
            cur.execute(sql, final_params)
            columns = [desc[0] for desc in cur.description]
            results = []

            for row in cur.fetchall():
                result_dict = dict(zip(columns, row))

                # Convert decimal types to float
                result_dict["relevance_score"] = float(result_dict.get("relevance_score", 0.0) or 0.0)
                result_dict["distance"] = float(result_dict.get("distance", float('inf')) or float('inf'))

                results.append(result_dict)

            return results

    # Convenience method that maps to the appropriate search
    def search(self, params: HybridSearchParams) -> List[Dict[str, Any]]:
        """
        Main search entry point that routes to appropriate search method.

        Args:
            params: Search parameters

        Returns:
            Search results
        """
        # Check for provider-specific searches
        provider = getattr(params, 'provider', None)
        providers = getattr(params, 'providers', None)

        # If only text search
        if params.query and not params.embedding:
            # Use simple hybrid_search for text-only
            return self.hybrid_search(
                query=params.query,
                resource_id=params.resource_ids[0] if params.resource_ids and len(params.resource_ids) == 1 else None,
                provider=provider,
                include_expired=getattr(params, 'include_expired', False),
                top_k=params.top_n,
                relevance_threshold=params.min_similarity or 0.0
            )

        # If only semantic search
        elif params.embedding and not params.query:
            return self.semantic_search_only(
                embedding=params.embedding,
                distance_type=params.distance_type,
                resource_ids=params.resource_ids,
                providers=providers or ([provider] if provider else None),
                include_expired=getattr(params, 'include_expired', False),
                min_similarity=params.min_similarity or 0.0,
                top_k=params.top_n
            )

        # If entity search only
        elif params.entity_filters and not params.query and not params.embedding:
            return self.entity_search(
                entity_filters=params.entity_filters,
                resource_ids=params.resource_ids,
                providers=providers or ([provider] if provider else None),
                include_expired=getattr(params, 'include_expired', False),
                top_k=params.top_n
            )

        # Full hybrid search with all parameters
        else:
            return self.advanced_hybrid_search(params)

    def hybrid_pipeline_search_(self, params: HybridSearchParams) -> List[Dict[str, Any]]:
        """
        Two-stage retrieval with correct boolean logic:
          - Facets (providers, resource_ids, include_expired, entity_filters group) always AND
          - Recall signals (query + tags) combine via match_all (AND vs OR)
          - ANN fallback is constrained by facets (not by recall)
        Steps
          1) BM25 high-recall filtering using prefix-aware tsquery
          2) ANN k-NN fallback for recall
          3) Semantic scoring on combined candidate IDs
          4) Optional cross-encoder rerank for final ranking

        This version uses prefix queries (to_tsquery with :*) to catch word variants like "mitigator".
        """
        import unicodedata, re, json

        # -------- helpers --------
        def normalize_query(q: str) -> str:
            # decompose accents/diacritics, strip non-alphanumeric, lowercase
            nfkd = unicodedata.normalize('NFKD', q or "")
            cleaned = re.sub(r'[^0-9A-Za-z\s]', ' ', nfkd)
            return cleaned.lower().strip()

        def build_entity_group(entity_filters, use_and: bool, param_sink: list) -> str:
            """
            Build an (entities @> %s [AND/OR ...]) group.
            The group itself is a facet and will be ANDed with other facets.
            """
            if not entity_filters:
                return ""
            parts = []
            for ent in entity_filters:
                parts.append("entities @> %s")
                # [{"key":"...", "value":"..."}]
                param_sink.append(json.dumps([{"key": ent.key, "value": ent.value}], ensure_ascii=False))
            joiner = " AND " if use_and else " OR "
            return "(" + joiner.join(parts) + ")"

        # -------- Stage 0: prepare query tokens --------
        # Normalize and tokenize user query
        raw_query = params.query or ""
        q_norm = normalize_query(raw_query)
        # Build prefix tsquery: use stems >3 chars with wildcard
        terms = [t for t in q_norm.split() if len(t) > 3]
        # Prefix tsquery to catch stems (leverages idx_<SCHEMA>_rs_search_vector)
        prefix_tsquery = ' & '.join(f"{t}:*" for t in terms) if terms else ""

        # -------- FACETS (ALWAYS AND) --------
        facet_clauses, facet_params = [], []

        # --------------------------
        # Stage 1: BM25 filtering with prefix-aware to_tsquery
        # --------------------------
        # resource_ids facet (idx_<SCHEMA>_rs_resource / idx_<SCHEMA>_rs_provider_resource)
        if params.resource_ids:
            ph = ",".join(["%s"] * len(params.resource_ids))
            facet_clauses.append(f"resource_id IN ({ph})")
            facet_params.extend(params.resource_ids)

        # providers facet (idx_<SCHEMA>_rs_provider / _provider_created)
        if getattr(params, "providers", None):
            ph = ",".join(["%s"] * len(params.providers))
            facet_clauses.append(f"provider IN ({ph})")
            facet_params.extend(params.providers)

        # include_expired facet via EXISTS on <SCHEMA>.datasource (idx_<SCHEMA>_ds_id_version, _ds_expiration)
        if not getattr(params, "include_expired", True):
            facet_clauses.append(f"""
                EXISTS (
                    SELECT 1
                    FROM {self.schema}.datasource ds
                    WHERE ds.id = resource_id
                      AND ds.version = version
                      AND (ds.expiration IS NULL OR ds.expiration > now())
                )
            """)

        # entity_filters group (GIN on entities jsonb_ops); internal AND/OR, group ANDed with other facets
        entities_match_all = getattr(params, "entities_match_all", params.match_all)
        ent_group = build_entity_group(getattr(params, "entity_filters", None),
                                       bool(entities_match_all), facet_params)
        if ent_group:
            facet_clauses.append(ent_group)

        facet_sql = " AND ".join(facet_clauses) if facet_clauses else ""

        # -------- RECALL (query + tags) by match_all --------
        recall_clauses, recall_params = [], []

        # query → search_vector @@ to_tsquery('english', ...)
        if prefix_tsquery:
            recall_clauses.append("search_vector @@ to_tsquery('english', %s)")
            recall_params.append(prefix_tsquery)

        # tags (GIN on text[]):
        # match_all=True  -> tags @> ARRAY[...]
        # match_all=False -> tags && ARRAY[...]
        if getattr(params, "tags", None):
            if params.match_all:
                recall_clauses.append("tags @> %s")
            else:
                recall_clauses.append("tags && %s")
            recall_params.append(params.tags)

        if recall_clauses:
            recall_joiner = " AND " if params.match_all else " OR "
            recall_sql = "(" + recall_joiner.join(recall_clauses) + ")"
        else:
            recall_sql = ""

        # -------- Stage 1: BM25 / prefix tsquery filter --------
        where_parts = []
        if facet_sql:
            where_parts.append(f"({facet_sql})")
        if recall_sql:
            where_parts.append(recall_sql)

        bm25_where = " AND ".join(where_parts) if where_parts else "TRUE"

        # ORDER BY: use ts_rank_cd if we have a tsquery; else fallback to recency
        if prefix_tsquery:
            bm25_order = "ts_rank_cd(search_vector, to_tsquery('english', %s), 32) DESC"
            order_params = [prefix_tsquery]
        else:
            bm25_order = "created_at DESC"
            order_params = []

        bm25_k = getattr(params, "bm25_k", 100)
        bm25_sql = f"""
            SELECT id AS segment_id
            FROM {self.schema}.retrieval_segment
            WHERE {bm25_where}
            ORDER BY {bm25_order}
            LIMIT %s
        """
        bm25_params = tuple(facet_params + recall_params + order_params + [bm25_k])
        bm25_rows = self.dbmgr.execute_sql(bm25_sql, data=bm25_params, as_dict=True)
        bm25_ids = [r['segment_id'] for r in bm25_rows]

        # -------- Stage 2: ANN fallback (constrained by FACETS) --------
        if params.embedding is None:
            return []

        embedding_str = to_pgvector_str(params.embedding)
        fallback_k = getattr(params, 'fallback_k', params.top_n)

        ann_where_parts = ["embedding IS NOT NULL"]
        ann_params = []

        if facet_sql:
            ann_where_parts.append(f"({facet_sql})")
            ann_params.extend(facet_params)

        ann_where = " AND ".join(ann_where_parts)
        ann_sql = f"""
            SELECT id AS segment_id
            FROM {self.schema}.retrieval_segment
            WHERE {ann_where}
            ORDER BY embedding <=> %s
            LIMIT %s
        """
        ann_params = tuple(ann_params + [embedding_str, fallback_k])
        ann_rows = self.dbmgr.execute_sql(ann_sql, data=ann_params, as_dict=True)
        ann_ids = [r['segment_id'] for r in ann_rows]

        # -------- Stage 3: semantic scoring on union of candidates --------
        candidate_ids = list(dict.fromkeys(bm25_ids + ann_ids))
        if not candidate_ids:
            return []

        semantic_sql = f"""
            SELECT
              id            AS segment_id,
              resource_id,
              version,
              provider,
              content,
              title,
              lineage,
              entities,
              extensions,
              tags,
              created_at,
              (1.0 - (embedding <=> %s)) AS semantic_score,
              CASE WHEN embedding IS NOT NULL THEN 1.0 ELSE 0.0 END AS has_embedding
            FROM {self.schema}.retrieval_segment
            WHERE id = ANY(%s)
        """
        sem_rows = self.dbmgr.execute_sql(
            semantic_sql,
            data=(embedding_str, candidate_ids),
            as_dict=True
        )

        thresh = params.min_similarity or 0.0
        filtered = [r for r in sem_rows if float(r.get('semantic_score', 0.0)) >= thresh]
        filtered.sort(key=lambda r: (r['semantic_score'], r['has_embedding'], r['created_at']), reverse=True)
        top_sem = filtered[: params.top_n]
        if not top_sem:
            return []

        # -------- Stage 4: optional cross-encoder rerank --------
        from kdcube_ai_app.infra.rerank.rerank import cross_encoder_rerank
        reranked = cross_encoder_rerank(raw_query or q_norm, top_sem, 'content')

        if params.rerank_threshold is not None and len(reranked) > (params.rerank_top_k or params.top_n) * 2:
            reranked = [r for r in reranked if r.get("rerank_score", 0.0) >= params.rerank_threshold]

        return reranked[: (params.rerank_top_k or params.top_n)]

    def hybrid_pipeline_search(self, params: HybridSearchParams) -> List[Dict[str, Any]]:
        """
        Two-stage retrieval (BM25 + ANN), then semantic scoring and optional rerank.
        NO early joins: Stage 1 & 2 hit retrieval_segment only.
        Stage 3 joins datasource ONLY for the candidate set (id ANY (...)) and applies provider/expiry facets there.
        """
        import unicodedata, re, json

        # -------- helpers --------
        def normalize_query(q: str) -> str:
            nfkd = unicodedata.normalize('NFKD', q or "")
            cleaned = re.sub(r'[^0-9A-Za-z\s]', ' ', nfkd)
            return cleaned.lower().strip()

        def build_entity_group(entity_filters, use_and: bool, param_sink: list) -> str:
            if not entity_filters:
                return ""
            parts = []
            for ent in entity_filters:
                parts.append("entities @> %s")
                param_sink.append(json.dumps([{"key": ent.key, "value": ent.value}], ensure_ascii=False))
            joiner = " AND " if use_and else " OR "
            return "(" + joiner.join(parts) + ")"

        rs = f"{self.schema}.retrieval_segment"     # base table only (no join)
        ds = f"{self.schema}.datasource"

        # -------- Stage 0: tokens --------
        raw_query = params.query or ""
        q_norm = normalize_query(raw_query)
        terms = [t for t in q_norm.split() if len(t) > 3]
        prefix_tsquery = ' & '.join(f"{t}:*" for t in terms) if terms else ""

        # -------- FACETS used in Stage 1/2 (join-free) --------
        # Keep only facets that live on retrieval_segment to avoid join:
        rs_facet_clauses, rs_facet_params = [], []

        # resource_ids (on rs)
        if params.resource_ids:
            ph = ",".join(["%s"] * len(params.resource_ids))
            rs_facet_clauses.append(f"resource_id IN ({ph})")
            rs_facet_params.extend(params.resource_ids)

        # entities (on rs)
        entities_match_all = getattr(params, "entities_match_all", params.match_all)
        ent_group = build_entity_group(getattr(params, "entity_filters", None),
                                       bool(entities_match_all), rs_facet_params)
        if ent_group:
            rs_facet_clauses.append(ent_group)

        # tags (on rs) — but they’re part of recall below to match your logic
        # provider + include_expired will be applied LATER in Stage 3 (post-candidates)

        rs_facets_sql = " AND ".join(rs_facet_clauses) if rs_facet_clauses else ""

        # -------- RECALL (query + tags) by match_all --------
        recall_clauses, recall_params = [], []

        if prefix_tsquery:
            recall_clauses.append("search_vector @@ to_tsquery('english', %s)")
            recall_params.append(prefix_tsquery)

        if getattr(params, "tags", None):
            if params.match_all:
                recall_clauses.append("tags @> %s")
            else:
                recall_clauses.append("tags && %s")
            recall_params.append(params.tags)

        if recall_clauses:
            recall_joiner = " AND " if params.match_all else " OR "
            recall_sql = "(" + recall_joiner.join(recall_clauses) + ")"
        else:
            recall_sql = ""

        # -------- Stage 1: BM25/prefix (rs only) --------
        where_parts = []
        if rs_facets_sql:
            where_parts.append(f"({rs_facets_sql})")
        if recall_sql:
            where_parts.append(recall_sql)
        bm25_where = " AND ".join(where_parts) if where_parts else "TRUE"

        if prefix_tsquery:
            bm25_order = "ts_rank_cd(search_vector, to_tsquery('english', %s), 32) DESC"
            order_params = [prefix_tsquery]
        else:
            bm25_order = "created_at DESC"
            order_params = []

        bm25_k = getattr(params, "bm25_k", 100)
        bm25_sql = f"""
            SELECT id AS segment_id
            FROM {rs}
            WHERE {bm25_where}
            ORDER BY {bm25_order}
            LIMIT %s
        """
        bm25_params = tuple(rs_facet_params + recall_params + order_params + [bm25_k])
        bm25_rows = self.dbmgr.execute_sql(bm25_sql, data=bm25_params, as_dict=True)
        bm25_ids = [r['segment_id'] for r in bm25_rows]

        # -------- Stage 2: ANN fallback (rs only) --------
        if params.embedding is None:
            return []

        embedding_str = to_pgvector_str(params.embedding)
        fallback_k = getattr(params, 'fallback_k', params.top_n)

        ann_where_parts, ann_params = ["embedding IS NOT NULL"], []
        if rs_facets_sql:
            ann_where_parts.append(f"({rs_facets_sql})")
            ann_params.extend(rs_facet_params)
        ann_where = " AND ".join(ann_where_parts)

        ann_sql = f"""
            SELECT id AS segment_id
            FROM {rs}
            WHERE {ann_where}
            ORDER BY embedding <=> %s
            LIMIT %s
        """
        ann_params = tuple(ann_params + [embedding_str, fallback_k])
        ann_rows = self.dbmgr.execute_sql(ann_sql, data=ann_params, as_dict=True)
        ann_ids = [r['segment_id'] for r in ann_rows]

        # -------- Stage 3: semantic scoring on union + JOIN datasource (POST-filter) --------
        candidate_ids = list(dict.fromkeys(bm25_ids + ann_ids))
        if not candidate_ids:
            return []

        # Build POST-filters on ds (provider/expiry/etc.) AFTER candidates are known.
        ds_filters, ds_params = [], []

        # ---------- provider facet
        if getattr(params, "providers", None):
            ph = ",".join(["%s"] * len(params.providers))
            ds_filters.append(f"ds.provider IN ({ph})")
            ds_params.extend(params.providers)

        # ---------- expiry facet
        if not getattr(params, "include_expired", True):
            ds_filters.append("(ds.expiration IS NULL OR ds.expiration > now())")

        # ---------- publication time filters ----------
        # JSON path: ds.metadata->'metadata'->>'published_time_iso'
        # pub_expr = "(ds.metadata->'metadata'->>'published_time_iso')::timestamptz"
        pub_expr = "ds.published_at"
        mod_expr = "ds.modified_at"

        pub_clauses, pub_vals = dt_utils.build_temporal_filters(
            col_expr=pub_expr, mode="timestamptz",
            on=params.published_on, after=params.published_after, before=params.published_before
        )

        # if getattr(params, "published_on", None):
        #     # Inclusive day range: [00:00, 23:59:59]
        #     ds_filters.append(f"{pub_expr}::date = %s::date")
        #     ds_params.append(str(params.published_on))
        #
        # if getattr(params, "published_after", None):
        #     ds_filters.append(f"{pub_expr} >= %s")
        #     ds_params.append(str(params.published_after))
        #
        # if getattr(params, "published_before", None):
        #     ds_filters.append(f"{pub_expr} <= %s")
        #     ds_params.append(str(params.published_before))

        # ---------- modified time filters ----------
        # mod_expr = "(ds.metadata->'metadata'->>'modified_time_iso')::timestamptz"

        mod_clauses, mod_vals = dt_utils.build_temporal_filters(
            col_expr=mod_expr, mode="timestamptz",
            on=params.modified_on,  after=params.modified_after,  before=params.modified_before
        )

        if pub_clauses:
            ds_filters.extend(pub_clauses)
            ds_params.extend(pub_vals)

        if mod_clauses:
            ds_filters.extend(mod_clauses)
            ds_params.extend(mod_vals)
        # if getattr(params, "modified_on", None):
        #     ds_filters.append(f"{mod_expr}::date = %s::date")
        #     ds_params.append(str(params.modified_on))
        #
        # if getattr(params, "modified_after", None):
        #     ds_filters.append(f"{mod_expr} >= %s")
        #     ds_params.append(str(params.modified_after))
        #
        # if getattr(params, "modified_before", None):
        #     ds_filters.append(f"{mod_expr} <= %s")
        #     ds_params.append(str(params.modified_before))


        ds_filter_sql = (" AND " + " AND ".join(ds_filters)) if ds_filters else ""

        semantic_sql = f"""
            SELECT
              rs.id            AS segment_id,
              rs.resource_id,
              rs.version,
              rs.provider,            -- rs.provider (kept for compatibility)
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
              (1.0 - (rs.embedding <=> %s)) AS semantic_score,
              CASE WHEN rs.embedding IS NOT NULL THEN 1.0 ELSE 0.0 END AS has_embedding,
              -- server-side datasource JSON (joined only on candidate set)
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
--                 'published_time_iso', ds.metadata->'metadata'->>'published_time_iso',
--                 'modified_time_iso',  ds.metadata->'metadata'->>'modified_time_iso'
                'published_at', to_char((ds.published_at AT TIME ZONE 'UTC'), 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                'modified_at',  to_char((ds.modified_at  AT TIME ZONE 'UTC'), 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                'event_ts',     to_char((ds.event_ts     AT TIME ZONE 'UTC'), 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
              ) AS datasource
            FROM {rs} rs
            JOIN {ds} ds
              ON ds.id = rs.resource_id
             AND ds.version = rs.version
            WHERE rs.id = ANY(%s)
            {ds_filter_sql}
        """
        sem_rows = self.dbmgr.execute_sql(
            semantic_sql,
            data=(embedding_str, candidate_ids, *ds_params),
            as_dict=True
        )

        # for r in sem_rows:
        #     r['published_ts'] = ((r['datasource'].get('metadata') or {}).get('metadata') or {}).get('published_time_iso')
        # Sorting by event_ts (fallback created_at)
        # def as_ts(v):  # robust None→min
        #     from datetime import datetime
        #     try:
        #         return datetime.fromisoformat(v.replace('Z','+00:00')) if v else datetime.min
        #     except Exception:
        #         return datetime.min
        def as_ts_safe(v):
            from datetime import datetime, timezone
            if not v:
                return datetime.min.replace(tzinfo=timezone.utc)
            if hasattr(v, "tzinfo"):
                return v
            try:
                # handle strings from jsonb_build_object (if any)
                return datetime.fromisoformat(str(v).replace('Z', '+00:00'))
            except Exception:
                return datetime.min.replace(tzinfo=timezone.utc)

        thresh = params.min_similarity or 0.0
        filtered = [r for r in sem_rows if float(r.get('semantic_score', 0.0)) >= thresh]
        # filtered.sort(key=lambda r: (r['semantic_score'], r['has_embedding'], as_ts(r.get('published_ts'))), reverse=True)

        filtered.sort(
            key=lambda r: (
                float(r.get('semantic_score', 0.0)),
                float(r.get('has_embedding', 0.0)),
                as_ts_safe(r.get('event_ts')) if r.get('event_ts') else as_ts_safe(r.get('created_at'))
            ),
            reverse=True
        )
        top_sem = filtered[: params.top_n]
        if not top_sem:
            return []

        # -------- Stage 4: optional cross-encoder rerank --------
        from kdcube_ai_app.infra.rerank.rerank import cross_encoder_rerank
        reranked = cross_encoder_rerank(raw_query or q_norm, top_sem, 'content')

        if params.rerank_threshold is not None and len(reranked) > (params.rerank_top_k or params.top_n) * 2:
            reranked = [r for r in reranked if r.get("rerank_score", 0.0) >= params.rerank_threshold]

        return reranked[: (params.rerank_top_k or params.top_n)]

