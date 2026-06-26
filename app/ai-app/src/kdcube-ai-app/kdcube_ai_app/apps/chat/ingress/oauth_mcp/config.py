# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Descriptor-backed configuration for OAuth/MCP integration access."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Tuple

DEFAULT_CLAUDE_REDIRECT_URIS: tuple[str, ...] = (
    "http://localhost/callback",
    "http://127.0.0.1/callback",
    "https://claude.ai/api/mcp/auth_callback",
)

DEFAULT_DCR_REDIRECT_URIS: tuple[str, ...] = (
    "https://claude.ai/api/mcp/auth_callback",
    "http://localhost/callback",
    "http://127.0.0.1/callback",
)


@dataclass(frozen=True)
class OAuthMcpPublicClientConfig:
    client_id: str
    redirect_uris: tuple[str, ...]
    token_endpoint_auth_method: str = "none"


@dataclass(frozen=True)
class OAuthMcpDynamicClientRegistrationConfig:
    allowed_redirect_uris: tuple[str, ...]


@dataclass(frozen=True)
class OAuthMcpConfig:
    enabled: bool
    issuer: str | None
    tenant: str
    project: str
    auth_cookie_name: str
    public_clients: tuple[OAuthMcpPublicClientConfig, ...]
    dynamic_client_registration: OAuthMcpDynamicClientRegistrationConfig


def _state_for(source: Any) -> Any | None:
    if source is None:
        return None
    app = getattr(source, "app", None) or source
    return getattr(app, "state", None)


def _coerce_str(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _coerce_string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        item = value.strip()
        return (item,) if item else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _default_public_clients() -> tuple[OAuthMcpPublicClientConfig, ...]:
    return (
        OAuthMcpPublicClientConfig(
            client_id="claude",
            redirect_uris=DEFAULT_CLAUDE_REDIRECT_URIS,
        ),
    )


def _parse_public_clients(raw: Any) -> tuple[OAuthMcpPublicClientConfig, ...]:
    if raw is None:
        return _default_public_clients()
    if not isinstance(raw, (list, tuple)):
        return ()
    clients: list[OAuthMcpPublicClientConfig] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        client_id = _coerce_str(item.get("client_id"))
        redirects = _coerce_string_tuple(item.get("redirect_uris"))
        if not client_id or not redirects:
            continue
        clients.append(
            OAuthMcpPublicClientConfig(
                client_id=client_id,
                redirect_uris=redirects,
                token_endpoint_auth_method=_coerce_str(item.get("token_endpoint_auth_method")) or "none",
            )
        )
    return tuple(clients)


def _parse_config(raw: Any, *, settings: Any | None = None) -> OAuthMcpConfig:
    node = raw if isinstance(raw, Mapping) else {}
    dcr = node.get("dynamic_client_registration")
    dcr_node = dcr if isinstance(dcr, Mapping) else {}
    allowed_redirects = _coerce_string_tuple(dcr_node.get("allowed_redirect_uris")) or DEFAULT_DCR_REDIRECT_URIS

    tenant = _coerce_str(getattr(settings, "TENANT", None)) or "home"
    project = _coerce_str(getattr(settings, "PROJECT", None)) or "demo"
    auth = getattr(settings, "AUTH", None)
    cookie_name = _coerce_str(getattr(auth, "AUTH_TOKEN_COOKIE_NAME", None)) or "__Secure-LATC"

    return OAuthMcpConfig(
        enabled=_coerce_bool(node.get("enabled"), False),
        issuer=_coerce_str(node.get("issuer")),
        tenant=tenant,
        project=project,
        auth_cookie_name=cookie_name,
        public_clients=_parse_public_clients(node.get("public_clients")),
        dynamic_client_registration=OAuthMcpDynamicClientRegistrationConfig(
            allowed_redirect_uris=allowed_redirects,
        ),
    )


def oauth_mcp_config(source: Any | None = None) -> OAuthMcpConfig:
    """Resolve OAuth/MCP config from app state override or assembly.yaml.

    Tests may set ``app.state.oauth_mcp_config`` to a mapping or ``OAuthMcpConfig``.
    Production reads ``auth.oauth_mcp`` from ``assembly.yaml``;
    tenant/project and cookie name come from the canonical Settings object, which
    already resolves ``context.*`` and ``auth.*`` from descriptors.
    """
    state = _state_for(source)
    override = getattr(state, "oauth_mcp_config", None) if state is not None else None
    if isinstance(override, OAuthMcpConfig):
        return override

    if override is not None:
        return _parse_config(override)

    from kdcube_ai_app.apps.chat.sdk.config import get_settings, read_plain

    settings = get_settings()
    raw = read_plain("auth.oauth_mcp", default={})
    return _parse_config(raw, settings=settings)
