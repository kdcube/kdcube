# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Connection Hub operations for delegated to KDCube.

This module contains the application-independent operation layer. Bundle
entrypoints should expose thin routes over these methods instead of reading
Connection Hub user props/secrets directly.
"""

from __future__ import annotations

import logging

import inspect
import time
from typing import Any, Callable, Mapping
from urllib.parse import urlencode

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.adapters import resolve_adapter
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.broker import DelegatedToKdcubeBroker
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.models import (
    CONNECTION_HUB_BUNDLE_ID,
    ConnectedAccount,
    IntegrationProvider,
    DelegatedToKdcubeConfig,
    as_dict,
    as_str,
    as_str_list,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.oauth import (
    OAuthStateStore,
    consume_oauth_state,
    create_oauth_state,
    state_digest,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.store import (
    DelegatedToKdcubeStore,
    credential_id_for,
)


SECRET_INPUT_KEYS = {
    "access_token",
    "refresh_token",
    "id_token",
    "app_password",
    "password",
    "api_key",
    "token",
    "secret",
}


def _clean_claims(provider: IntegrationProvider, connector_app_id: str, raw: Any) -> tuple[str, ...]:
    requested = set(as_str_list(raw))
    if not requested:
        app = provider.connector_apps.get(as_str(connector_app_id))
        requested = set(app.allowed_claims) if app and app.allowed_claims else set(provider.claims)
    known = set(provider.claims)
    selected = tuple(sorted(item for item in requested if item in known))
    unknown = sorted(item for item in requested if item not in known)
    if unknown:
        raise ValueError(f"unknown provider claim: {', '.join(unknown)}")
    app = provider.connector_apps.get(as_str(connector_app_id))
    if app and app.allowed_claims:
        outside = sorted(item for item in selected if item not in set(app.allowed_claims))
        if outside:
            raise ValueError(f"claim is outside connector app ceiling: {', '.join(outside)}")
    return selected


def _credential_from_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw = as_dict(payload.get("credential"))
    for key in SECRET_INPUT_KEYS:
        value = payload.get(key)
        if as_str(value):
            raw[key] = value
    return {str(key): value for key, value in raw.items() if value not in (None, "")}


def _build_url(base_url: str, params: Mapping[str, Any]) -> str:
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}{urlencode({k: v for k, v in params.items() if v not in (None, '')})}"


async def _resolve_client_secret(
    resolver: Callable[..., Any],
    *,
    provider_id: str,
    connector_app_id: str,
    connector_app: Any,
) -> str:
    value = resolver(provider_id=provider_id, connector_app_id=connector_app_id, connector_app=connector_app)
    if inspect.isawaitable(value):
        value = await value
    return as_str(value)


LOGGER = logging.getLogger("kdcube.connections.delegated_to_kdcube")


class DelegatedToKdcubeOperations:
    """Operation facade used by Connection Hub routes and tests."""

    def __init__(
        self,
        *,
        config: DelegatedToKdcubeConfig,
        store: DelegatedToKdcubeStore,
        consent_granted_notifier: Callable[..., Any] | None = None,
    ) -> None:
        self.config = config
        self.store = store
        # Called (best-effort) after a consent persists, with the granted
        # facts — the hub binds it to author `connections.consent.granted`
        # conversation events for pending demands (see consent_demand.py).
        self.consent_granted_notifier = consent_granted_notifier

    async def catalog(self, *, provider_id: str = "") -> dict[str, Any]:
        accounts = await self.store.list_accounts(provider_id=provider_id)
        return {
            "ok": True,
            "enabled": self.config.enabled,
            "providers": self.config.to_dict(include_client_ids=True)["providers"],
            "accounts": [await self._public_account(account) for account in accounts],
        }

    async def _public_account(self, account: ConnectedAccount) -> dict[str, Any]:
        data = account.public_dict()
        credential = await self.store.get_credential(account.credential_id)
        health = self._credential_health(account, credential)
        data.update(health)
        # A persisted live rejection outranks timestamp-derived health: a
        # provider may refuse a token whose stored expiry still looks valid.
        metadata = dict(account.metadata or {})
        persisted = as_str(metadata.get("credential_status"))
        if persisted in {"reconnect_required", "missing", "revoked"}:
            data["credential_status"] = persisted
            data["reconnect_required"] = persisted != "revoked"
            data["credential_message"] = (
                "The provider rejected the stored credential. Reconnect this account."
                if persisted == "reconnect_required"
                else data.get("credential_message") or ""
            )
        for key in ("credential_status_at", "last_error", "last_error_at"):
            value = metadata.get(key)
            if value:
                data[key] = value
        return data

    def _credential_health(self, account: ConnectedAccount, credential: dict[str, Any]) -> dict[str, Any]:
        if not credential:
            return {
                "credential_status": "missing",
                "credential_kind": "missing",
                "credential_refreshable": False,
                "credential_expires_at": 0,
                "reconnect_required": True,
                "credential_message": "Credential is missing. Reconnect this account.",
            }
        provider = self.config.provider(account.provider_id)
        adapter = None
        if provider and provider.adapter:
            try:
                adapter = resolve_adapter(provider.adapter)
            except Exception:
                adapter = None
        oauth = bool(credential.get("oauth") or credential.get("access_token"))
        expires_at = 0
        refreshable = False
        if adapter is not None:
            expires_at = adapter.credential_expires_at(credential)
            refreshable = adapter.credential_refreshable(credential)
        else:
            try:
                expires_at = int(credential.get("expires_at") or 0)
            except Exception:
                expires_at = 0
            refreshable = bool(str(credential.get("refresh_token") or "").strip())
        if not oauth:
            return {
                "credential_status": "active",
                "credential_kind": "static_secret",
                "credential_refreshable": False,
                "credential_expires_at": 0,
                "reconnect_required": False,
                "credential_message": "Credential is stored.",
            }
        if not expires_at:
            if refreshable:
                return {
                    "credential_status": "refreshable",
                    "credential_kind": "oauth",
                    "credential_refreshable": True,
                    "credential_expires_at": 0,
                    "reconnect_required": False,
                    "credential_message": "OAuth credential has a refresh token; KDCube will refresh it on next use.",
                }
            return {
                "credential_status": "active",
                "credential_kind": "oauth",
                "credential_refreshable": refreshable,
                "credential_expires_at": 0,
                "reconnect_required": False,
                "credential_message": "OAuth credential is stored.",
            }
        now = int(time.time())
        if expires_at <= now and not refreshable:
            return {
                "credential_status": "reconnect_required",
                "credential_kind": "oauth",
                "credential_refreshable": False,
                "credential_expires_at": expires_at,
                "reconnect_required": True,
                "credential_message": "OAuth access expired and no refresh token is stored. Reconnect this account.",
            }
        if expires_at <= now:
            return {
                "credential_status": "refreshable",
                "credential_kind": "oauth",
                "credential_refreshable": True,
                "credential_expires_at": expires_at,
                "reconnect_required": False,
                "credential_message": "OAuth access expired; KDCube will refresh it on next use.",
            }
        if expires_at <= now + 300:
            return {
                "credential_status": "expires_soon",
                "credential_kind": "oauth",
                "credential_refreshable": refreshable,
                "credential_expires_at": expires_at,
                "reconnect_required": False,
                "credential_message": "OAuth access expires soon.",
            }
        return {
            "credential_status": "active",
            "credential_kind": "oauth",
            "credential_refreshable": refreshable,
            "credential_expires_at": expires_at,
            "reconnect_required": False,
            "credential_message": "OAuth credential is active.",
        }

    async def connect_credential(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        provider_id = as_str(payload.get("provider_id"))
        connector_app_id = as_str(payload.get("connector_app_id"))
        provider = self.config.provider(provider_id)
        if not self.config.enabled:
            raise ValueError("delegated-to-KDCube connections are not enabled")
        if provider is None or not provider.enabled:
            raise ValueError(f"provider is not enabled: {provider_id or '<missing>'}")
        if not connector_app_id:
            raise ValueError("connector app is required")
        if connector_app_id not in provider.connector_apps:
            raise ValueError(f"connector app is not configured: {connector_app_id}")
        credential = _credential_from_payload(payload)
        if not credential:
            raise ValueError("credential is required")
        claims = _clean_claims(provider, connector_app_id, payload.get("claims"))
        if not claims:
            raise ValueError("at least one provider claim is required")

        account = ConnectedAccount(
            account_id=as_str(payload.get("account_id")),
            provider_id=provider_id,
            connector_app_id=connector_app_id,
            external_subject=as_str(payload.get("external_subject")),
            display_name=as_str(payload.get("display_name") or payload.get("label")),
            email=as_str(payload.get("email")),
            workspace=as_str(payload.get("workspace") or payload.get("team") or payload.get("tenant")),
            claims=claims,
            credential_id=as_str(payload.get("credential_id")),
            metadata=as_dict(payload.get("metadata")),
        )
        stored = await self.store.upsert_account(account)
        credential_id = stored.credential_id or credential_id_for(stored.account_id)
        credential_with_metadata = {
            "provider_id": provider_id,
            "connector_app_id": connector_app_id,
            "claims": list(claims),
            **credential,
        }
        await self.store.set_credential(credential_id, credential_with_metadata)
        if credential_id != stored.credential_id:
            stored = await self.store.upsert_account(
                ConnectedAccount(
                    account_id=stored.account_id,
                    provider_id=stored.provider_id,
                    connector_app_id=stored.connector_app_id,
                    external_subject=stored.external_subject,
                    display_name=stored.display_name,
                    email=stored.email,
                    workspace=stored.workspace,
                    claims=stored.claims,
                    credential_id=credential_id,
                    status=stored.status,
                    connected_at=stored.connected_at,
                    metadata=stored.metadata,
                )
            )
        # The grant is an authored conversation event: notify (best-effort)
        # so pending demands in chat conversations learn the consent landed.
        if self.consent_granted_notifier is not None:
            try:
                result = self.consent_granted_notifier(
                    provider_id=provider_id,
                    connector_app_id=connector_app_id,
                    claims=list(claims),
                    account_id=stored.account_id,
                )
                if hasattr(result, "__await__"):
                    await result
            except Exception:
                LOGGER.warning("[delegated.consent] granted notifier failed (best-effort)", exc_info=True)
        return {"ok": True, "account": stored.public_dict()}

    async def start_oauth(
        self,
        payload: Mapping[str, Any],
        *,
        user_id: str,
        callback_url: str,
        state_store: OAuthStateStore,
        state_secret: str,
        ttl_seconds: int = 900,
        source: str = "connection_hub_widget",
    ) -> dict[str, Any]:
        provider_id = as_str(payload.get("provider_id"))
        connector_app_id = as_str(payload.get("connector_app_id"))
        provider = self.config.provider(provider_id)
        if not self.config.enabled:
            raise ValueError("delegated-to-KDCube connections are not enabled")
        if provider is None or not provider.enabled:
            raise ValueError(f"provider is not enabled: {provider_id or '<missing>'}")
        if not connector_app_id:
            raise ValueError("connector app is required")
        connector_app = provider.connector_apps.get(connector_app_id)
        if connector_app is None or not connector_app.enabled:
            raise ValueError(f"connector app is not enabled: {connector_app_id}")
        if not connector_app.client_id:
            raise ValueError(f"OAuth client id is not configured for connector app: {connector_app_id}")
        adapter = resolve_adapter(provider.adapter)
        if not adapter.oauth_enabled:
            raise ValueError(f"provider adapter does not support OAuth: {provider.adapter}")
        claims = _clean_claims(provider, connector_app_id, payload.get("claims"))
        if not claims:
            raise ValueError("at least one provider claim is required")
        redirect_uri = connector_app.redirect_uri or as_str(callback_url)
        if not redirect_uri:
            raise ValueError("OAuth callback URL is not available")
        scopes = adapter.provider_scopes_for_claims(list(claims), provider.claims)
        state = await create_oauth_state(
            state_store,
            secret=state_secret,
            user_id=user_id,
            provider_id=provider_id,
            connector_app_id=connector_app_id,
            claims=claims,
            return_hint=as_str(payload.get("return_hint") or payload.get("return_to")),
            source=source,
            ttl_seconds=ttl_seconds,
        )
        params = {
            "response_type": "code",
            "client_id": connector_app.client_id,
            "redirect_uri": redirect_uri,
            "state": state["state"],
        }
        scope_param = adapter.authorize_scope_param()
        if scopes:
            params[scope_param] = " ".join(scopes)
        for key, value in adapter.authorize_extra_params().items():
            params.setdefault(key, value)
        return {
            "ok": True,
            "provider_id": provider_id,
            "connector_app_id": connector_app_id,
            "authorize_url": _build_url(adapter.authorize_url, params),
            "state_id": state["state_id"],
            "redirect_uri": redirect_uri,
            "claims": list(claims),
            "provider_scopes": scopes,
        }

    async def complete_oauth(
        self,
        *,
        code: str,
        state: str,
        callback_url: str,
        state_store: OAuthStateStore,
        state_secret: str,
        client_secret_resolver: Callable[..., Any],
    ) -> dict[str, Any]:
        payload = await consume_oauth_state(state_store, state=state, secret=state_secret)
        user_id = as_str(payload.get("user_id"))
        if user_id != self.store.user_id:
            raise ValueError("OAuth state user does not match integration store")
        provider_id = as_str(payload.get("provider_id"))
        connector_app_id = as_str(payload.get("connector_app_id"))
        provider = self.config.provider(provider_id)
        if provider is None or not provider.enabled:
            raise ValueError(f"provider is not enabled: {provider_id or '<missing>'}")
        connector_app = provider.connector_apps.get(connector_app_id)
        if connector_app is None or not connector_app.enabled:
            raise ValueError(f"connector app is not enabled: {connector_app_id}")
        adapter = resolve_adapter(provider.adapter)
        client_secret = await _resolve_client_secret(
            client_secret_resolver,
            provider_id=provider_id,
            connector_app_id=connector_app_id,
            connector_app=connector_app,
        )
        redirect_uri = connector_app.redirect_uri or as_str(callback_url)
        token = await adapter.exchange_code(
            code=code,
            redirect_uri=redirect_uri,
            client_id=connector_app.client_id,
            client_secret=client_secret,
        )
        access_token = as_str(token.get("access_token"))
        if not access_token:
            raise ValueError("OAuth token response did not include access_token")
        profile = await adapter.fetch_profile(access_token=access_token, token=token)
        if not profile:
            profile = await adapter.normalize_profile(token)
        claims = tuple(as_str_list(payload.get("claims")))
        credential = {
            "oauth": True,
            **token,
        }
        connected = await self.connect_credential(
            {
                "provider_id": provider_id,
                "connector_app_id": connector_app_id,
                "external_subject": profile.get("external_subject") or profile.get("sub") or profile.get("id"),
                "email": profile.get("email"),
                "display_name": profile.get("display_name") or profile.get("name"),
                "workspace": profile.get("workspace"),
                "claims": claims,
                "credential": credential,
                "metadata": {
                    "oauth_source": payload.get("source"),
                    "oauth_state_id": state_digest(state),
                    "workspace_label": profile.get("workspace_label"),
                },
            }
        )
        stored_account = connected.get("account") if isinstance(connected.get("account"), dict) else {}
        LOGGER.info(
            "[delegated.oauth] consent persisted: account=%s provider=%s connector=%s claims=%s user=%s",
            stored_account.get("account_id"),
            provider_id,
            connector_app_id,
            ",".join(stored_account.get("claims") or []),
            user_id,
        )
        return {
            "ok": True,
            "provider_id": provider_id,
            "connector_app_id": connector_app_id,
            "user_id": user_id,
            "account": connected.get("account"),
            "return_hint": as_str(payload.get("return_hint")),
        }

    async def disconnect(self, *, account_id: str) -> dict[str, Any]:
        removed = await self.store.disconnect_account(account_id)
        return {"ok": removed, "removed": removed, "account_id": as_str(account_id)}

    async def resolve(
        self,
        *,
        provider_id: str = "",
        claim: str = "",
        connector_app_id: str = "",
        account_id: str = "",
    ) -> dict[str, Any]:
        broker = DelegatedToKdcubeBroker(config=self.config, store=self.store)
        result = await broker.ensure_claim(
            provider_id=provider_id,
            connector_app_id=connector_app_id,
            claim=claim,
            account_id=account_id,
        )
        return result.to_dict(include_credential=False)


def operations_for_user(
    *,
    user_id: str,
    config: DelegatedToKdcubeConfig,
    bundle_id: str = CONNECTION_HUB_BUNDLE_ID,
    store: DelegatedToKdcubeStore | None = None,
    consent_granted_notifier: Callable[..., Any] | None = None,
) -> DelegatedToKdcubeOperations:
    resolved_store = store or DelegatedToKdcubeStore(user_id=user_id, bundle_id=bundle_id)
    return DelegatedToKdcubeOperations(
        config=config,
        store=resolved_store,
        consent_granted_notifier=consent_granted_notifier,
    )


__all__ = ["DelegatedToKdcubeOperations", "operations_for_user"]
