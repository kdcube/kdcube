---
id: ks:docs/sdk/bundle/bundle-reference-versatile-README.md
title: "Versatile Reference Bundle"
summary: "Primary full-feature bundle reference for bundle builders: React workflow, economics, props, secrets, custom tools, custom skills, storage, MCP, widget, custom main view, and isolated exec."
tags: ["sdk", "bundle", "reference", "example", "react", "economics", "configuration", "secrets", "mcp", "storage", "widget", "ui", "exec"]
keywords: ["versatile bundle", "reference bundle", "bundle example", "bundle props", "bundle secrets", "get_secret", "custom tools", "custom skills", "preferences", "isolated exec", "AIBundleStorage", "ui.main_view", "custom bundle ui"]
see_also:
  - ks:docs/sdk/bundle/bundle-index-README.md
  - ks:docs/sdk/bundle/bundle-dev-README.md
  - ks:docs/sdk/bundle/bundle-lifecycle-README.md
  - ks:docs/sdk/bundle/bundle-interfaces-README.md
---
# Versatile Reference Bundle

This is the **primary bundle reference** for bundle builders.

Actual bundle root:
`src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36`

Bundle README:
`src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/README.md`

## Why this bundle exists

Older examples split the platform across several bundles:
- one for React
- one for economics
- one for MCP
- one for isolated exec
- one for `ks:`

That is still useful for narrow investigation, but it is a poor starting point for bundle builders and bundle-builder copilots.

`versatile` is the one place that intentionally demonstrates the main bundle authoring surfaces together.

## Feature map

| Feature | Primary file(s) |
| --- | --- |
| Entrypoint + graph bootstrap | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/entrypoint.py` |
| React workflow orchestration | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/orchestrator/workflow.py` |
| Gate agent | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/agents/gate.py` |
| Economics / quotas | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/entrypoint.py` |
| Bundle-local tools | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/tools/preference_tools.py` |
| Bundle-local skill | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/skills/product/preferences/SKILL.md` |
| Bundle props / effective config | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/entrypoint.py`, `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/orchestrator/workflow.py` |
| Bundle secrets via `get_secret(...)` | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/tools/preference_tools.py` |
| Shared bundle storage model | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/preferences_store.py` |
| Storage backend snapshot | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/tools/preference_tools.py` |
| MCP connector surface | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/tools_descriptor.py` |
| Widget surface | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/ui/PreferencesBrowser.tsx` |
| Custom main view (iframe SPA) | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/ui-src/src/App.tsx`, `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/entrypoint.py` |
| Direct isolated exec from bundle code | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/entrypoint.py` |

## Minimal vs versatile

| Concern | Minimal bundle | Versatile bundle |
| --- | --- | --- |
| Entrypoint + compiled graph | required | yes |
| Role models / configuration | required | yes |
| Bundle props / effective config | required for real deployments | yes |
| Tools descriptor | required for tool-aware solver | yes |
| Skills descriptor | required for skill-aware solver | yes |
| Bundle-local tools | optional | yes |
| Bundle-local skills | optional | yes |
| Bundle secrets via `get_secret(...)` | optional | yes |
| Economics | optional | yes |
| Shared bundle storage | optional | yes |
| Storage backend export | optional | yes |
| MCP connectors | optional | yes |
| Widget / operations | optional | yes |
| Custom main view UI | optional | yes |
| Direct isolated exec from bundle code | optional | yes |

## How to study it

Recommended reading order:

1. `entrypoint.py`
2. `orchestrator/workflow.py`
3. `tools_descriptor.py`
4. `skills_descriptor.py`
5. `tools/preference_tools.py`
6. `preferences_store.py`
7. the “Bundle props and secrets” section in the bundle README
8. `ui/PreferencesBrowser.tsx`
9. `ui-src/src/App.tsx`
10. bundle pytest files under:
   `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle`
11. bundle-local tests under:
   `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/tests`

## When to branch to specialized examples

- For bundle-defined `ks:` and namespace resolution:
  use `kdcube.copilot@2026-04-03-19-05`
- For stripped-down isolated-exec scaffolding:
  use `with-isoruntime@2026-02-16-14-00`

Do not start with those examples unless the task is specifically about those narrower surfaces.

## Related validation

Current shared bundle pytest suite:
`src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle`

Bundle-local tests for this reference bundle:
`src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/tests`

Preferred broad validation command:

```bash
PYTHONPATH=app/ai-app/src/kdcube-ai-app \
python -m kdcube_ai_app.apps.chat.sdk.tests.bundle.run_bundle_suite \
  --bundle-path /abs/path/to/versatile@2026-03-31-13-36 -v --tb=short
```

Typical first validation subset:

```bash
BUNDLE_UNDER_TEST=/abs/path/to/versatile@2026-03-31-13-36 \
PYTHONPATH=app/ai-app/src/kdcube-ai-app \
pytest -q \
  app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle/test_initialization.py \
  app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle/test_configuration.py \
  app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle/test_graph.py
```
