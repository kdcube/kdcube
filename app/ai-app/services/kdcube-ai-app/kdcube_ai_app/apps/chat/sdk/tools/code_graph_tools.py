# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# -- sdk/tools/code_graph_tools.py --
# SDK-level Semantic Kernel plugin exposing code knowledge graph tools.
# Any bundle can reference this via:
#   {"module": "kdcube_ai_app.apps.chat.sdk.tools.code_graph_tools",
#    "alias": "code_graph", "use_sk": True}
#
# The client instance is shared via the _kdcube_code_graph_state module
# (importlib pattern, same as knowledge/resolver.py KNOWLEDGE_ROOT).

from __future__ import annotations

import json
import sys
import logging
import importlib.util
from pathlib import Path
from typing import Annotated, Any

import semantic_kernel as sk

try:
    from semantic_kernel.functions import kernel_function
except Exception:
    from semantic_kernel.utils.function_decorator import kernel_function

logger = logging.getLogger(__name__)

_GRAPH_UNAVAILABLE = "Code graph is not available. APP_GRAPH_ENABLED may be false or Neo4j is not running."


def _load_code_graph_state():
    """
    Load shared code graph state module.
    Uses a shared module name so that any entrypoint and this file
    access the same CLIENT global.
    """
    module_name = "_kdcube_code_graph_state"
    if module_name in sys.modules:
        return sys.modules[module_name]
    # Resolve from SDK tools directory
    state_path = Path(__file__).resolve().parent / "code_graph_state.py"
    if not state_path.exists():
        return None
    spec = importlib.util.spec_from_file_location(module_name, str(state_path))
    if not spec or not spec.loader:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)  # type: ignore
    return mod


def _get_client():
    """Retrieve the shared CodeGraphClient from the state module."""
    state = _load_code_graph_state()
    if state is None:
        return None
    return getattr(state, "CLIENT", None)


def _format_result(data: dict[str, Any]) -> str:
    """Format result dict as indented JSON for the agent."""
    return json.dumps(data, indent=2, default=str)


class CodeGraphTools:
    """Code knowledge graph tools -- structural code exploration via Neo4j."""

    @kernel_function(
        name="code_search",
        description=(
            "Search the code knowledge graph for classes, methods, functions, and modules. "
            "Supports three modes: 'fulltext' (fast keyword match), 'vector' (semantic similarity), "
            "'hybrid' (combined, default). Returns name, qualified_name, type, docstring, and score."
        ),
    )
    async def code_search(
        self,
        query: Annotated[str, "Search query -- class name, method name, or keyword (e.g. 'BaseEntrypoint', 'conversation store')."],
        search_type: Annotated[str, "Search mode: 'fulltext', 'vector', or 'hybrid' (default)."] = "hybrid",
        limit: Annotated[int, "Max results to return."] = 10,
    ) -> Annotated[str, "JSON array of matching code symbols with scores."]:
        client = _get_client()
        if not client or not getattr(client, "enabled", False):
            return _GRAPH_UNAVAILABLE
        result = await client.code_search(search_query=query, search_type=search_type, limit=limit)
        return _format_result(result)

    @kernel_function(
        name="class_footprint",
        description=(
            "Get the full footprint of a class: inheritance chain (ancestors + descendants), interfaces, "
            "methods (with signatures), properties, callers, callees, linked docs, tests, and decorators. "
            "Use the fully qualified name (e.g. 'kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint.BaseEntrypoint')."
        ),
    )
    async def class_footprint(
        self,
        qualified_name: Annotated[str, "Fully qualified class name (dot-separated Python path)."],
    ) -> Annotated[str, "JSON with class details: ancestors, descendants, methods, properties, callers, docs, tests."]:
        client = _get_client()
        if not client or not getattr(client, "enabled", False):
            return _GRAPH_UNAVAILABLE
        result = await client.class_footprint(qualified_name=qualified_name)
        return _format_result(result)

    @kernel_function(
        name="show_architecture",
        description=(
            "Show the package/module/class architecture tree. Optionally filter by package prefix "
            "(e.g. 'kdcube_ai_app.apps.chat' to see only chat subsystem). Returns packages with their modules and classes."
        ),
    )
    async def show_architecture(
        self,
        package_filter: Annotated[str, "Package prefix filter (e.g. 'kdcube_ai_app.apps.chat'). Empty string for all."] = "",
    ) -> Annotated[str, "JSON array of packages with modules and class counts."]:
        client = _get_client()
        if not client or not getattr(client, "enabled", False):
            return _GRAPH_UNAVAILABLE
        result = await client.show_architecture(package_filter=package_filter)
        return _format_result(result)

    @kernel_function(
        name="trace_call_chain",
        description=(
            "Trace the call chain from a function/method up to 5 levels deep. "
            "Shows the execution flow: who calls what, in order. Useful for understanding control flow."
        ),
    )
    async def trace_call_chain(
        self,
        qualified_name: Annotated[str, "Fully qualified name of the entry function/method to trace from."],
        max_depth: Annotated[int, "Maximum call depth to trace (1-5)."] = 5,
    ) -> Annotated[str, "JSON with call chains and depths."]:
        client = _get_client()
        if not client or not getattr(client, "enabled", False):
            return _GRAPH_UNAVAILABLE
        result = await client.trace_call_chain(qualified_name=qualified_name, max_depth=max_depth)
        return _format_result(result)

    @kernel_function(
        name="find_references",
        description=(
            "Find all references to a symbol: callers, subclasses, implementors, overrides, "
            "generic references, and tests. Comprehensive cross-reference lookup."
        ),
    )
    async def find_references(
        self,
        qualified_name: Annotated[str, "Fully qualified name of the symbol to find references for."],
    ) -> Annotated[str, "JSON with callers, subclasses, implementors, overrides, references, tests."]:
        client = _get_client()
        if not client or not getattr(client, "enabled", False):
            return _GRAPH_UNAVAILABLE
        result = await client.find_references(qualified_name=qualified_name)
        return _format_result(result)

    @kernel_function(
        name="impact_analysis",
        description=(
            "Analyze the impact of changing a symbol: who calls it, what subclasses it, "
            "what overrides it, and which tests cover it. Use before modifying public APIs."
        ),
    )
    async def impact_analysis(
        self,
        qualified_name: Annotated[str, "Fully qualified name of the symbol to analyze."],
    ) -> Annotated[str, "JSON with callers, subclasses, overrides, and tests that would be affected."]:
        client = _get_client()
        if not client or not getattr(client, "enabled", False):
            return _GRAPH_UNAVAILABLE
        result = await client.impact_analysis(qualified_name=qualified_name)
        return _format_result(result)

    @kernel_function(
        name="find_docs_for_code",
        description=(
            "Find documentation sections linked to a code symbol. Returns doc titles, file paths, "
            "section paths, and text previews. Useful for understanding the 'why' behind code."
        ),
    )
    async def find_docs_for_code(
        self,
        qualified_name: Annotated[str, "Fully qualified name of the code symbol to find docs for."],
    ) -> Annotated[str, "JSON with linked documentation sections."]:
        client = _get_client()
        if not client or not getattr(client, "enabled", False):
            return _GRAPH_UNAVAILABLE
        result = await client.find_docs_for_code(qualified_name=qualified_name)
        return _format_result(result)


kernel = sk.Kernel()
tools = CodeGraphTools()
kernel.add_plugin(tools, "code_graph")
