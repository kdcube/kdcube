---
id: ks:docs/sdk/bundle/bundle-developer-guide-README.md
title: "Bundle Developer Guide"
summary: "High-level entrypoint for bundle authors: what a bundle is, how tenant/project environments work, which runtime surfaces exist, and which docs to follow for authoring, config, testing, and delivery."
tags: ["sdk", "bundle", "development", "entrypoint", "workflow", "tools", "skills", "configuration"]
keywords: ["bundle authoring entrypoint", "what a bundle is", "tenant project environment", "runtime surfaces overview", "configuration model overview", "reference bundle path", "local authoring loop", "bundle documentation map"]
see_also:
  - ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - ks:docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - ks:docs/sdk/bundle/versatile-reference-bundle-README.md
  - ks:docs/sdk/bundle/bundle-platform-integration-README.md
  - ks:docs/sdk/bundle/bundle-transports-README.md
  - ks:docs/sdk/bundle/bundle-runtime-README.md
  - ks:docs/sdk/bundle/bundle-delivery-and-update-README.md
---
# Bundle Developer Guide

This page is the shortest complete path for bundle authors.

Use it together with:

- [build/how-to-configure-and-run-bundle-README.md](build/how-to-configure-and-run-bundle-README.md)
- [versatile-reference-bundle-README.md](versatile-reference-bundle-README.md)
- [bundle-platform-integration-README.md](bundle-platform-integration-README.md)
- [bundle-transports-README.md](bundle-transports-README.md)
- [bundle-runtime-README.md](bundle-runtime-README.md)
- [../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../configuration/bundle-runtime-configuration-and-secrets-README.md)

## Start Here

Primary reference bundle:

`src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36`

Read in this order:

1. this guide
2. the versatile reference doc
3. `entrypoint.py`
4. `orchestrator/workflow.py`
5. `tools_descriptor.py`
6. `skills_descriptor.py`

## Environment Boundary

For bundle authors, `tenant/project` means one isolated environment.

Use a separate `tenant/project` when you need:

- customer isolation
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
  tools_descriptor.py
  skills_descriptor.py
  tools/
  skills/
  ui/                 # optional widget TSX
  ui-src/             # optional iframe app
  tests/              # optional bundle-local tests
```

Required in practice:

- `entrypoint.py`
- bundle entrypoint registration
- a compiled graph or equivalent run path

Usually present in real bundles:

- `orchestrator/workflow.py`
- `tools_descriptor.py`
- `skills_descriptor.py`

## Minimal Entry Pattern

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

For the exact decorator contract, route mapping, widget/public endpoints, `@cron`, and `@venv`, use:

- [bundle-platform-integration-README.md](bundle-platform-integration-README.md)

## Decorator Matrix

| Decorator | Scope | Use it for |
| --- | --- | --- |
| `@agentic_workflow(...)` | entrypoint class | normal bundle registration |
| `@agentic_workflow_factory(...)` | factory function | custom workflow construction when class registration is not enough |
| `@bundle_id(...)` | entrypoint class | code-level bundle identity |
| `@api(...)` | entrypoint method | bundle HTTP operations and public endpoints |
| `@mcp(...)` | entrypoint method | bundle-served MCP endpoints |
| `@ui_widget(...)` | entrypoint method | widget manifest entries |
| `@ui_main` | entrypoint method | main iframe UI entrypoint |
| `@on_message` | entrypoint method | message-handler metadata |
| `@cron(...)` | entrypoint method | scheduled background jobs |
| `@venv(...)` | helper function or method | cached subprocess virtualenv execution for selected helpers |

Practical rule:

- most bundles need `@agentic_workflow(...)`, `@bundle_id(...)`, and optionally `@api(...)` / `@mcp(...)` / `@ui_widget(...)`
- use `@cron(...)` only for scheduled work
- use `@venv(...)` only for dependency-heavy leaf helpers, not general orchestration
- `enabled_config` is available on bundle/workflow, API, MCP, widget, and cron decorators when you need runtime feature gating from bundle props

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
  - `get_secret("canonical.key")`
- non-secret bundle config:
  - `self.bundle_prop(...)`
  - code defaults -> `bundles.yaml` -> runtime/admin overrides
- bundle secrets:
  - `get_secret("b:...")`
  - provisioned through `bundles.secrets.yaml` or the configured secrets provider
- user-scoped bundle state:
  - `get_user_prop(...)`
  - `get_user_secret(...)`
  - never exported back into descriptors
- raw mounted descriptor reads:
  - `get_plain(...)`
  - only when bundle code really must inspect descriptor files directly

Read the exact model here:

- [../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../configuration/bundle-runtime-configuration-and-secrets-README.md)
- [bundle-reserved-platform-properties-README.md](bundle-reserved-platform-properties-README.md)
- [build/how-to-configure-and-run-bundle-README.md](build/how-to-configure-and-run-bundle-README.md)

## Feature Gating With `enabled_config`

`enabled_config` is the standard way to enable or disable bundle surfaces from
bundle props instead of branching the code manually.

Use it on:

- `@agentic_workflow(...)` to gate the whole bundle
- `@api(...)` to gate one operation
- `@mcp(...)` to gate one MCP endpoint
- `@ui_widget(...)` to gate one widget
- `@cron(...)` to gate one scheduled job

Example:

```python
@agentic_workflow(
    name="ops.dashboard",
    version="1.0.0",
    enabled_config="features.dashboard.enabled",
)
@bundle_id("ops.dashboard@1.0.0")
class OpsDashboard(BaseEntrypoint):
    @ui_widget(
        alias="dashboard",
        icon={"tailwind": "heroicons-outline:chart-bar"},
        user_types=("privileged",),
        enabled_config="features.dashboard.widget_enabled",
    )
    def dashboard_widget(self, **kwargs):
        return ["<div>Dashboard</div>"]
```

```yaml
bundles:
  items:
    - id: "ops.dashboard@1.0.0"
      config:
        features:
          dashboard:
            enabled: true
            widget_enabled: false
```

Current rules:

- the path is resolved against effective bundle props
- missing path means enabled
- disabled values are:
  - `false`
  - `0`
  - `disable`
  - `disabled`
  - `off`
  - plus boolean `False` and integer `0`
- bundle-level disable wins over resource-level enable

Practical rule:

- use `enabled_config` for deployment/runtime feature flags
- keep the actual flag value in `bundles.yaml -> config:`
- do not invent parallel ad hoc `if self.bundle_prop(...): return 404` checks unless the logic is more complex than simple exposure gating

## Configuration Access Rules

Use the helper contract, not ad hoc access.

Required rules for bundle code:

- do not use `os.getenv(...)` or `os.environ[...]` for deployment-owned config
  or secrets
- do not call `get_secrets_manager(...).get_secret(...)` directly
- do not open descriptor YAML files through hardcoded paths

Use instead:

- `self.bundle_prop(...)` for effective bundle config
- `get_secret(...)` for deployment-scoped secrets
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

Do not:
- write mutable state next to the bundle source tree
- invent your own repo-relative `.runtime` directory for runtime data

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
- `AIBundleStorage`
- descriptor-backed props/secrets

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
kdcube reload my.bundle@1.0.0 --workdir <runtime-workdir>
```

What `kdcube reload` does:

- reapplies the bundle registry from descriptor/env state
- rebuilds descriptor-backed bundle props from `bundles.yaml`
- clears in-process proc bundle caches so new requests use the updated code/config

Use this when you changed:

- bundle code
- `bundles.yaml`
- `bundles.secrets.yaml`

If your bundle uses local bundle storage, `kdcube reload` does not wipe that storage automatically.
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
- custom iframe main view
- MCP connector descriptors
- public endpoint example
- direct isolated-exec operation

It does **not** currently demonstrate every bundle surface. In particular:

- it is not the reference for `@cron`
- it is not the reference for `@venv`

For those, use the dedicated docs:

- [bundle-scheduled-jobs-README.md](bundle-scheduled-jobs-README.md)
- [bundle-venv-README.md](bundle-venv-README.md)

For bundle widgets and iframe app integration, use:

- [bundle-widget-integration-README.md](bundle-widget-integration-README.md)

## Validation

Shared SDK bundle suite:

```bash
PYTHONPATH=app/ai-app/src/kdcube-ai-app \
python -m kdcube_ai_app.apps.chat.sdk.tests.bundle.run_bundle_suite \
  --bundle-path /abs/path/to/bundle
```

Use `<bundle>/tests` for bundle-specific behavior that is not part of the generic SDK contract.
