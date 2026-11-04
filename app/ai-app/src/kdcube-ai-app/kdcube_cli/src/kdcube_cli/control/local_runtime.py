# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from kdcube_cli import installer as installer_mod
from kdcube_cli.control.errors import (
    AmbiguousTargetError,
    InvalidDescriptorError,
    MissingTargetError,
)
from kdcube_cli.control.models import DeploymentTargetRef, LocalTargetPaths
from kdcube_cli.control.surfaces import load_descriptor


DEFAULT_RUNTIME_ROOT = Path.home() / ".kdcube" / "kdcube-runtime"
DEFAULT_CLI_LOCK = Path.home() / ".kdcube" / "cli-lock.json"
DOCKER_STATUS_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True)
class LocalRuntimeContext:
    repo_root: Path
    ai_app_root: Path
    docker_dir: Path
    workdir: Path
    config_dir: Path
    data_dir: Path


def runtime_env_exists(workdir: Path) -> bool:
    return (workdir / "config" / ".env").exists()


def discover_local_targets(
    base_workdir: Path = DEFAULT_RUNTIME_ROOT,
) -> Tuple[DeploymentTargetRef, ...]:
    base = Path(base_workdir).expanduser().resolve()
    if not base.exists() or not base.is_dir():
        return ()
    targets = []
    for child in base.iterdir():
        if child.is_dir() and runtime_env_exists(child):
            tenant, project = local_target_identity(child)
            targets.append(DeploymentTargetRef.local(child, tenant=tenant, project=project))
    return tuple(sorted(targets, key=lambda item: str(item.workdir)))


def _descriptor_context(
    descriptor_source: Optional[Path],
    assembly_path: Optional[Path],
) -> Tuple[Optional[str], Optional[str]]:
    source = descriptor_source / "assembly.yaml" if descriptor_source is not None else assembly_path
    if source is None or not source.exists():
        return None, None
    try:
        descriptor = load_descriptor(source)
    except InvalidDescriptorError:
        return None, None
    return installer_mod.descriptor_context_from_assembly(descriptor)


def resolve_local_workdir(
    workdir: Path,
    *,
    descriptor_source: Optional[Path] = None,
    assembly_path: Optional[Path] = None,
    tenant: Optional[str] = None,
    project: Optional[str] = None,
) -> Path:
    base = Path(workdir).expanduser().resolve()
    if runtime_env_exists(base) or (base / "config").exists():
        return base
    if tenant or project:
        return installer_mod.workspace_runtime_dir(base, tenant, project).resolve()

    tenant_hint, project_hint = _descriptor_context(descriptor_source, assembly_path)
    namespace = installer_mod.workspace_namespace(tenant_hint, project_hint)
    if base.name == namespace or "__" in base.name:
        return base
    if descriptor_source is not None or assembly_path is not None:
        return installer_mod.workspace_runtime_dir(base, tenant_hint, project_hint).resolve()

    candidates = discover_local_targets(base)
    if len(candidates) == 1 and candidates[0].workdir is not None:
        return candidates[0].workdir
    if len(candidates) > 1:
        raise AmbiguousTargetError(str(base), [str(item.workdir) for item in candidates])
    return installer_mod.workspace_runtime_dir(base, tenant_hint, project_hint).resolve()


def select_local_target(
    workdir: Path,
    *,
    descriptor_source: Optional[Path] = None,
    assembly_path: Optional[Path] = None,
    tenant: Optional[str] = None,
    project: Optional[str] = None,
    require_existing: bool = True,
) -> DeploymentTargetRef:
    resolved = resolve_local_workdir(
        workdir,
        descriptor_source=descriptor_source,
        assembly_path=assembly_path,
        tenant=tenant,
        project=project,
    )
    if require_existing and not runtime_env_exists(resolved) and not (resolved / "config").exists():
        raise MissingTargetError(str(resolved))
    resolved_tenant, resolved_project = local_target_identity(resolved)
    return DeploymentTargetRef.local(
        resolved,
        tenant=tenant or resolved_tenant,
        project=project or resolved_project,
    )


def read_json(path: Path) -> Optional[Dict[str, object]]:
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def local_target_identity(workdir: Path) -> Tuple[Optional[str], Optional[str]]:
    resolved = Path(workdir).expanduser().resolve()
    meta = read_json(resolved / "config" / "install-meta.json")
    if meta is not None:
        tenant = str(meta.get("tenant") or "").strip()
        project = str(meta.get("project") or "").strip()
        if tenant and project:
            return tenant, project
    assembly_path = resolved / "config" / "assembly.yaml"
    if assembly_path.exists():
        try:
            tenant, project = installer_mod.descriptor_context_from_assembly(
                load_descriptor(assembly_path)
            )
            if tenant and project:
                return tenant, project
        except InvalidDescriptorError:
            pass
    if "__" in resolved.name:
        tenant, _, project = resolved.name.partition("__")
        return tenant or None, project or None
    return None, resolved.name or None


def resolve_repo_root(reference: DeploymentTargetRef, explicit: Optional[Path]) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    if reference.workdir is None:
        raise MissingTargetError(reference.target_id)
    meta = read_json(reference.workdir / "config" / "install-meta.json") or {}
    raw_repo = str(meta.get("repo_root") or "").strip()
    if raw_repo and raw_repo != "None":
        return Path(raw_repo).expanduser().resolve()
    return (reference.workdir / "repo").resolve()


def build_local_context(reference: DeploymentTargetRef, repo_root: Path) -> LocalRuntimeContext:
    if reference.workdir is None:
        raise MissingTargetError(reference.target_id)
    resolved_repo = Path(repo_root).expanduser().resolve()
    ai_app_root = resolved_repo / "app" / "ai-app"
    all_in_one = ai_app_root / "deployment" / "docker" / "all_in_one_kdcube"
    if not (all_in_one / "docker-compose.yaml").exists():
        raise MissingTargetError(str(resolved_repo))
    if not (ai_app_root / "src" / "kdcube-ai-app" / "kdcube_ai_app").exists():
        raise MissingTargetError(str(resolved_repo))
    config_dir = reference.workdir / "config"
    compose_mode = "all-in-one"
    env_path = config_dir / ".env"
    if env_path.exists():
        env_file = installer_mod.load_env_file(env_path)
        raw_mode = env_file.entries.get("KDCUBE_COMPOSE_MODE", (None, None))[1]
        if clean_env_value(raw_mode) == "custom-ui-managed-infra":
            compose_mode = "custom-ui-managed-infra"
    docker_dir = ai_app_root / "deployment" / "docker" / (
        "custom-ui-managed-infra"
        if compose_mode == "custom-ui-managed-infra"
        else "all_in_one_kdcube"
    )
    return LocalRuntimeContext(
        repo_root=resolved_repo,
        ai_app_root=ai_app_root,
        docker_dir=docker_dir,
        workdir=reference.workdir,
        config_dir=config_dir,
        data_dir=reference.workdir / "data",
    )


def clean_env_value(raw: object) -> str:
    value = str(raw or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value.strip()


def env_value(env_file: Optional[installer_mod.EnvFile], name: str) -> Optional[str]:
    if env_file is None:
        return None
    return clean_env_value(env_file.entries.get(name, (None, None))[1]) or None


def logs_dir(env_file: installer_mod.EnvFile, workdir: Path) -> Path:
    value = env_value(env_file, "KDCUBE_LOGS_DIR")
    return Path(value).expanduser().resolve() if value else workdir / "logs"


def local_public_base_url(env_file: Optional[installer_mod.EnvFile]) -> str:
    port = env_value(env_file, "KDCUBE_PROXY_HTTP_PORT") or env_value(
        env_file, "KDCUBE_UI_PORT"
    ) or "80"
    return "http://localhost" if port == "80" else f"http://localhost:{port}"


def local_ui_url(env_file: installer_mod.EnvFile) -> str:
    port = env_value(env_file, "KDCUBE_PROXY_HTTP_PORT") or env_value(
        env_file, "KDCUBE_UI_PORT"
    ) or "80"
    routes_prefix = installer_mod.resolve_frontend_routes_prefix(
        env_value(env_file, "PATH_TO_FRONTEND_CONFIG_JSON")
    )
    return installer_mod.build_ui_url(port, routes_prefix)


def local_paths(
    context: LocalRuntimeContext,
    env_file: Optional[installer_mod.EnvFile],
    assembly_path: Path,
    bundles_path: Path,
) -> LocalTargetPaths:
    configured_logs = env_value(env_file, "KDCUBE_LOGS_DIR")
    return LocalTargetPaths(
        workdir=context.workdir,
        config_dir=context.config_dir,
        data_dir=context.data_dir,
        logs_dir=(
            Path(configured_logs).expanduser().resolve()
            if configured_logs
            else context.workdir / "logs"
        ),
        docker_dir=context.docker_dir,
        repo_root=context.repo_root,
        assembly_path=assembly_path if assembly_path.exists() else None,
        bundles_path=bundles_path if bundles_path.exists() else None,
        compose_mode=env_value(env_file, "KDCUBE_COMPOSE_MODE"),
        host_bundles_path=env_value(env_file, "HOST_BUNDLES_PATH"),
        container_bundles_root=env_value(env_file, "BUNDLES_ROOT"),
        host_managed_bundles_path=env_value(env_file, "HOST_MANAGED_BUNDLES_PATH"),
        container_managed_bundles_root=env_value(env_file, "MANAGED_BUNDLES_ROOT"),
        host_bundle_storage_path=env_value(env_file, "HOST_BUNDLE_STORAGE_PATH"),
        container_bundle_storage_root=env_value(env_file, "BUNDLE_STORAGE_ROOT"),
        host_exec_workspace_path=env_value(env_file, "HOST_EXEC_WORKSPACE_PATH"),
        container_exec_workspace_root=env_value(env_file, "EXEC_WORKSPACE_ROOT"),
        host_react_debug_path=env_value(env_file, "HOST_REACT_DEBUG_PATH"),
        container_react_debug_root=env_value(env_file, "REACT_DEBUG_ROOT"),
    )


def compose_environment(env_file: Path) -> Dict[str, str]:
    return installer_mod.compose_env(env_file)


def same_path(left: object, right: object) -> bool:
    if left is None or right is None:
        return False
    try:
        return Path(str(left)).expanduser().resolve() == Path(str(right)).expanduser().resolve()
    except Exception:
        return False


def namespace_key(value: object) -> str:
    return str(value or "").strip().replace("_", "-")
