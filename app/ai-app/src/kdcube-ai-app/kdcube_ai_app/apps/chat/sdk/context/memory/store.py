from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional, Sequence

from .models import (
    MemoryEvent,
    MemoryRecord,
    MemoryScope,
    MemorySearchRequest,
    MemorySearchResult,
    MemorySignal,
    is_user_visible,
    normalize_status,
    normalize_scope_filter,
    normalize_term,
    normalize_terms,
    normalize_visibility,
)
from .scoring import build_canonical_key, compute_memory_scores, event_weight, rank_candidate, search_query_terms

logger = logging.getLogger(__name__)

MEMORY_TABLE = "user_memory_entries"
EVENT_TABLE = "user_memory_events"
ALIAS_TABLE = "user_memory_aliases"
MAINTENANCE_TABLE = "user_memory_maintenance_artifacts"
USER_BUNDLE_PROPS_TABLE = "user_bundle_props"
MEMORY_PREFERENCES_SUBSYSTEM = "memory"
MEMORY_PREFERENCES_BUNDLE_ID = "*"
MEMORY_PREFERENCES_KEY = "preferences"


def _safe_identifier(value: str, *, fallback: str = "kdcube_default_default") -> str:
    raw = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "")).strip("_").lower()
    if not raw:
        raw = fallback
    if raw[0].isdigit():
        raw = f"_{raw}"
    return raw


def _schema_from_scope(tenant: str, project: str) -> str:
    tenant_part = _safe_identifier(tenant or "default", fallback="default")
    project_part = _safe_identifier(project or "default", fallback="default")
    schema = f"{tenant_part}_{project_part}"
    if not schema.startswith("kdcube_"):
        schema = f"kdcube_{schema}"
    return schema


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_datetime(value: Any, *, default: Optional[datetime] = None) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        try:
            if raw.endswith("Z"):
                raw = f"{raw[:-1]}+00:00"
            parsed = datetime.fromisoformat(raw)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except Exception:
            pass
    return default or _utc_now()


def _json(value: Any) -> Any:
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return {}
    return value


def _array(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return normalize_terms(value)
    return normalize_terms([str(v) for v in value])


def _embedding_vector(value: Sequence[float] | None) -> str | None:
    if value is None:
        return None
    from kdcube_ai_app.infra.embedding.embedding import convert_embedding_to_string

    return convert_embedding_to_string([float(v) for v in value])


def _lexical_tsquery(value: str) -> str:
    terms = search_query_terms(value)
    return " | ".join(f"{term}:*" for term in terms)


class UserMemoryStore:
    """Postgres-backed cross-conversation user memory store.

    The store expects an asyncpg-like pool.  It intentionally does not own
    request auth, bundle secrets, or UI policy; bundles pass the already
    authenticated user scope and decide which tools are write-enabled.
    """

    def __init__(
        self,
        *,
        pg_pool: Any | None = None,
        schema: str | None = None,
        tenant: str = "default",
        project: str = "default",
    ):
        self._pool = pg_pool
        self._owns_pool = False
        self.schema = _safe_identifier(schema) if schema else _schema_from_scope(tenant, project)

    async def init_from_settings(self) -> None:
        """Create an owned asyncpg pool from SDK settings.

        Prefer passing the processor's shared pg_pool in production.  This
        method is for scripts/tests and mirrors other SDK context stores.
        """

        if self._pool is not None:
            return
        import asyncpg

        from kdcube_ai_app.apps.chat.sdk.config import get_settings, resolve_asyncpg_ssl

        settings = get_settings()
        self._pool = await asyncpg.create_pool(
            host=settings.PGHOST,
            port=settings.PGPORT,
            user=settings.PGUSER,
            password=settings.PGPASSWORD,
            database=settings.PGDATABASE,
            ssl=resolve_asyncpg_ssl(settings),
        )
        self._owns_pool = True

    async def close(self) -> None:
        if self._pool is not None and self._owns_pool:
            await self._pool.close()
        self._pool = None

    def _require_pool(self) -> Any:
        if self._pool is None:
            raise RuntimeError("UserMemoryStore requires pg_pool or init_from_settings()")
        return self._pool

    async def ensure_schema(self) -> None:
        pool = self._require_pool()
        statements = self._schema_statements()
        async with pool.acquire() as con:
            for stmt in statements:
                await con.execute(stmt)

    def _schema_statements(self) -> list[str]:
        schema = self.schema
        return [
            f"CREATE SCHEMA IF NOT EXISTS {schema}",
            f"""
            CREATE TABLE IF NOT EXISTS {schema}.{MEMORY_TABLE} (
                id TEXT PRIMARY KEY,
                tenant TEXT NOT NULL,
                project TEXT NOT NULL,
                user_id TEXT NOT NULL,
                bundle_id TEXT NOT NULL DEFAULT '',
                canonical_key TEXT NOT NULL,
                memory TEXT NOT NULL,
                context TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT 'fact',
                status TEXT NOT NULL DEFAULT 'active',
                visibility TEXT NOT NULL DEFAULT 'user',
                visible_to_user BOOLEAN NOT NULL DEFAULT TRUE,
                labels TEXT[] NOT NULL DEFAULT '{{}}',
                keywords TEXT[] NOT NULL DEFAULT '{{}}',
                pinned BOOLEAN NOT NULL DEFAULT FALSE,
                search_text TEXT NOT NULL DEFAULT '',
                search_tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', coalesce(search_text, ''))) STORED,
                embedding VECTOR(1536),
                embedding_model TEXT NOT NULL DEFAULT '',
                evidence_count INTEGER NOT NULL DEFAULT 0,
                update_count INTEGER NOT NULL DEFAULT 0,
                confirmation_count INTEGER NOT NULL DEFAULT 0,
                contradiction_count INTEGER NOT NULL DEFAULT 0,
                positive_weight DOUBLE PRECISION NOT NULL DEFAULT 0,
                negative_weight DOUBLE PRECISION NOT NULL DEFAULT 0,
                confidence_score DOUBLE PRECISION NOT NULL DEFAULT 0.5,
                importance_score DOUBLE PRECISION NOT NULL DEFAULT 0.5,
                freshness_score DOUBLE PRECISION NOT NULL DEFAULT 1.0,
                salience_score DOUBLE PRECISION NOT NULL DEFAULT 0.5,
                confirmation_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
                tier INTEGER NOT NULL DEFAULT 3,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_event_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_confirmed_at TIMESTAMPTZ,
                source JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                revision INTEGER NOT NULL DEFAULT 1,
                merged_into_id TEXT REFERENCES {schema}.{MEMORY_TABLE}(id)
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS {schema}.{EVENT_TABLE} (
                id TEXT PRIMARY KEY,
                memory_id TEXT NOT NULL REFERENCES {schema}.{MEMORY_TABLE}(id) ON DELETE CASCADE,
                tenant TEXT NOT NULL,
                project TEXT NOT NULL,
                user_id TEXT NOT NULL,
                bundle_id TEXT NOT NULL DEFAULT '',
                conversation_id TEXT NOT NULL DEFAULT '',
                turn_id TEXT NOT NULL DEFAULT '',
                event_type TEXT NOT NULL,
                signal_text TEXT NOT NULL DEFAULT '',
                context TEXT NOT NULL DEFAULT '',
                originator TEXT NOT NULL DEFAULT 'agent',
                confidence DOUBLE PRECISION NOT NULL DEFAULT 0.5,
                importance DOUBLE PRECISION NOT NULL DEFAULT 0.5,
                labels TEXT[] NOT NULL DEFAULT '{{}}',
                keywords TEXT[] NOT NULL DEFAULT '{{}}',
                source JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                idempotency_key TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS {schema}.{ALIAS_TABLE} (
                memory_id TEXT NOT NULL REFERENCES {schema}.{MEMORY_TABLE}(id) ON DELETE CASCADE,
                alias_type TEXT NOT NULL,
                value TEXT NOT NULL,
                weight DOUBLE PRECISION NOT NULL DEFAULT 1.0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (memory_id, alias_type, value)
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS {schema}.{MAINTENANCE_TABLE} (
                id TEXT PRIMARY KEY,
                tenant TEXT NOT NULL,
                project TEXT NOT NULL,
                user_id TEXT NOT NULL,
                bundle_id TEXT NOT NULL DEFAULT '',
                artifact_type TEXT NOT NULL,
                artifact_id TEXT NOT NULL,
                scope_filter TEXT NOT NULL DEFAULT 'current_bundle',
                status TEXT NOT NULL DEFAULT '',
                storage_bundle_id TEXT NOT NULL DEFAULT '',
                summary JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                artifacts JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS {schema}.{USER_BUNDLE_PROPS_TABLE} (
                user_id TEXT NOT NULL,
                bundle_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value_json JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                subsystem TEXT NOT NULL DEFAULT 'bundle',
                PRIMARY KEY (user_id, bundle_id, key)
            )
            """,
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_user_memory_entries_canonical
            ON {schema}.{MEMORY_TABLE} (tenant, project, user_id, canonical_key)
            WHERE merged_into_id IS NULL
            """,
            f"ALTER TABLE {schema}.{USER_BUNDLE_PROPS_TABLE} ADD COLUMN IF NOT EXISTS subsystem TEXT NOT NULL DEFAULT 'bundle'",
            f"ALTER TABLE {schema}.{MEMORY_TABLE} ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT FALSE",
            f"CREATE INDEX IF NOT EXISTS idx_user_memory_entries_scope ON {schema}.{MEMORY_TABLE} (tenant, project, user_id, status, tier, updated_at DESC)",
            f"CREATE INDEX IF NOT EXISTS idx_user_memory_entries_hotset ON {schema}.{MEMORY_TABLE} (tenant, project, user_id, status, tier, pinned DESC, updated_at DESC)",
            f"CREATE INDEX IF NOT EXISTS idx_user_memory_entries_visible ON {schema}.{MEMORY_TABLE} (tenant, project, user_id, visible_to_user, updated_at DESC)",
            f"CREATE INDEX IF NOT EXISTS idx_user_memory_entries_labels ON {schema}.{MEMORY_TABLE} USING GIN (labels)",
            f"CREATE INDEX IF NOT EXISTS idx_user_memory_entries_keywords ON {schema}.{MEMORY_TABLE} USING GIN (keywords)",
            f"CREATE INDEX IF NOT EXISTS idx_user_memory_entries_tsv ON {schema}.{MEMORY_TABLE} USING GIN (search_tsv)",
            f"CREATE INDEX IF NOT EXISTS idx_user_memory_entries_embedding ON {schema}.{MEMORY_TABLE} USING ivfflat (embedding vector_cosine_ops) WITH (lists=50)",
            f"ALTER TABLE {schema}.{EVENT_TABLE} ADD COLUMN IF NOT EXISTS idempotency_key TEXT NOT NULL DEFAULT ''",
            f"CREATE INDEX IF NOT EXISTS idx_user_memory_events_scope ON {schema}.{EVENT_TABLE} (tenant, project, user_id, created_at DESC)",
            f"CREATE INDEX IF NOT EXISTS idx_user_memory_events_scope_bundle ON {schema}.{EVENT_TABLE} (tenant, project, user_id, bundle_id, created_at DESC)",
            f"CREATE INDEX IF NOT EXISTS idx_user_memory_events_memory ON {schema}.{EVENT_TABLE} (memory_id, created_at DESC)",
            f"CREATE INDEX IF NOT EXISTS idx_user_memory_maintenance_scope ON {schema}.{MAINTENANCE_TABLE} (tenant, project, user_id, artifact_type, updated_at DESC)",
            f"CREATE INDEX IF NOT EXISTS idx_user_memory_maintenance_scope_bundle ON {schema}.{MAINTENANCE_TABLE} (tenant, project, user_id, bundle_id, artifact_type, updated_at DESC)",
            f"CREATE INDEX IF NOT EXISTS idx_user_bundle_props_subsystem ON {schema}.{USER_BUNDLE_PROPS_TABLE} (user_id, subsystem, bundle_id, key, updated_at DESC)",
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_user_memory_maintenance_artifact
            ON {schema}.{MAINTENANCE_TABLE} (tenant, project, user_id, artifact_type, artifact_id)
            """,
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_user_memory_events_idempotency
            ON {schema}.{EVENT_TABLE} (tenant, project, user_id, idempotency_key)
            WHERE idempotency_key <> ''
            """,
        ]

    async def record_signal(
        self,
        *,
        scope: MemoryScope,
        signal: MemorySignal,
        match_memory_id: str = "",
        require_match: bool = False,
        merge_threshold: Optional[float] = 0.88,
        append_on_canonical_match: bool = True,
        include_retired_canonical: bool = False,
        ensure_schema: bool = False,
    ) -> MemoryRecord:
        """Create a memory or append evidence to an existing one.

        If match_memory_id is provided, the signal updates that record.
        Otherwise the store first checks active records by canonical_key, then
        can use a conservative hybrid candidate match before creating a new row.
        Widget-style create flows should pass append_on_canonical_match=False
        and merge_threshold=None so exact active duplicates are no-ops and
        similar-but-different memories remain separate until reconciliation.
        """

        if ensure_schema:
            await self.ensure_schema()
        pool = self._require_pool()
        scope = scope.normalized()
        normalized_signal = self._normalize_signal(scope, signal)

        async with pool.acquire() as con:
            async with con.transaction():
                row = None
                created = False
                if match_memory_id:
                    row = await self._fetch_memory_for_update(con, scope=scope, memory_id=match_memory_id)
                    if row is None and require_match:
                        raise ValueError(f"memory {match_memory_id!r} was not found")
                if row is None:
                    row = await self._fetch_by_canonical_key_for_update(
                        con,
                        scope=scope,
                        canonical_key=normalized_signal["canonical_key"],
                        statuses=None if include_retired_canonical else ("active", "weakened", "unsupported"),
                    )
                    if row is not None and not append_on_canonical_match:
                        return self._record_from_row(row)
                if (
                    row is None
                    and not normalized_signal["canonical_key_supplied"]
                    and merge_threshold is not None
                    and merge_threshold > 0
                ):
                    row = await self._find_merge_candidate_for_update(
                        con,
                        scope=scope,
                        signal=normalized_signal,
                        threshold=merge_threshold,
                    )
                if row is None:
                    row = await self._insert_memory(con, scope=scope, signal=normalized_signal)
                    if row is not None:
                        created = True
                    else:
                        row = await self._fetch_by_canonical_key_for_update(
                            con,
                            scope=scope,
                            canonical_key=normalized_signal["canonical_key"],
                            statuses=None if include_retired_canonical else ("active", "weakened", "unsupported"),
                        )
                        if row is None:
                            conflict = await self._fetch_by_canonical_key_for_update(
                                con,
                                scope=scope,
                                canonical_key=normalized_signal["canonical_key"],
                                statuses=None,
                            )
                            if conflict is not None and str(conflict.get("status") or "") == "retired":
                                raise ValueError("memory_exact_match_is_retired")
                            raise RuntimeError("memory insert lost to canonical conflict")
                if not created:
                    row = await self._append_event_and_update_scores(con, scope=scope, row=row, signal=normalized_signal)
        return self._record_from_row(row)

    async def search(self, request: MemorySearchRequest) -> list[MemorySearchResult | MemoryEvent]:
        pool = self._require_pool()
        scope = request.scope.normalized()
        mode = request.mode or "hybrid"
        if mode == "recent_events":
            return await self._search_recent_events(request)
        rows = await self._fetch_candidates(request)
        now = _utc_now()
        results: list[MemorySearchResult] = []
        relevance_requested = bool(str(request.query or "").strip() or normalize_terms(request.labels) or normalize_terms(request.keywords))
        try:
            min_relevance_score = max(0.0, min(1.0, float(request.min_relevance_score or 0.0)))
        except Exception:
            min_relevance_score = 0.0
        for row in rows:
            if mode == "recent":
                score = 1.0
                breakdown = {"updated_at": 1.0}
            elif mode == "recent_created":
                score = 1.0
                breakdown = {"created_at": 1.0}
            elif mode == "important":
                score = float(row.get("importance_score") or 0.0)
                breakdown = {"importance": score}
            elif mode == "confirmed":
                score = float(row.get("confirmation_rate") or 0.0)
                breakdown = {
                    "confirmation": score,
                    "count": float(row.get("confirmation_count") or 0),
                }
            elif mode == "hotset":
                score = float(row.get("salience_score") or 0.0)
                breakdown = {
                    "salience": score,
                    "tier": float(row.get("tier") or 3),
                    "importance": float(row.get("importance_score") or 0.0),
                }
            else:
                score, breakdown = rank_candidate(
                    query=request.query,
                    query_embedding=request.query_embedding,
                    requested_labels=request.labels,
                    requested_keywords=request.keywords,
                    row=row,
                    text_rank=float(row.get("text_rank") or 0.0),
                    half_life_days=request.half_life_days,
                    now=now,
                )
                if relevance_requested and min_relevance_score > 0:
                    relevance_score = max(
                        float(breakdown.get("semantic") or 0.0),
                        float(breakdown.get("text") or 0.0),
                        float(breakdown.get("labels") or 0.0),
                    )
                    if relevance_score < min_relevance_score:
                        continue
            results.append(
                MemorySearchResult(
                    memory=self._record_from_row(row),
                    score=score,
                    score_breakdown=breakdown,
                )
            )
        if mode == "recent":
            results.sort(key=lambda r: r.memory.updated_at, reverse=True)
        elif mode == "recent_created":
            results.sort(key=lambda r: r.memory.created_at, reverse=True)
        else:
            results.sort(
                key=lambda r: (
                    r.score,
                    -int(r.memory.tier or 4),
                    1 if r.memory.pinned else 0,
                    r.memory.confidence_score,
                    r.memory.salience_score,
                    r.memory.importance_score,
                    r.memory.freshness_score,
                    r.memory.updated_at,
                ),
                reverse=True,
            )
        offset = max(0, int(request.offset or 0))
        limit = max(0, int(request.limit or 8))
        return results[offset: offset + limit]

    async def get_hotset(
        self,
        *,
        scope: MemoryScope,
        limit: int = 8,
        visible_to_user: Optional[bool] = None,
    ) -> list[MemorySearchResult]:
        rows = await self.search(
            MemorySearchRequest(
                scope=scope,
                mode="hotset",
                status="active",
                visible_to_user=visible_to_user,
                limit=limit,
            )
        )
        return [row for row in rows if isinstance(row, MemorySearchResult)]

    async def list_recent_memories(
        self,
        *,
        scope: MemoryScope,
        limit: int = 10,
        created: bool = False,
        visible_to_user: Optional[bool] = None,
    ) -> list[MemorySearchResult]:
        rows = await self.search(
            MemorySearchRequest(
                scope=scope,
                mode="recent_created" if created else "recent",
                visible_to_user=visible_to_user,
                status="any",
                limit=limit,
            )
        )
        return [row for row in rows if isinstance(row, MemorySearchResult)]

    async def list_recent_events(
        self,
        *,
        scope: MemoryScope,
        limit: int = 20,
        visible_to_user: Optional[bool] = None,
    ) -> list[MemoryEvent]:
        rows = await self.search(
            MemorySearchRequest(
                scope=scope,
                mode="recent_events",
                visible_to_user=visible_to_user,
                status="any",
                limit=limit,
            )
        )
        return [row for row in rows if isinstance(row, MemoryEvent)]

    async def get_memory(
        self,
        *,
        scope: MemoryScope,
        memory_id: str,
        visible_to_user: Optional[bool] = None,
        scope_filter: str = "current_bundle",
    ) -> Optional[MemoryRecord]:
        pool = self._require_pool()
        scope = scope.normalized()
        args: list[Any] = [scope.tenant, scope.project, scope.user_id, str(memory_id or "")]
        where = ["tenant=$1", "project=$2", "user_id=$3", "id=$4", "merged_into_id IS NULL"]
        if visible_to_user is not None:
            args.append(bool(visible_to_user))
            where.append(f"visible_to_user=${len(args)}")
        self._append_scope_filter(where=where, args=args, scope=scope, scope_filter=scope_filter, table_alias="")
        sql = f"""
            SELECT *
            FROM {self.schema}.{MEMORY_TABLE}
            WHERE {' AND '.join(where)}
            LIMIT 1
        """
        async with pool.acquire() as con:
            row = await con.fetchrow(sql, *args)
        return self._record_from_row(dict(row)) if row else None

    async def list_memory_events(
        self,
        *,
        scope: MemoryScope,
        memory_id: str,
        limit: int = 25,
        visible_to_user: Optional[bool] = None,
        scope_filter: str = "current_bundle",
    ) -> list[MemoryEvent]:
        pool = self._require_pool()
        scope = scope.normalized()
        args: list[Any] = [scope.tenant, scope.project, scope.user_id, str(memory_id or "")]
        where = ["e.tenant=$1", "e.project=$2", "e.user_id=$3", "e.memory_id=$4"]
        if visible_to_user is not None:
            args.append(bool(visible_to_user))
            where.append(f"m.visible_to_user=${len(args)}")
        self._append_scope_filter(where=where, args=args, scope=scope, scope_filter=scope_filter, table_alias="m")
        limit = max(1, min(int(limit or 25), 200))
        sql = f"""
            SELECT e.*
            FROM {self.schema}.{EVENT_TABLE} e
            JOIN {self.schema}.{MEMORY_TABLE} m ON m.id = e.memory_id
            WHERE {' AND '.join(where)}
            ORDER BY e.created_at DESC
            LIMIT {limit}
        """
        async with pool.acquire() as con:
            rows = await con.fetch(sql, *args)
        return [self._event_from_row(dict(row)) for row in rows]

    async def get_user_preferences(self, *, scope: MemoryScope) -> Dict[str, Any]:
        """Return user-level memory preferences.

        Absence means enabled. This keeps existing deployments permissive until
        the user explicitly opts out.
        """

        pool = self._require_pool()
        scope = scope.normalized()
        async with pool.acquire() as con:
            row = await con.fetchrow(
                f"""
                SELECT value_json, created_at, updated_at
                FROM {self.schema}.{USER_BUNDLE_PROPS_TABLE}
                WHERE user_id=$1
                  AND bundle_id=$2
                  AND key=$3
                  AND COALESCE(subsystem, 'bundle')=$4
                LIMIT 1
                """,
                scope.user_id,
                MEMORY_PREFERENCES_BUNDLE_ID,
                MEMORY_PREFERENCES_KEY,
                MEMORY_PREFERENCES_SUBSYSTEM,
            )
        if not row:
            now = _utc_now().isoformat()
            return {
                "memory_enabled": True,
                "updated_by": "",
                "metadata": {},
                "created_at": now,
                "updated_at": now,
            }
        data = dict(row)
        prefs = _json(data.get("value_json"))
        if not isinstance(prefs, dict):
            prefs = {}
        return {
            "memory_enabled": bool(prefs.get("memory_enabled", True)),
            "updated_by": str(prefs.get("updated_by") or ""),
            "metadata": _json(prefs.get("metadata")),
            "created_at": _coerce_datetime(data.get("created_at")).isoformat(),
            "updated_at": _coerce_datetime(data.get("updated_at")).isoformat(),
        }

    async def set_user_preferences(
        self,
        *,
        scope: MemoryScope,
        memory_enabled: bool,
        updated_by: str = "user",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        pool = self._require_pool()
        scope = scope.normalized()
        now = _utc_now()
        value = {
            "memory_enabled": bool(memory_enabled),
            "updated_by": str(updated_by or "user"),
            "metadata": metadata or {},
            "tenant": scope.tenant,
            "project": scope.project,
        }
        async with pool.acquire() as con:
            row = await con.fetchrow(
                f"""
                INSERT INTO {self.schema}.{USER_BUNDLE_PROPS_TABLE}
                    (user_id, bundle_id, key, value_json, created_at, updated_at, subsystem)
                VALUES ($1, $2, $3, $4::jsonb, $5, $5, $6)
                ON CONFLICT (user_id, bundle_id, key)
                DO UPDATE SET
                    value_json = EXCLUDED.value_json,
                    subsystem = EXCLUDED.subsystem,
                    updated_at = EXCLUDED.updated_at
                RETURNING value_json, created_at, updated_at
                """,
                scope.user_id,
                MEMORY_PREFERENCES_BUNDLE_ID,
                MEMORY_PREFERENCES_KEY,
                json.dumps(value, ensure_ascii=False, default=str),
                now,
                MEMORY_PREFERENCES_SUBSYSTEM,
            )
        data = dict(row) if row else {}
        prefs = _json(data.get("value_json"))
        if not isinstance(prefs, dict):
            prefs = value
        return {
            "memory_enabled": bool(prefs.get("memory_enabled", memory_enabled)),
            "updated_by": str(prefs.get("updated_by") or updated_by or "user"),
            "metadata": _json(prefs.get("metadata")),
            "created_at": _coerce_datetime(data.get("created_at"), default=now).isoformat(),
            "updated_at": _coerce_datetime(data.get("updated_at"), default=now).isoformat(),
        }

    async def count_memories(
        self,
        *,
        scope: MemoryScope,
        query: str = "",
        labels: Sequence[str] = (),
        keywords: Sequence[str] = (),
        kind: str = "",
        status: str = "active",
        visible_to_user: Optional[bool] = None,
        include_private: bool = True,
        scope_filter: str = "current_bundle",
    ) -> int:
        pool = self._require_pool()
        scope = scope.normalized()
        args: list[Any] = [scope.tenant, scope.project, scope.user_id]
        where = ["tenant=$1", "project=$2", "user_id=$3", "merged_into_id IS NULL"]
        normalized_status = normalize_term(status)
        if normalized_status and normalized_status != "any":
            args.append(normalized_status)
            where.append(f"status=${len(args)}")
        if kind:
            args.append(normalize_term(kind))
            where.append(f"kind=${len(args)}")
        if visible_to_user is not None:
            args.append(bool(visible_to_user))
            where.append(f"visible_to_user=${len(args)}")
        elif not include_private:
            where.append("visible_to_user=TRUE")
        self._append_scope_filter(where=where, args=args, scope=scope, scope_filter=scope_filter, table_alias="")
        labels_list = normalize_terms(labels)
        if labels_list:
            args.append(labels_list)
            where.append(f"labels && ${len(args)}::text[]")
        keywords_list = normalize_terms(keywords)
        if keywords_list:
            args.append(keywords_list)
            where.append(f"keywords && ${len(args)}::text[]")
        normalized_query = str(query or "").strip()
        lexical_tsquery = _lexical_tsquery(normalized_query)
        if lexical_tsquery:
            args.append(lexical_tsquery)
            where.append(f"to_tsvector('english', search_text) @@ to_tsquery('english', ${len(args)})")
        async with pool.acquire() as con:
            count = await con.fetchval(
                f"""
                SELECT count(*)
                FROM {self.schema}.{MEMORY_TABLE}
                WHERE {' AND '.join(where)}
                """,
                *args,
            )
        return int(count or 0)

    async def delete_memory(
        self,
        *,
        scope: MemoryScope,
        memory_id: str,
        visible_to_user: Optional[bool] = None,
        scope_filter: str = "current_bundle",
        ensure_schema: bool = False,
    ) -> bool:
        """Hard-delete a user memory row.

        Events and aliases reference memory rows with ON DELETE CASCADE, so this
        removes the user-visible note and its attached audit events together.
        """

        if ensure_schema:
            await self.ensure_schema()
        pool = self._require_pool()
        scope = scope.normalized()
        args: list[Any] = [scope.tenant, scope.project, scope.user_id, str(memory_id or "")]
        where = ["tenant=$1", "project=$2", "user_id=$3", "id=$4"]
        if visible_to_user is not None:
            args.append(bool(visible_to_user))
            where.append(f"visible_to_user=${len(args)}")
        self._append_scope_filter(where=where, args=args, scope=scope, scope_filter=scope_filter, table_alias="")
        async with pool.acquire() as con:
            row = await con.fetchrow(
                f"""
                DELETE FROM {self.schema}.{MEMORY_TABLE}
                WHERE {' AND '.join(where)}
                RETURNING id
                """,
                *args,
            )
        return bool(row)

    async def register_maintenance_artifact(
        self,
        *,
        scope: MemoryScope,
        artifact_type: str,
        artifact_id: str,
        status: str = "",
        scope_filter: str = "current_bundle",
        storage_bundle_id: str = "",
        summary: Optional[Dict[str, Any]] = None,
        artifacts: Optional[Dict[str, Any]] = None,
        created_at: Any = None,
        updated_at: Any = None,
    ) -> Dict[str, Any]:
        """Register a user-scoped memory maintenance artifact.

        Jobs and snapshots may be created through any bundle, but when a widget
        reads `all_user_memories` it needs a user-level index independent from
        the bundle storage that owns the artifact bytes.
        """

        pool = self._require_pool()
        scope = scope.normalized()
        artifact_type = normalize_term(artifact_type).replace(" ", "_")
        artifact_id = str(artifact_id or "").strip()
        if not artifact_type or not artifact_id:
            raise ValueError("artifact_type and artifact_id are required")
        storage_bundle_id = str(storage_bundle_id or scope.bundle_id or "").strip()
        created = _coerce_datetime(created_at, default=_utc_now())
        updated = _coerce_datetime(updated_at, default=created)
        row_id = f"{scope.tenant}:{scope.project}:{scope.user_id}:{artifact_type}:{artifact_id}"
        async with pool.acquire() as con:
            row = await con.fetchrow(
                f"""
                INSERT INTO {self.schema}.{MAINTENANCE_TABLE}
                    (id, tenant, project, user_id, bundle_id, artifact_type, artifact_id,
                     scope_filter, status, storage_bundle_id, summary, artifacts, created_at, updated_at)
                VALUES
                    ($1, $2, $3, $4, $5, $6, $7,
                     $8, $9, $10, $11::jsonb, $12::jsonb, $13, $14)
                ON CONFLICT (tenant, project, user_id, artifact_type, artifact_id)
                DO UPDATE SET
                    bundle_id = EXCLUDED.bundle_id,
                    scope_filter = EXCLUDED.scope_filter,
                    status = EXCLUDED.status,
                    storage_bundle_id = EXCLUDED.storage_bundle_id,
                    summary = EXCLUDED.summary,
                    artifacts = EXCLUDED.artifacts,
                    created_at = LEAST({self.schema}.{MAINTENANCE_TABLE}.created_at, EXCLUDED.created_at),
                    updated_at = EXCLUDED.updated_at
                RETURNING *
                """,
                row_id,
                scope.tenant,
                scope.project,
                scope.user_id,
                scope.bundle_id,
                artifact_type,
                artifact_id,
                normalize_scope_filter(scope_filter),
                str(status or ""),
                storage_bundle_id,
                json.dumps(summary or {}, ensure_ascii=False, default=str),
                json.dumps(artifacts or {}, ensure_ascii=False, default=str),
                created,
                updated,
            )
        return self._maintenance_artifact_from_row(dict(row)) if row else {}

    async def list_maintenance_artifacts(
        self,
        *,
        scope: MemoryScope,
        artifact_type: str,
        scope_filter: str = "current_bundle",
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        pool = self._require_pool()
        scope = scope.normalized()
        artifact_type = normalize_term(artifact_type).replace(" ", "_")
        normalized_scope_filter = normalize_scope_filter(scope_filter)
        args: list[Any] = [scope.tenant, scope.project, scope.user_id, artifact_type]
        where = ["tenant=$1", "project=$2", "user_id=$3", "artifact_type=$4"]
        if normalized_scope_filter != "all_user_memories":
            args.append(scope.bundle_id)
            where.append(f"bundle_id=${len(args)}")
        limit = max(1, min(int(limit or 20), 100))
        offset = max(0, int(offset or 0))
        where_sql = " AND ".join(where)
        async with pool.acquire() as con:
            count = await con.fetchval(
                f"SELECT count(*) FROM {self.schema}.{MAINTENANCE_TABLE} WHERE {where_sql}",
                *args,
            )
            rows = await con.fetch(
                f"""
                SELECT *
                FROM {self.schema}.{MAINTENANCE_TABLE}
                WHERE {where_sql}
                ORDER BY updated_at DESC, created_at DESC
                LIMIT {limit} OFFSET {offset}
                """,
                *args,
            )
        return {
            "items": [self._maintenance_artifact_from_row(dict(row)) for row in rows],
            "count": int(count or 0),
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < int(count or 0),
        }

    async def get_maintenance_artifact(
        self,
        *,
        scope: MemoryScope,
        artifact_type: str,
        artifact_id: str,
        allow_cross_bundle: bool = False,
    ) -> Optional[Dict[str, Any]]:
        pool = self._require_pool()
        scope = scope.normalized()
        args: list[Any] = [
            scope.tenant,
            scope.project,
            scope.user_id,
            normalize_term(artifact_type).replace(" ", "_"),
            str(artifact_id or "").strip(),
        ]
        where = ["tenant=$1", "project=$2", "user_id=$3", "artifact_type=$4", "artifact_id=$5"]
        if not allow_cross_bundle:
            args.append(scope.bundle_id)
            where.append(f"bundle_id=${len(args)}")
        async with pool.acquire() as con:
            row = await con.fetchrow(
                f"SELECT * FROM {self.schema}.{MAINTENANCE_TABLE} WHERE {' AND '.join(where)} LIMIT 1",
                *args,
            )
        return self._maintenance_artifact_from_row(dict(row)) if row else None

    async def delete_maintenance_artifact(
        self,
        *,
        scope: MemoryScope,
        artifact_type: str,
        artifact_id: str,
        allow_cross_bundle: bool = False,
    ) -> bool:
        pool = self._require_pool()
        scope = scope.normalized()
        args: list[Any] = [
            scope.tenant,
            scope.project,
            scope.user_id,
            normalize_term(artifact_type).replace(" ", "_"),
            str(artifact_id or "").strip(),
        ]
        where = ["tenant=$1", "project=$2", "user_id=$3", "artifact_type=$4", "artifact_id=$5"]
        if not allow_cross_bundle:
            args.append(scope.bundle_id)
            where.append(f"bundle_id=${len(args)}")
        async with pool.acquire() as con:
            result = await con.execute(
                f"DELETE FROM {self.schema}.{MAINTENANCE_TABLE} WHERE {' AND '.join(where)}",
                *args,
            )
        return not str(result or "").endswith(" 0")

    async def confirm_memory(
        self,
        *,
        scope: MemoryScope,
        memory_id: str,
        note: str = "confirmed",
        originator: str = "user",
        importance: float = 0.7,
        source: Optional[Dict[str, Any]] = None,
    ) -> Optional[MemoryRecord]:
        try:
            return await self.record_signal(
                scope=scope,
                match_memory_id=memory_id,
                require_match=True,
                signal=MemorySignal(
                    memory=note or "confirmed",
                    event_type="confirmation",
                    originator=originator,
                    confidence=0.9,
                    importance=importance,
                    visibility="internal",
                    source=source or {},
                ),
            )
        except ValueError:
            return None

    async def retire_memory(
        self,
        *,
        scope: MemoryScope,
        memory_id: str,
        reason: str = "retired",
        originator: str = "user",
        source: Optional[Dict[str, Any]] = None,
    ) -> Optional[MemoryRecord]:
        try:
            return await self.record_signal(
                scope=scope,
                match_memory_id=memory_id,
                require_match=True,
                signal=MemorySignal(
                    memory=reason or "retired",
                    event_type="retired",
                    originator=originator,
                    status="retired",
                    confidence=0.9,
                    importance=0.5,
                    visibility="internal",
                    source=source or {},
                ),
            )
        except ValueError:
            return None

    async def merge_memories(
        self,
        *,
        scope: MemoryScope,
        source_memory_id: str,
        target_memory_id: str,
        reason: str = "merged by memory reconciliation",
        originator: str = "user",
        source: Optional[Dict[str, Any]] = None,
        scope_filter: str = "current_bundle",
        ensure_schema: bool = False,
    ) -> Optional[Dict[str, MemoryRecord]]:
        """Mark one memory as merged into another and audit both rows.

        Reconciliation is proposal-driven, so this method is deliberately
        explicit: both records must belong to the same user scope and pass the
        requested scope filter. The source row becomes `merged` with
        `merged_into_id=target`, while the target row receives merge evidence.
        """

        if ensure_schema:
            await self.ensure_schema()
        source_memory_id = str(source_memory_id or "").strip()
        target_memory_id = str(target_memory_id or "").strip()
        if not source_memory_id or not target_memory_id or source_memory_id == target_memory_id:
            return None

        pool = self._require_pool()
        scope = scope.normalized()
        base_source = dict(source or {})
        normalized_scope_filter = normalize_scope_filter(scope_filter)
        async with pool.acquire() as con:
            async with con.transaction():
                source_row = await self._fetch_memory_for_update_scoped(
                    con,
                    scope=scope,
                    memory_id=source_memory_id,
                    visible_to_user=True,
                    scope_filter=normalized_scope_filter,
                )
                target_row = await self._fetch_memory_for_update_scoped(
                    con,
                    scope=scope,
                    memory_id=target_memory_id,
                    visible_to_user=True,
                    scope_filter=normalized_scope_filter,
                )
                if source_row is None or target_row is None:
                    return None

                now = _utc_now()
                source_scope = MemoryScope(scope.tenant, scope.project, scope.user_id, str(source_row.get("bundle_id") or ""))
                source_signal = self._normalize_signal(
                    source_scope,
                    MemorySignal(
                        memory=reason or f"merged into {target_memory_id}",
                        event_type="merged",
                        originator=originator,
                        status="merged",
                        visibility="internal",
                        confidence=0.95,
                        importance=0.5,
                        source={
                            **base_source,
                            "action": "memory_reconciliation_merge_source",
                            "source_memory_id": source_memory_id,
                            "target_memory_id": target_memory_id,
                        },
                    ),
                )
                await self._insert_event(con, scope=source_scope, row=source_row, signal=source_signal)
                source_updated = await con.fetchrow(
                    f"""
                    UPDATE {self.schema}.{MEMORY_TABLE}
                    SET status='merged',
                        tier=4,
                        merged_into_id=$2,
                        update_count=update_count + 1,
                        negative_weight=negative_weight + 0.25,
                        updated_at=$3,
                        last_event_at=$3,
                        source=$4::jsonb,
                        metadata=metadata || $5::jsonb,
                        revision=revision + 1
                    WHERE id=$1
                    RETURNING *
                    """,
                    source_row["id"],
                    target_row["id"],
                    now,
                    json.dumps(source_signal["source"]),
                    json.dumps({"merged_into_id": target_memory_id}),
                )

                target_scope = MemoryScope(scope.tenant, scope.project, scope.user_id, str(target_row.get("bundle_id") or ""))
                target_signal = self._normalize_signal(
                    target_scope,
                    MemorySignal(
                        memory=reason or f"merged from {source_memory_id}",
                        event_type="merge",
                        originator=originator,
                        status=str(target_row.get("status") or "active"),
                        visibility="internal",
                        confidence=0.95,
                        importance=0.6,
                        source={
                            **base_source,
                            "action": "memory_reconciliation_merge_target",
                            "source_memory_id": source_memory_id,
                            "target_memory_id": target_memory_id,
                        },
                    ),
                )
                target_updated = await self._append_event_and_update_scores(
                    con,
                    scope=target_scope,
                    row=target_row,
                    signal=target_signal,
                )

        if source_updated is None or target_updated is None:
            return None
        return {
            "source": self._record_from_row(dict(source_updated)),
            "target": self._record_from_row(dict(target_updated)),
        }

    async def squash_memories(
        self,
        *,
        scope: MemoryScope,
        source_memory_ids: Sequence[str],
        target_memory_id: str,
        merged_memory: str,
        merged_context: str = "",
        merged_kind: str = "fact",
        labels: Sequence[str] = (),
        keywords: Sequence[str] = (),
        confidence: float = 0.95,
        importance: float = 0.7,
        pinned: Optional[bool] = None,
        embedding: Optional[Sequence[float]] = None,
        embedding_model: str = "",
        reason: str = "squashed by memory reconciliation",
        originator: str = "user",
        source: Optional[Dict[str, Any]] = None,
        scope_filter: str = "current_bundle",
        ensure_schema: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Rewrite one target memory and merge many source memories into it.

        The target receives one `squash` event that names every source id. Each
        source receives its own `merged` event and is hidden from future memory
        search via `merged_into_id`.
        """

        if ensure_schema:
            await self.ensure_schema()
        scope = scope.normalized()
        target_memory_id = str(target_memory_id or "").strip()
        merged_memory = str(merged_memory or "").strip()
        if not target_memory_id or not merged_memory:
            return None
        normalized_sources: list[str] = []
        seen_sources: set[str] = set()
        for item in source_memory_ids:
            source_memory_id = str(item or "").strip()
            if not source_memory_id or source_memory_id == target_memory_id or source_memory_id in seen_sources:
                continue
            seen_sources.add(source_memory_id)
            normalized_sources.append(source_memory_id)
        if not normalized_sources:
            return None

        pool = self._require_pool()
        base_source = dict(source or {})
        normalized_scope_filter = normalize_scope_filter(scope_filter)
        async with pool.acquire() as con:
            async with con.transaction():
                target_row = await self._fetch_memory_for_update_scoped(
                    con,
                    scope=scope,
                    memory_id=target_memory_id,
                    visible_to_user=True,
                    scope_filter=normalized_scope_filter,
                )
                if target_row is None:
                    return None

                source_rows: list[Dict[str, Any]] = []
                skipped_sources: list[Dict[str, str]] = []
                for source_memory_id in normalized_sources:
                    source_row = await self._fetch_memory_for_update_scoped(
                        con,
                        scope=scope,
                        memory_id=source_memory_id,
                        visible_to_user=True,
                        scope_filter=normalized_scope_filter,
                    )
                    if source_row is None:
                        skipped_sources.append({"source_memory_id": source_memory_id, "reason": "source_not_found"})
                    else:
                        source_rows.append(source_row)
                if not source_rows:
                    return {
                        "target": self._record_from_row(target_row),
                        "sources": [],
                        "skipped_sources": skipped_sources,
                    }

                target_scope = MemoryScope(scope.tenant, scope.project, scope.user_id, str(target_row.get("bundle_id") or ""))
                target_signal = self._normalize_signal(
                    target_scope,
                    MemorySignal(
                        memory=merged_memory,
                        context=merged_context,
                        kind=merged_kind or str(target_row.get("kind") or "fact"),
                        event_type="squash",
                        originator=originator,
                        status=str(target_row.get("status") or "active"),
                        visibility=str(target_row.get("visibility") or "user"),
                        labels=labels,
                        keywords=keywords,
                        pinned=pinned,
                        confidence=confidence,
                        importance=importance,
                        embedding=embedding,
                        embedding_model=embedding_model,
                        source={
                            **base_source,
                            "action": "memory_reconciliation_squash_target",
                            "source_memory_ids": [str(row.get("id") or "") for row in source_rows],
                            "target_memory_id": target_memory_id,
                            "reason": reason,
                        },
                    ),
                )
                target_updated = await self._append_event_and_update_scores(
                    con,
                    scope=target_scope,
                    row=target_row,
                    signal=target_signal,
                )

                now = _utc_now()
                updated_sources: list[MemoryRecord] = []
                for source_row in source_rows:
                    source_memory_id = str(source_row.get("id") or "")
                    source_scope = MemoryScope(scope.tenant, scope.project, scope.user_id, str(source_row.get("bundle_id") or ""))
                    source_signal = self._normalize_signal(
                        source_scope,
                        MemorySignal(
                            memory=reason or f"squashed into {target_memory_id}",
                            event_type="merged",
                            originator=originator,
                            status="merged",
                            visibility="internal",
                            confidence=0.95,
                            importance=0.5,
                            source={
                                **base_source,
                                "action": "memory_reconciliation_squash_source",
                                "source_memory_id": source_memory_id,
                                "source_memory_ids": [str(row.get("id") or "") for row in source_rows],
                                "target_memory_id": target_memory_id,
                            },
                        ),
                    )
                    await self._insert_event(con, scope=source_scope, row=source_row, signal=source_signal)
                    source_updated = await con.fetchrow(
                        f"""
                        UPDATE {self.schema}.{MEMORY_TABLE}
                        SET status='merged',
                            tier=4,
                            merged_into_id=$2,
                            update_count=update_count + 1,
                            negative_weight=negative_weight + 0.25,
                            updated_at=$3,
                            last_event_at=$3,
                            source=$4::jsonb,
                            metadata=metadata || $5::jsonb,
                            revision=revision + 1
                        WHERE id=$1
                        RETURNING *
                        """,
                        source_memory_id,
                        str(target_updated.get("id") or target_memory_id),
                        now,
                        json.dumps(source_signal["source"]),
                        json.dumps({"merged_into_id": str(target_updated.get("id") or target_memory_id)}),
                    )
                    if source_updated is not None:
                        updated_sources.append(self._record_from_row(dict(source_updated)))

        return {
            "target": self._record_from_row(dict(target_updated)),
            "sources": updated_sources,
            "skipped_sources": skipped_sources,
        }

    async def update_status(
        self,
        *,
        scope: MemoryScope,
        memory_id: str,
        status: str,
        source: Optional[Dict[str, Any]] = None,
    ) -> Optional[MemoryRecord]:
        signal = MemorySignal(
            memory=f"status:{normalize_status(status)}",
            event_type="manual_update",
            status=status,
            visibility="internal",
            confidence=0.5,
            importance=0.5,
            source=source or {},
        )
        try:
            return await self.record_signal(scope=scope, signal=signal, match_memory_id=memory_id, require_match=True)
        except ValueError:
            return None

    async def edit_memory(
        self,
        *,
        scope: MemoryScope,
        memory_id: str,
        memory: str,
        context: str = "",
        kind: str = "fact",
        status: str = "active",
        visibility: str = "user",
        labels: Sequence[str] = (),
        keywords: Sequence[str] = (),
        confidence: float = 0.95,
        importance: float = 0.7,
        pinned: Optional[bool] = None,
        embedding: Optional[Sequence[float]] = None,
        embedding_model: str = "",
        source: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        visible_to_user: Optional[bool] = True,
        scope_filter: str = "current_bundle",
        ensure_schema: bool = False,
    ) -> Optional[MemoryRecord]:
        if ensure_schema:
            await self.ensure_schema()
        pool = self._require_pool()
        scope = scope.normalized()
        normalized_signal = self._normalize_signal(
            scope,
            MemorySignal(
                memory=memory,
                context=context,
                kind=kind,
                event_type="user_edit",
                originator="user",
                status=status,
                visibility=visibility,
                labels=labels,
                keywords=keywords,
                confidence=confidence,
                importance=importance,
                pinned=pinned,
                embedding=embedding,
                embedding_model=embedding_model,
                source=source or {},
                metadata=metadata or {},
            ),
        )
        async with pool.acquire() as con:
            async with con.transaction():
                row = await self._fetch_memory_for_update_scoped(
                    con,
                    scope=scope,
                    memory_id=memory_id,
                    visible_to_user=visible_to_user,
                    scope_filter=scope_filter,
                )
                if row is None:
                    return None
                event_inserted = await self._insert_event(con, scope=scope, row=row, signal=normalized_signal)
                if not event_inserted:
                    return self._record_from_row(row)

                positive_delta, negative_delta, confirmed = event_weight(
                    normalized_signal["event_type"],
                    confidence=normalized_signal["confidence"],
                    importance=normalized_signal["importance"],
                )
                now = _utc_now()
                positive = float(row.get("positive_weight") or 0.0) + positive_delta
                negative = float(row.get("negative_weight") or 0.0) + negative_delta
                evidence_count = int(row.get("evidence_count") or 0) + 1
                update_count = int(row.get("update_count") or 0) + 1
                confirmation_count = int(row.get("confirmation_count") or 0) + (1 if confirmed else 0)
                contradiction_count = int(row.get("contradiction_count") or 0) + (1 if negative_delta > 0 else 0)
                pinned_value = (
                    bool(normalized_signal["pinned"])
                    if normalized_signal.get("pinned") is not None
                    else bool(row.get("pinned"))
                )
                scores = compute_memory_scores(
                    status=normalized_signal["status"],
                    positive_weight=positive,
                    negative_weight=negative,
                    evidence_count=evidence_count,
                    confirmation_count=confirmation_count,
                    contradiction_count=contradiction_count,
                    update_count=update_count,
                    current_importance=float(row.get("importance_score") or 0.5),
                    signal_importance=normalized_signal["importance"],
                    last_event_at=now,
                    pinned=pinned_value,
                )
                updated = await con.fetchrow(
                    f"""
                    UPDATE {self.schema}.{MEMORY_TABLE}
                    SET memory=$2,
                        context=$3,
                        kind=$4,
                        status=$5,
                        visibility=$6,
                        visible_to_user=$7,
                        labels=$8::text[],
                        keywords=$9::text[],
                        pinned=$10,
                        search_text=$11,
                        embedding=COALESCE($12::vector, embedding),
                        embedding_model=COALESCE(NULLIF($13, ''), embedding_model),
                        evidence_count=$14,
                        update_count=$15,
                        confirmation_count=$16,
                        contradiction_count=$17,
                        positive_weight=$18,
                        negative_weight=$19,
                        confidence_score=$20,
                        importance_score=$21,
                        freshness_score=$22,
                        salience_score=$23,
                        confirmation_rate=$24,
                        tier=$25,
                        updated_at=$26,
                        last_event_at=$27,
                        last_confirmed_at=CASE WHEN $28 THEN $27 ELSE last_confirmed_at END,
                        revision=revision + 1
                    WHERE id=$1
                    RETURNING *
                    """,
                    row["id"],
                    normalized_signal["memory"],
                    normalized_signal["context"],
                    normalized_signal["kind"],
                    normalized_signal["status"],
                    normalized_signal["visibility"],
                    normalized_signal["visible_to_user"],
                    normalized_signal["labels"],
                    normalized_signal["keywords"],
                    pinned_value,
                    self._search_text(normalized_signal),
                    normalized_signal["embedding"],
                    normalized_signal["embedding_model"],
                    evidence_count,
                    update_count,
                    confirmation_count,
                    contradiction_count,
                    positive,
                    negative,
                    scores["confidence_score"],
                    scores["importance_score"],
                    scores["freshness_score"],
                    scores["salience_score"],
                    scores["confirmation_rate"],
                    scores["tier"],
                    now,
                    now,
                    confirmed,
                )
                await self._upsert_aliases(
                    con,
                    memory_id=str(row["id"]),
                    labels=normalized_signal["labels"],
                    keywords=normalized_signal["keywords"],
                )
        return self._record_from_row(dict(updated)) if updated else None

    async def restore_snapshot(
        self,
        *,
        scope: MemoryScope,
        snapshot_id: str,
        memories: Sequence[Dict[str, Any]],
        scope_filter: str = "current_bundle",
        retire_extra: bool = True,
        source: Optional[Dict[str, Any]] = None,
        ensure_schema: bool = False,
    ) -> Dict[str, Any]:
        """Restore user-visible aggregate memories from a snapshot payload.

        This is intentionally explicit and audit-oriented: it restores the
        aggregate memory rows from memories.json, records restore events, and
        optionally retires currently-visible rows in the same scope that are not
        present in the snapshot. It does not silently merge with unrelated rows.
        """

        if ensure_schema:
            await self.ensure_schema()
        pool = self._require_pool()
        scope = scope.normalized()
        normalized_scope_filter = normalize_scope_filter(scope_filter)
        snapshot_id = str(snapshot_id or "").strip()
        base_source = dict(source or {})
        now = _utc_now()
        restored = 0
        updated = 0
        inserted = 0
        retired = 0
        skipped: list[Dict[str, Any]] = []

        async with pool.acquire() as con:
            async with con.transaction():
                current_rows = await self._fetch_scope_rows_for_update(
                    con,
                    scope=scope,
                    scope_filter=normalized_scope_filter,
                    visible_to_user=True,
                )
                current_ids = {str(row.get("id") or "") for row in current_rows}
                snapshot_ids: set[str] = set()

                for memory in memories:
                    if not isinstance(memory, dict):
                        continue
                    memory_id = str(memory.get("id") or "").strip()
                    memory_text = str(memory.get("memory") or "").strip()
                    if not memory_id or not memory_text:
                        skipped.append({"id": memory_id, "reason": "missing_id_or_memory"})
                        continue
                    bundle_id = str(memory.get("bundle_id") or scope.bundle_id or "").strip()
                    if not self._bundle_matches_scope_filter(bundle_id, scope=scope, scope_filter=normalized_scope_filter):
                        skipped.append({"id": memory_id, "reason": "outside_scope"})
                        continue
                    snapshot_ids.add(memory_id)

                    labels = normalize_terms(memory.get("labels") or [])
                    keywords = normalize_terms(memory.get("keywords") or [])
                    kind = normalize_term(memory.get("kind") or "fact") or "fact"
                    status = normalize_status(memory.get("status") or "active")
                    visibility = normalize_visibility(memory.get("visibility") or "user")
                    visible_to_user = is_user_visible(visibility)
                    context = str(memory.get("context") or "").strip()
                    canonical_key = build_canonical_key(
                        user_id=scope.user_id,
                        kind=kind,
                        memory=memory_text,
                        labels=labels,
                        keywords=keywords,
                    )
                    conflict = await con.fetchrow(
                        f"""
                        SELECT id, status
                        FROM {self.schema}.{MEMORY_TABLE}
                        WHERE tenant=$1 AND project=$2 AND user_id=$3
                          AND canonical_key=$4
                          AND id<>$5
                          AND merged_into_id IS NULL
                        LIMIT 1
                        """,
                        scope.tenant,
                        scope.project,
                        scope.user_id,
                        canonical_key,
                        memory_id,
                    )
                    if conflict is not None:
                        conflict_row = dict(conflict)
                        conflict_id = str(conflict_row.get("id") or "")
                        if conflict_id:
                            snapshot_ids.add(conflict_id)
                        skipped.append({
                            "id": memory_id,
                            "reason": "canonical_conflict",
                            "conflict_id": conflict_id,
                            "conflict_status": str(conflict_row.get("status") or ""),
                        })
                        continue

                    evidence_count = max(0, int(memory.get("evidence_count") or 0))
                    update_count = max(0, int(memory.get("update_count") or 0))
                    confirmation_count = max(0, int(memory.get("confirmation_count") or 0))
                    contradiction_count = max(0, int(memory.get("contradiction_count") or 0))
                    positive_weight = max(0.0, float(evidence_count - contradiction_count))
                    negative_weight = max(0.0, float(contradiction_count))
                    embedding = memory.get("embedding")
                    if not isinstance(embedding, (list, tuple)):
                        embedding = None
                    embedding_model = str(memory.get("embedding_model") or "")
                    row_source = dict(base_source)
                    row_source.update({
                        "action": "snapshot_restore",
                        "snapshot_id": snapshot_id,
                        "bundle_id": bundle_id,
                    })
                    row_metadata = {"restored_from_snapshot": snapshot_id}
                    existing_before = memory_id in current_ids
                    row = await con.fetchrow(
                        f"""
                        INSERT INTO {self.schema}.{MEMORY_TABLE} (
                            id, tenant, project, user_id, bundle_id, canonical_key, memory, context,
                            kind, status, visibility, visible_to_user, labels, keywords, pinned, search_text,
                            evidence_count, update_count, confirmation_count, contradiction_count,
                            positive_weight, negative_weight, confidence_score, importance_score,
                            freshness_score, salience_score, confirmation_rate, tier,
                            created_at, updated_at, last_event_at, last_confirmed_at,
                            source, metadata, revision, embedding, embedding_model
                        )
                        VALUES (
                            $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::text[],$14::text[],$15,$16,
                            $17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31,$32,$33::jsonb,$34::jsonb,$35
                            ,$36::vector,$37
                        )
                        ON CONFLICT (id) DO UPDATE
                        SET bundle_id=$5,
                            canonical_key=$6,
                            memory=$7,
                            context=$8,
                            kind=$9,
                            status=$10,
                            visibility=$11,
                            visible_to_user=$12,
                            labels=$13::text[],
                            keywords=$14::text[],
                            pinned=$15,
                            search_text=$16,
                            evidence_count=$17,
                            update_count=$18,
                            confirmation_count=$19,
                            contradiction_count=$20,
                            positive_weight=$21,
                            negative_weight=$22,
                            confidence_score=$23,
                            importance_score=$24,
                            freshness_score=$25,
                            salience_score=$26,
                            confirmation_rate=$27,
                            tier=$28,
                            updated_at=$30,
                            last_event_at=$31,
                            last_confirmed_at=$32,
                            source=$33::jsonb,
                            metadata=$34::jsonb,
                            revision=EXCLUDED.revision + 1,
                            embedding=$36::vector,
                            embedding_model=$37,
                            merged_into_id=NULL
                        RETURNING *
                        """,
                        memory_id,
                        scope.tenant,
                        scope.project,
                        scope.user_id,
                        bundle_id,
                        canonical_key,
                        memory_text,
                        context,
                        kind,
                        status,
                        visibility,
                        visible_to_user,
                        labels,
                        keywords,
                        bool(memory.get("pinned")),
                        self._search_text({"memory": memory_text, "context": context, "labels": labels, "keywords": keywords}),
                        evidence_count,
                        update_count,
                        confirmation_count,
                        contradiction_count,
                        positive_weight,
                        negative_weight,
                        max(0.0, min(1.0, float(memory.get("confidence_score") or 0.5))),
                        max(0.0, min(1.0, float(memory.get("importance_score") or 0.5))),
                        max(0.0, min(1.0, float(memory.get("freshness_score") or 1.0))),
                        max(0.0, min(1.0, float(memory.get("salience_score") or 0.5))),
                        max(0.0, min(1.0, float(memory.get("confirmation_rate") or 0.0))),
                        max(1, min(4, int(memory.get("tier") or 3))),
                        _coerce_datetime(memory.get("created_at"), default=now),
                        now,
                        now,
                        None,
                        json.dumps(row_source),
                        json.dumps(row_metadata),
                        max(1, int(memory.get("revision") or 1)),
                        _embedding_vector(embedding),
                        embedding_model,
                    )
                    if row is None:
                        skipped.append({"id": memory_id, "reason": "upsert_failed"})
                        continue
                    row_dict = dict(row)
                    row_scope = MemoryScope(scope.tenant, scope.project, scope.user_id, bundle_id)
                    await self._insert_event(
                        con,
                        scope=row_scope,
                        row=row_dict,
                        signal={
                            "memory": f"restored from snapshot {snapshot_id}",
                            "context": "",
                            "event_type": "snapshot_restore",
                            "originator": "user",
                            "confidence": 1.0,
                            "importance": 0.5,
                            "labels": labels,
                            "keywords": keywords,
                            "source": row_source,
                            "metadata": row_metadata,
                            "conversation_id": str(row_source.get("conversation_id") or ""),
                            "turn_id": str(row_source.get("turn_id") or ""),
                            "idempotency_key": f"snapshot_restore:{snapshot_id}:{memory_id}",
                        },
                    )
                    await self._upsert_aliases(con, memory_id=memory_id, labels=labels, keywords=keywords)
                    restored += 1
                    if existing_before:
                        updated += 1
                    else:
                        inserted += 1

                if retire_extra:
                    for row in current_rows:
                        memory_id = str(row.get("id") or "")
                        if memory_id in snapshot_ids or str(row.get("status") or "") == "retired":
                            continue
                        row_scope = MemoryScope(scope.tenant, scope.project, scope.user_id, str(row.get("bundle_id") or ""))
                        retire_source = dict(base_source)
                        retire_source.update({
                            "action": "snapshot_restore_retire_extra",
                            "snapshot_id": snapshot_id,
                            "bundle_id": row_scope.bundle_id,
                        })
                        updated_row = await con.fetchrow(
                            f"""
                            UPDATE {self.schema}.{MEMORY_TABLE}
                            SET status='retired',
                                tier=4,
                                updated_at=$2,
                                last_event_at=$2,
                                source=$3::jsonb,
                                metadata=metadata || $4::jsonb,
                                revision=revision + 1
                            WHERE id=$1
                            RETURNING *
                            """,
                            memory_id,
                            now,
                            json.dumps(retire_source),
                            json.dumps({"retired_by_snapshot_restore": snapshot_id}),
                        )
                        if updated_row is None:
                            continue
                        await self._insert_event(
                            con,
                            scope=row_scope,
                            row=dict(updated_row),
                            signal={
                                "memory": f"retired by restore to snapshot {snapshot_id}",
                                "context": "",
                                "event_type": "snapshot_restore_retire_extra",
                                "originator": "user",
                                "confidence": 1.0,
                                "importance": 0.5,
                                "labels": _array(row.get("labels")),
                                "keywords": _array(row.get("keywords")),
                                "source": retire_source,
                                "metadata": {"retired_by_snapshot_restore": snapshot_id},
                                "conversation_id": str(retire_source.get("conversation_id") or ""),
                                "turn_id": str(retire_source.get("turn_id") or ""),
                                "idempotency_key": f"snapshot_restore_retire_extra:{snapshot_id}:{memory_id}",
                            },
                        )
                        retired += 1

        return {
            "restored": restored,
            "updated": updated,
            "inserted": inserted,
            "retired_extra": retired,
            "skipped": skipped,
            "skipped_count": len(skipped),
            "retire_extra": retire_extra,
            "snapshot_id": snapshot_id,
        }

    async def _fetch_candidates(self, request: MemorySearchRequest) -> list[Dict[str, Any]]:
        pool = self._require_pool()
        scope = request.scope.normalized()
        args: list[Any] = [scope.tenant, scope.project, scope.user_id]
        where = ["tenant=$1", "project=$2", "user_id=$3", "merged_into_id IS NULL"]
        status = normalize_term(request.status)
        if status and status != "any":
            args.append(status)
            where.append(f"status=${len(args)}")
        if request.kind:
            args.append(normalize_term(request.kind))
            where.append(f"kind=${len(args)}")
        if request.visible_to_user is not None:
            args.append(bool(request.visible_to_user))
            where.append(f"visible_to_user=${len(args)}")
        elif not request.include_private:
            where.append("visible_to_user=TRUE")
        self._append_scope_filter(
            where=where,
            args=args,
            scope=scope,
            scope_filter=str(request.scope_filter or "current_bundle"),
            table_alias="",
        )
        labels = normalize_terms(request.labels)
        if labels:
            args.append(labels)
            where.append(f"labels && ${len(args)}::text[]")
        keywords = normalize_terms(request.keywords)
        if keywords:
            args.append(keywords)
            where.append(f"keywords && ${len(args)}::text[]")

        text_rank = "0.0"
        semantic_score = "0.0"
        query = str(request.query or "").strip()
        lexical_tsquery = _lexical_tsquery(query)
        if query and request.mode == "hybrid" and request.query_embedding is None and lexical_tsquery:
            args.append(lexical_tsquery)
            qpos = len(args)
            text_rank = f"ts_rank(to_tsvector('english', search_text), to_tsquery('english', ${qpos}))"
            where.append(f"to_tsvector('english', search_text) @@ to_tsquery('english', ${qpos})")
        elif query and lexical_tsquery:
            args.append(lexical_tsquery)
            qpos = len(args)
            text_rank = f"ts_rank(to_tsvector('english', search_text), to_tsquery('english', ${qpos}))"
        if request.query_embedding is not None:
            args.append(_embedding_vector(request.query_embedding))
            epos = len(args)
            semantic_score = f"CASE WHEN embedding IS NULL THEN 0.0 ELSE 1 - (embedding <=> ${epos}::vector) END"

        offset = max(0, int(request.offset or 0))
        requested_limit = max(1, int(request.limit or 20))
        min_candidate_limit = offset + requested_limit
        limit = max(1, min(max(int(request.candidate_limit or min_candidate_limit), min_candidate_limit), 1000))
        order_by = self._order_by_for_mode(request.mode)
        if request.mode == "hybrid" and request.query_embedding is not None:
            order_by = "semantic_score DESC, salience_score DESC, updated_at DESC"
        elif request.mode == "hybrid" and lexical_tsquery:
            order_by = "text_rank DESC, salience_score DESC, updated_at DESC"
        if request.mode == "hybrid" and request.query_embedding is not None and lexical_tsquery:
            sql = f"""
                WITH base AS (
                    SELECT *, {text_rank} AS text_rank, {semantic_score} AS semantic_score
                    FROM {self.schema}.{MEMORY_TABLE}
                    WHERE {' AND '.join(where)}
                ),
                lexical AS (
                    SELECT *
                    FROM base
                    WHERE to_tsvector('english', search_text) @@ to_tsquery('english', ${qpos})
                    ORDER BY text_rank DESC, salience_score DESC, updated_at DESC
                    LIMIT {limit}
                ),
                semantic AS (
                    SELECT *
                    FROM base
                    WHERE embedding IS NOT NULL
                    ORDER BY semantic_score DESC, salience_score DESC, updated_at DESC
                    LIMIT {limit}
                ),
                combined AS (
                    SELECT * FROM lexical
                    UNION ALL
                    SELECT * FROM semantic
                )
                SELECT DISTINCT ON (id) *
                FROM combined
                ORDER BY id, semantic_score DESC, text_rank DESC, salience_score DESC, updated_at DESC
            """
        else:
            sql = f"""
                SELECT *, {text_rank} AS text_rank, {semantic_score} AS semantic_score
                FROM {self.schema}.{MEMORY_TABLE}
                WHERE {' AND '.join(where)}
                ORDER BY {order_by}
                LIMIT {limit}
            """
        async with pool.acquire() as con:
            rows = await con.fetch(sql, *args)
        return [dict(row) for row in rows]

    async def _search_recent_events(self, request: MemorySearchRequest) -> list[MemoryEvent]:
        pool = self._require_pool()
        scope = request.scope.normalized()
        args: list[Any] = [scope.tenant, scope.project, scope.user_id]
        where = ["e.tenant=$1", "e.project=$2", "e.user_id=$3"]
        if request.visible_to_user is not None:
            args.append(bool(request.visible_to_user))
            where.append(f"m.visible_to_user=${len(args)}")
        elif not request.include_private:
            where.append("m.visible_to_user=TRUE")
        self._append_scope_filter(
            where=where,
            args=args,
            scope=scope,
            scope_filter=str(request.scope_filter or "current_bundle"),
            table_alias="m",
        )
        limit = max(1, min(int(request.limit or 20), 200))
        sql = f"""
            SELECT e.*
            FROM {self.schema}.{EVENT_TABLE} e
            JOIN {self.schema}.{MEMORY_TABLE} m ON m.id = e.memory_id
            WHERE {' AND '.join(where)}
            ORDER BY e.created_at DESC
            LIMIT {limit}
        """
        async with pool.acquire() as con:
            rows = await con.fetch(sql, *args)
        return [self._event_from_row(dict(row)) for row in rows]

    def _append_scope_filter(
        self,
        *,
        where: list[str],
        args: list[Any],
        scope: MemoryScope,
        scope_filter: str,
        table_alias: str = "",
    ) -> None:
        prefix = f"{table_alias}." if table_alias else ""
        normalized = normalize_scope_filter(scope_filter)
        if normalized == "all_user_memories":
            return
        if normalized == "global_only":
            where.append(f"{prefix}bundle_id=''")
            return
        if normalized == "current_bundle_or_global":
            args.append(scope.bundle_id)
            where.append(f"({prefix}bundle_id=${len(args)} OR {prefix}bundle_id='')")
            return
        args.append(scope.bundle_id)
        where.append(f"{prefix}bundle_id=${len(args)}")

    def _bundle_matches_scope_filter(self, bundle_id: str, *, scope: MemoryScope, scope_filter: str) -> bool:
        normalized = normalize_scope_filter(scope_filter)
        bundle_id = str(bundle_id or "")
        if normalized == "all_user_memories":
            return True
        if normalized == "global_only":
            return bundle_id == ""
        if normalized == "current_bundle_or_global":
            return bundle_id in {scope.bundle_id, ""}
        return bundle_id == scope.bundle_id

    async def _fetch_scope_rows_for_update(
        self,
        con: Any,
        *,
        scope: MemoryScope,
        scope_filter: str,
        visible_to_user: Optional[bool] = None,
    ) -> list[Dict[str, Any]]:
        args: list[Any] = [scope.tenant, scope.project, scope.user_id]
        where = ["tenant=$1", "project=$2", "user_id=$3", "merged_into_id IS NULL"]
        if visible_to_user is not None:
            args.append(bool(visible_to_user))
            where.append(f"visible_to_user=${len(args)}")
        self._append_scope_filter(where=where, args=args, scope=scope, scope_filter=scope_filter, table_alias="")
        rows = await con.fetch(
            f"""
            SELECT *
            FROM {self.schema}.{MEMORY_TABLE}
            WHERE {' AND '.join(where)}
            FOR UPDATE
            """,
            *args,
        )
        return [dict(row) for row in rows]

    def _order_by_for_mode(self, mode: str) -> str:
        if mode == "recent":
            return "updated_at DESC"
        if mode == "recent_created":
            return "created_at DESC"
        if mode == "important":
            return "importance_score DESC, salience_score DESC, updated_at DESC"
        if mode == "confirmed":
            return "confirmation_rate DESC, confirmation_count DESC, updated_at DESC"
        if mode == "hotset":
            return "tier ASC, pinned DESC, salience_score DESC, importance_score DESC, updated_at DESC"
        return "salience_score DESC, updated_at DESC"

    def _normalize_signal(self, scope: MemoryScope, signal: MemorySignal) -> dict[str, Any]:
        memory = str(signal.memory or "").strip()
        if not memory:
            raise ValueError("memory signal requires non-empty memory")
        labels = normalize_terms(signal.labels)
        keywords = normalize_terms(signal.keywords)
        canonical_key_supplied = bool(str(signal.canonical_key or "").strip())
        canonical_key = str(signal.canonical_key or "").strip() or build_canonical_key(
            user_id=scope.user_id,
            kind=signal.kind,
            memory=memory,
            labels=labels,
            keywords=keywords,
        )
        visibility = normalize_visibility(signal.visibility)
        source = dict(signal.source or {})
        source.setdefault("bundle_id", scope.bundle_id)
        return {
            "memory": memory,
            "context": str(signal.context or "").strip(),
            "kind": normalize_term(signal.kind or "fact") or "fact",
            "event_type": normalize_term(signal.event_type or "agent_observation").replace(" ", "_"),
            "originator": normalize_term(signal.originator or "agent") or "agent",
            "status": normalize_status(signal.status),
            "visibility": visibility,
            "visible_to_user": is_user_visible(visibility),
            "labels": labels,
            "keywords": keywords,
            "pinned": signal.pinned if signal.pinned is not None else None,
            "confidence": max(0.0, min(1.0, float(signal.confidence))),
            "importance": max(0.0, min(1.0, float(signal.importance))),
            "canonical_key": canonical_key,
            "canonical_key_supplied": canonical_key_supplied,
            "embedding": _embedding_vector(signal.embedding),
            "embedding_model": str(signal.embedding_model or ""),
            "source": source,
            "metadata": dict(signal.metadata or {}),
            "conversation_id": str(source.get("conversation_id") or ""),
            "turn_id": str(source.get("turn_id") or ""),
            "idempotency_key": str(source.get("idempotency_key") or signal.metadata.get("idempotency_key") or "").strip(),
        }

    async def _fetch_memory_for_update(self, con: Any, *, scope: MemoryScope, memory_id: str) -> Optional[Dict[str, Any]]:
        row = await con.fetchrow(
            f"""
            SELECT *
            FROM {self.schema}.{MEMORY_TABLE}
            WHERE tenant=$1 AND project=$2 AND user_id=$3 AND id=$4 AND merged_into_id IS NULL
            FOR UPDATE
            """,
            scope.tenant,
            scope.project,
            scope.user_id,
            memory_id,
        )
        return dict(row) if row else None

    async def _fetch_memory_for_update_scoped(
        self,
        con: Any,
        *,
        scope: MemoryScope,
        memory_id: str,
        visible_to_user: Optional[bool] = None,
        scope_filter: str = "current_bundle",
    ) -> Optional[Dict[str, Any]]:
        args: list[Any] = [scope.tenant, scope.project, scope.user_id, str(memory_id or "")]
        where = ["tenant=$1", "project=$2", "user_id=$3", "id=$4", "merged_into_id IS NULL"]
        if visible_to_user is not None:
            args.append(bool(visible_to_user))
            where.append(f"visible_to_user=${len(args)}")
        self._append_scope_filter(where=where, args=args, scope=scope, scope_filter=scope_filter, table_alias="")
        row = await con.fetchrow(
            f"""
            SELECT *
            FROM {self.schema}.{MEMORY_TABLE}
            WHERE {' AND '.join(where)}
            FOR UPDATE
            """,
            *args,
        )
        return dict(row) if row else None

    async def _fetch_by_canonical_key_for_update(
        self,
        con: Any,
        *,
        scope: MemoryScope,
        canonical_key: str,
        statuses: Optional[Sequence[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        args: list[Any] = [scope.tenant, scope.project, scope.user_id, canonical_key]
        where = ["tenant=$1", "project=$2", "user_id=$3", "canonical_key=$4", "merged_into_id IS NULL"]
        if statuses is not None:
            normalized_statuses = [normalize_status(status) for status in statuses]
            args.append(normalized_statuses)
            where.append(f"status = ANY(${len(args)}::text[])")
        row = await con.fetchrow(
            f"""
            SELECT *
            FROM {self.schema}.{MEMORY_TABLE}
            WHERE {' AND '.join(where)}
            FOR UPDATE
            """,
            *args,
        )
        return dict(row) if row else None

    async def _find_merge_candidate_for_update(
        self,
        con: Any,
        *,
        scope: MemoryScope,
        signal: Dict[str, Any],
        threshold: float,
    ) -> Optional[Dict[str, Any]]:
        rows = await con.fetch(
            f"""
            SELECT *, 0.0 AS text_rank
            FROM {self.schema}.{MEMORY_TABLE}
            WHERE tenant=$1 AND project=$2 AND user_id=$3
              AND kind=$4
              AND status IN ('active', 'weakened')
              AND merged_into_id IS NULL
              AND (labels && $5::text[] OR keywords && $6::text[] OR search_text ILIKE '%' || $7 || '%')
            ORDER BY updated_at DESC
            LIMIT 50
            """,
            scope.tenant,
            scope.project,
            scope.user_id,
            signal["kind"],
            signal["labels"],
            signal["keywords"],
            signal["memory"][:120],
        )
        best: Optional[Dict[str, Any]] = None
        best_score = 0.0
        for raw in rows:
            row = dict(raw)
            score, _breakdown = rank_candidate(
                query=signal["memory"],
                query_embedding=None,
                requested_labels=signal["labels"],
                requested_keywords=signal["keywords"],
                row=row,
            )
            if score > best_score:
                best_score = score
                best = row
        if best is None or best_score < threshold:
            return None
        locked = await self._fetch_memory_for_update(con, scope=scope, memory_id=str(best["id"]))
        return locked

    async def _insert_memory(self, con: Any, *, scope: MemoryScope, signal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        positive, negative, confirmed = event_weight(
            signal["event_type"],
            confidence=signal["confidence"],
            importance=signal["importance"],
        )
        now = _utc_now()
        scores = compute_memory_scores(
            status=signal["status"],
            positive_weight=positive,
            negative_weight=negative,
            evidence_count=1,
            confirmation_count=1 if confirmed else 0,
            contradiction_count=1 if negative > 0 else 0,
            update_count=1,
            current_importance=signal["importance"],
            signal_importance=signal["importance"],
            last_event_at=now,
            pinned=bool(signal.get("pinned")),
        )
        memory_id = f"mem_{uuid.uuid4().hex}"
        row = await con.fetchrow(
            f"""
            INSERT INTO {self.schema}.{MEMORY_TABLE} (
                id, tenant, project, user_id, bundle_id, canonical_key, memory, context,
                kind, status, visibility, visible_to_user, labels, keywords, pinned, search_text,
                embedding, embedding_model, evidence_count, update_count,
                confirmation_count, contradiction_count, positive_weight, negative_weight,
                confidence_score, importance_score, freshness_score, salience_score,
                confirmation_rate, tier, created_at, updated_at, last_event_at,
                last_confirmed_at, source, metadata, revision
            )
            VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::text[],$14::text[],$15,$16,
                $17::vector,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31,$32,$33,$34,$35::jsonb,$36::jsonb,$37
            )
            ON CONFLICT (tenant, project, user_id, canonical_key)
            WHERE merged_into_id IS NULL
            DO NOTHING
            RETURNING *
            """,
            memory_id,
            scope.tenant,
            scope.project,
            scope.user_id,
            scope.bundle_id,
            signal["canonical_key"],
            signal["memory"],
            signal["context"],
            signal["kind"],
            signal["status"],
            signal["visibility"],
            signal["visible_to_user"],
            signal["labels"],
            signal["keywords"],
            bool(signal.get("pinned")),
            self._search_text(signal),
            signal["embedding"],
            signal["embedding_model"],
            1,
            1,
            1 if confirmed else 0,
            1 if negative > 0 else 0,
            positive,
            negative,
            scores["confidence_score"],
            scores["importance_score"],
            scores["freshness_score"],
            scores["salience_score"],
            scores["confirmation_rate"],
            scores["tier"],
            now,
            now,
            now,
            now if confirmed else None,
            json.dumps(signal["source"]),
            json.dumps(signal["metadata"]),
            1,
        )
        if row is None:
            return None
        inserted = dict(row)
        await self._insert_event(con, scope=scope, row=inserted, signal=signal)
        await self._upsert_aliases(con, memory_id=memory_id, labels=signal["labels"], keywords=signal["keywords"])
        return inserted

    async def _append_event_and_update_scores(
        self,
        con: Any,
        *,
        scope: MemoryScope,
        row: Dict[str, Any],
        signal: Dict[str, Any],
    ) -> Dict[str, Any]:
        event_inserted = await self._insert_event(con, scope=scope, row=row, signal=signal)
        if not event_inserted:
            return row
        positive_delta, negative_delta, confirmed = event_weight(
            signal["event_type"],
            confidence=signal["confidence"],
            importance=signal["importance"],
        )
        now = _utc_now()
        positive = float(row.get("positive_weight") or 0.0) + positive_delta
        negative = float(row.get("negative_weight") or 0.0) + negative_delta
        evidence_count = int(row.get("evidence_count") or 0) + 1
        update_count = int(row.get("update_count") or 0) + 1
        confirmation_count = int(row.get("confirmation_count") or 0) + (1 if confirmed else 0)
        contradiction_count = int(row.get("contradiction_count") or 0) + (1 if negative_delta > 0 else 0)
        if signal["event_type"] in {"manual_update", "user_edit"}:
            status = signal["status"]
        else:
            status = signal["status"] if signal["status"] != "active" or str(row.get("status") or "") == "active" else row["status"]
        pinned = bool(signal["pinned"]) if signal.get("pinned") is not None else bool(row.get("pinned"))
        scores = compute_memory_scores(
            status=status,
            positive_weight=positive,
            negative_weight=negative,
            evidence_count=evidence_count,
            confirmation_count=confirmation_count,
            contradiction_count=contradiction_count,
            update_count=update_count,
            current_importance=float(row.get("importance_score") or 0.5),
            signal_importance=signal["importance"],
            last_event_at=now,
            pinned=pinned,
        )
        labels = sorted(set(_array(row.get("labels")) + signal["labels"]))
        keywords = sorted(set(_array(row.get("keywords")) + signal["keywords"]))
        memory_text = signal["memory"] if signal["event_type"] in {"user_edit", "manual_update", "refinement", "squash"} else row["memory"]
        context = signal["context"] or row.get("context") or ""
        updated = await con.fetchrow(
            f"""
            UPDATE {self.schema}.{MEMORY_TABLE}
            SET memory=$2,
                context=$3,
                status=$4,
                visibility=$5,
                visible_to_user=$6,
                labels=$7::text[],
                keywords=$8::text[],
                pinned=$9,
                search_text=$10,
                embedding=COALESCE($11::vector, embedding),
                embedding_model=COALESCE(NULLIF($12, ''), embedding_model),
                evidence_count=$13,
                update_count=$14,
                confirmation_count=$15,
                contradiction_count=$16,
                positive_weight=$17,
                negative_weight=$18,
                confidence_score=$19,
                importance_score=$20,
                freshness_score=$21,
                salience_score=$22,
                confirmation_rate=$23,
                tier=$24,
                updated_at=$25,
                last_event_at=$26,
                last_confirmed_at=CASE WHEN $27 THEN $26 ELSE last_confirmed_at END,
                revision=revision + 1
            WHERE id=$1
            RETURNING *
            """,
            row["id"],
            memory_text,
            context,
            status,
            signal["visibility"] if signal["visible_to_user"] else row["visibility"],
            bool(row.get("visible_to_user")) or bool(signal["visible_to_user"]),
            labels,
            keywords,
            pinned,
            self._search_text({**signal, "memory": memory_text, "context": context, "labels": labels, "keywords": keywords}),
            signal["embedding"],
            signal["embedding_model"],
            evidence_count,
            update_count,
            confirmation_count,
            contradiction_count,
            positive,
            negative,
            scores["confidence_score"],
            scores["importance_score"],
            scores["freshness_score"],
            scores["salience_score"],
            scores["confirmation_rate"],
            scores["tier"],
            now,
            now,
            confirmed,
        )
        await self._upsert_aliases(con, memory_id=str(row["id"]), labels=labels, keywords=keywords)
        return dict(updated)

    async def _insert_event(self, con: Any, *, scope: MemoryScope, row: Dict[str, Any], signal: Dict[str, Any]) -> bool:
        idempotency_key = str(signal.get("idempotency_key") or "").strip()
        inserted = await con.fetchrow(
            f"""
            INSERT INTO {self.schema}.{EVENT_TABLE} (
                id, memory_id, tenant, project, user_id, bundle_id, conversation_id, turn_id,
                event_type, signal_text, context, originator, confidence, importance,
                labels, keywords, source, metadata, idempotency_key, created_at
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::text[],$16::text[],$17::jsonb,$18::jsonb,$19,$20)
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            f"mev_{uuid.uuid4().hex}",
            row["id"],
            scope.tenant,
            scope.project,
            scope.user_id,
            scope.bundle_id,
            signal["conversation_id"],
            signal["turn_id"],
            signal["event_type"],
            signal["memory"],
            signal["context"],
            signal["originator"],
            signal["confidence"],
            signal["importance"],
            signal["labels"],
            signal["keywords"],
            json.dumps(signal["source"]),
            json.dumps(signal["metadata"]),
            idempotency_key,
            _utc_now(),
        )
        return inserted is not None

    async def _upsert_aliases(self, con: Any, *, memory_id: str, labels: Iterable[str], keywords: Iterable[str]) -> None:
        for label in normalize_terms(labels):
            await con.execute(
                f"""
                INSERT INTO {self.schema}.{ALIAS_TABLE} (memory_id, alias_type, value, weight)
                VALUES ($1, 'label', $2, 1.0)
                ON CONFLICT (memory_id, alias_type, value) DO NOTHING
                """,
                memory_id,
                label,
            )
        for keyword in normalize_terms(keywords):
            await con.execute(
                f"""
                INSERT INTO {self.schema}.{ALIAS_TABLE} (memory_id, alias_type, value, weight)
                VALUES ($1, 'keyword', $2, 0.8)
                ON CONFLICT (memory_id, alias_type, value) DO NOTHING
                """,
                memory_id,
                keyword,
            )

    def _search_text(self, signal: Dict[str, Any]) -> str:
        parts = [
            signal.get("memory") or "",
            signal.get("context") or "",
            " ".join(signal.get("labels") or []),
            " ".join(signal.get("keywords") or []),
        ]
        return "\n".join(str(part) for part in parts if str(part).strip())

    def _record_from_row(self, row: Dict[str, Any]) -> MemoryRecord:
        scope = MemoryScope(
            tenant=str(row.get("tenant") or ""),
            project=str(row.get("project") or ""),
            user_id=str(row.get("user_id") or ""),
            bundle_id=str(row.get("bundle_id") or ""),
        )
        return MemoryRecord(
            id=str(row["id"]),
            scope=scope,
            memory=str(row.get("memory") or ""),
            context=str(row.get("context") or ""),
            kind=str(row.get("kind") or "fact"),
            status=str(row.get("status") or "active"),
            visibility=str(row.get("visibility") or "user"),
            labels=_array(row.get("labels")),
            keywords=_array(row.get("keywords")),
            tier=int(row.get("tier") or 3),
            pinned=bool(row.get("pinned")),
            confidence_score=float(row.get("confidence_score") or 0.0),
            importance_score=float(row.get("importance_score") or 0.0),
            freshness_score=float(row.get("freshness_score") or 0.0),
            salience_score=float(row.get("salience_score") or 0.0),
            confirmation_rate=float(row.get("confirmation_rate") or 0.0),
            evidence_count=int(row.get("evidence_count") or 0),
            update_count=int(row.get("update_count") or 0),
            confirmation_count=int(row.get("confirmation_count") or 0),
            contradiction_count=int(row.get("contradiction_count") or 0),
            created_at=row.get("created_at") or _utc_now(),
            updated_at=row.get("updated_at") or _utc_now(),
            last_event_at=row.get("last_event_at") or _utc_now(),
            last_confirmed_at=row.get("last_confirmed_at"),
            source=_json(row.get("source")),
            metadata=_json(row.get("metadata")),
            revision=int(row.get("revision") or 1),
        )

    def _event_from_row(self, row: Dict[str, Any]) -> MemoryEvent:
        return MemoryEvent(
            id=str(row["id"]),
            memory_id=str(row["memory_id"]),
            scope=MemoryScope(
                tenant=str(row.get("tenant") or ""),
                project=str(row.get("project") or ""),
                user_id=str(row.get("user_id") or ""),
                bundle_id=str(row.get("bundle_id") or ""),
            ),
            event_type=str(row.get("event_type") or ""),
            signal_text=str(row.get("signal_text") or ""),
            context=str(row.get("context") or ""),
            originator=str(row.get("originator") or ""),
            confidence=float(row.get("confidence") or 0.0),
            importance=float(row.get("importance") or 0.0),
            labels=_array(row.get("labels")),
            keywords=_array(row.get("keywords")),
            created_at=row.get("created_at") or _utc_now(),
            source=_json(row.get("source")),
            metadata=_json(row.get("metadata")),
        )

    def _maintenance_artifact_from_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        summary = _json(row.get("summary"))
        if not isinstance(summary, dict):
            summary = {}
        artifacts = _json(row.get("artifacts"))
        if not isinstance(artifacts, dict):
            artifacts = {}

        artifact_type = str(row.get("artifact_type") or "")
        artifact_id = str(row.get("artifact_id") or "")
        storage_bundle_id = str(row.get("storage_bundle_id") or "")
        created_at = row.get("created_at")
        updated_at = row.get("updated_at")

        payload = dict(summary)
        payload.setdefault("artifact_type", artifact_type)
        payload.setdefault("artifact_id", artifact_id)
        if artifact_type == "snapshot":
            payload.setdefault("snapshot_id", artifact_id)
        elif artifact_type == "reconciliation_job":
            payload.setdefault("job_id", artifact_id)
        payload.setdefault("status", str(row.get("status") or ""))
        payload.setdefault("scope_filter", str(row.get("scope_filter") or "current_bundle"))
        payload.setdefault(
            "scope",
            {
                "tenant": str(row.get("tenant") or ""),
                "project": str(row.get("project") or ""),
                "user_id": str(row.get("user_id") or ""),
                "bundle_id": str(row.get("bundle_id") or ""),
            },
        )
        payload["artifacts"] = artifacts
        payload["storage_bundle_id"] = storage_bundle_id
        if created_at is not None:
            payload.setdefault("created_at", created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at))
        if updated_at is not None:
            payload.setdefault("updated_at", updated_at.isoformat() if hasattr(updated_at, "isoformat") else str(updated_at))
        return payload
