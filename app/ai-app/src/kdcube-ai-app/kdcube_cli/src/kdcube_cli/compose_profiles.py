# SPDX-License-Identifier: MIT
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional, Tuple


PROXYLOGIN_PROFILE = "proxylogin"
PROXYLOGIN_SERVICE = "proxylogin"


class ComposeProfileConfigurationError(ValueError):
    """Raised when descriptor-selected services cannot form a valid stack."""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _optional_bool(value: Any, *, field: str) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise ComposeProfileConfigurationError(f"{field} must be a boolean.")


def proxy_login_enabled(assembly: Mapping[str, Any]) -> bool:
    """Resolve the optional proxy-login process from the assembly descriptor."""

    auth = _mapping(assembly.get("auth"))
    auth_type = str(auth.get("type") or "simple").strip().lower()
    proxy_login = _mapping(auth.get("proxy_login"))
    configured = _optional_bool(
        proxy_login.get("enabled"),
        field="auth.proxy_login.enabled",
    )

    # Older delegated descriptors predate the process switch. Preserve that
    # topology while all other omitted values resolve to the disabled default.
    enabled = auth_type == "delegated" if configured is None else configured
    if auth_type == "delegated" and not enabled:
        raise ComposeProfileConfigurationError(
            "auth.type=delegated requires auth.proxy_login.enabled=true."
        )
    if auth_type != "delegated" and enabled:
        raise ComposeProfileConfigurationError(
            "auth.proxy_login.enabled=true requires auth.type=delegated."
        )
    return enabled


def compose_profile_args(assembly: Mapping[str, Any]) -> Tuple[str, ...]:
    if proxy_login_enabled(assembly):
        return ("--profile", PROXYLOGIN_PROFILE)
    return ()
