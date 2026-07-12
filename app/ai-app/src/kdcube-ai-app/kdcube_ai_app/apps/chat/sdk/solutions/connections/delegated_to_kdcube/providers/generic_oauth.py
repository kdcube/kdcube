# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Generic OAuth/OIDC adapter registration for delegated to KDCube."""

from __future__ import annotations

import base64
import json
from typing import Any, Mapping

import httpx

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.adapters import (
    DelegatedToKdcubeAdapter,
    adapter,
    register_adapter,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.models import (
    as_dict,
    as_str,
    as_str_list,
)


def _read_path(data: Mapping[str, Any], path: str) -> Any:
    current: Any = data
    for part in str(path or "").split("."):
        key = part.strip()
        if not key:
            continue
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _first(data: Mapping[str, Any], *paths: str) -> str:
    for path in paths:
        value = _read_path(data, path)
        text = as_str(value)
        if text:
            return text
    return ""


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = str(token or "").split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("utf-8"))
        parsed = json.loads(decoded.decode("utf-8"))
    except Exception:
        return {}
    return dict(parsed or {}) if isinstance(parsed, Mapping) else {}


def _provider_oauth_config(provider: Any) -> dict[str, Any]:
    config = {}
    adapter_config = as_dict(getattr(provider, "adapter_config", None))
    config.update(as_dict(adapter_config.get("oauth")))
    config.update(as_dict(adapter_config))
    config.update(as_dict(getattr(provider, "oauth", None)))
    return config


class _ConfiguredGenericOAuthAdapter(DelegatedToKdcubeAdapter):
    def __init__(self, *, adapter_id: str, config: Mapping[str, Any]) -> None:
        self.adapter_id = adapter_id
        self.label = as_str(config.get("label")) or "Generic OAuth"
        self.kind = as_str(config.get("kind")) or "oauth2"
        self.authorize_url = as_str(config.get("authorize_url") or config.get("authorization_url"))
        self.token_url = as_str(config.get("token_url"))
        self.oauth_default_scopes = as_str_list(config.get("default_scopes") or config.get("scopes"))
        self._config = dict(config or {})

    def authorize_scope_param(self) -> str:
        return as_str(self._config.get("scope_param")) or "scope"

    def authorize_extra_params(self) -> dict[str, Any]:
        params = as_dict(
            self._config.get("authorize_params")
            or self._config.get("authorization_params")
            or self._config.get("extra_authorize_params")
        )
        return {str(key): value for key, value in params.items() if value not in (None, "")}

    async def fetch_profile(self, *, access_token: str, token: dict[str, Any] | None = None) -> dict[str, Any]:
        userinfo_url = as_str(self._config.get("userinfo_url") or self._config.get("user_info_url"))
        if userinfo_url:
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(
                        userinfo_url,
                        headers={"Authorization": f"Bearer {access_token}"},
                    )
            except httpx.HTTPError as exc:
                raise RuntimeError(f"{self.adapter_id} userinfo request failed: {exc}") from exc
            try:
                data = response.json()
            except Exception:
                data = {}
            if not isinstance(data, Mapping) or response.status_code >= 400:
                detail = ""
                if isinstance(data, Mapping):
                    detail = as_str(data.get("error_description") or data.get("error"))
                raise RuntimeError(f"{self.adapter_id} userinfo failed: {detail or 'unknown error'}")
            normalized = self._normalize_identity(data)
            if normalized.get("external_subject"):
                return normalized
        return await self.normalize_profile(dict(token or {}))

    async def normalize_profile(self, credential: dict[str, Any]) -> dict[str, Any]:
        data = dict(credential or {})
        id_claims = _decode_jwt_payload(as_str(data.get("id_token")))
        merged = {**id_claims, **data}
        return self._normalize_identity(merged)

    def _normalize_identity(self, data: Mapping[str, Any]) -> dict[str, Any]:
        mapping = as_dict(self._config.get("profile") or self._config.get("profile_mapping"))
        subject_path = as_str(mapping.get("external_subject") or mapping.get("subject") or mapping.get("sub"))
        email_path = as_str(mapping.get("email"))
        name_path = as_str(mapping.get("display_name") or mapping.get("name"))
        workspace_path = as_str(mapping.get("workspace") or mapping.get("tenant") or mapping.get("organization"))
        external_subject = (
            _first(data, subject_path) if subject_path else _first(data, "sub", "id", "user_id", "username")
        )
        email = _first(data, email_path) if email_path else _first(data, "email", "mail")
        display_name = (
            _first(data, name_path) if name_path else _first(data, "name", "display_name", "preferred_username", "email", "sub")
        )
        workspace = (
            _first(data, workspace_path)
            if workspace_path
            else _first(data, "tenant", "tenant_id", "workspace", "workspace_id", "organization", "org_id")
        )
        return {
            "external_subject": external_subject,
            "email": email,
            "display_name": display_name,
            "workspace": workspace,
        }


@adapter("oauth2.generic")
class GenericOAuthAdapter(DelegatedToKdcubeAdapter):
    label = "Generic OAuth 2.0"
    kind = "oauth2"

    def bind(self, *, provider: Any = None, connector_app: Any = None) -> DelegatedToKdcubeAdapter:
        del connector_app
        return _ConfiguredGenericOAuthAdapter(
            adapter_id=self.adapter_id,
            config=_provider_oauth_config(provider),
        )

    async def normalize_profile(self, credential: dict[str, Any]) -> dict[str, Any]:
        return {}


class GenericOIDCAdapter(GenericOAuthAdapter):
    label = "Generic OIDC"
    kind = "oidc"


_oidc = GenericOIDCAdapter()
_oidc.adapter_id = "oidc.generic"
register_adapter(_oidc)


__all__ = ["GenericOAuthAdapter", "GenericOIDCAdapter"]
