# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

from kdcube_ai_app.auth.sessions import RequestContext, UserSession, UserType
from kdcube_ai_app.infra.namespaces import ns_key


FEDERATED_TOKEN_SCHEMA = "kdcube.federated_token.v1"
FEDERATED_TOKEN_PREFIX = "kft1"
FEDERATED_TOKEN_SECRET_KEY = "services.federated_token.secret"
FEDERATED_TOKEN_DEFAULT_TTL_SECONDS = 900
FEDERATED_TOKEN_MAX_TTL_SECONDS = 3600
FEDERATED_TOKEN_REDIS_BASE = "kdcube:federated-idp:token"
FEDERATED_CREDENTIAL_SCHEMA = "kdcube.credential.v1"
FEDERATED_INGRESS_SESSION_AUTHORITY_ID = "kdcube.ingress_session"
FEDERATED_INGRESS_SESSION_AUTHENTICATOR_ID = "kdcube.signed_active_record"


class FederatedTokenError(ValueError):
    """Base error for scoped federated Data Bus tokens."""


class FederatedTokenExpired(FederatedTokenError):
    """The token was well formed but expired."""


class FederatedTokenInvalid(FederatedTokenError):
    """The token is malformed, unsigned, revoked, or out of scope."""


@dataclass(frozen=True)
class FederatedTokenGrant:
    token: str
    session: UserSession
    claims: dict[str, Any]
    expires_at: int


@dataclass(frozen=True)
class FederatedTokenVerification:
    session: UserSession
    claims: dict[str, Any]


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def _compact_json(data: Mapping[str, Any]) -> str:
    return json.dumps(data, separators=(",", ":"), sort_keys=True)


def _as_list(values: Iterable[Any] | None) -> list[str]:
    return [str(value).strip() for value in (values or ()) if str(value).strip()]


def _bounded_ttl(ttl_seconds: int | None) -> int:
    try:
        ttl = int(ttl_seconds or FEDERATED_TOKEN_DEFAULT_TTL_SECONDS)
    except Exception as exc:
        raise FederatedTokenInvalid("federated token ttl must be an integer") from exc
    if ttl <= 0:
        raise FederatedTokenInvalid("federated token ttl must be positive")
    return min(ttl, FEDERATED_TOKEN_MAX_TTL_SECONDS)


def _secret_bytes(secret: str | bytes | None = None) -> bytes:
    if isinstance(secret, bytes):
        value = secret
    else:
        value = str(secret or "").encode("utf-8")
    if not value:
        raise FederatedTokenInvalid(
            f"federated token secret is not configured at {FEDERATED_TOKEN_SECRET_KEY}"
        )
    return value


async def _resolve_secret(secret: str | bytes | None = None) -> str | bytes:
    if secret is not None:
        return secret
    try:
        from kdcube_ai_app.apps.chat.sdk.config import get_secret

        resolved = await get_secret(FEDERATED_TOKEN_SECRET_KEY, default=None)
    except Exception as exc:
        raise FederatedTokenInvalid(
            f"federated token secret is not configured at {FEDERATED_TOKEN_SECRET_KEY}"
        ) from exc
    if not str(resolved or "").strip():
        raise FederatedTokenInvalid(
            f"federated token secret is not configured at {FEDERATED_TOKEN_SECRET_KEY}"
        )
    return str(resolved).strip()


def _token_subject(*, tenant: str, project: str, bundle_id: str) -> str:
    return f"federated-data-bus:{tenant}:{project}:{bundle_id}"


def _token_key(*, tenant: str, project: str, jti: str) -> str:
    return ns_key(f"{FEDERATED_TOKEN_REDIS_BASE}:{jti}", tenant=tenant, project=project)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _make_token(claims: Mapping[str, Any], *, secret: str | bytes | None = None) -> str:
    body = _b64url_encode(_compact_json(claims).encode("utf-8"))
    sig = _b64url_encode(
        hmac.new(_secret_bytes(secret), body.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{FEDERATED_TOKEN_PREFIX}.{body}.{sig}"


def _verify_token_signature(token: str, *, secret: str | bytes | None = None) -> dict[str, Any]:
    try:
        prefix, body, sig = str(token or "").strip().split(".", 2)
    except ValueError as exc:
        raise FederatedTokenInvalid("federated token is malformed") from exc
    if prefix != FEDERATED_TOKEN_PREFIX or not body or not sig:
        raise FederatedTokenInvalid("federated token is malformed")

    expected = _b64url_encode(
        hmac.new(_secret_bytes(secret), body.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(sig, expected):
        raise FederatedTokenInvalid("federated token signature is invalid")

    try:
        claims = json.loads(_b64url_decode(body).decode("utf-8"))
    except Exception as exc:
        raise FederatedTokenInvalid("federated token payload is invalid") from exc
    if not isinstance(claims, dict):
        raise FederatedTokenInvalid("federated token payload is invalid")
    if claims.get("schema") != FEDERATED_TOKEN_SCHEMA:
        raise FederatedTokenInvalid("federated token schema is unsupported")
    return claims


def _request_context_from_request(request: Any) -> RequestContext:
    gateway_adapter = getattr(getattr(request, "app", None), "state", None)
    gateway_adapter = getattr(gateway_adapter, "gateway_adapter", None)
    extractor = getattr(gateway_adapter, "_extract_context", None)
    if callable(extractor):
        try:
            return extractor(request)
        except Exception:
            pass

    headers = getattr(request, "headers", {}) or {}
    client = getattr(request, "client", None)
    client_ip = getattr(client, "host", None) or "unknown"
    return RequestContext(
        client_ip=client_ip,
        user_agent=headers.get("user-agent", "") if hasattr(headers, "get") else "",
    )


def _session_manager_from_request(request: Any) -> Any:
    state = getattr(getattr(request, "app", None), "state", None)
    gateway_adapter = getattr(state, "gateway_adapter", None)
    session_manager = getattr(getattr(gateway_adapter, "gateway", None), "session_manager", None)
    if session_manager is None:
        raise FederatedTokenInvalid("session manager is unavailable")
    return session_manager


def _redis_from_request_or_session_manager(request: Any, session_manager: Any) -> Any:
    state = getattr(getattr(request, "app", None), "state", None)
    redis = getattr(state, "redis_async", None)
    if redis is not None:
        return redis
    return getattr(session_manager, "redis", None)


async def issue_federated_data_bus_token(
    *,
    request: Any,
    tenant: str,
    project: str,
    bundle_id: str,
    user_id: str,
    user_type: str | UserType = UserType.REGISTERED,
    username: str | None = None,
    email: str | None = None,
    roles: Iterable[str] | None = None,
    permissions: Iterable[str] | None = None,
    identity_authority: Mapping[str, Any] | None = None,
    ttl_seconds: int = FEDERATED_TOKEN_DEFAULT_TTL_SECONDS,
    secret: str | bytes | None = None,
) -> FederatedTokenGrant:
    """
    Issue a short-lived, bundle-scoped token for Data Bus use.

    The caller must validate the upstream federated identity before calling
    this function. This helper only materializes the verified identity as a
    KDCube session plus a Redis-registered Data Bus token.
    """
    tenant_value = str(tenant or "").strip()
    project_value = str(project or "").strip()
    bundle_value = str(bundle_id or "").strip()
    user_id_value = str(user_id or "").strip()
    if not all((tenant_value, project_value, bundle_value, user_id_value)):
        raise FederatedTokenInvalid("tenant, project, bundle_id, and user_id are required")

    user_type_value = user_type.value if isinstance(user_type, UserType) else str(user_type or "").strip().lower()
    resolved_user_type = UserType(user_type_value or UserType.REGISTERED.value)
    ttl = _bounded_ttl(ttl_seconds)
    issued_at = int(time.time())
    expires_at = issued_at + ttl

    session_manager = _session_manager_from_request(request)
    context = _request_context_from_request(request)
    user_data = {
        "user_id": user_id_value,
        "username": username or user_id_value,
        "email": email,
        "roles": _as_list(roles),
        "permissions": _as_list(permissions),
        "identity_authority": dict(identity_authority or {}),
    }
    session = await session_manager.get_or_create_session(context, resolved_user_type, user_data)

    jti = f"fdt_{uuid.uuid4().hex}"
    credential: dict[str, Any] = {
        "schema": FEDERATED_CREDENTIAL_SCHEMA,
        "credential_id": jti,
        "credential_kind": "derived_session",
        "issuer_authority_id": FEDERATED_INGRESS_SESSION_AUTHORITY_ID,
        "issuer_authenticator_id": FEDERATED_INGRESS_SESSION_AUTHENTICATOR_ID,
        "subject": f"session:{session.session_id}",
        "tenant": tenant_value,
        "project": project_value,
        "audience": "kdcube:data_bus",
        "session_id": session.session_id,
        "iat": issued_at,
        "exp": expires_at,
    }
    if identity_authority:
        credential["verified_authority"] = dict(identity_authority)

    claims: dict[str, Any] = {
        "schema": FEDERATED_TOKEN_SCHEMA,
        "jti": jti,
        "sub": _token_subject(tenant=tenant_value, project=project_value, bundle_id=bundle_value),
        "tenant": tenant_value,
        "project": project_value,
        "bundle_id": bundle_value,
        "session_id": session.session_id,
        "allowed_transports": ["data_bus"],
        "credential": credential,
        "iat": issued_at,
        "exp": expires_at,
    }
    resolved_secret = await _resolve_secret(secret)
    token = _make_token(claims, secret=resolved_secret)
    record = {
        "schema": FEDERATED_TOKEN_SCHEMA,
        "token_sha256": _hash_token(token),
        "claims": claims,
    }

    redis = _redis_from_request_or_session_manager(request, session_manager)
    if redis is None:
        await session_manager.init_redis()
        redis = getattr(session_manager, "redis", None)
    if redis is None:
        raise FederatedTokenInvalid("redis is unavailable")
    await redis.setex(
        _token_key(tenant=tenant_value, project=project_value, jti=claims["jti"]),
        ttl,
        json.dumps(record, ensure_ascii=False),
    )
    return FederatedTokenGrant(token=token, session=session, claims=claims, expires_at=expires_at)


async def verify_federated_data_bus_token(
    *,
    token: str,
    tenant: str,
    project: str,
    bundle_id: str,
    redis: Any,
    session_manager: Any,
    secret: str | bytes | None = None,
    now: int | None = None,
) -> FederatedTokenVerification:
    resolved_secret = await _resolve_secret(secret)
    claims = _verify_token_signature(token, secret=resolved_secret)
    current_time = int(time.time() if now is None else now)
    try:
        expires_at = int(claims.get("exp") or 0)
    except Exception as exc:
        raise FederatedTokenInvalid("federated token expiry is invalid") from exc
    if expires_at < current_time:
        raise FederatedTokenExpired("federated token is expired")

    tenant_value = str(tenant or "").strip()
    project_value = str(project or "").strip()
    bundle_value = str(bundle_id or "").strip()
    expected_subject = _token_subject(tenant=tenant_value, project=project_value, bundle_id=bundle_value)
    if claims.get("sub") != expected_subject:
        raise FederatedTokenInvalid("federated token subject does not match")
    if claims.get("tenant") != tenant_value or claims.get("project") != project_value:
        raise FederatedTokenInvalid("federated token tenant/project does not match")
    if claims.get("bundle_id") != bundle_value:
        raise FederatedTokenInvalid("federated token bundle does not match")
    if "data_bus" not in set(_as_list(claims.get("allowed_transports"))):
        raise FederatedTokenInvalid("federated token is not valid for Data Bus")

    jti = str(claims.get("jti") or "").strip()
    if not jti:
        raise FederatedTokenInvalid("federated token id is missing")
    stored_raw = await redis.get(_token_key(tenant=tenant_value, project=project_value, jti=jti))
    if isinstance(stored_raw, (bytes, bytearray)):
        stored_raw = stored_raw.decode("utf-8")
    if not stored_raw:
        raise FederatedTokenInvalid("federated token is not active")
    try:
        stored = json.loads(stored_raw)
    except Exception as exc:
        raise FederatedTokenInvalid("federated token record is invalid") from exc
    if stored.get("token_sha256") != _hash_token(token):
        raise FederatedTokenInvalid("federated token record does not match")

    session_id = str(claims.get("session_id") or "").strip()
    session = await session_manager.get_session_by_id(session_id)
    if session is None:
        raise FederatedTokenInvalid("federated token session is unavailable")
    return FederatedTokenVerification(session=session, claims=claims)


__all__ = [
    "FEDERATED_TOKEN_DEFAULT_TTL_SECONDS",
    "FEDERATED_TOKEN_MAX_TTL_SECONDS",
    "FEDERATED_TOKEN_SCHEMA",
    "FEDERATED_TOKEN_SECRET_KEY",
    "FederatedTokenError",
    "FederatedTokenExpired",
    "FederatedTokenGrant",
    "FederatedTokenInvalid",
    "FederatedTokenVerification",
    "issue_federated_data_bus_token",
    "verify_federated_data_bus_token",
]
