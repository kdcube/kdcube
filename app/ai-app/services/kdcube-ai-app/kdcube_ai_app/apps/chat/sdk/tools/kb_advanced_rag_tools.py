# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# -- sdk/tools/kb_advanced_rag_tools.py --
# SDK-level Semantic Kernel plugin exposing the advanced multi-step KB
# retrieval pipeline (history-aware query rewrite + entity extraction +
# dual-pass hybrid retrieval + compound rerank + ±N neighbor expansion).
#
# Any bundle can attach this tool via tools_descriptor.py:
#   {"module": "kdcube_ai_app.apps.chat.sdk.tools.kb_advanced_rag_tools",
#    "alias": "rag", "use_sk": True}
#
# The runtime instance (KBClient + ConversationStore + model service +
# RuntimeCtx accessor) is shared via the _kdcube_advanced_rag_state module,
# the same importlib pattern used by code_graph_tools.

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Annotated, Any

import semantic_kernel as sk

try:
    from semantic_kernel.functions import kernel_function
except Exception:
    from semantic_kernel.utils.function_decorator import kernel_function

from kdcube_ai_app.apps.chat.sdk.tools._advanced_rag_internal.pipeline import run_advanced_rag

logger = logging.getLogger(__name__)

_UNAVAILABLE = {
    "rewritten_query": "",
    "entities": [],
    "sources": [],
    "stats": {"available": False},
}


def _load_state():
    """Load the shared advanced-RAG state module via importlib (same pattern as code_graph)."""
    module_name = "_kdcube_advanced_rag_state"
    if module_name in sys.modules:
        return sys.modules[module_name]
    state_path = Path(__file__).resolve().parent / "_advanced_rag_state.py"
    if not state_path.exists():
        return None
    spec = importlib.util.spec_from_file_location(module_name, str(state_path))
    if not spec or not spec.loader:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)  # type: ignore
    return mod


def _get_runtime():
    state = _load_state()
    if state is None:
        return None
    return getattr(state, "RUNTIME", None)


def set_runtime(runtime: Any) -> None:
    """Install or clear the advanced-RAG runtime. Called by the bundle entrypoint."""
    state = _load_state()
    if state is None:
        return
    setattr(state, "RUNTIME", runtime)


class KBAdvancedRAGTools:
    """Advanced multi-step KB retrieval (rewrite → entities → hybrid → rerank → neighbors)."""

    @kernel_function(
        name="advanced_rag_search",
        description=(
            "Advanced KB retrieval for multi-hop or context-dependent questions. "
            "Performs history-aware query rewrite, entity extraction, dual-pass hybrid "
            "search (BM25 + pgvector), compound cross-encoder reranking, and optional "
            "±N neighbor expansion. Prefer this over plain search_knowledge when the "
            "question contains pronouns referring to earlier turns, names specific "
            "entities/identifiers, or needs reasoning across multiple chunks. "
            "Per-turn behaviour follows the user's UI search settings; sane defaults "
            "are applied when no settings are provided."
        ),
    )
    async def advanced_rag_search(
            self,
            query: Annotated[str, "Natural-language question. Pronouns and ellipsis are OK; the tool resolves them from conversation history."],
            top_k: Annotated[int, "Maximum number of source chunks to return (1-25)."] = 8,
            history_messages: Annotated[int, "Conversation messages used by query rewrite (0 disables rewrite)."] = 6,
    ) -> Annotated[dict | None, "{'rewritten_query', 'entities', 'sources': [{'sid','title','url','text',...}], 'stats'}"]:
        runtime = _get_runtime()
        if runtime is None:
            return _UNAVAILABLE
        try:
            top_k = max(1, min(int(top_k or 8), 25))
        except Exception:
            top_k = 8
        try:
            history_messages = max(0, min(int(history_messages or 6), 20))
        except Exception:
            history_messages = 6

        try:
            return await run_advanced_rag(
                runtime=runtime,
                query=query or "",
                top_k=top_k,
                history_messages=history_messages,
            )
        except Exception:
            logger.exception("advanced_rag_search pipeline error")
            return _UNAVAILABLE


# Module-level exports for SK + tool subsystem.
kernel = sk.Kernel()
tools = KBAdvancedRAGTools()
kernel.add_plugin(tools, "kb_advanced_rag")
