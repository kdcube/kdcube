# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""KDCube's adapter for the server-side login lane.

The session itself (one HttpOnly cookie, a one-time browser-bound login
attempt, sliding renewal, and the OIDC code flow) is
``connection_hub.server_side_login``, host-neutral. This module is the host:

- ``PlatformSessionBackend``: the package's ``SessionBackend`` over the
  platform session authority (``BundleSessionAuthority``): the Redis session
  registry, the platform user record, the ``kst1`` token, roles and
  permissions resolved through the Connection Hub authority registry's
  grants (``resolve_platform_grants``, the same rule the Google reference
  login applies).
- ``RedisLoginAttemptStore``: one-time login attempts in the same Redis
  namespace as the sessions.
- ``platform_login_flow``: the flow assembled from the deployment's
  descriptors. The selected platform provider (``auth.connection_hub``) must
  be a server-side lane (``bundle``) whose ``input`` authenticator is a
  Cognito or OIDC provider; that authenticator's
  issuer, client id, secret reference and hosted UI come from the registry,
  never from the browser.

Nothing here is reachable from the browser but through the ingress router
(``apps/chat/ingress/platform_session.py``); the router is the only place
that knows FastAPI.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from connection_hub.server_side_login.cookies import StandardCookiePolicy
from connection_hub.server_side_login.flow import BrowserSessionFlow
from connection_hub.server_side_login.model import (
    IssuedSession,
    LoginAttempt,
    SessionPolicy,
    SessionState,
    VerifiedIdentity,
)
from connection_hub.server_side_login.oidc import OidcClientConfig, OidcCodeFlow, OidcEndpoints
from connection_hub.server_side_login.protocols import UpstreamIdentity

from kdcube_ai_app.auth.bundle.sessions import (
    BundleSessionAuthority,
    BundleSessionError,
    get_bundle_session_authority,
)

logger = logging.getLogger(__name__)

LOGIN_ROUTE = "/api/platform/session/login"
CALLBACK_ROUTE = "/api/platform/session/callback"
# The one post-logout URL an identity provider needs per origin: the browser
# comes back here after the upstream sign-out and continues to the
# destination the logout carried in the return cookie.
SIGNED_OUT_ROUTE = "/api/platform/session/signed-out"
RETURN_COOKIE_TTL_SECONDS = 300
LOGOUT_ROUTE = "/api/platform/logout"
PROFILE_ROUTE = "/profile"

BUNDLE_LOGIN_TYPES = {"bundle"}
OIDC_AUTHENTICATOR_TYPES = {"cognito", "multi_cognito", "multi-cognito", "cognito_id_token", "oidc"}

GrantsResolver = Callable[[VerifiedIdentity], tuple[list[str], list[str], str]]


def _str(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


# ---- the backend over the platform session authority -------------------------

class PlatformSessionBackend:
    """``SessionBackend`` over ``BundleSessionAuthority``.

    ``grants`` decides the roles and permissions a verified identity gets on
    the platform user record; the flow never sees that decision.
    """

    def __init__(
        self,
        authority: BundleSessionAuthority,
        *,
        grants: GrantsResolver,
        policy: SessionPolicy,
        issued_by: str = "kdcube.platform.session",
    ) -> None:
        self._authority = authority
        self._grants = grants
        self._policy = policy
        self._issued_by = issued_by

    async def login_or_register(
        self,
        identity: VerifiedIdentity,
        *,
        expires_at: int,
        metadata: Mapping[str, Any] | None = None,
    ) -> IssuedSession:
        now = int(time.time())
        sub = identity.canonical_subject
        email = identity.email.lower()
        username = email or f"{identity.provider}_{identity.subject}"
        roles, permissions, binding_source = self._grants(identity)
        grant = await self._authority.login_or_register(
            sub=sub,
            username=username,
            email=email or None,
            name=identity.name or email or username,
            roles=roles,
            permissions=permissions,
            provider=identity.provider,
            provider_subject=identity.subject,
            metadata={
                "source": self._issued_by,
                "role_binding_source": binding_source,
                "email_verified": bool(identity.email_verified),
                **{k: v for k, v in dict(metadata or {}).items() if isinstance(v, (str, int, float, bool))},
            },
            ttl_seconds=self._policy.max_ttl_seconds,
            idle_ttl_seconds=max(1, int(expires_at) - now),
        )
        claims = dict(grant.claims or {})
        return IssuedSession(
            token=grant.token,
            session_id=grant.session_id,
            subject=sub,
            issued_at=int(claims.get("iat") or now),
            expires_at=int(grant.expires_at),
            user=grant.user.to_public_dict(),
        )

    @staticmethod
    def _state(record: Mapping[str, Any], user: Mapping[str, Any]) -> SessionState:
        issued_at = int(record.get("iat") or 0)
        return SessionState(
            session_id=_str(record.get("session_id")),
            subject=_str(record.get("sub")),
            issued_at=issued_at,
            expires_at=int(record.get("exp") or 0),
            last_seen_at=int(record.get("last_seen") or issued_at),
            provider=_str(record.get("provider")),
            provider_subject=_str(record.get("provider_subject")),
            user=dict(user),
        )

    async def validate(self, token: str, *, now: int) -> SessionState | None:
        try:
            verification = await self._authority.validate_token(token, now=now)
        except BundleSessionError:
            return None
        return self._state(verification.record, verification.user.to_public_dict())

    async def touch(self, session_id: str, *, expires_at: int, now: int) -> SessionState | None:
        record = await self._authority.touch(session_id, expires_at=expires_at, now=now)
        if record is None:
            return None
        user = await self._authority.get_user(_str(record.get("sub")))
        return self._state(record, user.to_public_dict() if user is not None else {})

    async def logout(self, token: str) -> bool:
        try:
            return bool(await self._authority.logout(token=token))
        except BundleSessionError:
            return False


# ---- one-time login attempts in Redis ----------------------------------------

class RedisLoginAttemptStore:
    """``LoginAttemptStore`` beside the session records, keyed by state, with
    the attempt's own expiry as the key TTL. ``take`` is get-and-delete."""

    def __init__(self, authority: BundleSessionAuthority) -> None:
        self._authority = authority

    def _key(self, state: str) -> str:
        return self._authority._ns(f"kdcube:auth:browser-login:{state}")

    async def put(self, attempt: LoginAttempt) -> None:
        redis = await self._authority._redis_client()
        ttl = max(1, int(attempt.expires_at) - int(time.time()))
        payload = {
            "state": attempt.state,
            "binding": attempt.binding,
            "nonce": attempt.nonce,
            "code_verifier": attempt.code_verifier,
            "next_path": attempt.next_path,
            "created_at": attempt.created_at,
            "expires_at": attempt.expires_at,
            "upstream": attempt.upstream,
            "metadata": dict(attempt.metadata or {}),
        }
        await redis.setex(self._key(attempt.state), ttl, json.dumps(payload, separators=(",", ":")))

    async def take(self, state: str) -> LoginAttempt | None:
        key = self._key(_str(state))
        redis = await self._authority._redis_client()
        raw = None
        getdel = getattr(redis, "getdel", None)
        if callable(getdel):
            raw = await getdel(key)
        else:
            raw = await redis.get(key)
            if raw is not None:
                await redis.delete(key)
        if not raw:
            return None
        try:
            data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        except Exception:  # noqa: BLE001 - a corrupt attempt is an absent attempt
            return None
        if int(data.get("expires_at") or 0) <= int(time.time()):
            return None
        return LoginAttempt(
            state=_str(data.get("state")),
            binding=_str(data.get("binding")),
            nonce=_str(data.get("nonce")),
            code_verifier=_str(data.get("code_verifier")),
            next_path=_str(data.get("next_path")) or "/",
            created_at=int(data.get("created_at") or 0),
            expires_at=int(data.get("expires_at") or 0),
            upstream=_str(data.get("upstream")),
            metadata=_dict(data.get("metadata")),
        )


# ---- configuration from the deployment's descriptors --------------------------

@dataclass(frozen=True)
class BundleLoginConfig:
    """What the server-side login lane needs, resolved from the Connection Hub
    authority registry through the platform's selected provider."""

    provider_id: str
    authority_id: str
    provider: dict[str, Any]
    authority: dict[str, Any]
    authenticator_type: str
    issuer_url: str
    client_id: str
    client_secret_ref: str
    hosted_ui_domain: str
    scopes: tuple[str, ...]
    redirect_uri: str
    groups_claim: str
    policy: SessionPolicy
    cookie_secure: bool
    cookie_same_site: str
    cookie_domain: str
    session_cookie_name: str

    @property
    def authenticator_kind(self) -> str:
        return "cognito" if "cognito" in self.authenticator_type else "oidc"


def _cognito_issuer(authenticator: Mapping[str, Any]) -> str:
    explicit = _str(authenticator.get("issuer"))
    if explicit:
        return explicit
    region = _str(authenticator.get("region"))
    pool = _str(authenticator.get("user_pool_id") or authenticator.get("pool_id"))
    if region and pool:
        return f"https://cognito-idp.{region}.amazonaws.com/{pool}"
    return ""


def login_authenticator_is_oidc(platform_auth: Mapping[str, Any] | None) -> bool:
    """Whether the selected bundle lane uses a Cognito or OIDC authenticator."""
    if not isinstance(platform_auth, Mapping) or _str(platform_auth.get("auth_provider")) != "bundle":
        return False
    resolved = _dict(platform_auth.get("login_authenticator"))
    provider = _dict(resolved.get("provider"))
    provider_type = _str(provider.get("type") or resolved.get("provider_type")).lower().replace("-", "_")
    return bool(provider) and provider_type in {t.replace("-", "_") for t in OIDC_AUTHENTICATOR_TYPES}


def bundle_login_config(settings: Any | None = None) -> BundleLoginConfig | None:
    """The lane's configuration, or ``None`` when this deployment does not
    select a bundle lane with an OIDC or Cognito authenticator."""
    from kdcube_ai_app.apps.chat.sdk.config import get_settings

    settings = settings or get_settings()
    try:
        platform_auth = settings.connection_hub_platform_auth_config()
    except Exception:  # noqa: BLE001 - no registry, no lane
        logger.debug("Connection Hub platform provider lookup failed", exc_info=True)
        return None
    if not login_authenticator_is_oidc(platform_auth):
        return None
    provider = _dict(platform_auth.get("provider"))
    resolved_authenticator = _dict(platform_auth.get("login_authenticator"))
    authenticator_provider = _dict(resolved_authenticator.get("provider"))
    authenticator_type = _str(
        authenticator_provider.get("type") or resolved_authenticator.get("provider_type")
    ).lower().replace("-", "_")
    authenticator = _dict(authenticator_provider.get("authenticator")) or authenticator_provider
    issuer_url = _cognito_issuer(authenticator) if "cognito" in authenticator_type else _str(authenticator.get("issuer"))
    client_id = _str(authenticator.get("app_client_id") or authenticator.get("client_id"))
    if not issuer_url or not client_id:
        logger.warning(
            "Platform server-side login %s: the authenticator lacks issuer or client id",
            platform_auth.get("provider_id"),
        )
        return None
    input_cfg = _dict(provider.get("input"))
    issuer_cfg = _dict(provider.get("issuer"))
    cookie_cfg = _dict(issuer_cfg.get("cookie"))
    scopes = tuple(_str(s) for s in (input_cfg.get("scopes") or ("openid", "email", "profile")) if _str(s))
    return_origins = tuple(_str(o) for o in (issuer_cfg.get("return_origins") or ()) if _str(o))
    policy = SessionPolicy(
        return_origins=return_origins,
        idle_ttl_seconds=_int(issuer_cfg.get("ttl_seconds"), 12 * 3600),
        max_ttl_seconds=max(_int(issuer_cfg.get("max_ttl_seconds"), 7 * 24 * 3600), _int(issuer_cfg.get("ttl_seconds"), 12 * 3600)),
        touch_interval_seconds=_int(issuer_cfg.get("touch_interval_seconds"), 60),
        attempt_ttl_seconds=_int(issuer_cfg.get("attempt_ttl_seconds"), 600),
    )
    auth_cfg = getattr(settings, "AUTH", None)
    return BundleLoginConfig(
        provider_id=_str(platform_auth.get("provider_id")),
        authority_id=_str(platform_auth.get("authority_id")),
        provider=provider,
        authority=_dict(platform_auth.get("authority")),
        authenticator_type=authenticator_type,
        issuer_url=issuer_url,
        client_id=client_id,
        client_secret_ref=_str(input_cfg.get("client_secret_ref") or authenticator.get("app_client_secret_ref") or authenticator.get("client_secret_ref")),
        hosted_ui_domain=_str(authenticator.get("hosted_ui_domain") or authenticator.get("hosted_ui")),
        scopes=scopes or ("openid", "email", "profile"),
        redirect_uri=_str(input_cfg.get("redirect_uri")),
        groups_claim=_str(input_cfg.get("groups_claim")),
        policy=policy,
        cookie_secure=bool(cookie_cfg.get("secure", True)),
        cookie_same_site=_str(cookie_cfg.get("same_site") or cookie_cfg.get("samesite") or "lax").lower(),
        cookie_domain=_str(cookie_cfg.get("domain")),
        session_cookie_name=_str(getattr(auth_cfg, "AUTH_TOKEN_COOKIE_NAME", "")) or "__Secure-LATC",
    )


SITE_SIGN_IN_PATH = "/signin/"


def sign_in_bounce_path(settings: Any | None = None) -> str:
    """Where a signed-out browser navigation is sent to sign in: the
    platform's own login route when this deployment hosts the sign-in, else
    the application site's sign-in page. One constant, decided here."""
    try:
        configured = bundle_login_config(settings) is not None
    except Exception:  # noqa: BLE001 - no settings, no lane
        configured = False
    return LOGIN_ROUTE if configured else SITE_SIGN_IN_PATH


def accepted_cognito_providers(config: BundleLoginConfig, settings: Any | None = None) -> list[Any]:
    """The Cognito pools a token-bearing host may present tokens from on the
    server-side lane: the referenced authenticator and its
    ``trusted_providers`` rows, as ``CognitoTrustedProviderConfig`` values.
    Empty when the authenticator is not Cognito or the provider opts out with
    ``input.accept_authenticator_tokens: false``."""
    from kdcube_ai_app.apps.chat.sdk.config import get_settings

    if config.authenticator_kind != "cognito":
        return []
    input_cfg = _dict(config.provider.get("input"))
    if input_cfg.get("accept_authenticator_tokens") is False:
        return []
    resolved = _dict(_dict(settings_platform_auth(settings)).get("login_authenticator"))
    provider = _dict(resolved.get("provider"))
    authenticator = _dict(provider.get("authenticator")) or provider
    rows = authenticator.get("trusted_providers")
    resolver = (settings or get_settings())._resolve_cognito_trusted_providers
    return list(resolver(
        primary_region=_str(authenticator.get("region")) or None,
        primary_pool_id=_str(authenticator.get("user_pool_id") or authenticator.get("pool_id")) or None,
        primary_client_id=config.client_id or None,
        registry_providers=rows if isinstance(rows, list) else None,
    ))


def settings_platform_auth(settings: Any | None = None) -> Mapping[str, Any]:
    from kdcube_ai_app.apps.chat.sdk.config import get_settings

    try:
        result = (settings or get_settings()).connection_hub_platform_auth_config()
    except Exception:  # noqa: BLE001
        return {}
    return result if isinstance(result, Mapping) else {}


def sliding_policy(settings: Any | None = None) -> SessionPolicy | None:
    """The sliding policy for request-time validation, or ``None`` when the
    lane is not configured (sessions then keep their fixed expiry)."""
    config = bundle_login_config(settings)
    return config.policy if config is not None else None


def grants_resolver(config: BundleLoginConfig) -> GrantsResolver:
    """Roles and permissions for a verified identity: the authority registry's
    grants (subjects, bootstrap rules, defaults), plus the authenticator's group
    claim when the provider maps it (``input.groups_claim``)."""
    from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.authority_providers.bundle_login import (
        resolve_platform_grants,
    )

    def resolve(identity: VerifiedIdentity) -> tuple[list[str], list[str], str]:
        roles, permissions, source = resolve_platform_grants(
            authority_cfg=config.authority,
            provider_cfg=config.provider,
            sub=identity.canonical_subject,
            provider=identity.provider,
            provider_subject=identity.subject,
            verified_claims=identity.claims,
        )
        if config.groups_claim:
            groups = identity.claims.get(config.groups_claim)
            if isinstance(groups, (list, tuple)):
                for group in groups:
                    name = _str(group)
                    if name and name not in roles:
                        roles.append(name)
        return list(roles), list(permissions), source

    return resolve


_ENDPOINTS: dict[str, OidcEndpoints] = {}


async def _upstream(config: BundleLoginConfig, *, redirect_uri: str) -> UpstreamIdentity:
    from kdcube_ai_app.apps.chat.sdk.config import get_secret

    from connection_hub.server_side_login.oidc_jwt import PyJwtVerifier

    client_secret = ""
    if config.client_secret_ref:
        client_secret = _str(await get_secret(config.client_secret_ref, default=None))
    if config.authenticator_kind == "cognito":
        client = OidcClientConfig.cognito(
            issuer=config.issuer_url,
            client_id=config.client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            hosted_ui_domain=config.hosted_ui_domain,
            scopes=config.scopes,
        )
    else:
        client = OidcClientConfig(
            issuer=config.issuer_url,
            client_id=config.client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scopes=config.scopes,
        )
    flow = OidcCodeFlow(client, verifier=_verifier_for(config), discover=None)
    cached = _ENDPOINTS.get(config.issuer_url)
    if cached is None:
        cached = await flow.endpoints()
        _ENDPOINTS[config.issuer_url] = cached
    else:
        flow._endpoints = cached  # noqa: SLF001 - one discovery per issuer per process
    return flow


_VERIFIERS: dict[str, Any] = {}


def _verifier_for(config: BundleLoginConfig) -> Any:
    from connection_hub.server_side_login.oidc_jwt import PyJwtVerifier

    verifier = _VERIFIERS.get(config.issuer_url)
    if verifier is None:
        endpoints = _ENDPOINTS.get(config.issuer_url)
        jwks_uri = endpoints.jwks_uri if endpoints is not None and endpoints.jwks_uri else f"{config.issuer_url.rstrip('/')}/.well-known/jwks.json"
        verifier = PyJwtVerifier(jwks_uri)
        _VERIFIERS[config.issuer_url] = verifier
    return verifier


def public_origin(request: Any) -> str:
    """The origin the browser used, behind a trusted proxy: forwarded
    protocol and host first, else the request's own."""
    headers = request.headers
    proto = _str(headers.get("x-forwarded-proto")).split(",")[0].strip() or request.url.scheme
    host = _str(headers.get("x-forwarded-host")).split(",")[0].strip() or _str(headers.get("host")) or request.url.netloc
    return f"{proto}://{host}"


async def platform_login_flow(*, origin: str, settings: Any | None = None) -> BrowserSessionFlow | None:
    """The flow for one request origin, or ``None`` when the lane is not
    configured. The redirect URI is the registry's when set, else this
    origin's callback route (the Cognito app client must list it)."""
    config = bundle_login_config(settings)
    if config is None:
        return None
    authority = get_bundle_session_authority()
    redirect_uri = config.redirect_uri or f"{origin.rstrip('/')}{CALLBACK_ROUTE}"
    upstream = await _upstream(config, redirect_uri=redirect_uri)
    cookies = StandardCookiePolicy(
        session_name=config.session_cookie_name,
        secure=config.cookie_secure,
        same_site=config.cookie_same_site,
        domain=config.cookie_domain,
    )
    backend = PlatformSessionBackend(authority, grants=grants_resolver(config), policy=config.policy)
    return BrowserSessionFlow(
        backend=backend,
        attempts=RedisLoginAttemptStore(authority),
        upstream=upstream,
        cookies=cookies,
        policy=config.policy,
    )


__all__ = [
    "CALLBACK_ROUTE",
    "LOGIN_ROUTE",
    "LOGOUT_ROUTE",
    "PROFILE_ROUTE",
    "RETURN_COOKIE_TTL_SECONDS",
    "SIGNED_OUT_ROUTE",
    "BUNDLE_LOGIN_TYPES",
    "BundleLoginConfig",
    "PlatformSessionBackend",
    "RedisLoginAttemptStore",
    "accepted_cognito_providers",
    "bundle_login_config",
    "grants_resolver",
    "platform_login_flow",
    "public_origin",
    "sign_in_bounce_path",
    "settings_platform_auth",
    "sliding_policy",
    "login_authenticator_is_oidc",
]
