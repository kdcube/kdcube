# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

# apps/chat/sdk/infra/economics/plan_resolution.py

"""
Single source of truth for runtime plan resolution.

Two pure helpers, mirrored by every economics surface (the run() entrypoint, the
enforcement guard, the profile CLI, and the auth-hook role resolver):

  - subscription_is_active(): the "paid lane" predicate — a chargeable, active,
    not-past-due subscription. Free/admin baseline rows (price 0) are NOT active
    here by design: only a chargeable subscription flips a user onto the paid lane.
  - subscription_provides_plan_budget(): True only for an active EXTERNAL subscription
    whose plan is not entitlement-only; False for internal subscriptions, for
    entitlement-only plans, and for no subscription.
  - plan_is_entitlement_only(): the plan's `entitlement_only` property.
  - resolve_plan_id(): maps a user's normalized role + subscription state onto the
    platform plan-id the runtime resolves them into.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Mapping, Optional, Tuple

# Platform plan-id vocabulary (the ids the runtime resolves users into).
# `wallet` is the quota-only paid lane and the fallback plan_id for a chargeable
# subscription whose plan_id is null; it is never a user->plan mapping itself.
PLAN_ADMIN = "admin"
PLAN_ANONYMOUS = "anonymous"
PLAN_FREE = "free"
PLAN_WALLET = "wallet"

_ADMIN_ROLES = ("privileged", "admin")

# Plan property: the subscription buys entitlement (access, a tier, a seat) and
# funds no plan budget. Stored as `plans.metadata.entitlement_only`; declared in
# economics.yaml as `plans.<plan_id>.entitlement_only`.
ENTITLEMENT_ONLY_KEY = "entitlement_only"


def plan_metadata(plan: Any) -> Dict[str, Any]:
    """`plan.metadata` (or `plan["metadata"]`) as a dict.

    The column is jsonb. asyncpg decodes it to a dict only on a pool that
    registered the jsonb codec, so a JSON string is a legitimate shape here.
    """
    if plan is None:
        return {}
    metadata = plan.get("metadata") if isinstance(plan, Mapping) else getattr(plan, "metadata", None)
    if isinstance(metadata, (str, bytes)):
        try:
            metadata = json.loads(metadata)
        except (TypeError, ValueError):
            return {}
    return dict(metadata) if isinstance(metadata, Mapping) else {}


def plan_is_entitlement_only(plan: Any) -> bool:
    """True when the plan grants entitlement without funding a plan budget."""
    return bool(plan_metadata(plan).get(ENTITLEMENT_ONLY_KEY))


def plan_metadata_with_entitlement_only(
    metadata: Optional[Mapping[str, Any]], entitlement_only: Optional[bool]
) -> Optional[Dict[str, Any]]:
    """Fold the `entitlement_only` plan property into plan metadata.

    `None` leaves the metadata as given; `False` removes the key so the stored
    metadata only ever carries the flag when it is set.
    """
    if entitlement_only is None:
        return dict(metadata) if metadata is not None else None
    merged = dict(metadata or {})
    if entitlement_only:
        merged[ENTITLEMENT_ONLY_KEY] = True
    else:
        merged.pop(ENTITLEMENT_ONLY_KEY, None)
    return merged


def subscription_is_active(subscription: Any, now: datetime) -> bool:
    """True if the subscription is a chargeable, active, not-past-due paid plan.

    `now` must be timezone-aware UTC (the caller's clock)."""
    if not subscription:
        return False
    if getattr(subscription, "status", None) != "active":
        return False
    if int(getattr(subscription, "monthly_price_cents", 0) or 0) <= 0:
        return False
    due_at = getattr(subscription, "next_charge_at", None)
    if due_at is not None and due_at <= now:
        return False
    return True


def subscription_provides_plan_budget(subscription: Any, now: datetime, *, plan: Any = None) -> bool:
    """True if the subscription funds a separate plan budget: it is active, its
    provider is external (anything other than 'internal'), and its plan is not
    entitlement-only.

    `plan` is the subscription's plan row. Without it the plan's
    `entitlement_only` property cannot be seen and an active external
    subscription funds a plan budget.

    `now` must be timezone-aware UTC (the caller's clock)."""
    if not subscription_is_active(subscription, now):
        return False
    if (getattr(subscription, "provider", None) or "internal") == "internal":
        return False
    return not plan_is_entitlement_only(plan)


def subscription_needs_plan_lookup(subscription: Any, now: datetime) -> bool:
    """True when `subscription_provides_plan_budget` depends on the plan row,
    i.e. for an active external subscription with a plan_id. Callers use it to
    skip the plan read for everyone else."""
    return (
        subscription_is_active(subscription, now)
        and (getattr(subscription, "provider", None) or "internal") != "internal"
        and bool(getattr(subscription, "plan_id", None))
    )


def resolve_plan_id(*, role: str, has_active_subscription: bool, subscription: Any) -> Tuple[str, str]:
    """Resolve (plan_id, plan_source) from the user's role + subscription state.

    `role` must be the normalized lowercase role/user_type string. Order matters:
    role-derived plans (admin/anonymous) win over a subscription, then an active
    chargeable subscription, else the free baseline.
    """
    r = (role or "").strip().lower()
    if r in _ADMIN_ROLES:
        return PLAN_ADMIN, "role"
    if r == PLAN_ANONYMOUS:
        return PLAN_ANONYMOUS, "role"
    if has_active_subscription:
        return (getattr(subscription, "plan_id", None) or PLAN_WALLET), "subscription"
    return PLAN_FREE, "role"
