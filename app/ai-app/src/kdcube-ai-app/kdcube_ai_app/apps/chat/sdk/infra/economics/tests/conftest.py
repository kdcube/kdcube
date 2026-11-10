# SPDX-License-Identifier: MIT

"""Real-PostgreSQL harness for the economics money paths.

The idempotency ledger and the Stripe webhook effects depend on behaviour a
fake connection cannot reproduce faithfully: `ON CONFLICT DO NOTHING ...
RETURNING` returning nothing on a replay, `FOR UPDATE` serializing two
deliveries, a rollback erasing a mark, and jsonb encoding on pools with and
without the app's jsonb codec. Tests that need those use the `econ_pg`
fixture, which builds one disposable project schema from the shipped DDL.

Set `KDCUBE_TEST_POSTGRES_DSN` to a PostgreSQL the test may create and drop
schemas in; without it the DB-backed tests skip.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

SCHEMA_SQL = (
    Path(__file__).resolve().parents[6]
    / "ops" / "deployment" / "sql" / "chatbot" / "deploy-kdcube-proj-schema.sql"
)

PROJECT = "econtest"

# The economics tables these tests touch, extracted from the shipped DDL rather
# than retyped, so a column change in the schema file reaches the tests.
_DDL_BLOCKS = (
    ("CREATE OR REPLACE FUNCTION <SCHEMA>.update_updated_at()", "$$ LANGUAGE plpgsql;"),
    ("CREATE TABLE IF NOT EXISTS <SCHEMA>.user_lifetime_credits (",
     "CHECK (purchased_cents >= 0 AND spent_cents >= 0)\n);"),
    ("CREATE TABLE IF NOT EXISTS <SCHEMA>.plans (",
     "FOR EACH ROW EXECUTE FUNCTION <SCHEMA>.update_updated_at();"),
    ("CREATE TABLE IF NOT EXISTS <SCHEMA>.user_plans (",
     "FOR EACH ROW EXECUTE FUNCTION <SCHEMA>.update_updated_at();"),
    ("CREATE TABLE IF NOT EXISTS <SCHEMA>.external_economics_events (",
     "FOR EACH ROW EXECUTE FUNCTION <SCHEMA>.update_updated_at();"),
)


def _sql_block(text: str, start: str, end: str) -> str:
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j + len(end)]


async def _init_jsonb_codec(conn) -> None:
    """The codec the app installs on its pool (`apps/chat/ingress/resolvers.py`)."""
    for kind in ("json", "jsonb"):
        await conn.set_type_codec(kind, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def make_pool(dsn: str, *, codec: bool):
    import asyncpg

    return await asyncpg.create_pool(
        dsn, min_size=1, max_size=6, init=_init_jsonb_codec if codec else None
    )


class EconDB:
    """One disposable project schema plus the pools opened against it."""

    def __init__(self, dsn: str, tenant: str, project: str, schema: str, pool):
        self.dsn = dsn
        self.tenant = tenant
        self.project = project
        self.schema = schema
        self.pool = pool
        self._extra = []

    @property
    def events_table(self) -> str:
        return f"{self.schema}.external_economics_events"

    async def pool_without_codec(self):
        pool = await make_pool(self.dsn, codec=False)
        self._extra.append(pool)
        return pool

    async def event_row(self, source: str, kind: str, external_id: str):
        return await self.pool.fetchrow(
            f"SELECT * FROM {self.events_table} WHERE source=$1 AND kind=$2 AND external_id=$3",
            source, kind, external_id,
        )

    async def event_count(self) -> int:
        return await self.pool.fetchval(f"SELECT COUNT(*) FROM {self.events_table}")


@pytest.fixture
async def econ_pg():
    pytest.importorskip("asyncpg")
    dsn = os.environ.get("KDCUBE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("KDCUBE_TEST_POSTGRES_DSN is required for real-PostgreSQL economics tests")

    from kdcube_ai_app.ops.deployment.sql.db_deployment import project_schema

    tenant = f"t{uuid.uuid4().hex[:12]}"
    schema = project_schema(tenant, PROJECT)
    src = SCHEMA_SQL.read_text(encoding="utf-8")
    pool = await make_pool(dsn, codec=True)
    db = EconDB(dsn, tenant, PROJECT, schema, pool)
    try:
        async with pool.acquire() as conn:
            await conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            await conn.execute(f"CREATE SCHEMA {schema}")
            for start, end in _DDL_BLOCKS:
                await conn.execute(_sql_block(src, start, end).replace("<SCHEMA>", schema))
        yield db
    finally:
        for extra in db._extra:
            await extra.close()
        try:
            async with pool.acquire() as conn:
                await conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        finally:
            await pool.close()
