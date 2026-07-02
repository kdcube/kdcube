---
id: repo:kdcube-ai-app/app/ai-app/docs/economics/economics-descriptor-README.md
title: "Economics Descriptor"
summary: "The per tenant/project economics.yaml descriptor and how it is seeded at deploy time."
tags: ["economics", "descriptor", "seeding", "deployment"]
keywords: ["economics.yaml", "seeder", "reservation floor", "quota policies", "budget policies", "plans", "overdraft"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/economics/economic-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/economics/economic-enforcement-engine-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/descriptors-README.md
---
# Economics Descriptor

This document describes the `economics.yaml` descriptor: the per tenant/project
file that carries the default economics for a project, how it is seeded at deploy
time, and how the runtime reads and writes it back.

Descriptor sample:
- [economics.yaml](../../deployment/economics.yaml)

Implementation:
- Built-in baseline — [defaults.py](../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/infra/economics/defaults.py)
- Seeder — [economics_seed.py](../../src/kdcube-ai-app/kdcube_ai_app/ops/deployment/economics/economics_seed.py)
- Runtime write-back — [descriptor.py](../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/infra/economics/descriptor.py)
- Runtime read — [config_scopes.py](../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/config_scopes.py)

## Purpose

The descriptor is a platform-owned file that defines the economics defaults of a
project — quota policies, provider budget policies, the subscription catalog, the
project overdraft limit, and the reservation floor. It is:

- **seeded once at deploy time** into the project's economics tables, so a fresh
  project starts with sane economics without any manual steps;
- **kept current by runtime write-back**, so changes made while the project runs
  are not regressed the next time the project is provisioned;
- **the live source for the reservation floor**, which the runtime reads per
  turn directly from the file.

The descriptor is **per tenant/project**. Tenant and project are not declared in
the file — they are resolved from settings (`TENANT_ID` / `PROJECT_ID`).

## File location

The descriptor lives on the shared `/config` mount — a host bind mount in local
deployments and EFS on AWS ECS. The same file is visible to the chat
proc (where the runtime reads it) and the postgres-setup job (where it is
seeded).

The path is resolved, in order:

1. `ECONOMICS_YAML_DESCRIPTOR_PATH` (supports `file://` URIs);
2. `PLATFORM_DESCRIPTORS_DIR` + `/economics.yaml`;
3. `/config/economics.yaml` (default).

A missing file is not an error — the seeder still seeds the built-in baseline
(see below), and the runtime falls back to its defensive defaults.

## Structure

```yaml
version: 1
enforce: false

reservation:
  chat: 2.0

project_budget:
  overdraft_limit_usd: 0.0

quota_policies:
  anonymous:  { max_concurrent: 1, requests_per_day: 2,   requests_per_month: 60, ... }
  free:       { max_concurrent: 2, requests_per_day: 100, requests_per_month: 30000, ... }
  wallet: { max_concurrent: 4, requests_per_day: 200, requests_per_month: 6000 }
  admin:      { max_concurrent: 10 }

budget_policies:
  anthropic:  { usd_per_hour: 10.0, usd_per_day: 200.0, usd_per_month: 5000.0 }
  duckduckgo: { usd_per_hour: null, usd_per_day: null,  usd_per_month: null }

plans:
  free: { provider: internal, monthly_price_cents: 0, active: true }
  
price_tables:            # optional; whole-table replacement of the baseline (either/or)
  llm:                   # must include the reference model, else the baseline is used
    - { provider: anthropic, model: claude-sonnet-4-5-20250929, input_tokens_1M: 3.0, output_tokens_1M: 15.0 }

llm_reference_service:   # optional; the USD↔token unit of account
  provider: anthropic
  service_name: claude-sonnet-4-5-20250929
```

### `version`

Descriptor schema version. Currently `1`.

### `enforce`

Controls how each listed entity is reconciled into the database:

- `false` — seed only what is missing (`INSERT ... ON CONFLICT DO NOTHING`).
  Existing rows are left untouched.
- `true` — realign every listed entity to the descriptor values
  (`INSERT ... ON CONFLICT DO UPDATE SET ...`), including lowering a field back
  to `null` / unlimited.

### `reservation`

Reservation floor defaults, in USD, keyed by surface. Only `chat` is consumed
by the runtime.

The value is a **scalar**:

- a positive number enables the floor;
- a value `<= 0` disables it (the per-turn estimate falls back to a token-based
  estimate);
- omitting a surface means there is no platform default for it.

The reservation section is **not** seeded into any table — it is runtime config
read straight from the file (see [Runtime reads](#runtime-reads)).

### `price_tables`

Provider/model pricing catalog (`llm`, `embedding`, `web_search`) used for
cost calculation and USD↔token conversion. Like `reservation`, it is a pure
**runtime-read** section — never seeded, never in the DB.

- **Optional.** The authoritative baseline is the in-code `DEFAULT_PRICE_TABLE`
  (`infra/accounting/usage.py`); a missing section/file means the baseline is
  used unchanged.
- **Either/or, no merge.** When the descriptor provides `price_tables`, it is
  used as the **whole** table and the baseline is **not** consulted; when it is
  absent, the baseline is used in full. Sections are never combined — a
  `price_tables` block must list every model/service you rely on.
- **Reference model guard.** The block must carry the reference model
  (`anthropic`/`claude-sonnet-4-5-…`, the currency of the token economy) in its
  `llm` section. If it does not, the block is treated as **invalid** and the
  baseline is used in full. Symmetrically, an `llm_reference_service` that does
  not resolve in the effective table is treated as invalid and the in-code default
  reference is used — the economy (reservation/credits/Stripe/balance) can never
  break on a misconfigured descriptor.

### `llm_reference_service`

The token-economy reference model — the single model whose `output_tokens_1M`
defines the USD↔token unit (reservation, credits, Stripe, balance, per-turn
billable equivalent). Like `price_tables`, a pure **runtime-read** section —
never seeded, never in the DB.

- **Optional.** Absent section/file → the in-code default
  (`anthropic`/`claude-sonnet-4-5-…`) is used.
- **Live.** Read straight from the file (mtime-cached); a change re-points the
  whole economy without a restart.
- **Priced or default.** The `provider`/`service_name` must resolve in the
  effective `price_tables`; if they do not, the reference is invalid and the
  in-code default is used (see the reference model guard above).

### `project_budget`

Only `overdraft_limit_usd` is seeded (into `tenant_project_budget`):

- a number is the overdraft ceiling in USD;
- `null` means unlimited overdraft.

The running **balance is never written** by the descriptor; it is funded
separately.

### `quota_policies`

`plan_id -> quota policy`. Each policy carries `max_concurrent`,
`requests_per_day`, `requests_per_month`, `total_requests`, `tokens_per_hour`,
`tokens_per_day`, `tokens_per_month`. An omitted or `null` dimension means
unlimited for that window. Seeded into `plan_quota_policies`.

### `budget_policies`

`provider -> budget policy`. Spending limits in USD: `usd_per_hour`,
`usd_per_day`, `usd_per_month`; `null` means unlimited for that window. Seeded
into `application_budget_policies`.

### `plans`

Plans are the catalog/runtime entitlement records. A plan id appears in two
sections that play complementary roles:

- `quota_policies.<plan_id>` defines the **limits** (the RL token/request
  windows) for users on that plan;
- `plans.<plan_id>` defines the **catalog entry** — `provider` (`internal` or
  `stripe`), `monthly_price_cents`, `active` state, optional `stripe_price_id`
  (required when `provider: stripe`), optional `metadata` — i.e. price and
  subscribe/grant behaviour.

Most plans are subscribable. `wallet` and `anonymous` are built-in,
**non-subscribable** plans (they exist as catalog entities so users can be
resolved onto them, but no one subscribes to them). `admin` is
internal/operator-assigned. `anonymous`/`free`/`admin`/`wallet` are seeded from
the built-in baseline automatically; you only list a plan here to override a
baseline field or to add a chargeable catalog plan.

Seeded into the `plans` table.

## Built-in baseline

Some entities are always seeded from a platform-owned baseline
([defaults.py](../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/infra/economics/defaults.py)),
even when the descriptor omits them. When the descriptor does list them, its
fields override the baseline per field.

| Entity | Baseline | Descriptor behaviour |
|---|---|---|
| `quota_policies` | `anonymous`, `free`, `wallet`, `admin` (`DEFAULT_QUOTA_POLICIES`) | Always seeded; descriptor overrides per field; extra `plan_id`s seeded as-is. |
| `plans` | `anonymous`, `free`, `admin`, `wallet` — internal, `monthly_price_cents: 0`, `active: true` (`DEFAULT_PLANS`) | Always seeded; descriptor overrides per field; extra `plan_id`s seeded as-is. |
| `budget_policies` | none | Opt-in; seeded only for the providers listed. |

The four baseline quota plans (`anonymous` / `free` / `wallet` / `admin`)
are intrinsic to the runtime plan-resolution logic, so they must always exist.

## Seeding

The seeder
([economics_seed.py](../../src/kdcube-ai-app/kdcube_ai_app/ops/deployment/economics/economics_seed.py))
runs in the **postgres-setup** job, which already provisions the project schema.
It is invoked from
[deploy_project.py](../../src/kdcube-ai-app/kdcube_ai_app/ops/deployment/sql/deploy_project.py)
after `step_provision`, and is **non-fatal**: a seeding failure logs a warning
but does not fail the deployment.

It is plain `psycopg2` raw SQL (the postgres-setup image ships only
`psycopg2-binary` and `PyYAML`), separate from the schema deployment. Single
responsibility: write data rows after the schema exists.

Behaviour:

- Loads `economics.yaml` (resolving the path as above); a missing file means
  baseline-only seeding.
- Reconciles **per entity** — there is no global early-return gate. `enforce`
  decides `DO NOTHING` vs `DO UPDATE SET` for every entity.
- Writes:
  - `plan_quota_policies` — baseline four + descriptor entries/extras;
  - `plans` — baseline `anonymous`/`free`/`admin`/`wallet` + descriptor entries/extras;
  - `application_budget_policies` — descriptor providers only;
  - `tenant_project_budget` — `overdraft_limit_cents` only (balance untouched).
- Idempotent: re-running with `enforce: false` is a no-op for existing rows.

## Runtime reads

The **reservation**, **price_tables**, and **llm_reference_service** sections are
read by the runtime from the file; quota, budget, and subscription state are read
from the economics tables each turn.

`config_scopes.economics_reservation_default(floor)` reads `reservation.<floor>`,
`config_scopes.economics_price_tables()` reads `price_tables`, and
`config_scopes.economics_llm_reference_service()` (via `usage.llm_reference_service()`)
reads `llm_reference_service`. All readers are cached by file **mtime**, so edits
(including write-backs) are picked up without a restart, and they propagate to
every proc replica over the shared mount.
`price_table()` (`infra/accounting/usage.py`) uses the descriptor section in
full when present and valid, else the in-code baseline (no merge).

Per-turn resolution in the economics entrypoint
([entrypoint_with_economic.py](../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/chatbot/entrypoint_with_economic.py)):

1. the bundle prop `economics.reservation.<floor>` (legacy scalar
   `economics.reservation_amount_dollars` is also accepted) — a positive value
   enables the floor, `<= 0` disables it;
2. if the bundle does not set it, the platform default from the descriptor.

A bundle value therefore overrides the descriptor default; the descriptor is the
default used when the bundle is silent.

## Runtime write-back

When a project's economics change while it runs, `economics.yaml` is rewritten
from the live database state
([descriptor.py](../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/infra/economics/descriptor.py)).
This keeps the file current so the next deploy-time seed does not regress the
change.

- Rebuilds `quota_policies`, `budget_policies`, `plans`, and
  `project_budget.overdraft_limit_usd` from the database; **preserves** the
  `reservation` and `price_tables` sections (which are never stored in the database).
- Writes atomically (temp file + `os.replace`) under an exclusive `flock`, so
  concurrent writes do not lose the reservation section.
- Best-effort: a write failure logs a warning and never fails the originating
  operation.

## Staging

The installer stages a copy of the descriptor from `deployment/economics.yaml`
into the project config directory when one is not already present.
