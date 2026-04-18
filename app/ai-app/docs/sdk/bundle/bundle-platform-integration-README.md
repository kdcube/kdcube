---
id: ks:docs/sdk/bundle/bundle-platform-integration-README.md
title: "Bundle Platform Integration"
summary: "Current declarative bundle integration contract: supported decorators, manifest metadata, REST routes, and UI/static integration."
tags: ["sdk", "bundle", "integration", "decorators", "widgets", "operations", "ui", "manifest", "cron", "scheduled-jobs"]
keywords: ["agentic_workflow", "bundle_id decorator", "api decorator", "ui_widget", "ui_main", "on_message", "cron decorator", "scheduled jobs", "bundle manifest", "integrations widgets", "integrations operations", "public route"]
see_also:
  - ks:docs/sdk/bundle/bundle-interfaces-README.md
  - ks:docs/sdk/bundle/bundle-scheduled-jobs-README.md
  - ks:docs/sdk/bundle/bundle-dev-README.md
  - ks:docs/sdk/bundle/bundle-venv-README.md
  - ks:docs/sdk/bundle/bundle-index-README.md
  - ks:docs/sdk/bundle/bundle-reference-versatile-README.md
---
# Bundle Platform Integration

This document describes the bundle integration contract that is implemented now.
It covers:

- class and method decorators supported by the bundle loader
- bundle interface manifest discovery
- REST routing for bundle operations and public operations
- widget discovery and widget fetch
- bundle main UI entrypoints and static asset serving

All of this is implemented in:

- `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/agentic_loader.py`
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/proc/rest/integrations/integrations.py`

## 1) Supported decorators

Bundles currently support these decorators:

| Decorator | Scope | What it means |
| --- | --- | --- |
| `@agentic_workflow(...)` | entrypoint class | Declares the bundle workflow class used by the runtime. |
| `@agentic_workflow_factory(...)` | factory function | Declares a workflow factory function instead of a workflow class. |
| `@bundle_id(...)` | entrypoint class | Declares the code-level bundle id used when runtime needs to infer identity from the bundle code itself. |
| `@api(...)` | entrypoint method | Declares a remotely callable bundle HTTP operation. |
| `@ui_widget(...)` | entrypoint method | Declares a widget in the bundle interface manifest. |
| `@ui_main` | entrypoint method | Declares the main iframe UI entrypoint. |
| `@on_message` | entrypoint method | Declares the bundle message handler metadata. |
| `@cron(...)` | entrypoint method | Declares a scheduled background job managed by proc. |
| `@venv(...)` | helper function or method | Declares that a callable executes in a cached per-bundle subprocess venv. |

Important distinction:

- `@agentic_workflow(...)`, `@agentic_workflow_factory(...)`, `@bundle_id(...)`,
  `@api(...)`, `@ui_widget(...)`, `@ui_main`, `@on_message`, and `@cron(...)`
  participate in bundle manifest and runtime interface discovery
- `@venv(...)` is an execution decorator, not an HTTP/UI manifest decorator
- most bundles should use `@agentic_workflow(...)`; `@agentic_workflow_factory(...)`
  is the exception for custom construction cases

These decorators are runtime metadata. They are not deployment config.

### 1.1 `@agentic_workflow_factory(...)`

Declares a workflow factory function rather than a workflow class.

```python
from kdcube_ai_app.infra.plugin.agentic_loader import agentic_workflow_factory

@agentic_workflow_factory(name="My Bundle", version="1.0.0")
def build_bundle(config, **kwargs):
    ...
```

Use this only when the bundle must construct its runtime through a factory
function. Most bundles should use `@agentic_workflow(...)` on a class.

Side-by-side:

```python
from kdcube_ai_app.infra.plugin.agentic_loader import (
    agentic_workflow,
    agentic_workflow_factory,
    bundle_id,
)

@agentic_workflow(name="My Bundle", version="1.0.0")
@bundle_id("my.bundle@1.0.0")
class MyWorkflow:
    ...

@agentic_workflow_factory(name="My Bundle", version="1.0.0")
def build_workflow(config, **kwargs):
    return MyWorkflow(config=config, **kwargs)
```

In practice:

- class form means the runtime instantiates the workflow class directly
- factory form means the runtime calls your function and uses the returned
  workflow instance
- prefer the class form unless you specifically need dynamic selection,
  wrapping, or legacy construction adaptation

### 1.2 `@bundle_id(...)`

Declares the canonical bundle ID on the entrypoint class.

```python
from kdcube_ai_app.infra.plugin.agentic_loader import agentic_workflow, bundle_id

@agentic_workflow(name="My Bundle", version="1.0.0")
@bundle_id("my.bundle@1.0.0")
class MyBundle:
    ...
```

Use it when the code should declare its own stable bundle identity.

### 1.3 `@agentic_workflow(...)` — bundle-level `allowed_roles`

The `@agentic_workflow` decorator accepts an optional `allowed_roles` parameter
that restricts which users can see the bundle in the bundle listing.

```python
@agentic_workflow(
    name="Finance Copilot",
    version="1.0.0",
    allowed_roles=("kdcube:role:finance-team", "kdcube:role:super-admin"),
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

Current behavior:

- `GET /api/integrations/bundles` (non-admin) filters out bundles whose
  `allowed_roles` do not intersect with the calling user's raw roles
  (entries in the session that start with `kdcube:role:`)
- `GET /api/admin/integrations/bundles` is not filtered — admin always
  sees all bundles regardless of `allowed_roles`
- A bundle with no `allowed_roles` is always included for any authenticated
  user (backwards-compatible default)

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
- `public_auth`
  - used only with `route="public"`
  - current built-in modes:
    - `"none"`: explicitly unauthenticated public endpoint
    - `{"mode": "header_secret", "header": "<Header-Name>", "secret_key": "<bundle-secret-path>"}`:
      request must present the expected header value
  - default: required for `route="public"`, invalid for `route="operations"`

Important current rule:

- only methods decorated with `@api(...)` are remotely callable through bundle
  operation routes
- if both `user_types` and `roles` are provided, both must match for the
  endpoint to be visible/callable
- `user_types` are evaluated by minimum required level, not exact equality
- same-name fallback for undecorated methods is no longer part of the HTTP
  contract
- `route="public"` must also declare `public_auth`
- bundle API methods do not receive a separate communicator argument from proc
  by default
- if bundle code needs request-bound execution context, use runtime helpers:
  - `get_current_comm()`
  - `get_current_request_context()`
- for entrypoints based on `BaseEntrypoint`, prefer `self.comm` /
  `self.comm_context`

Route mapping:

- `@api(route="operations")` is callable through
  `/api/integrations/bundles/.../operations/{alias}`
- `@api(route="public")` is callable through
  `/api/integrations/bundles/.../public/{alias}`

### 1.5 `@ui_widget(...)`

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

Current rule:

- widget list and widget fetch are driven only by `@ui_widget(...)`

If the same widget method must also be callable through `/operations/...`,
decorate it with both `@ui_widget(...)` and `@api(route="operations", ...)`.

That is the current compatibility pattern for widgets that are still loaded
through operation calls in existing clients.

### 1.6 `@ui_main`

Marks the method that declares the bundle's main iframe UI surface.

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

### 1.7 `@on_message`

Marks the bundle message handler metadata.

```python
@on_message
async def run(self, **params):
    ...
```

Current practical pattern:

- base entrypoints already decorate `run()` with `@on_message`
- manifest discovery reports the message handler method name

### 1.8 `@cron(...)`

Marks a method as a recurring scheduled job managed by proc.

```python
from kdcube_ai_app.infra.plugin.agentic_loader import cron

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

Current behavior:

- proc discovers `@cron` methods through the same manifest discovery path as `@api` and `@ui_widget`
- `BundleSchedulerManager` in proc reconciles job tasks after every registry or props change
- no proc restart required for schedule changes
- if Redis is unavailable for `instance`/`system` spans, the tick is skipped and a warning is logged; the job is **not** silently degraded to `process`
- the method runs headlessly — no user session or SSE stream, but normal bundle runtime access is available (`self.bundle_props`, `self.redis`, `self.pg_pool`, secrets)
- async methods are executed directly; sync methods run via `asyncio.to_thread`
- overlapping runs within the same exclusivity scope are prevented (in-process flag for `process`, Redis lock for `instance`/`system`)

For full details on span semantics, cron resolution, and local debug:

- [docs/sdk/bundle/bundle-scheduled-jobs-README.md](bundle-scheduled-jobs-README.md)

### 1.9 `@venv(...)`

Marks a callable to execute in a cached per-bundle subprocess venv.

```python
from kdcube_ai_app.infra.plugin.agentic_loader import venv

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
    roles: tuple[str, ...] = ()
```

### 2.2 `UIWidgetSpec`

```python
@dataclass(frozen=True)
class UIWidgetSpec:
    method_name: str
    alias: str
    icon: dict[str, str]
    user_types: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
```

### 2.3 `OnMessageSpec`

```python
@dataclass(frozen=True)
class OnMessageSpec:
    method_name: str
```

### 2.4 `UIMainSpec`

```python
@dataclass(frozen=True)
class UIMainSpec:
    method_name: str
```

### 2.5 `CronJobSpec`

```python
@dataclass(frozen=True)
class CronJobSpec:
    method_name: str
    alias: str = ""
    cron_expression: str | None = None
    expr_config: str | None = None
    span: str = "system"
```

### 2.6 `BundleInterfaceManifest`

```python
@dataclass(frozen=True)
class BundleInterfaceManifest:
    bundle_id: str
    allowed_roles: tuple[str, ...] = ()
    ui_widgets: tuple[UIWidgetSpec, ...] = ()
    api_endpoints: tuple[APIEndpointSpec, ...] = ()
    ui_main: UIMainSpec | None = None
    on_message: OnMessageSpec | None = None
    scheduled_jobs: tuple[CronJobSpec, ...] = ()
```

`allowed_roles` is populated from the `allowed_roles` argument of
`@agentic_workflow`. Empty tuple means no restriction.

`scheduled_jobs` is populated from all `@cron`-decorated methods on the
entrypoint class, sorted by `alias`.

Discovery helpers currently exposed by the loader:

- `discover_bundle_interface_manifest(...)`
- `resolve_bundle_api_endpoint(...)`
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
- `ui_main`
- `on_message`
- `scheduled_jobs`
  - includes `method_name`
  - includes `alias`
  - includes `cron_expression` (declared value, not runtime-resolved)
  - includes `expr_config`
  - includes `span`

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
  "ui_main": {
    "method_name": "main_ui"
  },
  "on_message": {
    "method_name": "run"
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

### 3.4 Legacy no-bundle-id operations route

```text
POST /api/integrations/bundles/{tenant}/{project}/operations/{alias}
```

This route still exists for backward compatibility.

Current rules:

- it still resolves only declared `@api(..., route="operations")` methods
- `bundle_id` may come from the body
- otherwise the current default bundle is used

### 3.5 Widgets list

```text
GET /api/integrations/bundles/{tenant}/{project}/{bundle_id}/widgets
```

Returns only `@ui_widget(...)` metadata visible to the current user.

### 3.6 Widget fetch

```text
GET /api/integrations/bundles/{tenant}/{project}/{bundle_id}/widgets/{alias}
```

Current rules:

- resolves only `@ui_widget(...)` aliases
- applies role visibility before invocation
- does not use operation routing

### 3.7 Bundle static UI

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

## 4) Role visibility

Role visibility is enforced by the platform integration layer at two levels.

### 4.1 Bundle-level filtering (`allowed_roles` on `@agentic_workflow`)

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

## 5) Authoring rules

Use these rules for new bundles:

1. Decorate every remotely callable method with `@api(...)`.
2. Use `route="operations"` for authenticated/internal bundle operations.
3. Use `route="public"` only for intentionally public endpoints.
4. Decorate every widget method with `@ui_widget(...)`.
5. If a widget method is also called through `/operations/...`, add
   `@api(route="operations", alias="<operation-alias>")` to the same method.
6. Use `@ui_main` when the bundle has a main iframe application.
7. Use `@on_message` on the bundle message handler. The base entrypoints already
   do this on `run()`.
8. Use `@cron(...)` for recurring background work. Prefer `span="system"` (the
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
  `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/agentic_loader.py`
- integrations controller:
  `src/kdcube-ai-app/kdcube_ai_app/apps/chat/proc/rest/integrations/integrations.py`
- base entrypoint widget and `@on_message` usage:
  `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/chatbot/entrypoint.py`

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

### 7.3 Widget plus operation compatibility

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
