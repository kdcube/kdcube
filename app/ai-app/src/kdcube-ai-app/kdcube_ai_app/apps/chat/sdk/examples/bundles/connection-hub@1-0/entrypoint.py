from __future__ import annotations

import logging
import json
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional
from urllib.parse import urlencode, urlsplit, urlunsplit

from fastapi.responses import JSONResponse

from kdcube_ai_app.apps.chat.sdk.solutions.connections.federated_tokens.data_bus import issue_federated_data_bus_token
from kdcube_ai_app.apps.chat.emitters import ChatRelayCommunicator
from kdcube_ai_app.apps.chat.sdk.integrations.connections import settings as connections_settings
from kdcube_ai_app.apps.chat.sdk.integrations.email import settings as email_settings
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import (
    extract_telegram_init_data_from_request,
    validate_telegram_init_data,
)
from kdcube_ai_app.apps.chat.sdk.config import get_secret
from kdcube_ai_app.apps.chat.sdk.solutions.connections.authenticators.models import RequestEnvelope
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_memory import BaseEntrypointWithMemory
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceRegistry,
    RedisNamedServiceDiscovery,
    dispatch_named_service_api_request,
)
from kdcube_ai_app.infra.plugin.bundle_loader import api, bundle_entrypoint, bundle_id, ui_widget
from kdcube_ai_app.infra.service_hub.inventory import Config

from kdcube_ai_app.apps.chat.sdk.solutions.connections.hub.authenticator_store import AuthenticatorStore
from kdcube_ai_app.apps.chat.sdk.solutions.connections.hub.authenticators import (
    authenticate_request as authenticate_request_with_authenticators,
    descriptor_authenticator_rows,
    merged_authenticator_rows,
    matching_authenticator_rows,
    supported_authenticator_providers,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.hub.edges import (
    ConnectionEdgeStore,
    edge_actor,
    edge_target,
    resolve_principal_roles,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.hub.provider_impl import ConnectionHubProvider
from kdcube_ai_app.apps.chat.sdk.solutions.connections.hub.resolver import (
    resolve_delegated_identity_scope,
    resolve_identity_family,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.config import (
    DEFAULT_CLAUDE_REDIRECT_URIS,
    DEFAULT_DCR_REDIRECT_URIS,
    oauth_delegated_config,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.metadata import (
    authorization_server_metadata,
    protected_resource_metadata,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.http.routes import (
    authorize as oauth_authorize,
    authorize_consent as oauth_authorize_consent,
    oauth_logout,
    register_client as oauth_register_client,
    token as oauth_token,
)

BUNDLE_ID = "connection-hub@1-0"
ENTRYPOINT_NAME = "connection-hub"
LOGGER = logging.getLogger("kdcube.playground.connection_hub")


def _api_visibility(alias: str) -> Dict[str, str]:
    return {
        "user_types_config": f"surfaces.as_provider.api.operations.{alias}.visibility.user_types",
        "roles_config": f"surfaces.as_provider.api.operations.{alias}.visibility.roles",
    }


def _widget_visibility(alias: str) -> Dict[str, str]:
    return {
        "user_types_config": f"surfaces.as_provider.widget.{alias}.visibility.user_types",
        "roles_config": f"surfaces.as_provider.widget.{alias}.visibility.roles",
    }


def _payload(data: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    if isinstance(data, Mapping):
        nested = data.get("data")
        if isinstance(nested, Mapping):
            merged.update({str(k): v for k, v in nested.items()})
        else:
            merged.update({str(k): v for k, v in data.items()})
    for key, value in kwargs.items():
        if key not in {"request", "alias", "route", "endpoint_alias"} and value is not None:
            merged[key] = value
    return merged


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _safe_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part for part in (item.strip() for item in value.replace(",", " ").split()) if part]
    if isinstance(value, (list, tuple, set)):
        return [part for part in (str(item or "").strip() for item in value) if part]
    return []


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


AUTHENTICATOR_SECRET_VALUE_KEYS = {
    "secret_value",
    "secret",
    "token",
    "bot_token",
    "webhook_secret",
    "client_secret",
    "signing_secret",
    "api_key",
}


def _contains_authenticator_secret_value(payload: Mapping[str, Any]) -> bool:
    for key, value in payload.items():
        if str(key).strip().lower() in AUTHENTICATOR_SECRET_VALUE_KEYS and str(value or "").strip():
            return True
        if isinstance(value, Mapping) and _contains_authenticator_secret_value(value):
            return True
    return False


def _storage_root_or_error(entrypoint: Any) -> Any:
    root = entrypoint.bundle_storage_root()
    if root is None:
        raise RuntimeError("Bundle storage root is unavailable")
    return root


def _target_user_id(
    entrypoint: Any,
    *,
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> str:
    comm = getattr(entrypoint, "comm", None)
    return (
        str(user_id or "").strip()
        or str(fingerprint or "").strip()
        or str(getattr(comm, "user_id", None) or "").strip()
        or str(getattr(comm, "fingerprint", None) or "").strip()
        or "anonymous"
    )


def _entrypoint_identity_authority(entrypoint: Any) -> Dict[str, Any]:
    context_user = getattr(getattr(entrypoint, "comm_context", None), "user", None)
    raw = getattr(context_user, "identity_authority", None)
    if isinstance(raw, Mapping):
        return dict(raw)

    comm = getattr(entrypoint, "comm", None)
    raw = getattr(comm, "identity_authority", None)
    if isinstance(raw, Mapping):
        return dict(raw)

    service = getattr(comm, "service", None)
    if isinstance(service, Mapping):
        user_obj = service.get("user_obj")
        if isinstance(user_obj, Mapping):
            raw = user_obj.get("identity_authority")
            if isinstance(raw, Mapping):
                return dict(raw)
    return {}


def _platform_user_id(
    entrypoint: Any,
    *,
    user_id: Optional[str] = None,
) -> str:
    """Return a real authenticated platform user id, never an anonymous fingerprint.

    External/channel actors keep their actor user id (for example
    ``telegram_434804821``) even when platform authority is projected onto the
    session. In those cases the platform id must come from the explicit
    ``identity_authority.platform_user_id`` edge, not from legacy ``user_type``.
    """
    authority = _entrypoint_identity_authority(entrypoint)
    projected_platform_user = str(authority.get("platform_user_id") or "").strip()
    if projected_platform_user:
        return projected_platform_user

    actor_user_id = str(authority.get("actor_user_id") or "").strip()
    authority_id = str(
        authority.get("authority_id")
        or authority.get("issuer_authority_id")
        or authority.get("authority")
        or ""
    ).strip()
    external_authority = bool(authority_id and authority_id != "platform") or bool(actor_user_id)
    if external_authority:
        return ""

    comm = getattr(entrypoint, "comm", None)
    comm_user_id = str(getattr(comm, "user_id", None) or "").strip()
    explicit_user_id = str(user_id or "").strip()
    candidate = comm_user_id or explicit_user_id
    if candidate and candidate != "anonymous" and not candidate.startswith("telegram_"):
        return candidate
    if comm is not None:
        return ""
    if explicit_user_id and explicit_user_id != "anonymous" and not explicit_user_id.startswith("telegram_"):
        return explicit_user_id
    return ""


def _entrypoint_user_roles_permissions(entrypoint: Any) -> tuple[list[str], list[str]]:
    roles: list[str] = []
    permissions: list[str] = []

    context_user = getattr(getattr(entrypoint, "comm_context", None), "user", None)
    roles.extend(_safe_list(getattr(context_user, "roles", None)))
    permissions.extend(_safe_list(getattr(context_user, "permissions", None)))

    comm = getattr(entrypoint, "comm", None)
    roles.extend(_safe_list(getattr(comm, "roles", None)))
    permissions.extend(_safe_list(getattr(comm, "permissions", None)))

    service = getattr(comm, "service", None)
    if isinstance(service, Mapping):
        user_obj = service.get("user_obj")
        if isinstance(user_obj, Mapping):
            roles.extend(_safe_list(user_obj.get("roles")))
            permissions.extend(_safe_list(user_obj.get("permissions")))

    authority = _entrypoint_identity_authority(entrypoint)
    roles.extend(_safe_list(authority.get("platform_roles") or authority.get("roles")))
    permissions.extend(_safe_list(authority.get("platform_permissions") or authority.get("permissions")))
    return _dedupe(roles), _dedupe(permissions)


def _platform_delegation_grant_options(entrypoint: Any, platform_user_id: str) -> list[dict[str, Any]]:
    roles, permissions = _entrypoint_user_roles_permissions(entrypoint)
    principal = resolve_principal_roles(
        platform_user_id=platform_user_id,
        identity_config=_identity_config(entrypoint),
    )
    roles = _dedupe(roles + _safe_list(principal.get("roles")))
    permissions = _dedupe(permissions + _safe_list(principal.get("permissions")))

    options: list[dict[str, Any]] = [
        {
            "grant": "identity:family",
            "kind": "identity",
            "label": "Use linked KDCube identities",
            "description": (
                "Allow this Telegram account to read product data, such as Memories, "
                "across identities linked to this KDCube account."
            ),
            "default": True,
        },
        {
            "grant": "economics:platform-user",
            "kind": "economics",
            "label": "Use this KDCube account for usage limits",
            "description": (
                "Allow runtime accounting and paid-service limits to be evaluated "
                "against this KDCube account while Telegram remains the actor."
            ),
            "default": True,
        },
    ]
    for role in roles:
        options.append(
            {
                "grant": role,
                "kind": "platform_role",
                "label": role.replace("kdcube:role:", "KDCube role: "),
                "description": "Allow Telegram to derive this platform role when a boundary requires platform authority.",
                "default": False,
            }
        )
    for permission in permissions:
        options.append(
            {
                "grant": permission,
                "kind": "platform_permission",
                "label": permission,
                "description": "Allow Telegram to derive this platform permission when a boundary requires platform authority.",
                "default": False,
            }
        )
    return options


def _edge_store(entrypoint: Any) -> ConnectionEdgeStore:
    return ConnectionEdgeStore(_storage_root_or_error(entrypoint))


def _authenticator_store(entrypoint: Any) -> AuthenticatorStore:
    tenant, project = _runtime_tenant_project(entrypoint)
    return AuthenticatorStore(
        pg_pool=getattr(entrypoint, "pg_pool", None),
        tenant=tenant,
        project=project,
        bundle_id=_entrypoint_bundle_id(entrypoint),
    )


def _identity_config(entrypoint: Any) -> Dict[str, Any]:
    props = getattr(entrypoint, "bundle_props", None)
    if isinstance(props, Mapping):
        raw = props.get("identity")
        return dict(raw) if isinstance(raw, Mapping) else {}
    return {}


def _connections_config(entrypoint: Any) -> Dict[str, Any]:
    props = getattr(entrypoint, "bundle_props", None)
    if isinstance(props, Mapping):
        raw = props.get("connections")
        return dict(raw) if isinstance(raw, Mapping) else {}
    return {}


def _oauth_adapter_config(entrypoint: Any, request: Any) -> Dict[str, Any]:
    connections = _connections_config(entrypoint)
    delegated = connections.get("delegated_credentials")
    delegated_node = delegated if isinstance(delegated, Mapping) else {}
    raw = delegated_node.get("oauth")
    cfg: Dict[str, Any] = dict(raw) if isinstance(raw, Mapping) else {}
    tenant, project = _runtime_tenant_project(entrypoint)
    cfg["tenant"] = tenant
    cfg["project"] = project
    cfg.setdefault("enabled", False)
    cfg.setdefault("brand", "KDCube")
    cfg.setdefault(
        "public_clients",
        [
            {
                "client_id": "claude",
                "redirect_uris": list(DEFAULT_CLAUDE_REDIRECT_URIS),
            }
        ],
    )
    cfg.setdefault(
        "dynamic_client_registration",
        {"allowed_redirect_uris": list(DEFAULT_DCR_REDIRECT_URIS)},
    )
    public_base = _oauth_public_base_url(request)
    cfg["issuer"] = str(cfg.get("issuer") or public_base).rstrip("/")
    return cfg


def _oauth_public_base_url(request: Any) -> str:
    if request is None:
        return ""
    path = str(getattr(getattr(request, "url", None), "path", "") or "")
    marker = "/public/oauth"
    if marker in path:
        public_path = path.split(marker, 1)[0] + marker
    else:
        public_path = path.rstrip("/")
    try:
        headers = getattr(request, "headers", {}) or {}
        forwarded = str(headers.get("forwarded") or "").split(",", 1)[0].strip()
        forwarded_parts: Dict[str, str] = {}
        for item in forwarded.split(";"):
            if "=" not in item:
                continue
            key, value = item.split("=", 1)
            key = key.strip().lower()
            value = value.strip().strip('"')
            if key and value:
                forwarded_parts[key] = value
        raw_proto = (
            forwarded_parts.get("proto")
            or str(headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip()
            or str(getattr(getattr(request, "url", None), "scheme", "") or "").strip()
            or "http"
        )
        host = (
            forwarded_parts.get("host")
            or str(headers.get("x-forwarded-host") or headers.get("host") or "").split(",", 1)[0].strip()
            or str(getattr(getattr(request, "url", None), "netloc", "") or "").strip()
        )
        if host:
            host_name = host.split(":", 1)[0].strip().lower()
            proto = raw_proto
            if (
                raw_proto == "http"
                and host_name
                and host_name != "localhost"
                and not host_name.startswith("127.")
                and host_name != "::1"
                and not host_name.endswith(".local")
                and "." in host_name
            ):
                proto = "https"
            return f"{proto}://{host}{public_path}".rstrip("/")
    except Exception:
        pass
    return f"{str(request.base_url).rstrip('/')}{public_path}".rstrip("/")


def _bind_delegated_client_request_config(entrypoint: Any, request: Any) -> Dict[str, Any]:
    cfg = _oauth_adapter_config(entrypoint, request)
    if request is not None:
        request.state.oauth_delegated_config = cfg
        request.state.oauth_delegated_issuer = str(cfg.get("issuer") or "").rstrip("/")
    return cfg


def _delegated_client_capability_payload(request: Any, *, resource: str | None = None) -> list[dict[str, Any]]:
    cfg = oauth_delegated_config(request)
    caps = cfg.capability_map()
    tool_catalog = cfg.resource_tool_catalog(resource)
    out: list[dict[str, Any]] = []
    for grant in cfg.supported_scopes(resource):
        cap = caps.get(grant)
        if cap is None:
            out.append(
                {
                    "grant": grant,
                    "label": grant,
                    "description": "",
                    "tools": [
                        {
                            "name": tool.name,
                            "label": tool.label,
                            "description": tool.description,
                            "grants": list(tool.grants),
                        }
                        for tool in tool_catalog
                        if grant in tool.grants
                    ],
                }
            )
            continue
        out.append(
            {
                "grant": cap.grant,
                "label": cap.label,
                "description": cap.description,
                "tools": [
                    {
                        "name": tool.name,
                        "label": tool.label,
                        "description": tool.description,
                        "grants": list(tool.grants),
                    }
                    for tool in tool_catalog
                    if cap.grant in tool.grants
                ],
            }
        )
    return out


def _identity_link_flow_config(entrypoint: Any, provider: str) -> Dict[str, Any]:
    raw_flows = _identity_config(entrypoint).get("link_flows")
    flows = dict(raw_flows) if isinstance(raw_flows, Mapping) else {}
    raw = flows.get(provider)
    return dict(raw) if isinstance(raw, Mapping) else {}


def _telegram_link_flow_config(entrypoint: Any) -> Dict[str, Any]:
    return _identity_link_flow_config(entrypoint, "telegram")


def _authenticator_selector_cache_config(entrypoint: Any) -> Dict[str, Any]:
    identity = _identity_config(entrypoint)
    raw = identity.get("authenticator_selector_cache")
    if raw is None:
        raw = identity.get("selector_cache")
    cfg = dict(raw or {}) if isinstance(raw, Mapping) else {}
    try:
        ttl_seconds = int(cfg.get("ttl_seconds") or 30)
    except Exception:
        ttl_seconds = 30
    return {
        "enabled": _bool(cfg.get("enabled"), default=True),
        "ttl_seconds": max(1, ttl_seconds),
    }


def _authenticator_selector_cache_key(entrypoint: Any) -> str:
    tenant, project = _runtime_tenant_project(entrypoint)
    return f"kdcube:connection-hub:{tenant}:{project}:authenticators:v1"


async def _invalidate_authenticator_selector_cache(entrypoint: Any) -> None:
    redis = getattr(entrypoint, "redis", None)
    if redis is None:
        return
    try:
        await redis.delete(_authenticator_selector_cache_key(entrypoint))
    except Exception:
        LOGGER.warning("[connection-hub.authenticators] failed to invalidate selector cache", exc_info=True)


async def _cached_authenticator_rows(entrypoint: Any) -> list[dict[str, Any]]:
    cfg = _authenticator_selector_cache_config(entrypoint)
    redis = getattr(entrypoint, "redis", None)
    if redis is None or not cfg["enabled"]:
        return await _authenticator_store(entrypoint).list_rows()

    key = _authenticator_selector_cache_key(entrypoint)
    try:
        cached = await redis.get(key)
        if cached:
            if isinstance(cached, (bytes, bytearray)):
                cached = cached.decode("utf-8")
            rows = json.loads(str(cached))
            if isinstance(rows, list):
                return [dict(row) for row in rows if isinstance(row, Mapping)]
    except Exception:
        LOGGER.warning("[connection-hub.authenticators] failed to read selector cache", exc_info=True)

    rows = await _authenticator_store(entrypoint).list_rows()
    try:
        await redis.setex(
            key,
            int(cfg["ttl_seconds"]),
            json.dumps(rows, ensure_ascii=False, default=str),
        )
    except Exception:
        LOGGER.warning("[connection-hub.authenticators] failed to write selector cache", exc_info=True)
    return rows


async def _bootstrap_descriptor_authenticators(entrypoint: Any) -> int:
    rows = descriptor_authenticator_rows(_identity_config(entrypoint))
    if not rows:
        return 0
    store = _authenticator_store(entrypoint)
    count = 0
    for row in rows:
        await store.upsert_row(
            authenticator_id=str(row.get("authenticator_id") or row.get("id") or "").strip(),
            provider=str(row.get("provider") or "").strip(),
            authority_id=str(row.get("authority_id") or "").strip(),
            label=str(row.get("label") or "").strip(),
            enabled=row.get("enabled") is not False,
            connection_id=str(
                row.get("integration_id")
                or row.get("integrationId")
                or row.get("connection_id")
                or row.get("authenticator_id")
                or row.get("id")
                or ""
            ).strip(),
            role_providing=bool(row.get("role_providing")),
            subject_namespace=str(row.get("subject_namespace") or "").strip(),
            secret_ref=str(row.get("secret_ref") or "").strip(),
            selector=dict(row.get("selector") or {}) if isinstance(row.get("selector"), Mapping) else {},
            verifier=dict(row.get("verifier") or {}) if isinstance(row.get("verifier"), Mapping) else {},
            properties=dict(row.get("properties") or {}) if isinstance(row.get("properties"), Mapping) else {},
        )
        count += 1
    await _invalidate_authenticator_selector_cache(entrypoint)
    return count


def _request_origin(request: Any) -> str:
    if request is None:
        return ""
    try:
        headers = request.headers
        host = str(headers.get("x-forwarded-host") or headers.get("host") or "").strip()
        proto = str(headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip()
        if host:
            return f"{proto or 'https'}://{host}"
    except Exception:
        pass
    try:
        url = request.url
        return f"{url.scheme}://{url.netloc}"
    except Exception:
        return ""


def _append_query(url: str, params: Mapping[str, str]) -> str:
    parts = urlsplit(url)
    existing = parts.query
    extra = urlencode({k: v for k, v in params.items() if v})
    query = f"{existing}&{extra}" if existing and extra else existing or extra
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _runtime_tenant_project(entrypoint: Any) -> tuple[str, str]:
    ident = entrypoint.runtime_identity()
    tenant = str(ident.get("tenant") or "default").strip()
    project = str(ident.get("project") or "default").strip()
    return tenant, project


def _entrypoint_bundle_id(entrypoint: Any, default: str = BUNDLE_ID) -> str:
    spec = getattr(getattr(entrypoint, "config", None), "ai_bundle_spec", None)
    return str(
        getattr(spec, "id", None)
        or getattr(spec, "bundle_id", None)
        or default
        or ""
    ).strip()


def _platform_claim_url(
    entrypoint: Any,
    *,
    request: Any,
    challenge_id: str,
    request_origin: str = "",
) -> str:
    cfg = _telegram_link_flow_config(entrypoint)
    template = str(cfg.get("platform_claim_url") or "").strip()
    if template:
        return template.replace("{challenge_id}", challenge_id)
    origin = str(request_origin or "").strip() or _request_origin(request)
    if not origin:
        return ""
    tenant, project = _runtime_tenant_project(entrypoint)
    url = (
        f"{origin}/api/integrations/bundles/{tenant}/{project}/"
        f"{BUNDLE_ID}/public/widgets/connections_settings"
    )
    return _append_query(url, {"claim_challenge": challenge_id})


async def _bundle_secret_value(
    entrypoint: Any,
    *,
    secret_path: str,
    trace_scope: str = "",
    warn_missing: bool = True,
) -> str:
    resolved_bundle_id = _entrypoint_bundle_id(entrypoint)
    descriptor_key = f"bundles.{resolved_bundle_id}.secrets.{secret_path}"
    token = str(await get_secret(f"b:{secret_path}", bundle_id=resolved_bundle_id) or "").strip()
    if token:
        LOGGER.info(
            "[connection-hub.secrets] secret resolved scope=%s descriptor_key=%s bundle_id=%s",
            trace_scope,
            descriptor_key,
            resolved_bundle_id,
        )
        return token
    if warn_missing:
        LOGGER.warning(
            "[connection-hub.secrets] secret not configured scope=%s descriptor_key=%s bundle_id=%s",
            trace_scope,
            descriptor_key,
            resolved_bundle_id,
        )
    return ""


async def _authenticator_secret_value(
    entrypoint: Any,
    *,
    secret_ref: str,
    authenticator_id: str = "",
    provider: str = "",
    trace_scope: str = "",
    warn_missing: bool = True,
) -> str:
    del provider
    del authenticator_id
    secret_path = str(secret_ref or "").strip()
    if not secret_path:
        return ""
    return await _bundle_secret_value(
        entrypoint,
        secret_path=secret_path,
        trace_scope=trace_scope,
        warn_missing=warn_missing,
    )


async def _authenticator_secret_configured(
    entrypoint: Any,
    *,
    secret_ref: str,
    authenticator_id: str = "",
    trace_scope: str,
) -> bool:
    secret_path = str(secret_ref or "").strip()
    if not secret_path:
        return False
    token = await _authenticator_secret_value(
        entrypoint,
        secret_ref=secret_path,
        authenticator_id=authenticator_id,
        trace_scope=trace_scope,
        warn_missing=False,
    )
    return bool(token)


async def _validate_telegram_init_data_any(
    entrypoint: Any,
    *,
    init_data: str,
    authority_id: str = "",
    authenticator_id: str = "",
    trace_scope: str,
) -> tuple[Any, str, str, str, str]:
    last_error = ""
    rows = matching_authenticator_rows(
        _identity_config(entrypoint),
        "telegram",
        authority_id=authority_id,
        authenticator_id=authenticator_id,
        stored_rows=await _cached_authenticator_rows(entrypoint),
    )
    if (authority_id or authenticator_id) and not rows:
        selector = authenticator_id or authority_id
        return None, "", "", "", f"authenticator_not_configured:{selector}"
    for row in rows:
        auth_id = str(row.get("authenticator_id") or "telegram").strip()
        token = await _authenticator_secret_value(
            entrypoint,
            secret_ref=str(row.get("secret_ref") or "").strip(),
            authenticator_id=auth_id,
            provider="telegram",
            trace_scope=f"{trace_scope}.{auth_id}",
            warn_missing=False,
        )
        if not token:
            last_error = "telegram_bot_token_not_configured"
            continue
        try:
            props = row.get("properties") if isinstance(row.get("properties"), Mapping) else {}
            definition = props.get("definition") if isinstance(props.get("definition"), Mapping) else {}
            raw_max_age = (
                definition.get("web_app_auth_max_age_seconds")
                if isinstance(definition, Mapping)
                else None
            )
            if raw_max_age is None:
                raw_max_age = props.get("web_app_auth_max_age_seconds")
            verified = validate_telegram_init_data(
                init_data,
                bot_token=token,
                max_age_seconds=int(raw_max_age or 86400),
            )
            return (
                verified,
                auth_id,
                str(row.get("connection_id") or auth_id).strip(),
                str(row.get("authority_id") or "").strip(),
                "",
            )
        except Exception as exc:
            last_error = str(exc)
    return None, "", "", "", last_error


def _auth_selector_hints_from_request_payload(request: Any, payload: Mapping[str, Any]) -> Dict[str, str]:
    def _direct(*keys: str) -> str:
        for key in keys:
            value = payload.get(key)
            if str(value or "").strip():
                return str(value).strip()
        return ""

    hints: Dict[str, str] = {
        "authority_id": _direct("auth_authority_id", "authority_id", "authAuthorityId", "authorityId"),
        "authenticator_id": _direct("authenticator_id", "authAuthenticatorId", "authenticatorId"),
        "provider": _direct("auth_provider", "provider", "authProvider"),
    }
    headers = getattr(request, "headers", {}) or {}
    header_keys = {
        "authority_id": ("x-kdcube-auth-authority-id", "x-kdcube-auth-authority"),
        "authenticator_id": ("x-kdcube-auth-authenticator-id",),
        "provider": ("x-kdcube-auth-provider", "x-kdcube-auth-provider-id"),
    }
    for target, keys in header_keys.items():
        if hints[target]:
            continue
        for key in keys:
            try:
                value = headers.get(key)
            except Exception:
                value = None
            if str(value or "").strip():
                hints[target] = str(value).strip()
                break
    query = getattr(request, "query_params", {}) or {}
    query_keys = {
        "authority_id": ("auth_authority_id", "authority_id", "kdcube_auth_authority_id"),
        "authenticator_id": ("authenticator_id", "auth_authenticator_id", "kdcube_auth_authenticator_id"),
        "provider": ("auth_provider", "provider", "kdcube_auth_provider"),
    }
    for target, keys in query_keys.items():
        if hints[target]:
            continue
        for key in keys:
            try:
                value = query.get(key)
            except Exception:
                value = None
            if str(value or "").strip():
                hints[target] = str(value).strip()
                break
    return hints


async def _authenticate_request_context(
    entrypoint: Any,
    *,
    request: Any = None,
    payload: Optional[Mapping[str, Any]] = None,
    trace_scope: str = "request",
) -> Dict[str, Any]:
    if request is not None:
        envelope = await RequestEnvelope.from_request(request, include_body=False)
        envelope_data = envelope.to_dict()
        if payload:
            envelope_data["body_text"] = json.dumps(dict(payload), ensure_ascii=False)
    elif isinstance(payload, Mapping) and isinstance(payload.get("request"), Mapping):
        envelope_data = dict(payload.get("request") or {})
    else:
        envelope_data = dict(payload or {})
    result = await authenticate_request_with_authenticators(
        entrypoint,
        request_envelope=envelope_data,
        edge_store=_edge_store(entrypoint),
        identity_config=_identity_config(entrypoint),
        stored_authenticators=await _cached_authenticator_rows(entrypoint),
        secret_resolver=lambda **kw: _authenticator_secret_value(
            entrypoint,
            trace_scope=f"{trace_scope}.{kw.get('authenticator_id') or kw.get('provider') or 'authenticator'}",
            **kw,
        ),
    )
    return dict(result or {})


def _challenge_live_event(challenge: Mapping[str, Any] | None) -> Dict[str, Any]:
    metadata = challenge.get("metadata") if isinstance(challenge, Mapping) else {}
    if not isinstance(metadata, Mapping):
        return {}
    live_event = metadata.get("live_event")
    return dict(live_event) if isinstance(live_event, Mapping) else {}


def _edge_provider(edge: Mapping[str, Any] | None, challenge: Mapping[str, Any] | None = None) -> str:
    source = edge_actor(edge or {})
    return str(source.get("provider") or (challenge or {}).get("provider") or "").strip()


def _edge_subject(edge: Mapping[str, Any] | None, challenge: Mapping[str, Any] | None = None) -> str:
    source = edge_actor(edge or {})
    return str(source.get("subject") or (challenge or {}).get("provider_subject") or "").strip()


def _edge_platform_user_id(edge: Mapping[str, Any] | None) -> str:
    target = edge_target(edge or {})
    return str(target.get("user_id") or "").strip()


async def _emit_connection_edge_changed(
    entrypoint: Any,
    *,
    challenge: Mapping[str, Any] | None,
    edge: Mapping[str, Any] | None,
    action: str,
) -> None:
    live_event = _challenge_live_event(challenge)
    session_id = str(live_event.get("session_id") or "").strip()
    if not session_id:
        LOGGER.info(
            "[connection-hub.identity] no live requestor to notify action=%s challenge_id=%s",
            action,
            (challenge or {}).get("challenge_id") if isinstance(challenge, Mapping) else "",
        )
        return
    tenant, project = _runtime_tenant_project(entrypoint)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    challenge_id = str((challenge or {}).get("challenge_id") or "")
    provider = _edge_provider(edge, challenge)
    provider_subject = _edge_subject(edge, challenge)
    payload = {
        "type": "connection_hub.edge.changed",
        "timestamp": now,
        "service": {
            "request_id": f"connection-edge-{challenge_id}",
            "tenant": tenant,
            "project": project,
            "user": _edge_platform_user_id(edge),
            "bundle_id": BUNDLE_ID,
        },
        "conversation": {
            "session_id": session_id,
            "conversation_id": session_id,
            "turn_id": "",
        },
        "event": {
            "agent": "connection-hub",
            "title": "Connection Edge Changed",
            "status": "completed",
            "step": "connection.edge",
        },
        "data": {
            "action": action,
            "provider": provider,
            "provider_subject": provider_subject,
            "linked": action != "unlinked",
            "challenge_id": challenge_id,
            "edge": dict(edge or {}),
        },
        "route": "chat_service",
    }
    relay = ChatRelayCommunicator()
    try:
        LOGGER.info(
            "[connection-hub.identity] emitting live notification action=%s session_id=%s challenge_id=%s provider=%s provider_subject=%s",
            action,
            session_id,
            challenge_id,
            provider,
            provider_subject,
        )
        await relay.emit(
            event="chat_service",
            data=payload,
            tenant=tenant,
            project=project,
            session_id=session_id,
        )
        LOGGER.info(
            "[connection-hub.identity] notified live requestor action=%s session_id=%s challenge_id=%s",
            action,
            session_id,
            challenge_id,
        )
    except Exception:
        LOGGER.exception(
            "[connection-hub.identity] failed to notify live requestor action=%s session_id=%s challenge_id=%s",
            action,
            session_id,
            challenge_id,
        )


# Bind the bundle-owned policy hooks once (mirrors task-and-memo-app wiring).
connections_settings.configure_connections(
    storage_root_or_error=_storage_root_or_error,
    target_user_id=_target_user_id,
    bundle_id=BUNDLE_ID,
)
email_settings.configure_email_settings(
    storage_root_or_error=_storage_root_or_error,
    target_user_id=_target_user_id,
    resolve_identity=None,
    bundle_id=BUNDLE_ID,
)


@bundle_entrypoint(
    name=ENTRYPOINT_NAME,
    version="0.1.0",
    priority=20,
    allowed_roles_config="surfaces.as_provider.bundle.visibility.allowed_roles",
)
@bundle_id(id=BUNDLE_ID)
class ConnectionHubEntrypoint(BaseEntrypointWithMemory):
    """Connections hub: exposes the public `connections` named-service contract.

    Serves the whole contract over HTTP via the `named_service` alias, owns the
    public OAuth callback redirect route, and provides thin widget helper ops.
    """

    BUNDLE_ID = BUNDLE_ID

    def __init__(
        self,
        config: Config,
        pg_pool: Any = None,
        redis: Any = None,
        comm_context: Any = None,
    ) -> None:
        super().__init__(config=config, pg_pool=pg_pool, redis=redis, comm_context=comm_context)
        self._named_service_registry: Optional[NamedServiceRegistry] = None

    # ── named-service registry ───────────────────────────────────────────────

    def _named_services(self) -> NamedServiceRegistry:
        if self._named_service_registry is None:
            registry = NamedServiceRegistry()
            registry.register(ConnectionHubProvider(entrypoint=self, bundle_id=BUNDLE_ID))
            self._named_service_registry = registry
        return self._named_service_registry

    def named_services(self) -> NamedServiceRegistry:
        return self._named_services()

    async def on_bundle_load(self, **kwargs: Any) -> None:
        await super().on_bundle_load(**kwargs)
        pg_pool = self.pg_pool or kwargs.get("pg_pool")
        if pg_pool is not None:
            self.pg_pool = pg_pool
            try:
                await _authenticator_store(self).ensure_schema()
                bootstrapped = await _bootstrap_descriptor_authenticators(self)
                LOGGER.info(
                    "[connection-hub] on_bundle_load: bootstrapped request authenticators count=%s",
                    bootstrapped,
                )
            except Exception:
                LOGGER.exception("[connection-hub] on_bundle_load: failed to ensure/bootstrap authenticator schema")
        else:
            LOGGER.warning("[connection-hub] on_bundle_load: no pg_pool; request authenticator metadata store unavailable")
        redis = self.redis or kwargs.get("redis")
        if redis is None:
            LOGGER.info("[connection-hub] on_bundle_load: no redis; skipping discovery registration")
            return
        ident = self.runtime_identity()
        try:
            discovery = RedisNamedServiceDiscovery(
                redis,
                tenant=ident.get("tenant") or "default",
                project=ident.get("project") or "default",
            )
            entries = await discovery.register_registry(
                self._named_services(),
                bundle_id=BUNDLE_ID,
                transport="bundle_registry",
                registry_method="named_services",
            )
            LOGGER.info(
                "[connection-hub] registered named-service providers tenant=%s project=%s providers=%s",
                ident.get("tenant"),
                ident.get("project"),
                [entry.spec.provider_id for entry in entries],
            )
        except Exception:
            LOGGER.exception("[connection-hub] on_bundle_load: failed to register named-service providers")

    def configuration_defaults(self) -> Dict[str, Any]:
        return {
            "surfaces": {
                "as_provider": {
                    "api": {
                        "operations": {
                            "named_service": {"visibility": {"user_types": []}},
                            "connections_catalog": {"visibility": {"user_types": []}},
                            "connections_start_oauth": {"visibility": {"user_types": []}},
                            "connections_disconnect": {"visibility": {"user_types": []}},
                            "connection_edges_list": {"visibility": {"user_types": []}},
                            "connection_edge_upsert": {"visibility": {"user_types": []}},
                            "connection_edge_remove": {"visibility": {"user_types": []}},
                            "connection_edge_challenge_create": {"visibility": {"user_types": []}},
                            "connection_edge_challenge_claim": {"visibility": {"user_types": []}},
                            "connection_edge_challenge_status": {"visibility": {"user_types": []}},
                            "identity_family_resolve": {"visibility": {"user_types": []}},
                            "delegated_identity_scope_resolve": {"visibility": {"user_types": []}},
                            "identity_resolve": {"visibility": {"user_types": []}},
                            "authenticators_list": {"visibility": {"user_types": []}},
                            "authenticators_upsert": {"visibility": {"user_types": []}},
                            "authenticators_remove": {"visibility": {"user_types": []}},
                            "request_authenticate": {"visibility": {"user_types": []}},
                            "email_accounts_status": {"visibility": {"user_types": []}},
                            "email_connect_app_password": {"visibility": {"user_types": []}},
                            "email_disconnect_account": {"visibility": {"user_types": []}},
                            "connections_settings": {"visibility": {"user_types": []}},
                            "oauth": {"visibility": {"user_types": []}},
                        },
                    },
                    "widget": {
                        "connections_settings": {"visibility": {"user_types": []}},
                    },
                },
            },
            # No provider is hardcoded here. The set of available providers is
            # DYNAMIC — driven by the connection registry (any registered
            # `ConnectionProvider` = OAuth mechanics, no credentials).
            #
            # The middle level — CLIENT APPS (the OAuth application clients that
            # carry credentials) — is populated by deploy config, MANY per
            # provider:
            #   connections.providers.<provider>.apps: [
            #     {app_id, label, client_id, scopes, enabled}, ...
            #   ]
            # with secrets supplied separately (never in config/metadata):
            #   secret: connections.providers.<provider>.apps.<app_id>.client_secret
            #   secret: connections.oauth_state_secret            (hub-level, signs state)
            #
            # A user account is connected THROUGH one client app and records its
            # `app_id`. The OAuth callback is a single hub-level route shared by
            # all providers/apps.
            "connections": {
                "oauth": {
                    # Public base for the shared connection_oauth_callback route.
                    # Empty → derived from the request host at runtime.
                    "public_base_url": "",
                },
                "delegated_credentials": {
                    "oauth": {
                        "enabled": False,
                        "brand": "KDCube",
                        "issuer": "",
                        "public_clients": [
                            {
                                "client_id": "claude",
                                "redirect_uris": list(DEFAULT_CLAUDE_REDIRECT_URIS),
                            },
                        ],
                        "dynamic_client_registration": {
                            "allowed_redirect_uris": list(DEFAULT_DCR_REDIRECT_URIS),
                        },
                        "capabilities": [
                            {
                                "grant": "conversations:read",
                                "label": "Read conversations",
                                "description": "Read conversation transcripts that the grantor is allowed to delegate.",
                                "tools": [
                                    {
                                        "name": "conversations_export",
                                        "label": "Export conversation transcripts",
                                        "description": "Read-only conversation transcript export.",
                                    },
                                ],
                                "delegable_roles": ["kdcube:role:super-admin"],
                                "delegable_permissions": ["kdcube:*:conversations:*;read"],
                            },
                            {
                                "grant": "memories:read",
                                "label": "Read memories",
                                "description": "Read memory notes visible to the KDCube user who approves the connection.",
                                "delegable_roles": [
                                    "kdcube:role:chat-user",
                                    "kdcube:role:paid",
                                    "kdcube:role:privileged",
                                    "kdcube:role:super-admin",
                                ],
                                "delegable_permissions": ["memories:read"],
                            },
                            {
                                "grant": "knowledge:read",
                                "label": "Read KDCube knowledge",
                                "description": "Read KDCube knowledge notes through delegated MCP tools.",
                                "delegable_roles": [
                                    "kdcube:role:chat-user",
                                    "kdcube:role:paid",
                                    "kdcube:role:privileged",
                                    "kdcube:role:super-admin",
                                ],
                                "delegable_permissions": ["knowledge:read"],
                            },
                        ],
                        "resources": [
                            {
                                "resource": "*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/conversations*",
                                "label": "KDCube conversations MCP",
                                "tools": {
                                    "conversations_export": {
                                        "label": "Export conversations",
                                        "description": "Read conversation transcripts for feedback triage.",
                                        "grants": ["conversations:read"],
                                    },
                                },
                            },
                            {
                                "resource": "*/api/integrations/bundles/*/*/user-memories@2026-06-26/public/mcp/memories*",
                                "label": "User memories MCP",
                                "tools": {
                                    "memory_search": {
                                        "label": "Search memories",
                                        "description": "Search the user's memory notes.",
                                        "grants": ["memories:read"],
                                    },
                                    "memory_get": {
                                        "label": "Read memory",
                                        "description": "Read one memory note by id.",
                                        "grants": ["memories:read"],
                                    },
                                },
                            },
                            {
                                "resource": "*/api/integrations/bundles/*/*/knowledge@1-0/public/mcp/knowledge_managed*",
                                "label": "KDCube knowledge MCP",
                                "tools": {
                                    "get_access_context": {
                                        "label": "Get access context",
                                        "description": "Inspect the current knowledge access context.",
                                        "grants": ["knowledge:read"],
                                    },
                                    "about": {
                                        "label": "About KDCube knowledge",
                                        "description": "Explain when and how to use the KDCube knowledge server.",
                                        "grants": ["knowledge:read"],
                                    },
                                    "search": {
                                        "label": "Search knowledge",
                                        "description": "Search KDCube knowledge visible to this delegated connection.",
                                        "grants": ["knowledge:read"],
                                    },
                                    "read_refs": {
                                        "label": "Read references",
                                        "description": "Read exact knowledge or source references returned by search.",
                                        "grants": ["knowledge:read"],
                                    },
                                },
                            },
                        ],
                    },
                },
                # Deploy config populates this per provider (see comment above).
                "providers": {},
            },
            "identity": {
                "enabled": True,
                # Development fixture only. The target architecture is:
                # verified external identity -> Connection Hub edge -> platform
                # principal/role resolver. Connection Hub should not decide
                # final roles itself.
                "role_resolver": {
                    "mode": "platform",
                },
                "role_bindings": {},
                "authenticators": [
                    {
                        "id": "telegram.kdcube_ref",
                        "provider": "telegram",
                        "authority_id": "telegram.kdcube_ref",
                        "where": "built-in",
                        "definition": {
                            "label": "KDCube Ref Telegram bot",
                            "web_app_auth_max_age_seconds": 86400,
                        },
                        "secret_ref": "identity.authenticators.telegram_kdcube_ref.bot_token",
                        "enabled": True,
                    }
                ],
                "link_flows": {
                    "telegram": {
                        "enabled": True,
                        "challenge_ttl_seconds": 600,
                        # Optional URL used by Telegram-first flows after
                        # Telegram proof is created. Empty -> derived as
                        # public/widgets/connections_settings.
                        # Supported replacement: {challenge_id}.
                        "platform_claim_url": "",
                    },
                },
            },
            "integrations": {
                "email": {
                    "enabled": False,
                    "google": {
                        "client_id": "",
                        "scopes": [
                            "openid",
                            "email",
                            "profile",
                            "https://www.googleapis.com/auth/gmail.readonly",
                        ],
                    },
                    "oauth": {
                        "public_base_url": "",
                        "redirect_uri": "",
                    },
                },
            },
            "ui": {
                "widgets": {
                    "connections_settings": {
                        "enabled": True,
                        "src_folder": "ui/widgets/connections",
                        "build_command": "npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build",
                    },
                },
            },
        }

    # ── named-service over HTTP (serves the whole contract) ──────────────────

    @api(method="POST", alias="named_service", route="operations", **_api_visibility("named_service"))
    async def named_service(self, data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Any:
        payload = _payload(data, **kwargs)
        LOGGER.info(
            "[connection-hub] named_service request: namespace=%s operation=%s provider=%s",
            payload.get("namespace") or "",
            payload.get("operation") or "",
            (payload.get("payload") or {}).get("provider") if isinstance(payload.get("payload"), Mapping) else payload.get("provider"),
        )
        return await dispatch_named_service_api_request(self._named_services(), payload)

    # ── public OAuth callback (browser redirect) ─────────────────────────────

    @api(method="GET", alias="connection_oauth_callback", route="public")
    async def connection_oauth_callback(
        self,
        request: Any = None,
        code: str = "",
        state: str = "",
        error: str = "",
        **kwargs: Any,
    ):
        del kwargs
        return await connections_settings.callback(
            self, request=request, code=code, state=state, error=error
        )

    # ── delegated credential OAuth adapter ─────────────────────────────────

    @api(method="GET", alias="oauth", route="public")
    async def oauth_get(
        self,
        request: Any = None,
        path_tail: str = "",
        **kwargs: Any,
    ):
        cfg = _bind_delegated_client_request_config(self, request)
        if not _bool(cfg.get("enabled"), default=False):
            LOGGER.warning("[connection-hub.oauth] rejected disabled GET path=%s", path_tail)
            return JSONResponse(
                status_code=404,
                content={"error": "delegated_credentials_oauth_disabled"},
            )

        path = str(path_tail or "").strip("/")
        public_base = _oauth_public_base_url(request)
        issuer = str(cfg.get("issuer") or public_base).rstrip("/")
        LOGGER.info("[connection-hub.oauth] GET path=%s issuer=%s", path or ".", issuer)

        if path in {
            "",
            ".well-known/oauth-authorization-server",
            ".well-known/openid-configuration",
            "metadata",
            "authorization-server",
        }:
            parsed_cfg = oauth_delegated_config(request)
            return JSONResponse(
                authorization_server_metadata(
                    issuer,
                    authorization_endpoint=f"{public_base}/authorize",
                    token_endpoint=f"{public_base}/token",
                    registration_endpoint=f"{public_base}/register",
                    scopes_supported=parsed_cfg.supported_scopes(),
                )
            )
        if path in {".well-known/oauth-protected-resource", "protected-resource"}:
            resource = ""
            if request is not None:
                resource = str(request.query_params.get("resource") or "").strip()
            if not resource:
                resource = str(kwargs.get("resource") or "").strip()
            parsed_cfg = oauth_delegated_config(request)
            return JSONResponse(
                protected_resource_metadata(
                    issuer,
                    resource=resource or None,
                    scopes_supported=parsed_cfg.supported_scopes(resource),
                    capabilities=_delegated_client_capability_payload(request, resource=resource or None),
                    tools=[
                        {
                            "name": tool.name,
                            "label": tool.label,
                            "description": tool.description,
                            "grants": list(tool.grants),
                        }
                        for tool in parsed_cfg.resource_tool_catalog(resource)
                    ],
                )
            )
        if path == "authorize":
            return await oauth_authorize(request)

        return JSONResponse(status_code=404, content={"error": "oauth_route_not_found", "path": path})

    @api(method="POST", alias="oauth", route="public")
    async def oauth_post(
        self,
        request: Any = None,
        path_tail: str = "",
        **kwargs: Any,
    ):
        del kwargs
        cfg = _bind_delegated_client_request_config(self, request)
        if not _bool(cfg.get("enabled"), default=False):
            LOGGER.warning("[connection-hub.oauth] rejected disabled POST path=%s", path_tail)
            return JSONResponse(
                status_code=404,
                content={"error": "delegated_credentials_oauth_disabled"},
            )

        path = str(path_tail or "").strip("/")
        LOGGER.info(
            "[connection-hub.oauth] POST path=%s issuer=%s",
            path or ".",
            str(cfg.get("issuer") or "").rstrip("/"),
        )
        if path == "register":
            return await oauth_register_client(request)
        if path == "authorize/consent":
            return await oauth_authorize_consent(request)
        if path == "logout":
            return await oauth_logout(request)
        if path == "token":
            return await oauth_token(request)

        return JSONResponse(status_code=404, content={"error": "oauth_route_not_found", "path": path})

    # ── thin widget helper ops (Settings UI) ─────────────────────────────────

    @api(method="GET", alias="connections_catalog", route="operations", **_api_visibility("connections_catalog"))
    async def connections_catalog(
        self,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        del kwargs
        return await connections_settings.catalog(self, user_id=user_id, fingerprint=fingerprint)

    @api(method="POST", alias="connections_start_oauth", route="operations", **_api_visibility("connections_start_oauth"))
    async def connections_start_oauth(
        self,
        request: Any = None,
        provider: str = "",
        app_id: str = "",
        scopes: Any = None,
        return_hint: str = "",
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        del kwargs
        # `scopes` (optional): per-connect subset of the client app's configured
        # scopes. Accept a list or a space/comma-separated string.
        scope_list: Optional[list[str]] = None
        if isinstance(scopes, (list, tuple)):
            scope_list = [str(s).strip() for s in scopes if str(s).strip()]
        elif isinstance(scopes, str) and scopes.strip():
            scope_list = [s.strip() for s in scopes.replace(",", " ").split() if s.strip()]
        return await connections_settings.start_oauth(
            self,
            request=request,
            provider=provider,
            app_id=app_id or None,
            scopes=scope_list,
            return_hint=return_hint,
            user_id=user_id,
            fingerprint=fingerprint,
        )

    @api(method="POST", alias="connections_disconnect", route="operations", **_api_visibility("connections_disconnect"))
    async def connections_disconnect(
        self,
        provider: str = "",
        account_id: str = "",
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        del kwargs
        return await connections_settings.disconnect(
            self,
            provider=provider,
            account_id=account_id,
            user_id=user_id,
            fingerprint=fingerprint,
        )

    # ── connection edges (external identity -> delegated platform principal) ─

    @api(method="GET", alias="connection_edges_list", route="operations", **_api_visibility("connection_edges_list"))
    async def connection_edges_list(
        self,
        platform_user_id: str = "",
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        del kwargs
        current_user = _platform_user_id(self, user_id=user_id)
        if not current_user or current_user == "anonymous":
            return {"ok": False, "error": "connection_edges_require_authenticated_user"}
        requested_user = str(platform_user_id or current_user).strip()
        if not requested_user or requested_user == "anonymous":
            return {"ok": False, "error": "connection_edges_require_authenticated_user"}
        if requested_user != current_user:
            return {"ok": False, "error": "connection_edges_cross_user_access_denied"}
        return {
            "ok": True,
            "platform_user_id": requested_user,
            "edges": _edge_store(self).list_edges(target_user_id=requested_user),
        }

    @api(method="POST", alias="connection_edge_upsert", route="operations", **_api_visibility("connection_edge_upsert"))
    async def connection_edge_upsert(
        self,
        data: Optional[Dict[str, Any]] = None,
        provider: str = "",
        provider_subject: str = "",
        subject: str = "",
        platform_user_id: str = "",
        label: str = "",
        metadata: Optional[Mapping[str, Any]] = None,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        payload = _payload(
            data,
            provider=provider,
            provider_subject=provider_subject or subject,
            platform_user_id=platform_user_id,
            label=label,
            metadata=metadata,
            **kwargs,
        )
        current_user = _platform_user_id(self, user_id=user_id)
        if not current_user or current_user == "anonymous":
            return {"ok": False, "error": "connection_edge_requires_authenticated_user"}
        requested_user = str(payload.get("platform_user_id") or current_user).strip()
        if not requested_user or requested_user == "anonymous":
            return {"ok": False, "error": "connection_edge_requires_authenticated_user"}
        if requested_user != current_user:
            return {"ok": False, "error": "connection_edge_requires_admin_or_trusted_context"}
        try:
            row = _edge_store(self).upsert_edge(
                from_provider=str(payload.get("provider") or ""),
                from_subject=str(payload.get("provider_subject") or payload.get("subject") or ""),
                to_user_id=requested_user,
                label=str(payload.get("label") or ""),
                created_by=current_user,
                grants=payload.get("grants") if isinstance(payload.get("grants"), (list, tuple)) else None,
                metadata=payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else None,
            )
        except ValueError as exc:
            return {"ok": False, "error": "invalid_connection_edge", "message": str(exc)}
        principal = resolve_principal_roles(
            platform_user_id=requested_user,
            identity_config=_identity_config(self),
        )
        return {"ok": True, "edge": row, "principal": principal}

    @api(method="POST", alias="connection_edge_remove", route="operations", **_api_visibility("connection_edge_remove"))
    async def connection_edge_remove(
        self,
        data: Optional[Dict[str, Any]] = None,
        provider: str = "",
        provider_subject: str = "",
        subject: str = "",
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        payload = _payload(
            data,
            provider=provider,
            provider_subject=provider_subject or subject,
            **kwargs,
        )
        current_user = _platform_user_id(self, user_id=user_id)
        if not current_user or current_user == "anonymous":
            return {"ok": False, "error": "connection_edge_requires_authenticated_user"}
        return _edge_store(self).remove_edge(
            from_provider=str(payload.get("provider") or ""),
            from_subject=str(payload.get("provider_subject") or payload.get("subject") or ""),
            target_user_id=current_user,
        )

    @api(method="POST", alias="connection_edge_challenge_create", route="operations", **_api_visibility("connection_edge_challenge_create"))
    async def connection_edge_challenge_create(
        self,
        data: Optional[Dict[str, Any]] = None,
        request: Any = None,
        provider: str = "telegram",
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        payload = _payload(data, provider=provider, **kwargs)
        current_user = _platform_user_id(self, user_id=user_id)
        if not current_user or current_user == "anonymous":
            comm = getattr(self, "comm", None)
            LOGGER.info(
                "[connection-hub.identity] challenge_create rejected: authenticated platform user required comm_user_type=%s user_id_arg_present=%s",
                getattr(comm, "user_type", None),
                bool(str(user_id or "").strip()),
            )
            return {"ok": False, "error": "connection_edge_challenge_requires_authenticated_user"}
        provider_value = str(payload.get("provider") or "telegram").strip()
        if provider_value != "telegram":
            return {"ok": False, "error": "connection_edge_challenge_provider_not_supported", "provider": provider_value}
        cfg = _telegram_link_flow_config(self)
        ttl_seconds = int(cfg.get("challenge_ttl_seconds") or 600)
        try:
            challenge = _edge_store(self).create_edge_challenge(
                provider=provider_value,
                target_user_id=current_user,
                created_by=current_user,
                ttl_seconds=ttl_seconds,
                metadata={"source": "connection_hub.widget"},
            )
        except ValueError as exc:
            return {"ok": False, "error": "invalid_connection_edge_challenge", "message": str(exc)}
        return {
            "ok": True,
            "challenge": challenge,
            "message": (
                "Challenge created. Open the provider-specific host surface to "
                "prove the external identity. The platform user stays server-side "
                "on this challenge."
            ),
        }

    @api(method="POST", alias="connection_edge_challenge_claim", route="operations", **_api_visibility("connection_edge_challenge_claim"))
    async def connection_edge_challenge_claim(
        self,
        data: Optional[Dict[str, Any]] = None,
        challenge_id: str = "",
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        payload = _payload(data, challenge_id=challenge_id, **kwargs)
        confirmed = bool(payload.get("confirmed") or payload.get("confirm"))
        if not confirmed:
            return {
                "ok": False,
                "error": "connection_edge_challenge_confirmation_required",
                "message": "Confirm this connection before claiming the challenge.",
            }
        current_user = _platform_user_id(self, user_id=user_id)
        if not current_user or current_user == "anonymous":
            comm = getattr(self, "comm", None)
            LOGGER.info(
                "[connection-hub.identity] challenge_claim rejected: authenticated platform user required challenge_id=%s comm_user_type=%s user_id_arg_present=%s",
                str(payload.get("challenge_id") or ""),
                getattr(comm, "user_type", None),
                bool(str(user_id or "").strip()),
            )
            return {"ok": False, "error": "connection_edge_challenge_claim_requires_authenticated_user"}
        selected_grants = _dedupe(_safe_list(payload.get("grants")))
        grant_options = _platform_delegation_grant_options(self, current_user)
        allowed_grants = {str(item.get("grant") or "").strip() for item in grant_options if str(item.get("grant") or "").strip()}
        invalid_grants = sorted(set(selected_grants) - allowed_grants)
        if invalid_grants:
            return {
                "ok": False,
                "error": "connection_edge_challenge_invalid_grants",
                "message": "Some selected delegation grants are not available to the signed-in KDCube user.",
                "invalid_grants": invalid_grants,
                "delegation_options": grant_options,
            }
        if not selected_grants:
            return {
                "ok": False,
                "error": "connection_edge_challenge_requires_delegation_grants",
                "message": "Select at least one delegated capability for this connection.",
                "delegation_options": grant_options,
            }
        try:
            result = _edge_store(self).claim_provider_challenge(
                challenge_id=str(payload.get("challenge_id") or ""),
                target_user_id=current_user,
                claimed_by=current_user,
                grants=selected_grants,
            )
        except ValueError as exc:
            return {"ok": False, "error": "invalid_connection_edge_challenge_claim", "message": str(exc)}
        if not result.get("ok"):
            return result
        edge = result.get("edge") if isinstance(result.get("edge"), Mapping) else {}
        principal = resolve_principal_roles(
            platform_user_id=_edge_platform_user_id(edge) or current_user,
            identity_config=_identity_config(self),
        )
        challenge = result.get("challenge") if isinstance(result.get("challenge"), Mapping) else {}
        live_event = _challenge_live_event(challenge)
        LOGGER.info(
            "[connection-hub.identity] challenge_claim linked challenge_id=%s provider=%s provider_subject=%s platform_user_id=%s live_event_session=%s",
            challenge.get("challenge_id"),
            _edge_provider(edge, challenge),
            _edge_subject(edge, challenge),
            _edge_platform_user_id(edge) or current_user,
            live_event.get("session_id") or "",
        )
        await _emit_connection_edge_changed(
            self,
            challenge=challenge,
            edge=edge,
            action="linked",
        )
        return {
            "ok": True,
            "challenge": result.get("challenge"),
            "edge": edge,
            "principal": principal,
            "delegation_options": grant_options,
            "message": "Telegram identity proof connected to the authenticated platform user.",
        }

    @api(method="POST", alias="connection_edge_challenge_status", route="operations", **_api_visibility("connection_edge_challenge_status"))
    async def connection_edge_challenge_status(
        self,
        data: Optional[Dict[str, Any]] = None,
        challenge_id: str = "",
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        payload = _payload(data, challenge_id=challenge_id, **kwargs)
        current_user = _platform_user_id(self, user_id=user_id)
        if not current_user or current_user == "anonymous":
            return {"ok": False, "error": "connection_edge_challenge_requires_authenticated_user"}
        challenge = _edge_store(self).get_edge_challenge(challenge_id=str(payload.get("challenge_id") or ""))
        if not challenge:
            return {"ok": False, "error": "connection_edge_challenge_not_found"}
        challenge_user = str(challenge.get("target_user_id") or "").strip()
        if challenge_user and challenge_user != current_user:
            return {"ok": False, "error": "connection_edge_challenge_cross_user_access_denied"}
        response: Dict[str, Any] = {
            "ok": True,
            "challenge": challenge,
            "platform_user_id": current_user,
            "delegation_options": _platform_delegation_grant_options(self, current_user),
            "claimable_by_current_user": (
                not challenge_user
                and str(challenge.get("status") or "") == "pending_target_claim"
            ),
        }
        provider_subject = str(challenge.get("provider_subject") or "").strip()
        if provider_subject:
            edge = _edge_store(self).resolve_edge(
                from_provider=str(challenge.get("provider") or ""),
                from_subject=provider_subject,
            )
            if edge:
                response["edge"] = edge
        return response

    @api(method="POST", alias="federated_data_bus_claim", route="public")
    async def federated_data_bus_claim(
        self,
        data: Optional[Dict[str, Any]] = None,
        request: Any = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        payload = _payload(data, **kwargs)
        auth = await _authenticate_request_context(
            self,
            request=request,
            payload=payload,
            trace_scope="connection_hub_federated_data_bus_claim",
        )
        if not auth.get("ok") or not auth.get("authenticated"):
            LOGGER.warning(
                "[connection-hub.data_bus] claim rejected provider=%s authority_id=%s authenticator=%s error=%s",
                auth.get("provider") or "",
                auth.get("authority_id") or "",
                auth.get("selected_authenticator") or "",
                auth.get("error") or "not_authenticated",
            )
            return {
                "ok": False,
                "error": auth.get("error") or "request_not_authenticated",
                "message": auth.get("message") or "Connection Hub could not authenticate this request.",
            }

        tenant, project = _runtime_tenant_project(self)
        authority = auth.get("identity_authority") if isinstance(auth.get("identity_authority"), Mapping) else {}
        roles = list(authority.get("platform_roles") or [])
        permissions = list(authority.get("platform_permissions") or [])
        actor_user_id = str(auth.get("actor_user_id") or "").strip()
        platform_user_id = str(authority.get("platform_user_id") or auth.get("platform_user_id") or "").strip()
        runtime_user_type = "registered" if platform_user_id else "external"
        provider = str(auth.get("provider") or "").strip()
        provider_subject = str(auth.get("provider_subject") or "").strip()
        if not actor_user_id or not provider or not provider_subject:
            return {
                "ok": False,
                "error": "authenticated_request_missing_identity",
                "message": "Connection Hub authenticated the request but did not receive a stable subject.",
            }

        grant = await issue_federated_data_bus_token(
            request=request,
            tenant=tenant,
            project=project,
            bundle_id=BUNDLE_ID,
            user_id=actor_user_id,
            user_type=runtime_user_type,
            username=actor_user_id,
            roles=roles,
            permissions=permissions,
            identity_authority=authority,
        )
        LOGGER.info(
            "[connection-hub.data_bus] claim issued provider=%s authority_id=%s authenticator=%s actor_user_id=%s session_id=%s linked=%s",
            provider,
            auth.get("authority_id") or "",
            auth.get("selected_authenticator") or "",
            actor_user_id,
            grant.session.session_id,
            bool(auth.get("linked")),
        )
        return {
            "ok": True,
            "schema": "kdcube.federated_token_claim.v1",
            "federated_token": grant.token,
            "session_id": grant.session.session_id,
            "expires_at": grant.expires_at,
            "bundle_id": BUNDLE_ID,
        }

    @api(method="POST", alias="telegram_connection_edge_start", route="public")
    async def telegram_connection_edge_start(
        self,
        data: Optional[Dict[str, Any]] = None,
        request: Any = None,
        telegram_init_data: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        payload = _payload(data, telegram_init_data=telegram_init_data, **kwargs)
        cfg = _telegram_link_flow_config(self)
        LOGGER.info(
            "[connection-hub.telegram] link_start requested enabled=%s request_present=%s init_data_payload_present=%s",
            cfg.get("enabled") is not False,
            request is not None,
            bool(str(payload.get("telegram_init_data") or "").strip()),
        )
        if cfg.get("enabled") is False:
            LOGGER.warning("[connection-hub.telegram] link_start rejected: linking disabled")
            return {"ok": False, "error": "telegram_connection_edges_disabled"}
        init_data = str(payload.get("telegram_init_data") or "").strip() or extract_telegram_init_data_from_request(request)
        if not init_data:
            LOGGER.warning("[connection-hub.telegram] link_start rejected: init data missing")
            return {"ok": False, "error": "telegram_init_data_required"}
        hints = _auth_selector_hints_from_request_payload(request, payload)
        authority_id = hints.get("authority_id", "")
        authenticator_id = hints.get("authenticator_id", "")
        verified, selected_authenticator, selected_connection_id, selected_authority_id, auth_error = await _validate_telegram_init_data_any(
            self,
            init_data=init_data,
            authority_id=authority_id,
            authenticator_id=authenticator_id,
            trace_scope="telegram_connection_edge_start",
        )
        resolved_authority_id = selected_authority_id or authority_id
        if verified is None:
            code = "telegram_bot_token_not_configured" if auth_error == "telegram_bot_token_not_configured" else "telegram_init_data_invalid"
            LOGGER.warning("[connection-hub.telegram] link_start rejected: init data invalid error=%s", auth_error)
            return {"ok": False, "error": code, "message": auth_error}
        user = verified.user
        telegram_user_id = str(user.get("id") or "").strip()
        username = str(user.get("username") or "").strip()
        live_event_session_id = str(
            payload.get("live_event_session_id")
            or payload.get("event_session_id")
            or payload.get("data_bus_session_id")
            or ""
        ).strip()
        LOGGER.info(
            "[connection-hub.telegram] link_start verified telegram_user_id=%s username_present=%s live_event_session=%s",
            telegram_user_id,
            bool(username),
            bool(live_event_session_id),
        )
        existing_edge = _edge_store(self).resolve_edge(
            from_provider="telegram",
            from_subject=telegram_user_id,
        )
        if existing_edge:
            principal = resolve_principal_roles(
                platform_user_id=_edge_platform_user_id(existing_edge),
                identity_config=_identity_config(self),
            )
            return {
                "ok": True,
                "provider": "telegram",
                "authority_id": resolved_authority_id,
                "connection_id": selected_connection_id,
                "provider_subject": telegram_user_id,
                "linked": True,
                "edge": existing_edge,
                "principal": principal,
                "message": "This Telegram account is already linked to a KDCube user.",
            }
        display_name = (
            username
            or " ".join(str(user.get(key) or "").strip() for key in ("first_name", "last_name") if str(user.get(key) or "").strip())
            or telegram_user_id
        )
        ttl_seconds = int(cfg.get("challenge_ttl_seconds") or 600)
        try:
            metadata: Dict[str, Any] = {
                "telegram": {
                    "id": telegram_user_id,
                    "username": username,
                    "first_name": str(user.get("first_name") or "").strip(),
                    "last_name": str(user.get("last_name") or "").strip(),
                },
                "source": "telegram_miniapp",
                "selected_authenticator": selected_authenticator,
                "authority_id": resolved_authority_id,
                "connection_id": selected_connection_id,
            }
            if live_event_session_id:
                metadata["live_event"] = {
                    "transport": "socketio.chat_service",
                    "session_id": live_event_session_id,
                    "bundle_id": BUNDLE_ID,
                    "event_type": "connection_hub.edge.changed",
                }
            challenge = _edge_store(self).create_provider_claim_challenge(
                provider="telegram",
                provider_subject=telegram_user_id,
                label=display_name,
                created_by=f"telegram:{telegram_user_id}",
                ttl_seconds=ttl_seconds,
                metadata=metadata,
            )
        except ValueError as exc:
            LOGGER.warning("[connection-hub.telegram] link_start challenge creation failed error=%s", exc)
            return {"ok": False, "error": "invalid_telegram_connection_edge_start", "message": str(exc)}
        LOGGER.info(
            "[connection-hub.telegram] link_start created provider claim challenge_id=%s telegram_user_id=%s live_event_session=%s authority_id=%s authenticator=%s",
            challenge.get("challenge_id"),
            telegram_user_id,
            live_event_session_id,
            resolved_authority_id,
            selected_authenticator,
        )
        return {
            "ok": True,
            "provider": "telegram",
            "authority_id": resolved_authority_id,
            "connection_id": selected_connection_id,
            "provider_subject": telegram_user_id,
            "challenge": challenge,
            "platform_claim_url": _platform_claim_url(
                self,
                request=request,
                challenge_id=str(challenge.get("challenge_id") or ""),
                request_origin=str(payload.get("request_origin") or ""),
            ),
            "message": "Telegram account verified. Open KDCube to claim this identity as a platform user.",
        }

    @api(method="GET", alias="telegram_connection_edge_status", route="public")
    async def telegram_connection_edge_status(
        self,
        data: Optional[Dict[str, Any]] = None,
        request: Any = None,
        telegram_init_data: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        payload = _payload(data, telegram_init_data=telegram_init_data, **kwargs)
        cfg = _telegram_link_flow_config(self)
        if cfg.get("enabled") is False:
            return {"ok": False, "error": "telegram_connection_edges_disabled"}
        init_data = str(payload.get("telegram_init_data") or "").strip() or extract_telegram_init_data_from_request(request)
        if not init_data:
            return {"ok": False, "error": "telegram_init_data_required"}
        hints = _auth_selector_hints_from_request_payload(request, payload)
        authority_id = hints.get("authority_id", "")
        authenticator_id = hints.get("authenticator_id", "")
        verified, selected_authenticator, selected_connection_id, selected_authority_id, auth_error = await _validate_telegram_init_data_any(
            self,
            init_data=init_data,
            authority_id=authority_id,
            authenticator_id=authenticator_id,
            trace_scope="telegram_connection_edge_status",
        )
        resolved_authority_id = selected_authority_id or authority_id
        if verified is None:
            code = "telegram_bot_token_not_configured" if auth_error == "telegram_bot_token_not_configured" else "telegram_init_data_invalid"
            return {"ok": False, "error": code, "message": auth_error}
        user = verified.user
        telegram_user_id = str(user.get("id") or "").strip()
        edge = _edge_store(self).resolve_edge(
            from_provider="telegram",
            from_subject=telegram_user_id,
        )
        response: Dict[str, Any] = {
            "ok": True,
            "provider": "telegram",
            "authority_id": resolved_authority_id,
            "connection_id": selected_connection_id,
            "provider_subject": telegram_user_id,
            "linked": bool(edge),
            "selected_authenticator": selected_authenticator,
        }
        if edge:
            response["edge"] = edge
            response["principal"] = resolve_principal_roles(
                platform_user_id=_edge_platform_user_id(edge),
                identity_config=_identity_config(self),
            )
        return response

    @api(method="POST", alias="telegram_connection_edge_remove", route="public")
    async def telegram_connection_edge_remove(
        self,
        data: Optional[Dict[str, Any]] = None,
        request: Any = None,
        telegram_init_data: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        payload = _payload(data, telegram_init_data=telegram_init_data, **kwargs)
        cfg = _telegram_link_flow_config(self)
        if cfg.get("enabled") is False:
            return {"ok": False, "error": "telegram_connection_edges_disabled"}
        init_data = str(payload.get("telegram_init_data") or "").strip() or extract_telegram_init_data_from_request(request)
        if not init_data:
            return {"ok": False, "error": "telegram_init_data_required"}
        hints = _auth_selector_hints_from_request_payload(request, payload)
        authority_id = hints.get("authority_id", "")
        authenticator_id = hints.get("authenticator_id", "")
        verified, selected_authenticator, selected_connection_id, selected_authority_id, auth_error = await _validate_telegram_init_data_any(
            self,
            init_data=init_data,
            authority_id=authority_id,
            authenticator_id=authenticator_id,
            trace_scope="telegram_connection_edge_remove",
        )
        resolved_authority_id = selected_authority_id or authority_id
        if verified is None:
            code = "telegram_bot_token_not_configured" if auth_error == "telegram_bot_token_not_configured" else "telegram_init_data_invalid"
            return {"ok": False, "error": code, "message": auth_error}
        user = verified.user
        telegram_user_id = str(user.get("id") or "").strip()
        result = _edge_store(self).remove_edge(
            from_provider="telegram",
            from_subject=telegram_user_id,
        )
        if not result.get("ok"):
            return result
        removed_edge = {}
        removed_edges = result.get("edges") if isinstance(result.get("edges"), list) else []
        if removed_edges and isinstance(removed_edges[0], Mapping):
            removed_edge = dict(removed_edges[0])
        return {
            "ok": True,
            "provider": "telegram",
            "authority_id": resolved_authority_id,
            "connection_id": selected_connection_id,
            "provider_subject": telegram_user_id,
            "selected_authenticator": selected_authenticator,
            "linked": False,
            "removed": bool(result.get("removed")),
            "edge": removed_edge or None,
            "message": "Telegram account link removed." if result.get("removed") else "This Telegram account was not linked.",
        }

    @api(method="POST", alias="telegram_connection_edge_complete", route="public")
    async def telegram_connection_edge_complete(
        self,
        data: Optional[Dict[str, Any]] = None,
        request: Any = None,
        challenge_id: str = "",
        telegram_init_data: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        payload = _payload(data, challenge_id=challenge_id, telegram_init_data=telegram_init_data, **kwargs)
        cfg = _telegram_link_flow_config(self)
        LOGGER.info(
            "[connection-hub.telegram] link_complete requested enabled=%s request_present=%s challenge_present=%s init_data_payload_present=%s",
            cfg.get("enabled") is not False,
            request is not None,
            bool(str(payload.get("challenge_id") or "").strip()),
            bool(str(payload.get("telegram_init_data") or "").strip()),
        )
        if cfg.get("enabled") is False:
            LOGGER.warning("[connection-hub.telegram] link_complete rejected: linking disabled")
            return {"ok": False, "error": "telegram_connection_edges_disabled"}
        init_data = str(payload.get("telegram_init_data") or "").strip() or extract_telegram_init_data_from_request(request)
        if not init_data:
            LOGGER.warning("[connection-hub.telegram] link_complete rejected: init data missing")
            return {"ok": False, "error": "telegram_init_data_required"}
        hints = _auth_selector_hints_from_request_payload(request, payload)
        authority_id = hints.get("authority_id", "")
        authenticator_id = hints.get("authenticator_id", "")
        verified, selected_authenticator, selected_connection_id, selected_authority_id, auth_error = await _validate_telegram_init_data_any(
            self,
            init_data=init_data,
            authority_id=authority_id,
            authenticator_id=authenticator_id,
            trace_scope="telegram_connection_edge_complete",
        )
        resolved_authority_id = selected_authority_id or authority_id
        if verified is None:
            code = "telegram_bot_token_not_configured" if auth_error == "telegram_bot_token_not_configured" else "telegram_init_data_invalid"
            LOGGER.warning("[connection-hub.telegram] link_complete rejected: init data invalid error=%s", auth_error)
            return {"ok": False, "error": code, "message": auth_error}
        user = verified.user
        telegram_user_id = str(user.get("id") or "").strip()
        username = str(user.get("username") or "").strip()
        LOGGER.info(
            "[connection-hub.telegram] link_complete verified telegram_user_id=%s username_present=%s",
            telegram_user_id,
            bool(username),
        )
        display_name = (
            username
            or " ".join(str(user.get(key) or "").strip() for key in ("first_name", "last_name") if str(user.get(key) or "").strip())
            or telegram_user_id
        )
        result = _edge_store(self).complete_edge_challenge(
            challenge_id=str(payload.get("challenge_id") or ""),
            provider="telegram",
            provider_subject=telegram_user_id,
            label=display_name,
            completed_by=f"telegram:{telegram_user_id}",
            metadata={
                "telegram": {
                    "id": telegram_user_id,
                    "username": username,
                    "first_name": str(user.get("first_name") or "").strip(),
                    "last_name": str(user.get("last_name") or "").strip(),
                },
                "source": "telegram_miniapp",
                "selected_authenticator": selected_authenticator,
                "authority_id": resolved_authority_id,
                "connection_id": selected_connection_id,
            },
        )
        if not result.get("ok"):
            LOGGER.warning(
                "[connection-hub.telegram] link_complete challenge rejected challenge_id=%s error=%s",
                payload.get("challenge_id"),
                result.get("error"),
            )
            return result
        edge = result.get("edge") if isinstance(result.get("edge"), Mapping) else {}
        principal = resolve_principal_roles(
            platform_user_id=_edge_platform_user_id(edge),
            identity_config=_identity_config(self),
        )
        LOGGER.info(
            "[connection-hub.telegram] link_complete linked telegram_user_id=%s platform_user_id=%s",
            telegram_user_id,
            _edge_platform_user_id(edge),
        )
        return {
            "ok": True,
            "provider": "telegram",
            "authority_id": resolved_authority_id,
            "connection_id": selected_connection_id,
            "provider_subject": telegram_user_id,
            "challenge": result.get("challenge"),
            "edge": edge,
            "principal": principal,
        }

    @api(method="POST", alias="identity_resolve", route="operations", **_api_visibility("identity_resolve"))
    async def identity_resolve(
        self,
        data: Optional[Dict[str, Any]] = None,
        provider: str = "",
        provider_subject: str = "",
        subject: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        payload = _payload(
            data,
            provider=provider,
            provider_subject=provider_subject or subject,
            **kwargs,
        )
        provider_value = str(payload.get("provider") or "").strip()
        subject_value = str(payload.get("provider_subject") or payload.get("subject") or "").strip()
        if not provider_value or not subject_value:
            return {"ok": False, "error": "identity_resolve_requires_provider_and_subject"}
        edge = _edge_store(self).resolve_edge(
            from_provider=provider_value,
            from_subject=subject_value,
        )
        if not edge:
            return {
                "ok": False,
                "error": "identity_not_linked",
                "provider": provider_value,
                "provider_subject": subject_value,
            }
        principal = resolve_principal_roles(
            platform_user_id=_edge_platform_user_id(edge),
            identity_config=_identity_config(self),
        )
        return {
            "ok": True,
            "provider": provider_value,
            "provider_subject": subject_value,
            "connection_edge": edge,
            "principal": principal,
        }

    @api(method="POST", alias="identity_family_resolve", route="operations", **_api_visibility("identity_family_resolve"))
    async def identity_family_resolve(
        self,
        data: Optional[Dict[str, Any]] = None,
        input_user_id: str = "",
        actor_user_id: str = "",
        platform_user_id: str = "",
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        payload = _payload(
            data,
            input_user_id=input_user_id,
            actor_user_id=actor_user_id,
            platform_user_id=platform_user_id,
            **kwargs,
        )
        payload_authority = payload.get("identity_authority")
        if not isinstance(payload_authority, Mapping):
            payload_authority = {}
        current_platform_user = _platform_user_id(self, user_id=user_id)
        current_actor_user = _target_user_id(self, user_id=user_id, fingerprint=fingerprint)
        if not current_platform_user and (not current_actor_user or current_actor_user == "anonymous"):
            return {"ok": False, "error": "identity_family_requires_authenticated_actor"}

        requested_user = str(
            payload.get("input_user_id")
            or payload.get("target_user_id")
            or payload.get("actor_user_id")
            or payload.get("user_id")
            or current_platform_user
            or current_actor_user
            or ""
        ).strip()
        if not requested_user or requested_user == "anonymous":
            return {"ok": False, "error": "identity_family_requires_user_id"}

        projected_platform_user = str(payload_authority.get("platform_user_id") or "").strip()
        projected_grants = set(_safe_list(payload_authority.get("grants")))
        if projected_platform_user and "identity:family" in projected_grants:
            current_platform_user = current_platform_user or projected_platform_user

        result = resolve_identity_family(
            _edge_store(self),
            input_user_id=requested_user,
            actor_user_id=current_actor_user,
            platform_user_id=current_platform_user,
        )
        LOGGER.info(
            "[connection-hub.identity_family_resolve] requested_user=%s actor_user=%s platform_user=%s projected_platform_user=%s projected_grants=%s linked=%s memory_user_ids=%s",
            requested_user,
            current_actor_user,
            current_platform_user,
            projected_platform_user,
            sorted(projected_grants),
            result.get("linked") if isinstance(result, Mapping) else None,
            result.get("memory_user_ids") if isinstance(result, Mapping) else None,
        )
        family_platform_user = str(result.get("platform_user_id") or "").strip()
        if current_platform_user:
            if family_platform_user and family_platform_user != current_platform_user:
                return {"ok": False, "error": "identity_family_cross_user_access_denied"}
            if not family_platform_user and requested_user not in {current_platform_user, current_actor_user}:
                return {"ok": False, "error": "identity_family_cross_actor_access_denied"}
        elif requested_user != current_actor_user:
            return {"ok": False, "error": "identity_family_cross_actor_access_denied"}

        if family_platform_user:
            result["principal"] = resolve_principal_roles(
                platform_user_id=family_platform_user,
                identity_config=_identity_config(self),
            )
        result["context"] = {
            "actor_user_id": current_actor_user,
            "platform_user_id": current_platform_user,
        }
        return result

    @api(method="POST", alias="delegated_identity_scope_resolve", route="operations", **_api_visibility("delegated_identity_scope_resolve"))
    async def delegated_identity_scope_resolve(
        self,
        data: Optional[Dict[str, Any]] = None,
        credential: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        payload = _payload(data, credential=credential, **kwargs)
        raw_credential = payload.get("credential") or {}
        if not isinstance(raw_credential, Mapping) or not raw_credential:
            return {"ok": False, "error": "delegated_identity_scope_requires_credential"}
        grantor_authority = payload.get("grantor_authority")
        return resolve_delegated_identity_scope(
            _edge_store(self),
            credential=raw_credential,
            grantor_authority=grantor_authority if isinstance(grantor_authority, Mapping) else {},
        )

    @api(method="GET", alias="authenticators_list", route="operations", **_api_visibility("authenticators_list"))
    async def authenticators_list(
        self,
        provider: str = "",
        include_secret_status: bool = True,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        del kwargs
        provider_filter = str(provider or "").strip().lower()
        items = [
            dict(row)
            for row in merged_authenticator_rows(
                _identity_config(self),
                stored_rows=await _authenticator_store(self).list_rows(),
            )
            if not provider_filter or str(row.get("provider") or "").strip().lower() == provider_filter
        ]
        for item in items:
            item.setdefault("source", "config")
            item.setdefault("implemented", str(item.get("provider") or "").strip().lower() == "telegram")
            item.setdefault("authority_id", str(item.get("authority_id") or item.get("authenticator_id") or "").strip())
            item.pop("integration_id", None)
            item.pop("connection_id", None)
            if include_secret_status:
                item["secret_configured"] = await _authenticator_secret_configured(
                    self,
                    secret_ref=str(item.get("secret_ref") or "").strip(),
                    authenticator_id=str(item.get("authenticator_id") or "").strip(),
                    trace_scope=f"authenticators_list.{item.get('authenticator_id') or item.get('provider') or 'authenticator'}",
                )
        return {
            "ok": True,
            "items": items,
            "count": len(items),
            "supported_providers": supported_authenticator_providers(),
            "providers": sorted({str(item.get("provider") or "") for item in items if item.get("provider")}),
        }

    @api(method="POST", alias="authenticators_upsert", route="operations", **_api_visibility("authenticators_upsert"))
    async def authenticators_upsert(
        self,
        data: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        payload = _payload(data, **kwargs)
        if _contains_authenticator_secret_value(payload):
            return {
                "ok": False,
                "error": "secret_values_not_accepted",
                "message": (
                    "Authenticator operations store metadata only. Put secret values in "
                    "bundles.secrets.yaml or the configured bundle secrets provider and pass "
                    "only secret_ref here."
                ),
            }
        authenticator_id = str(payload.get("authenticator_id") or payload.get("id") or "").strip()
        provider = str(payload.get("provider") or "").strip().lower()
        properties = dict(payload.get("properties") or {}) if isinstance(payload.get("properties"), Mapping) else {}
        definition = payload.get("definition") if isinstance(payload.get("definition"), Mapping) else None
        if definition is not None and "definition" not in properties:
            properties["definition"] = dict(definition)
        where = str(payload.get("where") or properties.get("where") or "").strip()
        if where:
            properties["where"] = where
        try:
            row = await _authenticator_store(self).upsert_row(
                authenticator_id=authenticator_id,
                provider=provider,
                authority_id=str(
                    payload.get("authority_id")
                    or payload.get("authorityId")
                    or payload.get("authority")
                    or ""
                ).strip(),
                connection_id="",
                label=str(payload.get("label") or "").strip(),
                enabled=payload.get("enabled") is not False,
                role_providing=_bool(
                    payload.get("role_providing") if "role_providing" in payload else payload.get("roleProviding"),
                    default=False,
                ),
                subject_namespace=str(payload.get("subject_namespace") or "").strip(),
                secret_ref=str(payload.get("secret_ref") or "").strip(),
                selector=payload.get("selector") if isinstance(payload.get("selector"), Mapping) else None,
                verifier=payload.get("verifier") if isinstance(payload.get("verifier"), Mapping) else None,
                properties=properties,
            )
        except ValueError as exc:
            return {"ok": False, "error": "invalid_authenticator", "message": str(exc)}
        row["secret_configured"] = await _authenticator_secret_configured(
            self,
            secret_ref=str(row.get("secret_ref") or "").strip(),
            authenticator_id=str(row.get("authenticator_id") or "").strip(),
            trace_scope=f"authenticators_upsert.{row.get('authenticator_id') or row.get('provider')}",
        )
        await _invalidate_authenticator_selector_cache(self)
        return {"ok": True, "authenticator": row}

    @api(method="POST", alias="authenticators_remove", route="operations", **_api_visibility("authenticators_remove"))
    async def authenticators_remove(
        self,
        data: Optional[Dict[str, Any]] = None,
        authenticator_id: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        payload = _payload(data, authenticator_id=authenticator_id, **kwargs)
        result = await _authenticator_store(self).remove_row(
            authenticator_id=str(payload.get("authenticator_id") or "").strip(),
        )
        await _invalidate_authenticator_selector_cache(self)
        return result

    @api(method="POST", alias="request_authenticate", route="public")
    async def request_authenticate(
        self,
        data: Optional[Dict[str, Any]] = None,
        request: Any = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        payload = _payload(data, **kwargs)
        if isinstance(request, Mapping):
            envelope = request
        elif isinstance(payload.get("request"), Mapping):
            envelope = payload.get("request")
        else:
            envelope = payload
        return await authenticate_request_with_authenticators(
            self,
            request_envelope=envelope,
            edge_store=_edge_store(self),
            identity_config=_identity_config(self),
            stored_authenticators=await _cached_authenticator_rows(self),
            secret_resolver=lambda **kw: _authenticator_secret_value(self, **kw),
        )

    # ── email integration ops (iCloud app-password only; Gmail is a connections
    #    provider — see connections_* ops + connection_oauth_callback) ─────────

    @api(method="GET", alias="email_accounts_status", route="operations", **_api_visibility("email_accounts_status"))
    async def email_accounts_status(
        self,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        del kwargs
        return await email_settings.status(self, user_id=user_id, fingerprint=fingerprint)

    @api(method="POST", alias="email_connect_app_password", route="operations", **_api_visibility("email_connect_app_password"))
    async def email_connect_app_password(
        self,
        provider: str = "icloud",
        email: str = "",
        app_password: str = "",
        display_name: str = "",
        username: str = "",
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        del kwargs
        return await email_settings.connect_app_password(
            self,
            provider=provider,
            email=email,
            app_password=app_password,
            display_name=display_name,
            username=username,
            user_id=user_id,
            fingerprint=fingerprint,
        )

    @api(method="POST", alias="email_disconnect_account", route="operations", **_api_visibility("email_disconnect_account"))
    async def email_disconnect_account(
        self,
        account_id: str = "",
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        del kwargs
        return await email_settings.disconnect(
            self,
            account_id=account_id,
            user_id=user_id,
            fingerprint=fingerprint,
        )

    # ── Settings widget (stub) ───────────────────────────────────────────────

    @api(method="GET", alias="connections_settings", route="operations", **_api_visibility("connections_settings"))
    @ui_widget(
        alias="connections_settings",
        icon={"lucide": "PlugZap", "tailwind": "heroicons-outline:link"},
        **_widget_visibility("connections_settings"),
    )
    def connections_settings_widget(self, **kwargs: Any) -> list[str]:
        del kwargs
        return [
            "<div style=\"font-family:system-ui,sans-serif;padding:12px\">"
            "The Connections settings widget is served from ui/widgets/connections after build."
            "</div>"
        ]
