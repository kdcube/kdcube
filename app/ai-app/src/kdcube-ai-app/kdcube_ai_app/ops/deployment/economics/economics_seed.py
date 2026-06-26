# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

# ops/deployment/economics/economics_seed.py

"""
Economics seeder (raw SQL, psycopg2 — runs in the postgres-setup job).

Single responsibility: seed default economics data per tenant/project AFTER the
schema is provisioned. Schema deployment lives in db_deployment.py; this module
only writes data rows. See docs/economics/economics-descriptor-README.md.

seed_economics(): descriptor (+ baked-in baseline) -> DB.

Entities:
  - quota_policies          (4 mandatory baked-in plans + descriptor extras)
  - application_budget_policies   (descriptor opt-in)
  - plans                         (anonymous/free/admin/wallet baked-in + descriptor extras)
  - tenant_project_budget         (overdraft limit only; balance untouched)
Reservation floors are runtime config (not seeded).
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from psycopg2.extras import Json

from kdcube_ai_app.ops.deployment.sql.db_deployment import project_schema
from kdcube_ai_app.apps.chat.sdk.infra.economics.defaults import (
    DEFAULT_QUOTA_POLICIES,
    MANDATORY_QUOTA_PLAN_IDS,
    DEFAULT_PLANS,
    MANDATORY_PLAN_IDS,
)

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

_SEED_CREATED_BY = "economics_seed"
_SEED_NOTES = "Seeded from economics.yaml descriptor"

_DEFAULT_RESERVATION = {"chat": {"amount": 2.0, "active": True}}


def _log(msg: str) -> None:
    import sys
    print(f"[economics_seed] {msg}", file=sys.stderr, flush=True)


# ─── descriptor loading ───────────────────────────────────────────────────────

def _resolve_descriptor_path(explicit: Optional[str] = None) -> Optional[Path]:
    """economics.yaml location: explicit arg > env > descriptors dir > /config."""
    candidates = []
    if explicit:
        candidates.append(explicit)
    env_path = str(os.getenv("ECONOMICS_YAML_DESCRIPTOR_PATH") or "").strip()
    if env_path:
        candidates.append(env_path)
    descriptors_dir = str(os.getenv("PLATFORM_DESCRIPTORS_DIR") or "").strip()
    if descriptors_dir:
        candidates.append(str(Path(descriptors_dir) / "economics.yaml"))
    candidates.append("/config/economics.yaml")

    for raw in candidates:
        if not raw:
            continue
        if raw.startswith("file://"):
            from urllib.parse import unquote, urlparse
            raw = unquote(urlparse(raw).path)
        p = Path(raw).expanduser()
        if p.exists():
            return p
    return None


def load_economics_descriptor(path: Optional[str] = None) -> Dict[str, Any]:
    """Parse economics.yaml. Returns {} when no file is present."""
    resolved = _resolve_descriptor_path(path)
    if resolved is None:
        _log("No economics.yaml found; seeding mandatory quota baseline only.")
        return {}
    try:
        data = yaml.safe_load(resolved.read_text()) or {}
        _log(f"Loaded descriptor: {resolved}")
        return data if isinstance(data, dict) else {}
    except Exception as e:
        _log(f"WARN failed to parse {resolved}: {e}; baseline only.")
        return {}


def _effective_quota_policies(descriptor: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Built-in baseline for the four mandatory plans, overridden per field by the
    descriptor; any extra plan_id in the descriptor is included as-is.
    """
    desc_quota = dict(descriptor.get("quota_policies") or {})
    effective: Dict[str, Dict[str, Any]] = {}

    for plan_id in MANDATORY_QUOTA_PLAN_IDS:
        base = dataclasses.asdict(DEFAULT_QUOTA_POLICIES[plan_id])
        override = desc_quota.get(plan_id) or {}
        # Field present in the descriptor wins (including explicit null).
        for k in _QUOTA_FIELDS:
            if k in override:
                base[k] = override[k]
        effective[plan_id] = {k: base.get(k) for k in _QUOTA_FIELDS}

    for plan_id, override in desc_quota.items():
        if plan_id in effective:
            continue
        override = override or {}
        effective[plan_id] = {k: override.get(k) for k in _QUOTA_FIELDS}

    return effective


def _effective_plans(descriptor: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Built-in baseline for the mandatory plans (anonymous, free, admin, wallet),
    overridden per field by the descriptor; any extra plan_id in the descriptor
    is included as-is.
    """
    desc_plans = dict(descriptor.get("plans") or {})
    effective: Dict[str, Dict[str, Any]] = {}

    for plan_id in MANDATORY_PLAN_IDS:
        base = dict(DEFAULT_PLANS[plan_id])
        # Field present in the descriptor wins (including explicit null).
        base.update(desc_plans.get(plan_id) or {})
        effective[plan_id] = base

    for plan_id, override in desc_plans.items():
        if plan_id in effective:
            continue
        effective[plan_id] = dict(override or {})

    return effective


# ─── generic upsert ────────────────────────────────────────────────────────────

def _upsert(mgr, schema: str, table: str, conflict_cols: tuple, row: Dict[str, Any], enforce: bool) -> None:
    cols = list(row.keys())
    placeholders = ", ".join(["%s"] * len(cols))
    col_sql = ", ".join(cols)
    conflict_sql = ", ".join(conflict_cols)

    if enforce:
        update_cols = [c for c in cols if c not in conflict_cols]
        set_sql = ", ".join([f"{c} = EXCLUDED.{c}" for c in update_cols] + ["updated_at = NOW()"])
        conflict_action = f"DO UPDATE SET {set_sql}"
    else:
        conflict_action = "DO NOTHING"

    sql = (
        f"INSERT INTO {schema}.{table} ({col_sql}) VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict_sql}) {conflict_action}"
    )
    mgr.execute_sql(sql, data=tuple(row[c] for c in cols))


# ─── seed ───────────────────────────────────────────────────────────────────────

def seed_economics(tenant: str, project: str, *, mgr=None, path: Optional[str] = None) -> None:
    """
    Seed default economics for tenant/project. Idempotent: per-entity
    reconciliation, never a global gate. enforce (from the descriptor) decides
    DO NOTHING vs full realignment.
    """
    from kdcube_ai_app.infra.relational.psql.psql_base import PostgreSqlDbMgr

    mgr = mgr or PostgreSqlDbMgr()
    schema = project_schema(tenant, project)
    descriptor = load_economics_descriptor(path)
    enforce = bool(descriptor.get("enforce", False))
    _log(f"Seeding {tenant}/{project} (schema={schema}, enforce={enforce})")

    # 1) quota policies — always includes the four baked-in plans.
    quota = _effective_quota_policies(descriptor)
    for plan_id, fields in quota.items():
        row = {"tenant": tenant, "project": project, "plan_id": plan_id, **fields,
               "created_by": _SEED_CREATED_BY, "notes": _SEED_NOTES}
        _upsert(mgr, schema, "plan_quota_policies", ("tenant", "project", "plan_id"), row, enforce)
    _log(f"quota_policies: {len(quota)} ({', '.join(quota)})")

    # 2) provider budget policies — descriptor opt-in only.
    budget = dict(descriptor.get("budget_policies") or {})
    for provider, fields in budget.items():
        fields = fields or {}
        row = {"tenant": tenant, "project": project, "provider": provider,
               **{k: fields.get(k) for k in _BUDGET_FIELDS},
               "created_by": _SEED_CREATED_BY, "notes": _SEED_NOTES}
        _upsert(mgr, schema, "application_budget_policies", ("tenant", "project", "provider"), row, enforce)
    if budget:
        _log(f"budget_policies: {len(budget)} ({', '.join(budget)})")

    # 3) plans catalog — always includes the baked-in anonymous/free/admin/wallet plans.
    plans = _effective_plans(descriptor)
    for plan_id, fields in plans.items():
        fields = fields or {}
        metadata = fields.get("metadata")
        row = {"tenant": tenant, "project": project, "plan_id": plan_id,
               "provider": fields.get("provider", "internal"),
               "stripe_price_id": fields.get("stripe_price_id"),
               "monthly_price_cents": int(fields.get("monthly_price_cents") or 0),
               "active": bool(fields.get("active", True)),
               "metadata": Json(metadata) if metadata is not None else None,
               "created_by": _SEED_CREATED_BY, "notes": _SEED_NOTES}
        _upsert(mgr, schema, "plans", ("tenant", "project", "plan_id"), row, enforce)
    _log(f"plans: {len(plans)} ({', '.join(plans)})")

    # 4) project budget — overdraft limit only; balance is never written here.
    pb = descriptor.get("project_budget")
    if isinstance(pb, dict) and "overdraft_limit_usd" in pb:
        od_usd = pb.get("overdraft_limit_usd")
        od_cents = None if od_usd is None else int(round(float(od_usd) * 100))
        row = {"tenant": tenant, "project": project, "overdraft_limit_cents": od_cents}
        _upsert(mgr, schema, "tenant_project_budget", ("tenant", "project"), row, enforce)
        _log(f"project_budget.overdraft_limit_cents = {od_cents}")

    _log("Seeding complete.")
