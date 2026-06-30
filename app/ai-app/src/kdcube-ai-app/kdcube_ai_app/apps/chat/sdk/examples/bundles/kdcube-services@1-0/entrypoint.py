from __future__ import annotations

from typing import Any, Dict

from langgraph.graph import END, START, StateGraph

from kdcube_ai_app.apps.chat.sdk.protocol import ExternalEventPayload
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint
from kdcube_ai_app.infra.plugin.bundle_loader import bundle_entrypoint, bundle_id, mcp
from kdcube_ai_app.infra.service_hub.inventory import BundleState, Config

try:
    from .services.conversations import ConversationExportRequest, ConversationExportService
    from .services.named_services import NamedServicesMcpBridge
    from .surfaces.mcp import conversations as conversations_mcp_module
    from .surfaces.mcp import named_services as named_services_mcp_module
except Exception:  # pragma: no cover - bundle loader may import as loose module
    from services.conversations import ConversationExportRequest, ConversationExportService  # type: ignore
    from services.named_services import NamedServicesMcpBridge  # type: ignore
    from surfaces.mcp import conversations as conversations_mcp_module  # type: ignore
    from surfaces.mcp import named_services as named_services_mcp_module  # type: ignore


BUNDLE_ID = "kdcube-services@1-0"
WORKFLOW_NAME = "kdcube_services"


@bundle_entrypoint(
    name=WORKFLOW_NAME,
    version="1.0.0",
    priority=100,
    allowed_roles_config="surfaces.as_provider.bundle.visibility.allowed_roles",
)
@bundle_id(id=BUNDLE_ID)
class KDCubeServicesEntrypoint(BaseEntrypoint):
    """Read-only KDCube service surfaces.

    This bundle provides normal proc-served KDCube surfaces for delegated
    external clients. It deliberately does not create a root platform `/mcp`
    endpoint; callers connect to this bundle's managed MCP URL.
    """

    def __init__(
        self,
        config: Config,
        pg_pool: Any = None,
        redis: Any = None,
        comm_context: ExternalEventPayload = None,
    ):
        super().__init__(
            config=config,
            pg_pool=pg_pool,
            redis=redis,
            comm_context=comm_context,
        )
        self.graph = self._build_graph()

    def configuration_defaults(self) -> Dict[str, Any]:
        return {
            "surfaces": {
                "as_provider": {
                    "bundle": {"visibility": {"allowed_roles": []}},
                    "mcp": {
                        "conversations": {
                            "auth": {
                                "mode": "managed",
                                "authority_id": "delegated_client",
                                "tools": {
                                    "conversations_export": {
                                        "grants": ["conversations:read"],
                                    },
                                },
                                "selected_tool_grants": True,
                            },
                        },
                        "named_services": {
                            "auth": {
                                "mode": "managed",
                                "authority_id": "delegated_client",
                                "tools": {
                                    "named_services_list": {
                                        "grants": ["named_services:use"],
                                    },
                                    "named_services_about": {
                                        "grants": ["named_services:use"],
                                    },
                                    "named_services_capabilities": {
                                        "grants": ["named_services:use"],
                                    },
                                    "named_services_schema": {
                                        "grants": ["named_services:use"],
                                    },
                                    "named_services_search": {
                                        "grants": ["named_services:use"],
                                    },
                                    "named_services_get": {
                                        "grants": ["named_services:use"],
                                    },
                                    "named_services_upsert": {
                                        "grants": ["named_services:use"],
                                    },
                                    "named_services_host_file": {
                                        "grants": ["named_services:use"],
                                    },
                                    "named_services_action": {
                                        "grants": ["named_services:use"],
                                    },
                                    "named_services_delete": {
                                        "grants": ["named_services:use"],
                                    },
                                    "named_services_call": {
                                        "grants": ["named_services:use"],
                                    },
                                },
                                "selected_tool_grants": True,
                            },
                        },
                    },
                },
            },
        }

    @mcp(
        alias="conversations",
        route="public",
        transport="streamable-http",
        auth_config="surfaces.as_provider.mcp.conversations.auth",
    )
    def conversations_mcp(self, request=None, **kwargs):
        return conversations_mcp_module.build_conversations_mcp_app(
            name="KDCube conversations",
            pool_factory=lambda: self.pg_pool,
            request_model=ConversationExportRequest,
            service_cls=ConversationExportService,
            request=request,
        )

    @mcp(
        alias="named_services",
        route="public",
        transport="streamable-http",
        auth_config="surfaces.as_provider.mcp.named_services.auth",
    )
    def named_services_mcp(self, request=None, **kwargs):
        actor = getattr(self.comm_context, "actor", None)
        return named_services_mcp_module.build_named_services_mcp_app(
            name="KDCube named services",
            config_factory=lambda: {},
            tenant_factory=lambda: str(getattr(actor, "tenant_id", None) or ""),
            project_factory=lambda: str(getattr(actor, "project_id", None) or ""),
            request=request,
            bridge_factory=NamedServicesMcpBridge,
        )

    def _build_graph(self) -> StateGraph:
        g = StateGraph(BundleState)

        async def guide(state: BundleState) -> BundleState:
            state["final_answer"] = (
                "This bundle serves managed KDCube service MCP tools. Connect "
                "an external client to the KDCube services MCP surface."
            )
            return state

        g.add_node("guide", guide)
        g.add_edge(START, "guide")
        g.add_edge("guide", END)
        return g.compile()

    async def execute_core(
        self,
        *,
        state: Dict[str, Any],
        thread_id: str,
        params: Dict[str, Any],
    ):
        return await self.graph.ainvoke(state)
