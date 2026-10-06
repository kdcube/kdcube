"""Durable *test-only* custody for process-interruption qualification.

The table contains synthetic bearers in the isolated test schema. It is not a
production secret backend, encryption claim, or deployment recommendation.
"""
from __future__ import annotations

import asyncio
from typing import Callable, Awaitable


class PostgresTestCustody:
    def __init__(self, store, *, hook: Callable[[str], Awaitable[None]] | None = None):
        self.store = store
        self.hook = hook

    async def ensure_schema(self):
        async with self.store._pool.acquire() as connection:
            await connection.execute(
                f"CREATE TABLE IF NOT EXISTS {self.store.schema}.test_issuance_custody ("
                "secret_ref text PRIMARY KEY, value text NOT NULL, expires_at timestamptz NOT NULL)"
            )

    async def create(self, *, secret_ref, value, expires_at):
        async with self.store._pool.acquire() as connection:
            inserted = await connection.fetchval(
                f"INSERT INTO {self.store.schema}.test_issuance_custody "
                "(secret_ref, value, expires_at) VALUES ($1, $2, to_timestamp($3)) "
                "ON CONFLICT (secret_ref) DO NOTHING RETURNING secret_ref",
                secret_ref, value, expires_at,
            )
        if self.hook is not None:
            await self.hook("after_custody")
        return inserted is not None

    async def get(self, *, secret_ref):
        async with self.store._pool.acquire() as connection:
            return await connection.fetchval(
                f"SELECT value FROM {self.store.schema}.test_issuance_custody "
                "WHERE secret_ref = $1 AND expires_at > clock_timestamp()", secret_ref,
            )

    async def count(self):
        async with self.store._pool.acquire() as connection:
            return await connection.fetchval(
                f"SELECT count(*) FROM {self.store.schema}.test_issuance_custody"
            )


class PhasedStore:
    def __init__(self, store, hook):
        self.store = store
        self.hook = hook

    def __getattr__(self, name):
        return getattr(self.store, name)

    async def reserve_issuance(self, *args, **kwargs):
        original = await self.store.reserve_issuance(*args, **kwargs)
        await self.hook("after_reservation")
        return original

    async def activate_reserved(self, *args, **kwargs):
        original = await self.store.activate_reserved(*args, **kwargs)
        await self.hook("after_activation")
        return original


async def pause_after_commit(phase, wanted):
    if phase == wanted:
        # Only a phase marker crosses stdout; no bearer or credentials do.
        print(phase, flush=True)
        await asyncio.Event().wait()
