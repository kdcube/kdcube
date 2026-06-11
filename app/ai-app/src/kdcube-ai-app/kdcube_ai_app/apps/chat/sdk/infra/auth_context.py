# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Mapping


PRINCIPAL_USER = "user"
PRINCIPAL_ANONYMOUS = "anonymous"
PRINCIPAL_JOB = "job"
PRINCIPAL_SYSTEM = "system"
PRINCIPAL_SERVICE = "service"
AUTH_CONTEXT_TOKEN_SCOPE = "kdcube.auth_context"

AUTH_CONTEXT_CV: ContextVar["AuthContext | None"] = ContextVar("AUTH_CONTEXT_CV", default=None)


def _get(value: Any, *names: str, default: Any = None) -> Any:
    if value is None:
        return default
    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value.get(name)
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return dict(model_dump())
    legacy_dict = getattr(value, "dict", None)
    if callable(legacy_dict):
        return dict(legacy_dict())
    result: dict[str, Any] = {}
    for name in dir(value):
        if name.startswith("_"):
            continue
        try:
            item = getattr(value, name)
        except Exception:
            continue
        if callable(item):
            continue
        result[name] = item
    return result


def _string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _tuple(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, Mapping):
        return tuple(
            str(key).strip()
            for key, enabled in value.items()
            if enabled and str(key).strip()
        )
    try:
        return tuple(str(item).strip() for item in value if str(item).strip())
    except TypeError:
        text = str(value).strip()
        return (text,) if text else ()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_json(data: Mapping[str, Any]) -> str:
    return _b64url(json.dumps(dict(data), sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _unb64url_json(data: str) -> dict[str, Any]:
    padded = data + ("=" * (-len(data) % 4))
    parsed = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("auth context token payload is invalid")
    return parsed


@dataclass(frozen=True)
class AuthContext:
    """Transport-neutral caller identity for SDK provider calls.

    The context can represent a browser/user request, a Data Bus actor, a
    bundle job, or a system caller. It is intentionally not tied to HTTP
    ingress so scheduled jobs and in-process bundle calls can use the same
    provider surface.
    """

    tenant: str = ""
    project: str = ""
    bundle_id: str | None = None
    principal_kind: str = PRINCIPAL_ANONYMOUS
    principal_id: str | None = None
    user_id: str | None = None
    user_type: str = PRINCIPAL_ANONYMOUS
    username: str | None = None
    email: str | None = None
    fingerprint: str | None = None
    roles: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    session_id: str | None = None
    conversation_id: str | None = None
    turn_id: str | None = None
    stream_id: str | None = None
    request_id: str | None = None
    source: str | None = None
    actor: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant", _string(self.tenant) or "")
        object.__setattr__(self, "project", _string(self.project) or "")
        object.__setattr__(self, "bundle_id", _string(self.bundle_id))
        object.__setattr__(self, "principal_kind", _string(self.principal_kind) or PRINCIPAL_ANONYMOUS)
        object.__setattr__(self, "principal_id", _string(self.principal_id))
        object.__setattr__(self, "user_id", _string(self.user_id))
        object.__setattr__(self, "user_type", _string(self.user_type) or PRINCIPAL_ANONYMOUS)
        object.__setattr__(self, "username", _string(self.username))
        object.__setattr__(self, "email", _string(self.email))
        object.__setattr__(self, "fingerprint", _string(self.fingerprint))
        object.__setattr__(self, "roles", _tuple(self.roles))
        object.__setattr__(self, "permissions", _tuple(self.permissions))
        object.__setattr__(self, "session_id", _string(self.session_id))
        object.__setattr__(self, "conversation_id", _string(self.conversation_id))
        object.__setattr__(self, "turn_id", _string(self.turn_id))
        object.__setattr__(self, "stream_id", _string(self.stream_id))
        object.__setattr__(self, "request_id", _string(self.request_id))
        object.__setattr__(self, "source", _string(self.source))
        object.__setattr__(self, "actor", dict(self.actor or {}))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    @property
    def is_user(self) -> bool:
        return self.principal_kind == PRINCIPAL_USER

    @property
    def is_headless(self) -> bool:
        return self.principal_kind in {PRINCIPAL_JOB, PRINCIPAL_SYSTEM, PRINCIPAL_SERVICE}

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any] | None,
        *,
        source: str | None = None,
        principal_kind: str | None = None,
    ) -> "AuthContext":
        data = dict(value or {})
        actor = _mapping(data.get("actor"))
        user = _mapping(data.get("user"))
        routing = _mapping(data.get("routing"))
        meta = _mapping(data.get("meta"))

        tenant = (
            _string(data.get("tenant"))
            or _string(data.get("tenant_id"))
            or _string(actor.get("tenant"))
            or _string(actor.get("tenant_id"))
        )
        project = (
            _string(data.get("project"))
            or _string(data.get("project_id"))
            or _string(actor.get("project"))
            or _string(actor.get("project_id"))
        )
        user_id = _string(data.get("user_id")) or _string(user.get("user_id")) or _string(actor.get("user_id"))
        fingerprint = (
            _string(data.get("fingerprint"))
            or _string(user.get("fingerprint"))
            or _string(actor.get("fingerprint"))
        )
        bundle_id = (
            _string(data.get("bundle_id"))
            or _string(routing.get("bundle_id"))
            or _string(actor.get("bundle_id"))
        )
        resolved_kind = principal_kind or _string(data.get("principal_kind")) or _string(actor.get("principal_kind"))
        if not resolved_kind:
            if user_id or fingerprint:
                resolved_kind = PRINCIPAL_USER
            elif bundle_id:
                resolved_kind = PRINCIPAL_SERVICE
            else:
                resolved_kind = PRINCIPAL_ANONYMOUS
        principal_id = (
            _string(data.get("principal_id"))
            or _string(actor.get("principal_id"))
            or user_id
            or fingerprint
            or bundle_id
        )

        return cls(
            tenant=tenant or "",
            project=project or "",
            bundle_id=bundle_id,
            principal_kind=resolved_kind,
            principal_id=principal_id,
            user_id=user_id,
            user_type=(
                _string(data.get("user_type"))
                or _string(user.get("user_type"))
                or _string(actor.get("user_type"))
                or PRINCIPAL_ANONYMOUS
            ),
            username=_string(data.get("username")) or _string(user.get("username")) or _string(actor.get("username")),
            email=_string(data.get("email")) or _string(user.get("email")) or _string(actor.get("email")),
            fingerprint=fingerprint,
            roles=_tuple(data.get("roles") or user.get("roles") or actor.get("roles")),
            permissions=_tuple(data.get("permissions") or user.get("permissions") or actor.get("permissions")),
            session_id=(
                _string(data.get("session_id"))
                or _string(routing.get("session_id"))
                or _string(actor.get("session_id"))
            ),
            conversation_id=_string(data.get("conversation_id")) or _string(routing.get("conversation_id")),
            turn_id=_string(data.get("turn_id")) or _string(routing.get("turn_id")),
            stream_id=_string(data.get("stream_id")) or _string(routing.get("stream_id")) or _string(actor.get("stream_id")),
            request_id=_string(data.get("request_id")) or _string(meta.get("task_id")),
            source=source or _string(data.get("source")),
            actor=actor or {
                key: item
                for key, item in data.items()
                if key
                in {
                    "tenant",
                    "tenant_id",
                    "project",
                    "project_id",
                    "bundle_id",
                    "principal_kind",
                    "principal_id",
                    "user_id",
                    "user_type",
                    "username",
                    "email",
                    "fingerprint",
                    "roles",
                    "permissions",
                    "session_id",
                    "stream_id",
                }
            },
            metadata=dict(data.get("metadata") or {}),
        )

    @classmethod
    def from_external_event_payload(
        cls,
        payload: Any,
        *,
        source: str | None = "external_event",
        principal_kind: str | None = None,
    ) -> "AuthContext":
        actor = _get(payload, "actor")
        user = _get(payload, "user")
        routing = _get(payload, "routing")
        meta = _get(payload, "meta")
        event = _get(payload, "event")
        data = {
            "actor": _mapping(actor),
            "user": _mapping(user),
            "routing": _mapping(routing),
            "meta": _mapping(meta),
            "bundle_id": _get(routing, "bundle_id"),
            "request_id": _get(meta, "task_id"),
            "metadata": {
                "event_kind": _get(event, "kind"),
                "event_type": _get(event, "type"),
                "event_source_id": _get(event, "event_source_id"),
            },
        }
        return cls.from_mapping(data, source=source, principal_kind=principal_kind)

    @classmethod
    def from_current_request_context(
        cls,
        *,
        source: str | None = "current_request",
        principal_kind: str | None = None,
    ) -> "AuthContext":
        bound = get_current_auth_context()
        if bound is not None:
            return bound

        from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import (
            get_current_bundle_id,
            get_current_request_context,
            get_current_user_identity,
        )

        payload = get_current_request_context()
        if payload is not None:
            return cls.from_external_event_payload(payload, source=source, principal_kind=principal_kind)

        identity = get_current_user_identity()
        if identity:
            return cls.from_mapping(identity, source=source, principal_kind=principal_kind)

        bundle_id = get_current_bundle_id()
        if bundle_id:
            return cls.for_service(tenant="", project="", service_id=bundle_id, bundle_id=bundle_id, source=source)
        return cls(source=source, principal_kind=principal_kind or PRINCIPAL_ANONYMOUS)

    @classmethod
    def from_data_bus_context(
        cls,
        ctx: Any,
        *,
        source: str | None = "data_bus",
        principal_kind: str | None = None,
    ) -> "AuthContext":
        actor = _mapping(_get(ctx, "actor"))
        actor.setdefault("tenant", _get(ctx, "tenant"))
        actor.setdefault("project", _get(ctx, "project"))
        actor.setdefault("bundle_id", _get(ctx, "bundle_id"))
        actor.setdefault("stream_id", _get(ctx, "stream_id"))
        return cls.from_mapping(actor, source=source, principal_kind=principal_kind)

    @classmethod
    def for_service(
        cls,
        *,
        tenant: str,
        project: str,
        service_id: str,
        bundle_id: str | None = None,
        source: str | None = "service",
        metadata: Mapping[str, Any] | None = None,
    ) -> "AuthContext":
        return cls(
            tenant=tenant,
            project=project,
            bundle_id=bundle_id or service_id,
            principal_kind=PRINCIPAL_SERVICE,
            principal_id=service_id,
            user_type=PRINCIPAL_SERVICE,
            source=source,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def for_bundle_job(
        cls,
        *,
        tenant: str,
        project: str,
        bundle_id: str,
        job_alias: str | None = None,
        on_behalf_of: "AuthContext | None" = None,
        source: str | None = "bundle_job",
        metadata: Mapping[str, Any] | None = None,
    ) -> "AuthContext":
        details = dict(metadata or {})
        if job_alias:
            details["job_alias"] = str(job_alias)
        if on_behalf_of is not None:
            data = on_behalf_of.to_dict()
            inherited_metadata = dict(data.get("metadata") or {})
            inherited_metadata.update(details)
            data.update(
                {
                    "tenant": tenant,
                    "project": project,
                    "bundle_id": bundle_id,
                    "source": source,
                    "metadata": inherited_metadata,
                }
            )
            return AuthContext.from_mapping(
                data,
                principal_kind=on_behalf_of.principal_kind,
                source=source,
            )
        principal_id = f"{bundle_id}:{job_alias}" if job_alias else bundle_id
        return cls(
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
            principal_kind=PRINCIPAL_JOB,
            principal_id=principal_id,
            user_type=PRINCIPAL_SERVICE,
            source=source,
            metadata=details,
        )

    @classmethod
    def for_system(
        cls,
        *,
        tenant: str = "",
        project: str = "",
        bundle_id: str | None = None,
        source: str | None = "system",
        metadata: Mapping[str, Any] | None = None,
    ) -> "AuthContext":
        return cls(
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
            principal_kind=PRINCIPAL_SYSTEM,
            principal_id=bundle_id or PRINCIPAL_SYSTEM,
            user_type=PRINCIPAL_SERVICE,
            source=source,
            metadata=dict(metadata or {}),
        )

    def with_metadata(self, **metadata: Any) -> "AuthContext":
        data = self.to_dict()
        merged = dict(data.get("metadata") or {})
        merged.update(metadata)
        data["metadata"] = merged
        return AuthContext.from_mapping(data, principal_kind=self.principal_kind, source=self.source)

    def to_actor(self) -> dict[str, Any]:
        actor = dict(self.actor or {})
        actor.update(
            {
                "tenant": self.tenant,
                "project": self.project,
                "bundle_id": self.bundle_id,
                "principal_kind": self.principal_kind,
                "principal_id": self.principal_id,
                "user_type": self.user_type,
                "user_id": self.user_id,
                "username": self.username,
                "email": self.email,
                "fingerprint": self.fingerprint,
                "roles": list(self.roles or ()),
                "permissions": list(self.permissions or ()),
                "session_id": self.session_id,
                "stream_id": self.stream_id,
            }
        )
        return {key: value for key, value in actor.items() if value not in (None, "", [], {})}

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant": self.tenant,
            "project": self.project,
            "bundle_id": self.bundle_id,
            "principal_kind": self.principal_kind,
            "principal_id": self.principal_id,
            "user_id": self.user_id,
            "user_type": self.user_type,
            "username": self.username,
            "email": self.email,
            "fingerprint": self.fingerprint,
            "roles": list(self.roles or ()),
            "permissions": list(self.permissions or ()),
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "stream_id": self.stream_id,
            "request_id": self.request_id,
            "source": self.source,
            "actor": dict(self.actor or {}),
            "metadata": dict(self.metadata or {}),
        }

    def to_token_claims(
        self,
        *,
        scope: str = AUTH_CONTEXT_TOKEN_SCOPE,
        audience: str | None = None,
        expires_at: int | None = None,
        issued_at: int | None = None,
        token_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = int(issued_at or time.time())
        auth = self.to_dict()
        if metadata:
            auth_metadata = dict(auth.get("metadata") or {})
            auth_metadata.update(dict(metadata))
            auth["metadata"] = auth_metadata
        claims = {
            "v": 1,
            "scope": str(scope or AUTH_CONTEXT_TOKEN_SCOPE),
            "auth": auth,
            "iat": now,
            "exp": int(expires_at or now + 900),
        }
        if audience:
            claims["aud"] = str(audience)
        if token_id:
            claims["jti"] = str(token_id)
        return claims


def get_current_auth_context() -> AuthContext | None:
    return AUTH_CONTEXT_CV.get()


@contextmanager
def bind_auth_context(auth_context: AuthContext | None):
    token = AUTH_CONTEXT_CV.set(auth_context)
    try:
        yield auth_context
    finally:
        AUTH_CONTEXT_CV.reset(token)


def sign_auth_context_token(
    auth_context: AuthContext,
    *,
    secret: str,
    ttl_seconds: int = 900,
    scope: str = AUTH_CONTEXT_TOKEN_SCOPE,
    audience: str | None = None,
    token_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> str:
    raw_secret = str(secret or "").strip()
    if not raw_secret:
        raise ValueError("auth context token secret is not configured")
    now = int(time.time())
    ttl = max(1, int(ttl_seconds or 900))
    claims = auth_context.to_token_claims(
        scope=scope,
        audience=audience,
        issued_at=now,
        expires_at=now + ttl,
        token_id=token_id,
        metadata=metadata,
    )
    encoded = _b64url_json(claims)
    sig = hmac.new(raw_secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{encoded}.{sig}"


def verify_auth_context_token(
    token: str,
    *,
    secret: str,
    scope: str = AUTH_CONTEXT_TOKEN_SCOPE,
    audience: str | None = None,
    now: int | None = None,
) -> AuthContext:
    raw_secret = str(secret or "").strip()
    if not raw_secret:
        raise ValueError("auth context token secret is not configured")
    raw_token = str(token or "").strip()
    if "." not in raw_token:
        raise ValueError("auth context token is invalid")
    encoded, received_sig = raw_token.rsplit(".", 1)
    expected = hmac.new(raw_secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(received_sig, expected):
        raise ValueError("auth context token signature is invalid")
    claims = _unb64url_json(encoded)
    if str(claims.get("scope") or "") != str(scope or AUTH_CONTEXT_TOKEN_SCOPE):
        raise ValueError("auth context token scope is invalid")
    if audience is not None and str(claims.get("aud") or "") != str(audience):
        raise ValueError("auth context token audience is invalid")
    if int(claims.get("exp") or 0) < int(now or time.time()):
        raise ValueError("auth context token expired")
    auth_payload = claims.get("auth")
    if not isinstance(auth_payload, Mapping):
        raise ValueError("auth context token auth payload is invalid")
    return AuthContext.from_mapping(auth_payload)
