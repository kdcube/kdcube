# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Descriptor-backed Connection Hub authority registry helpers.

This module handles the static descriptor shape:

    authority_registry.authorities.<authority_id>.providers.<provider_id>

`provider_id` is a configured provider instance. `provider.type` is the
implementation type, for example `bundle_session_login` or `telegram_init_data`.
"""

from __future__ import annotations

from typing import Any, Mapping

DEFAULT_PLATFORM_AUTHORITY_ID = "kdcube.platform"
DEFAULT_PLATFORM_PROVIDER_ID = "cognito"


def _str(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _normalize_provider_type(value: Any) -> str:
    provider_type = _str(value).lower()
    cognito_alias = provider_type.replace("_", "-")
    if cognito_alias in {"multi-cognito", "cognito-multi"}:
        return "multi-cognito"
    return provider_type


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return _str(value).lower() in {"1", "true", "yes", "on", "enabled"}


def authority_registry_config(props: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return `authority_registry` from bundle props."""

    raw = _dict(props).get("authority_registry")
    return _dict(raw)


def _provider_entrypoints(provider: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Return browser/runtime entrypoints declared by one provider instance.

    Canonical descriptor shape:

        providers.<provider_id>.entrypoints.login
        providers.<provider_id>.entrypoints.session_issue
        providers.<provider_id>.entrypoints.consent

    Provider operations are declared only through `entrypoints`; this keeps the
    descriptor shape explicit and avoids overloading one `host` field for
    browser pages, callbacks, and consent renderers.
    """

    out: dict[str, dict[str, Any]] = {}
    raw = _dict(provider.get("entrypoints"))
    for name, value in raw.items():
        endpoint = _dict(value)
        if endpoint:
            out[_str(name)] = endpoint

    return out


def authority_provider_instances(registry: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Flatten configured authority provider instances.

    Returned rows include normalized `authority_id`, `provider_id`,
    `provider_type`, `platform`, `authority`, and `provider` fields.
    """

    out: list[dict[str, Any]] = []
    authorities = _dict(_dict(registry).get("authorities"))
    for authority_id, authority_raw in authorities.items():
        authority = _dict(authority_raw)
        providers = _dict(authority.get("providers"))
        for provider_id, provider_raw in providers.items():
            provider = _dict(provider_raw)
            if provider.get("enabled") is False:
                continue
            out.append(
                {
                    "authority_id": _str(authority_id),
                    "provider_id": _str(provider_id),
                    "provider_type": _normalize_provider_type(provider.get("type")),
                    "platform": _bool(authority.get("platform"), False),
                    "entrypoints": _provider_entrypoints(provider),
                    "authority": {
                        key: value
                        for key, value in authority.items()
                        if key != "providers"
                    },
                    "provider": {
                        **provider,
                        "id": _str(provider_id),
                        "provider_id": _str(provider_id),
                        "type": _normalize_provider_type(provider.get("type")),
                    },
                }
            )
    return out


def resolve_authority_provider_instance(
    registry: Mapping[str, Any] | None,
    *,
    authority_id: str = "",
    provider_id: str = "",
    provider_type: str = "",
    host_bundle_id: str = "",
    host_route: str = "",
    host_operation: str = "",
) -> dict[str, Any]:
    """Resolve one provider instance by id or by hosted operation."""

    wanted_authority = _str(authority_id)
    wanted_provider = _str(provider_id)
    wanted_type = _normalize_provider_type(provider_type)
    wanted_bundle = _str(host_bundle_id)
    wanted_route = _str(host_route)
    wanted_operation = _str(host_operation)

    def _endpoint_matches(endpoint: Mapping[str, Any]) -> bool:
        if wanted_bundle and _str(endpoint.get("bundle_id") or endpoint.get("app_id")) != wanted_bundle:
            return False
        if wanted_route and _str(endpoint.get("route")) != wanted_route:
            return False
        if wanted_operation and _str(endpoint.get("operation") or endpoint.get("alias")) != wanted_operation:
            return False
        return True

    matches: list[dict[str, Any]] = []
    for row in authority_provider_instances(registry):
        if wanted_authority and row["authority_id"] != wanted_authority:
            continue
        if wanted_provider and row["provider_id"] != wanted_provider:
            continue
        if wanted_type and row["provider_type"] != wanted_type:
            continue

        provider = _dict(row.get("provider"))
        entrypoints = _dict(row.get("entrypoints"))
        if (wanted_bundle or wanted_route or wanted_operation) and not any(
            _endpoint_matches(endpoint)
            for endpoint in entrypoints.values()
            if isinstance(endpoint, Mapping)
        ):
            continue
        matches.append(row)

    if not matches:
        return {"ok": False, "error": "authority_provider_not_found"}
    if len(matches) > 1:
        return {
            "ok": False,
            "error": "authority_provider_ambiguous",
            "matches": [
                {
                    "authority_id": item["authority_id"],
                    "provider_id": item["provider_id"],
                    "provider_type": item["provider_type"],
                }
                for item in matches
            ],
        }
    return {"ok": True, **matches[0]}


def _provider_field(provider: Mapping[str, Any], *keys: str) -> Any:
    authenticator = _dict(provider.get("authenticator"))
    issuer = _dict(provider.get("issuer"))
    for key in keys:
        if provider.get(key) not in (None, ""):
            return provider.get(key)
        if authenticator.get(key) not in (None, ""):
            return authenticator.get(key)
        if issuer.get(key) not in (None, ""):
            return issuer.get(key)
    return None


def _provider_cookie_field(provider: Mapping[str, Any], *keys: str) -> Any:
    cookie = _dict(provider.get("cookie"))
    authenticator_cookie = _dict(_dict(provider.get("authenticator")).get("cookie"))
    issuer_cookie = _dict(_dict(provider.get("issuer")).get("cookie"))
    session_cookie = _dict(_dict(provider.get("session")).get("cookie"))
    for key in keys:
        for source in (cookie, authenticator_cookie, issuer_cookie, session_cookie):
            if source.get(key) not in (None, ""):
                return source.get(key)
    return None


def _token_transport_config(provider: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id_token_header_name": _str(_provider_field(provider, "id_token_header_name")) or "X-ID-Token",
        "auth_token_cookie_name": _str(_provider_cookie_field(provider, "auth_token_cookie_name", "authTokenCookieName")) or "__Secure-LATC",
        "id_token_cookie_name": _str(_provider_cookie_field(provider, "id_token_cookie_name", "idTokenCookieName")) or "__Secure-LITC",
        "masqueraded_token_cookie_name": _str(
            _provider_cookie_field(provider, "masqueraded_token_cookie_name", "masqueradedTokenCookieName")
        ) or "__Secure-LMTC",
    }


def _cognito_provider_record(raw: Any, *, default_alias: str = "") -> dict[str, str]:
    value = _dict(raw)
    alias = _str(value.get("alias") or value.get("id") or value.get("name") or default_alias)
    region = _str(value.get("region"))
    user_pool_id = _str(value.get("user_pool_id") or value.get("pool_id"))
    app_client_id = _str(value.get("app_client_id") or value.get("client_id"))
    if not (alias and region and user_pool_id and app_client_id):
        return {}
    out = {
        "alias": alias,
        "kind": _str(value.get("kind") or "cognito") or "cognito",
        "region": region,
        "user_pool_id": user_pool_id,
        "app_client_id": app_client_id,
    }
    hosted_ui_domain = _str(value.get("hosted_ui_domain") or value.get("hosted_ui"))
    if hosted_ui_domain:
        out["hosted_ui_domain"] = hosted_ui_domain
    return out


def cognito_platform_auth_config(provider_result: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize a platform Cognito provider into runtime auth fields.

    The Connection Hub authority registry owns the descriptor shape. This
    function converts one resolved provider into the current runtime shape used
    by CognitoAuthManager/MultiCognitoAuthManager and frontend config.
    """

    result = _dict(provider_result)
    if not result.get("ok", True):
        return {}
    provider = _dict(result.get("provider"))
    provider_type = _normalize_provider_type(provider.get("type") or result.get("provider_type"))
    if provider_type not in {"cognito", "multi-cognito"}:
        return {}

    trusted: list[dict[str, str]] = []
    raw_trusted = (
        provider.get("trusted_providers")
        or _dict(provider.get("authenticator")).get("trusted_providers")
        or provider.get("providers")
    )
    if isinstance(raw_trusted, Mapping):
        for alias, raw in raw_trusted.items():
            record = _cognito_provider_record(raw, default_alias=_str(alias))
            if record:
                trusted.append(record)
    elif isinstance(raw_trusted, list):
        for raw in raw_trusted:
            record = _cognito_provider_record(raw)
            if record:
                trusted.append(record)

    primary = {
        "alias": "primary",
        "kind": "cognito",
        "region": _str(_provider_field(provider, "region")),
        "user_pool_id": _str(_provider_field(provider, "user_pool_id", "pool_id")),
        "app_client_id": _str(_provider_field(provider, "app_client_id", "client_id")),
    }
    hosted_ui_domain = _str(_provider_field(provider, "hosted_ui_domain", "hosted_ui"))
    if hosted_ui_domain:
        primary["hosted_ui_domain"] = hosted_ui_domain
    if primary["region"] and primary["user_pool_id"] and primary["app_client_id"]:
        key = (primary["region"], primary["user_pool_id"], primary["app_client_id"])
        if not any((row.get("region"), row.get("user_pool_id"), row.get("app_client_id")) == key for row in trusted):
            trusted.insert(0, primary)

    if provider_type == "cognito" and len(trusted) > 1:
        provider_type = "multi-cognito"
    if provider_type == "multi-cognito" and len(trusted) == 1:
        provider_type = "cognito"

    first = trusted[0] if trusted else {}
    return {
        "auth_provider": provider_type,
        "region": _str(_provider_field(provider, "region")) or _str(first.get("region")),
        "user_pool_id": _str(_provider_field(provider, "user_pool_id", "pool_id")) or _str(first.get("user_pool_id")),
        "app_client_id": _str(_provider_field(provider, "app_client_id", "client_id")) or _str(first.get("app_client_id")),
        "service_client_id": _str(_provider_field(provider, "service_client_id")) or _str(first.get("app_client_id")),
        "trusted_providers": trusted,
        **_token_transport_config(provider),
        "jwks_cache_ttl_seconds": _provider_field(provider, "jwks_cache_ttl_seconds") or 86400,
        "provider": provider,
        "authority": _dict(result.get("authority")),
    }


def platform_authority_auth_config(provider_result: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize any platform authority provider into runtime auth fields."""

    result = _dict(provider_result)
    if not result.get("ok", True):
        return {}
    provider = _dict(result.get("provider"))
    provider_type = _normalize_provider_type(provider.get("type") or result.get("provider_type"))
    if provider_type in {"cognito", "multi-cognito"}:
        return cognito_platform_auth_config(result)
    if provider_type in {"bundle_session_login", "bundle-session-login", "bundle_session", "bundle-session", "session"}:
        return {
            "auth_provider": "session",
            **_token_transport_config(provider),
            "provider": provider,
            "authority": _dict(result.get("authority")),
        }
    return {}


def resolve_platform_authority_provider(
    registry: Mapping[str, Any] | None,
    *,
    authority_id: str = DEFAULT_PLATFORM_AUTHORITY_ID,
    provider_id: str = DEFAULT_PLATFORM_PROVIDER_ID,
    provider_type: str = "",
) -> dict[str, Any]:
    """Resolve the selected platform authority provider from a registry."""

    return resolve_authority_provider_instance(
        registry,
        authority_id=_str(authority_id) or DEFAULT_PLATFORM_AUTHORITY_ID,
        provider_id=_str(provider_id) or DEFAULT_PLATFORM_PROVIDER_ID,
        provider_type=_str(provider_type),
    )


__all__ = [
    "DEFAULT_PLATFORM_AUTHORITY_ID",
    "DEFAULT_PLATFORM_PROVIDER_ID",
    "authority_provider_instances",
    "authority_registry_config",
    "cognito_platform_auth_config",
    "platform_authority_auth_config",
    "resolve_platform_authority_provider",
    "resolve_authority_provider_instance",
]
