# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import pathlib
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping

from kdcube_ai_app.apps.chat.proc.app_deployment.models import (
    AppStaticSurfaceManifest,
    DeployedWidgetSurface,
)
from kdcube_ai_app.apps.chat.proc.app_deployment.modes import (
    static_widget_deployment_enabled,
    static_widget_runtime_generation,
)
from kdcube_ai_app.apps.chat.proc.app_deployment.policy import (
    is_bundle_enabled,
    is_widget_enabled,
    static_widget_config,
    static_widget_explicitly_disabled,
)
from kdcube_ai_app.apps.chat.proc.app_deployment.storage import (
    app_resources_signature_path,
    deployment_manifest_ready,
    deployment_signature_path,
    load_deployment_manifest,
    resolve_app_storage_root,
    write_deployment_manifest,
)
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.infra.plugin.bundle_loader import (
    BundleSpec,
    apply_bundle_overrides,
    apply_widget_overrides,
    discover_bundle_interface_manifest,
    preload_bundle_async,
    provider_surface_auth,
)
from kdcube_ai_app.infra.plugin.bundle_once import run_once_for_shared_bundle_storage
from kdcube_ai_app.infra.plugin.bundle_store import (
    bundle_entry_to_spec,
    get_bundle_props_from_authority,
)
from kdcube_ai_app.infra.service_hub.inventory import AgentLogger

DEPLOYMENT_SCHEMA_VERSION = 1


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)


def props_fingerprint(props: Mapping[str, Any] | None) -> str:
    return hashlib.sha256(_stable_json(dict(props or {})).encode("utf-8")).hexdigest()


def source_generation_for_spec(spec: Any) -> str:
    payload = {
        "id": str(getattr(spec, "id", "") or ""),
        "path": str(getattr(spec, "path", "") or ""),
        "module": str(getattr(spec, "module", "") or ""),
        "singleton": bool(getattr(spec, "singleton", False)),
        "repo": str(getattr(spec, "repo", "") or ""),
        "ref": str(getattr(spec, "ref", "") or ""),
        "subdir": str(getattr(spec, "subdir", "") or ""),
        "git_commit": str(getattr(spec, "git_commit", "") or ""),
    }
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()[:24]


def _app_source_fingerprint_sync(spec: Any) -> str:
    root = pathlib.Path(str(getattr(spec, "path", "") or ""))
    if not root.is_dir():
        return "unavailable"
    ignored_dirs = {
        ".git",
        ".vite",
        ".vite-temp",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
    }
    sha = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if any(part in ignored_dirs for part in relative.parts) or path.is_dir():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        sha.update(relative.as_posix().encode("utf-8"))
        sha.update(b"\0")
        sha.update(str(stat.st_size).encode("ascii"))
        sha.update(b"\0")
        sha.update(str(stat.st_mtime_ns).encode("ascii"))
        sha.update(b"\n")
    return sha.hexdigest()


async def app_source_fingerprint(spec: Any) -> str:
    """Fingerprint app source without walking the tree on proc's event loop."""
    return await asyncio.to_thread(_app_source_fingerprint_sync, spec)


def _deep_merge_props(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(dict(base or {}))
    for key, value in dict(override or {}).items():
        if isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
            out[key] = _deep_merge_props(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _policy_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item for item in value.replace(",", " ").split() if item]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _manifest_auth_policy(auth: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Keep only fields consumed by the request-time authorization check."""
    if not isinstance(auth, Mapping):
        return None
    authority_id = str(auth.get("authority_id") or auth.get("authority") or "").strip()
    grants = _policy_values(
        auth.get("grants") or auth.get("scopes") or auth.get("required_grants")
    )
    projected: dict[str, Any] = {}
    if authority_id:
        projected["authority_id"] = authority_id
    if grants:
        projected["grants"] = grants
    return projected or None


def apply_effective_props(workflow: Any, descriptor_props: Mapping[str, Any]) -> dict[str, Any]:
    defaults = copy.deepcopy(getattr(workflow, "bundle_props_defaults", None) or {})
    if not defaults:
        defaults = copy.deepcopy(getattr(workflow, "bundle_props", None) or {})
    merger = getattr(workflow, "_deep_merge_props", None)
    effective = (
        merger(defaults, dict(descriptor_props))
        if callable(merger)
        else _deep_merge_props(defaults, descriptor_props)
    )
    try:
        setattr(workflow, "bundle_props", effective)
    except Exception:
        return effective
    for hook_name in ("_apply_bundle_props_overrides", "_sync_runtime_ctx_bundle_props"):
        hook = getattr(workflow, hook_name, None)
        if callable(hook):
            hook()
    return dict(getattr(workflow, "bundle_props", None) or effective)


def select_hook_kwargs(hook: Any, provided: dict[str, Any]) -> dict[str, Any]:
    try:
        signature = inspect.signature(hook)
    except (TypeError, ValueError):
        return {}
    if any(param.kind is inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
        return provided
    return {name: value for name, value in provided.items() if name in signature.parameters}


async def _invoke_on_app_deploy(
    *,
    workflow: Any,
    module: Any,
    bundle_spec: Any,
    agentic_spec: BundleSpec,
    storage_root: Any,
    tenant: str,
    project: str,
    props: dict[str, Any],
    pg_pool: Any,
    redis: Any,
    logger: AgentLogger,
    reread_props: Callable[[], Awaitable[dict[str, Any]]] | None = None,
) -> None:
    hook = getattr(workflow, "on_app_deploy", None)
    if not callable(hook):
        hook = getattr(module, "on_app_deploy", None)
    if not callable(hook):
        return
    if not inspect.iscoroutinefunction(hook):
        raise TypeError("bundle on_app_deploy hook must be declared with async def")
    kwargs = {
        "bundle_spec": bundle_spec,
        "agentic_spec": agentic_spec,
        "storage_root": storage_root,
        "tenant": tenant,
        "project": project,
        "props": copy.deepcopy(props),
        "pg_pool": pg_pool,
        "redis": redis,
        "logger": logger,
        "reread_props": reread_props,
    }
    await hook(**select_hook_kwargs(hook, kwargs))


def _surface_projection_sync(
    *,
    workflow: Any,
    bundle_id: str,
    props: dict[str, Any],
) -> tuple[bool, list[str], dict[str, DeployedWidgetSurface]]:
    discovered = discover_bundle_interface_manifest(workflow, bundle_id=bundle_id)
    bundle_manifest = apply_bundle_overrides(discovered, props)
    widgets: dict[str, DeployedWidgetSurface] = {}
    for raw_widget in discovered.ui_widgets:
        widget = apply_widget_overrides(raw_widget, props)
        static_cfg = None
        if not static_widget_explicitly_disabled(props, widget_alias=widget.alias):
            static_cfg = static_widget_config(props, widget_alias=widget.alias)
        build_signature = None
        if static_cfg is not None:
            compute_signature = getattr(workflow, "compute_ui_widget_signature", None)
            if callable(compute_signature):
                build_signature = compute_signature(widget.alias)
        widgets[widget.alias] = DeployedWidgetSurface(
            alias=widget.alias,
            method_name=widget.method_name,
            icon=dict(widget.icon or {}),
            user_types=list(widget.user_types),
            roles=list(widget.roles),
            auth=_manifest_auth_policy(
                provider_surface_auth(props, "widget", alias=widget.alias)
            ),
            enabled=is_widget_enabled(props, widget),
            static=static_cfg is not None,
            artifact_relpath=f"ui/widgets/{widget.alias}" if static_cfg is not None else None,
            build_signature=build_signature,
        )
    return is_bundle_enabled(props), list(bundle_manifest.allowed_roles), widgets


async def _surface_projection(
    *,
    workflow: Any,
    bundle_id: str,
    props: dict[str, Any],
) -> tuple[bool, list[str], dict[str, DeployedWidgetSurface]]:
    return await asyncio.to_thread(
        _surface_projection_sync,
        workflow=workflow,
        bundle_id=bundle_id,
        props=props,
    )


async def deploy_loaded_bundle_app_resources(
    *,
    workflow: Any,
    module: Any,
    agentic_spec: BundleSpec,
    bundle_spec: Any,
    tenant: str,
    project: str,
    pg_pool: Any = None,
    redis: Any = None,
) -> AppStaticSurfaceManifest | None:
    """Reconcile the resources required by one desired app generation.

    ``on_app_deploy`` is the app-level resource barrier. Deployed static UI is
    one participant in that barrier and is skipped when its delivery mode is
    disabled; non-UI app resources still reconcile.
    """

    bundle_id = str(getattr(bundle_spec, "id", None) or agentic_spec.id).strip()
    descriptor_props = await get_bundle_props_from_authority(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
    ) or {}
    effective_props = apply_effective_props(workflow, descriptor_props)
    storage_root = await resolve_app_storage_root(
        spec=bundle_spec,
        tenant=tenant,
        project=project,
        ensure=True,
    )
    if storage_root is None:
        raise RuntimeError(f"Bundle storage is unavailable for app deployment: {bundle_id}")

    logger = AgentLogger("bundle.on_app_deploy", getattr(getattr(workflow, "config", None), "log_level", "INFO"))
    source_generation = source_generation_for_spec(bundle_spec)
    descriptor_props_hash = props_fingerprint(descriptor_props)
    source_fingerprint = await app_source_fingerprint(bundle_spec)
    resource_generation = hashlib.sha256(
        _stable_json(
            {
                "schema_version": DEPLOYMENT_SCHEMA_VERSION,
                "tenant": tenant,
                "project": project,
                "bundle_id": bundle_id,
                "source_generation": source_generation,
                "app_source_fingerprint": source_fingerprint,
                "props_fingerprint": descriptor_props_hash,
                "runtime_generation": static_widget_runtime_generation(),
            }
        ).encode("utf-8")
    ).hexdigest()
    settings = get_settings()
    lock_ttl = max(
        900,
        int(settings.PLATFORM.APPLICATIONS.BUNDLES_PRELOAD_LOCK_TTL_SECONDS),
        int(settings.PLATFORM.APPLICATIONS.BUNDLES_PRELOAD_BUNDLE_LOCK_TTL_SECONDS),
    )

    async def _reread_effective_props() -> dict[str, Any]:
        """The effective props as the authority holds them right now.

        Offered to hooks that publish shared state under a lock: the props may
        have moved between this reconcile reading them and the hook acquiring
        that lock.
        """
        fresh = await get_bundle_props_from_authority(
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
        ) or {}
        return apply_effective_props(workflow, fresh)

    async def _deploy_app_resources() -> None:
        await _invoke_on_app_deploy(
            workflow=workflow,
            module=module,
            bundle_spec=bundle_spec,
            agentic_spec=agentic_spec,
            storage_root=storage_root,
            tenant=tenant,
            project=project,
            props=effective_props,
            pg_pool=pg_pool,
            redis=redis,
            logger=logger,
            reread_props=_reread_effective_props,
        )

    await run_once_for_shared_bundle_storage(
        storage_root=storage_root,
        operation="app-deploy-resources",
        signature_path=app_resources_signature_path(storage_root),
        signature=resource_generation,
        ready=lambda: True,
        action=_deploy_app_resources,
        logger=logger,
        owner_metadata={
            "kind": "app-resources",
            "bundle_id": bundle_id,
            "tenant": tenant,
            "project": project,
        },
        lock_wait_seconds=float(lock_ttl),
        lock_ttl_seconds=float(lock_ttl),
        allow_existing_on_timeout=False,
        log_prefix="[bundle.deploy.resources]",
    )

    if not static_widget_deployment_enabled():
        return None

    return await _reconcile_static_surfaces(
        workflow=workflow,
        bundle_spec=bundle_spec,
        bundle_id=bundle_id,
        tenant=tenant,
        project=project,
        descriptor_props=descriptor_props,
        effective_props=effective_props,
        storage_root=storage_root,
        logger=logger,
        source_generation=source_generation,
        source_fingerprint=source_fingerprint,
        descriptor_props_hash=descriptor_props_hash,
        lock_ttl=lock_ttl,
    )


async def _reconcile_static_surfaces(
    *,
    workflow: Any,
    bundle_spec: Any,
    bundle_id: str,
    tenant: str,
    project: str,
    descriptor_props: dict[str, Any],
    effective_props: dict[str, Any],
    storage_root: pathlib.Path,
    logger: AgentLogger,
    source_generation: str,
    source_fingerprint: str,
    descriptor_props_hash: str,
    lock_ttl: int,
) -> AppStaticSurfaceManifest | None:
    bundle_enabled, bundle_allowed_roles, widgets = await _surface_projection(
        workflow=workflow,
        bundle_id=bundle_id,
        props=effective_props,
    )
    signature_payload = {
        "schema_version": DEPLOYMENT_SCHEMA_VERSION,
        "tenant": tenant,
        "project": project,
        "bundle_id": bundle_id,
        "source_generation": source_generation,
        "props_fingerprint": descriptor_props_hash,
        "bundle_enabled": bundle_enabled,
        "bundle_allowed_roles": bundle_allowed_roles,
        "widgets": {alias: surface.model_dump(mode="json") for alias, surface in sorted(widgets.items())},
    }
    deployment_signature = hashlib.sha256(
        _stable_json(
            {
                **signature_payload,
                "app_source_fingerprint": source_fingerprint,
                "runtime_generation": static_widget_runtime_generation(),
            }
        ).encode("utf-8")
    ).hexdigest()
    manifest = AppStaticSurfaceManifest(
        **signature_payload,
        deployment_signature=deployment_signature,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    async def _deploy() -> None:
        logger.log(
            f"[bundle.deploy.ui] start: bundle={bundle_id} tenant={tenant} project={project}",
            level="INFO",
        )
        ensure_ui_build = getattr(workflow, "_ensure_ui_build", None)
        if callable(ensure_ui_build):
            result = ensure_ui_build()
            if inspect.isawaitable(result):
                await result
        await write_deployment_manifest(storage_root, manifest)
        logger.log(
            f"[bundle.deploy.ui] done: bundle={bundle_id} tenant={tenant} project={project} "
            f"widgets={sorted(widgets)} signature={deployment_signature[:12]}",
            level="INFO",
        )

    async def _deployment_ready() -> bool:
        return await deployment_manifest_ready(
            storage_root,
            expected_signature=deployment_signature,
        )

    await run_once_for_shared_bundle_storage(
        storage_root=storage_root,
        operation="app-deploy-static-surfaces",
        signature_path=deployment_signature_path(storage_root),
        signature=deployment_signature,
        ready=_deployment_ready,
        action=_deploy,
        logger=logger,
        owner_metadata={
            "kind": "app-deployment",
            "bundle_id": bundle_id,
            "tenant": tenant,
            "project": project,
        },
        lock_wait_seconds=float(lock_ttl),
        lock_ttl_seconds=float(lock_ttl),
        allow_existing_on_timeout=False,
        log_prefix="[bundle.deploy]",
    )
    return await load_deployment_manifest(storage_root)


async def deploy_registry_bundle_app_resources(
    *,
    entry: Any,
    tenant: str,
    project: str,
    pg_pool: Any = None,
    redis: Any = None,
) -> AppStaticSurfaceManifest | None:
    """Load a registry app and reconcile its desired resource generation."""
    bundle_spec = bundle_entry_to_spec(entry)
    agentic_spec = BundleSpec(
        id=bundle_spec.id,
        path=bundle_spec.path,
        module=bundle_spec.module,
        singleton=bool(bundle_spec.singleton),
    )
    workflow, module = await preload_bundle_async(
        agentic_spec,
        bundle_spec,
        tenant=tenant,
        project=project,
        pg_pool=pg_pool,
        redis=redis,
    )
    return await deploy_loaded_bundle_app_resources(
        workflow=workflow,
        module=module,
        agentic_spec=agentic_spec,
        bundle_spec=bundle_spec,
        tenant=tenant,
        project=project,
        pg_pool=pg_pool,
        redis=redis,
    )
