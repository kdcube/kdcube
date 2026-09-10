# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import copy
import inspect
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from kdcube_ai_app.apps.chat.proc.app_deployment.coordinator import (
    apply_effective_props,
    select_hook_kwargs,
    source_generation_for_spec,
)
from kdcube_ai_app.apps.chat.proc.app_deployment.storage import (
    resolve_app_storage_root,
)
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.infra import namespaces
from kdcube_ai_app.infra.plugin.bundle_loader import BundleSpec
from kdcube_ai_app.infra.plugin.bundle_store import get_bundle_props_from_authority
from kdcube_ai_app.infra.service_hub.inventory import AgentLogger
from kdcube_ai_app.storage.observed_file_locks import make_lock_metadata
from kdcube_ai_app.storage.observed_redis_locks import observed_redis_lock_async


DEPROVISION_RESULT_TTL_SECONDS = 7 * 24 * 60 * 60


class AppDeprovisionError(RuntimeError):
    """The app-owned deprovision phase did not complete."""

    def __init__(self, message: str, *, operation_id: str, bundle_id: str) -> None:
        self.operation_id = operation_id
        self.bundle_id = bundle_id
        super().__init__(message)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _deprovision_lock_key(*, tenant: str, project: str, bundle_id: str) -> str:
    return namespaces.CONFIG.BUNDLES.DEPROVISION_LOCK_FMT.format(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
    )


def _deprovision_result_key(
    *,
    tenant: str,
    project: str,
    bundle_id: str,
    operation_id: str,
) -> str:
    return namespaces.CONFIG.BUNDLES.DEPROVISION_RESULT_FMT.format(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
        operation_id=operation_id,
    )


def _decode_result(raw: Any) -> dict[str, Any] | None:
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        value = json.loads(raw) if isinstance(raw, str) else raw
        return dict(value) if isinstance(value, dict) else None
    except (TypeError, ValueError, UnicodeDecodeError):
        return None


async def _read_result(redis: Any, key: str) -> dict[str, Any] | None:
    return _decode_result(await redis.get(key))


async def _write_result(redis: Any, key: str, result: dict[str, Any]) -> None:
    await redis.set(
        key,
        json.dumps(result, ensure_ascii=True, sort_keys=True),
        ex=DEPROVISION_RESULT_TTL_SECONDS,
    )


def _completed_result(
    result: dict[str, Any] | None,
    *,
    bundle_id: str,
    operation_id: str,
    purge_data: bool,
) -> dict[str, Any] | None:
    if not result or result.get("status") != "ok":
        return None
    if str(result.get("bundle_id") or "") != bundle_id:
        raise ValueError(f"Deprovision operation {operation_id!r} belongs to another bundle")
    if bool(result.get("purge_data")) != purge_data:
        raise ValueError(
            f"Deprovision operation {operation_id!r} was created with a different purge-data policy"
        )
    replay = copy.deepcopy(result)
    replay["replayed"] = True
    return replay


def _find_hook(workflow: Any, module: Any) -> Any:
    hook = getattr(workflow, "on_app_deprovision", None)
    if not callable(hook):
        hook = getattr(module, "on_app_deprovision", None)
    return hook if callable(hook) else None


async def deprovision_loaded_bundle_app_resources(
    *,
    workflow: Any,
    module: Any,
    agentic_spec: BundleSpec,
    bundle_spec: Any,
    tenant: str,
    project: str,
    operation_id: str,
    purge_data: bool,
    pg_pool: Any = None,
    redis: Any,
) -> dict[str, Any]:
    """Run one app generation's optional cleanup hook with operation replay."""
    bundle_id = str(getattr(bundle_spec, "id", None) or agentic_spec.id).strip()
    normalized_operation_id = str(operation_id or "").strip()
    if not bundle_id:
        raise ValueError("bundle_id is required for app deprovision")
    if not normalized_operation_id:
        raise ValueError("operation_id is required for app deprovision")
    if redis is None:
        raise RuntimeError("Redis is required for coordinated app deprovision")

    result_key = _deprovision_result_key(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
        operation_id=normalized_operation_id,
    )
    completed = _completed_result(
        await _read_result(redis, result_key),
        bundle_id=bundle_id,
        operation_id=normalized_operation_id,
        purge_data=purge_data,
    )
    if completed is not None:
        return completed

    settings = get_settings()
    lock_ttl = max(
        900,
        int(settings.PLATFORM.APPLICATIONS.BUNDLES_PRELOAD_LOCK_TTL_SECONDS),
        int(settings.PLATFORM.APPLICATIONS.BUNDLES_PRELOAD_BUNDLE_LOCK_TTL_SECONDS),
    )
    lock_key = _deprovision_lock_key(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
    )
    owner_token = uuid.uuid4().hex
    metadata = make_lock_metadata(
        resource_id=f"{tenant}/{project}/{bundle_id}",
        operation="app-deprovision",
        owner_token=owner_token,
        extra={"bundle_id": bundle_id, "operation_id": normalized_operation_id},
    )
    logger = AgentLogger(
        "bundle.on_app_deprovision",
        getattr(getattr(workflow, "config", None), "log_level", "INFO"),
    )

    async with observed_redis_lock_async(
        client=redis,
        key=lock_key,
        metadata=metadata,
        ttl_seconds=lock_ttl,
        wait_seconds=lock_ttl,
        poll_seconds=0.25,
    ):
        completed = _completed_result(
            await _read_result(redis, result_key),
            bundle_id=bundle_id,
            operation_id=normalized_operation_id,
            purge_data=purge_data,
        )
        if completed is not None:
            return completed

        hook = _find_hook(workflow, module)
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
            ensure=False,
        )
        started_at = _utc_iso()
        logger.log(
            f"[bundle.deprovision] start: bundle={bundle_id} operation={normalized_operation_id} "
            f"purge_data={bool(purge_data)}",
            level="INFO",
        )
        try:
            if hook is not None:
                if not inspect.iscoroutinefunction(hook):
                    raise TypeError("bundle on_app_deprovision hook must be declared with async def")
                kwargs = {
                    "bundle_spec": bundle_spec,
                    "agentic_spec": agentic_spec,
                    "storage_root": storage_root,
                    "tenant": tenant,
                    "project": project,
                    "props": copy.deepcopy(effective_props),
                    "pg_pool": pg_pool,
                    "redis": redis,
                    "operation_id": normalized_operation_id,
                    "purge_data": bool(purge_data),
                    "logger": logger,
                }
                await hook(**select_hook_kwargs(hook, kwargs))
        except Exception as exc:
            failed = {
                "status": "error",
                "bundle_id": bundle_id,
                "operation_id": normalized_operation_id,
                "purge_data": bool(purge_data),
                "hook_present": hook is not None,
                "source_generation": source_generation_for_spec(bundle_spec),
                "started_at": started_at,
                "completed_at": _utc_iso(),
                "error_type": type(exc).__name__,
                "error": str(exc)[:1000],
            }
            await _write_result(redis, result_key, failed)
            logger.log(
                f"[bundle.deprovision] failed: bundle={bundle_id} "
                f"operation={normalized_operation_id} error={type(exc).__name__}: {exc}",
                level="ERROR",
            )
            raise AppDeprovisionError(
                f"App deprovision hook failed for {bundle_id!r}: {exc}",
                operation_id=normalized_operation_id,
                bundle_id=bundle_id,
            ) from exc

        result = {
            "status": "ok",
            "bundle_id": bundle_id,
            "operation_id": normalized_operation_id,
            "purge_data": bool(purge_data),
            "hook_present": hook is not None,
            "source_generation": source_generation_for_spec(bundle_spec),
            "started_at": started_at,
            "completed_at": _utc_iso(),
            "replayed": False,
        }
        await _write_result(redis, result_key, result)
        logger.log(
            f"[bundle.deprovision] done: bundle={bundle_id} operation={normalized_operation_id} "
            f"hook_present={hook is not None}",
            level="INFO",
        )
        return result
