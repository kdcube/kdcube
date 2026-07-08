# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Process-level Connection Hub public base URL.

The consent payload's ``connection_hub_url`` deep link must be openable
OUTSIDE the app origin — an external MCP agent relays it verbatim to a user
whose browser has no notion of the deployment host. The shared payload
builder therefore prefixes the deployment's public base URL, and every
surface (chat, API, MCP) agrees because they share the builder.

Source of truth: the Connection Hub bundle's
``connections.oauth.public_base_url`` — the exact key OAuth redirect
building already uses (``integrations/connections/oauth.py::callback_url``).
The value is seeded wherever the hub's effective props are read (delegated
client construction reads them for every consent resolution), one value per
deployment process.
"""

from __future__ import annotations

from typing import Any, Mapping

PUBLIC_BASE_URL_CONFIG_KEY = "connections.oauth.public_base_url"

_public_base_url: str = ""


def set_connection_hub_public_base_url(value: Any) -> None:
    """Remember the deployment's public base URL (empty clears it)."""
    global _public_base_url
    _public_base_url = str(value or "").strip().rstrip("/")


def connection_hub_public_base_url() -> str:
    return _public_base_url


def public_base_url_from_hub_props(props: Mapping[str, Any] | None) -> str:
    """Extract ``connections.oauth.public_base_url`` from hub bundle props."""
    if not isinstance(props, Mapping):
        return ""
    connections = props.get("connections")
    if not isinstance(connections, Mapping):
        return ""
    oauth = connections.get("oauth")
    if not isinstance(oauth, Mapping):
        return ""
    return str(oauth.get("public_base_url") or "").strip().rstrip("/")


__all__ = [
    "PUBLIC_BASE_URL_CONFIG_KEY",
    "connection_hub_public_base_url",
    "public_base_url_from_hub_props",
    "set_connection_hub_public_base_url",
]
