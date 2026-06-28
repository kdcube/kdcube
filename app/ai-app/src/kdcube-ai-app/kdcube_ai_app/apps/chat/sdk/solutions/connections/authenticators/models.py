# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Request-authenticator SDK contract for Connection Hub.

Connection Hub owns provider-specific request interpretation. Callers pass a
normalized request envelope and receive an authority envelope; they should not
parse Telegram/Slack/API-key details themselves.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any, Mapping


def _str(value: Any) -> str:
    return str(value or "").strip()


def _clean_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key).lower(): str(item)
        for key, item in value.items()
        if str(key or "").strip() and item is not None
    }


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


@dataclass(frozen=True)
class RequestEnvelope:
    """Serializable request view used by Connection Hub authenticators.

    It intentionally contains request facts, not framework objects. Starlette,
    webhook workers, and tests can all produce the same shape.
    """

    method: str = "GET"
    path: str = ""
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    query: dict[str, str] = field(default_factory=dict)
    cookies: dict[str, str] = field(default_factory=dict)
    body_base64: str = ""
    body_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        out = {
            "method": self.method,
            "path": self.path,
            "url": self.url,
            "headers": dict(self.headers),
            "query": dict(self.query),
            "cookies": dict(self.cookies),
        }
        if self.body_base64:
            out["body_base64"] = self.body_base64
        if self.body_text:
            out["body_text"] = self.body_text
        return out

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "RequestEnvelope":
        data = dict(value or {})
        return cls(
            method=_str(data.get("method") or "GET").upper(),
            path=_str(data.get("path")),
            url=_str(data.get("url")),
            headers=_clean_mapping(data.get("headers")),
            query=_clean_mapping(data.get("query")),
            cookies=_clean_mapping(data.get("cookies")),
            body_base64=_str(data.get("body_base64")),
            body_text=_str(data.get("body_text")),
        )

    @classmethod
    def coerce(cls, value: Any) -> "RequestEnvelope":
        if isinstance(value, cls):
            return value
        return cls.from_dict(value if isinstance(value, Mapping) else {})

    @classmethod
    async def from_request(cls, request: Any, *, include_body: bool = False) -> "RequestEnvelope":
        """Build an envelope from a Starlette/FastAPI-like request object."""

        headers = _clean_mapping(getattr(request, "headers", {}) or {})
        query = _clean_mapping(getattr(request, "query_params", {}) or {})
        cookies = _clean_mapping(getattr(request, "cookies", {}) or {})
        method = _str(getattr(request, "method", "") or "GET").upper()
        url_obj = getattr(request, "url", None)
        path = _str(getattr(url_obj, "path", "") or getattr(request, "path", ""))
        url = _str(url_obj)
        body_base64 = ""
        body_text = ""
        if include_body:
            body = b""
            body_fn = getattr(request, "body", None)
            if callable(body_fn):
                body = await body_fn()
            if body:
                body_base64 = base64.b64encode(bytes(body)).decode("ascii")
                try:
                    body_text = bytes(body).decode("utf-8")
                except UnicodeDecodeError:
                    body_text = ""
        return cls(
            method=method,
            path=path,
            url=url,
            headers=headers,
            query=query,
            cookies=cookies,
            body_base64=body_base64,
            body_text=body_text,
        )

    def json_body(self) -> dict[str, Any]:
        if not self.body_text:
            return {}
        try:
            parsed = json.loads(self.body_text)
        except Exception:
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}


@dataclass(frozen=True)
class AuthenticatorRegistration:
    """Admin-configured authenticator metadata.

    Secrets are referenced by key; secret values do not belong in this record.
    """

    authenticator_id: str
    provider: str
    authority_id: str = ""
    integration_id: str = ""
    connection_id: str = ""
    label: str = ""
    enabled: bool = True
    role_providing: bool = False
    subject_namespace: str = ""
    secret_ref: str = ""
    selector: dict[str, Any] = field(default_factory=dict)
    verifier: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "authenticator_id": self.authenticator_id,
            "provider": self.provider,
            "authority_id": self.authority_id,
            "integration_id": self.integration_id or self.connection_id,
            "connection_id": self.connection_id,
            "label": self.label,
            "enabled": self.enabled,
            "role_providing": self.role_providing,
            "subject_namespace": self.subject_namespace,
            "secret_ref": self.secret_ref,
            "selector": dict(self.selector),
            "verifier": dict(self.verifier),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "AuthenticatorRegistration":
        data = dict(value or {})
        return cls(
            authenticator_id=_str(data.get("authenticator_id") or data.get("id")),
            provider=_str(data.get("provider")),
            authority_id=_str(
                data.get("authority_id")
                or data.get("authorityId")
                or data.get("authority")
                or data.get("issuer")
            ),
            integration_id=_str(data.get("integration_id") or data.get("integrationId")),
            connection_id=_str(
                data.get("connection_id")
                or data.get("connectionId")
                or data.get("integration_id")
                or data.get("integrationId")
            ),
            label=_str(data.get("label")),
            enabled=_bool(data.get("enabled"), default=True),
            role_providing=_bool(
                data.get("role_providing") if "role_providing" in data else data.get("roleProviding"),
                default=False,
            ),
            subject_namespace=_str(data.get("subject_namespace") or data.get("namespace")),
            secret_ref=_str(data.get("secret_ref") or data.get("secret")),
            selector=dict(data.get("selector") or {}) if isinstance(data.get("selector"), Mapping) else {},
            verifier=dict(data.get("verifier") or {}) if isinstance(data.get("verifier"), Mapping) else {},
        )

    @classmethod
    def coerce(cls, value: Any) -> "AuthenticatorRegistration":
        if isinstance(value, cls):
            return value
        return cls.from_dict(value if isinstance(value, Mapping) else {})


@dataclass(frozen=True)
class AuthenticatedRequest:
    """Result of authenticating a request through Connection Hub."""

    ok: bool
    authenticated: bool = False
    authority_id: str = ""
    identity_subject: str = ""
    linked: bool = False
    provider: str = ""
    provider_subject: str = ""
    selected_authenticator: str = ""
    integration_id: str = ""
    connection_id: str = ""
    actor_user_id: str = ""
    platform_user_id: str = ""
    identity_link: dict[str, Any] = field(default_factory=dict)
    principal: dict[str, Any] = field(default_factory=dict)
    identity_authority: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        integration_id = self.integration_id or self.connection_id
        return {
            "ok": self.ok,
            "authenticated": self.authenticated,
            "authority_id": self.authority_id,
            "identity_subject": self.identity_subject or self.provider_subject,
            "linked": self.linked,
            "provider": self.provider,
            "provider_subject": self.provider_subject,
            "selected_authenticator": self.selected_authenticator,
            "integration_id": integration_id,
            "connection_id": self.connection_id,
            "actor_user_id": self.actor_user_id,
            "platform_user_id": self.platform_user_id,
            "identity_link": dict(self.identity_link),
            "principal": dict(self.principal),
            "identity_authority": dict(self.identity_authority),
            "error": self.error,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "AuthenticatedRequest":
        data = dict(value or {})
        identity_authority = data.get("identity_authority") if isinstance(data.get("identity_authority"), Mapping) else {}
        return cls(
            ok=bool(data.get("ok")),
            authenticated=bool(data.get("authenticated")),
            authority_id=_str(
                data.get("authority_id")
                or data.get("authorityId")
                or data.get("authority")
                or identity_authority.get("authority_id")
                or identity_authority.get("authority")
            ),
            identity_subject=_str(
                data.get("identity_subject")
                or data.get("subject")
                or data.get("provider_subject")
                or data.get("sub")
            ),
            linked=bool(data.get("linked")),
            provider=_str(data.get("provider")),
            provider_subject=_str(data.get("provider_subject") or data.get("subject")),
            selected_authenticator=_str(data.get("selected_authenticator") or data.get("authenticator_id")),
            integration_id=_str(data.get("integration_id") or data.get("integrationId")),
            connection_id=_str(
                data.get("connection_id")
                or data.get("connectionId")
                or data.get("integration_id")
                or data.get("integrationId")
            ),
            actor_user_id=_str(data.get("actor_user_id")),
            platform_user_id=_str(data.get("platform_user_id")),
            identity_link=dict(data.get("identity_link") or {}) if isinstance(data.get("identity_link"), Mapping) else {},
            principal=dict(data.get("principal") or {}) if isinstance(data.get("principal"), Mapping) else {},
            identity_authority=dict(data.get("identity_authority") or {}) if isinstance(data.get("identity_authority"), Mapping) else {},
            error=_str(data.get("error")),
            message=_str(data.get("message")),
        )

    @classmethod
    def coerce(cls, value: Any) -> "AuthenticatedRequest":
        if isinstance(value, cls):
            return value
        return cls.from_dict(value if isinstance(value, Mapping) else {})


__all__ = [
    "AuthenticatedRequest",
    "AuthenticatorRegistration",
    "RequestEnvelope",
]
