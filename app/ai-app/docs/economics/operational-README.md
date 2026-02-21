# Economics Operations (Schema + Jobs + Config)

This document describes the tables, maintenance jobs, emailing, and runtime configuration required for the economics subsystem to operate safely.

## Schema (Control Plane)

The authoritative schema is defined here:

- [deploy-kdcube-control-plane.sql](services/kdcube-ai-app/kdcube_ai_app/ops/deployment/sql/control_plane/deploy-kdcube-control-plane.sql)

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

### 1) Subscription rollover sweep

Purpose:

- Close subscription periods that have ended.
- Move unused subscription balance into project budget.
- Record idempotent internal events.

Entry point:

- `SubscriptionManager.sweep_due_subscription_rollovers(...)`
- Control plane endpoint: `POST /subscriptions/rollover/sweep`

Notes:

- Rollover is idempotent per period key.
- Rollover uses row locks and the `external_economics_events` table to prevent double processing.

### 2) Reap expired subscription reservations

Purpose:

- Clear stale reservation holds whose `expires_at` is in the past.
- Release held balance back to the subscription period.
- Prevents ghost reservations from blocking new requests.
 - Balances ignore expired reservations, but reaping keeps tables clean.

Entry points:

- Runtime entrypoint calls reaper before reading balances (best effort).
- Control plane endpoint: `POST /subscriptions/reservations/reap` (single user)
- Control plane endpoint: `POST /subscriptions/reservations/reap-all` (entire tenant/project)

### 3) Stripe pending reconcile

Purpose:

- Resolve pending wallet refunds and subscription cancels.
- Recover from missed webhooks.

Entry point:

- `StripeEconomicsAdminService.reconcile_pending_requests(...)`
- Control plane endpoint: `POST /stripe/reconcile`

### 4) Admin wallet refund and cancel flows

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
- Expired reservation cleanup: `POST /subscriptions/reservations/reap-all`
- Project budget balance: `GET /app-budget/status`

Redis note:
- Hourly token counters are stored as minute buckets under `toks:hour:bucket:{epoch_minute}` (rolling 60‑minute window).
- Global quota scope uses bundle id `__project__` (subject_id already includes tenant/project).
- Bundle index set is stored at `kdcube:economics:rl:bundles:{subject_id}` and refreshed on every commit (90‑day TTL).

## Deployment Notes

If you need a clean reset of the control‑plane schema:

- Standard drop script: [drop-kdcube-control-plane.sql](services/kdcube-ai-app/kdcube_ai_app/ops/deployment/sql/control_plane/drop-kdcube-control-plane.sql)

Always re‑deploy using the authoritative schema file linked at the top.
