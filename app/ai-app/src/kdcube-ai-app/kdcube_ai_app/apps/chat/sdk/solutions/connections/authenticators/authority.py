# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Authority-provider primitives for Connection Hub request auth.

The selector chooses authenticators. Authenticators return identities under an
authority. Surface guards then resolve grants under the required authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence, TypeVar

from .models import AuthenticatorRegistration, RequestEnvelope


HEADER_AUTHORITY_ID = "x-kdcube-auth-authority-id"
HEADER_AUTHENTICATOR_ID = "x-kdcube-auth-authenticator-id"
HEADER_INTEGRATION_ID = "x-kdcube-auth-integration-id"
HEADER_CONNECTION_ID = "x-kdcube-auth-connection-id"
HEADER_PROVIDER = "x-kdcube-auth-provider"


def _str(value: Any) -> str:
    return str(value or "").strip()


def _str_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(part.strip() for part in value.replace(",", " ").split() if part.strip())
    if isinstance(value, (list, tuple, set)):
        return tuple(str(part).strip() for part in value if str(part).strip())
    return ()


def _field(value: Any, name: str, default: Any = "") -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


@dataclass(frozen=True)
class AuthRequestHints:
    """Non-trusted selector hints extracted from a request envelope.

    Hints narrow authenticator candidates. They do not prove authority or
    identity; only the selected authenticator can do that.
    """

    authority_id: str = ""
    authenticator_id: str = ""
    integration_id: str = ""
    connection_id: str = ""
    provider: str = ""

    @classmethod
    def from_envelope(cls, envelope: RequestEnvelope | Mapping[str, Any]) -> "AuthRequestHints":
        env = RequestEnvelope.coerce(envelope)
        headers = env.headers
        query = env.query
        return cls(
            authority_id=_str(
                headers.get(HEADER_AUTHORITY_ID)
                or query.get("authority_id")
                or query.get("authority")
            ),
            authenticator_id=_str(
                headers.get(HEADER_AUTHENTICATOR_ID)
                or query.get("authenticator_id")
                or query.get("authenticator")
            ),
            integration_id=_str(
                headers.get(HEADER_INTEGRATION_ID)
                or query.get("integration_id")
                or query.get("integration")
            ),
            connection_id=_str(
                headers.get(HEADER_CONNECTION_ID)
                or query.get("connection_id")
                or query.get("connection")
            ),
            provider=_str(
                headers.get(HEADER_PROVIDER)
                or query.get("provider")
            ),
        )

    @classmethod
    def coerce(cls, value: Any) -> "AuthRequestHints":
        if isinstance(value, cls):
            return value
        if isinstance(value, RequestEnvelope):
            return cls.from_envelope(value)
        if isinstance(value, Mapping):
            if "headers" in value or "query" in value:
                return cls.from_envelope(value)
            return cls(
                authority_id=_str(value.get("authority_id") or value.get("authority")),
                authenticator_id=_str(value.get("authenticator_id") or value.get("authenticator")),
                integration_id=_str(value.get("integration_id") or value.get("integration")),
                connection_id=_str(value.get("connection_id") or value.get("connection")),
                provider=_str(value.get("provider")),
            )
        return cls()

    def to_dict(self) -> dict[str, str]:
        return {
            "authority_id": self.authority_id,
            "authenticator_id": self.authenticator_id,
            "integration_id": self.integration_id,
            "connection_id": self.connection_id,
            "provider": self.provider,
        }

    @property
    def has_explicit_selector(self) -> bool:
        return bool(self.authenticator_id or self.connection_id or self.integration_id or self.authority_id)


@dataclass(frozen=True)
class AuthorityIdentity:
    """Identity verified under one authority provider."""

    authority_id: str
    subject: str
    ref: str = ""
    label: str = ""
    attrs: dict[str, Any] = field(default_factory=dict)

    @property
    def canonical_ref(self) -> str:
        if self.ref:
            return self.ref
        authority = _str(self.authority_id)
        subject = _str(self.subject)
        return f"{authority}:{subject}" if authority and subject else subject

    def to_dict(self) -> dict[str, Any]:
        return {
            "authority_id": self.authority_id,
            "subject": self.subject,
            "ref": self.canonical_ref,
            "label": self.label,
            "attrs": dict(self.attrs),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "AuthorityIdentity":
        data = dict(value or {})
        return cls(
            authority_id=_str(data.get("authority_id") or data.get("authority")),
            subject=_str(data.get("subject") or data.get("provider_subject") or data.get("sub")),
            ref=_str(data.get("ref") or data.get("identity_ref")),
            label=_str(data.get("label") or data.get("name")),
            attrs=dict(data.get("attrs") or {}) if isinstance(data.get("attrs"), Mapping) else {},
        )

    @classmethod
    def coerce(cls, value: Any) -> "AuthorityIdentity":
        if isinstance(value, cls):
            return value
        return cls.from_dict(value if isinstance(value, Mapping) else {})


@dataclass(frozen=True)
class SurfaceGuardRequirement:
    """Authority/grant requirement declared by a protected surface."""

    required_authority: str = "kdcube.platform"
    required_grants: tuple[str, ...] = ()
    accepted_authorities: tuple[str, ...] = ()
    accepted_authenticators: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "required_authority": self.required_authority,
            "required_grants": list(self.required_grants),
            "accepted_authorities": list(self.accepted_authorities),
            "accepted_authenticators": list(self.accepted_authenticators),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "SurfaceGuardRequirement":
        data = dict(value or {})
        return cls(
            required_authority=_str(data.get("required_authority") or data.get("authority")) or "kdcube.platform",
            required_grants=_str_list(data.get("required_grants") or data.get("grants")),
            accepted_authorities=_str_list(data.get("accepted_authorities") or data.get("authority_ids")),
            accepted_authenticators=_str_list(data.get("accepted_authenticators") or data.get("authenticator_ids")),
        )

    @classmethod
    def coerce(cls, value: Any) -> "SurfaceGuardRequirement":
        if isinstance(value, cls):
            return value
        return cls.from_dict(value if isinstance(value, Mapping) else {})


T = TypeVar("T")


def select_authenticator_candidates(
    registrations: Iterable[T],
    hints: AuthRequestHints | RequestEnvelope | Mapping[str, Any] | None = None,
    *,
    surface: SurfaceGuardRequirement | Mapping[str, Any] | None = None,
) -> list[T]:
    """Select enabled authenticator candidates.

    Selection order is intentionally deterministic. Hints are narrowing inputs,
    not trusted authorization facts.
    """

    request_hints = AuthRequestHints.coerce(hints or {})
    requirement = SurfaceGuardRequirement.coerce(surface or {})
    enabled: list[T] = [row for row in registrations if bool(_field(row, "enabled", True))]

    if requirement.accepted_authenticators:
        accepted = set(requirement.accepted_authenticators)
        enabled = [row for row in enabled if _str(_field(row, "authenticator_id")) in accepted]

    if requirement.accepted_authorities:
        accepted_authorities = set(requirement.accepted_authorities)
        enabled = [row for row in enabled if _str(_field(row, "authority_id")) in accepted_authorities]

    if request_hints.authenticator_id:
        return [
            row
            for row in enabled
            if _str(_field(row, "authenticator_id")) == request_hints.authenticator_id
        ]

    narrowed = enabled
    if request_hints.authority_id:
        narrowed = [
            row
            for row in narrowed
            if _str(_field(row, "authority_id")) == request_hints.authority_id
        ]

    connection_hint = request_hints.connection_id or request_hints.integration_id
    if connection_hint:
        narrowed = [
            row
            for row in narrowed
            if connection_hint
            in {
                _str(_field(row, "connection_id")),
                _str(_field(row, "integration_id")),
            }
        ]

    if request_hints.authority_id or connection_hint:
        return narrowed

    if request_hints.provider:
        return [
            row
            for row in narrowed
            if _str(_field(row, "provider")) == request_hints.provider
        ]

    return narrowed


__all__ = [
    "AuthRequestHints",
    "AuthorityIdentity",
    "HEADER_AUTHENTICATOR_ID",
    "HEADER_AUTHORITY_ID",
    "HEADER_CONNECTION_ID",
    "HEADER_INTEGRATION_ID",
    "HEADER_PROVIDER",
    "SurfaceGuardRequirement",
    "select_authenticator_candidates",
]
