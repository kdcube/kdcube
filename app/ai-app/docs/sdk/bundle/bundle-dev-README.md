---
id: ks:docs/sdk/bundle/bundle-dev-README.md
title: "Bundle Dev"
summary: "Compact bundle authoring guide: minimal structure, runtime contract, configuration model, reference bundle, and local reload loop."
tags: ["sdk", "bundle", "development", "entrypoint", "workflow", "tools", "skills", "configuration"]
keywords: ["bundle authoring", "agentic_workflow", "bundle_id", "tools_descriptor", "skills_descriptor", "bundle_prop", "get_secret", "bundle reload", "versatile"]
see_also:
  - ks:docs/sdk/bundle/bundle-reference-versatile-README.md
  - ks:docs/sdk/bundle/bundle-platform-integration-README.md
  - ks:docs/sdk/bundle/bundle-runtime-README.md
  - ks:docs/sdk/bundle/bundle-props-secrets-README.md
  - ks:docs/sdk/bundle/bundle-ops-README.md
---
# Bundle Developer Guide

This page is the shortest complete path for bundle authors.

Use it together with:

- [bundle-reference-versatile-README.md](bundle-reference-versatile-README.md)
- [bundle-platform-integration-README.md](bundle-platform-integration-README.md)
- [bundle-runtime-README.md](bundle-runtime-README.md)
- [bundle-props-secrets-README.md](bundle-props-secrets-README.md)

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
| `@ui_widget(...)` | entrypoint method | widget manifest entries |
| `@ui_main` | entrypoint method | main iframe UI entrypoint |
| `@on_message` | entrypoint method | message-handler metadata |
| `@cron(...)` | entrypoint method | scheduled background jobs |
| `@venv(...)` | helper function or method | cached subprocess virtualenv execution for selected helpers |

Practical rule:

- most bundles need `@agentic_workflow(...)`, `@bundle_id(...)`, and optionally `@api(...)` / `@ui_widget(...)`
- use `@cron(...)` only for scheduled work
- use `@venv(...)` only for dependency-heavy leaf helpers, not general orchestration

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

## Configuration Model

The important split is:

- non-secret bundle config:
  - `self.bundle_prop(...)`
  - code defaults -> `bundles.yaml` -> runtime/admin overrides
- bundle secrets:
  - `get_secret("b:...")`
  - provisioned through `bundles.secrets.yaml` or the configured secrets provider
- raw mounted descriptor reads:
  - `get_plain(...)`
  - only when bundle code really must inspect descriptor files directly

Read the exact model here:

- [bundle-props-secrets-README.md](bundle-props-secrets-README.md)
- [bundle-platform-properties-README.md](bundle-platform-properties-README.md)

## Local Storage Rule

If your bundle needs local filesystem state on the proc instance, use the bundle-storage helper.

Do not:
- write mutable state next to the bundle source tree
- invent your own repo-relative `.runtime` directory for runtime data

Use:
- `self.bundle_storage_root()` when you want the bundle-scoped shared local root
- `bundle_storage_dir(..., version=None) / "_subsystem"` when you want a mutable unversioned local workspace for a subsystem

Example:

```python
from kdcube_ai_app.infra.plugin.bundle_storage import bundle_storage_dir

def _local_root(self) -> pathlib.Path:
    actor = getattr(self.comm_context, "actor", None)
    bundle_spec = getattr(self.config, "ai_bundle_spec", None)
    tenant = getattr(actor, "tenant_id", None) or self.settings.TENANT
    project = getattr(actor, "project_id", None) or self.settings.PROJECT
    bundle_id = getattr(bundle_spec, "id", None) or "my.bundle@1.0.0"
    return bundle_storage_dir(
        bundle_id=str(bundle_id),
        version=None,
        tenant=str(tenant),
        project=str(project),
        ensure=True,
    ) / "_my_subsystem"
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
kdcube --workdir <runtime-workdir> --bundle-reload my.bundle@1.0.0
```

What `--bundle-reload` does:

- reapplies the bundle registry from descriptor/env state
- rebuilds descriptor-backed bundle props from `bundles.yaml`
- clears in-process proc bundle caches so new requests use the updated code/config

Use this when you changed:

- bundle code
- `bundles.yaml`
- `bundles.secrets.yaml`

If your bundle uses local bundle storage, `--bundle-reload` does not wipe that storage automatically.
Design your subsystem roots intentionally:
- versioned root for rebuildable version-tied data
- unversioned `_subsystem` root for mutable local working state

If you need the deployment-side details and registry behavior, use:

- [bundle-ops-README.md](bundle-ops-README.md)

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
