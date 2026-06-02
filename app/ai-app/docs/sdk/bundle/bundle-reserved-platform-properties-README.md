---
id: ks:docs/sdk/bundle/bundle-reserved-platform-properties-README.md
title: "Bundle Reserved Platform Properties"
summary: "Reserved bundle config keys interpreted by the platform: model selection, embeddings, user memory, economics, execution runtime, MCP services, and other platform-owned bundle prop paths."
tags: ["sdk", "bundle", "configuration", "runtime", "economics", "exec", "memory", "pdf"]
keywords: ["platform interpreted bundle props", "model selection props", "embedding configuration props", "user memory configuration props", "economics reservation props", "execution runtime props", "mcp service props", "reserved bundle property paths", "platform owned bundle config", "pdf footer", "pdf_footer", "write_pdf footer"]
see_also:
  - ks:docs/sdk/bundle/bundle-properties-and-secrets-lifecycle-README.md
  - ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - ks:docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - ks:docs/configuration/bundles-descriptor-README.md
  - ks:docs/sdk/bundle/bundle-developer-guide-README.md
  - ks:docs/sdk/bundle/bundle-agent-integration-README.md
  - ks:docs/sdk/bundle/bundle-delivery-and-update-README.md
  - ks:docs/exec/distributed-exec-README.md
---
# Bundle Reserved Platform Properties

Start with:

- [bundle-properties-and-secrets-lifecycle-README.md](bundle-properties-and-secrets-lifecycle-README.md)
- [bundle-runtime-configuration-and-secrets-README.md](../../configuration/bundle-runtime-configuration-and-secrets-README.md)

Use this page after those when you specifically need the reserved bundle prop
paths interpreted by the platform.

For the operational CLI flow around those props, use
[how-to-configure-and-run-bundle-README.md#canonical-cli-flow-schemas](build/how-to-configure-and-run-bundle-README.md#canonical-cli-flow-schemas).
Reserved props are still ordinary deployment-scoped bundle props for
`bundle config apply`, `bundle reload`, and `export`.

Most bundle props are bundle-defined and opaque to the platform.  
Some property paths are **reserved** and interpreted by the platform entrypoints or runtimes.

These reserved properties can still be overridden through:
- bundle code defaults
- `bundles.yaml`
- runtime/admin props overrides

Effective precedence remains:

1. code defaults
2. `bundles.yaml`
3. runtime/admin overrides

Important:
- the bundle delivery id used in integrations routes is not a bundle prop
- bundle-specific clients should call
  `/api/integrations/bundles/{tenant}/{project}/{bundle_id}/operations/{operation}`
- the legacy omitted-bundle route still exists for generic platform callers and
  resolves the current default bundle id when `bundle_id` is not supplied

If proc runs with `BUNDLES_FORCE_ENV_ON_STARTUP=1` and the authoritative bundle
descriptor provider is file-backed, the props layer is rebuilt authoritatively
from `bundles.yaml`, so removed keys are deleted from Redis on env reset.

When a reserved property references a secret key, resolution still goes through
`get_secret(...)`. That means the same property works with any configured
runtime secrets provider: `secrets-service`, `aws-sm`, `secrets-file`, or
`in-memory`.

The storage rule is:
- these paths are still just bundle props
- the platform interprets them specially
- where they are stored depends on the bundle-props deployment mode, not simply on "AWS or not"

## Storage by deployment mode

| Mode | Authoritative store for reserved bundle props | Runtime cache | What bundle code reads |
|---|---|---|---|
| `BUNDLES_DESCRIPTOR_PROVIDER=file` | mounted writable `bundles.yaml` | Redis per tenant/project/bundle | `self.bundle_prop(...)` |
| `BUNDLES_DESCRIPTOR_PROVIDER=aws-sm` | grouped AWS SM bundle descriptor docs | Redis per tenant/project/bundle | `self.bundle_prop(...)` |
| no provider / code-only fallback | bundle code defaults only | none | `self.bundle_prop(...)` from defaults only |

The Redis cache key format is:

```text
kdcube:config:bundles:props:{tenant}:{project}:{bundle_id}
```

In `aws-sm`, the grouped bundle descriptor docs are:

| Document | Contents |
|---|---|
| `<prefix>/bundles-meta` | bundle registry inventory |
| `<prefix>/bundles/<bundle_id>/descriptor` | bundle registry entry and non-secret `config` |
| `<prefix>/bundles/<bundle_id>/secrets` | bundle-level secrets only |

## Reserved property paths

| Path | Default source | Interpreted by | Effect |
|---|---|---|---|
| `role_models` | bundle `configuration` / base configuration | `BaseEntrypoint` | Merged into `Config.role_models` and used by SDK model-role resolution |
| `embedding` | bundle `configuration` / base configuration | `BaseEntrypoint` | Applied via `Config.set_embedding(...)` |
| `memory` | disabled in memory mixin defaults | `MemoryEntrypointMixin`, memory tools/widget, ReAct announce integration | User Memory hotset, tools, widget, reconciliation, and snapshots for memory-enabled bundles |
| `ui.widgets.memories` | disabled in memory mixin defaults | `MemoryEntrypointMixin`, widget builder/loader | Enables and optionally overrides the built Memory widget UI |
| `economics.reservation_amount_dollars` | `2.0` in `BaseEntrypointWithEconomics.configuration` | `BaseEntrypointWithEconomics` | Per-bundle reservation floor for pre-run economics admission |
| `execution.runtime` | no default | `BaseEntrypoint`, `RuntimeCtx`, exec runtime | Per-bundle exec runtime selection/overrides |
| `exec_runtime` | no default | same as `execution.runtime` | Legacy compatibility alias for `execution.runtime` |
| `react.line_numbers_mode` | `ai.react.line_numbers_mode`, default `lines` | ReAct runtime | Rendered text preview line numbering mode: `disabled`, `lines`, or `sparsed` |
| `mcp.services` | no default | `BaseWorkflow`, MCP runtime/bootstrap | MCP server transport/auth config for tool subsystem |
| `pdf_footer` | no default (footer omitted) | `rendering_tools.write_pdf` | Plain-text string appended as a styled footer to every PDF generated by the bundle |
| `events.record.persist` | platform assembly default | `BaseEntrypoint._persist_steps_artifacts` | Per-bundle override for persist recording: which event types are saved in `conv.artifacts.events` |
| `events.record.telemetry` | platform assembly default | `BaseEntrypoint._build_telemetry_selector` | Per-bundle override for telemetry recording: which event types are shipped to the telemetry sink |

## Where each reserved property lives

All reserved paths below are still non-secret bundle props.

| Path | Normal read surface | `aws-sm` authority | file-backed authority | Redis role | Notes |
|---|---|---|---|---|---|
| `role_models` | `self.bundle_prop("role_models")` or resolved `Config.role_models` | `<prefix>/bundles/<bundle_id>/descriptor` `config.role_models` | `bundles.yaml -> items[].config.role_models` | cache | platform-owned model-role routing |
| `embedding` | `self.bundle_prop("embedding")` or resolved `Config.embedding` | `<prefix>/bundles/<bundle_id>/descriptor` `config.embedding` | `bundles.yaml -> items[].config.embedding` | cache | platform-owned embedding override |
| `memory` | `self.bundle_prop("memory")` through memory-enabled entrypoints | `<prefix>/bundles/<bundle_id>/descriptor` `config.memory` | `bundles.yaml -> items[].config.memory` | cache | User Memory subsystem config; interpreted only by bundles that use the memory mixin |
| `ui.widgets.memories` | `self.bundle_prop("ui.widgets.memories")` through widget loader | `<prefix>/bundles/<bundle_id>/descriptor` `config.ui.widgets.memories` | `bundles.yaml -> items[].config.ui.widgets.memories` | cache | Memory widget enable/build overrides |
| `economics.reservation_amount_dollars` | `self.bundle_prop("economics.reservation_amount_dollars")` | `<prefix>/bundles/<bundle_id>/descriptor` `config.economics.reservation_amount_dollars` | `bundles.yaml -> items[].config.economics.reservation_amount_dollars` | cache | used only by economics entrypoints |
| `execution.runtime` | `self.bundle_prop("execution.runtime")` or `resolve_exec_runtime(...)` | `<prefix>/bundles/<bundle_id>/descriptor` `config.execution.runtime` | `bundles.yaml -> items[].config.execution.runtime` | cache | canonical execution runtime path |
| `exec_runtime` | `self.bundle_prop("exec_runtime")` | `<prefix>/bundles/<bundle_id>/descriptor` `config.exec_runtime` | `bundles.yaml -> items[].config.exec_runtime` | cache | legacy alias, prefer `execution.runtime` |
| `react.line_numbers_mode` | effective ReAct runtime context; global default `ai.react.line_numbers_mode` / `AI_REACT_LINE_NUMBERS_MODE`, default `lines` | `<prefix>/bundles/<bundle_id>/descriptor` `config.react.line_numbers_mode` | `bundles.yaml -> items[].config.react.line_numbers_mode` | cache | per-bundle override for rendered text preview line numbering: `disabled`, `lines`, or `sparsed` |
| `mcp.services` | `self.bundle_prop("mcp.services")` | `<prefix>/bundles/<bundle_id>/descriptor` `config.mcp.services` | `bundles.yaml -> items[].config.mcp.services` | cache | MCP transport/auth config |
| `pdf_footer` | read via `get_plain("b:bundles.items.{bundle_id}.pdf_footer")` in `rendering_tools` | `<prefix>/bundles/<bundle_id>/descriptor` `config.pdf_footer` | `bundles.yaml -> items[].config.pdf_footer` | cache | plain-text PDF footer string; omitted if unset |
| `events.record.persist` | `self.bundle_prop("events.record.persist")` via `BaseEntrypoint._events_record_config("persist")` | `<prefix>/bundles/<bundle_id>/descriptor` `config.events.record.persist` | `bundles.yaml -> items[].config.events.record.persist` | cache | persist recording config; fields merged on top of assembly default |
| `events.record.telemetry` | `self.bundle_prop("events.record.telemetry")` via `BaseEntrypoint._events_record_config("telemetry")` | `<prefix>/bundles/<bundle_id>/descriptor` `config.events.record.telemetry` | `bundles.yaml -> items[].config.events.record.telemetry` | cache | telemetry recording config; fields merged on top of assembly default |

## Common confusion: reserved vs bundle-owned props

Not every important prop is platform-reserved.

| Prop path | Is it platform-reserved? | Who interprets it | Where it is stored |
|---|---|---|---|
| `role_models` | yes | platform entrypoint/runtime | bundle props authority + Redis cache |
| `embedding` | yes | platform entrypoint/runtime | bundle props authority + Redis cache |
| `memory` | yes for memory-enabled entrypoints | memory mixin, memory widget/tools, optional ReAct integration | bundle props authority + Redis cache |
| `ui.widgets.memories` | yes for memory-enabled entrypoints | memory widget loader/build flow | bundle props authority + Redis cache |
| `react.line_numbers_mode` | yes for ReAct-enabled entrypoints | ReAct timeline/read preview renderer | bundle props authority + Redis cache; falls back to global `ai.react.line_numbers_mode`, default `lines` |
| `mcp.services` | yes | platform runtime | bundle props authority + Redis cache |
| `pdf_footer` | yes | `rendering_tools.write_pdf` (all PDF formats) | bundle props authority + Redis cache |
| `events.record.persist` | yes | `BaseEntrypoint` (`_persist_steps_artifacts`) | bundle props authority + Redis cache; falls back to `assembly.yaml -> events.record.persist` |
| `events.record.telemetry` | yes | `BaseEntrypoint` (`_build_telemetry_selector`) | bundle props authority + Redis cache; falls back to `assembly.yaml -> events.record.telemetry` |
| `react.additional_instructions` | no, this is a bundle convention | only bundles/workflows that pass it into `build_react(...)` | same bundle props storage as any other non-secret prop |

So `react.additional_instructions` is still stored exactly like other bundle
props, but the platform does not interpret it globally by itself.

`react.line_numbers_mode` is different: the platform ReAct runtime reads it
when constructing `RuntimeCtx`. Use it only for presentation/cost tuning of
model-visible text previews. The default is `lines`. It does not change file
contents.

## `role_models`

`role_models` is the primary platform-level bundle override for model selection.

Example:

```yaml
config:
  role_models:
    solver.react.v2.decision.v2.strong:
      provider: anthropic
      model: claude-sonnet-4-6
```

Behavior:
- bundle code can set defaults with `setdefault(...)`
- `bundles.yaml` can override or add role entries
- runtime/admin props can override them again

Bundle implementation rule:
- if a bundle subclasses `BaseEntrypoint` and overrides `configuration_defaults()`,
  it must merge its bundle-specific defaults over `super().configuration_defaults()`
  instead of returning a replacement dict
- base defaults include platform-owned roles used by shared SDK tools, such as
  `tool.sources.filter.by.content` and
  `tool.sources.filter.by.content.and.segment`
- `bundles.yaml` and runtime/admin props are partial patches over the effective
  code defaults; they should not need to restate every platform role

Recommended pattern:

```python
def configuration_defaults(self) -> Dict[str, Any]:
    bundle_defaults = {
        "role_models": {
            "solver.react.v2.decision.v2.strong": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
            },
        },
        "my_bundle": {"enabled": True},
    }
    return self._deep_merge_props(super().configuration_defaults(), bundle_defaults)
```

Avoid:

```python
def configuration_defaults(self) -> Dict[str, Any]:
    return {"role_models": {"solver.react.v2.decision.v2.strong": {...}}}
```

That replacement form drops platform defaults unless the bundle repeats them.
The common failure mode is that shared tools fall back to generic model routing
instead of their reserved roles.

Storage summary:

| Question | Answer |
|---|---|
| Where do I set it for a deployment? | `bundles.yaml -> items[].config.role_models` or the live admin props API |
| Where does it live on AWS `aws-sm`? | `<prefix>/bundles/<bundle_id>/descriptor` |
| Where should it live in recommended ECS deployments? | mounted writable `bundles.yaml` on EFS |
| Where does proc read it from at runtime? | Descriptor/admin bundle props from Redis cache or authoritative store, then merged with code defaults by the entrypoint/runtime path |

This property is interpreted by `BaseEntrypoint`, not by bundle code directly.

### Three scopes for role model selection

Use the smallest scope that matches the desired lifetime.

```text
bundle source default
configuration / configuration_defaults()
        |
        v
deployment override
bundles.yaml -> items[].config.role_models
or live bundle props
        |
        v
one-call overlay
bundle_call_context.role_models
        |
        v
SDK ModelRouter(role)
```

Bundle-level code default:

```python
@property
def configuration(self) -> Dict[str, Any]:
    config = dict(super().configuration)
    role_models = dict(config.get("role_models") or {})
    role_models.setdefault(
        "my.named.agent",
        {"provider": "anthropic", "model": "claude-sonnet-4-6"},
    )
    config["role_models"] = role_models
    return config
```

External bundle props override:

```yaml
items:
  - id: my.bundle@1-0
    config:
      role_models:
        my.named.agent:
          provider: anthropic
          model: claude-sonnet-4-6
        solver.react.v2.decision.v2.regular:
          provider: anthropic
          model: claude-haiku-4-5
```

Request-scoped overlay through `bundle_call_context`:

```python
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import (
    bind_current_bundle_call_context_patch,
    get_current_bundle_call_context,
)

current = get_current_bundle_call_context()
role_models = dict(current.get("role_models") or {})
role_models["my.named.agent"] = {
    "provider": "anthropic",
    "model": "claude-haiku-4-5",
}

with bind_current_bundle_call_context_patch({"role_models": role_models}):
    await run_named_agent(...)
```

The request overlay is appropriate inside `@api`, `@mcp`, `@cron`,
`@on_message`, and `@on_job` handlers when the current request or job payload
chooses a temporary agent strength.

Precedence:

1. `bundle_call_context.role_models` for the currently bound invocation
2. effective bundle props `role_models`
3. platform defaults

The request-scoped override is portable into nested SDK agents, React,
in-process tools, and isolated runtimes because `bundle_call_context` is
snapshotted through `RUNTIME_GLOBALS_JSON`. It is not persisted back to
`bundles.yaml`, Redis, or admin props. If a bundle wants the same override to
apply to a later background job or request, it must store the selected mode in
its own durable state or job payload and re-apply it in that later invocation.

For full examples across API, MCP, cron, chat, and background-job surfaces, see
[Bundle Agent Integration](bundle-agent-integration-README.md#model-selection-for-agent-roles).

## `embedding`

`embedding` overrides the embedding provider/model for the bundle.

Example:

```yaml
config:
  embedding:
    provider: openai
    model: text-embedding-3-small
```

Behavior:
- applied by `BaseEntrypoint`
- stored in effective bundle props like any other prop
- affects SDK embedding calls that use the bundle’s resolved `Config`

Storage summary:

| Question | Answer |
|---|---|
| Where do I set it? | `config.embedding` in bundle props |
| Is it in secrets? | no |
| Is it in PostgreSQL `user_bundle_props`? | no |
| Is it exportable by `kdcube config export`? | yes |

## `memory`

`memory` config enables the User Memory subsystem for bundles that derive from
the memory entrypoint mixin, for example `BaseEntrypointWithMemory` or
`BaseEntrypointWithEconomicsAndMemory`.

This is not ordinary bundle-owned config. The memory mixin interprets it and
wires the user-facing memory widget, optional ReAct announce hotset, optional
memory tools, reconciliation jobs, and snapshots.

Example:

```yaml
config:
  memory:
    enabled: true
    announce:
      enabled: true
      limit: 6
      scope_filter: current_bundle # current_bundle | all_user_memories
      timeout_seconds: 1.5
    tools:
      enabled: true
      allow_write: false # keep read-only unless durable agent writes are policy-approved
      default_scope_filter: current_bundle
      embedding_enabled: true
      embedding_timeout_seconds: 3.0
    widget:
      enabled: true
      allow_write: true
      default_scope_filter: current_bundle
      allow_all_user_memories: true
      ensure_schema: true
      limit: 30
      max_memory_chars: 4000
      max_context_chars: 4000
      max_terms: 32
      max_term_chars: 64
    reconciliation:
      enabled: true
      max_candidates: 40
      max_jobs: 20
      storage_prefix: memory/reconciliation/jobs
      timeout_seconds: 45.0
    snapshots:
      enabled: true
      max_memories: 1000
      max_snapshots: 30
      storage_prefix: memory/snapshots
  ui:
    widgets:
      memories:
        enabled: true
      versatile_webapp:
        shared_sources:
          memory_widget:
            src_folder: sdk://context/memory/ui/widget/memories
            target: _shared/memory-widget
```

Behavior:

- `memory.enabled` gates the subsystem for the bundle.
- `memory.announce` controls the read-only hotset projected into ReAct
  announce context.
- `memory.tools` controls memory search/read/write tools; keep
  `allow_write: false` until the bundle has an explicit durable-memory write
  policy.
- `memory.widget` controls the user-owned CRUD widget and input hardening
  limits.
- `memory.reconciliation` and `memory.snapshots` control maintenance jobs,
  preview/apply flows, exports, and restores.
- `ui.widgets.memories.enabled` exposes the widget route; the default
  source folder and build command come from the memory mixin unless explicitly
  overridden.
- `ui.widgets.<alias>.shared_sources` can materialize reusable SDK UI
  source into a bundle widget build workspace. This is how a bundle widget can
  mount the built-in memory widget as a direct React component without an
  iframe and without depending on local monorepo paths.

Storage summary:

| Question | Answer |
|---|---|
| Where do I set it? | `config.memory` and `config.ui.widgets.memories` in bundle props |
| Is it in secrets? | no |
| Is it user-scoped memory data? | no, it is deployment-scoped subsystem config |
| Where is user memory data stored? | the project PostgreSQL memory tables, scoped by tenant/project/user and optionally bundle |
| Is it exportable by `kdcube config export`? | yes, as bundle config; not as user memory data |

## `economics.reservation_amount_dollars`

This property is reserved by `BaseEntrypointWithEconomics`.

Default:

```yaml
economics:
  reservation_amount_dollars: 2.0
```

Purpose:
- defines the per-turn reservation floor for economics admission
- affects pre-run budget reservation logic for economics-enabled bundles

Example override:

```yaml
config:
  economics:
    reservation_amount_dollars: 0.5
```

If a bundle does not use `BaseEntrypointWithEconomics`, this key is just data unless the bundle chooses to interpret it.

Storage summary:

| Question | Answer |
|---|---|
| Where does it live? | bundle descriptor `config.economics.reservation_amount_dollars` |
| Is it deployment-scoped or user-scoped? | deployment-scoped |
| Does it ever go to `user_bundle_props`? | no |

## `execution.runtime`

This property is reserved for bundle-level execution runtime control.

It is copied into runtime context and then propagated into exec tool execution.
The current primary use case is selecting Docker/Fargate execution and
overriding ISO runtime limits per bundle exec call instead of relying only on
proc-wide assembly defaults.

Example:

```yaml
config:
  execution:
    runtime:
      mode: fargate
      enabled: true
      region: eu-west-1
      cluster: arn:aws:ecs:eu-west-1:100258542545:cluster/kdcube-staging-cluster
      task_definition: kdcube-staging-exec
      container_name: exec
      subnets:
        - subnet-xxxx
        - subnet-yyyy
      security_groups:
        - sg-xxxx
      assign_public_ip: DISABLED
      max_file_bytes: 100m
      max_exec_workspace_delta_bytes: 250m
      max_workspace_bytes: 250m
      workspace_monitor_interval_s: 0.5
```

Bundles can also declare multiple supported runtime profiles in bundle props and
either choose one as default or select one at call time from workflow code:

```yaml
config:
  execution:
    runtime:
      default_profile: fargate
      profiles:
        docker:
          mode: docker
          image: py-code-exec:latest
          network_mode: host
          cpus: "1.5"
          memory: "2g"
          extra_args:
            - --pids-limit
            - "256"
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

Current behavior:
- `mode: fargate` routes exec tools to the external Fargate runtime
- `mode: docker` routes exec tools to the Docker runtime
- remaining keys are used as per-bundle runtime overrides
- any missing ISO runtime limit keys fall back to `assembly.yaml` under `platform.services.proc.exec`
- `profiles` lets a bundle declare multiple supported runtimes for itself
- `default_profile` / `profile` / `selected_profile` picks the default resolved runtime
- if a bundle defines profiles but no default, bundle code can choose explicitly at call time
- the canonical runtime config is exposed as `RuntimeCtx.exec_runtime`
- profile definitions stay nested inside that same `RuntimeCtx.exec_runtime` object

Bundle code can also read a concrete configured profile value directly by
dot-separated path:

```python
mode = self.bundle_prop("execution.runtime.profiles.fargate_default.mode")
cluster = self.bundle_prop("execution.runtime.profiles.fargate_default.cluster")
```

And resolve that same named profile for execution:

```python
exec_runtime = self.resolve_exec_runtime(profile="fargate_default")
```

The split is:
- `self.bundle_prop(...)` reads effective configured values from bundle props
- `resolve_exec_runtime(...)` resolves the named profile into the effective
  runtime config handed to the execution subsystem

Supported keys and defaults:

| Key | Applies to | Default / fallback | Notes |
|---|---|---|---|
| `mode` | docker, fargate | no default | Typical values: `docker`, `fargate` |
| `image` | docker | `PY_CODE_EXEC_IMAGE` -> `py-code-exec:latest` | Docker image used for `docker run` |
| `network_mode` | docker | `PY_CODE_EXEC_NETWORK_MODE` -> `host` | Passed as `--network` |
| `cpus` | docker | unset | Passed as `--cpus <value>` |
| `memory` | docker | unset | Passed as `--memory <value>` |
| `extra_args` | docker | unset | Extra raw `docker run` args; list or shell-style string |
| `max_file_bytes` | docker, fargate, local | `platform.services.proc.exec.max_file_bytes` -> `100m` | Max single generated file per exec call |
| `max_exec_workspace_delta_bytes` | docker, fargate, local | `platform.services.proc.exec.max_exec_workspace_delta_bytes` -> `250m` | Max net-new monitored writable bytes per exec call |
| `max_workspace_bytes` | docker, fargate, local | `platform.services.proc.exec.max_workspace_bytes` -> unset | Optional max total bytes currently present in the active workspace writable roots before finalization/offload |
| `workspace_monitor_interval_s` | docker, fargate, local | `platform.services.proc.exec.workspace_monitor_interval_s` -> `0.5` | Workspace quota polling interval |
| `descriptor_payload_scope` | docker, fargate | `all` | `active_bundle` filters only `bundles.yaml` and `bundles.secrets.yaml` to the caller bundle before packaging descriptor payloads for the trusted supervisor |
| `enabled` | fargate | `FARGATE_EXEC_ENABLED` -> disabled | Enables distributed exec |
| `region` | fargate | `AWS_REGION` / `AWS_DEFAULT_REGION` | ECS client region |
| `cluster` | fargate | `FARGATE_CLUSTER` | ECS cluster ARN/name |
| `task_definition` | fargate | `FARGATE_TASK_DEFINITION` | ECS task definition |
| `container_name` | fargate | `FARGATE_CONTAINER_NAME` | Target container inside task |
| `subnets` | fargate | `FARGATE_SUBNETS` | List or comma-separated string |
| `security_groups` | fargate | `FARGATE_SECURITY_GROUPS` | List or comma-separated string |
| `assign_public_ip` | fargate | `FARGATE_ASSIGN_PUBLIC_IP` -> `DISABLED` | `ENABLED` or `DISABLED` |
| `launch_type` | fargate | `FARGATE_LAUNCH_TYPE` -> `FARGATE` | ECS launch type |
| `platform_version` | fargate | `FARGATE_PLATFORM_VERSION` | Optional ECS platform version |
| `profiles` | meta | unset | Map of named bundle-supported runtime profiles |
| `default_profile` | meta | unset | Default selected profile |
| `profile` | meta | unset | Alternative selector alias |
| `selected_profile` | meta | unset | Alternative selector alias |

Docker notes:
- `extra_args` is appended after built-in runtime flags such as `--network`
- use it for advanced flags not yet modeled explicitly
- prefer explicit keys like `image`, `network_mode`, `cpus`, and `memory` when possible

`exec_runtime` is accepted as a legacy alias, but `execution.runtime` is the canonical path.

Fallback semantics:
- bundle runtime props win for keys they define
- missing ISO runtime limit keys fall back to proc settings from `assembly.yaml`
- the isolated runtime receives those values as internal `EXEC_*` env transport;
  configure descriptors and bundle props, not those env names
- descriptor payloads are full by default because the supervisor is platform
  trusted; `descriptor_payload_scope: active_bundle` narrows only bundle
  descriptor payloads to the active bundle and leaves `assembly.yaml`,
  `gateway.yaml`, and global `secrets.yaml` unchanged

Storage summary:

| Question | Answer |
|---|---|
| Where is it configured? | `config.execution.runtime` in bundle props |
| Where is it persisted on `aws-sm`? | bundle descriptor doc in AWS SM |
| Where is it exported from? | `kdcube config export` reconstructs it into `bundles.yaml` |

## `mcp.services`

This property is reserved for MCP connector configuration.

It is read by the workflow/runtime tool-subsystem path and propagated into
isolated exec, so MCP tool resolution does not depend on a process-global
`MCP_SERVICES` env var.

Preferred example:

```yaml
config:
  mcp:
    services:
      mcpServers:
        docs:
          transport: http
          url: https://mcp.internal.example.com
          auth:
            type: bearer
            secret: b:docs.token
        firecrawl:
          transport: stdio
          command: npx
          args: ["-y", "firecrawl-mcp"]
          env:
            FIRECRAWL_API_KEY: ${secret:b:firecrawl.api_key}
```

Behavior:
- `mcp.services.mcpServers` and `mcp.services.servers` are both accepted.
- `auth.secret` resolves through `get_secret("dot.path.key")` and is the
  preferred way to supply bearer/api-key/header auth.
- `${secret:...}` references inside stdio `env` blocks are resolved via
  `get_secret()` when the MCP session is created.
- for bundle-local MCP config, prefer:
  - `b:...` for current bundle secrets
  - no prefix / `a:...` for platform/global secrets
- fully qualified canonical keys such as `bundles.<bundle_id>.secrets...` are
  still accepted when you need the explicit form
- `MCP_SERVICES` env is still accepted only as a legacy/local-dev fallback when
  `mcp.services` is not configured in bundle props.

This property works together with `MCP_TOOL_SPECS` from the bundle
`tools_descriptor.py`:
- `MCP_TOOL_SPECS` controls which MCP tools are exposed
- `mcp.services` controls how those MCP servers are connected and authenticated

Storage summary:

| Question | Answer |
|---|---|
| Where do I set MCP server URLs/auth? | `config.mcp.services` in bundle props |
| Where should auth secrets go? | bundle or platform secrets via `get_secret(...)`, not inside plain props |
| What gets exported by CLI? | the non-secret `mcp.services` config, not the resolved secret values |

## `events.record`

`events.record.persist` and `events.record.telemetry` are the two per-bundle
event recording overrides. Platform defaults come from `assembly.yaml ->
events.record.*`. Bundle-level fields are merged on top of assembly defaults
field-by-field: a bundle can override only `enabled`, only `selector`, or both.
The `selector` list is replaced as a whole when present — lists are not
concatenated.

### `events.record.persist`

Controls which comm events are saved to the `conv.artifacts.events` artifact at
end of turn. The artifact is read back on conversation fetch and hydrated into
the UI (usage, timings, tool call steps).

```yaml
config:
  events:
    record:
      persist:
        enabled: true
        selector:
          - "accounting.usage"
          - "chat.turn.summary"
          - "chat.conversation.accepted"
          - "chat.conversation.turn.completed"
          - "react.tool.call"
```

Constraints:

- only types emitted through the **bundle entrypoint comm** (`self.comm`) can
  appear; events emitted by the processor comm after the bundle handler returns
  (e.g. `chat.complete`) are never in the buffer when the artifact is persisted
- the recording starts in `pre_run_hook`; events emitted before that point
  (e.g. before `super().pre_run_hook()` is called in a subclass) will not be
  captured by this rule, even if they were already in the buffer via the
  telemetry rule
- `enabled: false` produces no artifact for the turn

Implementation: `BaseEntrypoint._persist_steps_artifacts` / `_start_steps_recording`.

### `events.record.telemetry`

Controls which comm events are shipped to the external telemetry sink
(`telemetry_sink.endpoint_url` + secret token) as `kdcube.telemetry.v1` events.

```yaml
config:
  events:
    record:
      telemetry:
        enabled: true
        selector:
          - "accounting.usage"
          - "react.tool.call"
```

If `enabled: false` or no sink is configured, recording is skipped entirely and
the buffer is cleared. The selector only restricts what is sent to the sink; it
does not affect the persist artifact unless those types are also absent from the
persist selector.

Implementation: `BaseEntrypoint._build_telemetry_selector` /
`_telemetry_events_enabled`.

### Resolution order

```
bundle props (events.record.<section>)
  └─ assembly.yaml (events.record.<section>)
       └─ code defaults in BaseEntrypoint
```

The bundle prop is read via `_events_record_config(section)` which calls
`self.get_prop_path(self.bundle_props, f"events.record.{section}")`.

## `pdf_footer`

This property controls the footer text appended to every PDF generated by the
bundle through `write_pdf`.

It is read at render time from the bundle config — not from bundle code — so it
can be set per-bundle in `bundles.yaml` without touching the bundle source.

Example:

```yaml
config:
  pdf_footer: "Made by Acme Corp · acme.com · Confidential"
```

Behavior:
- applies to all three `write_pdf` content formats: `markdown`, `html`, and `mermaid`
- the text is HTML-escaped before injection, so plain text is always safe
- rendered as a styled `<div>` at the bottom of the page: small grey text with a
  top border, centered
- if `pdf_footer` is absent or empty, no footer element is added
- works in both the React agent loop (bundle ID from request context / env vars)
  and ISO runtime (bundle ID from `RUNTIME_GLOBALS_JSON.EXEC_CONTEXT.bundle_id`)

Implementation:
- `rendering_tools._pdf_footer_text()` calls
  `get_plain(f"b:bundles.items.{bundle_id}.pdf_footer")`
- `get_plain` with the `b:` prefix reads from the mounted `bundles.yaml`
  descriptor (or Redis cache in production)
- bundle ID is resolved from `RUNTIME_GLOBALS_JSON` (iso runtime) with fallback
  to `_resolve_current_bundle_id()` (request context / `KDCUBE_BUNDLE_ID` env)

Storage summary:

| Question | Answer |
|---|---|
| Where do I set it? | `config.pdf_footer` in bundle props |
| Is it in secrets? | no — it is plain display text |
| Does it affect DOCX or PPTX output? | no — only `write_pdf` |
| Is it exportable by `kdcube config export`? | yes, as part of bundle descriptor config |

## Exporting reserved properties back to descriptors

Reserved platform properties are not exported separately. They are exported as
part of the bundle descriptor `config`:

```bash
kdcube config export \
  --tenant <tenant> \
  --project <project> \
  --aws-region <region> \
  --out-dir /tmp/kdcube-export
```

This reconstructs:
- `bundles.yaml`
- `bundles.secrets.yaml`

So if you changed `role_models`, `embedding`, `execution.runtime`, or
`mcp.services` through the live admin props API in an `aws-sm` deployment, this
CLI export is the way to get those effective deployment-scoped values back into
descriptor files.

### Sourcing Fargate values for `execution.runtime`

For ECS/Fargate deployments, the infrastructure values come from Terraform state.
Run these from the Terraform directory of your ECS deployment (the directory that
contains `main.tf` and your `.tfvars` files — wherever you ran `terraform apply`):

```bash
terraform output -raw ecs_cluster_name                      # → kdcube-staging-cluster
aws sts get-caller-identity --query Account --output text   # → <account_id>
terraform output -json private_subnet_ids                   # → ["subnet-<id1>","subnet-<id2>"]
terraform output -raw ecs_tasks_sg_id                       # → sg-<group_id>
```

| Field | How to get | Staging example |
|---|---|---|
| `region` | `aws.deployment.yaml → aws_region` | `eu-west-1` |
| `cluster` | `arn:aws:ecs:<region>:<account_id>:cluster/<ecs_cluster_name>` | `arn:aws:ecs:eu-west-1:<account_id>:cluster/kdcube-staging-cluster` |
| `task_definition` | `<name_prefix>-exec` (no revision) | `kdcube-staging-exec` |
| `container_name` | always `exec` | `exec` |
| `subnets` | `terraform output -json private_subnet_ids` | `subnet-<id1>`, `subnet-<id2>` |
| `security_groups` | `terraform output -raw ecs_tasks_sg_id` | `sg-<group_id>` |
| `assign_public_ip` | always `DISABLED` (private subnets + NAT) | `DISABLED` |

Full example for a staging deployment:

```yaml
config:
  execution:
    runtime:
      default_profile: docker_builtin
      profiles:
        docker_builtin:
          mode: docker
          image: py-code-exec:latest
          network_mode: host
        fargate_default:
          mode: fargate
          enabled: true
          region: eu-west-1
          cluster: arn:aws:ecs:eu-west-1:<account_id>:cluster/kdcube-staging-cluster
          task_definition: kdcube-staging-exec
          container_name: exec
          subnets:
            - subnet-<id1>   # terraform output -json private_subnet_ids
            - subnet-<id2>
          security_groups:
            - sg-<group_id>  # terraform output -raw ecs_tasks_sg_id
          assign_public_ip: DISABLED
```

## Bundle author guidance

Use reserved properties when the behavior is platform-owned:
- model routing
- embedding defaults
- economics reservation behavior
- exec runtime routing
- PDF footer text (`pdf_footer`)

Use bundle-specific properties for everything else:
- prompts
- knowledge roots
- repo references
- feature flags
- workflow thresholds

If you define bundle defaults in code, preserve external overrides:

```python
@property
def configuration(self) -> Dict[str, Any]:
    config = dict(super().configuration)
    role_models = dict(config.get("role_models") or {})
    role_models.setdefault("solver.react.v2.decision.v2.strong", {
        "provider": "anthropic",
        "model": "claude-sonnet-4-5-20250929",
    })
    config["role_models"] = role_models
    return config
```

## References

- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/chatbot/entrypoint.py`
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/chatbot/entrypoint_with_economic.py`
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/react/v2/proto.py`
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/runtime/execution.py`
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/runtime/external/fargate.py`
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tools/rendering_tools.py` — `_pdf_footer_text`, `_inject_footer_html`, `_inject_footer_md`
