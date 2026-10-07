"""Terminal selection over synthetic capabilities; no physical custody proof."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from connection_hub.delegated_credentials.oauth_issuance import OAuthIssuanceResult, SlotOutcome
from kdcube_ai_app.auth.bundle.session_issuance import SessionIssuanceRefused
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_exchange import OriginalExchangeRefused
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_pair_provider import OriginalCredentialPairProvider
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.test_grants import _plan


@pytest.fixture
def terminal_pair():
    r = SimpleNamespace(plan=_plan(), retired=[], protected=[], fail_access=False)
    r.expiry = r.plan.expires_at - 1
    class Refresh:
        store = SimpleNamespace(tenant=r.plan.tenant, project=r.plan.project)
        async def retire(self, *, plan, terminal):
            assert plan == r.plan and terminal.plan.slot == "refresh"
            r.retired.append("refresh")
        async def protect_applied(self, *, plan, result):
            assert plan == r.plan and result.per_slot["refresh"].outcome == "applied"
            r.protected.append("refresh")
    class Authority:
        tenant, project = r.plan.tenant, r.plan.project
        async def retire_prepared_bound_session(self, terminal, *, custody):
            assert terminal.plan.slot == "access" and custody is r.custody
            r.retired.append("access")
            if r.fail_access:
                raise TimeoutError("unit-private-provider-canary")
    def factory(**namespace):
        assert namespace == {"tenant": r.plan.tenant, "project": r.plan.project}
        return Authority()
    # Exercise method selection only. Constructor/backend qualification has
    # separate actual-wrapper/PG tests; this fixture claims neither.
    r.provider = object.__new__(OriginalCredentialPairProvider)
    r.custody = object()
    r.provider.refresh, r.provider.custody = Refresh(), r.custody
    r.provider.authority_factory = factory
    return r


def outcome(r, access, refresh, *, state="committed"):
    return OAuthIssuanceResult(transaction_id=r.plan.transaction_id, intent_digest=r.plan.intent_digest,
        state=state, access_id=r.plan.access_id,
        card_revision=r.plan.candidate_revision if state == "committed" else r.plan.base_revision,
        expires_at=r.plan.expires_at, delivery_deadline=r.plan.delivery_deadline,
        receipt_digest="f" * 64 if state == "committed" else "",
        per_slot={slot: SlotOutcome(value, r.plan.effect_digests[slot],
            "" if state == "aborted" and value == "pending" else "a" * 64)
            for slot, value in {"access": access, "refresh": refresh}.items()})


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_slot", ["access", "refresh"])
async def test_mixed_result_retires_only_superseded_and_preserves_applied(terminal_pair, terminal_slot):
    r = terminal_pair
    result = outcome(r, "superseded" if terminal_slot == "access" else "applied",
                        "superseded" if terminal_slot == "refresh" else "applied")
    await r.provider.retire_pair(plan=r.plan, result=result, access_expires_at=r.expiry)
    assert r.retired == [terminal_slot]
    assert r.protected == (["refresh"] if terminal_slot == "access" else [])


@pytest.mark.asyncio
async def test_terminal_provider_failure_does_not_prevent_other_cleanup(terminal_pair):
    r = terminal_pair
    r.fail_access = True
    with pytest.raises(OriginalExchangeRefused, match="^original_exchange_retirement_unavailable$"):
        await r.provider.retire_pair(plan=r.plan, result=outcome(r, "superseded", "superseded"),
                                     access_expires_at=r.expiry)
    assert r.retired == ["access", "refresh"]


@pytest.mark.asyncio
async def test_entire_result_is_checked_before_first_cleanup(terminal_pair):
    r = terminal_pair
    result = outcome(r, "superseded", "superseded")
    result = replace(result, per_slot={**result.per_slot,
        "refresh": replace(result.per_slot["refresh"], effect_digest="0" * 64)})
    with pytest.raises(SessionIssuanceRefused, match="^issuance_identity_conflict$"):
        await r.provider.retire_pair(plan=r.plan, result=result, access_expires_at=r.expiry)
    assert r.retired == r.protected == []


@pytest.mark.asyncio
async def test_fully_applied_pair_never_enters_retirement(terminal_pair):
    r = terminal_pair
    with pytest.raises(OriginalExchangeRefused, match="^original_exchange_result_not_terminal$"):
        await r.provider.retire_pair(plan=r.plan, result=outcome(r, "applied", "applied"),
                                     access_expires_at=r.expiry)
    assert r.retired == r.protected == []


@pytest.mark.asyncio
async def test_abort_without_reservation_retires_both_no_mint_identities(terminal_pair):
    r = terminal_pair
    await r.provider.retire_pair(plan=r.plan, result=outcome(r, "pending", "pending", state="aborted"),
                                 access_expires_at=r.expiry)
    assert r.retired == ["access", "refresh"] and not r.protected


@pytest.mark.asyncio
async def test_missing_result_slot_cannot_partially_retire(terminal_pair):
    r = terminal_pair
    result = outcome(r, "superseded", "superseded")
    result = replace(result, per_slot={"access": result.per_slot["access"]})
    with pytest.raises(OriginalExchangeRefused, match="^original_exchange_result_invalid$"):
        await r.provider.retire_pair(plan=r.plan, result=result, access_expires_at=r.expiry)
    assert r.retired == r.protected == []
