"""ConnectionHubProvider — concrete `connections` provider for connection-hub.

Implements the public ``ConnectionsProviderBase`` hooks against the reusable
``integrations/connections`` mechanics. The STORAGE CHOICE lives here: tokens
are user-scoped via ``ConnectionStore`` (shared_tokens default), so any bundle
acting for the user can resolve them.

The provider is bound to the bundle entrypoint so it can resolve the per-request
user from the named-service context and reuse the bundle-owned policy hooks that
``configure_connections`` already registered (storage root, target user id).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Callable, Optional

from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceContext,
    TRANSPORT_API,
    TRANSPORT_LOCAL,
    named_service_provider,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections import (
    NAMESPACE,
    AmbiguousConnectionAccount,
    CatalogEntry,
    ClientApp,
    Connection,
    ConnectionToken,
    ConnectionsProviderBase,
    build_connection_operations,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connections import (
    ConnectionStore,
    catalog as registry_catalog,
    refresh_access_token,
    resolve as resolve_provider,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connections import apps as connections_apps
from kdcube_ai_app.apps.chat.sdk.integrations.connections import settings as connections_settings

# Importing the providers package registers the built-in connection providers
# (Slack, …) into the connections registry.
import kdcube_ai_app.apps.chat.sdk.integrations.connections.providers  # noqa: F401

BUNDLE_ID = "connection-hub@1-0"
LOGGER = logging.getLogger("kdcube.connection_hub.provider")

# Refresh a little BEFORE the real expiry so a token handed to a task is still
# valid by the time the task uses it (and to absorb clock skew).
_REFRESH_SKEW_SECONDS = 60


def _expires_at_epoch(value: Any) -> Optional[int]:
    """Coerce a stored `expires_at` (int epoch, numeric string, or ISO-8601) to an
    epoch int. Returns None when absent/unparseable (treated as non-expiring)."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        pass
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


def _is_expired(tokens: dict[str, Any], *, skew: int = _REFRESH_SKEW_SECONDS) -> bool:
    expires_at = _expires_at_epoch(tokens.get("expires_at"))
    if expires_at is None:
        return False
    return expires_at <= int(time.time()) + int(skew)


@named_service_provider(
    provider_id="connections",
    bundle_id=BUNDLE_ID,
    namespace=NAMESPACE,
    operations=build_connection_operations((TRANSPORT_LOCAL, TRANSPORT_API)),
)
class ConnectionHubProvider(ConnectionsProviderBase):
    def __init__(self, *, entrypoint: Any, bundle_id: str = BUNDLE_ID) -> None:
        super().__init__()
        self._entrypoint = entrypoint
        self._bundle_id = bundle_id

    # ── storage choice: user-scoped ConnectionStore ─────────────────────────

    def _user_id(self, ctx: NamedServiceContext) -> Optional[str]:
        return ctx.user_id or (ctx.principal_id if ctx.principal_kind == "user" else None)

    def _store(self, ctx: NamedServiceContext) -> tuple[ConnectionStore, str]:
        # Reuse the bundle-owned resolvers bound via configure_connections.
        return connections_settings.store_for(self._entrypoint, user_id=self._user_id(ctx))

    # ── hooks ────────────────────────────────────────────────────────────────

    async def list_catalog(self, ctx: NamedServiceContext) -> list[CatalogEntry]:
        store, _ = self._store(ctx)
        entries: list[CatalogEntry] = []
        for prov in registry_catalog():
            client_apps = connections_apps.list_client_apps(self._entrypoint, prov.provider)
            enabled_apps = [a for a in client_apps if a.enabled]
            accounts = await store.list_accounts_async(provider=prov.provider)
            connections = tuple(Connection.from_dict(acc) for acc in accounts)
            apps = tuple(
                ClientApp(
                    app_id=a.app_id, provider=a.provider, label=a.label,
                    enabled=a.enabled, scopes=tuple(a.scopes),  # the per-app scope ceiling
                )
                for a in client_apps
            )
            entries.append(
                CatalogEntry(
                    provider=prov.provider,
                    label=prov.label,
                    enabled=bool(enabled_apps),
                    configured=bool(enabled_apps),  # any enabled client app
                    connected=any(c.has_token for c in connections),
                    apps=apps,
                    accounts=connections,
                )
            )
        return entries

    async def status(self, ctx: NamedServiceContext, *, provider: str) -> dict[str, Any]:
        return await connections_settings.status(
            self._entrypoint, provider=provider, user_id=self._user_id(ctx)
        )

    async def get_token(
        self,
        ctx: NamedServiceContext,
        *,
        provider: str,
        account_id: str | None = None,
    ) -> ConnectionToken | None:
        store, _ = self._store(ctx)
        resolved_account_id = (account_id or "").strip()
        accounts = await store.list_accounts_async(provider=provider)
        if resolved_account_id:
            account_row = next(
                (a for a in accounts if str(a.get("account_id") or "") == resolved_account_id),
                None,
            )
        else:
            # A user may have several connected accounts (e.g. multiple Slack
            # workspaces). Only auto-pick when there is exactly one; otherwise the
            # caller must choose — never silently return an arbitrary account.
            connected = [a for a in accounts if a.get("has_token")]
            if not connected:
                return None
            if len(connected) > 1:
                raise AmbiguousConnectionAccount(
                    provider, [str(a.get("account_id") or "") for a in connected]
                )
            account_row = connected[0]
            resolved_account_id = str(account_row.get("account_id") or "").strip()
        if not resolved_account_id:
            return None
        tokens = await store.get_tokens_async(resolved_account_id)
        if not tokens or not str(tokens.get("access_token") or "").strip():
            return None
        # Google (and any expiring provider): if the stored access token is at/near
        # expiry and we hold a refresh_token, refresh it now and persist — so a token
        # delivered to a SCHEDULED/later task is valid, not just right after connect.
        if _is_expired(tokens) and str(tokens.get("refresh_token") or "").strip():
            refreshed = await self._refresh_tokens(
                provider=provider,
                account_id=resolved_account_id,
                account_row=account_row or {},
                tokens=tokens,
                store=store,
            )
            if refreshed:
                tokens = refreshed
        return ConnectionToken.from_dict(tokens)

    async def _refresh_tokens(
        self,
        *,
        provider: str,
        account_id: str,
        account_row: dict[str, Any],
        tokens: dict[str, Any],
        store: ConnectionStore,
    ) -> dict[str, Any] | None:
        """Refresh an expired access token best-effort.

        Resolves the account's client app (by its recorded `app_id`) to get the
        client_id/secret, calls the provider's refresh endpoint, MERGES the result
        over the old token (preserving the old refresh_token when the provider — e.g.
        Google — omits a new one), persists it, and returns the merged token. On any
        failure (no client secret, network error, …) returns None so the caller falls
        back to the existing token best-effort."""
        try:
            prov = resolve_provider(provider)
        except Exception:
            return None
        refresh_token = str(tokens.get("refresh_token") or "").strip()
        if not refresh_token:
            return None
        app_id = str(account_row.get("app_id") or "").strip() or None
        try:
            client_app = connections_apps.resolve_client_app(self._entrypoint, provider, app_id)
            client_secret = await connections_apps.client_app_secret(
                self._bundle_id, provider, client_app.app_id
            )
        except Exception as exc:
            LOGGER.warning("[connection-hub] cannot resolve client app to refresh %s/%s: %s", provider, account_id, exc)
            return None
        if not client_app.client_id or not client_secret:
            LOGGER.warning("[connection-hub] missing client_id/secret to refresh %s/%s", provider, account_id)
            return None
        try:
            new_token = await refresh_access_token(
                prov,
                refresh_token=refresh_token,
                client_id=client_app.client_id,
                client_secret=client_secret,
            )
        except Exception as exc:
            LOGGER.warning("[connection-hub] token refresh failed for %s/%s: %s", provider, account_id, exc)
            return None
        merged = dict(tokens)
        merged.update(new_token)
        # Google omits a new refresh_token on refresh — keep the old one.
        if not str(merged.get("refresh_token") or "").strip() and refresh_token:
            merged["refresh_token"] = refresh_token
        try:
            await store.set_tokens_async(account_id, merged)
        except Exception as exc:
            LOGGER.warning("[connection-hub] failed to persist refreshed token for %s/%s: %s", provider, account_id, exc)
        return merged

    async def disconnect(
        self,
        ctx: NamedServiceContext,
        *,
        provider: str,
        account_id: str,
    ) -> dict[str, Any]:
        return await connections_settings.disconnect(
            self._entrypoint, provider=provider, account_id=account_id, user_id=self._user_id(ctx)
        )

    async def start_oauth(
        self,
        ctx: NamedServiceContext,
        *,
        provider: str,
        app_id: str | None = None,
        scopes: list[str] | None = None,
        return_hint: str = "",
    ) -> dict[str, Any]:
        # `resolve_provider` raises for an unknown provider — surface a clean error.
        resolve_provider(provider)
        return await connections_settings.start_oauth(
            self._entrypoint,
            provider=provider,
            app_id=app_id,
            scopes=scopes,
            return_hint=return_hint,
            user_id=self._user_id(ctx),
            source="connection_hub_widget",
        )


__all__ = ["ConnectionHubProvider", "BUNDLE_ID"]
