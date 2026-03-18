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
  - ks:docs/sdk/bundle/bundle-platform-properties-README.md
---
# Bundle Developer Guide (SDK)

This guide is for **bundle developers** who build workflows, tools, and UI experiences on top of the KDCube Chat SDK.

If you need **ops/runtime config** (registry, env vars, git bundles, assembly descriptors), see:
[docs/sdk/bundle/bundle-ops-README.md](bundle-ops-README.md).

If you need **bundle config/secrets** (properties and secret keys), see:
[docs/service/configuration/bundle-configuration-README.md](../../service/configuration/bundle-configuration-README.md).

If you need the list of **platform-reserved bundle property paths** interpreted by
base/economics entrypoints and exec runtime, see:
[docs/sdk/bundle/bundle-platform-properties-README.md](bundle-platform-properties-README.md).

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

Model defaults live in `entrypoint.configuration` (`role_models`, `embedding`, etc).
Runtime overrides are applied via `bundle_props` (`bundles.yaml` + admin UI).
If you override `configuration`, call `super().configuration()` and use
`setdefault` for defaults so external overrides still win.

### Canonical turn error propagation

For entrypoint-level turn failures, use the base helper:

```python
try:
    orch = MyWorkflow(...)
    res = await orch.process({...})
    if not isinstance(res, dict):
        res = {}
    state["final_answer"] = res.get("answer") or ""
    state["followups"] = res.get("followups") or []
except Exception as e:
    await self.report_turn_error(state=state, exc=e, title="Turn Error")
```

Important details:
- Put workflow construction **inside** the `try`.
- This catches both constructor failures and runtime failures in the same path.
- `report_turn_error(...)` emits a real `chat.error` envelope for the client and
  also records turn-level error state.
- A plain `self.comm.step(status="error", ...)` is not the canonical user-facing
  path. It is useful for diagnostics, but the frontend primarily treats
  `chat.error` as the visible service-error channel.
- `report_turn_error(...)` also emits a diagnostic step event, so you get both:
  user-visible error propagation and step-level diagnostics.

Economics bundles:
- If your bundle extends `BaseEntrypointWithEconomics`, economics and quota
  exceptions must be allowed to propagate unchanged.
- The base `report_turn_error(...)` helper already re-raises
  `EconomicsLimitException`, so calling it from a broad `except Exception`
  block is safe.

Use this helper for **entrypoint/orchestration-level** failures. Inside deeper
workflow logic, you can still emit domain-specific `self.comm.error(...)` events
directly when you want to surface a structured tool/program/runtime failure to
the user before the whole turn aborts.

### Passing bundle props into custom workflows

`bundle_props` live on the entrypoint. If your custom workflow needs to read
bundle configuration directly with `self.bundle_prop(...)` or to resolve
runtime profiles with `self.resolve_exec_runtime(...)`, the workflow constructor
must accept `bundle_props` and forward them into `BaseWorkflow`.

Pattern:

```python
# entrypoint.py
orch = MyWorkflow(
    ...,
    bundle_props=self.bundle_props,
)
```

```python
# orchestrator/workflow.py
class MyWorkflow(BaseWorkflow):
    def __init__(
        self,
        *,
        conv_idx,
        kb,
        store,
        comm,
        model_service,
        conv_ticket_store,
        config,
        comm_context,
        ctx_client=None,
        bundle_props=None,
    ):
        super().__init__(
            conv_idx=conv_idx,
            kb=kb,
            store=store,
            comm=comm,
            model_service=model_service,
            conv_ticket_store=conv_ticket_store,
            config=config,
            comm_context=comm_context,
            ctx_client=ctx_client,
            bundle_props=bundle_props,
        )
```

Without that constructor wiring, entrypoint props are not automatically visible
inside the workflow instance.

---

## Bundle configuration & secrets

Bundles consume **non‑secret configuration** via `bundle_props` and **secrets**
via `get_secret()` using dot‑path keys.

Non‑secret config:
- Defined in `bundles.yaml` under `items[].config`.
- Base defaults are defined in `entrypoint.configuration`.
- Effective props are available via `bundle_props` (defaults + overrides).
- If you override `configuration`, call `super().configuration()` and use `setdefault`
  for defaults so external overrides still win.
- Nested YAML is preserved as a nested dict.
- Dot‑paths are not expanded at ingest time; resolve them at read time if needed.
- Platform-reserved property paths are interpreted by base entrypoints and runtime:
  - `role_models`
  - `embedding`
  - `economics.reservation_amount_dollars`
  - `execution.runtime`
- Canonical reference:
  [docs/sdk/bundle/bundle-platform-properties-README.md](bundle-platform-properties-README.md).

Example overrides:
```yaml
bundles:
  items:
    - id: "react@2026-02-10-02-44"
      config:
        role_models:
          solver.react.v2.decision.v2.strong:
            provider: "anthropic"
            model: "claude-sonnet-4-6"
          custom.agent.example:
            provider: "anthropic"
            model: "claude-3-5-haiku-20241022"
        embedding:
          provider: "openai"
          model: "text-embedding-3-small"
```

### Best practice: defining bundle defaults
When defining bundle defaults in code, prefer `configuration` and preserve
external overrides:

```python
@property
def configuration(self) -> Dict[str, Any]:
    config = dict(super().configuration)  # property, not a method
    role_models = dict(config.get("role_models") or {})
    role_models.setdefault("solver.react.v2.decision.v2.strong", {
        "provider": "anthropic",
        "model": "claude-sonnet-4-5-20250929",
    })
    config["role_models"] = role_models
    return config
```

This ensures the effective order remains:
`code defaults → bundles.yaml → runtime overrides`.

### Example: bundle-level Fargate exec override

If your bundle uses exec tools and you want it to route through Fargate without
depending only on proc-wide env vars, set:

```yaml
config:
  execution:
    runtime:
      mode: fargate
      enabled: true
      cluster: arn:aws:ecs:eu-west-1:100258542545:cluster/kdcube-staging-cluster
      task_definition: kdcube-staging-exec
      container_name: exec
      subnets:
        - subnet-xxxx
        - subnet-yyyy
      security_groups:
        - sg-xxxx
      assign_public_ip: DISABLED
```

That configuration is copied into `RuntimeCtx.exec_runtime` and then passed into
exec tool execution. Missing keys still fall back to proc env vars.

If your bundle supports more than one runtime, you can declare multiple
bundle-scoped profiles and either choose a default or select one at call time:

```yaml
config:
  execution:
    runtime:
      default_profile: fargate
      profiles:
        docker:
          mode: docker
        fargate:
          mode: fargate
          enabled: true
          cluster: arn:aws:ecs:eu-west-1:100258542545:cluster/kdcube-staging-cluster
          task_definition: kdcube-staging-exec
          container_name: exec
          subnets:
            - subnet-xxxx
            - subnet-yyyy
          security_groups:
            - sg-xxxx
          assign_public_ip: DISABLED
```

Bundle code can then use the default resolved runtime or explicitly choose
another supported profile.

Important:
- `bundles.yaml` is the source of truth for descriptor-backed bundle props.
- `Reset from env` and proc startup with `BUNDLES_FORCE_ENV_ON_STARTUP=1`
  rebuild the Redis props layer from `bundles.yaml` authoritatively.
- If a key was previously present in `bundles.yaml` and is later removed,
  the env reset deletes that stale Redis key instead of keeping the old value.
- Runtime/admin overrides can still be applied after startup, but they are
  overwritten again on the next env reset/startup when force-env is enabled.

Secrets:
- Defined in `bundles.secrets.yaml` under `items[].secrets`.
- Injected into the secrets manager using dot‑path keys like:
    - `services.anthropic.api_key`
    - `services.git.http_token`
    - `bundles.<bundle_id>.secrets.<key>`
See:
[docs/service/configuration/bundle-configuration-README.md](../../service/configuration/bundle-configuration-README.md).

Note: bundle secrets can be read at any time. If you use `bundles.secrets.yaml`
with the local secrets sidecar, keep read tokens non‑expiring:
`SECRETS_TOKEN_TTL_SECONDS=0` and `SECRETS_TOKEN_MAX_USES=0` in the workdir `.env`.
Bundle secrets are **write‑only**; admin UI shows keys only, not values.
If secrets come from `bundles.secrets.yaml`, the CLI stores the key list in the
sidecar as `bundles.<bundle_id>.secrets.__keys` so keys are visible in the UI.
Current semantics are upsert-only: removing a key from `bundles.secrets.yaml`
does not auto-delete it from the secrets provider.

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

See: [docs/sdk/bundle/bundle-storage-cache-README.md](bundle-storage-cache-README.md).

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
- [docs/sdk/bundle/bundle-knowledge-space-README.md](bundle-knowledge-space-README.md)

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
- If the workflow needs bundle configuration, accept `bundle_props` in the
  workflow constructor and forward it into `BaseWorkflow`.
- Inside the workflow, use:
  - `self.bundle_prop("some.dot.path")` to read raw configured values
  - `self.resolve_exec_runtime(profile="name")` to resolve a named exec profile

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
- [docs/service/comm/comm-system.md](../../service/comm/comm-system.md) (event types + filtering)
- [docs/sdk/bundle/bundle-firewall-README.md](bundle-firewall-README.md) (bundle outbound firewall)

---

## Files + attachments hosting

Bundles can emit files (artifacts) and consume user attachments. The platform
stores and serves them; the timeline includes references so the client can show
downloads and previews.

Docs:
- Attachments system: [docs/hosting/attachments-system.md](../../hosting/attachments-system.md)
- SSE events (attachments/artifacts): [docs/clients/sse-events-README.md](../../clients/sse-events-README.md)
---

## Bundle UI panels and operations

Bundles can expose **React panels** and **operations**:

- Base entrypoint: `apps/chat/sdk/solutions/chatbot/entrypoint.py`
  - Admin panels like `ai_bundles`, `svc_gateway` are exported as bundle apps.
- Integrations API: `apps/chat/proc/rest/integrations/integrations.py`
  - `POST /bundles/{tenant}/{project}/operations/{operation}` calls `workflow.<operation>(...)`.

Docs:
- [docs/sdk/bundle/bundle-interfaces-README.md](bundle-interfaces-README.md)

---

## Bundle developer capabilities (at a glance)

| Capability | What you get | Where to learn |
|---|---|---|
| Streaming | deltas, steps, widgets | `docs/sdk/comm/README-comm.md` |
| Timeline + context | read/write, search, attachments | `docs/sdk/runtime/solution/context/browser-README.md` |
| Tools | local + isolated + MCP | [docs/sdk/tools/tool-subsystem-README.md](../tools/tool-subsystem-README.md), [docs/sdk/tools/mcp-README.md](../tools/mcp-README.md) |
| Skills | prompt-time skills registry | [docs/sdk/skills/skills-README.md](../skills/skills-README.md), [docs/sdk/skills/skills-infra-README.md](../skills/skills-infra-README.md) |
| Storage | per‑bundle storage (file/S3) | [docs/sdk/bundle/bundle-storage-cache-README.md](bundle-storage-cache-README.md) |
| Knowledge space | bundle‑defined `ks:` docs + search | [docs/sdk/bundle/bundle-knowledge-space-README.md](bundle-knowledge-space-README.md) |
| Cache | Redis KV cache | [docs/sdk/bundle/bundle-storage-cache-README.md](bundle-storage-cache-README.md) |
| Custom UI | widgets + React panels | [docs/sdk/bundle/bundle-interfaces-README.md](bundle-interfaces-README.md) |
| Economics | budgets/usage tracking | `docs/sdk/infra/economics/economics-usage.md` |

---

## Custom tools (bundle‑local)

Use `tools_descriptor.py` to expose tools to the runtime. Example:
- Bundle: `apps/chat/sdk/examples/bundles/with-isoruntime@2026-02-16-14-00`
- Tool module: `apps/chat/sdk/examples/bundles/with-isoruntime@2026-02-16-14-00/tools/local_tools.py`
- Descriptor: `apps/chat/sdk/examples/bundles/with-isoruntime@2026-02-16-14-00/tools_descriptor.py`

Docs:
- [docs/sdk/tools/tool-subsystem-README.md](../tools/tool-subsystem-README.md)
- [docs/sdk/tools/mcp-README.md](../tools/mcp-README.md)
- [docs/sdk/tools/custom-tools-README.md](../tools/custom-tools-README.md)

---

## Custom skills (bundle‑local)

Use `skills_descriptor.py` to register bundle‑specific skills. Example:
- Bundle: `services/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/react@2026-02-10-02-44`
- Skill: `skills/product`
- Descriptor: `skills_descriptor.py`

Docs:
- [docs/sdk/skills/skills-README.md](../skills/skills-README.md)
- [docs/sdk/skills/skills-infra-README.md](../skills/skills-infra-README.md)
- [docs/sdk/skills/custom-skills-README.md](../skills/custom-skills-README.md)

## Cache and bundle lifetime

- Bundles may be loaded per request or reused as singletons.
- If `singleton=true` in the registry, the workflow instance is cached and reused.
- Use the KV cache abstraction for lightweight runtime state or config.
  See: `infra/service_hub/cache-README.md`

---

## Storage + cache

See: [docs/sdk/bundle/bundle-storage-cache-README.md](bundle-storage-cache-README.md)

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
  [docs/sdk/agents/react](../agents/react)
- Tool subsystem:
  [docs/sdk/tools/tool-subsystem-README.md](../tools/tool-subsystem-README.md)
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
