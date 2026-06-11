from __future__ import annotations

import pathlib
from typing import Any, Dict, List, Mapping

from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    extend_tool_specs_for_named_services,
)

BUNDLE_ROOT = pathlib.Path(__file__).resolve().parent

BASE_TOOLS_SPECS: List[Dict[str, Any]] = [
    {
        "module": "kdcube_ai_app.apps.chat.sdk.tools.io_tools",
        "alias": "io_tools",
        "use_sk": True,
    },
    {
        "module": "kdcube_ai_app.apps.chat.sdk.tools.ctx_tools",
        "alias": "ctx_tools",
        "use_sk": True,
    },
    {
        "module": "kdcube_ai_app.apps.chat.sdk.context.memory.tools",
        "alias": "memory",
        "use_sk": True,
    },
    {
        # Exposes the single canvas tool `canvas.patch` (the pin tool): the
        # Agent pins a produced/identified ref onto the board via a new_card
        # op. The canvas event-source resolver (events_descriptor) stays loaded
        # for cnv: rehosting; this adds the explicit, model-callable pin path.
        "module": "kdcube_ai_app.apps.chat.sdk.solutions.canvas.tools",
        "alias": "canvas",
        "use_sk": True,
    },
    {
        "module": "kdcube_ai_app.apps.chat.sdk.tools.exec_tools",
        "alias": "exec_tools",
        "use_sk": True,
    },
    {
        "module": "kdcube_ai_app.apps.chat.sdk.tools.web_tools",
        "alias": "web_tools",
        "use_sk": True,
    },
    {
        "module": "kdcube_ai_app.apps.chat.sdk.tools.rendering_tools",
        "alias": "rendering_tools",
        "use_sk": True,
    },
    {
        "module": "kdcube_ai_app.apps.chat.sdk.tools.browser_tools",
        "alias": "browser_tools",
        "use_sk": True,
    },
]

TOOLS_SPECS: List[Dict[str, Any]] = list(BASE_TOOLS_SPECS)

MCP_TOOL_SPECS: List[Dict[str, Any]] = [
    {"server_id": "web_search", "alias": "web_search", "tools": ["web_search"]},
    {"server_id": "deepwiki", "alias": "deepwiki", "tools": ["*"]},
    {"server_id": "stack", "alias": "stack", "tools": ["*"]},
    {"server_id": "docs", "alias": "docs", "tools": ["*"]},
    {"server_id": "local", "alias": "local", "tools": ["*"]},
    {"server_id": "firecrawl", "alias": "firecrawl", "tools": ["*"]},
    {
        "server_id": "knowledge",
        "alias": "knowledge",
        "tools": ["*"],
    },
]

TOOL_RUNTIME: Dict[str, str] = {
    "web_tools.web_search": "local",
    "web_tools.fetch_url_contents": "local",
    "browser_tools.open_page": "none",
    "browser_tools.click": "none",
    "browser_tools.fill": "none",
    "browser_tools.scroll": "none",
    "browser_tools.status": "none",
    "browser_tools.close": "none",
}


def tools_for_client(
    client_id: str,
    *,
    bundle_props: Mapping[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    return extend_tool_specs_for_named_services(
        BASE_TOOLS_SPECS,
        bundle_props=bundle_props,
        client_id=client_id,
    )
