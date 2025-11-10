# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/context/vector/conv_index.py

from __future__ import annotations
import json
import asyncpg
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Sequence, Union, Callable, Iterable, Tuple


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

CONV_STATE_KIND = "artifact:conversation.state"

def _state_tag(state: str) -> str:
    return f"conv.state:{state}"

def _base_state_tags(conversation_id: str) -> list[str]:
    return [CONV_STATE_KIND, "conv.state", f"conv:{conversation_id}"]

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

    async def get_conversation_state_row(
            self, *, user_id: str, conversation_id: str
    ) -> Optional[Dict[str, Any]]:
        q = f"""
          SELECT id, message_id, role, text, s3_uri, ts, tags, track_id, turn_id, bundle_id
          FROM {self.schema}.conv_messages
          WHERE user_id=$1 AND conversation_id=$2
            AND role='artifact'
            AND tags @> ARRAY[$3,$4]::text[]   -- artifact:conversation.state + conv.state
          ORDER BY ts DESC
          LIMIT 1
        """
        async with self._pool.acquire() as con:
            row = await con.fetchrow(q, user_id, conversation_id, CONV_STATE_KIND, "conv.state")
        return dict(row) if row else None

    # conv_index.py (where your try_set_conversation_state_cas lives)
    async def try_set_conversation_state_cas(
            self,
            *,
            user_id: str,
            conversation_id: str,
            new_state: str,             # 'idle' | 'in_progress' | 'error'
            s3_uri: str,
            now_ts: str,
            require_not_in_progress: bool = False,
            last_turn_id: str | None = None,   # <— NEW (to tag the active turn)
    ) -> dict:
        """
        Upsert the conv.state row with optional CAS.
        Returns: {
          "ok": bool,
          "row": {"id": int, "tags": list[str]},
          "state": str | None,
          "current_turn_id": str | None,
        }
        """
        base_tags = _base_state_tags(conversation_id)
        new_state_tag = _state_tag(new_state)
        turn_tag = f"conv.turn:{last_turn_id}" if last_turn_id else None

        def _parse_tags(tags: list[str]) -> tuple[str | None, str | None]:
            state = None
            turn  = None
            for t in tags or []:
                if t.startswith("conv.state:"):
                    state = t.split(":", 1)[1]
                elif t.startswith("conv.turn:"):
                    turn = t.split(":", 1)[1]
            return state, turn

        # 1) Existing row?
        row = await self.get_conversation_state_row(user_id=user_id, conversation_id=conversation_id)
        async with self._pool.acquire() as con:
            if row is None:
                # INSERT
                tags = base_tags + [new_state_tag] + ([turn_tag] if turn_tag else [])
                q_ins = f"""
                  INSERT INTO {self.schema}.conv_messages
                    (user_id, conversation_id, role, text, s3_uri, ts, ttl_days, user_type, tags)
                  VALUES ($1,$2,'artifact','', $3, $4, 3650, 'system', $5)
                  RETURNING id, tags
                """
                rec = await con.fetchrow(q_ins, user_id, conversation_id, s3_uri, _coerce_ts(now_ts), tags)
                st, cur_turn = _parse_tags(rec["tags"])
                return {"ok": True, "row": {"id": rec["id"], "tags": rec["tags"]}, "state": st, "current_turn_id": cur_turn}

            # 2) UPDATE with CAS; replace any old conv.state:* and conv.turn:* with new
            q_upd = f"""
              UPDATE {self.schema}.conv_messages
              SET
                s3_uri = $1,
                ts     = $2,
                tags   = (
                    SELECT ARRAY(
                        SELECT DISTINCT t
                        FROM unnest(tags) AS t
                        WHERE t NOT LIKE 'conv.state:%' AND t NOT LIKE 'conv.turn:%'
                        UNION ALL
                        SELECT unnest($3::text[])
                    )
                )
              WHERE id = $4
              {"AND NOT (tags && ARRAY['conv.state:in_progress']::text[])" if require_not_in_progress else ""}
              RETURNING id, tags
            """
            tags_add = base_tags + [new_state_tag] + ([turn_tag] if turn_tag else [])
            rec = await con.fetchrow(q_upd, s3_uri, _coerce_ts(now_ts), tags_add, int(row["id"]))
            if rec is None:
                # CAS failed
                # Fetch current row to report state/turn to caller
                cur = await self.get_conversation_state_row(user_id=user_id, conversation_id=conversation_id)
                st, cur_turn = _parse_tags((cur or {}).get("tags") if cur else [])
                return {"ok": False, "row": cur, "state": st, "current_turn_id": cur_turn}

            st, cur_turn = _parse_tags(rec["tags"])
            return {"ok": True, "row": {"id": rec["id"], "tags": rec["tags"]}, "state": st, "current_turn_id": cur_turn}

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
            track_id: Optional[str] = None,
            turn_id: Optional[str] = None,
            bundle_id: Optional[str] = None,
    ) -> int:
        ts_dt = _coerce_ts(ts)
        async with self._pool.acquire() as con:
            q = f"""
                INSERT INTO {self.schema}.conv_messages
                  (user_id, conversation_id, message_id, role, text, s3_uri, ts,
                   ttl_days, user_type, tags, embedding, track_id, turn_id, bundle_id)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::vector,$12,$13,$14)
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
                turn_id,
                bundle_id,
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
            track_id: Optional[str] = None,
            bundle_id: Optional[str] = None,
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
        if bundle_id:
            args.append(bundle_id)
            where.append(f"bundle_id = ${len(args)}")

        q = f"""
            SELECT id, message_id, role, text, s3_uri, ts, tags, track_id, turn_id, bundle_id,
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
            track_id: Optional[str] = None,
            bundle_id: Optional[str] = None,
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
            clauses.append(f"tags && ${len(args)}::text[] ")
        if all_tags:
            args.append(all_tags)
            clauses.append(f"tags @> ${len(args)}::text[]")
        if track_id:
            args.append(track_id)
            clauses.append(f"track_id = ${len(args)}")
        if bundle_id:
            args.append(bundle_id)
            clauses.append(f"bundle_id = ${len(args)}")

        q = f"""
            SELECT id, message_id, role, text, s3_uri, ts, tags, track_id, turn_id, bundle_id,
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
          h.ts AS hit_ts, h.tags AS hit_tags, h.track_id AS hit_track_id, h.turn_id AS hit_turn_id, h.score AS hit_score,
        
          u.id AS user_id_msg, u.text AS user_text, u.s3_uri AS user_s3_uri, u.ts AS user_ts, u.turn_id AS user_turn_id,
          a.id AS assistant_id_msg, a.text AS assistant_text, a.s3_uri AS assistant_s3_uri, a.ts AS assistant_ts, a.turn_id AS assistant_turn_id
        
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
                    track_id=meta.get("track_id"),
                    turn_id=meta.get("turn_id"),
                    bundle_id=meta.get("bundle_id"),
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
            turn_id: Optional[str] = None,
            query_embedding: Optional[List[float]] = None,   # <-- now optional
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
            bundle_id: Optional[str] = None,
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

        # Calculate recency decay
        rec_sql = """
            exp(-ln(2) * EXTRACT(EPOCH FROM (now() - m.ts)) / ({half_life_days_s}*24*3600.0)) AS rec,
        """

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
            if turn_id is not None:
                args.append(turn_id)
                where.append(f"m.turn_id = ${len(args)}")
        elif scope == "conversation" and conversation_id:
            args.append(conversation_id)
            where.append(f"m.conversation_id = ${len(args)}")
            if turn_id is not None:
                args.append(turn_id)
                where.append(f"m.turn_id = ${len(args)}")

        if bundle_id:
            args.append(bundle_id)
            where.append(f"m.bundle_id = ${len(args)}")

        # tags
        if any_tags:
            args.append(list(any_tags))
            where.append(f"m.tags && ${len(args)}::text[]")
        if all_tags:
            args.append(list(all_tags))
            where.append(f"m.tags @> ${len(args)}::text[]")
        if not_tags:
            args.append(list(not_tags))
            where.append(f"NOT (m.tags && ${len(args)}::text[])")

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
            val = _coerce_ts(val)
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
        rec_sql = f"""
        exp(-ln(2) * EXTRACT(EPOCH FROM (now() - m.ts)) / ({half_life_days_s}*24*3600.0)) AS rec
        """
        q = f"""
          WITH base AS (
            SELECT m.*,
                   {sim_sql},
                   EXTRACT(EPOCH FROM (now() - m.ts)) AS age_sec,
                   {rec_sql}, 
                   CASE m.role WHEN 'artifact' THEN 1.10 WHEN 'assistant' THEN 1.00 ELSE 0.98 END AS rboost
            FROM {self.schema}.conv_messages m
            WHERE {' AND '.join(where)}
          )
          SELECT m.id, m.message_id, m.role, m.text, m.s3_uri, m.ts, m.tags, m.track_id, m.turn_id, m.bundle_id,
                 m.sim, m.rec, m.age_sec, m.rboost,
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
            bundle_id: Optional[str] = None,
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
        if bundle_id:
            args.append(bundle_id); where.append(f"bundle_id = ${len(args)}")
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
          SELECT id, message_id, role, text, s3_uri, ts, tags, track_id, turn_id, bundle_id
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
            turn_id: Optional[str] = None,
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
            turn_id=turn_id,
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
            turn_id: Optional[str] = None,
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
                turn_id=turn_id,
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
            turn_id: Optional[str] = None,
            bundle_id: Optional[str] = None,
    ) -> int:
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
            ts_dt = _coerce_ts(ts)  # moved inside the guard
            sets.append(f"ts = ${len(args)+1}::timestamptz")
            args.append(ts_dt)
        if turn_id is not None:
            sets.append(f"turn_id = ${len(args)+1}")
            args.append(turn_id)
        if bundle_id is not None:
            sets.append(f"bundle_id = ${len(args)+1}"); args.append(bundle_id)
        if not sets:
            return 0

        if id:
            where = f"id = ${len(args)+1}"
            args.append(int(id))
        else:
            where = f"message_id = ${len(args)+1}"
            args.append(str(message_id))

        q = f"UPDATE {self.schema}.conv_messages SET {', '.join(sets)} WHERE {where}"
        async with self._pool.acquire() as con:
            res = await con.execute(q, *args)
        try:
            return int(res.split()[-1])
        except Exception:
            return 0

    async def count_turns(
            self,
            *,
            user_id: str,
            conversation_id: str,
            days: int = 365,                 # configurable window; mirrors other APIs
            track_id: Optional[str] = None,  # optional scope-narrowing
            bundle_id: Optional[str] = None,
    ) -> int:
        """
        Return the number of unique (non-NULL) turn_id values for the given conversation,
        respecting TTL and a recency window.
        """
        args = [user_id, conversation_id, str(days)]
        where = [
            "user_id = $1",
            "conversation_id = $2",
            "ts >= now() - ($3::text || ' days')::interval",
            "ts + (ttl_days || ' days')::interval >= now()",
            "turn_id IS NOT NULL"
        ]
        if track_id:
            args.append(track_id)
            where.append(f"track_id = ${len(args)}")
        if bundle_id:
            args.append(bundle_id); where.append(f"bundle_id = ${len(args)}")

        q = f"""
            SELECT COALESCE(COUNT(DISTINCT turn_id), 0) AS n
            FROM {self.schema}.conv_messages
            WHERE {' AND '.join(where)}
        """
        async with self._pool.acquire() as con:
            row = await con.fetchrow(q, *args)
        return int(row["n"] or 0)

    async def get_conversation_turn_ids_from_tags(
            self,
            *,
            user_id: str,
            conversation_id: str,
            days: int = 365,
            track_id: Optional[str] = None,
            bundle_id: Optional[str] = None,
            turn_ids: Optional[Sequence[str]] = None,

    ) -> List[Dict[str, Any]]:
        """
        Return occurrences of turn tags ('turn:<id>') for the given user & conversation,
        ordered oldest→newest, preserving duplicates. Each item includes:
          - turn_id: the extracted turn id
          - ts:     timestamp of the message where the tag was found (ISO8601 str)
          - tags:   full tags array from that message
        TTL is respected and a recency window can be applied via `days`.
        """
        args: List[Any] = [user_id, conversation_id, str(days)]
        where = [
            "m.user_id = $1",
            "m.conversation_id = $2",
            "m.ts >= now() - ($3::text || ' days')::interval",
            "m.ts + (m.ttl_days || ' days')::interval >= now()",
        ]
        if track_id:
            args.append(track_id)
            where.append(f"m.track_id = ${len(args)}")

        if bundle_id:
            args.append(bundle_id); where.append(f"m.bundle_id = ${len(args)}")

        turn_ids_cond = "TRUE"
        if turn_ids:
            args.append(list(turn_ids))
            turn_ids_cond = f"split_part(t.tag, ':', 2) = ANY(${len(args)}::text[])"

        q = f"""
            WITH exploded AS (
              SELECT
                m.ts,
                m.message_id AS mid,
                m.s3_uri     AS s3_uri,
                m.tags       AS tags,
                m.bundle_id  AS bundle_id,
                t.tag        AS tag,
                array_position(m.tags, t.tag) AS tag_idx
              FROM {self.schema}.conv_messages m
              JOIN LATERAL unnest(m.tags) AS t(tag) ON TRUE
              WHERE {' AND '.join(where)}
                AND t.tag LIKE 'turn:%'
                AND {turn_ids_cond}
            )
            SELECT substring(tag FROM '^turn:(.+)$') AS turn_id,
                   ts, tags, mid, s3_uri, bundle_id
            FROM exploded
            ORDER BY ts ASC, mid ASC, tag_idx ASC
        """
        async with self._pool.acquire() as con:
            rows = await con.fetch(q, *args)

        out: List[Dict[str, Any]] = []
        for r in rows:
            tid = r["turn_id"]
            if not tid:
                continue
            ts = r["ts"]
            out.append({
                "turn_id": tid,
                "ts": ts.isoformat() if hasattr(ts, "isoformat") else ts,
                "tags": list(r.get("tags") or []),
                "mid": r.get("mid"),
                "s3_uri": r.get("s3_uri"),
                "bundle_id": r.get("bundle_id"),
            })
        return out

    async def list_user_conversations(
            self,
            *,
            user_id: str,
            since: Optional[Union[str, datetime]] = None,
            limit: Optional[int] = None,
            days: int = 365,
            include_conv_start_text: bool = False,
            bundle_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        List conversations for a user, filtered server-side:
          - last_activity_at: MAX(ts) across messages (TTL respected),
            additionally constrained by `since` (if provided) and the rolling `days` window
          - started_at: MIN(ts) among conv-start fingerprint rows (TTL + days window)
          - conv_start_text: optional small JSON text blob of the latest conv-start
        Ordered by last_activity_at DESC. Applies LIMIT if given.
        """
        # $1=user_id, $2=days
        args: List[Any] = [user_id, str(days)]

        extra = []
        if bundle_id:
            args.append(bundle_id)
            extra.append(f"AND m.bundle_id = ${len(args)}")

        since_cond = "TRUE"
        if since is not None:
            dt = _coerce_ts(since)
            args.append(dt)  # $3
            since_cond = f"m.ts >= ${len(args)}::timestamptz"

        limit_sql = f"LIMIT {int(limit)}" if (limit and limit > 0) else ""

        conv_text_col = "NULL::text AS conv_start_text"
        join_conv_text = ""
        if include_conv_start_text:
            conv_text_col = "st.conv_start_text"
            join_conv_text = f"""
            LEFT JOIN LATERAL (
              SELECT s.text AS conv_start_text
              FROM {self.schema}.conv_messages s
              WHERE s.user_id = $1
                AND s.conversation_id = r.conversation_id
                AND s.ts + (s.ttl_days || ' days')::interval >= now()
                AND s.ts >= now() - ($2::text || ' days')::interval
                {'AND s.bundle_id = $3' if bundle_id else ''}
                AND s.tags @> ARRAY['conv.start','artifact:turn.fingerprint.v1']::text[]
              ORDER BY s.ts DESC
              LIMIT 1
            ) st ON TRUE
            """

        q = f"""
          WITH recent AS (
            SELECT m.conversation_id, MAX(m.ts) AS last_activity_at
            FROM {self.schema}.conv_messages m
            WHERE m.user_id = $1
              AND m.ts + (m.ttl_days || ' days')::interval >= now()
              AND m.ts >= now() - ($2::text || ' days')::interval
              {' '.join(extra)}
              AND {since_cond}
            GROUP BY m.conversation_id
          ),
          starts AS (
            SELECT m.conversation_id, MIN(m.ts) AS started_at
            FROM {self.schema}.conv_messages m
            WHERE m.user_id = $1
              AND m.ts + (m.ttl_days || ' days')::interval >= now()
              AND m.ts >= now() - ($2::text || ' days')::interval
              {' '.join(extra)}
              AND m.tags @> ARRAY['conv.start','artifact:turn.fingerprint.v1']::text[]
            GROUP BY m.conversation_id
          )
          SELECT
            r.conversation_id,
            r.last_activity_at,
            s.started_at,
            {conv_text_col}
          FROM recent r
          LEFT JOIN starts s ON s.conversation_id = r.conversation_id
          {join_conv_text}
          ORDER BY r.last_activity_at DESC
          {limit_sql}
        """

        async with self._pool.acquire() as con:
            rows = await con.fetch(q, *args)

        out: List[Dict[str, Any]] = []
        for r in rows:
            la = r["last_activity_at"]
            sa = r["started_at"]
            out.append({
                "conversation_id": r["conversation_id"],
                "last_activity_at": la.isoformat() if hasattr(la, "isoformat") else la,
                "started_at": sa.isoformat() if (sa and hasattr(sa, "isoformat")) else sa,
                **({"conv_start_text": r.get("conv_start_text")} if include_conv_start_text else {}),
            })
        return out

    async def search_turn_logs_via_content(
            self,
            *,
            user_id: str,
            conversation_id: Optional[str],
            track_id: Optional[str],
            query_embedding: Optional[List[float]] = None,
            query_text: Optional[str] = None,
            search_roles: tuple[str, ...] = ("user", "assistant", "artifact"),  # roles to search for turn_id match
            search_tags: Optional[Sequence[str]] = None,
            top_k: int = 8,
            days: int = 90,
            scope: str = "track",
            bundle_id: Optional[str] = None,
            half_life_days: float = 7.0,
    ) -> List[Dict[str, Any]]:
        """
        Two-stage search optimized for finding turn logs:
        1. Search across specified roles/tags to find relevant turn_ids
        2. For each matched turn_id, fetch the corresponding artifact:turn.log

        Returns turn log artifacts (role='artifact', tags contain 'artifact:turn.log')
        ranked by the relevance score of the content that matched them.

        Use cases:
        - Find turn logs where user/assistant discussed X:
          search_roles=("user", "assistant"), search_tags=None

        - Find turn logs where project logs mention Y:
          search_roles=("artifact",), search_tags=["artifact:project.log"]

        Now returns both similarity (sim) and recency (rec) scores, plus combined score.
        """

        # Build WHERE clause for content search (stage 1)
        args: List[Any] = [user_id, list(search_roles), str(days)]
        where = [
            "m.user_id = $1",
            "m.role = ANY($2)",
            "m.ts >= now() - ($3::text || ' days')::interval",
            "m.ts + (m.ttl_days || ' days')::interval >= now()",
        ]

        # Semantic similarity (if embedding provided)
        if query_embedding is not None:
            args.append(convert_embedding_to_string(query_embedding))
            sim_sql = f"1 - (m.embedding <=> ${len(args)}::vector) AS sim"
            where.append("m.embedding IS NOT NULL")
            order_by = "sim DESC, m.ts DESC"
        else:
            sim_sql = "0.0::float AS sim"
            order_by = "m.ts DESC"

        if search_tags:
            args.append(list(search_tags))
            where.append(f"m.tags && ${len(args)}::text[]")

        # Scope filters
        if scope == "track" and track_id:
            args.append(track_id)
            where.append(f"m.track_id = ${len(args)}")
            if conversation_id:
                args.append(conversation_id)
                where.append(f"m.conversation_id = ${len(args)}")
        elif scope == "conversation" and conversation_id:
            args.append(conversation_id)
            where.append(f"m.conversation_id = ${len(args)}")

        if bundle_id:
            args.append(bundle_id)
            where.append(f"m.bundle_id = ${len(args)}")

        # Optional text filter (ILIKE)
        if query_text:
            args.append(f"%{query_text}%")
            where.append(f"m.text ILIKE ${len(args)}")

        # alf_life_days parameter for recency calculation
        args.append(max(0.1, float(half_life_days)))
        half_life_days_param = f"${len(args)}::float"

        # Main query: find matching content → fetch turn logs via LATERAL
        q = f"""
        WITH content_matches AS (
            SELECT 
                m.turn_id,
                m.role AS matched_role,
                m.ts AS matched_ts,
                {sim_sql},
                exp(-ln(2) * EXTRACT(EPOCH FROM (now() - m.ts)) / ({half_life_days_param}*24*3600.0)) AS rec,
                ROW_NUMBER() OVER (PARTITION BY m.turn_id ORDER BY m.ts DESC) AS rn
            FROM {self.schema}.conv_messages m
            WHERE {' AND '.join(where)}
              AND m.turn_id IS NOT NULL
            ORDER BY {order_by}
            LIMIT {int(top_k * 2)}  -- over-fetch since we'll dedupe by turn_id
        ),
        unique_turns AS (
            SELECT 
                turn_id, 
                matched_role, 
                matched_ts, 
                sim,
                rec,
                (0.80 * sim + 0.20 * rec) AS score
            FROM content_matches
            WHERE rn = 1
            ORDER BY score DESC, matched_ts DESC
            LIMIT {int(top_k)}
        )
        SELECT 
            log.id, log.message_id, log.role, log.text, log.s3_uri, log.ts, log.tags,
            log.track_id, log.turn_id, log.bundle_id,
            ut.sim,
            ut.rec,
            ut.score,
            ut.sim AS relevance_score,
            ut.matched_role,
            ut.matched_ts
        FROM unique_turns ut
        JOIN LATERAL (
            SELECT *
            FROM {self.schema}.conv_messages
            WHERE user_id = $1
              AND turn_id = ut.turn_id
              AND role = 'artifact'
              AND tags @> ARRAY['artifact:turn.log']::text[]
              AND ts + (ttl_days || ' days')::interval >= now()
            ORDER BY ts DESC
            LIMIT 1
        ) log ON TRUE
        ORDER BY ut.score DESC, ut.matched_ts DESC
        """

        async with self._pool.acquire() as con:
            rows = await con.fetch(q, *args)

        return [dict(r) for r in rows]

    async def delete_message(
            self,
            *,
            id: Optional[int] = None,
            message_id: Optional[str] = None,
    ) -> int:
        """
        Delete a row from conv_messages. Also deletes edges referencing it.
        Returns number of deleted conv_messages rows (0 or 1).
        """
        if not id and not message_id:
            raise ValueError("delete_message requires either id or message_id")

        async with self._pool.acquire() as con:
            async with con.transaction():
                # Resolve id when message_id is provided
                if id is None:
                    q_get = f"SELECT id FROM {self.schema}.conv_messages WHERE message_id = $1 LIMIT 1"
                    row = await con.fetchrow(q_get, str(message_id))
                    if not row:
                        return 0
                    id = int(row["id"])

                # Delete edges first (defensive)
                q_edges = f"""
                    DELETE FROM {self.schema}.conv_artifact_edges
                    WHERE from_id = $1 OR to_id = $1
                """
                await con.execute(q_edges, int(id))

                # Delete the message row
                q_del = f"DELETE FROM {self.schema}.conv_messages WHERE id = $1"
                res = await con.execute(q_del, int(id))
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

