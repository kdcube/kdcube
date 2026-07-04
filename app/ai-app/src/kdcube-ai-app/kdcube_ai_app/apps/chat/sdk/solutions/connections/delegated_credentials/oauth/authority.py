# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""OAuth delegated credential authority adapter for the Connection Hub SDK registry."""

from __future__ import annotations

import secrets
import time
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.grants import (
    DELEGATED_CLIENT_ROLE,
    integration_subject,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry import (
    DELEGATED_CLIENT_AUTHENTICATOR_ID,
    DELEGATED_CLIENT_AUTHORITY_ID,
    AuthorityProviderSpec,
    AuthorityRegistry,
    AuthorityResolution,
    CredentialEnvelope,
    RedisAuthorityDiscovery,
)


DELEGATED_CLIENT_AUDIENCE = "kdcube:delegated_client"
DELEGATED_CLIENT_CREDENTIAL_KIND = "delegated_client_access"


def delegated_client_authority_spec(*, bundle_id: str = "platform:delegated_client") -> AuthorityProviderSpec:
    return AuthorityProviderSpec(
        authority_id=DELEGATED_CLIENT_AUTHORITY_ID,
        provider_id=DELEGATED_CLIENT_AUTHORITY_ID,
        bundle_id=bundle_id,
        label="KDCube delegated client access",
        credential_kinds=(DELEGATED_CLIENT_CREDENTIAL_KIND,),
        audiences=(DELEGATED_CLIENT_AUDIENCE,),
        authenticators=(DELEGATED_CLIENT_AUTHENTICATOR_ID,),
        transports=("local", "redis_registry"),
        metadata={
            "grant_model": "delegated_connection",
        },
    )


def build_delegated_client_credential(
    *,
    grantor_subject: str,
    client_id: str,
    scopes: list[str] | tuple[str, ...],
    tools: list[str] | tuple[str, ...],
    tenant: str,
    project: str,
    expires_in: int,
    resource: str | None = None,
    identity_scope: str = "",
    issued_at: int | None = None,
) -> CredentialEnvelope:
    iat = int(issued_at if issued_at is not None else time.time())
    subject = integration_subject(grantor_subject, client_id=client_id)
    return CredentialEnvelope(
        credential_id="cred_" + secrets.token_urlsafe(18),
        credential_kind=DELEGATED_CLIENT_CREDENTIAL_KIND,
        issuer_authority_id=DELEGATED_CLIENT_AUTHORITY_ID,
        issuer_authenticator_id=DELEGATED_CLIENT_AUTHENTICATOR_ID,
        subject=subject,
        tenant=str(tenant or "").strip(),
        project=str(project or "").strip(),
        audience=DELEGATED_CLIENT_AUDIENCE,
        iat=iat,
        exp=iat + max(1, int(expires_in or 1)),
        attrs={
            "grantor_subject": str(grantor_subject or "").strip(),
            "client_id": str(client_id or "").strip(),
            "scopes": list(scopes or []),
            "tools": list(tools or []),
            "resource": str(resource or "").strip(),
            "identity_scope": str(identity_scope or "grantor").strip() or "grantor",
        },
    )


class OAuthDelegatedClientAuthorityProvider:
    """Verifier for OAuth-issued delegated client access credentials.

    The authoritative token check still happens through the existing bearer
    token/session + GrantStore path. This provider is the SDK-facing authority
    adapter: it interprets the stored credential envelope and exposes a unified
    `AuthorityResolution`.
    """

    def __init__(self, *, spec: AuthorityProviderSpec | None = None) -> None:
        self.spec = spec or delegated_client_authority_spec()

    async def verify_credential(
        self,
        credential: CredentialEnvelope | Mapping[str, Any],
        *,
        token: str = "",
        context: Mapping[str, Any] | None = None,
    ) -> AuthorityResolution:
        envelope = CredentialEnvelope.coerce(credential)
        if not self.spec.matches(envelope):
            return AuthorityResolution(
                ok=False,
                authority_id=envelope.issuer_authority_id,
                authenticator_id=envelope.issuer_authenticator_id,
                credential=envelope,
                error="delegated_client_authority_mismatch",
                message="Credential is not an OAuth-issued delegated-client credential.",
            )
        ctx = dict(context or {})
        grant_record = ctx.get("grant_record") if isinstance(ctx.get("grant_record"), Mapping) else {}
        tools = list(grant_record.get("tools") or envelope.attrs.get("tools") or [])
        scopes = list(envelope.attrs.get("scopes") or [])
        identity_scope = envelope.attrs.get("identity_scope") or "grantor"
        return AuthorityResolution(
            ok=True,
            authority_id=DELEGATED_CLIENT_AUTHORITY_ID,
            authenticator_id=DELEGATED_CLIENT_AUTHENTICATOR_ID,
            subject=envelope.subject,
            actor_user_id=envelope.subject,
            roles=(DELEGATED_CLIENT_ROLE,),
            permissions=tuple(scopes),
            grants=tuple(scopes),
            credential=envelope,
            metadata={
                "client_id": envelope.attrs.get("client_id"),
                "grantor_subject": envelope.attrs.get("grantor_subject"),
                "scopes": scopes,
                "tools": tools,
                "resource": envelope.attrs.get("resource"),
                "identity_scope": identity_scope,
                "token_present": bool(token),
            },
        )


async def register_delegated_client_authority(
    *,
    registry: AuthorityRegistry | None = None,
    discovery: RedisAuthorityDiscovery | None = None,
    provider: OAuthDelegatedClientAuthorityProvider | None = None,
) -> OAuthDelegatedClientAuthorityProvider:
    authority_provider = provider or OAuthDelegatedClientAuthorityProvider()
    if registry is not None and registry.get(authority_provider.spec.authority_id) is None:
        registry.register(authority_provider)
    if discovery is not None:
        await discovery.register_provider(authority_provider.spec)
    return authority_provider


__all__ = [
    "DELEGATED_CLIENT_AUDIENCE",
    "DELEGATED_CLIENT_CREDENTIAL_KIND",
    "OAuthDelegatedClientAuthorityProvider",
    "build_delegated_client_credential",
    "delegated_client_authority_spec",
    "register_delegated_client_authority",
]
