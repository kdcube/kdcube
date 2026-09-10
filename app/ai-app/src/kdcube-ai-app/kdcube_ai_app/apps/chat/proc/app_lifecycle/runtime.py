# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from typing import Any

from kdcube_ai_app.apps.chat.proc.app_deployment.coordinator import (
    deploy_loaded_bundle_app_resources,
    props_fingerprint,
    source_generation_for_spec,
)
from kdcube_ai_app.apps.chat.proc.app_deployment.deprovision import (
    deprovision_loaded_bundle_app_resources,
)
from kdcube_ai_app.apps.chat.proc.app_deployment.modes import (
    static_widget_runtime_generation,
)
from kdcube_ai_app.apps.chat.proc.app_lifecycle.supervisor import (
    ApplicationLifecycleSupervisor,
    ApplicationPreparation,
)
from kdcube_ai_app.infra.plugin.app_readiness import (
    ApplicationLifecycleState,
    ApplicationReadinessMode,
    ApplicationReadinessRegistry,
    application_readiness_registry,
    normalize_readiness_mode,
)
from kdcube_ai_app.infra.plugin.bundle_loader import (
    BundleSpec,
    evict_bundle_scope,
    load_bundle_for_deprovision_async,
    load_bundle_manifest,
    preload_bundle_async,
)
from kdcube_ai_app.infra.plugin.bundle_registry import (
    ADMIN_BUNDLE_ID,
    resolve_git_bundle_entry_async,
    upsert_bundles_async,
)
from kdcube_ai_app.infra.plugin.bundle_store import (
    BundleEntry,
    BundlesRegistry,
    bundle_entry_to_spec,
    get_bundle_props_from_authority,
)

APP_PREPARATION_SCHEMA_VERSION = 1


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)


def _explicitly_disabled(value: Any) -> bool:
    if isinstance(value, bool):
        return not value
    if isinstance(value, str):
        return value.strip().lower() in {"0", "false", "no", "off", "disabled"}
    return False


def _configured_widget_aliases(props: dict[str, Any] | None) -> list[str]:
    ui = props.get("ui") if isinstance(props, dict) else None
    widgets = ui.get("widgets") if isinstance(ui, dict) else None
    if not isinstance(widgets, dict):
        return []
    aliases: set[str] = set()
    for alias, config in widgets.items():
        normalized = str(alias or "").strip()
        if not normalized:
            continue
        if isinstance(config, dict) and _explicitly_disabled(config.get("enabled", True)):
            continue
        if not isinstance(config, dict) and _explicitly_disabled(config):
            continue
        aliases.add(normalized)
    return sorted(aliases)


async def validate_prepared_application_manifest(
    *,
    application_id: str,
    spec: BundleSpec,
    tenant: str,
    project: str,
    logger: logging.Logger,
) -> None:
    """Prove that this process can discover each configured static UI surface."""
    props = await get_bundle_props_from_authority(
        tenant=tenant,
        project=project,
        bundle_id=application_id,
    ) or {}
    expected_widgets = _configured_widget_aliases(dict(props))
    manifest = load_bundle_manifest(spec, bundle_id=application_id)
    discovered_widgets = sorted({item.alias for item in manifest.ui_widgets})
    missing_widgets = sorted(set(expected_widgets) - set(discovered_widgets))

    if missing_widgets:
        evicted = evict_bundle_scope(spec, drop_sys_modules=True)
        logger.warning(
            "Application manifest mismatch; evicted local caches and retrying discovery: "
            "application=%s expected_widgets=%s discovered_widgets=%s evicted=%s",
            application_id,
            expected_widgets,
            discovered_widgets,
            evicted,
        )
        manifest = load_bundle_manifest(spec, bundle_id=application_id)
        discovered_widgets = sorted({item.alias for item in manifest.ui_widgets})
        missing_widgets = sorted(set(expected_widgets) - set(discovered_widgets))

    if missing_widgets:
        raise RuntimeError(
            "Configured static widget aliases are not declared with @ui_widget: "
            f"application={application_id} missing={missing_widgets} "
            f"configured={expected_widgets} discovered={discovered_widgets} path={spec.path}"
        )


def _readiness_mode(entry: BundleEntry) -> ApplicationReadinessMode:
    service = getattr(entry, "service", None)
    return normalize_readiness_mode(getattr(service, "readiness", None))


def _application_generation(
    *,
    entry: BundleEntry,
    source_fingerprint: str,
    descriptor_props_fingerprint: str,
) -> str:
    payload = {
        "schema_version": APP_PREPARATION_SCHEMA_VERSION,
        "id": entry.id,
        "path": entry.path,
        "module": entry.module,
        "singleton": bool(entry.singleton),
        "repo": entry.repo,
        "ref": entry.ref,
        "subdir": entry.subdir,
        "git_commit": entry.git_commit,
        "source_fingerprint": source_fingerprint,
        "descriptor_props_fingerprint": descriptor_props_fingerprint,
        "runtime_generation": static_widget_runtime_generation(),
    }
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()[:24]


class ProcApplicationLifecycle:
    """Process-local app preparation, state publication, and supersession."""

    def __init__(
        self,
        *,
        tenant: str,
        project: str,
        redis: Any,
        pg_pool: Any,
        concurrency: int,
        retry_initial_seconds: float,
        retry_max_seconds: float,
        registry: ApplicationReadinessRegistry = application_readiness_registry,
        logger: logging.Logger | None = None,
    ) -> None:
        self.tenant = str(tenant).strip()
        self.project = str(project).strip()
        self.redis = redis
        self.pg_pool = pg_pool
        self.registry = registry
        self.logger = logger or logging.getLogger(__name__)
        self._reconcile_lock = asyncio.Lock()
        self._last_registry: BundlesRegistry | None = None
        self._ready_callback: Callable[[ApplicationPreparation], Awaitable[None]] | None = None
        self.supervisor = ApplicationLifecycleSupervisor(
            tenant=self.tenant,
            project=self.project,
            registry=registry,
            prepare=self._prepare,
            on_ready=self._notify_ready,
            concurrency=concurrency,
            retry_initial_seconds=retry_initial_seconds,
            retry_max_seconds=retry_max_seconds,
            logger=self.logger,
        )

    def set_ready_callback(
        self,
        callback: Callable[[ApplicationPreparation], Awaitable[None]] | None,
    ) -> None:
        self._ready_callback = callback

    async def _notify_ready(self, preparation: ApplicationPreparation) -> None:
        if self._ready_callback is not None:
            await self._ready_callback(preparation)

    async def _build_preparation(self, entry: BundleEntry) -> ApplicationPreparation:
        try:
            props = await get_bundle_props_from_authority(
                tenant=self.tenant,
                project=self.project,
                bundle_id=entry.id,
            ) or {}
            props_hash = props_fingerprint(dict(props))
        except Exception:
            self.logger.warning(
                "Could not fingerprint application properties; preparation will retry the authoritative read: "
                "application=%s",
                entry.id,
                exc_info=True,
            )
            props_hash = "unavailable"

        source_hash = source_generation_for_spec(bundle_entry_to_spec(entry))

        return ApplicationPreparation(
            application_id=entry.id,
            generation=_application_generation(
                entry=entry,
                source_fingerprint=source_hash,
                descriptor_props_fingerprint=props_hash,
            ),
            readiness=_readiness_mode(entry),
            payload=entry,
        )

    async def reconcile(
        self,
        registry: BundlesRegistry,
        *,
        force: Iterable[str] | None = None,
    ) -> None:
        """Publish desired state and start, replace, or retain per-app tasks."""
        async with self._reconcile_lock:
            self._last_registry = registry
            entries = [
                entry
                for application_id, entry in (registry.bundles or {}).items()
                if application_id != ADMIN_BUNDLE_ID
            ]
            built = await asyncio.gather(
                *(self._build_preparation(entry) for entry in entries),
            )
            preparations = {item.application_id: item for item in built}
            await self.supervisor.reconcile(
                preparations,
                force={str(value).strip() for value in (force or ()) if str(value).strip()},
            )

    async def retire(self, application_id: str, registry: BundlesRegistry) -> None:
        """Retire one application while retaining all sibling lifecycle tasks."""
        async with self._reconcile_lock:
            self._last_registry = registry
            await self.supervisor.retire(application_id)

    async def deprovision(
        self,
        entry: BundleEntry | dict[str, Any],
        *,
        operation_id: str,
        purge_data: bool,
        quiesce_runtime: Callable[[], Awaitable[dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        """Close one app locally, then run its operation-coordinated cleanup hook."""
        bundle_entry = (
            entry if isinstance(entry, BundleEntry) else BundleEntry.model_validate(entry)
        )
        application_id = str(bundle_entry.id or "").strip()
        async with self._reconcile_lock:
            snapshot = self.registry.snapshot(
                tenant=self.tenant,
                project=self.project,
                application_id=application_id,
            )
            if snapshot is None:
                raise KeyError(f"Application {application_id!r} has no active lifecycle state")
            generation = snapshot.desired_generation
            self.registry.transition(
                tenant=self.tenant,
                project=self.project,
                application_id=application_id,
                generation=generation,
                state=ApplicationLifecycleState.DEPROVISIONING,
            )
            await self.supervisor.quiesce(application_id)

            try:
                quiesce_result = (
                    await quiesce_runtime() if quiesce_runtime is not None else {}
                )
                source_value = str(bundle_entry.path or "").strip()
                source_path = Path(source_value) if source_value else None
                if source_path is None or not source_path.exists():
                    raise RuntimeError(
                        f"Installed application source is unavailable: "
                        f"application={application_id} path={source_value!r}"
                    )
                bundle_spec = bundle_entry_to_spec(bundle_entry)
                agentic_spec = BundleSpec(
                    id=application_id,
                    path=bundle_entry.path,
                    module=bundle_entry.module,
                    singleton=bool(bundle_entry.singleton),
                )
                workflow, module = await load_bundle_for_deprovision_async(
                    agentic_spec,
                    bundle_spec,
                    tenant=self.tenant,
                    project=self.project,
                    pg_pool=self.pg_pool,
                    redis=self.redis,
                )
                result = await deprovision_loaded_bundle_app_resources(
                    workflow=workflow,
                    module=module,
                    agentic_spec=agentic_spec,
                    bundle_spec=bundle_spec,
                    tenant=self.tenant,
                    project=self.project,
                    operation_id=operation_id,
                    purge_data=purge_data,
                    pg_pool=self.pg_pool,
                    redis=self.redis,
                )
            except Exception as exc:
                self.registry.transition(
                    tenant=self.tenant,
                    project=self.project,
                    application_id=application_id,
                    generation=generation,
                    state=ApplicationLifecycleState.DEPROVISION_FAILED,
                    error=exc,
                )
                raise

            return {**result, "quiesce": quiesce_result}

    async def retry(self, application_id: str) -> None:
        if self._last_registry is None:
            raise RuntimeError("Application lifecycle has not received a registry snapshot")
        normalized_id = str(application_id or "").strip()
        if normalized_id not in (self._last_registry.bundles or {}):
            raise KeyError(f"Application {normalized_id!r} is not configured")
        await self.reconcile(self._last_registry, force={normalized_id})

    async def _resolve_entry(self, entry: BundleEntry) -> BundleEntry:
        resolved = await resolve_git_bundle_entry_async(
            entry.id,
            entry.model_dump(mode="python", exclude_none=True),
            source="application.preparation",
        )
        resolved_entry = BundleEntry.model_validate(resolved)
        await upsert_bundles_async(
            {entry.id: resolved_entry.model_dump(mode="python", exclude_none=True)},
            None,
            resolve_git=False,
            source="application.preparation.resolved",
        )
        return resolved_entry

    async def _prepare(self, preparation: ApplicationPreparation) -> None:
        entry = preparation.payload
        if not isinstance(entry, BundleEntry):
            entry = BundleEntry.model_validate(entry)
        resolved_entry = await self._resolve_entry(entry)
        if not str(resolved_entry.path or "").strip() or not Path(resolved_entry.path).exists():
            raise RuntimeError(
                f"Application source is not materialized: application={resolved_entry.id} "
                f"path={resolved_entry.path!r}"
            )

        bundle_spec = bundle_entry_to_spec(resolved_entry)
        spec = BundleSpec(
            id=resolved_entry.id,
            path=resolved_entry.path,
            module=resolved_entry.module,
            singleton=bool(resolved_entry.singleton),
        )
        workflow, module = await preload_bundle_async(
            spec,
            bundle_spec,
            tenant=self.tenant,
            project=self.project,
            pg_pool=self.pg_pool,
            redis=self.redis,
        )
        await validate_prepared_application_manifest(
            application_id=resolved_entry.id,
            spec=spec,
            tenant=self.tenant,
            project=self.project,
            logger=self.logger,
        )
        await deploy_loaded_bundle_app_resources(
            workflow=workflow,
            module=module,
            agentic_spec=spec,
            bundle_spec=bundle_spec,
            tenant=self.tenant,
            project=self.project,
            pg_pool=self.pg_pool,
            redis=self.redis,
        )

    async def wait_for_current(self) -> None:
        await self.supervisor.wait_for_current()

    async def shutdown(self) -> None:
        await self.supervisor.shutdown()
