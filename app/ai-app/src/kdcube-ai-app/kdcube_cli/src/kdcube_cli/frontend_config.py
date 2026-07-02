# SPDX-License-Identifier: MIT
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping, Optional, Union

import yaml


def as_text(value: Any) -> str:
    return str(value or "").strip()


__all__ = [
    "as_text",
    "is_placeholder",
    "normalize_routes_prefix",
    "get_nested",
    "deep_merge",
    "load_yaml_descriptor",
    "load_json_file",
    "build_frontend_config",
    "build_frontend_config_from_assembly",
    "write_frontend_config_file",
]


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


def deep_merge(base: Optional[Mapping[str, Any]], overlay: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = copy.deepcopy(dict(base or {}))
    for key, value in dict(overlay or {}).items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = deep_merge(result.get(key), value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_yaml_descriptor(path: Union[Path, str]) -> dict[str, Any]:
    descriptor_path = Path(path).expanduser()
    data = yaml.safe_load(descriptor_path.read_text()) if descriptor_path.exists() else {}
    return data if isinstance(data, dict) else {}


def load_json_file(path: Optional[Union[Path, str]]) -> dict[str, Any]:
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


def _frontend_config_overrides(assembly: Optional[Mapping[str, Any]]) -> dict[str, Any]:
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


def _assembly_auth_type(assembly: Optional[Mapping[str, Any]]) -> str:
    auth_type = as_text(get_nested(assembly or {}, "auth", "type")).lower()
    auth_idp = as_text(get_nested(assembly or {}, "auth", "idp")).lower()
    if auth_type == "delegated":
        return "delegated"
    if auth_type in {"bundle", "bundle-session"} or auth_idp in {"session", "bundle", "bundle-session"}:
        return "bundle"
    if auth_type == "simple" or auth_idp == "simple":
        return "simple"
    if auth_type == "cognito" or auth_idp == "cognito":
        return "cognito"
    return "simple"


def _assembly_auth_declared(assembly: Optional[Mapping[str, Any]]) -> bool:
    return bool(as_text(get_nested(assembly or {}, "auth", "type")) or as_text(get_nested(assembly or {}, "auth", "idp")))


def _normalize_frontend_auth_type(value: Any) -> str:
    auth_type = as_text(value).lower()
    if auth_type == "hardcoded":
        return "simple"
    return auth_type


def _build_oidc_authority(region: str, user_pool_id: str) -> str:
    if region and user_pool_id:
        return f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
    return ""


def build_frontend_config(
    *,
    tenant: str,
    project: str,
    assembly: Optional[Mapping[str, Any]] = None,
    template_data: Optional[Mapping[str, Any]] = None,
    existing_data: Optional[Mapping[str, Any]] = None,
    token: str = "test-admin-token-123",
    cognito_region: Optional[str] = None,
    cognito_user_pool_id: Optional[str] = None,
    cognito_app_client_id: Optional[str] = None,
    routes_prefix: Optional[str] = None,
    company_name: Optional[str] = None,
    turnstile_development_token: Optional[str] = None,
    auth_token_cookie_name: Optional[str] = None,
    id_token_cookie_name: Optional[str] = None,
    platform_auth_config: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """
    Build the public frontend runtime config.

    Merge order is template < existing file < assembly.frontend.config, then the
    installer-owned fields are normalized from assembly/env inputs.
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
    explicit_frontend_auth_type = get_nested(frontend_overrides, "auth", "authType")
    if as_text(explicit_frontend_auth_type):
        auth_type = _normalize_frontend_auth_type(explicit_frontend_auth_type)
    elif _assembly_auth_declared(assembly):
        auth_type = _assembly_auth_type(assembly)
    else:
        auth_type = _normalize_frontend_auth_type(auth.get("authType")) or _assembly_auth_type(assembly)
    auth["authType"] = auth_type

    provider_auth = dict(platform_auth_config or {})
    id_token_header = (
        as_text(provider_auth.get("id_token_header_name"))
        or as_text(get_nested(assembly or {}, "auth", "id_token_header_name"))
        or "X-ID-Token"
    )
    if auth_type == "simple":
        if auth.get("token") in (None, "", "test-admin-token-123"):
            auth["token"] = token
    elif auth_type in {"cognito", "oauth"}:
        auth.pop("token", None)
        oidc_cfg = copy.deepcopy(auth.get("oidcConfig") if isinstance(auth.get("oidcConfig"), dict) else {})
        region = (
            as_text(cognito_region)
            or as_text(provider_auth.get("region"))
            or as_text(get_nested(assembly or {}, "auth", "cognito", "region"))
        )
        user_pool_id = (
            as_text(cognito_user_pool_id)
            or as_text(provider_auth.get("user_pool_id"))
            or as_text(get_nested(assembly or {}, "auth", "cognito", "user_pool_id"))
        )
        client_id = (
            as_text(cognito_app_client_id)
            or as_text(provider_auth.get("app_client_id"))
            or as_text(get_nested(assembly or {}, "auth", "cognito", "app_client_id"))
        )
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
    elif auth_type == "bundle":
        auth.pop("token", None)
        connection_hub_ref = (
            auth.get("connectionHub")
            if isinstance(auth.get("connectionHub"), Mapping)
            else get_nested(assembly or {}, "auth", "connection_hub")
            or get_nested(assembly or {}, "auth", "connectionHub")
            or get_nested(assembly or {}, "auth", "bundle_session", "connection_hub")
        )
        if isinstance(connection_hub_ref, Mapping):
            raw_ref = copy.deepcopy(dict(connection_hub_ref))
            normalized_ref: dict[str, Any] = {}
            for source, target in (
                ("bundle_id", "bundleId"),
                ("authority_id", "authorityId"),
                ("provider_id", "providerId"),
                ("provider_type", "providerType"),
                ("entrypoint", "entrypoint"),
            ):
                value = raw_ref.get(source, raw_ref.get(target))
                if value not in (None, ""):
                    normalized_ref[target] = value
            if normalized_ref:
                auth["connectionHub"] = normalized_ref
        login_url = (
            as_text(auth.get("loginUrl"))
            or as_text(get_nested(assembly or {}, "auth", "login_url"))
            or as_text(get_nested(assembly or {}, "auth", "login", "url"))
            or as_text(get_nested(assembly or {}, "auth", "bundle_session", "login_url"))
        )
        if login_url:
            auth["loginUrl"] = login_url

    turnstile_token = (
        as_text(turnstile_development_token)
        or as_text(get_nested(assembly or {}, "auth", "turnstile_development_token"))
    )
    if turnstile_token and not is_placeholder(turnstile_token):
        auth["turnstileDevelopmentToken"] = turnstile_token
    elif is_placeholder(as_text(auth.get("turnstileDevelopmentToken"))):
        auth.pop("turnstileDevelopmentToken", None)

    # Non-masquerade auth cookie names, so a parent page (e.g. a co-located
    # landing site) can set exactly the cookies the proxy reads for the
    # embedded same-origin widgets. Source: assembly auth.*_cookie_name with the
    # platform defaults.
    auth["authTokenCookieName"] = (
        as_text(auth_token_cookie_name)
        or as_text(provider_auth.get("auth_token_cookie_name"))
        or as_text(get_nested(assembly or {}, "auth", "auth_token_cookie_name"))
        or "__Secure-LATC"
    )
    auth["idTokenCookieName"] = (
        as_text(id_token_cookie_name)
        or as_text(provider_auth.get("id_token_cookie_name"))
        or as_text(get_nested(assembly or {}, "auth", "id_token_cookie_name"))
        or "__Secure-LITC"
    )
    auth["profileUrl"] = as_text(auth.get("profileUrl")) or "/profile"
    auth["logoutUrl"] = as_text(auth.get("logoutUrl")) or "/api/platform/logout"

    merged["auth"] = auth

    platform_ref = as_text(get_nested(assembly or {}, "platform", "ref"))
    if platform_ref:
        versions = copy.deepcopy(merged.get("versions") if isinstance(merged.get("versions"), dict) else {})
        versions["platform"] = platform_ref
        merged["versions"] = versions

    return merged


def build_frontend_config_from_assembly(
    assembly: Mapping[str, Any],
    *,
    template_data: Optional[Mapping[str, Any]] = None,
    existing_data: Optional[Mapping[str, Any]] = None,
    token: str = "test-admin-token-123",
    cognito_region: Optional[str] = None,
    cognito_user_pool_id: Optional[str] = None,
    cognito_app_client_id: Optional[str] = None,
    routes_prefix: Optional[str] = None,
    company_name: Optional[str] = None,
    turnstile_development_token: Optional[str] = None,
    platform_auth_config: Optional[Mapping[str, Any]] = None,
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
        platform_auth_config=platform_auth_config,
    )


def write_frontend_config_file(path: Union[Path, str], **kwargs: Any) -> dict[str, Any]:
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
