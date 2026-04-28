---
id: ks:docs/sdk/bundle/bundle-reserved-platform-properties-README.md
title: "Bundle Reserved Platform Properties"
summary: "Reserved bundle config keys interpreted by the platform: model selection, embeddings, economics, execution runtime, MCP services, and other platform-owned bundle prop paths."
tags: ["sdk", "bundle", "configuration", "runtime", "economics", "exec"]
keywords: ["platform interpreted bundle props", "model selection props", "embedding configuration props", "economics reservation props", "execution runtime props", "mcp service props", "reserved bundle property paths", "platform owned bundle config"]
see_also:
  - ks:docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - ks:docs/configuration/bundles-descriptor-README.md
  - ks:docs/sdk/bundle/bundle-developer-guide-README.md
  - ks:docs/sdk/bundle/bundle-delivery-and-update-README.md
  - ks:docs/exec/distributed-exec-README.md
---
# Bundle Reserved Platform Properties

Start with:

- [bundle-runtime-configuration-and-secrets-README.md](../../configuration/bundle-runtime-configuration-and-secrets-README.md)

Use this page after that when you specifically need the reserved bundle prop
paths interpreted by the platform.

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
| `BUNDLES_DESCRIPTOR_PROVIDER=file` | mounted writable `bundles.yaml` | Redis per tenant/project/bundle | `self.bundle_prop(...)` / `self.bundle_props` |
| `BUNDLES_DESCRIPTOR_PROVIDER=aws-sm` | grouped AWS SM bundle descriptor docs | Redis per tenant/project/bundle | `self.bundle_prop(...)` / `self.bundle_props` |
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
| `economics.reservation_amount_dollars` | `2.0` in `BaseEntrypointWithEconomics.configuration` | `BaseEntrypointWithEconomics` | Per-bundle reservation floor for pre-run economics admission |
| `execution.runtime` | no default | `BaseEntrypoint`, `RuntimeCtx`, exec runtime | Per-bundle exec runtime selection/overrides |
| `exec_runtime` | no default | same as `execution.runtime` | Legacy compatibility alias for `execution.runtime` |
| `mcp.services` | no default | `BaseWorkflow`, MCP runtime/bootstrap | MCP server transport/auth config for tool subsystem |

## Where each reserved property lives

All reserved paths below are still non-secret bundle props.

| Path | Normal read surface | `aws-sm` authority | file-backed authority | Redis role | Notes |
|---|---|---|---|---|---|
| `role_models` | `self.bundle_prop("role_models")` or resolved `Config.role_models` | `<prefix>/bundles/<bundle_id>/descriptor` `config.role_models` | `bundles.yaml -> items[].config.role_models` | cache | platform-owned model-role routing |
| `embedding` | `self.bundle_prop("embedding")` or resolved `Config.embedding` | `<prefix>/bundles/<bundle_id>/descriptor` `config.embedding` | `bundles.yaml -> items[].config.embedding` | cache | platform-owned embedding override |
| `economics.reservation_amount_dollars` | `self.bundle_prop("economics.reservation_amount_dollars")` | `<prefix>/bundles/<bundle_id>/descriptor` `config.economics.reservation_amount_dollars` | `bundles.yaml -> items[].config.economics.reservation_amount_dollars` | cache | used only by economics entrypoints |
| `execution.runtime` | `self.bundle_prop("execution.runtime")` or `resolve_exec_runtime(...)` | `<prefix>/bundles/<bundle_id>/descriptor` `config.execution.runtime` | `bundles.yaml -> items[].config.execution.runtime` | cache | canonical execution runtime path |
| `exec_runtime` | `self.bundle_prop("exec_runtime")` | `<prefix>/bundles/<bundle_id>/descriptor` `config.exec_runtime` | `bundles.yaml -> items[].config.exec_runtime` | cache | legacy alias, prefer `execution.runtime` |
| `mcp.services` | `self.bundle_prop("mcp.services")` | `<prefix>/bundles/<bundle_id>/descriptor` `config.mcp.services` | `bundles.yaml -> items[].config.mcp.services` | cache | MCP transport/auth config |

## Common confusion: reserved vs bundle-owned props

Not every important prop is platform-reserved.

| Prop path | Is it platform-reserved? | Who interprets it | Where it is stored |
|---|---|---|---|
| `role_models` | yes | platform entrypoint/runtime | bundle props authority + Redis cache |
| `embedding` | yes | platform entrypoint/runtime | bundle props authority + Redis cache |
| `mcp.services` | yes | platform runtime | bundle props authority + Redis cache |
| `react.additional_instructions` | no, this is a bundle convention | only bundles/workflows that pass it into `build_react(...)` | same bundle props storage as any other non-secret prop |

So `react.additional_instructions` is still stored exactly like other bundle
props, but the platform does not interpret it globally by itself.

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

Storage summary:

| Question | Answer |
|---|---|
| Where do I set it for a deployment? | `bundles.yaml -> items[].config.role_models` or the live admin props API |
| Where does it live on AWS `aws-sm`? | `<prefix>/bundles/<bundle_id>/descriptor` |
| Where should it live in recommended ECS deployments? | mounted writable `bundles.yaml` on EFS |
| Where does proc read it from at runtime? | Redis effective bundle props cache, with fallback to the authoritative store |

This property is interpreted by `BaseEntrypoint`, not by bundle code directly.

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
| Is it exportable by `kdcube export`? | yes |

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
The current primary use case is selecting distributed Fargate execution per bundle instead of relying only on proc-wide env vars.

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
- any missing keys fall back to proc service env vars where supported
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
- `bundle_prop(...)` reads raw configured values from bundle props
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
- missing keys fall back to the proc service's resolved platform settings export
- raw proc env vars still override those resolved settings when explicitly present

Storage summary:

| Question | Answer |
|---|---|
| Where is it configured? | `config.execution.runtime` in bundle props |
| Where is it persisted on `aws-sm`? | bundle descriptor doc in AWS SM |
| Where is it exported from? | `kdcube export` reconstructs it into `bundles.yaml` |

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

## Exporting reserved properties back to descriptors

Reserved platform properties are not exported separately. They are exported as
part of the bundle descriptor `config`:

```bash
kdcube export \
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
