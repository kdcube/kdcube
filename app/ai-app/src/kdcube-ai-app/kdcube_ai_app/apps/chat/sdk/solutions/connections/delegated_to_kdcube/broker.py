# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Credential broker for delegated to KDCube."""

from __future__ import annotations

import logging

from typing import Any, Callable, Mapping

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.adapters import (
    resolve_adapter,
)

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.models import (
    CREDENTIAL_ACTIVE,
    CREDENTIAL_MISSING,
    CREDENTIAL_RECONNECT_REQUIRED,
    REASON_ACCOUNT_REQUIRED,
    REASON_CLAIM_UPGRADE_REQUIRED,
    REASON_CONNECT_REQUIRED,
    REASON_RECONNECT_REQUIRED,
    ClaimResolution,
    CredentialHandle,
    DelegatedToKdcubeConfig,
    ToolClaimPolicy,
    account_choice,
    as_str,
    as_str_list,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.store import DelegatedToKdcubeStore


LOGGER = logging.getLogger("kdcube.connections.delegated_to_kdcube")


class DelegatedToKdcubeBroker:
    """Resolve provider claims for one platform user."""

    def __init__(
        self,
        *,
        config: DelegatedToKdcubeConfig,
        store: DelegatedToKdcubeStore,
        client_secret_resolver: Callable[..., Any] | None = None,
        refresh_skew_seconds: int = 120,
    ) -> None:
        self.config = config
        self.store = store
        self.client_secret_resolver = client_secret_resolver
        self.refresh_skew_seconds = max(0, int(refresh_skew_seconds or 0))

    async def ensure_claim(
        self,
        *,
        provider_id: str,
        claim: str,
        connector_app_id: str | None = None,
        account_id: str | None = None,
        account_claim_scope: Mapping[str, Any] | None = None,
        purpose: str = "",
        force_refresh: bool = False,
    ) -> ClaimResolution:
        """Resolve one provider claim.

        ``account_claim_scope`` is the calling AGENT's per-account claim binding
        for this provider (`account_scope[provider_id]` on the agent grant),
        shaped ``{account_id: [claims]}``: for each connected account, the exact
        claims this agent may use ON that account. The account key ``"*"`` means
        any account; a claim entry ``"*"`` (or the whole binding being ``None`` /
        empty) means any claim. An account may satisfy this claim only when it is
        bound AND the binding covers ``claim`` — so "read+write from account 1,
        read-only from account 2" is enforced here, independent of what each
        account is itself capable of. ``None``/empty = no restriction (the
        default and every non-agent turn). An explicit ``account_id`` must
        itself be bound for this claim.

        ``force_refresh`` refreshes the credential even when its timestamps
        look valid — the live-401 retry path uses it when the provider
        rejected a token the timestamps still trust.
        """
        del purpose
        provider_key = as_str(provider_id)
        claim_key = as_str(claim)
        connector_key = as_str(connector_app_id)
        account_key = as_str(account_id)
        # The agent's per-account claim binding for this provider:
        # {account_id: {claims}} where account "*" = any account and claim "*" =
        # any claim. Empty/None => no restriction (any account, any claim).
        scope = {
            as_str(acc): {as_str(c) for c in (claims or ()) if as_str(c)}
            for acc, claims in dict(account_claim_scope or {}).items()
            if as_str(acc)
        }
        restrict = bool(scope)

        def _binding_allows(aid: str) -> bool:
            if not restrict:
                return True
            claims = scope.get(aid)
            if claims is None:
                claims = scope.get("*")
            if claims is None:
                return False  # the agent is not bound to this account
            if not claim_key:
                return True  # probe / connect hint: account membership is enough
            return "*" in claims or claim_key in claims

        if account_key and restrict and not _binding_allows(account_key):
            return self._needs_user_action(
                reason=REASON_ACCOUNT_REQUIRED,
                provider_id=provider_key,
                claim=claim_key,
                connector_app_id=connector_key,
                account_id=account_key,
                message=f"This agent may not use {claim_key or 'this provider'} on account {account_key}.",
            )
        provider = self.config.provider(provider_key)
        if not self.config.enabled or provider is None or not provider.enabled:
            return self._needs_user_action(
                reason=REASON_CONNECT_REQUIRED,
                provider_id=provider_key,
                claim=claim_key,
                connector_app_id=connector_key,
                account_id=account_key,
                message=f"Provider {provider_key or '<missing>'} is not enabled in Connection Hub.",
            )
        if claim_key not in provider.claims:
            return ClaimResolution(
                ok=False,
                provider_id=provider_key,
                claim=claim_key,
                connector_app_id=connector_key,
                account_id=account_key,
                error="claim_not_configured",
                message=f"Claim {claim_key or '<missing>'} is not configured for provider {provider_key}.",
            )
        if connector_key:
            connector_app = provider.connector_apps.get(connector_key)
            if connector_app is None or not connector_app.enabled:
                return ClaimResolution(
                    ok=False,
                    provider_id=provider_key,
                    claim=claim_key,
                    connector_app_id=connector_key,
                    account_id=account_key,
                    error="connector_app_not_configured",
                    message=f"Connector app {connector_key} is not configured for provider {provider_key}.",
                )
            if connector_app.allowed_claims and claim_key not in set(connector_app.allowed_claims):
                return ClaimResolution(
                    ok=False,
                    provider_id=provider_key,
                    claim=claim_key,
                    connector_app_id=connector_key,
                    account_id=account_key,
                    error="claim_outside_connector_app",
                    message=f"Claim {claim_key} is outside connector app {connector_key}.",
                )

        accounts = await self.store.list_accounts(provider_id=provider_key)
        LOGGER.info(
            "[delegated.broker] resolve claim=%s provider=%s connector=%s account_key=%s accounts=%s",
            claim_key, provider_key, connector_key or "-", account_key or "-",
            "; ".join(
                f"{item.account_id}(status={item.status},claims={','.join(item.claims)})"
                for item in accounts
            ) or "none",
        )
        if account_key:
            account = next(
                (
                    item for item in accounts
                    if item.account_id == account_key and (not connector_key or item.connector_app_id == connector_key)
                ),
                None,
            )
            if account is None:
                return self._needs_user_action(
                    reason=REASON_CONNECT_REQUIRED,
                    provider_id=provider_key,
                    claim=claim_key,
                    connector_app_id=connector_key,
                    account_id=account_key,
                    message=f"Connected account {account_key} is not available.",
                )
        else:
            candidates = [
                item for item in accounts
                if item.connected and item.allows(claim_key) and (not connector_key or item.connector_app_id == connector_key)
                and _binding_allows(item.account_id)
            ]
            if not candidates:
                connected = [
                    item for item in accounts
                    if item.connected and (not connector_key or item.connector_app_id == connector_key)
                    and _binding_allows(item.account_id)
                ]
                if connected:
                    # Accounts exist but none has approved this claim: the fix
                    # is a claim upgrade on one of them, not a new connection.
                    return self._needs_user_action(
                        reason=REASON_CLAIM_UPGRADE_REQUIRED,
                        provider_id=provider_key,
                        claim=claim_key,
                        connector_app_id=connector_key,
                        message=f"Approve {claim_key} for your connected {provider.label or provider.provider_id} account.",
                        candidates=tuple(account_choice(item) for item in connected),
                    )
                return self._needs_user_action(
                    reason=REASON_CONNECT_REQUIRED,
                    provider_id=provider_key,
                    claim=claim_key,
                    connector_app_id=connector_key,
                    message=f"Connect {provider.label or provider.provider_id} and approve {claim_key}.",
                    candidates=tuple(account_choice(item) for item in accounts),
                )
            if len(candidates) > 1:
                return self._needs_user_action(
                    reason=REASON_ACCOUNT_REQUIRED,
                    provider_id=provider_key,
                    claim=claim_key,
                    connector_app_id=connector_key,
                    message="Several connected accounts can satisfy this claim; choose an account_id.",
                    candidates=tuple(account_choice(item) for item in candidates),
                )
            account = candidates[0]

        if not account.connected:
            return self._needs_user_action(
                reason=REASON_RECONNECT_REQUIRED,
                provider_id=provider_key,
                claim=claim_key,
                connector_app_id=connector_key,
                account_id=account.account_id,
                message="The connected account has no usable credential. Reconnect it in Connection Hub.",
            )
        if not account.allows(claim_key):
            return self._needs_user_action(
                reason=REASON_CLAIM_UPGRADE_REQUIRED,
                provider_id=provider_key,
                claim=claim_key,
                connector_app_id=connector_key,
                account_id=account.account_id,
                message=f"The connected account has not approved {claim_key}.",
            )
        credential = await self.store.get_credential(account.credential_id)
        if not credential:
            await self.store.set_account_status(
                account.account_id,
                account.status,
                credential_status=CREDENTIAL_MISSING,
                last_error="credential record is missing",
            )
            return self._needs_user_action(
                reason=REASON_RECONNECT_REQUIRED,
                provider_id=provider_key,
                claim=claim_key,
                connector_app_id=connector_key,
                account_id=account.account_id,
                message="The connected account credential is missing. Reconnect the account in Connection Hub.",
            )
        credential = await self._refresh_credential_if_needed(
            provider_id=provider_key,
            claim=claim_key,
            connector_app_id=account.connector_app_id or connector_key,
            account_id=account.account_id,
            credential_id=account.credential_id,
            credential=credential,
            force=force_refresh,
        )
        if credential is None:
            # Health transition is user-visible: Connection Hub must stop
            # showing this account as healthy.
            await self.store.set_account_status(
                account.account_id,
                account.status,
                credential_status=CREDENTIAL_RECONNECT_REQUIRED,
                last_error="credential expired and could not be refreshed",
            )
            return self._needs_user_action(
                reason=REASON_RECONNECT_REQUIRED,
                provider_id=provider_key,
                claim=claim_key,
                connector_app_id=account.connector_app_id or connector_key,
                account_id=account.account_id,
                message=(
                    "The connected account authorization expired and could not "
                    "be refreshed. Reconnect the account in Connection Hub."
                ),
            )
        handle = CredentialHandle(
            provider_id=provider_key,
            account_id=account.account_id,
            credential_id=account.credential_id,
            claims=account.claims,
            credential=credential,
        )
        LOGGER.info(
            "[delegated.broker] verdict=ok claim=%s provider=%s account=%s claims=%s",
            claim_key, provider_key, account.account_id, ",".join(account.claims),
        )
        return ClaimResolution(
            ok=True,
            provider_id=provider_key,
            claim=claim_key,
            connector_app_id=connector_key,
            account_id=account.account_id,
            credential=handle,
        )

    async def _resolve_client_secret(self, *, provider_id: str, connector_app_id: str, connector_app: Any) -> str:
        if self.client_secret_resolver is None:
            return ""
        value = self.client_secret_resolver(
            provider_id=provider_id,
            connector_app_id=connector_app_id,
            connector_app=connector_app,
        )
        if hasattr(value, "__await__"):
            value = await value
        return as_str(value)

    async def _refresh_credential_if_needed(
        self,
        *,
        provider_id: str,
        claim: str,
        connector_app_id: str,
        account_id: str,
        credential_id: str,
        credential: dict[str, Any],
        force: bool = False,
    ) -> dict[str, Any] | None:
        provider = self.config.provider(provider_id)
        if provider is None or not provider.adapter:
            return None if force else credential
        try:
            resolved = resolve_adapter(provider.adapter)
        except Exception:
            return None if force else credential
        connector_app = provider.connector_apps.get(connector_app_id)
        adapter = resolved.bind(provider=provider, connector_app=connector_app)
        if not force and not adapter.credential_refresh_needed(credential, skew_seconds=self.refresh_skew_seconds):
            return credential
        if not adapter.credential_refreshable(credential):
            return None
        if connector_app is None:
            return None
        client_secret = await self._resolve_client_secret(
            provider_id=provider_id,
            connector_app_id=connector_app_id,
            connector_app=connector_app,
        )
        if not client_secret:
            return None
        try:
            refreshed = await adapter.refresh_credential(
                credential,
                client_id=connector_app.client_id,
                client_secret=client_secret,
            )
        except Exception:
            return None
        refreshed.update(
            {
                "provider_id": provider_id,
                "connector_app_id": connector_app_id,
                "claims": list(as_str_list(credential.get("claims")) or (claim,)),
                "account_id": account_id,
            }
        )
        await self.store.set_credential(credential_id, refreshed)
        # A successful refresh supersedes any persisted rejection: Connection
        # Hub must stop telling the user to reconnect a working account.
        await self.store.set_account_status(
            account_id,
            "",
            credential_status=CREDENTIAL_ACTIVE,
        )
        return refreshed

    async def ensure_tool_claims(
        self,
        *,
        policy: ToolClaimPolicy,
        account_ids: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Resolve all connected-account claims declared by application code."""
        tool_key = as_str(policy.tool_name)
        if not tool_key:
            return {
                "ok": False,
                "tool_name": tool_key,
                "error": "tool_policy_required",
                "message": "A tool claim policy is required.",
            }
        selections = account_ids or {}
        resolved: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for index, requirement in enumerate(policy.connected_accounts):
            selection_key = (
                requirement.account_id
                or selections.get(requirement.provider_id)
                or selections.get(f"{requirement.provider_id}:{requirement.connector_app_id}")
                or ""
            )
            for claim in requirement.claims:
                result = await self.ensure_claim(
                    provider_id=requirement.provider_id,
                    connector_app_id=requirement.connector_app_id,
                    claim=claim,
                    account_id=selection_key,
                )
                item = {
                    "requirement_index": index,
                    **result.to_dict(include_credential=False),
                }
                if result.ok:
                    resolved.append(item)
                else:
                    failures.append(item)
        if failures:
            return {
                "ok": False,
                "tool_name": tool_key,
                "error": "connection_required",
                "requirements": [item.to_dict() for item in policy.connected_accounts],
                "resolved": resolved,
                "failures": failures,
            }
        return {
            "ok": True,
            "tool_name": tool_key,
            "requirements": [item.to_dict() for item in policy.connected_accounts],
            "resolved": resolved,
        }

    def _needs_user_action(
        self,
        *,
        reason: str,
        provider_id: str,
        claim: str,
        connector_app_id: str = "",
        account_id: str = "",
        message: str = "",
        candidates: tuple[dict[str, Any], ...] = (),
    ) -> ClaimResolution:
        """One user-fixable resolution failure. ``reason`` is a REASON_*
        constant; retrying after the Connection Hub action should succeed,
        hence retry_hint=True."""
        LOGGER.info(
            "[delegated.broker] verdict=%s claim=%s provider=%s connector=%s account=%s: %s",
            reason, claim, provider_id, connector_app_id or "-", account_id or "-", message,
        )
        return ClaimResolution(
            ok=False,
            provider_id=provider_id,
            claim=claim,
            connector_app_id=connector_app_id,
            account_id=account_id,
            error=reason,
            message=message,
            candidates=candidates,
            retry_hint=True,
        )


def broker_for_user(
    *,
    user_id: str,
    config: DelegatedToKdcubeConfig,
    bundle_id: str = "",
    store: DelegatedToKdcubeStore | None = None,
    client_secret_resolver: Callable[..., Any] | None = None,
) -> DelegatedToKdcubeBroker:
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.models import CONNECTION_HUB_BUNDLE_ID

    resolved_store = store or DelegatedToKdcubeStore(
        user_id=user_id,
        bundle_id=as_str(bundle_id) or CONNECTION_HUB_BUNDLE_ID,
    )
    return DelegatedToKdcubeBroker(
        config=config,
        store=resolved_store,
        client_secret_resolver=client_secret_resolver,
    )


__all__ = ["DelegatedToKdcubeBroker", "broker_for_user"]
