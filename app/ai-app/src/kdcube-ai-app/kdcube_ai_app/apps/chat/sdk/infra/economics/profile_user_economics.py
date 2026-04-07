#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

if __package__ in (None, ""):
    app_root = Path("/app")
    if (app_root / "kdcube_ai_app").exists():
        sys.path.insert(0, str(app_root))
    else:
        for candidate in Path(__file__).resolve().parents:
            if (candidate / "kdcube_ai_app").exists():
                sys.path.insert(0, str(candidate))
                break

import asyncpg

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.infra.control_plane.manager import ControlPlaneManager
from kdcube_ai_app.apps.chat.sdk.infra.economics.limiter import (
    GLOBAL_BUNDLE_ID,
    UserEconomicsRateLimiter,
    _bundle_index_key,
    _k,
    _merge_policy_with_plan_override,
    subject_id_of,
)
from kdcube_ai_app.apps.chat.sdk.infra.economics.project_budget import ProjectBudgetLimiter
from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription import (
    SubscriptionManager,
    build_subscription_period_descriptor,
)
from kdcube_ai_app.apps.chat.sdk.infra.economics.subscription_budget import SubscriptionBudgetLimiter
from kdcube_ai_app.apps.chat.sdk.infra.economics.user_budget import UserBudgetBreakdownService
from kdcube_ai_app.infra.accounting.usage import llm_output_price_usd_per_token
from kdcube_ai_app.ops.deployment.sql.db_deployment import project_schema as _project_schema
from kdcube_ai_app.infra.namespaces import REDIS, ns_key
from kdcube_ai_app.infra.redis.client import get_async_redis_client


DEFAULT_PLAN_ADMIN = "admin"
DEFAULT_PLAN_ANON = "anonymous"
DEFAULT_PLAN_FREE = "free"
DEFAULT_PLAN_PAYG = "payasyougo"

SAFETY_MARGIN = 1.15
EST_TURN_TOKENS_FLOOR = 2000
DEFAULT_OUTPUT_BUDGET = 4000
_COLUMN_EXISTS_CACHE: dict[tuple[str, str, str], bool] = {}


def _dt(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _normalize_role(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "__dict__"):
        return value.__dict__
    return str(value)


def _parse_reservation_meta(meta: Optional[str]) -> dict[str, Any]:
    if not meta:
        return {"tokens_reserved": 0}
    parts = str(meta).split("|")
    tokens_reserved = int(parts[0] or 0) if parts else 0
    return {
        "tokens_reserved": tokens_reserved,
        "reserved_hour_key": parts[1] if len(parts) > 1 else None,
        "reserved_day_key": parts[2] if len(parts) > 2 else None,
        "reserved_month_key": parts[3] if len(parts) > 3 else None,
    }


async def _resolve_role_from_session(
    redis,
    *,
    tenant: str,
    project: str,
    user_id: str,
) -> dict[str, Any]:
    result = {
        "resolved_role": None,
        "source": None,
        "matched_key": None,
        "checked_keys": [],
    }
    if redis is None:
        return result

    try:
        prefix = ns_key(REDIS.SESSION, tenant=tenant, project=project)
        keys = [
            f"{prefix}:paid:{user_id}",
            f"{prefix}:registered:{user_id}",
        ]
        result["checked_keys"] = list(keys)
        for key in keys:
            raw = await redis.get(key)
            if not raw:
                continue
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8", errors="ignore")
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            session_role = payload.get("user_type") or payload.get("role")
            if session_role:
                result["resolved_role"] = str(session_role).strip().lower()
                result["source"] = "session"
                result["matched_key"] = key
                return result
    except Exception as exc:
        result["error"] = str(exc)

    return result


async def _resolve_plan_id_for_user(
    *,
    mgr: ControlPlaneManager,
    redis,
    tenant: str,
    project: str,
    user_id: str,
    role: Optional[str],
    explicit_plan_id: Optional[str],
) -> dict[str, Any]:
    if explicit_plan_id:
        return {
            "plan_id": explicit_plan_id,
            "plan_source": "explicit",
            "role": _normalize_role(role),
            "role_source": "explicit" if role else None,
            "has_active_subscription": False,
            "subscription": None,
        }

    role_norm = _normalize_role(role)
    role_source = "explicit" if role_norm else None
    if not role_norm:
        role_info = await _resolve_role_from_session(
            redis,
            tenant=tenant,
            project=project,
            user_id=user_id,
        )
        role_norm = role_info.get("resolved_role")
        role_source = role_info.get("source")
    else:
        role_info = {
            "resolved_role": role_norm,
            "source": role_source,
            "matched_key": None,
            "checked_keys": [],
        }

    if role_norm in ("privileged", "admin"):
        return {
            "plan_id": DEFAULT_PLAN_ADMIN,
            "plan_source": "role",
            "role": role_norm,
            "role_source": role_source,
            "role_info": role_info,
            "has_active_subscription": False,
            "subscription": None,
        }
    if role_norm == "anonymous":
        return {
            "plan_id": DEFAULT_PLAN_ANON,
            "plan_source": "role",
            "role": role_norm,
            "role_source": role_source,
            "role_info": role_info,
            "has_active_subscription": False,
            "subscription": None,
        }

    subscription = await mgr.subscription_mgr.get_subscription(
        tenant=tenant,
        project=project,
        user_id=user_id,
    )
    now = datetime.now(timezone.utc)
    due_at = getattr(subscription, "next_charge_at", None) if subscription else None
    chargeable = bool(subscription and int(getattr(subscription, "monthly_price_cents", 0) or 0) > 0)
    past_due = bool(due_at and due_at <= now)
    has_active_subscription = bool(
        subscription
        and getattr(subscription, "status", None) == "active"
        and chargeable
        and not past_due
    )
    if has_active_subscription:
        return {
            "plan_id": getattr(subscription, "plan_id", None) or DEFAULT_PLAN_PAYG,
            "plan_source": "subscription",
            "role": role_norm,
            "role_source": role_source,
            "role_info": role_info,
            "has_active_subscription": True,
            "subscription": subscription,
        }

    return {
        "plan_id": DEFAULT_PLAN_FREE,
        "plan_source": "role",
        "role": role_norm or "registered",
        "role_source": role_source or "default",
        "role_info": role_info,
        "has_active_subscription": False,
        "subscription": subscription,
    }


async def _fetch_rl_state(
    *,
    redis,
    rl: UserEconomicsRateLimiter,
    tenant: str,
    project: str,
    user_id: str,
    bundle_id: str,
    now: datetime,
) -> dict[str, Any]:
    subject_id = subject_id_of(tenant, project, user_id)
    ymd = now.strftime("%Y%m%d")
    ymdh = now.strftime("%Y%m%d%H")
    period_start, period_end, period_key = await rl._rolling_month_period(
        bundle_id=bundle_id,
        subject_id=subject_id,
        now=now,
        create_if_missing=False,
    )

    k_req_d = _k(rl.ns, bundle_id, subject_id, "reqs:day", ymd)
    k_req_t = _k(rl.ns, bundle_id, subject_id, "reqs:total")
    k_tok_d = _k(rl.ns, bundle_id, subject_id, "toks:day", ymd)
    k_locks = _k(rl.ns, bundle_id, subject_id, "locks")
    k_tok_h_prefix = _k(rl.ns, bundle_id, subject_id, "toks:hour:bucket")
    k_tok_hr = _k(rl.ns, bundle_id, subject_id, "toks_resv:hour", ymdh)
    k_tok_dr = _k(rl.ns, bundle_id, subject_id, "toks_resv:day", ymd)
    k_month_anchor = _k(rl.ns, bundle_id, subject_id, "month_anchor")
    k_resv_idx = _k(rl.ns, bundle_id, subject_id, "toks_resv:index")
    k_resv_map = _k(rl.ns, bundle_id, subject_id, "toks_resv:data")
    k_bundle_index = _bundle_index_key(rl.ns, subject_id)

    k_req_m = None
    k_tok_m = None
    k_tok_mr = None
    if period_key:
        k_req_m = _k(rl.ns, bundle_id, subject_id, "reqs:month", period_key)
        k_tok_m = _k(rl.ns, bundle_id, subject_id, "toks:month", period_key)
        k_tok_mr = _k(rl.ns, bundle_id, subject_id, "toks_resv:month", period_key)

    values = await redis.mget(
        *[k for k in [k_req_d, k_req_m, k_req_t, k_tok_d, k_tok_m, k_tok_hr, k_tok_dr, k_tok_mr, k_month_anchor] if k]
    )
    value_map = dict(
        zip([k for k in [k_req_d, k_req_m, k_req_t, k_tok_d, k_tok_m, k_tok_hr, k_tok_dr, k_tok_mr, k_month_anchor] if k], values)
    )

    tok_hour_committed, tok_hour_reset_at = await rl._rolling_hour_stats(k_tok_h_prefix, now)
    req_day = int(value_map.get(k_req_d) or 0)
    req_month = int(value_map.get(k_req_m) or 0) if k_req_m else 0
    req_total = int(value_map.get(k_req_t) or 0)
    tok_day = int(value_map.get(k_tok_d) or 0)
    tok_month = int(value_map.get(k_tok_m) or 0) if k_tok_m else 0
    tok_hour_reserved = int(value_map.get(k_tok_hr) or 0)
    tok_day_reserved = int(value_map.get(k_tok_dr) or 0)
    tok_month_reserved = int(value_map.get(k_tok_mr) or 0) if k_tok_mr else 0
    month_anchor_raw = value_map.get(k_month_anchor)

    lock_rows = await redis.zrange(k_locks, 0, -1, withscores=True)
    reservation_rows = await redis.zrange(k_resv_idx, 0, -1, withscores=True)
    reservation_ids = [row[0] for row in reservation_rows]
    reservation_meta_values = await redis.hmget(k_resv_map, reservation_ids) if reservation_ids else []
    bundle_index_members = await redis.smembers(k_bundle_index)

    reservations: list[dict[str, Any]] = []
    for (reservation_id, expires_at), meta in zip(reservation_rows, reservation_meta_values):
        rid = reservation_id.decode("utf-8", errors="ignore") if isinstance(reservation_id, (bytes, bytearray)) else str(reservation_id)
        parsed_meta = _parse_reservation_meta(
            meta.decode("utf-8", errors="ignore") if isinstance(meta, (bytes, bytearray)) else meta
        )
        parsed_meta["reservation_id"] = rid
        parsed_meta["expires_at"] = _dt(datetime.fromtimestamp(float(expires_at), tz=timezone.utc))
        reservations.append(parsed_meta)

    locks: list[dict[str, Any]] = []
    for member, expires_at in lock_rows:
        lock_id = member.decode("utf-8", errors="ignore") if isinstance(member, (bytes, bytearray)) else str(member)
        locks.append({
            "lock_id": lock_id,
            "expires_at": _dt(datetime.fromtimestamp(float(expires_at), tz=timezone.utc)),
        })

    return {
        "subject_id": subject_id,
        "bundle_id": bundle_id,
        "namespace": rl.ns,
        "bundle_index_members": sorted(
            [
                value.decode("utf-8", errors="ignore") if isinstance(value, (bytes, bytearray)) else str(value)
                for value in (bundle_index_members or [])
            ]
        ),
        "month_window": {
            "period_key": period_key,
            "period_start": _dt(period_start),
            "period_end": _dt(period_end),
            "month_anchor": _dt(
                datetime.fromtimestamp(int(month_anchor_raw), tz=timezone.utc)
            ) if month_anchor_raw else None,
        },
        "day_window": {
            "kind": "calendar_utc_day",
            "day_key": ymd,
            "day_start": _dt(datetime(now.year, now.month, now.day, tzinfo=timezone.utc)),
            "day_end": _dt(datetime(now.year, now.month, now.day, tzinfo=timezone.utc) + timedelta(days=1)),
        },
        "hour_window": {
            "kind": "rolling_60m",
            "hour_reset_at": _dt(
                datetime.fromtimestamp(int(tok_hour_reset_at), tz=timezone.utc)
            ) if tok_hour_reset_at else None,
        },
        "committed": {
            "requests_today": req_day,
            "requests_this_rolling_month": req_month,
            "requests_total": req_total,
            "tokens_this_hour": int(tok_hour_committed),
            "tokens_today": tok_day,
            "tokens_this_rolling_month": tok_month,
            "concurrent": len(locks),
        },
        "reserved": {
            "tokens_this_hour": tok_hour_reserved,
            "tokens_today": tok_day_reserved,
            "tokens_this_rolling_month": tok_month_reserved,
            "active_locks": locks,
            "active_token_reservations": reservations,
        },
        "effective_for_admit": {
            "tokens_this_hour": int(tok_hour_committed) + int(tok_hour_reserved),
            "tokens_today": int(tok_day) + int(tok_day_reserved),
            "tokens_this_rolling_month": int(tok_month) + int(tok_month_reserved),
        },
        "redis_keys": {
            "req_day": k_req_d,
            "req_month": k_req_m,
            "req_total": k_req_t,
            "tok_day": k_tok_d,
            "tok_month": k_tok_m,
            "tok_hour_prefix": k_tok_h_prefix,
            "tok_hour_reserved": k_tok_hr,
            "tok_day_reserved": k_tok_dr,
            "tok_month_reserved": k_tok_mr,
            "locks": k_locks,
            "month_anchor": k_month_anchor,
            "reservation_index": k_resv_idx,
            "reservation_map": k_resv_map,
            "bundle_index": k_bundle_index,
        },
    }


def _remaining(limit: Optional[int], used: int) -> Optional[int]:
    if limit is None:
        return None
    return max(int(limit) - int(used), 0)


def _window_end_for_shortfall(
    *,
    now: datetime,
    month_end: Optional[datetime],
    day_end: datetime,
    hour_end: datetime,
    remaining_hour: Optional[int],
    remaining_day: Optional[int],
    remaining_month: Optional[int],
    needed_tokens: int,
) -> Optional[dict[str, Any]]:
    candidates: list[tuple[str, datetime]] = []
    if remaining_hour is not None and remaining_hour < needed_tokens:
        candidates.append(("hour", hour_end))
    if remaining_day is not None and remaining_day < needed_tokens:
        candidates.append(("day", day_end))
    if remaining_month is not None and remaining_month < needed_tokens and month_end is not None:
        candidates.append(("month", month_end))
    if not candidates:
        return None
    scope, reset_at = max(candidates, key=lambda item: item[1])
    return {
        "scope": scope,
        "reset_at": _dt(reset_at),
        "retry_after_sec": max(int((reset_at - now).total_seconds()), 0),
    }


def _simulate_next_turn(
    *,
    now: datetime,
    role: str,
    plan_id: str,
    plan_source: str,
    effective_policy,
    rl_state: dict[str, Any],
    project_budget: Optional[dict[str, Any]],
    subscription_budget: Optional[dict[str, Any]],
    wallet_available_tokens: int,
    est_turn_tokens: int,
    reservation_amount_dollars: Optional[float],
    usd_per_token: float,
) -> dict[str, Any]:
    committed = rl_state.get("committed") or {}
    reserved = rl_state.get("reserved") or {}
    effective_usage = rl_state.get("effective_for_admit") or {}

    remaining_hour = _remaining(getattr(effective_policy, "tokens_per_hour", None), effective_usage.get("tokens_this_hour", 0))
    remaining_day = _remaining(getattr(effective_policy, "tokens_per_day", None), effective_usage.get("tokens_today", 0))
    remaining_month = _remaining(getattr(effective_policy, "tokens_per_month", None), effective_usage.get("tokens_this_rolling_month", 0))
    remaining_req_day = _remaining(getattr(effective_policy, "requests_per_day", None), committed.get("requests_today", 0))
    remaining_req_month = _remaining(getattr(effective_policy, "requests_per_month", None), committed.get("requests_this_rolling_month", 0))
    remaining_req_total = _remaining(getattr(effective_policy, "total_requests", None), committed.get("requests_total", 0))
    remaining_concurrent = _remaining(getattr(effective_policy, "max_concurrent", None), committed.get("concurrent", 0))

    plan_covered_tokens = int(est_turn_tokens)
    for remaining_tokens in (remaining_hour, remaining_day, remaining_month):
        if remaining_tokens is not None:
            plan_covered_tokens = min(int(plan_covered_tokens), int(remaining_tokens))
    plan_covered_tokens = max(int(plan_covered_tokens), 0)
    overflow_tokens = max(int(est_turn_tokens) - int(plan_covered_tokens), 0)

    project_available_usd = float((project_budget or {}).get("available_usd") or 0.0)
    subscription_available_usd = float((subscription_budget or {}).get("available_usd") or 0.0)
    est_turn_usd = round(float(est_turn_tokens) * float(usd_per_token) * float(SAFETY_MARGIN), 6)
    plan_covered_usd = round(float(plan_covered_tokens) * float(usd_per_token) * float(SAFETY_MARGIN), 6)
    overflow_usd = round(float(overflow_tokens) * float(usd_per_token) * float(SAFETY_MARGIN), 6)

    month_end = None
    month_window = rl_state.get("month_window") or {}
    if month_window.get("period_end"):
        month_end = datetime.fromisoformat(month_window["period_end"])
    day_end = datetime(now.year, now.month, now.day, tzinfo=timezone.utc) + timedelta(days=1)
    hour_end = datetime(now.year, now.month, now.day, now.hour, tzinfo=timezone.utc) + timedelta(hours=1)
    next_unblock = _window_end_for_shortfall(
        now=now,
        month_end=month_end,
        day_end=day_end,
        hour_end=hour_end,
        remaining_hour=remaining_hour,
        remaining_day=remaining_day,
        remaining_month=remaining_month,
        needed_tokens=int(est_turn_tokens),
    )

    likely_status = "not_currently_blocked_by_snapshot"
    likely_reason = None
    if remaining_req_day == 0:
        likely_status = "blocked_by_requests_per_day"
        likely_reason = "Daily request quota is exhausted."
    elif remaining_req_month == 0:
        likely_status = "blocked_by_requests_per_month"
        likely_reason = "Rolling 30-day request quota is exhausted."
    elif remaining_req_total == 0:
        likely_status = "blocked_by_total_requests"
        likely_reason = "Total request quota is exhausted."
    elif remaining_concurrent == 0:
        likely_status = "blocked_by_concurrency"
        likely_reason = "Max concurrent requests is already in flight."
    elif overflow_tokens > 0 and wallet_available_tokens < overflow_tokens and plan_id == DEFAULT_PLAN_FREE and plan_source == "role":
        likely_status = "blocked_by_plan_overflow_no_personal_credits"
        likely_reason = (
            "Estimated turn exceeds the remaining plan tokens in the rolling 30-day window, "
            "and the missing overflow must come from personal credits."
        )
    elif project_budget is not None and project_available_usd <= 0 and overflow_tokens <= 0:
        likely_status = "blocked_by_project_budget"
        likely_reason = "Project budget is exhausted."
    elif subscription_budget is not None and subscription_available_usd <= 0:
        likely_status = "blocked_by_subscription_budget"
        likely_reason = "Subscription budget is exhausted."

    return {
        "estimated_turn_tokens": int(est_turn_tokens),
        "reservation_amount_dollars": reservation_amount_dollars,
        "estimated_turn_usd_with_safety_margin": float(est_turn_usd),
        "remaining_before_reservation": {
            "requests_today": remaining_req_day,
            "requests_this_rolling_month": remaining_req_month,
            "requests_total": remaining_req_total,
            "concurrent": remaining_concurrent,
            "tokens_this_hour": remaining_hour,
            "tokens_today": remaining_day,
            "tokens_this_rolling_month": remaining_month,
        },
        "plan_covered_tokens_est": int(plan_covered_tokens),
        "plan_covered_usd_est": float(plan_covered_usd),
        "overflow_tokens_est": int(overflow_tokens),
        "overflow_usd_est": float(overflow_usd),
        "wallet_available_tokens": int(wallet_available_tokens),
        "wallet_tokens_short_for_overflow": max(int(overflow_tokens) - int(wallet_available_tokens), 0),
        "project_budget_available_usd": float(project_available_usd),
        "subscription_available_usd": float(subscription_available_usd),
        "current_effective_usage": {
            "tokens_this_hour": effective_usage.get("tokens_this_hour", 0),
            "tokens_today": effective_usage.get("tokens_today", 0),
            "tokens_this_rolling_month": effective_usage.get("tokens_this_rolling_month", 0),
            "reserved_tokens_this_hour": (reserved or {}).get("tokens_this_hour", 0),
            "reserved_tokens_today": (reserved or {}).get("tokens_today", 0),
            "reserved_tokens_this_rolling_month": (reserved or {}).get("tokens_this_rolling_month", 0),
        },
        "next_unblock": next_unblock,
        "likely_status": likely_status,
        "likely_reason": likely_reason,
        "notes": [
            "Daily/monthly semantics follow the running deployment limiter; rely on the reported remaining counters and reset timestamps instead of assuming calendar windows.",
            "Project budget can fund only the plan-covered part of a turn. Any overflow beyond plan tokens must come from user personal credits.",
        ],
    }


async def _fetch_active_project_reservations(
    *,
    pg_pool: asyncpg.Pool,
    tenant: str,
    project: str,
    user_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    _schema = _project_schema(tenant, project)
    has_updated_at = await _table_has_column(
        pg_pool=pg_pool,
        schema=_schema,
        table="tenant_project_budget_reservations",
        column="updated_at",
    )
    updated_at_expr = "updated_at" if has_updated_at else "NULL::timestamptz AS updated_at"
    sql = f"""
        SELECT reservation_id, bundle_id, provider, user_id, request_id,
               amount_cents, actual_spent_cents, status,
               expires_at, created_at, {updated_at_expr}, released_at, committed_at, notes
        FROM {_schema}.tenant_project_budget_reservations
        WHERE tenant=$1 AND project=$2 AND user_id=$3 AND status='active'
        ORDER BY created_at DESC
        LIMIT $4
    """
    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(sql, tenant, project, user_id, limit)
    return [
        {
            "reservation_id": str(row["reservation_id"]),
            "bundle_id": row["bundle_id"],
            "provider": row["provider"],
            "request_id": row["request_id"],
            "amount_usd": float(int(row["amount_cents"] or 0)) / 100.0,
            "actual_spent_usd": float(int(row["actual_spent_cents"] or 0)) / 100.0 if row["actual_spent_cents"] is not None else None,
            "status": row["status"],
            "expires_at": _dt(row["expires_at"]),
            "created_at": _dt(row["created_at"]),
            "updated_at": _dt(row["updated_at"]),
            "released_at": _dt(row["released_at"]),
            "committed_at": _dt(row["committed_at"]),
            "notes": row["notes"],
        }
        for row in rows
    ]


async def _fetch_active_subscription_reservations(
    *,
    pg_pool: asyncpg.Pool,
    tenant: str,
    project: str,
    user_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    _schema = _project_schema(tenant, project)
    has_updated_at = await _table_has_column(
        pg_pool=pg_pool,
        schema=_schema,
        table="user_subscription_period_reservations",
        column="updated_at",
    )
    updated_at_expr = "updated_at" if has_updated_at else "NULL::timestamptz AS updated_at"
    sql = f"""
        SELECT reservation_id, period_key, bundle_id, provider, request_id,
               amount_cents, actual_spent_cents, status,
               expires_at, created_at, {updated_at_expr}, released_at, committed_at, notes
        FROM {_schema}.user_subscription_period_reservations
        WHERE tenant=$1 AND project=$2 AND user_id=$3 AND status='active'
        ORDER BY created_at DESC
        LIMIT $4
    """
    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(sql, tenant, project, user_id, limit)
    return [
        {
            "reservation_id": str(row["reservation_id"]),
            "period_key": row["period_key"],
            "bundle_id": row["bundle_id"],
            "provider": row["provider"],
            "request_id": row["request_id"],
            "amount_usd": float(int(row["amount_cents"] or 0)) / 100.0,
            "actual_spent_usd": float(int(row["actual_spent_cents"] or 0)) / 100.0 if row["actual_spent_cents"] is not None else None,
            "status": row["status"],
            "expires_at": _dt(row["expires_at"]),
            "created_at": _dt(row["created_at"]),
            "updated_at": _dt(row["updated_at"]),
            "released_at": _dt(row["released_at"]),
            "committed_at": _dt(row["committed_at"]),
            "notes": row["notes"],
        }
        for row in rows
    ]


async def _augment_breakdown_with_hourly_usage(
    *,
    breakdown: dict[str, Any],
    rl: UserEconomicsRateLimiter,
    tenant: str,
    project: str,
    user_id: str,
    now: datetime,
    usd_per_token: float,
) -> dict[str, Any]:
    bundle_breakdown = breakdown.get("bundle_breakdown") or {}
    if not bundle_breakdown:
        breakdown.setdefault("current_usage", {})["tokens_this_hour"] = 0
        breakdown.setdefault("current_usage", {})["tokens_this_hour_usd"] = 0.0
        breakdown.setdefault("remaining", {})["tokens_this_hour"] = None
        breakdown.setdefault("remaining", {})["tokens_this_hour_usd"] = None
        return breakdown

    subject_id = subject_id_of(tenant, project, user_id)
    total_hour = 0
    for bundle_id, payload in bundle_breakdown.items():
        bucket_prefix = _k(rl.ns, bundle_id, subject_id, "toks:hour:bucket")
        tok_hour, _ = await rl._rolling_hour_stats(bucket_prefix, now)
        payload["tokens_this_hour"] = int(tok_hour)
        payload["tokens_this_hour_usd"] = round(float(tok_hour) * float(usd_per_token), 2)
        total_hour += int(tok_hour)

    current_usage = breakdown.setdefault("current_usage", {})
    remaining = breakdown.setdefault("remaining", {})
    current_usage["tokens_this_hour"] = int(total_hour)
    current_usage["tokens_this_hour_usd"] = round(float(total_hour) * float(usd_per_token), 2)

    effective_policy = breakdown.get("effective_policy") or {}
    tokens_per_hour = effective_policy.get("tokens_per_hour")
    if tokens_per_hour is None:
        remaining["tokens_this_hour"] = None
        remaining["tokens_this_hour_usd"] = None
    else:
        remaining_hour = int(tokens_per_hour) - int(total_hour)
        remaining["tokens_this_hour"] = remaining_hour
        remaining["tokens_this_hour_usd"] = round(float(remaining_hour) * float(usd_per_token), 2)
    return breakdown


async def _table_has_column(
    *,
    pg_pool: asyncpg.Pool,
    schema: str,
    table: str,
    column: str,
) -> bool:
    cache_key = (schema, table, column)
    cached = _COLUMN_EXISTS_CACHE.get(cache_key)
    if cached is not None:
        return cached

    sql = """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = $1 AND table_name = $2 AND column_name = $3
        ) AS present
    """
    async with pg_pool.acquire() as conn:
        present = bool(await conn.fetchval(sql, schema, table, column))
    _COLUMN_EXISTS_CACHE[cache_key] = present
    return present


async def _main(args) -> int:
    settings = get_settings()
    tenant = args.tenant or settings.TENANT
    project = args.project or settings.PROJECT
    now = datetime.now(timezone.utc)

    pg_pool = await asyncpg.create_pool(
        host=settings.PGHOST,
        port=settings.PGPORT,
        user=settings.PGUSER,
        password=settings.PGPASSWORD,
        database=settings.PGDATABASE,
        ssl=settings.PGSSL,
    )
    redis = get_async_redis_client(settings.REDIS_URL)

    try:
        mgr = ControlPlaneManager(pg_pool=pg_pool, redis=redis)
        rl = UserEconomicsRateLimiter(redis, user_balance_snapshot_mgr=mgr.plan_balance_snapshot_mgr)
        role_plan_info = await _resolve_plan_id_for_user(
            mgr=mgr,
            redis=redis,
            tenant=tenant,
            project=project,
            user_id=args.user_id,
            role=args.role,
            explicit_plan_id=args.plan_id,
        )
        role = role_plan_info.get("role") or "registered"
        plan_id = role_plan_info["plan_id"]
        plan_source = role_plan_info["plan_source"]
        subscription = role_plan_info.get("subscription")
        has_active_subscription = bool(role_plan_info.get("has_active_subscription"))

        base_policy = await mgr.get_plan_quota_policy(
            tenant=tenant,
            project=project,
            plan_id=plan_id,
        )
        if not base_policy:
            raise RuntimeError(f"No quota policy found for plan_id={plan_id}")

        plan_balance = await mgr.get_user_plan_balance(
            tenant=tenant,
            project=project,
            user_id=args.user_id,
            include_expired=True,
        )
        plan_balance_effective = await mgr.get_user_plan_balance(
            tenant=tenant,
            project=project,
            user_id=args.user_id,
            include_expired=False,
        )
        effective_policy = _merge_policy_with_plan_override(base_policy, plan_balance_effective) if plan_balance_effective else base_policy

        wallet_available_tokens = await mgr.user_credits_mgr.get_lifetime_balance(
            tenant=tenant,
            project=project,
            user_id=args.user_id,
        )
        wallet_available_tokens = int(wallet_available_tokens or 0)

        reference_provider = args.reference_provider
        reference_model = args.reference_model
        usd_per_token = float(llm_output_price_usd_per_token(reference_provider, reference_model))

        usage_bundle_ids = args.usage_bundle_ids or ["*"]
        breakdown_service = UserBudgetBreakdownService(pg_pool=pg_pool, redis=redis)
        user_budget_breakdown = await breakdown_service.get_user_budget_breakdown(
            tenant=tenant,
            project=project,
            user_id=args.user_id,
            role=role,
            plan_id=plan_id,
            plan_source=plan_source,
            base_policy=base_policy,
            include_expired_override=True,
            reservations_limit=args.limit,
            bundle_ids=usage_bundle_ids,
            reference_provider=reference_provider,
            reference_model=reference_model,
        )
        user_budget_breakdown = await _augment_breakdown_with_hourly_usage(
            breakdown=user_budget_breakdown,
            rl=rl,
            tenant=tenant,
            project=project,
            user_id=args.user_id,
            now=now,
            usd_per_token=usd_per_token,
        )

        project_budget_limiter = ProjectBudgetLimiter(
            redis=redis,
            pg_pool=pg_pool,
            tenant=tenant,
            project=project,
        )
        project_budget = await project_budget_limiter.get_app_budget_balance()
        project_spending = await project_budget_limiter.get_spending_by_bundle(now=now)

        subscription_budget = None
        subscription_descriptor = None
        if subscription:
            subscription_descriptor = build_subscription_period_descriptor(
                tenant=tenant,
                project=project,
                user_id=args.user_id,
                provider=getattr(subscription, "provider", "internal") or "internal",
                stripe_subscription_id=getattr(subscription, "stripe_subscription_id", None),
                period_end=getattr(subscription, "next_charge_at", None),
                period_start=getattr(subscription, "last_charged_at", None),
            )
            limiter = SubscriptionBudgetLimiter(
                pg_pool=pg_pool,
                tenant=tenant,
                project=project,
                user_id=args.user_id,
                period_key=subscription_descriptor["period_key"],
                period_start=subscription_descriptor["period_start"],
                period_end=subscription_descriptor["period_end"],
            )
            subscription_budget = await limiter.get_subscription_budget_balance()

        rl_state = await _fetch_rl_state(
            redis=redis,
            rl=rl,
            tenant=tenant,
            project=project,
            user_id=args.user_id,
            bundle_id=args.rl_bundle_id,
            now=now,
        )

        reservation_amount_dollars = args.reservation_amount_dollars
        est_turn_tokens = args.est_turn_tokens
        if est_turn_tokens is None:
            if reservation_amount_dollars is not None and reservation_amount_dollars > 0:
                est_turn_tokens = int(
                    math.ceil(
                        float(reservation_amount_dollars)
                        / max(float(usd_per_token) * float(SAFETY_MARGIN), 1e-9)
                    )
                )
                est_turn_tokens = max(int(EST_TURN_TOKENS_FLOOR), int(est_turn_tokens))
            else:
                est_turn_tokens = max(int(EST_TURN_TOKENS_FLOOR), int(DEFAULT_OUTPUT_BUDGET))

        simulated_next_turn = _simulate_next_turn(
            now=now,
            role=role,
            plan_id=plan_id,
            plan_source=plan_source,
            effective_policy=effective_policy,
            rl_state=rl_state,
            project_budget=project_budget,
            subscription_budget=subscription_budget,
            wallet_available_tokens=wallet_available_tokens,
            est_turn_tokens=int(est_turn_tokens),
            reservation_amount_dollars=reservation_amount_dollars,
            usd_per_token=usd_per_token,
        )

        active_project_reservations = await _fetch_active_project_reservations(
            pg_pool=pg_pool,
            tenant=tenant,
            project=project,
            user_id=args.user_id,
            limit=args.limit,
        )
        active_subscription_reservations = await _fetch_active_subscription_reservations(
            pg_pool=pg_pool,
            tenant=tenant,
            project=project,
            user_id=args.user_id,
            limit=args.limit,
        )

        result = {
            "profiled_at": _dt(now),
            "tenant": tenant,
            "project": project,
            "user_id": args.user_id,
            "resolved_role": role,
            "role_source": role_plan_info.get("role_source"),
            "role_resolution": role_plan_info.get("role_info"),
            "plan": {
                "plan_id": plan_id,
                "plan_source": plan_source,
                "base_policy": base_policy.__dict__,
                "effective_policy": effective_policy.__dict__,
                "plan_balance_snapshot": plan_balance.__dict__ if plan_balance else None,
            },
            "reference_pricing": {
                "provider": reference_provider,
                "model": reference_model,
                "usd_per_token": usd_per_token,
            },
            "user_budget_breakdown": user_budget_breakdown,
            "rl_state": rl_state,
            "project_budget": {
                "balance": project_budget,
                "spending": project_spending,
                "active_reservations_for_user": active_project_reservations,
            },
            "subscription": {
                "has_active_subscription": has_active_subscription,
                "record": subscription.__dict__ if subscription else None,
                "period_descriptor": subscription_descriptor,
                "budget": subscription_budget,
                "active_reservations_for_user": active_subscription_reservations,
            },
            "simulation": simulated_next_turn,
            "summary": {
                "status": simulated_next_turn["likely_status"],
                "reason": simulated_next_turn["likely_reason"],
                "rolling_month_reset_at": (rl_state.get("month_window") or {}).get("period_end"),
                "rolling_month_tokens_remaining_before_reservation": (simulated_next_turn.get("remaining_before_reservation") or {}).get("tokens_this_rolling_month"),
                "estimated_turn_tokens": simulated_next_turn["estimated_turn_tokens"],
                "plan_covered_tokens_est": simulated_next_turn["plan_covered_tokens_est"],
                "overflow_tokens_est": simulated_next_turn["overflow_tokens_est"],
                "wallet_available_tokens": simulated_next_turn["wallet_available_tokens"],
                "project_budget_available_usd": simulated_next_turn["project_budget_available_usd"],
                "subscription_available_usd": simulated_next_turn["subscription_available_usd"],
            },
        }

        print(json.dumps(result, indent=2, sort_keys=False, default=_json_default))
        return 0
    finally:
        await pg_pool.close()
        if not getattr(redis, "_kdcube_shared", False):
            await redis.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Profile a single user's economics state against the running deployment code path. "
            "Outputs quota/budget breakdown, raw RL windows for the chosen RL bundle, "
            "active reservations, and a simulated next-turn overflow check."
        )
    )
    parser.add_argument("--user-id", required=True, help="Target user id.")
    parser.add_argument("--tenant", help="Tenant id. Defaults to env TENANT_ID.")
    parser.add_argument("--project", help="Project id. Defaults to env PROJECT_ID.")
    parser.add_argument("--role", help="Optional role hint: registered, anonymous, privileged, admin.")
    parser.add_argument("--plan-id", help="Optional explicit plan id override.")
    parser.add_argument(
        "--rl-bundle-id",
        default=GLOBAL_BUNDLE_ID,
        help=f"Bundle id used by the RL counters. Defaults to {GLOBAL_BUNDLE_ID}.",
    )
    parser.add_argument(
        "--usage-bundle-id",
        dest="usage_bundle_ids",
        action="append",
        help="Bundle ids for the user-budget breakdown. Repeatable. Defaults to '*' if omitted.",
    )
    parser.add_argument(
        "--est-turn-tokens",
        type=int,
        help="Optional explicit estimated next-turn tokens. If omitted, computed from reservation dollars.",
    )
    parser.add_argument(
        "--reservation-amount-dollars",
        type=float,
        default=2.0,
        help="Bundle reservation_amount_dollars used to estimate next-turn tokens when --est-turn-tokens is omitted.",
    )
    parser.add_argument(
        "--reference-provider",
        default="anthropic",
        help="Reference provider for USD/token conversion.",
    )
    parser.add_argument(
        "--reference-model",
        default="claude-sonnet-4-5-20250929",
        help="Reference model for USD/token conversion.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Max rows to show for reservation lists.",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        return asyncio.run(_main(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
