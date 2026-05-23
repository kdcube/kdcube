---
id: ks:docs/sdk/bundle/versatile-reference-bundle-README.md
title: "Versatile Reference Bundle"
summary: "Reference implementation guide for the versatile bundle: file layout, exposed surfaces, configuration patterns, and where to mine working bundle patterns."
tags: ["sdk", "bundle", "reference", "example", "react", "configuration", "widget", "api", "mcp"]
keywords: ["reference implementation bundle", "working bundle patterns", "file layout example", "configuration surface example", "widget api mcp example", "versatile bundle reference"]
see_also:
  - ks:docs/sdk/bundle/bundle-developer-guide-README.md
  - ks:docs/sdk/bundle/bundle-agent-integration-README.md
  - ks:docs/sdk/bundle/bundle-entrypoint-classes-README.md
  - ks:docs/sdk/bundle/bundle-runtime-README.md
  - ks:docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - ks:docs/sdk/bundle/bundle-platform-integration-README.md
---
# Versatile Reference Bundle

Reference bundle root:

`src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36`

This is the bundle to study first.

## What It Demonstrates

| Capability | Where to look |
| --- | --- |
| Entry point and graph bootstrap | `entrypoint.py` |
| React workflow orchestration | `orchestrator/workflow.py` |
| Economics + memory entrypoint | `entrypoint.py` via `BaseEntrypointWithEconomicsAndMemory` |
| Bundle-local tools | `tools/preference_tools.py` |
| Bundle-local skills | `skills_descriptor.py` and bundle `skills/` tree |
| Effective bundle props | `entrypoint.py`, `orchestrator/workflow.py` |
| Bundle secrets; prefer `get_secret("b:...")` in new async code | `tools/preference_tools.py` |
| Bundle storage backend usage | `preferences_store.py` |
| Widget + widget operations | `entrypoint.py`, `ui/PreferencesBrowser.tsx` |
| Iframe main view | `ui/main/src/App.tsx`, `entrypoint.py` |
| Public bundle endpoint | `entrypoint.py:preferences_public_info` |
| Direct isolated exec from bundle code | `entrypoint.py:preferences_exec_report` |
| MCP connector declarations | `tools_descriptor.py` |

## What It Does Not Demonstrate

Do not use `versatile` as the reference for:

- `@cron`
- `@venv`
- Claude Code subagents with generated MCP workspace config

Those are documented separately:

- [bundle-scheduled-jobs-README.md](bundle-scheduled-jobs-README.md)
- [bundle-venv-README.md](bundle-venv-README.md)
- [bundle-agent-integration-README.md](bundle-agent-integration-README.md)

## Study Order

1. `entrypoint.py`
2. `orchestrator/workflow.py`
3. `tools_descriptor.py`
4. `skills_descriptor.py`
5. `tools/preference_tools.py`
6. `preferences_store.py`
7. `ui/PreferencesBrowser.tsx`
8. `ui/main/src/App.tsx`
9. bundle-local tests under `tests/`

## Config Surfaces Used by This Bundle

Actual non-secret props demonstrated here:

- `preferences.auto_capture`
- `execution.runtime`
- `mcp.services`

Actual bundle secret demonstrated here:

- `preferences.snapshot_hmac_key`

Read the exact rules here:

- [../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../configuration/bundle-runtime-configuration-and-secrets-README.md)
- [bundle-reserved-platform-properties-README.md](bundle-reserved-platform-properties-README.md)

## API and UI Surface Actually Present

This bundle currently demonstrates:

- authenticated operations endpoints via `@api(..., route="operations")`
- widget discovery via `@ui_widget(...)`
- a public endpoint via `@api(..., route="public", public_auth="none")`
- a custom main UI
- the single-`@on_job` dispatch pattern for SDK mixins: call
  `await super().handle_job(**kwargs)` first, then process bundle-owned
  `work_kind` values only when the superclass returns `handled=false`

Use the exact decorator and route contract here:

- [bundle-platform-integration-README.md](bundle-platform-integration-README.md)

## Validation

Shared SDK bundle suite:

```bash
PYTHONPATH=app/ai-app/src/kdcube-ai-app \
python -m kdcube_ai_app.apps.chat.sdk.tests.bundle.run_bundle_suite \
  --bundle-path /abs/path/to/versatile@2026-03-31-13-36
```

Bundle-local tests:

```bash
PYTHONPATH=app/ai-app/src/kdcube-ai-app \
pytest -q app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/tests
```
