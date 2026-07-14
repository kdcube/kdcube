# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""A LangChain embeddings provider backed by KDCube's model service.

`KDCubeEmbeddings` implements LangChain's synchronous ``Embeddings`` interface
over any object exposing an async ``embed_texts`` method — the raw accounted
``ModelServiceBase`` or the economics-guarded ``EconomicSearchModelService``
facade (both bill per text via ``@track_embedding``; the facade adds a per-call
budget preflight/settlement). A ported agent that calls ``embed_documents`` /
``embed_query`` synchronously keeps working while embeddings are billed to the
turn. Async variants are provided for callers that can await.
"""
from __future__ import annotations

import asyncio
import contextvars
from concurrent.futures import ThreadPoolExecutor
from typing import Any, List

from langchain_core.embeddings import Embeddings


def _run_coro_blocking(coro: Any) -> Any:
    """Run ``coro`` to completion from a synchronous caller.

    LangChain's ``Embeddings`` contract is synchronous, but ``embed_texts`` is a
    coroutine. Two situations arise inside a ported graph:

      - Called from a worker thread (LangGraph runs *sync* nodes in an executor)
        where no event loop is running -> ``asyncio.run`` directly.
      - Called on the main thread *while* an event loop is running (a sync helper
        invoked from an ``async`` node) -> ``asyncio.run`` would raise. Run the
        coroutine in a one-off worker thread instead, under a COPY of the current
        ``contextvars.Context`` so the turn's bound accounting envelope is still
        visible to ``@track_embedding`` and embeddings are billed to this turn.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    ctx = contextvars.copy_context()
    with ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: ctx.run(asyncio.run, coro)).result()


class KDCubeEmbeddings(Embeddings):
    """LangChain ``Embeddings`` over any object exposing async ``embed_texts``.

    The wrapped ``embedder`` is anything that implements
    ``async embed_texts(texts) -> list[list[float]]``. Two common providers:

      - ``ModelServiceBase`` — the raw accounted model service (bills each text
        via ``@track_embedding`` within the turn's accounting context).
      - ``EconomicSearchModelService`` — the economics-guarded search facade
        (per-call budget preflight/settlement on top of the same accounting).

    The handle is named ``embedder``; the legacy ``models_service`` positional /
    keyword and the ``self.models_service`` attribute are preserved for
    backward compatibility.
    """

    def __init__(self, embedder: Any = None, *, models_service: Any = None) -> None:
        resolved = embedder if embedder is not None else models_service
        self.embedder = resolved
        # Backward-compat alias: older callers/tests read `.models_service`.
        self.models_service = resolved

    # -- sync (LangChain Embeddings contract) -------------------------------

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return _run_coro_blocking(self.embedder.embed_texts(list(texts)))

    def embed_query(self, text: str) -> List[float]:
        return _run_coro_blocking(self.embedder.embed_texts([text]))[0]

    # -- async (for callers that can await) ---------------------------------

    async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
        return await self.embedder.embed_texts(list(texts))

    async def aembed_query(self, text: str) -> List[float]:
        return (await self.embedder.embed_texts([text]))[0]
