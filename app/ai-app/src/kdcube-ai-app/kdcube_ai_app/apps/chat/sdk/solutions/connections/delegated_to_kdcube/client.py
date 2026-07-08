# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Application-facing client for delegated to KDCube."""

from __future__ import annotations

from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.broker import (
    DelegatedToKdcubeBroker,
    broker_for_user,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.config import (
    delegated_to_kdcube_config,
    delegated_to_kdcube_config_from_entrypoint,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.models import (
    CONNECTION_HUB_BUNDLE_ID,
    CREDENTIAL_RECONNECT_REQUIRED,
    ClaimResolution,
    ConnectedAccount,
    DelegatedToKdcubeConfig,
    ToolClaimPolicy,
    as_str,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.public_base import (
    public_base_url_from_hub_props,
    set_connection_hub_public_base_url,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.store import DelegatedToKdcubeStore
from kdcube_ai_app.apps.chat.sdk.solutions.connections.connection_edges import (
    connection_hub_bundle_id_from_entrypoint,
)
from kdcube_ai_app.apps.chat.sdk import config as sdk_config


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _runtime_tenant_project(entrypoint: Any) -> tuple[str, str]:
    runtime_identity = getattr(entrypoint, "runtime_identity", None)
    if callable(runtime_identity):
        try:
            ident = runtime_identity()
        except Exception:
            ident = {}
        if isinstance(ident, Mapping):
            return _clean(ident.get("tenant")), _clean(ident.get("project"))
    comm_context = getattr(entrypoint, "comm_context", None)
    actor = getattr(comm_context, "actor", None)
    return _clean(getattr(actor, "tenant_id", "")), _clean(getattr(actor, "project_id", ""))


class DelegatedToKdcubeClient:
    """Small facade applications/tools can use without reading Connection Hub storage."""

    def __init__(self, *, broker: DelegatedToKdcubeBroker) -> None:
        self._broker = broker

    @classmethod
    def from_user(
        cls,
        *,
        user_id: str,
        config: DelegatedToKdcubeConfig,
        bundle_id: str = "",
        store: DelegatedToKdcubeStore | None = None,
        client_secret_resolver: Any = None,
    ) -> "DelegatedToKdcubeClient":
        return cls(
            broker=broker_for_user(
                user_id=user_id,
                config=config,
                bundle_id=bundle_id,
                store=store,
                client_secret_resolver=client_secret_resolver,
            )
        )

    @classmethod
    def from_entrypoint(cls, entrypoint: Any, *, user_id: str, store: DelegatedToKdcubeStore | None = None) -> "DelegatedToKdcubeClient":
        config = delegated_to_kdcube_config_from_entrypoint(entrypoint)
        try:
            hub_public_base = str(
                entrypoint.bundle_prop("connections.oauth.public_base_url", "") or ""
            ).strip()
        except Exception:
            hub_public_base = ""
        if hub_public_base:
            set_connection_hub_public_base_url(hub_public_base)
        return cls.from_user(user_id=user_id, config=config, bundle_id=CONNECTION_HUB_BUNDLE_ID, store=store)

    @classmethod
    async def from_connection_hub(
        cls,
        entrypoint: Any,
        *,
        user_id: str,
        connection_hub_bundle_id: str | None = None,
        tenant: str | None = None,
        project: str | None = None,
        store: DelegatedToKdcubeStore | None = None,
    ) -> "DelegatedToKdcubeClient":
        """Build a client from the configured Connection Hub registry.

        Applications use this instead of reading Connection Hub descriptors
        directly. The implementation reads the effective Connection Hub bundle
        props from the shared runtime cache when available, and falls back to
        the current entrypoint only when it already carries the registry.
        """
        bundle_id = _clean(connection_hub_bundle_id) or connection_hub_bundle_id_from_entrypoint(entrypoint)
        resolved_tenant, resolved_project = _runtime_tenant_project(entrypoint)
        resolved_tenant = _clean(tenant) or resolved_tenant
        resolved_project = _clean(project) or resolved_project
        props: dict[str, Any] = {}
        redis = getattr(entrypoint, "redis", None)
        if redis is not None and resolved_tenant and resolved_project and bundle_id:
            from kdcube_ai_app.infra.plugin.bundle_store import get_bundle_props

            props = dict(
                await get_bundle_props(
                    redis,
                    tenant=resolved_tenant,
                    project=resolved_project,
                    bundle_id=bundle_id,
                )
                or {}
            )
        if not props:
            current_props = getattr(entrypoint, "bundle_props", None)
            if isinstance(current_props, dict):
                props = dict(current_props)
        # The hub props carry the deployment's public base URL (the OAuth
        # source of truth); remember it so consent deep links ship absolute.
        hub_public_base = public_base_url_from_hub_props(props)
        if hub_public_base:
            set_connection_hub_public_base_url(hub_public_base)

        async def _client_secret_resolver(*, provider_id: str, connector_app_id: str, connector_app: Any) -> str:
            configured_ref = _clean(getattr(connector_app, "client_secret_ref", ""))
            secret_ref = configured_ref or (
                f"connections.delegated_to_kdcube.providers.{provider_id}."
                f"connector_apps.{connector_app_id}.client_secret"
            )
            return _clean(
                await sdk_config.get_secret(secret_ref, bundle_id=bundle_id)
                or await sdk_config.get_secret(f"b:{secret_ref}", bundle_id=bundle_id)
            )

        return cls.from_user(
            user_id=user_id,
            config=delegated_to_kdcube_config(props),
            bundle_id=bundle_id or CONNECTION_HUB_BUNDLE_ID,
            store=store,
            client_secret_resolver=_client_secret_resolver,
        )

    async def catalog(self) -> dict[str, Any]:
        return self._broker.config.to_dict()

    async def list_accounts(self, provider_id: str = "") -> list[ConnectedAccount]:
        return await self._broker.store.list_accounts(provider_id=provider_id)

    async def ensure_claim(
        self,
        *,
        provider_id: str,
        claim: str,
        connector_app_id: str = "",
        account_id: str | None = None,
        purpose: str = "",
        force_refresh: bool = False,
    ) -> ClaimResolution:
        return await self._broker.ensure_claim(
            provider_id=provider_id,
            connector_app_id=connector_app_id,
            claim=claim,
            account_id=account_id,
            purpose=purpose,
            force_refresh=force_refresh,
        )

    async def mark_account_auth_failure(
        self,
        account_id: str,
        *,
        last_error: str = "",
    ) -> ConnectedAccount | None:
        """Record that the provider rejected this account's stored credential.

        The account keeps its lifecycle status; ``credential_status`` becomes
        ``reconnect_required`` so Connection Hub stops presenting it as healthy.
        """
        account = await self._broker.store.get_account(account_id)
        if account is None:
            return None
        return await self._broker.store.set_account_status(
            account.account_id,
            account.status,
            credential_status=CREDENTIAL_RECONNECT_REQUIRED,
            last_error=last_error or "provider rejected the stored credential",
        )

    async def ensure_tool_claims(
        self,
        *,
        policy: ToolClaimPolicy,
        account_ids: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return await self._broker.ensure_tool_claims(policy=policy, account_ids=account_ids)

    async def get_credential(self, *, account_id: str, claim: str) -> ClaimResolution:
        account = await self._broker.store.get_account(account_id)
        if account is None:
            return ClaimResolution(
                ok=False,
                provider_id="",
                claim=as_str(claim),
                account_id=as_str(account_id),
                error="account_not_found",
                message="Connected account not found.",
            )
        return await self.ensure_claim(
            provider_id=account.provider_id,
            connector_app_id=account.connector_app_id,
            claim=claim,
            account_id=account.account_id,
        )


__all__ = ["DelegatedToKdcubeClient"]
