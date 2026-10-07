"""Actual Hub decision/issuance seam, real PG/Redis and ASGI private-file custody.

Physical volume qualification and signing keys are fixtures; no installed/live
deployment, encryption or process-crash proof is inferred from these tests.
"""
import pytest

from kdcube_ai_app.auth.tests.test_bound_session_issuance_store import counts
from kdcube_ai_app.infra.secrets.tests.test_runtime_http import rig as secrets_rig
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.http import routes
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.original_hub_fixture import hub_world, token_digests


@pytest.mark.asyncio
async def test_actual_hub_and_sdk_return_the_same_original_pair(hub_world):
    r = hub_world
    first = await routes.token(r.request)
    assert first.status_code == 200, r.errors
    again = await routes.token(r.request)
    assert again.status_code == 200 and token_digests(again) == token_digests(first)
    assert r.creates == 2 and r.signs == 1 and await counts(r.sessions) == (1, 1, 1)
    mapping = await r.ledger.read(r.proof)
    result = await r.hub.read_oauth_issuance(transaction_id=mapping.plan["transaction_id"])
    assert result.state == "committed" and all(value.outcome == "applied" for value in result.per_slot.values())
    assert {slot: value.token_sha256 for slot, value in result.per_slot.items()} == token_digests(first)


@pytest.mark.asyncio
async def test_actual_hub_wrong_provider_card_kind_withholds_pair(hub_world):
    r = hub_world
    r.provider.card_kind = "automation"
    response = await routes.token(r.request)
    assert response.status_code == 503
    assert r.creates == 2 and r.signs == 1 and await counts(r.sessions) == (1, 1, 0)
    mapping = await r.ledger.read(r.proof)
    result = await r.hub.read_oauth_issuance(transaction_id=mapping.plan["transaction_id"])
    assert result.state == "pending"
    assert all(value.outcome == "pending" for value in result.per_slot.values())


@pytest.mark.asyncio
async def test_actual_hub_lost_begin_answer_recovers_by_request_without_second_code(hub_world):
    r = hub_world
    begin = r.hub.begin_oauth_issuance
    async def lost(**kwargs):
        await begin(**kwargs)
        raise TimeoutError("synthetic-lost-original-begin-answer")
    r.hub.begin_oauth_issuance = lost
    assert (await routes.token(r.request)).status_code == 503
    assert (await r.ledger.read(r.proof)).plan is None and r.creates == r.signs == 0
    r.hub.begin_oauth_issuance = begin
    assert (await routes.token(r.request)).status_code == 200, r.errors
    assert r.creates == 2 and r.signs == 1


@pytest.mark.asyncio
async def test_actual_hub_partial_finish_retry_reads_originals_without_reserving_again(hub_world):
    r = hub_world
    activate = r.oauth.activate_issued_credential
    async def interrupted(**kwargs):
        if kwargs["slot"] == "refresh":
            raise ConnectionError("synthetic-interrupted-refresh-activation")
        return await activate(**kwargs)
    r.oauth.activate_issued_credential = interrupted
    assert (await routes.token(r.request)).status_code == 503
    mapping = await r.ledger.read(r.proof)
    result = await r.hub.read_oauth_issuance(transaction_id=mapping.plan["transaction_id"])
    assert result.state == "pending" and result.per_slot["access"].outcome == "applied", r.errors
    assert r.creates == 2 and r.signs == 1 and await counts(r.sessions) == (1, 1, 0)
    r.oauth.activate_issued_credential = activate
    async def no_reserve(**kwargs):
        pytest.fail("actual committed Hub decision received another reservation")
    r.hub.reserve_oauth_issuance = no_reserve
    response = await routes.token(r.request)
    assert response.status_code == 200 and token_digests(response) == {
        slot: value.token_sha256 for slot, value in result.per_slot.items()}
    assert r.creates == 2 and r.signs == 1 and await counts(r.sessions) == (1, 1, 1)
