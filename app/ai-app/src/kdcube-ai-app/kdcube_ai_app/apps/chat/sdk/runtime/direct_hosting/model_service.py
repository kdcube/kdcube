"""Build the SDK model service from the standard platform descriptors."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from kdcube_ai_app.apps.chat.reg import MODEL_CONFIGS
from kdcube_ai_app.apps.chat.sdk.config import get_plain, get_settings
from kdcube_ai_app.infra.service_hub.inventory import (
    ConfigRequest,
    ModelServiceBase,
    create_workflow_config,
    resolve_config_request_secrets,
)


@dataclass(frozen=True)
class DirectModelSelection:
    """One descriptor-owned provider/model choice for a direct agent host."""

    provider: str
    model: str


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_positive_int(value: Any, *, path: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{path} must be a positive integer")
    return parsed


def _custom_model_endpoint(value: Any) -> str | None:
    endpoint = _optional_text(value)
    if endpoint is None:
        return None
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(
            "services.llm.custom.endpoint must be an absolute HTTP(S) URL"
        )
    return endpoint


def configured_model_selection() -> DirectModelSelection:
    """Resolve the default model without silently replacing an unknown ID."""

    settings = get_settings()
    model = _optional_text(getattr(settings, "DEFAULT_MODEL_LLM_ID", None))
    if model is None:
        raise ValueError("models.default_llm_model_id is required in assembly.yaml")

    provider = _optional_text(get_plain("a:models.default_llm_provider"))
    if provider is not None:
        return DirectModelSelection(provider=provider.lower(), model=model)

    registered = MODEL_CONFIGS.get(model)
    if not registered:
        raise ValueError(
            f"model {model!r} is not in the built-in registry; set "
            "models.default_llm_provider explicitly in assembly.yaml"
        )
    return DirectModelSelection(
        provider=str(registered["provider"]),
        model=str(registered["model_name"]),
    )


def _request_from_descriptors(*, role: str) -> ConfigRequest:
    settings = get_settings()
    selected = configured_model_selection()

    raw_custom = get_plain("a:services.llm.custom", {})
    if raw_custom is None:
        raw_custom = {}
    if not isinstance(raw_custom, Mapping):
        raise ValueError("services.llm.custom must be a mapping in assembly.yaml")
    endpoint = _custom_model_endpoint(raw_custom.get("endpoint"))
    num_ctx = _optional_positive_int(
        raw_custom.get("num_ctx"),
        path="services.llm.custom.num_ctx",
    )
    if selected.provider == "custom" and endpoint is None:
        raise ValueError(
            "provider 'custom' requires services.llm.custom.endpoint in assembly.yaml"
        )

    return ConfigRequest(
        role_models={
            role: {"provider": selected.provider, "model": selected.model}
        },
        custom_model_endpoint=endpoint,
        custom_model_num_ctx=num_ctx,
        tenant=_optional_text(getattr(settings, "TENANT", None)),
        project=_optional_text(getattr(settings, "PROJECT", None)),
    )


async def build_model_service(
    *,
    role: str,
    check_only: bool,
) -> ModelServiceBase:
    request = _request_from_descriptors(role=role)
    if not check_only:
        request = await resolve_config_request_secrets(request)
    workflow = create_workflow_config(request)
    selected = workflow.ensure_role(role)
    provider = str(selected.get("provider") or "").strip()
    credential = {
        "openai": ("openai_api_key", "platform.services.openai.api_key"),
        "anthropic": ("claude_api_key", "platform.services.anthropic.api_key"),
        "google": ("google_api_key", "platform.services.google.api_key"),
        "openrouter": ("openrouter_api_key", "platform.services.openrouter.api_key"),
    }.get(provider)
    if not check_only and credential and not getattr(workflow, credential[0], None):
        raise RuntimeError(f"secret {credential[1]!r} is not set in secrets.yaml")
    return ModelServiceBase(workflow)


def embedding_service_if_configured(
    service: ModelServiceBase | None,
) -> ModelServiceBase | None:
    """Expose semantic indexing only when its selected provider is usable."""
    if service is None:
        return None
    config = getattr(service, "config", None)
    embedder = getattr(config, "embedder_config", None) or {}
    provider = str(embedder.get("provider") or "").strip().lower()
    if provider == "custom":
        return service if getattr(config, "custom_embedding_endpoint", None) else None
    credential_field = {
        "openai": "openai_api_key",
        "anthropic": "claude_api_key",
        "google": "google_api_key",
        "hugging-face": "huggingface_api_key",
        "huggingface": "huggingface_api_key",
        "openrouter": "openrouter_api_key",
    }.get(provider)
    if credential_field and str(getattr(config, credential_field, "") or "").strip():
        return service
    return None


__all__ = [
    "DirectModelSelection",
    "build_model_service",
    "configured_model_selection",
    "embedding_service_if_configured",
]
