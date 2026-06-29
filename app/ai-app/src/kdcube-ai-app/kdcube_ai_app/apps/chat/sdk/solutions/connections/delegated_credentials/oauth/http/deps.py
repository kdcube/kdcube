# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
Per-request dependency resolution for the OAuth2 AS / MCP routes.

Each dependency prefers an override on ``app.state`` (set by tests) and otherwise
builds the real platform-backed implementation lazily:

- session authentication via :class:`BundleSessionAuthManager` (validates the
  opaque ``kst1`` token against the Redis user record);
- the :class:`GrantStore` on the platform Redis client.

The OAuth tenant/project (which session namespace the consenting admin belongs
to) and the auth cookie name are resolved from platform descriptors.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from fastapi import Request

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.config import oauth_delegated_config
from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_projection import (
    authority_has_platform_privilege,
)

AuthenticateFn = Callable[[str], Awaitable[Optional[dict]]]

def oauth_tenant_project(source: Any | None = None) -> tuple[str, str]:
    cfg = oauth_delegated_config(source)
    return cfg.tenant, cfg.project


def get_authenticate(request: Request) -> AuthenticateFn:
    fn = getattr(request.app.state, "oauth_authenticate", None)
    if fn is not None:
        return fn

    # delegated_client authenticates TWO distinct token kinds, so try each validator in
    # turn and accept the first that resolves a user. ORDER MATTERS:
    #   1) the platform bundle authority — validates the integration ACCESS token
    #      minted for /mcp by grants.mint_feedback_reader_access_token (a STATEFUL
    #      bundle kst1: a real Redis session whose roles live in the user record).
    #      It is strict: it rejects the stateless web-login token (no session
    #      record), which then falls through to (2).
    #   2) the platform gateway's configured session manager (resolved from the
    #      descriptor-registered platform authenticator) — validates the admin's web-login session at
    #      /oauth/authorize (e.g. a stateless kdcube_ext kst1 carrying roles in its
    #      claims).
    # Bundle MUST come first: a custom provider's verifier may share the token
    # schema and would otherwise ACCEPT the access token but read roles from its
    # (role-less) claims -> empty roles -> 403 "feedback-reader role required".
    # A single hardcoded manager breaks one of the two endpoints.
    from kdcube_ai_app.apps.chat.ingress.resolvers import create_auth_manager
    from kdcube_ai_app.auth.bundle import BundleSessionAuthManager, get_bundle_session_authority

    tenant, project = oauth_tenant_project(request)
    managers = [
        BundleSessionAuthManager(
            authority=get_bundle_session_authority(tenant=tenant, project=project)
        ),
        create_auth_manager(),
    ]

    async def _authenticate(token: str) -> Optional[dict]:
        for manager in managers:
            try:
                user = await manager.authenticate(token)
            except Exception:
                continue
            if user is None:
                continue
            sub = getattr(user, "sub", None) or getattr(user, "username", None)
            return {"sub": sub, "roles": list(getattr(user, "roles", None) or [])}
        return None

    return _authenticate


def get_grant_store(request: Request) -> Any:
    store = getattr(request.app.state, "oauth_grant_store", None)
    if store is not None:
        return store

    from kdcube_ai_app.apps.chat.sdk.config import get_settings
    from kdcube_ai_app.infra.redis.client import get_async_redis_client

    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.store import GrantStore

    tenant, project = oauth_tenant_project(request)
    redis = get_async_redis_client(get_settings().REDIS_URL)
    return GrantStore(redis, tenant, project)


def get_access_token_minter(request: Request) -> Callable[[str, list], Awaitable[dict]]:
    """Returns ``async (sub, scopes) -> {access_token, expires_in}``.

    Production mints a short-lived read-only integration session (the
    ``feedback-reader`` grant); see :mod:`grants`.
    """
    fn = getattr(request.app.state, "oauth_mint_access_token", None)
    if fn is not None:
        return fn

    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.grants import mint_feedback_reader_access_token

    return mint_feedback_reader_access_token


def extract_bearer(request: Request) -> Optional[str]:
    """The admin's session token: Authorization bearer first, then auth cookie."""
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    cookie_name = oauth_delegated_config(request).auth_cookie_name
    return request.cookies.get(cookie_name)


def is_admin(roles) -> bool:
    return authority_has_platform_privilege(roles)
