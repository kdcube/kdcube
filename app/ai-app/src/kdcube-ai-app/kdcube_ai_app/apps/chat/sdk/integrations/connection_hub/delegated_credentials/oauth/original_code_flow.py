# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Original code-to-pair composition over the host's durable capabilities.

The host supplies the namespace-bound Hub service, candidate input builder,
original credential provider, signing provider and live-target fence. They
are trusted application capabilities, never request-selected implementations.
Hub owns the sole decision. Lookup and committed replay are read-only until
activation of the original access session; they never prepare a replacement.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Protocol

from connection_hub.delegated_credentials.cards.card_participant import PARTICIPANT
from connection_hub.delegated_credentials.oauth.grants import ACCESS_TOKEN_TTL_SECONDS
from connection_hub.delegated_credentials.oauth_issuance import (
    OAuthIssuancePlan, OAuthIssuanceResult, original_input_digest,
)
from kdcube_ai_app.auth.bundle.session_issuance import SessionIssuanceReceipt
from kdcube_ai_app.auth.bundle.session_planned_issuance import PlannedIssuanceContext
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.http.original_code import (
    OriginalCodeExchangeHandler, OriginalTokenPair,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_exchange import (
    CodeExchangeProof, OriginalExchangeRefused, ValidatedCodeExchange, canonical, digest, hex_digest, plan_snapshot,
    validate_consumed_code_payload,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_candidate_inputs import oauth_issuance_arguments

DECISION_SCOPE = f"{PARTICIPANT}:oauth-issuance"


class OriginalExchangePending(RuntimeError):
    """Finite retryable pending result; no credential or provider detail."""

    def __init__(self) -> None:
        super().__init__("original_exchange_pending")


@dataclass(frozen=True)
class PreparedOriginalCredential:
    context: PlannedIssuanceContext
    receipt: SessionIssuanceReceipt
    record: Mapping[str, Any]
    ttl_seconds: int


class OriginalPairProvider(Protocol):
    """Durable original issuer; preparation is idempotent, read never mints.

    The refresh artifact has its own purpose and is never activated as a Bundle
    access session. Records/receipts carry no bearers and nothing stores one: ``bearers`` re-signs the
    stored claims and the flow checks each against its committed fingerprint.
    """

    async def prepare_pair(self, *, plan: OAuthIssuancePlan, access_expires_at: int) -> Mapping[str, PreparedOriginalCredential]: ...
    async def read_pair(self, *, plan: OAuthIssuancePlan, access_expires_at: int) -> Mapping[str, PreparedOriginalCredential]: ...
    async def activate_access(self, *, plan: OAuthIssuancePlan, result: OAuthIssuanceResult,
                              credential: PreparedOriginalCredential) -> SessionIssuanceReceipt: ...
    async def retire_pair(self, *, plan: OAuthIssuancePlan, result: OAuthIssuanceResult,
                          access_expires_at: int) -> None: ...
    async def bearers(self, *, plan: OAuthIssuancePlan, result: OAuthIssuanceResult,
                      pair: Mapping[str, PreparedOriginalCredential]) -> Mapping[str, str]: ...


class OriginalCodeExchangeFlow:
    def __init__(self, *, ledger: Any, grant_store: Any, hub: Any, provider: OriginalPairProvider,
                 candidate_inputs: Callable[..., Awaitable[Mapping[str, Any]]],
                 fence_target: Callable[..., Awaitable[bool]], custody: Any = None):
        self.ledger, self.grant_store, self.hub = ledger, grant_store, hub
        self.provider = provider
        self.candidate_inputs, self.fence_target = candidate_inputs, fence_target

    def handler(self) -> OriginalCodeExchangeHandler:
        return OriginalCodeExchangeHandler(tenant=self.ledger.tenant, project=self.ledger.project,
                                           exchange=self.exchange)

    @staticmethod
    def _binding(proof: CodeExchangeProof, original: Any, plan: OAuthIssuancePlan) -> ValidatedCodeExchange:
        return ValidatedCodeExchange(proof, plan.grantor_subject, original.payload_digest,
                                     original.original_input_digest, DECISION_SCOPE).validated()

    @staticmethod
    def _plan(proof: CodeExchangeProof, original: Any, plan: Any) -> OAuthIssuancePlan:
        if type(plan) is not OAuthIssuancePlan:
            raise OriginalExchangeRefused("original_exchange_plan_invalid")
        binding = OriginalCodeExchangeFlow._binding(proof, original, plan)
        snapshot = plan_snapshot(binding, plan)
        if original.decision_request_id != plan.decision_request_id:
            raise OriginalExchangeRefused("original_exchange_plan_mismatch")
        if original.plan is not None and snapshot != original.plan:
            raise OriginalExchangeRefused("original_exchange_plan_mismatch")
        if set(plan.slots) != {"access", "refresh"}:
            raise OriginalExchangeRefused("original_exchange_plan_invalid")
        return plan

    @staticmethod
    def _result(plan: OAuthIssuancePlan, result: Any) -> OAuthIssuanceResult:
        if type(result) is not OAuthIssuanceResult or result.state not in {"pending", "committed", "aborted"}:
            raise OriginalExchangeRefused("original_exchange_result_invalid")
        revision = plan.candidate_revision if result.state == "committed" else plan.base_revision
        if (result.transaction_id != plan.transaction_id or result.intent_digest != plan.intent_digest
                or result.access_id != plan.access_id or result.card_revision != revision
                or result.expires_at != plan.expires_at or result.delivery_deadline != plan.delivery_deadline
                or set(result.per_slot) != set(plan.slots)):
            raise OriginalExchangeRefused("original_exchange_result_mismatch")
        for slot in plan.slots:
            if result.per_slot[slot].effect_digest != plan.effect_digests[slot]:
                raise OriginalExchangeRefused("original_exchange_result_mismatch")
        if result.state == "committed":
            hex_digest(result.receipt_digest)
            for slot in plan.slots:
                if result.per_slot[slot].outcome not in {"applied", "superseded"}:
                    raise OriginalExchangeRefused("original_exchange_result_invalid")
                hex_digest(result.per_slot[slot].token_sha256)
        elif result.state == "aborted":
            if result.receipt_digest != "":
                raise OriginalExchangeRefused("original_exchange_result_invalid")
            for slot in plan.slots:
                outcome = result.per_slot[slot]
                if outcome.outcome not in {"pending", "released"} or (
                        outcome.outcome == "pending" and outcome.token_sha256 != ""):
                    raise OriginalExchangeRefused("original_exchange_result_invalid")
                if outcome.token_sha256:
                    hex_digest(outcome.token_sha256)
        else:
            for slot in plan.slots:
                outcome = result.per_slot[slot]
                if outcome.outcome not in {"pending", "applied", "superseded", "released"}:
                    raise OriginalExchangeRefused("original_exchange_result_invalid")
                if outcome.token_sha256:
                    hex_digest(outcome.token_sha256)
                elif outcome.outcome != "pending":
                    raise OriginalExchangeRefused("original_exchange_result_invalid")
        return result

    async def _refuse_terminal_pair(self, plan, result, expiry):
        aborted = result.state == "aborted"
        superseded = result.state == "committed" and any(
            result.per_slot[slot].outcome == "superseded" for slot in plan.slots)
        if not aborted and not superseded:
            return
        retire = getattr(self.provider, "retire_pair", None)
        if not callable(retire):
            # A missing cleanup capability is unavailable, not a completed
            # retirement. Retrying the original never selects another issuer.
            raise OriginalExchangePending()
        await retire(plan=plan, result=result, access_expires_at=expiry)
        raise OriginalExchangeRefused("original_exchange_aborted" if aborted else "original_exchange_superseded")

    @staticmethod
    def _pair(plan: OAuthIssuancePlan, expiry: int, pair: Any) -> Mapping[str, PreparedOriginalCredential]:
        if not isinstance(pair, Mapping) or set(pair) != set(plan.slots):
            raise OriginalExchangeRefused("original_exchange_pair_invalid")
        for slot in plan.slots:
            value = pair[slot]
            if type(value) is not PreparedOriginalCredential or type(value.receipt) is not SessionIssuanceReceipt:
                raise OriginalExchangeRefused("original_exchange_pair_invalid")
            expected = PlannedIssuanceContext.from_oauth_plan(
                plan, slot=slot, expires_at=expiry if slot == "access" else plan.expires_at,
            )
            if PlannedIssuanceContext.from_context(value.context) != expected:
                raise OriginalExchangeRefused("original_exchange_pair_mismatch")
            value.receipt.validated()
            if type(value.ttl_seconds) is not int or value.ttl_seconds < 1:
                raise OriginalExchangeRefused("original_exchange_pair_invalid")
        return pair

    async def exchange(self, *, proof: CodeExchangeProof, code: str) -> OriginalTokenPair:
        if type(proof) is not CodeExchangeProof or type(code) is not str or digest(code) != proof.code_sha256:
            raise OriginalExchangeRefused("original_exchange_proof_mismatch")
        proof.validated()
        if (proof.tenant, proof.project) != (self.ledger.tenant, self.ledger.project):
            raise OriginalExchangeRefused("original_exchange_namespace_mismatch")
        original = await self.ledger.read(proof)
        if original is None:
            payload = await self.grant_store.consume_auth_code(code)
            if payload is None:
                raise OriginalExchangeRefused("original_exchange_validation_failed")
            validate_consumed_code_payload(proof, payload)
            payload = json.loads(canonical(dict(payload)))
            # Only the host's consumed server payload supplies actor and intent.
            inputs = oauth_issuance_arguments(await self.candidate_inputs(payload=json.loads(canonical(payload))))
            if (inputs.get("grantor_subject") != payload.get("sub")
                    or inputs.get("client_id") != proof.client_id or "original_request_id" in inputs):
                raise OriginalExchangeRefused("original_exchange_binding_invalid")
            binding = ValidatedCodeExchange.from_consumed(
                proof, payload, original_input_digest=original_input_digest(inputs), decision_scope=DECISION_SCOPE,
            )
            original = await self.ledger.begin(binding)
            plan = await self.hub.begin_oauth_issuance(original_request_id=proof.identity, **inputs)
            self._plan(proof, original, plan)
            original = await self.ledger.pin_plan(binding, plan, access_ttl_seconds=ACCESS_TOKEN_TTL_SECONDS)
        elif original.plan is None:
            # Crash after Hub begin, before local pin: recover only the original
            # request. Missing public capability is pending, never re-begin.
            reader = getattr(self.hub, "read_oauth_issuance_plan_by_request", None)
            if not callable(reader):
                raise OriginalExchangePending()
            plan = await reader(decision_request_id=original.decision_request_id)
            if plan is None:
                raise OriginalExchangePending()
            self._plan(proof, original, plan)
            original = await self.ledger.pin_plan(self._binding(proof, original, plan), plan,
                                                  access_ttl_seconds=ACCESS_TOKEN_TTL_SECONDS)
        else:
            plan = await self.hub.read_oauth_issuance_plan(transaction_id=original.plan["transaction_id"])
            self._plan(proof, original, plan)
        expiry = original.access_expires_at
        if type(expiry) is not int:
            raise OriginalExchangeRefused("original_exchange_access_expiry_unknown")
        result = self._result(plan, await self.hub.read_oauth_issuance(transaction_id=plan.transaction_id))
        await self._refuse_terminal_pair(plan, result, expiry)
        if result.state == "committed":
            pair = self._pair(plan, expiry, await self.provider.read_pair(plan=plan, access_expires_at=expiry))
        else:
            # Any Hub-held digest proves preparation already crossed the
            # original reservation boundary. Metadata loss there
            # refuses; it never authorizes another preparation.
            read_original = any(result.per_slot[slot].token_sha256 for slot in plan.slots)
            if not read_original and await self.fence_target(plan=plan, result=result) is not True:
                raise OriginalExchangeRefused("original_exchange_target_moved")
            reader = self.provider.read_pair if read_original else self.provider.prepare_pair
            pair = self._pair(plan, expiry, await reader(plan=plan, access_expires_at=expiry))
            # A concurrent original completion may have won while preparation ran.
            result = self._result(plan, await self.hub.read_oauth_issuance(transaction_id=plan.transaction_id))
            if result.state == "pending":
                missing = []
                # Compare every existing commitment before reserving an absent
                # slot. Partial FINISH retries skip existing reservations and
                # resume the same decision's completion, even if it decided.
                for slot in plan.slots:
                    held = result.per_slot[slot]
                    if held.token_sha256:
                        if not hmac.compare_digest(held.token_sha256, pair[slot].receipt.bearer_sha256):
                            raise OriginalExchangeRefused("original_exchange_pair_mismatch")
                    elif held.outcome == "pending":
                        missing.append(slot)
                    else:
                        raise OriginalExchangeRefused("original_exchange_result_invalid")
                for slot in missing:
                    value = pair[slot]
                    await self.hub.reserve_oauth_issuance(
                        plan=plan, slot=slot, token_sha256=value.receipt.bearer_sha256,
                        record=value.record, ttl_seconds=value.ttl_seconds,
                    )
                # ONLY Hub's existing decision owner may prepare/commit/finish.
                result = self._result(plan, await self.hub.complete_oauth_issuance(
                    transaction_id=plan.transaction_id,
                    expect={slot: pair[slot].receipt.bearer_sha256 for slot in plan.slots},
                ))
        if result.state == "pending":
            raise OriginalExchangePending()
        await self._refuse_terminal_pair(plan, result, expiry)
        if result.state != "committed":
            raise OriginalExchangeRefused("original_exchange_aborted")
        for slot in plan.slots:
            if not hmac.compare_digest(result.per_slot[slot].token_sha256, pair[slot].receipt.bearer_sha256):
                raise OriginalExchangeRefused("original_exchange_pair_mismatch")
        # Re-read durable expiry and fence before any access activation/secret
        # delivery. The host fence must check the exact original live target.
        current = await self.ledger.read(proof)
        if current is None or current.plan != plan.to_dict() or current.access_expires_at != expiry:
            raise OriginalExchangeRefused("original_exchange_plan_mismatch")
        if await self.fence_target(plan=plan, result=result) is not True:
            raise OriginalExchangeRefused("original_exchange_target_moved")
        activated = await self.provider.activate_access(plan=plan, result=result, credential=pair["access"])
        if (type(activated) is not SessionIssuanceReceipt
                or (activated.session_id, activated.secret_ref, activated.bearer_sha256)
                != (pair["access"].receipt.session_id, pair["access"].receipt.secret_ref,
                    pair["access"].receipt.bearer_sha256)):
            raise OriginalExchangeRefused("original_exchange_pair_mismatch")
        # Re-signed from the stored claims (no custody); each must be exactly the committed fingerprint.
        resigned = await self.provider.bearers(plan=plan, result=result, pair=pair)
        bearers = {}
        for slot in plan.slots:
            bearer = resigned.get(slot) if isinstance(resigned, Mapping) else None
            if (type(bearer) is not str or not bearer or not hmac.compare_digest(
                    hashlib.sha256(bearer.encode()).hexdigest(), pair[slot].receipt.bearer_sha256)):
                raise OriginalExchangeRefused("original_exchange_signing_mismatch")
            bearers[slot] = bearer
        # Provider reads can yield while the target changes. Final fence and
        # ledger read precede publication; serving still follows live authority.
        final = await self.ledger.read(proof)
        if final is None or final.plan != plan.to_dict() or final.access_expires_at != expiry:
            raise OriginalExchangeRefused("original_exchange_plan_mismatch")
        if await self.fence_target(plan=plan, result=result) is not True:
            raise OriginalExchangeRefused("original_exchange_target_moved")
        return OriginalTokenPair(
            proof_fingerprint=proof.fingerprint, access_token=bearers["access"], refresh_token=bearers["refresh"],
            access_expires_at=expiry, delivery_deadline=plan.delivery_deadline,
            scopes=tuple(sorted({scope for values in plan.resource_grants.values() for scope in values})),
            access_id=plan.access_id, card_kind=str(pair["refresh"].record.get("card_kind") or ""),
        )
