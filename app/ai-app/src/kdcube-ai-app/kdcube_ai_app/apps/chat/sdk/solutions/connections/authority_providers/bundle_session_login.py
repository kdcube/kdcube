# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Connection Hub runtime for bundle-hosted platform session authorities.

The bundle owns the user-facing login route/UI. This SDK module owns the
authority-registry lookup, upstream proof verification, role/provisioning
resolution, and KDCube bundle-session issuance.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Mapping

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from kdcube_ai_app.apps.chat.sdk.config import get_secret, get_settings
from kdcube_ai_app.apps.chat.sdk.integrations.google import oidc as google_oidc
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import widget_auth as telegram_widget_auth
from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry_client import AuthorityRegistryClient
from kdcube_ai_app.auth.bundle import get_bundle_session_authority

log = logging.getLogger("kdcube.connection_hub.authority_provider.bundle_session_login")

PROVIDER_TYPE = "bundle_session_login"
DEFAULT_TELEGRAM_OPERATION = "auth_telegram_session"
DEFAULT_GOOGLE_OPERATION = "auth_google_session"


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _str(value: Any) -> str:
    return str(value or "").strip()


def _entrypoint_matches_current_operation(
    endpoint: Mapping[str, Any],
    *,
    bundle_id: str,
    route: str,
    operation: str,
) -> bool:
    return (
        _str(endpoint.get("bundle_id") or endpoint.get("app_id")) == bundle_id
        and (_str(endpoint.get("route")) or "public") == route
        and _str(endpoint.get("operation") or endpoint.get("alias")) == operation
    )


def _required_positive_int(config: Mapping[str, Any], key: str) -> int:
    try:
        value = int(config.get(key) or 0)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Authority provider issuer {key} must be an integer") from exc
    if value <= 0:
        raise HTTPException(status_code=500, detail=f"Authority provider issuer {key} must be positive")
    return value


async def resolve_bundle_session_login_provider(
    entrypoint: Any,
    *,
    bundle_id: str,
    operation: str,
) -> dict[str, Any]:
    """Resolve the platform-capable provider hosted by this bundle operation."""

    result = await AuthorityRegistryClient(entrypoint).resolve_provider(
        provider_type=PROVIDER_TYPE,
        host_bundle_id=bundle_id,
        host_route="public",
        host_operation=operation,
    )
    if not result.get("ok"):
        raise HTTPException(
            status_code=500,
            detail=f"Connection Hub authority provider is not registered: {result.get('error') or 'not_found'}",
        )
    if not bool(result.get("platform")):
        raise HTTPException(status_code=500, detail="Registered authority provider is not platform-capable")
    entrypoints = _dict(result.get("entrypoints"))
    if not any(
        _entrypoint_matches_current_operation(
            _dict(endpoint),
            bundle_id=bundle_id,
            route="public",
            operation=operation,
        )
        for endpoint in entrypoints.values()
    ):
        raise HTTPException(
            status_code=500,
            detail="Registered authority provider entrypoint does not match this bundle operation",
        )
    return result


async def resolve_provider_ref(entrypoint: Any, ref: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve an authority/provider reference from the registry."""

    authority_id = _str(ref.get("authority_id"))
    provider_id = _str(ref.get("provider_id") or ref.get("authenticator_id"))
    result = await AuthorityRegistryClient(entrypoint).resolve_provider(
        authority_id=authority_id,
        provider_id=provider_id,
    )
    if not result.get("ok"):
        raise HTTPException(
            status_code=500,
            detail=f"Connection Hub upstream authenticator is not registered: {result.get('error') or 'not_found'}",
        )
    return result


async def authenticator_client_id(authenticator: Mapping[str, Any]) -> str:
    client_id = _str(authenticator.get("client_id") or authenticator.get("audience"))
    secret_ref = _str(authenticator.get("client_id_secret_ref") or authenticator.get("audience_secret_ref"))
    if not client_id and secret_ref:
        client_id = _str(await get_secret(secret_ref, default=None))
    if not client_id:
        raise HTTPException(status_code=500, detail="Google authority provider authenticator.client_id is not configured")
    return client_id


def _lookup_mapping(mapping: Mapping[str, Any], key: str) -> dict[str, Any]:
    wanted = _str(key)
    if not wanted:
        return {}
    if wanted in mapping and isinstance(mapping.get(wanted), Mapping):
        return dict(mapping[wanted])
    lowered = wanted.lower()
    for item_key, item_value in mapping.items():
        if _str(item_key).lower() == lowered and isinstance(item_value, Mapping):
            return dict(item_value)
    return {}


def _claim_value(claims: Mapping[str, Any], key: str) -> Any:
    cur: Any = claims
    for part in _str(key).split("."):
        if not part:
            continue
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(part)
    return cur


def _values_equal(actual: Any, expected: Any) -> bool:
    if isinstance(expected, bool):
        if isinstance(actual, str):
            actual_bool = actual.lower() in {"1", "true", "yes", "on"}
            if not expected and actual.lower() in {"0", "false", "no", "off", ""}:
                actual_bool = False
            return actual_bool is expected
        return bool(actual) is expected
    return _str(actual).lower() == _str(expected).lower()


def _authority_grants_config(authority_cfg: Mapping[str, Any]) -> dict[str, Any]:
    return _dict(authority_cfg.get("grants"))


def _grant_from_subject_record(record: Mapping[str, Any]) -> dict[str, Any]:
    grant = {
        "roles": record.get("roles"),
        "permissions": record.get("permissions"),
    }
    return {key: value for key, value in grant.items() if value is not None}


def _find_subject_grant(
    authority_cfg: Mapping[str, Any],
    *,
    sub: str,
    provider: str,
    provider_subject: str,
) -> tuple[dict[str, Any], str]:
    grants_cfg = _authority_grants_config(authority_cfg)
    subjects = _dict(grants_cfg.get("subjects"))
    for lookup_key in (
        sub,
        f"{provider}:{provider_subject}" if provider and provider_subject else "",
    ):
        record = _lookup_mapping(subjects, lookup_key)
        grant = _grant_from_subject_record(record)
        if grant:
            return grant, _str(record.get("label") or record.get("source") or "grants.subjects")
    return {}, ""


def _bootstrap_rule_matches(
    rule: Mapping[str, Any],
    *,
    sub: str,
    provider: str,
    provider_subject: str,
    verified_claims: Mapping[str, Any],
) -> bool:
    when = _dict(rule.get("when"))
    rule_provider = _str(when.get("provider"))
    if rule_provider and rule_provider != provider:
        return False
    configured_subject = _str(when.get("subject") or when.get("sub"))
    if configured_subject and configured_subject.lower() != sub.lower():
        return False
    configured_provider_subject = _str(when.get("provider_subject"))
    if configured_provider_subject and configured_provider_subject.lower() != provider_subject.lower():
        return False

    claims_match = _dict(when.get("claims"))
    if not claims_match:
        return bool(configured_subject or configured_provider_subject or rule_provider)

    # Email can be a bootstrap matcher only after the upstream authority proves
    # it. The identity key remains the stable authority subject, not email.
    if provider == "google" and "email" in claims_match and bool(verified_claims.get("email_verified")) is not True:
        return False

    for key, expected in claims_match.items():
        actual = _claim_value(verified_claims, key)
        if not _values_equal(actual, expected):
            return False
    return True


def _find_bootstrap_grant(
    *,
    authority_cfg: Mapping[str, Any],
    sub: str,
    provider: str,
    provider_subject: str,
    verified_claims: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    grants_cfg = _authority_grants_config(authority_cfg)
    rules = grants_cfg.get("bootstrap_rules")
    if not isinstance(rules, list):
        return {}, ""
    for raw_rule in rules:
        rule = _dict(raw_rule)
        if rule and _bootstrap_rule_matches(
            rule,
            sub=sub,
            provider=provider,
            provider_subject=provider_subject,
            verified_claims=verified_claims,
        ):
            grant = {
                "roles": rule.get("roles"),
                "permissions": rule.get("permissions"),
            }
            grant = {key: value for key, value in grant.items() if value is not None}
            if grant:
                return grant, _str(rule.get("id") or rule.get("label") or "grants.bootstrap_rule")
    return {}, ""


def _bounded_values(
    requested: list[str],
    *,
    allowed: list[str],
    field: str,
) -> list[str]:
    if not requested:
        return []
    if not allowed:
        raise HTTPException(
            status_code=500,
            detail=f"Authority provider subject access requests non-assignable {field}: {requested}",
        )
    allowed_set = set(allowed)
    denied = [item for item in requested if item not in allowed_set]
    if denied:
        raise HTTPException(
            status_code=500,
            detail=f"Authority provider subject access requests non-assignable {field}: {denied}",
        )
    return requested


def resolve_platform_grants(
    *,
    authority_cfg: Mapping[str, Any],
    provider_cfg: Mapping[str, Any],
    sub: str,
    provider: str,
    provider_subject: str,
    verified_claims: Mapping[str, Any] | None = None,
) -> tuple[list[str], list[str], str]:
    """Resolve roles/permissions for the issued platform session.

    `authority.grants.subjects` is the canonical persisted assignment surface.
    `authority.grants.bootstrap_rules` exists for bootstrap cases where an
    admin knows a verified upstream claim, such as a Google email, before they
    know the stable provider subject.
    """

    grants_cfg = _dict(provider_cfg.get("grants"))
    default_access = _dict(grants_cfg.get("default"))
    assignable_access = _dict(grants_cfg.get("assignable"))
    default_roles = _as_str_list(default_access.get("roles"))
    default_permissions = _as_str_list(default_access.get("permissions"))
    assignable_roles = _as_str_list(assignable_access.get("roles"))
    assignable_permissions = _as_str_list(assignable_access.get("permissions"))

    subject_grant, subject_source = _find_subject_grant(
        authority_cfg,
        sub=sub,
        provider=provider,
        provider_subject=provider_subject,
    )
    if subject_grant:
        roles = _as_str_list(subject_grant.get("roles"))
        permissions = _as_str_list(subject_grant.get("permissions"))
        return (
            _bounded_values(roles, allowed=assignable_roles, field="roles"),
            _bounded_values(permissions, allowed=assignable_permissions, field="permissions"),
            subject_source,
        )

    bootstrap_grant, bootstrap_source = _find_bootstrap_grant(
        authority_cfg=authority_cfg,
        sub=sub,
        provider=provider,
        provider_subject=provider_subject,
        verified_claims=_dict(verified_claims),
    )
    if bootstrap_grant:
        roles = _as_str_list(bootstrap_grant.get("roles"))
        permissions = _as_str_list(bootstrap_grant.get("permissions"))
        return (
            _bounded_values(roles, allowed=assignable_roles, field="roles"),
            _bounded_values(permissions, allowed=assignable_permissions, field="permissions"),
            bootstrap_source,
        )

    return default_roles, default_permissions, "grants.default"


def _cookie_config(issuer_cfg: Mapping[str, Any]) -> tuple[bool, str]:
    cookie_cfg = _dict(issuer_cfg.get("cookie"))
    secure = bool(cookie_cfg.get("secure", True))
    samesite = _str(cookie_cfg.get("same_site") or cookie_cfg.get("samesite") or "lax") or "lax"
    return secure, samesite


def _session_response(
    *,
    issuer_cfg: Mapping[str, Any],
    authority_id: str,
    provider_id: str,
    provider: str,
    provider_subject: str,
    sub: str,
    grant: Any,
    roles: list[str],
    permissions: list[str],
    role_binding_source: str,
) -> JSONResponse:
    auth_cfg = get_settings().AUTH
    secure, samesite = _cookie_config(issuer_cfg)
    response = JSONResponse(
        {
            "ok": True,
            "auth_surface": "bundle_session",
            "authority_id": authority_id,
            "authority_provider_id": provider_id,
            "provider": provider,
            "provider_subject": provider_subject,
            "sub": sub,
            "session_id": grant.session_id,
            "expires_at": grant.expires_at,
            "roles": roles,
            "permissions": permissions,
            "role_binding_source": role_binding_source,
        }
    )
    cookie_name = _str(auth_cfg.AUTH_TOKEN_COOKIE_NAME)
    if cookie_name:
        response.set_cookie(
            cookie_name,
            grant.token,
            path="/",
            secure=secure,
            httponly=True,
            samesite=samesite,
        )
    return response


async def google_login_client_config(
    entrypoint: Any,
    *,
    bundle_id: str,
    operation: str = DEFAULT_GOOGLE_OPERATION,
) -> dict[str, Any]:
    registry_provider = await resolve_bundle_session_login_provider(
        entrypoint,
        bundle_id=bundle_id,
        operation=operation,
    )
    provider_cfg = _dict(registry_provider.get("provider"))
    input_authenticator_ref = _dict(_dict(provider_cfg.get("input")).get("authenticator_ref"))
    upstream = await resolve_provider_ref(entrypoint, input_authenticator_ref)
    upstream_provider_cfg = _dict(upstream.get("provider"))
    client_id = await authenticator_client_id(_dict(upstream_provider_cfg.get("authenticator")))
    return {
        "registry_provider": registry_provider,
        "provider": provider_cfg,
        "input_authenticator_ref": input_authenticator_ref,
        "upstream": upstream,
        "client_id": client_id,
    }


async def issue_telegram_session(
    entrypoint: Any,
    *,
    request: Any = None,
    telegram_init_data: str = "",
    payload: Mapping[str, Any] | None = None,
    bundle_id: str,
    operation: str = DEFAULT_TELEGRAM_OPERATION,
):
    del payload
    registry_provider = await resolve_bundle_session_login_provider(
        entrypoint,
        bundle_id=bundle_id,
        operation=operation,
    )
    provider_cfg = _dict(registry_provider.get("provider"))
    authority_cfg = _dict(registry_provider.get("authority"))
    input_cfg = _dict(provider_cfg.get("input"))
    input_authenticator_ref = _dict(input_cfg.get("authenticator_ref"))
    issuer_cfg = _dict(provider_cfg.get("issuer"))

    provider = "telegram"
    authority_id = _str(registry_provider.get("authority_id"))
    provider_id = _str(registry_provider.get("provider_id"))
    integration_id = _str(input_authenticator_ref.get("integration_id") or input_authenticator_ref.get("provider_id"))
    ttl_seconds = _required_positive_int(issuer_cfg, "ttl_seconds")

    identity = await telegram_widget_auth.resolve_identity(
        entrypoint,
        request=request,
        telegram_init_data=telegram_init_data,
        integration_id=integration_id,
        allowed_roles=(),
        create_if_missing=True,
    )
    provider_subject = str(identity.telegram_user_id or "").strip()
    if not provider_subject:
        raise HTTPException(status_code=401, detail="Telegram user id is required")

    username = _str(identity.telegram_username) or f"telegram_{provider_subject}"
    name = _str(identity.user.get("first_name")) or _str(identity.user.get("last_name")) or username
    sub = f"{provider}:{provider_subject}"
    roles, permissions, role_binding_source = resolve_platform_grants(
        authority_cfg=authority_cfg,
        provider_cfg=provider_cfg,
        sub=sub,
        provider=provider,
        provider_subject=provider_subject,
    )
    grant = await get_bundle_session_authority().login_or_register(
        sub=sub,
        username=username,
        name=name,
        roles=roles,
        permissions=permissions,
        provider=provider,
        provider_subject=provider_subject,
        metadata={
            "issued_by_bundle_id": bundle_id,
            "source": f"{bundle_id}.{operation}",
            "authority_id": authority_id,
            "authority_provider_id": provider_id,
            "integration_id": integration_id,
            "telegram_user_id": provider_subject,
            "telegram_username": username,
            "role_binding_source": role_binding_source,
        },
        ttl_seconds=ttl_seconds,
    )

    log.info(
        "[bundle_session_login] issued authority=%s authority_provider=%s provider=%s provider_subject=%s sub=%s roles=%s role_binding=%s session_id=%s",
        authority_id,
        provider_id,
        provider,
        provider_subject,
        sub,
        roles,
        role_binding_source,
        grant.session_id,
    )
    return _session_response(
        issuer_cfg=issuer_cfg,
        authority_id=authority_id,
        provider_id=provider_id,
        provider=provider,
        provider_subject=provider_subject,
        sub=sub,
        grant=grant,
        roles=roles,
        permissions=permissions,
        role_binding_source=role_binding_source,
    )


async def issue_google_session(
    entrypoint: Any,
    *,
    request: Any = None,
    credential: str = "",
    id_token: str = "",
    payload: Mapping[str, Any] | None = None,
    bundle_id: str,
    operation: str = DEFAULT_GOOGLE_OPERATION,
):
    del request
    payload_map = _dict(payload)
    token = _str(credential or id_token or payload_map.get("credential") or payload_map.get("id_token"))
    registry_provider = await resolve_bundle_session_login_provider(
        entrypoint,
        bundle_id=bundle_id,
        operation=operation,
    )
    provider_cfg = _dict(registry_provider.get("provider"))
    authority_cfg = _dict(registry_provider.get("authority"))
    input_cfg = _dict(provider_cfg.get("input"))
    input_authenticator_ref = _dict(input_cfg.get("authenticator_ref"))
    issuer_cfg = _dict(provider_cfg.get("issuer"))
    upstream = await resolve_provider_ref(entrypoint, input_authenticator_ref)
    upstream_provider_cfg = _dict(upstream.get("provider"))
    authenticator_cfg = _dict(upstream_provider_cfg.get("authenticator"))
    client_id = await authenticator_client_id(authenticator_cfg)
    jwks_url = _str(authenticator_cfg.get("jwks_url")) or google_oidc.GOOGLE_JWKS_URL

    try:
        claims = await asyncio.to_thread(
            google_oidc.verify_google_id_token,
            token,
            client_id=client_id,
            jwks_url=jwks_url,
        )
    except google_oidc.GoogleTokenInvalid as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    provider = "google"
    provider_subject = _str(claims.get("sub"))
    email = _str(claims.get("email")).lower()
    if not provider_subject:
        raise HTTPException(status_code=401, detail="Google subject is required")

    authority_id = _str(registry_provider.get("authority_id"))
    provider_id = _str(registry_provider.get("provider_id"))
    ttl_seconds = _required_positive_int(issuer_cfg, "ttl_seconds")
    sub = f"{provider}:{provider_subject}"
    username = email or f"google_{provider_subject}"
    name = _str(claims.get("name")) or email or username
    roles, permissions, role_binding_source = resolve_platform_grants(
        authority_cfg=authority_cfg,
        provider_cfg=provider_cfg,
        sub=sub,
        provider=provider,
        provider_subject=provider_subject,
        verified_claims=claims,
    )

    grant = await get_bundle_session_authority().login_or_register(
        sub=sub,
        username=username,
        email=email,
        name=name,
        roles=roles,
        permissions=permissions,
        provider=provider,
        provider_subject=provider_subject,
        metadata={
            "issued_by_bundle_id": bundle_id,
            "source": f"{bundle_id}.{operation}",
            "authority_id": authority_id,
            "authority_provider_id": provider_id,
            "input_authority_id": _str(upstream.get("authority_id")),
            "input_authority_provider_id": _str(upstream.get("provider_id")),
            "google_sub": provider_subject,
            "google_email": email,
            "google_email_verified": bool(claims.get("email_verified")),
            "role_binding_source": role_binding_source,
        },
        ttl_seconds=ttl_seconds,
    )
    log.info(
        "[bundle_session_login] issued authority=%s authority_provider=%s provider=%s provider_subject=%s email=%s sub=%s roles=%s role_binding=%s session_id=%s",
        authority_id,
        provider_id,
        provider,
        provider_subject,
        email,
        sub,
        roles,
        role_binding_source,
        grant.session_id,
    )
    return _session_response(
        issuer_cfg=issuer_cfg,
        authority_id=authority_id,
        provider_id=provider_id,
        provider=provider,
        provider_subject=provider_subject,
        sub=sub,
        grant=grant,
        roles=roles,
        permissions=permissions,
        role_binding_source=role_binding_source,
    )


__all__ = [
    "DEFAULT_GOOGLE_OPERATION",
    "DEFAULT_TELEGRAM_OPERATION",
    "PROVIDER_TYPE",
    "authenticator_client_id",
    "google_login_client_config",
    "issue_google_session",
    "issue_telegram_session",
    "resolve_bundle_session_login_provider",
    "resolve_platform_grants",
    "resolve_provider_ref",
]
