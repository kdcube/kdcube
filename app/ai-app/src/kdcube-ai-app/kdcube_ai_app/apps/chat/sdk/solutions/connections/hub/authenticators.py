from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Mapping, Optional

from kdcube_ai_app.apps.chat.sdk.identity_authority import resolve_platform_authority
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import validate_telegram_init_data
from kdcube_ai_app.apps.chat.sdk.solutions.connections.authenticators.models import (
    AuthenticatedRequest,
    RequestEnvelope,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.authenticators.authority import (
    AuthRequestHints,
    select_authenticator_candidates,
)
from kdcube_ai_app.auth.AuthManager import REGISTERED_ROLE

from .identity_links import IdentityLinkStore, resolve_principal_roles

LOGGER = logging.getLogger("kdcube.connection_hub.authenticators")


def _str(value: Any) -> str:
    return str(value or "").strip()


def _first(values: list[Any]) -> str:
    for value in values:
        text = _str(value)
        if text:
            return text
    return ""


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return _str(value).lower() in {"1", "true", "yes", "on", "enabled"}


SUPPORTED_AUTHENTICATOR_PROVIDERS: list[dict[str, Any]] = [
    {
        "provider": "telegram",
        "label": "Telegram Bot / Mini App",
        "implemented": True,
        "secret_label": "Bot token",
        "subject_namespace": "telegram",
        "proofs": ["Telegram WebApp initData", "Telegram bot webhook secret"],
    },
    {
        "provider": "slack",
        "label": "Slack",
        "implemented": False,
        "secret_label": "Signing secret",
        "subject_namespace": "slack",
        "proofs": ["Slack request signature"],
    },
    {
        "provider": "oidc",
        "label": "OIDC",
        "implemented": False,
        "secret_label": "Client secret / JWKS config",
        "subject_namespace": "oidc",
        "proofs": ["ID token", "authorization-code callback"],
    },
    {
        "provider": "google",
        "label": "Google Identity",
        "implemented": False,
        "secret_label": "OAuth client secret",
        "subject_namespace": "google",
        "proofs": ["Google ID token"],
    },
    {
        "provider": "webhook",
        "label": "Webhook HMAC",
        "implemented": False,
        "secret_label": "HMAC secret",
        "subject_namespace": "webhook",
        "proofs": ["provider-specific HMAC headers"],
    },
    {
        "provider": "api-key",
        "label": "API Key",
        "implemented": False,
        "secret_label": "API key secret",
        "subject_namespace": "api-key",
        "proofs": ["header or query API key"],
    },
]


def supported_authenticator_providers() -> list[dict[str, Any]]:
    return [dict(row) for row in SUPPORTED_AUTHENTICATOR_PROVIDERS]


def _provider_supported(provider: str) -> bool:
    known = {_str(row.get("provider")).lower() for row in SUPPORTED_AUTHENTICATOR_PROVIDERS}
    return _str(provider).lower() in known


def _telegram_init_data(envelope: RequestEnvelope) -> str:
    body = envelope.json_body()
    return _first(
        [
            envelope.headers.get("x-telegram-init-data"),
            envelope.headers.get("telegram-init-data"),
            envelope.headers.get("x-kdcube-telegram-init-data"),
            envelope.query.get("telegram_init_data"),
            envelope.query.get("tgwebappdata"),
            body.get("telegram_init_data") if isinstance(body, Mapping) else "",
            body.get("initData") if isinstance(body, Mapping) else "",
        ]
    )


def auth_provider_hint(envelope: RequestEnvelope) -> str:
    body = envelope.json_body()
    return _first(
        [
            envelope.headers.get("x-kdcube-auth-provider"),
            envelope.headers.get("x-kdcube-auth-provider-id"),
            envelope.query.get("auth_provider"),
            envelope.query.get("kdcube_auth_provider"),
            body.get("auth_provider") if isinstance(body, Mapping) else "",
            body.get("authProvider") if isinstance(body, Mapping) else "",
        ]
    ).lower()


def auth_integration_id_hint(envelope: RequestEnvelope) -> str:
    body = envelope.json_body()
    return _first(
        [
            envelope.headers.get("x-kdcube-auth-integration-id"),
            envelope.headers.get("x-kdcube-integration-id"),
            envelope.query.get("auth_integration_id"),
            envelope.query.get("integration_id"),
            envelope.query.get("kdcube_auth_integration_id"),
            body.get("auth_integration_id") if isinstance(body, Mapping) else "",
            body.get("integration_id") if isinstance(body, Mapping) else "",
            body.get("authIntegrationId") if isinstance(body, Mapping) else "",
            body.get("integrationId") if isinstance(body, Mapping) else "",
        ]
    )


def auth_selector_hints(envelope: RequestEnvelope) -> AuthRequestHints:
    body = envelope.json_body()
    hints = AuthRequestHints.from_envelope(envelope)
    if not hints.provider:
        hints = AuthRequestHints(
            authority_id=hints.authority_id,
            authenticator_id=hints.authenticator_id,
            integration_id=hints.integration_id,
            connection_id=hints.connection_id,
            provider=auth_provider_hint(envelope),
        )
    if not hints.authority_id:
        hints = AuthRequestHints(
            authority_id=_first(
                [
                    envelope.headers.get("x-kdcube-auth-authority"),
                    envelope.query.get("auth_authority_id"),
                    envelope.query.get("kdcube_auth_authority_id"),
                    body.get("auth_authority_id") if isinstance(body, Mapping) else "",
                    body.get("authorityId") if isinstance(body, Mapping) else "",
                ]
            ),
            authenticator_id=hints.authenticator_id,
            integration_id=hints.integration_id,
            connection_id=hints.connection_id,
            provider=hints.provider,
        )
    return hints


def _telegram_display_name(user: Mapping[str, Any]) -> str:
    username = _str(user.get("username"))
    if username:
        return username
    return (
        " ".join(
            _str(user.get(key))
            for key in ("first_name", "last_name")
            if _str(user.get(key))
        )
        or _str(user.get("id"))
    )


def _entrypoint_bundle_id(entrypoint: Any, default: str = "connection-hub@1-0") -> str:
    spec = getattr(getattr(entrypoint, "config", None), "ai_bundle_spec", None)
    return _str(getattr(spec, "id", None) or getattr(spec, "bundle_id", None) or default)


_SECRET_DEFINITION_KEYS = {
    "bot_token",
    "webhook_secret",
    "client_secret",
    "secret",
    "token",
    "api_key",
    "private_key",
}


def _public_definition(definition: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in dict(definition or {}).items()
        if str(key or "").strip() not in _SECRET_DEFINITION_KEYS
    }


def _normalize_authenticator_row(row: Mapping[str, Any], *, provider: str = "", source: str = "config", index: int = 0) -> dict[str, Any]:
    provider_value = _str(row.get("provider") or provider).lower()
    if not provider_value:
        provider_value = "telegram"
    definition = dict(row.get("definition") or {}) if isinstance(row.get("definition"), Mapping) else {}
    authenticator_id = _str(row.get("authenticator_id") or row.get("id") or f"{provider_value}.{index + 1}")
    integration_id = _str(
        row.get("integration_id")
        or row.get("integrationId")
        or row.get("connection_id")
        or row.get("connectionId")
        or authenticator_id
    )
    authority_id = _str(
        row.get("authority_id")
        or row.get("authorityId")
        or row.get("authority")
        or row.get("issuer")
        or integration_id
    )
    secret_ref = _str(row.get("secret_ref") or row.get("secret"))
    if not secret_ref and provider_value == "telegram":
        secret_ref = "identity.telegram.bot_token"
    properties = dict(row.get("properties") or {}) if isinstance(row.get("properties"), Mapping) else {}
    public_definition = _public_definition(definition)
    if public_definition and "definition" not in properties:
        properties["definition"] = public_definition
    where = _str(row.get("where")) or _str(properties.get("where")) or "built-in"
    properties["where"] = where
    return {
        **dict(row),
        "authenticator_id": authenticator_id,
        "provider": provider_value,
        "authority_id": authority_id,
        "integration_id": integration_id,
        "connection_id": integration_id,
        "label": _str(row.get("label") or definition.get("label")) or authenticator_id,
        "where": where,
        "enabled": row.get("enabled") is not False,
        "role_providing": _bool(row.get("role_providing") if "role_providing" in row else row.get("roleProviding"), default=False),
        "subject_namespace": _str(row.get("subject_namespace") or row.get("namespace") or provider_value),
        "secret_ref": secret_ref,
        "selector": dict(row.get("selector") or {}) if isinstance(row.get("selector"), Mapping) else {},
        "verifier": dict(row.get("verifier") or {}) if isinstance(row.get("verifier"), Mapping) else {},
        "properties": properties,
        "source": _str(row.get("source") or source) or source,
        "implemented": provider_value == "telegram",
    }


def descriptor_authenticator_rows(identity_config: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Rows from descriptors, accepting both generic and legacy Telegram shapes."""

    out: list[dict[str, Any]] = []
    raw_generic = identity_config.get("authenticators")
    if isinstance(raw_generic, list):
        for idx, row in enumerate(raw_generic):
            if isinstance(row, Mapping):
                out.append(_normalize_authenticator_row(row, source="config", index=idx))

    telegram_cfg = dict(identity_config.get("telegram") or {}) if isinstance(identity_config.get("telegram"), Mapping) else {}
    raw_telegram = telegram_cfg.get("authenticators")
    if raw_telegram is None:
        raw_telegram = telegram_cfg.get("bots")
    if isinstance(raw_telegram, list):
        for idx, row in enumerate(telegram_authenticator_rows(telegram_cfg)):
            out.append(_normalize_authenticator_row(row, provider="telegram", source="config", index=idx))
    elif not out and telegram_cfg.get("enabled") is not False:
        out.append(
            _normalize_authenticator_row(
                {
                    "authenticator_id": "telegram.default",
                    "provider": "telegram",
                    "secret_ref": "identity.telegram.bot_token",
                    "enabled": True,
                },
                provider="telegram",
                source="config",
            )
        )

    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for row in out:
        key = _str(row.get("authenticator_id"))
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def merged_authenticator_rows(
    identity_config: Mapping[str, Any],
    *,
    stored_rows: Optional[list[Mapping[str, Any]]] = None,
) -> list[dict[str, Any]]:
    rows = descriptor_authenticator_rows(identity_config)
    by_id = {_str(row.get("authenticator_id")): row for row in rows if _str(row.get("authenticator_id"))}
    for idx, raw in enumerate(stored_rows or []):
        if not isinstance(raw, Mapping):
            continue
        row = _normalize_authenticator_row(raw, source="storage", index=idx)
        by_id[_str(row.get("authenticator_id"))] = row
    return sorted(by_id.values(), key=lambda row: (_str(row.get("provider")), _str(row.get("authenticator_id"))))


def authenticator_rows_for_provider(
    identity_config: Mapping[str, Any],
    provider: str,
    *,
    stored_rows: Optional[list[Mapping[str, Any]]] = None,
) -> list[dict[str, Any]]:
    provider_value = _str(provider).lower()
    rows = [
        row
        for row in merged_authenticator_rows(identity_config, stored_rows=stored_rows)
        if row.get("enabled") is not False and _str(row.get("provider")).lower() == provider_value
    ]
    return sorted(rows, key=lambda row: (not bool(row.get("role_providing")), _str(row.get("connection_id")), _str(row.get("authenticator_id"))))


def matching_authenticator_rows(
    identity_config: Mapping[str, Any],
    provider: str,
    *,
    authority_id: str = "",
    authenticator_id: str = "",
    connection_id: str = "",
    integration_id: str = "",
    stored_rows: Optional[list[Mapping[str, Any]]] = None,
) -> list[dict[str, Any]]:
    rows = authenticator_rows_for_provider(identity_config, provider, stored_rows=stored_rows)
    hints = AuthRequestHints(
        authority_id=_str(authority_id),
        authenticator_id=_str(authenticator_id),
        connection_id=_str(connection_id),
        integration_id=_str(integration_id) or _str(connection_id),
        provider=_str(provider).lower(),
    )
    return [dict(row) for row in select_authenticator_candidates(rows, hints)]


def telegram_authenticator_rows(cfg: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = cfg.get("authenticators")
    if raw is None:
        raw = cfg.get("bots")
    rows = [dict(row) for row in raw if isinstance(row, Mapping)] if isinstance(raw, list) else []
    if not rows:
        rows = [{"authenticator_id": "telegram.default", "secret_ref": "identity.telegram.bot_token"}]
    out: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        if row.get("enabled") is False:
            continue
        authenticator_id = _str(row.get("authenticator_id") or row.get("id") or f"telegram.{idx + 1}")
        secret_ref = _str(row.get("secret_ref") or row.get("secret") or "identity.telegram.bot_token")
        out.append({**row, "provider": "telegram", "authenticator_id": authenticator_id, "secret_ref": secret_ref})
    return out


async def _telegram_token_for_row(
    entrypoint: Any,
    *,
    row: Mapping[str, Any],
    secret_resolver: Optional[Callable[..., Awaitable[str]]] = None,
    telegram_bot_token_resolver: Optional[Callable[..., Awaitable[str]]] = None,
) -> str:
    secret_ref = _str(row.get("secret_ref"))
    if secret_resolver is not None:
        return _str(
            await secret_resolver(
                secret_ref=secret_ref or "identity.telegram.bot_token",
                authenticator_id=_str(row.get("authenticator_id")),
                provider="telegram",
            )
        )
    if telegram_bot_token_resolver is not None and (not secret_ref or secret_ref == "identity.telegram.bot_token"):
        return await telegram_bot_token_resolver(trace_scope=f"request_authenticate.{row.get('authenticator_id') or 'telegram'}")
    return ""


def _row_web_app_auth_max_age(row: Mapping[str, Any], cfg: Mapping[str, Any]) -> int:
    props = row.get("properties") if isinstance(row.get("properties"), Mapping) else {}
    definition = props.get("definition") if isinstance(props.get("definition"), Mapping) else {}
    value = (
        definition.get("web_app_auth_max_age_seconds")
        if isinstance(definition, Mapping)
        else None
    )
    if value is None:
        value = props.get("web_app_auth_max_age_seconds")
    if value is None:
        value = cfg.get("web_app_auth_max_age_seconds")
    try:
        return int(value or 86400)
    except Exception:
        return 86400


async def authenticate_request(
    entrypoint: Any,
    *,
    request_envelope: RequestEnvelope | Mapping[str, Any],
    identity_store: IdentityLinkStore,
    identity_config: Mapping[str, Any],
    stored_authenticators: Optional[list[Mapping[str, Any]]] = None,
    secret_resolver: Optional[Callable[..., Awaitable[str]]] = None,
    telegram_bot_token_resolver: Optional[Callable[..., Awaitable[str]]] = None,
) -> dict[str, Any]:
    """Authenticate one request through configured Connection Hub providers.

    Today this implements Telegram Mini App/WebApp proof. The selector shape is
    provider-neutral: later Slack/webhook/API-key authenticators can add their
    own proof extraction without callers changing code.
    """

    envelope = RequestEnvelope.coerce(request_envelope)
    hints = auth_selector_hints(envelope)
    provider_hint = hints.provider
    authority_id = hints.authority_id
    authenticator_id = hints.authenticator_id
    integration_id = hints.integration_id or hints.connection_id
    init_data = _telegram_init_data(envelope)
    trace = bool(provider_hint or authority_id or authenticator_id or integration_id or init_data)
    if trace:
        LOGGER.info(
            "[connection-hub.request_authenticate] start method=%s path=%s provider_hint=%s authority_id=%s authenticator_id=%s integration_id=%s has_telegram_init_data=%s",
            envelope.method,
            envelope.path,
            provider_hint or "",
            authority_id or "",
            authenticator_id or "",
            integration_id or "",
            bool(init_data),
        )
    if provider_hint and provider_hint != "telegram":
        LOGGER.info(
            "[connection-hub.request_authenticate] declined provider=%s integration_id=%s error=no_authenticator_accepted",
            provider_hint,
            integration_id or "",
        )
        return AuthenticatedRequest(
            ok=False,
            authenticated=False,
            provider=provider_hint,
            integration_id=integration_id,
            connection_id=integration_id,
            error="no_authenticator_accepted",
        ).to_dict()

    if not init_data:
        if trace:
            LOGGER.info(
                "[connection-hub.request_authenticate] declined provider=%s integration_id=%s error=no_authenticator_accepted",
                provider_hint or "",
                integration_id or "",
            )
        return AuthenticatedRequest(
            ok=False,
            authenticated=False,
            integration_id=integration_id,
            connection_id=integration_id,
            error="no_authenticator_accepted",
        ).to_dict()

    cfg = dict((identity_config or {}).get("telegram") or {})
    if cfg.get("enabled") is False:
        LOGGER.info(
            "[connection-hub.request_authenticate] declined provider=telegram integration_id=%s error=telegram_authenticator_disabled",
            integration_id or "",
        )
        return AuthenticatedRequest(
            ok=False,
            authenticated=False,
            provider="telegram",
            integration_id=integration_id,
            connection_id=integration_id,
            error="telegram_authenticator_disabled",
        ).to_dict()

    verified = None
    selected_authenticator = ""
    selected_integration_id = integration_id
    selected_authority_id = authority_id
    last_error = ""
    rows = matching_authenticator_rows(
        identity_config,
        "telegram",
        authority_id=authority_id,
        authenticator_id=authenticator_id,
        connection_id=integration_id,
        integration_id=integration_id,
        stored_rows=stored_authenticators,
    )
    if trace:
        LOGGER.info(
            "[connection-hub.request_authenticate] candidate_rows provider=telegram authority_id=%s authenticator_id=%s integration_id=%s count=%d",
            authority_id or "",
            authenticator_id or "",
            integration_id or "",
            len(rows),
        )
    if (authority_id or authenticator_id or integration_id) and not rows:
        LOGGER.info(
            "[connection-hub.request_authenticate] declined provider=telegram authority_id=%s authenticator_id=%s integration_id=%s error=authenticator_not_configured",
            authority_id or "",
            authenticator_id or "",
            integration_id,
        )
        return AuthenticatedRequest(
            ok=False,
            authenticated=False,
            provider="telegram",
            authority_id=authority_id,
            integration_id=integration_id,
            connection_id=integration_id,
            selected_authenticator=authenticator_id,
            error="authenticator_not_configured",
            message="No enabled Telegram authenticator is configured for the supplied selector hints.",
        ).to_dict()

    for row in rows:
        row_id = _str(row.get("authenticator_id"))
        token = await _telegram_token_for_row(
            entrypoint,
            row=row,
            secret_resolver=secret_resolver,
            telegram_bot_token_resolver=telegram_bot_token_resolver,
        )
        if not token:
            LOGGER.info(
                "[connection-hub.request_authenticate] row_skipped provider=telegram authenticator=%s integration_id=%s error=telegram_bot_token_not_configured",
                row_id,
                _str(row.get("integration_id") or row.get("connection_id")),
            )
            last_error = "telegram_bot_token_not_configured"
            continue
        try:
            verified = validate_telegram_init_data(
                init_data,
                bot_token=token,
                max_age_seconds=_row_web_app_auth_max_age(row, cfg),
            )
            selected_authenticator = _str(row.get("authenticator_id"))
            selected_authority_id = _str(row.get("authority_id")) or selected_authority_id
            selected_integration_id = _str(row.get("integration_id") or row.get("connection_id")) or selected_authenticator
            break
        except Exception as exc:
            LOGGER.info(
                "[connection-hub.request_authenticate] row_rejected provider=telegram authenticator=%s integration_id=%s error=%s",
                row_id,
                _str(row.get("integration_id") or row.get("connection_id")),
                exc,
            )
            last_error = str(exc)
            verified = None

    if verified is None:
        code = "telegram_bot_token_not_configured" if last_error == "telegram_bot_token_not_configured" else "telegram_init_data_invalid"
        LOGGER.info(
            "[connection-hub.request_authenticate] declined provider=telegram integration_id=%s selected_authenticator=%s error=%s message=%s",
            integration_id or selected_integration_id or "",
            selected_authenticator or "",
            code,
            last_error,
        )
        return AuthenticatedRequest(
            ok=False,
            authenticated=False,
            provider="telegram",
            integration_id=integration_id,
            connection_id=integration_id,
            error=code,
            message=last_error,
        ).to_dict()

    user = verified.user
    telegram_user_id = _str(user.get("id"))
    if not telegram_user_id:
        LOGGER.info(
            "[connection-hub.request_authenticate] declined provider=telegram integration_id=%s selected_authenticator=%s error=telegram_subject_missing",
            selected_integration_id or "",
            selected_authenticator or "",
        )
        return AuthenticatedRequest(
            ok=False,
            authenticated=False,
            provider="telegram",
            integration_id=selected_integration_id,
            connection_id=selected_integration_id,
            error="telegram_subject_missing",
        ).to_dict()

    actor_user_id = f"telegram_{telegram_user_id}"
    link = identity_store.resolve_link(provider="telegram", provider_subject=telegram_user_id) or {}
    platform_user_id = _str(link.get("platform_user_id"))
    principal = (
        resolve_principal_roles(platform_user_id=platform_user_id, identity_config=identity_config)
        if platform_user_id
        else {
            "platform_user_id": "",
            "roles": [REGISTERED_ROLE],
            "permissions": [],
            "role_resolution": {"status": "identity_not_linked", "source": "connection_hub"},
        }
    )
    authority = await resolve_platform_authority(
        entrypoint,
        actor_user_id=actor_user_id,
        platform_user_id=platform_user_id,
        default_user_type="registered",
        provider="telegram",
        provider_subject=telegram_user_id,
        source="connection_hub.request_auth",
    )
    if not authority.get("platform_roles"):
        authority["platform_roles"] = list(principal.get("roles") or [REGISTERED_ROLE])
    if not authority.get("platform_permissions"):
        authority["platform_permissions"] = list(principal.get("permissions") or [])

    LOGGER.info(
        "[connection-hub.request_authenticate] accepted provider=telegram integration_id=%s selected_authenticator=%s actor_user_id=%s platform_user_present=%s linked=%s principal_roles=%s authority_user_type=%s",
        selected_integration_id or selected_authenticator or "telegram.default",
        selected_authenticator or "telegram.default",
        actor_user_id,
        bool(platform_user_id),
        bool(platform_user_id),
        list(principal.get("roles") or []),
        _str(authority.get("economics_user_type") or authority.get("platform_user_type") or authority.get("user_type")),
    )
    return AuthenticatedRequest(
        ok=True,
        authenticated=True,
        linked=bool(platform_user_id),
            provider="telegram",
            authority_id=selected_authority_id or authority_id,
            provider_subject=telegram_user_id,
            identity_subject=telegram_user_id,
            selected_authenticator=selected_authenticator or "telegram.default",
        integration_id=selected_integration_id or selected_authenticator or "telegram.default",
        connection_id=selected_integration_id or selected_authenticator or "telegram.default",
        actor_user_id=actor_user_id,
        platform_user_id=platform_user_id,
        identity_link=dict(link),
        principal=dict(principal),
        identity_authority=authority,
        message=f"Telegram identity verified for {_telegram_display_name(user)}.",
    ).to_dict()


__all__ = [
    "authenticate_request",
    "auth_integration_id_hint",
    "auth_provider_hint",
    "auth_selector_hints",
    "authenticator_rows_for_provider",
    "descriptor_authenticator_rows",
    "matching_authenticator_rows",
    "merged_authenticator_rows",
    "supported_authenticator_providers",
    "telegram_authenticator_rows",
]
