# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Search solution: economics-aware semantic search support.

This package owns all search-specific logic that *uses* economics primitives:

- `model_service` — the economics-aware model-service facade
  (`EconomicSearchModelService`), the legacy verify-only
  `make_semantic_search_guard`, and embedding reservation/rate helpers.
- `factory` — feature-neutral builders that turn an entrypoint into a
  search-aware model service (`make_search_model_service`, ...).

Economics/accounting are the correct downward dependency: search → economics.
"""
from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.solutions.search_service.model_service import (
    EconomicSearchModelService,
    make_semantic_search_guard,
    embedding_rate_per_1m,
    embedding_reservation_usd,
    embedding_reservation_usd_for_texts,
)
from kdcube_ai_app.apps.chat.sdk.solutions.search_service.factory import (
    economics_enabled,
    embedding_provider_model,
    economics_search_subject,
    make_search_model_service,
)

__all__ = [
    "EconomicSearchModelService",
    "make_semantic_search_guard",
    "embedding_rate_per_1m",
    "embedding_reservation_usd",
    "embedding_reservation_usd_for_texts",
    "economics_enabled",
    "embedding_provider_model",
    "economics_search_subject",
    "make_search_model_service",
]
