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

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.config import oauth_delegated_config
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.store import (
    GrantStore,
    GrantStoreUnavailable,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_projection import (
    authority_has_platform_privilege,
)
from kdcube_ai_app.apps.middleware.token_extract import resolve_auth_from_headers_and_cookies
from kdcube_ai_app.auth.AuthManager import ensure_platform_registered_role

AuthenticateFn = Callable[[str], Awaitable[Optional[dict]]]

def oauth_tenant_project(source: Any | None = None) -> tuple[str, str]:
    cfg = oauth_delegated_config(source)
    return cfg.tenant, cfg.project


def get_authenticate(request: Request) -> AuthenticateFn:
    fn = getattr(request.app.state, "oauth_authenticate", None)
    if fn is not None:
        return fn
    fn_both = getattr(request.app.state, "oauth_authenticate_with_both", None)
    if fn_both is not None:
        async def _authenticate_override(token: str) -> Optional[dict]:
            _auth_cfg = get_settings().AUTH
            _, id_token = resolve_auth_from_headers_and_cookies(
                request.headers.get("authorization"),
                request.headers.get(_auth_cfg.ID_TOKEN_HEADER_NAME)
                or request.headers.get(_auth_cfg.ID_TOKEN_HEADER_NAME.lower()),
                request.cookies,
            )
            return await fn_both(token, id_token)

        return _authenticate_override

    # delegated_client authenticates TWO distinct token kinds, so try each validator in
    # turn and accept the first that resolves a user. ORDER MATTERS:
    #   1) the platform bundle authority — validates the integration ACCESS token
    #      minted for /mcp by grants.mint_delegated_client_access_token (a STATEFUL
    #      bundle kst1: a real Redis session whose roles live in the user record).
    #      It is strict: it rejects the stateless web-login token (no session
    #      record), which then falls through to (2).
    #   2) the platform gateway's configured session manager (resolved from the
    #      descriptor-registered platform authenticator) — validates the admin's web-login session at
    #      /oauth/authorize (e.g. a stateless kdcube_ext kst1 carrying roles in its
    #      claims).
    # Bundle MUST come first: a custom provider's verifier may share the token
    # schema and would otherwise ACCEPT the access token but read roles from its
    # role-less claims, producing a misleading delegated-access denial.
    # A single hardcoded manager breaks one of the two endpoints.
    from kdcube_ai_app.apps.chat.ingress.resolvers import create_auth_manager
    from kdcube_ai_app.auth.bundle import BundleSessionAuthManager, get_bundle_session_authority

    tenant, project = oauth_tenant_project(request)
    # (manager, apply_registered_baseline). The bundle-session authority
    # resolves DELEGATED-CREDENTIAL bearers whose roles are EXACTLY the
    # grant's roles - a bounded token (e.g. feedback-reader) must never widen
    # to registered by transport, so NO baseline. The gateway session manager
    # resolves the INTERACTIVE platform user consenting on the authorize page;
    # they are a real authenticated platform user and get the registered
    # baseline (a federated user whose IdP group is not a kdcube role must
    # still be able to delegate registered-level grants).
    managers = [
        (
            BundleSessionAuthManager(
                authority=get_bundle_session_authority(tenant=tenant, project=project)
            ),
            False,
        ),
        (create_auth_manager(), True),
    ]

    def _user_dict(user: Any) -> dict:
        if hasattr(user, "model_dump"):
            data = user.model_dump()
        elif hasattr(user, "dict"):
            data = user.dict()
        else:
            data = {}
        sub = (
            data.get("sub")
            or getattr(user, "sub", None)
            or data.get("username")
            or getattr(user, "username", None)
        )
        out = {
            "sub": sub,
            "user_id": data.get("user_id") or sub,
            "username": data.get("username") or getattr(user, "username", None),
            "email": data.get("email") or getattr(user, "email", None),
            "name": data.get("name") or getattr(user, "name", None),
            "roles": list(data.get("roles") or getattr(user, "roles", None) or []),
            "permissions": list(data.get("permissions") or getattr(user, "permissions", None) or []),
        }
        return {key: value for key, value in out.items() if value not in (None, "", [])}

    async def _authenticate(token: str) -> Optional[dict]:
        _auth_cfg = get_settings().AUTH
        _, id_token = resolve_auth_from_headers_and_cookies(
            request.headers.get("authorization"),
            request.headers.get(_auth_cfg.ID_TOKEN_HEADER_NAME)
            or request.headers.get(_auth_cfg.ID_TOKEN_HEADER_NAME.lower()),
            request.cookies,
        )
        for manager, apply_baseline in managers:
            try:
                user = await manager.authenticate_with_both(token, id_token)
                if apply_baseline:
                    user = ensure_platform_registered_role(user)
            except Exception:
                continue
            if user is None:
                continue
            return _user_dict(user)
        return None

    return _authenticate


def get_grant_store(request: Request) -> Any:
    store = getattr(request.app.state, "oauth_grant_store", None)
    if store is not None:
        return store

    from kdcube_ai_app.infra.redis.client import get_async_redis_client

    try:
        # In proc, reuse the application-owned async pool. The factory fallback
        # keeps the adapter usable when mounted standalone in SDK tests/apps.
        redis = getattr(request.app.state, "redis_async", None)
        if redis is None:
            redis = get_async_redis_client(get_settings().REDIS_URL)
        tenant, project = oauth_tenant_project(request)
    except GrantStoreUnavailable:
        raise
    except Exception as exc:
        raise GrantStoreUnavailable("initialize") from exc
    return GrantStore(redis, tenant, project)


class AutomationAccessUnavailable(RuntimeError):
    """No delegated-access capability was bound to this request."""


def get_automation_access(request: Request) -> Any:
    """The delegated-access service the hosting app bound to this request.

    Request code consumes the capability; composing it belongs to the app that
    owns card storage and the delegated catalog. An unbound request fails
    closed rather than building a service with unknown storage.
    """
    factory = getattr(getattr(request, "state", None), "automation_access_factory", None)
    if not callable(factory):
        # A host that mounts the router instead of calling the handlers binds
        # the capability once, at application scope.
        factory = getattr(
            getattr(request.app, "state", None), "automation_access_factory", None
        )
    if not callable(factory):
        raise AutomationAccessUnavailable("automation_access_not_bound")
    return factory()


def get_access_token_minter(request: Request) -> Callable[[str, list], Awaitable[dict]]:
    """Returns ``async (sub, scopes) -> {access_token, expires_in}``."""
    fn = getattr(request.app.state, "oauth_mint_access_token", None)
    if fn is not None:
        return fn

    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.grants import mint_delegated_client_access_token

    return mint_delegated_client_access_token


def extract_bearer(request: Request) -> Optional[str]:
    """The admin's session token: Authorization bearer first, then auth cookie."""
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    cookie_name = oauth_delegated_config(request).auth_cookie_name
    return request.cookies.get(cookie_name)


def is_admin(roles) -> bool:
    return authority_has_platform_privilege(roles)
