---
name: bundle_builder
id: bundle_builder
description: |
  Canonical bundle-authoring playbook for KDCube. Use this when designing,
  generating, modifying, extracting, reviewing, or repairing bundles. Covers the
  bundle tree, runtime surfaces, lifecycle, operations/public APIs, widgets,
  storage, props, secrets, @venv, @cron, React v2, Claude Code, custom agents,
  and optional Node or TypeScript backend bridges.
version: 1.0.0
category: product-knowledge
tags:
  - bundle
  - sdk
  - authoring
  - react
  - claude-code
  - venv
  - cron
  - storage
  - props
  - secrets
  - node
  - typescript
when_to_use:
  - The user asks to build a new bundle
  - The user asks to add or repair bundle functionality
  - The user asks how to structure a bundle or which SDK surface to use
  - The user asks how to expose widgets, operations, public endpoints, or main UI
  - The user asks how to combine React v2, Claude Code, custom agents, or a Node backend in one bundle
  - The user asks how bundle props, secrets, storage, @venv, or @cron work
author: kdcube
created: 2026-04-12
namespace: product
---

# Bundle Builder

## Purpose

Use this as the primary bundle-authoring skill.

When the task is about writing or repairing a bundle, keep this skill loaded together with:

- `sk:tests.bundles`

Division of responsibility:

- `sk:product.bundle_builder`
  - bundle architecture
  - SDK surface selection
  - docs and example routing
  - runtime and lifecycle model
- `sk:tests.bundles`
  - current validation contract
  - exact pytest discovery and execution workflow

## Read order before coding

Start here in this order:

1. `ks:docs/sdk/bundle/bundle-index-README.md`
2. `ks:docs/sdk/bundle/bundle-reference-versatile-README.md`
3. `ks:docs/sdk/bundle/bundle-dev-README.md`
4. `ks:docs/sdk/bundle/bundle-runtime-README.md`
5. `ks:docs/sdk/bundle/bundle-platform-integration-README.md`
6. `ks:docs/sdk/bundle/bundle-props-secrets-README.md`
7. `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/README.md`
8. the smallest relevant files under:
   `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle`

Branch only after that:

- `ks:docs/sdk/bundle/bundle-scheduled-jobs-README.md`
- `ks:docs/sdk/bundle/bundle-node-backend-bridge-README.md`
- `ks:docs/sdk/agents/claude/claude-code-README.md`
- `ks:docs/sdk/agents/react/structure-README.md`
- `ks:docs/sdk/agents/react/plan-README.md`
- `ks:docs/sdk/agents/react/react-announce-README.md`
- `ks:docs/sdk/agents/react/external-exec-README.md`

## Bundle mental model

A bundle is an end-to-end KDCube app slice.

Normal shape:

- Python backend entrypoint
- optional bundle-local tools
- optional bundle-local skills
- optional widget UI
- optional custom main-view UI
- optional agent workflow
- optional scheduled jobs
- optional isolated/dependency-heavy helper runtimes

One bundle can combine:

- React v2
- Claude Code
- custom Python agents
- custom tools
- MCP tools
- Node or TypeScript domain logic behind a Python bridge

Python remains the KDCube-native app shell.

## Standard bundle tree

Use this as the default mental template:

```text
my.bundle@1-0/
  entrypoint.py
  orchestrator/
    workflow.py
  agents/
    gate.py
  tools_descriptor.py
  skills_descriptor.py
  tools/
    local_tools.py
  skills/
    product/
      my_skill/
        SKILL.md
  ui/
    MyWidget.tsx
  ui-src/
    src/
      App.tsx
  resources/
  tests/
  requirements.txt        # only when actually needed
  backend_bridge/         # optional Node/TS bridge
    cli.mjs
    ts_loader.mjs
    sample_routes.ts
```

Minimal bundles need much less than that. Real bundles often use most of it.

## Which surface to use

| Need | Use | Read first | Example |
| --- | --- | --- | --- |
| normal chat participation | `@on_message` + entrypoint/workflow | `ks:docs/sdk/bundle/bundle-dev-README.md` | `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/orchestrator/workflow.py` |
| authenticated bundle API | `@api(route="operations")` | `ks:docs/sdk/bundle/bundle-platform-integration-README.md` | `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/entrypoint.py` |
| anonymous or externally authenticated endpoint | `@api(route="public", public_auth=...)` | `ks:docs/sdk/bundle/bundle-platform-integration-README.md` | `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/entrypoint.py` |
| widget | `@ui_widget(...)` | `ks:docs/sdk/bundle/bundle-interfaces-README.md` | `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/ui/PreferencesBrowser.tsx` |
| full app UI | `@ui_main` + `ui-src/` | `ks:docs/sdk/bundle/bundle-interfaces-README.md` | `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/ui-src/src/App.tsx` |
| scheduled logic | `@cron(...)` | `ks:docs/sdk/bundle/bundle-scheduled-jobs-README.md` | `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/echo.ui@2026-03-30/entrypoint.py` |
| dependency-heavy Python leaf work | `@venv(...)` | `ks:docs/sdk/bundle/bundle-lifecycle-README.md` | `ks:docs/sdk/bundle/design/bundle-custom-venv-README.md` |
| direct code execution | isolated exec / `exec_tools.execute_code_python` | `ks:docs/sdk/agents/react/external-exec-README.md` | `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/with-isoruntime@2026-02-16-14-00/README.md` |
| Node or TypeScript domain backend | Python bridge + local Node backend | `ks:docs/sdk/bundle/bundle-node-backend-bridge-README.md` | `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/resources/node-backend-bridge/cli.mjs` |

## Runtime model

Keep these runtimes distinct:

1. shared proc runtime
   - bundle entrypoint code
   - communicator
   - request context
   - DB/Redis handles
   - bundle props and secrets

2. isolated exec runtime
   - code executed through `exec_tools.execute_code_python`
   - good for generated programs, file work, searches, controlled execution

3. bundle-local `@venv(...)` subprocess runtime
   - cached per bundle
   - for dependency-heavy leaf helpers
   - no live proc communicator/DB/request bindings inside the venv child

4. optional Node or TS backend bridge
   - not a bundle entrypoint replacement
   - Python resolves KDCube props/secrets/context first
   - Python passes a narrow payload to Node

If the task confuses these runtimes, stop and separate them before coding.

## Lifecycle model

The bundle lifecycle is:

1. discovery by decorators
2. entrypoint instance creation or singleton reuse
3. `on_bundle_load(...)` one-time prep
4. request-bound context refresh
5. turn/API/widget execution
6. optional `@venv(...)` or isolated exec boundaries inside that invocation

Read:

- `ks:docs/sdk/bundle/bundle-lifecycle-README.md`

Do not store durable state in Python instance fields.

Durable state belongs in:

- bundle props
- bundle secrets
- user props
- user secrets
- bundle storage
- database records
- cached bundle venvs

## Operations and public endpoints

Bundle APIs are explicit. Only decorated methods are remotely callable.

Authenticated operation example:

```python
@api(
    alias="preferences_summary",
    method="GET",
    route="operations",
    roles=("registered",),
)
async def preferences_summary(self, **kwargs):
    ...
```

Public endpoint example:

```python
@api(
    alias="registration-request",
    method="POST",
    route="public",
    roles=(),
    public_auth="none",
)
async def submit_registration_request(self, *, email: str, **kwargs):
    ...
```

Rules:

- `route="operations"` for authenticated internal app operations
- `route="public"` for deliberately public/external endpoints
- if `route="public"`, set `public_auth`
- support `GET` or `POST` only when the API shape really needs them

Primary example:

- `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/entrypoint.py`

## Widgets and main UI

Use:

- `@ui_widget(...)` for discoverable widgets
- `@ui_main` for the bundle's full custom iframe app

Primary examples:

- widget:
  `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/ui/PreferencesBrowser.tsx`
- main UI:
  `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/ui-src/src/App.tsx`

## Storage, props, and secrets

Deployment-scoped non-secret config:

- `self.bundle_prop("dot.path", default=...)`
- `self.bundle_props`

Bundle-scoped deployment secrets:

- `get_secret("b:some.dot.path")`

User-scoped non-secret bundle props:

- `get_user_prop("some.key")`
- `set_user_prop("some.key", value)`

User-scoped secrets:

- `get_user_secret("some.key")`
- `set_user_secret("some.key", value)`

Shared bundle storage:

- use the bundle storage APIs from the storage docs/reference bundle
- keep large or persistent bundle-owned state there, not in props

Read:

- `ks:docs/sdk/bundle/bundle-props-secrets-README.md`
- `ks:docs/sdk/bundle/bundle-storage-cache-README.md`

Primary examples:

- props and APIs:
  `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/entrypoint.py`
- storage:
  `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/preferences_store.py`
- bundle-local tools reading bundle/user state:
  `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/tools/preference_tools.py`

Normal restriction:

- bundle code should read its own bundle secrets through `b:...`
- do not design normal bundle code around reading another bundle's secrets

## `@venv(...)`

Use `@venv(...)` only when:

- the dependency is not safely assumed in shared proc runtime
- the work is leaf-level and serializable
- you want bundle-scoped cached dependency isolation

Do not put request-bound communicator or DB/Redis objects across that boundary.

Read:

- `ks:docs/sdk/bundle/bundle-lifecycle-README.md`
- `ks:docs/sdk/bundle/design/bundle-custom-venv-README.md`

## `@cron(...)`

Use `@cron(...)` when the bundle needs scheduled jobs.

The bundle can combine:

- scheduled APIs
- bundle props
- bundle storage
- user-managed schedules/preferences
- agent execution underneath

Read:

- `ks:docs/sdk/bundle/bundle-scheduled-jobs-README.md`

## React v2, Claude Code, or both

Use React v2 when the bundle needs:

- long-running ReAct loop
- plan management through `react.plan`
- ANNOUNCE and timeline-driven collaboration
- source pool and timeline-based context
- `react.hide` / cache-aware self-cleanup
- exec-heavy or tool-heavy work

Read:

- `ks:docs/sdk/agents/react/structure-README.md`
- `ks:docs/sdk/agents/react/plan-README.md`
- `ks:docs/sdk/agents/react/react-announce-README.md`
- `ks:docs/sdk/agents/react/external-exec-README.md`

Use Claude Code when the bundle needs:

- workspace-scoped code agent behavior
- persistent Claude Code session binding per user/conversation
- native Claude Code tools and permission modes

Read:

- `ks:docs/sdk/agents/claude/claude-code-README.md`

Bundle rule:

- React v2 is not the only agent option
- Claude Code is not the only agent option
- one bundle may use React for orchestration and Claude Code for workspace tasks
- one bundle may also add custom agents for domain-specific work

## Node or TypeScript backend bridge

If a real backend service already exists in Node or TypeScript, keep Python as the bundle shell and add a narrow bridge.

Read:

- `ks:docs/sdk/bundle/bundle-node-backend-bridge-README.md`

Public example paths:

- `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/resources/node-backend-bridge/cli.mjs`
- `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/resources/node-backend-bridge/ts_loader.mjs`
- `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/resources/node-backend-bridge/sample_routes.ts`

Rules:

- Python owns KDCube lifecycle, auth, props, secrets, and public APIs
- Node stays behind an explicit local bridge
- pass only narrow values from Python to Node

## Dependency rule before adding packages

Do not guess runtime dependencies.

Before adding:

- a new import
- `requirements.txt`
- or `@venv(...)`

inspect the actual runtime package set first from isolated exec.

Typical probes:

- `python -m pip list`
- targeted filtering
- small import/version checks

Then decide:

- dependency already in runtime and safe in proc
  - do not add it
- dependency missing or intentionally isolated
  - add the minimal package set
- dependency-heavy leaf logic
  - isolate it behind `@venv(...)`

Do not copy full `pip freeze`.

## Authoring workflow

Use this loop:

1. read the bundle docs start point
2. read the reference bundle docs and exact source files
3. load `sk:tests.bundles`
4. read the smallest relevant current pytest files
5. map the request to exact bundle surfaces
6. confirm imports/runtime symbols from current docs/source
7. only then write code
8. run the smallest relevant validation subset

Default source anchors:

- `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/entrypoint.py`
- `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/orchestrator/workflow.py`
- `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/tools_descriptor.py`
- `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/skills_descriptor.py`
- `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/with-isoruntime@2026-02-16-14-00/README.md`

If the task is narrower than `versatile`, branch only then.

## Do not do this

- do not invent SDK import paths
- do not skip reading tests for bundle code generation
- do not use `@venv(...)` for whole orchestration paths when only a leaf helper needs it
- do not put bundle secrets lookup logic on the Node side
- do not confuse public routes with normal authenticated operations
- do not treat Python instance fields as durable state
