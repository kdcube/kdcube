"""Child process for tests; killed after a confirmed durable commit."""
from __future__ import annotations

import asyncio
import json
import sys

import asyncpg

from kdcube_ai_app.auth.bundle import BundleSessionAuthority
from kdcube_ai_app.auth.bundle.session_issuance import IssuanceContext
from kdcube_ai_app.auth.bundle.session_store import PostgresBundleSessionStore
from kdcube_ai_app.auth.tests._bound_session_crash_fixtures import (
    PhasedStore, PostgresTestCustody, pause_after_commit,
)


async def main():
    request = json.loads(sys.stdin.readline())
    pool = await asyncpg.create_pool(request["dsn"], min_size=1, max_size=2)
    store = PostgresBundleSessionStore(
        pg_pool=pool, tenant=request["context"]["tenant"], project=request["context"]["project"],
    )

    async def hook(phase):
        await pause_after_commit(phase, request["phase"])

    try:
        issuer = BundleSessionAuthority(
            tenant=store.tenant, project=store.project,
            authority_store=PhasedStore(store, hook), secret="unit-session-signing-secret",
        )
        await issuer.issue_bound_session(
            IssuanceContext(**request["context"]), user_id="integration:unit:human",
            roles=["delegated-client"], permissions=["records:read"],
            custody=PostgresTestCustody(store, hook=hook),
        )
        raise AssertionError("requested interruption boundary was not reached")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
