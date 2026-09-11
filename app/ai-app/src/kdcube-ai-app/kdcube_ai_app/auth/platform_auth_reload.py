# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Authentication consumer for live platform-settings updates.

Provider details may be rebuilt in place. The selected provider and sign-in
lane are pinned for the life of the process because changing either under
active browser sessions can lock users out; a refresh starts a new process
with that selection.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from kdcube_ai_app.apps.chat.sdk.config_scopes import (
    _load_assembly_plain,
    _load_bundles_plain,
)
from kdcube_ai_app.infra.platform_settings.updates import PlatformSettingsUpdate

logger = logging.getLogger(__name__)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _provider_family(provider_type: str) -> str:
    value = _text(provider_type).lower().replace("-", "_")
    if value in {"bundle_session_login", "bundle_session", "session"}:
        raise ValueError(
            f"authority provider type '{provider_type}' was removed; use 'bundle'"
        )
    if value in {"cognito", "multi_cognito", "cognito_multi", "cognito_id_token"}:
        return "cognito"
    if value == "bundle":
        return "bundle"
    if value in {"simple", "simple_idp"}:
        return "simple"
    return value


@dataclass(frozen=True)
class PlatformAuthSelection:
    auth_type: str
    bundle_id: str
    authority_id: str
    provider_id: str
    provider_family: str

    def to_dict(self) -> dict[str, str]:
        return {
            "auth_type": self.auth_type,
            "bundle_id": self.bundle_id,
            "authority_id": self.authority_id,
            "provider_id": self.provider_id,
            "provider_family": self.provider_family,
        }


def read_platform_auth_selection() -> PlatformAuthSelection:
    ref = _load_assembly_plain("auth.connection_hub")
    ref = dict(ref) if isinstance(ref, Mapping) else {}
    bundle_id = _text(ref.get("bundle_id") or ref.get("bundleId") or "connection-hub@1-0")
    authority_id = _text(ref.get("authority_id") or ref.get("authorityId") or "kdcube.platform")
    provider_id = _text(ref.get("provider_id") or ref.get("providerId") or "cognito")
    provider = _load_bundles_plain(
        f"{bundle_id}.authority_registry.authorities.{authority_id}.providers.{provider_id}"
    )
    provider = dict(provider) if isinstance(provider, Mapping) else {}
    return PlatformAuthSelection(
        auth_type=_text(_load_assembly_plain("auth.type")).lower(),
        bundle_id=bundle_id,
        authority_id=authority_id,
        provider_id=provider_id,
        provider_family=_provider_family(_text(provider.get("type"))),
    )


def build_platform_auth_manager(*, service_label: str) -> Any:
    """Build from a new descriptor snapshot without changing the process-wide
    settings cache used by unrelated platform sections."""
    from kdcube_ai_app.apps.chat.sdk.config import Settings
    from kdcube_ai_app.apps.middleware.platform_auth import create_platform_auth_manager

    return create_platform_auth_manager(
        settings=Settings(),
        service_label=service_label,
    )


@dataclass(frozen=True)
class PlatformAuthReloadResult:
    status: str
    reason: str
    old_descriptor: Mapping[str, str]
    new_descriptor: Mapping[str, str]
    selection: PlatformAuthSelection

    @property
    def reloaded(self) -> bool:
        return self.status == "reloaded"


def _manager_descriptor(manager: Any) -> dict[str, str]:
    return {
        "authenticator_id": _text(getattr(manager, "authenticator_id", "")),
        "authority_id": _text(getattr(manager, "authority_id", "")),
        "provider": _text(getattr(manager, "authenticator_provider", "")),
        "manager": type(manager).__name__,
    }


class PlatformAuthManagerHolder:
    """Stable reference whose current auth manager can be atomically replaced.

    In-flight requests keep the manager they already resolved. A later request
    resolves the new manager through this holder.
    """

    def __init__(
        self,
        factory: Callable[[], Any],
        *,
        selection_reader: Callable[[], PlatformAuthSelection] = read_platform_auth_selection,
        service_label: str = "platform",
    ) -> None:
        self._factory = factory
        self._selection_reader = selection_reader
        self._service_label = service_label
        self._lock = threading.RLock()
        self._selection = selection_reader()
        self._current = factory()

    @property
    def current(self) -> Any:
        with self._lock:
            return self._current

    @property
    def selection(self) -> PlatformAuthSelection:
        with self._lock:
            return self._selection

    @property
    def runtime_auth_config(self) -> Any:
        return getattr(self.current, "runtime_auth_config", None)

    def __getattr__(self, name: str) -> Any:
        # Bound methods already obtained by an in-flight request remain valid
        # while a later request sees the replacement.
        return getattr(self.current, name)

    def refresh_required(self, reason: str) -> PlatformAuthReloadResult:
        with self._lock:
            descriptor = _manager_descriptor(self._current)
            selection = self._selection_reader()
        logger.info(
            "Platform auth selection changed; runtime refresh required: service=%s reason=%s current=%s requested=%s",
            self._service_label,
            reason,
            self._selection.to_dict(),
            selection.to_dict(),
        )
        return PlatformAuthReloadResult(
            status="refresh_required",
            reason=reason,
            old_descriptor=descriptor,
            new_descriptor=descriptor,
            selection=selection,
        )

    def rebuild(self, reason: str) -> PlatformAuthReloadResult:
        with self._lock:
            selected = self._selection_reader()
            if selected != self._selection:
                return self.refresh_required(reason)
            old_descriptor = _manager_descriptor(self._current)
            candidate = self._factory()
            selected_after_build = self._selection_reader()
            if selected_after_build != self._selection:
                logger.warning(
                    "Platform auth selection moved during rebuild; keeping current manager: service=%s reason=%s",
                    self._service_label,
                    reason,
                )
                return PlatformAuthReloadResult(
                    status="refresh_required",
                    reason=reason,
                    old_descriptor=old_descriptor,
                    new_descriptor=old_descriptor,
                    selection=selected_after_build,
                )
            self._current = candidate
            new_descriptor = _manager_descriptor(candidate)
        logger.info(
            "Platform auth manager rebuilt: service=%s reason=%s old=%s new=%s",
            self._service_label,
            reason,
            old_descriptor,
            new_descriptor,
        )
        return PlatformAuthReloadResult(
            status="reloaded",
            reason=reason,
            old_descriptor=old_descriptor,
            new_descriptor=new_descriptor,
            selection=selected,
        )


class PlatformAuthSettingsHandler:
    """Apply the `auth` section of the generic platform-settings stream."""

    def __init__(self, holder: PlatformAuthManagerHolder) -> None:
        self.holder = holder
        self.last_result: PlatformAuthReloadResult | None = None

    async def __call__(self, event: PlatformSettingsUpdate) -> None:
        if event.section != "auth":
            return
        reason = event.reason or f"platform-settings:{event.scope}:{event.event_id}"
        if event.scope == "lane":
            self.last_result = self.holder.refresh_required(reason)
            return
        if event.scope in {"providers", "catch_up"}:
            self.last_result = self.holder.rebuild(reason)
            return
        logger.info(
            "Platform auth ignored unsupported settings scope: scope=%s event_id=%s",
            event.scope,
            event.event_id,
        )


__all__ = [
    "PlatformAuthManagerHolder",
    "PlatformAuthReloadResult",
    "PlatformAuthSelection",
    "PlatformAuthSettingsHandler",
    "build_platform_auth_manager",
    "read_platform_auth_selection",
]
