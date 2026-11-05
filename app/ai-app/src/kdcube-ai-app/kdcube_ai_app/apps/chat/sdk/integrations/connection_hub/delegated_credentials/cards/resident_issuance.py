# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Resident access issuance over the existing inactive original-session adapter.

The trusted Hub host authenticates the stored plan, decision and exact live
Card before activation. This adapter creates no Card decision or custody row.
Its activated handle result stays on the trusted side for the credential
effect to install through the deployment's resident handle store.
"""

from __future__ import annotations

from typing import Any, Mapping

from connection_hub.delegated_credentials.cards.identity import (
    CARD_KIND_AGENT, RESIDENT_CLIENT_PREFIX, stable_resident_access_id,
)
from connection_hub.delegated_credentials.cards.model import CardCredentialHandles
from connection_hub.delegated_credentials.oauth_issuance import OAuthIssuancePlan, OAuthIssuanceResult
from kdcube_ai_app.auth.bundle.session_issuance import SessionIssuanceRefused
from kdcube_ai_app.auth.bundle.session_planned_issuance import (
    AppliedIssuanceContext, PlannedIssuanceContext, TerminalIssuanceContext,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.grants import (
    PreparedDelegatedClientAccess, _access_context, activate_prepared_delegated_client_access_token,
    prepare_delegated_client_access_token, read_delegated_client_access_bearer,
    read_prepared_delegated_client_access_token,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_code_flow import (
    PreparedOriginalCredential,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_pair_provider import (
    original_credential_record,
)


class ResidentCredentialProvider:
    """Access-only originals for a host-authenticated resident Card plan.

    Constructor inputs are trusted host composition. A typed plan or result
    constructed from caller JSON carries no authorization by itself.
    """

    def __init__(self, *, authority_factory: Any) -> None:
        if not callable(authority_factory):
            raise SessionIssuanceRefused("issuance_store_unavailable")
        self._authority_factory = authority_factory

    @staticmethod
    def _context(plan: object) -> PlannedIssuanceContext:
        try:
            valid = (type(plan) is OAuthIssuancePlan and tuple(plan.slots) == ("access",)
                and isinstance(plan.effect_digests, Mapping) and "access" in plan.effect_digests
                and type(plan.client_id) is str
                and plan.client_id.startswith(RESIDENT_CLIENT_PREFIX)
                and plan.access_id == stable_resident_access_id(plan.grantor_subject, plan.client_id))
        except (AttributeError, TypeError, ValueError):
            valid = False
        if not valid:
            raise SessionIssuanceRefused("resident_issuance_plan_invalid")
        return _access_context(PlannedIssuanceContext.from_oauth_plan(plan, slot="access", expires_at=plan.expires_at))

    async def prepare(self, *, plan: OAuthIssuancePlan) -> PreparedOriginalCredential:
        """Prepare fixed claims inactive and return only the original metadata."""
        self._context(plan)
        await prepare_delegated_client_access_token(
            plan=plan, expires_at=plan.expires_at, authority_factory=self._authority_factory)
        return await self.read(plan=plan)

    async def read(self, *, plan: OAuthIssuancePlan) -> PreparedOriginalCredential:
        """Read the pinned original without preparing, signing or touching custody."""
        self._context(plan)
        snapshot = await read_prepared_delegated_client_access_token(
            plan=plan, expires_at=plan.expires_at, authority_factory=self._authority_factory)
        record = original_credential_record(plan=plan, context=snapshot.context, receipt=snapshot.receipt,
                                            issued_at=snapshot.issued_at, card_kind=CARD_KIND_AGENT)
        return PreparedOriginalCredential(snapshot.context, snapshot.receipt, record,
                                           plan.expires_at - snapshot.issued_at)

    async def activate(self, *, plan: OAuthIssuancePlan, result: OAuthIssuanceResult) -> CardCredentialHandles:
        """Activate the committed original; the effect then installs its resident handles."""
        context = self._context(plan)
        if (type(result) is not OAuthIssuanceResult or not isinstance(result.per_slot, Mapping)
                or set(result.per_slot) != {"access"}):
            raise SessionIssuanceRefused("issuance_result_invalid")
        AppliedIssuanceContext.from_oauth_result(context, result)
        original = await self.read(plan=plan)
        prepared = PreparedDelegatedClientAccess(original.context, original.receipt)
        activated = await activate_prepared_delegated_client_access_token(
            prepared=prepared, result=result, authority_factory=self._authority_factory)
        bearer = await read_delegated_client_access_bearer(
            prepared=prepared, result=result, authority_factory=self._authority_factory)
        return CardCredentialHandles(access_id=plan.access_id, access_token=bearer, session_id=activated.session_id)

    async def retire(self, *, plan: OAuthIssuancePlan, result: OAuthIssuanceResult) -> None:
        """Retire an authenticated aborted or superseded INACTIVE original.

        This operation never retires a previously activated session.
        """
        context = self._context(plan)
        if (type(result) is not OAuthIssuanceResult or not isinstance(result.per_slot, Mapping)
                or set(result.per_slot) != {"access"}):
            raise SessionIssuanceRefused("issuance_result_invalid")
        terminal = TerminalIssuanceContext.from_oauth_result(context, result)
        authority = self._authority_factory(tenant=plan.tenant, project=plan.project)
        if (getattr(authority, "tenant", None), getattr(authority, "project", None)) != (plan.tenant, plan.project):
            raise SessionIssuanceRefused("issuance_namespace_mismatch")
        retire = getattr(authority, "retire_prepared_bound_session", None)
        if not callable(retire):
            raise SessionIssuanceRefused("issuance_store_unavailable")
        await retire(terminal)

    async def retire_previous(self, *, plan: OAuthIssuancePlan, result: OAuthIssuanceResult,
                              session_id: str, token_sha256: str) -> None:
        """End the old activated session named by a trusted committed rotation.

        The Hub authenticates the decision, Card fence and pinned old-handle
        effect before calling. The typed result is not authorization by itself.
        Only this one session is logged out; no user or sibling is invalidated.
        """
        context = self._context(plan)
        if (type(result) is not OAuthIssuanceResult or not isinstance(result.per_slot, Mapping)
                or set(result.per_slot) != {"access"}):
            raise SessionIssuanceRefused("issuance_result_invalid")
        if type(result) is OAuthIssuanceResult and result.state == "committed" and result.per_slot.get("access") \
                and result.per_slot["access"].outcome == "superseded":
            TerminalIssuanceContext.from_oauth_result(context, result)
        else:
            AppliedIssuanceContext.from_oauth_result(context, result)
        if (type(session_id) is not str or not session_id or session_id != session_id.strip()
                or len(session_id) > 256 or type(token_sha256) is not str or len(token_sha256) != 64
                or token_sha256.strip("0123456789abcdef")
                or token_sha256 == result.per_slot["access"].token_sha256):
            raise SessionIssuanceRefused("resident_rotation_previous_invalid")
        authority = self._authority_factory(tenant=plan.tenant, project=plan.project)
        if (getattr(authority, "tenant", None), getattr(authority, "project", None)) != (plan.tenant, plan.project):
            raise SessionIssuanceRefused("issuance_namespace_mismatch")
        logout = getattr(authority, "logout", None)
        if not callable(logout):
            raise SessionIssuanceRefused("issuance_store_unavailable")
        outcome = await logout(session_id=session_id)
        if type(outcome) is not bool:
            raise SessionIssuanceRefused("issuance_store_unavailable")
