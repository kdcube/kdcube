# Economics subsystem

This subsystem controls:
1) **Economic rate limiting** (requests/tokens/concurrency; per-user; tier-aware)
2) **Charging** (who pays for each request: user credits first, then project budget)
3) **Budgets** (tier quotas, tier overrides, user lifetime credits, project money)
4) **Top-ups** (admin top-ups, subscription top-ups, Stripe credit purchases)

It is designed so:
- **Rate limiting is fast** (Redis counters + Lua for atomic ops).
- **Money/accounting is correct** (PostgreSQL row locks + ledger).
- **User credits are concurrency-safe** (reservations prevent oversubscription).

---

## Namespaces (Redis)

All Redis keys must use `kdcube_ai_app.infra.namespaces.REDIS`.

Economics:
- `REDIS.ECONOMICS.RATE_LIMIT = "kdcube:economics:rl"`
- `REDIS.ECONOMICS.PROJ_BUDGET = "kdcube:economics:proj.budget"`
- `REDIS.ECONOMICS.TIER_BALANCE_CACHE = "kdcube:economics:tier.balance"`

Important: changing namespaces changes the Redis key-space (effectively resets usage counters unless you dual-read/migrate).

---

## Data model overview

### A) User tier overrides + credits (PostgreSQL)
**Tables:**
- `kdcube_control_plane.user_tier_overrides` — temporary quota overrides (expires).
- `kdcube_control_plane.user_lifetime_credits` — wallet credits (lifetime tokens).

### B) User credit reservations (PostgreSQL)
**Table:** `kdcube_control_plane.user_token_reservations`

This is how we prevent concurrent requests from spending the same user credits twice.
Reservations are short-lived (TTL) and are either:
- `reserved` → then later `committed` or `released`
- auto-ignored after expiry

### C) Plan quota policies (PostgreSQL)
**Table:** `kdcube_control_plane.plan_quota_policies`

- Base quota envelopes keyed by `plan_id`.
- Runtime resolves `plan_id` per request and applies these limits.

### D) Economic RL (Redis)
**Module:** `UserEconomicsRateLimiter`

Tracks (quota counters are global per tenant/project; accounting still uses bundle = product):
- Concurrency: ZSET lock (members have expiry timestamps)
- Requests: day / rolling‑30‑day / total counters
- Tokens: rolling‑hour / day / rolling‑30‑day counters
- Last turn: `last_turn_tokens`, `last_turn_at`
- Bundle index: `kdcube:economics:rl:bundles:{subject_id}` (set of bundle IDs, 90‑day TTL)

Policy source:
- Base policy comes from **plan_id** (not role).
- Optional tier override is pulled from `user_tier_overrides` and merged as **OVERRIDE** (not additive).

Window semantics:
- Hourly tokens: **rolling 60‑minute** window (minute buckets).
- Monthly requests/tokens: **rolling 30‑day** window anchored to first usage per tenant/project.
- Daily: calendar day (UTC).

### D) Project budget (money) (PostgreSQL + Redis analytics)
**Module:** `ProjectBudgetLimiter`

- **PostgreSQL**: `tenant_project_budget` (balance + reserved + overdraft_limit)
- **PostgreSQL**: `tenant_project_budget_reservations` (per-request holds)
- **PostgreSQL**: `tenant_project_budget_ledger` (signed ledger)

- **Redis**: analytics counters per bundle/provider (hour/day/month spend)

The money “source of truth” is PostgreSQL.
Redis is for fast spend reporting / analytics only.

---

# Phases

## Phase 1 — Economic Rate Limiting (Economical RL)

**Goal:** decide quickly if a request may start.

Inputs:
- `subject_id = "{tenant}:{project}:{user_id}"` (or session-scoped variant)
- `bundle_id` (product id for accounting/analytics)
- `base_policy` (derived from `plan_id`)
- optional `tier_override` (from `user_tier_overrides`)

Process:
1) Load tier override (if enabled) and compute **effective policy**:
    - If override exists and is not expired → override any configured limits.
    - Otherwise use base policy.

2) Read current counters from Redis (global quota scope):
   - Global quota counters use bundle id `__project__` in Redis keys.
    - reqs: day / rolling‑30‑day / total
    - toks: rolling‑hour / day / rolling‑30‑day

3) Check policy violations:
    - requests_per_day / month / total
    - tokens_per_hour / day / month

4) If allowed, attempt concurrency lock (Lua + ZSET):
    - ZREM expired holders (by score)
    - if ZCARD >= max_concurrent → deny
    - else ZADD(lock_id, expire_ts)

Output:
- `AdmitResult(allowed, reason, lock_id, snapshot, used_tier_override, effective_policy)`

Important semantics:
- Token limits are **post‑paid**: checks are based on committed counters from previous turns.
- Hourly window is rolling; monthly is rolling 30‑day (anchored to first usage per tenant/project).
- Concurrency lock is released at commit (or forced release on error).

---

## Phase 2 — How we charge the user

Charging happens after the model run, when we know the actual tokens spent.

Charging order:
1) **User lifetime credits** (if any) — token-based, depleting
2) **Project budget** — money-based, deducted from tenant/project balance

### 2.1 User lifetime credits (token budget)
Module: `UserCreditsManager`
- Reserve before work (optional but strongly recommended):
    - `reserve_lifetime_tokens(...)`
- Commit after we know actual token usage:
    - `commit_reserved_lifetime_tokens(...)`
- Any amount not covered becomes `overflow_tokens`.

If you skip reservations, use:
- `consume_lifetime_tokens(...)`  
  which will NOT steal from other active reservations.

### 2.2 Project budget (money)
Module: `ProjectBudgetLimiter`
- Recommended flow:
    - `reserve(amount_usd_estimate)` before running the model
    - `commit_reserved_spend(spent_usd_actual)` after completion
- Legacy flow:
    - `commit(spent_usd)` (no reservation)

If actual spend > reserved estimate, the delta is also charged and overdraft-checked.

Overdraft:
- overdraft_limit == NULL → unlimited negative allowed
- overdraft_limit == 0 → no overdraft allowed
- otherwise can go negative up to the limit

---

## Phase 3 — Budgets we have and how to manage them

We have 4 “budget dimensions”:

### 3.1 Base plan quotas (per plan_id)
Stored as policies (control plane):
- `plan_quota_policies` keyed by `plan_id`
- Enforced by Economic RL (Redis counters, global per tenant/project)

### 3.2 Tier override (temporary replacement)
Stored in `user_tier_overrides`, with optional expiry.
Semantics: **OVERRIDE**, not additive.

Use cases:
- trials
- promos
- compensation
- admin “grant user more limits for N days”

### 3.3 User lifetime credits (depleting token bucket)
Stored in `user_lifetime_credits`:
- lifetime_tokens_purchased
- lifetime_tokens_consumed

Concurrency-safe via `user_token_reservations`.

Use cases:
- one-time purchases (Stripe payments)
- manual grants

### 3.4 Project budget (money)
Stored in `tenant_project_budget` and ledger.
Used when:
- user credits are insufficient OR not present
- plan admits the request but funding is project‑paid (registered role)

Common management operations:
- top up
- set overdraft limit
- reconcile via ledger

---

## Phase 4 — Top-ups

### 4.1 Top up project budget (money)
- Admin endpoint: `/app-budget/topup`
- Subscription scheduler can do this monthly
- Stripe “invoice.paid” can also do this for subscriptions

### 4.2 Override tier (temporary)
- Admin endpoints:
    - `/tier-balance/grant-trial`
    - `/tier-balance/update` (partial updates)

### 4.3 Add user lifetime credits (token budget)
- Admin endpoint: `/tier-balance/add-lifetime-credits`
- Stripe `payment_intent.succeeded` (credits purchase)

---

# Integration points for Subscription + Payments

See:
- Subscription creation + monthly charging module (internal “imitated billing”)
- Stripe webhook handler (real money events)
- Control plane endpoints (manual admin operations)

Engineers integrating payment systems should treat:
- Project budget topups as “revenue credited”
- User lifetime credits as “prepaid token credits”
- Tier override as “temporary quota override”
