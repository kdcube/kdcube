---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/search-operations-README.md
title: "Canvas Card Search Operations"
summary: "The pin-board search contract: exactly what text is indexed per card (the card-level snapshot, not the source object), when the per-user index is built (one index across all of a user's boards), the faiss vector backend and its files on shared storage, the semantic floor (incl. turning semantic off), the economical guard, and the observability logs."
status: active
tags: ["sdk", "solutions", "canvas", "pins", "search", "index", "faiss", "embeddings", "hybrid-search"]
keywords:
  [
    "canvas_search",
    "CanvasPinSearch",
    "card_text",
    "indexed material",
    "pin index",
    "faiss-local",
    "pins.index.sqlite",
    "pins.index.faiss",
    "card-level snapshot",
    "canvas.pin_search_backend",
    "docs_total log",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/pin-operations-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/pin-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/index/hybrid-index-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/index/hybrid-scoring-README.md
---
# Canvas Card Search Operations

`canvas_search` is read-only hybrid search over a user's canvas cards — semantic + lexical
+ recency, reciprocal-rank-fused — exposed as the generic `CanvasPinSearch` service
any bundle that mounts the canvas reuses. This page is the precise contract: what
gets indexed, when, on what backend, and how to observe it. The wiring is in
[Pin Integration → Pin Search](./pin-integration-README.md#pin-search); the fusion
math is the generic [hybrid scoring](../index/hybrid-scoring-README.md).

## What is indexed (the card-level snapshot)

The index material is **not the card id** and **not the source object's contents** —
it is a text blob composed from the card's own human-readable fields (`card_text`),
captured at pin/update time. A pin proxies an object in another subsystem that may
be unversioned, so we never re-fetch the source; the index reflects exactly what the
card holds. The blob is newline-joined, in this order (blank fields skipped):

| # | Field | Note |
|---|---|---|
| 1 | **label** | first non-empty of `label` / `map_label` / `title` / `name` |
| 2 | **title** | only if different from the label above |
| 3 | **description** | |
| 4 | **content_preview** | cached display preview, if any |
| 5 | **mime** | |
| 6 | **comments** | each comment's `text`/`body` |
| 7 | **kind** | e.g. `file`, `note`, `canvas`, `memory` |
| 8 | **ref / logical_path** | e.g. `fi:…`, `task:issue:…`, `cnv:…`, `mem:record:…` |

That blob is what gets **embedded** (semantic) and **FTS5-indexed** (lexical);
**recency** comes from the card's `updated_at`/`created_at`. The `card_id` is
metadata only — returned on a hit, never searched.

## Search payload and filters

Canvas card search has two compatible surfaces:

- `canvas_search`: the canvas-owned operation used by bundles that mount the
  canvas search service directly.
- `cnv` named-service facet: the standard provider surface for runtimes that
  register canvas as a named-service namespace. In those runtimes,
  `named_services.search_objects(namespace="cnv", ...)` searches cards,
  `named_services.object_schema(namespace="cnv")` returns board/card/object
  schemas and filters under `ret.extra.schema.search.filters`, and
  `named_services.upsert_object(namespace="cnv", ...)` creates or updates
  boards/cards when that operation is allowed for the consumer.

Canvas-owned refs are normal namespace refs with subnamespace/path segments:
`cnv:<board-name>`, `cnv:<board-name>@<revision>`, and hosted card content such
as `cnv:canvas/users/<user>/canvases/<board>/objects/<kind>/<card-id>/v000001.md`.
Direct mutation of the hosted object path is not a separate API; update the
owning `canvas.card`, and the storage layer produces the hosted `cnv:` object.

The current Versatile demo bundle may expose only `task` and `mem` in its
`named_services.*` catalog until it registers the canvas provider. That is
configuration, not a different protocol. The shared filter payload is:

```json
{
  "query": "text to search",
  "limit": 20,
  "canvas_name": "main",
  "canvas_id": "cnv:<user>:main",
  "all_boards": false,
  "kinds": ["file", "memory", "task", "note", "canvas", "search.result"],
  "namespaces": ["fi", "cnv", "mem", "task", "so"],
  "thresholds": {
    "semantic_score": 0.30
  }
}
```

Filter semantics:

| Field | Meaning |
|---|---|
| `canvas_name` | Named canvas to search. Defaults to `main` when omitted. |
| `canvas_id` | Explicit board id. Usually omitted unless the caller already has it. |
| `all_boards` | When true, searches all boards for the user. Otherwise searches one board. |
| `kinds` | Card-kind allowlist. A card matches if its canvas card kind is in the list. |
| `namespaces` | Root namespace allowlist for pinned refs, for example `fi`, `cnv`, `mem`, `task`, `so`. |
| `thresholds.semantic_score` | Cosine-similarity floor for semantic candidates. Raise to tighten. Set below `0` to turn semantic search off. |

Use `thresholds.semantic_score` for per-call semantic-floor control.

When rendered through `named_services.search_objects`, the tool catalog should
show the same keys concisely:

```text
cnv:
  - cnv — canvas cards (filters: canvas_name, canvas_id, all_boards, kinds, namespaces, thresholds; details: object_schema(namespace="cnv"))
```

**So you match on what's visible on the card** — its title, description, your
comments, its kind, and its ref/path. Searching for words that only exist *inside*
the proxied file/issue (never on the card) returns nothing — that's expected. For a
pin `rl_techniques.pdf`, "react actions" matches only if those words are in the
filename, description, or a comment.

## When the index is built

- **On canvas update** (pin add / edit / remove) — `CanvasPinSearch.index` embeds the
  changed cards and (re)builds the vector index, serialized per user with the
  runtime's observed file lock. Because the index file lives on shared storage (EFS),
  that lock is the **cluster critical section** that keeps two replicas from rebuilding
  the same per-user index at once — see
  [synchronization mechanisms](../../../service/synch-mechanisms/critical-section-README.md).
  This is the only place that pays the embedder for pin material. Embed-on-write: only
  new/changed cards are embedded.
- **Lazily on search (self-heal)** — if the active board has zero indexed docs (pins
  predate the indexing wiring, or a fresh process), `canvas_search` builds the index
  from the live cards before searching. Bounds the "search finds nothing because
  nothing was ever indexed" failure; cheap on later searches (embed-on-write).

**Layout-only patches are skipped.** A pure drag / resize (a `canvas_patch` whose ops
are all `move_card` / `resize_card`) changes no indexed text — `card_text` excludes
placement — so the index op is skipped entirely: no embed, no re-sync, no lock taken.
Dragging a card never triggers reindexing. A patch with any content op still indexes.

Search itself is read-only and embeds only the **query**, gated by the economical
guard (see below).

## Vector backends and their files

The index persists to a per-user SQLite DB; the vector backend is pluggable:

| Backend | Selector | Files (per user) | Notes |
|---|---|---|---|
| **faiss (file-backed)** | `faiss-local` (default) | `pins.index.sqlite` + `pins.index.faiss` | the production path. On shared storage (EFS) the files are shared across the cluster; needs faiss + numpy |
| faiss (cross-process) | `faiss-cached` | via `FaissProjectCache` | Redis-coordinated faiss for deployments with no shared FS |
| brute-force | `bruteforce` | `pins.index.sqlite` only | no-dep fallback for tests / no-faiss envs — not a production path |

Backend resolution order: explicit `vector_backend` arg → bundle prop
`canvas.pin_search_backend` → **`faiss-local`**. Files live under:

```
<bundle storage_root>/<tenant>/<project>/<safe(bundle_id)>/.pin-index/<safe(user_id)>/pins.index.sqlite
                                                                                       pins.index.faiss
```

where `<bundle storage_root>` is env `BUNDLE_STORAGE_ROOT` (or
`PLATFORM.APPLICATIONS.BUNDLE_STORAGE_ROOT`, else `<bundles_root>/_bundle_storage`).
The implementation lives in the generic infra: the [hybrid index](../index/hybrid-index-README.md)
(`infra/index/sqlite`) with backends in `infra/index/vector_store` (brute-force) and
`infra/index/faiss` (faiss — the index *uses* faiss, it does not contain it).

## Economical guard

The query embed costs money, so the semantic pass is gated by the shared
`search_semantic_guard(flow="canvas.pins.search")` — the same verify-only
`economic_preflight` gate memory and task-tracker search use. On denial (or for a
trivial query), search degrades to lexical + recency at zero embed cost. Indexing
(write-side embeds) is never gated; the board always stays indexed. Tuning knobs
(weights, RRF k, recency half-life, semantic floor) are in
[hybrid scoring](../index/hybrid-scoring-README.md).

### Semantic floor / turning semantic off

`CanvasPinSearch(min_semantic_score=…)` (or bundle prop
`canvas.pin_search_min_semantic_score`, or per-call
`payload["thresholds"]["semantic_score"]`) sets the semantic relevance floor —
default `0.30` so an unrelated query doesn't match the whole board (the filter/dim
UX needs a real boundary). Pass a **negative** value (e.g. `-1`) to turn the
semantic factor **off** entirely and run on lexical + recency only — for a bundle
with no embeddings or no budget. The three regimes
(`< 0` off / `= 0` no-floor / `> 0` floor) are documented in
[hybrid scoring](../index/hybrid-scoring-README.md#min_semantic_score--one-knob-three-regimes).

## Observability

Index and search log to `kdcube.canvas.pins` with the absolute DB path and counts:

```
[canvas.pins.index]  board=<id> cards=<n> docs_total=<n> db=<abs path>
[canvas.pins.search] q='<query>' scope=<board|all_boards> docs_total=<n> results=<n> db=<abs path>
[canvas.pins.search] lazy-built board=<id> cards=<n> docs_total=<n> db=<abs path>
```

`docs_total=0` on a search means nothing is indexed for that scope — distinguishes
"empty index" from "indexed but no match for this query".
