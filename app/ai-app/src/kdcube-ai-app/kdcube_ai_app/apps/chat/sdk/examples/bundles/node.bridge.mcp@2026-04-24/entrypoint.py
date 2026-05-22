from __future__ import annotations

from typing import Any, Dict

from langgraph.graph import END, START, StateGraph

from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.apps.chat.sdk.runtime.node import BundleNodeBackendBridge
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint
from kdcube_ai_app.infra.plugin.bundle_loader import bundle_entrypoint, api, bundle_id, mcp
from kdcube_ai_app.infra.service_hub.inventory import BundleState, Config

from . import node_mcp_tools

BUNDLE_ID = "node.bridge.mcp"


@bundle_entrypoint(name=BUNDLE_ID, version="1.0.0", priority=10)
@bundle_id(id="node.bridge.mcp@2026-04-24")
class NodeBridgeBundle(BaseEntrypoint):
    """Minimal runnable example for a bundle-local Node/TS backend sidecar."""

    def __init__(
        self,
        config: Config,
        pg_pool: Any = None,
        redis: Any = None,
        comm_context: ChatTaskPayload = None,
    ):
        super().__init__(
            config=config,
            pg_pool=pg_pool,
            redis=redis,
            comm_context=comm_context,
        )
        self._node_mcp_app: Any = None
        self.graph = self._build_graph()

    def configuration_defaults(self) -> Dict[str, Any]:
        return {
            "node_bridge": {
                "source_dir": "backend_src",
                "entry_module": "src/bridge_app.ts",
                "allowed_prefixes": ["/api/projects"],
                "runtime_config": {
                    "statusLabel": "node-sidecar",
                    "searchPrefix": "Node match for",
                },
            }
        }

    def _build_graph(self) -> StateGraph:
        g = StateGraph(BundleState)

        async def guide(state: BundleState) -> BundleState:
            state["final_answer"] = (
                "This bundle demonstrates a bundle-local Node backend sidecar. "
                "Use the node_status API, node_search API, or the node_tools MCP endpoint."
            )
            return state

        g.add_node("guide", guide)
        g.add_edge(START, "guide")
        g.add_edge("guide", END)
        return g.compile()

    def _node_bridge(self) -> BundleNodeBackendBridge:
        allowed_prefixes = self.bundle_prop("node_bridge.allowed_prefixes", ["/api/projects"])
        if isinstance(allowed_prefixes, str):
            normalized_allowed = tuple(
                item.strip()
                for item in allowed_prefixes.split(",")
                if item.strip()
            )
        else:
            normalized_allowed = tuple(
                str(item).strip()
                for item in (allowed_prefixes or [])
                if str(item).strip()
            )
        return BundleNodeBackendBridge(
            bundle=self,
            name="node-backend",
            source_dir=self.bundle_prop("node_bridge.source_dir", "backend_src"),
            entry_module=self.bundle_prop("node_bridge.entry_module", "src/bridge_app.ts"),
            allowed_prefixes=normalized_allowed or ("/api/projects",),
            live_config=self.bundle_prop("node_bridge.runtime_config", {}) or {},
            reconfigure_path="/__kdcube/reconfigure",
        )

    @api(alias="node_status")
    async def node_status(self, **kwargs) -> Dict[str, Any]:
        response = await self._node_bridge().get_json("/api/projects/status")
        return {
            "ok": response.ok,
            "status": response.status,
            "data": response.data,
            "error": response.error,
        }

    @api(alias="node_search")
    async def node_search(self, *, query: str = "", **kwargs) -> Dict[str, Any]:
        response = await self._node_bridge().post_json(
            "/api/projects/search",
            body={"query": str(query or "")},
        )
        return {
            "ok": response.ok,
            "status": response.status,
            "data": response.data,
            "error": response.error,
        }

    @mcp(alias="node_tools", route="operations", transport="streamable-http")
    def node_tools_mcp(self, **kwargs):
        if self._node_mcp_app is None:
            self._node_mcp_app = node_mcp_tools.build_node_bridge_mcp_app(
                name=f"{BUNDLE_ID}.node_tools",
                bridge_provider=self._node_bridge,
            )
        return self._node_mcp_app

    async def execute_core(
        self,
        *,
        state: Dict[str, Any],
        thread_id: str,
        params: Dict[str, Any],
    ):
        return await self.graph.ainvoke(state)
