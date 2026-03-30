---
id: ks:docs/accounting/accounting-README.md
title: "Accounting & Usage Tracking"
summary: "How usage events are captured, stored, aggregated, and priced across the platform."
tags: ["accounting", "economics", "usage", "tracking", "opex"]
keywords: ["accounting events", "usage tracking", "cost calculation", "RateCalculator", "turn cache", "aggregations", "storage layout", "ACCOUNTING_SERVICES"]
see_also:
  - ks:docs/aggregations/README-AGGREGATIONS.md
  - ks:docs/economics/economic-README.md
  - ks:docs/economics/eco-admin-README.md
  - ks:docs/sdk/storage/sdk-store-README.md
  - ks:docs/service/README-monitoring-observability.md
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
```

OPEX endpoints in `apps/chat/ingress/opex/opex.py` read from both raw and aggregated data.

See:

- `ks:docs/aggregations/README-AGGREGATIONS.md`

---

## 7) Configuration touchpoints

Key inputs that affect accounting behavior:

- **Storage backend**: derived from `KDCUBE_STORAGE_PATH` / `settings.STORAGE_PATH`
- **Web search pricing tiers**: `ACCOUNTING_SERVICES` (JSON) in `infra/accounting/usage.py` and OPEX API
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
