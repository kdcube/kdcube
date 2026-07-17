# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Public `connections` named-service contract.

Transport-agnostic operation names, the operation map for the provider spec,
and the frozen dataclasses that round-trip the named-service ``payload``/``ret``.

No storage choice lives here. A bundle implements the abstract hooks in
``provider.ConnectionsProviderBase`` and picks its own storage. The contract is
consumed by other bundles through ``client.ConnectionsClient`` over either the
local (in-process) or API (HTTP) transport.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceOperationSpec,
    TRANSPORT_LOCAL,
)

NAMESPACE = "connections"

# ── operation string constants ──────────────────────────────────────────────
CONNECTION_CATALOG = "connection.catalog"
CONNECTION_STATUS = "connection.status"
CONNECTION_GET_TOKEN = "connection.get_token"
CONNECTION_DISCONNECT = "connection.disconnect"
OAUTH_START = "oauth.start"
# A hosted agent (a "Delegated By KDCube" entity) fetching the CONSENTED bearer of
# its own per-agent delegated grant — the token the user's grant already bound, so
# a KDCube @mcp guard passes. Returns no token when consent is pending. This is the
# per-turn read a bundle makes; the GRANT itself is created by the user in the
# Connection Hub (the "Delegated by KDCube" tab), not through this op.
AGENT_GRANT_GET_TOKEN = "agent_grant.get_token"

CONNECTION_OPERATIONS = (
    CONNECTION_CATALOG,
    CONNECTION_STATUS,
    CONNECTION_GET_TOKEN,
    CONNECTION_DISCONNECT,
    OAUTH_START,
    AGENT_GRANT_GET_TOKEN,
)


def build_connection_operations(
    transports: Sequence[str] = (TRANSPORT_LOCAL,),
) -> dict[str, NamedServiceOperationSpec]:
    """Operations map for the provider spec (mirrors ``build_default_operations``).

    Every connections operation is exposed over the given transports.
    """
    return {op: NamedServiceOperationSpec.from_value(op, list(transports)) for op in CONNECTION_OPERATIONS}


class AmbiguousConnectionAccount(Exception):
    """`get_token` was called without an account_id, but the user has more than
    one connected account for that provider (e.g. several Slack workspaces). The
    caller must choose one — the account ids are carried for the caller/UI."""

    def __init__(self, provider: str, account_ids: Sequence[str]) -> None:
        self.provider = str(provider or "")
        self.account_ids = [str(a) for a in (account_ids or [])]
        super().__init__(
            f"provider '{self.provider}' has {len(self.account_ids)} connected accounts; "
            f"specify account_id (one of: {', '.join(self.account_ids)})"
        )


def _str(value: Any) -> str:
    return str(value or "").strip()


def _opt_str(value: Any) -> str | None:
    text = _str(value)
    return text or None


def _str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [s.strip() for s in value.replace(",", " ").split() if s.strip()]
    if isinstance(value, (list, tuple)):
        return [str(s).strip() for s in value if str(s).strip()]
    return []


@dataclass(frozen=True)
class ClientApp:
    """A provider's OAuth application client, as exposed to the UI.

    The UI-facing shape carries NO secret and NO client_id — only what the
    connect UI needs to label an app, let the user choose one when a provider has
    several enabled apps, and offer a per-connect scope choice (`scopes` is the
    admin-configured CEILING; a connect may request a subset).
    """

    app_id: str
    provider: str
    label: str = ""
    enabled: bool = True
    scopes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "app_id": self.app_id,
            "provider": self.provider,
            "label": self.label,
            "enabled": self.enabled,
            "scopes": list(self.scopes),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ClientApp":
        data = dict(value or {})
        enabled = data.get("enabled", True)
        return cls(
            app_id=_str(data.get("app_id")),
            provider=_str(data.get("provider")),
            label=_str(data.get("label")),
            enabled=bool(enabled) if enabled is not None else True,
            scopes=tuple(_str_list(data.get("scopes"))),
        )

    @classmethod
    def coerce(cls, value: Any) -> "ClientApp":
        if isinstance(value, cls):
            return value
        return cls.from_dict(value or {})


@dataclass(frozen=True)
class Connection:
    """One connected account for a provider (user-scoped)."""

    provider: str
    account_id: str
    app_id: str = ""
    external_user_id: str = ""
    workspace: str = ""
    display_name: str = ""
    status: str = "connected"
    scope: tuple[str, ...] = ()
    has_token: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "account_id": self.account_id,
            "app_id": self.app_id,
            "external_user_id": self.external_user_id,
            "workspace": self.workspace,
            "display_name": self.display_name,
            "status": self.status,
            "scope": list(self.scope),
            "has_token": self.has_token,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Connection":
        data = dict(value or {})
        return cls(
            provider=_str(data.get("provider")),
            account_id=_str(data.get("account_id")),
            app_id=_str(data.get("app_id")),
            external_user_id=_str(data.get("external_user_id") or data.get("external_id")),
            workspace=_str(data.get("workspace")),
            display_name=_str(data.get("display_name")),
            status=_str(data.get("status")) or "connected",
            scope=tuple(_str_list(data.get("scope"))),
            has_token=bool(data.get("has_token")),
        )

    # `coerce` accepts a Connection or any mapping; convenient at parse boundaries.
    @classmethod
    def coerce(cls, value: Any) -> "Connection":
        if isinstance(value, cls):
            return value
        return cls.from_dict(value or {})


@dataclass(frozen=True)
class ConnectionToken:
    """The access token (plus optional refresh metadata) for a connection."""

    access_token: str
    refresh_token: str | None = None
    expires_at: str | None = None
    scope: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"access_token": self.access_token, "scope": list(self.scope)}
        if self.refresh_token is not None:
            out["refresh_token"] = self.refresh_token
        if self.expires_at is not None:
            out["expires_at"] = self.expires_at
        return out

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ConnectionToken":
        data = dict(value or {})
        return cls(
            access_token=_str(data.get("access_token")),
            refresh_token=_opt_str(data.get("refresh_token")),
            expires_at=_opt_str(data.get("expires_at")),
            scope=tuple(_str_list(data.get("scope"))),
        )

    @classmethod
    def coerce(cls, value: Any) -> "ConnectionToken":
        if isinstance(value, cls):
            return value
        return cls.from_dict(value or {})


@dataclass(frozen=True)
class CatalogEntry:
    """One row in Settings → Connections: a provider plus its connected state."""

    provider: str
    label: str = ""
    enabled: bool = False
    configured: bool = False
    connected: bool = False
    apps: tuple[ClientApp, ...] = field(default_factory=tuple)
    accounts: tuple[Connection, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "label": self.label,
            "enabled": self.enabled,
            "configured": self.configured,
            "connected": self.connected,
            "apps": [app.to_dict() for app in self.apps],
            "accounts": [acc.to_dict() for acc in self.accounts],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CatalogEntry":
        data = dict(value or {})
        accounts_raw = data.get("accounts") or []
        accounts = tuple(Connection.coerce(item) for item in accounts_raw if isinstance(item, Mapping))
        apps_raw = data.get("apps") or []
        apps = tuple(ClientApp.coerce(item) for item in apps_raw if isinstance(item, Mapping))
        return cls(
            provider=_str(data.get("provider")),
            label=_str(data.get("label")),
            enabled=bool(data.get("enabled")),
            configured=bool(data.get("configured")),
            connected=bool(data.get("connected")),
            apps=apps,
            accounts=accounts,
        )

    @classmethod
    def coerce(cls, value: Any) -> "CatalogEntry":
        if isinstance(value, cls):
            return value
        return cls.from_dict(value or {})


__all__ = [
    "NAMESPACE",
    "CONNECTION_CATALOG",
    "CONNECTION_STATUS",
    "CONNECTION_GET_TOKEN",
    "CONNECTION_DISCONNECT",
    "OAUTH_START",
    "AGENT_GRANT_GET_TOKEN",
    "CONNECTION_OPERATIONS",
    "build_connection_operations",
    "Connection",
    "ConnectionToken",
    "CatalogEntry",
    "ClientApp",
    "AmbiguousConnectionAccount",
]
