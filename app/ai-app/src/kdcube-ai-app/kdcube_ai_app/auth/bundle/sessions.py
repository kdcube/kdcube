# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
Bundle-owned platform session auth.

Docs:
- repo:./app/ai-app/docs/service/auth/bundle-session-auth-README.md
- repo:./app/ai-app/docs/service/auth/auth-README.md
"""

from __future__ import annotations

import base64
import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping, Optional

from kdcube_ai_app.auth.AuthManager import AuthManager, AuthenticationError, User
from kdcube_ai_app.infra.namespaces import ns_key
from kdcube_ai_app.infra.redis.client import get_async_redis_client

logger = logging.getLogger(__name__)

SESSION_TOKEN_SCHEMA = "kdcube.session_token.v1"
SESSION_TOKEN_PREFIX = "kst1"
BUNDLE_SESSION_SECRET_KEY = "services.session_token.secret"
BUNDLE_SESSION_REDIS_BASE = "kdcube:auth:bundle-session"
BUNDLE_SESSION_DEFAULT_TTL_SECONDS = 12 * 3600
BUNDLE_SESSION_MAX_TTL_SECONDS = 7 * 24 * 3600
BUNDLE_SESSION_MUTATION_LOCK_TTL_SECONDS = 30
BUNDLE_SESSION_MUTATION_LOCK_WAIT_SECONDS = 5.0


class BundleSessionError(ValueError):
    """Base error for bundle-owned platform sessions."""


class BundleSessionInvalid(BundleSessionError):
    """The session token or backing session record is invalid."""


class BundleSessionExpired(BundleSessionInvalid):
    """The session token or backing session record is expired."""


@dataclass(frozen=True)
class BundleSessionUser:
    sub: str
    username: str | None = None
    email: str | None = None
    name: str | None = None
    roles: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    provider: str | None = None
    provider_subject: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    disabled: bool = False
    created_at: int = 0
    updated_at: int = 0

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "BundleSessionUser":
        now = int(time.time())
        return cls(
            sub=str(data.get("sub") or "").strip(),
            username=_optional_str(data.get("username")),
            email=_optional_str(data.get("email")),
            name=_optional_str(data.get("name")),
            roles=_as_list(data.get("roles")),
            permissions=_as_list(data.get("permissions")),
            provider=_optional_str(data.get("provider")),
            provider_subject=_optional_str(data.get("provider_subject")),
            metadata=dict(data.get("metadata") or {}),
            disabled=bool(data.get("disabled") or False),
            created_at=int(data.get("created_at") or now),
            updated_at=int(data.get("updated_at") or now),
        )

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BundleSessionGrant:
    token: str
    session_id: str
    user: BundleSessionUser
    claims: dict[str, Any]
    expires_at: int


@dataclass(frozen=True)
class BundleSessionVerification:
    session_id: str
    user: BundleSessionUser
    claims: dict[str, Any]


class BundleSessionAuthUser(User):
    sub: str | None = None


def _optional_str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _as_list(values: Iterable[Any] | None) -> list[str]:
    if isinstance(values, str):
        values = [values]
    return [str(value).strip() for value in (values or ()) if str(value).strip()]


_CREDENTIAL_CLAIM_SCHEMA = "kdcube.credential.v1"
_CREDENTIAL_CLAIM_KEYS = {
    "schema",
    "credential_id",
    "credential_kind",
    "issuer_authority_id",
    "issuer_authenticator_id",
    "subject",
    "tenant",
    "project",
    "audience",
    "session_id",
    "verified_authority",
    "attrs",
    "iat",
    "exp",
}


def _session_credential_claim(metadata: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(metadata, Mapping):
        return None
    credential = metadata.get("credential")
    if not isinstance(credential, Mapping):
        return None
    if credential.get("schema") != _CREDENTIAL_CLAIM_SCHEMA:
        return None
    claim = {
        key: credential[key]
        for key in _CREDENTIAL_CLAIM_KEYS
        if key in credential and credential[key] not in (None, "", [], {})
    }
    return claim or None


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def _compact_json(data: Mapping[str, Any]) -> str:
    return json.dumps(data, separators=(",", ":"), sort_keys=True)


def _secret_bytes(secret: str | bytes | None = None) -> bytes:
    if isinstance(secret, bytes):
        value = secret
    else:
        value = str(secret or "").encode("utf-8")
    if not value:
        raise BundleSessionInvalid(
            f"bundle session secret is not configured at {BUNDLE_SESSION_SECRET_KEY}"
        )
    return value


def _bounded_ttl(ttl_seconds: int | None) -> int:
    try:
        ttl = int(ttl_seconds or BUNDLE_SESSION_DEFAULT_TTL_SECONDS)
    except Exception as exc:
        raise BundleSessionInvalid("bundle session ttl must be an integer") from exc
    if ttl <= 0:
        raise BundleSessionInvalid("bundle session ttl must be positive")
    return min(ttl, BUNDLE_SESSION_MAX_TTL_SECONDS)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _hash_subject(sub: str) -> str:
    return hashlib.sha256(sub.encode("utf-8")).hexdigest()


def _make_token(claims: Mapping[str, Any], *, secret: str | bytes) -> str:
    body = _b64url_encode(_compact_json(claims).encode("utf-8"))
    sig = _b64url_encode(
        hmac.new(_secret_bytes(secret), body.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{SESSION_TOKEN_PREFIX}.{body}.{sig}"


def _verify_token_signature(token: str, *, secret: str | bytes) -> dict[str, Any]:
    try:
        prefix, body, sig = str(token or "").strip().split(".", 2)
    except ValueError as exc:
        raise BundleSessionInvalid("bundle session token is malformed") from exc
    if prefix != SESSION_TOKEN_PREFIX or not body or not sig:
        raise BundleSessionInvalid("bundle session token is malformed")

    expected = _b64url_encode(
        hmac.new(_secret_bytes(secret), body.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(sig, expected):
        raise BundleSessionInvalid("bundle session token signature is invalid")

    try:
        claims = json.loads(_b64url_decode(body).decode("utf-8"))
    except Exception as exc:
        raise BundleSessionInvalid("bundle session token payload is invalid") from exc
    if not isinstance(claims, dict):
        raise BundleSessionInvalid("bundle session token payload is invalid")
    if claims.get("schema") != SESSION_TOKEN_SCHEMA:
        raise BundleSessionInvalid("bundle session token schema is unsupported")
    return claims


class BundleSessionAuthority:
    """
    Async authority for bundle-owned platform login sessions.

    Redis stores the mutable truth: user profile, active session records, and
    per-user token version. The signed cookie token carries only enough data to
    locate and verify the backing session.
    """

    def __init__(
        self,
        *,
        tenant: str | None = None,
        project: str | None = None,
        redis: Any | None = None,
        redis_url: str | None = None,
        secret: str | bytes | None = None,
    ):
        self.tenant = tenant
        self.project = project
        self._redis = redis
        self._redis_url = redis_url
        self._secret = secret

    def _ns(self, base: str) -> str:
        return ns_key(base, tenant=self.tenant, project=self.project)

    def _user_key(self, sub: str) -> str:
        return self._ns(f"{BUNDLE_SESSION_REDIS_BASE}:user:{sub}")

    def _session_key(self, session_id: str) -> str:
        return self._ns(f"{BUNDLE_SESSION_REDIS_BASE}:session:{session_id}")

    def _user_sessions_key(self, sub: str) -> str:
        return self._ns(f"{BUNDLE_SESSION_REDIS_BASE}:user-sessions:{sub}")

    def _user_version_key(self, sub: str) -> str:
        return self._ns(f"{BUNDLE_SESSION_REDIS_BASE}:user-version:{sub}")

    def _user_lock_key(self, sub: str) -> str:
        return self._ns(f"{BUNDLE_SESSION_REDIS_BASE}:lock:user:{_hash_subject(sub)}")

    async def _redis_client(self) -> Any:
        if self._redis is not None:
            return self._redis
        if not self._redis_url:
            from kdcube_ai_app.apps.chat.sdk.config import get_settings

            self._redis_url = get_settings().REDIS_URL
        if not self._redis_url:
            raise BundleSessionInvalid("redis url is required for bundle sessions")
        self._redis = get_async_redis_client(self._redis_url)
        return self._redis

    async def _resolve_secret(self) -> str | bytes:
        if self._secret is not None:
            return self._secret
        from kdcube_ai_app.apps.chat.sdk.config import get_secret

        resolved = await get_secret(BUNDLE_SESSION_SECRET_KEY, default=None)
        if not str(resolved or "").strip():
            raise BundleSessionInvalid(
                f"bundle session secret is not configured at {BUNDLE_SESSION_SECRET_KEY}"
            )
        self._secret = str(resolved).strip()
        return self._secret

    async def _get_json(self, key: str) -> dict[str, Any] | None:
        redis = await self._redis_client()
        raw = await redis.get(key)
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        if not raw:
            return None
        try:
            value = json.loads(str(raw))
        except Exception as exc:
            raise BundleSessionInvalid(f"bundle session record is invalid at {key}") from exc
        if not isinstance(value, dict):
            raise BundleSessionInvalid(f"bundle session record is invalid at {key}")
        return value

    async def _set_json(self, key: str, value: Mapping[str, Any], *, ttl_seconds: int | None = None) -> None:
        redis = await self._redis_client()
        payload = json.dumps(dict(value), separators=(",", ":"), sort_keys=True)
        if ttl_seconds:
            await redis.setex(key, ttl_seconds, payload)
        else:
            await redis.set(key, payload)

    async def _current_version(self, sub: str) -> int:
        redis = await self._redis_client()
        key = self._user_version_key(sub)
        raw = await redis.get(key)
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        if raw is None:
            await redis.set(key, "1")
            return 1
        try:
            return int(raw)
        except Exception as exc:
            raise BundleSessionInvalid("bundle session user version is invalid") from exc

    @asynccontextmanager
    async def _user_mutation_lock(self, sub: str):
        redis = await self._redis_client()
        lock_key = self._user_lock_key(sub)
        token = uuid.uuid4().hex
        deadline = time.monotonic() + BUNDLE_SESSION_MUTATION_LOCK_WAIT_SECONDS
        while True:
            acquired = await redis.set(
                lock_key,
                token,
                nx=True,
                ex=BUNDLE_SESSION_MUTATION_LOCK_TTL_SECONDS,
            )
            if acquired:
                break
            if time.monotonic() >= deadline:
                raise BundleSessionInvalid("timed out acquiring bundle session user mutation lock")
            await asyncio.sleep(0.05)
        try:
            yield
        finally:
            try:
                current = await redis.get(lock_key)
                if isinstance(current, (bytes, bytearray)):
                    current = current.decode("utf-8")
                if current == token:
                    await redis.delete(lock_key)
            except Exception:
                logger.warning("Failed to release bundle session user mutation lock key=%s", lock_key, exc_info=True)

    async def register_user(
        self,
        *,
        sub: str,
        username: str | None = None,
        email: str | None = None,
        name: str | None = None,
        roles: Iterable[str] | None = None,
        permissions: Iterable[str] | None = None,
        provider: str | None = None,
        provider_subject: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        disabled: bool = False,
    ) -> BundleSessionUser:
        sub_value = str(sub or "").strip()
        if not sub_value:
            raise BundleSessionInvalid("bundle session user sub is required")

        async with self._user_mutation_lock(sub_value):
            return await self._register_user_unlocked(
                sub=sub_value,
                username=username,
                email=email,
                name=name,
                roles=roles,
                permissions=permissions,
                provider=provider,
                provider_subject=provider_subject,
                metadata=metadata,
                disabled=disabled,
            )

    async def _register_user_unlocked(
        self,
        *,
        sub: str,
        username: str | None = None,
        email: str | None = None,
        name: str | None = None,
        roles: Iterable[str] | None = None,
        permissions: Iterable[str] | None = None,
        provider: str | None = None,
        provider_subject: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        disabled: bool = False,
    ) -> BundleSessionUser:
        sub_value = str(sub or "").strip()
        now = int(time.time())
        existing = await self._get_json(self._user_key(sub_value))
        created_at = int((existing or {}).get("created_at") or now)
        merged = {
            "sub": sub_value,
            "username": username if username is not None else (existing or {}).get("username"),
            "email": email if email is not None else (existing or {}).get("email"),
            "name": name if name is not None else (existing or {}).get("name"),
            "roles": _as_list(roles if roles is not None else (existing or {}).get("roles")),
            "permissions": _as_list(permissions if permissions is not None else (existing or {}).get("permissions")),
            "provider": provider if provider is not None else (existing or {}).get("provider"),
            "provider_subject": (
                provider_subject if provider_subject is not None else (existing or {}).get("provider_subject")
            ),
            "metadata": dict(metadata if metadata is not None else (existing or {}).get("metadata") or {}),
            "disabled": bool(disabled),
            "created_at": created_at,
            "updated_at": now,
        }
        user = BundleSessionUser.from_mapping(merged)
        await self._set_json(self._user_key(sub_value), user.to_public_dict())
        await self._current_version(sub_value)
        logger.info(
            "Bundle session user registered sub=%s provider=%s roles=%s",
            sub_value,
            user.provider,
            len(user.roles),
        )
        return user

    async def get_user(self, sub: str) -> BundleSessionUser | None:
        sub_value = str(sub or "").strip()
        if not sub_value:
            return None
        data = await self._get_json(self._user_key(sub_value))
        return BundleSessionUser.from_mapping(data) if data else None

    async def login(
        self,
        *,
        sub: str,
        provider: str | None = None,
        provider_subject: str | None = None,
        ttl_seconds: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> BundleSessionGrant:
        sub_value = str(sub or "").strip()
        if not sub_value:
            raise BundleSessionInvalid("bundle session user sub is required")
        async with self._user_mutation_lock(sub_value):
            return await self._login_unlocked(
                sub=sub_value,
                provider=provider,
                provider_subject=provider_subject,
                ttl_seconds=ttl_seconds,
                metadata=metadata,
            )

    async def _login_unlocked(
        self,
        *,
        sub: str,
        provider: str | None = None,
        provider_subject: str | None = None,
        ttl_seconds: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> BundleSessionGrant:
        sub_value = str(sub or "").strip()
        user = await self.get_user(sub_value)
        if user is None:
            raise BundleSessionInvalid("bundle session user is not registered")
        if user.disabled:
            raise BundleSessionInvalid("bundle session user is disabled")

        ttl = _bounded_ttl(ttl_seconds)
        issued_at = int(time.time())
        expires_at = issued_at + ttl
        session_id = f"bsn_{uuid.uuid4().hex}"
        version = await self._current_version(sub_value)
        session_record = {
            "schema": SESSION_TOKEN_SCHEMA,
            "session_id": session_id,
            "sub": sub_value,
            "provider": provider or user.provider,
            "provider_subject": provider_subject or user.provider_subject,
            "token_sha256": None,
            "version": version,
            "active": True,
            "metadata": dict(metadata or {}),
            "iat": issued_at,
            "exp": expires_at,
        }
        claims = {
            "schema": SESSION_TOKEN_SCHEMA,
            "iss": "kdcube-bundle-session",
            "sid": session_id,
            "sub": sub_value,
            "provider": session_record["provider"],
            "provider_subject": session_record["provider_subject"],
            "ver": version,
            "iat": issued_at,
            "exp": expires_at,
        }
        credential_claim = _session_credential_claim(metadata)
        if credential_claim is not None:
            claims["credential"] = credential_claim
        secret = await self._resolve_secret()
        token = _make_token(claims, secret=secret)
        session_record["token_sha256"] = _hash_token(token)

        redis = await self._redis_client()
        await self._set_json(self._session_key(session_id), session_record, ttl_seconds=ttl)
        await redis.sadd(self._user_sessions_key(sub_value), session_id)
        await redis.expire(self._user_sessions_key(sub_value), max(ttl, BUNDLE_SESSION_DEFAULT_TTL_SECONDS))
        logger.info("Bundle session login issued sub=%s session=%s ttl=%s", sub_value, session_id, ttl)
        return BundleSessionGrant(
            token=token,
            session_id=session_id,
            user=user,
            claims=claims,
            expires_at=expires_at,
        )


    async def login_or_register(
        self,
        *,
        sub: str,
        username: str | None = None,
        email: str | None = None,
        name: str | None = None,
        roles: Iterable[str] | None = None,
        permissions: Iterable[str] | None = None,
        provider: str | None = None,
        provider_subject: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        ttl_seconds: int | None = None,
    ) -> BundleSessionGrant:
        await self.register_user(
            sub=sub,
            username=username,
            email=email,
            name=name,
            roles=roles,
            permissions=permissions,
            provider=provider,
            provider_subject=provider_subject,
            metadata=metadata,
        )
        return await self.login(
            sub=sub,
            provider=provider,
            provider_subject=provider_subject,
            metadata=metadata,
            ttl_seconds=ttl_seconds,
        )

    async def logout(self, *, token: str | None = None, session_id: str | None = None) -> bool:
        sid = str(session_id or "").strip()
        if not sid and token:
            secret = await self._resolve_secret()
            claims = _verify_token_signature(token, secret=secret)
            sid = str(claims.get("sid") or "").strip()
        if not sid:
            return False
        redis = await self._redis_client()
        removed = await redis.delete(self._session_key(sid))
        logger.info("Bundle session logout session=%s removed=%s", sid, bool(removed))
        return bool(removed)

    async def invalidate_user(self, sub: str) -> int:
        sub_value = str(sub or "").strip()
        if not sub_value:
            return 0
        async with self._user_mutation_lock(sub_value):
            return await self._invalidate_user_unlocked(sub_value)

    async def _invalidate_user_unlocked(self, sub_value: str) -> int:
        redis = await self._redis_client()
        await redis.incr(self._user_version_key(sub_value))
        raw_sessions = await redis.smembers(self._user_sessions_key(sub_value))
        session_ids = [
            item.decode("utf-8") if isinstance(item, (bytes, bytearray)) else str(item)
            for item in (raw_sessions or [])
        ]
        removed = 0
        if session_ids:
            removed = int(await redis.delete(*[self._session_key(sid) for sid in session_ids]) or 0)
        await redis.delete(self._user_sessions_key(sub_value))
        logger.info("Bundle session user invalidated sub=%s sessions_removed=%s", sub_value, removed)
        return removed

    async def delete_user(self, sub: str) -> bool:
        sub_value = str(sub or "").strip()
        if not sub_value:
            return False
        async with self._user_mutation_lock(sub_value):
            await self._invalidate_user_unlocked(sub_value)
            redis = await self._redis_client()
            removed = await redis.delete(self._user_key(sub_value))
            logger.info("Bundle session user deleted sub=%s removed=%s", sub_value, bool(removed))
            return bool(removed)

    async def validate_token(self, token: str, *, now: int | None = None) -> BundleSessionVerification:
        secret = await self._resolve_secret()
        claims = _verify_token_signature(token, secret=secret)
        current_time = int(time.time() if now is None else now)
        try:
            expires_at = int(claims.get("exp") or 0)
        except Exception as exc:
            raise BundleSessionInvalid("bundle session token expiry is invalid") from exc
        if expires_at < current_time:
            raise BundleSessionExpired("bundle session token is expired")

        session_id = str(claims.get("sid") or "").strip()
        sub = str(claims.get("sub") or "").strip()
        if not session_id or not sub:
            raise BundleSessionInvalid("bundle session token subject/session is missing")

        session_record = await self._get_json(self._session_key(session_id))
        if not session_record or not session_record.get("active", True):
            raise BundleSessionInvalid("bundle session is not active")
        if session_record.get("sub") != sub:
            raise BundleSessionInvalid("bundle session subject does not match")
        if session_record.get("token_sha256") != _hash_token(token):
            raise BundleSessionInvalid("bundle session token record does not match")
        if int(session_record.get("exp") or 0) < current_time:
            raise BundleSessionExpired("bundle session record is expired")

        current_version = await self._current_version(sub)
        if int(claims.get("ver") or 0) != current_version:
            raise BundleSessionInvalid("bundle session token was invalidated")

        user = await self.get_user(sub)
        if user is None:
            raise BundleSessionInvalid("bundle session user is unavailable")
        if user.disabled:
            raise BundleSessionInvalid("bundle session user is disabled")
        return BundleSessionVerification(session_id=session_id, user=user, claims=claims)


_AUTHORITIES: dict[tuple[str | None, str | None, str | None], BundleSessionAuthority] = {}


def get_bundle_session_authority(
    *,
    tenant: str | None = None,
    project: str | None = None,
    redis: Any | None = None,
    redis_url: str | None = None,
    secret: str | bytes | None = None,
) -> BundleSessionAuthority:
    if redis is not None or secret is not None:
        return BundleSessionAuthority(
            tenant=tenant,
            project=project,
            redis=redis,
            redis_url=redis_url,
            secret=secret,
        )
    key = (tenant, project, redis_url)
    authority = _AUTHORITIES.get(key)
    if authority is None:
        authority = BundleSessionAuthority(tenant=tenant, project=project, redis_url=redis_url)
        _AUTHORITIES[key] = authority
    return authority


async def register_bundle_session_user(**kwargs: Any) -> BundleSessionUser:
    return await get_bundle_session_authority().register_user(**kwargs)


async def login_bundle_session(**kwargs: Any) -> BundleSessionGrant:
    return await get_bundle_session_authority().login(**kwargs)


async def login_or_register_bundle_session(**kwargs: Any) -> BundleSessionGrant:
    return await get_bundle_session_authority().login_or_register(**kwargs)


async def logout_bundle_session(**kwargs: Any) -> bool:
    return await get_bundle_session_authority().logout(**kwargs)


async def invalidate_bundle_session_user(sub: str) -> int:
    return await get_bundle_session_authority().invalidate_user(sub)


async def delete_bundle_session_user(sub: str) -> bool:
    return await get_bundle_session_authority().delete_user(sub)


async def validate_bundle_session_token(token: str) -> BundleSessionVerification:
    return await get_bundle_session_authority().validate_token(token)


class BundleSessionAuthManager(AuthManager):
    """Gateway auth manager for bundle-owned platform session tokens."""

    def __init__(
        self,
        send_validation_error_details: bool = False,
        *,
        authority: BundleSessionAuthority | None = None,
    ):
        super().__init__(send_validation_error_details)
        self.authority = authority or get_bundle_session_authority()

    async def authenticate(self, token: str) -> BundleSessionAuthUser:
        if not token:
            raise AuthenticationError("No token provided")
        try:
            verification = await self.authority.validate_token(token)
        except BundleSessionError as exc:
            raise AuthenticationError(str(exc)) from exc
        user = verification.user
        return BundleSessionAuthUser(
            username=user.username or user.sub,
            email=user.email,
            name=user.name or user.username,
            roles=list(user.roles or []),
            permissions=list(user.permissions or []),
            sub=user.sub,
        )

    async def get_service_token(self) -> str:
        return ""
