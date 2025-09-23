# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/context/vector/conv_index.py

from __future__ import annotations
import json
import asyncpg
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Sequence, Union, Callable, Iterable


from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.infra.embedding.embedding import convert_embedding_to_string

def _coerce_ts(ts: Union[str, datetime]) -> datetime:
    """Ensure ts is a timezone-aware datetime."""
    if isinstance(ts, datetime):
        return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, str):
        # Handle ISO8601 with 'Z' or offset
        s = ts.replace("Z", "+00:00") if ts.endswith("Z") else ts
        return datetime.fromisoformat(s)  # raises if invalid; that’s good
    raise TypeError("ts must be a datetime or ISO8601 string")

def _normalize_ts_for_sql(ts: Union[str, datetime]) -> str:
    dt = _coerce_ts(ts)  # already tz-aware
    return dt.isoformat()

def _safe_json_loads(value: Any) -> Any:
    """Safely deserialize JSON value, returning original if not JSON string."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value
    return value

class ConvIndex:
    def __init__(self,
                 pool: Optional[asyncpg.Pool] = None):
        self._pool: Optional[asyncpg.Pool] = pool
        self._settings = get_settings()

        tenant = self._settings.TENANT.replace("-", "_").replace(" ", "_")
        project = self._settings.PROJECT.replace("-", "_").replace(" ", "_")

        schema_name = f"{tenant}_{project}"
        if schema_name and not schema_name.startswith("kdcube_"):
            schema_name = f"kdcube_{schema_name}"

        self.schema: str = schema_name

    async def init(self):
        if not self._pool:
            self._pool = await asyncpg.create_pool(
                host=self._settings.PGHOST,
                port=self._settings.PGPORT,
                user=self._settings.PGUSER,
                password=self._settings.PGPASSWORD,
                database=self._settings.PGDATABASE,
                ssl=self._settings.PGSSL,
            )

    async def close(self):
        if self._pool:
            await self._pool.close()

    async def ensure_schema(self):
        sql_raw = (await self._read_sql()).decode()
        sql = sql_raw.replace("<SCHEMA>", self.schema)
        # Execute statements defensively (simple splitter; keep statements single-purpose)
        async with self._pool.acquire() as con:
            for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
                await con.execute(stmt)

    async def _read_sql(self) -> bytes:
        import pkgutil

        # Try both names so packaging can pick either
        data = pkgutil.get_data(__package__, "conversation_history.sql")
        if data:
            return data
        data = pkgutil.get_data(__package__, "deploy-conversation-history.sql")
        if data:
            return data
        raise FileNotFoundError("conversation_history.sql / deploy-conversation-history.sql not found in package")

    async def add_message(
            self,
            *,
            user_id: str,
            conversation_id: str,
            role: str,
            text: str,
            s3_uri: str,
            ts: Union[str, datetime],
            tags: Optional[List[str]] = None,
            ttl_days: int = 365,
            user_type: str = "anonymous",
            embedding: Optional[List[float]] = None,
            message_id: Optional[str] = None,
            track_id: Optional[str] = None,  # NEW
    ) -> int:
        ts_dt = _coerce_ts(ts)
        async with self._pool.acquire() as con:
            q = f"""
                INSERT INTO {self.schema}.conv_messages
                    (user_id, conversation_id, message_id, role, text, s3_uri, ts, ttl_days, user_type, tags, embedding, track_id)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::vector,$12)
                RETURNING id
            """
            rec = await con.fetchrow(
                q,
                user_id,
                conversation_id,
                message_id,
                role,
                text,
                s3_uri,
                ts_dt,
                int(ttl_days),
                user_type,
                (tags or []),
                convert_embedding_to_string(embedding) if embedding else None,
                track_id,
            )
            return int(rec["id"])

    async def search_recent(
            self,
            *,
            user_id: str,
            conversation_id: str,
            query_embedding: List[float],
            top_k: int = 8,
            days: int = 90,
            roles: tuple[str, ...] = ("user", "assistant", "artifact"),
            track_id: Optional[str] = None,  # NEW
    ) -> List[Dict[str, Any]]:
        args = [user_id, conversation_id, list(roles), str(days), convert_embedding_to_string(query_embedding)]
        where = [
            "user_id = $1",
            "conversation_id = $2",
            "role = ANY($3)",
            "ts >= now() - ($4::text || ' days')::interval",
            "ts + (ttl_days || ' days')::interval >= now()",
            "embedding IS NOT NULL",
        ]
        if track_id:
            args.append(track_id)
            where.append(f"track_id = ${len(args)}")

        q = f"""
            SELECT id, message_id, role, text, s3_uri, ts, tags, track_id,
                   1 - (embedding <=> $5::vector) AS score
            FROM {self.schema}.conv_messages
            WHERE {' AND '.join(where)}
            ORDER BY embedding <=> $5::vector
            LIMIT {int(top_k)}
        """
        async with self._pool.acquire() as con:
            rows = await con.fetch(q, *args)
        return [dict(r) for r in rows]

    async def search_recent_with_tags(
            self,
            *,
            user_id: str,
            conversation_id: str,
            query_embedding: List[float],
            top_k: int = 8,
            days: int = 90,
            roles: tuple[str, ...] = ("user", "assistant", "artifact"),
            any_tags: Optional[List[str]] = None,
            all_tags: Optional[List[str]] = None,
            track_id: Optional[str] = None,  # NEW
    ) -> List[Dict[str, Any]]:
        """
        Optional server-side tag filtering flavor. Most callers should prefer search_recent
        + Python-side filtering to avoid extra scans.
        """
        args: List[Any] = [
            user_id,  # $1
            conversation_id,  # $2
            list(roles),  # $3
            str(days),  # $4
            convert_embedding_to_string(query_embedding),  # $5
        ]
        clauses = [
            "user_id = $1",
            "conversation_id = $2",
            "role = ANY($3)",
            "ts >= now() - ($4::text || ' days')::interval",
            "ts + (ttl_days || ' days')::interval >= now()",  # TTL guard
            "embedding IS NOT NULL",
        ]
        if any_tags:
            args.append(any_tags)
            clauses.append(f"tags && ${len(args)}")
        if all_tags:
            args.append(all_tags)
            clauses.append(f"tags @> ${len(args)}::text[]")
        if track_id:
            args.append(track_id)
            clauses.append(f"track_id = ${len(args)}")

        q = f"""
            SELECT id, message_id, role, text, s3_uri, ts, tags, track_id,
                   1 - (embedding <=> $5::vector) AS score
            FROM {self.schema}.conv_messages
            WHERE {' AND '.join(clauses)}
            ORDER BY embedding <=> $5::vector
            LIMIT {int(top_k)}
        """
        async with self._pool.acquire() as con:
            rows = await con.fetch(q, *args)
        return [dict(r) for r in rows]

    async def search_recent_with_turn_pairs(
            self,
            *,
            user_id: str,
            conversation_id: str,
            query_embedding: List[float],
            top_k: int = 8,
            days: int = 90,
            track_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Returns nearest neighbors, each augmented with the *closest prior user*
        and the *closest following assistant* message within the same conversation.
        Pairing is done on the DB side via LATERAL joins.
        """
        args = [
            user_id,
            conversation_id,
            str(days),
            convert_embedding_to_string(query_embedding),
        ]
        where = [
            "m.user_id = $1",
            "m.conversation_id = $2",
            "m.ts >= now() - ($3::text || ' days')::interval",
            "m.ts + (m.ttl_days || ' days')::interval >= now()",
            "m.embedding IS NOT NULL",
        ]
        if track_id:
            args.append(track_id)
            where.append(f"m.track_id = ${len(args)}")

        q = f"""
        WITH hits AS (
          SELECT
            m.*,
            1 - (m.embedding <=> $4::vector) AS score
          FROM {self.schema}.conv_messages m
          WHERE {' AND '.join(where)}
          ORDER BY m.embedding <=> $4::vector
          LIMIT {int(top_k)}
        )
        SELECT
          h.id AS hit_id, h.role AS hit_role, h.text AS hit_text, h.s3_uri AS hit_s3_uri,
          h.ts AS hit_ts, h.tags AS hit_tags, h.track_id AS hit_track_id, h.score AS hit_score,

          u.id AS user_id_msg, u.text AS user_text, u.s3_uri AS user_s3_uri, u.ts AS user_ts,
          a.id AS assistant_id_msg, a.text AS assistant_text, a.s3_uri AS assistant_s3_uri, a.ts AS assistant_ts

        FROM hits h
        LEFT JOIN LATERAL (
          SELECT u.*
          FROM {self.schema}.conv_messages u
          WHERE u.user_id = h.user_id
            AND u.conversation_id = h.conversation_id
            AND u.role = 'user'
            AND u.ts <= h.ts
          ORDER BY u.ts DESC
          LIMIT 1
        ) u ON TRUE
        LEFT JOIN LATERAL (
          SELECT a.*
          FROM {self.schema}.conv_messages a
          WHERE a.user_id = h.user_id
            AND a.conversation_id = h.conversation_id
            AND a.role = 'assistant'
            AND a.ts >= COALESCE(u.ts, h.ts)
          ORDER BY a.ts ASC
          LIMIT 1
        ) a ON TRUE
        ORDER BY h.score DESC, h.ts DESC
        """
        async with self._pool.acquire() as con:
            rows = await con.fetch(q, *args)
        return [dict(r) for r in rows]

    async def purge_user_type(self, *, user_type: str, older_than_days: Optional[int] = None) -> int:
        """Quick purge by cohort. If older_than_days is None, delete all rows for that user_type."""
        async with self._pool.acquire() as con:
            if older_than_days is None:
                q = f"DELETE FROM {self.schema}.conv_messages WHERE user_type = $1"
                res = await con.execute(q, user_type)
            else:
                q = f"""
                    DELETE FROM {self.schema}.conv_messages
                    WHERE user_type = $1
                      AND ts < now() - ($2::text || ' days')::interval
                """
                res = await con.execute(q, user_type, str(older_than_days))
            return int(res.split()[-1])

    async def purge_expired(self) -> int:
        """TTL-based purge (same criteria as the cleanup job)."""
        async with self._pool.acquire() as con:
            q = f"""
                DELETE FROM {self.schema}.conv_messages
                WHERE ts + (ttl_days || ' days')::interval < now()
            """
            res = await con.execute(q)
            return int(res.split()[-1])

    async def backfill_from_store(
            self,
            *,
            records: List[Dict[str, Any]],
            default_ttl_days: int = 365,
            default_user_type: str = "anonymous",
            embedder: Optional[Callable[[str], List[float]]] = None,
    ) -> int:
        """
        Re-index previously persisted messages. Each record is a JSON dict as written by ConversationStore.
        If 'embedding' is present in the record, it is used; otherwise, if 'embedder' is provided, it is called.
        """
        n = 0
        for rec in records:
            meta = rec.get("meta") or {}
            emb = rec.get("embedding") or meta.get("embedding")
            if emb is None and embedder:
                try:
                    emb = embedder(rec.get("text") or "")
                except Exception:
                    emb = None
            ttl_days = int(meta.get("ttl_days", default_ttl_days))
            user_type = str(meta.get("user_type", default_user_type))
            try:
                await self.add_message(
                    user_id=rec.get("user") or "anonymous",
                    conversation_id=rec.get("conversation_id"),
                    message_id=meta.get("message_id"),
                    role=rec.get("role"),
                    text=rec.get("text") or "",
                    s3_uri=meta.get("s3_uri") or "",
                    ts=rec.get("timestamp") or meta.get("timestamp") or datetime.utcnow(),
                    ttl_days=ttl_days,
                    user_type=user_type,
                    tags=meta.get("tags") or [],
                    embedding=emb,
                )
                n += 1
            except Exception:
                continue
        return n

    async def add_edges_by_id(self, *, from_id: int, to_ids: Iterable[int], policy: str = "none") -> int:
        rows = [(int(from_id), int(t), policy) for t in to_ids if t and int(t) != int(from_id)]
        if not rows:
            return 0
        q = f"INSERT INTO {self.schema}.conv_artifact_edges (from_id, to_id, policy) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING"
        async with self._pool.acquire() as con:
            await con.executemany(q, rows)
        return len(rows)

    async def search_context(
            self,
            *,
            user_id: str,
            conversation_id: Optional[str],
            track_id: Optional[str],
            query_embedding: Optional[List[float]],   # <-- now optional
            top_k: int = 12,
            days: int = 90,
            scope: str = "track",  # 'track' | 'conversation' | 'user'
            roles: tuple[str, ...] = ("user", "assistant", "artifact"),
            any_tags: Optional[Sequence[str]] = None,  # OR
            all_tags: Optional[Sequence[str]] = None,  # AND (tags @> array)
            not_tags: Optional[Sequence[str]] = None,  # NOT (tags && array)
            text_query: Optional[str] = None,  # ILIKE '%q%' (trgm indexed)
            kinds: Optional[Sequence[str]] = None,  # sugar: expands to any_tags += kinds
            half_life_days: float = 7.0,
            include_deps: bool = True,
            sort: str = "hybrid",  # 'hybrid' | 'semantic' | 'recency'
            timestamp_filters: Optional[List[Dict[str, Any]]] = None,   # [{'op': '>=', 'value': iso/datetime}, ...]
    ) -> List[Dict[str, Any]]:
        if kinds:
            any_tags = (list(any_tags or []) + list(kinds))

        # start args without the embedding; add it only if present
        args: List[Any] = [user_id, list(roles), str(days)]
        where = [
            "m.user_id = $1",
            "m.role = ANY($2)",
            "m.ts >= now() - ($3::text || ' days')::interval",
            "m.ts + (m.ttl_days || ' days')::interval >= now()",
        ]

        # semantic similarity: only when an embedding is provided
        if query_embedding is not None:
            args.append(convert_embedding_to_string(query_embedding))
            # index of the just-appended vector param
            sim_sql = f"1 - (m.embedding <=> ${len(args)}::vector) AS sim"
            where.append("m.embedding IS NOT NULL")
        else:
            # no embedding -> neutral sim
            sim_sql = "0.0::float AS sim"

        # scope
        if scope == "track" and track_id:
            args.append(track_id)
            where.append(f"m.track_id = ${len(args)}")
            if conversation_id:
                args.append(conversation_id)
                where.append(f"m.conversation_id = ${len(args)}")
        elif scope == "conversation" and conversation_id:
            args.append(conversation_id)
            where.append(f"m.conversation_id = ${len(args)}")

        # tags
        if any_tags:
            args.append(list(any_tags))
            where.append(f"m.tags && ${len(args)}")
        if all_tags:
            args.append(list(all_tags))
            where.append(f"m.tags @> ${len(args)}::text[]")
        if not_tags:
            args.append(list(not_tags))
            where.append(f"NOT (m.tags && ${len(args)})")

        # text match (optional)
        if text_query:
            args.append(f"%{text_query}%")
            where.append(f"m.text ILIKE ${len(args)}")

        # timestamp filters (push to SQL)
        valid_ops = {"<", "<=", "=", ">=", ">", "<>"}
        for tf in (timestamp_filters or []):
            op = str(tf.get("op", "")).strip()
            if op not in valid_ops:
                continue
            val = _normalize_ts_for_sql(tf.get("value") or datetime.utcnow())
            args.append(val)
            where.append(f"m.ts {op} ${len(args)}::timestamptz")

        # half-life param
        args.append(max(0.1, float(half_life_days)))
        half_life_days_s = f"${len(args)}::float"

        # ordering
        if sort == "semantic" and query_embedding is None:
            # no embedding -> semantic sort meaningless; fall back to recency
            order_by = "m.ts DESC"
        else:
            order_by = {
                "semantic": "sim DESC, m.ts DESC",
                "recency": "m.ts DESC",
                "hybrid": f"(0.70*sim + 0.25*exp(-ln(2) * age_sec / ({half_life_days_s}*24*3600.0)) + 0.05*rboost) DESC, m.ts DESC",
            }.get(sort, "m.ts DESC")

        deps_select, deps_join = "", ""
        if include_deps:
            deps_select = ", COALESCE(d.deps, '[]'::json) AS deps"
            deps_join = f"""
              LEFT JOIN LATERAL (
                SELECT COALESCE(json_agg(
                  json_build_object(
                    'id', cm2.id, 'message_id', cm2.message_id, 'role', cm2.role,
                    'tags', cm2.tags, 's3_uri', cm2.s3_uri, 'ts', cm2.ts,
                    'policy', e.policy,
                    'text_preview', CASE WHEN cm2.text IS NULL THEN NULL ELSE left(cm2.text, 400) END
                  )
                  ORDER BY cm2.ts ASC
                ), '[]'::json) AS deps
                FROM {self.schema}.conv_artifact_edges e
                JOIN {self.schema}.conv_messages cm2 ON cm2.id = e.to_id
                WHERE e.from_id = m.id
              ) d ON TRUE
            """

        q = f"""
          WITH base AS (
            SELECT m.*,
                   {sim_sql},
                   EXTRACT(EPOCH FROM (now() - m.ts)) AS age_sec,
                   CASE m.role WHEN 'artifact' THEN 1.10 WHEN 'assistant' THEN 1.00 ELSE 0.98 END AS rboost
            FROM {self.schema}.conv_messages m
            WHERE {' AND '.join(where)}
          )
          SELECT m.id, m.message_id, m.role, m.text, m.s3_uri, m.ts, m.tags, m.track_id,
                 m.sim, m.age_sec, m.rboost,
                 (0.70*m.sim + 0.25*exp(-ln(2) * m.age_sec / ({half_life_days_s}*24*3600.0)) + 0.05*m.rboost) AS score
                 {deps_select}
          FROM base m
          {deps_join}
          ORDER BY {order_by}
          LIMIT {int(top_k)}
        """

        async with self._pool.acquire() as con:
            rows = await con.fetch(q, *args)
        return [dict(r) for r in rows]

    async def fetch_recent(
            self,
            *,
            user_id: str,
            conversation_id: Optional[str] = None,
            track_id: Optional[str] = None,
            roles: tuple[str, ...] = ("user", "assistant", "artifact"),
            any_tags: Optional[Sequence[str]] = None,
            all_tags: Optional[Sequence[str]] = None,
            not_tags: Optional[Sequence[str]] = None,
            limit: int = 30,
            days: int = 30,
    ) -> List[Dict[str, Any]]:
        args: List[Any] = [user_id, list(roles), str(days)]
        where = [
            "user_id = $1",
            "role = ANY($2)",
            "ts >= now() - ($3::text || ' days')::interval",
            "ts + (ttl_days || ' days')::interval >= now()",
        ]
        if track_id:
            args.append(track_id)
            where.append(f"track_id = ${len(args)}")
            if conversation_id:
                args.append(conversation_id)
                where.append(f"conversation_id = ${len(args)}")
        elif conversation_id:
            args.append(conversation_id)
            where.append(f"conversation_id = ${len(args)}")
        if any_tags:
            args.append(list(any_tags))
            where.append(f"tags && ${len(args)}::text[]")
        if all_tags:
            args.append(list(all_tags))
            where.append(f"tags @> ${len(args)}::text[]")
        if not_tags:
            args.append(list(not_tags))
            where.append(f"NOT (tags && ${len(args)}::text[])")

        q = f"""
          SELECT id, message_id, role, text, s3_uri, ts, tags, track_id
          FROM {self.schema}.conv_messages
          WHERE {' AND '.join(where)}
          ORDER BY ts DESC
          LIMIT {int(limit)}
        """
        async with self._pool.acquire() as con:
            rows = await con.fetch(q, *args)
        return [dict(r) for r in rows]

    async def hybrid_context(
            self,
            *,
            user_id: str,
            conversation_id: str,
            query_embedding: List[float],
            track_id: Optional[str],
            recent_limit: int = 30,  # deterministic
            recent_days: int = 30,
            semantic_top_k: int = 12,  # add older-but-relevant
            semantic_days: int = 365,
            roles: tuple[str, ...] = ("user", "assistant", "artifact"),
            topic_tags: Optional[Sequence[str]] = None,
    ) -> List[Dict[str, Any]]:
        # A) recent window (recency only)
        recent = await self.fetch_recent(
            user_id=user_id,
            conversation_id=conversation_id,
            track_id=track_id,
            roles=roles,
            any_tags=topic_tags,
            limit=recent_limit,
            days=recent_days,
        )
        seen_ids = {r["id"] for r in recent}

        # B) semantic extras (exclude those already present)
        sem = await self.search_context(
            user_id=user_id,
            conversation_id=conversation_id,
            track_id=track_id,
            query_embedding=query_embedding,
            top_k=semantic_top_k,
            days=semantic_days,
            scope=("track" if track_id else "conversation"),
            roles=roles,
            any_tags=topic_tags,
            include_deps=True,
            sort="hybrid",
        )
        sem = [r for r in sem if r["id"] not in seen_ids]

        # Merge: newest-first within recent, then sem by score
        return recent + sem

    async def find_last_deliverable_for_mention(
            self,
            *,
            user_id: str,
            conversation_id: str,
            track_id: Optional[str],
            mention: str,
            mention_emb: Optional[List[float]],
            prefer_kinds: Sequence[str] = ("codegen.out.inline", "codegen.program.presentation", "codegen.out.file"),
            window_limit: int = 40,
    ) -> Optional[Dict[str, Any]]:
        # 1) Look in recent window for deliverables
        recent = await self.fetch_recent(
            user_id=user_id,
            conversation_id=conversation_id,
            track_id=track_id,
            roles=("artifact",),
            any_tags=list(prefer_kinds),
            limit=window_limit,
            days=90,
        )
        # 2) If mention text looks exact-ish, try ILIKE in-window first
        if mention and len(mention) >= 3:
            txt = mention.strip()
            for r in recent:
                if txt.lower() in (r.get("text") or "").lower():
                    return r

        # 3) Otherwise do semantic over deliverable kinds (guard if no embedding)
        if mention_emb:
            sem = await self.search_context(
                user_id=user_id,
                conversation_id=conversation_id,
                track_id=track_id,
                query_embedding=mention_emb,
                top_k=8,
                days=365,
                scope=("track" if track_id else "conversation"),
                roles=("artifact",),
                any_tags=list(prefer_kinds),
                include_deps=True,
                sort="hybrid",
            )
            return sem[0] if sem else (recent[0] if recent else None)
        else:
            return recent[0] if recent else None

    async def fetch_latest_summary(self, *, user_id: str, conversation_id: str, kind: str = "conversation.summary") -> Optional[Dict[str, Any]]:
        q = f"""
          SELECT id, message_id, role, text, s3_uri, ts, tags, track_id
          FROM {self.schema}.conv_messages
          WHERE user_id=$1 AND conversation_id=$2
            AND role='artifact' AND tags @> ARRAY[$3]::text[]
          ORDER BY ts DESC
          LIMIT 1
        """
        async with self._pool.acquire() as con:
            row = await con.fetchrow(q, user_id, conversation_id, f"kind:{kind}")
        return dict(row) if row else None

    async def fetch_last_turn_logs(self, *, user_id: str, conversation_id: str, max_turns: int = 3) -> List[Dict[str, Any]]:
        """
        Returns newest-first up to one log per turn (tagged 'kind:turn.log').
        Avoids SRFs in non-top-level contexts (PG14+).
        """
        q = f"""
        WITH cand AS (
          SELECT id, message_id, role, text, s3_uri, ts, tags, track_id
          FROM {self.schema}.conv_messages
          WHERE user_id=$1 AND conversation_id=$2
            AND role='artifact' AND tags @> ARRAY['kind:turn.log']::text[]
        ),
        tagged AS (
          SELECT
            c.*,
            (
              SELECT substring(tag FROM '^turn:(.+)$')
              FROM unnest(c.tags) AS tag
              WHERE tag LIKE 'turn:%'
              LIMIT 1
            ) AS turn_key
          FROM cand c
        ),
        ranked AS (
          SELECT *,
                 row_number() OVER (PARTITION BY turn_key ORDER BY ts DESC) AS rn
          FROM tagged
          WHERE turn_key IS NOT NULL
        )
        SELECT id, message_id, role, text, s3_uri, ts, tags, track_id
        FROM ranked
        WHERE rn = 1
        ORDER BY ts DESC
        LIMIT {int(max_turns)}
        """
        async with self._pool.acquire() as con:
            rows = await con.fetch(q, user_id, conversation_id)
        # newest-first already enforced in SQL
        rows = [dict(r) for r in rows]
        for r in rows:
            ts = r.get("ts")
            if hasattr(ts, "isoformat"):
                r["ts"] = ts.isoformat()
        return rows

    async def insert_turn_prefs(
            self, *, user_id: str, conversation_id: str, turn_id: str,
            assertions: List[Dict[str, Any]], exceptions: List[Dict[str, Any]]
    ) -> None:
        """
        Insert turn preferences with proper JSON serialization for value fields.
        """
        async with self._pool.acquire() as con:
            async with con.transaction():
                if assertions:
                    # Prepare assertion data with proper JSON serialization
                    assertion_data = []
                    for a in assertions:
                        if not a.get("key"):
                            continue

                        # Serialize the value to JSON string
                        value_json = json.dumps(a.get("value")) if a.get("value") is not None else None

                        assertion_data.append((
                            user_id,
                            conversation_id,
                            turn_id,
                            a.get("key"),
                            value_json,  # Now properly JSON-serialized
                            bool(a.get("desired", True)),
                            a.get("scope") or "conversation",
                            float(a.get("confidence", 0.6)),
                            a.get("reason") or "nl-extracted",
                            a.get("tags") or []
                        ))

                    if assertion_data:
                        await con.executemany(
                            f"""
                            INSERT INTO {self.schema}.conv_prefs
                              (user_id, conversation_id, turn_id, key, value_json, desired, scope, confidence, reason, tags)
                            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                            """,
                            assertion_data
                        )

                if exceptions:
                    # Prepare exception data with proper JSON serialization
                    exception_data = []
                    for e in exceptions:
                        if not e.get("rule_key"):
                            continue

                        # Serialize the value to JSON string
                        value_json = json.dumps(e.get("value")) if e.get("value") is not None else None

                        exception_data.append((
                            user_id,
                            conversation_id,
                            turn_id,
                            e.get("rule_key"),
                            value_json,  # Now properly JSON-serialized
                            e.get("scope") or "conversation",
                            float(e.get("confidence", 0.6)),
                            e.get("reason") or "nl-extracted",
                        ))

                    if exception_data:
                        await con.executemany(
                            f"""
                            INSERT INTO {self.schema}.conv_pref_exceptions
                              (user_id, conversation_id, turn_id, rule_key, value_json, scope, confidence, reason)
                            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                            """,
                            exception_data
                        )

    async def fetch_conversation_prefs(
            self, *, user_id: str, conversation_id: str, days: int = 90, min_conf: float = 0.55
    ) -> Dict[str, Any]:
        """
        Aggregate, newest-first per key, last-write-wins; filter by confidence.
        """
        q1 = f"""
          SELECT key, value_json, desired, confidence, ts
          FROM {self.schema}.conv_prefs
          WHERE user_id=$1 AND conversation_id=$2
            AND ts >= now() - ($3::text || ' days')::interval
            AND confidence >= $4
          ORDER BY ts DESC
        """
        q2 = f"""
          SELECT rule_key, value_json, confidence, ts
          FROM {self.schema}.conv_pref_exceptions
          WHERE user_id=$1 AND conversation_id=$2
            AND ts >= now() - ($3::text || ' days')::interval
            AND confidence >= $4
          ORDER BY ts DESC
        """
        async with self._pool.acquire() as con:
            rows_a = await con.fetch(q1, user_id, conversation_id, str(days), float(min_conf))
            rows_e = await con.fetch(q2, user_id, conversation_id, str(days), float(min_conf))

        seen = set()
        assertions: List[Dict[str, Any]] = []
        for r in rows_a:
            k = r["key"]
            if k in seen:
                continue
            seen.add(k)
            # Deserialize JSON value back to Python object
            row_dict = dict(r)
            row_dict["value"] = _safe_json_loads(row_dict["value_json"])
            assertions.append(row_dict)

        # exceptions: keep newest per rule_key
        seen_e = set()
        exceptions: List[Dict[str, Any]] = []
        for r in rows_e:
            k = r["rule_key"]
            if k in seen_e:
                continue
            seen_e.add(k)
            # Deserialize JSON value back to Python object
            row_dict = dict(r)
            row_dict["value"] = _safe_json_loads(row_dict["value_json"])
            exceptions.append(row_dict)

        return {"assertions": assertions, "exceptions": exceptions}

    async def load_turn_prefs(self, *, user_id: str, conversation_id: str, turn_id: str) -> Dict[str, Any]:
        q1 = f"""SELECT key, value_json, desired, confidence FROM {self.schema}.conv_prefs
                 WHERE user_id=$1 AND conversation_id=$2 AND turn_id=$3 ORDER BY ts ASC"""
        q2 = f"""SELECT rule_key, value_json, confidence FROM {self.schema}.conv_pref_exceptions
                 WHERE user_id=$1 AND conversation_id=$2 AND turn_id=$3 ORDER BY ts ASC"""
        async with self._pool.acquire() as con:
            prefs = await con.fetch(q1, user_id, conversation_id, turn_id)
            excs = await con.fetch(q2, user_id, conversation_id, turn_id)

        # Deserialize JSON values back to Python objects
        assertions = []
        for p in prefs:
            row_dict = dict(p)
            row_dict["value"] = _safe_json_loads(row_dict["value_json"])
            assertions.append(row_dict)

        exceptions = []
        for e in excs:
            row_dict = dict(e)
            row_dict["value"] = _safe_json_loads(row_dict["value_json"])
            exceptions.append(row_dict)

        return {
            "assertions": assertions,
            "exceptions": exceptions,
        }

    async def fetch_recent_topics(
            self, *, user_id: str, conversation_id: str, max_turns: int = 5
    ) -> List[str]:
        rows = await self.fetch_last_turn_logs(
            user_id=user_id, conversation_id=conversation_id, max_turns=max_turns
        )
        seen = set()
        out: List[str] = []
        for r in rows:
            for tag in (r.get("tags") or []):
                if isinstance(tag, str) and tag.startswith("topic:"):
                    t = tag.split(":", 1)[1].strip()
                    if t and t not in seen:
                        seen.add(t)
                        out.append(t)
        return out

    async def fetch_latest_summary_text(self, *, user_id: str, conversation_id: str) -> str:
        q = f"""
        SELECT text FROM {self.schema}.conv_messages
        WHERE user_id=$1 AND conversation_id=$2
          AND role='artifact' AND tags @> ARRAY['artifact:conversation.summary']::text[]
        ORDER BY ts DESC LIMIT 1
        """
        async with self._pool.acquire() as con:
            row = await con.fetchrow(q, user_id, conversation_id)
        return (row["text"] if row else "") or ""

    async def fetch_last_turn_summaries(self, *, user_id: str, conversation_id: str, limit: int = 3) -> List[str]:
        q = f"""
        SELECT text FROM {self.schema}.conv_messages
        WHERE user_id=$1 AND conversation_id=$2
          AND role='artifact' AND tags @> ARRAY['artifact:turn.summary']::text[]
        ORDER BY ts DESC LIMIT {int(limit)}
        """
        async with self._pool.acquire() as con:
            rows = await con.fetch(q, user_id, conversation_id)
        return [r["text"] for r in rows]

    async def update_message(
            self,
            *,
            id: Optional[int] = None,
            message_id: Optional[str] = None,
            text: Optional[str] = None,
            tags: Optional[List[str]] = None,
            s3_uri: Optional[str] = None,
            ts: Optional[Union[str, datetime]] = None,
    ) -> int:
        """
        In-place UPDATE of a single row in conv_messages. Use either (id) or (message_id).
        Only the provided fields are updated. Returns the number of affected rows (0 or 1).

        NOTE: This updates the INDEX record (conv_messages). If you also want to replace
        the stored payload/blob, write a new blob via ConversationStore first,
        then set s3_uri/text here to point this index row at the new content.
        """
        if not id and not message_id:
            raise ValueError("update_message requires either id or message_id")
        sets, args = [], []
        if text is not None:
            sets.append(f"text = ${len(args)+1}")
            args.append(text)
        if tags is not None:
            sets.append(f"tags = ${len(args)+1}")
            args.append(list(tags))
        if s3_uri is not None:
            sets.append(f"s3_uri = ${len(args)+1}")
            args.append(s3_uri)
        if ts is not None:
            sets.append(f"ts = ${len(args)+1}::timestamptz")
            args.append(_normalize_ts_for_sql(ts))
        if not sets:
            return 0  # nothing to do

        where = ""
        if id:
            where = f"id = ${len(args)+1}"
            args.append(int(id))
        else:
            where = f"message_id = ${len(args)+1}"
            args.append(str(message_id))

        q = f"UPDATE {self.schema}.conv_messages SET {', '.join(sets)} WHERE {where}"
        async with self._pool.acquire() as con:
            res = await con.execute(q, *args)
        # res format: 'UPDATE <n>'
        try:
            return int(res.split()[-1])
        except Exception:
            return 0

"""
        if tags_mode == "all":
            where.append(f"tags @> ${len(args)}::text[]")
        elif tags_mode == "exact":
            where.append(f"(tags @> ${len(args)}::text[] AND tags <@ ${len(args)}::text[])")
        else:  # "any"
            where.append(f"tags && ${len(args)}::text[]")
"""