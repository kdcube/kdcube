# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# ── tools/local_tools.py ──
# Site-scoped web search tool — restricts results to kdcube.tech.
#
# Currently DISABLED in tools_descriptor.py (the entry is commented out).
# To enable, uncomment the {"ref": "tools/local_tools.py", ...} entry
# in TOOLS_SPECS.
#
# The tool uses the SDK's search_backends.web_search() backend and
# prepends "site:kdcube.tech" to every query. Dependency injection is
# handled via bind_service() and bind_integrations() hooks called by
# the tool loader at startup.

from __future__ import annotations

from typing import Annotated, Optional, Any

import semantic_kernel as sk

try:
    from semantic_kernel.functions import kernel_function
except Exception:
    from semantic_kernel.utils.function_decorator import kernel_function

import kdcube_ai_app.apps.chat.sdk.tools.backends.web.search_backends as search_backends

# Injected at startup by the tool loader via bind_service() / bind_integrations()
_SERVICE = None
_INTEGRATIONS = None


def bind_service(svc):
    """Called by the tool loader — injects the model service for embeddings/reranking."""
    global _SERVICE
    _SERVICE = svc


def bind_integrations(integrations):
    """Called by the tool loader — injects shared integrations (kv_cache, etc.)."""
    global _INTEGRATIONS
    _INTEGRATIONS = integrations or {}


@kernel_function(
    name="search",
    description=(
        "Search the official KDCube site (kdcube.tech) for product information. "
        "Returns an array of results added to the sources pool."
    ),
)
async def search(
    query: Annotated[str, "Query focused on KDCube features, components, or architecture."],
    objective: Annotated[Optional[str], "Optional objective to refine results."] = None,
    n: Annotated[int, "Max results (1-8). Prefer 3-5."] = 5,
    fetch_content: Annotated[bool, "Fetch page content when available."] = True,
) -> Annotated[list[dict] | None, "List of sources (sid/title/url/text/etc)"]:
    if not query or not str(query).strip():
        return []
    q = f"site:kdcube.tech {str(query).strip()}"
    cache: Any | None = None
    if isinstance(_INTEGRATIONS, dict):
        cache = _INTEGRATIONS.get("kv_cache")
    return await search_backends.web_search(
        _SERVICE=_SERVICE,
        queries=[q],
        objective=objective or "KDCube product information",
        refinement="balanced",
        n=n,
        fetch_content=fetch_content,
        namespaced_kv_cache=cache,
    )
