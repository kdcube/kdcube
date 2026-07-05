# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Connection Hub request authentication surface.

This module is intentionally middleware-facing. It can run before a platform
session exists: the gateway passes a raw request envelope to Connection Hub.
Connection Hub provider modules verify any recognized proof using Connection Hub
config, secrets, and connection-edge data. This surface converts the returned
authority into a normal ``UserSession`` for the rest of the gateway.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

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
from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_projection import (
    authority_has_platform_privilege,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.config import (
    oauth_delegated_config,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.surface_guard import (
    authorize_delegated_rest_request,
    delegated_platform_admin_runtime_projection,
    delegated_request_resource,
    delegated_rest_runtime_projection,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.request_auth import SessionFactory
from kdcube_ai_app.auth.AuthManager import AuthenticationError, AuthorizationError, PAID_ROLES
from kdcube_ai_app.auth.sessions import RequestContext, UserSession, UserType
from kdcube_ai_app.infra.plugin.bundle_store import (
    _get_bundle_props_from_authority as get_bundle_props_from_authority,
)

logger = logging.getLogger(__name__)

DEFAULT_CONNECTION_HUB_BUNDLE_ID = "connection-hub@1-0"
DEFAULT_CONNECTION_HUB_AUTH_OPERATION = "request_authenticate"
DEFAULT_DELEGATED_AUTHORITY_ID = "delegated_client"


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
        or summary.get("has_telegram_init_data")
        or summary.get("has_provider_signature")
    )


def _should_trace_auth_attempt(summary: Mapping[str, Any]) -> bool:
    return _should_attempt_connection_hub(summary)


def _roles_user_type(roles: list[str] | tuple[str, ...] | None) -> UserType:
    role_set = set(roles or [])
    if authority_has_platform_privilege(role_set):
        return UserType.PRIVILEGED
    if PAID_ROLES & role_set:
        return UserType.PAID
    if role_set:
        return UserType.REGISTERED
    return UserType.EXTERNAL


def _auth_error_from_denial(denial: JSONResponse) -> Exception:
    status_code = getattr(denial, "status_code", 500)
    raw = getattr(denial, "body", b"") or b""
    message = ""
    try:
        import json

        payload = json.loads(raw.decode("utf-8")) if raw else {}
        if isinstance(payload, Mapping):
            message = str(payload.get("error_description") or payload.get("detail") or payload.get("error") or "")
    except Exception:
        message = ""
    if not message:
        message = f"delegated credential rejected with status {status_code}"
    if status_code == 401:
        return AuthenticationError(message)
    return AuthorizationError(message)


class ConnectionHubAuthenticationSurface:
    """Gateway-facing authentication surface backed by the Connection Hub app.

    Connection Hub owns the selector inside this surface: it decides whether
    the request carries enough external auth material, which configured
    authenticator can verify it, and which linked authority should be projected
    onto the resulting session.
    """

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
    ) -> "ConnectionHubAuthenticationSurface | None":
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
        delegated_session = await self._try_delegated_platform_bearer(request, context, session_factory)
        if delegated_session is not None:
            return delegated_session

        include_body = self._should_include_body(request)
        envelope = await RequestEnvelope.from_request(request, include_body=include_body)
        summary = _external_auth_summary(envelope)
        has_selector_hint = _should_attempt_connection_hub(summary)
        if not has_selector_hint:
            logger.debug(
                "[auth.connection_hub.surface] skipped tenant=%s project=%s bundle=%s method=%s path=%s reason=no_external_auth_material",
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
                "[auth.connection_hub.surface] start tenant=%s project=%s bundle=%s operation=%s method=%s path=%s authority_id=%s authenticator_id=%s provider_hint=%s has_telegram_init_data=%s has_provider_signature=%s has_authorization=%s has_cookie=%s include_body=%s",
                self.tenant,
                self.project,
                self.bundle_id,
                self.operation,
                envelope.method,
                envelope.path,
                summary.get("authority_id") or "",
                summary.get("authenticator_id") or "",
                summary.get("provider") or "",
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
                    "[auth.connection_hub.surface] failed tenant=%s project=%s bundle=%s operation=%s provider_hint=%s authority_id=%s authenticator_id=%s error=%s",
                    self.tenant,
                    self.project,
                    self.bundle_id,
                    self.operation,
                    summary.get("provider") or "",
                    summary.get("authority_id") or "",
                    summary.get("authenticator_id") or "",
                    exc,
                )
            raise
        authenticated = AuthenticatedRequest.coerce(response)
        if not (authenticated.ok and authenticated.authenticated):
            if trace:
                logger.info(
                    "[auth.connection_hub.surface] declined tenant=%s project=%s provider=%s authority_id=%s selected_authenticator=%s ok=%s authenticated=%s error=%s message=%s",
                    self.tenant,
                    self.project,
                    authenticated.provider or summary.get("provider") or "",
                    authenticated.authority_id or summary.get("authority_id") or "",
                    authenticated.selected_authenticator or "",
                    authenticated.ok,
                    authenticated.authenticated,
                    authenticated.error or "",
                    authenticated.message or "",
                )
            return None

        authority = dict(authenticated.identity_authority or {})
        if authenticated.authority_id and not authority.get("authority_id"):
            authority["authority_id"] = authenticated.authority_id
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
        platform_user_id = _str(authenticated.platform_user_id or authority.get("platform_user_id"))
        roles = list(authority.get("platform_roles") or [])
        permissions = list(authority.get("platform_permissions") or [])
        session_type = (
            UserType.PRIVILEGED
            if authority_has_platform_privilege(roles)
            else UserType.REGISTERED
            if platform_user_id
            else UserType.EXTERNAL
        )
        user_data = {
            "user_id": actor_user_id,
            "username": actor_user_id,
            "roles": roles,
            "permissions": permissions,
            "identity_authority": authority,
        }
        session = await session_factory(context, session_type, user_data)
        session.identity_authority = authority
        if trace:
            logger.info(
                "[auth.connection_hub.surface] accepted tenant=%s project=%s provider=%s authority_id=%s selected_authenticator=%s actor_user_id=%s platform_user_present=%s session_label=%s roles=%s",
                self.tenant,
                self.project,
                authenticated.provider or summary.get("provider") or "",
                authenticated.authority_id or summary.get("authority_id") or "",
                authenticated.selected_authenticator or "",
                actor_user_id,
                bool(platform_user_id),
                session_type.value,
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
                identity_authority={},
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

    def _delegated_oauth_raw_config(self, request: Request) -> dict[str, Any]:
        props = get_bundle_props_from_authority(
            tenant=self.tenant,
            project=self.project,
            bundle_id=self.bundle_id,
        )
        props = props if isinstance(props, Mapping) else {}
        connections = props.get("connections")
        connections = connections if isinstance(connections, Mapping) else {}
        delegated = connections.get("delegated_credentials")
        delegated = delegated if isinstance(delegated, Mapping) else {}
        raw = delegated.get("oauth")
        cfg = dict(raw) if isinstance(raw, Mapping) else {}
        cfg["tenant"] = self.tenant
        cfg["project"] = self.project
        cfg.setdefault("enabled", False)
        if not cfg.get("issuer"):
            path = (
                f"/api/integrations/bundles/{self.tenant}/{self.project}/"
                f"{self.bundle_id}/public/oauth"
            )
            try:
                headers = request.headers
                proto = str(headers.get("x-forwarded-proto") or request.url.scheme or "http").split(",", 1)[0].strip()
                host = str(headers.get("x-forwarded-host") or headers.get("host") or request.url.netloc or "").split(",", 1)[0].strip()
                if host:
                    cfg["issuer"] = f"{proto}://{host}{path}".rstrip("/")
            except Exception:
                pass
        return cfg

    def _bind_delegated_oauth_config(self, request: Request) -> Any:
        cfg = self._delegated_oauth_raw_config(request)
        request.state.oauth_delegated_config = cfg
        return oauth_delegated_config(request)

    def _delegated_platform_resource_config(self, request: Request) -> Any:
        cfg = self._bind_delegated_oauth_config(request)
        if not cfg.enabled:
            return None
        resource = delegated_request_resource(request)
        return cfg.resource_config(resource)

    def _delegated_platform_operation(self, request: Request) -> str:
        resource_cfg = self._delegated_platform_resource_config(request)
        if resource_cfg is None:
            return ""
        cfg = self._bind_delegated_oauth_config(request)
        operations = cfg.resource_operation_catalog(delegated_request_resource(request))
        if not operations:
            return ""
        if len(operations) != 1:
            raise AuthorizationError(
                "delegated platform resource has ambiguous operation catalog: "
                f"{delegated_request_resource(request)}"
        )
        return str(operations[0].name or "").strip()

    def _delegated_platform_all_resources_enabled(self, request: Request) -> bool:
        resource_cfg = self._delegated_platform_resource_config(request)
        if resource_cfg is None:
            return False
        return bool(getattr(resource_cfg, "admin_only", False) and str(resource_cfg.resource or "").strip() == "*")

    async def _session_from_delegated_projection(
        self,
        *,
        request: Request,
        context: RequestContext,
        session_factory: SessionFactory,
        projection: Mapping[str, Any],
    ) -> UserSession:
        roles = list(projection.get("roles") or [])
        permissions = list(projection.get("permissions") or [])
        identity_authority = dict(projection.get("identity_authority") or {})
        user_id = _str(projection.get("user_id"))
        if not user_id:
            raise AuthorizationError("delegated credential did not resolve a platform user")
        user_data = {
            "user_id": user_id,
            "username": projection.get("username") or user_id,
            "roles": roles,
            "permissions": permissions,
            "identity_authority": identity_authority,
        }
        session = await session_factory(context, _roles_user_type(roles), user_data)
        session.identity_authority = identity_authority
        logger.info(
            "[auth.connection_hub.surface] accepted delegated platform bearer tenant=%s project=%s resource=%s user_id=%s roles=%s grants=%s",
            self.tenant,
            self.project,
            delegated_request_resource(request),
            user_id,
            roles,
            projection.get("grants") or [],
        )
        return session

    async def _try_delegated_platform_bearer(
        self,
        request: Request,
        context: RequestContext,
        session_factory: SessionFactory,
    ) -> Optional[UserSession]:
        auth_header = str(request.headers.get("authorization") or "").strip()
        if not auth_header.lower().startswith("bearer "):
            return None
        operation = self._delegated_platform_operation(request)
        if operation:
            endpoint_auth = {
                "mode": "managed",
                "authority_id": DEFAULT_DELEGATED_AUTHORITY_ID,
                "selected_operation_grants": True,
            }
            denial = await authorize_delegated_rest_request(
                request=request,
                auth=endpoint_auth,
                operation=operation,
                method=request.method,
            )
            if denial is not None:
                raise _auth_error_from_denial(denial)
            projection = delegated_rest_runtime_projection(request)
            if not projection:
                raise AuthorizationError("delegated credential did not produce a runtime projection")
            return await self._session_from_delegated_projection(
                request=request,
                context=context,
                session_factory=session_factory,
                projection=projection,
            )

        if not self._delegated_platform_all_resources_enabled(request):
            return None
        projection = await delegated_platform_admin_runtime_projection(
            request,
            authority_id=DEFAULT_DELEGATED_AUTHORITY_ID,
        )
        if not projection:
            return None
        return await self._session_from_delegated_projection(
            request=request,
            context=context,
            session_factory=session_factory,
            projection=projection,
        )


def maybe_install_connection_hub_authentication_surface(
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
            "Connection Hub authentication surface disabled tenant=%s project=%s config_present=%s",
            tenant,
            project,
            bool(cfg),
        )
        return False
    surface = ConnectionHubAuthenticationSurface.from_descriptors(
        redis=redis,
        pg_pool=pg_pool,
        tenant=tenant,
        project=project,
    )
    if surface is None:
        return False
    gateway_adapter.install_connection_hub_authentication_surface(surface)
    logger.info(
        "Connection Hub authentication surface installed tenant=%s project=%s bundle=%s operation=%s",
        tenant,
        project,
        surface.bundle_id,
        surface.operation,
    )
    return True


__all__ = [
    "ConnectionHubAuthenticationSurface",
    "connection_hub_auth_enabled",
    "maybe_install_connection_hub_authentication_surface",
]
