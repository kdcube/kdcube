# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

import uuid
from typing import Callable, Optional, Mapping
from fastapi import Depends, Request
from fastapi.security import HTTPBearer

from kdcube_ai_app.auth.sessions import UserSession, UserType, RequestContext
from kdcube_ai_app.infra.accounting import AccountingSystem
from kdcube_ai_app.apps.middleware.auth import FastAPIAuthAdapter
from kdcube_ai_app.auth.AuthManager import RequirementBase, User, AuthenticationError, PRIVILEGED_ROLES
from kdcube_ai_app.auth.AuthManager import User as AuthUser, AuthorizationError
from kdcube_ai_app.apps.middleware.token_extract import resolve_socket_auth_tokens

class MiddlewareAuthWithAccounting:
    def __init__(
            self,
            base_auth_adapter: FastAPIAuthAdapter,              # your existing FastAPIAuthAdapter instance
            get_tenant_fn: Callable[[], str],
            storage_backend,                # << add
            accounting_enabled: bool = True,
            default_component: str = "kb-rest",
    ):
        self.base_auth = base_auth_adapter
        self.get_tenant = get_tenant_fn
        self.storage_backend = storage_backend
        self.accounting_enabled = accounting_enabled
        self.default_component = default_component
        self.security = HTTPBearer(auto_error=False)

    def require_auth_with_accounting(
            self,
            *requirements: RequirementBase,
            require_all: bool = True,
            component: Optional[str] = None,
    ):
        """
        Create a dependency that:
          - runs the base auth dependency
          - sets AccountingSystem context (user_id, session_id, project, tenant, request_id, component)
          - returns the authenticated `user` object
        """
        base_dep = self.base_auth.require_session(*requirements, require_all=require_all)
        comp = component or self.default_component

        async def dependency(
                request: Request,
                # user: User = Depends(base_dep),
                session: UserSession = Depends(base_dep),
        ):
            # 1) ensure storage exists in THIS request context
            AccountingSystem.init_storage(self.storage_backend, self.accounting_enabled)

            # 2) set context AFTER auth
            AccountingSystem.set_context(
                user_id=getattr(session, "user_id", None),
                session_id=request.headers.get("User-Session-ID") or getattr(session, "session_id", None),
                project_id=request.path_params.get("project"),
                tenant_id=self.get_tenant(),
                request_id=request.headers.get("X-Request-ID", str(uuid.uuid4())),
                component=comp,
            )
            # optional: expose user on state
            request.state.user = {"user_id": getattr(session, "user_id", None),
                                  "roles": getattr(session, "roles", [])}
            return session

        return dependency

    # convenience helpers if you have pre-built requirement factories
    def require_read_with_accounting(self, *reqs: RequirementBase, **kw):
        return self.require_auth_with_accounting(*reqs, **kw)

    def require_write_with_accounting(self, *reqs: RequirementBase, **kw):
        return self.require_auth_with_accounting(*reqs, **kw)

    def require_admin_with_accounting(self, *reqs: RequirementBase, **kw):
        return self.require_auth_with_accounting(*reqs, **kw)

    async def process_socket_connect(
            self,
            auth: dict,
            environ: Mapping[str, str],
            *requirements: RequirementBase,
            require_all: bool = True,
            component: Optional[str] = None,
            # keep the knobs (default OFF so connect is flexible):
            require_existing_session: bool = False,
            verify_token_session_match: bool = False,
    ) -> UserSession | None:
        bearer_token, id_token = resolve_socket_auth_tokens(auth, environ)

        user_session_id = (auth or {}).get("user_session_id")
        if not bearer_token:
            raise AuthenticationError("No bearer token provided for socket connect")

        # user = await self.base_auth.auth_manager.authenticate_and_authorize_with_both(
        #     bearer_token, id_token, *requirements, require_all=require_all
        # )
        user = await self.base_auth.auth_manager.authenticate_with_both(bearer_token, id_token)
        roles = set(getattr(user, "roles", []) or [])
        service_user = self.base_auth.service_role_name in roles
        if not service_user and requirements:
            self.base_auth.auth_manager.validate_requirements(user, *requirements, require_all=require_all)

        service_user = self.base_auth.service_role_name in roles

        session = None

        if require_existing_session:
            if not user_session_id:
                raise AuthenticationError("user_session_id is required")
            session = await self.base_auth.session_manager.get_session_by_id(user_session_id)
            if not session:
                raise AuthenticationError("Unknown user_session_id")
        else:
            # service sockets may connect without a session
            if not service_user:
                # regular user: reuse or create their own session
                if user_session_id:
                    session = await self.base_auth.session_manager.get_session_by_id(user_session_id)
                    if not session:
                        raise AuthenticationError("Unknown user_session_id")
                else:
                    user_type = UserType.PRIVILEGED if (roles & PRIVILEGED_ROLES) else UserType.REGISTERED
                    ctx = RequestContext(
                        client_ip=environ.get("REMOTE_ADDR", environ.get("HTTP_X_FORWARDED_FOR", "unknown")),
                        user_agent=environ.get("HTTP_USER_AGENT", ""),
                        authorization_header=f"Bearer {bearer_token}",
                        id_token=id_token,
                    )
                    user_data = {
                        "user_id": getattr(user, "sub", None) or user.username,
                        "username": user.username,
                        "email": getattr(user, "email", None),
                        "roles": list(roles),
                        "permissions": list(getattr(user, "permissions", []) or []),
                    }
                    session = await self.base_auth.session_manager.get_or_create_session(ctx, user_type, user_data)

        if verify_token_session_match and session and session.user_type.value != "anonymous":
            claimed_user_id = getattr(user, "sub", None) or user.username
            if session.user_id and claimed_user_id and session.user_id != claimed_user_id:
                raise AuthenticationError("Bearer token user does not match provided session")

        # initialize accounting storage once per process (idempotent)
        AccountingSystem.init_storage(self.storage_backend, self.accounting_enabled)

        # set a default context for the socket connect itself (may be service-only)
        AccountingSystem.set_context(
            user_id=getattr(session, "user_id", None) if session else None,
            session_id=getattr(session, "session_id", None) if session else None,
            tenant_id=(auth or {}).get("tenant") or self.get_tenant(),
            project_id=(auth or {}).get("project"),
            request_id=str(uuid.uuid4()),
            component=(component or self.default_component),
        )

        return session

    def apply_event_accounting(
            self,
            *,
            session: UserSession | None,
            component: str,
            tenant_id: str | None = None,
            project_id: str | None = None,
            extra: dict | None = None,
    ):
        AccountingSystem.set_context(
            user_id=getattr(session, "user_id", None) if session else None,
            session_id=getattr(session, "session_id", None) if session else None,
            tenant_id=tenant_id or self.get_tenant(),
            project_id=project_id,
            request_id=str(uuid.uuid4()),
            component=component,
            **(extra or {}),
        )

    async def get_session_by_id(self, session_id: str) -> UserSession | None:
        return await self.base_auth.session_manager.get_session_by_id(session_id)

    def process_orchestrator_task_execution(self):
        pass

    def authorize_session_user(
            self,
            session: UserSession | None,
            *requirements: RequirementBase,
            require_all: bool = True,
    ):
        if session is None:
            raise AuthorizationError("on_behalf_session_id or user session required.", 403)

        eff_user = AuthUser(
            username=session.username,
            email=getattr(session, "email", None),
            roles=getattr(session, "roles", []) or [],
            permissions=getattr(session, "permissions", []) or [],
        )
        self.base_auth.auth_manager.validate_requirements(
            eff_user, *requirements, require_all=require_all
        )
