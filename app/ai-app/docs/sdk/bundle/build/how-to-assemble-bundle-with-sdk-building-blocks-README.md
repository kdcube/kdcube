---
id: ks:docs/sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md
title: "How To Assemble A Bundle With SDK Building Blocks"
summary: "Tier 1 bundle-builder map for choosing reusable KDCube SDK and platform blocks before writing custom bundle services: tools, agents, storage, widgets, jobs, integrations, and solutions."
tags: ["sdk", "bundle", "tier-1", "building-blocks", "integrations", "solutions", "tools"]
keywords: ["bundle building blocks", "sdk integrations", "sdk solutions", "bundle assembly map", "reuse sdk components", "telegram integration", "email integration", "tasks solution", "delivery integration", "built in tools", "react tools"]
see_also:
  - ks:docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md
  - ks:docs/sdk/bundle/build/how-to-write-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-test-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - ks:docs/sdk/solutions/tasks-README.md
  - ks:docs/sdk/integrations/README.md
  - ks:docs/sdk/integrations/email/README.md
  - ks:docs/sdk/integrations/telegram/README.md
  - ks:docs/sdk/integrations/browser/browser-tools-README.md
  - ks:docs/sdk/tools/sdk-tools-README.md
  - ks:docs/sdk/bundle/bundle-agent-integration-README.md
  - ks:docs/sdk/bundle/bundle-platform-integration-README.md
  - ks:docs/sdk/bundle/bundle-runtime-README.md
---
# How To Assemble A Bundle With SDK Building Blocks

Use this page before implementing a new subsystem in a bundle.

The goal is to assemble product behavior from reusable KDCube blocks where the
platform already owns the mechanics, and keep bundle code focused on product
policy, route aliases, prompts, UI composition, and user-scope decisions.

## Assembly Rule

For each feature, choose the closest existing block first:

```text
product need
  -> SDK/platform block
  -> bundle binding hooks
  -> product policy and UI
```

Write custom code when the product policy is new, the provider is not covered,
or the bundle needs domain-specific storage and prompts.

When a feature becomes reusable across bundles, move it into an SDK integration
or solution package and update this page.

## Current Reusable Blocks

| Need | Use | Primary docs |
| --- | --- | --- |
| Saved tasks, schedules, fresh executions, execution journals, output recovery | `kdcube_ai_app.apps.chat.sdk.solutions.tasks` | [Tasks SDK Solution](../../solutions/tasks-README.md) |
| Gmail/iCloud accounts, OAuth/settings, email attachment materialization, Email MCP, Claude Code email processing | `kdcube_ai_app.apps.chat.sdk.integrations.email` | [Email Integration](../../integrations/email/README.md) |
| Telegram webhook, Bot API rendering, progress streaming, Mini App auth, widget operations, user registry, signed downloads | `kdcube_ai_app.apps.chat.sdk.integrations.telegram` | [Telegram Integration](../../integrations/telegram/README.md) |
| Explicit report delivery to email/Telegram with delivered-file metadata | `kdcube_ai_app.apps.chat.sdk.integrations.delivery` | [Email Integration](../../integrations/email/email-README.md), [Telegram Integration](../../integrations/telegram/telegram-README.md) |
| Web search and web fetch with source-pool provenance | `web_tools` | [SDK Tools](../../tools/sdk-tools-README.md) |
| Real browser verification for generated HTML, widgets, and local browser flows | `browser_tools`, shared Playwright backend, per-turn BrowserContext | [Browser Tools](../../integrations/browser/browser-tools-README.md), [Playwright Backend](../../integrations/browser/playwright-README.md) |
| PDF, DOCX, PPTX, PNG, HTML generation | `rendering_tools` plus public rendering skills | [SDK Tools](../../tools/sdk-tools-README.md) |
| Isolated code execution and generated-code work | `exec_tools`, isolated runtime, tool bridge | [Bundle Agent Integration](../bundle-agent-integration-README.md), [SDK Tools](../../tools/sdk-tools-README.md) |
| Context, attachments, hosted files, and conversation-scoped reads | `ctx_tools`, `io_tools`, hosting/runtime APIs | [Bundle Runtime](../bundle-runtime-README.md), [SDK Tools](../../tools/sdk-tools-README.md) |
| ReAct agent with bundle tools and skills | `BaseWorkflow.build_react(...)`, `tools_descriptor.py`, `skills_descriptor.py` | [Bundle Agent Integration](../bundle-agent-integration-README.md) |
| Bundle-served MCP endpoint | `@mcp(...)` | [Bundle Platform Integration](../bundle-platform-integration-README.md), [MCP Tools](../../tools/mcp-README.md) |
| Claude Code subagent with scoped MCP/tools | `ClaudeCodeAgent`, `ClaudeCodeWorkspaceConfig` | [Bundle Agent Integration](../bundle-agent-integration-README.md) |
| Browser widget or Mini App | `@ui_widget(...)`, source-folder widget build, operations/public APIs | [Bundle Widget Integration](../bundle-widget-integration-README.md) |
| Scheduled scan and background execution | `@cron(...)`, `@on_job`, jobs stream; use Tasks Solution for saved task execution | [Scheduled Jobs](../bundle-scheduled-jobs-README.md), [Tasks SDK Solution](../../solutions/tasks-README.md) |
| Local mutable files, generated indexes, git working copies, runtime caches | bundle storage helpers, `AIBundleStorage`, KV cache, git helpers | [Bundle Storage And Cache](../bundle-storage-and-cache-README.md) |
| Node/TypeScript backend inside a bundle | Python bundle shell + Node sidecar bridge | [Bundle Node Backend Bridge](../bundle-node-backend-bridge-README.md) |
| Bundle-specific Python dependencies | `@venv(...)` | [Bundle Venv](../bundle-venv-README.md) |

## Where Blocks Are Wired

| File or layer | What belongs there |
| --- | --- |
| `entrypoint.py` | Decorators, route aliases, SDK module configuration, storage-root and user-scope hooks, product role policy. |
| `tools_descriptor.py` | Tool aliases for SDK tool modules and bundle-local tool modules used by the agent. |
| `skills_descriptor.py` | Built-in SDK skill ids and bundle-local product skill roots. |
| `config/bundles.template.yaml` | Deployment-scoped non-secret props that enable/configure the block. |
| `config/bundles.secrets.template.yaml` | Deployment-scoped secrets such as bot tokens, OAuth client secrets, signing keys. |
| user settings UI | User-owned credentials and choices, such as personal email accounts. |
| `docs/integrations/*` in a bundle | Operator homework outside KDCube, such as BotFather or Google Cloud setup. |
| `docs/design/*` in a bundle | Current product boundary: which SDK blocks are used and what policy remains in the bundle. |

## Common Product Recipes

### Chat Agent With Files, Search, And Reports

```text
React workflow
  -> web_tools for research
  -> browser_tools for generated HTML/widget verification when needed
  -> rendering_tools for PDF/DOCX/PPTX/PNG/HTML
  -> delivery integration for email/Telegram delivery
  -> hosted file metadata in turn timeline
```

Bundle code owns the prompt, allowed tool aliases, delivery target policy, and
UI route aliases.

### Task Automation App

```text
Tasks SDK Solution
  -> task storage and search
  -> execution journals and artifacts
  -> due scan + job handler
  -> task/job skills and tools
  -> widget operation helpers
```

Bundle code owns product-specific task wording, user identity resolution,
widget composition, and route exposure.

### Telegram App With Mini App Controls

```text
Telegram Integration
  -> webhook validation and update normalization
  -> mapped user/conversation scope
  -> progress streaming and final send
  -> Mini App initData auth
  -> widget operation helpers
  -> signed file downloads
```

Bundle code owns role policy, which conversations can be selected, which
operations are public, and what workflow handles a message.

### Email-Enabled Assistant

```text
Email Integration
  -> account store and settings routes
  -> Gmail OAuth/API or iCloud IMAP/SMTP
  -> attachment materialization
  -> Email MCP for scoped message access
  -> Claude Code email-processing subagent when needed
```

Bundle code owns the product instruction, account selection policy, delivery
choice, and UI affordances for connecting accounts.

## Test The Assembly Boundary

When a bundle uses an SDK block, tests should prove the binding, not duplicate
the SDK internals:

- routes call the configured SDK module with the right user scope;
- public paths validate their external auth, for example Telegram `initData`;
- deployment props/secrets are read from the documented paths;
- user-owned credentials are stored as user-scoped runtime state/secrets;
- generated files are exposed only through the supported artifact/download
  contract;
- scheduled jobs restore runtime context and do not ask the model to invent
  task ids, execution ids, account ids, or storage paths;
- bundle design docs say which SDK blocks are used and what policy remains in
  the bundle.

Use SDK-level tests for the reusable mechanics and bundle-local tests for the
product binding.

## Adding New Reusable Blocks

When adding a reusable block such as cross-conversation memory, knowledge-base
retrieval, or a neural processing subagent:

1. Put reusable mechanics under `sdk.integrations.*` or `sdk.solutions.*`.
2. Keep product policy and route aliases in the bundle.
3. Add an SDK doc for the package.
4. Add an external-prerequisites doc if the integration needs provider/admin
   setup outside KDCube.
5. Add the block to this assembly map and to the Tier 1 navigation page.
6. Add SDK tests for the reusable package and bundle tests for the binding.
