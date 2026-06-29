# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Public `connections` named-service contract (OAuth integrations).

The transport-neutral contract for letting a user connect external systems and
for other bundles to fetch the user's access token. A bundle implements
``ConnectionsProviderBase`` against its chosen storage; consumers use
``ConnectionsClient`` over the local or API transport.

See docs/sdk/integrations/connections-README.md for the design.
"""

from __future__ import annotations

from .contract import (
    NAMESPACE,
    CONNECTION_CATALOG,
    CONNECTION_STATUS,
    CONNECTION_GET_TOKEN,
    CONNECTION_DISCONNECT,
    OAUTH_START,
    CONNECTION_OPERATIONS,
    build_connection_operations,
    Connection,
    ConnectionToken,
    CatalogEntry,
    ClientApp,
    AmbiguousConnectionAccount,
)
from .provider import ConnectionsProviderBase
from .client import ConnectionsClient, ConnectionsError
from .connection_edges import (
    DEFAULT_CONNECTION_HUB_BUNDLE_ID,
    ConnectionEdgesClient,
    connection_hub_bundle_id,
    connection_hub_bundle_id_from_entrypoint,
    request_origin,
)
from .authenticators import (
    AuthRequestHints,
    AuthenticatedRequest,
    AuthenticatorRegistration,
    AuthorityIdentity,
    ConnectionHubAuthenticatorsClient,
    REQUEST_AUTHENTICATE_OPERATION,
    RequestEnvelope,
    SurfaceGuardRequirement,
    select_authenticator_candidates,
)
from .authority_registry import (
    AUTHORITY_DISCOVERY_SCHEMA,
    CREDENTIAL_SCHEMA,
    INGRESS_SESSION_AUTHENTICATOR_ID,
    INGRESS_SESSION_AUTHORITY_ID,
    DELEGATED_CLIENT_AUTHENTICATOR_ID,
    DELEGATED_CLIENT_AUTHORITY_ID,
    AuthorityProviderSpec,
    AuthorityRegistry,
    AuthorityResolution,
    CredentialEnvelope,
    RedisAuthorityDiscovery,
    authority_provider_spec_from_declaration,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_inventory import (
    DELEGATION_EDGE_SCHEMA,
    PLATFORM_AUTHORITY_ID,
    AuthorityDelegationEdge,
    AuthorityGrantDefinition,
    AuthorityGrantInventory,
    AuthorityGrantInventoryProvider,
    AuthorityIdentity,
    DelegableAuthorityGrant,
    PlatformAuthorityInventoryProvider,
    platform_identity_from_user,
    selected_delegation_edge,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_projection import (
    PLATFORM_PRIVILEGED_ROLE_GRANTS,
    AuthorityExecutionProjection,
    authority_actor_user_id,
    authority_budget_bypass,
    authority_economics_user_id,
    authority_explicit_budget_bypass,
    authority_has_platform_privilege,
    authority_permissions,
    authority_roles,
    normalize_authority_values,
    project_execution_authority,
)
from .request_auth import (
    PlatformTokenAuthenticator,
    RequestAuthenticationSurface,
    RequestAuthResolver,
    SessionFactory,
)
from .authentication_surface import (
    ConnectionHubAuthenticationSurface,
    connection_hub_auth_enabled,
    maybe_install_connection_hub_authentication_surface,
)

__all__ = [
    "NAMESPACE",
    "CONNECTION_CATALOG",
    "CONNECTION_STATUS",
    "CONNECTION_GET_TOKEN",
    "CONNECTION_DISCONNECT",
    "OAUTH_START",
    "CONNECTION_OPERATIONS",
    "build_connection_operations",
    "Connection",
    "ConnectionToken",
    "CatalogEntry",
    "ClientApp",
    "AmbiguousConnectionAccount",
    "ConnectionsProviderBase",
    "ConnectionsClient",
    "ConnectionsError",
    "DEFAULT_CONNECTION_HUB_BUNDLE_ID",
    "ConnectionEdgesClient",
    "connection_hub_bundle_id",
    "connection_hub_bundle_id_from_entrypoint",
    "request_origin",
    "AuthRequestHints",
    "AuthenticatedRequest",
    "AuthenticatorRegistration",
    "AuthorityIdentity",
    "ConnectionHubAuthenticatorsClient",
    "REQUEST_AUTHENTICATE_OPERATION",
    "RequestEnvelope",
    "SurfaceGuardRequirement",
    "select_authenticator_candidates",
    "AUTHORITY_DISCOVERY_SCHEMA",
    "CREDENTIAL_SCHEMA",
    "INGRESS_SESSION_AUTHENTICATOR_ID",
    "INGRESS_SESSION_AUTHORITY_ID",
    "DELEGATED_CLIENT_AUTHENTICATOR_ID",
    "DELEGATED_CLIENT_AUTHORITY_ID",
    "AuthorityProviderSpec",
    "AuthorityRegistry",
    "AuthorityResolution",
    "CredentialEnvelope",
    "RedisAuthorityDiscovery",
    "authority_provider_spec_from_declaration",
    "DELEGATION_EDGE_SCHEMA",
    "PLATFORM_AUTHORITY_ID",
    "AuthorityDelegationEdge",
    "AuthorityGrantDefinition",
    "AuthorityGrantInventory",
    "AuthorityGrantInventoryProvider",
    "AuthorityIdentity",
    "DelegableAuthorityGrant",
    "PlatformAuthorityInventoryProvider",
    "platform_identity_from_user",
    "selected_delegation_edge",
    "PLATFORM_PRIVILEGED_ROLE_GRANTS",
    "AuthorityExecutionProjection",
    "authority_actor_user_id",
    "authority_budget_bypass",
    "authority_economics_user_id",
    "authority_explicit_budget_bypass",
    "authority_has_platform_privilege",
    "authority_permissions",
    "authority_roles",
    "normalize_authority_values",
    "project_execution_authority",
    "PlatformTokenAuthenticator",
    "RequestAuthenticationSurface",
    "RequestAuthResolver",
    "SessionFactory",
    "ConnectionHubAuthenticationSurface",
    "connection_hub_auth_enabled",
    "maybe_install_connection_hub_authentication_surface",
]
