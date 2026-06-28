# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Connection Hub request-auth bridge.

This module is intentionally middleware-facing. It can run before a platform
session exists: the gateway passes a raw request envelope to Connection Hub.
Connection Hub provider modules verify any recognized proof using Connection Hub
config, secrets, and identity-link data. This bridge converts the returned
authority into a normal ``UserSession`` for the rest of the gateway.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from fastapi import Request

from kdcube_ai_app.apps.chat.sdk.config import get_plain
from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import (
    BundleOperationCall,
    invoke_local_bundle_operation,
)
from kdcube_ai_app.apps.chat.sdk.protocol import (
    ExternalEventActor,
    ExternalEventPayload,
    ExternalEventRequest,
    ExternalEventRouting,
    ExternalEventUser,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.authenticators.authority import AuthRequestHints
from kdcube_ai_app.apps.chat.sdk.solutions.connections.authenticators.models import (
    AuthenticatedRequest,
    RequestEnvelope,
)
from kdcube_ai_app.apps.middleware.request_auth import SessionFactory
from kdcube_ai_app.auth.sessions import RequestContext, UserSession, UserType

logger = logging.getLogger(__name__)

DEFAULT_CONNECTION_HUB_BUNDLE_ID = "connection-hub@1-0"
DEFAULT_CONNECTION_HUB_AUTH_OPERATION = "request_authenticate"


def _str(value: Any) -> str:
    return str(value or "").strip()


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _user_type(value: Any, default: str = "registered") -> UserType:
    raw = _str(getattr(value, "value", value)).lower() or _str(default).lower() or "registered"
    if raw == "admin":
        raw = "privileged"
    try:
        return UserType(raw)
    except Exception:
        return UserType.REGISTERED


def _authenticator_config() -> dict[str, Any]:
    cfg = get_plain("auth.authenticators.connection_hub", default=None)
    if not isinstance(cfg, Mapping):
        cfg = get_plain("auth.connection_hub", default=None)
    return dict(cfg or {}) if isinstance(cfg, Mapping) else {}


def connection_hub_auth_enabled() -> bool:
    return _bool(_authenticator_config().get("enabled"), default=False)


def _first(*values: Any) -> str:
    for value in values:
        text = _str(value)
        if text:
            return text
    return ""


def _external_auth_summary(envelope: RequestEnvelope) -> dict[str, Any]:
    hints = AuthRequestHints.from_envelope(envelope)
    headers = envelope.headers or {}
    query = envelope.query or {}
    has_telegram_init_data = bool(
        _first(
            headers.get("x-telegram-init-data"),
            headers.get("telegram-init-data"),
            headers.get("x-kdcube-telegram-init-data"),
            query.get("telegram_init_data"),
            query.get("tgwebappdata"),
        )
    )
    has_provider_signature = bool(
        _first(
            headers.get("x-slack-signature"),
            headers.get("x-hub-signature"),
            headers.get("x-hub-signature-256"),
            headers.get("x-kdcube-webhook-signature"),
            headers.get("x-kdcube-api-key"),
        )
    )
    return {
        "authority_id": hints.authority_id,
        "authenticator_id": hints.authenticator_id,
        "provider": hints.provider,
        "integration_id": hints.integration_id or hints.connection_id,
        "has_telegram_init_data": has_telegram_init_data,
        "has_provider_signature": has_provider_signature,
        "has_authorization": bool(headers.get("authorization")),
        "has_cookie": bool(headers.get("cookie") or envelope.cookies),
    }


def _should_attempt_connection_hub(summary: Mapping[str, Any]) -> bool:
    return bool(
        summary.get("authority_id")
        or summary.get("authenticator_id")
        or summary.get("provider")
        or summary.get("integration_id")
        or summary.get("has_telegram_init_data")
        or summary.get("has_provider_signature")
    )


def _should_trace_auth_attempt(summary: Mapping[str, Any]) -> bool:
    return _should_attempt_connection_hub(summary)


class ConnectionHubRequestAuthBridge:
    """Gateway selector bridge backed by the Connection Hub app."""

    def __init__(
        self,
        *,
        redis: Any,
        pg_pool: Any,
        tenant: str,
        project: str,
        bundle_id: str = DEFAULT_CONNECTION_HUB_BUNDLE_ID,
        operation: str = DEFAULT_CONNECTION_HUB_AUTH_OPERATION,
    ) -> None:
        self.redis = redis
        self.pg_pool = pg_pool
        self.tenant = tenant
        self.project = project
        self.bundle_id = _str(bundle_id) or DEFAULT_CONNECTION_HUB_BUNDLE_ID
        self.operation = _str(operation) or DEFAULT_CONNECTION_HUB_AUTH_OPERATION

    @classmethod
    def from_descriptors(
        cls,
        *,
        redis: Any,
        pg_pool: Any,
        tenant: str,
        project: str,
    ) -> "ConnectionHubRequestAuthBridge | None":
        cfg = _authenticator_config()
        if not _bool(cfg.get("enabled"), default=False):
            return None
        return cls(
            redis=redis,
            pg_pool=pg_pool,
            tenant=tenant,
            project=project,
            bundle_id=_str(cfg.get("app_id") or cfg.get("bundle_id") or DEFAULT_CONNECTION_HUB_BUNDLE_ID),
            operation=_str(cfg.get("operation") or DEFAULT_CONNECTION_HUB_AUTH_OPERATION),
        )

    async def __call__(
        self,
        request: Request,
        context: RequestContext,
        session_factory: SessionFactory,
    ) -> Optional[UserSession]:
        include_body = self._should_include_body(request)
        envelope = await RequestEnvelope.from_request(request, include_body=include_body)
        summary = _external_auth_summary(envelope)
        has_selector_hint = _should_attempt_connection_hub(summary)
        if not has_selector_hint:
            logger.debug(
                "[auth.selector.connection_hub] skipped tenant=%s project=%s bridge_bundle=%s method=%s path=%s reason=no_external_auth_material",
                self.tenant,
                self.project,
                self.bundle_id,
                envelope.method,
                envelope.path,
            )
            return None
        trace = _should_trace_auth_attempt(summary)
        if trace:
            logger.info(
                "[auth.selector.connection_hub] start tenant=%s project=%s bridge_bundle=%s operation=%s method=%s path=%s authority_id=%s authenticator_id=%s provider_hint=%s integration_id=%s has_telegram_init_data=%s has_provider_signature=%s has_authorization=%s has_cookie=%s include_body=%s",
                self.tenant,
                self.project,
                self.bundle_id,
                self.operation,
                envelope.method,
                envelope.path,
                summary.get("authority_id") or "",
                summary.get("authenticator_id") or "",
                summary.get("provider") or "",
                summary.get("integration_id") or "",
                summary.get("has_telegram_init_data"),
                summary.get("has_provider_signature"),
                summary.get("has_authorization"),
                summary.get("has_cookie"),
                include_body,
            )
        try:
            response = await self._call_connection_hub(envelope)
        except Exception as exc:
            if trace:
                logger.warning(
                    "[auth.selector.connection_hub] failed tenant=%s project=%s bridge_bundle=%s operation=%s provider_hint=%s integration_id=%s error=%s",
                    self.tenant,
                    self.project,
                    self.bundle_id,
                    self.operation,
                    summary.get("provider") or "",
                    summary.get("integration_id") or "",
                    exc,
                )
            raise
        authenticated = AuthenticatedRequest.coerce(response)
        if not (authenticated.ok and authenticated.authenticated):
            if trace:
                logger.info(
                    "[auth.selector.connection_hub] declined tenant=%s project=%s provider=%s integration_id=%s selected_authenticator=%s ok=%s authenticated=%s error=%s message=%s",
                    self.tenant,
                    self.project,
                    authenticated.provider or summary.get("provider") or "",
                    authenticated.integration_id or authenticated.connection_id or summary.get("integration_id") or "",
                    authenticated.selected_authenticator or "",
                    authenticated.ok,
                    authenticated.authenticated,
                    authenticated.error or "",
                    authenticated.message or "",
                )
            return None

        authority = dict(authenticated.identity_authority or {})
        if authenticated.connection_id and not authority.get("connection_id"):
            authority["connection_id"] = authenticated.connection_id
        if authenticated.provider and not authority.get("identity_provider"):
            authority["identity_provider"] = authenticated.provider
        if authenticated.provider_subject and not authority.get("identity_provider_subject"):
            authority["identity_provider_subject"] = authenticated.provider_subject
        actor_user_id = (
            _str(authority.get("actor_user_id"))
            or _str(authenticated.actor_user_id)
            or _str(f"{authenticated.provider}_{authenticated.provider_subject}".replace(":", "_"))
        )
        if not actor_user_id:
            logger.warning("Connection Hub authenticated request without actor user id")
            return None
        roles = list(authority.get("platform_roles") or authenticated.principal.get("roles") or [])
        permissions = list(authority.get("platform_permissions") or authenticated.principal.get("permissions") or [])
        effective_user_type = _user_type(
            authority.get("economics_user_type")
            or authority.get("platform_user_type")
            or authority.get("user_type")
            or authenticated.principal.get("user_type"),
            default="registered",
        )
        user_data = {
            "user_id": actor_user_id,
            "username": actor_user_id,
            "roles": roles,
            "permissions": permissions,
            "identity_authority": authority,
        }
        session = await session_factory(context, effective_user_type, user_data)
        session.identity_authority = authority
        if trace:
            logger.info(
                "[auth.selector.connection_hub] accepted tenant=%s project=%s provider=%s integration_id=%s selected_authenticator=%s actor_user_id=%s platform_user_present=%s effective_user_type=%s roles=%s",
                self.tenant,
                self.project,
                authenticated.provider or summary.get("provider") or "",
                authenticated.integration_id or authenticated.connection_id or summary.get("integration_id") or "",
                authenticated.selected_authenticator or "",
                actor_user_id,
                bool(authenticated.platform_user_id or authority.get("platform_user_id")),
                effective_user_type.value,
                roles,
            )
        return session

    def _should_include_body(self, request: Request) -> bool:
        content_length = _str(request.headers.get("content-length"))
        try:
            size = int(content_length) if content_length else 0
        except ValueError:
            size = 0
        if size <= 0 or size > 128 * 1024:
            return False
        content_type = _str(request.headers.get("content-type")).lower()
        return any(token in content_type for token in ("json", "form", "text"))

    async def _call_connection_hub(self, envelope: RequestEnvelope) -> Mapping[str, Any]:
        comm_context = ExternalEventPayload(
            request=ExternalEventRequest(request_id="connection-hub-gateway-auth"),
            routing=ExternalEventRouting(
                session_id="",
                bundle_id=self.bundle_id,
                conversation_id="",
                turn_id="",
                socket_id="",
            ),
            actor=ExternalEventActor(tenant_id=self.tenant, project_id=self.project),
            user=ExternalEventUser(
                user_type="anonymous",
                user_id=None,
                username=None,
                email=None,
                fingerprint=None,
                roles=[],
                permissions=[],
                timezone=None,
                utc_offset_min=None,
            ),
        )
        result = await invoke_local_bundle_operation(
            BundleOperationCall(
                bundle_id=self.bundle_id,
                operation=self.operation,
                route="public",
                data={"request": envelope.to_dict()},
                tenant=self.tenant,
                project=self.project,
            ),
            comm_context=comm_context,
            redis=self.redis,
            pg_pool=self.pg_pool,
        )
        return dict(result or {})


def maybe_register_connection_hub_auth_bridge(
    gateway_adapter: Any,
    *,
    redis: Any,
    pg_pool: Any,
    tenant: str,
    project: str,
) -> bool:
    cfg = _authenticator_config()
    if not _bool(cfg.get("enabled"), default=False):
        logger.info(
            "Connection Hub request-auth bridge disabled tenant=%s project=%s config_present=%s",
            tenant,
            project,
            bool(cfg),
        )
        return False
    bridge = ConnectionHubRequestAuthBridge.from_descriptors(
        redis=redis,
        pg_pool=pg_pool,
        tenant=tenant,
        project=project,
    )
    if bridge is None:
        return False
    gateway_adapter.register_request_auth_candidate(bridge)
    logger.info(
        "Connection Hub request-auth bridge registered tenant=%s project=%s bundle=%s operation=%s",
        tenant,
        project,
        bridge.bundle_id,
        bridge.operation,
    )
    return True


__all__ = [
    "ConnectionHubRequestAuthBridge",
    "connection_hub_auth_enabled",
    "maybe_register_connection_hub_auth_bridge",
]
