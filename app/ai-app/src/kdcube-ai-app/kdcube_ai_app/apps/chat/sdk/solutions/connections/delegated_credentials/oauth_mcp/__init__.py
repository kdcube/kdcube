# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""OAuth/MCP delegated credential adapter primitives."""

from .authority import (
    OAUTH_MCP_AUDIENCE,
    OAUTH_MCP_CREDENTIAL_KIND,
    OAuthMcpAuthorityProvider,
    build_oauth_mcp_credential,
    oauth_mcp_authority_spec,
    register_oauth_mcp_authority,
)
from .config import OAuthMcpConfig, oauth_mcp_config
from .grants import (
    ACCESS_TOKEN_TTL_SECONDS,
    ADMIN_ROLES,
    CONVERSATIONS_READ_PERMISSION,
    FEEDBACK_READER_ROLE,
    ROLE_TOOLS,
    can_call_tool,
    integration_subject,
    mint_feedback_reader_access_token,
)
from .metadata import (
    CONVERSATIONS_READ_SCOPE,
    authorization_server_metadata,
    protected_resource_metadata,
)
from .store import GrantStore

__all__ = [
    "ACCESS_TOKEN_TTL_SECONDS",
    "ADMIN_ROLES",
    "CONVERSATIONS_READ_PERMISSION",
    "CONVERSATIONS_READ_SCOPE",
    "FEEDBACK_READER_ROLE",
    "OAUTH_MCP_AUDIENCE",
    "OAUTH_MCP_CREDENTIAL_KIND",
    "OAuthMcpAuthorityProvider",
    "OAuthMcpConfig",
    "ROLE_TOOLS",
    "GrantStore",
    "authorization_server_metadata",
    "build_oauth_mcp_credential",
    "can_call_tool",
    "integration_subject",
    "mint_feedback_reader_access_token",
    "oauth_mcp_authority_spec",
    "oauth_mcp_config",
    "protected_resource_metadata",
    "register_oauth_mcp_authority",
]
