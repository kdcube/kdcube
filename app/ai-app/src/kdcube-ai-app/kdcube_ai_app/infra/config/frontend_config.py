# SPDX-License-Identifier: MIT
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml


def as_text(value: Any) -> str:
    return str(value or "").strip()


def is_placeholder(value: Optional[str]) -> bool:
    if value is None:
        return True
    stripped = value.strip().strip("'\"")
    if not stripped:
        return True
    if stripped.upper() in {"TENANT_ID", "PROJECT_ID"}:
        return True
    if "<" in stripped and ">" in stripped:
        return True
    lowered = stripped.lower()
    if "/absolute/path" in lowered or "absolute/path" in lowered:
        return True
    if "path/to/" in lowered or lowered.startswith("path/to"):
        return True
    if "relative_path" in lowered:
        return True
    if "platform-repo/" in stripped or "frontend-repo/" in stripped:
        return True
    if "..." in stripped:
        return True
    if "changeme" in lowered:
        return True
    return False


def normalize_routes_prefix(value: Any, default: str = "/chatbot") -> str:
    raw = as_text(value) or default
    if raw == "/":
        return ""
    return "/" + raw.strip("/")


def get_nested(data: Any, *path: str) -> Any:
    cur = data
    for key in path:
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(key)
    return cur


def deep_merge(base: Mapping[str, Any] | None, overlay: Mapping[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = copy.deepcopy(dict(base or {}))
    for key, value in dict(overlay or {}).items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = deep_merge(result.get(key), value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_yaml_descriptor(path: Path | str) -> dict[str, Any]:
    descriptor_path = Path(path).expanduser()
    data = yaml.safe_load(descriptor_path.read_text()) if descriptor_path.exists() else {}
    return data if isinstance(data, dict) else {}


def load_json_file(path: Path | str | None) -> dict[str, Any]:
    if not path:
        return {}
    json_path = Path(path).expanduser()
    if not json_path.exists():
        return {}
    try:
        data = json.loads(json_path.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _frontend_config_overrides(assembly: Mapping[str, Any] | None) -> dict[str, Any]:
    frontend = get_nested(assembly or {}, "frontend")
    overrides: dict[str, Any] = {}
    if isinstance(frontend, Mapping):
        auth = frontend.get("auth")
        if isinstance(auth, Mapping):
            auth_overrides = copy.deepcopy(dict(auth))
            for source, target in (
                ("totp_app_name", "totpAppName"),
                ("totp_issuer", "totpIssuer"),
                ("api_base", "apiBase"),
            ):
                if source in auth_overrides and target not in auth_overrides:
                    auth_overrides[target] = auth_overrides[source]
                auth_overrides.pop(source, None)
            overrides["auth"] = auth_overrides
        for key in (
            "routesPrefix",
            "routes_prefix",
            "apiBase",
            "api_base",
            "supportEmail",
            "support_email",
            "userRegistrationBundleId",
            "user_registration_bundle_id",
            "debug",
        ):
            if key in frontend:
                target_key = {
                    "routes_prefix": "routesPrefix",
                    "api_base": "apiBase",
                    "support_email": "supportEmail",
                    "user_registration_bundle_id": "userRegistrationBundleId",
                }.get(key, key)
                overrides[target_key] = copy.deepcopy(frontend.get(key))
    raw = get_nested(assembly or {}, "frontend", "config")
    if isinstance(raw, dict):
        overrides = deep_merge(overrides, raw)
    return overrides


def _assembly_auth_type(assembly: Mapping[str, Any] | None) -> str:
    auth_type = as_text(get_nested(assembly or {}, "auth", "type")).lower()
    auth_idp = as_text(get_nested(assembly or {}, "auth", "idp")).lower()
    if auth_type == "delegated":
        return "delegated"
    if auth_type == "simple" or auth_idp == "simple":
        return "hardcoded"
    if auth_type == "cognito" or auth_idp == "cognito":
        return "cognito"
    return "hardcoded"


def _build_oidc_authority(region: str, user_pool_id: str) -> str:
    if region and user_pool_id:
        return f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
    return ""


def build_frontend_config(
    *,
    tenant: str,
    project: str,
    assembly: Mapping[str, Any] | None = None,
    template_data: Mapping[str, Any] | None = None,
    existing_data: Mapping[str, Any] | None = None,
    token: str = "test-admin-token-123",
    cognito_region: str | None = None,
    cognito_user_pool_id: str | None = None,
    cognito_app_client_id: str | None = None,
    routes_prefix: str | None = None,
    company_name: str | None = None,
    turnstile_development_token: str | None = None,
) -> dict[str, Any]:
    """
    Build the public frontend runtime config.

    Merge order is template < existing file < assembly.frontend.config, then the
    installer/runtime-owned fields are normalized from assembly/env inputs.
    """
    frontend_overrides = _frontend_config_overrides(assembly)
    merged = deep_merge(template_data, existing_data)
    merged = deep_merge(merged, frontend_overrides)

    merged["tenant"] = tenant
    merged["project"] = project
    if "tenant_id" in merged:
        merged["tenant_id"] = tenant
    if "project_id" in merged:
        merged["project_id"] = project

    override_routes = frontend_overrides.get("routesPrefix") or frontend_overrides.get("routes_prefix")
    if override_routes:
        merged["routesPrefix"] = normalize_routes_prefix(override_routes)
    elif routes_prefix:
        merged["routesPrefix"] = normalize_routes_prefix(routes_prefix)
    else:
        merged["routesPrefix"] = normalize_routes_prefix(merged.get("routesPrefix"))

    assembly_company = as_text(get_nested(assembly or {}, "company"))
    company = as_text(company_name) or assembly_company or "KDCube"
    auth = copy.deepcopy(merged.get("auth") if isinstance(merged.get("auth"), dict) else {})
    auth_type = as_text(auth.get("authType")) or _assembly_auth_type(assembly)
    auth["authType"] = auth_type

    id_token_header = as_text(get_nested(assembly or {}, "auth", "id_token_header_name")) or "X-ID-Token"
    if auth_type == "hardcoded":
        if auth.get("token") in (None, "", "test-admin-token-123"):
            auth["token"] = token
    elif auth_type in {"cognito", "oauth"}:
        auth.pop("token", None)
        oidc_cfg = copy.deepcopy(auth.get("oidcConfig") if isinstance(auth.get("oidcConfig"), dict) else {})
        region = as_text(cognito_region) or as_text(get_nested(assembly or {}, "auth", "cognito", "region"))
        user_pool_id = as_text(cognito_user_pool_id) or as_text(get_nested(assembly or {}, "auth", "cognito", "user_pool_id"))
        client_id = as_text(cognito_app_client_id) or as_text(get_nested(assembly or {}, "auth", "cognito", "app_client_id"))
        authority = _build_oidc_authority(region, user_pool_id)
        if authority:
            oidc_cfg["authority"] = authority
        if client_id:
            oidc_cfg["client_id"] = client_id
        auth["idTokenHeaderName"] = as_text(auth.get("idTokenHeaderName")) or id_token_header
        auth["oidcConfig"] = oidc_cfg
    elif auth_type == "delegated":
        auth.pop("token", None)
        if auth.get("totpAppName") in (None, "", "COMPANY_NAME", "<COMPANY_NAME>"):
            auth["totpAppName"] = company
        if auth.get("totpIssuer") in (None, "", "COMPANY_NAME", "<COMPANY_NAME>"):
            auth["totpIssuer"] = company
        auth.setdefault("apiBase", "/auth/")

    turnstile_token = (
        as_text(turnstile_development_token)
        or as_text(get_nested(assembly or {}, "auth", "turnstile_development_token"))
    )
    if turnstile_token and not is_placeholder(turnstile_token):
        auth["turnstileDevelopmentToken"] = turnstile_token
    elif is_placeholder(as_text(auth.get("turnstileDevelopmentToken"))):
        auth.pop("turnstileDevelopmentToken", None)

    merged["auth"] = auth
    return merged


def build_frontend_config_from_assembly(
    assembly: Mapping[str, Any],
    *,
    template_data: Mapping[str, Any] | None = None,
    existing_data: Mapping[str, Any] | None = None,
    token: str = "test-admin-token-123",
    cognito_region: str | None = None,
    cognito_user_pool_id: str | None = None,
    cognito_app_client_id: str | None = None,
    routes_prefix: str | None = None,
    company_name: str | None = None,
    turnstile_development_token: str | None = None,
) -> dict[str, Any]:
    tenant = as_text(get_nested(assembly, "context", "tenant")) or "demo-tenant"
    project = as_text(get_nested(assembly, "context", "project")) or "demo-project"
    route_prefix = routes_prefix
    if route_prefix is None:
        route_prefix = as_text(get_nested(assembly, "proxy", "route_prefix"))
    return build_frontend_config(
        tenant=tenant,
        project=project,
        assembly=assembly,
        template_data=template_data,
        existing_data=existing_data,
        token=token,
        cognito_region=cognito_region,
        cognito_user_pool_id=cognito_user_pool_id,
        cognito_app_client_id=cognito_app_client_id,
        routes_prefix=route_prefix or None,
        company_name=company_name,
        turnstile_development_token=turnstile_development_token,
    )


def write_frontend_config_file(path: Path | str, **kwargs: Any) -> dict[str, Any]:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    template_path = kwargs.pop("template_path", None)
    config = build_frontend_config(
        template_data=load_json_file(template_path),
        existing_data=load_json_file(target),
        **kwargs,
    )
    target.write_text(json.dumps(config, indent=2) + "\n")
    return config
