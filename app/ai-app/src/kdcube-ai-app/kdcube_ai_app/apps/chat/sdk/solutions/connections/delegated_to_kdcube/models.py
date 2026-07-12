# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Delegated-to-KDCube data models.

These models are intentionally storage- and transport-neutral. They describe the
Connection Hub-owned contract for external provider accounts connected by a
platform user. Raw provider credentials are not part of browser/MCP-facing
models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping


CONNECTION_HUB_BUNDLE_ID = "connection-hub@1-0"
STATUS_CONNECTED = "connected"
STATUS_REVOKED = "revoked"

# ── Resolution reasons ──────────────────────────────────────────────────────
# Minted by the broker and carried VERBATIM through consent payloads into tool
# envelopes and named-service errors. Each is a distinct user situation with a
# distinct fix; do not collapse them.
REASON_CONNECT_REQUIRED = "connect_required"          # no eligible connected account
REASON_CLAIM_UPGRADE_REQUIRED = "claim_upgrade_required"  # account exists, claim not approved
REASON_RECONNECT_REQUIRED = "reconnect_required"      # credential missing/unrefreshable/rejected
REASON_ACCOUNT_REQUIRED = "account_required"          # several eligible accounts; pick one

# Reasons a USER can fix in Connection Hub (retryable after the fix). Operator
# config errors (claim_not_configured, connector_app_not_configured,
# claim_outside_connector_app) are deliberately not in this set.
USER_ACTIONABLE_REASONS = frozenset(
    {
        REASON_CONNECT_REQUIRED,
        REASON_CLAIM_UPGRADE_REQUIRED,
        REASON_RECONNECT_REQUIRED,
        REASON_ACCOUNT_REQUIRED,
    }
)

# ── Credential health vocabulary ────────────────────────────────────────────
# Shown by Connection Hub per connected account; persisted on transitions
# (refresh failure, live provider rejection) and computed from credential
# expiry otherwise.
CREDENTIAL_ACTIVE = "active"
CREDENTIAL_EXPIRES_SOON = "expires_soon"
CREDENTIAL_REFRESHABLE = "refreshable"
CREDENTIAL_RECONNECT_REQUIRED = "reconnect_required"
CREDENTIAL_MISSING = "missing"
CREDENTIAL_REVOKED = "revoked"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def as_str(value: Any) -> str:
    return str(value or "").strip()


def as_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = as_str(value).lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def as_str_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(part.strip() for part in value.replace(",", " ").split() if part.strip())
    if isinstance(value, (list, tuple, set)):
        return tuple(as_str(part) for part in value if as_str(part))
    return ()


def as_dict(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


@dataclass(frozen=True)
class ProviderClaim:
    """One KDCube-normalized claim exposed by an external provider account."""

    claim_id: str
    label: str = ""
    description: str = ""
    provider_scopes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "label": self.label,
            "description": self.description,
            "provider_scopes": list(self.provider_scopes),
        }

    @classmethod
    def from_config(cls, claim_id: str, value: Any) -> "ProviderClaim":
        data = as_dict(value)
        return cls(
            claim_id=as_str(claim_id),
            label=as_str(data.get("label")),
            description=as_str(data.get("description")),
            provider_scopes=as_str_list(data.get("provider_scopes") or data.get("scopes")),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProviderClaim":
        data = dict(value or {})
        return cls(
            claim_id=as_str(data.get("claim_id") or data.get("id")),
            label=as_str(data.get("label")),
            description=as_str(data.get("description")),
            provider_scopes=as_str_list(data.get("provider_scopes") or data.get("scopes")),
        )


@dataclass(frozen=True)
class ConnectorApp:
    """Admin-configured connector application for one provider."""

    connector_app_id: str
    provider_id: str
    label: str = ""
    enabled: bool = True
    client_id: str = ""
    client_secret_ref: str = ""
    redirect_uri: str = ""
    allowed_claims: tuple[str, ...] = ()

    def to_dict(self, *, include_client_id: bool = False, include_secret_refs: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "connector_app_id": self.connector_app_id,
            "provider_id": self.provider_id,
            "label": self.label,
            "enabled": self.enabled,
            "redirect_uri": self.redirect_uri,
            "allowed_claims": list(self.allowed_claims),
        }
        if include_client_id:
            data["client_id"] = self.client_id
        if include_secret_refs and self.client_secret_ref:
            data["client_secret_ref"] = self.client_secret_ref
        return data

    @classmethod
    def from_config(cls, provider_id: str, connector_app_id: str, value: Any) -> "ConnectorApp":
        data = as_dict(value)
        return cls(
            connector_app_id=as_str(connector_app_id),
            provider_id=as_str(provider_id),
            label=as_str(data.get("label")),
            enabled=as_bool(data.get("enabled"), default=True),
            client_id=as_str(data.get("client_id")),
            client_secret_ref=as_str(data.get("client_secret_ref")),
            redirect_uri=as_str(data.get("redirect_uri")),
            allowed_claims=as_str_list(data.get("allowed_claims") or data.get("claims")),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ConnectorApp":
        data = dict(value or {})
        return cls(
            connector_app_id=as_str(data.get("connector_app_id")),
            provider_id=as_str(data.get("provider_id")),
            label=as_str(data.get("label")),
            enabled=as_bool(data.get("enabled"), default=True),
            client_id=as_str(data.get("client_id")),
            client_secret_ref=as_str(data.get("client_secret_ref")),
            redirect_uri=as_str(data.get("redirect_uri")),
            allowed_claims=as_str_list(data.get("allowed_claims") or data.get("claims")),
        )


@dataclass(frozen=True)
class IntegrationProvider:
    """Connection Hub provider registry row."""

    provider_id: str
    label: str = ""
    adapter: str = ""
    enabled: bool = True
    oauth: dict[str, Any] = field(default_factory=dict)
    adapter_config: dict[str, Any] = field(default_factory=dict)
    claims: dict[str, ProviderClaim] = field(default_factory=dict)
    connector_apps: dict[str, ConnectorApp] = field(default_factory=dict)

    def to_dict(self, *, include_client_ids: bool = False, include_secret_refs: bool = False) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "label": self.label,
            "adapter": self.adapter,
            "enabled": self.enabled,
            "oauth": dict(self.oauth or {}),
            "claims": {
                key: value.to_dict() for key, value in sorted(self.claims.items())
            },
            "connector_apps": {
                key: value.to_dict(include_client_id=include_client_ids, include_secret_refs=include_secret_refs)
                for key, value in sorted(self.connector_apps.items())
            },
        }

    @classmethod
    def from_config(cls, provider_id: str, value: Any) -> "IntegrationProvider":
        data = as_dict(value)
        claims = {
            as_str(key): ProviderClaim.from_config(as_str(key), raw)
            for key, raw in as_dict(data.get("claims")).items()
            if as_str(key)
        }
        apps = {
            as_str(key): ConnectorApp.from_config(provider_id, as_str(key), raw)
            for key, raw in as_dict(data.get("connector_apps")).items()
            if as_str(key)
        }
        return cls(
            provider_id=as_str(provider_id),
            label=as_str(data.get("label")),
            adapter=as_str(data.get("adapter") or data.get("adapter_id")),
            enabled=as_bool(data.get("enabled"), default=True),
            oauth=as_dict(data.get("oauth")),
            adapter_config=as_dict(data.get("adapter_config") or data.get("adapter_settings") or data.get("settings")),
            claims=claims,
            connector_apps=apps,
        )


@dataclass(frozen=True)
class ToolClaimRequirement:
    """Provider claims one KDCube tool needs from a connected account."""

    provider_id: str
    connector_app_id: str = ""
    claims: tuple[str, ...] = ()
    account_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "provider_id": self.provider_id,
            "claims": list(self.claims),
        }
        if self.connector_app_id:
            data["connector_app_id"] = self.connector_app_id
        if self.account_id:
            data["account_id"] = self.account_id
        return data

    @classmethod
    def from_config(cls, value: Any) -> "ToolClaimRequirement":
        data = as_dict(value)
        return cls(
            provider_id=as_str(data.get("provider_id")),
            connector_app_id=as_str(data.get("connector_app_id")),
            claims=as_str_list(data.get("claims")),
            account_id=as_str(data.get("account_id")),
        )


@dataclass(frozen=True)
class ToolClaimPolicy:
    """Configured connected-account claim requirements for one KDCube tool."""

    tool_name: str
    label: str = ""
    description: str = ""
    connected_accounts: tuple[ToolClaimRequirement, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "label": self.label,
            "description": self.description,
            "connected_accounts": [item.to_dict() for item in self.connected_accounts],
        }

    @classmethod
    def from_config(cls, tool_name: str, value: Any) -> "ToolClaimPolicy":
        data = as_dict(value)
        raw_requirements = data.get("connected_accounts") or []
        if not isinstance(raw_requirements, (list, tuple)):
            raw_requirements = []
        requirements = tuple(
            item
            for item in (
                ToolClaimRequirement.from_config(raw)
                for raw in raw_requirements
            )
            if item.provider_id and item.claims
        )
        return cls(
            tool_name=as_str(tool_name),
            label=as_str(data.get("label")),
            description=as_str(data.get("description")),
            connected_accounts=requirements,
        )

    @classmethod
    def from_tool_config(cls, tool_name: str, value: Any) -> "ToolClaimPolicy":
        """Parse a whole application tool config or its delegated-to-KDCube block."""
        data = as_dict(value)
        connections = as_dict(data.get("connections"))
        delegated = as_dict(connections.get("delegated_to_kdcube"))
        return cls.from_config(tool_name, delegated or data)


@dataclass(frozen=True)
class DelegatedToKdcubeConfig:
    """Parsed Connection Hub delegated-to-kdcube config."""

    enabled: bool = False
    providers: dict[str, IntegrationProvider] = field(default_factory=dict)

    def provider(self, provider_id: str) -> IntegrationProvider | None:
        return self.providers.get(as_str(provider_id))

    def to_dict(self, *, include_client_ids: bool = False, include_secret_refs: bool = False) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "providers": {
                key: value.to_dict(include_client_ids=include_client_ids, include_secret_refs=include_secret_refs)
                for key, value in sorted(self.providers.items())
            },
        }

    @classmethod
    def from_config(cls, value: Any) -> "DelegatedToKdcubeConfig":
        data = as_dict(value)
        providers = {
            as_str(key): IntegrationProvider.from_config(as_str(key), raw)
            for key, raw in as_dict(data.get("providers")).items()
            if as_str(key)
        }
        return cls(enabled=as_bool(data.get("enabled"), default=bool(providers)), providers=providers)


@dataclass(frozen=True)
class ConnectedAccount:
    """One external provider account connected by a platform user."""

    account_id: str
    provider_id: str
    connector_app_id: str = ""
    external_subject: str = ""
    display_name: str = ""
    email: str = ""
    workspace: str = ""
    claims: tuple[str, ...] = ()
    credential_id: str = ""
    status: str = STATUS_CONNECTED
    connected_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def connected(self) -> bool:
        return self.status == STATUS_CONNECTED and bool(self.credential_id)

    def allows(self, claim: str) -> bool:
        return as_str(claim) in set(self.claims)

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "provider_id": self.provider_id,
            "connector_app_id": self.connector_app_id,
            "external_subject": self.external_subject,
            "display_name": self.display_name,
            "email": self.email,
            "workspace": self.workspace,
            "claims": list(self.claims),
            "credential_id": self.credential_id,
            "status": self.status,
            "connected_at": self.connected_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata or {}),
        }

    def public_dict(self) -> dict[str, Any]:
        data = self.to_dict()
        data["has_credential"] = bool(data.pop("credential_id", ""))
        return data

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ConnectedAccount":
        data = dict(value or {})
        return cls(
            account_id=as_str(data.get("account_id")),
            provider_id=as_str(data.get("provider_id") or data.get("provider")),
            connector_app_id=as_str(data.get("connector_app_id")),
            external_subject=as_str(data.get("external_subject")),
            display_name=as_str(data.get("display_name")),
            email=as_str(data.get("email")),
            workspace=as_str(data.get("workspace")),
            claims=as_str_list(data.get("claims")),
            credential_id=as_str(data.get("credential_id")),
            status=as_str(data.get("status")) or STATUS_CONNECTED,
            connected_at=as_str(data.get("connected_at")),
            updated_at=as_str(data.get("updated_at")),
            metadata=as_dict(data.get("metadata")),
        )


def account_choice(account: "ConnectedAccount") -> dict[str, Any]:
    """Public, labeled summary of one account for choice lists.

    This is what `account_required` candidates carry so chat/MCP clients can
    render a real selection ("NestLogic — T2AH06VEC"), never bare ids.
    """
    return {
        "account_id": account.account_id,
        "label": account.display_name or account.email or account.workspace or account.account_id,
        "email": account.email,
        "workspace": account.workspace,
        "status": account.status,
        "claims": list(account.claims),
    }


@dataclass(frozen=True)
class CredentialHandle:
    """Server-side result that authorizes provider use for one connected account."""

    provider_id: str
    account_id: str
    credential_id: str
    claims: tuple[str, ...]
    credential: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, include_credential: bool = False) -> dict[str, Any]:
        data = {
            "provider_id": self.provider_id,
            "account_id": self.account_id,
            "credential_id": self.credential_id,
            "claims": list(self.claims),
            "has_credential": bool(self.credential),
        }
        if include_credential:
            data["credential"] = dict(self.credential or {})
        return data


@dataclass(frozen=True)
class ClaimResolution:
    """Result of broker.ensure_claim.

    ``error`` carries one of the REASON_* constants (or an operator config
    error). ``candidates`` carries labeled account summaries (see
    ``account_choice``), never bare ids. ``retry_hint`` says whether retrying
    the same operation after the user completes the Connection Hub action
    should succeed.
    """

    ok: bool
    provider_id: str
    claim: str
    connector_app_id: str = ""
    account_id: str = ""
    credential: CredentialHandle | None = None
    error: str = ""
    message: str = ""
    connect_url: str = ""
    candidates: tuple[dict[str, Any], ...] = ()
    retry_hint: bool = False

    @property
    def consent_required(self) -> bool:
        return self.error in USER_ACTIONABLE_REASONS

    def to_dict(self, *, include_credential: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "ok": self.ok,
            "provider_id": self.provider_id,
            "claim": self.claim,
            "connector_app_id": self.connector_app_id,
            "account_id": self.account_id,
        }
        if self.credential is not None:
            data["credential"] = self.credential.to_dict(include_credential=include_credential)
        if self.error:
            data["error"] = self.error
        if self.message:
            data["message"] = self.message
        if self.connect_url:
            data["connect_url"] = self.connect_url
        if self.candidates:
            data["candidates"] = [dict(item) for item in self.candidates]
        data["retry_hint"] = self.retry_hint
        return data


__all__ = [
    "CONNECTION_HUB_BUNDLE_ID",
    "STATUS_CONNECTED",
    "STATUS_REVOKED",
    "REASON_CONNECT_REQUIRED",
    "REASON_CLAIM_UPGRADE_REQUIRED",
    "REASON_RECONNECT_REQUIRED",
    "REASON_ACCOUNT_REQUIRED",
    "USER_ACTIONABLE_REASONS",
    "CREDENTIAL_ACTIVE",
    "CREDENTIAL_EXPIRES_SOON",
    "CREDENTIAL_REFRESHABLE",
    "CREDENTIAL_RECONNECT_REQUIRED",
    "CREDENTIAL_MISSING",
    "CREDENTIAL_REVOKED",
    "account_choice",
    "ProviderClaim",
    "ConnectorApp",
    "IntegrationProvider",
    "ToolClaimRequirement",
    "ToolClaimPolicy",
    "DelegatedToKdcubeConfig",
    "ConnectedAccount",
    "CredentialHandle",
    "ClaimResolution",
    "as_bool",
    "as_dict",
    "as_str",
    "as_str_list",
    "utc_now",
]
