"""Child process for tests: killed after a confirmed durable commit, or a fresh recovery.

``issue`` pauses at the requested commit boundary until SIGKILL. ``recover`` is a
fresh interpreter that recovers the issuance, re-signs the stored claims and
prints only the bearer's SHA-256 (never the bearer). Both pass a custody that
fails on any call.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys

import asyncpg

from kdcube_ai_app.auth.bundle import BundleSessionAuthority
from kdcube_ai_app.auth.bundle.session_issuance import IssuanceContext
from kdcube_ai_app.auth.bundle.session_store import PostgresBundleSessionStore
from kdcube_ai_app.auth.bundle.sessions import _make_token
from kdcube_ai_app.auth.tests._bound_session_crash_fixtures import (
    SIGNING_SECRET, ForbiddenCustody, PhasedStore, pause_after_commit,
)


async def main():
    request = json.loads(sys.stdin.readline())
    pool = await asyncpg.create_pool(request["dsn"], min_size=1, max_size=2)
    store = PostgresBundleSessionStore(
        pg_pool=pool, tenant=request["context"]["tenant"], project=request["context"]["project"],
    )

    async def hook(phase):
        await pause_after_commit(phase, request.get("phase"))

    try:
        custody = ForbiddenCustody()
        issuer = BundleSessionAuthority(
            tenant=store.tenant, project=store.project,
            authority_store=PhasedStore(store, hook), secret=SIGNING_SECRET,
        )
        bound = IssuanceContext(**request["context"])
        receipt = await issuer.issue_bound_session(
            bound, user_id="integration:unit:human",
            roles=["delegated-client"], permissions=["records:read"], custody=custody,
        )
        if request["mode"] != "recover":
            raise AssertionError("requested interruption boundary was not reached")
        original = await store.read_issuance(bound.identity)
        bearer = _make_token(original.record["claims"], secret=await issuer._resolve_secret())
        print(json.dumps({
            "outcome": receipt.outcome, "session_id": receipt.session_id,
            "bearer_sha256": hashlib.sha256(bearer.encode()).hexdigest(),
            "custody_calls": len(custody.calls),
        }), flush=True)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
