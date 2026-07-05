# SPDX-License-Identifier: MIT

from __future__ import annotations

import copy
import logging
import os
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.config_cache import get_plain_cache
from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry_config import (
    platform_authority_auth_config,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.client import ConnectionHubClient
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


def _router_redis() -> Any:
    state = getattr(router, "state", None)
    middleware = getattr(state, "middleware", None)
    return (
        getattr(state, "redis", None)
        or getattr(state, "redis_async", None)
        or getattr(middleware, "redis", None)
        or getattr(middleware, "redis_async", None)
    )


async def _resolve_bundle_auth_login_url(
    config: dict[str, Any],
    *,
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
    connection_hub = auth.get("connectionHub")
    if not isinstance(connection_hub, dict):
        return

    redis = _router_redis()
    if redis is None:
        logger.warning("Cannot resolve bundle login URL: runtime Redis is unavailable")
        return

    result = await ConnectionHubClient(
        connection_hub_bundle_id=_text(connection_hub.get("bundleId")),
        tenant=tenant,
        project=project,
        redis=redis,
    ).resolve_authority_provider_entrypoint(
        authority_id=_text(connection_hub.get("authorityId")),
        provider_id=_text(connection_hub.get("providerId")),
        provider_type=_text(connection_hub.get("providerType")),
        entrypoint=_text(connection_hub.get("entrypoint")) or "login",
        request=request,
    )
    if not result.get("ok"):
        logger.warning(
            "Connection Hub did not resolve bundle login URL: authority=%s provider=%s error=%s",
            connection_hub.get("authorityId"),
            connection_hub.get("providerId"),
            result.get("error"),
        )
        return
    auth["loginUrl"] = _text(result.get("url"))


async def _resolve_platform_auth_config(
    settings: Any,
    assembly: dict[str, Any],
) -> dict[str, Any]:
    auth = assembly.get("auth")
    ref = auth.get("connection_hub") if isinstance(auth, dict) else None
    if not isinstance(ref, dict):
        ref = auth.get("connectionHub") if isinstance(auth, dict) else None
    if not isinstance(ref, dict):
        ref = settings.plain("auth.connection_hub")
    if not isinstance(ref, dict):
        ref = settings.plain("auth.connectionHub")
    if not isinstance(ref, dict):
        return settings.connection_hub_platform_auth_config()

    redis = _router_redis()
    if redis is None:
        return settings.connection_hub_platform_auth_config()

    result = await ConnectionHubClient(
        connection_hub_bundle_id=_text(ref.get("bundle_id") or ref.get("bundleId")),
        tenant=settings.TENANT,
        project=settings.PROJECT,
        redis=redis,
    ).resolve_authority_provider(
        authority_id=_text(ref.get("authority_id") or ref.get("authorityId")),
        provider_id=_text(ref.get("provider_id") or ref.get("providerId")),
        provider_type=_text(ref.get("provider_type") or ref.get("providerType")),
    )
    config = platform_authority_auth_config(result)
    return config or settings.connection_hub_platform_auth_config()


async def build_frontend_config(request: Request | None = None) -> dict[str, Any]:
    settings = get_settings()
    assembly = _load_assembly_descriptor() or _assembly_from_settings(settings)
    auth_cfg = getattr(settings, "AUTH", None)
    platform_auth_config = await _resolve_platform_auth_config(settings, assembly)

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
    await _resolve_bundle_auth_login_url(
        config,
        tenant=settings.TENANT,
        project=settings.PROJECT,
        request=request,
    )
    return config


@router.get("/api/cp-frontend-config")
async def cp_frontend_config(request: Request) -> JSONResponse:
    return JSONResponse(
        content=await build_frontend_config(request=request),
        headers={"Cache-Control": "no-store, no-cache"},
    )
