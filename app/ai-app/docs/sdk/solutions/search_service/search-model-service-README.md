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
