# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Descriptor-backed platform authority authenticator construction."""

from __future__ import annotations

import logging
import os
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.config import get_settings

logger = logging.getLogger(__name__)


def normalize_platform_auth_provider(value: str) -> str:
    provider = str(value or "").strip().lower()
    aliases = {
        "bundle": "session",
        "bundle-session": "session",
        "cognito-multi": "multi-cognito",
        "delegated": "cognito",
    }
    return aliases.get(provider, provider or "simple")


def platform_authenticator_descriptor(settings: Any | None = None) -> dict[str, Any]:
    """Normalize the descriptor for the platform role-providing authority.

    The canonical descriptor selects the provider through
    ``auth.connection_hub``. This function derives the concrete runtime
    authenticator from settings populated from that selected provider.
    """

    settings = settings or get_settings()
    raw = settings.plain("auth.authenticators.platform", default=None)

    if isinstance(raw, list):
        raw = next((row for row in raw if isinstance(row, Mapping) and row.get("enabled") is not False), None)
    elif isinstance(raw, Mapping) and isinstance(raw.get("items"), list):
        raw = next(
            (
                row
                for row in raw.get("items") or []
                if isinstance(row, Mapping) and row.get("enabled") is not False
            ),
            None,
        )

    if isinstance(raw, Mapping):
        provider = str(
            raw.get("provider")
            or raw.get("kind")
            or raw.get("type")
            or raw.get("idp")
            or ""
        ).strip().lower()
        if provider:
            provider = normalize_platform_auth_provider(provider)
            return {
                "authenticator_id": str(raw.get("authenticator_id") or raw.get("id") or f"kdcube.{provider}").strip(),
                "authority_id": str(raw.get("authority_id") or "kdcube.platform").strip(),
                "provider": provider,
                "source": "auth.authenticators.platform",
            }

    descriptor_provider = str(getattr(settings, "AUTH_PROVIDER", "") or "").strip().lower()
    if not descriptor_provider:
        descriptor_provider = str(settings.plain("auth.idp", default="") or "").strip().lower()
    if not descriptor_provider:
        descriptor_provider = str(settings.plain("auth.type", default="") or "").strip().lower()
    if not descriptor_provider:
        descriptor_provider = "simple"
    provider = normalize_platform_auth_provider(descriptor_provider)
    trusted_providers = list(getattr(settings.AUTH, "COGNITO_TRUSTED_PROVIDERS", None) or [])
    if provider == "cognito" and len(trusted_providers) > 1:
        provider = "multi-cognito"
    return {
        "authenticator_id": f"kdcube.{provider}",
        "authority_id": "kdcube.platform",
        "provider": provider,
        "source": "auth.idp",
    }


def with_authenticator_metadata(manager: Any, descriptor: Mapping[str, Any]) -> Any:
    setattr(manager, "authenticator_id", str(descriptor.get("authenticator_id") or "kdcube.platform.token"))
    setattr(manager, "authority_id", str(descriptor.get("authority_id") or "kdcube.platform"))
    setattr(manager, "authenticator_provider", str(descriptor.get("provider") or ""))
    return manager


def create_platform_auth_manager(
    *,
    settings: Any | None = None,
    service_label: str = "platform",
    send_validation_error_details: bool = True,
):
    """Create the role-providing platform authenticator from descriptors."""

    settings = settings or get_settings()
    descriptor = platform_authenticator_descriptor(settings)
    provider = str(descriptor.get("provider") or "simple").strip().lower()
    logger.info(
        "Using %s platform authenticator descriptor id=%s provider=%s source=%s authority_id=%s",
        service_label,
        descriptor.get("authenticator_id") or "",
        provider,
        descriptor.get("source") or "",
        descriptor.get("authority_id") or "kdcube.platform",
    )

    if provider in {"multi-cognito", "cognito-multi"}:
        from kdcube_ai_app.auth.implementations.multi_cognito import MultiCognitoAuthManager

        providers = list(settings.AUTH.COGNITO_TRUSTED_PROVIDERS or [])
        logger.info("Using MultiCognitoAuthManager for %s platform authentication providers=%s", service_label, len(providers))
        return with_authenticator_metadata(
            MultiCognitoAuthManager(providers, send_validation_error_details=send_validation_error_details),
            descriptor,
        )

    if provider == "cognito":
        from kdcube_ai_app.auth.implementations.cognito import CognitoAuthManager

        logger.info("Using CognitoAuthManager for %s platform authentication", service_label)
        return with_authenticator_metadata(
            CognitoAuthManager(send_validation_error_details=send_validation_error_details),
            descriptor,
        )

    if provider in {"session", "bundle", "bundle-session"}:
        from kdcube_ai_app.auth.bundle import BundleSessionAuthManager

        logger.info("Using BundleSessionAuthManager for %s platform authentication", service_label)
        return with_authenticator_metadata(
            BundleSessionAuthManager(send_validation_error_details=send_validation_error_details),
            descriptor,
        )

    if provider == "oauth":
        logger.warning(
            "OAuth platform authenticator is declared for %s but no descriptor-backed OAuth manager is active; falling back to SimpleIDP",
            service_label,
        )

    from kdcube_ai_app.apps.middleware.simple_idp import SimpleIDP

    logger.info("Using SimpleIDP for %s platform authentication", service_label)
    return with_authenticator_metadata(
        SimpleIDP(send_validation_error_details=send_validation_error_details, service_user_token=os.getenv("SERVICE_USER_TOKEN")),
        descriptor,
    )


def platform_authenticator_provider(settings: Any | None = None) -> str:
    return str(platform_authenticator_descriptor(settings).get("provider") or "simple").strip().lower()


def is_simple_platform_auth(settings: Any | None = None) -> bool:
    return platform_authenticator_provider(settings) == "simple"


__all__ = [
    "create_platform_auth_manager",
    "is_simple_platform_auth",
    "normalize_platform_auth_provider",
    "platform_authenticator_descriptor",
    "platform_authenticator_provider",
    "with_authenticator_metadata",
]
