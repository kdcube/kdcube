# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Public `connections` SDK surface.

This package is imported by low-level configuration code during process boot.
Keep this module lazy: importing ``kdcube_ai_app.apps.chat.sdk.config`` must not
eagerly import named services, storage, or bundle runtimes through this package.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, str] = {
    "NAMESPACE": "kdcube_ai_app.apps.chat.sdk.solutions.connections.contract",
    "CONNECTION_CATALOG": "kdcube_ai_app.apps.chat.sdk.solutions.connections.contract",
    "CONNECTION_STATUS": "kdcube_ai_app.apps.chat.sdk.solutions.connections.contract",
    "CONNECTION_GET_TOKEN": "kdcube_ai_app.apps.chat.sdk.solutions.connections.contract",
    "CONNECTION_DISCONNECT": "kdcube_ai_app.apps.chat.sdk.solutions.connections.contract",
    "OAUTH_START": "kdcube_ai_app.apps.chat.sdk.solutions.connections.contract",
    "CONNECTION_OPERATIONS": "kdcube_ai_app.apps.chat.sdk.solutions.connections.contract",
    "build_connection_operations": "kdcube_ai_app.apps.chat.sdk.solutions.connections.contract",
    "Connection": "kdcube_ai_app.apps.chat.sdk.solutions.connections.contract",
    "ConnectionToken": "kdcube_ai_app.apps.chat.sdk.solutions.connections.contract",
    "CatalogEntry": "kdcube_ai_app.apps.chat.sdk.solutions.connections.contract",
    "ClientApp": "kdcube_ai_app.apps.chat.sdk.solutions.connections.contract",
    "AmbiguousConnectionAccount": "kdcube_ai_app.apps.chat.sdk.solutions.connections.contract",
    "ConnectionsProviderBase": "kdcube_ai_app.apps.chat.sdk.solutions.connections.provider",
    "ConnectionHubClient": "kdcube_ai_app.apps.chat.sdk.solutions.connections.client",
    "ConnectionsClient": "kdcube_ai_app.apps.chat.sdk.solutions.connections.client",
    "ConnectionsError": "kdcube_ai_app.apps.chat.sdk.solutions.connections.client",
    "DEFAULT_CONNECTION_HUB_BUNDLE_ID": "kdcube_ai_app.apps.chat.sdk.solutions.connections.connection_edges",
    "ConnectionEdgesClient": "kdcube_ai_app.apps.chat.sdk.solutions.connections.connection_edges",
    "connection_hub_bundle_id": "kdcube_ai_app.apps.chat.sdk.solutions.connections.connection_edges",
    "connection_hub_bundle_id_from_entrypoint": "kdcube_ai_app.apps.chat.sdk.solutions.connections.connection_edges",
    "request_origin": "kdcube_ai_app.apps.chat.sdk.solutions.connections.connection_edges",
    "AuthorityRegistryClient": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry_client",
    "DEFAULT_PLATFORM_AUTHORITY_ID": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry_config",
    "DEFAULT_PLATFORM_PROVIDER_ID": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry_config",
    "authority_provider_instances": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry_config",
    "authority_registry_config": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry_config",
    "cognito_platform_auth_config": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry_config",
    "platform_authority_auth_config": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry_config",
    "resolve_authority_provider_instance": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry_config",
    "resolve_platform_authority_provider": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry_config",
    "AuthRequestHints": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authenticators",
    "AuthenticatedRequest": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authenticators",
    "AuthenticatorRegistration": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authenticators",
    "ConnectionHubAuthenticatorsClient": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authenticators",
    "REQUEST_AUTHENTICATE_OPERATION": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authenticators",
    "RequestEnvelope": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authenticators",
    "SurfaceGuardRequirement": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authenticators",
    "select_authenticator_candidates": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authenticators",
    "AUTHORITY_DISCOVERY_SCHEMA": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry",
    "CREDENTIAL_SCHEMA": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry",
    "INGRESS_SESSION_AUTHENTICATOR_ID": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry",
    "INGRESS_SESSION_AUTHORITY_ID": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry",
    "DELEGATED_CLIENT_AUTHENTICATOR_ID": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry",
    "DELEGATED_CLIENT_AUTHORITY_ID": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry",
    "AuthorityProviderSpec": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry",
    "AuthorityRegistry": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry",
    "AuthorityResolution": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry",
    "CredentialEnvelope": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry",
    "RedisAuthorityDiscovery": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry",
    "authority_provider_spec_from_declaration": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry",
    "DELEGATION_EDGE_SCHEMA": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_inventory",
    "PLATFORM_AUTHORITY_ID": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_inventory",
    "AuthorityDelegationEdge": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_inventory",
    "AuthorityGrantDefinition": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_inventory",
    "AuthorityGrantInventory": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_inventory",
    "AuthorityGrantInventoryProvider": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_inventory",
    "AuthorityIdentity": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_inventory",
    "DelegableAuthorityGrant": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_inventory",
    "PlatformAuthorityInventoryProvider": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_inventory",
    "platform_identity_from_user": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_inventory",
    "selected_delegation_edge": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_inventory",
    "PLATFORM_PRIVILEGED_ROLE_GRANTS": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_projection",
    "AuthorityExecutionProjection": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_projection",
    "authority_actor_user_id": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_projection",
    "authority_budget_bypass": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_projection",
    "authority_economics_user_id": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_projection",
    "authority_explicit_budget_bypass": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_projection",
    "authority_has_platform_privilege": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_projection",
    "authority_permissions": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_projection",
    "authority_roles": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_projection",
    "normalize_authority_values": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_projection",
    "project_execution_authority": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_projection",
    "PlatformTokenAuthenticator": "kdcube_ai_app.apps.chat.sdk.solutions.connections.request_auth",
    "RequestAuthenticationSurface": "kdcube_ai_app.apps.chat.sdk.solutions.connections.request_auth",
    "RequestAuthResolver": "kdcube_ai_app.apps.chat.sdk.solutions.connections.request_auth",
    "SessionFactory": "kdcube_ai_app.apps.chat.sdk.solutions.connections.request_auth",
    "ConnectionHubAuthenticationSurface": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authentication_surface",
    "connection_hub_auth_enabled": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authentication_surface",
    "maybe_install_connection_hub_authentication_surface": "kdcube_ai_app.apps.chat.sdk.solutions.connections.authentication_surface",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if not module_name:
        raise AttributeError(name)
    module = import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value
