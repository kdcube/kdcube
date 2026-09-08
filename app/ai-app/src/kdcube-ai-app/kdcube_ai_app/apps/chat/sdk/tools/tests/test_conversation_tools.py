# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.conversation.api import (
    ConversationSearchResult,
)
from kdcube_ai_app.apps.chat.sdk.tools import conversation_tools


def _tool_subsystem() -> SimpleNamespace:
    return SimpleNamespace(
        comm=SimpleNamespace(
            tenant="tenant-a",
            project="project-a",
            user_id="user-a",
            user_type="regular",
            service={
                "tenant": "tenant-a",
                "project": "project-a",
                "user": "user-a",
                "conversation_id": "conversation-current",
                "turn_id": "turn-current",
                "bundle_id": "bundle@1-0",
                "agent_id": "agent-a",
            },
            conversation={
                "conversation_id": "conversation-current",
                "turn_id": "turn-current",
            },
        ),
        registry={},
        bundle_spec=SimpleNamespace(id="bundle@1-0"),
    )


def test_model_facing_search_signature_has_no_identity_parameters() -> None:
    parameters = set(inspect.signature(conversation_tools.tools.search).parameters)

    assert parameters == {
        "query",
        "scope",
        "targets",
        "top_k",
        "days",
        "include_recovery_sessions",
    }
    assert not parameters.intersection(
        {
            "tenant",
            "project",
            "user_id",
            "conversation_id",
            "turn_id",
            "bundle_id",
            "agent_id",
        }
    )


@pytest.mark.asyncio
async def test_search_delegates_to_common_engine_with_bound_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = object()
    run = AsyncMock(
        return_value=ConversationSearchResult(
            hits=[
                {
                    "conversation_id": "conversation-earlier",
                    "turn_id": "turn-earlier",
                    "turn_index_path": "conv:ar:turn-earlier.react.turn.index",
                    "snippets": [
                        {
                            "role": "assistant",
                            "path": "conv:ar:turn-earlier.assistant.completion",
                            "text": "The retained source is https://example.test/source.",
                        }
                    ],
                    "score": 0.9,
                }
            ],
            effective_mode="hybrid",
            tokens=12,
        )
    )
    monkeypatch.setattr(
        conversation_tools,
        "_INTEGRATIONS",
        {"tool_subsystem": _tool_subsystem(), "ctx_client": object()},
    )
    monkeypatch.setattr(
        conversation_tools,
        "make_conversation_search_backend_from_client",
        lambda **_kwargs: backend,
    )
    monkeypatch.setattr(conversation_tools, "run_conversation_search", run)

    result = await conversation_tools.tools.search(
        query="retained source",
        scope="user",
        targets=["assistant", "summary"],
        top_k=3,
    )

    assert result["ok"] is True
    assert result["ret"]["scope"] == "user"
    assert result["ret"]["items"][0]["body"]["conversation_id"] == (
        "conversation-earlier"
    )
    call = run.await_args.kwargs
    assert call["search_backend"] is backend
    assert call["context"].user_id == "user-a"
    assert call["context"].conversation_id == "conversation-current"
    assert call["context"].agent_id == "agent-a"
    assert call["params"].scope == "user"
    assert call["params"].targets == ["assistant", "summary"]
    assert call["params"].top_k == 3


@pytest.mark.asyncio
async def test_search_returns_managed_error_without_bound_conversation_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        conversation_tools,
        "_INTEGRATIONS",
        {"tool_subsystem": _tool_subsystem(), "ctx_client": None},
    )

    result = await conversation_tools.tools.search(query="earlier decision")

    assert result["ok"] is False
    assert result["error"]["code"] == "RuntimeError"
    assert "conversation client" in result["error"]["message"]
