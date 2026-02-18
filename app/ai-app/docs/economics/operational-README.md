# Economics Operations (Schema + Jobs + Config)

This document describes the **tables**, **maintenance jobs**, **emailing**, and **runtime configuration** required for the economics subsystem to operate safely.

## Schema (Control Plane)

The authoritative schema is defined here:

- [deploy-kdcube-control-plane.sql](services/kdcube-ai-app/kdcube_ai_app/ops/deployment/sql/control_plane/deploy-kdcube-control-plane.sql)

Key table groups:

- **Quota policies**: `user_quota_policies`
- **Tier overrides**: `user_tier_overrides`
- **Lifetime credits**: `user_lifetime_credits`
- **Credit reservations**: `user_token_reservations`
- **Project budget**: `tenant_project_budget`, `tenant_project_budget_reservations`, `tenant_project_budget_ledger`
- **Subscription settings**: `user_subscription_budget_settings`
- **Subscription period budgets**: `user_subscription_period_budget`, `user_subscription_period_reservations`, `user_subscription_period_ledger`
- **Subscriptions**: `user_subscriptions`
- **Idempotency & audit**: `external_economics_events`

## Maintenance Jobs

### 1) Subscription rollover sweep

Purpose:

- Close subscription periods that have ended.
- Move unused subscription balance (balance - reserved) into project budget.
- Record idempotent internal events.

Entry point:

- `SubscriptionManager.sweep_due_subscription_rollovers(...)`
- Control plane endpoint: `POST /subscriptions/rollover/sweep`

Notes:

- Rollover is **idempotent** per period key.
- Rollover uses row locks and the `external_economics_events` table to prevent double processing.

### 2) Stripe pending reconcile

Purpose:

- Resolve pending wallet refunds and subscription cancels.
- Recover from missed webhooks.

Entry point:

- `StripeEconomicsAdminService.reconcile_pending_requests(...)`
- Control plane endpoint: `POST /stripe/reconcile`

### 3) Admin wallet refund and cancel flows

These are not scheduled jobs but operational actions:

- `POST /wallet/refund` — immediate credit removal; Stripe refund finalized via webhook or reconcile
- `POST /subscriptions/cancel` — cancel at period end; status finalized via webhook or reconcile

## Email Notifications

Admin alerts are sent for:

- Wallet refund requested/completed/failed
- Subscription cancel requested/completed/failed
- Reconcile results

Implementation:

- [email.py](services/kdcube-ai-app/kdcube_ai_app/infra/channel/email.py)

Required env vars are listed in the service config doc.

## Required Service Configuration

See the service configuration document and ensure these sections are set:

- [service-config-README.md](docs/service/service-config-README.md)

At minimum, economics requires:

- PostgreSQL (control plane schema)
- Redis (rate limiting + analytics)
- Stripe keys (if Stripe is enabled)
- Email config (admin notifications)

## Operational Checks

Recommended routine checks:

- Pending Stripe events: `GET /stripe/pending`
- Pending internal economics events: `GET /economics/pending`
- Subscription balances for paid users: `GET /subscriptions/user/{user_id}`
- Project budget balance: `GET /app-budget/status`

## Deployment Notes

If you need a clean reset of the control‑plane schema:

- Standard drop script: [drop-kdcube-control-plane.sql](services/kdcube-ai-app/kdcube_ai_app/ops/deployment/sql/control_plane/drop-kdcube-control-plane.sql)

Always re‑deploy using the authoritative schema file linked at the top.
