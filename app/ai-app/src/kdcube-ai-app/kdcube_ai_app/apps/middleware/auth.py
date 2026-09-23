# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

# /middleware/auth.py
"""
Framework-specific adapters for the auth system
"""

# ================================
# FastAPI Adapter
# ================================
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.requests import Request
from starlette.status import HTTP_400_BAD_REQUEST

from kdcube_ai_app.auth.AuthManager import AuthManager, RequirementBase, AuthenticationError, AuthorizationError, \
    HTTP_401_UNAUTHORIZED, PRIVILEGED_ROLES
from kdcube_ai_app.auth.sessions import RequestContext, UserType, session_user_data
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.middleware.token_extract import (
    extract_auth_tokens_from_cookies,
    resolve_auth_from_headers_and_cookies,
)
import logging
import os

logger = logging.getLogger(__name__)

def _auth_debug_enabled() -> bool:
    return os.getenv("AUTH_DEBUG", "").lower() in {"1", "true", "yes", "on"}


def user_type_from_roles(roles) -> UserType:
    role_set = set(roles or [])
    if PRIVILEGED_ROLES & role_set:
        return UserType.PRIVILEGED
    if not role_set:
        return UserType.EXTERNAL
    return UserType.REGISTERED


class UserSessionError(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=HTTP_400_BAD_REQUEST, detail="No user session id provided")


class UserSessionID:
    def __init__(self, header_name: str = "User-Session-ID", auto_error: bool = False):
        self.header_name = header_name
        self.auto_error = auto_error

    def __call__(self, request: Request):
        user_session_id = request.headers.get(self.header_name)
        if self.auto_error and (user_session_id is None or user_session_id.strip() == ""):
            raise UserSessionError()
        return user_session_id


class FastAPIAuthAdapter:
    """Adapter for FastAPI framework"""

    def __init__(self,
                 auth_manager: AuthManager,
                 session_manager: 'SessionManager',
                 service_role_name: str):
        self.auth_manager = auth_manager
        self.security = HTTPBearer(auto_error=False)
        self.service_role_name = service_role_name
        self.session_manager = session_manager

    def _extract_context(self, request: Request) -> RequestContext:
        """Extract request context from FastAPI request"""
        _auth_cfg = get_settings().AUTH
        auth_header, id_token = resolve_auth_from_headers_and_cookies(
            request.headers.get("authorization"),
            request.headers.get(_auth_cfg.ID_TOKEN_HEADER_NAME)
            or request.headers.get(_auth_cfg.ID_TOKEN_HEADER_NAME.lower()),
            request.cookies,
        )
        if _auth_debug_enabled():
            logger.info(
                "Auth context: has_auth=%s has_id=%s id_hdr=%s cookie_auth=%s cookie_id=%s path=%s",
                bool(auth_header),
                bool(id_token),
                bool(request.headers.get(_auth_cfg.ID_TOKEN_HEADER_NAME) or request.headers.get(_auth_cfg.ID_TOKEN_HEADER_NAME.lower())),
                bool(request.cookies.get(_auth_cfg.AUTH_TOKEN_COOKIE_NAME)),
                bool(request.cookies.get(_auth_cfg.ID_TOKEN_COOKIE_NAME)),
                request.url.path if request else "",
            )
        return RequestContext(
            client_ip=request.client.host if request.client else "unknown",
            user_agent=request.headers.get("user-agent", ""),
            authorization_header=auth_header,
            id_token=id_token,
        )

    def require(self, *requirements: RequirementBase, require_all: bool = True):
        async def dependency(
                credentials: HTTPAuthorizationCredentials = Depends(self.security),
                user_session_id: str = Depends(UserSessionID(auto_error=False)),
                request: Request = None,
        ):
            cookie_token, cookie_id_token = extract_auth_tokens_from_cookies(request.cookies) if request else (None, None)
            if not credentials and not cookie_token:
                raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="No authentication credentials provided")

            _id_hdr = get_settings().AUTH.ID_TOKEN_HEADER_NAME
            id_token = (
                request.headers.get(_id_hdr)
                or request.headers.get(_id_hdr.lower())
                or cookie_id_token
            )

            try:
                user = await self.auth_manager.authenticate_and_authorize_with_both(
                    (credentials.credentials if credentials else cookie_token),
                    id_token,
                    *requirements,
                    require_all=require_all
                )
                # service “on behalf of” branch stays the same...
                if self.service_role_name in (user.roles or []):
                    if user_session_id is None or user_session_id.strip() == "":
                        raise UserSessionError()
                    session = await self.session_manager.get_session_by_id(user_session_id)
                    if session:
                        return session.to_user()
                    raise UserSessionError()
                return user
            except AuthenticationError as e:
                raise HTTPException(status_code=e.code, detail=e.message)
            except AuthorizationError as e:
                raise HTTPException(status_code=e.code, detail=e.message)
        return dependency

    def require_session(self, *requirements: RequirementBase, require_all: bool = True):
        async def dependency(
                credentials: HTTPAuthorizationCredentials = Depends(self.security),
                user_session_id: str = Depends(UserSessionID(auto_error=False)),
                request: Request = None,
        ):
            cookie_token, cookie_id_token = extract_auth_tokens_from_cookies(request.cookies) if request else (None, None)
            if not credentials and not cookie_token:
                raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="No authentication credentials provided")

            _id_hdr = get_settings().AUTH.ID_TOKEN_HEADER_NAME
            id_token = (
                request.headers.get(_id_hdr)
                or request.headers.get(_id_hdr.lower())
                or cookie_id_token
            )

            try:
                user = await self.auth_manager.authenticate_and_authorize_with_both(
                    (credentials.credentials if credentials else cookie_token),
                    id_token,
                    *requirements,
                    require_all=require_all
                )
                user_type = user_type_from_roles(user.roles)
                user_data = session_user_data(user)
                if _auth_debug_enabled():
                    logger.info(
                        "Auth session: user=%s roles=%s perms=%s privileged=%s",
                        user.username,
                        len(user_data.get("roles") or []),
                        len(user_data.get("permissions") or []),
                        user_type == UserType.PRIVILEGED,
                    )

                if self.service_role_name in (user.roles or []):
                    if user_session_id is None or user_session_id.strip() == "":
                        raise UserSessionError()
                    session = await self.session_manager.get_session_by_id(user_session_id)
                    if session:
                        return session
                    raise UserSessionError()
                else:
                    request_context = self._extract_context(request)
                    session = await self.session_manager.get_or_create_session(request_context, user_type, user_data)
                return session
            except AuthenticationError as e:
                raise HTTPException(status_code=e.code, detail=e.message)
            except AuthorizationError as e:
                raise HTTPException(status_code=e.code, detail=e.message)
        return dependency
