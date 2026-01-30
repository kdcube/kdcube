# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""
MCP server wrapper for web search.

Supports:
  - stdio (on-demand / local process)
  - http / sse (remote server mode)

Tool implementation delegates to search_backends.web_search and uses
ModelService + cache built from environment variables.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import Any, Dict, List, Optional

from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase, _build_model_service_from_env
from kdcube_ai_app.infra.service_hub.cache import create_kv_cache_from_env
import kdcube_ai_app.apps.chat.sdk.tools.backends.web.search_backends as search_backends
from kdcube_ai_app.apps.chat.sdk.tools.mcp.mcp_app_transport import run_http, run_sse, run_stdio

_SERVICE: Optional[ModelServiceBase] = None
_CACHE = None


def _get_service() -> ModelServiceBase:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = _build_model_service_from_env()
    return _SERVICE


def _get_cache() -> Any:
    global _CACHE
    if _CACHE is None:
        _CACHE = create_kv_cache_from_env(ttl_env_var="WEB_SEARCH_CACHE_TTL_SECONDS")
    return _CACHE


async def web_search(
    queries: str | List[str],
    objective: Optional[str] = None,
    refinement: str = "balanced",
    n: int = 8,
    fetch_content: bool = True,
    include_binary_base64: bool = True,
    freshness: Optional[str] = None,
    country: Optional[str] = None,
    safesearch: str = "moderate",
) -> List[Dict[str, Any]]:
    svc = _get_service()
    cache = _get_cache()
    return await search_backends.web_search(
        _SERVICE=svc,
        queries=queries,
        objective=objective,
        refinement=refinement,
        n=n,
        fetch_content=fetch_content,
        include_binary_base64=include_binary_base64,
        freshness=freshness,
        country=country,
        safesearch=safesearch,
        namespaced_kv_cache=cache,
    )


def _build_mcp_app():
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception as e:  # pragma: no cover - runtime dependency
        raise ImportError("mcp server SDK is not installed") from e

    mcp = FastMCP("web_search")

    @mcp.tool(
        name="web_search",
        description="Web search with optional content fetch and refinement",
    )
    async def _tool(
        queries: str | List[str],
        objective: Optional[str] = None,
        refinement: str = "balanced",
        n: int = 8,
        fetch_content: bool = True,
        include_binary_base64: bool = True,
        freshness: Optional[str] = None,
        country: Optional[str] = None,
        safesearch: str = "moderate",
    ) -> List[Dict[str, Any]]:
        return await web_search(
            queries=queries,
            objective=objective,
            refinement=refinement,
            n=n,
            fetch_content=fetch_content,
            include_binary_base64=include_binary_base64,
            freshness=freshness,
            country=country,
            safesearch=safesearch,
        )

    return mcp


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP web search server")
    parser.add_argument("--transport", default="stdio", choices=["stdio", "sse", "http"])
    parser.add_argument("--host", default=os.environ.get("MCP_SERVER_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MCP_SERVER_PORT", "8787")))
    args = parser.parse_args()

    app = _build_mcp_app()
    if args.transport == "stdio":
        run_stdio(app)
    elif args.transport == "sse":
        run_sse(app, host=args.host, port=args.port)
    else:
        run_http(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
