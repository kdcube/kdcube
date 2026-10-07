# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""KDCube session-authority adapter for Connection Hub delegated OAuth grants."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Mapping

from connection_hub.authority_registry import DELEGATED_CLIENT_AUTHORITY_ID
from connection_hub.delegated_credentials.oauth.grants import (
    ACCESS_TOKEN_TTL_SECONDS,
    DELEGATED_CLIENT_ROLE,
    SessionAuthorityFactory,
    integration_subject,
    mint_delegated_client_access_token as _portable_mint,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.config import (
    oauth_delegated_config,
)
from kdcube_ai_app.auth.bundle import get_bundle_session_authority
from kdcube_ai_app.auth.bundle.session_issuance import SessionIssuanceReceipt, SessionIssuanceRefused
from kdcube_ai_app.auth.bundle.session_planned_issuance import AppliedIssuanceContext, PlannedIssuanceContext


def oauth_tenant_project(source: Any | None = None) -> tuple[str, str]:
    config = oauth_delegated_config(source)
    return config.tenant, config.project


async def mint_delegated_client_access_token(
    sub: str,
    scopes: List[str],
    *,
    authority: Any = None,
    authority_factory: SessionAuthorityFactory | None = None,
    client_id: str = "",
    operations: List[str] | None = None,
    credential: Mapping[str, Any] | None = None,
    ttl_seconds: int = ACCESS_TOKEN_TTL_SECONDS,
) -> dict:
    resolved_factory = authority_factory
    config = None
    if authority is None:
        resolved_factory = resolved_factory or get_bundle_session_authority
        config = oauth_delegated_config()
    return await _portable_mint(
        sub,
        scopes,
        authority=authority,
        authority_factory=resolved_factory,
        config=config,
        client_id=client_id,
        operations=operations,
        credential=credential,
        ttl_seconds=ttl_seconds,
    )


@dataclass(frozen=True)
class PreparedDelegatedClientAccess:
    """Inactive original coordinates, never a bearer or authorization proof."""

    context: PlannedIssuanceContext
    receipt: SessionIssuanceReceipt


def _access_context(context: object) -> PlannedIssuanceContext:
    bound = PlannedIssuanceContext.from_context(context)
    if (bound.slot != "access" or bound.actor.startswith("integration:")
            or bound.credential_issuer != DELEGATED_CLIENT_AUTHORITY_ID
            or bound.credential_subject != integration_subject(bound.actor, client_id=bound.client_id)):
        raise SessionIssuanceRefused("issuance_identity_conflict")
    return bound


def _planned_authority(bound: PlannedIssuanceContext, *, authority: Any,
                       authority_factory: SessionAuthorityFactory | None) -> Any:
    if authority is None:
        factory = authority_factory or get_bundle_session_authority
        try:
            authority = factory(tenant=bound.tenant, project=bound.project)
        except Exception:
            raise SessionIssuanceRefused("issuance_store_unavailable") from None
    if (getattr(authority, "tenant", None) != bound.tenant
            or getattr(authority, "project", None) != bound.project):
        raise SessionIssuanceRefused("issuance_namespace_mismatch")
    if not all(callable(getattr(authority, name, None)) for name in
               ("prepare_bound_session", "activate_prepared_bound_session")):
        raise SessionIssuanceRefused("issuance_store_unavailable")
    return authority


async def prepare_delegated_client_access_token(
    *, plan: object, expires_at: int, custody: Any, authority: Any = None,
    authority_factory: SessionAuthorityFactory | None = None,
) -> PreparedDelegatedClientAccess:
    """Prepare the authenticated original plan without ordinary login or delivery.

    The host authenticates and pins the complete Hub plan before this call,
    including its first captured access expiry. Replays supply that SAME expiry,
    never current time plus a TTL. A typed plan is data, not authentication;
    request JSON and a caller-supplied plan are not trusted host inputs.
    """
    # Older portable packages may still use the ordinary delegated minter.
    # They cannot silently take its active-login path for planned issuance.
    try:
        from connection_hub.delegated_credentials.oauth_issuance import OAuthIssuancePlan
    except ImportError:
        raise SessionIssuanceRefused("issuance_plan_api_unavailable") from None
    if type(plan) is not OAuthIssuancePlan:
        raise SessionIssuanceRefused("issuance_context_invalid")
    bound = _access_context(PlannedIssuanceContext.from_oauth_plan(plan, slot="access", expires_at=expires_at))
    try:
        if (not isinstance(plan.resource_grants, Mapping)
                or any(type(key) is not str or not key for key in plan.resource_grants)
                or any(not isinstance(items, (tuple, list)) for items in plan.resource_grants.values())
                or any(type(scope) is not str or not scope or scope != scope.strip()
                       for items in plan.resource_grants.values() for scope in items)):
            raise ValueError
        permissions = sorted({scope for items in plan.resource_grants.values() for scope in items})
    except (TypeError, ValueError):
        raise SessionIssuanceRefused("issuance_authority_invalid") from None
    resolved = _planned_authority(bound, authority=authority, authority_factory=authority_factory)
    receipt = await resolved.prepare_bound_session(
        bound, user_id=bound.credential_subject, roles=[DELEGATED_CLIENT_ROLE],
        permissions=permissions, custody=custody,
    )
    if type(receipt) is not SessionIssuanceReceipt:
        raise SessionIssuanceRefused("session_issuance_result_invalid")
    return PreparedDelegatedClientAccess(bound, receipt.validated())


async def activate_prepared_delegated_client_access_token(
    *, prepared: PreparedDelegatedClientAccess, result: object, custody: Any,
    authority: Any = None, authority_factory: SessionAuthorityFactory | None = None,
) -> SessionIssuanceReceipt:
    """Activate only the host-authenticated original committed/applied result.

    The host reads the original result and fences its target. This adapter does
    not complete a Hub decision, resolve a replacement Card, or publish bearer
    material. The public session authority verifies custody and activates the
    original under its PostgreSQL lock; no ordinary minter fallback exists.
    """
    if type(prepared) is not PreparedDelegatedClientAccess:
        raise SessionIssuanceRefused("issuance_context_invalid")
    bound = _access_context(prepared.context)
    if type(prepared.receipt) is not SessionIssuanceReceipt:
        raise SessionIssuanceRefused("session_issuance_result_invalid")
    receipt = prepared.receipt.validated()
    applied = AppliedIssuanceContext.from_oauth_result(bound, result)
    if applied.token_sha256 != receipt.bearer_sha256:
        raise SessionIssuanceRefused("issuance_commitment_mismatch")
    resolved = _planned_authority(bound, authority=authority, authority_factory=authority_factory)
    activated = await resolved.activate_prepared_bound_session(applied, custody=custody)
    if type(activated) is not SessionIssuanceReceipt:
        raise SessionIssuanceRefused("session_issuance_result_invalid")
    activated.validated()
    if (activated.session_id != receipt.session_id or activated.secret_ref != receipt.secret_ref
            or activated.bearer_sha256 != receipt.bearer_sha256):
        raise SessionIssuanceRefused("issuance_identity_conflict")
    return activated


__all__ = [
    "ACCESS_TOKEN_TTL_SECONDS",
    "DELEGATED_CLIENT_ROLE",
    "SessionAuthorityFactory",
    "integration_subject",
    "mint_delegated_client_access_token",
    "PreparedDelegatedClientAccess",
    "prepare_delegated_client_access_token",
    "activate_prepared_delegated_client_access_token",
    "oauth_tenant_project",
]
