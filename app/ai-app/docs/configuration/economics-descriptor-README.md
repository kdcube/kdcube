---
id: repo:kdcube-ai-app/app/ai-app/docs/configuration/economics-descriptor-README.md
title: "Economics Descriptor"
summary: "Per tenant/project economics configuration in economics.yaml: reservation floor, project overdraft, quota policies, provider budget policies, the plan catalog, the price table, and the token-economy reference model. Seeded into the economics tables at deploy time and read live for the reservation floor, price table, and reference model."
tags: ["service", "configuration", "economics", "deployment", "descriptor", "seeding"]
keywords: ["economics.yaml", "reservation floor", "price tables", "quota policies", "budget policies", "plan catalog", "project overdraft", "reference model", "deploy-time seeding", "runtime write-back"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/descriptors-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/service-runtime-configuration-mapping-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/economics/economics-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/economics/economic-README.md
---
# Economics Descriptor

`economics.yaml` is the per tenant/project economics descriptor.

It defines:

- the per-surface reservation floor (USD)
- the project budget overdraft limit
- per-plan quota policies (RL token/request windows)
- per-provider spending budget policies
- the plan catalog (provider, price, active state, subscribe/grant behaviour)
- the provider/model price table (USD↔token conversion for the whole economy)
- the token-economy reference model (the USD↔token unit of account)

This descriptor has **two runtime roles**:

- **seeded into the economics tables at deploy time** — quota policies, budget
  policies, the plan catalog, and the overdraft limit are written to Postgres
  once; Postgres is then the live authority for those sections;
- **read live from the file** — the `reservation`, `price_tables`, and
  `llm_reference_service` sections are never stored in the database; the runtime
  reads them live from the file (mtime-cached), so an edit is picked up without a
  restart. An admin mutation triggers a descriptor write-back that regenerates the
  seeded sections from the DB and preserves these live sections verbatim.

It does not provide values through `get_settings()`; the two live sections are
read through dedicated helpers in
[config_scopes.py](../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/config_scopes.py),
and the seeded sections are read from the economics tables each turn.

The descriptor is **per tenant/project**. Tenant and project are not declared in
the file — they are resolved from settings (`TENANT_ID` / `PROJECT_ID`).

For the economics semantics behind these sections (funding split, plan
resolution, reservation model), see the economics docs:

- [economics-descriptor-README.md](../economics/economics-descriptor-README.md)
- [economic-README.md](../economics/economic-README.md)

## Direct runtime contract

### Supported access path

| Need | Supported mechanism | Notes |
|---|---|---|
| explicit descriptor file path | `ECONOMICS_YAML_DESCRIPTOR_PATH` | highest precedence; supports `file://` URIs; used by both the runtime reader and the deploy-time seeder |
| descriptor directory fallback | `PLATFORM_DESCRIPTORS_DIR/economics.yaml` | used when `ECONOMICS_YAML_DESCRIPTOR_PATH` is unset |
| default mount path | `/config/economics.yaml` | used when neither of the above is set |

There is no `get_settings()`, `read_plain(...)`, or `get_secret(...)` surface for
individual `economics.yaml` fields. The seeded sections are reached through the
economics tables; the live sections through `config_scopes` helpers.

### Loader precedence

The descriptor path is resolved, in order, by both the runtime reader and the
seeder:

1. `ECONOMICS_YAML_DESCRIPTOR_PATH` (supports `file://`)
2. `PLATFORM_DESCRIPTORS_DIR/economics.yaml`
3. `/config/economics.yaml`

A missing file is **not** an error: the seeder still seeds the built-in baseline
(see [Built-in baseline](#built-in-baseline)), and the runtime falls back to its
defensive defaults (in-code reservation/price defaults).

## YAML shape

The supported descriptor shape is:

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
  wallet:     { max_concurrent: 4, requests_per_day: 200, requests_per_month: 6000 }
  admin:      { max_concurrent: 10 }

budget_policies:
  anthropic:  { usd_per_hour: 10.0, usd_per_day: 200.0, usd_per_month: 5000.0 }
  duckduckgo: { usd_per_hour: null, usd_per_day: null,  usd_per_month: null }

plans:
  free: { provider: internal, monthly_price_cents: 0, active: true }
  pro:  { provider: stripe, monthly_price_cents: 2000, active: true, stripe_price_id: price_xxx }

price_tables:            # optional; whole-table replacement of the in-code baseline
  llm:                   # must include the reference model, else the baseline is used
    - { provider: anthropic, model: claude-sonnet-4-5-20250929, input_tokens_1M: 3.0, output_tokens_1M: 15.0 }
  embedding: [ ... ]
  web_search: [ ... ]

llm_reference_service:   # optional; the USD↔token unit of account (defaults to the in-code reference)
  provider: anthropic
  service_name: claude-sonnet-4-5-20250929
```

## What each section does

For each section below: **seeded** means written to Postgres at deploy time
(Postgres is then the live authority); **runtime-read** means read live from the
file (mtime-cached) and never stored.

### `version`

Descriptor schema version. Currently `1`.

### `enforce`

Controls how each **seeded** entity is reconciled into the database:

| Value | Behaviour |
|---|---|
| `false` | seed only what is missing (`INSERT ... ON CONFLICT DO NOTHING`); existing rows are left untouched |
| `true` | realign every listed entity to the descriptor values (`INSERT ... ON CONFLICT DO UPDATE SET ...`), including lowering a field back to `null` / unlimited |

`enforce` has no effect on the runtime-read sections (`reservation`,
`price_tables`, `llm_reference_service`), which are always the file's live value.

### `reservation` (runtime-read)

Per-surface reservation floor defaults, in USD. Only `chat` is consumed by the
runtime.

| Rule | Effect |
|---|---|
| positive number | enables the floor at that USD amount |
| value `<= 0` | disables the floor (the reservation falls back to a token-based estimate) |
| surface omitted | no platform default for that surface |

The floor is converted to a token hold using the **reference model** output
price (see [Reference model](#reference-model)); it is **not** seeded into any
table. A bundle may override it through `config.economics.reservation.<floor>`;
the descriptor value is the default when the bundle is silent.

Runtime consumers:
- [config_scopes.py](../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/config_scopes.py) — `economics_reservation_default(floor)`
- [entrypoint_with_economic.py](../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/chatbot/entrypoint_with_economic.py) — effective floor resolution (bundle prop over descriptor default)

### `price_tables` (runtime-read)

Provider/model pricing catalog (`llm`, `embedding`, `web_search`) used for cost
calculation and USD↔token conversion. Like `reservation`, it is read straight
from the file and never stored.

| Property | Behaviour |
|---|---|
| optional | absent section/file → the in-code baseline `DEFAULT_PRICE_TABLE` is used unchanged |
| either/or, no merge | when present, it is used as the **whole** table; the baseline is not consulted, so it must list every model/service you rely on |
| reference-model guard | the `llm` section must carry the reference model; if it does not, the block is treated as **invalid** and the baseline is used in full |

Per-model LLM fields include `input_tokens_1M`, `output_tokens_1M`,
`cache_read_tokens_1M`, `cache_write_tokens_1M`, and a `cache_pricing.{5m,1h}`
block. Note: cache **read** is priced from the flat `cache_read_tokens_1M`; the
`cache_pricing.<ttl>` block is only consulted for cache **writes**
(`write_tokens_1M`).

Runtime consumers:
- [config_scopes.py](../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/config_scopes.py) — `economics_price_tables()`
- [usage.py](../../src/kdcube-ai-app/kdcube_ai_app/infra/accounting/usage.py) — `price_table()` selects descriptor-or-baseline

### `llm_reference_service` (runtime-read)

The token-economy reference model: the single model whose output price defines
the USD↔token unit (see [Reference model](#reference-model)). `provider` and
`service_name` name the model.

| Property | Behaviour |
|---|---|
| optional | absent section/file → the in-code default (`anthropic` / `claude-sonnet-4-5-20250929`) is used |
| live | read straight from the file (mtime-cached); a change is picked up without a restart |
| priced or default | if the reference resolves in the effective `price_tables`, its `output_tokens_1M` is the conversion rate; if it does not resolve, the reference is treated as invalid and the in-code default is used |

Runtime consumers:
- [config_scopes.py](../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/config_scopes.py) — `economics_llm_reference_service()`
- [usage.py](../../src/kdcube-ai-app/kdcube_ai_app/infra/accounting/usage.py) — `llm_reference_service()` / `usd_per_reference_token()`

### `project_budget` (seeded)

Only `overdraft_limit_usd` is seeded (into `tenant_project_budget`):

| Value | Meaning |
|---|---|
| number | overdraft ceiling in USD |
| `null` | unlimited overdraft |

The running **balance is never written** by the descriptor; it is funded
separately (admin UI / app-budget top-up).

### `quota_policies` (seeded)

`plan_id -> quota policy`. Each policy carries `max_concurrent`,
`requests_per_day`, `requests_per_month`, `total_requests`, `tokens_per_hour`,
`tokens_per_day`, `tokens_per_month`. An omitted or `null` dimension means
unlimited for that window. Seeded into `plan_quota_policies`.

### `budget_policies` (seeded)

`provider -> budget policy`. Spending limits in USD: `usd_per_hour`,
`usd_per_day`, `usd_per_month`; `null` means unlimited for that window. Seeded
into `application_budget_policies`. Opt-in — there is no baseline, so only the
providers listed here are seeded.

### `plans` (seeded)

`plan_id -> catalog entry`: `provider` (`internal` or `stripe`),
`monthly_price_cents`, `active`, optional `stripe_price_id` (required when
`provider: stripe`), optional `metadata`. Seeded into the `plans` table.

`quota_policies.<plan_id>` defines the limits for a plan; `plans.<plan_id>`
defines its catalog entry (price, provider, active state). Most plans are
subscribable; `wallet` and `anonymous` are built-in non-subscribable plans;
`admin` is operator-assigned. `anonymous`/`free`/`admin`/`wallet` are seeded
from the built-in baseline automatically — list a plan here only to override a
baseline field or to add a chargeable catalog plan.

## Reference model

The whole token economy (reservation, credits, Stripe, balance) is denominated
in **reference tokens**: USD↔token conversion always uses one reference model's
output price. The reference is chosen by the `llm_reference_service` section and
read live via `llm_reference_service()` in
[usage.py](../../src/kdcube-ai-app/kdcube_ai_app/infra/accounting/usage.py),
falling back to the in-code default `anthropic` / `claude-sonnet-4-5-20250929`.
Both the economics runtime and bundles read the reference from this single source,
so a descriptor change re-points the whole economy without code edits.

The reference is a **pricing unit only** — it does not select the models that
execute a turn. Bundle `role_models` are independent; changing the reference
re-values the USD↔token unit (reservation, credits, balance, accounting), not
which model runs.

Consequences:

- the reference model must resolve in the effective `price_tables`. This is a
  two-way guard: a `price_tables` block that omits the reference is treated as
  invalid and the baseline is used instead; and an `llm_reference_service` that
  does not resolve in the effective table is treated as invalid and the in-code
  default reference is used — a malformed / partially-updated descriptor degrades
  gracefully instead of raising;
- the rate is the reference entry's `output_tokens_1M`. The reference is matched
  by the `model` field, so a duplicate/mislabeled `model` key can shadow the
  intended entry and silently mis-price the whole economy — keep `model` keys
  unique per provider;
- changing the reference (or its price) re-values previously-accumulated token
  balances in USD terms, because the balance is stored in reference tokens and
  the USD figure is reconstructed at the current reference price. Since the
  reference is read live, this re-valuation applies immediately across every
  display and settle.

## Built-in baseline

Some seeded entities are always written from a platform-owned baseline
([defaults.py](../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/infra/economics/defaults.py)),
even when the descriptor omits them; when the descriptor lists them, its fields
override the baseline per field.

| Entity | Baseline | Descriptor behaviour |
|---|---|---|
| `quota_policies` | `anonymous`, `free`, `wallet`, `admin` | always seeded; descriptor overrides per field; extra plan_ids seeded as-is |
| `plans` | `anonymous`, `free`, `admin`, `wallet` (internal, `monthly_price_cents: 0`, `active: true`) | always seeded; descriptor overrides per field; extra plan_ids seeded as-is |
| `budget_policies` | none | opt-in; seeded only for the providers listed |

## Seeding

The seeder
([economics_seed.py](../../src/kdcube-ai-app/kdcube_ai_app/ops/deployment/economics/economics_seed.py))
runs in the postgres-setup job after schema provisioning. It is plain
`psycopg2` raw SQL and is **non-fatal** — a seeding failure logs a warning but
does not fail the deployment.

It reconciles **per entity** (no global gate); `enforce` decides `DO NOTHING`
vs `DO UPDATE SET`. It writes `plan_quota_policies`, `application_budget_policies`,
`plans`, and `tenant_project_budget` (overdraft only). The `reservation` and
`price_tables` sections are runtime config and are never seeded.

## Runtime write-back

When a project's economics change while it runs (admin mutations), `economics.yaml`
is rewritten from the live database state
([descriptor.py](../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/infra/economics/descriptor.py)),
so the next deploy-time seed does not regress the change.

- Rebuilds `quota_policies`, `budget_policies`, `plans`, and
  `project_budget.overdraft_limit_usd` from the database.
- **Preserves** the `reservation` and `price_tables` sections verbatim (they are
  never stored in the DB).
- Writes atomically (temp file + `os.replace`) under an exclusive `flock`.
- Best-effort: a write failure logs a warning and never fails the originating
  admin operation.

## CLI compose descriptor mode

The installer stages `economics.yaml` into the project config directory
alongside the other descriptors, copying `deployment/economics.yaml` as the
template when the target does not already exist
([installer.py](../../src/kdcube-ai-app/kdcube_cli/src/kdcube_cli/installer.py)).
Service env files point runtime and the postgres-setup job to `/config` via
`PLATFORM_DESCRIPTORS_DIR`, so the descriptor-source-of-truth path is the staged
`/config/economics.yaml`.

Because `reservation` and `price_tables` are read straight from that file
(mtime-cached), editing the staged `/config/economics.yaml` changes the
reservation floor and the price table live, without a restart, across every proc
replica sharing the mount. The seeded sections change only on the next deploy-time
seed or through an admin mutation (which then writes back to the file).

## Direct local service run

For a direct local `proc` or `ingress` run, either:

- set `ECONOMICS_YAML_DESCRIPTOR_PATH=/abs/path/to/economics.yaml`, or
- set `PLATFORM_DESCRIPTORS_DIR=/abs/path/to/descriptors` (the runtime then reads
  `economics.yaml` from that directory).

## Inspecting the effective descriptor

- **live reservation / price table** — read straight from the resolved file; a
  quick check is to confirm the running process sees the intended file:
  `PLATFORM_DESCRIPTORS_DIR/economics.yaml` (or `ECONOMICS_YAML_DESCRIPTOR_PATH`).
- **seeded sections** — the live authority is Postgres, not the file. Inspect the
  `plan_quota_policies`, `application_budget_policies`, `plans`, and
  `tenant_project_budget` tables, or the economics admin dashboard.
