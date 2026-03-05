---
id: ks:docs/sdk/bundle/bundle-dev-README.md
title: "Bundle Dev"
summary: "Bundle developer guide: entrypoint setup, layout, tools/skills, ops API, and one‑time bundle init."
tags: ["sdk", "bundle", "development", "workflow", "entrypoint", "tools", "skills", "ui", "operations"]
keywords: ["agentic_workflow", "BaseEntrypoint", "tools_descriptor", "skills_descriptor", "operations API", "on_bundle_load", "knowledge space", "event_filter"]
see_also:
  - ks:docs/sdk/bundle/bundle-index-README.md
  - ks:docs/sdk/bundle/bundle-interfaces-README.md
  - ks:docs/sdk/bundle/bundle-ops-README.md
---
# Bundle Developer Guide (SDK)

This guide is for **bundle developers** who build workflows, tools, and UI experiences on top of the KDCube Chat SDK.

If you need **ops/runtime config** (registry, env vars, git bundles, release descriptors), see:
`docs/sdk/bundle/bundle-ops-README.md`.

---

## Reference bundle (start here)

Reference implementation:
`services/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/react@2026-02-10-02-44`

Key files:
- `entrypoint.py` — bundle entrypoint (decorated workflow)
- `orchestrator/workflow.py` — orchestration (BaseWorkflow)
- `agents/` — bundle-local agents (gate, etc.)
- `tools_descriptor.py` — tool registry for this bundle
- `skills_descriptor.py` — skills visibility config
- `resources.py` — user-facing error messages
- `event_filter.py` — event filtering policy

Related runtime entrypoints:
- Processor task runner: `apps/chat/processor.py` (loads bundle + calls `run`)
- Integrations API: `apps/chat/proc/rest/integrations/integrations.py`
  - `POST /bundles/{tenant}/{project}/operations/{operation}` invokes `workflow.<operation>(...)`
- Base entrypoint features: `apps/chat/sdk/solutions/chatbot/entrypoint.py`
  - Admin React apps like `ai_bundles`, `svc_gateway` can be exposed from bundles

---

## Recommended bundle layout

```
my_bundle/
  entrypoint.py
  orchestrator/
    workflow.py
  agents/
    gate.py
  tools_descriptor.py
  skills_descriptor.py
  resources.py
  event_filter.py
```

Notes:
- `entrypoint.py` is required.
- `orchestrator/workflow.py` is recommended for real bundles.
- `tools_descriptor.py` and `skills_descriptor.py` define the tool/skill surface.

---

## Entrypoint: register the bundle

Entrypoint uses `@agentic_workflow` and typically extends `BaseEntrypoint`.

Minimal pattern:
```python
from kdcube_ai_app.infra.plugin.agentic_loader import agentic_workflow
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint
from langgraph.graph import StateGraph, START, END

BUNDLE_ID = "my.bundle"

@agentic_workflow(name=BUNDLE_ID, version="1.0.0", priority=100)
class MyWorkflow(BaseEntrypoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
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

Model configuration lives in `entrypoint.configuration` (`role_models` etc).

---

## Bundle load hook (one‑time init)

Bundles can run a **one‑time initialization hook** when they are first loaded
(per process, per tenant/project). Use it to prepare local assets or indexes.

Hook signature (override in your entrypoint):
```python
class MyWorkflow(BaseEntrypoint):
    def on_bundle_load(self, *, storage_root=None, bundle_spec=None, logger=None, **_):
        # One-time init. Keep it deterministic and idempotent.
        if not storage_root:
            return
        # Example: create a local index folder inside the knowledge space
        ws = pathlib.Path(storage_root)
        (ws / "index").mkdir(parents=True, exist_ok=True)

        # Example: clone a docs repo into workspace (optional)
        # from kdcube_ai_app.infra.plugin.git_bundle import ensure_git_bundle
        # ensure_git_bundle(
        #     bundle_id="my-docs",
        #     git_url="git@github.com:org/docs.git",
        #     git_ref="v1.2.3",
        #     bundles_root=ws,
        # )
```

Notes:
- The hook is **synchronous**. If you need async work, run it inside the hook.
- Use `storage_root` to store shared local bundle data (see below).

---

## Storage types (clear separation)

Bundles have **two distinct storage options**:

1) **Bundle storage backend (localfs/S3)**  
   Configured via `CB_BUNDLE_STORAGE_URL`.  
   Use this for **read/write** data that should persist beyond the host.

2) **Shared bundle local storage (filesystem)**  
   Configured via `BUNDLE_STORAGE_ROOT`.  
   This is a **local filesystem mount** (host or EFS), namespaced per
   tenant/project/bundle. You can store **any shared local data** here
   (indexes, repos, caches, datasets).  
   The **knowledge space** is just one optional use of this storage.

See: `docs/sdk/bundle/bundle-storage-cache-README.md`.

---

## Knowledge space (read‑only, filesystem)

Bundles can prepare a **knowledge space** (local FS or EFS) to store indexes,
cloned repos, or cached reference data that should be reused across users/conversations/flows.

Env:
```
BUNDLE_STORAGE_ROOT=/bundles/_bundle_storage   # shared local bundle storage (default if not set)
```

Runtime:
- The platform resolves `bundle_storage` for the active bundle.
- ReAct tools can read from it using `ks:<relpath>` with `react.read`.
- `react.search_knowledge(query="...")` searches it when the bundle provides a resolver.

Example path:
```
ks:docs/README.md
ks:src/apps/chat/sdk/solutions/react/v2/runtime.py
ks:deploy/docker/all_in_one_kdcube/docker-compose.yaml
```

See:
- `docs/sdk/bundle/bundle-knowledge-space-README.md`

---

## Orchestrator workflow (BaseWorkflow)

Use `BaseWorkflow` to manage:
- timeline persistence
- scratchpad lifecycle
- turn completion

Key patterns:
- Call `start_turn(scratchpad)` and `finish_turn(scratchpad, ok=...)`.
- Use `ContextBrowser` for context/timeline access.
- Build and run the ReAct agent via `build_react(...)` and `react.run(...)`.

Output contract:
- Set `scratchpad.answer` for the assistant message.
- Set `scratchpad.suggested_followups` if you have followups.
- Return `{ "answer": ..., "suggested_followups": [...] }`.

---

## Inputs and outputs

Primary runtime input:
- `ChatTaskPayload` / `ChatTaskRequest` (tenant, project, conversation_id, turn_id, user, etc.)
- Passed into the workflow via `run(...)` / `execute_core(...)`

Operation inputs:
- `POST /bundles/{tenant}/{project}/operations/{operation}` passes JSON body as kwargs to `workflow.<operation>(...)`.

Outputs:
- Streaming events (deltas, steps, widgets) via `ChatCommunicator`.
- Final JSON response (`final_answer`, `suggested_followups`, etc.) returned by the workflow.

Code references:
- `apps/chat/sdk/protocol.py`
- `apps/chat/sdk/solutions/chatbot/base_workflow.py`

---

## Tools and skills configuration

### tools_descriptor.py
Defines tool modules (SDK tools, local tools, MCP tools).
React uses these specs to build the tool catalog.

### skills_descriptor.py
Controls which skills are visible to specific agents.
Use `AGENTS_CONFIG` to enable or hide skills per role.

---

## Event filter (optional)

`event_filter.py` lets you restrict what events are visible to non-privileged users.
If you don’t need filtering, omit it or pass no filter to `BaseEntrypoint`.

---

## Streaming and output

Prefer BaseWorkflow’s built-in emitters:
- `mk_thinking_streamer` for thinking
- ReAct streaming via `react.write` (canvas or timeline_text)

If you need direct streaming, use `AIBEmitters(self.comm)`:
- `delta(...)` for token streams
- `step(...)` for progress steps
- `event(...)` for custom widgets

Common markers:
- `answer`
- `thinking`
- `canvas`
- `timeline_text`
- `subsystem`

---

## Event filtering (per‑bundle)

Bundles can define an **event filter** (bundle‑level outbound firewall) to control
which events are visible to non‑privileged users.

Example:
- `services/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/react@2026-02-10-02-44/event_filter.py`

Docs:
- `docs/service/comm/comm-system.md` (event types + filtering)
- `docs/sdk/bundle/bundle-firewall-README.md` (bundle outbound firewall)

---

## Files + attachments hosting

Bundles can emit files (artifacts) and consume user attachments. The platform
stores and serves them; the timeline includes references so the client can show
downloads and previews.

Docs:
- Attachments system: `docs/hosting/attachments-system.md`
- SSE events (attachments/artifacts): `docs/clients/sse-events-README.md`
---

## Bundle UI panels and operations

Bundles can expose **React panels** and **operations**:

- Base entrypoint: `apps/chat/sdk/solutions/chatbot/entrypoint.py`
  - Admin panels like `ai_bundles`, `svc_gateway` are exported as bundle apps.
- Integrations API: `apps/chat/proc/rest/integrations/integrations.py`
  - `POST /bundles/{tenant}/{project}/operations/{operation}` calls `workflow.<operation>(...)`.

Docs:
- `docs/sdk/bundle/bundle-interfaces-README.md`

---

## Bundle developer capabilities (at a glance)

| Capability | What you get | Where to learn |
|---|---|---|
| Streaming | deltas, steps, widgets | `docs/sdk/comm/README-comm.md` |
| Timeline + context | read/write, search, attachments | `docs/sdk/runtime/solution/context/browser-README.md` |
| Tools | local + isolated + MCP | `docs/sdk/tools/tool-subsystem-README.md`, `docs/sdk/tools/mcp-README.md` |
| Skills | prompt-time skills registry | `docs/sdk/skills/skills-README.md`, `docs/sdk/skills/skills-infra-README.md` |
| Storage | per‑bundle storage (file/S3) | `docs/sdk/bundle/bundle-storage-cache-README.md` |
| Knowledge space | bundle‑defined `ks:` docs + search | `docs/sdk/bundle/bundle-knowledge-space-README.md` |
| Cache | Redis KV cache | `docs/sdk/bundle/bundle-storage-cache-README.md` |
| Custom UI | widgets + React panels | `docs/sdk/bundle/bundle-interfaces-README.md` |
| Economics | budgets/usage tracking | `docs/sdk/infra/economics/economics-usage.md` |

---

## Custom tools (bundle‑local)

Use `tools_descriptor.py` to expose tools to the runtime. Example:
- Bundle: `apps/chat/sdk/examples/bundles/with-isoruntime@2026-02-16-14-00`
- Tool module: `apps/chat/sdk/examples/bundles/with-isoruntime@2026-02-16-14-00/tools/local_tools.py`
- Descriptor: `apps/chat/sdk/examples/bundles/with-isoruntime@2026-02-16-14-00/tools_descriptor.py`

Docs:
- `docs/sdk/tools/tool-subsystem-README.md`
- `docs/sdk/tools/mcp-README.md`
- `docs/sdk/tools/custom-tools-README.md`

---

## Custom skills (bundle‑local)

Use `skills_descriptor.py` to register bundle‑specific skills. Example:
- Bundle: `services/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/react@2026-02-10-02-44`
- Skill: `skills/product`
- Descriptor: `skills_descriptor.py`

Docs:
- `docs/sdk/skills/skills-README.md`
- `docs/sdk/skills/skills-infra-README.md`
- `docs/sdk/skills/custom-skills-README.md`

## Cache and bundle lifetime

- Bundles may be loaded per request or reused as singletons.
- If `singleton=true` in the registry, the workflow instance is cached and reused.
- Use the KV cache abstraction for lightweight runtime state or config.
  See: `infra/service_hub/cache-README.md`

---

## Storage + cache

See: `docs/sdk/bundle/bundle-storage-cache-README.md`

---

## Register for local dev

Local path bundle registration (dev only):
```bash
export AGENTIC_BUNDLES_JSON='{
  "default_bundle_id": "react@2026-02-10-02-44",
  "bundles": {
    "react@2026-02-10-02-44": {
      "id": "react@2026-02-10-02-44",
      "name": "React Bundle",
      "path": "/bundles",
      "module": "react@2026-02-10-02-44.entrypoint",
      "singleton": false,
      "description": "Reference bundle"
    }
  }
}'
```

If running in Docker:
```
export AGENTIC_BUNDLES_ROOT=/bundles
```

---

## Examples

- ReAct Agent:
  `services/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/react@2026-02-10-02-44`
- Iso Runtime Demo:
  `services/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/with-isoruntime@2026-02-16-14-00`
- Economics Demo:
  `services/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/eco@2026-02-18-15-06`

---

## Related SDK docs

- ReAct agent docs:
  `docs/sdk/agents/react`
- Tool subsystem:
  `docs/sdk/tools/tool-subsystem-README.md`
- Comm system:
  `docs/sdk/comm/README-comm.md`
- Context browser:
  `docs/sdk/runtime/solution/context/browser-README.md`
- ISO runtime:
  `docs/sdk/runtime/isolated/README-iso-runtime.md`

---

## References (code)

- Bundle loader + cache: `services/kdcube-ai-app/kdcube_ai_app/infra/plugin/agentic_loader.py`
- Bundle registry: `services/kdcube-ai-app/kdcube_ai_app/infra/plugin/bundle_registry.py`
- Task processor: `services/kdcube-ai-app/kdcube_ai_app/apps/chat/processor.py`
- Integrations ops API: `services/kdcube-ai-app/kdcube_ai_app/apps/chat/proc/rest/integrations/integrations.py`
- Base entrypoint: `services/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/chatbot/entrypoint.py`
- Protocol types: `services/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/protocol.py`
- Base workflow: `services/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/chatbot/base_workflow.py`
- Event filter example: `services/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/react@2026-02-10-02-44/event_filter.py`
