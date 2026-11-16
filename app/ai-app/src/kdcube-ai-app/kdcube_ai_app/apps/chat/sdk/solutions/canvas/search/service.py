# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Generic pin-board search service — usable by ANY bundle that mounts the canvas
solution, not just one bundle's service.

It derives its runtime model dependency from the host entrypoint's
`entrypoint.search_model_service(flow=...)` facade. That facade is the single
place where the runtime decides how semantic embeddings are authorized,
accounted, and degraded.

A bundle's canvas mount then needs no bespoke embed/guard code:

    pins = CanvasPinSearch(self)                 # `self` = the bundle entrypoint
    await pins.index(store=s, user_id=u, payload=p)   # on canvas update
    await pins.clear(store=s, user_id=u, payload=p)   # on canvas delete
    await pins.search(store=s, user_id=u, payload=p)  # on query (read-only)

Indexing (embedding) happens on updates; search is read-only and embeds only the
query (degrading to lexical when the facade declines). See `pin_search`/`pin_index`.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from kdcube_ai_app.infra.index.sqlite import VectorStore

from kdcube_ai_app.apps.chat.sdk.solutions.canvas.search.pin_search import (
    DEFAULT_EMBEDDING_DIM,
    DEFAULT_MIN_SEMANTIC_SCORE,
    clear_pins,
    index_pins,
    search_pins,
)


class CanvasPinSearch:
    """Bundle-agnostic pin search/index/clear over the canvas pin index."""

    def __init__(
        self,
        entrypoint: Any,
        *,
        flow: str = "canvas.pins.search",
        dim: int = DEFAULT_EMBEDDING_DIM,
        vector_backend: Optional[str] = None,
        vector_store: Optional[VectorStore] = None,
        min_semantic_score: Optional[float] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.entrypoint = entrypoint
        self.flow = flow
        self.dim = dim
        self.vector_store = vector_store
        # Vector backend: explicit arg > bundle prop `canvas.pin_search_backend` >
        # "faiss-local" (file-backed per-user faiss). Set "bruteforce" to opt out
        # (pure-python, no faiss dep).
        self.vector_backend = vector_backend or self._configured_backend(entrypoint)
        # Semantic floor: explicit arg > bundle prop
        # `canvas.pin_search_min_semantic_score` > default (0.30). Set < 0 (e.g. -1)
        # to turn the semantic factor off entirely (lexical + recency only) — for
        # bundles with no embeddings/budget. A query may still override per-call.
        self.min_semantic_score = (
            min_semantic_score if min_semantic_score is not None
            else self._configured_floor(entrypoint)
        )
        self.logger = logger or logging.getLogger("kdcube.canvas.pins")

    @staticmethod
    def _configured_backend(entrypoint: Any) -> str:
        bundle_prop = getattr(entrypoint, "bundle_prop", None)
        if callable(bundle_prop):
            try:
                return str(bundle_prop("canvas.pin_search_backend", "faiss-local") or "faiss-local")
            except Exception:
                pass
        return "faiss-local"

    @staticmethod
    def _configured_floor(entrypoint: Any) -> float:
        bundle_prop = getattr(entrypoint, "bundle_prop", None)
        if callable(bundle_prop):
            try:
                return float(bundle_prop("canvas.pin_search_min_semantic_score", DEFAULT_MIN_SEMANTIC_SCORE))
            except Exception:
                pass
        return DEFAULT_MIN_SEMANTIC_SCORE

    def _embed_fn(self, model_service: Any | None = None):
        model_service = model_service or self._model_service()
        embed_texts = getattr(model_service, "embed_texts", None)
        if embed_texts is None:
            raise RuntimeError("search model_service.embed_texts is not available for canvas pin search")
        return embed_texts

    def _model_service(self):
        factory = getattr(self.entrypoint, "search_model_service", None)
        if callable(factory):
            try:
                service = factory(flow=self.flow)
                if service is not None:
                    return service
            except Exception:
                self.logger.warning("[canvas.pins] search model service unavailable; using raw model service", exc_info=True)
        return getattr(self.entrypoint, "models_service", None)

    async def index(self, *, store: Any, user_id: str, payload: Mapping[str, Any]) -> dict:
        model_service = self._model_service()
        return await index_pins(
            store=store, user_id=user_id, payload=payload,
            embed_fn=self._embed_fn(model_service), model_service=model_service, dim=self.dim,
            vector_backend=self.vector_backend, vector_store=self.vector_store,
        )

    async def clear(self, *, store: Any, user_id: str, payload: Mapping[str, Any]) -> dict:
        model_service = self._model_service()
        return await clear_pins(
            store=store, user_id=user_id, payload=payload,
            embed_fn=self._embed_fn(model_service), model_service=model_service, dim=self.dim,
            vector_backend=self.vector_backend, vector_store=self.vector_store,
        )

    async def search(self, *, store: Any, user_id: str, payload: Mapping[str, Any]) -> dict:
        # Per-call override (e.g. a tool/UI passing min_semantic_score) wins over the
        # configured default; < 0 turns the semantic factor off for this query.
        floor = self.min_semantic_score
        if payload.get("min_semantic_score") is not None:
            try:
                floor = float(payload["min_semantic_score"])
            except (TypeError, ValueError):
                pass
        model_service = self._model_service()
        return await search_pins(
            store=store, user_id=user_id, payload=payload,
            embed_fn=self._embed_fn(model_service), model_service=model_service, dim=self.dim,
            vector_backend=self.vector_backend, vector_store=self.vector_store,
            semantic_guard=None, min_semantic_score=floor,
        )


__all__ = ["CanvasPinSearch"]
