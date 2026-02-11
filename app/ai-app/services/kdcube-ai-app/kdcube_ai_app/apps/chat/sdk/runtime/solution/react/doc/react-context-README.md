# ReAct v2 — Context + Turn Data

This doc is the single source of truth for what ReAct agents see and how context is built in v2.

---

## 1) What the Decision Agent Sees

The decision agent sees a **system message** and a **single human message** composed of blocks:

```
SYSTEM MESSAGE
  - policies + protocol
  - tool catalog
  - skills catalog
  - time guardrail

HUMAN MESSAGE
  - timeline blocks (history → current user → in‑turn progress)
  - optional sources pool (tail, uncached)
  - optional announce block (tail, uncached)
```

The human message blocks are created by `ContextBrowser.timeline(...)`.

---

## 2) Timeline Block Layout (v2)

`ContextBrowser.load_context(...)` builds and caches:
- **history blocks** (prior turns + summaries)
- **current turn user blocks** (user prompt + attachments)

Agents then append **in‑turn progress blocks** via `ContextBrowser.contribute(...)`.

Final timeline order:
```
[HISTORY BLOCKS]
[CURRENT TURN USER BLOCKS]
[TURN PROGRESS LOG]
[SOURCES POOL]   (optional, uncached)
[ANNOUNCE]       (optional, uncached)
```

Block formatting lives in:
- `react/v2/layout.py`

---

## 3) In‑turn Progress Blocks

Any agent can append progress blocks (gate/coordinator/react/etc.).
These represent work done **so far** in response to the current request.

```
ctx_browser.contribute(
    scratchpad=scratchpad,
    blocks=[...],
    persist=True,  # store in turn log
)
```

If `persist=True`, blocks are written to the turn log blocks.

---

## 4) Compaction

If the timeline is too large:
- `timeline(force_sanitize=True)` compacts the context and inserts `conv.range.summary`.
- Summaries are stored in the index, **not** in the turn log.

Compaction can happen mid‑turn and rewrites the cached stream.

See: `context-progression.md` + `context-caching-README.md`.

---

## 5) Turn Log (v2)

Turn log is persisted as a single JSON artifact (`artifact:turn.log`).
It is the source of truth for next‑turn reconstruction.

Minimal schema:
```
{
  "turn_id": "...",
  "ts": "...",
  "user": {
    "prompt": "...",
    "prompt_summary": "...",
    "attachments": [ ... ]
  },
  "assistant": {
    "completion": "...",
    "files": [ ... ],
    "blocks": [ ... ],
    "react_state": { ... }
  }
}
```

See: `turn-log-README.md` and `turn-data-README.md`.

---

## 6) Coordinator (v2)

Coordinator is a planning agent only:
- emits plan steps + budgets
- produces `instructions_for_downstream`
- no contract/slots in v2

Its output is contributed to the timeline as an in‑turn progress block.

---

## 7) Reference Docs

- `context-layout.md`
- `context-progression.md`
- `context-caching-README.md`
- `react-tools-README.md`
- `turn-log-README.md`
- `turn-data-README.md`
- `conversation-artifacts-README.md`
