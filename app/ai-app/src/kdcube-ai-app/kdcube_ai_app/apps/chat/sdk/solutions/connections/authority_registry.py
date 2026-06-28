# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Connection Hub authority registry and credential envelope primitives.

Authorities are identity/grant realms. A runtime can verify a credential only
when the authority provider is reachable in that runtime. The local registry is
the fast in-process path; Redis discovery records make authority metadata
visible across ingress/proc boundaries without importing bundle code.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


CREDENTIAL_SCHEMA = "kdcube.credential.v1"
AUTHORITY_DISCOVERY_SCHEMA = "kdcube.authority.discovery.v1"

INGRESS_SESSION_AUTHORITY_ID = "kdcube.ingress_session"
INGRESS_SESSION_AUTHENTICATOR_ID = "kdcube.signed_active_record"
OAUTH_MCP_AUTHORITY_ID = "oauth_mcp"
OAUTH_MCP_AUTHENTICATOR_ID = "oauth_mcp.bearer"


def _str(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(item.strip() for item in value.replace(",", " ").split() if item.strip())
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _dict(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _key_part(value: Any) -> str:
    text = _str(value)
    return "".join(ch if ch.isalnum() or ch in {"-", "_", ".", "@", ":"} else "_" for ch in text) or "_"


@dataclass(frozen=True)
class CredentialEnvelope:
    """Canonical credential hints used by the authority selector.

    The envelope may be the token body itself or a nested claim inside an
    existing token format such as `kst1`. It is not authorization by itself; it
    only tells the SDK which authority/authenticator can attempt verification.
    """

    credential_id: str = ""
    credential_kind: str = ""
    issuer_authority_id: str = ""
    issuer_authenticator_id: str = ""
    subject: str = ""
    tenant: str = ""
    project: str = ""
    audience: str = ""
    session_id: str = ""
    verified_authority: dict[str, Any] = field(default_factory=dict)
    attrs: dict[str, Any] = field(default_factory=dict)
    iat: int = 0
    exp: int = 0
    schema: str = CREDENTIAL_SCHEMA

    @property
    def authority_id(self) -> str:
        return self.issuer_authority_id

    @property
    def authenticator_id(self) -> str:
        return self.issuer_authenticator_id

    def public_claim(self) -> dict[str, Any]:
        """Minimal safe subset suitable for embedding in another signed token."""

        return {
            "schema": self.schema,
            "credential_id": self.credential_id,
            "credential_kind": self.credential_kind,
            "issuer_authority_id": self.issuer_authority_id,
            "issuer_authenticator_id": self.issuer_authenticator_id,
            "subject": self.subject,
            "tenant": self.tenant,
            "project": self.project,
            "audience": self.audience,
            "session_id": self.session_id,
            "iat": self.iat,
            "exp": self.exp,
        }

    def to_dict(self) -> dict[str, Any]:
        out = self.public_claim()
        if self.verified_authority:
            out["verified_authority"] = dict(self.verified_authority)
        if self.attrs:
            out["attrs"] = dict(self.attrs)
        return out

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "CredentialEnvelope":
        data = dict(value or {})
        return cls(
            credential_id=_str(data.get("credential_id") or data.get("jti")),
            credential_kind=_str(data.get("credential_kind") or data.get("kind")),
            issuer_authority_id=_str(
                data.get("issuer_authority_id")
                or data.get("authority_id")
                or data.get("authority")
            ),
            issuer_authenticator_id=_str(
                data.get("issuer_authenticator_id")
                or data.get("authenticator_id")
                or data.get("authenticator")
            ),
            subject=_str(data.get("subject") or data.get("sub")),
            tenant=_str(data.get("tenant")),
            project=_str(data.get("project")),
            audience=_str(data.get("audience") or data.get("aud")),
            session_id=_str(data.get("session_id") or data.get("sid")),
            verified_authority=_dict(data.get("verified_authority")),
            attrs=_dict(data.get("attrs")),
            iat=int(data.get("iat") or 0),
            exp=int(data.get("exp") or 0),
            schema=_str(data.get("schema")) or CREDENTIAL_SCHEMA,
        )

    @classmethod
    def coerce(cls, value: Any) -> "CredentialEnvelope":
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            nested = value.get("credential")
            if isinstance(nested, Mapping):
                return cls.from_dict(nested)
            return cls.from_dict(value)
        return cls()


@dataclass(frozen=True)
class AuthorityProviderSpec:
    authority_id: str
    provider_id: str = ""
    bundle_id: str = ""
    label: str = ""
    credential_kinds: tuple[str, ...] = ()
    audiences: tuple[str, ...] = ()
    authenticators: tuple[str, ...] = ()
    transports: tuple[str, ...] = ("local",)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "authority_id": self.authority_id,
            "provider_id": self.provider_id or self.authority_id,
            "bundle_id": self.bundle_id,
            "label": self.label,
            "credential_kinds": list(self.credential_kinds),
            "audiences": list(self.audiences),
            "authenticators": list(self.authenticators),
            "transports": list(self.transports),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "AuthorityProviderSpec":
        data = dict(value or {})
        authority_id = _str(data.get("authority_id") or data.get("id") or data.get("authority"))
        return cls(
            authority_id=authority_id,
            provider_id=_str(data.get("provider_id") or data.get("provider") or authority_id),
            bundle_id=_str(data.get("bundle_id")),
            label=_str(data.get("label")),
            credential_kinds=_list(data.get("credential_kinds") or data.get("kinds")),
            audiences=_list(data.get("audiences") or data.get("audience")),
            authenticators=_list(data.get("authenticators") or data.get("authenticator_ids")),
            transports=_list(data.get("transports")) or ("local",),
            metadata=_dict(data.get("metadata")),
        )

    def matches(self, credential: CredentialEnvelope | Mapping[str, Any]) -> bool:
        env = CredentialEnvelope.coerce(credential)
        if self.authority_id and env.issuer_authority_id and self.authority_id != env.issuer_authority_id:
            return False
        if self.authenticators and env.issuer_authenticator_id and env.issuer_authenticator_id not in self.authenticators:
            return False
        if self.credential_kinds and env.credential_kind and env.credential_kind not in self.credential_kinds:
            return False
        if self.audiences and env.audience and env.audience not in self.audiences:
            return False
        return True


@dataclass(frozen=True)
class AuthorityResolution:
    ok: bool
    authority_id: str = ""
    authenticator_id: str = ""
    subject: str = ""
    actor_user_id: str = ""
    platform_user_id: str = ""
    roles: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    grants: tuple[str, ...] = ()
    credential: CredentialEnvelope = field(default_factory=CredentialEnvelope)
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "authority_id": self.authority_id,
            "authenticator_id": self.authenticator_id,
            "subject": self.subject,
            "actor_user_id": self.actor_user_id,
            "platform_user_id": self.platform_user_id,
            "roles": list(self.roles),
            "permissions": list(self.permissions),
            "grants": list(self.grants),
            "credential": self.credential.to_dict(),
            "metadata": dict(self.metadata),
            "error": self.error,
            "message": self.message,
        }


class AuthorityProvider(Protocol):
    spec: AuthorityProviderSpec

    async def verify_credential(
        self,
        credential: CredentialEnvelope | Mapping[str, Any],
        *,
        token: str = "",
        context: Mapping[str, Any] | None = None,
    ) -> AuthorityResolution:
        ...


@dataclass(frozen=True)
class RegisteredAuthorityProvider:
    spec: AuthorityProviderSpec
    provider: AuthorityProvider


class AuthorityRegistry:
    """Local in-process registry for authority providers."""

    def __init__(self) -> None:
        self._providers: dict[str, RegisteredAuthorityProvider] = {}

    def register(
        self,
        provider: AuthorityProvider,
        spec: AuthorityProviderSpec | Mapping[str, Any] | None = None,
    ) -> RegisteredAuthorityProvider:
        provider_spec = spec or getattr(provider, "spec", None)
        if provider_spec is None:
            raise ValueError("authority provider spec is required")
        if not isinstance(provider_spec, AuthorityProviderSpec):
            provider_spec = AuthorityProviderSpec.from_dict(provider_spec)
        if not provider_spec.authority_id:
            raise ValueError("authority_id is required")
        if provider_spec.authority_id in self._providers:
            raise ValueError(f"authority provider already registered: {provider_spec.authority_id}")
        entry = RegisteredAuthorityProvider(spec=provider_spec, provider=provider)
        self._providers[provider_spec.authority_id] = entry
        return entry

    def get(self, authority_id: str) -> RegisteredAuthorityProvider | None:
        return self._providers.get(_str(authority_id))

    def providers(self) -> list[RegisteredAuthorityProvider]:
        return list(self._providers.values())

    def resolve(self, credential: CredentialEnvelope | Mapping[str, Any]) -> RegisteredAuthorityProvider | None:
        env = CredentialEnvelope.coerce(credential)
        if env.issuer_authority_id:
            entry = self.get(env.issuer_authority_id)
            if entry and entry.spec.matches(env):
                return entry
            return None
        matches = [entry for entry in self._providers.values() if entry.spec.matches(env)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError("multiple authority providers match credential; include issuer_authority_id")
        return None

    async def verify(
        self,
        credential: CredentialEnvelope | Mapping[str, Any],
        *,
        token: str = "",
        context: Mapping[str, Any] | None = None,
    ) -> AuthorityResolution:
        env = CredentialEnvelope.coerce(credential)
        entry = self.resolve(env)
        if entry is None:
            return AuthorityResolution(
                ok=False,
                credential=env,
                authority_id=env.issuer_authority_id,
                authenticator_id=env.issuer_authenticator_id,
                error="authority_not_registered",
                message="No reachable authority provider can verify this credential.",
            )
        return await entry.provider.verify_credential(env, token=token, context=context)


class RedisAuthorityDiscovery:
    """Redis-backed authority spec discovery table for one tenant/project."""

    def __init__(self, redis: Any, *, tenant: str, project: str, ttl_seconds: int = 0) -> None:
        self.redis = redis
        self.tenant = _str(tenant)
        self.project = _str(project)
        self.ttl_seconds = max(0, int(ttl_seconds or 0))
        self._base = f"kdcube:authorities:{_key_part(self.tenant)}:{_key_part(self.project)}"

    def _authority_key(self, authority_id: str) -> str:
        return f"{self._base}:authority:{_key_part(authority_id)}"

    def _all_key(self) -> str:
        return f"{self._base}:authorities"

    async def register_provider(
        self,
        spec: AuthorityProviderSpec | Mapping[str, Any],
        *,
        ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        provider_spec = spec if isinstance(spec, AuthorityProviderSpec) else AuthorityProviderSpec.from_dict(spec)
        now = time.time()
        record = {
            "schema": AUTHORITY_DISCOVERY_SCHEMA,
            "spec": provider_spec.to_dict(),
            "registered_at": now,
            "expires_at": now + int(ttl_seconds or self.ttl_seconds or 0) if (ttl_seconds or self.ttl_seconds) else 0,
        }
        raw = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        key = self._authority_key(provider_spec.authority_id)
        ttl = int(ttl_seconds if ttl_seconds is not None else self.ttl_seconds)
        if ttl > 0:
            await self.redis.setex(key, ttl, raw)
        else:
            await self.redis.set(key, raw)
        await self.redis.sadd(self._all_key(), provider_spec.authority_id)
        return record

    async def list_providers(self) -> list[AuthorityProviderSpec]:
        raw_ids = await self.redis.smembers(self._all_key())
        ids = [
            item.decode("utf-8") if isinstance(item, (bytes, bytearray)) else str(item)
            for item in (raw_ids or [])
        ]
        specs: list[AuthorityProviderSpec] = []
        for authority_id in sorted(ids):
            raw = await self.redis.get(self._authority_key(authority_id))
            if raw is None:
                continue
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8")
            try:
                data = json.loads(raw)
            except Exception:
                continue
            if data.get("schema") != AUTHORITY_DISCOVERY_SCHEMA:
                continue
            specs.append(AuthorityProviderSpec.from_dict(data.get("spec") or {}))
        return specs


def authority_provider_spec_from_declaration(
    declaration: Any,
    *,
    bundle_id: str = "",
) -> AuthorityProviderSpec:
    """Convert a bundle manifest authority declaration to registry metadata."""

    authority_id = _str(getattr(declaration, "authority_id", None) or _dict(declaration).get("authority_id"))
    authenticator_id = _str(
        getattr(declaration, "authenticator_id", None) or _dict(declaration).get("authenticator_id")
    )
    credential_kinds = _list(
        getattr(declaration, "credential_kinds", None) or _dict(declaration).get("credential_kinds")
    )
    audiences = _list(getattr(declaration, "audiences", None) or _dict(declaration).get("audiences"))
    transports = _list(getattr(declaration, "transports", None) or _dict(declaration).get("transports")) or ("local",)
    label = _str(getattr(declaration, "label", None) or _dict(declaration).get("label"))
    return AuthorityProviderSpec(
        authority_id=authority_id,
        provider_id=authority_id,
        bundle_id=_str(bundle_id or _dict(declaration).get("bundle_id")),
        label=label,
        credential_kinds=credential_kinds,
        audiences=audiences,
        authenticators=(authenticator_id,) if authenticator_id else (),
        transports=transports,
        metadata={"source": "bundle_manifest"},
    )


__all__ = [
    "AUTHORITY_DISCOVERY_SCHEMA",
    "CREDENTIAL_SCHEMA",
    "INGRESS_SESSION_AUTHENTICATOR_ID",
    "INGRESS_SESSION_AUTHORITY_ID",
    "OAUTH_MCP_AUTHENTICATOR_ID",
    "OAUTH_MCP_AUTHORITY_ID",
    "AuthorityProvider",
    "AuthorityProviderSpec",
    "AuthorityRegistry",
    "AuthorityResolution",
    "CredentialEnvelope",
    "RedisAuthorityDiscovery",
    "RegisteredAuthorityProvider",
    "authority_provider_spec_from_declaration",
]
