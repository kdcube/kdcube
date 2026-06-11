---
id: repo:kdcube-ai-app/app/ai-app/docs/economics/economic-enforcement-engine-README.md
title: "Economics Enforcement Engine"
summary: "Reusable API for enforcing the economics model (quota, funding, settlement) on accountable flows outside the chat entrypoint."
tags: ["economics", "enforcement", "engine", "api", "integration"]
keywords: ["EconomicsGuard", "economic_preflight", "EconomicsSubject", "RoleResolver", "FlowPolicy", "reservation", "settlement", "scope_id", "quota lock", "paid lane", "lane switch", "allow_paid_lane_fallback", "enforce_quota_lock"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/economics/economic-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/economics/economics-events-README.md
---
# Economics Enforcement Engine

The economics model (roles, plans, funding lanes, reservations, settlement — see
[economic-README.md](./economic-README.md)) is enforced for chat turns by the chat
entrypoint. The **enforcement engine** exposes that same model as a small, reusable
API so that any accountable flow — one that runs model calls on a user's behalf
without a chat turn — can verify feasibility, reserve funding, and settle actual
cost through the **same** role → plan → funding resolution, lanes, project→wallet
overflow split, and shortfall absorption.

Module:
- [enforcement.py](../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/infra/economics/enforcement.py)

The engine reuses the owning entrypoint's runtime primitives (`cp_manager`, `rl`,
`budget_limiter`, `run_accounting`, `comm`, `logger`). It does not change how chat
turns are charged.

## When to use which entry point

The engine offers two entry points. Choose based on **who settles the cost**:

| Entry point | Verifies | Reserves | Settles | Use when |
| --- | --- | --- | --- | --- |
| `EconomicsGuard` | ✅ | ✅ | ✅ | the accounted work runs *inside* the guard and you want it metered and charged |
| `economic_preflight` | ✅ | — | — | you only need a feasibility gate — the cost is metered elsewhere, or the caller degrades gracefully on denial |

Both verify the user's quota and funding **at the start** and raise
`EconomicsLimitException` when the flow is not feasible.

## Identity (who pays)

Every flow carries an `EconomicsSubject` — the resolved economics identity:

```python
from kdcube_ai_app.apps.chat.sdk.infra.economics.enforcement import EconomicsSubject

subject = EconomicsSubject(
    tenant="acme", project="main", user_id="u-123",
    user_type="paid",          # resolved economics role; never a hardcoded default
    timezone="Europe/Kyiv",     # optional; anchors rolling windows
)
```

For **detached** flows (e.g. a job run later by a worker) the original session is
gone, so re-derive `paid`/`registered` from economics state with `RoleResolver`.
`privileged`/`admin` cannot be derived from economics state and must be carried from
the enqueue side and passed as `carried_role`:

```python
from kdcube_ai_app.apps.chat.sdk.infra.economics.enforcement import RoleResolver

resolver = RoleResolver(pg_pool=entrypoint.pg_pool, tenant=tenant, project=project)
role = await resolver.resolve(user_id="u-123", carried_role=carried_role)  # preserves privileged/admin
subject = EconomicsSubject(tenant=tenant, project=project, user_id="u-123", user_type=role)
```

## `EconomicsGuard` — verify, reserve, settle

An async context manager around a single accountable flow. On enter it resolves the
plan and funding, runs the rate-limit admit, reserves funding, and binds accounting
to the flow's `scope_id`. On exit it aggregates the flow's accounting events for that
`scope_id` and settles the actual cost across the funding lanes (committing or
releasing the reservations).

```python
from kdcube_ai_app.apps.chat.sdk.infra.economics.enforcement import (
    EconomicsGuard, EconomicsEstimate, FlowPolicy,
)
from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import EconomicsLimitException

try:
    async with EconomicsGuard(
        entrypoint,                         # economics-enabled entrypoint
        subject=subject,
        scope_id="myflow_42",               # stable, unique accountable request id
        flow="my.flow",                     # label for logs/events
        estimate=EconomicsEstimate(reservation_usd=0.05),
        policy=FlowPolicy(enforce_concurrency=False, emit_user_events=False),
    ) as decision:
        result = await do_the_work()        # accounted model calls run here
except EconomicsLimitException as exc:
    # not feasible — nothing ran; inspect exc.code / exc.data
    ...
```

The `scope_id` is the flow's **accountable request id**: pick a stable, unique,
self-describing value (e.g. `<flow>_<entity_id>`). Reservations, ledger entries, and
spend are recorded under it and are traceable with
`GET /economics/request-lineage?request_id=<scope_id>`.

## `economic_preflight` — verify only

Use when you only need to gate the start of a flow and will degrade gracefully on
denial, or when the cost is metered by something else (for example a chat turn that
already settles, or a step whose cost you choose not to charge separately). It runs
the same admit + funding resolution but performs **no reservation and no
settlement**.

```python
from kdcube_ai_app.apps.chat.sdk.infra.economics.enforcement import (
    economic_preflight, EconomicsEstimate, FlowPolicy,
)
from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import EconomicsLimitException

try:
    decision = await economic_preflight(
        entrypoint, subject=subject,
        estimate=EconomicsEstimate(reservation_usd=0.01),
        flow="my.search",
        policy=FlowPolicy(enforce_concurrency=False, emit_user_events=False),
    )
except EconomicsLimitException:
    use_cheaper_path()    # e.g. skip the expensive model call, fall back
```

## Contracts

### `EconomicsEstimate` — reservation size

| Field | Meaning |
| --- | --- |
| `reservation_usd` | primary lever: fixed USD estimate for the flow (most non-chat flows cost pennies) |
| `input_text` / `output_budget_tokens` | token-estimate path used when `reservation_usd` is not given |
| `min_tokens` | floor for the token-estimate path (default 500) |

### `FlowPolicy` — per-flow knobs

| Field | Default | Meaning |
| --- | --- | --- |
| `enforce_concurrency` | `False` | take a concurrency slot (chat-only; background flows keep this off) |
| `reservation_ttl_sec` | `900` | reservation hold lifetime |
| `lock_ttl_sec` | `180` | admit lock lifetime |
| `emit_user_events` | `False` | emit `rate_limit.*` SSE events on denial (needs a `comm` channel); background flows log only |
| `allow_paid_lane_fallback` | `False` | when the plan lane cannot cover but the user can pay, switch to a wallet‑only paid lane instead of denying (see *Paid‑lane switch* below) |
| `enforce_quota_lock` | `False` | serialize the admit→reserve window per user with a distributed lock (reserving flows only; see *Quota lock* below) |
| `quota_lock_ttl_sec` | `60` | quota‑lock key lifetime (safety net if the holder dies) |
| `quota_lock_wait_sec` | `5.0` | how long to wait for the lock before denying as `quota_lock_timeout` |

### `EconomicsDecision` — outcome (returned by both APIs)

Carries the resolved `lane` (`plan` / `paid` / `bypass`), `plan_id`,
`funding_source` (`subscription` / `project` / `wallet` / `none`),
`funding_available_usd`, `est_turn_tokens`, `est_turn_usd`, `budget_bypass`,
`nested`, and `scope_id`. Useful for logging the decision and the applicable limits.

## Behavior notes

- **Denial.** When the flow is not feasible the API raises `EconomicsLimitException`
  before any work runs. `exc.code` is `rate_limited` (quota), `no_funding_source`
  (no eligible funding), or `quota_lock_timeout` (lock contended — see *Quota lock*);
  `exc.data` carries the snapshot. See
  [economics-events-README.md](./economics-events-README.md) for the event payloads
  emitted when `emit_user_events` is on.
- **Paid‑lane switch.** With `allow_paid_lane_fallback` on, a plan‑lane request that
  cannot be served from the plan — the plan admit is rate‑limited, or plan funding is
  exhausted and cannot be reserved — switches to a **wallet‑only paid lane** instead
  of denying, provided the user has a wallet: the plan reservation is released, admit
  is retried against the pay‑as‑you‑go policy, and the wallet becomes the primary
  funding. Off by default, so a flow denies rather than charging the wallet unless it
  opts in.
- **Settlement (guard only).** On exit, actual spend is charged across the same lanes
  as chat — plan/subscription/project first, wallet for the overflow, with project
  budget absorbing any shortfall. A paid‑lane flow settles from the wallet (project
  absorbs any uncovered remainder). A flow whose actual cost is zero releases its
  holds rather than charging. Subscriptions and wallets never go negative.
- **Quota lock.** With `enforce_quota_lock` on (and Redis available on the
  entrypoint), the admit→reserve planning window is serialized per user with a
  distributed lock, closing the read‑remaining‑quota → reserve race between concurrent
  requests of the same user. The lock is held only across planning and released before
  the work runs; if it cannot be acquired within `quota_lock_wait_sec` the flow is
  denied with `quota_lock_timeout`. It is skipped for `privileged`/`admin` (which
  bypass funding) and is only meaningful for reserving flows — `economic_preflight`
  takes no reservation and ignores it. Without Redis it degrades to no lock.
- **Nested safety.** If a guard is entered while an economics scope is already active
  on the same logical task, it automatically degrades to verify-only (the outer scope
  settles) so the same work is never charged twice.
- **Accounting binding.** The guard binds an accounting context keyed by `scope_id`
  so a detached/background flow's events are persisted and readable at settlement,
  even without a chat turn cache.
