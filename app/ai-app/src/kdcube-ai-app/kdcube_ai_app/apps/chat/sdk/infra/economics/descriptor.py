# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

# apps/chat/sdk/infra/economics/descriptor.py

"""
Runtime write-back of the economics descriptor (mounted-file authority).

The economics descriptor (`economics.yaml`) lives on the shared, writable
`/config` mount — a host bind mount locally and EFS on AWS ECS (see
docs/ops/ecs/ecs-deployment-README.md: "ECS deployment relies on EFS for shared
mutable state", `/config` is EFS-backed). Both ingress (where admin mutations
run) and proc (where the runtime reads) see the same file.

Two distinct purposes:
  - quota / budget / subscription: Postgres is the live runtime authority. The
    descriptor is only a persistence snapshot so the next deploy-time
    `seed_economics` does not regress runtime/admin changes.
  - reservation / price_tables: the descriptor file IS the live runtime source
    (read per turn via config_scopes, mtime-cached). These are never stored in the
    DB, so write-backs preserve them verbatim from the existing file. Writing here
    lets runtime pick up changes in real time across replicas over the shared mount.

This is best-effort: callers must not fail the admin mutation if the descriptor
write fails.
"""

from __future__ import annotations

import fcntl
import logging
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

import yaml

from kdcube_ai_app.apps.chat.sdk.config_scopes import _descriptor_path

logger = logging.getLogger(__name__)

_QUOTA_FIELDS = (
    "max_concurrent",
    "requests_per_day",
    "requests_per_month",
    "total_requests",
    "tokens_per_hour",
    "tokens_per_day",
    "tokens_per_month",
)
_BUDGET_FIELDS = ("usd_per_hour", "usd_per_day", "usd_per_month")
_DEFAULT_RESERVATION = {"chat": 2.0}


def _economics_descriptor_path() -> Path:
    return _descriptor_path(
        env_name="ECONOMICS_YAML_DESCRIPTOR_PATH",
        filename="economics.yaml",
        default="/config/economics.yaml",
    )


def _num(v) -> Optional[float]:
    return None if v is None else float(v)


def read_reservation() -> Dict[str, Any]:
    """Current reservation surfaces from the descriptor file (for admin display)."""
    existing = _read_existing(_economics_descriptor_path())
    reservation = existing.get("reservation")
    return dict(reservation) if isinstance(reservation, dict) else dict(_DEFAULT_RESERVATION)


def _read_existing(path: Path) -> Dict[str, Any]:
    try:
        if path.exists():
            data = yaml.safe_load(path.read_text()) or {}
            return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("[economics.descriptor] failed to read %s: %s", path, e)
    return {}


async def build_economics_descriptor(
    cp_manager,
    *,
    tenant: str,
    project: str,
    existing: Optional[Dict[str, Any]] = None,
    reservation_overrides: Optional[Mapping[str, Any]] = None,
    reservation_deletes: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Assemble a descriptor dict from live DB state, preserving reservation."""
    quota = await cp_manager.list_plan_quota_policies(tenant=tenant, project=project, limit=1000)
    quota_policies: Dict[str, Any] = {
        p.plan_id: {k: getattr(p, k) for k in _QUOTA_FIELDS if getattr(p, k) is not None}
        for p in quota
    }

    budget = await cp_manager.list_tenant_project_budget_policies(tenant=tenant, project=project, limit=1000)
    budget_policies = {
        b.provider: {k: _num(getattr(b, k)) for k in _BUDGET_FIELDS}
        for b in budget
    }

    plan_rows = await cp_manager.subscription_mgr.list_plans(
        tenant=tenant, project=project, active_only=False, limit=5000
    )
    plans: Dict[str, Any] = {}
    for pl in plan_rows:
        entry = {
            "provider": pl.provider,
            "monthly_price_cents": int(pl.monthly_price_cents or 0),
            "active": bool(pl.active),
        }
        if getattr(pl, "stripe_price_id", None):
            entry["stripe_price_id"] = pl.stripe_price_id
        plans[pl.plan_id] = entry

    # Overdraft limit lives in tenant_project_budget; the balance is intentionally
    # never part of the descriptor.
    overdraft_usd: Optional[float] = 0.0
    try:
        from kdcube_ai_app.apps.chat.sdk.infra.economics.project_budget import ProjectBudgetLimiter
        limiter = ProjectBudgetLimiter(
            redis=getattr(cp_manager, "_redis", None),
            pg_pool=getattr(cp_manager, "_pg_pool", None),
            tenant=tenant,
            project=project,
        )
        snap = await limiter.get_app_budget_balance()
        overdraft_usd = snap.get("overdraft_limit_usd")
    except Exception as e:
        logger.warning("[economics.descriptor] overdraft read failed: %s", e)

    existing = existing if existing is not None else {}
    reservation = dict(existing.get("reservation") or _DEFAULT_RESERVATION)
    if reservation_overrides:
        reservation.update(reservation_overrides)
    for floor in (reservation_deletes or ()):
        reservation.pop(floor, None)

    payload = {
        "version": int(existing.get("version", 1)),
        "enforce": bool(existing.get("enforce", False)),
        "reservation": reservation,
        "project_budget": {"overdraft_limit_usd": overdraft_usd},
        "quota_policies": quota_policies,
        "budget_policies": budget_policies,
        "plans": plans,
    }
    price_tables = existing.get("price_tables")
    if isinstance(price_tables, dict):
        payload["price_tables"] = price_tables
    return payload


def _write_locked(path: Path, builder) -> None:
    """
    Hold an exclusive flock around read-modify-write so concurrent admin
    mutations don't lose the reservation section, then replace atomically.
    `builder(existing_dict) -> new_dict`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with open(lock_path, "a+") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            existing = _read_existing(path)
            payload = builder(existing)
            text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


async def sync_economics_descriptor(
    cp_manager,
    *,
    tenant: str,
    project: str,
    reservation_overrides: Optional[Mapping[str, Any]] = None,
    reservation_deletes: Optional[Iterable[str]] = None,
) -> bool:
    """
    Rewrite economics.yaml from the live DB state (preserving/merging the
    reservation section). Best-effort: returns True on success, False on
    failure (never raises). Call after an admin economics mutation.
    """
    path = _economics_descriptor_path()
    deletes = tuple(reservation_deletes or ())
    try:
        # Build under the lock so we read the freshest reservation section.
        existing = _read_existing(path)
        descriptor = await build_economics_descriptor(
            cp_manager, tenant=tenant, project=project,
            existing=existing, reservation_overrides=reservation_overrides,
            reservation_deletes=deletes,
        )

        def _builder(current: Dict[str, Any]) -> Dict[str, Any]:
            # Re-merge reservation under the lock in case it changed between the
            # async DB read and acquiring the lock.
            merged = dict(descriptor)
            reservation = dict(current.get("reservation") or _DEFAULT_RESERVATION)
            if reservation_overrides:
                reservation.update(reservation_overrides)
            for floor in deletes:
                reservation.pop(floor, None)
            merged["reservation"] = reservation
            return merged

        _write_locked(path, _builder)
        logger.info("[economics.descriptor] synced %s for %s/%s", path, tenant, project)
        return True
    except Exception as e:
        logger.warning("[economics.descriptor] sync failed for %s/%s: %s", tenant, project, e)
        return False
