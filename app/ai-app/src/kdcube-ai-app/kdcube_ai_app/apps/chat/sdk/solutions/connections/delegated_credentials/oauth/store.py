# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
Redis-backed store for OAuth authorization codes and refresh tokens.

Authorization codes are short-lived and single-use (replay protection via
delete-on-consume). Refresh tokens are long-lived and rotated on use so a
feedback-triage routine that runs *daily or seldom* keeps working unattended;
rotation invalidates the previous token (reuse-detection boundary).

Keys are tenant/project namespaced, matching the bundle-session auth convention.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# Authorization codes are exchanged immediately by the client.
AUTH_CODE_TTL_SECONDS = 60

# Generous refresh lifetime: a routine may run daily or only occasionally and
# must still be able to refresh rather than re-consent.
REFRESH_TTL_SECONDS = 180 * 24 * 3600

# DCR client registrations carry a SLIDING TTL: every read re-arms it, so a
# connector in use never expires, while abandoned registrations (every retried
# `claude mcp add` mints a fresh dcr-* client) age out instead of accumulating
# forever. Must comfortably outlive REFRESH_TTL_SECONDS - a valid refresh token
# must never point at an expired client record.
CLIENT_TTL_SECONDS = 210 * 24 * 3600

# Consent CSRF tokens live only for the duration a human spends on the screen.
CSRF_TTL_SECONDS = 600


_ATOMIC_GETDEL = """
local value = redis.call('GET', KEYS[1])
if not value then
    return nil
end
redis.call('DEL', KEYS[1])
return value
"""

_ATOMIC_GET_AND_EXPIRE = """
local value = redis.call('GET', KEYS[1])
if not value then
    return nil
end
redis.call('EXPIRE', KEYS[1], ARGV[1])
return value
"""

_ATOMIC_ROTATE_REFRESH_TOKEN = """
local current = redis.call('GET', KEYS[1])
if not current then
    return 0
end
if current ~= ARGV[1] then
    return -1
end
if redis.call('EXISTS', KEYS[2]) == 1 then
    return -2
end
redis.call('DEL', KEYS[1])
redis.call('SETEX', KEYS[2], ARGV[3], ARGV[2])
return 1
"""


class GrantStoreUnavailable(RuntimeError):
    """The shared OAuth state store could not complete an operation."""

    def __init__(self, operation: str):
        self.operation = str(operation or "unknown")
        super().__init__(f"OAuth grant store unavailable during {self.operation}")


@dataclass(frozen=True)
class RefreshTokenState:
    """Exact Redis record used to authorize one refresh-token rotation."""

    token: str
    raw: Any
    record: Dict[str, Any]


class GrantStore:
    def __init__(
        self,
        redis: Any,
        tenant: str,
        project: str,
        *,
        auth_code_ttl: int = AUTH_CODE_TTL_SECONDS,
        refresh_ttl: int = REFRESH_TTL_SECONDS,
    ):
        self._r = redis
        self._tenant = tenant
        self._project = project
        self._auth_code_ttl = auth_code_ttl
        self._refresh_ttl = refresh_ttl

    async def _redis_call(
        self,
        operation: str,
        method_name: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        try:
            method = getattr(self._r, method_name)
            return await method(*args, **kwargs)
        except GrantStoreUnavailable:
            raise
        except Exception as exc:
            raise GrantStoreUnavailable(operation) from exc

    def _key(self, kind: str, token: str) -> str:
        return f"{self._tenant}:{self._project}:kdcube:oauth:{kind}:{token}"

    # --------------------------- authorization codes ---------------------------

    async def create_auth_code(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        sub: str,
        scopes: List[str],
        operations: Optional[List[str]] = None,
        resource: Optional[str] = None,
        identity_scope: str = "",
        credential: Optional[Dict[str, Any]] = None,
        grantor_authority: Optional[Dict[str, Any]] = None,
        delegation_edges: Optional[List[Dict[str, Any]]] = None,
        named_services: Optional[Dict[str, Any]] = None,
        named_service_operations: Any = None,
        catalog_version: str = "",
        account_scope: Optional[Dict[str, Any]] = None,
        client_metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        code = secrets.token_urlsafe(32)
        payload = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "sub": sub,
            "scopes": scopes,
            "operations": list(operations or []),
            "resource": resource or "",
            "identity_scope": identity_scope or "",
            "credential": credential or {},
            "grantor_authority": grantor_authority or {},
            "delegation_edges": list(delegation_edges or []),
            "named_services": named_services or {},
            # The operator's operation choice and the catalog generation it was
            # made against; carried to token exchange so the card is born with
            # an explicit selection rather than an absent one.
            "named_service_operations": named_service_operations,
            "catalog_version": catalog_version or "",
            # Per-account claim picks made on the consent screen; carried to
            # token exchange so the registry card is born with the binding.
            "account_scope": dict(account_scope or {}),
            "client_metadata": dict(client_metadata or {}),
        }
        await self._redis_call(
            "authorization_code.create",
            "setex",
            self._key("code", code),
            self._auth_code_ttl,
            json.dumps(payload),
        )
        return code

    async def consume_auth_code(self, code: str) -> Optional[Dict[str, Any]]:
        raw = await self._redis_call(
            "authorization_code.consume",
            "eval",
            _ATOMIC_GETDEL,
            1,
            self._key("code", code),
        )
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    # ----------------------------- refresh tokens -----------------------------

    async def create_refresh_token(
        self,
        *,
        client_id: str,
        sub: str,
        scopes: List[str],
        operations: Optional[List[str]] = None,
        resource: Optional[str] = None,
        identity_scope: str = "",
        credential: Optional[Dict[str, Any]] = None,
        grantor_authority: Optional[Dict[str, Any]] = None,
        delegation_edges: Optional[List[Dict[str, Any]]] = None,
        named_services: Optional[Dict[str, Any]] = None,
        registry_access_id: str = "",
        client_metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        rt = secrets.token_urlsafe(40)
        payload = {
            "registry_access_id": str(registry_access_id or "").strip(),
            "client_id": client_id,
            "sub": sub,
            "scopes": scopes,
            "operations": list(operations or []),
            "resource": resource or "",
            "identity_scope": identity_scope or "",
            "credential": credential or {},
            "grantor_authority": grantor_authority or {},
            "delegation_edges": list(delegation_edges or []),
            "named_services": named_services or {},
            "client_metadata": dict(client_metadata or {}),
        }
        await self._redis_call(
            "refresh_token.create",
            "setex",
            self._key("refresh", rt),
            self._refresh_ttl,
            json.dumps(payload),
        )
        return rt

    async def get_refresh_token_state(
        self,
        refresh_token: str,
    ) -> Optional[RefreshTokenState]:
        token = str(refresh_token or "").strip()
        if not token:
            return None
        raw = await self._redis_call(
            "refresh_token.read",
            "get",
            self._key("refresh", token),
        )
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        return RefreshTokenState(token=token, raw=raw, record=payload)

    async def validate_refresh_token(self, refresh_token: str) -> Optional[Dict[str, Any]]:
        state = await self.get_refresh_token_state(refresh_token)
        return dict(state.record) if state is not None else None

    async def revoke_refresh_token(self, refresh_token: str) -> bool:
        token = str(refresh_token or "").strip()
        if not token:
            return False
        return bool(
            await self._redis_call(
                "refresh_token.revoke",
                "delete",
                self._key("refresh", token),
            )
        )

    @property
    def redis(self) -> Any:
        return self._r

    @property
    def refresh_ttl(self) -> int:
        return self._refresh_ttl

    # ------------------------------ consent CSRF ------------------------------

    async def create_csrf_token(
        self,
        sub: str,
        *,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Mint a single-use CSRF token bound to the user and consent context."""
        token = secrets.token_urlsafe(32)
        await self._redis_call(
            "consent_csrf.create",
            "setex",
            self._key("csrf", token),
            CSRF_TTL_SECONDS,
            json.dumps({"sub": sub, "context": dict(context or {})}),
        )
        return token

    async def consume_csrf_token(self, token: Optional[str], sub: str) -> bool:
        """True iff ``token`` exists, is bound to ``sub``, and was not used before."""
        ok, _reason = await self.consume_csrf_token_with_reason(token, sub)
        return ok

    async def consume_csrf_token_with_reason(self, token: Optional[str], sub: str) -> tuple[bool, str]:
        """Consume a CSRF token and return a non-secret diagnostic reason."""
        ok, reason, _context = await self.consume_csrf_token_context(token, sub)
        return ok, reason

    async def consume_csrf_token_context(
        self,
        token: Optional[str],
        sub: str,
    ) -> tuple[bool, str, Dict[str, Any]]:
        """Consume a CSRF token and return its server-authored consent context."""
        if not token:
            return False, "missing", {}
        raw = await self._redis_call(
            "consent_csrf.consume",
            "eval",
            _ATOMIC_GETDEL,
            1,
            self._key("csrf", token),
        )
        if raw is None:
            return False, "not_found", {}
        try:
            payload = json.loads(raw)
            stored_sub = payload.get("sub")
        except Exception:
            return False, "malformed_record", {}
        if stored_sub != sub:
            return False, "subject_mismatch", {}
        context = payload.get("context")
        return True, "ok", dict(context) if isinstance(context, dict) else {}

    # ------------------------- dynamic client registration -------------------------

    async def register_client(
        self,
        *,
        redirect_uris: List[str],
        application_type: str = "native",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        client_id = "dcr-" + secrets.token_urlsafe(16)
        record = {
            "client_id": client_id,
            "redirect_uris": list(redirect_uris),
            "token_endpoint_auth_method": "none",
            "application_type": application_type,
            "metadata": metadata or {},
        }
        # Sliding TTL (see CLIENT_TTL_SECONDS): long-lived for a connector in
        # use, finite for the registration junk repeated reconnects leave.
        await self._redis_call(
            "dynamic_client.register",
            "setex",
            self._key("client", client_id),
            CLIENT_TTL_SECONDS,
            json.dumps(record),
        )
        return record

    async def get_client_record(self, client_id: str) -> Optional[Dict[str, Any]]:
        raw = await self._redis_call(
            "dynamic_client.read",
            "eval",
            _ATOMIC_GET_AND_EXPIRE,
            1,
            self._key("client", client_id),
            CLIENT_TTL_SECONDS,
        )
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    # ----------------------- client metadata documents -----------------------

    def _client_metadata_key(self, client_id: str) -> str:
        digest = hashlib.sha256(client_id.encode("utf-8")).hexdigest()
        return self._key("client-metadata", digest)

    async def cache_client_metadata_document(
        self,
        client_id: str,
        client: Dict[str, Any],
        *,
        ttl_seconds: int,
    ) -> None:
        payload = {"status": "ok", "client": dict(client or {})}
        await self._redis_call(
            "client_metadata.cache",
            "setex",
            self._client_metadata_key(client_id),
            max(1, int(ttl_seconds)),
            json.dumps(payload),
        )

    async def get_client_metadata_cache(self, client_id: str) -> Optional[Dict[str, Any]]:
        raw = await self._redis_call(
            "client_metadata.read",
            "get",
            self._client_metadata_key(client_id),
        )
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    async def delete_client_metadata_cache(self, client_id: str) -> bool:
        return bool(
            await self._redis_call(
                "client_metadata.delete",
                "delete",
                self._client_metadata_key(client_id),
            )
        )

    async def rotate_refresh_token(
        self,
        refresh_token: str,
        *,
        scopes: Optional[List[str]] = None,
        operations: Optional[List[str]] = None,
        state: Optional[RefreshTokenState] = None,
    ) -> Optional[str]:
        """Rotate a refresh token and persist any freshly resolved authority.

        Pointer-backed callers resolve the current grant card before rotation.
        Their replacement record keeps that live snapshot for observability and
        consistency, while the pointer remains the authority on every use.
        """
        token = str(refresh_token or "").strip()
        current = state or await self.get_refresh_token_state(token)
        if current is None:
            return None
        if current.token != token:
            raise ValueError("refresh token state does not match the token being rotated")

        rec = current.record
        replacement = {
            "registry_access_id": str(rec.get("registry_access_id") or "").strip(),
            "client_id": rec["client_id"],
            "sub": rec["sub"],
            "scopes": list(rec.get("scopes") or []) if scopes is None else list(scopes),
            "operations": (
                list(rec.get("operations") or [])
                if operations is None
                else list(operations)
            ),
            "resource": rec.get("resource") or "",
            "identity_scope": rec.get("identity_scope") or "",
            "credential": rec.get("credential") or {},
            "grantor_authority": rec.get("grantor_authority") or {},
            "delegation_edges": list(rec.get("delegation_edges") or []),
            "named_services": rec.get("named_services") or {},
            "client_metadata": dict(rec.get("client_metadata") or {}),
        }
        encoded_replacement = json.dumps(replacement)

        # A generated-token collision must not consume the old token. The Lua
        # transition checks the replacement key before deleting the old key.
        for _attempt in range(3):
            new_token = secrets.token_urlsafe(40)
            if new_token == token:
                continue
            result = await self._redis_call(
                "refresh_token.rotate",
                "eval",
                _ATOMIC_ROTATE_REFRESH_TOKEN,
                2,
                self._key("refresh", token),
                self._key("refresh", new_token),
                current.raw,
                encoded_replacement,
                self._refresh_ttl,
            )
            code = int(result)
            if code == 1:
                return new_token
            if code in {0, -1}:
                return None
            if code != -2:
                raise GrantStoreUnavailable("refresh_token.rotate")
        raise GrantStoreUnavailable("refresh_token.rotate")

    # ---------------------- access-token operation grant ----------------------

    def _agrant_key(self, access_token: str) -> str:
        digest = hashlib.sha256(access_token.encode("utf-8")).hexdigest()
        return self._key("agrant", digest)

    async def bind_access_grant(
        self,
        access_token: str,
        operations: List[str],
        ttl_seconds: int,
        *,
        credential: Optional[Dict[str, Any]] = None,
        grantor_authority: Optional[Dict[str, Any]] = None,
        delegation_edges: Optional[List[Dict[str, Any]]] = None,
        named_services: Optional[Dict[str, Any]] = None,
        registry_access_id: str = "",
    ) -> None:
        """Record the consented operation allowlist and credential envelope for a token.

        ``registry_access_id`` makes the binding a POINTER: the guard resolves
        the registry card live, so card edits (extend/narrow/revoke) apply to
        this bearer immediately. A binding without it stays a legacy snapshot."""
        payload: Dict[str, Any] = {
            "operations": list(operations or []),
            "credential": credential or {},
            "grantor_authority": grantor_authority or {},
            "delegation_edges": list(delegation_edges or []),
            "named_services": named_services or {},
        }
        if str(registry_access_id or "").strip():
            payload["registry_access_id"] = str(registry_access_id).strip()
        await self._redis_call(
            "access_grant.bind",
            "setex",
            self._agrant_key(access_token),
            max(1, int(ttl_seconds)),
            json.dumps(payload),
        )

    async def revoke_access_grant(self, access_token: str) -> bool:
        token = str(access_token or "").strip()
        if not token:
            return False
        return bool(
            await self._redis_call(
                "access_grant.revoke",
                "delete",
                self._agrant_key(token),
            )
        )

    async def get_access_grant_record(self, access_token: str) -> Optional[Dict[str, Any]]:
        """Grant metadata bound to ``access_token`` (None if no grant record)."""
        raw = await self._redis_call(
            "access_grant.read",
            "get",
            self._agrant_key(access_token),
        )
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    async def get_access_grant(self, access_token: str) -> Optional[List[str]]:
        """The consented operations bound to ``access_token`` (None if no grant record)."""
        payload = await self.get_access_grant_record(access_token)
        if payload is None:
            return None
        try:
            return list(payload.get("operations") or [])
        except Exception:
            return None
