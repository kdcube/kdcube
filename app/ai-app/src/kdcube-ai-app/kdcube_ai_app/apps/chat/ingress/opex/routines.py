# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# # kdcube_ai_app/apps/chat/ingress/opex/routines.py

import asyncio
from typing import Optional
import importlib.util
from pathlib import Path
import uuid, os, logging
from datetime import datetime, timedelta, date, timezone
from zoneinfo import ZoneInfo
from croniter import croniter

import redis.asyncio as aioredis

from kdcube_ai_app.apps.chat.sdk.config import get_settings, read_plain
from kdcube_ai_app.infra.accounting.aggregator import AccountingAggregator
from kdcube_ai_app.storage.storage import create_storage_backend
from kdcube_ai_app.infra.redis.client import get_async_redis_client

logger = logging.getLogger("Periodical.Routines")

ACCOUNTING_TZ = ZoneInfo("Europe/Berlin")

_scheduler_task: Optional[asyncio.Task] = None
_aggregator: Optional[AccountingAggregator] = None
_agg_redis: Optional[aioredis.Redis] = None
_idp_import_task: Optional[asyncio.Task] = None

def _get_aggregator() -> AccountingAggregator:
    """
    Lazily create a shared AccountingAggregator instance for the scheduler.
    Uses the same STORAGE_PATH logic as _get_calculator.
    """
    global _aggregator
    if _aggregator is not None:
        return _aggregator

    _settings = get_settings()
    kdcube_path = _settings.STORAGE_PATH or "file:///tmp/kdcube_data"
    backend = create_storage_backend(kdcube_path)
    _aggregator = AccountingAggregator(backend,
                                       raw_base="accounting",
                                       agg_base="analytics",)
    return _aggregator


def _idp_import_enabled() -> bool:
    try:
        return bool(get_settings().IDP_IMPORT_ENABLED)  # type: ignore[attr-defined]
    except Exception:
        return os.environ.get("IDP_IMPORT_ENABLED", "0").lower() in {"1", "true", "yes"}


def _idp_import_run_at() -> Optional[datetime]:
    raw = os.environ.get("IDP_IMPORT_RUN_AT")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except Exception:
        logger.exception("[IDP Import] Invalid IDP_IMPORT_RUN_AT: %s", raw)
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _idp_import_script_path() -> Optional[Path]:
    raw = os.environ.get("IDP_IMPORT_SCRIPT_PATH")
    if not raw:
        return None
    p = Path(raw).expanduser()
    return p if p.exists() else None


async def _run_idp_import_once(*, actor: str = "scheduler") -> dict:
    if not _idp_import_enabled():
        return {"status": "disabled"}

    run_at = _idp_import_run_at()
    if not run_at:
        return {"status": "error", "message": "IDP_IMPORT_RUN_AT not set"}

    script_path = _idp_import_script_path()
    if not script_path:
        return {"status": "error", "message": "IDP_IMPORT_SCRIPT_PATH not found"}

    settings = get_settings()
    tenant = settings.TENANT
    project = settings.PROJECT

    redis = await _get_agg_redis()
    lock_key = f"idp_import:lock:{tenant}:{project}:{run_at.isoformat()}"
    done_key = f"idp_import:done:{tenant}:{project}:{run_at.isoformat()}"
    token = str(uuid.uuid4())

    if redis:
        if await redis.get(done_key):
            logger.info("[IDP Import] Already completed; skipping (%s)", done_key)
            return {"status": "skipped", "message": "already_completed"}

        got_lock = await redis.set(lock_key, token, ex=3600, nx=True)
        if not got_lock:
            logger.info("[IDP Import] Another instance holds lock; skipping")
            return {"status": "skipped", "message": "lock_held"}

    try:
        dotenv_path = os.environ.get("IDP_IMPORT_DOTENV_PATH")
        if dotenv_path:
            try:
                from dotenv import load_dotenv
                load_dotenv(dotenv_path)
            except Exception:
                logger.exception("[IDP Import] Failed to load dotenv at %s", dotenv_path)

        def _run() -> None:
            spec = importlib.util.spec_from_file_location("idp_import_users", str(script_path))
            if not spec or not spec.loader:
                raise RuntimeError(f"Unable to load script: {script_path}")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if not hasattr(mod, "process_users"):
                raise RuntimeError("import_users.py has no process_users()")
            mod.process_users()

        await asyncio.to_thread(_run)
        if redis:
            await redis.set(done_key, datetime.now(timezone.utc).isoformat())
        logger.info("[IDP Import] Completed successfully by %s", actor)
        return {"status": "ok"}
    except Exception as e:
        logger.exception("[IDP Import] Failed")
        return {"status": "error", "message": str(e)}
    finally:
        if redis:
            try:
                current_val = await redis.get(lock_key)
                if current_val is not None and current_val.decode() == token:
                    await redis.delete(lock_key)
            except Exception:
                logger.exception("[IDP Import] Failed to release lock %s", lock_key)


async def idp_import_scheduler_once() -> None:
    """
    One-time scheduler for the IDP import script.
    Controlled by env:
      - IDP_IMPORT_ENABLED=1
      - IDP_IMPORT_RUN_AT=ISO-8601 timestamp (UTC if no tz)
      - IDP_IMPORT_SCRIPT_PATH=/abs/path/to/import_users.py
      - optional IDP_IMPORT_DOTENV_PATH=/abs/path/to/.env
    """
    if not _idp_import_enabled():
        logger.info("[IDP Import] Scheduler disabled (IDP_IMPORT_ENABLED=0)")
        return

    run_at = _idp_import_run_at()
    if not run_at:
        logger.warning("[IDP Import] Missing IDP_IMPORT_RUN_AT; scheduler not started")
        return

    now = datetime.now(timezone.utc)
    if run_at > now:
        sleep_seconds = (run_at - now).total_seconds()
        logger.info(
            "[IDP Import] Sleeping %.0f seconds until %s",
            sleep_seconds,
            run_at.isoformat(),
        )
        try:
            await asyncio.sleep(sleep_seconds)
        except asyncio.CancelledError:
            logger.info("[IDP Import] Scheduler cancelled before run")
            return

    await _run_idp_import_once(actor="scheduler")


async def _get_agg_redis() -> Optional[aioredis.Redis]:
    """
    Redis connection for distributed locks between API instances.

    Configure via REDIS_URL, e.g.:
      REDIS_URL=redis://redis:6379/0
    """
    global _agg_redis
    if _agg_redis is not None:
        return _agg_redis

    _settings = get_settings()
    redis_url = _settings.REDIS_URL
    if not redis_url:
        logger.warning(
            "[OPEX Aggregator] REDIS_URL is not set; "
            "scheduled aggregation will run *without* distributed locking."
        )
        _agg_redis = None
        return None

    _agg_redis = get_async_redis_client(redis_url)
    return _agg_redis

def _get_cron_expression() -> str:
    """
    Return cron expression from assembly.yaml, settings, or env.

    Priority:
      1) assembly.yaml routines.opex.agg_cron
      2) settings.OPEX_AGG_CRON if exists
      3) env OPEX_AGG_CRON
      4) default: "0 3 * * *" (daily at 03:00)
    """
    expr = read_plain("routines.opex.agg_cron", default=None)
    try:
        if not expr:
            _settings = get_settings()
            expr = getattr(_settings, "OPEX_AGG_CRON", None)
    except Exception:
        # if settings don’t have it, silently ignore
        pass

    expr = expr or os.getenv("OPEX_AGG_CRON")
    if not expr:
        expr = "0 3 * * *"
    return expr


def _bundle_cleanup_enabled() -> bool:
    try:
        return bool(get_settings().BUNDLE_CLEANUP_ENABLED)
    except Exception:
        return os.environ.get("BUNDLE_CLEANUP_ENABLED", "1").lower() in {"1", "true", "yes"}

def _bundle_cleanup_interval_seconds() -> int:
    try:
        return int(get_settings().BUNDLE_CLEANUP_INTERVAL_SECONDS)
    except Exception:
        return int(os.environ.get("BUNDLE_CLEANUP_INTERVAL_SECONDS", "3600") or "3600")

def _bundle_cleanup_lock_ttl_seconds() -> int:
    try:
        return int(get_settings().BUNDLE_CLEANUP_LOCK_TTL_SECONDS)
    except Exception:
        return int(os.environ.get("BUNDLE_CLEANUP_LOCK_TTL_SECONDS", "900") or "900")


def _compute_next_run(now: datetime) -> datetime:
    """
    Compute next run datetime in ACCOUNTING_TZ from cron expression.
    Falls back to '0 3 * * *' if the configured cron is invalid.
    """
    expr = _get_cron_expression()
    try:
        it = croniter(expr, now)
        return it.get_next(datetime)
    except Exception:
        logger.exception(
            "[OPEX Aggregator] Invalid cron expression '%s', "
            "falling back to '0 3 * * *'",
            expr,
        )
        it = croniter("0 3 * * *", now)
        return it.get_next(datetime)


async def run_aggregation_range(start: date, end: date) -> None:
    """
    Run daily + monthly aggregation for each date in [start, end], inclusive.

    Uses the same Redis-locking logic as the scheduler, so multiple
    API instances can safely call this at the same time.
    """
    if end < start:
        return

    current = start
    while current <= end:
        await _run_daily_and_monthly_for_date(current)
        current += timedelta(days=1)

async def _run_daily_and_monthly_for_date(run_date: date) -> None:
    """
    Compute daily aggregate for run_date and monthly aggregate for its month.

    Uses a Redis lock so only one instance per (tenant, project, date)
    actually does the work.
    """
    agg = _get_aggregator()
    redis = await _get_agg_redis()

    from kdcube_ai_app.apps.chat.sdk.config import get_settings
    settings = get_settings()
    tenant = settings.TENANT
    project = settings.PROJECT

    lock_key = f"acct:agg:{tenant}:{project}:{run_date.isoformat()}"
    token = str(uuid.uuid4())
    lock_ttl_seconds = 4 * 3600  # 4 hours

    # Acquire distributed lock if Redis is available
    if redis:
        got_lock = await redis.set(lock_key, token, ex=lock_ttl_seconds, nx=True)
        if not got_lock:
            logger.info(
                "[OPEX Aggregator] Another instance holds lock %s, "
                "skipping aggregation for %s/%s on %s",
                lock_key, tenant, project, run_date.isoformat()
            )
            return
    else:
        logger.info(
            "[OPEX Aggregator] Redis not configured, running aggregation for %s/%s "
            "on %s without distributed lock",
            tenant, project, run_date.isoformat()
        )

    try:
        date_str = run_date.isoformat()

        # Daily (only this date, skip_existing=True so reruns are safe)
        logger.info(
            "[OPEX Aggregator] Aggregating daily for %s/%s on %s",
            tenant, project, date_str
        )
        await agg.aggregate_daily_range_for_project(
            tenant_id=tenant,
            project_id=project,
            date_from=date_str,
            date_to=date_str,
            skip_existing=True,
        )

        # Monthly
        logger.info(
            "[OPEX Aggregator] Aggregating monthly for %s/%s year=%d month=%d",
            tenant, project, run_date.year, run_date.month
        )
        await agg.aggregate_monthly_from_daily(
            tenant_id=tenant,
            project_id=project,
            year=run_date.year,
            month=run_date.month,
            require_full_coverage=False,
        )

        logger.info(
            "[OPEX Aggregator] Aggregation done for %s/%s on %s",
            tenant, project, run_date.isoformat()
        )

    except Exception:
        logger.exception(
            "[OPEX Aggregator] Failed to aggregate for %s/%s on %s",
            tenant, project, run_date.isoformat()
        )
    finally:
        if redis:
            try:
                current_val = await redis.get(lock_key)
                if current_val is not None and current_val.decode() == token:
                    await redis.delete(lock_key)
            except Exception:
                logger.exception(
                    "[OPEX Aggregator] Failed to release lock %s", lock_key
                )

async def aggregation_scheduler_loop() -> None:
    """
    Background loop:

      - reads cron from assembly.yaml routines.opex.agg_cron
      - falls back to settings/env only for compatibility
      - waits until the next scheduled time in Europe/Berlin
      - runs aggregation for *yesterday* (Berlin date)
      - repeats forever
    """
    logger.info(
        "[OPEX Aggregator] Scheduler loop started (tz=%s, cron=%s)",
        ACCOUNTING_TZ,
        _get_cron_expression(),
    )

    while True:
        now = datetime.now(ACCOUNTING_TZ)
        next_run = _compute_next_run(now)

        sleep_seconds = (next_run - now).total_seconds()
        logger.info(
            "[OPEX Aggregator] Sleeping %.0f seconds until next run at %s",
            sleep_seconds,
            next_run.isoformat(),
        )

        try:
            await asyncio.sleep(sleep_seconds)
        except asyncio.CancelledError:
            logger.info("[OPEX Aggregator] Scheduler loop cancelled")
            break

        # Semantics: always aggregate "yesterday" in Berlin time.
        run_date = (datetime.now(ACCOUNTING_TZ) - timedelta(days=1)).date()
        logger.info(
            "[OPEX Aggregator] Running scheduled aggregation for date %s (trigger=%s)",
            run_date.isoformat(),
            next_run.isoformat(),
        )
        await _run_daily_and_monthly_for_date(run_date)


async def _run_bundle_cleanup_once() -> None:
    if not _bundle_cleanup_enabled():
        return
    settings = get_settings()
    tenant = settings.TENANT
    project = settings.PROJECT
    redis = await _get_agg_redis()
    lock_key = f"bundles:cleanup:{tenant}:{project}"
    token = str(uuid.uuid4())
    lock_ttl = _bundle_cleanup_lock_ttl_seconds()

    if redis:
        got_lock = await redis.set(lock_key, token, ex=lock_ttl, nx=True)
        if not got_lock:
            logger.info(
                "[Bundles] Cleanup lock held by another instance: %s", lock_key
            )
            return
    else:
        logger.info("[Bundles] Redis not configured; running cleanup without lock")

    try:
        from kdcube_ai_app.infra.plugin.bundle_registry import get_all
        from kdcube_ai_app.infra.plugin.bundle_store import (
            _discover_example_bundle_ids,
            cleanup_old_shared_example_bundles,
        )
        from kdcube_ai_app.infra.plugin.git_bundle import (
            cleanup_old_git_bundles_async,
            resolve_bundles_root,
            resolve_git_bundles_root,
            bundle_dir_for_git,
        )
        from kdcube_ai_app.infra.plugin.bundle_refs import get_active_paths

        active_paths = await get_active_paths(redis, tenant=tenant, project=project)
        total_removed = 0
        bundles = get_all() or {}
        for bid, entry in bundles.items():
            repo = entry.get("repo")
            if not repo:
                continue
            base_dir = bundle_dir_for_git(bid, entry.get("ref"))
            removed = await cleanup_old_git_bundles_async(
                bundle_id=base_dir,
                bundles_root=resolve_git_bundles_root(),
                active_paths=active_paths,
            )
            total_removed += removed
        for bid in _discover_example_bundle_ids():
            total_removed += cleanup_old_shared_example_bundles(
                bundle_id=bid,
                bundles_root=resolve_bundles_root(),
                active_paths=active_paths,
            )
        if total_removed:
            logger.info("[Bundles] Cleanup removed %d old bundle dirs", total_removed)
    except Exception:
        logger.exception("[Bundles] Cleanup failed")
    finally:
        if redis:
            try:
                current_val = await redis.get(lock_key)
                if current_val is not None and current_val.decode() == token:
                    await redis.delete(lock_key)
            except Exception:
                logger.exception("[Bundles] Failed to release cleanup lock %s", lock_key)


async def bundle_cleanup_loop() -> None:
    """
    Periodic cleanup loop for git bundles (safe across workers via Redis lock).
    """
    if not _bundle_cleanup_enabled():
        logger.info("[Bundles] Cleanup loop disabled (BUNDLE_CLEANUP_ENABLED=0)")
        return

    interval = _bundle_cleanup_interval_seconds()
    logger.info("[Bundles] Cleanup loop started (interval=%ss)", interval)
    while True:
        try:
            await _run_bundle_cleanup_once()
        except asyncio.CancelledError:
            logger.info("[Bundles] Cleanup loop cancelled")
            break
        except Exception:
            logger.exception("[Bundles] Cleanup loop error")
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("[Bundles] Cleanup loop cancelled")
            break
