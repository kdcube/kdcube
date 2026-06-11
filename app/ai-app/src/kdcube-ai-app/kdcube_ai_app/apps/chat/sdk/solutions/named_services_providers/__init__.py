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
    named_service_namespace_client_tools_config,
    named_service_namespace_config,
    named_service_namespace_provider_config,
    named_service_namespaces,
    named_services_config,
    named_service_tool_spec,
)
from .canvas_resolver import (
    NamedServiceCanvasObjectResolver,
    register_configured_named_service_canvas_resolvers,
)
from .client import NamedServiceClient
from .provider import NamedServiceProvider, named_service_provider
from .registry import NamedServiceRegistry
from .transports.api_client import NamedServiceApiEndpoint, call_named_service_api_endpoint
from .transports.api import NamedServiceApiTransport, dispatch_named_service_api_request
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
    NamedServiceContext,
    NamedServiceError,
    NamedServiceOperationSpec,
    NamedServiceProviderSpec,
    NamedServiceRequest,
    NamedServiceResponse,
    build_default_operations,
    namespace_for_ref,
)

__all__ = [
    "NAMED_SERVICE_REQUEST_SCHEMA",
    "NAMED_SERVICE_RESPONSE_SCHEMA",
    "NAMED_SERVICE_TOOLS_ALIAS",
    "NAMED_SERVICE_TOOLS_MODULE",
    "TRANSPORT_API",
    "TRANSPORT_DATA_BUS",
    "TRANSPORT_LOCAL",
    "TRANSPORT_MCP",
    "BLOCK_PRODUCE",
    "BLOCK_RENDER",
    "EVENT_ACTION",
    "EVENT_RESOLVE",
    "AuthContext",
    "NamedServiceApiTransport",
    "NamedServiceApiEndpoint",
    "NamedServiceCanvasObjectResolver",
    "NamedServiceClient",
    "NamedServiceContext",
    "NamedServiceError",
    "NamedServiceOperationSpec",
    "NamedServiceProvider",
    "NamedServiceProviderSpec",
    "NamedServiceRegistry",
    "NamedServiceRequest",
    "NamedServiceResponse",
    "build_default_operations",
    "call_named_service_api_endpoint",
    "client_has_named_service_tools",
    "dispatch_named_service_api_request",
    "extend_tool_specs_for_named_services",
    "named_service_provider",
    "named_service_namespace_client_tools_config",
    "named_service_namespace_config",
    "named_service_namespace_provider_config",
    "named_service_namespaces",
    "named_services_config",
    "named_service_tool_spec",
    "namespace_for_ref",
    "register_configured_named_service_canvas_resolvers",
]
