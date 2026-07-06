# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Connection Hub delegated to KDCube SDK."""

from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.adapters import (
    DelegatedToKdcubeAdapter,
    adapter,
    list_adapters,
    register_adapter,
    resolve_adapter,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.broker import (
    DelegatedToKdcubeBroker,
    broker_for_user,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.client import DelegatedToKdcubeClient
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.config import (
    CONNECTIONS_CONFIG_KEY,
    DELEGATED_TO_KDCUBE_CONFIG_KEY,
    delegated_to_kdcube_config,
    delegated_to_kdcube_config_from_entrypoint,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.models import (
    CONNECTION_HUB_BUNDLE_ID,
    STATUS_CONNECTED,
    STATUS_REVOKED,
    ClaimResolution,
    ConnectedAccount,
    ConnectorApp,
    CredentialHandle,
    IntegrationProvider,
    ProviderClaim,
    ToolClaimPolicy,
    ToolClaimRequirement,
    DelegatedToKdcubeConfig,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.oauth import (
    MemoryOAuthStateStore,
    OAuthStateStore,
    RedisOAuthStateStore,
    consume_oauth_state,
    create_oauth_state,
    peek_state_payload,
    sign_state,
    state_digest,
    verify_state,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.operations import (
    DelegatedToKdcubeOperations,
    operations_for_user,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.preflight import (
    CONSENT_NEEDED_CODE,
    PREFLIGHT_SCHEMA,
    connected_account_consent_payload,
    preflight_tool_claim_policies,
    unavailable_tools_by_provider,
    unavailable_tools_message,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.store import (
    DelegatedToKdcubeStore,
    account_id_for,
    credential_id_for,
)

__all__ = [
    "CONNECTION_HUB_BUNDLE_ID",
    "CONNECTIONS_CONFIG_KEY",
    "CONSENT_NEEDED_CODE",
    "DELEGATED_TO_KDCUBE_CONFIG_KEY",
    "PREFLIGHT_SCHEMA",
    "STATUS_CONNECTED",
    "STATUS_REVOKED",
    "ClaimResolution",
    "ConnectedAccount",
    "ConnectorApp",
    "CredentialHandle",
    "IntegrationProvider",
    "MemoryOAuthStateStore",
    "OAuthStateStore",
    "ProviderClaim",
    "ToolClaimPolicy",
    "ToolClaimRequirement",
    "RedisOAuthStateStore",
    "DelegatedToKdcubeAdapter",
    "DelegatedToKdcubeBroker",
    "DelegatedToKdcubeStore",
    "DelegatedToKdcubeOperations",
    "DelegatedToKdcubeClient",
    "DelegatedToKdcubeConfig",
    "account_id_for",
    "adapter",
    "broker_for_user",
    "consume_oauth_state",
    "credential_id_for",
    "connected_account_consent_payload",
    "create_oauth_state",
    "list_adapters",
    "operations_for_user",
    "peek_state_payload",
    "preflight_tool_claim_policies",
    "unavailable_tools_by_provider",
    "unavailable_tools_message",
    "register_adapter",
    "resolve_adapter",
    "sign_state",
    "state_digest",
    "delegated_to_kdcube_config",
    "delegated_to_kdcube_config_from_entrypoint",
    "verify_state",
]
