---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md
title: "How To Navigate KDCube Bundle Docs"
summary: "Navigation guide for KDCube bundle docs: use docs/search surfaces first when available, resolve repo links, choose the next document by task, and avoid reading or editing unrelated docs."
tags: ["sdk", "bundle", "docs", "navigation", "tier-1", "authoring"]
keywords:
  [
    "bundle docs navigation",
    "kdcube docs mcp",
    "docs search first",
    "ks docs links",
    "repo links",
    "tier 1 reading order",
    "which doc to read next",
    "bundle docs map",
  ]
updated_at: 2026-06-11
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/how-to-integrate-with-kdcube-apps-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-index-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-test-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-avoid-common-bundle-integration-failures-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-write-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-subsystem-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-properties-and-secrets-lifecycle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-bootstrap-local-bundle-runtime-as-coding-agent-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-release-bundle-content-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-agent-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-widget-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-entrypoint-classes-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/versatile-reference-bundle-README.md
---
# How To Navigate KDCube Bundle Docs

This page is only a navigation guide. It tells an agent how to find the right
KDCube documentation, how to resolve KDCube link formats, and which document to
open next for a bundle task.

This page is not the place for detailed implementation rules. Import rules,
widget rules, Data Bus rules, subsystem integration rules, release procedures,
and test procedures belong in their own documents and are linked from here.

## Navigation Flow

Use this order.

1. **Use KDCube docs/knowledge search first when available.**

   If the environment exposes a KDCube docs, knowledge, or MCP search tool, ask
   it a narrow question before scanning the repository. The result should give
   repository paths, relative Markdown links, or tool-specific document ids.
   Treat those links as ranked entrypoints, then open the source docs they point
   to.

   Good search prompts:

   ```text
   how to integrate an SDK subsystem into a bundle
   widget visibility user_types_config roles_config
   source-folder widget sdk shared_sources
   bundle data bus handler object_ref partitioning
   React event source timeline announce policy
   ```

2. **Resolve returned links.**

   KDCube docs use repository and Markdown links:

   | Link | Meaning |
   | --- | --- |
   | `repo:kdcube-ai-app/...` | Path relative to the KDCube platform repo checkout. |
   | `repo:applications/...` | Path relative to the applications/content repo checkout. |
   | `repo:website/...` | Path relative to the website repo checkout. |
   | relative Markdown link | Resolve relative to the current doc file. |

   Do not replace reusable docs with one developer's absolute filesystem path.
   Use repository-relative or Markdown-relative links in docs and handoff notes.

3. **Open the smallest set of source docs needed.**

   Read the doc that directly matches the task. Follow `see_also` only when the
   first doc explicitly says the other surface is required.

4. **Use repository search only after docs search is insufficient.**

   Use `rg` for source navigation. Do not infer platform contracts only from
   one implementation file when a doc exists.

5. **When docs and source disagree, record the gap.**

   Fix the code or the doc that is wrong. If you cannot fix it in the current
   task, write a short dated note in the nearest component journal and link the
   exact doc/source paths.

## Tier 1 Bundle Pack

For serious bundle work, read these as one compact pack:

| Order | Doc | Purpose |
| --- | --- | --- |
| 1 | this page | Find the right docs and resolve repository/document links. |
| 2 | [how-to-test-bundle-README.md](how-to-test-bundle-README.md) | Know what the bundle must prove before designing the change. |
| 3 | [how-to-assemble-bundle-with-sdk-building-blocks-README.md](how-to-assemble-bundle-with-sdk-building-blocks-README.md) | Reuse existing SDK/platform blocks before writing new mechanics. |
| 4 | [how-to-write-bundle-README.md](how-to-write-bundle-README.md) | Bundle authoring structure and code layout. |
| 5 | [../bundle-properties-and-secrets-lifecycle-README.md](../bundle-properties-and-secrets-lifecycle-README.md) | Code defaults, descriptor/admin props, effective props, secrets, and materialization. |
| 6 | [../../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../../configuration/bundle-runtime-configuration-and-secrets-README.md) | Full configuration/secrets ownership across scopes. |
| 7 | [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md) | Local runtime, descriptor staging, reload, refresh, and export flow. |

Conditional additions:

| Situation | Add |
| --- | --- |
| Mounting memory, canvas, tasks, Telegram, delivery, or another reusable SDK subsystem | [../bundle-subsystem-integration-README.md](../bundle-subsystem-integration-README.md) |
| Integrating a host product or server with KDCube app surfaces, deciding iframe vs direct client vs backend-only app | [../../../how-to-integrate-with-kdcube-apps-README.md](../../../how-to-integrate-with-kdcube-apps-README.md) |
| Exposing a namespace of objects for other bundles, or consuming another bundle's namespace (canvas pins / chat chips / agent tools) | [../../namespace-services/README.md](../../namespace-services/README.md) |
| Touching bundle imports, widget assets/origins, widget visibility, live events, Data Bus, event policies, or resolver registration | [how-to-avoid-common-bundle-integration-failures-README.md](how-to-avoid-common-bundle-integration-failures-README.md) |
| Agent tools, skills, MCP, file-producing tools, role models, Claude Code | [../bundle-agent-integration-README.md](../bundle-agent-integration-README.md) |
| Authored external events, custom event policies, snapshots, artifact rehosters | [../bundle-events-README.md](../bundle-events-README.md) |
| Widget source folders, static widget builds, shared SDK UI source | [../bundle-widget-integration-README.md](../bundle-widget-integration-README.md) and [../ui-components-lifecycle-README.md](../ui-components-lifecycle-README.md) |
| Entrypoint inheritance, mixins, singleton behavior, request context | [../bundle-entrypoint-classes-README.md](../bundle-entrypoint-classes-README.md) |
| Release tag, descriptor ref, release validation | [how-to-release-bundle-content-README.md](how-to-release-bundle-content-README.md) |
| Agent should configure and run local KDCube | [how-to-bootstrap-local-bundle-runtime-as-coding-agent-README.md](how-to-bootstrap-local-bundle-runtime-as-coding-agent-README.md) |

## Question-To-Doc Map

| Question | Open First |
| --- | --- |
| What docs should I read for bundle work? | [../bundle-index-README.md](../bundle-index-README.md), then this page. |
| What reusable blocks already exist? | [how-to-assemble-bundle-with-sdk-building-blocks-README.md](how-to-assemble-bundle-with-sdk-building-blocks-README.md) |
| What recurring implementation rules should I check before editing bundle code? | [how-to-avoid-common-bundle-integration-failures-README.md](how-to-avoid-common-bundle-integration-failures-README.md) |
| How do I mount an existing SDK subsystem correctly? | [../bundle-subsystem-integration-README.md](../bundle-subsystem-integration-README.md) |
| How does one bundle call another bundle's objects/actions (e.g. canvas shows a `task:` pin)? | [../../namespace-services/README.md](../../namespace-services/README.md) |
| How do I write or structure a bundle? | [how-to-write-bundle-README.md](how-to-write-bundle-README.md) |
| Which entrypoint base or mixin should I use? | [../bundle-entrypoint-classes-README.md](../bundle-entrypoint-classes-README.md) |
| How do code defaults, `bundles.yaml`, admin props, and secrets merge? | [../bundle-properties-and-secrets-lifecycle-README.md](../bundle-properties-and-secrets-lifecycle-README.md) |
| Where do platform settings vs bundle props vs user state belong? | [../../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../../configuration/bundle-runtime-configuration-and-secrets-README.md) |
| How do I run or reload a bundle locally? | [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md) |
| How do I test the bundle? | [how-to-test-bundle-README.md](how-to-test-bundle-README.md) |
| How do I expose APIs, widgets, MCP, cron, jobs, or Data Bus handlers? | [../bundle-platform-integration-README.md](../bundle-platform-integration-README.md) |
| How do browser widgets communicate with bundle operations and streams? | [../bundle-client-communication-README.md](../bundle-client-communication-README.md) |
| How does a host app or server integrate with KDCube without necessarily using a widget? | [../../../how-to-integrate-with-kdcube-apps-README.md](../../../how-to-integrate-with-kdcube-apps-README.md) |
| How do I configure Data Bus publish limits? | [../../../configuration/gateway-descriptor-README.md#data_buspublish_limits](../../../configuration/gateway-descriptor-README.md#data_buspublish_limits), [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md) |
| How do I build source-folder widgets or reuse SDK widget source? | [../bundle-widget-integration-README.md](../bundle-widget-integration-README.md), [../ui-components-lifecycle-README.md](../ui-components-lifecycle-README.md) |
| How does ReAct see tools, skills, MCP, and generated files? | [../bundle-agent-integration-README.md](../bundle-agent-integration-README.md) |
| How do authored external events render to timeline/ANNOUNCE? | [../bundle-events-README.md](../bundle-events-README.md), [../../agents/react/event-source/event-source-README.md](../../agents/react/event-source/event-source-README.md) |
| How do I route conversation events vs Data Bus messages? | [../../../service/comm/conversation-event-bus-and-data-bus-README.md](../../../service/comm/conversation-event-bus-and-data-bus-README.md), [../../../service/comm/bus-routing-and-partitioning-README.md](../../../service/comm/bus-routing-and-partitioning-README.md) |
| How do I expose local KDCube through public HTTPS? | [../../../service/cicd/ngrok-README.md](../../../service/cicd/ngrok-README.md) |
| How do I study a known-good reference bundle? | [../versatile-reference-bundle-README.md](../versatile-reference-bundle-README.md) |

## Role Paths

Use these paths only to choose the first docs. Real bundle work often combines
several roles.

| Role | Start With |
| --- | --- |
| Bundle creator | test -> assemble -> write -> config/secrets -> configure/run |
| Existing app wrapper | assemble -> write -> platform integration -> configure/run -> test |
| Subsystem integrator | subsystem integration -> widget integration -> entrypoint classes -> events/agent docs as needed |
| Configurator | bundle runtime configuration -> properties/secrets lifecycle -> configure/run |
| Local QA | test -> configure/run -> platform integration |
| Release owner | test -> release content -> delivery/update |

## Agent Rules

- Prefer docs/knowledge MCP search when available.
- Preserve repository-relative and Markdown-relative links in notes and docs.
- Open source docs behind returned links before changing code.
- Do not read the whole docs tree unless the task is a docs audit.
- Do not paste implementation rules into this navigation page; add them to the
  specific implementation doc and link it from here.
- When adding a new reusable SDK subsystem, add its package docs and add one
  routing row to this page only if it changes the navigation map.
