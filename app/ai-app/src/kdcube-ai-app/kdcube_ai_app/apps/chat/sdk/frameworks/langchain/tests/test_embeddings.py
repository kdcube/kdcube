# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Unit tests for KDCubeEmbeddings over a fake model service.

Covers the sync bridge from both call contexts: no running loop (plain sync
call) and inside a running event loop (a sync helper invoked from an async node),
plus the async variants.
"""
from __future__ import annotations

import asyncio

from kdcube_ai_app.apps.chat.sdk.frameworks.langchain import KDCubeEmbeddings


class _FakeModelService:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed_texts(self, texts):
        self.calls.append(list(texts))
        # Deterministic: vector length encodes the text length.
        return [[float(len(t)), 1.0, 2.0] for t in texts]


def test_embed_documents_sync_no_running_loop() -> None:
    ms = _FakeModelService()
    emb = KDCubeEmbeddings(ms)
    vecs = emb.embed_documents(["ab", "cde"])
    assert vecs == [[2.0, 1.0, 2.0], [3.0, 1.0, 2.0]]
    assert ms.calls == [["ab", "cde"]]


def test_embed_query_sync_no_running_loop() -> None:
    ms = _FakeModelService()
    emb = KDCubeEmbeddings(ms)
    assert emb.embed_query("hello") == [5.0, 1.0, 2.0]


def test_embed_documents_from_within_running_loop() -> None:
    """The sync method must work even when called on a thread with a live loop
    (mirrors a sync memory write invoked from an async graph node)."""
    ms = _FakeModelService()
    emb = KDCubeEmbeddings(ms)

    async def _go():
        # Direct sync call while an event loop is running in this thread.
        return emb.embed_documents(["xyz"])

    assert asyncio.run(_go()) == [[3.0, 1.0, 2.0]]


def test_async_variants() -> None:
    ms = _FakeModelService()
    emb = KDCubeEmbeddings(ms)

    async def _go():
        docs = await emb.aembed_documents(["ab"])
        one = await emb.aembed_query("cde")
        return docs, one

    docs, one = asyncio.run(_go())
    assert docs == [[2.0, 1.0, 2.0]]
    assert one == [3.0, 1.0, 2.0]
