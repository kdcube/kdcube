---
id: ks:docs/sdk/bundle/bundle-platform-integration-README.md
title: "Bundle Platform Integration"
summary: "Declarative platform contract for exposing bundle capabilities through decorators, manifest metadata, REST operations, widgets, MCP routes, static UI, public routes, Data Bus handlers, scheduled jobs, and background job handlers."
tags: ["sdk", "bundle", "integration", "decorators", "widgets", "operations", "mcp", "ui", "manifest", "cron", "scheduled-jobs", "background-jobs", "data-bus"]
keywords: ["decorator based integration", "bundle manifest contract", "rest operations exposure", "widget exposure", "mcp route exposure", "static ui exposure", "public route exposure", "data bus handler", "scheduled job exposure", "on_job background job handler"]
updated_at: 2026-05-24
see_also:
  - ks:docs/sdk/bundle/bundle-agent-integration-README.md
  - ks:docs/sdk/bundle/bundle-entrypoint-classes-README.md
  - ks:docs/sdk/bundle/bundle-properties-and-secrets-lifecycle-README.md
  - ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - ks:docs/sdk/bundle/bundle-transports-README.md
  - ks:docs/sdk/bundle/auth-bundle-federated-README.md
  - ks:docs/sdk/bundle/bundle-client-communication-README.md
  - ks:docs/sdk/bundle/bundle-interfaces-README.md
  - ks:docs/sdk/bundle/bundle-scheduled-jobs-README.md
  - ks:docs/sdk/bundle/bundle-event-recording-and-sinks-README.md
  - ks:docs/sdk/bundle/bundle-developer-guide-README.md
  - ks:docs/sdk/bundle/bundle-venv-README.md
  - ks:docs/service/streams/background-jobs-README.md
  - ks:docs/service/comm/data-bus-README.md
  - ks:docs/sdk/bundle/bundle-index-README.md
  - ks:docs/sdk/bundle/versatile-reference-bundle-README.md
---
# Bundle Platform Integration

This document describes the bundle integration contract that is implemented now.
It covers:

- class and method decorators supported by the bundle loader
- bundle interface manifest discovery
- REST routing for bundle operations, public operations, and bundle MCP endpoints
- widget discovery and widget fetch
- bundle main UI entrypoints and static asset serving
- Data Bus handler discovery for durable non-conversation domain messages
- background job stream dispatch through `@on_job`

For how these decorators fit into React agents, tools/skills descriptors, MCP
connectors, and Claude Code subagents, read
[Bundle Agent Integration](bundle-agent-integration-README.md).

Skills are not declared by decorators. React skills are discovered through the
skills subsystem: core SDK skills, SDK solution skills, and the bundle
`CUSTOM_SKILLS_ROOT`, then filtered by `skills_descriptor.py` `AGENTS_CONFIG`.
Skills can also mark tool refs in `tools.yaml` with `required: true`; ReAct
then omits that skill from catalog/import/read paths whenever the active tool
catalog lacks those tool ids. Use `AGENTS_CONFIG` for explicit allow-lists or
hard denies that are stricter than tool availability. Use
`agent_disclosure: hidden` in `SKILL.md` only to suppress catalog/self-description
disclosure for guidance that remains loadable by exact id or import. See
[Bundle Agent Integration](bundle-agent-integration-README.md#react-bundle-agent-integration).

For the higher-level inbound/outbound transport map, use
[bundle-transports-README.md](bundle-transports-README.md).

For the CLI lifecycle that makes these integration changes visible in a local
runtime, use
[how-to-configure-and-run-bundle-README.md#canonical-cli-flow-schemas](build/how-to-configure-and-run-bundle-README.md#canonical-cli-flow-schemas).
In short: `init` creates the runtime, `refresh` changes platform source/images,
rebuilds when requested, and restarts the stack unless `--no-restart` is used;
`bundle config apply` reapplies seed bundle descriptors, and
`bundle reload` clears bundle caches for code/config changes.

All of this is implemented in:

- `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/bundle_loader.py`
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/proc/rest/integrations/integrations.py`

Critical import rule:

- bundle-local Python imports must be package-relative, for example
  `from .services.news import build_news_service`
- do not import bundle-local folders as process-global top-level packages such
  as `services`, `apps`, `tools`, or `resources`
- see [Bundle Runtime](bundle-runtime-README.md#critical-bundle-local-import-rule)
  for the import-isolation contract

## Bundle-to-client event scopes

Bundle code can use the request-bound or entrypoint-bound communicator to emit
events to connected clients. There are three distinct scopes:

| Primitive | Scope | Where to use it |
| --- | --- | --- |
| `comm.service_event(..., broadcast=False)` | the current peer when `KDC-Stream-ID` was supplied; otherwise the current session | direct progress/reply from an `@api`, widget operation, or other request-bound method |
| `comm.service_event(..., broadcast=True)` | all connected peers in the current authenticated session | session-wide state changes for the current user |
| `comm.project_event(...)` | all SSE clients in the same tenant/project that subscribed with `project_events=true` | compact tenant/project snapshots, such as operational dashboards |

Project events are SSE-only in the current contract. They are for small,
bounded, already-safe payloads. Do not publish raw telemetry events, prompts,
answers, logs, or unbounded result lists through this path. The concrete client
and bundle recipe lives in
[Bundle Client Communication](bundle-client-communication-README.md#tenantproject-sse-broadcast).

## 1) Supported decorators

Bundles currently support these decorators:

| Decorator | Scope | What it means |
| --- | --- | --- |
| `@bundle_entrypoint(...)` | entrypoint class | Declares the bundle entrypoint class used by the runtime. |
| `@bundle_entrypoint_factory(...)` | factory function | Declares an entrypoint factory function instead of an entrypoint class. |
| `@agentic_workflow(...)` | entrypoint class | Legacy compatibility alias for `@bundle_entrypoint(...)`. |
| `@agentic_workflow_factory(...)` | factory function | Legacy compatibility alias for `@bundle_entrypoint_factory(...)`. |
| `@bundle_id(...)` | entrypoint class | Declares the code-level bundle id used when runtime needs to infer identity from the bundle code itself. |
| `@api(...)` | entrypoint method | Declares a remotely callable bundle HTTP operation. |
| `@mcp(...)` | entrypoint method | Declares a remotely callable bundle MCP endpoint. |
| `@ui_widget(...)` | entrypoint method | Declares a widget in the bundle interface manifest. |
| `@ui_main` | entrypoint method | Declares the bundle main UI entrypoint. |
| `@on_message` | entrypoint method | Declares the bundle message handler metadata. |
| `@data_bus_handler(...)` | entrypoint method | Declares a durable Data Bus subject handler managed by proc. |
| `@cron(...)` | entrypoint method | Declares a scheduled background job managed by proc. |
| `@on_job` | entrypoint method | Declares the bundle handler for ready background jobs claimed by proc. |
| `@venv(...)` | helper function or method | Declares that a callable executes in a cached per-bundle subprocess venv. |

Important distinction:

- `@bundle_entrypoint(...)`, `@bundle_entrypoint_factory(...)`, `@bundle_id(...)`,
  `@api(...)`, `@mcp(...)`, `@ui_widget(...)`, `@ui_main`, `@on_message`,
  `@data_bus_handler(...)`, `@cron(...)`, and `@on_job`
  participate in bundle manifest and runtime interface discovery
- `@venv(...)` is an execution decorator, not an HTTP/UI manifest decorator
- most bundles should use `@bundle_entrypoint(...)`; `@bundle_entrypoint_factory(...)`
  is the exception for custom construction cases
- `@agentic_workflow(...)` remains supported for existing bundles, but new
  bundle code should use `@bundle_entrypoint(...)` so non-chat bundles are not
  mislabeled as workflows

These decorators are runtime metadata. They are not deployment config.

### 1.1 `@bundle_entrypoint_factory(...)`

Declares an entrypoint factory function rather than an entrypoint class.

```python
from kdcube_ai_app.infra.plugin.bundle_loader import bundle_entrypoint_factory

@bundle_entrypoint_factory(name="My Bundle", version="1.0.0")
def build_bundle(config, **kwargs):
    ...
```

Use this only when the bundle must construct its runtime through a factory
function. Most bundles should use `@bundle_entrypoint(...)` on a class.

Side-by-side:

```python
from kdcube_ai_app.infra.plugin.bundle_loader import (
    bundle_entrypoint,
    bundle_entrypoint_factory,
    bundle_id,
)

@bundle_entrypoint(name="My Bundle", version="1.0.0")
@bundle_id("my.bundle@1.0.0")
class MyBundleEntrypoint:
    ...

@bundle_entrypoint_factory(name="My Bundle", version="1.0.0")
def build_entrypoint(config, **kwargs):
    return MyBundleEntrypoint(config=config, **kwargs)
```

In practice:

- class form means the runtime instantiates the entrypoint class directly
- factory form means the runtime calls your function and uses the returned
  entrypoint instance
- prefer the class form unless you specifically need dynamic selection,
  wrapping, or legacy construction adaptation

### 1.2 `@bundle_id(...)`

Declares the canonical bundle ID on the entrypoint class.

```python
from kdcube_ai_app.infra.plugin.bundle_loader import bundle_entrypoint, bundle_id

@bundle_entrypoint(name="My Bundle", version="1.0.0")
@bundle_id("my.bundle@1.0.0")
class MyBundle:
    ...
```

Use it when the code should declare its own stable bundle identity.

### 1.3 `@bundle_entrypoint(...)` — bundle-level `allowed_roles`

The `@bundle_entrypoint` decorator accepts optional `allowed_roles` and
`allowed_roles_config` parameters that restrict which users can see the bundle
in the bundle listing.

```python
@bundle_entrypoint(
    name="Finance Copilot",
    version="1.0.0",
    allowed_roles=("kdcube:role:finance-team", "kdcube:role:super-admin"),
    allowed_roles_config="visibility.bundle.allowed_roles",
)
@bundle_id("finance.copilot@1.0.0")
class FinanceCopilot:
    ...
```

Current fields relevant to access control:

- `allowed_roles`
  - tuple/list of non-derived (externally defined) role names
  - must use `kdcube:role:<name>` format — Cognito group IDs
  - do **not** use derived platform types here (`"registered"`, `"privileged"`)
  - empty or omitted means the bundle is visible to all authenticated users
  - OR semantics: user passes if at least one of their raw roles matches
- `allowed_roles_config`
  - optional dot-path into bundle props, e.g. `"visibility.bundle.allowed_roles"`
  - when set and the path resolves to a list of strings, the resolved value
    overrides the decorator default at listing time (per-request lookup)
  - empty list `[]` is a valid intentional override meaning "visible to all"
  - invalid types or a missing path fall back silently to the decorator default
  - when declared, the field becomes editable in the Bundle Admin dashboard
- bundle-level feature gate: `enabled.bundle` in bundle props
  - boolean (or string equivalent) that controls the whole bundle
  - if the resolved value is falsy, the bundle is treated as disabled:
    all its HTTP routes return 404 and all its scheduled jobs are skipped,
    regardless of any resource-level `enabled.*` values
  - absent key means always enabled (opt-in disabling, not opt-in enabling)
  - enforced in `integrations.py` for HTTP resources (widgets, operations, MCP endpoints)
    and in `bundle_scheduler.py` for scheduled jobs, before any per-resource check

Current behavior:

- `GET /api/integrations/bundles` (non-admin) filters out bundles whose
  effective `allowed_roles` do not intersect with the calling user's raw roles
  (entries in the session that start with `kdcube:role:`).
  Bundle props are loaded before the check so `allowed_roles_config` overrides
  are applied at listing time.
- `GET /api/admin/integrations/bundles` is not filtered — admin always
  sees all bundles regardless of `allowed_roles`
- A bundle with no effective `allowed_roles` is always included for any
  authenticated user (backwards-compatible default)

### 1.3.1 YAML Anchors In Descriptor Files

YAML anchors and aliases may be used in descriptor source files such as
`bundles.yaml`, `bundles.template.yaml`, and `bundles.secrets.yaml` as authoring
shorthand only:

```yaml
visibility:
  api:
    public_reader: &public_reader_visibility
      user_types: []
      roles: []
    news_data: *public_reader_visibility
```

The YAML parser resolves the anchor when the descriptor is loaded. After that,
KDCube works with normal in-memory dictionaries and bundle props. Runtime code
and `bundle_loader.py` do not see the anchor name or alias syntax.

If bundle props or secrets are edited through the Bundle Admin interface, the
descriptor may be written back to YAML. That write serializes the resolved
dictionary values, not the original YAML anchor structure. The first admin edit
can therefore change the descriptor text from anchored YAML to materialized
in-place values. This is expected and should not change the effective values,
but the anchor names and alias links are not preserved.

Do not depend on YAML anchors for linked mutable configuration semantics. If two
paths must remain linked after runtime edits, the platform needs an explicit
reference mechanism rather than YAML anchors.

### 1.3.2 Canonical `enabled.*` Contract

The platform-native feature-flag hook for bundle surfaces lives under the
`enabled.*` section of effective bundle props. The platform derives the
lookup path from decorator metadata, so every surface has one canonical place
to switch it on or off.

Canonical bundle-props shape:

```yaml
enabled:
  bundle: true|false
  api:
    "<route>.<api-alias>.<METHOD>": true|false   # flat key under enabled.api
  mcp:
    <mcp-alias>: true|false
  widget:
    <widget-alias>: true|false
  cron:
    <cron-alias>: true|false
```

Treat this section as deployment overrides, not as the declaration of every
bundle resource. Bundle code and decorator metadata define the default enabled
state. If config does not provide an `enabled.*` value for a resource, the
runtime uses that code default. Add config only when the deployment needs to
override the default, usually `false` for a rare disable. Remove or null the
override to return to the code default.

Mapping per decorator:

| Decorator | Canonical path |
| --- | --- |
| `@bundle_entrypoint(...)` | `enabled.bundle` |
| `@api(alias=A, method=M, route=R, ...)` | `enabled.api["R.A.M"]` (flat key) |
| `@mcp(alias=A, ...)` | `enabled.mcp.A` |
| `@ui_widget(alias=A, ...)` | `enabled.widget.A` |
| `@cron(alias=A, ...)` | `enabled.cron.A` |

For API gates, the route-aware flat key `<route>.<alias>.<METHOD>` lives under `enabled.api`; the legacy `<alias>.<METHOD>` key remains a fallback for persisted descriptors.

Example:

```python
@bundle_entrypoint(
    name="News Admin",
    version="1.0.0",
)
```

```yaml
bundles:
  items:
    - id: "news.admin@1-0"
      config:
        enabled:
          bundle: true
          widget:
            news-admin: true
          cron:
            news-sync: false
```

Resolution/enforcement rules:

- bundle-level `enabled.bundle` is checked first
- if the bundle-level check disables the bundle:
  - bundle listing hides it from the normal integrations listing
  - widget/API/MCP requests return `404`
  - scheduled jobs are not scheduled
- resource-level `enabled.<kind>.<alias>` is checked only if the bundle itself is enabled
- missing section, missing sub-section, or missing key means enabled
- this is opt-in disabling, not opt-in enabling

Disabled values:

- boolean `False`
- integer `0`
- strings `false`, `disable`, `disabled`, `off`, `0`
  - case-insensitive after trimming

Enabled values:

- boolean `True`
- non-zero integers
- any string not in the disabled set

Operational rule:

- keep the switches in bundle props under `bundles.yaml -> bundles.items[].config -> enabled: ...`
- do not use these flags as a secrets mechanism
- do not hardcode separate enable/disable logic inside the route method when platform gating is enough

Use the canonical `enabled.*` switches when you need:

- staged rollout of a bundle or widget
- environment-specific feature exposure
- turning off one scheduled job while leaving the rest of the bundle live
- hiding an operation/widget without removing its code or descriptor entry

### Runtime Hooks

Runtime hooks are normal entrypoint methods that a bundle may override. They are
not manifest decorators unless explicitly listed as decorators above. Keep hook
implementations idempotent: proc can run multiple workers, bundles can reload,
and background work can be retried after task interruption.

| Hook | Available On | When It Runs | Main Use | Important Contract |
| --- | --- | --- | --- | --- |
| `on_bundle_load(**kwargs)` | `BaseEntrypoint` and subclasses | Once per process / tenant / project when the bundle is loaded or preloaded | Build static UI, warm local indexes, prepare per-process assets | Accept only kwargs you need. Default refreshes bundle props and ensures UI build. If overriding a `BaseEntrypoint` family class, call `await super().on_bundle_load(**kwargs)` after applying needed runtime handles from `kwargs`. Avoid long unbounded work. |
| `on_props_changed(previous_props, current_props, reason, tenant, project, updated_by, source)` | `BaseEntrypoint` and subclasses | After effective bundle props change for the live bundle instance | Reconcile side effects after config changes | Default is no-op except UI-related cache handling. Treat props as deployment/runtime config, not secrets. |
| `pre_run_hook(state)` | `BaseEntrypoint` and subclasses | Before the main `@on_message` execution core | Per-turn setup, state enrichment, request-local checks | Keep fast. For `BaseEntrypointWithEconomics`, the hook may also accept `econ_ctx`. |
| `post_run_hook(state, result)` | `BaseEntrypoint` and subclasses | After successful main turn execution | Per-turn finalization based on output | Keep fast and non-critical. For `BaseEntrypointWithEconomics`, the hook may also accept `econ_ctx`. |
| `on_turn_completed(state, result, error, status, reason, **kwargs)` | `BaseEntrypoint` and subclasses | After the turn exits, errors, or is cancelled | Cleanup that must run even when a turn fails | If overriding in a class that also uses mixins, call `super().on_turn_completed(...)` so platform/mixin cleanup still runs. |
| `handle_job(**kwargs)` | Bundles with `@on_job`, plus mixins that implement job handling | When proc claims a background job and dispatches it to the bundle | Handle custom background work | If inheriting mixins, call `await super().handle_job(**kwargs)` first and return it when `handled=true`. Then process bundle-specific `work_kind` values. |
| `on_memory_reconciliation_request(request)` | `MemoryEntrypointMixin` / `BaseEntrypointWithMemory` / `BaseEntrypointWithEconomicsAndMemory` | Inside `memories_widget_reconcile_run`, before the dry-run job is stored and enqueued | Validate or augment request-local memory reconciliation controls | Return `None` or a JSON-safe dict patch. Return `{"ok": false, "error": "...", "message": "..."}` to reject. Put bundle-specific controls under `reconciliation_context`. |

#### Memory Reconciliation Request Hook

The memory reconciliation request hook is the extension point for bundle-owned
controls that should travel with one reconciliation job. Do not add a new
top-level platform field for every bundle-specific option. Use:

```json
{
  "agent_type": "regular",
  "reconciliation_context": {
    "policy": "strict"
  }
}
```

`agent_type` is a platform-supported selector with these values:

- `lite`
- `regular`
- `strong`

The selected value maps the logical `memory.reconciler` role to the configured
`memory.reconciler.lite`, `memory.reconciler.regular`, or
`memory.reconciler.strong` role model for this job only.

`reconciliation_context` is an opaque JSON-safe object owned by the bundle. The
SDK persists it in the job status, sends it with the background job payload, and
rebinds it when the reconciler actually runs:

```text
bundle_call_context.memory.reconciliation.context
```

Example hook:

```python
async def on_memory_reconciliation_request(self, *, request: dict) -> dict | None:
    context = dict(request.get("reconciliation_context") or {})
    context.setdefault("policy", "strict")
    return {
        "agent_type": request.get("agent_type") or "regular",
        "reconciliation_context": context,
    }
```

A rejecting hook:

```python
async def on_memory_reconciliation_request(self, *, request: dict) -> dict | None:
    if not self.bundle_prop("memory.reconciliation.allow_manual", True):
        return {
            "ok": False,
            "error": "memory_reconciliation_not_allowed",
            "message": "Manual memory reconciliation is disabled for this bundle.",
        }
    return None
```

This hook runs only on request submission. The background job later uses the
persisted job payload. If the bundle needs the data during downstream tool,
agent, or isolated-runtime work, read it from `bundle_call_context`, not from
the original HTTP request.

### 1.4 `@api(...)`

Marks a method as a remotely callable bundle operation.

Current signature:

```python
@api(
    method="POST",
    alias="preferences_exec_report",
    route="operations",
    user_types=("registered",),
    public_auth=None,
)
async def preferences_exec_report(self, **kwargs):
    ...
```

Current fields:

- `method`
  - `GET` or `POST`
  - default: `POST`
- `alias`
  - public operation alias in the URL
  - default: Python method name
- `route`
  - `operations` or `public`
  - default: `operations`
- `user_types`
  - tuple/list of inferred internal user types
  - examples: `registered`, `paid`, `privileged`, `anonymous`
  - empty means no user-type restriction
  - threshold semantics:
    - ordered as `anonymous < registered < paid < privileged`
    - declaring `user_types=("registered",)` permits `registered`, `paid`, and `privileged`
    - declaring `user_types=("paid",)` permits `paid` and `privileged`
    - declaring `user_types=("privileged",)` permits only `privileged`
    - declaring `user_types=("anonymous",)` permits any current user type
- `roles`
  - tuple/list of raw external roles
  - use actual auth role ids such as `kdcube:role:super-admin`
  - empty means no raw-role restriction
- `user_types_config` / `roles_config`
  - optional dot-paths into bundle props
  - when set and the path resolves to a list of strings, the resolved value
    overrides the decorator default at request time (per-request lookup)
  - empty list is a valid intentional override; invalid types fall back
    silently to the decorator default
  - missing path also falls back to the decorator default
- `public_auth`
  - used only with `route="public"`
  - current built-in modes:
    - `"none"`: explicitly unauthenticated public endpoint
    - `{"mode": "header_secret", "header": "<Header-Name>", "secret_key": "<bundle-secret-path>"}`:
      request must present the expected header value
    - `"bundle"`: proc forwards the request into the bundle method and the
      bundle authenticates it itself
  - default: required for `route="public"`, invalid for `route="operations"`
- canonical feature gate: `enabled.api["<route>.<alias>.<METHOD>"]` (flat key)
  - boolean (or string equivalent) under `enabled.api` in bundle props
  - if the resolved value is falsy, this endpoint returns 404
  - absent key means always enabled
  - checked after the bundle-level `enabled.bundle` — if the bundle is disabled,
    this check is never reached

Important current rule:

- only methods decorated with `@api(...)` are remotely callable through bundle
  operation routes
- if both `user_types` and `roles` are provided, both must match for the
  endpoint to be visible/callable
- `user_types` are evaluated by minimum required level, not exact equality
- same-name fallback for undecorated methods is no longer part of the HTTP
  contract
- `route="public"` must also declare `public_auth`
- if the bundle method accepts `request=`, proc passes the original FastAPI
  request object into the method
- `public_auth="bundle"` is for hook-style integrations where the bundle, not
  proc, verifies the inbound request
- for `public_auth="bundle"`, keep `user_types` / `roles` empty and implement
  the auth check inside the bundle method
- bundle-owned federated Data Bus token claims use `route="public"` with
  `public_auth="bundle"`; see
  [Bundle Federated Auth For Data Bus](auth-bundle-federated-README.md)
- bundle API methods do not receive a separate communicator argument from proc
  by default
- if bundle code needs request-bound execution context, use runtime helpers:
  - `get_current_comm()`
  - `get_current_request_context()`
  - `get_current_user_identity()`
  - `get_current_bundle_call_context()`
  - `update_current_bundle_call_context(...)`
  - `bind_current_bundle_call_context_patch(...)`
- for entrypoints based on `BaseEntrypoint`, prefer `self.comm` /
  `self.comm_context`
- decorated singleton bundle entrypoints should use a `BaseEntrypoint` family
  class; `BaseWorkflow` subclasses are per-message orchestrators created inside
  the entrypoint turn execution, not singleton entrypoints. See
  [Bundle Entrypoint Classes](bundle-entrypoint-classes-README.md).
- if the method needs to record selected comm events and send them to a sink,
  use the scoped recording pattern in
  [Bundle Event Recording And Sinks](bundle-event-recording-and-sinks-README.md)
- use `bundle_call_context` for JSON-safe bundle-owned metadata that must
  follow the current API/widget invocation into tools, nested agents, or
  isolated runtimes; for request-scoped model routing, set
  `bundle_call_context.role_models`
- the same pattern applies in `@mcp`, `@cron`, `@on_message`, and `@on_job`:
  bind the `bundle_call_context.role_models` override around the downstream
  SDK agent/React/tool call, not around unrelated setup code

Model-selection sketch:

```text
@api / @mcp / @cron / @on_message / @on_job
        |
        | bind_current_bundle_call_context_patch({
        |   "role_models": {"my.agent": {"provider": "...", "model": "..."}}
        | })
        v
downstream SDK agent / React / tool call
        |
        v
ModelRouter("my.agent") uses the temporary model for this invocation
```

For complete code examples, read
[bundle-agent-integration-README.md#model-selection-for-agent-roles](bundle-agent-integration-README.md#model-selection-for-agent-roles).

Route mapping:

- `@api(route="operations")` is callable through
  `/api/integrations/bundles/.../operations/{alias}`
- `@api(route="public")` is callable through
  `/api/integrations/bundles/.../public/{alias}`

### 1.5 `@mcp(...)`

Marks a method as a bundle-served MCP endpoint.

Current signature:

```python
@mcp(
    alias="tools",
    route="operations",
    transport="streamable-http",
)
def tools_mcp(self):
    ...
```

Current fields:

- `alias`
  - MCP endpoint alias in the URL
  - default: Python method name
- `route`
  - `operations` or `public`
  - default: `operations`
- `transport`
  - current supported value: `streamable-http`
- `transport_config`
  - optional dot-path into bundle props
  - when set and the path resolves to a supported transport string, the
    resolved value overrides the decorator default at request time
  - invalid / unknown transport values fall back silently to the decorator default
  - missing path also falls back to the decorator default
- canonical feature gate: `enabled.mcp.<alias>`
  - boolean (or string equivalent) nested under `enabled.mcp` in bundle props
  - if the resolved value is falsy, the MCP endpoint returns 404
  - absent key means always enabled
  - checked after the bundle-level `enabled.bundle`

Current rule:

- the decorated method must return a `FastMCP` app exposing
  `streamable_http_app()` or an ASGI app already prepared for MCP HTTP handling
- proc resolves the bundle endpoint, obtains that MCP app from the bundle
  method, and dispatches the incoming request into it
- proc forwards the original request headers and body to the MCP subapp
- proc does **not** authenticate or authorize MCP requests before dispatch
- bundle code owns MCP request authentication/authorization
- if the provider method accepts `request=`, proc passes the original FastAPI
  request object so bundle code can inspect headers before returning the MCP app

Route semantics:

- `route="operations"`
  - non-public MCP URL family
  - use when the bundle intends to authenticate or otherwise gate the caller
- `route="public"`
  - public MCP URL family
  - use when the endpoint is intentionally public

Important:

- `@mcp(...)` does not support proc-side `user_types`, `roles`, or `public_auth`
- if a bundle needs header-based secrets, bearer tokens, API keys, custom JWT
  validation, or any other MCP auth scheme, that logic must live in the bundle
  MCP app itself
- canonical pattern:
  - put the client-facing header name in bundle props such as
    `self.bundle_prop("mcp.inbound.auth.header_name")`
  - put the verification material in bundle secrets such as
    `await get_secret("b:mcp.inbound.auth.shared_token")`
  - read `request.headers[...]` in the provider and reject with
    `HTTPException(status_code=401, ...)` before returning the MCP app
- full worked example:
  [bundle-transports-README.md](bundle-transports-README.md)

Route mapping:

- `@mcp(route="operations")` is callable through
  `/api/integrations/bundles/.../mcp/{alias}`
- `@mcp(route="public")` is callable through
  `/api/integrations/bundles/.../public/mcp/{alias}`

### 1.6 `@ui_widget(...)`

Marks a method as a discoverable widget endpoint.

```python
@ui_widget(
    icon={
        "tailwind": "heroicons-outline:adjustments-horizontal",
        "lucide": "SlidersHorizontal",
    },
    alias="preferences",
    user_types=("registered", "privileged"),
)
def preferences_widget(self, **kwargs):
    ...
```

Current fields:

- `icon`
  - preferred shape is a provider map
  - supported providers: `tailwind`, `lucide`
  - legacy string icons are normalized to `{"tailwind": "<value>"}`
- `alias`
  - widget alias used by widget discovery/fetch
  - default: Python method name
- `user_types`
  - inferred internal user types allowed to see the widget
  - same threshold rule as `@api(...)`:
    - `anonymous < registered < paid < privileged`
    - `user_types=("registered",)` means registered-or-higher
- `roles`
  - raw external roles allowed to see the widget
  - use values such as `kdcube:role:super-admin`
- `user_types_config` / `roles_config`
  - optional dot-paths into bundle props
  - when set and the path resolves to a list of strings, the resolved value
    overrides the decorator default at request time (per-request lookup)
  - empty list is a valid intentional override; invalid types fall back
    silently to the decorator default
  - missing path also falls back to the decorator default
- canonical feature gate: `enabled.widget.<alias>`
  - boolean (or string equivalent) nested under `enabled.widget` in bundle props
  - if the resolved value is falsy, the widget fetch returns 404
  - absent key means always enabled
  - checked after the bundle-level `enabled.bundle`

Current rule:

- widget list and widget fetch are driven only by `@ui_widget(...)`

If the same widget method must also be callable through `/operations/...`,
decorate it with both `@ui_widget(...)` and `@api(route="operations", ...)`.

That is the current compatibility pattern for widgets that are still loaded
through operation calls in existing clients.

Widget runtime config contract:

- request runtime config from the display environment with `CONFIG_REQUEST`
- accept both `CONN_RESPONSE` and `CONFIG_RESPONSE`
- build operation URLs from `baseUrl`, `defaultTenant`, `defaultProject`, and `defaultAppBundleId`
- do not hardcode tenant/project/bundle id from the source tree

Use the dedicated frontend contract doc for the exact pattern and example:

- [bundle-widget-integration-README.md](bundle-widget-integration-README.md)

### 1.7 `@ui_main`

Marks the method that declares the bundle's main UI surface. KDCube serves that
UI as static assets, like it serves bundle APIs and MCP routes. A frontend may
display it directly or embed it, but the bundle declares UI assets, not an
iframe object.

```python
@ui_main
def main_ui(self, **kwargs):
    ...
```

Current behavior:

- the bundle manifest reports `ui_main`
- proc serves the built UI assets from the bundle static route
- build-on-first-request is supported for bundles that have a UI defined but
  were not yet built in the current proc

### 1.8 `@on_message`

Marks the bundle message handler metadata.

```python
@on_message
async def run(self, **params):
    ...
```

Current practical pattern:

- base entrypoints already decorate `run()` with `@on_message`
- manifest discovery reports the message handler method name

### 1.9 `@on_job`

Marks the bundle handler for ready background jobs claimed by proc from the
background jobs stream.

```python
from kdcube_ai_app.infra.plugin.bundle_loader import on_job

@on_job
async def on_job(self, job: dict, **kwargs) -> dict:
    ...
```

Current behavior:

- proc discovers at most one `@on_job` method through the manifest discovery path
- a background job is not an HTTP request and has no public URL
- the processor claims a Redis Stream job, builds a normal bundle request context,
  and invokes the discovered `@on_job` method with the job envelope
- `@on_job` must be async; proc does not wrap it in a sync fallback
- the job envelope carries platform routing fields plus bundle-owned `work_kind`,
  `metadata`, and `payload`
- the bundle owns job semantics: validate `work_kind`, load bundle-owned records,
  execute the work, and update bundle-owned status/results
- reusable SDK mixins must not add their own decorated `@on_job` method; expose a
  normal `handle_job(...)` dispatcher instead

Use `@cron(...)` to detect scheduled due work. Use `@on_job` to execute ready
work that has been enqueued for fair processor claiming.

Recommended dispatch pattern:

```python
from kdcube_ai_app.infra.plugin.bundle_loader import on_job

@on_job
async def on_job(self, **kwargs) -> dict:
    handled = await super().handle_job(**kwargs)
    if handled.get("handled"):
        return handled

    job = kwargs.get("job") or {}
    work_kind = kwargs.get("work_kind") or job.get("work_kind")
    if work_kind == "my.bundle.job":
        return await self.my_subsystem.run(job.get("payload") or {})
    return {"ok": False, "handled": False, "error": {"code": "unsupported_job"}}
```

See:

- [docs/service/streams/background-jobs-README.md](../../service/streams/background-jobs-README.md)

### 1.10 `@data_bus_handler(...)`

Marks a method as a durable Data Bus subject handler. Data Bus handlers process
non-conversation domain messages that were accepted through Socket.IO
`data_bus.publish` and written to the bundle-scoped Data Bus Redis Stream.

```python
from kdcube_ai_app.apps.chat.sdk.data_bus import data_bus_handler

@data_bus_handler(
    subject="task_tracker.canvas.patch",
    partition_by="object_ref",
    ordering="serial_per_partition",
    idempotency="required",
)
async def handle_canvas_patch(self, ctx, message):
    result = await self.canvas.apply_patch(
        object_ref=message.object_ref,
        idempotency_key=message.idempotency_key,
        payload=message.payload,
        actor=message.actor,
    )
    await ctx.reply.ok({"revision": result.revision})
    return {"status": "ok", "data": {"revision": result.revision}}
```

Current fields:

- `subject`
  - stable domain subject handled by this bundle
  - must be unique within one bundle
- `partition_by`
  - `"none"` for independent messages
  - `"object_ref"` when messages operate on a shared object such as a canvas,
    board, issue, or document
- `ordering`
  - `"parallel"` for normal concurrent handling
  - `"serial_per_partition"` for one active handler at a time per partition
- `idempotency`
  - `"optional"` or `"required"`
  - use `"required"` for mutations
- `user_types` / `roles`
  - same visibility selectors used by other manifest-exposed bundle methods

Current behavior:

- proc discovers all `@data_bus_handler(...)` methods through the manifest path
- the Socket.IO `data_bus.publish` ingress validates bundle visibility, subject
  visibility, required `object_ref`, and required `idempotency_key`
- Socket.IO accepts scoped federated Data Bus tokens issued by bundle public
  claim endpoints for clients without a platform browser session
- accepted messages are written to
  `kdcube:data-bus:{tenant}:{project}:{bundle_id}:messages`
- the processor-owned Data Bus runtime reconciles the active registry and
  starts managed workers for bundles with registered handlers
- bundles do not create their own Redis consumers
- `serial_per_partition` uses a Redis token lock so two workers do not execute
  the same subject/object partition concurrently
- handler replies use `ctx.reply.*` through the existing communicator relay
  when reply metadata is present
- durable truth is the bundle-owned storage mutation, not the reply event

Data Bus handlers do not create chat turns, `external_events[]`, timeline
entries, or `ev:` artifacts unless the bundle explicitly bridges a handled
domain message into conversation ingress.

See:

- [docs/service/comm/data-bus-README.md](../../service/comm/data-bus-README.md)
- [bundle-client-communication-README.md#data-bus-contract](bundle-client-communication-README.md#data-bus-contract)

### 1.11 `@cron(...)`

Marks a method as a recurring scheduled job managed by proc.

```python
from kdcube_ai_app.infra.plugin.bundle_loader import cron

@cron(
    alias="rebuild-indexes",
    cron_expression="0 * * * *",
    expr_config="routines.reindex.cron",
    span="system",
)
async def rebuild_indexes(self) -> None:
    ...
```

Current fields:

- `alias`
  - stable job identifier used in lock keys and logs
  - default: Python method name
- `cron_expression`
  - inline cron expression, e.g. `"*/15 * * * *"`
  - used when `expr_config` is not set
- `expr_config`
  - dot-separated path into bundle props/config, e.g. `"routines.reindex.cron"`
  - if set, takes precedence over `cron_expression` at runtime
  - if the resolved value is missing, blank, or `"disable"` (case-insensitive) → job is **not** scheduled
  - does **not** fall back to `cron_expression` when `expr_config` is set but unresolvable
- `span`
  - `"process"` — runs independently in every proc worker process (no lock)
  - `"instance"` — runs once per host instance, Redis lock per `INSTANCE_ID`
  - `"system"` — runs once across all instances for this tenant/project/bundle/job, Redis lock
  - omitting `span` or passing an empty string defaults to `"system"`
  - an unrecognised value raises `ValueError` at decoration time
- `timezone`
  - IANA timezone name, e.g. `"Europe/Berlin"`
  - defaults to UTC when omitted
- `tz_config`
  - dot-separated path into bundle props/config for the timezone override
  - if set and resolved to a non-blank string, takes precedence over `timezone`
- canonical feature gate: `enabled.cron.<alias>`
  - boolean (or string equivalent) nested under `enabled.cron` in bundle props
  - if the resolved value is falsy, this job is not scheduled
  - absent key means always enabled
  - checked after the bundle-level `enabled.bundle` in `bundle_scheduler.py` —
    if the bundle itself is disabled, no per-job check is performed

Current behavior:

- proc discovers `@cron` methods through the same manifest discovery path as `@api` and `@ui_widget`
- `BundleSchedulerManager` in proc reconciles job tasks after every registry or props change
- no proc restart required for schedule changes
- if Redis is unavailable for `instance`/`system` spans, the tick is skipped and a warning is logged; the job is **not** silently degraded to `process`
- the method runs headlessly — no user session or SSE stream, but normal bundle runtime access is available (`self.bundle_prop(...)`, `self.redis`, `self.pg_pool`, `await get_secret("b:...")`)
- async methods are executed directly; sync methods run via `asyncio.to_thread`
- overlapping runs within the same exclusivity scope are prevented (in-process flag for `process`, Redis lock for `instance`/`system`)

For full details on span semantics, cron resolution, and local debug:

- [docs/sdk/bundle/bundle-scheduled-jobs-README.md](bundle-scheduled-jobs-README.md)

### 1.12 `@venv(...)`

Marks a callable to execute in a cached per-bundle subprocess venv.

```python
from kdcube_ai_app.infra.plugin.bundle_loader import venv

@venv(requirements="requirements.txt", timeout_seconds=120)
def parse_large_pdf(payload: dict) -> dict:
    ...
```

Current fields:

- `requirements`
  - bundle-relative path to the requirements file
  - default: `requirements.txt`
- `python`
  - optional base Python executable used to create the venv
  - default: current runtime Python
- `timeout_seconds`
  - optional subprocess timeout for that callable

Current behavior:

- the decorated callable remains visible as a normal Python function to the rest of the bundle
- when invoked from proc, the runtime:
  - resolves the bundle root and bundle id
  - creates or reuses a cached venv under bundle-managed local storage
  - builds that venv from the selected base Python
  - overlays current runtime packages into it
  - installs the bundle's `requirements.txt` on top of that runtime layer
  - rebuilds only when the requirements file hash changed
  - executes the callable in a subprocess using that venv
  - deserializes the result back into proc

Important current rule:

- `@venv(...)` is an execution decorator, not an HTTP/UI manifest decorator
- it does **not** create routes, widgets, or manifest entries by itself
- it is intended for dependency-heavy helper functions
- prefer plain module-level helpers or other easily serializable callables
- do not use it for methods that depend on live proc-owned objects or shared singleton state
- the venv child does **not** receive proc-bound runtime bindings such as `self.comm`, `self.comm_context`, `get_current_comm()`, `get_current_request_context()`, `TOOL_SUBSYSTEM`, `COMMUNICATOR`, `KV_CACHE`, `CTX_CLIENT`, DB pools, Redis clients, or framework request objects
- a `@venv(...)` helper should not call `bundle_tool_context.host_files(...)`
  directly. Return serializable data or write files for the trusted catalog tool
  that called the helper; that catalog tool can then declare
  `ret.artifact_type: "files"` or call `host_files(...)` from the prepared tool
  context.
- changing bundle Python source still requires the normal proc-side bundle reload path; `@venv(...)` only controls the helper execution environment

## 2) Metadata model

The loader stores interface metadata as typed dataclasses.

### 2.1 `APIEndpointSpec`

```python
@dataclass(frozen=True)
class APIEndpointSpec:
    method_name: str
    alias: str
    http_method: str = "POST"
    route: str = "operations"
    user_types: tuple[str, ...] = ()
    user_types_config: str | None = None
    roles: tuple[str, ...] = ()
    roles_config: str | None = None
```

### 2.2 `UIWidgetSpec`

```python
@dataclass(frozen=True)
class UIWidgetSpec:
    method_name: str
    alias: str
    icon: dict[str, str]
    user_types: tuple[str, ...] = ()
    user_types_config: str | None = None
    roles: tuple[str, ...] = ()
    roles_config: str | None = None
```

### 2.3 `MCPEndpointSpec`

```python
@dataclass(frozen=True)
class MCPEndpointSpec:
    method_name: str
    alias: str
    route: str = "operations"
    transport: str = "streamable-http"
    transport_config: str | None = None
```

### 2.4 `OnMessageSpec`

```python
@dataclass(frozen=True)
class OnMessageSpec:
    method_name: str
```

### 2.5 `OnJobSpec`

```python
@dataclass(frozen=True)
class OnJobSpec:
    method_name: str
```

### 2.6 `UIMainSpec`

```python
@dataclass(frozen=True)
class UIMainSpec:
    method_name: str
```

### 2.7 `CronJobSpec`

```python
@dataclass(frozen=True)
class CronJobSpec:
    method_name: str
    alias: str = ""
    cron_expression: str | None = None
    expr_config: str | None = None
    timezone: str | None = None
    tz_config: str | None = None
    span: str = "system"
```

### 2.8 `DataBusHandlerSpec`

```python
@dataclass(frozen=True)
class DataBusHandlerSpec:
    method_name: str
    subject: str
    partition_by: str = "none"
    ordering: str = "parallel"
    idempotency: str = "optional"
    user_types: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
```

### 2.9 `BundleInterfaceManifest`

```python
@dataclass(frozen=True)
class BundleInterfaceManifest:
    bundle_id: str
    allowed_roles: tuple[str, ...] = ()
    allowed_roles_config: str | None = None
    ui_widgets: tuple[UIWidgetSpec, ...] = ()
    api_endpoints: tuple[APIEndpointSpec, ...] = ()
    mcp_endpoints: tuple[MCPEndpointSpec, ...] = ()
    ui_main: UIMainSpec | None = None
    on_message: OnMessageSpec | None = None
    on_job: OnJobSpec | None = None
    scheduled_jobs: tuple[CronJobSpec, ...] = ()
    data_bus_handlers: tuple[DataBusHandlerSpec, ...] = ()
```

`allowed_roles` is populated from the `allowed_roles` argument of
`@bundle_entrypoint`. Empty tuple means no restriction.

`allowed_roles_config` is the dot-path declared via `allowed_roles_config=` on
`@bundle_entrypoint`. When set, `apply_bundle_overrides(manifest, props)` resolves
the path against bundle props and returns a new manifest with the effective
`allowed_roles`. The admin descriptor exposes `allowed_roles_default`,
`allowed_roles_config`, and `allowed_roles_overridden` alongside the effective
`allowed_roles`.

`scheduled_jobs` is populated from all `@cron`-decorated methods on the
entrypoint class, sorted by `alias`.

`data_bus_handlers` is populated from all `@data_bus_handler(...)` methods on
the entrypoint class, sorted by `subject`.

Discovery helpers currently exposed by the loader:

- `discover_bundle_interface_manifest(...)`
- `resolve_bundle_api_endpoint(...)`
- `resolve_bundle_mcp_endpoint(...)`
- `resolve_bundle_widget(...)`
- `resolve_bundle_message_method(...)`

## 3) Current REST and static routes

### 3.1 Bundle manifest

```text
GET /api/integrations/bundles/{tenant}/{project}/{bundle_id}
```

Returns bundle interface metadata visible to the current user.

Current response shape includes:

- `ui_widgets`
- `api_endpoints`
  - includes `alias`
  - includes `http_method`
  - includes `route`
  - includes `user_types`
  - includes `roles`
- `mcp_endpoints`
  - includes `alias`
  - includes `route`
  - includes `transport`
  - includes `user_types`
  - includes `roles`
- `ui_main`
- `on_message`
- `on_job`
- `scheduled_jobs`
  - includes `method_name`
  - includes `alias`
  - includes `cron_expression` (declared value, not runtime-resolved)
  - includes `expr_config`
  - includes `span`
- `data_bus_handlers`
  - includes `subject`
  - includes `partition_by`
  - includes `ordering`
  - includes `idempotency`
  - includes `user_types`
  - includes `roles`

Example:

```json
{
  "bundle_id": "versatile@2026-03-31-13-36",
  "tenant": "demo-tenant",
  "project": "demo-project",
  "ui_widgets": [
    {
      "alias": "preferences",
      "icon": {
        "tailwind": "heroicons-outline:adjustments-horizontal",
        "lucide": "SlidersHorizontal"
      },
      "user_types": ["registered", "privileged"],
      "roles": []
    }
  ],
  "api_endpoints": [
    {
      "alias": "preferences_exec_report",
      "http_method": "POST",
      "route": "operations",
      "user_types": ["registered"],
      "roles": []
    }
  ],
  "mcp_endpoints": [
    {
      "alias": "tools",
      "route": "operations",
      "transport": "streamable-http"
    }
  ],
  "ui_main": {
    "method_name": "main_ui"
  },
  "on_message": {
    "method_name": "run"
  },
  "on_job": {
    "method_name": "on_job"
  },
  "scheduled_jobs": [
    {
      "method_name": "rebuild_indexes",
      "alias": "rebuild-indexes",
      "cron_expression": "0 * * * *",
      "expr_config": "routines.reindex.cron",
      "span": "system"
    }
  ]
}
```

### 3.2 Operations route

```text
GET  /api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/{alias}
POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/{alias}
```

Current rules:

- only `@api(..., route="operations")` methods are callable here
- `POST` forwards `payload.data` as kwargs
- `GET` forwards query params as kwargs
- request/session context still comes from platform session and `self.comm`
- if the alias exists for a different HTTP method on the same route, proc
  returns `405`
- if the alias is not declared for route `operations`, proc returns `404`

### 3.3 Public route

```text
GET  /api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/{alias}
POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/{alias}
```

Current rules:

- only `@api(..., route="public")` methods are callable here
- route matching is strict; an `operations` method is not callable through
  `/public/...`
- public methods must also declare `public_auth`
- current built-in public auth modes are:
  - `public_auth="none"` for intentionally open public endpoints
  - `public_auth={"mode":"header_secret", ...}` for shared-secret webhook headers
  - `public_auth="bundle"` for bundle-owned webhook auth
- with `public_auth="bundle"`, proc still owns dispatch but the bundle method
  verifies the inbound request itself

### 3.4 Bundle MCP routes

```text
GET  /api/integrations/bundles/{tenant}/{project}/{bundle_id}/mcp/{alias}
POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/mcp/{alias}
GET  /api/integrations/bundles/{tenant}/{project}/{bundle_id}/mcp/{alias}/{path}
POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/mcp/{alias}/{path}

GET  /api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/mcp/{alias}
POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/mcp/{alias}
GET  /api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/mcp/{alias}/{path}
POST /api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/mcp/{alias}/{path}
```

Current rules:

- only `@mcp(...)` methods are callable here
- route matching is strict:
  - `route="operations"` is not callable through `/public/mcp/...`
  - `route="public"` is not callable through `/mcp/...`
- current supported transport is `streamable-http`
- proc rewrites the routed request onto the MCP subapp path expected by the
  current FastMCP HTTP transport
- bundle code returns the MCP app; proc does not synthesize MCP tools from
  ordinary `@api(...)` methods
- proc forwards original request headers/body to the bundle MCP subapp
- proc does not authenticate MCP at this route layer
- bundle code is responsible for MCP request authentication if the endpoint is
  not intentionally public
### 3.5 Legacy no-bundle-id operations route

```text
POST /api/integrations/bundles/{tenant}/{project}/operations/{alias}
```

This route still exists for backward compatibility.

Current rules:

- it still resolves only declared `@api(..., route="operations")` methods
- `bundle_id` may come from the body
- otherwise the current default bundle is used

### 3.6 Widgets list

```text
GET /api/integrations/bundles/{tenant}/{project}/{bundle_id}/widgets
```

Returns only `@ui_widget(...)` metadata visible to the current user.

### 3.7 Widget fetch

```text
GET /api/integrations/bundles/{tenant}/{project}/{bundle_id}/widgets/{alias}
```

Current rules:

- resolves only `@ui_widget(...)` aliases
- applies role visibility before invocation
- does not use operation routing

### 3.8 Bundle static UI

```text
GET /api/integrations/static/{tenant}/{project}/{bundle_id}
GET /api/integrations/static/{tenant}/{project}/{bundle_id}/{path}
```

Current behavior:

- serves assets built under the bundle's UI storage root
- supports SPA fallback to `index.html`
- injects `<base>` into `index.html` so relative assets resolve correctly
- can trigger a build on first request if the UI was not yet built in the
  current proc

This is the route used for bundle main-view apps embedded in the host UI.
The built app must use relative asset URLs. For Vite main UIs, configure
`base: './'` and verify the built HTML uses `./assets/...`; `/assets/...`
will resolve at the KDCube domain root instead of this bundle static route.

## 4) Role visibility

Role visibility is enforced by the platform integration layer at two levels.

### 4.1 Bundle-level filtering (`allowed_roles` on `@bundle_entrypoint`)

Applies to the bundle listing endpoint (`GET /api/integrations/bundles`).

- The platform compares the user's **raw roles** — `session.roles` entries
  that start with `kdcube:role:` — against the bundle's `allowed_roles`.
- Raw roles are externally defined: they are Cognito group IDs propagated
  directly from the ID token without transformation.
- Derived platform types (`"registered"`, `"privileged"`, `"paid"`) are
  **not** considered for this check.
- A bundle is included in the listing if the intersection is non-empty
  (OR semantics).
- A bundle with empty `allowed_roles` is always included.
- Admin listing (`GET /api/admin/integrations/bundles`) is not filtered.

### 4.2 Per-method filtering (`user_types` and `roles` on `@api` and `@ui_widget`)

Applies within a bundle manifest — controls which apis and widgets are
visible to a given user.

- `user_types` are matched against the session's inferred platform user type.
- `user_types` use ordered threshold semantics:
  - `anonymous < registered < paid < privileged`
  - a method is permitted when the current user type is greater than or equal
    to the lowest declared user type
  - `user_types=("registered",)` means registered-or-higher
  - `user_types=("paid",)` means paid-or-higher
  - `user_types=("privileged",)` means privileged only
  - `user_types=("anonymous",)` means no effective restriction by user type
- `roles` are matched against the session's raw `kdcube:role:*` entries.
- If both are present, both conditions must pass.
- Enforced by `_endpoint_visible` in the integration layer.
- Direct widget fetch and operation routes also reject unauthorized aliases.

Bundle methods should still enforce business-level authorization when needed,
but route-level visibility is already enforced from decorator metadata.

`@mcp(...)` is different:

- proc does not enforce `user_types` / `roles` for MCP
- MCP authentication/authorization is owned by the bundle MCP app

## 5) Authoring rules

Use these rules for new bundles:

1. Decorate every remotely callable HTTP method with `@api(...)`.
2. Decorate every remotely callable MCP surface with `@mcp(...)`.
3. Use `route="operations"` for authenticated/internal bundle operations and
   for MCP endpoints that the bundle intends to authenticate itself.
4. Use `route="public"` only for intentionally public endpoints.
5. Decorate every widget method with `@ui_widget(...)`.
6. If a widget method is also called through `/operations/...`, add
   `@api(route="operations", alias="<operation-alias>")` to the same method.
7. Use `@ui_main` when the bundle has a main static UI application.
8. Use `@on_message` on the bundle message handler. The base entrypoints already
   do this on `run()`.
9. Use `@data_bus_handler(...)` when the bundle accepts durable non-chat domain
   messages from widgets or services. Keep it async, make mutation subjects
   idempotent, and use `partition_by="object_ref"` plus
   `ordering="serial_per_partition"` for shared objects.
10. Use `@on_job` when the bundle receives ready background jobs from the
   processor job stream. Keep the method async and define at most one per bundle.
   If the entrypoint derives from mixins with background work, call
   `await super().handle_job(**kwargs)` first and only handle the job locally
   when it returns `handled=false`.
11. Use `@cron(...)` for recurring background work. Prefer `span="system"` (the
   default) unless process-local or instance-local semantics are required.
   See [docs/sdk/bundle/bundle-scheduled-jobs-README.md](bundle-scheduled-jobs-README.md)
   for details on span semantics, `expr_config` resolution, and local debug.

Important current rule:

- do not rely on undecorated same-name method exposure for HTTP routes

## 6) Reference implementations

Primary reference bundle:

- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36`

Smaller custom main-view example:

- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/echo.ui@2026-03-30`

Relevant code:

- decorator implementation:
  `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/bundle_loader.py`
- integrations controller:
  `src/kdcube-ai-app/kdcube_ai_app/apps/chat/proc/rest/integrations/integrations.py`
- base entrypoint widget and `@on_message` usage:
  `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/chatbot/entrypoint.py`
- background job stream:
  `src/kdcube-ai-app/kdcube_ai_app/infra/jobs/stream.py`

## 7) Practical examples

### 7.1 Normal authenticated operation

```python
@api(alias="preferences_exec_report", route="operations", user_types=("registered",))
async def preferences_exec_report(self, **kwargs):
    ...
```

### 7.2 Public webhook-style operation

```python
@api(
    alias="telegram_webhook",
    route="public",
    public_auth={
        "mode": "header_secret",
        "header": "X-Telegram-Bot-Api-Secret-Token",
        "secret_key": "telegram.webhook_secret",
    },
)
async def telegram_webhook(self, **kwargs):
    ...
```

### 7.3 Bundle-authenticated public hook

```python
from fastapi import HTTPException, Request
from kdcube_ai_app.apps.chat.sdk.config import get_secret

@api(
    alias="telegram_webhook",
    route="public",
    public_auth="bundle",
)
async def telegram_webhook(self, request: Request, **kwargs):
    header_name = self.bundle_prop(
        "telegram.webhook.auth.header_name",
        "X-Telegram-Bot-Api-Secret-Token",
    )
    expected_token = await get_secret("b:telegram.webhook.auth.shared_token")
    provided_token = request.headers.get(header_name)
    if not expected_token or provided_token != expected_token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {"ok": True}
```

Server-side contract:

```yaml
# bundles.yaml
bundles:
  version: "1"
  items:
    - id: "partner.tools@1-0"
      config:
        telegram:
          webhook:
            auth:
              header_name: "X-Telegram-Bot-Api-Secret-Token"
```

```yaml
# bundles.secrets.yaml
bundles:
  version: "1"
  items:
    - id: "partner.tools@1-0"
      secrets:
        telegram:
          webhook:
            auth:
              shared_token: "replace-in-real-deployment"
```

Client-side call shape:

```bash
curl -X POST \
  "http://localhost:5173/api/integrations/bundles/<tenant>/<project>/<bundle_id>/public/telegram_webhook" \
  -H "X-Telegram-Bot-Api-Secret-Token: <shared-token>" \
  -H "Content-Type: application/json" \
  -d '{"update_id": 1}'
```

What the bundle shares with the client:

- the public operations route for alias `telegram_webhook`
- the header name from bundle props
- the token provisioned in bundle secrets

### 7.4 Widget plus operation compatibility

```python
@api(alias="preferences_widget", route="operations", user_types=("registered",))
@ui_widget(
    alias="preferences",
    icon={"tailwind": "heroicons-outline:adjustments-horizontal"},
    user_types=("registered",),
)
def preferences_widget(self, **kwargs):
    ...
```

This pattern is appropriate when:

- the platform should list/fetch the method as a widget by widget alias
- an existing client still calls the same method through `/operations/...`
