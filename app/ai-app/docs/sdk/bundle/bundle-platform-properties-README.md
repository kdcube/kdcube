---
id: ks:docs/sdk/bundle/bundle-platform-properties-README.md
title: "Bundle Platform Properties"
summary: "Reserved bundle property paths interpreted by the platform entrypoints and runtimes."
tags: ["sdk", "bundle", "configuration", "runtime", "economics", "exec"]
keywords: ["role_models", "embedding", "economics.reservation_amount_dollars", "execution.runtime", "exec_runtime", "mcp.services"]
see_also:
  - ks:docs/service/configuration/bundle-configuration-README.md
  - ks:docs/sdk/bundle/bundle-dev-README.md
  - ks:docs/sdk/bundle/bundle-ops-README.md
  - ks:docs/exec/distributed-exec-README.md
---
# Bundle Platform Properties

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

If proc runs with `BUNDLES_FORCE_ENV_ON_STARTUP=1`, the descriptor-backed props layer is rebuilt
authoritatively from `bundles.yaml`, so removed keys are deleted from Redis on env reset.

When a reserved property references a secret key, resolution still goes through
`get_secret(...)`. That means the same property works with any configured
runtime secrets provider: `secrets-service`, `aws-sm`, `secrets-file`, or
`in-memory`.

## Reserved property paths

| Path | Default source | Interpreted by | Effect |
|---|---|---|---|
| `role_models` | bundle `configuration` / base configuration | `BaseEntrypoint` | Merged into `Config.role_models` and used by SDK model-role resolution |
| `embedding` | bundle `configuration` / base configuration | `BaseEntrypoint` | Applied via `Config.set_embedding(...)` |
| `economics.reservation_amount_dollars` | `2.0` in `BaseEntrypointWithEconomics.configuration` | `BaseEntrypointWithEconomics` | Per-bundle reservation floor for pre-run economics admission |
| `execution.runtime` | no default | `BaseEntrypoint`, `RuntimeCtx`, exec runtime | Per-bundle exec runtime selection/overrides |
| `exec_runtime` | no default | same as `execution.runtime` | Legacy compatibility alias for `execution.runtime` |
| `mcp.services` | no default | `BaseWorkflow`, MCP runtime/bootstrap | MCP server transport/auth config for tool subsystem |

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
            secret: bundles.react.mcp@2026-03-09.secrets.docs.token
        firecrawl:
          transport: stdio
          command: npx
          args: ["-y", "firecrawl-mcp"]
          env:
            FIRECRAWL_API_KEY: ${secret:bundles.react.mcp@2026-03-09.secrets.firecrawl.api_key}
```

Behavior:
- `mcp.services.mcpServers` and `mcp.services.servers` are both accepted.
- `auth.secret` resolves through `get_secret("dot.path.key")` and is the
  preferred way to supply bearer/api-key/header auth.
- `${secret:...}` references inside stdio `env` blocks are resolved via
  `get_secret()` when the MCP session is created.
- `MCP_SERVICES` env is still accepted only as a legacy/local-dev fallback when
  `mcp.services` is not configured in bundle props.

This property works together with `MCP_TOOL_SPECS` from the bundle
`tools_descriptor.py`:
- `MCP_TOOL_SPECS` controls which MCP tools are exposed
- `mcp.services` controls how those MCP servers are connected and authenticated

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
