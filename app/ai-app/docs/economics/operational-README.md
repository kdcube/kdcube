---
id: ks:docs/economics/operational-README.md
title: "Operational"
summary: "Operational requirements for economics: schemas, jobs, and configuration."
tags: ["economics", "operations", "maintenance", "control-plane"]
keywords: ["database schema", "maintenance jobs", "emailing", "plan policies", "runtime config"]
see_also:
  - ks:docs/economics/economic-README.md
  - ks:docs/economics/stripe-README.md
  - ks:docs/economics/eco-admin-README.md
  - ks:docs/service/configuration/service-config-README.md
---
# Economics Operations (Schema + Jobs + Config)

This document describes the tables, maintenance jobs, emailing, and runtime configuration required for the economics subsystem to operate safely.

## Schema (Control Plane)

The authoritative schema is defined here:

- [deploy-kdcube-control-plane.sql](../../services/kdcube-ai-app/kdcube_ai_app/ops/deployment/sql/control_plane/deploy-kdcube-control-plane.sql)

Key table groups:

- Quota policies: `plan_quota_policies`
- Plan overrides: `user_plan_overrides`
- Lifetime credits: `user_lifetime_credits`
- Credit reservations: `user_token_reservations`
- Project budget: `tenant_project_budget`, `tenant_project_budget_reservations`, `tenant_project_budget_ledger`
- Subscription plans: `subscription_plans`
- Subscriptions: `user_subscriptions`
- Subscription period budgets: `user_subscription_period_budget`, `user_subscription_period_reservations`, `user_subscription_period_ledger`
- Idempotency and audit: `external_economics_events`

## Plan Policy Seeding

Plan quotas are seeded once by a master bundle:

- `ensure_policies_initialized()` inserts defaults from `app_quota_policies` into `plan_quota_policies` if missing.
- After the first seed, use the admin UI to adjust limits.
- If you change code defaults, update DB policies in the admin UI or clear the table to re‑seed.

Window semantics (global per tenant/project):
- Hourly tokens: rolling 60‑minute window (minute buckets).
- Monthly requests/tokens: rolling 30‑day window anchored to first usage per tenant/project.
- Daily: calendar day (UTC).
- Reservation floor is configured per bundle via props `economics.reservation_amount_dollars`.

Funding split (runtime):
- If a user has a subscription **and** a wallet, subscription balance is reserved up to available and wallet covers overflow for that turn.
- If actual spend exceeds both plan funding and wallet, project budget absorbs the remainder (shortfall note in ledger).
- If subscription funds **zero** for a turn, the request switches to **paid lane** and **payasyougo** quotas apply.
- Subscriptions and wallets never go negative; only project budget can absorb shortfalls.

Absorption reporting:
- Project budget absorption events are written to `tenant_project_budget_ledger` with notes `shortfall:wallet_subscription`, `shortfall:wallet_paid`, `shortfall:wallet_plan`, `shortfall:subscription_overage`, `shortfall:free_plan`.
- View: `kdcube_control_plane.tenant_project_budget_absorption`
- Detail view: `kdcube_control_plane.tenant_project_budget_absorption_detail`
- API: `GET /app-budget/absorption-report?period=day|month&days=90&group_by=none|user|bundle&format=json|csv`

Request lineage:
- `GET /economics/request-lineage?request_id=turn_id`

## Maintenance Jobs

### 1) Stripe reconcile (scheduled)

Purpose:

- Replay Stripe events that were missed due to service downtime, restarts, or webhook delivery failures.
- Recover wallet top-ups and subscription updates that Stripe fired but the service never processed.

Entry points:

- Automatic: `stripe_reconcile_scheduler_loop()` — background asyncio task, started at app lifespan.
- Manual trigger: `POST /api/economics/admin/stripe/reconcile`
- Code: `apps/chat/api/economics/routines.py` → `run_stripe_reconcile_sweep_once()`

Configuration (env vars):

| Variable | Default | Purpose |
|----------|---------|---------|
| `STRIPE_RECONCILE_ENABLED` | `true` | Enable/disable the scheduler |
| `STRIPE_RECONCILE_CRON` | `45 * * * *` | Cron schedule (UTC) |
| `STRIPE_RECONCILE_LOCK_TTL_SECONDS` | `900` | Distributed lock TTL |

Redis keys:

| Key | Purpose |
|-----|---------|
| `stripe:reconcile:{tenant}:{project}` | Distributed lock (NX + TTL) — prevents concurrent runs |
| `stripe:reconcile:last_ts:{tenant}:{project}` | Watermark: Unix timestamp of last processed Stripe event |

Notes:

- Events are fetched from Stripe ordered ascending by `created` (oldest first) to guarantee
  causal replay order (e.g. `invoice.paid` before `customer.subscription.updated`).
- First run defaults watermark to `now - 24h`.
- After each sweep, watermark is advanced to `max(event.created)` from the batch.
- All event processing is idempotent via `external_economics_events` (keyed by Stripe event ID).

### 2) Subscription rollover sweep (scheduled)

Purpose:

- Close subscription periods that have ended.
- Move unused subscription balance into project budget.
- Record idempotent internal events.

Entry points:

- Automatic: `subscription_rollover_scheduler_loop()` — background asyncio task, started at app lifespan.
- Manual trigger: `POST /api/economics/admin/subscriptions/rollover/sweep`
- Code: `apps/chat/api/economics/routines.py` → `run_subscription_rollover_sweep_once()`

Configuration (env vars):

| Variable | Default | Purpose |
|----------|---------|---------|
| `SUBSCRIPTION_ROLLOVER_ENABLED` | `true` | Enable/disable the scheduler |
| `SUBSCRIPTION_ROLLOVER_CRON` | `15 * * * *` | Cron schedule (UTC) |
| `SUBSCRIPTION_ROLLOVER_LOCK_TTL_SECONDS` | `900` | Distributed lock TTL |
| `SUBSCRIPTION_ROLLOVER_SWEEP_LIMIT` | `500` | Max subscriptions per invocation |

Redis keys:

| Key | Purpose |
|-----|---------|
| `subscription:rollover:{tenant}:{project}` | Distributed lock (NX + TTL) — prevents concurrent runs |

Notes:

- Rollover is idempotent per period key.
- Sweeps in batches of `SUBSCRIPTION_ROLLOVER_SWEEP_LIMIT`; loops until batch < limit.
- Uses row locks and `external_economics_events` to prevent double processing.

### 3) Reap expired subscription reservations

Purpose:

- Clear stale reservation holds whose `expires_at` is in the past.
- Release held balance back to the subscription period.
- Prevents ghost reservations from blocking new requests.
- Balances ignore expired reservations, but reaping keeps tables clean.

Entry points:

- Runtime entrypoint calls reaper before reading balances (best effort).
- `POST /api/economics/admin/subscriptions/reservations/reap` (single user)
- `POST /api/economics/admin/subscriptions/reservations/reap-all` (entire tenant/project)

### 4) Admin wallet refund and cancel flows

These are not scheduled jobs but operational actions:

- `POST /api/economics/admin/wallet/refund` — immediate credit removal; Stripe refund finalized via webhook or reconcile
- `POST /api/economics/admin/subscriptions/cancel` — cancel at period end; status finalized via webhook or reconcile

## Email Notifications

Admin alerts are sent for:

- Wallet refund requested/completed/failed
- Subscription cancel requested/completed/failed
- Reconcile results

Implementation:

- [email.py](../../services/kdcube-ai-app/kdcube_ai_app/infra/channel/email.py)

Required env vars are listed in the service config doc.

## Required Service Configuration

See the service configuration document and ensure these sections are set:

- [service-config-README.md](../service/service-config-README.md)

At minimum, economics requires:

- PostgreSQL (control plane schema)
- Redis (rate limiting + analytics)
- Stripe keys (if Stripe is enabled)
- Email config (admin notifications)

## Operational Checks

Recommended routine checks:

- Pending Stripe events: `GET /api/economics/admin/stripe/pending`
- Subscription balances for paid users: `GET /api/economics/admin/subscriptions/user/{user_id}`
- Expired reservation cleanup: `POST /api/economics/admin/subscriptions/reservations/reap-all`
- Project budget balance: `GET /api/economics/admin/app-budget/status`

## Redis Keys Reference

### Rate limiting

| Key pattern | Purpose |
|-------------|---------|
| `toks:hour:bucket:{epoch_minute}` | Hourly token counter (rolling 60-min window, minute buckets) |
| `kdcube:economics:rl:bundles:{subject_id}` | Bundle index set; refreshed on every commit (90-day TTL) |

Global quota scope uses bundle id `__project__` (subject_id already includes tenant/project).

### Scheduler distributed locks and state

| Key pattern | Job | Purpose |
|-------------|-----|---------|
| `stripe:reconcile:{tenant}:{project}` | Stripe reconcile | Distributed lock (NX + TTL) |
| `stripe:reconcile:last_ts:{tenant}:{project}` | Stripe reconcile | Watermark: last processed Stripe event timestamp |
| `subscription:rollover:{tenant}:{project}` | Subscription rollover | Distributed lock (NX + TTL) |

Lock TTLs are configured via `STRIPE_RECONCILE_LOCK_TTL_SECONDS` and
`SUBSCRIPTION_ROLLOVER_LOCK_TTL_SECONDS` (default: 900s each).

## Deployment Notes

If you need a clean reset of the control‑plane schema:

- Standard drop script: [drop-kdcube-control-plane.sql](../../services/kdcube-ai-app/kdcube_ai_app/ops/deployment/sql/control_plane/drop-kdcube-control-plane.sql)

Always re‑deploy using the authoritative schema file linked at the top.
