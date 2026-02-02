# Context Browser (Conversation Context + Search)

This guide shows how to use `ContextBrowser` to:
- search the conversation history (vector + recency)
- materialize turn logs and deliverables
- build a `ReactContext` from reconciled history

Relevant code:
- [browser.py](browser.py)
- [ctx_rag.py](../../../context/retrieval/ctx_rag.py)

---

## 1) Create a ContextBrowser

```python
from kdcube_ai_app.apps.chat.sdk.runtime.solution.context.browser import ContextBrowser

browser = ContextBrowser(
    ctx_client=self.ctx_client,
    logger=self.logger,
    turn_view_class=TurnView,
)
```

Notes:
- `ctx_client` is required for both `search(...)` and `materialize(...)`.
  It already holds `conv_idx` and `model_service`, so you don't need to pass them separately.

---

## 2) Search context (from gate queries)

In a typical flow, a gate agent proposes context queries (e.g., `gate_ctx_queries`).
You can pass those directly to the browser:

```python
targets = [
    {"where": "assistant", "query": "risk register"},
    {"where": "user", "query": "budget"},
]

best_tid, hits = await browser.search(
    targets=targets,
    user=user_id,
    conv=conversation_id,
    track=track_id,
    top_k=5,
    days=365,
    half_life_days=7.0,
    scoring_mode="hybrid",
    with_payload=True,
)
```

Return values:
- `best_tid`: best-matching turn id (or `None`)
- `hits`: list of search hits (optionally with materialized payloads)

---

## 3) Materialize turn history (turn log + deliverables)

`materialize(...)` loads turn logs and reconciles citations into a `ContextBundle`.

```python
browser = ContextBrowser(
    ctx_client=self.ctx_client,
    logger=self.logger,
    turn_view_class=TurnView,
)

bundle = await browser.materialize(
    materialize_turn_ids=turn_ids,
    user_id=user_id,
    conversation_id=conversation_id,
)
```

The resulting `ContextBundle` contains:
- `program_history`: raw turns
- `program_history_reconciled`: turns with reconciled citations
- `sources_pool`: global sources pool

Each turn entry includes a `turn_log` with structured user/assistant artifacts.

---

## 4) Save an artifact (proxy to ctx_rag)

```python
await browser.save_artifact(
    kind="conv.user_shortcuts",
    tenant=tenant,
    project=project,
    user_id=user_id,
    conversation_id=conversation_id,
    user_type=user_type,
    turn_id=turn_id,
    track_id=track_id,
    content={"items": shortcuts},
    content_str=json.dumps({"items": shortcuts}),
    bundle_id=bundle_id,
)
```

Notes:
- This is a convenience proxy to `ContextRAGClient.save_artifact(...)`.
- `content_str` is what gets indexed in `conv_messages.text`.
- Embeddings must be computed by the caller and passed in.

---

## 5) Build a ReactContext

```python
ctx = browser.make_react_context(
    bundle=bundle,
    scratchpad=scratchpad,
    user_id=user_id,
    conversation_id=conversation_id,
    turn_id=turn_id,
    bundle_id=bundle_id,
)
```

The `ReactContext` seeds:
- prior turns + deliverables
- sources pool (canonical SIDs)
- current turn metadata

---

## 6) Notes on turn logs

Turn logs are stored as artifacts in conversation storage and surfaced via
`program_history` entries. They include:
- user prompt + attachments
- assistant response + assistant‑produced files
- all agent responses (`agents_responses`)
- solver deliverables / results
- sources pool (canonical SIDs)
- turn summary
- full structured turn log entries (timeline)

This data is what the context browser uses to rebuild history and reconcile citations.

Storage layout reference:
- [sdk-store-README.md](../../../storage/sdk-store-README.md)
- [conversation-artifacts-README.md](conversation-artifacts-README.md)

---

If you want a minimal example with the gate -> search -> materialize pipeline,
see the orchestrator workflow:
- [workflow.py](../../../examples/bundles/with_context@2026-02-01-23-25/orchestrator/workflow.py)
