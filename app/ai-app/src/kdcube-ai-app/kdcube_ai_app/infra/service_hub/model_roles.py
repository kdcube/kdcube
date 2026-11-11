# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Model-role vocabulary shared by the config and the model router.

A *role* (``classifier``, ``format_fixer``, ``answer_generator``, a bundle's own
role names) maps to ``{"provider": ..., "model": ...}``. Role specs arrive from
four scopes, lowest precedence first:

1. assembly ``models.role_models`` (``assembly.yaml``);
2. bundle ``config.role_models`` (bundle defaults merged with descriptor props);
3. per-agent ``config.react.<agent_id>.role_models``;
4. request-scoped ``bundle_call_context.role_models``.

Every role without a spec in any scope runs on ``models.default_llm_model_id``.
Provider ids are canonical everywhere: ``anthropic``, ``openai``, ``google``
(for ``gemini-*`` models), ``openrouter``, ``huggingface`` and ``custom``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

ASSEMBLY_ROLE_MODELS_KEY = "a:models.role_models"

# Non-canonical provider spellings accepted at the descriptor boundary.
_PROVIDER_ALIASES = {
    "gemini": "google",
}


def canonical_provider(provider: Any) -> str:
    """Return the canonical provider id, normalizing known aliases with a warning."""
    text = str(provider or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    canonical = _PROVIDER_ALIASES.get(lowered)
    if canonical:
        logger.warning(
            "model role provider %r is not a canonical provider id; using %r "
            "(Gemini models are configured as provider 'google', model 'gemini-*')",
            text,
            canonical,
        )
        return canonical
    return lowered


def normalize_role_spec(spec: Any) -> Optional[Dict[str, Any]]:
    """Normalize one role spec; a bare string is a model name.

    Returns ``None`` for anything that is not a spec. Keys other than
    ``provider`` and ``model`` (for example ``num_ctx``) are preserved.
    """
    if isinstance(spec, str):
        model = spec.strip()
        return {"model": model} if model else None
    if not isinstance(spec, Mapping):
        return None
    out: Dict[str, Any] = {
        str(key): value
        for key, value in spec.items()
        if key not in ("provider", "model") and value is not None
    }
    provider = canonical_provider(spec.get("provider"))
    model = str(spec.get("model") or "").strip()
    if provider:
        out["provider"] = provider
    if model:
        out["model"] = model
    return out if (provider or model) else None


def normalize_role_models(role_models: Any) -> Dict[str, Dict[str, Any]]:
    """Normalize a ``{role: spec}`` map, dropping entries that are not specs."""
    if not isinstance(role_models, Mapping):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for role, spec in role_models.items():
        name = str(role or "").strip()
        normalized = normalize_role_spec(spec)
        if name and normalized:
            out[name] = normalized
    return out


def assembly_role_models() -> Dict[str, Dict[str, Any]]:
    """Read assembly ``models.role_models``; empty when not configured."""
    try:
        from kdcube_ai_app.apps.chat.sdk.config import get_plain

        raw = get_plain(ASSEMBLY_ROLE_MODELS_KEY, None)
    except Exception:
        logger.debug("assembly models.role_models unavailable", exc_info=True)
        return {}
    return normalize_role_models(raw)
