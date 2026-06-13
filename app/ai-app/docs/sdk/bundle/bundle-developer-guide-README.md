---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-developer-guide-README.md
title: "Bundle Developer Guide"
summary: "High-level entrypoint for bundle authors: what a bundle is, how tenant/project environments work, which runtime surfaces exist, and which docs to follow for authoring, config, testing, and delivery."
tags: ["sdk", "bundle", "development", "entrypoint", "workflow", "tools", "skills", "configuration", "background-jobs", "data-bus"]
keywords: ["bundle authoring entrypoint", "what a bundle is", "tenant project environment", "runtime surfaces overview", "configuration model overview", "reference bundle path", "shared sdk widget components", "local authoring loop", "bundle documentation map", "data bus handlers", "on_job background jobs"]
updated_at: 2026-05-22
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-subsystem-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/versatile-reference-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-entrypoint-classes-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-properties-and-secrets-lifecycle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-agent-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-platform-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-transports-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/auth-bundle-federated-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-runtime-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-delivery-and-update-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/bus-routing-and-partitioning-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/streams/background-jobs-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/synch-mechanisms/critical-section-README.md
---
# Bundle Developer Guide

This page is the shortest complete path for bundle authors.

Use it together with:

- [build/how-to-configure-and-run-bundle-README.md](build/how-to-configure-and-run-bundle-README.md)
- [build/how-to-assemble-bundle-with-sdk-building-blocks-README.md](build/how-to-assemble-bundle-with-sdk-building-blocks-README.md)
- [bundle-subsystem-integration-README.md](bundle-subsystem-integration-README.md)
- [versatile-reference-bundle-README.md](versatile-reference-bundle-README.md)
- [bundle-entrypoint-classes-README.md](bundle-entrypoint-classes-README.md)
- [bundle-properties-and-secrets-lifecycle-README.md](bundle-properties-and-secrets-lifecycle-README.md)
- [bundle-agent-integration-README.md](bundle-agent-integration-README.md)
- [bundle-platform-integration-README.md](bundle-platform-integration-README.md)
- [bundle-transports-README.md](bundle-transports-README.md)
- [bundle-runtime-README.md](bundle-runtime-README.md)
- [../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../configuration/bundle-runtime-configuration-and-secrets-README.md)

## Start Here

Primary reference bundle:

`src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36`

Read in this order:

1. this guide
2. [build/how-to-assemble-bundle-with-sdk-building-blocks-README.md](build/how-to-assemble-bundle-with-sdk-building-blocks-README.md)
3. the versatile reference doc
4. [bundle-entrypoint-classes-README.md](bundle-entrypoint-classes-README.md)
5. [bundle-properties-and-secrets-lifecycle-README.md](bundle-properties-and-secrets-lifecycle-README.md)
6. `entrypoint.py`
7. `orchestrator/workflow.py`
8. bundle-owned consumer-surface defaults, when present
9. `skills_descriptor.py`
10. [bundle-agent-integration-README.md](bundle-agent-integration-README.md) when the bundle has React tools/skills, MCP, or Claude Code subagents

The assembly map is the fastest way to find reusable Tasks, Email, Telegram,
Delivery, web/rendering/exec tools, storage, widgets, jobs, MCP, and Claude
Code blocks before writing a bundle-local service.

When the selected reusable block is an SDK subsystem, read
[bundle-subsystem-integration-README.md](bundle-subsystem-integration-README.md)
before wiring it. A subsystem integration is a full bundle surface:
entrypoint mixins/decorators, config, widget visibility, UI source, APIs,
tools, skills, event policies, resolvers, storage/schema hooks, transport, and
tests.

Critical Python import rule:

- bundle-local code must use package-relative imports such as
  `from .services.storage import ...`
- do not import bundle-local folders as top-level packages such as `services`,
  `apps`, `tools`, or `resources`
- see [bundle-runtime-README.md#critical-bundle-local-import-rule](bundle-runtime-README.md#critical-bundle-local-import-rule)

If a bundle tool produces user-visible files, read
[bundle-agent-integration-README.md](bundle-agent-integration-README.md) and
[../tools/custom-tools-README.md](../tools/custom-tools-README.md). The bundle
tool should either return `ret.artifact_type: "files"` with `ret.files[]`, or
host the files from trusted tool code through `bundle_tool_context.host_files(...)`.
That helper is available in normal tool execution and in isolated
supervisor/runtime tool execution after the SDK has prepared the tool context
with hosting service, tenant/project/user/conversation/turn scope, conversation
storage, and output directory.

If a widget or service sends durable non-chat domain messages into the bundle,
read [Bus Routing And Partitioning](../../service/comm/bus-routing-and-partitioning-README.md),
[Data Bus](../../service/comm/data-bus-README.md),
[Bundle Federated Auth For Data Bus](auth-bundle-federated-README.md),
[bundle-platform-integration-README.md#110-data_bus_handler](bundle-platform-integration-README.md#110-data_bus_handler),
and [bundle-client-communication-README.md#data-bus-contract](bundle-client-communication-README.md#data-bus-contract).
Use Data Bus for bundle-owned state mutations that need retry, idempotency, or
per-object serialization; use chat ingress only for conversation turns and
authored `external_events[]`.
Use `target.agent_id` for conversation agent lanes; use Data Bus `subject` for
durable handler routing; use `object_ref` for object partitioning.
When the client has bundle-owned upstream identity instead of a platform
browser session, expose a bundle public `federated_token_claim` operation and
use a scoped federated Data Bus token.

## Common Recipe: Choose A Model For One Agent Call

Use this when an API, widget, chat request, or job lets the caller choose a
temporary agent strength such as `lite`, `regular`, or `strong`.

```text
bundle code default
  -> config.role_models
deployment/admin override
  -> bundles.yaml items[].config.role_models
one invocation only
  -> bundle_call_context.role_models
```

The model router resolves the current call in that order:

1. `bundle_call_context.role_models`
2. effective bundle props `config.role_models`
3. platform defaults

The one-call override is visible to nested SDK agents, React, in-process tools,
and isolated Docker/Fargate tool runtimes while the context is bound. It is not
saved to bundle props; re-apply it for later jobs or requests from the job
payload or durable state.

Bundle-level default in code:

```python
class MyEntrypoint(BaseEntrypoint):
    @property
    def configuration(self):
        config = dict(super().configuration)
        role_models = dict(config.get("role_models") or {})
        role_models.setdefault(
            "report.writer",
            {"provider": "anthropic", "model": "claude-sonnet-4-6"},
        )
        config["role_models"] = role_models
        return config
```

External deployment override:

```yaml
items:
  - id: my.bundle@1-0
    config:
      role_models:
        report.writer:
          provider: anthropic
          model: claude-sonnet-4-6
        solver.react.v2.decision.v2.regular:
          provider: anthropic
          model: claude-haiku-4-5
```

Ad hoc override in `@api`, `@mcp`, `@cron`, `@on_reactive_event`, or `@on_job` code:

```python
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import (
    bind_current_bundle_call_context_patch,
    get_current_bundle_call_context,
)

def role_context(role: str, model: str):
    current = get_current_bundle_call_context()
    role_models = dict(current.get("role_models") or {})
    role_models[role] = {"provider": "anthropic", "model": model}
    return {"role_models": role_models}

with bind_current_bundle_call_context_patch(
    role_context("report.writer", "claude-haiku-4-5")
):
    await self.run_my_agent_or_react(...)
```

When the selection comes from ingress rather than bundle code, put the same JSON
object into `ExternalEventPayload.bundle_call_context` before enqueueing the task.
The processor binds that payload field into runtime context before bundle code
runs. See [bundle-agent-integration-README.md](bundle-agent-integration-README.md#model-selection-for-agent-roles)
and [bundle-runtime-README.md](bundle-runtime-README.md#request-scoped-role-model-override).

## Environment Boundary

For bundle authors, `tenant/project` means one isolated environment.

Use a separate `tenant/project` when you need:

- tenant isolation
- a separate lifecycle stage such as `dev`, `staging`, or `prod`

Keep multiple bundles inside the same `tenant/project` when they belong to the
same environment.

So the platform model is:

- one environment = one `tenant/project`
- one environment can host many bundles
- one bundle = one end-to-end application unit inside that environment

## Minimal Bundle Shape

Recommended layout:

```text
my_bundle/
  entrypoint.py
  orchestrator/
    workflow.py
  config/
    bundles.template.yaml # default consumer/tool/UI policy
  skills_descriptor.py
  tools/
  skills/
  ui/
    main/             # optional main UI app source
    widgets/          # optional widget app source
  tests/              # optional bundle-local tests
```

Required in practice:

- `entrypoint.py`
- bundle entrypoint registration
- a compiled graph or equivalent run path

Usually present in real bundles:

- `orchestrator/workflow.py`
- bundle-owned consumer-surface defaults, when code defaults are useful
- `skills_descriptor.py`

Skills are discovered from more than the bundle folder. The active registry
loads core SDK skills, SDK solution skills, and then bundle-local
`CUSTOM_SKILLS_ROOT`. Use `skills_descriptor.py` `AGENTS_CONFIG` to narrow the
catalog for exact consumer ids such as `solver.react.v2.decision.v2.strong` and
`solver.react.v2.decision.v2.regular`. Skills that declare required tools are
also filtered against the active tool catalog, so solution skills disappear
automatically when their tools are not exposed. Use `AGENTS_CONFIG` when policy
needs an explicit allow-list or hard deny.

For skills that should exist only when their tools are available, add
`required: true` to those tool entries in the skill's `tools.yaml`. ReAct checks
these requirements against the active tool catalog, so the skill disappears
from catalog/import/read paths when the corresponding tools are not exposed.

Use `agent_disclosure: hidden` in a skill front matter only when the skill is
operational guidance that may be loaded by exact id/import but must not be
listed by the agent. It is not an authorization boundary; use `AGENTS_CONFIG`
to make a skill unavailable.

## Minimal Entry Pattern

```python
from langgraph.graph import END, START, StateGraph

from kdcube_ai_app.infra.plugin.bundle_loader import bundle_entrypoint, bundle_id
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint
from kdcube_ai_app.infra.service_hub.inventory import BundleState


@bundle_entrypoint(name="my.bundle", version="1.0.0")
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

For the exact decorator contract, route mapping, widget/public endpoints, `@cron`, `@on_job`, and `@venv`, use:

- [bundle-platform-integration-README.md](bundle-platform-integration-README.md)

## Decorator Matrix

| Decorator | Scope | Use it for |
| --- | --- | --- |
| `@bundle_entrypoint(...)` | entrypoint class | normal bundle registration |
| `@bundle_entrypoint_factory(...)` | factory function | custom entrypoint construction when class registration is not enough |
| `@agentic_workflow(...)` | entrypoint class | legacy alias for `@bundle_entrypoint(...)` |
| `@agentic_workflow_factory(...)` | factory function | legacy alias for `@bundle_entrypoint_factory(...)` |
| `@bundle_id(...)` | entrypoint class | code-level bundle identity |
| `@api(...)` | entrypoint method | bundle HTTP operations and public endpoints |
| `@mcp(...)` | entrypoint method | bundle-served MCP endpoints |
| `@ui_widget(...)` | entrypoint method | widget manifest entries |
| `@ui_main` | entrypoint method | bundle main UI entrypoint |
| `@on_reactive_event` | entrypoint method | conversation external-event handler metadata |
| `@cron(...)` | entrypoint method | scheduled background jobs |
| `@on_job` | entrypoint method | ready background jobs claimed by proc from the jobs stream |
| `@venv(...)` | helper function or method | cached subprocess virtualenv execution for selected helpers |

Practical rule:

- most bundles need `@bundle_entrypoint(...)`, `@bundle_id(...)`, and optionally `@api(...)` / `@mcp(...)` / `@ui_widget(...)`
- use `@cron(...)` only for scheduled work
- use `@on_job` when work is submitted to the background job stream and must be executed later by proc
- use `@venv(...)` only for dependency-heavy leaf helpers, not general orchestration
- runtime feature gating uses canonical `enabled.*` switches in bundle props (see "Feature Gating With Canonical `enabled.*`" below)

Background job rule:

- `@cron(...)` decides when something is due; it should stay small and can enqueue ready work
- `@on_job` receives the ready job envelope later and executes it with a fresh runtime context
- `@on_job` is not an HTTP route and not a widget operation
- define at most one `@on_job` method per bundle
- make `@on_job` async
- keep bundle-specific job semantics in the job `work_kind`, `metadata`, and `payload`
- when deriving from SDK mixins, call `await super().handle_job(**kwargs)` first
  and return immediately if it reports `handled=true`; this lets mixins consume
  their own `work_kind` values without adding another `@on_job`
- use [background-jobs-README.md](../../service/streams/background-jobs-README.md) for the platform queue contract

Visibility rule:

- `user_types` on `@api(...)` and `@ui_widget(...)` are threshold-based, not exact-match
- order is:
  - `anonymous < registered < paid < privileged`
- so:
  - `user_types=("registered",)` means registered-or-higher
  - `user_types=("paid",)` means paid-or-higher
  - `user_types=("privileged",)` means privileged only
- use `roles=(...)` for raw external auth roles such as `kdcube:role:super-admin`
- if both `user_types` and `roles` are declared, both checks must pass
- `@mcp(...)` is different:
  - proc does not enforce `user_types`, `roles`, or `public_auth` for MCP
  - the bundle MCP app owns MCP request authentication/authorization

## Configuration Model

The important split is:

- platform/global config and secrets:
  - `get_settings()`
  - `await get_secret("canonical.key")` in async code
- non-secret bundle config:
  - `self.bundle_prop(...)`
  - code defaults -> `bundles.yaml` -> runtime/admin overrides
- bundle secrets:
  - `await get_secret("b:...")` in async code
  - provisioned through `bundles.secrets.yaml` or the configured secrets provider
- user-scoped bundle state:
  - `get_user_prop(...)`
  - `await get_secret("u:...")`
  - never exported back into descriptors
- raw mounted descriptor reads:
  - `get_plain(...)`
  - only when bundle code really must inspect descriptor files directly

Async bundle paths should use the SDK helpers directly so provider IO does not
block the event loop.

Read the exact model here:

- [../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../configuration/bundle-runtime-configuration-and-secrets-README.md)
- [bundle-reserved-platform-properties-README.md](bundle-reserved-platform-properties-README.md)
- [build/how-to-configure-and-run-bundle-README.md](build/how-to-configure-and-run-bundle-README.md)

## Feature Gating With Canonical `enabled.*`

The platform-native way to enable or disable bundle surfaces is the
`enabled.*` section of effective bundle props. The platform derives the
lookup path from decorator metadata, so there is one canonical place per
surface.

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
@bundle_entrypoint(name="ops.dashboard", version="1.0.0")
@bundle_id("ops.dashboard@1.0.0")
class OpsDashboard(BaseEntrypoint):
    @ui_widget(
        alias="dashboard",
        icon={"tailwind": "heroicons-outline:chart-bar"},
        user_types=("privileged",),
    )
    def dashboard_widget(self, **kwargs):
        return ["<div>Dashboard</div>"]
```

```yaml
bundles:
  items:
    - id: "ops.dashboard@1.0.0"
      config:
        enabled:
          bundle: true
          widget:
            dashboard: false
```

Current rules:

- missing section, missing sub-section, or missing key means enabled
- bundle-level `enabled.bundle = false` overrides every resource-level value
- disabled values are:
  - `false`
  - `0`
  - `disable`
  - `disabled`
  - `off`
  - plus boolean `False` and integer `0`

Practical rule:

- keep the switches in bundle props under `bundles.yaml -> config: enabled: ...`
- do not invent parallel ad hoc `if self.bundle_prop(...): return 404` checks unless the logic is more complex than simple exposure gating

### Runtime overrides for decorator defaults

Beyond the on/off `enabled.*` switches, decorator defaults for `user_types`,
`roles`, `transport`, `cron_expression`, and `timezone` can additionally be
overridden from bundle props by declaring a `*_config` dot-path on the
decorator:

| Decorator | Overridable field | Parameter |
| --- | --- | --- |
| `@api(...)`, `@ui_widget(...)` | `user_types` | `user_types_config="..."` |
| `@api(...)`, `@ui_widget(...)` | `roles` | `roles_config="..."` |
| `@mcp(...)` | `transport` | `transport_config="..."` |
| `@cron(...)` | `cron_expression` | `expr_config="..."` |
| `@cron(...)` | `timezone` | `tz_config="..."` |

When the dot-path resolves against effective bundle props, the resolved value
replaces the decorator default at request/scheduling time. Empty list / blank
string / invalid types fall back to the decorator default; missing path also
falls back to the decorator default. The single exception is `@cron`'s
`expr_config`: with `expr_config` declared, a missing path means "do not
schedule" (see `bundle-scheduled-jobs-README.md` for the full cron contract).

## Configuration Access Rules

Use the helper contract, not ad hoc access.

Required rules for bundle code:

- do not use `os.getenv(...)` or `os.environ[...]` for deployment-owned config
  or secrets
- do not call `get_secrets_manager(...).get_secret(...)` directly
- do not open descriptor YAML files through hardcoded paths

Use instead:

- `self.bundle_prop(...)` for effective bundle config
- `await get_secret(...)` for deployment-scoped secrets
- `get_plain(...)` only for raw descriptor inspection
- `get_settings()` for effective typed platform/runtime settings

The only normal exception for raw env access is code that explicitly sits at
the iso-runtime or sandbox boundary and is intentionally driven by process env.

## Git Auth Environment Boundary

If bundle code needs to run git commands, treat git auth as subprocess configuration, not as mutable bundle-local process state.

Rules:

- read git configuration from the managed settings/secrets layer
- if you need a git subprocess env, build a per-call env dict and pass it to `subprocess.run(..., env=env)`
- do not write git auth values back into `os.environ` from bundle code

Important boundary:

- the processor process may already start with inherited `GIT_*` variables
- those inherited variables are shared by design across applications in the same processor
- explicit git helper overrides are local to the subprocess env dict and do not mutate the processor process env

Practical implication:

- do not assume one bundle can safely rewrite processor-level git auth for itself only
- if you need bundle-specific git auth, pass it as an explicit subprocess override instead of mutating global process env

Transport contract:

- git-backed repos may use either HTTPS or SSH remote forms
- if `GIT_HTTP_TOKEN` is configured, the shared helper prefers HTTPS token auth
- when HTTPS token auth is selected, an SSH-style remote may be normalized to HTTPS before git is called
- if SSH transport is intended, configure:
  - `GIT_SSH_KEY_PATH`
  - `GIT_SSH_KNOWN_HOSTS`
  - `GIT_SSH_STRICT_HOST_KEY_CHECKING`

Practical rule:

- HTTPS + PAT is usually the simpler runtime/deployment path
- SSH is supported, but it is a stricter operational contract because key and known-hosts material
  must be present and mounted correctly

## Local Storage Rule

If your bundle needs local filesystem state on the proc instance, use the bundle-storage helper.
The runtime chooses and provides the storage root. Bundle code must create a
subtree under that root; it must not configure or infer its own filesystem,
host, or URI root.

Do not:
- write mutable state next to the bundle source tree
- invent your own repo-relative `.runtime` directory for runtime data
- put `file://...`, host absolute paths, or deployment mount paths in bundle
  props for bundle-local storage

Use:
- `self.bundle_storage_root()` when you want the bundle-scoped shared local root
- `bundle_storage_dir(...)` only when you are outside entrypoint code and therefore do not have `self.bundle_storage_root()`

Example:

```python
def _local_root(self) -> pathlib.Path:
    storage_root = self.bundle_storage_root()
    if storage_root is None:
        raise RuntimeError("Bundle storage root is unavailable.")
    local_root = storage_root / "_my_subsystem"
    local_root.mkdir(parents=True, exist_ok=True)
    return local_root
```

Use this for:
- local repo checkouts
- cached prepared files
- local cron workspace
- working state that should survive across requests on the same instance

This is separate from:
- `BundleArtifactStorage`
- descriptor-backed props/secrets

## Guarded Shared Build Rule

If that local storage contains a derived object that may be created by several
requests or workers, protect it with the platform guarded-build helpers. Common
examples are:

- an indexed knowledge registry behind an MCP endpoint
- a local mirror derived from a git repo or remote API
- a generated search index
- a prepared model/resource bundle

Do not rely on "this usually runs once" assumptions. A local CLI/docker-compose
runtime and a cloud runtime can both have concurrent workers touching the same
mounted bundle-storage path.

Use:

- `kdcube_ai_app.storage.observed_file_locks.observed_file_lock(...)` when the
  bundle owns the signature and readiness checks
- `observed_file_lock_async(...)` in async code that must not block while
  waiting
- the platform UI build configuration for main UI and widgets; the
  `BaseEntrypoint` family already uses the higher-level `bundle_once.py`
  helper for UI outputs

When a `BaseEntrypoint` family subclass overrides `on_bundle_load(...)`, keep
the base hook in the chain:

```python
async def on_bundle_load(self, **kwargs):
    if kwargs.get("comm_context") is not None:
        self.comm_context = kwargs["comm_context"]
    await super().on_bundle_load(**kwargs)
    await self._prepare_my_bundle_state()
```

The base hook refreshes effective bundle props and builds configured static UI.
If it is skipped, preload may appear successful while the widget builds only on
first live access.

Pattern:

1. check `signature + ready` before taking the lock
2. acquire the lock with a bounded wait
3. check `signature + ready` again under the lock
4. build the object
5. verify readiness
6. write the signature last

Detailed helper usage:

- [../../service/synch-mechanisms/critical-section-README.md](../../service/synch-mechanisms/critical-section-README.md)

## Local Development Loop

Recommended local loop:

1. mount your bundle under the configured host bundles root
2. point `bundles.yaml` to the container-visible bundle path such as `/bundles/my.bundle`
3. install or rebuild once:

```bash
kdcube --descriptors-location <dir> --build
```

4. after each code or descriptor change, reload:

```bash
kdcube bundle reload my.bundle@1.0.0 --workdir <runtime-workdir>
```

What `kdcube bundle reload` does:

- reapplies the bundle registry from descriptor/env state
- rebuilds descriptor-backed bundle props from `bundles.yaml`
- evicts the target bundle from proc bundle-loader caches
- drops matching dynamic bundle modules from `sys.modules`
- invalidates static widget entrypoint load state for that bundle
- broadcasts `changed_bundle_ids` so other proc workers evict the same bundle
- lets new requests import updated code/config without a full platform rebuild

Use this when you changed:

- bundle code
- `bundles.yaml`
- `bundles.secrets.yaml`

The CLI and Bundle Admin call the same reload authority. For the exact local
flow, endpoints, and expected diagnostics, see:

- [../../service/cicd/cli-README.md#bundle-reload-flow](../../service/cicd/cli-README.md#bundle-reload-flow)

If your bundle uses local bundle storage, `kdcube bundle reload` does not wipe that storage automatically.
Design your subsystem roots intentionally:
- stable bundle root for all bundle-managed local data
- explicit `_subsystem` roots for mutable local working state
- optional bundle-owned subdirectories for rebuildable caches if you intentionally need them

If you need the deployment-side details and registry behavior, use:

- [bundle-delivery-and-update-README.md](bundle-delivery-and-update-README.md)
- [build/how-to-configure-and-run-bundle-README.md](build/how-to-configure-and-run-bundle-README.md)

## Reference Bundle Scope

The reference bundle is `versatile@2026-03-31-13-36`.

It intentionally demonstrates:

- a normal React bundle entrypoint
- economics-enabled entrypoint
- bundle-local tools
- bundle-local skills
- props and secrets
- widget operations
- custom main UI
- MCP connector descriptors
- public endpoint example
- direct isolated-exec operation

It does **not** currently demonstrate every bundle surface. In particular:

- it is not the reference for `@cron`
- it is not the reference for `@venv`

For those, use the dedicated docs:

- [bundle-scheduled-jobs-README.md](bundle-scheduled-jobs-README.md)
- [bundle-venv-README.md](bundle-venv-README.md)

For bundle widgets and main UI integration, use:

- [bundle-widget-integration-README.md](bundle-widget-integration-README.md)

When a widget needs a platform-owned UI capability inside the same React tree,
use the shared-source pattern from that doc instead of duplicating panels in the
bundle. Current reusable examples are:

- User Memory: `sdk://context/memory/ui/widget/memories`
- Telegram admin/channels: `sdk://integrations/telegram/ui/widget.telegram`

The bundle still owns route aliases, role policy, Telegram identity mapping,
and operation callers. Shared widget components are UI source, not backend
authorization.

## Validation

Shared SDK bundle suite:

```bash
PYTHONPATH=app/ai-app/src/kdcube-ai-app \
python -m kdcube_ai_app.apps.chat.sdk.tests.bundle.run_bundle_suite \
  --bundle-path /abs/path/to/bundle
```

Use `<bundle>/tests` for bundle-specific behavior that is not part of the generic SDK contract.
