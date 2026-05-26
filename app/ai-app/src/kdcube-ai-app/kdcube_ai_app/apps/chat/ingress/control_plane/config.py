# SPDX-License-Identifier: MIT

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from kdcube_ai_app.apps.chat.sdk.config import get_settings
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
        data = yaml.safe_load(path.read_text())
    except Exception as exc:
        logger.warning("Failed to load assembly descriptor for frontend config: %s", exc)
        return {}
    return data if isinstance(data, dict) else {}


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
            "idp": getattr(settings, "AUTH_PROVIDER", ""),
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
    }
    if isinstance(frontend_plain, dict):
        assembly["frontend"] = frontend_plain
    return assembly


def build_frontend_config() -> dict[str, Any]:
    settings = get_settings()
    assembly = _load_assembly_descriptor() or _assembly_from_settings(settings)
    auth_cfg = getattr(settings, "AUTH", None)

    return build_frontend_config_payload(
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
    )


@router.get("/api/cp-frontend-config")
async def cp_frontend_config() -> JSONResponse:
    return JSONResponse(
        content=build_frontend_config(),
        headers={"Cache-Control": "no-store, no-cache"},
    )
