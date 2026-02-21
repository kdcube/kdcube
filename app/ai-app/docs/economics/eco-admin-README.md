# Economics Admin UI Guide

This document describes what can be done from the Economics admin dashboard and how each action affects the control plane.

UI file:
- [EconomicsDashboard.tsx](services/kdcube-ai-app/kdcube_ai_app/apps/chat/api/control_plane/EconomicsDashboard.tsx)

Backend routes:
- [control_plane.py](services/kdcube-ai-app/kdcube_ai_app/apps/chat/api/control_plane/control_plane.py)

## Overview of Admin Capabilities

From the Economics dashboard you can:

- Create and manage subscription plans (plan_id → price mapping)
- Create subscriptions (internal or Stripe)
- Look up subscription status and balances
- Manually top up subscription balances
- Manually renew internal subscriptions (top up + advance next charge)
- Reap expired subscription reservations (single user or all users in project)
- Sweep expired subscription periods (rollover to project budget)
- Top up the project budget
- Add lifetime credits (wallet)
- Refund wallet credits (Stripe refunds)
- Cancel Stripe subscriptions
- Reconcile pending Stripe requests
- View pending Stripe requests and pending economics events
- Manage plan quota policies and provider budget policies

## Terminology

- **Plan** = quota policy (limits for requests/tokens/concurrency).
- **Plan override** = temporary per‑user override of plan limits.
- **Lane** = `plan` lane or `paid` lane.

## Plan Quotas and Limits

### Plan quota policies

UI card: **Quota Policies**

- Policies are stored in `plan_quota_policies` and keyed by `plan_id`.
- These are the base limits used by the rate limiter.
- Plan overrides (`user_plan_overrides`) can temporarily replace these values per user.
- Quotas are enforced **per tenant/project** (global across bundles).
- Hourly limits use a **rolling 60‑minute** window; monthly limits use a **rolling 30‑day** window anchored to first usage per tenant/project; daily is **calendar day (UTC)**.
- Reservation floor is **per bundle**, configured via bundle props `economics.reservation_amount_dollars` (not in this UI).

### Bundle reservation floor (per bundle)

This is configured via bundle props (not the economics UI):

- Key: `economics.reservation_amount_dollars`
- Scope: tenant/project + bundle_id

Admin API (Integrations):
- `GET /admin/integrations/bundles/{bundle_id}/props`
- `POST /admin/integrations/bundles/{bundle_id}/props`

Special case (wallet + no subscription):
- Plan stays `free`, but **service limits** (requests/concurrency) are taken from `payasyougo`.
- **Token limits** still come from `free`.

Special case (subscription + wallet):
- Plan remains the subscription plan.
- **Subscription balance** is reserved up to what is available.
- **Wallet** covers any overflow for that turn.
- If actual spend exceeds both, project budget absorbs the remainder (shortfall note in ledger).
- If subscription funds **zero** for the turn, the request switches to **paid lane** and **payasyougo** quotas apply.
- Subscriptions and wallets never go negative; only project budget can absorb shortfalls.

### How plan limits are initialized

Plan quotas are seeded once by a master bundle:

- The entrypoint seeds from `app_quota_policies` if `plan_quota_policies` is missing records.
- After seeding, updates should be made in the admin UI.

If you change `app_quota_policies` in code, you must either:

- Update the policies directly in the admin UI, or
- Clear the table and let the master bundle re‑seed

## Subscription Plans

UI card: **Subscription Plans**

This is where you define plan metadata and (optionally) Stripe price mapping.

Fields:

- `plan_id` (required)
- `provider` (internal or stripe)
- `stripe_price_id` (required if provider is stripe)
- `monthly_price_cents`
- `active` flag

Plans are later referenced by subscriptions and used by plan quota policies.

## Subscription Management

### Create subscription

UI card: **Create Subscription**

- Internal subscription: creates a row in `user_subscriptions` with provider `internal`.
- Stripe subscription: creates a Stripe subscription and stores a snapshot.
- Plan ID is required and must exist in `subscription_plans`.

Backend: `POST /subscriptions/create`

### Lookup subscription

UI card: **Lookup Subscription (by user)**

- Shows subscription metadata and current period balance.
- Displays a Renew now button for internal subscriptions.

Backend: `GET /subscriptions/user/{user_id}`

## App Budget & Absorption Report

UI card: **Application Budget**

- Shows current balance, lifetime totals, and spending by bundle.
- Includes **Budget absorption report** (when project budget absorbs wallet/plan shortfalls).
- Includes subscription overage shortfalls (`shortfall:subscription_overage`) and free plan overages (`shortfall:free_plan`).

Backend:
- `GET /app-budget/balance`
- `POST /app-budget/topup`
- `GET /app-budget/absorption-report?period=day|month&days=90&group_by=none|user|bundle&format=json|csv`
- `GET /economics/request-lineage?request_id=turn_id`

Note: `request_id` is the **turn_id** from runtime logs.

### Renew internal subscription

UI button: **Renew now** (visible only for internal active subscriptions)

- Tops up the subscription period balance.
- Advances `next_charge_at`.

Backend: `POST /subscriptions/internal/renew-once`

### Manual subscription balance top‑up

UI card: **Subscription Balance Admin**

- Manually adds USD to the current subscription period budget.
- Optional Force topup allows multiple topups within the same period.

Backend: `POST /subscriptions/budget/topup`

Note: Manual top‑ups do not advance billing dates. Use Renew now for internal billing cycles.

Subscriptions do not support overdraft. Any shortfall beyond subscription + wallet is absorbed by the project budget (shortfall note in the ledger).

### Sweep expired subscription periods

UI card: **Subscription Rollovers**

- Closes any subscription periods whose `next_charge_at <= now`.
- Moves remaining balance to project budget.
- Idempotent per period.

Backend: `POST /subscriptions/rollover/sweep`

### Reap expired subscription reservations

UI card: **Reap Expired Subscription Reservations**

- Expires active reservation holds whose `expires_at` has passed.
- Releases held balance back to the subscription period.
- Can target a single user or sweep the entire tenant/project.

Backend:

- `POST /subscriptions/reservations/reap` (single user or explicit period)
- `POST /subscriptions/reservations/reap-all` (all users or all periods in project)

## Project Budget

### Top up project budget

UI card: **App Budget**

- Adds USD to project budget.
- Updates ledger and lifetime totals.

Backend: `POST /app-budget/topup`

## Wallet (Lifetime Credits)

### Add lifetime credits

UI card: **Lifetime Credits**

- Adds token credits based on USD amount and reference model pricing.

Backend: `POST /plan-override/add-lifetime-credits`

### Check lifetime balance

UI action: **Check balance**

Backend: `GET /plan-override/lifetime-balance/{user_id}`

### Wallet refund (Stripe)

UI card: **Wallet Refund (Stripe)**

- Removes credits immediately.
- Creates a Stripe refund request.
- Finalizes on webhook or reconcile.

Backend: `POST /wallet/refund`

## Stripe Operations

### Cancel Stripe subscription

UI card: **Cancel Stripe Subscription**

- Requests cancellation at period end.
- Balance remains usable until period closes.

Backend: `POST /subscriptions/cancel`

### Reconcile pending Stripe requests

UI card: **Reconcile Stripe Requests**

- Checks Stripe for pending refunds/cancels.
- Applies final status in control plane.

Backend: `POST /stripe/reconcile`

### View pending Stripe requests

UI card: **Pending Stripe Requests**

Backend: `GET /stripe/pending`

### View pending internal economics events

UI card: **Pending Economics Events**

Backend: `GET /economics/pending`

## Provider Budget Policies

UI card: **Budget Policies**

- Set per‑provider spend limits stored in `application_budget_policies`.

Backend: `POST /policies/budget`

## User Budget Breakdown

UI card: **Budget Breakdown**

- Shows base policy vs override vs effective policy, plus current usage and remaining headroom.
- Usage counters are **global per tenant/project** (bundles share the same quota pool).
- If you provide a **Bundle ID**, the UI shows **rolling reset timestamps**.
Use `__project__` for the global quota scope. Hourly reset uses a rolling 60‑minute window.
30‑day reset is anchored to first usage per tenant/project.

Backend: `GET /users/{user_id}/budget-breakdown` (optional `bundle_id`)

## Notes and Limits

- Manual subscription topups are per‑period by default; Force topup can override once‑per‑period guard.
- Stripe actions are idempotent via `external_economics_events`.
- Admin email notifications are sent for wallet refunds and subscription cancels/reconciles.

For Stripe details, see [stripe-README.md](stripe-README.md).
For operational procedures, see [operational-README.md](operational-README.md).
