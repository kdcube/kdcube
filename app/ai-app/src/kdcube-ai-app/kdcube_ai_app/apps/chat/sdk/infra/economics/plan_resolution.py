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
  - subscription_provides_plan_budget(): True only for an active EXTERNAL subscription;
    False for internal subscriptions and for no subscription.
  - resolve_plan_id(): maps a user's normalized role + subscription state onto the
    platform plan-id the runtime resolves them into.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Tuple

# Platform plan-id vocabulary (the ids the runtime resolves users into).
# `wallet` is the quota-only paid lane and the fallback plan_id for a chargeable
# subscription whose plan_id is null; it is never a user->plan mapping itself.
PLAN_ADMIN = "admin"
PLAN_ANONYMOUS = "anonymous"
PLAN_FREE = "free"
PLAN_WALLET = "wallet"

_ADMIN_ROLES = ("privileged", "admin")


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


def subscription_provides_plan_budget(subscription: Any, now: datetime) -> bool:
    """True if the subscription funds a separate plan budget, i.e. it is active and
    its provider is external (anything other than 'internal').

    `now` must be timezone-aware UTC (the caller's clock)."""
    if not subscription_is_active(subscription, now):
        return False
    return (getattr(subscription, "provider", None) or "internal") != "internal"


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
