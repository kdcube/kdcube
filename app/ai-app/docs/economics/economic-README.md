---
id: repo:kdcube-ai-app/app/ai-app/docs/economics/economic-README.md
title: "Economic"
summary: "Authoritative economics model and runtime enforcement rules."
tags: ["economics", "model", "runtime", "control-plane"]
keywords: ["plans", "wallets", "funding split", "rate limits", "charging", "project scope"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/economics/economic-enforcement-engine-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/economics/economics-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/economics/economics-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/economics/economics-usage.md
  - repo:kdcube-ai-app/app/ai-app/docs/economics/eco-admin-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/economics/eco-kickoff-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/economics/eco-test-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/economics/operational-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/economics/stripe-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/economics/rate-limit-simulation-playbook.md
  - repo:kdcube-ai-app/app/ai-app/docs/accounting/accounting-README.md
---
# Economics Model (Control Plane)

This document is the authoritative description of the current economics model and how it is enforced at runtime.
It replaces the older usage notes and reflects the production app flow and control‑plane schema.

## Code map

Where each mechanism described below lives (paths under
`src/kdcube-ai-app/kdcube_ai_app/`):

- [entrypoint_with_economic.py](../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/chatbot/entrypoint_with_economic.py) —
  the economics-aware chat entrypoint: pre-run estimate → admit → reserve,
  post-run accounting → settle, for every chat turn.
- [enforcement.py](../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/infra/economics/enforcement.py) —
  **`EconomicsGuard`**: the same enforcement as an async context manager for any
  accountable flow (API operation, background job, standalone search). Entry:
  estimate → resolve plan/funding → admit → reserve → bind accounting; exit:
  aggregate → settle.
- [funding_flow.py](../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/infra/economics/funding_flow.py) —
  the unified funding split: estimate, reservation, `settle_plan_funding`.
- [settlement_allocation.py](../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/infra/economics/settlement_allocation.py) —
  the plan-first / wallet-overflow settlement split and shortfall tagging.
- [limiter.py](../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/infra/economics/limiter.py) —
  `UserEconomicsRateLimiter`: quota admission (tokens, requests, concurrency).
- [quota_lock.py](../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/infra/economics/quota_lock.py) —
  distributed admit→reserve serialization so concurrent turns cannot
  double-spend quota headroom.
- [plan_resolution.py](../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/infra/economics/plan_resolution.py) —
  `resolve_plan_id` and `subscription_is_active` (the shared plan-resolution
  rules documented below).
- [defaults.py](../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/infra/economics/defaults.py) —
  `DEFAULT_QUOTA_POLICIES`, the built-in plan baseline the descriptor overlays.
- [descriptor.py](../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/infra/economics/descriptor.py) —
  runtime reader of the economics descriptor (`reservation.chat`,
  `price_tables`, reference service).
- [usage.py](../../src/kdcube-ai-app/kdcube_ai_app/infra/accounting/usage.py) —
  `price_table()` / `DEFAULT_PRICE_TABLE` and the USD↔reference-token
  conversion used for reservations and quota units.
- [project_budget.py](../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/infra/economics/project_budget.py) /
  [subscription_budget.py](../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/infra/economics/subscription_budget.py) —
  project and subscription budget limiters (holds, ledgers, absorption).
- [subscription.py](../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/infra/economics/subscription.py) —
  subscription periods, top-ups, rollovers (`SubscriptionManager`).
- [user_budget.py](../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/infra/economics/user_budget.py) —
  the quota-equivalent budget breakdown behind the user billing widget.
- [economics_seed.py](../../src/kdcube-ai-app/kdcube_ai_app/ops/deployment/economics/economics_seed.py) —
  deploy-time seeder: descriptor → control-plane rows (see the seeding flow
  below).

## Related economics docs

- [economic-enforcement-engine-README.md](./economic-enforcement-engine-README.md) — the enforcement engine deep dive: `EconomicsSubject`, admission/reservation/settlement lifecycle, `[economics.enforcement]` tracing.
- [economics-descriptor-README.md](./economics-descriptor-README.md) — the `economics.yaml` descriptor: seeded vs runtime-read sections, `price_tables`, `reservation`, plans.
- [economics-events-README.md](./economics-events-README.md) — the SSE events (`rate_limit.*`, snapshots, absorption) clients render.
- [economics-usage.md](./economics-usage.md) — Redis key map and where money truth lives (PostgreSQL) vs fast counters (Redis).
- [eco-admin-README.md](./eco-admin-README.md) — admin operations: plans, overrides, budgets, absorption report.
- [eco-kickoff-README.md](./eco-kickoff-README.md) — funding-source and plan model orientation.
- [eco-test-README.md](./eco-test-README.md) — how to verify enforcement behavior end to end.
- [operational-README.md](./operational-README.md) — request lineage and operational checks.
- [stripe-README.md](./stripe-README.md) — external subscriptions, checkout, webhooks.
- [rate-limit-simulation-playbook.md](./rate-limit-simulation-playbook.md) — simulating limits before changing them.
- [../accounting/accounting-README.md](../accounting/accounting-README.md) — the usage events economics consumes: tracking decorators, storage, aggregation, pricing.

## Scope

The economics subsystem covers:

- Rate limits and quotas (requests, tokens, concurrency)
- Funding sources (subscription budget, project budget, wallet credits)
- Reservation semantics for correctness under concurrency
- Accounting and cost attribution
- Subscription period management and rollovers

## Core Concepts

### Terminology

- **Plan** = quota policy identity (limits for requests/tokens/concurrency).
- **Plan override** = temporary per‑user override of plan limits.
- **Funding source** = the plan's primary budget: `subscription` (active **external** subscription) or `project` (everyone else, including **internal** subscriptions). The **wallet** is never a primary source — it covers the over‑quota/over‑funds remainder.

Unified funding split (important):
- Every request is **one split**: `plan_part = min(R, Q, P)` is funded by the primary source (bounded by the remaining quota `Q` **and** the primary funds `P`); `wallet_part = R − plan_part` is covered by the wallet.
- When the plan quota/funds run out, `plan_part` simply shrinks (to 0) and the wallet covers the rest within the same pass.
- An **external (Stripe) subscription** uses **subscription funding** as its primary. A registered/free user and an **internal subscription** use **project funding**: internal plans carry no plan budget and draw from the project budget bounded by quota.

Tracing a single request:
- Chat requests use the chat **turn_id** as `request_id`.
- Non-chat top-level flows use their own stable accountable request id.
- Use `GET /economics/request-lineage?request_id=<request_id>` to fetch ledger + reservation rows.

### Role (economics role)

Authority is the funding access decision, not the quota policy. Modern flows
derive economics authority from the authenticated principal and any Connection
Hub delegation edge, then pass an `EconomicsSubject` into enforcement. Runtime
`user_type` is not economics authority. Queue/session labels may exist in older
runtime schemas, but delegated/channel-owned work must charge from the
`EconomicsSubject` projected out of Connection Hub authority context.
The `external` session label means “authenticated by an integration/channel but
not projected to a platform user”; it is treated as no platform economics
authority until Connection Hub provides a platform/grantor projection.

SDK operation visibility follows the same rule. `user_types` declarations on
older decorators/descriptors are not authorization. Central app-operation and
Data Bus dispatch ignore `user_types`; surfaces must use roles and/or
authority/grant requirements. This keeps Telegram, delegated-client, browser,
and background-job executions on the same authority model.

Admin UI role resolution:
- The admin endpoints auto‑resolve role from the user’s cached session in Redis.
- If the user has never logged in (no session), role falls back to subscription/wallet detection (paid) or `registered`.

### Plan (quota policy identity)

Plan (`plan_id`) is the quota policy identity used by the rate limiter. It is distinct from role.

Plan is resolved in the entrypoint at request time. The active plan determines base quotas via `plan_quota_policies`.

### Funding Sources

Funding sources are money or tokens used to pay for requests:

- Subscription period budget (per‑month balance, USD)
- Wallet or lifetime credits (USD balance, stored in cents)
- Project budget (tenant/project balance, USD)

Role determines which funding sources are allowed, while plan determines rate limits.

## Plan Resolution (Runtime)

Plan resolution is performed in the entrypoint using the following priority:

1. `privileged/admin` → `admin`
2. `anonymous` → `anonymous`
3. active subscription → `subscription.plan_id`
4. default → `free`

Special handling for wallet users with no subscription:

- The **plan stays `free`**, but **service limits** (requests/concurrency) are taken from the `wallet` plan.
- **Token limits** still come from the `free` plan.
- When the free token quota is insufficient and a wallet exists, the over‑quota remainder is covered by the **wallet** via the unified split.

**What "active subscription" means** (`subscription_is_active`): a **chargeable**
(`monthly_price_cents > 0`), `status='active'`, **not `past_due`** row within its
billing period. Zero‑cost baseline rows (`free`/`admin`) are intentionally *not*
"active" here — only a chargeable plan flips a user onto a subscription plan.

**Baseline rows.** Every authenticated user gets a `user_plans` row on
first session (zero‑cost `free`, or `admin` for privileged) via a
post‑session‑create hook. Resolution always reads the current row; anonymous users
get no row.

**No downgrade sweep.** The plan is computed **live** from the row on every
request. A `canceled` or `past_due` subscription therefore resolves to `free`
everywhere (widget, enforcement, profile) immediately — the row stays for history,
but no background job is needed to "downgrade" users.

Visual summary:

```text
request arrives
      |
      v
    role?
      |-- admin/privileged --------> plan_id = admin
      |-- anonymous ---------------> plan_id = anonymous
      |-- other
            |
            v
      active subscription?
            |-- yes --------------> plan_id = subscription.plan_id
            |-- no
                  |
                  v
            plan_id = free
                  |
                  v
            wallet exists? -- yes -> wallet service limits
                                     + free token limits
```

## Where Limits Come From (Plan Quotas)

Plan quotas are stored in the control plane table `plan_quota_policies`.

**Scope and window semantics (important):**
- Quotas are enforced **per tenant/project** (global across apps).
- Hourly token limits use a **rolling 60‑minute** window (minute buckets).
- Daily limits use the **current 24‑hour quota period since the last daily reset**.
- Monthly limits use the **current 30‑day quota period since the last monthly reset**.
- Total requests do not reset.
Reservation amount configuration:
- Per‑app fixed reservation is set via the app's properties (bundle props): `economics.reservation.chat`
  (scalar USD; a `{amount: ...}` object is tolerated; a value ≤ 0 disables the floor).
  The legacy spelling `economics.reservation_amount_dollars` is still accepted.
- If set, the reservation estimate uses that fixed USD amount (regardless of funding source).
  When the app does not define it, the platform default from the economics
  descriptor's `reservation.chat` applies.
  Configure via Integrations bundle props API (see `eco-admin-README.md`).

Accounting and spend are still recorded **per app** for reporting, but quota enforcement is global per tenant/project.
Global quota counters use the sentinel `__project__` in the bundle-id slot of Redis keys (subject_id already encodes tenant/project).

Seeding flow:

- Plan quotas are **seeded at deploy time** from the economics descriptor
  (`deployment/economics.yaml`) by the postgres-setup job via
  [economics_seed.py](../../src/kdcube-ai-app/kdcube_ai_app/ops/deployment/economics/economics_seed.py) (see
  [economics-descriptor-README.md](./economics-descriptor-README.md)).
- The mandatory plans (`anonymous`/`free`/`wallet`/`admin`) have a built-in baseline
  (`DEFAULT_QUOTA_POLICIES`, the single source shared with the seeder); descriptor entries
  override that baseline per field. `enforce: false` (default) seeds only missing entries and
  preserves operator/admin edits; `enforce: true` realigns every listed entity to the descriptor.
- After seeding, adjust limits in the admin UI (or re-run the seeder with an updated descriptor).
- Runtime prefers the DB policy, with a defensive fallback to the built-in defaults if a plan
  row is missing. The legacy app-runtime seeder `ensure_policies_initialized()` is a
  deprecated no-op shim.

```text
deploy: postgres-setup job
      |
      v
read economics.yaml descriptor
      |
      v
merge over built-in baseline (DEFAULT_QUOTA_POLICIES)
      |
      v
   enforce?
      |-- false -> seed only missing plan_quota_policies rows
      |-- true --> realign every listed entity
      |
      v
runtime uses DB policies
(defensive fallback to built-in defaults if a plan row is missing)
```

## Funding Sources and Reservation Semantics

Every request runs as **one split**. The primary funding source covers the plan
part; the wallet covers the over‑quota/over‑funds remainder.

### Reserve (pre‑run)

- `plan_part = min(R, Q, P)` — R is the estimated turn cost (tokens), Q the remaining plan
  token quota, P the primary funds (subscription period budget for external subscribers,
  project budget for everyone else, including internal subscriptions). The primary money
  hold is placed for `plan_part`.
- `wallet_part = R − plan_part` — reserved against the wallet whenever a wallet is present
  (balance > 0). The hold is stored in **USD** (`usd_reserved_cents`, the wallet's USD value of
  `wallet_part` at the live rate); `wallet_part` tokens are only the split unit.
- Admit succeeds if, and only if the wallet can cover `wallet_part` and any indivisible
  requests/concurrency gate is satisfied (a wallet lifts that gate). Otherwise the request is
  denied and **no money hold is left behind**.
- Privileged bypass → no pre‑check; project budget is charged after run.
- Wallet‑backed free users keep `plan_id=free` but use **wallet service limits**
  (requests/concurrency) while **token limits** remain from `free`.

### Settle (post‑run)

Settlement reads current available capacity net of active reservations, then adds back this
request's own still‑live reservation per source. It charges the **maximum possible share to
plan quota + primary funds first**; the wallet pays only the over‑quota remainder, and
wallet‑paid tokens do **not** consume plan quota.

If the **actual** spend exceeds what the primary funds and the wallet covered, the residual
cascades: for a **subscription** primary it first draws the subscription budget's remaining
headroom (`shortfall:subscription_overage`), and only then the **project budget absorbs** the
rest as a last resort; for a **project** primary the project absorbs directly
(`shortfall:wallet_plan` / `shortfall:free_plan`). If plan quota remains, a project‑absorbed
fallback also consumes quota. **Subscriptions and wallets never go negative.**

Shortfall notes are tagged `shortfall:wallet_subscription`, `shortfall:wallet_plan`,
`shortfall:subscription_overage`, or `shortfall:free_plan` for reporting.

### Reservation types

- Rate limiter token reservation (Redis) for the plan part
- Subscription reservations in `user_plan_period_reservations`
- Project budget reservations in `tenant_project_budget_reservations`
- Wallet reservations in `user_credit_reservations`

Reservations are committed or released after execution and accounting. Expired reservations are reaped automatically.

## Decision Tree (Role → Plan → Funding)

```text
request
   |
   v
resolve role -> resolve plan_id -> load plan quota policy
   |
   v
size primary funds P
   |
   v
wallet-aware admit: can the wallet cover wallet_part?
   |-- no --------------------------------> DENY
   |                                        (no money hold left behind)
   |-- yes
        |
        v
   hold primary for plan_part
        |
        v
   wallet_part > 0? -- yes -> hold wallet for wallet_part
        |                          |
        v                          v
   run (every paid call metered / accounting)
        |
        v
   settle: primary + wallet first;
   project budget absorbs any residual
```

## Subscription Periods and Rollovers

Subscription budgets are per billing period and apply to **external (Stripe)** subscriptions
only. Internal subscriptions have no period budget (project-funded by quota) and are excluded
from rollover.

- Each period is keyed by `(tenant, project, user_id, period_key)`.
- Top‑up is once per period by default (idempotent).
- Periods are closed at the end date.
- Unused balance is rolled into project budget.

Maintenance entry points:

- [`SubscriptionManager.sweep_due_subscription_rollovers(...)`](../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/infra/economics/subscription.py)
- Control plane endpoint: `POST /subscriptions/rollover/sweep`
- Reservation reaper: `POST /subscriptions/reservations/reap-all`

## Data Model (Tables)

Authoritative schema: the economics tables live in the **per-project schema**,
rendered from the `<SCHEMA>` placeholder at deploy time:

- [deploy-kdcube-proj-schema.sql](../../src/kdcube-ai-app/kdcube_ai_app/ops/deployment/sql/chatbot/deploy-kdcube-proj-schema.sql)

Key tables:

- `plan_quota_policies` — base policy per plan_id
- `user_plan_overrides` — temporary plan overrides
- `user_lifetime_credits` — wallet credits (USD-native)
- `user_credit_reservations` — wallet reservations (USD-in-cents holds)
- `tenant_project_budget` — project money balance
- `tenant_project_budget_reservations` — project budget holds
- `tenant_project_budget_ledger` — project budget ledger
- `tenant_project_budget_absorption` — view for shortfall absorption reporting
- `tenant_project_budget_absorption_detail` — view for shortfall reporting by user/app
- `plans` — plan catalog and Stripe price mapping (free/admin baseline + chargeable plans)
- `user_plans` — per‑user plan row (one per tenant/project/user). Carries a baseline `free`/`admin` row for every authenticated user (see Plan Resolution), `provider` (`internal`|`stripe`), Stripe linkage ids, and `rl_month_anchor_at` — a durable mirror of the Redis monthly‑quota window anchor so the window survives a Redis flush.
- `user_plan_period_budget` — per period subscription balance
- `user_plan_period_reservations` — subscription holds
- `user_plan_period_ledger` — subscription ledger
- `external_economics_events` — idempotency and audit for external/internal economic operations

## Accounting and Costing

Accounting events are emitted by service wrappers (LLM calls, web search, etc.) —
see [accounting-README.md](../accounting/accounting-README.md) for the tracking
decorators, storage, and aggregation.
Events are aggregated per turn and priced in USD from **each model's own**
[price-table](../../src/kdcube-ai-app/kdcube_ai_app/infra/accounting/usage.py) entry — the actual dollar cost is reference-independent. The reference
model then converts that USD into the **token unit** for the token-denominated
surface (the RL quota).

Reference model conversion is used for:

- Estimation of request cost for reservations (reservation floor USD → tokens)
- Per-turn billable-token equivalent (USD → reference tokens), read live so it
  stays in the same unit as the reservation and quota
- Wallet admission split and display projections (available USD ↔ tokens). The
  wallet **balance and holds are stored in USD** (cents) and do **not** re-value
  when the reference changes; only the transient split unit and the cosmetic token
  figure use the live rate. Plan and project budgets are likewise USD.
