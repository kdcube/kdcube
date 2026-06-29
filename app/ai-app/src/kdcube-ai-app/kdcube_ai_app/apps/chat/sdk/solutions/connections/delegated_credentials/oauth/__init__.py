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
    oauth_delegated_config,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.grants import (
    ACCESS_TOKEN_TTL_SECONDS,
    CONVERSATIONS_READ_PERMISSION,
    FEEDBACK_READER_ROLE,
    ROLE_TOOLS,
    can_call_tool,
    integration_subject,
    mint_feedback_reader_access_token,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.metadata import (
    CONVERSATIONS_READ_SCOPE,
    authorization_server_metadata,
    protected_resource_metadata,
    protected_resource_metadata_url,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.store import GrantStore

__all__ = [
    "ACCESS_TOKEN_TTL_SECONDS",
    "CONVERSATIONS_READ_PERMISSION",
    "CONVERSATIONS_READ_SCOPE",
    "FEEDBACK_READER_ROLE",
    "DELEGATED_CLIENT_AUDIENCE",
    "DELEGATED_CLIENT_CREDENTIAL_KIND",
    "OAuthDelegatedClientAuthorityProvider",
    "OAuthDelegatedClientConfig",
    "ROLE_TOOLS",
    "GrantStore",
    "authorization_server_metadata",
    "build_delegated_client_credential",
    "can_call_tool",
    "integration_subject",
    "mint_feedback_reader_access_token",
    "delegated_client_authority_spec",
    "oauth_delegated_config",
    "protected_resource_metadata",
    "protected_resource_metadata_url",
    "register_delegated_client_authority",
]
