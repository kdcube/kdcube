# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# middleware/gateway.py
"""
FastAPI adapter for the simplified gateway
"""

from fastapi import Request, HTTPException
from typing import List, Optional, Dict, Iterable
from pydantic import BaseModel

from kdcube_ai_app.infra.gateway.gateway import (
    RequestGateway
)
from kdcube_ai_app.infra.gateway.backpressure import BackpressureError
from kdcube_ai_app.infra.gateway.rate_limiter import RateLimitError
from kdcube_ai_app.infra.gateway.definitions import GatewayError
from kdcube_ai_app.auth.sessions import UserType, UserSession, RequestContext
from kdcube_ai_app.auth.AuthManager import RequirementBase, AuthenticationError, AuthorizationError, RequireUser, \
    RequireRoles
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.middleware.token_extract import (
    resolve_auth_from_headers,
    resolve_auth_from_headers_and_cookies,
)
import logging
import os

logger = logging.getLogger(__name__)

def _auth_debug_enabled() -> bool:
    return os.getenv("AUTH_DEBUG", "").lower() in {"1", "true", "yes", "on"}

STATE_ADMIN_CHECKED = "_gw_admin_checked"
STATE_AUTH_MODE = "_gw_auth_mode"

class CircuitBreakerStatusResponse(BaseModel):
    name: str
    state: str
    failure_count: int
    success_count: int
    total_requests: int
    total_failures: int
    consecutive_failures: int
    current_window_failures: int
    last_failure_time: Optional[float]
    last_success_time: Optional[float]
    opened_at: Optional[float]

class CircuitBreakerSummaryResponse(BaseModel):
    total_circuits: int
    open_circuits: int
    half_open_circuits: int
    closed_circuits: int

class CircuitBreakersResponse(BaseModel):
    summary: CircuitBreakerSummaryResponse
    circuits: Dict[str, CircuitBreakerStatusResponse]

STATE_FLAG = "_gw_processed"
STATE_SESSION = "user_session"
STATE_USER_TYPE = "user_type"
STATE_STREAM_ID = "stream_id"
STREAM_ID_HEADER = get_settings().RUNTIME_CONFIG.STREAM_ID_HEADER_NAME


def _state_auth_mode(header_only_auth: bool) -> str:
    return "headers_only" if header_only_auth else "default"


def _can_reuse_state_session(request: Request, *, header_only_auth: bool) -> bool:
    existing: Optional[UserSession] = getattr(request.state, STATE_SESSION, None)
    if existing is None:
        return False
    if not header_only_auth:
        return True
    return getattr(request.state, STATE_AUTH_MODE, None) == _state_auth_mode(True)


def extract_stream_id(request: Request) -> Optional[str]:
    value = request.headers.get(STREAM_ID_HEADER) or request.headers.get(STREAM_ID_HEADER.lower())
    if value:
        value = value.strip()
    return value or None


def bind_stream_id_to_request_state(request: Request) -> Optional[str]:
    stream_id = extract_stream_id(request)
    setattr(request.state, STATE_STREAM_ID, stream_id)
    return stream_id

class FastAPIGatewayAdapter:
    """FastAPI adapter for the request gateway"""

    def __init__(self, gateway: RequestGateway,
                 policy_resolver):
        self.gateway = gateway
        self.policy = policy_resolver
        self.econ_role_resolver = None

    def set_econ_role_resolver(self, resolver):
        self.econ_role_resolver = resolver
        if hasattr(self.gateway, "set_econ_role_resolver"):
            self.gateway.set_econ_role_resolver(resolver)

    def _extract_context(self, request: Request, *, header_only_auth: bool = False) -> RequestContext:
        """Extract request context from FastAPI request"""
        def _parse_int(v):
            try:
                return int(v) if v is not None else None
            except Exception:
                return None

        _auth_cfg = get_settings().AUTH
        _rc = get_settings().RUNTIME_CONFIG
        raw_auth_header = request.headers.get("authorization")
        raw_id_token = (
            request.headers.get(_auth_cfg.ID_TOKEN_HEADER_NAME)
            or request.headers.get(_auth_cfg.ID_TOKEN_HEADER_NAME.lower())
        )
        if header_only_auth:
            auth_header, id_token = resolve_auth_from_headers(
                raw_auth_header,
                raw_id_token,
            )
        else:
            auth_header, id_token = resolve_auth_from_headers_and_cookies(
                raw_auth_header,
                raw_id_token,
                request.cookies,
            )
        if _auth_debug_enabled():
            logger.info(
                "Gateway adapter: has_auth=%s has_id=%s header_only=%s path=%s",
                bool(auth_header),
                bool(id_token),
                header_only_auth,
                request.url.path,
            )

        return RequestContext(
            client_ip=request.client.host if request.client else "unknown",
            user_agent=request.headers.get("user-agent", ""),
            authorization_header=auth_header,
            id_token=id_token,
            user_timezone=request.headers.get(_rc.USER_TIMEZONE_HEADER_NAME) or request.headers.get(_rc.USER_TIMEZONE_HEADER_NAME.lower()),
            user_utc_offset_min=_parse_int(request.headers.get(_rc.USER_UTC_OFFSET_MIN_HEADER_NAME) or request.headers.get(_rc.USER_UTC_OFFSET_MIN_HEADER_NAME.lower()),)
        )

    def _extract_user_session_id(self, request: Request) -> Optional[str]:
        user_session_id = request.headers.get("User-Session-ID") or request.query_params.get("user_session_id")
        if user_session_id:
            user_session_id = user_session_id.strip()
        return user_session_id or None

    async def _enforce_user_session_ownership(self, request: Request, session: UserSession) -> None:
        """
        If a user_session_id is provided, ensure it belongs to the authenticated user.
        This prevents reuse of stolen session IDs across accounts.
        """
        user_session_id = self._extract_user_session_id(request)
        if not user_session_id:
            return
        if not session.user_id:
            raise HTTPException(status_code=401, detail="No user in session")
        requested = await self.gateway.session_manager.get_session_by_id(user_session_id)
        if not requested:
            raise HTTPException(status_code=401, detail="Unknown session")
        if requested.user_type == UserType.ANONYMOUS:
            raise HTTPException(status_code=401, detail="Anonymous sessions are not allowed")
        if not requested.user_id or requested.user_id != session.user_id:
            raise HTTPException(status_code=403, detail="Session does not belong to current user")

    async def resolve_session(self, request: Request, *, header_only_auth: bool = False) -> UserSession:
        """
        AuthN/AuthZ + session resolution only.
        No rate limit, no backpressure.
        Safe for middleware + SSE stream connect.
        """
        return await self.process_request(
            request,
            requirements=[],
            bypass_throttling=True,
            bypass_gate=True,
            header_only_auth=header_only_auth,
        )

    def get_session_light(self, *, header_only_auth: bool = False):
        async def dependency(request: Request) -> UserSession:
            existing: Optional[UserSession] = getattr(request.state, STATE_SESSION, None)
            if _can_reuse_state_session(request, header_only_auth=header_only_auth):
                return existing

            session = await self.resolve_session(request, header_only_auth=header_only_auth)
            setattr(request.state, STATE_SESSION, session)
            setattr(request.state, STATE_USER_TYPE, session.user_type.value)
            setattr(request.state, STATE_FLAG, True)
            setattr(request.state, STATE_AUTH_MODE, _state_auth_mode(header_only_auth))
            return session
        return dependency

    async def process_by_policy(self, request: Request, *, header_only_auth: bool = False) -> UserSession:
        pol = self.policy.resolve(request)
        return await self.process_request(
            request,
            requirements=pol.requirements or [],
            bypass_throttling=pol.bypass_throttling,
            bypass_gate=pol.bypass_gate,
            bypass_backpressure=pol.bypass_backpressure,
            header_only_auth=header_only_auth,
        )

    async def process_request(self,
                              request: Request,
                              requirements: List[RequirementBase] = None,
                              bypass_throttling: bool = False,
                              bypass_gate: bool = False,
                              bypass_backpressure: bool = False,
                              header_only_auth: bool = False) -> UserSession:
        """Process request and return session"""
        requirements = requirements or []
        context = self._extract_context(request, header_only_auth=header_only_auth)
        endpoint = request.url.path

        try:
            session = await self.gateway.process_request(
                context,
                requirements,
                endpoint,
                bypass_throttling,
                bypass_gate=bypass_gate,
                bypass_backpressure=bypass_backpressure,
            )
            await self._enforce_user_session_ownership(request, session)
            return session


        except AuthenticationError as e:
            raise HTTPException(status_code=401, detail=e.message)
        except AuthorizationError as e:
            raise HTTPException(status_code=403, detail=e.message)
        except RateLimitError as e:
            sess = e.session
            content={"detail": e.message, "user_type": sess.user_type.value}
            raise HTTPException(
                status_code=429,
                detail=content,
                headers={"Retry-After": str(e.retry_after)}
            )
        except BackpressureError as e:
            sess = e.session
            content={"detail": e.message, "user_type": sess.user_type.value}
            raise HTTPException(
                status_code=503,
                detail=content,
                headers={"Retry-After": str(e.retry_after)}
            )
        except GatewayError as e:
            raise HTTPException(status_code=e.code, detail=e.message)

    def require_admin(self, *requirements: RequirementBase):
        """Create FastAPI dependency that enforces requirements with throttling bypass"""
        async def dependency(request: Request) -> UserSession:
            return await self.process_request(request,
                                              list(requirements),
                                              bypass_throttling=True,
                                              bypass_gate=True)
        return dependency

    def require(self, *requirements: RequirementBase):
        """Create FastAPI dependency that enforces requirements"""
        async def dependency(request: Request) -> UserSession:
            pol = self.policy.resolve(request)
            return await self.process_request(
                request,
                list(requirements),
                bypass_throttling=pol.bypass_throttling,
                bypass_gate=pol.bypass_gate,
                bypass_backpressure=pol.bypass_backpressure,
            )
        return dependency

    def require_headers_only(self, *requirements: RequirementBase):
        """Create FastAPI dependency that enforces requirements using header-only JWT auth."""
        async def dependency(request: Request) -> UserSession:
            pol = self.policy.resolve(request)
            return await self.process_request(
                request,
                list(requirements),
                bypass_throttling=pol.bypass_throttling,
                bypass_gate=pol.bypass_gate,
                bypass_backpressure=pol.bypass_backpressure,
                header_only_auth=True,
            )
        return dependency

    def get_session(self, bypass_gate: bool = False):
        """Create FastAPI dependency that just gets the session (no requirements)"""
        async def dependency(request: Request) -> UserSession:
            return await self.process_request(request, [], bypass_gate=bypass_gate)
        return dependency

    def get_session_headers_only(self, bypass_gate: bool = False):
        """Create FastAPI dependency that gets the session using header-only JWT auth."""
        async def dependency(request: Request) -> UserSession:
            return await self.process_request(
                request,
                [],
                bypass_gate=bypass_gate,
                header_only_auth=True,
            )
        return dependency


    # --- your dependency ---
    def get_user_session_dependency(self, *, header_only_auth: bool = False):
        async def dependency(request: Request) -> UserSession:
            # If middleware already did it, reuse
            existing: Optional[UserSession] = getattr(request.state, STATE_SESSION, None)
            if _can_reuse_state_session(request, header_only_auth=header_only_auth):
                return existing

            # Otherwise process once here and mark
            # session = await self.process_request(request, [])
            session = await self.resolve_session(request, header_only_auth=header_only_auth)
            setattr(request.state, STATE_SESSION, session)
            setattr(request.state, STATE_USER_TYPE, session.user_type.value)
            setattr(request.state, STATE_FLAG, True)
            setattr(request.state, STATE_AUTH_MODE, _state_auth_mode(header_only_auth))
            return session
        return dependency

    def auth_without_pressure(self, requirements: Optional[Iterable[RequirementBase]] = None, *, header_only_auth: bool = False):
        """
        Authenticate + authorize for admin endpoints.
        Always bypass throttling + gate.
        Reuse cached session only if we know admin checks were already applied.
        """
        DEFAULT_ADMIN_REQUIREMENTS: List[RequirementBase] = [
            RequireUser(),
            RequireRoles("kdcube:role:super-admin"),
        ]
        reqs = list(requirements) if requirements is not None else DEFAULT_ADMIN_REQUIREMENTS

        async def dependency(request: Request) -> UserSession:
            existing: Optional[UserSession] = getattr(request.state, STATE_SESSION, None)
            admin_checked: bool = getattr(request.state, STATE_ADMIN_CHECKED, False)

            # Only reuse if this request already ran admin auth
            if existing is not None and admin_checked and _can_reuse_state_session(request, header_only_auth=header_only_auth):
                return existing

            session = await self.process_request(
                request,
                reqs,
                bypass_throttling=True,
                bypass_gate=True,
                header_only_auth=header_only_auth,
            )

            setattr(request.state, STATE_SESSION, session)
            setattr(request.state, STATE_USER_TYPE, session.user_type.value)
            setattr(request.state, STATE_FLAG, True)
            setattr(request.state, STATE_ADMIN_CHECKED, True)
            setattr(request.state, STATE_AUTH_MODE, _state_auth_mode(header_only_auth))

            return session

        return dependency

    async def get_system_status(self) -> dict:
        """Get system status"""
        return await self.gateway.get_system_status()


# Convenience functions for dependency injection
def create_session_dependency(gateway_adapter: FastAPIGatewayAdapter):
    """Create a dependency that just gets the session"""
    return gateway_adapter.get_session()


def create_auth_dependency(gateway_adapter: FastAPIGatewayAdapter, *requirements: RequirementBase):
    """Create a dependency with specific auth requirements"""
    return gateway_adapter.require(*requirements)


# Example usage helpers
def get_user_session_from_state(request: Request) -> Optional[UserSession]:
    """Get user session from request state (if middleware was used)"""
    return getattr(request.state, 'user_session', None)


def get_user_type_from_state(request: Request) -> Optional[UserType]:
    """Get user type from request state (if middleware was used)"""
    session = get_user_session_from_state(request)
    return session.user_type if session else None

import uuid
from typing import Optional, Callable
from fastapi import Request, Depends

from kdcube_ai_app.infra.accounting import AccountingSystem, SystemResource, with_accounting
from kdcube_ai_app.auth.sessions import UserSession

class AccountingContextBinder:
    """
    Centralizes:
      - init_storage once
      - per-request context (FastAPI dependency)
      - per-socket connect snapshot
      - per-socket event rebind + optional accounting scope
    """

    def __init__(
            self,
            gateway_adapter: FastAPIGatewayAdapter,
            storage_backend,
            get_tenant_fn: Callable[[], str],
            accounting_enabled: bool = True,
            default_component: str = "chat-rest",
    ):
        self.gateway_adapter = gateway_adapter
        self.storage_backend = storage_backend
        self.get_tenant = get_tenant_fn
        self.enabled = accounting_enabled
        self.default_component = default_component

        # Initialize storage once (safe to call multiple times)
        AccountingSystem.init_storage(self.storage_backend, enabled=self.enabled)

    # -------- FastAPI dependency (HTTP) --------
    def http_dependency(self, component: Optional[str] = None, *, header_only_auth: bool = False):
        """
        Use in FastAPI endpoints:
            session: UserSession = Depends(binder.http_dependency("chat-rest"))
        """
        comp = component or self.default_component

        async def dep(request: Request) -> UserSession:
            session: Optional[UserSession] = getattr(request.state, STATE_SESSION, None)
            if not _can_reuse_state_session(request, header_only_auth=header_only_auth):
                if header_only_auth:
                    session = await self.gateway_adapter.process_by_policy(
                        request,
                        header_only_auth=True,
                    )
                else:
                    session = await self.gateway_adapter.process_by_policy(request)
                setattr(request.state, STATE_SESSION, session)
                setattr(request.state, STATE_USER_TYPE, session.user_type.value)
                setattr(request.state, STATE_FLAG, True)
                setattr(request.state, STATE_AUTH_MODE, _state_auth_mode(header_only_auth))
            AccountingSystem.set_context(
                user_id=getattr(session, "user_id", None),
                session_id=getattr(session, "session_id", None),
                project_id=request.path_params.get("project"),
                tenant_id=self.get_tenant(),
                request_id=request.headers.get("X-Request-ID", str(uuid.uuid4())),
                component=comp,
                timezone=getattr(session, "timezone", None),
            )
            # optional convenience for code that expects request.state.user
            request.state.user = {
                "user_id": getattr(session, "user_id", None),
                "roles": getattr(session, "roles", []),
            }
            return session

        return dep
