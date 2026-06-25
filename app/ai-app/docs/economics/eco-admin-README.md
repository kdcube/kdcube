---
id: repo:kdcube-ai-app/app/ai-app/docs/economics/eco-admin-README.md
title: "Eco Admin"
summary: "Economics admin dashboard capabilities and how actions affect the control plane."
tags: ["economics", "admin", "control-plane", "subscriptions", "budgets"]
keywords: ["admin UI", "plans", "quotas", "limits", "wallets", "subscriptions"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/economics/eco-kickoff-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/economics/eco-test-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/economics/stripe-README.md
---
# Economics Admin UI Guide

This document describes what can be done from the Economics admin dashboard and how each action affects the control plane.

UI file:
- [EconomicsDashboard.tsx](../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/control_plane/EconomicsDashboard.tsx)

Backend routes:
- [control_plane.py](../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/control_plane/control_plane.py)

## Overview of Admin Capabilities

From the Economics dashboard you can:

- Create and manage subscription plans (plan_id → price mapping)
- Create subscriptions (internal or Stripe)
- Look up subscription status and balances
- Manually top up external (Stripe) subscription balances
- Reset an internal plan's quota windows (month + day + hour)
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
- **Funding split** = one pass: the primary source (subscription/project) covers `plan_part`, the wallet covers the over‑quota remainder.

## Plan Quotas and Limits

### Plan quota policies

UI card: **Quota Policies**

- Policies are stored in `plan_quota_policies` and keyed by `plan_id`.
- These are the base limits used by the rate limiter.
- Plan overrides (`user_plan_overrides`) can temporarily replace these values per user.
- Quotas are enforced **per tenant/project** (global across bundles).
- Hourly limits use a **rolling 60‑minute** window; daily limits use the **current 24‑hour quota period since the last daily reset**; monthly limits use the **current 30‑day quota period since the last monthly reset**.
- Reservation floor default comes from the economics descriptor (`reservation.chat`), editable in the **Reservation** card below, and overridable per bundle.

### Reservation floor (descriptor default + per-bundle override)

UI card: **Reservation**

The reservation floor (USD) sizes a turn's pre-run hold. It is resolved per turn from two layers:

1. **Descriptor default** — `reservation.<floor>` in the economics descriptor (e.g. `chat: 2.0`),
   seeded at deploy and read live per turn. Edit it from the **Reservation** card, which writes
   straight into the descriptor (survives a re-seed; bundles pick it up live):
   - `GET /economics/reservation` — current floors
   - `POST /economics/reservation` — set `{floor, amount}` (amount `<= 0` disables the floor)
   - `DELETE /economics/reservation/{floor}` — remove the floor (the surface inherits the default again)
2. **Per-bundle override** — `config.economics.reservation.<floor>` in `bundles.yaml` (legacy bundle
   prop `economics.reservation_amount_dollars` is still accepted), via the Integrations bundle props API:
   - `GET /admin/integrations/bundles/{bundle_id}/props`
   - `POST /admin/integrations/bundles/{bundle_id}/props`

Runtime resolution (chat): the bundle override wins if set; otherwise the descriptor default
applies. A positive value enables the floor (`est_turn_tokens = ceil(amount / (usd_per_token × SAFETY_MARGIN))`);
`<= 0` disables it (token-based estimate).

Special case (wallet + no subscription):
- Plan stays `free`, but **service limits** (requests/concurrency) are taken from `wallet`.
- **Token limits** still come from `free`.

Special case (subscription + wallet):
- Plan remains the subscription plan.
- **Subscription balance and subscription plan quota** cover the maximum eligible request share.
- **Wallet** covers only overflow; wallet-paid tokens do **not** consume subscription plan quota.
- If actual spend exceeds both plan funding and wallet, project budget absorbs the remainder (shortfall note in ledger). If plan quota remains, that absorbed fallback also consumes quota.
- If the subscription budget can't fully fund a turn, the over‑quota remainder is covered by the **wallet** via the unified split.
- Subscriptions and wallets never go negative; only project budget can absorb shortfalls.

### How plan limits are initialized

Plan quotas are **seeded at deploy time** from the economics descriptor
(`deployment/economics.yaml`) by the postgres-setup job 
(see [economics-descriptor-README.md](./economics-descriptor-README.md)). The mandatory
plans (`anonymous`/`free`/`wallet`/`admin`) have a built-in baseline (`DEFAULT_QUOTA_POLICIES`);
descriptor entries override it per field.

- `enforce: false` (default) seeds only missing entries, preserving operator/admin edits.
- `enforce: true` realigns every listed entity back to the descriptor.

After seeding, adjust limits in the admin UI, or re-run the seeder with an updated descriptor.
The legacy bundle-runtime seeder (`ensure_policies_initialized()`) is a deprecated no-op shim.

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

**Change guard.** Both admin create and user checkout pass through
`assert_plan_change_allowed`. A target that is a quota‑only policy (`wallet`,
`anonymous`) is always rejected (`409 target_not_subscribable`) — these are not
subscribable plans. Operator create skips the remaining self‑serve rules (an
operator may grant `admin`/`free` or move an `admin` user).

**Internal grant vs an existing Stripe row.** Granting an internal plan **does not
touch an `active` `provider='stripe'` subscription** (the operation no‑ops and
returns the unchanged row). A **non‑active** Stripe row (`canceled`/`past_due`/…)
**can** be overridden onto an internal plan — the row flips to `provider='internal'`
and its Stripe ids are cleared. So to move a Stripe subscriber to internal, cancel
the Stripe subscription first, then grant internal.

### Lookup subscription

UI card: **Lookup Subscription (by user)**

- Shows subscription metadata and current period balance.
- Displays a Reset quota button for internal subscriptions.

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
- `GET /economics/request-lineage?request_id=<request_id>`

Note: chat requests use the runtime `turn_id` as `request_id`; non-chat top-level flows use their own stable accountable request id.

### Reset internal quota

UI button: **Reset quota** (visible for internal active subscriptions)

- Re-anchors the monthly and daily windows to now and clears the hour buckets.
- All rolling request/token counters (month, day, hour) start fresh.

Backend: `POST /subscriptions/internal/reset-quota`

Internal plans carry no plan budget. They draw from the project budget bounded by their quota; their allowance is the rolling RL quota.

### Manual subscription balance top‑up

UI card: **Subscription Balance Admin**

- Adds USD to the current subscription period budget.
- Optional Force topup allows multiple topups within the same period.
- Applies only to external (Stripe) subscriptions. Internal subscriptions return `400` (use Reset quota).

Backend: `POST /subscriptions/budget/topup`

Note: Manual top‑ups do not advance billing dates.

External subscription budgets do not support overdraft. Any shortfall beyond subscription + wallet is absorbed by the project budget (shortfall note in the ledger).

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
 - Role is auto‑resolved from the user’s cached session; if no session exists, it falls back to subscription/wallet detection.
Use `__project__` for the global quota scope. Hourly reset uses a rolling 60‑minute window.
30‑day reset is anchored to first usage per tenant/project.

Backend: `GET /users/{user_id}/budget-breakdown` (optional `bundle_id`)

## Notes and Limits

- Manual subscription topups are per‑period by default; Force topup can override once‑per‑period guard.
- Stripe actions are idempotent via `external_economics_events`.
- Admin email notifications are sent for wallet refunds and subscription cancels/reconciles.

For Stripe details, see [stripe-README.md](stripe-README.md).
For operational procedures, see [operational-README.md](operational-README.md).
