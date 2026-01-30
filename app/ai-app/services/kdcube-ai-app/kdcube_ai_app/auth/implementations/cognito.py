# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# auth/cognito_manager.py
from typing import Any, Dict, Optional
import os
import logging
from kdcube_ai_app.auth.AuthManager import AuthenticationError, User
from kdcube_ai_app.auth.OAuthManager import OAuthManager, OAuth2Config

logger = logging.getLogger(__name__)

def _auth_debug_enabled() -> bool:
    return os.getenv("AUTH_DEBUG", "").lower() in {"1", "true", "yes", "on"}

class CognitoUser(User):
    sub: str
    preferred_username: Optional[str] = None

def _cfg() -> OAuth2Config:
    region    = os.getenv("COGNITO_REGION")
    pool_id   = os.getenv("COGNITO_USER_POOL_ID")
    client_id = os.getenv("COGNITO_APP_CLIENT_ID")
    hosted_ui = os.getenv("COGNITO_HOSTED_UI_DOMAIN")

    if not (region and pool_id and client_id):
        raise RuntimeError("COGNITO_REGION, COGNITO_USER_POOL_ID, COGNITO_APP_CLIENT_ID are required")

    issuer  = f"https://cognito-idp.{region}.amazonaws.com/{pool_id}"
    jwks    = f"{issuer}/.well-known/jwks.json"
    userinfo = f"{hosted_ui}/oauth2/userInfo" if hosted_ui else None

    return OAuth2Config(
        oauth2_issuer=issuer,
        oauth2_audience=client_id,
        oauth2_jwks_url=jwks,
        oauth2_userinfo_url=userinfo,
        verification_method="jwks",
        verify_signature=True,
    )

class CognitoAuthManager(OAuthManager):
    def __init__(self, send_validation_error_details: bool = False):
        super().__init__(_cfg(), send_validation_error_details)

    async def authenticate(self, token: str) -> CognitoUser:
        """
        For Cognito, this method should only be used with ID tokens
        or when you don't need user roles/groups (just basic auth).

        For full user info with roles, use authenticate_with_both()
        """
        if not token:
            raise AuthenticationError("No token provided")

        # Check if this looks like an ID token or access token
        try:
            import jwt
            unverified = jwt.decode(token, options={"verify_signature": False})
            token_use = unverified.get("token_use")

            if token_use == "access":
                # This is an access token - we can verify it but won't have user roles
                payload = await self._verify_access_token(token)
                return self._create_user_from_access_token(payload)
            elif token_use == "id":
                # This is an ID token - verify and extract full user info
                payload = self._verify_id_token(token)
                return self._create_user_from_id_token(payload)
            else:
                raise AuthenticationError("Unknown token type")

        except Exception as e:
            raise AuthenticationError(f"Token validation failed: {str(e)}")

    async def authenticate_with_both(self, access_token: str, id_token: Optional[str]) -> CognitoUser:
        """
        CORRECT COGNITO PATTERN:
        - Verify access token for API authorization
        - Extract user identity, roles, groups from ID token
        - Merge the information appropriately
        """
        if not access_token:
            raise AuthenticationError("Access token is required")

        # 1. Verify access token (proves API access rights)
        try:
            access_payload = await self._verify_access_token(access_token)
        except Exception as e:
            raise AuthenticationError(f"Access token validation failed: {str(e)}")
        if _auth_debug_enabled():
            logger.info("Cognito auth: access token ok, id_token_present=%s", bool(id_token))

        # 2. If we have ID token, extract user identity from it
        if id_token:
            try:
                id_payload = self._verify_id_token(id_token)

                # Verify subjects match
                access_sub = access_payload.get("sub")
                id_sub = id_payload.get("sub")
                if access_sub and id_sub and access_sub != id_sub:
                    raise AuthenticationError("Token subjects don't match")

                # Create user from ID token (has roles/groups)
                user = self._create_user_from_id_token(id_payload)
                if _auth_debug_enabled():
                    logger.info(
                        "Cognito auth: roles=%s perms=%s user=%s",
                        len(user.roles or []),
                        len(user.permissions or []),
                        user.username,
                    )

                # Cache under access token key since that's what we'll use for API calls
                user_data = user.model_dump()
                self._cache_put(access_token, user_data, id_payload.get("exp"))

                return user

            except Exception as e:
                raise AuthenticationError(f"ID token validation failed: {str(e)}")
        else:
            # No ID token - create basic user from access token
            return self._create_user_from_access_token(access_payload)

    def _create_user_from_access_token(self, payload: Dict[str, Any]) -> CognitoUser:
        """
        Create user from access token - limited info, typically no roles/groups
        """
        return CognitoUser(
            sub=payload.get("sub"),
            username=payload.get("username") or payload.get("client_id"),
            email=None,  # Access tokens typically don't have email
            name=None,   # Access tokens typically don't have name
            roles=[],    # Access tokens typically don't have roles
            permissions=[],  # Access tokens typically don't have permissions
            preferred_username=payload.get("username")
        )

    def _create_user_from_id_token(self, payload: Dict[str, Any]) -> CognitoUser:
        """
        Create user from ID token - full user identity with roles/groups
        """
        # Extract roles from various possible claims in ID token
        roles = []

        # Cognito groups (most common for roles)
        cognito_groups = payload.get("cognito:groups", [])
        if cognito_groups:
            roles.extend(cognito_groups)

        # Custom roles attribute
        custom_roles = payload.get("custom:roles", [])
        if isinstance(custom_roles, str):
            custom_roles = custom_roles.split(",")
        if custom_roles:
            roles.extend(custom_roles)

        # Direct roles claim
        direct_roles = payload.get("roles", [])
        if isinstance(direct_roles, str):
            direct_roles = direct_roles.split(",")
        if direct_roles:
            roles.extend(direct_roles)

        # Extract permissions
        permissions = []
        custom_permissions = payload.get("custom:permissions", [])
        if isinstance(custom_permissions, str):
            custom_permissions = custom_permissions.split(",")
        if custom_permissions:
            permissions.extend(custom_permissions)

        return CognitoUser(
            sub=payload.get("sub"),
            username=(
                    payload.get("cognito:username") or
                    payload.get("preferred_username") or
                    payload.get("email")
            ),
            email=payload.get("email"),
            name=(
                    payload.get("name") or
                    payload.get("given_name") or
                    payload.get("cognito:username")
            ),
            roles=list(set(roles)),  # Remove duplicates
            permissions=list(set(permissions)),  # Remove duplicates
            preferred_username=payload.get("preferred_username")
        )

    def _verify_id_token(self, id_token: str) -> Dict[str, Any]:
        """Override to handle Cognito ID token specifics"""
        return self._jwt_verify(id_token, audience=self.oauth_config.OAUTH2_AUDIENCE)

    async def get_service_token(self) -> str:
        raise NotImplementedError("Service tokens are not issued by Cognito User Pools.")
