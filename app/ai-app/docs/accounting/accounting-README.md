---
id: repo:kdcube-ai-app/app/ai-app/docs/accounting/accounting-README.md
title: "Accounting & Usage Tracking"
summary: "How runtime usage and cost events are emitted, persisted, aggregated, and priced so economics, billing, and reporting can reason about platform activity."
tags: ["accounting", "economics", "usage", "tracking", "opex"]
keywords: ["usage accounting", "cost event capture", "pricing calculation", "event aggregation", "billing telemetry", "turn-level usage tracking", "accounting storage layout", "economics inputs"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/aggregations/README-AGGREGATIONS.md
  - repo:kdcube-ai-app/app/ai-app/docs/economics/economic-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/economics/eco-admin-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/storage/sdk-store-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/README-monitoring-observability.md
---
# Accounting & Usage Tracking

KDCube uses a **self‑contained accounting system** to track service usage (LLMs, embeddings, web search, and more), store raw events under `accounting/`, and compute per‑turn costs for economics and limits. The same data is also aggregated into `analytics/` for OPEX queries.

This document explains:

1. What gets tracked and where it is stored
2. How context is bound to a request and propagated across tasks
3. How costs are computed per turn
4. How aggregates are generated and consumed

---

**Overview**

The accounting flow spans three layers:

1. **Tracking**: decorators capture usage for each LLM/embed/web search call.
2. **Storage**: raw JSON events are written to the storage backend and optionally cached per‑turn in Redis.
3. **Accounting**: `RateCalculator` computes cost breakdowns and emits `accounting.usage` steps used by economics limits.

---

## 1) What is tracked

### 1.1 Decorators

The accounting system auto‑tracks usage via decorators:

- `@track_llm` in `kdcube_ai_app/infra/service_hub/inventory.py`
- `@track_embedding` in `kdcube_ai_app/infra/embedding/embedding.py`
- `@track_web_search` in `kdcube_ai_app/apps/chat/sdk/tools/backends/web/search_backends.py`

These decorators wrap model/back‑end calls and emit an `AccountingEvent` with:

- `service_type` (llm, embedding, web_search, …)
- `provider` and `model_or_service`
- `usage` (tokens, requests, cache stats, etc.)
- `success` and `error_message`
- **context snapshot** taken from `AccountingContext`

For semantic search, the economics feasibility check and the accounting event are
separate steps. Searchable components should receive a model-service facade rather
than separate guard/embed callbacks. The facade exposes `embed_texts(...)` for
document/index material and `embed_search_query(query, flow=...)` for query
embeddings. The actual discharge requires the embedding call to go through an
accounting-aware model service and then through a settlement owner:

- chat-turn search: the chat processor has already bound the real
  `conversation_id`/`turn_id`; `BaseEntrypointWithEconomics.run(...)` marks the
  active parent economics scope, so the search guard performs a verify-only
  check and the embedding event stays in the parent turn for turn settlement.
- standalone UI/API search: there is no parent chat turn; `EconomicsGuard` binds
  accounting under an operation scope such as `canvas_pins_search_<id>` or
  `memory_search_<id>` and settles that operation on exit.

An `EconomicsGuard` marks its own local settlement scope. A nested guard inside
an active economics scope verifies feasibility and leaves the accounting event in
the active scope. Custom embedding backends also emit `embedding` events; a
non-zero dollar charge requires a matching provider/model entry in the accounting
price table.

Runtime enforcement can be traced centrally with the log marker
`[economics.enforcement]`. Search for that marker together with a flow name such
as `memory.search`, `canvas.pins.search`, `task_tracker.issue.search`,
`memory.reconciler`, or `tasks` to verify admission, reservation, accounting
binding, settlement, denial, and lexical fallback behavior.

### 1.2 Context + enrichment

Every event carries a context snapshot derived from `AccountingContext` (stored in async‑safe `contextvars`). The system flattens selected context keys into the event root using `CONTEXT_EXPORT_KEYS` and keeps the full context under `context`.

Key helpers:

- `with_accounting(component, **context)` to set component and overlay context keys.
- `set_context(...)` and `get_context()`
- `register_context_keys(...)` to expose additional context keys at the event root.

Reference:

- `kdcube_ai_app/infra/accounting/__init__.py`

---

## 2) Request flow (ingress → proc → accounting)

### 2.1 Ingress builds an envelope

Ingress creates an `AccountingEnvelope` using:

- `build_envelope_from_session()` in `kdcube_ai_app/infra/accounting/envelope.py`
- Example usage in `kdcube_ai_app/apps/chat/ingress/chat_core.py`

The envelope includes:

- `tenant_id`, `project_id`, `user_id`, `session_id`, `user_type`
- `request_id`, `component`, `app_bundle_id`
- metadata + seed system resources (optional)

### 2.2 Processor binds the context

Processor binds the envelope and storage backend to the async context:

- `bind_accounting()` in `kdcube_ai_app/infra/accounting/envelope.py`
- Used in `kdcube_ai_app/apps/chat/processor.py`

The binder:

1. Initializes accounting storage
2. Creates a fresh `AccountingContext`
3. Seeds metadata + system resources for enrichment

From this point on, all tracked calls emit events with this context.

### 2.3 Per‑step accounting scopes

The workflow uses `with_accounting()` to declare component/step‑level attribution:

- Example in `kdcube_ai_app/apps/chat/sdk/solutions/chatbot/base_workflow.py`
- Also used in React runtime and tool backends

Each nested scope can set:

- `component` name (e.g., `chat.orchestrator`, `context.compaction`)
- extra metadata (agent name, phase, tool id, etc.)

---

## 3) Storage layout

Accounting events are written by `FileAccountingStorage` into the configured storage backend (local FS or S3).

Base path: `accounting/`  
Default path strategy: `grouped_by_component_and_seed()`  
Path format (simplified):

```
accounting/<tenant>/<project>/<YYYY>.<MM>.<DD>/<service_type>/<group>/cb|<user>|<conv>|<turn>|<agent>|<ts>.json
```

Key points:

- Group folder uses `component` and `seed_system_resources` (if present)
- Filenames are conversation‑aware to enable fast prefix filtering
- Legacy date paths (`YYYY/MM/DD`) are still read by the calculator

References:

- `kdcube_ai_app/infra/accounting/__init__.py`
- `kdcube_ai_app/infra/accounting/calculator.py`

---

## 4) Per‑turn cache (Redis)

Accounting events can also be mirrored into Redis for fast per‑turn queries:

- Class: `TurnEventCache` in `kdcube_ai_app/infra/accounting/turn_cache.py`
- Key format: `acct:turn:<tenant>:<project>:<conversation_id>:<turn_id>`
- Value: Redis LIST of JSON events
- TTL is **sliding** (refreshed on each append)

This cache is used by `RateCalculator.calculate_turn_costs()` when `use_memory_cache=True`.

---

## 5) Cost calculation and economics

### 5.1 RateCalculator

Per‑turn cost calculation is performed by:

- `RateCalculator.calculate_turn_costs()` in `kdcube_ai_app/infra/accounting/calculator.py`

Outputs:

- `cost_total_usd`
- `cost_breakdown` (service/provider/model)
- `agent_costs`
- `token_summary` (weighted tokens for limits)

### 5.2 Turn accounting in workflows

- `apply_accounting()` in `kdcube_ai_app/apps/chat/sdk/solutions/chatbot/entrypoint.py`
- `entrypoint_with_economic.py` uses the same accounting results for budget enforcement

When applied, the workflow emits `accounting.usage` as:

- an SSE step (`comm.event`)
- a service event (`comm.service_event`)

These results feed:

- per‑turn economics decisions
- user budgets and rate‑limiters
- cost reporting for monitoring/analytics

---

## 6) Aggregation & OPEX queries

Aggregates are computed by `AccountingAggregator`:

- `kdcube_ai_app/infra/accounting/aggregator.py`
- scheduled via `kdcube_ai_app/apps/chat/ingress/opex/routines.py`

Raw events live under `accounting/…`  
Aggregates are written under:

```
analytics/<tenant>/<project>/accounting/
    daily/<YYYY>/<MM>/<DD>/{total,users,agents,apps}.json
    monthly/<YYYY>/<MM>/{total,users,agents,apps}.json
    yearly/<YYYY>/{total,users,agents,apps}.json
    hourly/<YYYY>/<MM>/<DD>/<HH>/total.json
```

Three per-dimension file families exist: **users** (`user_id`), **agents**
(`agent_name`), and **apps** (`apps.json`, keyed by the app's technical
`bundle_id` from the event's `app_bundle_id`). Raw event files are read with
bounded concurrency (`AccountingAggregator(read_concurrency=…)`, default 32).

Two schedulers keep aggregates current (both in `opex/routines.py`, started by
the opex lifespan):

- **nightly** (`OPEX_AGG_CRON` / assembly `routines.opex.agg_cron`, default
  03:00 Berlin) — aggregates *yesterday*, then folds the month.
- **today-refresh** (`OPEX_TODAY_REFRESH_CRON` / assembly
  `routines.opex.today_refresh_cron`, default hourly; `off` disables) —
  recomputes *today* and refolds the month, so windows including the current
  day are served from aggregates at most one interval stale instead of raw
  scans per request.

`RateCalculator.usage_by_user(...)` / `usage_by_app(...)` accept
`aggregates_only=True`: latency-sensitive callers (user-facing dashboards)
skip the raw-scan fallback — which reads every event file in the window — and
report "window not aggregated" instead. `POST
/api/opex/admin/run-aggregation-range` backfills a window synchronously and is
resumable: a day is skipped only when **all** its dimension files exist, so a
range aggregated before a new dimension was introduced regenerates cleanly.

**Reported-cost rule** (shared with turn settlement): when pricing a rollup
line, a price-table entry wins; a line without one is priced at the
provider-reported cost accumulated in `spent.cost_usd` (e.g. runtimes that
self-report spend). See `_compute_cost_estimate` in `opex/opex.py` and the
same rule in `RateCalculator.calculate_turn_costs`.

Spend-report surfaces on top of this data:

- `GET /api/opex/users` / `/agents` / `/apps` — per-dimension usage + true
  per-model cost (admin).
- `GET /api/economics/me/cost-breakdown` — the authenticated user's actual
  per-model spend (aggregates-only; `coverage` flags un-aggregated windows).
- Economics admin dashboard **Cost Report** tab
  (`apps/chat/ingress/control_plane/EconomicsDashboard.tsx`) — group by
  user/agent/app, id filter, CSV export, and a **Rebuild aggregates** action
  over `run-aggregation-range`. In the user grouping, recorded system
  principals (e.g. events whose `user_id` is literally `bundle`) are listed
  separately from people.

See:

- `repo:kdcube-ai-app/app/ai-app/docs/aggregations/README-AGGREGATIONS.md`

---

## 7) Configuration touchpoints

Key inputs that affect accounting behavior:

- **Storage backend**: derived from `KDCUBE_STORAGE_PATH` / `settings.STORAGE_PATH`
- **Web search pricing tiers**: `ACCOUNTING_SERVICES` (JSON) in `src/kdcube-ai-app/kdcube_ai_app/infra/accounting/usage.py` and OPEX API
- **Redis turn cache**: enabled in `AccountingSystem.init_storage()` by default

The accounting system itself is controlled by the service entrypoints and binders; there is no single global switch unless explicitly wired in the caller.

---

## 8) Key references

- Core system: `kdcube_ai_app/infra/accounting/__init__.py`
- Envelope + binding: `kdcube_ai_app/infra/accounting/envelope.py`
- Storage cache: `kdcube_ai_app/infra/accounting/turn_cache.py`
- Cost calculator: `kdcube_ai_app/infra/accounting/calculator.py`
- Aggregator: `kdcube_ai_app/infra/accounting/aggregator.py`
- OPEX API: `kdcube_ai_app/apps/chat/ingress/opex/opex.py`
- Aggregation scheduler: `kdcube_ai_app/apps/chat/ingress/opex/routines.py`
