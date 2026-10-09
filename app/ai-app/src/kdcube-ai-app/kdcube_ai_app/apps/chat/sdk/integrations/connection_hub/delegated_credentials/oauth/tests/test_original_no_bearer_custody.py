"""No issued bearer in secret custody (operator, 2026-10-09: "i need the stronger version now").

Actual Hub decision, real PostgreSQL/Redis and the SDK pair flow. Every custody operation fails the
test; the records root of the file secrets backend stays empty. (a) a crash after the seal and before
the response recovers the identical pair; (b) a changed key after the seal refuses; (c) issuance works
with no custody at all; (d) nothing is written under the records root.
"""
import hashlib
import json
import logging

import pytest

from kdcube_ai_app.auth.tests.test_bound_session_issuance_store import counts
from kdcube_ai_app.auth.tests.test_bound_session_issuer import authority
from kdcube_ai_app.infra.secrets import issuance
from kdcube_ai_app.infra.secrets.tests.test_runtime_http import rig as secrets_rig
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.http import routes
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_refresh_issuer import HmacOriginalRefreshSigner
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.original_hub_fixture import hub_world, token_digests


@pytest.fixture(autouse=True)
def no_custody(monkeypatch):
    """Any bearer-custody use, by any path, fails the test."""
    def forbidden(name):
        def call(*args, **kwargs):
            pytest.fail(f"bearer custody used: {name}")
        return call
    monkeypatch.setattr(issuance, "issuance_secret_custody", forbidden("issuance_secret_custody"))
    for name in ("create", "get", "delete", "qualify", "purge_expired"):
        monkeypatch.setattr(issuance.KDCubeIssuanceSecretCustody, name, forbidden(name))


def tokens(response):
    body = json.loads(response.body)
    return body["access_token"], body["refresh_token"]


def files_under(root):
    return sorted(str(path.relative_to(root)) for path in root.rglob("*")) if root.exists() else []


async def committed_digests(r):
    mapping = await r.ledger.read(r.proof)
    result = await r.hub.read_oauth_issuance(transaction_id=mapping.plan["transaction_id"])
    assert result.state == "committed"
    return {slot: value.token_sha256 for slot, value in result.per_slot.items()}


def crash_after(r, point):
    """The process dies after the named durable step, before any response is written."""
    if point == "complete":
        target, name = r.hub, "complete_oauth_issuance"
    else:
        target, name = r.provider, "bearers"
    original = getattr(target, name)

    async def crashed(**kwargs):
        await original(**kwargs)
        setattr(target, name, original)
        raise TimeoutError("synthetic-crash-before-response")
    setattr(target, name, crashed)


@pytest.mark.asyncio
@pytest.mark.parametrize("point", ["complete", "bearers"])
async def test_a_crash_after_seal_before_response_returns_the_identical_pair(hub_world, point, caplog):
    r = hub_world
    caplog.set_level(logging.DEBUG)
    crash_after(r, point)
    lost = await routes.token(r.request)
    assert lost.status_code == 503 and b"_token" not in lost.body
    sealed = await committed_digests(r)
    retry = await routes.token(r.request)
    assert retry.status_code == 200, r.errors
    assert token_digests(retry) == sealed
    again = await routes.token(r.request)
    assert again.status_code == 200 and tokens(again) == tokens(retry)
    assert await counts(r.sessions) == (1, 1, 1)
    body = json.loads(retry.body)
    for slot in ("access", "refresh"):
        bearer = body[slot + "_token"]
        assert bearer not in caplog.text and hashlib.sha256(bearer.encode()).hexdigest() not in caplog.text
    assert files_under(r.custody_root) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("slot", ["access", "refresh"])
async def test_b_a_key_changed_after_seal_refuses_and_never_yields_another_bearer(hub_world, slot):
    r = hub_world
    crash_after(r, "complete")
    assert (await routes.token(r.request)).status_code == 503
    sealed = await committed_digests(r)
    if slot == "access":
        r.provider.authority_factory = lambda **kwargs: authority(r.sessions, secret="another-session-secret-32-bytes!!")
    else:
        async def other_key():
            return b"another-original-refresh-key-32-bytes"
        r.provider.refresh.signer = HmacOriginalRefreshSigner(r.tenant, r.project, other_key)
    refused = await routes.token(r.request)
    assert refused.status_code != 200 and b"_token" not in refused.body
    assert r.errors[-1][0] in {"OriginalExchangeRefused", "SessionIssuanceRefused"}, r.errors
    assert await committed_digests(r) == sealed
    assert files_under(r.custody_root) == []


@pytest.mark.asyncio
async def test_c_issuance_works_with_no_custody_and_d_writes_nothing_under_the_records_root(hub_world):
    r = hub_world
    before = files_under(r.custody_root)
    first = await routes.token(r.request)
    assert first.status_code == 200, r.errors
    assert token_digests(first) == await committed_digests(r)
    assert tokens(await routes.token(r.request)) == tokens(first)
    assert files_under(r.custody_root) == before == []
