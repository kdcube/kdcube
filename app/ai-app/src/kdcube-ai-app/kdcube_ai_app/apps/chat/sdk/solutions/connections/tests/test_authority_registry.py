# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry import (
    AuthorityProviderSpec,
    AuthorityRegistry,
    AuthorityResolution,
    CredentialEnvelope,
    authority_provider_spec_from_declaration,
)


class _Provider:
    spec = AuthorityProviderSpec(
        authority_id="example.authority",
        credential_kinds=("authority_access",),
        audiences=("bundle:example",),
        authenticators=("example.oauth",),
    )

    async def verify_credential(self, credential, *, token="", context=None):
        envelope = CredentialEnvelope.coerce(credential)
        return AuthorityResolution(
            ok=True,
            authority_id=envelope.issuer_authority_id,
            authenticator_id=envelope.issuer_authenticator_id,
            subject=envelope.subject,
            credential=envelope,
            metadata={"token_present": bool(token), "context": dict(context or {})},
        )


async def test_local_authority_registry_routes_by_credential_envelope():
    registry = AuthorityRegistry()
    registry.register(_Provider())

    credential = CredentialEnvelope(
        credential_id="cred_test",
        credential_kind="authority_access",
        issuer_authority_id="example.authority",
        issuer_authenticator_id="example.oauth",
        subject="example:user:123",
        audience="bundle:example",
    )

    result = await registry.verify(credential, token="bearer", context={"surface": "mcp"})

    assert result.ok is True
    assert result.authority_id == "example.authority"
    assert result.authenticator_id == "example.oauth"
    assert result.subject == "example:user:123"
    assert result.metadata["token_present"] is True
    assert result.metadata["context"]["surface"] == "mcp"


async def test_local_authority_registry_fails_closed_when_unreachable():
    registry = AuthorityRegistry()

    result = await registry.verify({
        "schema": "kdcube.credential.v1",
        "issuer_authority_id": "bundle.local.only",
        "issuer_authenticator_id": "bundle.local.only.oauth",
        "credential_kind": "authority_access",
        "subject": "bundle:user:1",
    })

    assert result.ok is False
    assert result.error == "authority_not_registered"


def test_authority_provider_spec_from_bundle_declaration():
    spec = authority_provider_spec_from_declaration(
        {
            "authority_id": "custom.identity",
            "authenticator_id": "custom.identity.oauth",
            "credential_kinds": ["authority_access"],
            "audiences": ["bundle:navigator-tg-bot@1-0"],
            "label": "Yay Identity",
            "transports": ["local"],
        },
        bundle_id="navigator-tg-bot@1-0",
    )

    assert spec.authority_id == "custom.identity"
    assert spec.authenticators == ("custom.identity.oauth",)
    assert spec.credential_kinds == ("authority_access",)
    assert spec.bundle_id == "navigator-tg-bot@1-0"
    assert spec.metadata["source"] == "bundle_manifest"
