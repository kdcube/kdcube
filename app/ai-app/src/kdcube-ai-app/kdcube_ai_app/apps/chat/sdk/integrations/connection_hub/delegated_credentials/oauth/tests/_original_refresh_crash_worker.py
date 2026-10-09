"""SIGKILL fixture: real PG, synthetic signing key; no bearer custody.

Only phase markers and opaque metadata/hashes cross stdout; the bearer itself
never does. Deployed key storage and process supervision are not tested.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys

import asyncpg

from connection_hub.delegated_credentials.oauth_issuance import OAuthIssuancePlan
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_exchange import canonical, digest
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_refresh_store import PostgresOriginalRefreshStore
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_refresh_issuer import HmacOriginalRefreshSigner, OriginalRefreshIssuer

KEY = "unit-original-refresh-key-32-bytes!"


async def main():
    request = json.loads(sys.stdin.readline())
    plan = OAuthIssuancePlan.from_mapping(request["plan"])
    pool = await asyncpg.create_pool(request["dsn"], min_size=1, max_size=2)

    async def boundary(phase):
        if request.get("phase") == phase:
            print(phase, flush=True)
            await asyncio.Event().wait()

    class PhasedStore(PostgresOriginalRefreshStore):
        async def reserve(self, **kwargs):
            original = await super().reserve(**kwargs)
            await boundary("after_reservation")
            return original

        async def seal(self, original, token_sha256, *, ready=False):
            original = await super().seal(original, token_sha256, ready=ready)
            await boundary("after_ready" if ready else "after_seal")
            return original

    try:
        async def key():
            return request.get("key", KEY).encode()
        store = PhasedStore(pg_pool=pool, tenant=plan.tenant, project=plan.project)
        await store.ensure_schema()
        issuer = OriginalRefreshIssuer(store=store,
            signer=HmacOriginalRefreshSigner(plan.tenant, plan.project, key),
            card_kind="automation", ttl_seconds=180 * 86400)
        original = await issuer.prepare(plan=plan)
        bearer = await issuer.bearer(plan=plan)
        print(json.dumps({"sid": original.claims["sid"], "secret_ref": original.secret_ref,
            "bearer_sha256": original.bearer_sha256, "expires_at": original.context.expires_at,
            "claims_digest": digest(canonical(original.claims)), "state": original.state,
            "delivered_sha256": hashlib.sha256(bearer.encode()).hexdigest()}), flush=True)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
