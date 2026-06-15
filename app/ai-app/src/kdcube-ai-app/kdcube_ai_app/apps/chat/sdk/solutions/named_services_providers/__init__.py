# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Named service provider SDK surface.

This package owns the transport-neutral provider/client contract. The first
implementation is local async dispatch; API, MCP, and Data Bus adapters should
delegate to the same types and registry instead of redefining operation shapes.
"""

from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.infra.auth_context import AuthContext

from .client_tools import (
    NAMED_SERVICE_TOOLS_ALIAS,
    NAMED_SERVICE_TOOLS_MODULE,
    client_has_named_service_tools,
    extend_tool_specs_for_named_services,
    named_service_agent_event_source_namespaces,
    named_service_agent_pull_namespaces,
    named_service_canvas_resolver_namespaces,
    named_service_namespace_client_resolver_config,
    named_service_namespace_client_tools_config,
    named_service_namespace_config,
    named_service_namespace_provider_configs,
    named_service_namespace_provider_configs_from_config,
    named_service_namespaces,
    named_services_config,
    named_service_tool_spec,
)
from .canvas_resolver import (
    NamedServiceCanvasObjectResolver,
    register_configured_named_service_canvas_resolvers,
)
from .artifact_rehoster import (
    NamedServiceArtifactNamespaceRehoster,
    register_configured_named_service_artifact_rehosters,
)
from .block_policy_adapter import (
    NAMED_SERVICE_BLOCK_PRODUCTION_POLICY_ID,
    NAMED_SERVICE_EVENT_SOURCE_PREFIX,
    named_service_event_source_id,
    register_configured_named_service_event_sources,
)
from .discovery import (
    ConfiguredNamedServiceDiscovery,
    DEFAULT_DISCOVERY_TTL_SECONDS,
    NAMED_SERVICE_DISCOVERY_SCHEMA,
    NamedServiceDiscoveryEntry,
    RedisNamedServiceDiscovery,
    bind_named_service_discovery,
    get_current_named_service_discovery,
)
from .client import NamedServiceClient
from .instructions import NAMED_SERVICES_REACT_ADDITIONAL_INSTRUCTIONS
from .provider import NamedServiceProvider, named_service_provider
from .registry import NamedServiceRegistry
from .transports.api_client import (
    ENDPOINT_TRANSPORT_BUNDLE_OPERATION,
    ENDPOINT_TRANSPORT_BUNDLE_REGISTRY,
    ENDPOINT_TRANSPORT_MODULE,
    NamedServiceEndpoint,
    call_named_service_endpoint,
    call_named_service_endpoint_stream,
)
from .transports.api import (
    NamedServiceApiTransport,
    dispatch_named_service_api_request,
    dispatch_named_service_api_stream_request,
)
from .types import (
    NAMED_SERVICE_RESPONSE_SCHEMA,
    NAMED_SERVICE_REQUEST_SCHEMA,
    TRANSPORT_API,
    TRANSPORT_DATA_BUS,
    TRANSPORT_LOCAL,
    TRANSPORT_MCP,
    BLOCK_PRODUCE,
    BLOCK_RENDER,
    EVENT_ACTION,
    EVENT_RESOLVE,
    OBJECT_HOST_FILE,
    OBJECT_SCHEMA,
    NamedServiceContext,
    NamedServiceError,
    NamedServiceOperationSpec,
    NamedServiceProviderSpec,
    NamedServiceRequest,
    NamedServiceResponse,
    NamedServiceSearchScope,
    NamedServiceStreamResult,
    build_default_operations,
    normalize_search_scopes,
    namespace_for_ref,
)

__all__ = [
    "NAMED_SERVICES_REACT_ADDITIONAL_INSTRUCTIONS",
    "NAMED_SERVICE_REQUEST_SCHEMA",
    "NAMED_SERVICE_RESPONSE_SCHEMA",
    "NAMED_SERVICE_TOOLS_ALIAS",
    "NAMED_SERVICE_TOOLS_MODULE",
    "NAMED_SERVICE_BLOCK_PRODUCTION_POLICY_ID",
    "NAMED_SERVICE_DISCOVERY_SCHEMA",
    "NAMED_SERVICE_EVENT_SOURCE_PREFIX",
    "DEFAULT_DISCOVERY_TTL_SECONDS",
    "TRANSPORT_API",
    "TRANSPORT_DATA_BUS",
    "TRANSPORT_LOCAL",
    "TRANSPORT_MCP",
    "ENDPOINT_TRANSPORT_BUNDLE_OPERATION",
    "ENDPOINT_TRANSPORT_BUNDLE_REGISTRY",
    "ENDPOINT_TRANSPORT_MODULE",
    "BLOCK_PRODUCE",
    "BLOCK_RENDER",
    "EVENT_ACTION",
    "EVENT_RESOLVE",
    "OBJECT_HOST_FILE",
    "OBJECT_SCHEMA",
    "AuthContext",
    "NamedServiceApiTransport",
    "ConfiguredNamedServiceDiscovery",
    "NamedServiceEndpoint",
    "NamedServiceArtifactNamespaceRehoster",
    "NamedServiceCanvasObjectResolver",
    "NamedServiceClient",
    "NamedServiceContext",
    "NamedServiceDiscoveryEntry",
    "NamedServiceError",
    "NamedServiceOperationSpec",
    "NamedServiceProvider",
    "NamedServiceProviderSpec",
    "NamedServiceRegistry",
    "NamedServiceRequest",
    "NamedServiceResponse",
    "NamedServiceSearchScope",
    "NamedServiceStreamResult",
    "build_default_operations",
    "normalize_search_scopes",
    "call_named_service_endpoint",
    "call_named_service_endpoint_stream",
    "client_has_named_service_tools",
    "dispatch_named_service_api_request",
    "dispatch_named_service_api_stream_request",
    "extend_tool_specs_for_named_services",
    "named_service_agent_event_source_namespaces",
    "named_service_agent_pull_namespaces",
    "named_service_canvas_resolver_namespaces",
    "named_service_provider",
    "named_service_event_source_id",
    "named_service_namespace_client_tools_config",
    "named_service_namespace_client_resolver_config",
    "named_service_namespace_config",
    "named_service_namespace_provider_configs",
    "named_service_namespace_provider_configs_from_config",
    "named_service_namespaces",
    "named_services_config",
    "named_service_tool_spec",
    "namespace_for_ref",
    "register_configured_named_service_canvas_resolvers",
    "register_configured_named_service_artifact_rehosters",
    "register_configured_named_service_event_sources",
    "RedisNamedServiceDiscovery",
    "bind_named_service_discovery",
    "get_current_named_service_discovery",
]
