# Bundle Authoring Guide (Chat SDK)

This guide replaces the old “First AI Bundle” doc. It’s based on the current reference bundle:
`kdcube_ai_app/apps/chat/sdk/examples/bundles/react@2026-02-10-02-44`.

Use it as the canonical template for building and running bundles in our platform.

---

## 1) Reference Bundle (Use This as Your Starting Point)

Bundle path:
`kdcube_ai_app/apps/chat/sdk/examples/bundles/react@2026-02-10-02-44`

Key files:
- `entrypoint.py` — bundle entrypoint (agentic_workflow)
- `orchestrator/workflow.py` — orchestration logic (BaseWorkflow)
- `agents/` — bundle-local agents (gate, etc.)
- `tools_descriptor.py` — tool registry for react
- `skills_descriptor.py` — skills visibility config
- `resources.py` — friendly error messages
- `event_filter.py` — event filtering policy

---

## 2) Recommended Bundle Layout

```
my_bundle/
├── entrypoint.py
├── orchestrator/
│   └── workflow.py
├── agents/
│   └── gate.py
├── tools_descriptor.py
├── skills_descriptor.py
├── resources.py
└── event_filter.py
```

Notes:
- `entrypoint.py` is required.
- `orchestrator/workflow.py` is recommended for real bundles.
- `tools_descriptor.py` and `skills_descriptor.py` define what react can use.

---

## 3) Entrypoint: Register the Bundle

Entrypoint uses `@agentic_workflow` and extends `BaseEntrypoint`. The reference bundle uses
LangGraph to drive orchestration.

Minimal pattern:
```python
from kdcube_ai_app.infra.plugin.agentic_loader import agentic_workflow
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint
from langgraph.graph import StateGraph, START, END

BUNDLE_ID = "my.bundle"

@agentic_workflow(name=BUNDLE_ID, version="1.0.0", priority=100)
class MyWorkflow(BaseEntrypoint):
    def __init__(...):
        super().__init__(..., event_filter=MyEventFilter())
        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        g = StateGraph(BundleState)
        g.add_node("orchestrate", orchestrate)
        g.add_edge(START, "orchestrate")
        g.add_edge("orchestrate", END)
        return g.compile()

    async def execute_core(self, *, state, thread_id, params):
        return await self.graph.ainvoke(state, config={"configurable": {"thread_id": thread_id}})
```

Model configuration lives in `entrypoint.configuration` and maps logical roles to providers/models
(`role_models`), as shown in the reference bundle.

---

## 4) Orchestrator Workflow (BaseWorkflow)

Use `BaseWorkflow` to handle:
- conversation context
- timeline persistence
- scratchpad lifecycle
- turn completion

Reference: `react@2026-02-10-02-44/orchestrator/workflow.py`.

Key patterns:
- Call `start_turn(scratchpad)` and `finish_turn(scratchpad, ok=...)`.
- Use the built-in `ContextBrowser` and timeline to render/react.
- Use `build_react(...)` to construct the react runtime and run `react.run(...)`.
- Gate is used only to extract conversation title on the first turn.

Output contract:
- Set `scratchpad.answer` for the assistant message.
- Set `scratchpad.suggested_followups` if you have followups.
- Return `{ "answer": ..., "suggested_followups": [...] }` from the workflow.

---

## 5) Tools and Skills Configuration

### tools_descriptor.py
Defines tool modules (SDK tools, local tools, MCP tools).
React uses these specs to build the tool catalog.

Fields to know:
- `TOOLS_SPECS`: list of modules or local refs.
- `MCP_TOOL_SPECS`: external MCP servers (aliases/allowlists).
- `TOOL_RUNTIME`: optional per-tool runtime (local/docker).

### skills_descriptor.py
Controls which skills are visible to specific agents.
Use `AGENTS_CONFIG` to enable/disable skills per role.

---

## 6) Event Filter

`event_filter.py` lets you restrict what events are visible to non-privileged users.
The reference bundle uses a minimal filter that blocks some internal events.

If you don’t need filtering, you can omit it or pass no filter to BaseEntrypoint.

---

## 7) Resources (Friendly Errors)

`resources.py` defines the user-facing error messages for:
- usage_limit
- rate_limit
- server_error
- timeout

BaseWorkflow can use this automatically when producing error responses.

---

## 8) Streaming and Output

In most bundles you should rely on BaseWorkflow’s built-in emitters:
- `mk_thinking_streamer` for agent thinking
- react streaming through `react.write` (canvas or timeline_text)

If you need direct streaming, use `AIBEmitters(self.comm)` and emit:
- `delta(...)` for token streams
- `step(...)` for progress steps
- `event(...)` for custom widgets

Markers commonly used:
- `answer`
- `thinking`
- `canvas`
- `timeline_text`

---

## 9) Register the Bundle

Configure in `AGENTIC_BUNDLES_JSON`:
```bash
export AGENTIC_BUNDLES_JSON='{
  "default_bundle_id": "react",
  "bundles": {
    "react": {
      "id": "react",
      "name": "React Bundle",
      "path": "/bundles/react",
      "module": "entrypoint",
      "singleton": false,
      "description": "Reference bundle"
    }
  }
}'
```

If running in Docker, also set:
```bash
export AGENTIC_BUNDLES_ROOT=/bundles
```

---

## 10) Checklist for a New Bundle

1. Create bundle folder with `entrypoint.py` and `orchestrator/workflow.py`.
2. Implement `BaseEntrypoint` with a small graph.
3. Implement `BaseWorkflow` orchestration.
4. Define `tools_descriptor.py` and `skills_descriptor.py`.
5. Register in `AGENTIC_BUNDLES_JSON`.
6. Run and confirm:
   - streaming appears in UI
   - timeline is persisted
   - followups and files are visible

---

## 11) Notes / Gotchas

- Use `react.write` for text artifacts only.
- Use `rendering_tools.write_*` for PDF/PPTX/DOCX/PNG.
- Always call `react.read` before using large or truncated artifacts.
- When using `channel=canvas`, the filename extension must be one of:
  `.md/.markdown, .html/.htm, .mermaid/.mmd, .json, .yaml/.yml, .txt, .xml`.

---

If you want a “starter bundle” repo, I can generate one from the reference bundle. 
