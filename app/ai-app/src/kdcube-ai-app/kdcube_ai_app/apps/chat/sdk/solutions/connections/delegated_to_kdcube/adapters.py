# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Provider adapter registry for delegated to KDCube.

Adapters are provider protocol mechanics only. They do not own platform
identity, policy, consent, or credential storage.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any
import time
import httpx


class DelegatedToKdcubeAdapter(ABC):
    adapter_id: str = ""
    label: str = ""
    kind: str = ""
    authorize_url: str = ""
    token_url: str = ""
    oauth_default_scopes: tuple[str, ...] = ()

    @property
    def oauth_enabled(self) -> bool:
        return bool(self.authorize_url and self.token_url)

    def bind(self, *, provider: Any = None, connector_app: Any = None) -> "DelegatedToKdcubeAdapter":
        """Return an adapter configured for one provider/connector app.

        Built-in fixed providers return ``self``. Config-driven providers
        override this to read endpoints and profile mapping from Connection Hub
        provider config without leaking that config into operation code.
        """
        del provider, connector_app
        return self

    def provider_scopes_for_claims(self, claims: list[str], claim_map: dict[str, Any]) -> list[str]:
        scopes: list[str] = []
        seen: set[str] = set()
        for claim in claims:
            raw = claim_map.get(str(claim or "").strip())
            provider_scopes = []
            if hasattr(raw, "provider_scopes"):
                provider_scopes = list(getattr(raw, "provider_scopes") or [])
            elif isinstance(raw, dict):
                provider_scopes = list(raw.get("provider_scopes") or raw.get("scopes") or [])
            for scope in provider_scopes:
                value = str(scope or "").strip()
                if value and value not in seen:
                    seen.add(value)
                    scopes.append(value)
        if not scopes:
            scopes.extend(self.oauth_default_scopes)
        return scopes

    def authorize_scope_param(self) -> str:
        return "scope"

    def authorize_extra_params(self) -> dict[str, Any]:
        return {}

    def extract_token(self, raw: dict[str, Any]) -> dict[str, Any]:
        return dict(raw or {})

    def credential_expires_at(self, credential: dict[str, Any]) -> int:
        try:
            return int(credential.get("expires_at") or 0)
        except Exception:
            return 0

    def credential_refreshable(self, credential: dict[str, Any]) -> bool:
        return bool(self.oauth_enabled and str(credential.get("refresh_token") or "").strip())

    def credential_expired(self, credential: dict[str, Any], *, skew_seconds: int = 120) -> bool:
        expires_at = self.credential_expires_at(credential)
        return bool(expires_at and expires_at <= int(time.time()) + int(skew_seconds or 0))

    def credential_refresh_needed(self, credential: dict[str, Any], *, skew_seconds: int = 120) -> bool:
        if self.credential_expired(credential, skew_seconds=skew_seconds):
            return True
        # Older connected-account records may have a refresh token but no
        # expires_at. Refresh once to normalize them before provider use.
        return bool(
            credential.get("oauth")
            and self.credential_refreshable(credential)
            and not self.credential_expires_at(credential)
            and not credential.get("refreshed_at")
        )

    async def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        client_id: str,
        client_secret: str,
    ) -> dict[str, Any]:
        if not self.token_url:
            raise ValueError(f"{self.adapter_id} does not support OAuth token exchange")
        if not client_id or not client_secret:
            raise ValueError(f"{self.adapter_id} OAuth client id/secret are not configured")
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.token_url,
                    data=data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"{self.adapter_id} token exchange failed: {exc}") from exc
        try:
            parsed = response.json()
        except Exception:
            parsed = {}
        if response.status_code >= 400:
            message = ""
            if isinstance(parsed, dict):
                message = str(parsed.get("error_description") or parsed.get("error") or "")
            raise RuntimeError(message or f"{self.adapter_id} token exchange failed: HTTP {response.status_code}")
        token = self.extract_token(parsed if isinstance(parsed, dict) else {})
        if "expires_in" in token and "expires_at" not in token:
            try:
                token["expires_at"] = int(time.time()) + int(token["expires_in"])
            except Exception:
                pass
        return token

    async def refresh_credential(
        self,
        credential: dict[str, Any],
        *,
        client_id: str,
        client_secret: str,
    ) -> dict[str, Any]:
        if not self.token_url:
            raise ValueError(f"{self.adapter_id} does not support OAuth refresh")
        refresh_token = str(credential.get("refresh_token") or "").strip()
        if not refresh_token:
            raise ValueError(f"{self.adapter_id} credential has no refresh token")
        if not client_id or not client_secret:
            raise ValueError(f"{self.adapter_id} OAuth client id/secret are not configured")
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.token_url,
                    data=data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"{self.adapter_id} token refresh failed: {exc}") from exc
        try:
            parsed = response.json()
        except Exception:
            parsed = {}
        if response.status_code >= 400:
            message = ""
            if isinstance(parsed, dict):
                message = str(parsed.get("error_description") or parsed.get("error") or "")
            raise RuntimeError(message or f"{self.adapter_id} token refresh failed: HTTP {response.status_code}")
        token = self.extract_token(parsed if isinstance(parsed, dict) else {})
        if "expires_in" in token and "expires_at" not in token:
            try:
                token["expires_at"] = int(time.time()) + int(token["expires_in"])
            except Exception:
                pass
        refreshed = dict(credential or {})
        refreshed.update(token)
        refreshed["refresh_token"] = str(token.get("refresh_token") or refresh_token)
        refreshed["oauth"] = bool(refreshed.get("oauth", True))
        refreshed["refreshed_at"] = int(time.time())
        return refreshed

    async def fetch_profile(self, *, access_token: str, token: dict[str, Any] | None = None) -> dict[str, Any]:
        del access_token, token
        return {}

    @abstractmethod
    async def normalize_profile(self, credential: dict[str, Any]) -> dict[str, Any]:
        """Return normalized connected-account identity fields for a credential."""
        raise NotImplementedError


_ADAPTERS: dict[str, DelegatedToKdcubeAdapter] = {}


def register_adapter(adapter: DelegatedToKdcubeAdapter | type[DelegatedToKdcubeAdapter]) -> DelegatedToKdcubeAdapter:
    inst = adapter() if isinstance(adapter, type) else adapter
    adapter_id = str(getattr(inst, "adapter_id", "") or "").strip()
    if not adapter_id:
        raise ValueError("DelegatedToKdcubeAdapter.adapter_id is required")
    _ADAPTERS[adapter_id] = inst
    return inst


def adapter(adapter_id: str):
    def _decorator(cls: type[DelegatedToKdcubeAdapter]) -> type[DelegatedToKdcubeAdapter]:
        inst = cls()
        inst.adapter_id = str(adapter_id or getattr(inst, "adapter_id", "") or "").strip()
        register_adapter(inst)
        return cls

    return _decorator


def resolve_adapter(adapter_id: str) -> DelegatedToKdcubeAdapter:
    key = str(adapter_id or "").strip()
    try:
        return _ADAPTERS[key]
    except KeyError as exc:
        raise KeyError(f"unknown delegated to KDCube adapter: {key!r}") from exc


def list_adapters() -> list[DelegatedToKdcubeAdapter]:
    return sorted(_ADAPTERS.values(), key=lambda item: getattr(item, "adapter_id", ""))


__all__ = [
    "DelegatedToKdcubeAdapter",
    "adapter",
    "list_adapters",
    "register_adapter",
    "resolve_adapter",
]
