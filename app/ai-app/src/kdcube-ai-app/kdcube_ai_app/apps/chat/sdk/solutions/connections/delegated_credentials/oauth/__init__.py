# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""OAuth protocol adapter for delegated-client credentials."""

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.authority import (
    DELEGATED_CLIENT_AUDIENCE,
    DELEGATED_CLIENT_CREDENTIAL_KIND,
    OAuthDelegatedClientAuthorityProvider,
    build_delegated_client_credential,
    delegated_client_authority_spec,
    register_delegated_client_authority,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.config import (
    OAuthDelegatedClientConfig,
    OAuthDelegatedConsentUIConfig,
    oauth_delegated_config,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.grants import (
    ACCESS_TOKEN_TTL_SECONDS,
    DELEGATED_CLIENT_ROLE,
    integration_subject,
    mint_delegated_client_access_token,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.metadata import (
    authorization_server_metadata,
    protected_resource_metadata,
    protected_resource_metadata_url,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.store import GrantStore

__all__ = [
    "ACCESS_TOKEN_TTL_SECONDS",
    "DELEGATED_CLIENT_ROLE",
    "DELEGATED_CLIENT_AUDIENCE",
    "DELEGATED_CLIENT_CREDENTIAL_KIND",
    "OAuthDelegatedClientAuthorityProvider",
    "OAuthDelegatedClientConfig",
    "OAuthDelegatedConsentUIConfig",
    "GrantStore",
    "authorization_server_metadata",
    "build_delegated_client_credential",
    "integration_subject",
    "mint_delegated_client_access_token",
    "delegated_client_authority_spec",
    "oauth_delegated_config",
    "protected_resource_metadata",
    "protected_resource_metadata_url",
    "register_delegated_client_authority",
]
