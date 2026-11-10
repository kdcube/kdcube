# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

# infra/service_hub/model_credentials.py
"""Model provider credentials: one table and the client-config rule.

Every model credential a turn uses is resolved server-side, at the door, by
``resolve_config_request_secrets``. This module owns the facts that resolution
and its neighbours must agree on:

- ``MODEL_CREDENTIALS`` — each ``ConfigRequest`` credential field, the provider
  it serves, and its canonical platform secret id. The key an app or a user
  stores the same credential under is derived from that id in exactly one
  place (``ModelCredential.scoped_key``).
- ``strip_client_model_credentials`` — client-supplied config never carries a
  credential into a turn.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping, Optional

logger = logging.getLogger("kdcube.service_hub.model_credentials")


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
