# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# oauth_manager.py
"""
Framework-agnostic OAuth Manager implementation
"""
import base64, time, hashlib
import logging
import os
from functools import lru_cache
from typing import Optional, Dict, Any, Tuple

import httpx
import jwt
from jwt import PyJWTError
from pydantic import BaseModel

from kdcube_ai_app.auth.AuthManager import AuthManager, User, AuthenticationError

logger = logging.getLogger("OAuthManager")

def _auth_debug_enabled() -> bool:
    return os.getenv("AUTH_DEBUG", "").lower() in {"1", "true", "yes", "on"}


class OAuthUser(User):
    sub: str
    preferred_username: Optional[str] = None


class OAuth2Config:
    def __init__(self,
                 oauth2_issuer: str,
                 oauth2_audience: str,
                 oauth2_jwks_url: Optional[str] = None,
                 oauth2_userinfo_url: Optional[str] = None,
                 oauth2_introspection_url: Optional[str] = None,
                 oauth2_token_url: Optional[str] = None,
                 introspection_client_id: Optional[str] = None,
                 introspection_client_secret: Optional[str] = None,
                 service_client_id: Optional[str] = None,
                 service_client_secret: Optional[str] = None,
                 verification_method: str = "jwks",
                 verify_signature: bool = True,

                always_introspect_access: bool = False,  # if True, check “active” even after JWKS verify
                cache_ttl_fallback_secs: int = 300,      # used if token has no exp
                negative_cache_ttl_secs: int = 60,
                ):
        self.OAUTH2_ISSUER = oauth2_issuer
        self.OAUTH2_AUDIENCE = oauth2_audience
        self.OAUTH2_JWKS_URL = oauth2_jwks_url or f"{oauth2_issuer}/.well-known/jwks.json"
        self.OAUTH2_USERINFO_URL = oauth2_userinfo_url or f"{oauth2_issuer}/userinfo"

        # Token introspection configuration
        self.OAUTH2_INTROSPECTION_URL = oauth2_introspection_url or f"{oauth2_issuer}/oauth/introspect"
        self.OAUTH2_TOKEN_URL = oauth2_token_url or f"{oauth2_issuer}/oauth/token"
        self.INTROSPECTION_CLIENT_ID = introspection_client_id
        self.INTROSPECTION_CLIENT_SECRET = introspection_client_secret

        # Verification method preference: 'jwks', 'introspection', or 'both'
        self.VERIFICATION_METHOD = verification_method

        # For development/testing - set to False in production
        self.VERIFY_SIGNATURE = verify_signature

        self.SERVICE_CLIENT_ID = service_client_id
        self.SERVICE_CLIENT_SECRET = service_client_secret

        self.ALWAYS_INTROSPECT_ACCESS = always_introspect_access
        self.CACHE_TTL_FALLBACK_SECS = cache_ttl_fallback_secs
        self.NEGATIVE_CACHE_TTL_SECS = negative_cache_ttl_secs


class TokenIntrospectionResponse(BaseModel):
    active: bool
    sub: Optional[str] = None
    client_id: Optional[str] = None
    username: Optional[str] = None
    email: Optional[str] = None
    scope: Optional[str] = None
    exp: Optional[int] = None
    iat: Optional[int] = None
    token_type: Optional[str] = None
    aud: Optional[str] = None
    iss: Optional[str] = None


class OAuthManager(AuthManager):
    """
    Access token:
      - verify by JWKS or introspection (configurable); can do BOTH to catch revocation.
    ID token:
      - verify by JWKS only (no introspection in most IdPs).
    """

    def __init__(self, oauth_config, send_validation_error_details: bool = False):
        super().__init__(send_validation_error_details)
        self.oauth_config = oauth_config
        # small in-proc caches
        self._user_cache: dict[str, Tuple[dict, float]] = {}      # token_hash -> (user_dict, exp_ts)
        self._negative_cache: dict[str, float] = {}               # token_hash -> exp_ts (inactive/revoked)

    # -------------- Utilities --------------

    @staticmethod
    def _th(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _cache_get(self, token: str) -> Optional[dict]:
        th = self._th(token)
        rec = self._user_cache.get(th)
        if not rec:
            return None
        user_dict, exp_ts = rec
        if exp_ts and exp_ts > time.time():
            return user_dict
        self._user_cache.pop(th, None)
        return None

    def _cache_put(self, token: str, user_data: dict, exp: Optional[int]):
        ttl = exp or (int(time.time()) + self.oauth_config.CACHE_TTL_FALLBACK_SECS)
        if len(self._user_cache) > 512:
            self._user_cache.clear()
        self._user_cache[self._th(token)] = (user_data, ttl)

    def _neg_hit(self, token: str) -> bool:
        th = self._th(token)
        exp = self._negative_cache.get(th)
        if exp and exp > time.time():
            return True
        if exp:
            self._negative_cache.pop(th, None)
        return False

    def _neg_put(self, token: str):
        self._negative_cache[self._th(token)] = int(time.time()) + self.oauth_config.NEGATIVE_CACHE_TTL_SECS

    # -------------- JWKS & Introspection --------------

    @lru_cache(maxsize=1)
    def get_jwks_keys(self):
        try:
            with httpx.Client() as client:
                r = client.get(self.oauth_config.OAUTH2_JWKS_URL)
                r.raise_for_status()
                return r.json()
        except Exception as e:
            raise AuthenticationError(f"Failed to fetch JWKS: {e}")

    def _jwt_verify(self, token: str, *, audience: Optional[str] = None) -> Dict[str, Any]:
        if not self.oauth_config.VERIFY_SIGNATURE:
            return jwt.decode(token, options={"verify_signature": False})
        jwks = self.get_jwks_keys()
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        key = None
        for jwk_key in jwks.get("keys", []):
            if jwk_key.get("kid") == kid:
                key = jwt.algorithms.RSAAlgorithm.from_jwk(jwk_key)
                break
        if not key:
            raise AuthenticationError("No matching JWKS key")
        return jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=audience or self.oauth_config.OAUTH2_AUDIENCE,
            issuer=self.oauth_config.OAUTH2_ISSUER,
            options={"verify_aud": False}
        )

    async def introspect_token(self, token: str) -> TokenIntrospectionResponse:
        try:
            async with httpx.AsyncClient() as client:
                auth = httpx.BasicAuth(
                    self.oauth_config.INTROSPECTION_CLIENT_ID,
                    self.oauth_config.INTROSPECTION_CLIENT_SECRET
                ) if (self.oauth_config.INTROSPECTION_CLIENT_ID and self.oauth_config.INTROSPECTION_CLIENT_SECRET) else None

                data = {"token": token, "token_type_hint": "access_token"}
                headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
                r = await client.post(self.oauth_config.OAUTH2_INTROSPECTION_URL, data=data, headers=headers, auth=auth)
                r.raise_for_status()
                return TokenIntrospectionResponse(**r.json())
        except Exception as e:
            raise AuthenticationError(f"Token introspection failed: {e}")

    # -------------- Public helpers (kept) --------------

    async def check_token_status(self, token: str) -> Dict[str, Any]:
        try:
            insp = await self.introspect_token(token)
            return {"active": insp.active, "token_info": insp.dict()}
        except Exception as e:
            return {"active": False, "error": str(e)}

    async def get_service_token(self) -> str:
        """
        Client Credentials grant – only if your IdP/app client supports it.
        """
        creds = f"{self.oauth_config.SERVICE_CLIENT_ID}:{self.oauth_config.SERVICE_CLIENT_SECRET}"
        encoded = base64.b64encode(creds.encode()).decode()
        headers = {'Authorization': f'Basic {encoded}', 'Content-Type': 'application/x-www-form-urlencoded'}
        data = {'grant_type': 'client_credentials'}
        async with httpx.AsyncClient() as client:
            r = await client.post(self.oauth_config.OAUTH2_TOKEN_URL, headers=headers, data=data)
            r.raise_for_status()
            return r.json()['access_token']

    # -------------- Core verification paths --------------

    async def _verify_access_token(self, token: str) -> Dict[str, Any]:
        """
        Access token verification with optional revocation check.
        """
        method = (self.oauth_config.VERIFICATION_METHOD or "jwks").lower()

        # Negative cache hit: fail fast
        if self._neg_hit(token):
            raise AuthenticationError("Token previously marked inactive")

        if method == "introspection":
            insp = await self.introspect_token(token)
            if not insp.active:
                self._neg_put(token)
                raise AuthenticationError("Token is not active")
            # normalize to a payload-like dict
            return {
                "sub": insp.sub, "email": insp.email, "username": insp.username,
                "client_id": insp.client_id, "scope": insp.scope, "exp": insp.exp,
                "iat": insp.iat, "aud": insp.aud, "iss": insp.iss,
            }

        # JWKS (default) or BOTH
        payload = self._jwt_verify(token, audience=self.oauth_config.OAUTH2_AUDIENCE)

        if method == "both" or self.oauth_config.ALWAYS_INTROSPECT_ACCESS:
            try:
                insp = await self.introspect_token(token)
                if not insp.active:
                    self._neg_put(token)
                    raise AuthenticationError("Token is not active")
                # Optionally merge any fields the introspection knows better:
                payload = {
                    **payload,
                    "client_id": insp.client_id or payload.get("client_id"),
                    "scope": insp.scope or payload.get("scope"),
                    "exp": insp.exp or payload.get("exp"),
                }
            except AuthenticationError:
                # re-raise as-is (keeps message)
                raise

        return payload

    def _verify_id_token(self, id_token: str) -> Dict[str, Any]:
        """
        ID tokens are JWTs meant for the client; validate signature/iss/aud.
        """
        return self._jwt_verify(id_token, audience=self.oauth_config.OAUTH2_AUDIENCE)

    async def get_user_info(self, token: str) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(self.oauth_config.OAUTH2_USERINFO_URL, headers={"Authorization": f"Bearer {token}"})
                r.raise_for_status()
                return r.json()
        except Exception:
            return {}

    # -------------- AuthManager interface --------------

    async def authenticate(self, token: str) -> OAuthUser:
        if not token:
            raise AuthenticationError("No token provided")

        cached = self._cache_get(token)
        if cached:
            return OAuthUser(**cached)

        payload = await self._verify_access_token(token)

        user_data = {
            "sub": payload.get("sub"),
            "email": payload.get("email"),
            "name": payload.get("name") or payload.get("username"),
            "roles": payload.get("roles", []),
            "permissions": payload.get("permissions", []),
            "username": payload.get("username") or payload.get("preferred_username"),
            "preferred_username": payload.get("preferred_username") or payload.get("username"),
        }

        # Optional userinfo enrichment
        if self.oauth_config.OAUTH2_USERINFO_URL:
            ui = await self.get_user_info(token)
            user_data["email"] = ui.get("email") or user_data.get("email")
            user_data["name"] = ui.get("name") or user_data.get("name")

        if not user_data.get("sub"):
            raise AuthenticationError("Invalid token payload")

        self._cache_put(token, user_data, payload.get("exp"))
        return OAuthUser(**user_data)

    async def authenticate_with_both(self, access_token: str, id_token: Optional[str]) -> OAuthUser:
        """
        - Access token: verify (JWKS / introspection / both).
        - ID token: verify by JWKS; subjects MUST match if both present.
        - Merge identity niceties from ID token into the access-derived user.
        """
        user = await self.authenticate(access_token)

        if not id_token:
            if _auth_debug_enabled():
                logger.info(
                    "OAuth auth: access token ok, id_token missing, roles=%s perms=%s",
                    len(user.roles or []),
                    len(user.permissions or []),
                )
            return user

        id_payload = self._verify_id_token(id_token)

        acc_sub = getattr(user, "sub", None)
        id_sub = id_payload.get("sub")
        if acc_sub and id_sub and acc_sub != id_sub:
            raise AuthenticationError("ID token subject does not match access token")

        merged = user.model_dump()
        for k in ("email", "name", "preferred_username", "username"):
            merged[k] = id_payload.get(k) or merged.get(k)

        # cache merged view under the access token key
        self._cache_put(access_token, merged, id_payload.get("exp"))
        if _auth_debug_enabled():
            logger.info(
                "OAuth auth: merged user roles=%s perms=%s",
                len(merged.get("roles") or []),
                len(merged.get("permissions") or []),
            )
        return OAuthUser(**merged)
