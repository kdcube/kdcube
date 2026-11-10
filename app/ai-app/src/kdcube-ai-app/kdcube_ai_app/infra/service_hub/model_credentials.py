# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

# infra/service_hub/model_credentials.py
"""Model provider credentials: one table, three scopes, and the client-config rule.

Every model credential a turn uses is resolved server-side, at the door, by
``resolve_config_request_secrets``. This module owns the facts that resolution
and its neighbours must agree on:

- ``MODEL_CREDENTIALS`` — each ``ConfigRequest`` credential field, the provider
  it serves, and its canonical platform secret id. The key an app or a user
  stores the same credential under is derived from that id in exactly one
  place (``ModelCredential.scoped_key``), so the writer and the reader of a
  user's own key cannot drift apart.
- ``strip_client_model_credentials`` — client-supplied config never carries a
  credential into a turn.
- ``UserKeyPolicy`` — the per-app descriptor switch that lets a signed-in user's
  own provider key (stored with ``set_user_secret``) serve their turns. Off
  unless the app's ``bundles.yaml`` entry turns it on.

Precedence for each field: a value already on the server-built
``ConfigRequest`` > the caller's own key (when the app enables it) > the app
key > the platform key.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

logger = logging.getLogger("kdcube.service_hub.model_credentials")

SOURCE_REQUEST = "request"
SOURCE_USER = "user"
SOURCE_BUNDLE = "bundle"
SOURCE_PLATFORM = "platform"


@dataclass(frozen=True)
class ModelCredential:
    field: str
    """Attribute name on ``ConfigRequest`` and ``Config``."""
    provider: str
    """Provider id as ``role_models`` names it."""
    platform_key: str
    """Canonical platform secret id (``platform.services.<provider>.api_key``)."""

    @property
    def scoped_key(self) -> str:
        """Key tail inside an app's or a user's secret namespace.

        App secrets live at ``bundles.<app>.secrets.<tail>`` and a user's own
        secrets at ``users.<user>.bundles.<app>.secrets.<tail>``. Both use the
        tail without the ``platform.`` prefix: ``services.<provider>.api_key``.
        """
        return self.platform_key.removeprefix("platform.")


MODEL_CREDENTIALS: tuple[ModelCredential, ...] = (
    ModelCredential("openai_api_key", "openai", "platform.services.openai.api_key"),
    ModelCredential("claude_api_key", "anthropic", "platform.services.anthropic.api_key"),
    ModelCredential("google_api_key", "google", "platform.services.google.api_key"),
    ModelCredential("huggingface_api_key", "huggingface", "platform.services.huggingface.api_key"),
    ModelCredential("openrouter_api_key", "openrouter", "platform.services.openrouter.api_key"),
    # provider "custom" (locally served models / models gateway)
    ModelCredential("custom_model_api_key", "custom", "platform.services.llm.custom.api_key"),
)

_PROVIDER_ALIASES = {
    "gemini": "google",
    "hugging-face": "huggingface",
    "hugging_face": "huggingface",
}

_BY_FIELD = {credential.field: credential for credential in MODEL_CREDENTIALS}
_BY_PROVIDER = {credential.provider: credential for credential in MODEL_CREDENTIALS}

CLIENT_FORBIDDEN_CONFIG_KEYS: frozenset[str] = frozenset(_BY_FIELD)
"""Config keys a client may never set: every model credential field."""


def normalize_provider(provider: Any) -> str:
    name = str(provider or "").strip().lower()
    return _PROVIDER_ALIASES.get(name, name)


def credential_for_provider(provider: Any) -> Optional[ModelCredential]:
    return _BY_PROVIDER.get(normalize_provider(provider))


def credential_for_field(field_name: str) -> Optional[ModelCredential]:
    return _BY_FIELD.get(str(field_name or ""))


def user_model_credential_key(provider: str) -> str:
    """The key an app stores a user's own provider key under.

    ``await set_user_secret(user_model_credential_key("anthropic"), value)``
    writes exactly the key the user rung of ``resolve_config_request_secrets``
    reads (``services.anthropic.api_key``).
    """
    credential = credential_for_provider(provider)
    if credential is None:
        raise ValueError(f"Unknown model provider: {provider!r}")
    return credential.scoped_key


# ---------------------------------------------------------------------------
# Client-supplied config
# ---------------------------------------------------------------------------
def strip_client_model_credentials(
    values: Optional[Mapping[str, Any]],
    *,
    door: str,
    subject: Optional[str] = None,
) -> dict[str, Any]:
    """Return a copy of client-supplied config without any model credential.

    A credential on a ``ConfigRequest`` outranks every vault lookup, so a
    client able to set one would choose which key — and whose account — a turn
    runs on. A dropped field is logged by name with the door and subject; the
    value is never logged.
    """
    kept: dict[str, Any] = {}
    dropped: list[str] = []
    for key, value in (values or {}).items():
        if str(key).strip().lower() in CLIENT_FORBIDDEN_CONFIG_KEYS:
            dropped.append(str(key))
            continue
        kept[key] = value
    if dropped:
        logger.warning(
            "Dropped client-supplied model credential field(s) %s door=%s subject=%s",
            sorted(dropped), door, subject,
        )
    return kept


# ---------------------------------------------------------------------------
# Per-app policy: may a user's own key serve their turns?
# ---------------------------------------------------------------------------
USER_KEYS_PROP_PATH = ("model_credentials", "user_keys")


@dataclass(frozen=True)
class UserKeyPolicy:
    """``config.model_credentials.user_keys`` of one app's ``bundles.yaml`` entry."""

    enabled: bool = False
    providers: frozenset[str] = field(default_factory=frozenset)

    def allows(self, credential: ModelCredential) -> bool:
        if not self.enabled:
            return False
        return not self.providers or credential.provider in self.providers


USER_KEYS_DISABLED = UserKeyPolicy()


def user_key_policy_from_props(props: Optional[Mapping[str, Any]]) -> UserKeyPolicy:
    node: Any = props or {}
    for part in USER_KEYS_PROP_PATH:
        node = node.get(part) if isinstance(node, Mapping) else None
    if not isinstance(node, Mapping) or node.get("enabled") is not True:
        return USER_KEYS_DISABLED
    raw_providers = node.get("providers")
    if isinstance(raw_providers, str):
        raw_providers = [raw_providers]
    providers = frozenset(
        normalize_provider(item) for item in (raw_providers or []) if normalize_provider(item)
    )
    return UserKeyPolicy(enabled=True, providers=providers)


def signed_in_user_id(user: Any) -> Optional[str]:
    """``user.user_id`` of a request user, or None for an anonymous or absent one.

    An anonymous caller has a fingerprint, not an account; a vault namespace
    keyed by a fingerprint is not that caller's, so there is no fallback.
    """
    if user is None:
        return None
    if str(getattr(user, "user_type", "") or "").strip().lower() == "anonymous":
        return None
    return str(getattr(user, "user_id", None) or "").strip() or None


def current_signed_in_user_id() -> Optional[str]:
    """The signed-in user of the request bound to this execution context."""
    try:
        from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import get_current_request_context

        ctx = get_current_request_context()
    except Exception:
        return None
    return signed_in_user_id(getattr(ctx, "user", None))


async def load_user_key_policy(
    bundle_id: Optional[str],
    *,
    tenant: Optional[str] = None,
    project: Optional[str] = None,
) -> UserKeyPolicy:
    """Read the app's user-key policy; any failure reads as disabled."""
    bundle_id = str(bundle_id or "").strip()
    if not bundle_id:
        return USER_KEYS_DISABLED
    try:
        from kdcube_ai_app.apps.chat.sdk.config import get_settings
        from kdcube_ai_app.infra.plugin.bundle_store import get_bundle_props
        from kdcube_ai_app.infra.redis.client import get_async_redis_client

        settings = get_settings()
        tenant = str(tenant or getattr(settings, "TENANT", "") or "").strip()
        project = str(project or getattr(settings, "PROJECT", "") or "").strip()
        if not tenant or not project:
            return USER_KEYS_DISABLED
        props = await get_bundle_props(
            get_async_redis_client(settings.REDIS_URL),
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
        )
    except Exception:
        logger.warning("User model key policy unreadable for app %s; user keys stay off", bundle_id, exc_info=True)
        return USER_KEYS_DISABLED
    return user_key_policy_from_props(props)


# ---------------------------------------------------------------------------
# Config-level facts used after resolution
# ---------------------------------------------------------------------------
def credential_sources(config: Any) -> dict[str, str]:
    """``{field: source}`` recorded by ``resolve_config_request_secrets``."""
    sources = getattr(config, "credential_sources", None)
    return dict(sources) if isinstance(sources, Mapping) else {}


def without_user_credentials(config: Any) -> Any:
    """A shallow copy of ``config`` holding only shared (app/platform) credentials.

    A process-wide instance outlives the turn that built it and serves every
    user, so it must never be constructed with one user's own key.
    """
    sources = credential_sources(config)
    user_fields = [name for name, source in sources.items() if source == SOURCE_USER]
    if not user_fields:
        return config
    shared = getattr(config, "shared_credentials", None) or {}
    clone = copy.copy(config)
    new_sources = dict(sources)
    for name in user_fields:
        value, source = shared.get(name, ("", None))
        setattr(clone, name, value or "")
        if source:
            new_sources[name] = source
        else:
            new_sources.pop(name, None)
    clone.credential_sources = new_sources
    clone.shared_credentials = {}
    return clone
