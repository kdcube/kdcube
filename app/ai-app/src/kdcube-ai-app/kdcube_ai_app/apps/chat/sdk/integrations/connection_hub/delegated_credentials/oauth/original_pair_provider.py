# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Concrete original access/refresh provider over public authorities; no bearer is kept in custody.

Operator, 2026-10-09: "i need the stronger version now". Both bearers are deterministic signatures over the
claims PostgreSQL stores; delivery and replay re-sign them and must match the sealed fingerprints.
"""
from __future__ import annotations

from typing import Any

from connection_hub.authority_registry import CredentialEnvelope, DELEGATED_CLIENT_AUTHENTICATOR_ID
from connection_hub.delegated_credentials.oauth.authority import DELEGATED_CLIENT_AUDIENCE, DELEGATED_CLIENT_CREDENTIAL_KIND
from connection_hub.delegated_credentials.oauth.grants import ACCESS_TOKEN_TTL_SECONDS
from connection_hub.delegated_credentials.oauth_issuance import OAuthIssuancePlan, OAuthIssuanceResult
from kdcube_ai_app.auth.bundle.session_planned_issuance import (
    AppliedIssuanceContext, PlannedIssuanceContext, TerminalIssuanceContext,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_exchange import (
    OriginalExchangeRefused, text,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_code_flow import PreparedOriginalCredential
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_refresh_issuer import (
    HmacOriginalRefreshSigner, OriginalRefreshIssuer,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.original_refresh_store import PostgresOriginalRefreshStore
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.grants import (
    PreparedDelegatedClientAccess, activate_prepared_delegated_client_access_token,
    prepare_delegated_client_access_token, read_delegated_client_access_bearer,
    read_prepared_delegated_client_access_token,
)


class OriginalCredentialPairProvider:
    """Host-only binding; constructor inputs never come from client JSON.

    card_kind and refresh_ttl_seconds are the original hosting flow's policy,
    checked and retained with the refresh signing input. The host owns the signing keys (the refresh
    signing secret and the bundle session secret, read through the secrets manager); no bearer custody.
    """
    def __init__(self, *, refresh_store: PostgresOriginalRefreshStore,
                 refresh_signer: HmacOriginalRefreshSigner, card_kind: str,
                 refresh_ttl_seconds: int, authority_factory: Any,
                 custody: Any = None, custody_namespace: str | None = None):
        text(card_kind)
        if not callable(authority_factory):
            raise OriginalExchangeRefused("original_exchange_authority_not_bound")
        self.authority_factory = authority_factory
        self.refresh = OriginalRefreshIssuer(store=refresh_store, signer=refresh_signer,
                                            card_kind=card_kind, ttl_seconds=refresh_ttl_seconds)
        self.card_kind = card_kind

    def _record(self, *, plan, context, receipt, issued_at):
        authority = {"operations": list(plan.operations),
                     "resource_grants": {key: list(value) for key, value in plan.resource_grants.items()},
                     "resource_operations": {key: list(value) for key, value in plan.resource_operations.items()}}
        attrs = {**authority, "grantor_subject": plan.grantor_subject, "client_id": plan.client_id,
                 "registry_access_id": plan.access_id}
        credential = CredentialEnvelope(
            credential_id="cred_" + context.identity, credential_kind=DELEGATED_CLIENT_CREDENTIAL_KIND,
            issuer_authority_id=plan.credential_issuer, issuer_authenticator_id=DELEGATED_CLIENT_AUTHENTICATOR_ID,
            subject=plan.credential_subject, tenant=plan.tenant, project=plan.project,
            audience=DELEGATED_CLIENT_AUDIENCE, session_id=receipt.session_id,
            attrs=attrs, iat=issued_at, exp=context.expires_at,
        )
        return {**authority, "credential": credential.to_dict(), "sub": plan.grantor_subject,
                "client_id": plan.client_id, "registry_access_id": plan.access_id,
                "card_kind": self.card_kind, "resources": sorted(plan.resource_grants),
                "scopes": sorted({scope for values in plan.resource_grants.values() for scope in values})}

    async def _access(self, *, plan, access_expires_at):
        snapshot = await read_prepared_delegated_client_access_token(
            plan=plan, expires_at=access_expires_at, authority_factory=self.authority_factory)
        return PreparedOriginalCredential(snapshot.context, snapshot.receipt,
            self._record(plan=plan, context=snapshot.context, receipt=snapshot.receipt, issued_at=snapshot.issued_at),
            ACCESS_TOKEN_TTL_SECONDS)

    def _refresh(self, *, plan, original):
        receipt = original.receipt()
        return PreparedOriginalCredential(original.context, receipt,
            self._record(plan=plan, context=original.context, receipt=receipt, issued_at=original.claims["iat"]),
            self.refresh.ttl_seconds)

    async def prepare_pair(self, *, plan, access_expires_at):
        await prepare_delegated_client_access_token(plan=plan, expires_at=access_expires_at,
            authority_factory=self.authority_factory)
        access = await self._access(plan=plan, access_expires_at=access_expires_at)
        original = await self.refresh.prepare(plan=plan)
        return {"access": access, "refresh": self._refresh(plan=plan, original=original)}

    async def read_pair(self, *, plan, access_expires_at):
        # Both readers expose only existing original metadata and receipts.
        access = await self._access(plan=plan, access_expires_at=access_expires_at)
        original = await self.refresh.read(plan=plan)
        return {"access": access, "refresh": self._refresh(plan=plan, original=original)}

    async def activate_access(self, *, plan, result, credential):
        # Pin the applied refresh outcome before crossing the access activation
        # boundary. This records applied protection, not recipient delivery.
        await self.refresh.protect_applied(plan=plan, result=result)
        return await activate_prepared_delegated_client_access_token(
            prepared=PreparedDelegatedClientAccess(credential.context, credential.receipt),
            result=result, authority_factory=self.authority_factory)

    async def bearers(self, *, plan, result, pair) -> dict[str, str]:
        """Both committed bearers, re-signed from their stored claims; nothing is read from custody."""
        access = pair["access"]
        return {
            "access": await read_delegated_client_access_bearer(
                prepared=PreparedDelegatedClientAccess(access.context, access.receipt),
                result=result, authority_factory=self.authority_factory),
            "refresh": await self.refresh.bearer(plan=plan),
        }

    async def retire_pair(self, *, plan, result, access_expires_at):
        if (type(plan) is not OAuthIssuancePlan or type(result) is not OAuthIssuanceResult
                or set(plan.slots) != {"access", "refresh"} or set(result.per_slot) != set(plan.slots)
                or (plan.tenant, plan.project) != (self.refresh.store.tenant, self.refresh.store.project)):
            raise OriginalExchangeRefused("original_exchange_result_invalid")
        # Validate the complete result before crossing either cleanup boundary.
        # An applied slot is preserved; only the original terminal slot is
        # eligible for retirement. No access activation occurs on this path.
        contexts = {}
        for slot in ("access", "refresh"):
            context = PlannedIssuanceContext.from_oauth_plan(plan, slot=slot,
                expires_at=access_expires_at if slot == "access" else plan.expires_at)
            contexts[slot] = (AppliedIssuanceContext.from_oauth_result(context, result)
                if result.state == "committed" and result.per_slot[slot].outcome == "applied"
                else TerminalIssuanceContext.from_oauth_result(context, result))
        if not any(isinstance(value, TerminalIssuanceContext) for value in contexts.values()):
            raise OriginalExchangeRefused("original_exchange_result_not_terminal")
        # Attempt each original cleanup even if the other provider is unavailable.
        error = None
        for slot in ("access", "refresh"):
            try:
                context = contexts[slot]
                if isinstance(context, AppliedIssuanceContext):
                    if slot == "refresh":
                        await self.refresh.protect_applied(plan=plan, result=result)
                    continue
                if slot == "refresh":
                    await self.refresh.retire(plan=plan, terminal=context)
                else:
                    authority = self.authority_factory(tenant=plan.tenant, project=plan.project)
                    if (getattr(authority, "tenant", None), getattr(authority, "project", None)) != (plan.tenant, plan.project):
                        raise OriginalExchangeRefused("original_exchange_namespace_mismatch")
                    await authority.retire_prepared_bound_session(context)
            except Exception as exc:
                error = error or exc
        if error is not None:
            raise OriginalExchangeRefused("original_exchange_retirement_unavailable") from None
