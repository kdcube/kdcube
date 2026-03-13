# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

# kdcube_ai_app/apps/chat/api/economics/routines.py

import asyncio
import uuid
import os
import logging
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import redis.asyncio as aioredis
from croniter import croniter

from kdcube_ai_app.apps.chat.sdk.config import get_settings, get_secret
from kdcube_ai_app.apps.chat.sdk.infra.economics.project_budget import ProjectBudgetLimiter
from kdcube_ai_app.infra.redis.client import get_async_redis_client

logger = logging.getLogger(__name__)

SUBSCRIPTION_TZ = ZoneInfo("UTC")

_economics_redis: Optional[aioredis.Redis] = None


async def _get_redis() -> Optional[aioredis.Redis]:
    """Redis connection for distributed locks between API instances."""
    global _economics_redis
    if _economics_redis is not None:
        return _economics_redis

    redis_url = get_settings().REDIS_URL
    if not redis_url:
        logger.warning(
            "[Economics Routines] REDIS_URL not set; running without distributed locking."
        )
        _economics_redis = None
        return None

    _economics_redis = get_async_redis_client(redis_url)
    return _economics_redis


# =============================================================================
# Stripe Reconcile
# =============================================================================

def stripe_reconcile_enabled() -> bool:
    return get_secret("STRIPE_RECONCILE_ENABLED", "1").lower() in {"1", "true", "yes"}

def _get_stripe_reconcile_cron_expression() -> str:
    return get_secret("STRIPE_RECONCILE_CRON", "45 * * * *")

def _stripe_reconcile_lock_ttl_seconds() -> int:
    try:
        return int(get_secret("STRIPE_RECONCILE_LOCK_TTL_SECONDS", "900"))
    except Exception:
        return 900

def _compute_next_stripe_reconcile_run(now: datetime) -> datetime:
    expr = _get_stripe_reconcile_cron_expression()
    try:
        return croniter(expr, now).get_next(datetime)
    except Exception:
        logger.exception(
            "[Stripe Reconcile] Invalid cron expression '%s', falling back to '45 * * * *'", expr
        )
        return croniter("45 * * * *", now).get_next(datetime)


async def run_stripe_reconcile_sweep_once(*, actor: str = "scheduler") -> dict:
    if not stripe_reconcile_enabled():
        return {"status": "disabled"}

    settings = get_settings()
    tenant = settings.TENANT
    project = settings.PROJECT

    from kdcube_ai_app.apps.chat.api.resolvers import get_pg_pool
    from kdcube_ai_app.apps.chat.sdk.infra.economics.stripe import (
        StripeEconomicsAdminService,
        StripeEconomicsWebhookHandler,
        SubscriptionManager,
        UserCreditsManager,
    )
    from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription_budget import SubscriptionBudgetLimiter

    pg_pool = await get_pg_pool()
    if not pg_pool:
        logger.warning("[Stripe Reconcile] PG pool not available; skipping sweep")
        return {"status": "error", "message": "pg_pool unavailable"}

    redis = await _get_redis()
    lock_key = f"stripe:reconcile:{tenant}:{project}"
    state_key = f"stripe:reconcile:last_ts:{tenant}:{project}"
    token = str(uuid.uuid4())
    lock_ttl = _stripe_reconcile_lock_ttl_seconds()

    import time
    if redis:
        got_lock = await redis.set(lock_key, token, ex=lock_ttl, nx=True)
        if not got_lock:
            logger.info("[Stripe Reconcile] Another instance holds lock %s; skipping", lock_key)
            return {"status": "skipped", "message": "lock_held"}

        last_ts_raw = await redis.get(state_key)
        last_ts = int(last_ts_raw) if last_ts_raw else (int(time.time()) - 24 * 3600)
    else:
        logger.info("[Stripe Reconcile] Redis not configured; running without distributed lock")
        last_ts = int(time.time()) - 24 * 3600

    try:
        from kdcube_ai_app.infra.accounting.usage import anthropic, sonnet_45
        REF_PROVIDER = anthropic
        REF_MODEL = sonnet_45

        subscription_mgr = SubscriptionManager(pg_pool=pg_pool)
        user_credits_mgr = UserCreditsManager(pg_pool=pg_pool)

        def subscription_budget_factory(t, p, u, pk, ps, pe):
            return SubscriptionBudgetLimiter(
                pg_pool=pg_pool, tenant=t, project=p,
                user_id=u, period_key=pk, period_start=ps, period_end=pe,
            )

        def project_budget_factory(t, p):
            return ProjectBudgetLimiter(redis, pg_pool, tenant=t, project=p)

        handler = StripeEconomicsWebhookHandler(
            pg_pool=pg_pool,
            user_credits_mgr=user_credits_mgr,
            subscription_budget_factory=subscription_budget_factory,
            project_budget_factory=project_budget_factory,
            subscription_mgr=subscription_mgr,
            default_tenant=tenant,
            default_project=project,
            stripe_webhook_secret=get_secret("STRIPE_WEBHOOK_SECRET"),
        )

        admin_svc = StripeEconomicsAdminService(
            pg_pool=pg_pool,
            user_credits_mgr=user_credits_mgr,
            subscription_mgr=subscription_mgr,
            ref_provider=REF_PROVIDER,
            ref_model=REF_MODEL,
        )

        res = await admin_svc.reconcile_stripe_events(handler=handler, since_timestamp=last_ts)

        if redis and res.get("latest_event_timestamp"):
            await redis.set(state_key, str(res["latest_event_timestamp"]))

        logger.info(
            "[Stripe Reconcile] Sweep done tenant=%s project=%s reconciled=%d applied=%d errors=%d next_ts=%s",
            tenant, project,
            res.get("reconciled"), res.get("applied"), res.get("errors"), res.get("latest_event_timestamp"),
        )
        return res

    except Exception:
        logger.exception("[Stripe Reconcile] Sweep failed")
        raise
    finally:
        if redis:
            try:
                current_val = await redis.get(lock_key)
                if current_val is not None and current_val.decode() == token:
                    await redis.delete(lock_key)
            except Exception:
                logger.exception("[Stripe Reconcile] Failed to release lock %s", lock_key)


async def stripe_reconcile_scheduler_loop() -> None:
    if not stripe_reconcile_enabled():
        logger.info("[Stripe Reconcile] Scheduler disabled (STRIPE_RECONCILE_ENABLED=0)")
        return

    logger.info(
        "[Stripe Reconcile] Scheduler loop started (tz=%s, cron=%s)",
        SUBSCRIPTION_TZ, _get_stripe_reconcile_cron_expression(),
    )

    while True:
        now = datetime.now(SUBSCRIPTION_TZ)
        next_run = _compute_next_stripe_reconcile_run(now)
        sleep_seconds = (next_run - now).total_seconds()
        logger.info(
            "[Stripe Reconcile] Sleeping %.0f seconds until next run at %s",
            sleep_seconds, next_run.isoformat(),
        )

        try:
            await asyncio.sleep(sleep_seconds)
        except asyncio.CancelledError:
            logger.info("[Stripe Reconcile] Scheduler loop cancelled")
            break

        try:
            await run_stripe_reconcile_sweep_once(actor="scheduler")
        except asyncio.CancelledError:
            logger.info("[Stripe Reconcile] Scheduler loop cancelled")
            break
        except Exception:
            logger.exception("[Stripe Reconcile] Scheduler run failed")


# =============================================================================
# Subscription Rollover
# =============================================================================

def subscription_rollover_enabled() -> bool:
    try:
        return bool(get_settings().SUBSCRIPTION_ROLLOVER_ENABLED)
    except Exception:
        return os.environ.get("SUBSCRIPTION_ROLLOVER_ENABLED", "1").lower() in {"1", "true", "yes"}

def _get_subscription_rollover_cron_expression() -> str:
    expr = None
    try:
        expr = getattr(get_settings(), "SUBSCRIPTION_ROLLOVER_CRON", None)
    except Exception:
        pass
    return expr or os.getenv("SUBSCRIPTION_ROLLOVER_CRON") or "15 * * * *"

def _subscription_rollover_lock_ttl_seconds() -> int:
    try:
        return int(get_settings().SUBSCRIPTION_ROLLOVER_LOCK_TTL_SECONDS)
    except Exception:
        return int(os.environ.get("SUBSCRIPTION_ROLLOVER_LOCK_TTL_SECONDS", "900") or "900")

def _subscription_rollover_sweep_limit() -> int:
    try:
        return int(get_settings().SUBSCRIPTION_ROLLOVER_SWEEP_LIMIT)
    except Exception:
        return int(os.environ.get("SUBSCRIPTION_ROLLOVER_SWEEP_LIMIT", "500") or "500")

def _compute_next_subscription_rollover_run(now: datetime) -> datetime:
    expr = _get_subscription_rollover_cron_expression()
    try:
        return croniter(expr, now).get_next(datetime)
    except Exception:
        logger.exception(
            "[Subscription Rollover] Invalid cron expression '%s', falling back to '15 * * * *'", expr
        )
        return croniter("15 * * * *", now).get_next(datetime)


async def run_subscription_rollover_sweep_once(*, actor: str = "scheduler") -> dict:
    if not subscription_rollover_enabled():
        return {"status": "disabled", "count": 0, "moved_usd": 0.0}

    settings = get_settings()
    tenant = settings.TENANT
    project = settings.PROJECT
    limit = _subscription_rollover_sweep_limit()

    from kdcube_ai_app.apps.chat.api.resolvers import get_pg_pool
    from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription import SubscriptionManager

    pg_pool = await get_pg_pool()
    if not pg_pool:
        logger.warning("[Subscription Rollover] PG pool not available; skipping sweep")
        return {"status": "error", "count": 0, "moved_usd": 0.0, "message": "pg_pool unavailable"}

    redis = await _get_redis()
    lock_key = f"subscription:rollover:{tenant}:{project}"
    token = str(uuid.uuid4())
    lock_ttl = _subscription_rollover_lock_ttl_seconds()

    if redis:
        got_lock = await redis.set(lock_key, token, ex=lock_ttl, nx=True)
        if not got_lock:
            logger.info("[Subscription Rollover] Another instance holds lock %s; skipping", lock_key)
            return {"status": "skipped", "count": 0, "moved_usd": 0.0, "message": "lock_held"}
    else:
        logger.info("[Subscription Rollover] Redis not configured; running without distributed lock")

    try:
        mgr = SubscriptionManager(pg_pool=pg_pool)

        def project_budget_factory(t, p):
            return ProjectBudgetLimiter(redis, pg_pool, tenant=t, project=p)

        total = 0
        moved_total = 0.0
        while True:
            res = await mgr.sweep_due_subscription_rollovers(
                tenant=tenant,
                project=project,
                now=datetime.now(timezone.utc),
                limit=limit,
                project_budget_factory=project_budget_factory,
                actor=actor,
            )
            total += int(res.get("count") or 0)
            moved_total += float(res.get("moved_usd") or 0.0)
            if int(res.get("count") or 0) < int(limit):
                break

        logger.info(
            "[Subscription Rollover] Sweep done tenant=%s project=%s count=%s moved_usd=%.2f",
            tenant, project, total, moved_total,
        )
        return {"status": "ok", "count": total, "moved_usd": moved_total}

    except Exception:
        logger.exception("[Subscription Rollover] Sweep failed")
        raise
    finally:
        if redis:
            try:
                current_val = await redis.get(lock_key)
                if current_val is not None and current_val.decode() == token:
                    await redis.delete(lock_key)
            except Exception:
                logger.exception("[Subscription Rollover] Failed to release lock %s", lock_key)


async def subscription_rollover_scheduler_loop() -> None:
    if not subscription_rollover_enabled():
        logger.info("[Subscription Rollover] Scheduler disabled (SUBSCRIPTION_ROLLOVER_ENABLED=0)")
        return

    logger.info(
        "[Subscription Rollover] Scheduler loop started (tz=%s, cron=%s)",
        SUBSCRIPTION_TZ, _get_subscription_rollover_cron_expression(),
    )

    while True:
        now = datetime.now(SUBSCRIPTION_TZ)
        next_run = _compute_next_subscription_rollover_run(now)
        sleep_seconds = (next_run - now).total_seconds()
        logger.info(
            "[Subscription Rollover] Sleeping %.0f seconds until next run at %s",
            sleep_seconds, next_run.isoformat(),
        )

        try:
            await asyncio.sleep(sleep_seconds)
        except asyncio.CancelledError:
            logger.info("[Subscription Rollover] Scheduler loop cancelled")
            break

        try:
            await run_subscription_rollover_sweep_once(actor="scheduler")
        except asyncio.CancelledError:
            logger.info("[Subscription Rollover] Scheduler loop cancelled")
            break
        except Exception:
            logger.exception("[Subscription Rollover] Scheduler run failed")
