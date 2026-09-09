# SPDX-License-Identifier: MIT

from __future__ import annotations

import copy
import logging
import os
from pathlib import Path
from typing import Any, Mapping

import yaml
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.config_cache import get_plain_cache
from kdcube_ai_app.apps.chat.sdk.infra.bundle_urls import bundle_operation_url
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.connection_edges import request_origin
from kdcube_ai_app.apps.middleware.platform_auth import platform_authenticator_provider
from kdcube_ai_app.infra.config.frontend_config import build_frontend_config as build_frontend_config_payload

router = APIRouter()
logger = logging.getLogger(__name__)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _assembly_path() -> Path | None:
    explicit = _text(os.getenv("ASSEMBLY_YAML_DESCRIPTOR_PATH"))
    if explicit:
        return Path(explicit).expanduser()
    descriptors_dir = _text(os.getenv("PLATFORM_DESCRIPTORS_DIR"))
    if descriptors_dir:
        return Path(descriptors_dir).expanduser() / "assembly.yaml"
    default = Path("/config/assembly.yaml")
    return default if default.exists() else None


def _load_assembly_descriptor() -> dict[str, Any]:
    path = _assembly_path()
    if not path or not path.exists():
        return {}
    try:
        # Mtime/size-keyed parse cache (same pattern as settings.plain): the
        # file is stat()ed per call, so any descriptor change re-parses; only
        # the redundant re-parse of an unchanged file is skipped.
        st = path.stat()
        data = get_plain_cache(
            path=str(path),
            mtime_ns=st.st_mtime_ns,
            size=st.st_size,
            dotted_path="__assembly_descriptor__",
            loader=lambda: yaml.safe_load(path.read_text()),
        )
    except Exception as exc:
        logger.warning("Failed to load assembly descriptor for frontend config: %s", exc)
        return {}
    # Deep-copy: callers may embed/augment this mapping in per-request payloads.
    return copy.deepcopy(data) if isinstance(data, dict) else {}


def _assembly_from_settings(settings: Any) -> dict[str, Any]:
    auth_cfg = getattr(settings, "AUTH", None)
    frontend_plain = settings.plain("frontend")
    assembly: dict[str, Any] = {
        "company": settings.plain("company"),
        "context": {
            "tenant": settings.TENANT,
            "project": settings.PROJECT,
        },
        "auth": {
            "type": settings.plain("auth.type"),
            "idp": platform_authenticator_provider(settings),
            "login_url": settings.plain("auth.login_url"),
            "id_token_header_name": _text(getattr(auth_cfg, "ID_TOKEN_HEADER_NAME", "")),
            "turnstile_development_token": settings.plain("auth.turnstile_development_token"),
            "cognito": {
                "region": _text(getattr(auth_cfg, "COGNITO_REGION", "")),
                "user_pool_id": _text(getattr(auth_cfg, "COGNITO_USER_POOL_ID", "")),
                "app_client_id": _text(getattr(auth_cfg, "COGNITO_APP_CLIENT_ID", "")),
            },
        },
        "proxy": {
            "route_prefix": settings.plain("proxy.route_prefix"),
        },
        "platform": {
            "ref": settings.plain("platform.ref"),
        }
    }
    if isinstance(frontend_plain, dict):
        assembly["frontend"] = frontend_plain
    return assembly


def _resolve_bundle_auth_login_url(
    config: dict[str, Any],
    *,
    platform_auth_config: Mapping[str, Any],
    tenant: str,
    project: str,
    request: Request | None = None,
) -> None:
    auth = config.get("auth")
    if not isinstance(auth, dict):
        return
    if _text(auth.get("authType")).lower() != "bundle":
        return
    if _text(auth.get("loginUrl")):
        return

    # The platform-hosted sign-in: the selected session provider names an
    # OIDC or Cognito upstream, so the browser goes to the platform's own
    # login route and comes back with the session cookie. No bundle route.
    from kdcube_ai_app.auth.bundle.browser_session import LOGIN_ROUTE, LOGOUT_ROUTE, upstream_is_oidc

    if upstream_is_oidc(platform_auth_config):
        origin = request_origin(request) or ""
        auth["loginUrl"] = f"{origin.rstrip('/')}{LOGIN_ROUTE}"
        auth["logoutUrl"] = _text(auth.get("logoutUrl")) or LOGOUT_ROUTE
        auth["sessionLane"] = "platform"
        auth.pop("oidcConfig", None)
        return

    connection_hub = auth.get("connectionHub")
    if not isinstance(connection_hub, dict):
        return

    provider = platform_auth_config.get("provider")
    if not isinstance(provider, Mapping):
        logger.warning("Cannot resolve bundle login URL: platform provider descriptor is unavailable")
        return

    entrypoint_name = _text(connection_hub.get("entrypoint")) or "login"
    entrypoints = provider.get("entrypoints")
    endpoint = entrypoints.get(entrypoint_name) if isinstance(entrypoints, Mapping) else None
    if not isinstance(endpoint, Mapping):
        logger.warning(
            "Cannot resolve bundle login URL: provider=%s has no %s entrypoint",
            provider.get("provider_id") or provider.get("id") or "",
            entrypoint_name,
        )
        return

    login_url = bundle_operation_url(
        tenant=tenant,
        project=project,
        bundle_id=endpoint.get("bundle_id") or endpoint.get("bundle") or endpoint.get("app_id"),
        route=_text(endpoint.get("route")) or "public",
        operation=endpoint.get("operation") or endpoint.get("alias"),
        base_url=request_origin(request),
    )
    if not login_url:
        logger.warning(
            "Cannot resolve bundle login URL: provider=%s entrypoint=%s is incomplete",
            provider.get("provider_id") or provider.get("id") or "",
            entrypoint_name,
        )
        return
    auth["loginUrl"] = login_url


def build_frontend_config(request: Request | None = None) -> dict[str, Any]:
    """Build public deployment metadata without runtime service calls.

    The selected authority provider is already present in the mounted bundle
    descriptor. Settings reads that descriptor through its mtime-keyed process
    cache, so this request never depends on Connection Hub execution, Redis, or
    Postgres.
    """

    settings = get_settings()
    assembly = _load_assembly_descriptor() or _assembly_from_settings(settings)
    auth_cfg = getattr(settings, "AUTH", None)
    platform_auth_config = settings.connection_hub_platform_auth_config()
    if not isinstance(platform_auth_config, Mapping):
        platform_auth_config = {}

    config = build_frontend_config_payload(
        tenant=settings.TENANT,
        project=settings.PROJECT,
        assembly=assembly,
        cognito_region=_text(getattr(auth_cfg, "COGNITO_REGION", "")),
        cognito_user_pool_id=_text(getattr(auth_cfg, "COGNITO_USER_POOL_ID", "")),
        cognito_app_client_id=_text(getattr(auth_cfg, "COGNITO_APP_CLIENT_ID", "")),
        routes_prefix=_text(settings.plain("proxy.route_prefix")) or None,
        company_name=_text(settings.plain("company")) or None,
        turnstile_development_token=_text(settings.plain("auth.turnstile_development_token")) or None,
        auth_token_cookie_name=_text(getattr(auth_cfg, "AUTH_TOKEN_COOKIE_NAME", "")) or None,
        id_token_cookie_name=_text(getattr(auth_cfg, "ID_TOKEN_COOKIE_NAME", "")) or None,
        platform_auth_config=platform_auth_config,
    )
    _resolve_bundle_auth_login_url(
        config,
        platform_auth_config=platform_auth_config,
        tenant=settings.TENANT,
        project=settings.PROJECT,
        request=request,
    )
    return config


@router.get("/api/cp-frontend-config")
def cp_frontend_config(request: Request) -> JSONResponse:
    return JSONResponse(
        content=build_frontend_config(request=request),
        headers={"Cache-Control": "no-store, no-cache"},
    )
