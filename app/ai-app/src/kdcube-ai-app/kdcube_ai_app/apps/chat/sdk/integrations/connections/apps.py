"""Client apps — the ADMIN-managed OAuth application clients for a provider.

A *client app* is the middle level of the three-level connection model:

    provider TYPE  →  client app(s)  →  account(s)

The provider type is code (OAuth mechanics, no credentials). A client app holds
the credentials (`client_id`, `client_secret`, app-specific scopes). There can be
MANY client apps per provider; a user account is connected THROUGH one client app
and records its `app_id`.

Now: client apps come from deploy-time bundle config —
  `connections.providers.<provider>.apps`  (a list of
    `{app_id, label, client_id, scopes, enabled}`)
with `client_secret` resolved from bundle secrets at
  `connections.providers.<provider>.apps.<app_id>.client_secret`
and the hub-level state-signing secret at
  `connections.oauth_state_secret`.

`client_secret` NEVER lives in the dataclass or in config/metadata/logs — it is
resolved separately from the secret store on demand.

Later: an admin view backed by a store can supply the same `ClientApp` records at
runtime; the contract and account model don't change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

try:
    from kdcube_ai_app.apps.chat.sdk.config import get_secret
except Exception:
    get_secret = None  # type: ignore[assignment]

from kdcube_ai_app.apps.chat.sdk.integrations.connections.registry import DEFAULT_BUNDLE_ID, _entrypoint_bundle_id

# Where deploy config / secrets for client apps live.
PROVIDERS_CONFIG_PREFIX = "connections.providers"
OAUTH_STATE_SECRET_KEY = "connections.oauth_state_secret"


class AmbiguousClientApp(Exception):
    """`resolve_client_app` was called without an `app_id`, but the provider has
    more than one enabled client app. The caller must choose one; the candidate
    app ids are carried for the caller/UI."""

    def __init__(self, provider: str, app_ids: Sequence[str]) -> None:
        self.provider = str(provider or "")
        self.app_ids = [str(a) for a in (app_ids or [])]
        super().__init__(
            f"provider '{self.provider}' has {len(self.app_ids)} enabled client apps; "
            f"specify app_id (one of: {', '.join(self.app_ids)})"
        )


def _str(value: Any) -> str:
    return str(value or "").strip()


def _str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [s.strip() for s in value.replace(",", " ").split() if s.strip()]
    if isinstance(value, (list, tuple)):
        return [str(s).strip() for s in value if str(s).strip()]
    return []


@dataclass(frozen=True)
class ClientApp:
    """One OAuth application client for a provider (admin data; no secret).

    `client_secret` is intentionally NOT a field — it is resolved on demand from
    the secret store via `client_app_secret(...)`.
    """

    app_id: str
    provider: str
    label: str = ""
    client_id: str = ""
    scopes: tuple[str, ...] = field(default_factory=tuple)
    redirect_uri: str = ""
    enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "app_id": self.app_id,
            "provider": self.provider,
            "label": self.label,
            "client_id": self.client_id,
            "scopes": list(self.scopes),
            "redirect_uri": self.redirect_uri,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ClientApp":
        data = dict(value or {})
        enabled = data.get("enabled", True)
        return cls(
            app_id=_str(data.get("app_id")),
            provider=_str(data.get("provider")),
            label=_str(data.get("label")),
            client_id=_str(data.get("client_id")),
            scopes=tuple(_str_list(data.get("scopes"))),
            redirect_uri=_str(data.get("redirect_uri")),
            enabled=bool(enabled) if enabled is not None else True,
        )

    @classmethod
    def coerce(cls, value: Any) -> "ClientApp":
        if isinstance(value, cls):
            return value
        return cls.from_dict(value or {})


# ── reading client apps from deploy config ──────────────────────────────────


def list_client_apps(entrypoint: Any, provider: str) -> List[ClientApp]:
    """All configured client apps for `provider`, in config order.

    Reads `connections.providers.<provider>.apps` (a list) from the bundle config
    via `entrypoint.bundle_prop`. Each entry is coerced to a `ClientApp`; the
    `provider` field is forced to `provider` regardless of what the entry says.
    """
    prov = _str(provider)
    raw = entrypoint.bundle_prop(f"{PROVIDERS_CONFIG_PREFIX}.{prov}.apps", None)
    if not isinstance(raw, (list, tuple)):
        return []
    apps: List[ClientApp] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        data = dict(item)
        data["provider"] = prov
        app = ClientApp.from_dict(data)
        if not app.app_id:
            continue
        apps.append(app)
    return apps


def resolve_client_app(
    entrypoint: Any,
    provider: str,
    app_id: Optional[str] = None,
) -> ClientApp:
    """Resolve the client app to connect/refresh through.

    - explicit `app_id` → that app (must exist).
    - no `app_id` and exactly ONE enabled app → that app.
    - no `app_id` and several enabled apps → raise `AmbiguousClientApp`.
    - no `app_id` and no enabled app → raise a clear error.
    """
    prov = _str(provider)
    apps = list_client_apps(entrypoint, prov)
    wanted = _str(app_id)
    if wanted:
        match = next((a for a in apps if a.app_id == wanted), None)
        if match is None:
            raise ValueError(f"unknown client app '{wanted}' for provider '{prov}'")
        return match
    enabled = [a for a in apps if a.enabled]
    if not enabled:
        raise ValueError(f"provider '{prov}' has no enabled client app configured")
    if len(enabled) > 1:
        raise AmbiguousClientApp(prov, [a.app_id for a in enabled])
    return enabled[0]


# ── secrets (resolved separately, never in config/metadata/logs) ────────────


async def _secret_lookup(*keys: str) -> str:
    if get_secret is None:
        return ""
    for key in keys:
        value = await get_secret(key)
        if value:
            return value
    return ""


async def client_app_secret(bundle_id: str, provider: str, app_id: str) -> str:
    """Resolve a client app's `client_secret` from the secret store.

    Key: `connections.providers.<provider>.apps.<app_id>.client_secret`. Uses the
    same `b:<key>` / `bundles.<bundle>.secrets.<key>` lookup convention the old
    per-provider `client_secret` used.
    """
    bundle = str(bundle_id or DEFAULT_BUNDLE_ID).strip() or DEFAULT_BUNDLE_ID
    prov = _str(provider)
    app = _str(app_id)
    key = f"{PROVIDERS_CONFIG_PREFIX}.{prov}.apps.{app}.client_secret"
    return await _secret_lookup(f"b:{key}", f"bundles.{bundle}.secrets.{key}")


async def oauth_state_secret(entrypoint: Any) -> str:
    """Resolve the hub-level OAuth state-signing secret.

    Key: `connections.oauth_state_secret` (one per hub, not per provider/app).
    Falls back to a same-named non-secret bundle prop for local/dev wiring.
    """
    bundle_id = _entrypoint_bundle_id(entrypoint)
    return (
        await _secret_lookup(
            f"b:{OAUTH_STATE_SECRET_KEY}",
            f"bundles.{bundle_id}.secrets.{OAUTH_STATE_SECRET_KEY}",
        )
        or str(entrypoint.bundle_prop(OAUTH_STATE_SECRET_KEY, "") or "").strip()
    )


__all__ = [
    "ClientApp",
    "AmbiguousClientApp",
    "list_client_apps",
    "resolve_client_app",
    "client_app_secret",
    "oauth_state_secret",
    "PROVIDERS_CONFIG_PREFIX",
    "OAUTH_STATE_SECRET_KEY",
]
