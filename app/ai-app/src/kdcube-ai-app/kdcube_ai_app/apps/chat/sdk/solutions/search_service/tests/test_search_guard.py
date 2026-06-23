# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.search_service import model_service as search_guard
from kdcube_ai_app.apps.chat.sdk.infra.economics.enforcement import EconomicsSubject
from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import EconomicsLimitException
from kdcube_ai_app.infra import accounting as acct


async def test_nested_search_embedding_preserves_parent_turn_and_stamps_flow(monkeypatch):
    class _NestedGuard:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return SimpleNamespace(nested=True)

        async def __aexit__(self, exc_type, exc, tb):
            return False

    seen = {}

    class _ModelService:
        async def embed_texts(self, texts):
            seen["context"] = acct.get_context()
            return [[1.0, 2.0, 3.0]]

    monkeypatch.setattr(search_guard, "EconomicsGuard", _NestedGuard)

    svc = search_guard.EconomicSearchModelService(
        entrypoint=SimpleNamespace(),
        model_service=_ModelService(),
        subject=EconomicsSubject(
            tenant="t",
            project="p",
            user_id="u",
            user_type="registered",
        ),
        default_flow="memory.search",
    )

    async with acct.with_accounting(
        "chat.orchestrator",
        conversation_id="conv-1",
        turn_id="turn-1",
        request_id="req-1",
        metadata={"conversation_id": "conv-1", "turn_id": "turn-1"},
    ):
        assert await svc.embed_search_query("hello") == [1.0, 2.0, 3.0]

    context = seen["context"]
    assert context["component"] == "memory.search"
    assert context["conversation_id"] == "conv-1"
    assert context["turn_id"] == "turn-1"
    assert context["request_id"].startswith("memory_search_")


async def test_nested_document_embedding_preserves_parent_turn_and_stamps_flow(monkeypatch):
    class _NestedGuard:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return SimpleNamespace(nested=True)

        async def __aexit__(self, exc_type, exc, tb):
            return False

    seen = {}

    class _ModelService:
        async def embed_texts(self, texts):
            seen["context"] = acct.get_context()
            seen["texts"] = list(texts)
            return [[1.0, 2.0, 3.0] for _ in texts]

    monkeypatch.setattr(search_guard, "EconomicsGuard", _NestedGuard)

    svc = search_guard.EconomicSearchModelService(
        entrypoint=SimpleNamespace(),
        model_service=_ModelService(),
        subject=EconomicsSubject(
            tenant="t",
            project="p",
            user_id="u",
            user_type="registered",
        ),
        default_flow="canvas.pins.search",
    )

    async with acct.with_accounting(
        "chat.orchestrator",
        conversation_id="conv-1",
        turn_id="turn-1",
        request_id="req-1",
        metadata={"conversation_id": "conv-1", "turn_id": "turn-1"},
    ):
        assert await svc.embed_texts(["hello", "world"]) == [
            [1.0, 2.0, 3.0],
            [1.0, 2.0, 3.0],
        ]

    context = seen["context"]
    assert seen["texts"] == ["hello", "world"]
    assert context["component"] == "canvas.pins.search"
    assert context["conversation_id"] == "conv-1"
    assert context["turn_id"] == "turn-1"
    assert context["request_id"].startswith("canvas_pins_search_")


async def test_document_embedding_denial_propagates(monkeypatch):
    class _DenyGuard:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            raise EconomicsLimitException("rate limited", code="rate_limited")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _ModelService:
        async def embed_texts(self, texts):
            raise AssertionError("embedder must not run after economics denial")

    monkeypatch.setattr(search_guard, "EconomicsGuard", _DenyGuard)

    svc = search_guard.EconomicSearchModelService(
        entrypoint=SimpleNamespace(),
        model_service=_ModelService(),
        subject=EconomicsSubject(
            tenant="t",
            project="p",
            user_id="u",
            user_type="registered",
        ),
        default_flow="canvas.pins.search",
    )

    with pytest.raises(EconomicsLimitException):
        await svc.embed_texts(["hello"])
