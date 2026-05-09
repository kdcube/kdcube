# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import base64
import copy
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional
from urllib.parse import unquote, urlparse

from kdcube_ai_app.apps.chat.sdk.runtime.isolated.environment import filter_host_environment


# Centralized catalog of platform env families. This does not mean every
# family should be propagated to every runtime. The external exec runtimes
# use a narrower subset built from this catalog.
PLATFORM_ENV_GROUPS: dict[str, tuple[str, ...]] = {
    "runtime_bootstrap": (
        "AWS_CONFIG_FILE",
        "AWS_DEFAULT_REGION",
        "AWS_EC2_METADATA_DISABLED",
        "AWS_PROFILE",
        "AWS_REGION",
        "AWS_SDK_LOAD_CONFIG",
        "AWS_SHARED_CREDENTIALS_FILE",
        "NO_PROXY",
        "SECRETS_PROVIDER",
        "SECRETS_AWS_REGION",
        "SECRETS_AWS_SM_PREFIX",
        "SECRETS_SM_PREFIX",
        "SECRETS_SM_REGION",
        "SECRETS_TOKEN",
        "SECRETS_URL",
    ),
    "runtime_core": (
        "APP_DOMAIN",
        "AUTH_PROVIDER",
        "AUTH_TOKEN_COOKIE_NAME",
        "AWS_CONFIG_FILE",
        "AWS_DEFAULT_REGION",
        "AWS_EC2_METADATA_DISABLED",
        "AWS_PROFILE",
        "AWS_REGION",
        "AWS_SDK_LOAD_CONFIG",
        "AWS_SHARED_CREDENTIALS_FILE",
        "BUNDLE_STORAGE_ROOT",
        "CB_BUNDLE_STORAGE_URL",
        "DEFAULT_BUNDLE_ID",
        "DEFAULT_EMBEDDING_MODEL_ID",
        "DEFAULT_LLM_MODEL_ID",
        "GATEWAY_COMPONENT",
        "ID_TOKEN_COOKIE_NAME",
        "ID_TOKEN_HEADER_NAME",
        "INSTANCE_ID",
        "KDCUBE_STORAGE_PATH",
        "NO_PROXY",
        "PROJECT_ID",
        "REACT_WORKSPACE_IMPLEMENTATION",
        "REACT_WORKSPACE_GIT_REPO",
        "STREAM_ID_HEADER_NAME",
        "TENANT_ID",
    ),
    "gateway": (
        "GATEWAY_CONFIG_JSON",
    ),
    "secrets_provider": (
        "SECRETS_ADMIN_TOKEN",
        "SECRETS_AWS_REGION",
        "SECRETS_AWS_SM_PREFIX",
        "SECRETS_PROVIDER",
        "SECRETS_SM_PREFIX",
        "SECRETS_SM_REGION",
        "SECRETS_TOKEN",
        "SECRETS_URL",
    ),
    "descriptor_payload": (
        "KDCUBE_RUNTIME_ASSEMBLY_YAML_B64",
        "KDCUBE_RUNTIME_BUNDLES_YAML_B64",
        "KDCUBE_RUNTIME_GATEWAY_YAML_B64",
        "KDCUBE_RUNTIME_SECRETS_YAML_B64",
        "KDCUBE_RUNTIME_BUNDLES_SECRETS_YAML_B64",
    ),
    "platform_secret_exports": (
        "KDCUBE_PLATFORM_SECRETS_JSON",
        "KDCUBE_BUNDLES_SECRETS_JSON",
    ),
    "relational": (
        "POSTGRES_DATABASE",
        "POSTGRES_HOST",
        "POSTGRES_PASSWORD",
        "POSTGRES_PORT",
        "POSTGRES_SSL",
        "POSTGRES_SSL_MODE",
        "POSTGRES_SSL_ROOT_CERT",
        "POSTGRES_USER",
        "REDIS_DB",
        "REDIS_HOST",
        "REDIS_PASSWORD",
        "REDIS_PORT",
        "REDIS_URL",
    ),
    "auth": (
        "COGNITO_APP_CLIENT_ID",
        "COGNITO_REGION",
        "COGNITO_SERVICE_CLIENT_ID",
        "COGNITO_USER_POOL_ID",
        "OIDC_SERVICE_USER_EMAIL",
        "OIDC_SERVICE_ADMIN_PASSWORD",
        "OIDC_SERVICE_ADMIN_USERNAME",
    ),
    "bundles": (
        "BUNDLE_CLEANUP_ENABLED",
        "BUNDLE_CLEANUP_INTERVAL_SECONDS",
        "BUNDLE_CLEANUP_LOCK_TTL_SECONDS",
        "BUNDLE_GIT_ALWAYS_PULL",
        "BUNDLE_GIT_ATOMIC",
        "BUNDLE_GIT_FAIL_BACKOFF_SECONDS",
        "BUNDLE_GIT_FAIL_MAX_BACKOFF_SECONDS",
        "BUNDLE_GIT_KEEP",
        "BUNDLE_GIT_PREFETCH_ENABLED",
        "BUNDLE_GIT_PREFETCH_INTERVAL_SECONDS",
        "BUNDLE_GIT_REDIS_LOCK",
        "BUNDLE_GIT_RESOLUTION_ENABLED",
        "BUNDLE_GIT_TTL_HOURS",
        "BUNDLE_REF_TTL_SECONDS",
        "BUNDLES_FORCE_ENV_LOCK_TTL_SECONDS",
        "BUNDLES_FORCE_ENV_ON_STARTUP",
        "BUNDLES_INCLUDE_EXAMPLES",
        "BUNDLES_PRELOAD_LOCK_TTL_SECONDS",
        "HOST_MANAGED_BUNDLES_PATH",
        "GIT_HTTP_TOKEN",
        "GIT_HTTP_USER",
        "GIT_SSH_COMMAND",
        "GIT_SSH_KEY_PATH",
        "GIT_SSH_KNOWN_HOSTS",
        "GIT_SSH_STRICT_HOST_KEY_CHECKING",
    ),
    "web_research": (
        "TOOLS_WEB_SEARCH_FETCH_CONTENT",
        "WEB_FETCH_RESOURCES_MEDIUM",
        "WEB_SEARCH_AGENTIC_THINKING_BUDGET",
        "WEB_SEARCH_BACKEND",
        "WEB_SEARCH_CACHE_TTL_SECONDS",
        "WEB_SEARCH_HYBRID_MODE",
        "WEB_SEARCH_MAX_BASE64_CHARS",
        "WEB_SEARCH_PRIMARY_BACKEND",
        "WEB_SEARCH_SEGMENTER",
    ),
    "providers": (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "CLAUDE_CODE_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "BRAVE_API_KEY",
        "OPENROUTER_API_KEY",
        "HUGGING_FACE_KEY",
        "HUGGINGFACE_API_KEY",
        "STRIPE_SECRET_KEY",
        "STRIPE_API_KEY",
        "STRIPE_WEBHOOK_SECRET",
        "GITHUB_TOKEN",
        "GH_TOKEN",
    ),
    "mcp": (
        "MCP_CACHE_TTL_SECONDS",
        "MCP_SERVICES",
    ),
    "exec_runtime": (
        "EXEC_DESCRIPTOR_PAYLOAD_SCOPE",
        "EXEC_RUNTIME_MODE",
        "EXEC_WORKSPACE_ROOT",
        "FARGATE_ASSIGN_PUBLIC_IP",
        "FARGATE_CLUSTER",
        "FARGATE_CONTAINER_NAME",
        "FARGATE_EXEC_ENABLED",
        "FARGATE_LAUNCH_TYPE",
        "FARGATE_PLATFORM_VERSION",
        "FARGATE_SECURITY_GROUPS",
        "FARGATE_SUBNETS",
        "FARGATE_TASK_DEFINITION",
        "PY_CODE_EXEC_IMAGE",
        "PY_CODE_EXEC_CONTAINER_STRATEGY",
        "PY_CODE_EXEC_NETWORK_MODE",
        "PY_CODE_EXEC_TIMEOUT",
    ),
    "logging": (
        "LOG_BACKUP_COUNT",
        "LOG_LEVEL",
        "LOG_MAX_MB",
    ),
}


PLATFORM_ENV_PREFIX_GROUPS: dict[str, tuple[str, ...]] = {
    "web_research": (
        "WEB_FETCH_RESOURCES_",
    ),
}


# External exec should prefer resolving secrets through get_secret()/settings
# and provider configuration instead of blindly inheriting the full proc env.
# In particular, the large deployment JSON secret blobs stay cataloged above
# but are intentionally omitted here to avoid ECS override bloat.
EXTERNAL_RUNTIME_ENV_GROUPS: tuple[str, ...] = (
    "runtime_core",
    "secrets_provider",
    "descriptor_payload",
    "relational",
    "auth",
    "bundles",
    "web_research",
    "providers",
    "mcp",
    "exec_runtime",
    "logging",
)


EXTERNAL_RUNTIME_INLINE_ENV_GROUPS: tuple[str, ...] = (
    "runtime_bootstrap",
    "logging",
)


_DESCRIPTOR_SOURCE_ENV_KEYS = frozenset(
    {
        "PLATFORM_DESCRIPTORS_DIR",
        "GLOBAL_SECRETS_YAML",
        "BUNDLE_SECRETS_YAML",
    }
)


def _normalize_env_value(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    if text == "":
        return None
    return text


def _resolve_local_path(raw_value: object) -> Path | None:
    text = _normalize_env_value(raw_value)
    if text is None:
        return None
    if text.startswith("{") or text.startswith("["):
        return None
    parsed = urlparse(text)
    if parsed.scheme and parsed.scheme != "file":
        return None
    path_text = unquote(parsed.path if parsed.scheme == "file" else text).strip()
    if not path_text:
        return None
    return Path(path_text).expanduser()


_BUNDLE_DESCRIPTOR_PAYLOAD_ENVS = {
    "KDCUBE_RUNTIME_BUNDLES_YAML_B64",
    "KDCUBE_RUNTIME_BUNDLES_SECRETS_YAML_B64",
}


def _normalize_descriptor_payload_scope(value: object | None) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    if text in {"active", "active_bundle", "bundle", "caller_bundle", "current_bundle"}:
        return "active_bundle"
    if text in {"all", "full", "none", "no_filter", "unfiltered", "platform"}:
        return "all"
    return ""


def _load_descriptor_mapping(text: str, *, path: Path) -> Optional[Mapping[str, Any]]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    try:
        if path.suffix.lower() == ".json" or raw.startswith("{"):
            parsed = json.loads(raw)
        else:
            import yaml  # type: ignore

            parsed = yaml.safe_load(raw)
    except Exception:
        return None
    return parsed if isinstance(parsed, Mapping) else None


def _dump_descriptor_mapping(data: Mapping[str, Any]) -> Optional[str]:
    try:
        import yaml  # type: ignore

        return yaml.safe_dump(dict(data), allow_unicode=True, sort_keys=False)
    except Exception:
        return None


def _filter_bundle_item_list(items: Any, bundle_id: str) -> list[Any]:
    if not isinstance(items, list):
        return []
    return [
        copy.deepcopy(item)
        for item in items
        if isinstance(item, Mapping) and str(item.get("id") or "").strip() == bundle_id
    ]


def _filter_bundle_mapping_block(block: Mapping[str, Any], bundle_id: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in block.items():
        key_text = str(key)
        if key_text == "items":
            out[key_text] = _filter_bundle_item_list(value, bundle_id)
            continue
        if key_text == "default_bundle_id":
            out[key_text] = bundle_id
            continue
        if key_text == bundle_id:
            out[key_text] = copy.deepcopy(value)
            continue
        if key_text in {"version", "schema_version", "kind"}:
            out[key_text] = copy.deepcopy(value)
    if "items" not in out and bundle_id in block:
        out[bundle_id] = copy.deepcopy(block[bundle_id])
    return out


def _filter_bundle_descriptor_text(text: str, *, path: Path, bundle_id: str) -> Optional[str]:
    data = _load_descriptor_mapping(text, path=path)
    if data is None:
        return None
    scoped = copy.deepcopy(dict(data))
    if isinstance(scoped.get("bundles"), Mapping):
        scoped["bundles"] = _filter_bundle_mapping_block(scoped["bundles"], bundle_id)
    elif isinstance(scoped.get("items"), list):
        scoped["items"] = _filter_bundle_item_list(scoped.get("items"), bundle_id)
        if "default_bundle_id" in scoped:
            scoped["default_bundle_id"] = bundle_id
    elif bundle_id in scoped:
        scoped = {bundle_id: copy.deepcopy(scoped[bundle_id])}
    else:
        scoped = {}
    return _dump_descriptor_mapping(scoped)


def _read_descriptor_payload(
    path: Path | None,
    *,
    env_name: str,
    bundle_id: str | None = None,
    descriptor_payload_scope: str | None = None,
) -> str | None:
    if path is None or not path.exists() or not path.is_file():
        return None
    try:
        raw_bytes = path.read_bytes()
        if (
            env_name in _BUNDLE_DESCRIPTOR_PAYLOAD_ENVS
            and _normalize_descriptor_payload_scope(descriptor_payload_scope) == "active_bundle"
        ):
            scoped_bundle_id = str(bundle_id or "").strip()
            if not scoped_bundle_id:
                return None
            filtered = _filter_bundle_descriptor_text(
                raw_bytes.decode("utf-8"),
                path=path,
                bundle_id=scoped_bundle_id,
            )
            if filtered is None:
                return None
            raw_bytes = filtered.encode("utf-8")
        return base64.b64encode(raw_bytes).decode("ascii")
    except Exception:
        return None


def _descriptor_source_path(
    *,
    host_env: Mapping[str, object],
    filename: str,
    env_keys: tuple[str, ...],
) -> Path | None:
    for env_key in env_keys:
        path = _resolve_local_path(host_env.get(env_key))
        if path is not None:
            return path
    descriptors_dir = _resolve_local_path(host_env.get("PLATFORM_DESCRIPTORS_DIR"))
    if descriptors_dir is not None:
        return descriptors_dir / filename
    return None


def _collect_descriptor_payload_env(
    host_env: Mapping[str, object],
    *,
    bundle_id: str | None = None,
    descriptor_payload_scope: str | None = None,
) -> Dict[str, str]:
    specs = (
        ("KDCUBE_RUNTIME_ASSEMBLY_YAML_B64", "assembly.yaml", ("ASSEMBLY_YAML_DESCRIPTOR_PATH",)),
        ("KDCUBE_RUNTIME_BUNDLES_YAML_B64", "bundles.yaml", ("BUNDLES_YAML_DESCRIPTOR_PATH",)),
        ("KDCUBE_RUNTIME_GATEWAY_YAML_B64", "gateway.yaml", ("GATEWAY_YAML_PATH",)),
        ("KDCUBE_RUNTIME_SECRETS_YAML_B64", "secrets.yaml", ("GLOBAL_SECRETS_YAML",)),
        ("KDCUBE_RUNTIME_BUNDLES_SECRETS_YAML_B64", "bundles.secrets.yaml", ("BUNDLE_SECRETS_YAML",)),
    )
    exported: Dict[str, str] = {}
    for env_name, filename, env_keys in specs:
        payload = _read_descriptor_payload(
            _descriptor_source_path(
                host_env=host_env,
                filename=filename,
                env_keys=env_keys,
            ),
            env_name=env_name,
            bundle_id=bundle_id,
            descriptor_payload_scope=descriptor_payload_scope,
        )
        if payload:
            exported[env_name] = payload
    return exported


def collect_platform_env_groups(
    host_env: Mapping[str, object],
    groups: Iterable[str],
    *,
    bundle_id: str | None = None,
    descriptor_payload_scope: str | None = None,
) -> Dict[str, str]:
    requested = tuple(groups)
    filtered_host_env = filter_host_environment(
        {str(k): str(v) for k, v in host_env.items() if v is not None}
    )
    collected: Dict[str, str] = {}
    for group in requested:
        for key in PLATFORM_ENV_GROUPS.get(group, ()):
            if key not in filtered_host_env:
                continue
            value = _normalize_env_value(filtered_host_env.get(key))
            if value is None:
                continue
            collected[key] = value
        prefixes = PLATFORM_ENV_PREFIX_GROUPS.get(group, ())
        if prefixes:
            for key, raw_value in filtered_host_env.items():
                if key in collected:
                    continue
                if not any(str(key).startswith(prefix) for prefix in prefixes):
                    continue
                value = _normalize_env_value(raw_value)
                if value is None:
                    continue
                collected[str(key)] = value
    if "descriptor_payload" in requested:
        scope = _normalize_descriptor_payload_scope(
            descriptor_payload_scope or host_env.get("EXEC_DESCRIPTOR_PAYLOAD_SCOPE")
        )
        for key, value in _collect_descriptor_payload_env(
            host_env,
            bundle_id=bundle_id,
            descriptor_payload_scope=scope,
        ).items():
            collected.setdefault(key, value)
    return collected


def _merge_host_and_managed_env(
    host_env: Mapping[str, object],
    *,
    keys: Iterable[str],
    settings: object | None = None,
) -> Dict[str, object]:
    merged: Dict[str, object] = {}
    try:
        from kdcube_ai_app.apps.chat.sdk.config import export_managed_env

        merged.update(export_managed_env(settings=settings, keys=keys))
    except Exception:
        pass
    for key, value in (host_env or {}).items():
        if _normalize_env_value(value) is None:
            continue
        merged[str(key)] = value
    return merged


def build_external_runtime_base_env(
    host_env: Mapping[str, object],
    *,
    settings: object | None = None,
    bundle_id: str | None = None,
    descriptor_payload_scope: str | None = None,
) -> Dict[str, str]:
    requested_keys = EXTERNAL_RUNTIME_ENV_KEYS | _DESCRIPTOR_SOURCE_ENV_KEYS
    merged_env = _merge_host_and_managed_env(
        host_env,
        keys=requested_keys,
        settings=settings,
    )
    return collect_platform_env_groups(
        merged_env,
        EXTERNAL_RUNTIME_ENV_GROUPS,
        bundle_id=bundle_id,
        descriptor_payload_scope=descriptor_payload_scope,
    )


def build_external_runtime_inline_env(
    host_env: Mapping[str, object],
    *,
    settings: object | None = None,
) -> Dict[str, str]:
    requested_keys = frozenset(
        key
        for group in EXTERNAL_RUNTIME_INLINE_ENV_GROUPS
        for key in PLATFORM_ENV_GROUPS.get(group, ())
    ) | _DESCRIPTOR_SOURCE_ENV_KEYS
    merged_env = _merge_host_and_managed_env(
        host_env,
        keys=requested_keys,
        settings=settings,
    )
    return collect_platform_env_groups(merged_env, EXTERNAL_RUNTIME_INLINE_ENV_GROUPS)


EXTERNAL_RUNTIME_ENV_KEYS = frozenset(
    key
    for group in EXTERNAL_RUNTIME_ENV_GROUPS
    for key in PLATFORM_ENV_GROUPS.get(group, ())
)
