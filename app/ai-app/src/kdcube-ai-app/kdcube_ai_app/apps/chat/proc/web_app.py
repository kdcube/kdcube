# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

# chat/proc/web_app.py
"""
Processor service: runs the queue processor and exposes minimal integrations API.
"""
import asyncio
import faulthandler
import logging
import os
import signal
import sys
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv, find_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders

# Default component identity for shared .env usage
os.environ.setdefault("GATEWAY_COMPONENT", "proc")

_ENV_DIR = Path(__file__).resolve().parent
_CONFIG_DIR = os.environ.get("KDCUBE_CONFIG_DIR")
_IN_CONTAINER = Path("/.dockerenv").exists()

if _CONFIG_DIR:
    _CONFIG_ENV = Path(_CONFIG_DIR) / ".env.proc"
    if _CONFIG_ENV.exists():
        load_dotenv(_CONFIG_ENV, override=True)
elif not _IN_CONTAINER:
    # Local dev only (avoid overriding compose envs in containers).
    load_dotenv(_ENV_DIR / ".env.proc", override=True)

if not _IN_CONTAINER:
    load_dotenv(find_dotenv(usecwd=False))

# Ensure per-replica instance id is set (do not override explicit env)
os.environ.setdefault("INSTANCE_ID", f"proc-{uuid.uuid4().hex[:8]}")

from kdcube_ai_app.apps.utils.cors import configure_cors
from kdcube_ai_app.apps.middleware.gateway import (
    STATE_FLAG,
    STATE_AUTH_MODE,
    STATE_SESSION,
    STATE_USER_TYPE,
    bind_stream_id_to_request_state,
)
from kdcube_ai_app.apps.middleware.token_extract import extract_auth_tokens_from_query_params
from kdcube_ai_app.infra.gateway.config import (
    get_gateway_config,
    apply_gateway_config_from_cache,
    apply_gateway_config_from_env,
    should_force_gateway_config_from_env,
    subscribe_gateway_config_updates,
    GatewayConfigFactory,
    gateway_config_cache_key,
)
from kdcube_ai_app.infra.gateway.backpressure import create_atomic_chat_queue_manager
from kdcube_ai_app.infra.plugin.bundle_loader import BundleSpec
from kdcube_ai_app.infra.rendering.link_preview import close_shared_link_preview
from kdcube_ai_app.infra.rendering.shared_browser import close_shared_browser

from kdcube_ai_app.apps.chat.emitters import ChatRelayCommunicator
from kdcube_ai_app.apps.chat.ingress.chat_submitter import ChatIngressSubmitter
from kdcube_ai_app.apps.chat.processor import prefetch_git_bundles
from kdcube_ai_app.apps.chat.ingress.resolvers import (
    get_fastapi_adapter,
    get_fast_api_accounting_binder,
    INSTANCE_ID,
    CHAT_APP_PORT,
    CHAT_PROCESSOR_PORT,
    REDIS_URL,
    get_pg_pool,
    get_conversation_system,
    get_shared_async_redis_client,
    close_redis_clients,
    get_redis_monitor_instance,
    get_heartbeats_mgr_and_middleware,
    get_external_request_processor,
    service_health_checker,
)
from kdcube_ai_app.apps.chat.sdk.config import get_settings, log_secret_statuses
from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import EconomicsLimitException
from kdcube_ai_app.apps.chat.sdk.runtime.local_sidecars import shutdown_all_local_sidecars
from kdcube_ai_app.apps.chat.proc.rest.integrations import mount_integrations_routers
from kdcube_ai_app.infra.namespaces import CONFIG
from kdcube_ai_app.infra.plugin.bundle_store import (
    _get_bundle_props_from_authority,
    bundle_entry_to_spec,
    load_registry as load_bundle_runtime_registry,
    resolve_bundle_spec_from_store,
)
from kdcube_ai_app.infra.availability.shutdown_diagnostics import (
    install_uvicorn_shutdown_diagnostics,
    log_shutdown_diagnostics,
)

# Ensure per-replica instance id is set (do not override explicit env)
os.environ.setdefault("INSTANCE_ID", f"proc-{uuid.uuid4().hex[:8]}")

import kdcube_ai_app.apps.utils.logging_config as logging_config
logging_config.configure_logging()
get_settings.cache_clear()
log_secret_statuses(force=True)
logger = logging.getLogger("ChatProc.WebApp")


def _is_bundle_mcp_route(path: str) -> bool:
    if not path.startswith("/api/integrations/bundles/"):
        return False
    return "/mcp/" in path


def _install_crash_logging() -> None:
    try:
        faulthandler.enable(all_threads=True)
    except Exception:
        logger.warning("Failed to enable faulthandler", exc_info=True)

    # Do NOT register SIGTERM here.
    # Uvicorn / the process supervisor should own SIGTERM for graceful shutdown.
    for sig in (signal.SIGABRT, signal.SIGSEGV):
        try:
            faulthandler.register(sig, all_threads=True)
        except Exception:
            # Some signals may not be supported on all platforms.
            pass

    def _excepthook(exc_type, exc, tb):
        logger.critical("Unhandled exception", exc_info=(exc_type, exc, tb))

    sys.excepthook = _excepthook


_install_crash_logging()


def _get_uvicorn_workers_from_config() -> int:
    try:
        cfg = GatewayConfigFactory.create_from_env()
        return max(1, int(cfg.service_capacity.processes_per_instance))
    except Exception:
        logger.exception("Failed to resolve Uvicorn workers from gateway config; using 1")
        return 1


def _get_uvicorn_worker_healthcheck_timeout() -> int:
    """
    Maximum seconds Uvicorn waits for a worker to answer its startup healthcheck.

    Default to something much larger than Uvicorn's default because this app
    intentionally performs heavy per-worker lifespan initialization.
    """
    try:
        return max(5, int(os.getenv("UVICORN_TIMEOUT_WORKER_HEALTHCHECK", "60")))
    except Exception:
        logger.exception(
            "Invalid UVICORN_TIMEOUT_WORKER_HEALTHCHECK value; falling back to 60"
        )
        return 60


def _uvicorn_run_supports_timeout_worker_healthcheck(uvicorn_module) -> bool:
    try:
        import inspect
        return "timeout_worker_healthcheck" in inspect.signature(uvicorn_module.run).parameters
    except Exception:
        logger.exception("Failed to inspect uvicorn.run signature")
        return False


def _get_proc_container_stop_timeout_sec() -> int:
    """
    Effective task/container stop window from deployment configuration.

    This must match the ECS task definition stopTimeout. Task scale-in
    protection only delays selection for stop; once ECS starts shutdown, this
    window is still the hard upper bound.
    """
    try:
        return max(15, int(os.getenv("PROC_CONTAINER_STOP_TIMEOUT_SEC", "120")))
    except Exception:
        logger.exception(
            "Invalid PROC_CONTAINER_STOP_TIMEOUT_SEC value; falling back to 120"
        )
        return 120


def _get_proc_graceful_shutdown_timeout_sec() -> int:
    """
    Uvicorn graceful shutdown budget.

    By default we keep a small buffer below the task/container stop window so
    the app shutdown path finishes before ECS hard-stops the container. Set
    PROC_UVICORN_GRACEFUL_SHUTDOWN_TIMEOUT_SEC explicitly only if deployment
    stopTimeout has been raised accordingly.
    """
    container_stop_timeout = _get_proc_container_stop_timeout_sec()
    try:
        configured = os.getenv("PROC_UVICORN_GRACEFUL_SHUTDOWN_TIMEOUT_SEC")
        timeout = int(configured) if configured is not None else max(5, container_stop_timeout - 10)
    except Exception:
        logger.exception(
            "Invalid PROC_UVICORN_GRACEFUL_SHUTDOWN_TIMEOUT_SEC value; "
            "deriving from container stop timeout=%s",
            container_stop_timeout,
        )
        timeout = max(5, container_stop_timeout - 10)

    if timeout >= container_stop_timeout:
        adjusted = max(5, container_stop_timeout - 10)
        logger.warning(
            "PROC_UVICORN_GRACEFUL_SHUTDOWN_TIMEOUT_SEC=%s must stay below "
            "PROC_CONTAINER_STOP_TIMEOUT_SEC=%s; using %s",
            timeout,
            container_stop_timeout,
            adjusted,
        )
        return adjusted
    return max(5, timeout)


PROC_UVICORN_GRACEFUL_SHUTDOWN_TIMEOUT_SEC = _get_proc_graceful_shutdown_timeout_sec()


def _git_prefetch_enabled() -> bool:
    return get_settings().PLATFORM.APPLICATIONS.GIT.BUNDLE_GIT_PREFETCH_ENABLED


def _git_resolution_enabled() -> bool:
    return get_settings().PLATFORM.APPLICATIONS.GIT.BUNDLE_GIT_RESOLUTION_ENABLED


def _bundles_preload_enabled() -> bool:
    return bool(get_settings().PLATFORM.APPLICATIONS.BUNDLES_PRELOAD_ON_START)


def _bundle_preload_lock_ttl_seconds() -> int:
    return int(get_settings().PLATFORM.APPLICATIONS.BUNDLES_PRELOAD_LOCK_TTL_SECONDS)


def _is_explicitly_disabled(value) -> bool:
    if isinstance(value, bool):
        return not value
    if isinstance(value, str):
        return value.strip().lower() in {"0", "false", "no", "off", "disabled"}
    return False


def _enabled_configured_widget_aliases_from_props(props: dict | None) -> list[str]:
    if not isinstance(props, dict):
        return []
    ui = props.get("ui")
    if not isinstance(ui, dict):
        return []
    widgets = ui.get("widgets")
    if not isinstance(widgets, dict):
        return []

    aliases: set[str] = set()
    for alias, cfg in widgets.items():
        alias_s = str(alias or "").strip()
        if not alias_s:
            continue
        if isinstance(cfg, dict):
            if _is_explicitly_disabled(cfg.get("enabled", True)):
                continue
        elif _is_explicitly_disabled(cfg):
            continue
        aliases.add(alias_s)
    return sorted(aliases)


def _load_authoritative_bundle_props_for_preload(*, tenant: str, project: str, bundle_id: str) -> dict:
    try:
        props = _get_bundle_props_from_authority(
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
        )
        return dict(props or {})
    except Exception as e:
        raise RuntimeError(
            "Failed to load authoritative bundle props during preload: "
            f"tenant={tenant} project={project} bundle={bundle_id}"
        ) from e


def _validate_preloaded_bundle_manifest(
    *,
    bundle_id: str,
    spec: BundleSpec,
    tenant: str,
    project: str,
) -> None:
    """
    Verify that the local worker can discover the bundle surfaces it must serve.

    `@ui_widget`, `@api`, and `@mcp` decorators remain the source of truth.
    Descriptor `ui.widgets` only configures static build/serve behavior
    for a widget alias that the bundle actually declares.
    """
    from kdcube_ai_app.infra.plugin.bundle_loader import (
        evict_bundle_scope,
        load_bundle_manifest,
    )

    props = _load_authoritative_bundle_props_for_preload(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
    )
    expected_static_widgets = _enabled_configured_widget_aliases_from_props(props)
    manifest = load_bundle_manifest(spec, bundle_id=bundle_id)
    discovered_widgets = sorted({item.alias for item in manifest.ui_widgets})
    missing_widgets = sorted(set(expected_static_widgets) - set(discovered_widgets))

    if missing_widgets:
        evicted = evict_bundle_scope(spec, drop_sys_modules=True)
        logger.warning(
            "[Bundles] Manifest/widget mismatch during preload; evicted local "
            "bundle caches and retrying discovery: id=%s path=%s expected_static_widgets=%s "
            "discovered_widgets=%s evicted=%s",
            bundle_id,
            spec.path,
            expected_static_widgets,
            discovered_widgets,
            evicted,
        )
        manifest = load_bundle_manifest(spec, bundle_id=bundle_id)
        discovered_widgets = sorted({item.alias for item in manifest.ui_widgets})
        missing_widgets = sorted(set(expected_static_widgets) - set(discovered_widgets))

    if missing_widgets:
        raise RuntimeError(
            "Configured static widget aliases are not declared with @ui_widget: "
            f"bundle={bundle_id} missing={missing_widgets} "
            f"configured={expected_static_widgets} discovered={discovered_widgets} "
            f"path={spec.path}"
        )

    logger.info(
        "[Bundles] Manifest validated: id=%s path=%s widgets=%s "
        "configured_static_widgets=%s api=%s mcp=%s ui_main=%s on_message=%s "
        "on_job=%s cron=%s",
        bundle_id,
        spec.path,
        discovered_widgets,
        expected_static_widgets,
        [item.alias for item in manifest.api_endpoints],
        [item.alias for item in manifest.mcp_endpoints],
        bool(manifest.ui_main),
        bool(manifest.on_message),
        bool(manifest.on_job),
        [item.alias for item in manifest.scheduled_jobs],
    )


async def _prefetch_git_bundles_loop(app, registry=None) -> None:
    """
    Prefetch git bundles once on startup to gate readiness.
    No retries here; config updates or restarts trigger a new resolution.
    """
    try:
        errors = await prefetch_git_bundles(registry)
        if not errors:
            app.state.bundle_git_ready = True
            app.state.bundle_git_errors = {}
            return
        app.state.bundle_git_ready = False
        app.state.bundle_git_errors = errors
    except Exception as e:
        app.state.bundle_git_ready = False
        app.state.bundle_git_errors = {"_internal": str(e)}


async def _initial_git_bundle_prefetch(app) -> None:
    """
    Resolve git-backed bundles before bundle preload and scheduler startup.

    The registry already points repo-backed bundles at /managed-bundles/... paths.
    If scheduler reconcile runs before those paths exist, manifest loading fails
    on first startup and scheduled jobs are skipped until a later registry update.
    """
    app.state.bundle_git_ready = True
    app.state.bundle_git_errors = {}
    app.state.bundle_git_task = None

    if not (_git_prefetch_enabled() and _git_resolution_enabled()):
        return

    settings = get_settings()
    redis = getattr(app.state, "redis_async", None)
    if redis is None:
        return
    reg_now = await load_bundle_runtime_registry(redis, settings.TENANT, settings.PROJECT)
    if not any(getattr(entry, "repo", None) for entry in (reg_now.bundles or {}).values()):
        return

    app.state.bundle_git_ready = False
    await _prefetch_git_bundles_loop(app, reg_now)

    if app.state.bundle_git_errors:
        logger.warning(
            "[Bundles] Git prefetch completed with failures: %s",
            app.state.bundle_git_errors,
        )
    else:
        logger.info("[Bundles] Git prefetch complete")


async def _preload_bundles_loop(app) -> None:
    """
    Eagerly load all configured bundle modules and run on_bundle_load hooks.
    Runs after git prefetch (modules must exist on disk before import).
    Attempts every loadable configured bundle and records per-bundle failures.
    A broken bundle must not make the whole proc unhealthy.
    Every proc still performs local bundle preload; storage-scoped once locks
    inside bundle UI/index builders prevent duplicate shared build work.
    """
    from kdcube_ai_app.infra.plugin.bundle_loader import preload_bundle_async
    from kdcube_ai_app.infra.plugin.bundle_registry import ADMIN_BUNDLE_ID

    # Git repos must be cloned before we can import Python modules from them.
    git_task = getattr(app.state, "bundle_git_task", None)
    if git_task is not None:
        try:
            await git_task
        except Exception:
            pass  # git errors already logged in _prefetch_git_bundles_loop

    settings = get_settings()
    redis = getattr(app.state, "redis_async", None)
    lock_key = CONFIG.BUNDLES.PRELOAD_LOCK_FMT.format(
        tenant=settings.TENANT,
        project=settings.PROJECT,
    )
    lock_token = f"{INSTANCE_ID}:{uuid.uuid4().hex}"
    lock_acquired = False

    if redis is not None:
        try:
            lock_acquired = bool(
                await redis.set(
                    lock_key,
                    lock_token,
                    ex=_bundle_preload_lock_ttl_seconds(),
                    nx=True,
                )
            )
        except Exception:
            logger.exception("[Bundles] Failed to acquire preload lock %s", lock_key)
            lock_acquired = False
        if not lock_acquired:
            logger.info(
                "[Bundles] Preload lock held by another instance; continuing local preload: %s",
                lock_key,
            )
    else:
        logger.info("[Bundles] Redis not configured; running preload without lock")

    reg = await load_bundle_runtime_registry(redis, settings.TENANT, settings.PROJECT) if redis is not None else None
    registry_bundles = (reg.bundles if reg is not None else {}) or {}
    logger.info(
        "[Bundles] Preload registry snapshot: total=%s default=%s ids=%s",
        len(registry_bundles),
        getattr(reg, "default_bundle_id", None),
        sorted(registry_bundles.keys()),
    )
    total = 0
    ok = 0
    errors: dict[str, str] = {}
    try:
        for bid, entry in registry_bundles.items():
            if bid == ADMIN_BUNDLE_ID:
                continue
            path = (entry.path or "").strip()
            if not path:
                logger.warning("[Bundles] Preload skip (no path): id=%s", bid)
                continue
            total += 1
            spec = BundleSpec(
                path=path,
                module=entry.module,
                singleton=bool(entry.singleton),
            )
            bundle_spec = bundle_entry_to_spec(entry)
            try:
                await preload_bundle_async(
                    spec,
                    bundle_spec,
                    tenant=settings.TENANT,
                    project=settings.PROJECT,
                    pg_pool=app.state.pg_pool,
                    redis=app.state.redis_async,
                )
                _validate_preloaded_bundle_manifest(
                    bundle_id=bid,
                    spec=spec,
                    tenant=settings.TENANT,
                    project=settings.PROJECT,
                )
                ok += 1
                logger.info("[Bundles] Preloaded: id=%s path=%s", bid, path)
            except Exception as e:
                errors[bid] = str(e)
                logger.exception("[Bundles] Preload failed: id=%s", bid)

        logger.info(
            "[Bundles] Preload complete: total=%s ok=%s failed=%s",
            total, ok, len(errors),
        )
        app.state.bundles_preload_ready = True
        app.state.bundles_preload_errors = errors
    finally:
        if lock_acquired and redis is not None:
            try:
                current_val = await redis.get(lock_key)
                if isinstance(current_val, bytes):
                    current_val = current_val.decode("utf-8", "ignore")
                if current_val == lock_token:
                    await redis.delete(lock_key)
            except Exception:
                logger.exception("[Bundles] Failed to release preload lock %s", lock_key)


async def _safe_shutdown_step(name: str, coro, timeout: float = 5.0) -> None:
    try:
        await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("Shutdown step timed out: %s (>%ss)", name, timeout)
    except Exception:
        logger.exception("Shutdown step failed: %s", name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "Proc lifespan startup begin: port=%s pid=%s workers_cfg=%s reload_env=%s",
        CHAT_PROCESSOR_PORT,
        os.getpid(),
        _get_uvicorn_workers_from_config(),
        get_settings().PLATFORM.SERVICE.UVICORN_RELOAD,
    )
    app.state.shutting_down = False
    app.state.draining = False

    try:
        # Gateway adapter (used by integrations auth)
        app.state.gateway_adapter = get_fastapi_adapter()
        settings = get_settings()
        if should_force_gateway_config_from_env():
            await apply_gateway_config_from_env(
                gateway_adapter=app.state.gateway_adapter,
                tenant=settings.TENANT,
                project=settings.PROJECT,
                redis_url=REDIS_URL,
            )
            app.state.gateway_config_source = "env (forced)"
            logger.info(
                "Gateway config source: env (forced) tenant=%s project=%s",
                settings.TENANT,
                settings.PROJECT,
            )
        else:
            cache_applied = await apply_gateway_config_from_cache(
                gateway_adapter=app.state.gateway_adapter,
                tenant=settings.TENANT,
                project=settings.PROJECT,
                redis_url=REDIS_URL,
            )
            if cache_applied:
                app.state.gateway_config_source = "redis-cache"
                logger.info(
                    "Gateway config source: redis-cache tenant=%s project=%s key=%s",
                    settings.TENANT,
                    settings.PROJECT,
                    gateway_config_cache_key(tenant=settings.TENANT, project=settings.PROJECT),
                )
            else:
                source = "env"
                if os.getenv("GATEWAY_CONFIG_JSON"):
                    source = "env (GATEWAY_CONFIG_JSON)"
                app.state.gateway_config_source = source
                logger.info(
                    "Gateway config source: %s tenant=%s project=%s",
                    source,
                    settings.TENANT,
                    settings.PROJECT,
                )
        # Redis clients may be touched while loading gateway config from cache.
        # Reset them here so the steady-state pool is recreated from the final config.
        await close_redis_clients()
        app.state.gateway_config_stop = asyncio.Event()
        app.state.gateway_config_task = asyncio.create_task(
            subscribe_gateway_config_updates(
                gateway_adapter=app.state.gateway_adapter,
                tenant=settings.TENANT,
                project=settings.PROJECT,
                redis_url=REDIS_URL,
                stop_event=app.state.gateway_config_stop,
            )
        )
        gateway_config = get_gateway_config()
        app.state.chat_queue_manager = create_atomic_chat_queue_manager(
            gateway_config.redis_url,
            gateway_config,
            app.state.gateway_adapter.gateway.throttling_monitor,
        )
        app.state.acc_binder = get_fast_api_accounting_binder()
    except Exception:
        logger.exception("Proc lifespan startup failed during gateway initialization")
        raise

    # Shared Redis pools + monitor
    try:
        app.state.redis_async = get_shared_async_redis_client()
        app.state.redis_async_decode = None
        app.state.redis_sync = None
        logger.info("Redis pool ready (shared async only)")
    except Exception:
        logger.exception("Failed to initialize shared async Redis pool")
        raise
    try:
        app.state.redis_monitor = await get_redis_monitor_instance()
    except Exception:
        logger.exception("Failed to start Redis connection monitor")
        raise

    # Relay communicator (processor publishes through it)
    app.state.chat_comm = ChatRelayCommunicator(redis_url=REDIS_URL, channel="chat.events")

    # Postgres
    app.state.pg_pool = await get_pg_pool()

    try:
        from kdcube_ai_app.apps.middleware.economics_role import EconomicsRoleResolver
        app.state.gateway_adapter.set_econ_role_resolver(
            EconomicsRoleResolver(
                pg_pool=app.state.pg_pool,
                tenant=settings.TENANT,
                project=settings.PROJECT,
            ).resolve_role_for_user_id
        )
    except Exception as e:
        logger.warning("Failed to attach economics role resolver: %s", e)

    port = CHAT_PROCESSOR_PORT

    async def agentic_app_func(comm_context: "ChatTaskPayload"):
        """
        Entry-point invoked by the processor.
        """
        import inspect
        from kdcube_ai_app.infra.plugin.bundle_loader import (
            discover_bundle_interface_manifest,
            get_workflow_instance_async,
        )
        from kdcube_ai_app.infra.jobs.stream import BACKGROUND_JOB_OPERATION
        from kdcube_ai_app.infra.service_hub.inventory import ConfigRequest, create_workflow_config

        cfg_req = ConfigRequest(**(comm_context.config.values or {}))
        wf_config = create_workflow_config(cfg_req)
        bundle_id = comm_context.routing.bundle_id
        tenant_id = getattr(getattr(comm_context, "actor", None), "tenant_id", None) or get_settings().TENANT
        project_id = getattr(getattr(comm_context, "actor", None), "project_id", None) or get_settings().PROJECT
        spec_resolved = await resolve_bundle_spec_from_store(
            app.state.redis_async,
            tenant=tenant_id,
            project=project_id,
            bundle_id=bundle_id,
        )
        if not spec_resolved:
            raise HTTPException(status_code=404, detail=f"Bundle {bundle_id} not found")

        wf_config.ai_bundle_spec = spec_resolved
        spec = BundleSpec(
            path=spec_resolved.path,
            module=spec_resolved.module,
            singleton=bool(spec_resolved.singleton),
        )
        try:
            workflow, _ = await get_workflow_instance_async(
                spec=spec,
                config=wf_config,
                comm_context=comm_context,
                pg_pool=app.state.pg_pool,
                redis=app.state.redis_async,
            )
        except Exception as e:
            logger.exception(
                "Failed to load requested bundle: bundle=%s tenant=%s project=%s",
                getattr(spec_resolved, "id", bundle_id),
                tenant_id,
                project_id,
            )
            raise RuntimeError(
                f"Bundle {getattr(spec_resolved, 'id', bundle_id)} failed to load: "
                f"{type(e).__name__}: {e}"
            ) from e

        state = {
            "request_id": comm_context.request.request_id,
            "tenant": comm_context.actor.tenant_id,
            "project": comm_context.actor.project_id,
            "user": comm_context.user.user_id,
            "user_type": comm_context.user.user_type,
            "session_id": comm_context.routing.session_id,
            "conversation_id": (comm_context.routing.conversation_id or comm_context.routing.session_id),
            "text": comm_context.request.message or (comm_context.request.payload or {}).get("text") or "",
            "attachments": (comm_context.request.payload or {}).get("attachments") or [],
            "turn_id": comm_context.routing.turn_id,
            "history": comm_context.request.chat_history or [],
            "final_answer": "",
            "followups": [],
            "step_logs": [],
            "start_time": comm_context.meta.created_at,
        }
        if hasattr(workflow, "set_state"):
            maybe = workflow.set_state(state)
            if inspect.isawaitable(maybe):
                await maybe

        async def _call_turn_completed_hook(
                *,
                status: str,
                result: dict | None = None,
                error: BaseException | None = None,
                reason: str | None = None,
        ) -> None:
            hook = getattr(workflow, "on_turn_completed", None)
            if not callable(hook):
                return
            kwargs = {
                "state": state,
                "result": result,
                "error": error,
                "status": status,
                "reason": reason,
                "comm_context": comm_context,
                "command": command,
            }
            try:
                sig = inspect.signature(hook)
                params_sig = sig.parameters
                accepts_kwargs = any(
                    p.kind == inspect.Parameter.VAR_KEYWORD
                    for p in params_sig.values()
                )
                if not accepts_kwargs:
                    kwargs = {k: v for k, v in kwargs.items() if k in params_sig}
            except Exception:
                pass
            try:
                maybe = hook(**kwargs)
                if inspect.isawaitable(maybe):
                    await asyncio.wait_for(maybe, timeout=10.0)
            except Exception:
                logger.warning("Bundle on_turn_completed hook failed", exc_info=True)

        params = dict(comm_context.request.payload or {})
        if "text" not in params and comm_context.request.message:
            params["text"] = comm_context.request.message
        command = comm_context.request.operation or params.pop("command", None)

        result = None
        hook_status = "completed"
        hook_error = None
        hook_reason = None
        try:
            if command == BACKGROUND_JOB_OPERATION:
                manifest = discover_bundle_interface_manifest(workflow, bundle_id=bundle_id)
                if manifest.on_job is None:
                    raise RuntimeError(f"Bundle {bundle_id!r} does not implement @on_job")
                fn = getattr(workflow, manifest.on_job.method_name)
                if not inspect.iscoroutinefunction(fn):
                    raise RuntimeError(f"Bundle {bundle_id!r} @on_job handler must be async")
                result = await fn(**params)
            else:
                fn = getattr(workflow, command) if (command and hasattr(workflow, command)) else workflow.run
                if inspect.iscoroutinefunction(fn):
                    result = await fn(**params)
                else:
                    result = await asyncio.to_thread(fn, **params)
                    if inspect.isawaitable(result):
                        result = await result
            hook_status = "completed"
            return result or {}
        except asyncio.CancelledError as e:
            hook_status = "cancelled"
            hook_error = e
            hook_reason = "task_cancelled"
            raise
        except Exception as e:
            hook_status = "error"
            hook_error = e
            hook_reason = type(e).__name__
            logger.error(traceback.format_exc())
            if not isinstance(e, EconomicsLimitException):
                result = {"error_message": str(e), "final_answer": "An error occurred."}
                return result
        finally:
            await _call_turn_completed_hook(
                status=hook_status,
                result=result if isinstance(result, dict) else None,
                error=hook_error,
                reason=hook_reason,
            )

    try:
        redis_async = app.state.redis_async
        from kdcube_ai_app.infra.metrics.pool_stats import build_pool_metadata

        def _heartbeat_metadata():
            metadata = build_pool_metadata(
                pg_pool=app.state.pg_pool,
                redis_clients={
                    "async": app.state.redis_async,
                },
            )
            processor = getattr(app.state, "processor", None)
            if processor is not None:
                metadata["processor"] = processor.get_runtime_metadata()
            return metadata

        middleware, heartbeat_manager = get_heartbeats_mgr_and_middleware(
            service_type="chat",
            service_name="proc",
            port=port,
            redis_client=redis_async,
            metadata_provider=_heartbeat_metadata,
        )
        health_checker = service_health_checker(middleware)

        app.state.middleware = middleware
        app.state.heartbeat_manager = heartbeat_manager
        app.state.health_checker = health_checker

        conversation_browser, conversation_index, conversation_store = await get_conversation_system(app.state.pg_pool)
        app.state.conversation_browser = conversation_browser
        app.state.conversation_index = conversation_index
        app.state.conversation_store = conversation_store
        app.state.chat_submitter = ChatIngressSubmitter(app=app)

        processor = get_external_request_processor(middleware, agentic_app_func, app, redis=redis_async)
        app.state.processor = processor
        heartbeat_manager.load_provider = processor.get_current_load

        await heartbeat_manager.start_heartbeat(interval=10)
        try:
            from kdcube_ai_app.infra.plugin.bundle_store import load_registry as _load_store_registry
            from kdcube_ai_app.infra.plugin.bundle_store import force_env_reset_if_requested
            from kdcube_ai_app.infra.plugin.bundle_registry import set_registry as _set_mem_registry
            reg = None
            try:
                reg = await force_env_reset_if_requested(
                    redis_async,
                    tenant=settings.TENANT,
                    project=settings.PROJECT,
                    actor="startup-env",
                )
            except Exception as e:
                logger.warning("Bundle env reset failed; falling back to Redis. %s", e)
                reg = None
            if not reg:
                reg = await _load_store_registry(redis_async)
            bundles_dict = {bid: entry.model_dump() for bid, entry in reg.bundles.items()}
            _set_mem_registry(bundles_dict, reg.default_bundle_id)
            logger.info(
                "Bundles registry loaded from active registry: %s items (default=%s)",
                len(bundles_dict),
                reg.default_bundle_id,
            )
        except Exception as e:
            logger.warning(
                "Failed to load bundles registry from active registry; using env-only registry. %s",
                e,
            )

        # Resolve git-backed bundles before preload/scheduler startup so the
        # registry's /managed-bundles/... paths exist when manifests are loaded.
        await _initial_git_bundle_prefetch(app)

        app.state.bundles_preload_ready = True
        app.state.bundles_preload_errors = {}
        app.state.bundles_preload_task = None
        if _bundles_preload_enabled():
            app.state.bundles_preload_ready = False
            app.state.bundles_preload_task = asyncio.create_task(_preload_bundles_loop(app))

        await processor.start_processing()
    except Exception:
        logger.exception("Could not start processor service")
        raise

    # Bundle cleanup loop (proc only)
    try:
        from kdcube_ai_app.apps.chat.ingress.opex import routines as opex_routines
        app.state.bundle_cleanup_task = asyncio.create_task(opex_routines.bundle_cleanup_loop())
        logger.info("[Bundles] Cleanup task started (proc)")
    except Exception:
        logger.exception("Failed to start bundle cleanup loop")

    yield

    # mark draining so /health returns 503 during graceful shutdown window
    app.state.draining = True
    app.state.shutting_down = True
    if getattr(app.state, "processor", None):
        logger.info(
            "Starting processor drain: metadata=%s",
            app.state.processor.get_runtime_metadata(),
        )
        await app.state.processor.stop_processing()
    if getattr(app.state, "heartbeat_manager", None):
        await _safe_shutdown_step("heartbeat.stop", app.state.heartbeat_manager.stop_heartbeat(), timeout=5.0)
    if getattr(app.state, "gateway_config_stop", None):
        app.state.gateway_config_stop.set()
    if getattr(app.state, "gateway_config_task", None):
        await _safe_shutdown_step("gateway_config_task", app.state.gateway_config_task, timeout=5.0)
    if getattr(app.state, "bundle_cleanup_task", None):
        app.state.bundle_cleanup_task.cancel()
        try:
            await app.state.bundle_cleanup_task
        except asyncio.CancelledError:
            pass
        app.state.bundle_cleanup_task = None
    await _safe_shutdown_step(
        "bundle_sidecars.shutdown",
        shutdown_all_local_sidecars(terminate_timeout_sec=10.0, kill_timeout_sec=3.0),
        timeout=20.0,
    )
    if getattr(app.state, "bundle_git_task", None):
        app.state.bundle_git_task.cancel()
        try:
            await app.state.bundle_git_task
        except asyncio.CancelledError:
            pass
        app.state.bundle_git_task = None
    if getattr(app.state, "bundles_preload_task", None):
        app.state.bundles_preload_task.cancel()
        try:
            await app.state.bundles_preload_task
        except asyncio.CancelledError:
            pass
        app.state.bundles_preload_task = None
    if hasattr(app.state, "redis_monitor"):
        await _safe_shutdown_step("redis_monitor.stop", app.state.redis_monitor.stop(), timeout=5.0)
    if hasattr(app.state, "pg_pool"):
        await _safe_shutdown_step("pg_pool.close", app.state.pg_pool.close(), timeout=10.0)
    if hasattr(app.state, "redis_async"):
        await _safe_shutdown_step("redis_clients.close", close_redis_clients(), timeout=5.0)
    await close_shared_link_preview()
    await close_shared_browser()
    logger.info("Chat processor service stopped")


app = FastAPI(
    title="Chat Processor Service",
    description="Queue processor + integrations",
    lifespan=lifespan,
)


def _proc_debug_headers() -> dict[str, str]:
    return {
        "X-KDCube-Proc-Instance": str(INSTANCE_ID),
        "X-KDCube-Worker-Pid": str(os.getpid()),
        "X-KDCube-Bundles-Preload-Ready": str(getattr(app.state, "bundles_preload_ready", True)).lower(),
    }


allowed_origins = configure_cors(app)


@app.middleware("http")
async def gateway_middleware(request: Request, call_next):
    if getattr(request.app.state, "draining", False) and not request.url.path.startswith("/health"):
        return JSONResponse(
            status_code=503,
            content={"detail": "Service is draining", "status": "draining"},
        )
    if request.method == "OPTIONS" or request.url.path.startswith(("/health", "/docs", "/openapi.json", "/favicon.ico")):
        return await call_next(request)

    if getattr(request.state, STATE_FLAG, False):
        return await call_next(request)

    try:
        bind_stream_id_to_request_state(request)

        mcp_route = _is_bundle_mcp_route(request.url.path)

        # Inject auth tokens from query params if needed.
        # MCP routes intentionally do not use query-param auth or cookie fallback;
        # they are header-only JWT endpoints.
        if request.url.path.startswith("/api/integrations/") and not mcp_route:
            bearer_token, id_token = extract_auth_tokens_from_query_params(request.query_params)
            user_timezone = request.query_params.get("user_timezone")
            user_utc_offset_min = request.query_params.get("user_utc_offset_min")

            headers = MutableHeaders(scope=request.scope)
            if bearer_token and "authorization" not in {k.lower(): v for k, v in headers.items()}:
                headers["authorization"] = f"Bearer {bearer_token}"
            if id_token:
                headers[get_settings().AUTH.ID_TOKEN_HEADER_NAME] = id_token
            if user_timezone:
                headers[get_settings().RUNTIME_CONFIG.USER_TIMEZONE_HEADER_NAME] = user_timezone
            if user_utc_offset_min:
                headers[get_settings().RUNTIME_CONFIG.USER_UTC_OFFSET_MIN_HEADER_NAME] = user_utc_offset_min

        session = await app.state.gateway_adapter.process_by_policy(
            request,
            header_only_auth=mcp_route,
        )
        setattr(request.state, STATE_SESSION, session)
        setattr(request.state, STATE_USER_TYPE, session.user_type.value)
        setattr(request.state, STATE_FLAG, True)
        setattr(request.state, STATE_AUTH_MODE, "headers_only" if mcp_route else "default")

        response = await call_next(request)
        response.headers["X-User-Type"] = session.user_type.value
        response.headers["X-Session-ID"] = session.session_id
        for key, value in _proc_debug_headers().items():
            response.headers[key] = value
        return response
    except RuntimeError as e:
        if str(e) == "No response returned.":
            logger.warning(
                "Request ended without response: method=%s path=%s pid=%s instance=%s preload_ready=%s",
                request.method,
                request.url.path,
                os.getpid(),
                INSTANCE_ID,
                getattr(app.state, "bundles_preload_ready", True),
            )
            return JSONResponse(
                status_code=499,
                content={"detail": "Client disconnected before response was returned"},
                headers=_proc_debug_headers(),
            )
        raise
    except HTTPException as e:
        headers = {**_proc_debug_headers(), **(getattr(e, "headers", {}) or {})}
        return JSONResponse(
            status_code=e.status_code,
            content=e.detail if isinstance(e.detail, dict) else {"detail": e.detail},
            headers=headers,
        )


@app.get("/health")
async def health():
    draining = getattr(app.state, "draining", False)
    bundles_git_ready = getattr(app.state, "bundle_git_ready", True)
    bundle_git_errors = getattr(app.state, "bundle_git_errors", {}) or {}
    bundles_preload_ready = getattr(app.state, "bundles_preload_ready", True)
    bundles_preload_errors = getattr(app.state, "bundles_preload_errors", {}) or {}
    ready = bundles_git_ready and bundles_preload_ready
    payload = {
        "status": "draining" if draining else ("ok" if ready else "not_ready"),
        "draining": draining,
        "service": "chat-proc",
        "instance_id": INSTANCE_ID,
        "bundles_git_ready": bundles_git_ready,
        "bundles_git_errors": bundle_git_errors,
        "bundles_preload_ready": bundles_preload_ready,
        "bundles_preload_errors": bundles_preload_errors,
    }
    if draining or not ready:
        return JSONResponse(status_code=503, content=payload)
    return payload


@app.get("/monitoring/processor")
async def monitoring_processor():
    processor = getattr(app.state, "processor", None)
    if processor is None:
        return JSONResponse(status_code=503, content={"status": "not_ready", "processor": None})
    return {
        "status": "ok",
        "service": "chat-proc",
        "instance_id": INSTANCE_ID,
        "processor": processor.get_runtime_metadata(),
    }


# Mount integrations API at /api/integrations
mount_integrations_routers(app)


if __name__ == "__main__":
    import uvicorn

    # Enable faulthandler to capture native crashes and dump tracebacks.
    faulthandler.enable()
    install_uvicorn_shutdown_diagnostics(uvicorn, logger, component="chat-proc")

    workers = _get_uvicorn_workers_from_config()
    reload_enabled = get_settings().PLATFORM.SERVICE.UVICORN_RELOAD
    worker_healthcheck_timeout = _get_uvicorn_worker_healthcheck_timeout()

    # Uvicorn requires an import string when using workers or reload.
    use_import_string = workers > 1 or reload_enabled
    app_target = "kdcube_ai_app.apps.chat.proc.web_app:app" if use_import_string else app

    run_kwargs = {
        "host": "0.0.0.0",
        "port": CHAT_PROCESSOR_PORT,
        "log_config": None,
        "log_level": None,
        "timeout_keep_alive": 45,
        "timeout_graceful_shutdown": PROC_UVICORN_GRACEFUL_SHUTDOWN_TIMEOUT_SEC,
    }

    if _uvicorn_run_supports_timeout_worker_healthcheck(uvicorn):
        run_kwargs["timeout_worker_healthcheck"] = worker_healthcheck_timeout
    else:
        logger.warning(
            "Installed uvicorn does not support timeout_worker_healthcheck; "
            "configured value=%s will be ignored",
            worker_healthcheck_timeout,
        )

    if use_import_string:
        run_kwargs["workers"] = workers
        if reload_enabled:
            run_kwargs["reload"] = True

    logger.info(
        "Starting Uvicorn (proc): target=%s workers=%s reload=%s port=%s pid=%s "
        "worker_healthcheck_timeout=%s timeout_supported=%s",
        app_target,
        workers,
        reload_enabled,
        CHAT_PROCESSOR_PORT,
        os.getpid(),
        worker_healthcheck_timeout,
        _uvicorn_run_supports_timeout_worker_healthcheck(uvicorn),
    )
    run_started_at = time.monotonic()
    try:
        uvicorn.run(app_target, **run_kwargs)
    finally:
        elapsed = time.monotonic() - run_started_at
        logger.warning(
            "uvicorn.run returned: component=%s pid=%s elapsed=%.3fs version=%s",
            "chat-proc",
            os.getpid(),
            elapsed,
            getattr(uvicorn, "__version__", "unknown"),
        )
        log_shutdown_diagnostics(
            logger,
            reason=f"chat-proc:uvicorn.run.returned:elapsed={elapsed:.3f}s",
            include_traceback=True,
        )
