# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Custom authority provider registration and discovery helpers."""

from ..authority_registry import (
    AUTHORITY_DISCOVERY_SCHEMA,
    CREDENTIAL_SCHEMA,
    AuthorityProvider,
    AuthorityProviderSpec,
    AuthorityRegistry,
    AuthorityResolution,
    CredentialEnvelope,
    RedisAuthorityDiscovery,
    RegisteredAuthorityProvider,
    authority_provider_spec_from_declaration,
)

__all__ = [
    "AUTHORITY_DISCOVERY_SCHEMA",
    "CREDENTIAL_SCHEMA",
    "AuthorityProvider",
    "AuthorityProviderSpec",
    "AuthorityRegistry",
    "AuthorityResolution",
    "CredentialEnvelope",
    "RedisAuthorityDiscovery",
    "RegisteredAuthorityProvider",
    "authority_provider_spec_from_declaration",
]
