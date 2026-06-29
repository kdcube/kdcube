# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Economics-guarded embedding facade builder (feature-neutral).

This is the single source of truth for turning an entrypoint into a
search-aware model service. Entrypoints do not carry the build logic; they
simply call `make_search_model_service(self, flow=...)` (optionally passing an
explicit subject). The functions here depend only on standard entrypoint state
(`cp_manager` / `rl` / `budget_limiter`, `models_service`, and
`runtime_identity()` / `comm_context`), so any economics entrypoint — memory,
canvas, news, … — shares one copy of the facade.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Tuple

from kdcube_ai_app.apps.chat.sdk.infra.economics.enforcement import (
    EconomicsSubject,
    economics_subject_from_authority_context,
)
from kdcube_ai_app.apps.chat.sdk.solutions.search_service.model_service import EconomicSearchModelService

logger = logging.getLogger(__name__)


def economics_enabled(entrypoint: Any) -> bool:
    """True when the entrypoint has the economics runtime wired."""
    return bool(
        getattr(entrypoint, "cp_manager", None)
        and getattr(entrypoint, "rl", None)
        and getattr(entrypoint, "budget_limiter", None)
    )


def embedding_provider_model(entrypoint: Any) -> Tuple[str, str]:
    """Configured embedding provider/model used for search estimates."""
    model_service = getattr(entrypoint, "models_service", None)
    emb_model = getattr(model_service, "_emb_model", None)
    provider = ""
    model = ""
    try:
        provider_obj = getattr(getattr(emb_model, "provider", None), "provider", None)
        provider = str(getattr(provider_obj, "value", provider_obj) or "").strip()
        model = str(getattr(emb_model, "systemName", "") or "").strip()
    except Exception:
        provider = ""
        model = ""
    if provider and model:
        return provider, model
    cfg = getattr(getattr(model_service, "config", None), "embedder_config", None)
    if isinstance(cfg, dict):
        provider = str(cfg.get("provider") or "").strip()
        model = str(cfg.get("model_name") or cfg.get("model") or "").strip()
    return provider or "openai", model or "text-embedding-3-small"


def _identity_authority(entrypoint: Any) -> dict[str, Any]:
    user = getattr(getattr(entrypoint, "comm_context", None), "user", None)
    raw = getattr(user, "identity_authority", None)
    if isinstance(raw, Mapping):
        return dict(raw)
    comm = getattr(entrypoint, "comm", None)
    raw = getattr(comm, "identity_authority", None)
    if isinstance(raw, Mapping):
        return dict(raw)
    return {}


def economics_search_subject(entrypoint: Any) -> EconomicsSubject:
    """Feature-neutral EconomicsSubject for any in-request semantic search
    (issues, pins, news, …). Tenant/project from runtime identity; user_id +
    role from the authenticated session."""
    ident = entrypoint.runtime_identity() if hasattr(entrypoint, "runtime_identity") else {}
    user = getattr(getattr(entrypoint, "comm_context", None), "user", None)
    actor = getattr(getattr(entrypoint, "comm_context", None), "actor", None)
    authority = _identity_authority(entrypoint)
    fallback_user_id = str(getattr(user, "user_id", None) or getattr(actor, "user_id", None) or "").strip()
    return economics_subject_from_authority_context(
        tenant=str((ident or {}).get("tenant") or ""),
        project=str((ident or {}).get("project") or ""),
        identity_authority=authority,
        actor_user_id=fallback_user_id,
        fallback_user_id=fallback_user_id,
        fallback_roles=getattr(user, "roles", None) or (),
        fallback_permissions=getattr(user, "permissions", None) or (),
        fallback_user_type=str(getattr(user, "user_type", "") or ""),
    )


def make_search_model_service(entrypoint: Any, *, flow: str, subject: EconomicsSubject | None = None):
    """Model-service facade for searchable components.

    Components receive this single dependency. Query and document embeddings are
    guarded and settled by the facade; query callers may degrade to lexical
    search, while index/write callers should let failures propagate.

    Falls back to the raw `entrypoint.models_service` when economics is disabled,
    when the subject is anonymous/incomplete, or on any unexpected failure.
    """
    if not economics_enabled(entrypoint):
        return getattr(entrypoint, "models_service", None)
    try:
        subject = subject if subject is not None else economics_search_subject(entrypoint)
        if not (subject.tenant and subject.project and subject.user_id and subject.user_id != "anonymous"):
            return getattr(entrypoint, "models_service", None)
        provider, model = embedding_provider_model(entrypoint)
        return EconomicSearchModelService(
            entrypoint=entrypoint,
            model_service=entrypoint.models_service,
            subject=subject,
            provider=provider,
            model=model,
            default_flow=flow,
        )
    except Exception:
        logger.warning("[economics] search model service unavailable; embedding ungated", exc_info=True)
        return getattr(entrypoint, "models_service", None)


__all__ = [
    "economics_enabled",
    "embedding_provider_model",
    "economics_search_subject",
    "make_search_model_service",
]
