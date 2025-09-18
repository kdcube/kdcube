# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/retrieval/kb_client.py
import asyncpg, json
from typing import List, Dict, Any, Optional

from kdcube_ai_app.infra.embedding.embedding import convert_embedding_to_string
from kdcube_ai_app.apps.chat.sdk.config import get_settings

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

        if not self._pool:
            self._pool = await asyncpg.create_pool(
                host=self._settings.PGHOST, port=self._settings.PGPORT,
                user=self._settings.PGUSER, password=self._settings.PGPASSWORD, database=self._settings.PGDATABASE,
                ssl=self._settings.PGSSL,
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
