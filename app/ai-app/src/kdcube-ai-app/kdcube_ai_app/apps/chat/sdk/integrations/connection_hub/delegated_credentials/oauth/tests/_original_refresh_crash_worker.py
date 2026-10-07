"""SIGKILL fixture: real PG and private file service, synthetic credentials.

Only phase markers and opaque metadata/hashes cross stdout. Physical filesystem
qualification is a fixture; encryption and deployed mount lifecycle are not tested.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys

import asyncpg
import pytest
from fastapi import FastAPI

from connection_hub.delegated_credentials.oauth_issuance import OAuthIssuancePlan
from kdcube_ai_app.infra.secrets import runtime_contract, runtime_file, runtime_http
from kdcube_ai_app.infra.secrets.issuance import issuance_secret_custody
from kdcube_ai_app.infra.secrets.tests.test_runtime_http import service_adapter
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_exchange import canonical, digest
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_refresh_store import PostgresOriginalRefreshStore
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_refresh_issuer import HmacOriginalRefreshSigner, OriginalRefreshIssuer


async def main():
    request = json.loads(sys.stdin.readline())
    plan = OAuthIssuancePlan.from_mapping(request["plan"])
    pool = await asyncpg.create_pool(request["dsn"], min_size=1, max_size=2)
    patches = pytest.MonkeyPatch()

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
        patches.setattr(runtime_contract, "persistent_filesystem", lambda root: True)
        policy = runtime_contract.RuntimeScopePolicy(json.dumps({
            "schema": runtime_contract.POLICY_SCHEMA,
            "read": {hashlib.sha256(b"synthetic-reader").hexdigest(): ["custody"]},
            "write": {hashlib.sha256(b"synthetic-writer").hexdigest(): ["custody"]},
        }))
        app = FastAPI()
        runtime_http.install_runtime_routes(app, policy=policy, store_factory=lambda namespace:
            runtime_file.RuntimeFileStore(root=request["root"], namespace=namespace,
                                          authorized_namespaces=("custody",)))
        custody = issuance_secret_custody(namespace="custody", manager=service_adapter(app, patches))
        create = custody.create
        async def phased_create(**kwargs):
            made = await create(**kwargs)
            await boundary("after_custody")
            return made
        custody.create = phased_create

        async def key():
            if request.get("forbid_sign"):
                raise AssertionError("existing original attempted signing")
            return b"unit-original-refresh-key-32-bytes!"
        store = PhasedStore(pg_pool=pool, tenant=plan.tenant, project=plan.project)
        await store.ensure_schema()
        issuer = OriginalRefreshIssuer(store=store, custody=custody,
            signer=HmacOriginalRefreshSigner(plan.tenant, plan.project, key),
            card_kind="automation", ttl_seconds=180 * 86400)
        original = await issuer.prepare(plan=plan)
        print(json.dumps({"sid": original.claims["sid"], "secret_ref": original.secret_ref,
            "bearer_sha256": original.bearer_sha256, "expires_at": original.context.expires_at,
            "claims_digest": digest(canonical(original.claims)), "state": original.state}), flush=True)
    finally:
        patches.undo()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
