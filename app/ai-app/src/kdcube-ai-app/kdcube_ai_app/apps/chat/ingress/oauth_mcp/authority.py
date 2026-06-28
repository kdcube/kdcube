# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""OAuth/MCP authority adapter for the Connection Hub SDK registry."""

from __future__ import annotations

import secrets
import time
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.ingress.oauth_mcp.grants import (
    CONVERSATIONS_READ_PERMISSION,
    FEEDBACK_READER_ROLE,
    integration_subject,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry import (
    OAUTH_MCP_AUTHENTICATOR_ID,
    OAUTH_MCP_AUTHORITY_ID,
    AuthorityProviderSpec,
    AuthorityRegistry,
    AuthorityResolution,
    CredentialEnvelope,
    RedisAuthorityDiscovery,
)


OAUTH_MCP_AUDIENCE = "kdcube:mcp"
OAUTH_MCP_CREDENTIAL_KIND = "delegated_client_access"


def oauth_mcp_authority_spec(*, bundle_id: str = "platform:oauth_mcp") -> AuthorityProviderSpec:
    return AuthorityProviderSpec(
        authority_id=OAUTH_MCP_AUTHORITY_ID,
        provider_id=OAUTH_MCP_AUTHORITY_ID,
        bundle_id=bundle_id,
        label="KDCube OAuth/MCP delegated client access",
        credential_kinds=(OAUTH_MCP_CREDENTIAL_KIND,),
        audiences=(OAUTH_MCP_AUDIENCE,),
        authenticators=(OAUTH_MCP_AUTHENTICATOR_ID,),
        transports=("local", "redis_registry"),
        metadata={
            "grant_model": "delegated_connection",
            "roles": [FEEDBACK_READER_ROLE],
            "permissions": [CONVERSATIONS_READ_PERMISSION],
        },
    )


def build_oauth_mcp_credential(
    *,
    grantor_subject: str,
    client_id: str,
    scopes: list[str] | tuple[str, ...],
    tools: list[str] | tuple[str, ...],
    tenant: str,
    project: str,
    expires_in: int,
    issued_at: int | None = None,
) -> CredentialEnvelope:
    iat = int(issued_at if issued_at is not None else time.time())
    subject = integration_subject(grantor_subject)
    return CredentialEnvelope(
        credential_id="cred_" + secrets.token_urlsafe(18),
        credential_kind=OAUTH_MCP_CREDENTIAL_KIND,
        issuer_authority_id=OAUTH_MCP_AUTHORITY_ID,
        issuer_authenticator_id=OAUTH_MCP_AUTHENTICATOR_ID,
        subject=subject,
        tenant=str(tenant or "").strip(),
        project=str(project or "").strip(),
        audience=OAUTH_MCP_AUDIENCE,
        iat=iat,
        exp=iat + max(1, int(expires_in or 1)),
        attrs={
            "grantor_subject": str(grantor_subject or "").strip(),
            "client_id": str(client_id or "").strip(),
            "scopes": list(scopes or []),
            "tools": list(tools or []),
        },
    )


class OAuthMcpAuthorityProvider:
    """Verifier for OAuth/MCP delegated client access credentials.

    The authoritative token check still happens through the existing bearer
    token/session + GrantStore path. This provider is the SDK-facing authority
    adapter: it interprets the stored authority envelope and exposes a unified
    `AuthorityResolution`.
    """

    def __init__(self, *, spec: AuthorityProviderSpec | None = None) -> None:
        self.spec = spec or oauth_mcp_authority_spec()

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
                error="oauth_mcp_authority_mismatch",
                message="Credential is not an OAuth/MCP delegated-client credential.",
            )
        ctx = dict(context or {})
        grant_record = ctx.get("grant_record") if isinstance(ctx.get("grant_record"), Mapping) else {}
        tools = list(grant_record.get("tools") or envelope.attrs.get("tools") or [])
        return AuthorityResolution(
            ok=True,
            authority_id=OAUTH_MCP_AUTHORITY_ID,
            authenticator_id=OAUTH_MCP_AUTHENTICATOR_ID,
            subject=envelope.subject,
            actor_user_id=envelope.subject,
            roles=(FEEDBACK_READER_ROLE,),
            permissions=(CONVERSATIONS_READ_PERMISSION,),
            grants=tuple(tools),
            credential=envelope,
            metadata={
                "client_id": envelope.attrs.get("client_id"),
                "grantor_subject": envelope.attrs.get("grantor_subject"),
                "scopes": list(envelope.attrs.get("scopes") or []),
                "token_present": bool(token),
            },
        )


async def register_oauth_mcp_authority(
    *,
    registry: AuthorityRegistry | None = None,
    discovery: RedisAuthorityDiscovery | None = None,
    provider: OAuthMcpAuthorityProvider | None = None,
) -> OAuthMcpAuthorityProvider:
    authority_provider = provider or OAuthMcpAuthorityProvider()
    if registry is not None and registry.get(authority_provider.spec.authority_id) is None:
        registry.register(authority_provider)
    if discovery is not None:
        await discovery.register_provider(authority_provider.spec)
    return authority_provider


__all__ = [
    "OAUTH_MCP_AUDIENCE",
    "OAUTH_MCP_CREDENTIAL_KIND",
    "OAuthMcpAuthorityProvider",
    "build_oauth_mcp_credential",
    "oauth_mcp_authority_spec",
    "register_oauth_mcp_authority",
]
