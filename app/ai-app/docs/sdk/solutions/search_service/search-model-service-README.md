---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/search_service/search-model-service-README.md
title: "Search Model Service"
summary: "Economics-guarded, accounting-emitting embedding facade for search: how any app indexes documents and embeds queries with budget enforcement and automatic accounting.usage events, without reimplementing economics."
status: draft
tags: ["sdk", "solutions", "search", "embeddings", "economics", "accounting"]
updated_at: 2026-06-23
keywords:
  [
    "search_model_service",
    "EconomicSearchModelService",
    "embed_texts",
    "embed_search_query",
    "economics guard",
    "accounting.usage",
    "make_search_model_service",
    "search flow",
    "headless config",
    "scheduled job",
    "databus runtime",
    "embedder provider api key",
    "401 unauthorized",
    "resolve_config_request_secrets",
    "AuthContext",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-subsystem-integration-README.md
---
# Search Model Service

Read these words first:

| Term | Meaning |
| --- | --- |
| App | The product/bundle whose entrypoint runs the work. |
| Entrypoint | The app's server object. It owns dependencies (model service, economics, accounting context). |
| Embedding | A vector produced from text, used for semantic search. Computing it costs money. |
| Economics enforcement | Reserving budget before the spend, then settling it, and denying when over budget. |
| `accounting.usage` | The usage/cost event the platform records for a billable operation. |
| Flow | The label a spend is attributed to, e.g. `news.search`. It tags the accounting event. |
| Search model service | The facade in this package that wraps embedding with economics + accounting. |
| Subject | The tenant/project/user the spend is charged to. Anonymous/incomplete ⇒ no enforcement. |

This package owns **search embeddings, enforced by economics**. It is a search
concern that *depends on* the economics and accounting primitives — never the
other way around. Apps do not call the economics layer directly and do not
reimplement reserve/settle/accounting; they ask their entrypoint for a guarded
embedder and call two methods.

## The one prerequisite

Your entrypoint must expose `search_model_service(...)`. It does when it extends
**`BaseEntrypointWithEconomics`** (or a memory variant). A plain `BaseEntrypoint`
does **not** have it.

```python
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_economic import BaseEntrypointWithEconomics

class MyEntrypoint(BaseEntrypointWithEconomics):
    ...
```

## Indexing documents (write path)

Get the guarded service for a flow, then embed. The **write path fails loud** —
an index write must not silently lose vectors.

```python
async def index_docs(self, docs):
    svc = self.search_model_service(flow="myapp.index")
    # embed_texts: economics reserved → embeddings computed → settled → accounting.usage emitted
    vectors = await svc.embed_texts([d.text for d in docs])
    # ... store vectors in your index ...
```

If you build on the shared FAISS+SQLite engine, hand it `svc.embed_texts` as its
`embed_fn` and pass `svc` as `model_service` — the engine re-embeds only new or
text-changed documents (the cached vectors live in the index), so a reindex
charges embeddings for the delta only.

## Embedding a query (read path)

The **query path degrades** instead of failing: on budget denial it returns
`None`, and the caller falls back to lexical/BM25.

```python
async def search(self, query: str):
    svc = self.search_model_service(flow="myapp.search")
    qvec = await svc.embed_search_query(query, flow="myapp.search")
    if qvec is None:
        return await self.lexical_search(query)   # economics denied → degrade
    return await self.semantic_search(query, qvec)
```

## What happens automatically

Inside `embed_texts` / `embed_search_query`, the service:

1. **reserves** budget for the estimated spend,
2. runs the embedding,
3. **settles** the actual cost, and
4. emits the **`accounting.usage`** event tagged with your `flow`.

You write none of that. The economics and accounting wiring lives behind the
facade.

## The fallback (read this)

`search_model_service(...)` returns the **raw `models_service`** — embeddings
still work but are **ungated** (no enforcement, no usage event) — when:

- economics is not wired on the entrypoint, or
- the subject is anonymous / missing tenant/project/user.

So enforcement requires economics enabled **and** a real subject. If you need a
hard guarantee that a path is metered, assert the returned service is an
`EconomicSearchModelService`, or fail closed on an anonymous subject yourself.

## Runtime requirements

The facade only **guards and meters** when the runtime gives it what it needs.
The hosting entrypoint/runtime must satisfy all of:

| Requirement | Why | Source |
| --- | --- | --- |
| Entrypoint extends `BaseEntrypointWithEconomics` (or a memory variant) | exposes `search_model_service(...)` | base class |
| Economics wired: `cp_manager`, `rl`, `budget_limiter` on the entrypoint | `economics_enabled(...)` gates the guard | economics base |
| Non-anonymous subject: tenant + project + user | the spend must be charged to someone | `runtime_identity()` + `comm_context` |
| Embedder provider **with a resolved API key** on `models_service` | the embedding HTTP call needs the credential | bundle config/secrets → `ModelServiceBase(config)` |
| Accounting storage initialized | so `accounting.usage` can be stored | platform / bundle |

If economics is off or the subject is anonymous, the facade falls back to raw
`models_service` (see [The fallback](#the-fallback-read-this)). But the **embedder
key requirement is absolute** — without it the embedding call itself fails with
`401 Unauthorized`, guarded or not.

### Headless contexts: scheduled jobs and the databus runtime

This is the sharp edge. In an HTTP request the entrypoint's `config` is populated
inline (session/request), so `models_service` already holds the provider key and
`comm_context` carries the user. **Outside a request — a scheduled job
(`@on_job`/cron) or a databus event handler — neither is true unless the runtime
establishes it.** A headless entrypoint built from a bare `Config()` has **no
provider key**, so `embed_texts` returns `401 Unauthorized`: search silently
degrades to lexical while indexing fails loud — the classic *"search works but
indexing fails"* symptom.

Before building the entrypoint for headless work, the runtime must:

1. **Resolve provider secrets into the config** so `ModelServiceBase` gets the key:
   ```python
   cfg_req = ConfigRequest(agentic_bundle_id=bundle_id, tenant=tenant, project=project)
   cfg_req = await resolve_config_request_secrets(cfg_req, bundle_id=bundle_id)
   config = create_workflow_config(cfg_req)   # config now carries the embedder key
   # entrypoint(config) → _rebuild_models_service() → ModelServiceBase(config) has the key
   ```
2. **Bind an auth context** so the subject resolves off-request:
   ```python
   auth = AuthContext.for_bundle_job(tenant=…, project=…, bundle_id=…, job_alias=…, source="bundle_scheduler")
   with bind_auth_context(auth), bind_current_bundle_id(bundle_id):
       instance = await get_workflow_instance_async(…)
   ```

This is what the bundle scheduler now does — see `sdk/runtime/bundle_scheduler.py`
(`_make_headless_config` + the auth-context bind) and the databus worker
(`AuthContext.from_mapping(...)`). Any new scheduled/databus path that embeds must
replicate **both** steps or it will 401. (Fixed in `28930a80`, which replaced a
bare `Config()` with `resolve_config_request_secrets` + `create_workflow_config`.)

## Flow naming

Use `"<app>.<operation>"`, e.g. `news.search`, `myapp.index`. The string is what
the `accounting.usage` event is attributed to, so keep it stable and meaningful.

## Where it lives

```text
sdk/solutions/search_service/
  factory.py        make_search_model_service(entrypoint, *, flow, subject=None)
                    economics_enabled / embedding_provider_model / economics_search_subject
  model_service.py  EconomicSearchModelService  (embed_texts, embed_search_query),
                    make_semantic_search_guard, embedding rate/reservation helpers
  __init__.py       public re-exports
```

The entrypoint method `search_model_service(flow=...)` is a one-line delegator to
`make_search_model_service`, shared by `BaseEntrypointWithEconomics` and the
memory mixin — one implementation, no duplication.

## Reference implementations

- **Canvas pin search** (in this repo) — the original consumer:
  `sdk/solutions/canvas/search/service.py` obtains the service via
  `getattr(entrypoint, "search_model_service", ...)` and routes index vs query.
- **News app** (in the `applications` repo) — a full bundle example:
  - `news@2026-05-20-12-05/entrypoint.py` — `class NewsEntrypoint(BaseEntrypointWithEconomics)`; `svc = self.search_model_service(flow="news.search")`, then `embed_fn=svc.embed_texts`, `model_service=svc`.
  - `news@2026-05-20-12-05/services/news/service.py` — `search_issues` builds/refreshes the index under a cluster lock and mirrors it to durable bundle storage.
  - `news@2026-05-20-12-05/services/news/search.py` — `NewsSearchIndex.reindex` (index embeddings) and `.search` (query embeddings).
