# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Economics support for semantic search.

Semantic search runs a (cheap but non-zero) embedder call per query. The
preferred integration is to pass `EconomicSearchModelService` to the searchable
component as its `model_service`. The component calls `embed_search_query(...)`;
the facade reserves, binds accounting, runs the underlying embedder, and settles
the embedding call at the service boundary. If the facade is entered inside
another active `EconomicsGuard` for a local composite flow, that inner guard
degrades to verify-only and the active guard settles the tracked event.

`make_semantic_search_guard(...)` remains as a legacy verify-only predicate for
older components that still expose a separate `semantic_guard` hook.

The reservation estimate is grounded in the live price table (text-embedding-3-small
= $0.02 / 1M tokens), sized to the query — pennies-to-fractions, enough for the
feasibility/quota/funding check.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Awaitable, Callable, Optional, Sequence
from uuid import uuid4

from kdcube_ai_app.apps.chat.sdk.infra.economics.enforcement import (
    EconomicsGuard,
    EconomicsEstimate,
    EconomicsSubject,
    FlowPolicy,
    economic_preflight,
)
from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import EconomicsLimitException
from kdcube_ai_app.infra.accounting.usage import (
    embedding_price_usd_per_1m,
    estimate_embedding_tokens,
    quote_embedding_usd,
)

DEFAULT_EMBEDDING_PROVIDER = "openai"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
_MIN_RESERVATION_USD = 1e-6
logger = logging.getLogger(__name__)


def embedding_rate_per_1m(
    model: str = DEFAULT_EMBEDDING_MODEL,
    *,
    provider: str = DEFAULT_EMBEDDING_PROVIDER,
) -> float:
    """USD per 1M embedding tokens for provider/model, read from the price table."""
    rate = embedding_price_usd_per_1m(provider=provider, model=model)
    if rate > 0:
        return rate
    if provider == DEFAULT_EMBEDDING_PROVIDER and model == DEFAULT_EMBEDDING_MODEL:
        return 0.02
    return 0.0


def embedding_reservation_usd(
    query: str,
    *,
    model: str = DEFAULT_EMBEDDING_MODEL,
    provider: str = DEFAULT_EMBEDDING_PROVIDER,
) -> float:
    """Estimated USD to embed one query: price-table rate × shared token estimate."""
    return embedding_reservation_usd_for_texts(
        [query],
        provider=provider,
        model=model,
    )


def embedding_reservation_usd_for_texts(
    texts: Sequence[str],
    *,
    model: str = DEFAULT_EMBEDDING_MODEL,
    provider: str = DEFAULT_EMBEDDING_PROVIDER,
) -> float:
    """Estimated USD to embed a batch, floored to a non-zero feasibility amount."""
    cost = quote_embedding_usd(
        [str(text or "") for text in texts],
        provider=provider,
        model=model,
        min_tokens_per_text=16,
    )
    return max(_MIN_RESERVATION_USD, float(cost or 0.0))


def make_semantic_search_guard(
    entrypoint: Any,
    *,
    subject: EconomicsSubject,
    provider: str = DEFAULT_EMBEDDING_PROVIDER,
    model: str = DEFAULT_EMBEDDING_MODEL,
    flow: str = "search.semantic",
    policy: Optional[FlowPolicy] = None,
) -> Callable[[str], Awaitable[bool]]:
    """Build the async legacy `semantic_guard` predicate for a search index.

    Returns an async `(query) -> bool`: True when the user may incur the embed
    (feasibility verified via `economic_preflight`), False on `EconomicsLimitException`
    so the index degrades to lexical. New components should prefer
    `EconomicSearchModelService`.
    """
    flow_policy = policy or FlowPolicy(enforce_concurrency=False, emit_user_events=False)

    async def guard(query: str) -> bool:
        try:
            await economic_preflight(
                entrypoint,
                subject=subject,
                estimate=EconomicsEstimate(
                    reservation_usd=embedding_reservation_usd(
                        query,
                        provider=provider,
                        model=model,
                    ),
                    min_tokens=max(1, estimate_embedding_tokens(query, min_tokens=16)),
                ),
                flow=flow,
                policy=flow_policy,
            )
            return True
        except EconomicsLimitException:
            return False

    return guard


class EconomicSearchModelService:
    """Economics-aware model-service facade for searchable components.

    Components receive this as their `model_service` and only call model-service
    methods. They do not compose economics guards, provider/model pricing, or
    settlement scopes themselves.
    """

    def __init__(
        self,
        *,
        entrypoint: Any,
        model_service: Any,
        subject: EconomicsSubject,
        provider: str = DEFAULT_EMBEDDING_PROVIDER,
        model: str = DEFAULT_EMBEDDING_MODEL,
        default_flow: str = "search.semantic",
        policy: Optional[FlowPolicy] = None,
    ) -> None:
        self.entrypoint = entrypoint
        self.model_service = model_service
        self.subject = subject
        self.provider = provider
        self.model = model
        self.default_flow = default_flow
        self.policy = policy or FlowPolicy(enforce_concurrency=False, emit_user_events=False)

    def _scope_id(self, flow_name: str) -> str:
        return f"{flow_name.replace('.', '_')}_{uuid4().hex}"

    def _embedder_debug(self) -> dict[str, Any]:
        emb_model = getattr(self.model_service, "_emb_model", None)
        token = ""
        try:
            token = str(getattr(getattr(emb_model, "provider", None), "apiToken", "") or "")
        except Exception:
            token = ""
        provider = self.provider
        model = self.model
        try:
            provider_value = getattr(getattr(getattr(emb_model, "provider", None), "provider", None), "value", None)
            provider = str(provider_value or provider or "")
            model = str(getattr(emb_model, "systemName", "") or model or "")
        except Exception:
            pass
        return {
            "provider": provider,
            "model": model,
            "token_present": bool(token),
            "token_len": len(token),
            "token_sha8": hashlib.sha256(token.encode()).hexdigest()[:8] if token else "",
            "model_service_id": id(self.model_service),
        }

    async def _run_guarded_embedding(
        self,
        texts: Sequence[str],
        *,
        flow_name: str,
        scope_id: str,
        reservation_usd: float,
        min_tokens: int,
    ) -> list[list[float]]:
        provenance = self.subject.provenance if isinstance(self.subject.provenance, dict) else {}
        logger.info(
            "[economics.search] guarded embedding flow=%s subject_user=%s actor_user=%s budget_bypass=%s roles=%s provider=%s model=%s text_count=%s",
            flow_name,
            self.subject.user_id,
            provenance.get("actor_user_id") or "",
            self.subject.budget_bypass,
            list(self.subject.roles or ()),
            self.provider,
            self.model,
            len(texts or ()),
        )
        async with EconomicsGuard(
            self.entrypoint,
            subject=self.subject,
            scope_id=scope_id,
            flow=flow_name,
            estimate=EconomicsEstimate(
                reservation_usd=reservation_usd,
                min_tokens=max(1, int(min_tokens or 1)),
            ),
            policy=self.policy,
        ) as decision:
            if bool(getattr(decision, "nested", False)):
                from kdcube_ai_app.infra import accounting as acct

                async with acct.with_accounting(
                    flow_name,
                    request_id=scope_id,
                    metadata={
                        "flow": flow_name,
                        "scope_id": scope_id,
                    },
                ):
                    return await self.model_service.embed_texts(list(texts))
            return await self.model_service.embed_texts(list(texts))

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Document/index embeddings through the same runtime economics facade.

        Unlike query embeddings, document/index embeddings are part of write-side
        index correctness. If the guard denies or the embedder fails, the
        exception propagates and the owning index operation fails loudly.
        """
        batch = [str(text or "") for text in texts]
        if not batch:
            return []
        flow_name = self.default_flow
        scope_id = self._scope_id(flow_name)
        reservation_usd = embedding_reservation_usd_for_texts(
            batch,
            provider=self.provider,
            model=self.model,
        )
        min_tokens = sum(max(1, estimate_embedding_tokens(text, min_tokens=16)) for text in batch)
        dbg = self._embedder_debug()
        logger.info(
            "[search.embedding] index embed start flow=%s scope_id=%s provider=%s model=%s docs=%s chars=%s token_present=%s token_len=%s token_sha8=%s model_service_id=%s",
            flow_name,
            scope_id,
            dbg["provider"],
            dbg["model"],
            len(batch),
            sum(len(text) for text in batch),
            dbg["token_present"],
            dbg["token_len"],
            dbg["token_sha8"],
            dbg["model_service_id"],
        )
        try:
            vectors = await self._run_guarded_embedding(
                batch,
                flow_name=flow_name,
                scope_id=scope_id,
                reservation_usd=reservation_usd,
                min_tokens=min_tokens,
            )
        except Exception:
            logger.warning(
                "[search.embedding] index embed failed flow=%s scope_id=%s provider=%s model=%s docs=%s token_present=%s token_len=%s token_sha8=%s model_service_id=%s",
                flow_name,
                scope_id,
                dbg["provider"],
                dbg["model"],
                len(batch),
                dbg["token_present"],
                dbg["token_len"],
                dbg["token_sha8"],
                dbg["model_service_id"],
                exc_info=True,
            )
            raise
        logger.info(
            "[search.embedding] index embed done flow=%s scope_id=%s provider=%s model=%s vectors=%s dim=%s model_service_id=%s",
            flow_name,
            scope_id,
            dbg["provider"],
            dbg["model"],
            len(vectors),
            len(vectors[0]) if vectors else 0,
            dbg["model_service_id"],
        )
        return vectors

    async def embed_search_query(self, query: str, *, flow: Optional[str] = None) -> list[float] | None:
        """Query embedding with economics around the actual model call.

        This reserves, binds accounting, runs the embedding, and settles at the
        service boundary. Inside another active `EconomicsGuard`, the guard
        degrades to verify-only and the active guard settles the tracked event.
        """
        text = str(query or "").strip()
        if not text:
            return None
        flow_name = flow or self.default_flow
        reservation_usd = embedding_reservation_usd(
            text,
            provider=self.provider,
            model=self.model,
        )
        scope_id = self._scope_id(flow_name)
        dbg = self._embedder_debug()
        logger.info(
            "[search.embedding] query embed start flow=%s scope_id=%s provider=%s model=%s chars=%s token_present=%s token_len=%s token_sha8=%s model_service_id=%s",
            flow_name,
            scope_id,
            dbg["provider"],
            dbg["model"],
            len(text),
            dbg["token_present"],
            dbg["token_len"],
            dbg["token_sha8"],
            dbg["model_service_id"],
        )
        try:
            vectors = await self._run_guarded_embedding(
                [text],
                flow_name=flow_name,
                scope_id=scope_id,
                reservation_usd=reservation_usd,
                min_tokens=max(1, estimate_embedding_tokens(text, min_tokens=16)),
            )
            vector = vectors[0] if vectors else None
            logger.info(
                "[search.embedding] query embed done flow=%s scope_id=%s provider=%s model=%s dim=%s model_service_id=%s",
                flow_name,
                scope_id,
                dbg["provider"],
                dbg["model"],
                len(vector) if vector else 0,
                dbg["model_service_id"],
            )
            return vector
        except EconomicsLimitException as exc:
            logger.info(
                "[economics.enforcement] semantic search denied; degrading to lexical flow=%s scope_id=%s provider=%s model=%s code=%s",
                flow_name,
                scope_id,
                self.provider,
                self.model,
                getattr(exc, "code", "rate_limited"),
            )
            return None
        except Exception:
            logger.warning(
                "[search.embedding] query embed failed; degrading to lexical flow=%s scope_id=%s provider=%s model=%s token_present=%s token_len=%s token_sha8=%s model_service_id=%s",
                flow_name,
                scope_id,
                dbg["provider"],
                dbg["model"],
                dbg["token_present"],
                dbg["token_len"],
                dbg["token_sha8"],
                dbg["model_service_id"],
                exc_info=True,
            )
            return None


__all__ = [
    "make_semantic_search_guard",
    "EconomicSearchModelService",
    "embedding_reservation_usd",
    "embedding_reservation_usd_for_texts",
    "embedding_rate_per_1m",
    "DEFAULT_EMBEDDING_PROVIDER",
    "DEFAULT_EMBEDDING_MODEL",
]
