---
id: ks:docs/sdk/bundle/bundle-index-README.md
title: "Bundle Index"
summary: "Bundle developer index for authoring docs, the primary reference bundle, and the current bundle pytest suite."
tags: ["sdk", "bundle", "docs", "index", "developer", "reference", "tests"]
keywords: ["bundle docs index", "bundle developer guide", "reference bundle", "versatile bundle", "bundle pytest suite", "bundle builder"]
see_also:
  - ks:docs/sdk/bundle/bundle-dev-README.md
  - ks:docs/sdk/bundle/bundle-reference-versatile-README.md
  - ks:docs/sdk/bundle/bundle-lifecycle-README.md
  - ks:docs/sdk/bundle/bundle-runtime-README.md
  - ks:docs/sdk/bundle/bundle-config-README.md
  - ks:docs/sdk/bundle/bundle-platform-integration-README.md
---
# Bundle Docs Index

Use this as the **docs start point** when building, repairing, or reviewing a bundle.

## Bundle-builder quickstart

1. Start with [[docs/sdk/bundle/bundle-dev-README.md](bundle-dev-README.md)](bundle-dev-README.md).
2. Read the primary full-feature reference:
   [[docs/sdk/bundle/bundle-reference-versatile-README.md](bundle-reference-versatile-README.md)](bundle-reference-versatile-README.md).
3. Read the runtime execution model:
   [[docs/sdk/bundle/bundle-runtime-README.md](bundle-runtime-README.md)](bundle-runtime-README.md).
4. Read the declarative platform integration design:
   [[docs/sdk/bundle/bundle-platform-integration-README.md](bundle-platform-integration-README.md)](bundle-platform-integration-README.md).
5. Read the current shared bundle pytest suite under:
   `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle`
6. Only after that, branch into narrower specialized examples if the task is specifically about `ks:` or direct isolated-exec internals.

## Bundle-builder map

| Concern | Read first | Then inspect |
| --- | --- | --- |
| Minimal bundle contract | [[docs/sdk/bundle/bundle-dev-README.md](bundle-dev-README.md)](bundle-dev-README.md) | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/entrypoint.py`, `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/orchestrator/workflow.py` |
| Bundle lifecycle + instance model | [[docs/sdk/bundle/bundle-lifecycle-README.md](bundle-lifecycle-README.md)](bundle-lifecycle-README.md) | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/entrypoint.py` |
| Runtime surfaces across REST, SSE, tools, and iso runtime | [[docs/sdk/bundle/bundle-runtime-README.md](bundle-runtime-README.md)](bundle-runtime-README.md) | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/proc/rest/integrations/integrations.py`, `src/kdcube-ai-app/kdcube_ai_app/apps/chat/proc/web_app.py`, `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/runtime/tool_subsystem.py`, `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/runtime/bootstrap.py` |
| Config, secrets, reserved platform props | [[docs/sdk/bundle/bundle-config-README.md](bundle-config-README.md)](bundle-config-README.md), [[docs/sdk/bundle/bundle-platform-properties-README.md](bundle-platform-properties-README.md)](bundle-platform-properties-README.md) | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/entrypoint.py` |
| Custom tools + MCP | [[docs/sdk/tools/custom-tools-README.md](../tools/custom-tools-README.md)](../tools/custom-tools-README.md) | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/tools_descriptor.py`, `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/tools/preference_tools.py` |
| Custom skills | [[docs/sdk/skills/custom-skills-README.md](../skills/custom-skills-README.md)](../skills/custom-skills-README.md) | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/skills_descriptor.py`, `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/skills/product/preferences/SKILL.md` |
| Storage, cache, bundle state | [[docs/sdk/bundle/bundle-storage-cache-README.md](bundle-storage-cache-README.md)](bundle-storage-cache-README.md) | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/preferences_store.py` |
| Widgets + operations | [[docs/sdk/bundle/bundle-platform-integration-README.md](bundle-platform-integration-README.md)](bundle-platform-integration-README.md), [[docs/sdk/bundle/bundle-interfaces-README.md](bundle-interfaces-README.md)](bundle-interfaces-README.md) | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/ui/PreferencesBrowser.tsx`, `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/entrypoint.py` |
| Direct isolated exec from bundle code | [[docs/sdk/bundle/bundle-reference-versatile-README.md](bundle-reference-versatile-README.md)](bundle-reference-versatile-README.md) | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/entrypoint.py` |
| Bundle-defined `ks:` knowledge space | [[docs/sdk/bundle/bundle-knowledge-space-README.md](bundle-knowledge-space-README.md)](bundle-knowledge-space-README.md) | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/react.doc@2026-03-02-22-10` |
| Validation | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle` | read the exact relevant pytest files, then run the smallest relevant subset |

## Primary reference bundle

- Primary all-features reference bundle:
  [[docs/sdk/bundle/bundle-reference-versatile-README.md](bundle-reference-versatile-README.md)](bundle-reference-versatile-README.md)
- Actual bundle root:
  `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36`
- Bundle README:
  `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/README.md`

Why this is the primary reference:
- it shows the normal React bundle skeleton
- it keeps the economics-enabled entrypoint
- it includes bundle-local tools and skills
- it demonstrates shared local bundle storage
- it demonstrates storage-backend export through `AIBundleStorage`
- it includes MCP connector examples
- it includes a TSX widget
- it includes a direct isolated-exec operation

## Core bundle docs

- Developer guide:
  [[docs/sdk/bundle/bundle-dev-README.md](bundle-dev-README.md)](bundle-dev-README.md)
- Lifecycle, instance model, and storage surfaces:
  [[docs/sdk/bundle/bundle-lifecycle-README.md](bundle-lifecycle-README.md)](bundle-lifecycle-README.md)
- Runtime surfaces across entrypoints, tools, and isolation:
  [[docs/sdk/bundle/bundle-runtime-README.md](bundle-runtime-README.md)](bundle-runtime-README.md)
- Bundle configuration and secrets:
  [[docs/sdk/bundle/bundle-config-README.md](bundle-config-README.md)](bundle-config-README.md)
- Reserved platform property paths:
  [[docs/sdk/bundle/bundle-platform-properties-README.md](bundle-platform-properties-README.md)](bundle-platform-properties-README.md)
- Shared local storage, backend storage, and cache:
  [[docs/sdk/bundle/bundle-storage-cache-README.md](bundle-storage-cache-README.md)](bundle-storage-cache-README.md)
- Bundle interfaces (widgets + operations + streaming):
  [[docs/sdk/bundle/bundle-interfaces-README.md](bundle-interfaces-README.md)](bundle-interfaces-README.md)
- Declarative platform integration design (`@api`, `@ui_widget`, `@ui_main`, `@on_message`):
  [[docs/sdk/bundle/bundle-platform-integration-README.md](bundle-platform-integration-README.md)](bundle-platform-integration-README.md)
- Bundle knowledge space (`ks:`):
  [[docs/sdk/bundle/bundle-knowledge-space-README.md](bundle-knowledge-space-README.md)](bundle-knowledge-space-README.md)
- Bundle outbound firewall:
  [[docs/sdk/bundle/bundle-firewall-README.md](bundle-firewall-README.md)](bundle-firewall-README.md)
- Bundle-local tools:
  [[docs/sdk/tools/custom-tools-README.md](../tools/custom-tools-README.md)](../tools/custom-tools-README.md)
- Bundle-local skills:
  [[docs/sdk/skills/custom-skills-README.md](../skills/custom-skills-README.md)](../skills/custom-skills-README.md)

## Validation

- Current shared bundle pytest suite root:
  `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle`
- Start with the smallest relevant subset:
  - `test_initialization.py`
  - `test_configuration.py`
  - `test_graph.py`
- Then add feature-specific subsets:
  - `test_custom_tools_*.py`
  - `test_custom_skills_*.py`
  - `test_storage*.py`

## Specialized focused examples

These are useful when the primary reference bundle is too broad for the question:

- `react.doc@2026-03-02-22-10`
  - use when the question is specifically about bundle-defined `ks:` knowledge space, local docs indexing, or namespace resolution
  - root:
    `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/react.doc@2026-03-02-22-10`
- `with-isoruntime@2026-02-16-14-00`
  - use when the question is specifically about direct isolated exec and scenario-driven runtime diagnostics
  - root:
    `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/with-isoruntime@2026-02-16-14-00`
- older focused examples still exist under `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles`, but they are not the default starting point for bundle authoring anymore

## Ops after authoring

Most bundle builders do **not** need ops docs first.

Read this only when you are wiring delivery or deployment:
- [[docs/sdk/bundle/bundle-ops-README.md](bundle-ops-README.md)](bundle-ops-README.md)
- Assembly descriptors, delivery descriptors, and similar ops-only material are intentionally not part of the normal bundle-authoring start path.
