---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-economics-integration-README.md
title: "Bundle Economics Integration"
summary: "Practical guide for bundle authors wiring economics enforcement to chat turns, semantic search, background jobs, and task execution surfaces."
tags: ["sdk", "bundle", "economics", "semantic-search", "accounting", "testing"]
keywords:
  [
    "bundle economics integration",
    "search_model_service",
    "EconomicSearchModelService",
    "semantic search economics",
    "economics enforcement traces",
    "memory search economics",
    "canvas pin search economics",
    "task tracker search economics",
  ]
updated_at: 2026-06-17
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/economics/economic-enforcement-engine-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/accounting/accounting-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-entrypoint-classes-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-test-bundle-README.md
---
# Bundle Economics Integration

This page is the bundle-author entrypoint for economics enforcement. The
canonical engine details live in
[Economic Enforcement Engine](../../economics/economic-enforcement-engine-README.md);
this page shows how a bundle wires the engine through SDK building blocks.

## What Is Enforced

| Surface | Existing bundle/example | Enforcement shape | Runtime result |
| --- | --- | --- | --- |
| Chat or agent turn | any entrypoint derived from `BaseEntrypointWithEconomics` | `BaseEntrypointWithEconomics.run(...)` reserves, binds accounting, runs the turn, and settles usage | turn is admitted, denied, or charged at the chat-run boundary |
| Memory named-service search | versatile memory provider, namespace `mem` | provider receives `entrypoint.search_model_service(flow="memory.search")`; query embedding calls `embed_search_query(...)` | query embedding reserves and settles locally; denial returns no query vector and memory search ranks with lexical/text factors |
| Memory reconciliation | memory mixin reconciliation jobs | `EconomicsGuard(flow="memory.reconciler")` around the reconciliation job | job starts only after economics admission; job usage settles under the reconciliation scope |
| Canvas pin search | canvas solution `CanvasPinSearch` | search service receives `entrypoint.search_model_service(flow="canvas.pins.search")`; hybrid index calls `embed_search_query(...)` | query embedding reserves and settles locally; denial skips the semantic arm and keeps lexical/recency ranking |
| Custom searchable SDK component | any component that accepts a `model_service` | the entrypoint passes `search_model_service(flow="<your.surface>.search")` into the component; the component calls `embed_search_query(...)` | query embedding reserves and settles locally; denial falls back to lexical/recency ranking |
| Task execution | tasks solution run-now / due execution, including `task-and-memo-app@1-0` and `user-automation@1-0` | `_task_verify_economics(...)` checks feasibility before enqueue/run; the actual ReAct work routes through the economics entrypoint | denied tasks are cancelled before work; admitted task React work is charged by the economic entrypoint |
| Task list/search operations | tasks solution `tasks_search` and `task_executions_search` in task-and-memo and user-automation | SQLite FTS5/BM25 only | no semantic embedding spend is incurred; no semantic search guard is needed for these APIs |

## The Search Facade

Searchable components should receive one dependency:

```python
model_service = self.search_model_service(flow="your.surface.search")
```

The component then calls:

```python
await model_service.embed_texts(docs)
await model_service.embed_search_query(query, flow="your.surface.search")
```

`embed_texts(...)` is for document/index material and uses the underlying
accounting-aware model service. `embed_search_query(...)` wraps the actual query
embedding in `EconomicsGuard`, using the requested embedding provider/model and
the accounting price table to size the reservation.

The reservation estimate is:

```text
reservation_usd = max(0.000001, estimated_tokens * embedding_tokens_1M / 1_000_000)
estimated_tokens = max(16, chars / 4) per embedded query/text
```

For `openai/text-embedding-3-small` at `$0.02 / 1M tokens`, short queries usually
reserve the `$0.000001` floor.

## Implementation Recipes

### Entrypoint With Economics

For a chat/agent app:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_economic import (
    BaseEntrypointWithEconomics,
)


class MyEntrypoint(BaseEntrypointWithEconomics):
    ...
```

For an app that also mounts the memory subsystem:

```python
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_memory import (
    BaseEntrypointWithEconomicsAndMemory,
)


class MyEntrypoint(BaseEntrypointWithEconomicsAndMemory):
    ...
```

### Searchable SDK Component

The bundle entrypoint creates the searchable service with the facade:

```python
service = MySearchService(
    store=store,
    model_service=self.search_model_service(flow="my_component.search"),
)
```

The service uses the facade at query time:

```python
async def _query_embedding(self, query: str):
    embed = getattr(self.model_service, "embed_search_query", None)
    if callable(embed):
        return await embed(query, flow="my_component.search")
    return None
```

A `None` result means semantic ranking is unavailable for this call. Components
with lexical recall continue with lexical/recency ranking. Components without a
fallback surface the economics denial to the caller.

### Background Or Job Flow

For a background operation that performs paid work itself:

```python
async with EconomicsGuard(
    self,
    subject=subject,
    scope_id=f"job_{job_id}",
    flow="my_component.job",
    estimate=EconomicsEstimate(reservation_usd=0.05),
    policy=FlowPolicy(enforce_quota_lock=True),
):
    await run_paid_work()
```

For task execution through the tasks solution, the SDK already performs a
verify-only start check and then routes ReAct execution through
`BaseEntrypointWithEconomics.run(...)`, which owns the reservation and settlement
for the actual agent work.

## Existing Bundle Examples

| Example | File | What To Look For |
| --- | --- | --- |
| Memory named-service search | `kdcube_ai_app/apps/chat/sdk/solutions/chatbot/entrypoint_with_memory.py` and `context/memory/named_service.py` | `_memory_named_service_provider(... model_service=self.search_model_service(flow="memory.search"))` and provider `_search_embedding(...)` |
| Canvas pin search | `kdcube_ai_app/apps/chat/sdk/solutions/canvas/search/service.py` | `CanvasPinSearch._model_service()` resolving `entrypoint.search_model_service(flow=self.flow)` |
| Generic hybrid index | `kdcube_ai_app/infra/index/sqlite/hybrid_index.py` | `_embed_query(...)` calling `model_service.embed_search_query(...)` and skipping semantic ranking when it returns `None` |
| Enforcement core + search guard | `kdcube_ai_app/apps/chat/sdk/infra/economics/enforcement.py` and `infra/economics/search_guard.py` | the central enforcement state machine, and the `embed_search_query(...)` guard wrapper that reserves/settles per query and returns `None` on denial |
| Task execution economics | `kdcube_ai_app/apps/chat/sdk/solutions/tasks/operations.py` | `_task_verify_economics(...)` before execution and later ReAct work through the economics entrypoint |

## Logs And Verification

Economics enforcement emits centralized traces with the marker:

```text
[economics.enforcement]
```

Important stages:

| Stage | Meaning |
| --- | --- |
| `preflight_start` / `preflight_ok` | verify-only check was evaluated |
| `plan_resolved` | user role, plan, funding lane, and estimate were resolved |
| `admit` | quota admission ran and may have reserved plan tokens |
| `reserve_ok` | funding reservation was created |
| `accounting_bound` | accounting context was bound under the operation scope |
| `accounting_run_start` / `accounting_run_done` | recorded usage was collected for settlement |
| `settle_start` / `settle` | actual spend was settled and remaining reservation was released |
| `deny` | economics rejected the operation |
| `deny_cleanup` | reservations/locks are being released after a denial path |

Filter examples:

```bash
rg "\\[economics\\.enforcement\\]" "$LOG_DIR"
rg "memory.search|canvas.pins.search|task_tracker.issue.search|memory.reconciler|tasks" "$LOG_DIR"
```

Focused platform tests:

```bash
cd app/ai-app/src/kdcube-ai-app
python -m pytest \
  kdcube_ai_app/apps/chat/sdk/infra/economics/tests/test_enforcement.py \
  kdcube_ai_app/apps/chat/sdk/infra/economics/tests/test_enforcement_memory_search.py \
  kdcube_ai_app/infra/index/sqlite/tests/test_hybrid_index.py \
  kdcube_ai_app/apps/chat/sdk/context/memory/tests/test_named_service.py \
  kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/tests/test_canvas_pin_search_wiring.py
```

Bundle tests should prove the concrete surface:

- semantic search calls the economics-aware `model_service`;
- economics denial produces the documented fallback or denial state;
- lexical-only task searches remain deterministic and require no embedding
  reservation;
- background jobs record a denied/cancelled state before paid work starts.
