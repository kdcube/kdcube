# End‑to‑end flow (react v2)

This document provides a high‑level view of the turn lifecycle and how the React runtime
interacts with the timeline. We currently support a **single agent loop** on the shared
timeline (React). Gate is optional and only runs on new conversations to set the title.

## ASCII diagram

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Turn start                                                                 │
│  - BaseWorkflow.start_turn                                                   │
│  - ctx_browser.load_timeline() -> Timeline.load/persist                      │
│  - contribute user prompt + attachments                                      │
└───────────────┬────────────────────────────────────────────────────────────┘
                │
                ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ Gate agent (optional; new conversation only)                                 │
│  - timeline.render(include_sources=false, include_announce=false)            │
│  - emits gate block (+ clarifications) into timeline                         │
└───────────────┬────────────────────────────────────────────────────────────┘
                │
                ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ ReAct runtime                                                                │
│  - decision loop uses timeline.render(include_sources=true, include_announce=true)
│  - each tool call contributes:                                               │
│      • react.tool.call                                                      │
│      • react.tool.result (+ artifacts blocks)                               │
│      • react.notice on protocol/errors                                      │
│  - plan acknowledgements add:                                                │
│      • react.plan.ack (text)                                                 │
│      • updated react.plan (JSON snapshot)                                    │
│  - final answer produced by React and stored as assistant.completion          │
└───────────────┬────────────────────────────────────────────────────────────┘
                │
                ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ Turn end                                                                     │
│  - persist timeline                                                          │
│  - write turn_log (current‑turn blocks only)                                 │
└────────────────────────────────────────────────────────────────────────────┘
```

## Mermaid diagram

```mermaid
flowchart TD
    A[Turn start: load_timeline + user prompt/attachments] --> B[Gate agent optional]
    B --> C[ReAct runtime single agent loop]
    C --> D[Persist timeline + turn_log]

    B -->|contribute gate/clarifications| T1[(Timeline)]
    C -->|tool call/result blocks + plans + assistant completion| T1
```

## Referent flow (current)

The reference implementation is:
`kdcube_ai_app/apps/chat/sdk/examples/bundles/react@2026-02-10-02-44/orchestrator/workflow.py`

Key points:
- Gate runs only for new conversations (title extraction).
- React is the sole agent loop.
- No coordinator or separate final‑answer generator.
