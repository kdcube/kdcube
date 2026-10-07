"""Original HTTP composition with synthetic durable-port implementations.

These prove orchestration/negative boundaries, not PostgreSQL, provider ACLs,
physical restart durability or installed host binding.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from starlette.datastructures import FormData

from connection_hub.delegated_credentials.oauth.pkce import make_s256_challenge
from connection_hub.delegated_credentials.oauth_issuance import OAuthIssuancePlan, OAuthIssuanceResult, SlotOutcome, original_input_digest
from kdcube_ai_app.auth.bundle.session_issuance import SessionIssuanceReceipt
from kdcube_ai_app.auth.bundle.session_planned_issuance import PlannedIssuanceContext
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_exchange import (
    CodeExchangeProof, OriginalExchangeRefused, ValidatedCodeExchange, plan_snapshot,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_exchange_store import OriginalExchange
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_code_flow import (
    DECISION_SCOPE, OriginalCodeExchangeFlow, OriginalExchangePending, PreparedOriginalCredential,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_candidate_inputs import oauth_issuance_arguments
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.http import routes, original_code


@pytest.fixture
def rig(monkeypatch):
    now = int(time.time())
    r = SimpleNamespace(code="unit-original", verifier="x" * 48, consumes=0, prepares=0,
                        pair_reads=0, completions=0, activations=0, reserved=[], gets=0,
                        fenced=True, lose_complete=False, outcome="pending", original=None, retired=0,
                        slot_outcomes={}, candidate_reads=0)
    r.proof = CodeExchangeProof.from_request(tenant="unit-tenant", project="unit-project", code=r.code,
                                             client_id="unit-client", redirect_uri="https://unit.test/cb", verifier=r.verifier)
    r.payload = {"sub": "human", "client_id": "unit-client", "redirect_uri": "https://unit.test/cb",
                 "code_challenge": make_s256_challenge(r.verifier)}
    r.inputs = oauth_issuance_arguments({"grantor_subject": "human", "client_id": "unit-client", "scopes": ["records:read"]})
    binding = ValidatedCodeExchange.from_consumed(r.proof, r.payload,
                                                  original_input_digest=original_input_digest(r.inputs), decision_scope=DECISION_SCOPE)
    r.binding = binding
    r.plan = OAuthIssuancePlan(
        transaction_id="a" * 64, decision_request_id=binding.decision_request_id, intent_digest="b" * 64,
        original_input_digest=binding.original_input_digest, tenant="unit-tenant", project="unit-project",
        access_id="unit-card", grantor_subject="human", client_id="unit-client",
        credential_issuer="unit-issuer", credential_subject="integration:unit:human", base_revision=1,
        candidate_revision=2, expires_at=now + 7200, card_content_hash="c" * 64,
        operations=("records.read",), resource_grants={"/records": ("records:read",)},
        resource_operations={"/records": ("records.read",)}, delivery_deadline=now + 600,
        reserved_until=now + 300, slots=("access", "refresh"), effect_digests={"access": "d" * 64, "refresh": "e" * 64},
    )
    r.bearers = {"access": "unit-original-access", "refresh": "unit-original-refresh"}
    r.receipts = {slot: SessionIssuanceReceipt(session_id="unit-" + slot, secret_ref=str(i) * 32,
                   bearer_sha256=hashlib.sha256(r.bearers[slot].encode()).hexdigest())
                  for i, slot in enumerate(r.plan.slots, 1)}

    class Ledger:
        tenant, project = "unit-tenant", "unit-project"
        async def read(self, proof):
            if proof != r.proof:
                raise OriginalExchangeRefused("original_exchange_proof_mismatch")
            return r.original
        async def begin(self, value):
            assert value == r.binding
            r.original = OriginalExchange(identity=r.proof.identity, decision_request_id=value.decision_request_id,
                payload_digest=value.payload_digest, original_input_digest=value.original_input_digest,
                pending_until=now + 600, delivery_deadline=None, plan=None, created=True)
            return r.original
        async def pin_plan(self, value, plan, *, access_ttl_seconds):
            assert value == r.binding and access_ttl_seconds == 3600
            snapshot = plan_snapshot(value, plan)
            r.original = replace(r.original, plan=copy.deepcopy(snapshot), delivery_deadline=plan.delivery_deadline,
                                  access_expires_at=now + 3600, access_ttl_seconds=3600)
            return r.original
    class Grant:
        async def consume_auth_code(self, code):
            r.consumes += 1
            assert code == r.code
            return r.payload if r.consumes == 1 else None
    class Hub:
        async def begin_oauth_issuance(self, *, original_request_id, **inputs):
            assert original_request_id == r.proof.identity and inputs == r.inputs
            return r.plan
        async def read_oauth_issuance_plan(self, *, transaction_id):
            assert transaction_id == "a" * 64
            return r.plan
        async def read_oauth_issuance(self, *, transaction_id):
            assert transaction_id == "a" * 64
            return OAuthIssuanceResult(
                transaction_id=r.plan.transaction_id, intent_digest=r.plan.intent_digest, state=r.outcome,
                access_id=r.plan.access_id, card_revision=r.plan.candidate_revision if r.outcome == "committed" else r.plan.base_revision,
                expires_at=r.plan.expires_at, delivery_deadline=r.plan.delivery_deadline,
                receipt_digest="f" * 64 if r.outcome == "committed" else "",
                per_slot={slot: SlotOutcome(r.slot_outcomes.get(slot, "applied" if r.outcome == "committed" else "pending"),
                         r.plan.effect_digests[slot], r.receipts[slot].bearer_sha256 if r.outcome == "committed" else "")
                          for slot in r.plan.slots})
        async def reserve_oauth_issuance(self, **value):
            assert value["plan"] == r.plan
            r.reserved.append(value["slot"])
        async def complete_oauth_issuance(self, *, transaction_id, expect):
            assert transaction_id == r.plan.transaction_id
            assert expect == {slot: r.receipts[slot].bearer_sha256 for slot in r.plan.slots}
            r.completions += 1
            r.outcome = "committed"
            if r.lose_complete:
                r.lose_complete = False
                raise TimeoutError("unit-unknown-COMMIT-canary")
            return await self.read_oauth_issuance(transaction_id=transaction_id)
    class Provider:
        def pair(self, plan, expiry):
            return {slot: PreparedOriginalCredential(
                context=PlannedIssuanceContext.from_oauth_plan(plan, slot=slot, expires_at=expiry if slot == "access" else plan.expires_at),
                receipt=r.receipts[slot], record={"card_kind": "automation"}, ttl_seconds=3600 if slot == "access" else 7200)
                    for slot in plan.slots}
        async def prepare_pair(self, *, plan, access_expires_at):
            r.prepares += 1
            return self.pair(plan, access_expires_at)
        async def read_pair(self, *, plan, access_expires_at):
            r.pair_reads += 1
            return self.pair(plan, access_expires_at)
        async def activate_access(self, *, plan, result, credential):
            r.activations += 1
            return credential.receipt
        async def retire_pair(self, *, plan, result, access_expires_at):
            assert plan == r.plan and result.state in {"aborted", "committed"}
            assert access_expires_at == r.original.access_expires_at
            r.retired += 1
    class Custody:
        async def get(self, *, secret_ref):
            r.gets += 1
            return next((r.bearers[slot] for slot in r.plan.slots if r.receipts[slot].secret_ref == secret_ref), None)
    async def candidates(*, payload):
        r.candidate_reads += 1
        assert payload == r.payload
        return r.inputs
    async def fence(**value):
        return r.fenced
    r.ledger, r.hub, r.provider, r.custody = Ledger(), Hub(), Provider(), Custody()
    r.flow = OriginalCodeExchangeFlow(ledger=r.ledger, grant_store=Grant(), hub=r.hub,
        provider=r.provider, custody=r.custody, candidate_inputs=candidates, fence_target=fence)
    monkeypatch.setattr(original_code, "oauth_tenant_project", lambda request: ("unit-tenant", "unit-project"))
    def legacy(request):
        pytest.fail("original flow reached legacy consume/mint")
    monkeypatch.setattr(routes, "get_grant_store", legacy)
    app_state = SimpleNamespace(oauth_original_exchange_factory=r.flow.handler)
    r.request = SimpleNamespace(state=SimpleNamespace(), app=SimpleNamespace(state=app_state))
    async def form():
        return FormData({"grant_type": "authorization_code", "code": r.code, "client_id": "unit-client",
                         "redirect_uri": "https://unit.test/cb", "code_verifier": r.verifier})
    r.request.form = form
    return r


@pytest.mark.asyncio
async def test_actual_http_committed_retry_reads_original_pair_without_rebegin_or_prepare(rig):
    one = await routes.token(rig.request)
    two = await routes.token(rig.request)
    assert one.status_code == two.status_code == 200
    assert json.loads(one.body)["access_token"] == json.loads(two.body)["access_token"]
    assert json.loads(one.body)["refresh_token"] == json.loads(two.body)["refresh_token"]
    assert rig.consumes == rig.prepares == rig.completions == rig.pair_reads == 1
    assert rig.reserved == ["access", "refresh"]


@pytest.mark.asyncio
async def test_unknown_commit_response_recovers_without_second_prepare_or_complete(rig):
    rig.lose_complete = True
    lost = await routes.token(rig.request)
    retry = await routes.token(rig.request)
    assert lost.status_code == 503 and b"canary" not in lost.body
    assert retry.status_code == 200
    assert rig.consumes == rig.prepares == rig.completions == rig.pair_reads == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("transaction_id", "0" * 64), ("intent_digest", "0" * 64), ("original_input_digest", "0" * 64),
    ("decision_request_id", "0" * 64), ("tenant", "other"), ("project", "other"),
    ("access_id", "other"), ("grantor_subject", "other"), ("client_id", "other"),
    ("credential_subject", "other"), ("credential_issuer", "other"), ("card_content_hash", "0" * 64),
    ("candidate_revision", 3), ("base_revision", 0), ("operations", ("write",)),
    ("resource_grants", {"/records": ("records:write",)}),
    ("resource_operations", {"/records": ("write",)}), ("effect_digests", {"access": "1" * 64, "refresh": "e" * 64}),
    ("expires_at", 2000000000), ("delivery_deadline", 2000000000),
    ("reserved_until", 2000000000), ("slots", ("access",)),
])
async def test_changed_authenticated_original_plan_refuses_before_credential_reads(rig, field, value):
    assert (await routes.token(rig.request)).status_code == 200
    before = (rig.prepares, rig.pair_reads, rig.gets)
    rig.plan = replace(rig.plan, **{field: value})
    assert (await routes.token(rig.request)).status_code == 400
    assert (rig.prepares, rig.pair_reads, rig.gets) == before


@pytest.mark.asyncio
async def test_pre_pin_missing_request_reader_is_pending_not_second_begin(rig):
    await rig.ledger.begin(rig.binding)
    response = await routes.token(rig.request)
    assert response.status_code == 503
    assert rig.consumes == rig.prepares == rig.completions == rig.gets == 0


@pytest.mark.asyncio
async def test_pre_pin_original_request_reader_recovers_exact_existing_plan(rig):
    await rig.ledger.begin(rig.binding)
    async def reader(*, decision_request_id):
        assert decision_request_id == rig.binding.decision_request_id
        return rig.plan
    rig.hub.read_oauth_issuance_plan_by_request = reader
    assert (await routes.token(rig.request)).status_code == 200
    assert rig.consumes == 0 and rig.prepares == 1 and rig.completions == 1


@pytest.mark.asyncio
async def test_target_moved_before_prepare_refuses_without_any_bearer(rig):
    rig.fenced = False
    response = await routes.token(rig.request)
    assert response.status_code == 400
    assert rig.prepares == rig.gets == rig.activations == 0


@pytest.mark.asyncio
async def test_missing_original_custody_never_authorizes_replacement(rig):
    assert (await routes.token(rig.request)).status_code == 200
    rig.bearers["refresh"] = ""
    response = await routes.token(rig.request)
    assert response.status_code == 400
    assert rig.prepares == rig.completions == 1


@pytest.mark.asyncio
async def test_final_target_change_after_custody_read_withholds_both(rig):
    original_get = rig.custody.get
    async def moved(*, secret_ref):
        result = await original_get(secret_ref=secret_ref)
        if secret_ref == rig.receipts["refresh"].secret_ref:
            rig.fenced = False
        return result
    rig.custody.get = moved
    response = await routes.token(rig.request)
    assert response.status_code == 400
    assert b"unit-original-access" not in response.body and b"unit-original-refresh" not in response.body


@pytest.mark.asyncio
async def test_original_abort_result_retires_without_prepare_or_custody_read(rig):
    rig.outcome = "aborted"
    response = await routes.token(rig.request)
    assert response.status_code == 400 and rig.retired == 1
    assert rig.prepares == rig.activations == rig.gets == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("already_committed", [True, False])
async def test_original_mixed_committed_result_retires_without_publication(rig, already_committed):
    rig.outcome = "committed" if already_committed else "pending"
    rig.slot_outcomes = {"access": "applied", "refresh": "superseded"}
    response = await routes.token(rig.request)
    assert response.status_code == 400 and rig.retired == 1
    assert rig.pair_reads == rig.activations == rig.gets == 0
    assert rig.prepares == (0 if already_committed else 1)
    assert b"unit-original-access" not in response.body and b"unit-original-refresh" not in response.body


@pytest.mark.asyncio
async def test_terminal_result_without_cleanup_capability_stays_retryable(rig):
    rig.outcome = "aborted"
    rig.provider.retire_pair = None
    response = await routes.token(rig.request)
    assert response.status_code == 503
    assert rig.prepares == rig.pair_reads == rig.activations == rig.gets == 0


@pytest.mark.asyncio
async def test_invalid_consumed_proof_never_calls_host_candidate_builder(rig):
    rig.payload["client_id"] = "other-client"
    response = await routes.token(rig.request)
    assert response.status_code == 400 and rig.candidate_reads == 0
    assert rig.prepares == rig.activations == rig.gets == 0 and rig.original is None
