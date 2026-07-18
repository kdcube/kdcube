# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# ── pg_target.py ── the storage-edge injection point (tenant/project schema) ──
#
# This is where each preserved agent's OWN store stops being single-machine and
# becomes hosted-at-scale — the non-hosted → hosted STORAGE transition.
#
# Each standalone agent keeps all its mutable state in its OWN Postgres
# (`DATABASE_URL`): lg-solution's per-user pgvector `memories` + seeded KB, and
# each agent's LangGraph checkpointer (per-conversation graph state). Hosted by
# KDCube, that same store is routed onto KDCube's SHARED Postgres (`self.pg_pool`'s
# database), into the ONE per-tenant/project schema `kdcube_{tenant}_{project}`
# that `conv_index` / `UserMemoryStore` already use. Every mutable byte then lives
# in shared Postgres, tagged by SCOPE COLUMNS — so the hosted app holds nothing
# per-turn in-process and any processor worker can serve any turn (stateless,
# distributed-safe).
#
# SCHEMA MODEL (the canonical KDCube pattern): there is exactly ONE schema per
# tenant/project — `kdcube_{tenant}_{project}`. It is NOT per-agent, NOT per-bundle,
# NOT per-version. The bundle's tables are bundle-prefixed NAMES inside that shared
# schema (`ported_langgraph_agents_memories`, `ported_langgraph_agents_kb`). The
# platform's PostgresSetup job provides the `vector` / `pg_trgm` / `pgcrypto`
# extensions, so the bundle only ever `CREATE SCHEMA IF NOT EXISTS` +
# `CREATE TABLE/INDEX IF NOT EXISTS` — it never provisions an extension itself.
#
# ISOLATION IS BY COLUMN, NOT SCHEMA: every row is tagged with the scope
# `(tenant, project, bundle_id, agent_id, user_id)` and every read filters on it.
# This one app hosts two agents, `lg-solution` and `lg-react`; their rows share the
# same table and stay apart via the `agent_id` COLUMN. Combined with the identity
# gate folding agent_id into the per-user key, the two agents' state can never mix.
#
# The ONLY selection is this injection point — there is no runtime toggle:
#
#   1. pg_pool present  ->  KDCube shared Postgres + tenant/project schema  (HOSTED)
#   2. else             ->  the agent's own DATABASE_URL                    (LOCAL / poc)
#   3. DB unreachable   ->  callers degrade: empty recall + MemorySaver     (OFFLINE)
#
# DRIVER SPLIT (the fix): lg-solution's own store has two edges with different
# driver needs, so this seam routes them differently:
#
#   - memory + KB (pgvector)  ->  KDCube's `pg_pool` DIRECTLY. That pool is an
#     *asyncpg* pool (the SAME object `conv_index` uses), and asyncpg IS installed
#     in the proc. `resolve_solution_memory` hands the memory/KB constructors the
#     pool + the tenant/project schema; they acquire from it, qualify their tables
#     with that schema, and scope every row by columns. No psycopg — this was the
#     "No module named 'psycopg'" runtime degradation.
#   - checkpointer + optional `langgraph_store`  ->  a psycopg/libpq DSN. LangGraph's
#     `AsyncPostgresSaver`/`AsyncPostgresStore` genuinely require psycopg v3 (asyncpg
#     can't drive them), so for THOSE we derive a DSN from the SAME platform settings
#     `get_pg_pool()` builds the pool from (`get_settings()` PG* fields), carrying the
#     tenant/project schema on its libpq search_path. psycopg[binary] is added to the
#     proc requirements for this path; absent it, the checkpointer degrades to
#     MemorySaver.
#
# The pool's PRESENCE is the hosted signal; the settings are the durable bridge for
# the psycopg DSN. We never hand the asyncpg pool across the psycopg boundary.

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import quote

from ..solution.lg_solution._pg import with_search_path


def _safe_identifier(value: str, *, fallback: str = "default") -> str:
    """Fold an arbitrary tenant/project token into a plain SQL identifier (the
    same rule `conv_index` / `UserMemoryStore` land on), so the schema name is a
    bare identifier the search_path options string carries safely."""
    raw = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "")).strip("_").lower()
    if not raw:
        raw = fallback
    if raw[0].isdigit():
        raw = f"_{raw}"
    return raw


def schema_for_scope(tenant: str, project: str) -> str:
    """The ONE per-tenant/project Postgres schema. Mirrors `ConvIndex` /
    `UserMemoryStore`: `kdcube_{tenant}_{project}` (unsafe chars folded). There is
    no per-agent, per-bundle, or per-version schema — agents are separated by the
    `agent_id` column inside bundle-prefixed tables in this shared schema."""
    tenant_part = _safe_identifier(tenant, fallback="default")
    project_part = _safe_identifier(project, fallback="default")
    schema = f"{tenant_part}_{project_part}"
    if not schema.startswith("kdcube_"):
        schema = f"kdcube_{schema}"
    return schema


@dataclass(frozen=True)
class SolutionPgTarget:
    """Where the psycopg-backed stores (checkpointer + optional `langgraph_store`)
    connect for this process.

    - ``database_url`` — what `config.database_url` becomes; when hosted it carries
      the tenant/project schema on its libpq search_path, so those stores land there.
    - ``schema`` — the schema to `CREATE` before any DDL (None => own DB / offline).
    - ``base_url`` — the DSN WITHOUT the search_path options, used only to create
      the schema (None => own DB).
    - ``hosted`` — True when routed onto KDCube's shared Postgres.
    """

    database_url: str
    schema: Optional[str]
    base_url: Optional[str]
    hosted: bool


@dataclass(frozen=True)
class SolutionMemoryTarget:
    """Where lg-solution's pgvector memory + KB connect: KDCube's SHARED asyncpg
    pool DIRECTLY (no psycopg), in the tenant/project schema.

    - ``pool`` — the injected asyncpg pool, or None (offline => empty recall).
    - ``schema`` — the tenant/project schema the tables are qualified with (None
      offline). Rows are scoped by columns, not by this schema.
    - ``hosted`` — True when a pool is present.
    """

    pool: Any
    schema: Optional[str]
    hosted: bool


def resolve_solution_memory(pg_pool: Any, schema: str) -> SolutionMemoryTarget:
    """Resolve the asyncpg pool + tenant/project schema for memory/KB. The memory/KB
    constructors take these and drive the pool directly, scoping rows by columns.
    Never raises; with no pool it yields the offline target (empty recall + skipped
    writes)."""
    if pg_pool is not None:
        return SolutionMemoryTarget(pool=pg_pool, schema=schema, hosted=True)
    return SolutionMemoryTarget(pool=None, schema=None, hosted=False)


def _kdcube_pg_dsn() -> Optional[str]:
    """Build a psycopg/libpq DSN from KDCube's platform settings — the SAME PG*
    fields `get_pg_pool()` uses to build the shared asyncpg pool. Returns None if
    settings are unavailable, so the caller falls back to the own DATABASE_URL."""
    try:
        from kdcube_ai_app.apps.chat.sdk.config import get_settings

        s = get_settings()
    except Exception:
        return None

    host = getattr(s, "PGHOST", None)
    if not host:
        return None

    user = quote(str(getattr(s, "PGUSER", "postgres") or "postgres"), safe="")
    pwd = quote(str(getattr(s, "PGPASSWORD", "") or ""), safe="")
    port = getattr(s, "PGPORT", 5432)
    db = getattr(s, "PGDATABASE", "postgres")
    dsn = f"postgresql://{user}:{pwd}@{host}:{port}/{db}"

    # Mirror the platform's libpq SSL semantics (same POSTGRES_SSL* namespace the
    # asyncpg pool honors) so the derived psycopg connection reaches the same DB.
    params = []
    if getattr(s, "PGSSL", False):
        mode = getattr(s, "PGSSL_MODE", None) or "require"
        params.append(f"sslmode={quote(str(mode), safe='')}")
        root = getattr(s, "PGSSL_ROOT_CERT", None)
        if root:
            params.append(f"sslrootcert={quote(str(root), safe='')}")
    if params:
        dsn = dsn + "?" + "&".join(params)
    return dsn


def resolve_solution_pg(pg_pool: Any, own_database_url: str, schema: str) -> SolutionPgTarget:
    """The injection point. `pg_pool` present -> HOSTED (KDCube shared Postgres +
    the given tenant/project ``schema``); else the agent's own DATABASE_URL (LOCAL /
    poc). Never raises — a resolution failure falls back to the own DB, which
    itself degrades to empty recall + a MemorySaver when unreachable."""
    if pg_pool is not None:
        base = _kdcube_pg_dsn()
        if base:
            return SolutionPgTarget(
                database_url=with_search_path(base, schema),
                schema=schema,
                base_url=base,
                hosted=True,
            )
    # LOCAL / poc: the agent keeps its own DB; no schema qualification, so the
    # standalone behavior is byte-for-byte unchanged.
    return SolutionPgTarget(
        database_url=own_database_url,
        schema=None,
        base_url=None,
        hosted=False,
    )
