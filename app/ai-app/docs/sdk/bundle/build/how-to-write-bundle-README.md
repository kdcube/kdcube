---
id: ks:docs/sdk/bundle/build/how-to-write-bundle-README.md
title: "How To Write A Bundle"
summary: "Operational guide for building KDCube bundles: design flow, entrypoint patterns, widget/API contracts, storage, config, and the bundle-builder pitfalls to avoid."
tags: ["sdk", "bundle", "authoring", "workflow", "widget", "api", "testing"]
keywords: ["how to write bundle", "bundle builder", "bundle authoring", "versatile reference", "bundle widget", "bundle api", "bundle storage", "bundle props"]
see_also:
  - ks:docs/sdk/bundle/build/how-to-test-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - ks:docs/sdk/bundle/bundle-dev-README.md
  - ks:docs/sdk/bundle/bundle-reference-versatile-README.md
  - ks:docs/sdk/bundle/bundle-platform-integration-README.md
  - ks:docs/sdk/bundle/bundle-widget-integration-README.md
  - ks:docs/sdk/bundle/bundle-runtime-README.md
  - ks:docs/sdk/bundle/bundle-props-secrets-README.md
---
# How To Write A KDCube Bundle

This document is written for a builder agent or engineer who must create or maintain bundles in this repo.

It is not a conceptual overview.
It is the working instruction set for doing the job correctly.

Primary references:

- bundle docs under `docs/sdk/bundle/`
- the reference bundle:
  `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36`

Use this document together with:

- [how-to-test-bundle-README.md](how-to-test-bundle-README.md)
- [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md)
- [bundle-dev-README.md](../bundle-dev-README.md)
- [bundle-reference-versatile-README.md](../bundle-reference-versatile-README.md)
- [bundle-platform-integration-README.md](../bundle-platform-integration-README.md)
- [bundle-widget-integration-README.md](../bundle-widget-integration-README.md)
- [bundle-runtime-README.md](../bundle-runtime-README.md)
- [bundle-props-secrets-README.md](../bundle-props-secrets-README.md)

## 1. Working Method

When you build a bundle, do not invent the platform contract from memory.

Work in this order:

1. read the relevant bundle docs first
2. inspect the `versatile` reference bundle for the nearest working pattern
3. inspect the platform implementation only when docs/reference are not enough
4. then write the bundle
5. then run the shared bundle suite and bundle-local tests
6. then verify the actual UI/API runtime behavior

Practical rule:

- docs define the intended contract
- `versatile` shows the reference bundle shape
- platform source is the last resort for unresolved edge cases

Configuration/runtime rule:

- use this page for how to structure the bundle code
- use [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md) for `assembly.yaml`, `bundles.yaml`, `bundles.secrets.yaml`, `kdcube --build --upstream`, and `kdcube --info`

## 1A. What A Bundle Is

A KDCube bundle is a descriptor-addressed application unit that the platform
can discover, load, expose, and run through one or more surfaces.

In practical terms, a bundle is:

- code resolved from a bundle entry in `bundles.yaml`
- runtime metadata declared by decorators in `entrypoint.py`
- deployment-scoped non-secret config from bundle props
- deployment-scoped secret config from bundle secrets
- optional local mutable filesystem state under bundle storage
- optional remote state in platform or external storage systems

A bundle is not only a chat workflow.

It may expose one or more of these surfaces:

- on-message/chat handling
- authenticated operations APIs
- public APIs
- widgets
- iframe apps
- MCP endpoints
- scheduled jobs

Operational rule:

- think of a bundle as a product module with a runtime contract
- descriptors decide how it is wired into the environment
- decorators decide what interfaces it exposes
- runtime context decides which execution path the code is in

Environment rule:

- one `tenant/project` runtime is one isolated environment
- use a different `tenant/project` when you need separate customer data or
  separate stages such as `dev`, `staging`, and `prod`
- keep multiple bundles inside one `tenant/project` when they belong to the
  same environment

So a bundle is the end-to-end application unit inside an environment.
`tenant/project` is the environment boundary, not the bundle boundary.

## 1B. Bundle Lifecycle

When a bundle exists in a real environment, its lifecycle is:

1. A bundle entry in `bundles.yaml` identifies the code and supplies bundle props.
2. The platform resolves the bundle root/module and imports `entrypoint.py`.
3. Decorators are discovered and the bundle interface manifest is built.
4. The bundle becomes discoverable through integrations listing, subject to:
   - roles / user-types
   - bundle-level `enabled_config`
   - resource-level `enabled_config`
5. The bundle is then entered through one of the runtime paths:
   - chat/on-message
   - operations/public API
   - widget-driven operation calls
   - MCP endpoint dispatch
   - cron/scheduled job
6. During execution, the bundle reads:
   - effective bundle props via `bundle_prop(...)`
   - secrets via `get_secret(...)`
   - typed platform settings via `get_settings()`
7. Mutable state goes to the right tier:
   - bundle local storage for instance-local filesystem state
   - `AIBundleStorage` for bundle artifacts
   - DB/Redis/external systems for runtime/business state
8. Config changes are applied by reload/reconcile:
   - `bundles.yaml` / `bundles.secrets.yaml` changes
   - bundle reload
   - scheduler reconciliation for cron jobs

Builder rule:

- design the bundle around this lifecycle explicitly
- do not treat the code as if it only ever runs from one widget click path

## 1C. Bundle Design Decision Matrix

Before writing code, classify the product surface and state model.

| Product need | Primary surface | Typical runtime path | Typical state/storage | Notes |
| --- | --- | --- | --- | --- |
| Copilot/chat experience | `@agentic_workflow` / `@on_message` | request-bound chat path | conversation stores, retrieval systems, bundle props | start here for assistant-style products |
| Admin console | `@ui_widget` + `@api(route="operations")` | widget -> operations | descriptor-backed config, bundle local storage, DB/Redis | keep admin separate from public/user surface |
| External webhook/integration | `@api(route="public")` | public HTTP path | bundle props + secrets, external systems | auth boundary must be explicit |
| Tool-serving integration | `@mcp(...)` | MCP dispatch path | bundle props + secrets, external systems | bundle owns MCP auth |
| Background automation | `@cron(...)` plus helper service | cron/system path | bundle local storage, DB/Redis, external APIs | do not assume request-bound actor/session |
| Mixed product app | combine widget/API/chat/cron intentionally | multiple runtime paths | split state by storage tier | this is common; design boundaries explicitly |

State-placement rule:

- bundle props/secrets:
  deployment-scoped configuration
- bundle local storage:
  instance-local mutable files/workspaces/caches
- `AIBundleStorage`:
  persisted bundle artifacts
- DB/Redis/external APIs:
  runtime or business state

## 2. Decide What Kind Of Bundle You Are Building

Before writing code, classify the bundle.

Typical bundle surfaces:

- chat-first workflow bundle
- operations/API bundle
- widget bundle
- iframe app bundle
- MCP-serving bundle
- scheduled-job bundle
- mixed bundle with several surfaces

You should explicitly decide:

- what the primary user-facing surface is
- which methods are read-only
- which methods mutate state
- whether there is a separate admin surface
- what state must persist locally on the instance
- what state must be descriptor-backed

Do not collapse all concerns into one public widget.

Preferred split:

- end-user-facing widget or operations surface
- separate admin widget/API for privileged operations
- scheduled jobs for background automation

## 2.1 Process Environment Boundary

Multiple applications may run inside the same processor process.

That means:

- inherited processor environment variables are shared by design
- bundle code must not treat `os.environ` as private mutable state

For git-backed helpers in particular:

- read git configuration through `get_settings()` / `get_secret()`
- build a subprocess env dict for git commands
- pass that env only to the git subprocess
- do not write `GIT_HTTP_TOKEN`, `GIT_SSH_COMMAND`, or similar values back into the processor process env

Correct pattern:

```python
env = build_git_env(
    git_http_token=get_secret("services.git.http_token"),
    git_http_user=get_secret("services.git.http_user"),
)
subprocess.run(["git", "fetch", "--prune", "origin"], env=env, check=True)
```

Interpretation:

- inherited process env remains shared
- descriptor-backed settings/secrets remain the normal source of truth
- explicit overrides remain local to the subprocess call only

Transport rule:

- git-backed workspace or storage repos may be configured with either HTTPS or SSH remotes
- if HTTPS token auth is configured, the shared helper prefers that path and may normalize an
  SSH-style remote such as `git@github.com:org/repo.git` to `https://github.com/org/repo.git`
- if SSH transport is intended, configure the SSH settings explicitly:
  - `GIT_SSH_KEY_PATH`
  - `GIT_SSH_KNOWN_HOSTS`
  - `GIT_SSH_STRICT_HOST_KEY_CHECKING`
- do not half-configure both modes and assume git will choose the intended one silently

Operationally:

- HTTPS + PAT is usually the simpler deployment choice
- SSH is supported, but it requires key and host-verification material to be mounted and configured

## 3. Start From The Minimal Bundle Shape

Recommended layout:

```text
my_bundle/
  entrypoint.py
  orchestrator/
    workflow.py
  tools_descriptor.py
  skills_descriptor.py
  tools/
  skills/
  ui/                 # optional TSX widget templates
  ui-src/             # optional full iframe SPA
  tests/
```

Required in practice:

- `entrypoint.py`
- bundle registration decorators
- compiled graph or equivalent execution path

Usually present:

- `orchestrator/workflow.py`
- `tools_descriptor.py`
- `skills_descriptor.py`

## 4. Copy The Right Reference Pattern

Use `versatile` as the default reference bundle.

Study in this order:

1. `entrypoint.py`
2. `orchestrator/workflow.py`
3. `tools_descriptor.py`
4. `skills_descriptor.py`
5. `ui/PreferencesBrowser.tsx`
6. `ui-src/src/App.tsx`
7. `tests/`

What `versatile` is good for:

- entrypoint and graph bootstrap
- workflow orchestration
- bundle-local tools and skills
- widget and operations integration
- public endpoint example
- iframe app example
- bundle storage usage

What `versatile` is not the reference for:

- `@cron`
- `@venv`

Use dedicated docs for those:

- [bundle-scheduled-jobs-README.md](../bundle-scheduled-jobs-README.md)
- [bundle-venv-README.md](../bundle-venv-README.md)

## 4.1 Copyable Feature Snippets

Use these as the smallest correct starting points.

### Authenticated API

```python
@api(alias="task_list", route="operations", method="GET", user_types=("registered",))
async def task_list(self, **kwargs):
    return {"items": []}
```

Reference:
- [bundle-platform-integration-README.md](../bundle-platform-integration-README.md)

### Public API with explicit platform auth

```python
@api(
    alias="incoming_webhook",
    route="public",
    method="POST",
    public_auth={"mode": "header_secret", "header": "X-Webhook-Secret", "secret_key": "incoming.secret"},
)
async def incoming_webhook(self, **kwargs):
    return {"ok": True}
```

Reference:
- [bundle-platform-integration-README.md](../bundle-platform-integration-README.md)

### Public API with bundle-owned auth

```python
from fastapi import HTTPException, Request
from kdcube_ai_app.apps.chat.sdk.config import get_secret

@api(alias="telegram_webhook", route="public", method="POST", public_auth="bundle")
async def telegram_webhook(self, request: Request, **kwargs):
    header_name = self.bundle_prop("telegram.webhook.auth.header_name", "X-Telegram-Bot-Api-Secret-Token")
    expected_token = get_secret("b:telegram.webhook.auth.shared_token")
    if request.headers.get(header_name) != expected_token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {"ok": True}
```

Reference:
- [bundle-platform-integration-README.md](../bundle-platform-integration-README.md)
- [bundle-transports-README.md](../bundle-transports-README.md)
- [bundle-props-secrets-README.md](../bundle-props-secrets-README.md)
- [bundle-dev-README.md](../bundle-dev-README.md)

### Widget plus structured API

```python
@api(alias="task-board", route="operations", method="POST", user_types=("registered",))
@ui_widget(alias="task-board", icon={"tailwind": "heroicons-outline:check-badge"}, user_types=("registered",))
def task_board(self, **kwargs):
    return ["<div id='root'></div>"]

@api(alias="task-board-api", route="operations", method="POST", user_types=("registered",))
async def task_board_api(self, **kwargs):
    return {"items": []}
```

Reference:
- [bundle-widget-integration-README.md](../bundle-widget-integration-README.md)

### Public MCP

```python
@mcp(alias="docs_public", route="public", transport="streamable-http")
def docs_public_mcp(self, **kwargs):
    return build_docs_mcp_app()
```

Reference:
- [bundle-platform-integration-README.md](../bundle-platform-integration-README.md)
- [bundle-transports-README.md](../bundle-transports-README.md)

### Bundle-authenticated MCP

```python
from fastapi import HTTPException, Request
from kdcube_ai_app.apps.chat.sdk.config import get_secret

@mcp(alias="docs", route="operations", transport="streamable-http")
def docs_mcp(self, request: Request, **kwargs):
    header_name = self.bundle_prop("mcp.docs.auth.header_name", "X-Docs-MCP-Token")
    expected_token = get_secret("b:mcp.docs.auth.shared_token")
    if request.headers.get(header_name) != expected_token:
        raise HTTPException(status_code=401, detail=f"Missing or invalid {header_name}")
    return build_docs_mcp_app()
```

Reference:
- [bundle-transports-README.md](../bundle-transports-README.md)
- [bundle-props-secrets-README.md](../bundle-props-secrets-README.md)

### Scheduled job

```python
@cron(alias="sync", expr_config="task_tracker.sync", span="system")
async def sync(self, **kwargs):
    await self._sync_tasks()
```

Reference:
- [bundle-scheduled-jobs-README.md](../bundle-scheduled-jobs-README.md)

### Platform-gated surface with `enabled_config`

```python
@ui_widget(
    alias="task-board",
    icon={"tailwind": "heroicons-outline:check-badge"},
    user_types=("registered",),
    enabled_config="features.task_board.widget_enabled",
)
def task_board(self, **kwargs):
    return ["<div id='root'></div>"]
```

```yaml
bundles:
  items:
    - id: "task.board@1-0"
      config:
        features:
          task_board:
            widget_enabled: true
```

Use this when the platform should hide or suppress the surface directly instead
of the bundle method deciding at runtime.

### Bundle props and secrets

```python
enabled = self.bundle_prop("features.auto_sync", False)
api_key = get_secret("b:external.api_key")
```

Reference:
- [bundle-props-secrets-README.md](../bundle-props-secrets-README.md)

### Bundle local storage

```python
root = self.bundle_storage_root()
workspace = root / "_task_tracker"
workspace.mkdir(parents=True, exist_ok=True)
```

Reference:
- [bundle-storage-cache-README.md](../bundle-storage-cache-README.md)

### Per-bundle virtualenv helper

```python
@venv(requirements="requirements.txt")
def render_report(payload: dict) -> dict:
    return {"ok": True, "payload": payload}
```

Reference:
- [bundle-venv-README.md](../bundle-venv-README.md)

## 4.2 Feature Gating With `enabled_config`

This feature is important enough to treat as a first-class authoring tool.

`enabled_config` is the platform-native feature flag for bundle surfaces.

You can attach it to:

- `@agentic_workflow(...)`
- `@api(...)`
- `@mcp(...)`
- `@ui_widget(...)`
- `@cron(...)`

What it does:

- resolves a dot-path against effective bundle props
- if the resolved value is disabled, the platform suppresses that surface
- missing path means enabled
- bundle-level disable overrides resource-level settings

Current disabled values:

- boolean `False`
- integer `0`
- strings `false`, `disable`, `disabled`, `off`, `0`

Use it for:

- staged rollout
- environment-specific exposure
- disabling one job/widget/API without deleting code
- temporarily hiding unfinished surfaces

Do not use it for:

- secrets
- per-user authorization
- complex business predicates that depend on request payload or database state

Authoring rule:

- put the flag value in bundle props under `bundles.yaml -> config:`
- keep the decorator path stable
- let the platform do the 404/scheduler suppression instead of duplicating the check in method bodies

## 5. Entrypoint Rules

Every bundle should make the entrypoint simple and explicit.

Core requirements:

- register the bundle with `@agentic_workflow(...)`
- declare bundle identity with `@bundle_id(...)` when code-level identity matters
- compile the graph once in `__init__`
- keep route methods thin
- move real business logic into helper/service/orchestrator modules

Minimal pattern:

```python
from langgraph.graph import END, START, StateGraph

from kdcube_ai_app.infra.plugin.agentic_loader import agentic_workflow, bundle_id
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint
from kdcube_ai_app.infra.service_hub.inventory import BundleState


@agentic_workflow(name="my.bundle", version="1.0.0")
@bundle_id("my.bundle@1.0.0")
class MyEntrypoint(BaseEntrypoint):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.graph = self._build_graph()

    def _build_graph(self):
        g = StateGraph(BundleState)
        g.add_node("orchestrate", self._orchestrate)
        g.add_edge(START, "orchestrate")
        g.add_edge("orchestrate", END)
        return g.compile()
```

Entrypoint responsibilities:

- runtime wiring
- surface declaration through decorators
- lightweight access control
- creation of helper/service objects
- passing runtime context into those helpers

Entrypoint should not contain:

- large HTML blobs unless unavoidable
- business logic mixed with route handling
- direct deployment/env assumptions
- ad hoc local path construction next to the source tree

## 6. Runtime Context Rules

This must be explicit in the builder’s mental model.

Different bundle execution paths expose different runtime surfaces.
Do not write code as if every path looked like a request-bound widget call.

### Chat turn / SSE / socket-driven request

This is the normal processor-driven request path.

In this path, entrypoint code has request-bound runtime context:

- `self.comm`
- `self.comm_context`
- actor/session/routing details
- `self.bundle_props`
- `self.pg_pool`
- `self.redis`
- storage helpers
- `get_secret(...)` / `get_user_secret(...)`

This is the path where communicator behavior is request-bound and peer/session-aware.

### REST bundle operation path

Bundle operations called through `/api/integrations/bundles/.../operations/...` also run with request-bound runtime context.

In this path, entrypoint code also has:

- `self.comm`
- `self.comm_context`
- `self.bundle_props`
- DB/Redis handles when available

So the practical rule is:

- chat/SSE path: request-bound comm context exists
- REST operations path: request-bound comm context also exists

If a widget or iframe calls a bundle operation, do not treat it as a detached background job.

### Cron / scheduled-job path

Cron is different.

When code runs from `@cron(...)`, there may be no meaningful end-user actor/session/socket context.

Do not assume in cron code that you have:

- a real user actor
- a request routing session
- a socket target
- request-bound streaming semantics

Cron-safe assumptions:

- bundle props are available
- storage helpers are available
- DB/Redis handles are available when configured
- request/actor/communicator details may be absent or not user-scoped

Practical rule:

- do not build cron logic around `self.comm_context.actor`
- do not depend on request headers or peer state
- pass explicit tenant/project/bundle scope into subsystem helpers when needed

### Tool execution in normal in-process runtime

Tool modules do not get the same surface as the bundle entrypoint.

They should use the documented tool bindings such as:

- `_SERVICE` / `SERVICE`
- `_INTEGRATIONS` / `INTEGRATIONS`
- `_TOOL_SUBSYSTEM`
- `_COMMUNICATOR`
- `_KV_CACHE`
- `_CTX_CLIENT`

Do not assume a tool module has:

- `self.comm`
- `self.comm_context`
- arbitrary entrypoint internals

### Tool execution in isolated runtime

Isolated runtime is narrower again.

It does not inherit arbitrary live Python objects from the host process.
It receives a reconstructed portable runtime contract.

That means:

- do not rely on random globals from the host process
- do not rely on live in-memory objects created in the parent process
- use only the documented portable surfaces

If code may run in isolated execution, write it as if only the documented bindings are available.

### Writing Rule

Before writing a method or helper, explicitly decide which runtime path it belongs to:

- request-bound entrypoint logic
- REST operation logic
- cron/system logic
- in-proc tool logic
- isolated tool logic

If code crosses those boundaries, make the dependency explicit instead of assuming one path behaves like another.

## 7. Singleton And Exclusivity Rules

These are related, but they are not the same thing.

### Bundle singleton

A bundle can be configured as `singleton`.

Meaning:

- the workflow instance is cached and reused inside the proc process
- subsequent requests reuse that same entrypoint instance instead of creating a fresh one each time

What singleton is good for:

- expensive bundle initialization you want to keep warm inside the process
- long-lived in-memory helpers that are safe to reuse
- reducing repeated setup cost

What singleton does **not** mean:

- it does not make bundle operations exclusive
- it does not serialize concurrent requests
- it does not give cross-process or cross-instance exclusivity
- it does not replace locks

Important runtime consequence:

- request-bound context is rebound on reuse
- singleton bundles must not treat request state as permanently stored on `self`

Practical rule:

- if the bundle is singleton, assume `self` is process-lifetime state
- request-specific data must come from the current request context, method arguments, or task-local/context-local surfaces

### Exclusive operations

If you need “only one run at a time”, do not rely on `singleton`.

Use an explicit exclusivity mechanism.

#### For cron

Use `@cron(span=...)`.

This is the supported exclusivity control:

- `span="process"`
  - one run per proc process
- `span="instance"`
  - one run per host instance
- `span="system"`
  - one run across the whole deployed system for that tenant/project/bundle/job

For recurring background jobs, `span` is the first control to choose.

Default recommendation:

- use `span="system"` unless you explicitly want per-process or per-instance behavior

#### For non-cron operations

Use an explicit lock in the operation or subsystem logic.

Typical choices:

- Redis lock keyed by tenant/project/bundle/operation
- DB advisory lock or equivalent DB-scoped lock when appropriate
- local fallback lock only for standalone/local debugging

Practical rule:

- singleton controls instance reuse
- lock controls exclusivity

Do not confuse them.

## 8. Identity Rules

This is one of the easiest places to break bundles.

Runtime identity is descriptor-driven.
The source folder name is not authoritative when descriptors already define the bundle.

Authoritative identity sources, in order of trust:

1. loaded descriptor / `ai_bundle_spec.id`
2. explicit runtime bundle id passed into context
3. code fallback such as `@bundle_id(...)`
4. source folder name only as a last-resort local fallback

Do not build these from the source folder name when runtime already has descriptor context:

- storage roots
- workspace branches
- conversation IDs
- widget operation URLs
- admin operation URLs

If you ignore this, you will get split state:

- one local root for the source folder name
- another root for the runtime bundle id
- diverging branches, sessions, or archive trees

## 9. Configuration Rules

### Use the correct surface

For non-secret deployment config:

- `self.bundle_prop(...)`

For bundle-scoped secrets:

- `get_secret("b:...")`

For platform/global secrets:

- `get_secret("...")` or `get_secret("a:...")`

For descriptor-file reads only when absolutely necessary:

- `get_plain(...)`

For platform settings:

- `get_settings()`

### Do not read deployment-owned config with raw `os.getenv(...)`

Bundle logic should not depend on raw env variable names for operational config when the platform already provides:

- `bundle_prop(...)`
- `get_settings()`
- `get_secret(...)`
- `get_plain(...)`

Treat this as prohibited in normal bundle code:

- do not call `os.getenv(...)` or read `os.environ[...]` for deployment-owned
  config or secrets
- do not invent bundle-local env variable names as a second config contract

Exception:

- direct env access is acceptable only in code that explicitly lives at the
  iso-runtime or sandbox boundary and is intentionally driven by process env

If you add a standalone helper script for local debugging:

- load `.env` into the platform settings path
- then read through `get_settings()` / `get_secret()`
- do not let the runtime bundle depend on bundle-local `.env` files

### Do not call the secrets provider directly

Bundle or feature code must not call secrets-provider internals such as
`get_secrets_manager(...).get_secret(...)` directly.

Use:

- `get_secret(...)`
- `get_settings()` for promoted secret-backed settings

Reason:

- direct provider calls bypass canonical key handling, env-first behavior, and
  mode-specific resolution
- they couple bundle code to one provider implementation instead of the
  supported helper contract

### Do not open descriptor YAML files through hardcoded paths

Bundle code must not open `assembly.yaml`, `bundles.yaml`, or other descriptor
YAML files through hardcoded filesystem paths.

Use:

- `get_plain(...)` for raw descriptor inspection
- `bundle_prop(...)` for effective bundle config
- `get_settings()` for effective typed platform/runtime settings

Reason:

- direct file opens hardcode one runtime path layout
- they bypass descriptor path indirection and alternate runtime wiring
- they are easier to break in direct local runs, tests, and non-default mounts

### Descriptor-backed values are the durable source of truth

If a setting must survive reload and deployment refresh, it belongs in descriptors and bundle props.

Typical examples:

- cron expression
- default window sizes
- feature toggles
- workspace repo/branch overrides
- validation toggles

## 10. Local Storage Rules

If the bundle needs mutable filesystem state on the proc instance, use the platform helper.

Do not:

- write mutable runtime data into the bundle source tree
- create a repo-relative `.runtime/` folder for operational data
- assume current working directory is stable or durable

Use:

- `self.bundle_storage_root()`
- or `bundle_storage_dir(...) / "_subsystem"` only when you are outside entrypoint code and do not have `self.bundle_storage_root()`

Use local bundle storage for:

- cloned repos
- local archive mirrors
- prepared indexes
- cron workspaces
- temporary generated files that belong to this instance

This is separate from `AIBundleStorage`.

Mental model:

- local bundle storage = instance-visible filesystem
- `AIBundleStorage` = backend storage API for bundle artifacts

## 11. Widget Design Rules

Widget bundles fail most often because authors treat them like isolated frontends.
They are not.

KDCube widgets run inside a platform iframe shell.

### Required contract

The widget must:

- request runtime config from the parent frame
- accept both `CONN_RESPONSE` and `CONFIG_RESPONSE`
- use host-provided auth tokens
- build operation URLs from runtime config

Required config fields:

- `baseUrl`
- `accessToken`
- `idToken`
- `idTokenHeader`
- `defaultTenant`
- `defaultProject`
- `defaultAppBundleId`

Do not hardcode:

- tenant
- project
- bundle id
- localhost URLs
- source-folder names in operation URLs

### Separate display and structured API

Recommended pattern:

- widget method:
  - `@ui_widget(alias="task-board", ...)`
- compatibility operation on the same method if needed:
  - `@api(alias="task-board", route="operations", ...)`
- separate structured backend API:
  - `@api(alias="task-tracker-api", route="operations", method="POST", ...)`

The widget should call the structured API alias, not the widget alias.

### Public and admin surfaces should be separate

Good pattern:

- `task-board`
  - end-user-facing
  - read-only on initial load
- `task-tracker-admin`
  - privileged/admin-only
  - mutating operations

Do not put destructive or administrative actions into the normal public widget unless that is explicitly the product.

### Read-only load by default

Initial widget load should not mutate external state.

Prefer:

- initial load: read-only bootstrap
- explicit button such as `Refresh`, `Sync`, `Run now`, `Save settings` for mutations

This avoids accidental pushes or expensive jobs on every widget open.

### Operation body shape

For widgets, preferred POST body shape is:

```json
{ "data": { "operation": "bootstrap", "payload": { ... } } }
```

The integrations layer also accepts raw JSON objects, but widget code should use the platform wrapper consistently.

Also remember:

- integrations responses are enveloped
- widgets should unwrap the `[alias]` field in the response body

## 12. Access Control Rules

Use `user_types` and `roles` correctly.

Current `user_types` order:

- `anonymous < registered < paid < privileged`

This is threshold-based, not exact-match.

Examples:

- `user_types=("registered",)` means registered-or-higher
- `user_types=("paid",)` means paid-or-higher
- `user_types=("privileged",)` means privileged only

Use `roles=(...)` for raw external roles such as:

- `kdcube:role:super-admin`

If both `user_types` and `roles` are present:

- both must pass

For admin widgets and APIs, use the platform’s privileged pattern.

In entrypoints derived from `BaseEntrypoint`, prefer:

- `_ensure_privileged(...)`

This keeps the access check consistent with the rest of the platform.

## 13. Scheduled Jobs And Background Pipelines

If the bundle runs background work through `@cron(...)`, treat it as an operational subsystem.

Rules:

- lock the job so concurrent instances do not corrupt shared work
- use Redis lock with TTL when runtime Redis is available
- keep local fallback lock only for standalone/local use
- use bundle local storage for the working root
- keep schedule and first-run/default-window settings in bundle props
- use `@cron(span=...)` as the primary exclusivity control for scheduled jobs

For automation that may still need operator control:

- keep cron for regular background runs
- expose a privileged admin API/widget for:
  - changing schedule
  - changing default window
  - running now
  - deleting bad outputs
  - rebuilding indexes or archives

## 14. Standalone Scripts Inside Bundles

Sometimes a bundle subsystem benefits from a local standalone runner for debugging.
That is acceptable, but only under these rules:

- standalone mode is for local development/debugging
- operational runtime must still work entirely through KDCube wiring
- standalone env must be loaded into `get_settings()` / `get_secret()`
- operational config must still come from descriptors/bundle props in real runtime

Do not let a successful standalone path hide a broken runtime path.

When a subsystem has both:

- standalone mode
- in-bundle runtime mode

you must test both.

## 15. Pitfalls That Recur In Real Bundle Work

### Pitfall: using the source folder name as runtime bundle id

Symptom:

- storage root, workspace branch, or session path differs between runtime and standalone

Fix:

- resolve bundle id from descriptor/runtime context first

### Pitfall: repo-relative mutable runtime folders

Symptom:

- state ends up under the bundle source tree
- reloads and operational data get mixed together

Fix:

- use bundle local storage helper

### Pitfall: widget only listens for `CONFIG_RESPONSE`

Symptom:

- widget gets stuck waiting for config in some host paths

Fix:

- accept both `CONN_RESPONSE` and `CONFIG_RESPONSE`

### Pitfall: widget builds `////operations/...` URLs

Symptom:

- missing tenant/project/bundleId in generated request path

Fix:

- treat config handshake as mandatory
- refuse to call operation endpoints when config is incomplete

### Pitfall: widget initial load mutates remote state

Symptom:

- opening the widget triggers syncs, commits, or background work

Fix:

- initial load read-only
- explicit buttons for mutating actions

### Pitfall: Python f-string HTML/JS/CSS builders with unescaped braces

Symptom:

- runtime `NameError` from CSS like `@page{...}` or JS template placeholders `${...}`

Fix:

- inside Python f-strings, escape literal braces as `{{` and `}}`
- test HTML-builder functions directly, not only by syntax compile

### Pitfall: runtime config read via `os.getenv`

Symptom:

- bundle works only under one local shell shape
- runtime descriptors and props are ignored

Fix:

- use `bundle_prop(...)`, `get_settings()`, `get_secret(...)`, `get_plain(...)`

### Pitfall: direct descriptor file reads through hardcoded paths

Symptom:

- bundle works only when descriptors happen to be mounted at one expected path
- direct local runs or alternative runtime layouts break

Fix:

- use `get_plain(...)` for raw descriptor inspection
- use `bundle_prop(...)` or `get_settings()` for effective runtime values

### Pitfall: direct secrets-provider calls from bundle code

Symptom:

- bundle is coupled to one secrets backend
- alias handling, env-first behavior, or provider substitution is bypassed

Fix:

- use `get_secret(...)`
- use `get_settings()` for promoted secret-backed settings

### Pitfall: writing cron logic as if it were a request-bound widget/API call

Symptom:

- code expects actor/session/socket details during scheduled execution
- cron path breaks or behaves inconsistently

Fix:

- treat cron as system/background execution
- pass explicit scope into helpers
- do not assume request-bound `comm_context` details exist

### Pitfall: writing isolated-exec code against host-process globals

Symptom:

- helper works in one local path but fails in isolated execution

Fix:

- use only documented tool/runtime bindings
- assume isolated runtime reconstructs a narrow portable surface

### Pitfall: assuming `singleton` makes an operation exclusive

Symptom:

- concurrent requests or jobs still overlap
- state corruption happens despite singleton bundle configuration

Fix:

- use `singleton` only for instance reuse
- use `@cron(span=...)` or an explicit lock for exclusivity

### Pitfall: public widget and admin controls mixed together

Symptom:

- access model becomes unclear
- widget load surface becomes dangerous

Fix:

- keep a separate admin widget/API

## 16. Writing Checklist

Before considering the bundle “implemented”, verify:

- entrypoint decorators are correct
- runtime identity does not depend on folder name
- all mutable local state uses bundle storage helper
- all deployment config uses bundle props/settings/secrets instead of raw env
- widgets follow the iframe config handshake
- widget URLs are built from runtime config
- public load paths are read-only by default
- admin surfaces are separated and privileged
- singleton is used only when process-level instance reuse is actually wanted
- scheduled/background work is locked
- exclusivity is implemented with `span` or explicit locks, not by singleton
- cron/background logic does not assume request-bound comm context
- isolated-exec code does not assume host-process globals
- destructive operations are explicit
- bundle-local tests exist for bundle-specific logic
- shared bundle suite passes

## 17. Minimum Deliverable Standard

A bundle implementation is not complete when it only “works once”.

It is complete when:

- it follows the documented platform contract
- it survives reloads
- runtime identity is stable
- widget/API surfaces are discoverable
- state is stored in the correct tier
- local and runtime execution both work
- the shared test suite and bundle-local tests pass

If you are unsure, default to the simpler, more explicit design:

- thin entrypoint
- service/helper module
- separate admin surface
- descriptor-backed settings
- bundle local storage for mutable filesystem state
