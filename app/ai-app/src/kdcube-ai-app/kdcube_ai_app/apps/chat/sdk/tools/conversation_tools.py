# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Descriptor-selectable tools for the user's conversation memory realm."""

from __future__ import annotations

import json
from typing import Annotated, Any

import semantic_kernel as sk

try:
    from semantic_kernel.functions import kernel_function
except Exception:
    from semantic_kernel.utils.function_decorator import kernel_function

from kdcube_ai_app.apps.chat.sdk.solutions.conversation.api import (
    DEFAULT_TARGETS,
    SCOPE_USER,
    ConversationSearchParams,
    run_conversation_search,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.presentation import (
    turn_hit_to_object,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.search_backend import (
    conversation_search_context_from_tool_subsystem,
    make_conversation_search_backend_from_client,
)


_INTEGRATIONS: dict[str, Any] = {}


def bind_integrations(integrations: dict[str, Any] | None) -> None:
    global _INTEGRATIONS
    _INTEGRATIONS = dict(integrations or {})


def _error(code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "where": "conversation_tools.search",
            "managed": True,
        },
        "ret": None,
    }


def _targets(value: str | list[str]) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raw = str(value or "").strip()
    if not raw:
        return list(DEFAULT_TARGETS)
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = None
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    return [item.strip() for item in raw.split(",") if item.strip()]


class ConversationTools:
    @kernel_function(
        name="search",
        description=(
            "Search what this user said, what assistants answered, working summaries, "
            "and user attachment text in this or earlier conversations. Identity is "
            "bound by the harness and cannot be selected in tool arguments. Use "
            "scope='user' for cross-conversation recall and scope='conversation' for "
            "the current conversation. Returns {ok, error, ret}; ret.items contains "
            "turn refs and excerpts."
        ),
    )
    async def search(
        self,
        query: Annotated[str, "Topic or fact to recover from conversation history."],
        scope: Annotated[
            str,
            "Search scope: user for this user's conversations, conversation for only the current conversation, or agent for this user's conversations owned by the current agent.",
        ] = SCOPE_USER,
        targets: Annotated[
            str | list[str],
            "Content kinds: assistant, user, attachment, summary, notes. Pass a list or comma-separated string.",
        ] = "assistant,user,attachment,summary",
        top_k: Annotated[int, "Maximum matching turns to return (1-20)."] = 5,
        days: Annotated[int, "Maximum age in days for searched turns (1-3650)."] = 365,
        include_recovery_sessions: Annotated[
            bool,
            "Include recovery-session turns when true.",
        ] = False,
    ) -> dict[str, Any]:
        tool_subsystem = _INTEGRATIONS.get("tool_subsystem")
        context_client = _INTEGRATIONS.get("ctx_client")
        try:
            context = conversation_search_context_from_tool_subsystem(tool_subsystem)
            backend = make_conversation_search_backend_from_client(
                context_rag_client=context_client,
                context=context,
            )
            params = ConversationSearchParams.from_tool_params(
                {
                    "query": query,
                    "scope": scope or SCOPE_USER,
                    "targets": _targets(targets),
                    "top_k": max(1, min(int(top_k or 5), 20)),
                    "days": max(1, min(int(days or 365), 3650)),
                    "include_recovery_sessions": bool(include_recovery_sessions),
                }
            )
            result = await run_conversation_search(
                context=context,
                params=params,
                search_backend=backend,
            )
            if result.missing_query:
                return _error(
                    "conversation_query_required",
                    "query is required for conversation topic search",
                )
            return {
                "ok": True,
                "error": None,
                "ret": {
                    "items": [
                        turn_hit_to_object(hit)
                        for hit in result.hits
                        if isinstance(hit, dict) and str(hit.get("turn_id") or "").strip()
                    ],
                    "query": params.query,
                    "mode": result.effective_mode,
                    "scope": params.scope,
                    "tokens": result.tokens,
                    "warnings": list(result.warnings),
                },
            }
        except Exception as exc:
            return _error(type(exc).__name__, str(exc) or "conversation search failed")


kernel = sk.Kernel()
tools = ConversationTools()
kernel.add_plugin(tools, "conversation_tools")


__all__ = ["ConversationTools", "bind_integrations", "kernel", "tools"]
