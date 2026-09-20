# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Public `connections` provider base.

``ConnectionsProviderBase`` implements the named-service methods that the
framework dispatches to (``connection.get_token`` → ``connection_get_token``,
etc.). Each method parses the ``NamedServiceRequest``, calls an abstract async
hook, and wraps the result in ``NamedServiceResponse.ok_response`` /
``error_response``.

A bundle subclasses this, decorates the subclass with ``named_service_provider``
(supplying ``build_connection_operations(...)``), and implements the abstract
hooks against its chosen storage. No storage choice lives in this module.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceContext,
    NamedServiceProvider,
    NamedServiceRequest,
    NamedServiceResponse,
)

from connection_hub.contract import (
    NAMESPACE,
    AmbiguousConnectionAccount,
    CatalogEntry,
    Connection,
    ConnectionToken,
)


def _provider_name(request: NamedServiceRequest) -> str:
    payload = dict(request.payload or {})
    return str(payload.get("provider") or request.provider or "").strip()


class ConnectionsProviderBase(NamedServiceProvider):
    """Named-service provider for the `connections` namespace.

    Subclasses implement the abstract hooks; the named-service method wrappers
    here are stable and transport-neutral.
    """

    # ── named-service methods (dispatched by operation name) ────────────────

    async def connection_catalog(
        self,
        ctx: NamedServiceContext,
        request: NamedServiceRequest,
    ) -> NamedServiceResponse:
        try:
            entries = await self.list_catalog(ctx)
        except Exception as exc:  # pragma: no cover - defensive wrapper
            return self._error("connections_catalog_failed", str(exc))
        items = [CatalogEntry.coerce(entry).to_dict() for entry in (entries or [])]
        return NamedServiceResponse.ok_response(
            provider=self.provider_identity(),
            namespace=NAMESPACE,
            items=items,
        )

    async def connection_status(
        self,
        ctx: NamedServiceContext,
        request: NamedServiceRequest,
    ) -> NamedServiceResponse:
        provider = _provider_name(request)
        if not provider:
            return self._error("connections_provider_required", "provider is required", status=400)
        try:
            result = await self.status(ctx, provider=provider)
        except Exception as exc:
            return self._error("connections_status_failed", str(exc))
        return NamedServiceResponse.ok_response(
            provider=self.provider_identity(),
            namespace=NAMESPACE,
            object=dict(result or {}),
        )

    async def connection_get_token(
        self,
        ctx: NamedServiceContext,
        request: NamedServiceRequest,
    ) -> NamedServiceResponse:
        payload = dict(request.payload or {})
        provider = _provider_name(request)
        account_id = str(payload.get("account_id") or "").strip() or None
        if not provider:
            return self._error("connections_provider_required", "provider is required", status=400)
        try:
            token = await self.get_token(ctx, provider=provider, account_id=account_id)
        except AmbiguousConnectionAccount as exc:
            # Multiple connected accounts (e.g. several Slack workspaces) and no
            # account_id — the caller must choose. Surface the candidates.
            return NamedServiceResponse.error_response(
                code="connections_account_required",
                message=str(exc),
                status=409,
                provider=self.provider_identity(),
                namespace=NAMESPACE,
                details={"provider": exc.provider, "account_ids": exc.account_ids},
            )
        except Exception as exc:
            return self._error("connections_get_token_failed", str(exc))
        if token is None:
            return NamedServiceResponse.ok_response(
                provider=self.provider_identity(),
                namespace=NAMESPACE,
                object={},
                attrs={"has_token": False},
            )
        return NamedServiceResponse.ok_response(
            provider=self.provider_identity(),
            namespace=NAMESPACE,
            object=ConnectionToken.coerce(token).to_dict(),
            attrs={"has_token": True},
        )

    async def connection_disconnect(
        self,
        ctx: NamedServiceContext,
        request: NamedServiceRequest,
    ) -> NamedServiceResponse:
        payload = dict(request.payload or {})
        provider = _provider_name(request)
        account_id = str(payload.get("account_id") or "").strip()
        if not provider:
            return self._error("connections_provider_required", "provider is required", status=400)
        if not account_id:
            return self._error("connections_account_required", "account_id is required", status=400)
        try:
            result = await self.disconnect(ctx, provider=provider, account_id=account_id)
        except Exception as exc:
            return self._error("connections_disconnect_failed", str(exc))
        return NamedServiceResponse.ok_response(
            provider=self.provider_identity(),
            namespace=NAMESPACE,
            object=dict(result or {}),
        )

    async def oauth_start(
        self,
        ctx: NamedServiceContext,
        request: NamedServiceRequest,
    ) -> NamedServiceResponse:
        payload = dict(request.payload or {})
        provider = _provider_name(request)
        return_hint = str(payload.get("return_hint") or "").strip()
        app_id = str(payload.get("app_id") or "").strip() or None
        # Optional per-connect scope override — a scenario can request a SUBSET of
        # the client app's configured scopes (the admin ceiling).
        raw_scopes = payload.get("scopes")
        scopes = None
        if isinstance(raw_scopes, (list, tuple)):
            scopes = [str(s).strip() for s in raw_scopes if str(s).strip()]
        elif isinstance(raw_scopes, str) and raw_scopes.strip():
            scopes = [s.strip() for s in raw_scopes.replace(",", " ").split() if s.strip()]
        if not provider:
            return self._error("connections_provider_required", "provider is required", status=400)
        try:
            result = await self.start_oauth(ctx, provider=provider, app_id=app_id, scopes=scopes, return_hint=return_hint)
        except Exception as exc:
            return self._error("connections_oauth_start_failed", str(exc))
        result = dict(result or {})
        if not result.get("authorize_url"):
            return self._error(
                "connections_oauth_start_failed",
                str(result.get("error") or "OAuth start did not return an authorize_url"),
            )
        return NamedServiceResponse.ok_response(
            provider=self.provider_identity(),
            namespace=NAMESPACE,
            object=result,
        )

    async def agent_grant_get_token(
        self,
        ctx: NamedServiceContext,
        request: NamedServiceRequest,
    ) -> NamedServiceResponse:
        payload = dict(request.payload or {})
        client_id = str(payload.get("client_id") or "").strip()
        resource = str(payload.get("resource") or "").strip()
        access_id = str(payload.get("access_id") or "").strip()
        resident_profile_lookup = bool(access_id)
        if not client_id or (not resident_profile_lookup and not resource):
            return self._error(
                "connections_agent_grant_args_required",
                (
                    "client_id and either resource or resident profile "
                    "access_id are required"
                ),
                status=400,
            )
        try:
            if resident_profile_lookup:
                resident = await self.resident_agent_grant_for_access_id(
                    ctx,
                    client_id=client_id,
                    access_id=access_id,
                )
                if resident is None:
                    token = None
                else:
                    raw = dict(resident)
                    token_value = str(raw.get("access_token") or "").strip()
                    card = raw.get("card")
                    if (
                        not token_value
                        or str(raw.get("client_id") or "").strip() != client_id
                        or not str(raw.get("access_id") or "").strip()
                        or not isinstance(card, Mapping)
                    ):
                        raise ValueError("resident agent grant contract is invalid")
                    token = {
                        "access_token": token_value,
                        "expires_at": str(raw.get("expires_at") or "") or None,
                        "access_id": str(raw["access_id"]),
                        "client_id": client_id,
                        "identity_scope": str(
                            raw.get("identity_scope") or "grantor"
                        ),
                        "card_revision": int(raw.get("card_revision") or 0),
                        "card": dict(card),
                    }
            else:
                token = await self.agent_grant_token(
                    ctx,
                    client_id=client_id,
                    resource=resource,
                )
        except Exception as exc:
            message = (
                "resident Card resolution failed"
                if resident_profile_lookup
                else str(exc)
            )
            return self._error("connections_agent_grant_token_failed", message)
        if token is None:
            # Consent pending: the user has not granted THIS agent the resource.
            return NamedServiceResponse.ok_response(
                provider=self.provider_identity(),
                namespace=NAMESPACE,
                object={},
                attrs={"has_token": False},
            )
        if resident_profile_lookup:
            result = dict(token)
        else:
            result = ConnectionToken.coerce(token).to_dict()
        return NamedServiceResponse.ok_response(
            provider=self.provider_identity(),
            namespace=NAMESPACE,
            object=result,
            attrs={"has_token": True},
        )

    async def agent_grant_check(
        self,
        ctx: NamedServiceContext,
        request: NamedServiceRequest,
    ) -> NamedServiceResponse:
        payload = dict(request.payload or {})
        client_id = str(payload.get("client_id") or "").strip()
        namespace = str(payload.get("namespace") or "").strip()
        operation = str(payload.get("operation") or "").strip()
        access_id = str(payload.get("access_id") or "").strip()
        delegate_identity = str(payload.get("delegate_identity") or "").strip()
        if not client_id or not namespace or not operation:
            return self._error(
                "connections_agent_grant_args_required",
                "client_id, namespace, and operation are required", status=400,
            )
        try:
            selector: dict[str, str] = {}
            if access_id:
                selector["access_id"] = access_id
            if delegate_identity:
                selector["delegate_identity"] = delegate_identity
            state = await self.agent_grant_state(
                ctx,
                client_id=client_id,
                namespace=namespace,
                operation=operation,
                **selector,
            )
        except Exception as exc:
            return self._error("connections_agent_grant_check_failed", str(exc))
        return NamedServiceResponse.ok_response(
            provider=self.provider_identity(),
            namespace=NAMESPACE,
            object=dict(state or {"governed": False}),
        )

    async def agent_capability_sync(
        self,
        ctx: NamedServiceContext,
        request: NamedServiceRequest,
    ) -> NamedServiceResponse:
        payload = dict(request.payload or {})
        required_strings = ("application", "agent_id", "descriptor_revision")
        missing = [
            key
            for key in required_strings
            if not str(payload.get(key) or "").strip()
        ]
        if not isinstance(payload.get("descriptor_payload"), Mapping):
            missing.append("descriptor_payload")
        if not isinstance(payload.get("capability_authority"), Mapping):
            missing.append("capability_authority")
        if missing:
            return self._error(
                "connections_agent_capability_args_required",
                f"Required capability sync fields are missing or invalid: {', '.join(missing)}",
                status=400,
            )
        try:
            result = dict(
                await self.sync_agent_capabilities(
                    ctx,
                    payload=payload,
                )
                or {}
            )
        except Exception as exc:
            return self._error("connections_agent_capability_sync_failed", str(exc))
        if result.get("ok") is not True:
            status = int(result.get("status") or 400)
            code = str(result.get("error") or "connections_agent_capability_sync_failed")
            message = str(result.get("message") or result.get("reason") or code)
            return NamedServiceResponse.error_response(
                code=code,
                message=message,
                status=status,
                details=result,
                provider=self.provider_identity(),
                namespace=NAMESPACE,
            )
        return NamedServiceResponse.ok_response(
            provider=self.provider_identity(),
            namespace=NAMESPACE,
            object=result,
        )

    # ── error helper ─────────────────────────────────────────────────────────

    def _error(self, code: str, message: str, *, status: int = 500) -> NamedServiceResponse:
        return NamedServiceResponse.error_response(
            code=code,
            message=message,
            status=status,
            provider=self.provider_identity(),
            namespace=NAMESPACE,
        )

    # ── abstract hooks the bundle implements ────────────────────────────────

    @abstractmethod
    async def get_token(
        self,
        ctx: NamedServiceContext,
        *,
        provider: str,
        account_id: str | None = None,
    ) -> ConnectionToken | None:
        """Return the access token for (user, provider, optional account_id)."""
        raise NotImplementedError

    async def agent_grant_token(
        self,
        ctx: NamedServiceContext,
        *,
        client_id: str,
        resource: str,
    ) -> ConnectionToken | None:
        """Return the consented bearer of the caller's per-agent delegated grant
        for (user, client_id, resource), or ``None`` when consent is pending.

        Concrete default: no grant (``None``). A bundle that backs per-agent
        delegated grants (the Connection Hub) overrides this over its automation
        access store; providers that do not simply expose no agent grants."""
        return None

    async def resident_agent_grant_for_access_id(
        self,
        ctx: NamedServiceContext,
        *,
        client_id: str,
        access_id: str,
    ) -> Mapping[str, Any] | None:
        """Return one resident profile's exact card-bound credential.

        The default exposes no resident authority. Connection Hub implements
        this against its live Card service; the response includes the public
        Card view observed with the credential so runtime projection can
        reject a subsequent Gateway observation race.
        """

        return None

    async def agent_grant_state(
        self,
        ctx: NamedServiceContext,
        *,
        client_id: str,
        namespace: str,
        operation: str,
        access_id: str = "",
        delegate_identity: str = "",
    ) -> dict[str, Any]:
        """Resolve delegated admission for one named-service invocation.

        The result is ``{"governed": False}`` when this provider publishes no
        matching delegated capability, or ``{"governed": True, "granted":
        bool, "resource", "claims"}`` when it does.

        Concrete default: ungoverned. The admission caller interprets that
        state according to its positively selected authority mode; a delegated
        invocation does not become application-owned when this provider has no
        delegated-grant catalog."""
        del client_id, namespace, operation, access_id, delegate_identity
        return {"governed": False}

    async def sync_agent_capabilities(
        self,
        ctx: NamedServiceContext,
        *,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Synchronize a trusted host's descriptor ceiling.

        Only the Connection Hub host implements this mutation. Other providers
        keep the portable contract concrete while refusing the operation.
        """

        del ctx, payload
        return {
            "ok": False,
            "error": "agent_capability_sync_not_supported",
            "status": 501,
        }

    @abstractmethod
    async def list_catalog(self, ctx: NamedServiceContext) -> list[CatalogEntry]:
        """List registered providers plus the user's connected state."""
        raise NotImplementedError

    @abstractmethod
    async def status(self, ctx: NamedServiceContext, *, provider: str) -> dict[str, Any]:
        """Status for one provider."""
        raise NotImplementedError

    @abstractmethod
    async def disconnect(
        self,
        ctx: NamedServiceContext,
        *,
        provider: str,
        account_id: str,
    ) -> dict[str, Any]:
        """Disconnect an account."""
        raise NotImplementedError

    @abstractmethod
    async def start_oauth(
        self,
        ctx: NamedServiceContext,
        *,
        provider: str,
        app_id: str | None = None,
        scopes: list[str] | None = None,
        return_hint: str = "",
    ) -> dict[str, Any]:
        """Begin OAuth through a client app; return at least {"authorize_url": ...}.

        `app_id` selects the client app; required when a provider has more than
        one enabled app, defaulted when it has exactly one. `scopes` is an optional
        per-connect subset of that client app's configured scopes (the admin
        ceiling) — a scenario can request less consent, never more."""
        raise NotImplementedError


__all__ = ["ConnectionsProviderBase"]
