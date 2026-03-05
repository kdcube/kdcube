# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# ── tools/react_tools.py ──
# Bundle-local tool: react.search_knowledge
#
# Provides the LLM agent with the ability to search the knowledge space
# index (index.json) for relevant docs by keyword matching. Registered
# as a Semantic Kernel plugin with alias "react" in tools_descriptor.py,
# so the tool ID becomes "react.search_knowledge".
#
# The knowledge resolver is loaded via importlib with a shared module name
# (_kdcube_react_doc_knowledge_resolver) so that this file and entrypoint.py
# both access the same KNOWLEDGE_ROOT global state.

from __future__ import annotations

from typing import Annotated, Optional, Any
from pathlib import Path
import importlib.util
import sys

import semantic_kernel as sk

try:
    from semantic_kernel.functions import kernel_function
except Exception:
    from semantic_kernel.utils.function_decorator import kernel_function

def _load_knowledge_resolver():
    """
    Load bundle-local knowledge resolver by file path.
    Tool modules are loaded via spec_from_file_location, so relative imports
    won't work unless we resolve them manually. Uses a shared module name
    to ensure a single KNOWLEDGE_ROOT across entrypoint + tools.
    """
    module_name = "_kdcube_react_doc_knowledge_resolver"
    if module_name in sys.modules:
        return sys.modules[module_name]

    bundle_root = Path(__file__).resolve().parent.parent
    resolver_path = bundle_root / "knowledge" / "resolver.py"
    if not resolver_path.exists():
        raise ImportError(f"Knowledge resolver not found: {resolver_path}")

    spec = importlib.util.spec_from_file_location(module_name, str(resolver_path))
    if not spec or not spec.loader:
        raise ImportError(f"Cannot load knowledge resolver: {resolver_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)  # type: ignore
    return mod


class ReactDocTools:
    """SK plugin exposing knowledge-space search to the ReAct agent."""
    @kernel_function(
        name="search_knowledge",
        description=(
            "Search the local knowledge space (docs + sources) exposed by this bundle. "
            "Use this for product/architecture questions before external search."
        ),
    )
    async def search_knowledge(
        self,
        query: Annotated[str, "Search query for docs/knowledge index."],
        root: Annotated[Optional[str], "Optional root selector, e.g. ks:docs (default)."] = "ks:docs",
        keywords: Annotated[Optional[list[str]], "Optional keywords/tags to bias ranking."] = None,
        top_k: Annotated[int, "Max hits to return (default 20)."] = 20,
    ) -> Annotated[list[dict] | None, "List of hits with ks: paths + titles."]:
        if not query or not str(query).strip():
            return []
        root_sel = (root or "ks:docs").strip()
        try:
            knowledge_resolver = _load_knowledge_resolver()
            result = knowledge_resolver.search_knowledge(
                query=str(query).strip(),
                root=root_sel,
                max_hits=int(top_k or 20),
                keywords=keywords,
            )
            if hasattr(result, "__await__"):
                result = await result
            if isinstance(result, list):
                return result
            if isinstance(result, dict):
                return list(result.get("hits") or [])
            return []
        except Exception:
            return []


# Module-level exports for SK + tool subsystem.
# The tool loader expects a `kernel` with registered plugins at module scope.
kernel = sk.Kernel()
tools = ReactDocTools()
kernel.add_plugin(tools, "react_doc_tools")
