"""Actual Hub decision/issuance seam, real PG/Redis; no bearer custody.

Signing keys are fixtures; replay re-signs the stored claims. No installed/live
deployment or process-crash proof is inferred from these tests.
"""
import pytest

from connection_hub.delegated_credentials.oauth_issuance import IssuanceRefused, original_input_digest
from connection_hub.invocation_policy import SURFACE_OUTER, InvocationAuthority
from kdcube_ai_app.auth.tests.test_bound_session_issuance_store import counts
from kdcube_ai_app.infra.secrets.tests.test_runtime_http import rig as secrets_rig
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.http import routes
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_candidate_inputs import oauth_issuance_arguments
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.original_hub_fixture import RESOURCE, hub_world, refresh_originals, token_digests


@pytest.mark.asyncio
async def test_actual_hub_and_sdk_return_the_same_original_pair(hub_world):
    r = hub_world
    first = await routes.token(r.request)
    assert first.status_code == 200, r.errors
    again = await routes.token(r.request)
    assert again.status_code == 200 and token_digests(again) == token_digests(first)
    assert await counts(r.sessions) == (1, 1, 1)
    [(_, state, refresh_sha256)] = await refresh_originals(r)
    assert (state, refresh_sha256) == ("applied", token_digests(first)["refresh"])
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
    assert await counts(r.sessions) == (1, 1, 0) and len(await refresh_originals(r)) == 1
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
    assert (await r.ledger.read(r.proof)).plan is None and r.signs == 0
    assert await refresh_originals(r) == []
    r.hub.begin_oauth_issuance = begin
    response = await routes.token(r.request)
    assert response.status_code == 200, r.errors
    [(_, _, refresh_sha256)] = await refresh_originals(r)
    assert refresh_sha256 == token_digests(response)["refresh"]


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
    assert await counts(r.sessions) == (1, 1, 0)
    originals = await refresh_originals(r)
    assert [sha for _, _, sha in originals] == [result.per_slot["refresh"].token_sha256]
    r.oauth.activate_issued_credential = activate
    async def no_reserve(**kwargs):
        pytest.fail("actual committed Hub decision received another reservation")
    r.hub.reserve_oauth_issuance = no_reserve
    response = await routes.token(r.request)
    assert response.status_code == 200 and token_digests(response) == {
        slot: value.token_sha256 for slot, value in result.per_slot.items()}
    assert await counts(r.sessions) == (1, 1, 1)
    assert [(sid, sha) for sid, _, sha in await refresh_originals(r)] == [(sid, sha) for sid, _, sha in originals]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["once", "always"])
async def test_actual_hub_policy_and_original_pair_share_one_decision_and_replay(hub_world, mode):
    r = hub_world
    r.candidate_overrides = {
        "client_label": "Unit client / records", "client_metadata": {"label": "Unit client"},
        "properties": {"consent": {"source": "synthetic-host"}},
        "resource_operations": {RESOURCE: ["search"]},
        "invocation_policies": {RESOURCE: {"search": mode}},
    }
    async def no_post_grant_policy_write(**kwargs):
        pytest.fail("original issuance attempted a second post-grant policy write")
    r.hub.apply_oauth_invocation_policies = no_post_grant_policy_write
    first = await routes.token(r.request)
    assert first.status_code == 200, r.errors
    mapping = await r.ledger.read(r.proof)
    plan = await r.hub.read_oauth_issuance_plan(transaction_id=mapping.plan["transaction_id"])
    arguments = oauth_issuance_arguments({
        "grantor_subject": r.subject, "client_id": r.client, "scopes": ["records:read"],
        "resource": RESOURCE, **r.candidate_overrides})
    assert plan.original_input_digest == original_input_digest(arguments)
    policy_authority = InvocationAuthority(
        access_id=plan.access_id, resource=RESOURCE, surface=SURFACE_OUTER, operation="search")
    assert policy_authority.key in dict(plan.effect_digests)
    policy = await r.policies.get(owner_subject=r.subject, authority=policy_authority)
    assert (policy.mode, policy.revision) == (mode, 1)
    assert await counts(r.sessions) == (1, 1, 1)
    again = await routes.token(r.request)
    assert again.status_code == 200 and token_digests(again) == token_digests(first)
    originals = await refresh_originals(r)
    assert [sha for _, _, sha in originals] == [token_digests(first)["refresh"]]
    assert (await r.policies.get(owner_subject=r.subject, authority=policy_authority)).revision == 1
    changed = {**arguments, "invocation_policies": {RESOURCE: {"search": "always" if mode == "once" else "once"}}}
    with pytest.raises(IssuanceRefused, match="^issuance_replay_changed$"):
        await r.hub.begin_oauth_issuance(original_request_id=r.proof.identity, **changed)
    assert await refresh_originals(r) == originals
    assert (await r.policies.get(owner_subject=r.subject, authority=policy_authority)).mode == mode


@pytest.mark.asyncio
async def test_actual_hub_empty_policy_selection_refuses_without_preparing_pair(hub_world):
    r = hub_world
    r.candidate_overrides = {"resource_operations": {RESOURCE: ["search"]}, "invocation_policies": {}}
    response = await routes.token(r.request)
    assert response.status_code == 503
    assert r.signs == 0 and await counts(r.sessions) == (0, 0, 0) and await refresh_originals(r) == []
    assert (await r.ledger.read(r.proof)).plan is None
