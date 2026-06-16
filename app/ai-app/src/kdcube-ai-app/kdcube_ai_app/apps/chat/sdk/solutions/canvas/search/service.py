# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Generic pin-board search service — usable by ANY bundle that mounts the canvas
solution, not just one bundle's service.

It derives the two runtime dependencies from the host entrypoint:
  - the embedder, `entrypoint.models_service.embed_texts`, and
  - the economics guard, `entrypoint.search_semantic_guard(flow=...)` — the SAME
    verify-only `economic_preflight` gate memory and task-tracker search use.

A bundle's canvas mount then needs no bespoke embed/guard code:

    pins = CanvasPinSearch(self)                 # `self` = the bundle entrypoint
    await pins.index(store=s, user_id=u, story_id=t, payload=p)   # on canvas update
    await pins.clear(store=s, user_id=u, story_id=t, payload=p)   # on canvas delete
    await pins.search(store=s, user_id=u, story_id=t, payload=p)  # on query (read-only)

Indexing (embedding) happens on updates; search is read-only and embeds only the
query (degrading to lexical when the guard denies). See `pin_search`/`pin_index`.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from kdcube_ai_app.infra.index.sqlite import VectorStore

from .pin_search import DEFAULT_EMBEDDING_DIM, clear_pins, index_pins, search_pins


class CanvasPinSearch:
    """Bundle-agnostic pin search/index/clear over the canvas pin index."""

    def __init__(
        self,
        entrypoint: Any,
        *,
        flow: str = "canvas.pins.search",
        dim: int = DEFAULT_EMBEDDING_DIM,
        vector_store: Optional[VectorStore] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.entrypoint = entrypoint
        self.flow = flow
        self.dim = dim
        self.vector_store = vector_store
        self.logger = logger or logging.getLogger("kdcube.canvas.pins")

    def _embed_fn(self):
        model_service = getattr(self.entrypoint, "models_service", None)
        embed_texts = getattr(model_service, "embed_texts", None)
        if embed_texts is None:
            raise RuntimeError("models_service.embed_texts is not available for canvas pin search")
        return embed_texts

    def _guard(self):
        """The shared economics guard, if the host entrypoint provides it
        (chat-derived bundles do). None → search stays ungated → lexical-capable."""
        guard_factory = getattr(self.entrypoint, "search_semantic_guard", None)
        return guard_factory(flow=self.flow) if callable(guard_factory) else None

    async def index(self, *, store: Any, user_id: str, story_id: str, payload: Mapping[str, Any]) -> dict:
        return await index_pins(
            store=store, user_id=user_id, story_id=story_id, payload=payload,
            embed_fn=self._embed_fn(), dim=self.dim, vector_store=self.vector_store,
        )

    async def clear(self, *, store: Any, user_id: str, story_id: str, payload: Mapping[str, Any]) -> dict:
        return await clear_pins(
            store=store, user_id=user_id, story_id=story_id, payload=payload,
            embed_fn=self._embed_fn(), dim=self.dim, vector_store=self.vector_store,
        )

    async def search(self, *, store: Any, user_id: str, story_id: str, payload: Mapping[str, Any]) -> dict:
        return await search_pins(
            store=store, user_id=user_id, story_id=story_id, payload=payload,
            embed_fn=self._embed_fn(), dim=self.dim, vector_store=self.vector_store,
            semantic_guard=self._guard(),
        )


__all__ = ["CanvasPinSearch"]
