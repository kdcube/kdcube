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
from typing import Any, Dict, List, Optional

# Authorization codes are exchanged immediately by the client.
AUTH_CODE_TTL_SECONDS = 60

# Generous refresh lifetime: a routine may run daily or only occasionally and
# must still be able to refresh rather than re-consent.
REFRESH_TTL_SECONDS = 180 * 24 * 3600

# Consent CSRF tokens live only for the duration a human spends on the screen.
CSRF_TTL_SECONDS = 600


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
        }
        await self._r.setex(self._key("code", code), self._auth_code_ttl, json.dumps(payload))
        return code

    async def consume_auth_code(self, code: str) -> Optional[Dict[str, Any]]:
        key = self._key("code", code)
        raw = await self._r.get(key)
        if raw is None:
            return None
        await self._r.delete(key)  # single use
        return json.loads(raw)

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
    ) -> str:
        rt = secrets.token_urlsafe(40)
        payload = {
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
        }
        await self._r.setex(self._key("refresh", rt), self._refresh_ttl, json.dumps(payload))
        return rt

    async def validate_refresh_token(self, refresh_token: str) -> Optional[Dict[str, Any]]:
        raw = await self._r.get(self._key("refresh", refresh_token))
        if raw is None:
            return None
        return json.loads(raw)

    async def revoke_refresh_token(self, refresh_token: str) -> bool:
        token = str(refresh_token or "").strip()
        if not token:
            return False
        return bool(await self._r.delete(self._key("refresh", token)))

    @property
    def redis(self) -> Any:
        return self._r

    @property
    def refresh_ttl(self) -> int:
        return self._refresh_ttl

    # ------------------------------ consent CSRF ------------------------------

    async def create_csrf_token(self, sub: str) -> str:
        """Mint a single-use CSRF token bound to the consenting admin's subject."""
        token = secrets.token_urlsafe(32)
        await self._r.setex(
            self._key("csrf", token), CSRF_TTL_SECONDS, json.dumps({"sub": sub})
        )
        return token

    async def consume_csrf_token(self, token: Optional[str], sub: str) -> bool:
        """True iff ``token`` exists, is bound to ``sub``, and was not used before."""
        ok, _reason = await self.consume_csrf_token_with_reason(token, sub)
        return ok

    async def consume_csrf_token_with_reason(self, token: Optional[str], sub: str) -> tuple[bool, str]:
        """Consume a CSRF token and return a non-secret diagnostic reason."""
        if not token:
            return False, "missing"
        key = self._key("csrf", token)
        raw = await self._r.get(key)
        if raw is None:
            return False, "not_found"
        await self._r.delete(key)  # single use
        try:
            stored_sub = json.loads(raw).get("sub")
        except Exception:
            return False, "malformed_record"
        if stored_sub != sub:
            return False, "subject_mismatch"
        return True, "ok"

    # ------------------------- dynamic client registration -------------------------

    async def register_client(
        self, *, redirect_uris: List[str], metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        client_id = "dcr-" + secrets.token_urlsafe(16)
        record = {
            "client_id": client_id,
            "redirect_uris": list(redirect_uris),
            "token_endpoint_auth_method": "none",
            "metadata": metadata or {},
        }
        # Registrations persist (no TTL) — a connector is long-lived.
        await self._r.set(self._key("client", client_id), json.dumps(record))
        return record

    async def get_client_record(self, client_id: str) -> Optional[Dict[str, Any]]:
        raw = await self._r.get(self._key("client", client_id))
        if raw is None:
            return None
        return json.loads(raw)

    async def rotate_refresh_token(self, refresh_token: str) -> Optional[str]:
        rec = await self.validate_refresh_token(refresh_token)
        if rec is None:
            return None
        await self._r.delete(self._key("refresh", refresh_token))
        return await self.create_refresh_token(
            client_id=rec["client_id"], sub=rec["sub"], scopes=rec["scopes"],
            operations=rec.get("operations") or [],
            resource=rec.get("resource") or "",
            identity_scope=rec.get("identity_scope") or "",
            credential=rec.get("credential") or {},
            grantor_authority=rec.get("grantor_authority") or {},
            delegation_edges=rec.get("delegation_edges") or [],
            named_services=rec.get("named_services") or {},
        )

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
    ) -> None:
        """Record the consented operation allowlist and credential envelope for a token."""
        await self._r.setex(
            self._agrant_key(access_token), max(1, int(ttl_seconds)),
            json.dumps({
                "operations": list(operations or []),
                "credential": credential or {},
                "grantor_authority": grantor_authority or {},
                "delegation_edges": list(delegation_edges or []),
                "named_services": named_services or {},
            }),
        )

    async def revoke_access_grant(self, access_token: str) -> bool:
        token = str(access_token or "").strip()
        if not token:
            return False
        return bool(await self._r.delete(self._agrant_key(token)))

    async def get_access_grant_record(self, access_token: str) -> Optional[Dict[str, Any]]:
        """Grant metadata bound to ``access_token`` (None if no grant record)."""
        raw = await self._r.get(self._agrant_key(access_token))
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
