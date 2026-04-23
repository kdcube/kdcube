---
id: ks:docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md
title: "How To Navigate KDCube Bundle Docs"
summary: "Tier 1 navigation guide for bundle builders, bundle integrators, and readers who need the shortest path through KDCube docs without reading the whole tree."
tags: ["sdk", "bundle", "docs", "navigation", "tier-1", "authoring"]
keywords: ["bundle docs navigation", "tier 1 reading order", "new bundle path", "wrap existing app into bundle", "bundle integrator path", "kdcube docs reading strategy", "which doc to read next"]
see_also:
  - ks:docs/sdk/bundle/bundle-index-README.md
  - ks:docs/sdk/bundle/build/how-to-write-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-test-bundle-README.md
  - ks:docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - ks:docs/sdk/bundle/versatile-reference-bundle-README.md
---
# How To Navigate KDCube Bundle Docs

This page is the Tier 1 entrypoint for people who do not want to read the whole
SDK tree.

Use it when you are:

- creating a new bundle from scratch
- wrapping an existing backend, UI, webhook, cron job, or tool into a bundle
- integrating an existing bundle into a KDCube environment
- trying to find the right doc fast without guessing from filenames

## 1. The Short Answer

Do not start by reading every bundle doc.

Start with these four Tier 1 pages in this order:

1. this page
2. [how-to-write-bundle-README.md](how-to-write-bundle-README.md)
3. [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md)
4. [how-to-test-bundle-README.md](how-to-test-bundle-README.md)

Then branch to deeper docs only for the concrete question you have.

## 2. Which Path Fits Your Job

### A. I am creating a new bundle

Read in this order:

1. [how-to-write-bundle-README.md](how-to-write-bundle-README.md)
2. [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md)
3. [../../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../../configuration/bundle-runtime-configuration-and-secrets-README.md)
4. [../bundle-platform-integration-README.md](../bundle-platform-integration-README.md)
5. [../bundle-runtime-README.md](../bundle-runtime-README.md)
6. [../versatile-reference-bundle-README.md](../versatile-reference-bundle-README.md)
7. [how-to-test-bundle-README.md](how-to-test-bundle-README.md)

Interpretation:

- `how-to-write` tells you what to build
- `how-to-configure-and-run` tells you how the runtime is staged and wired
- `bundle-runtime-configuration-and-secrets` tells you where values belong
- `bundle-platform-integration` tells you how to expose the surfaces
- `bundle-runtime` tells you what runtime helpers exist
- `versatile` shows a working bundle shape
- `how-to-test` tells you how to prove the contract

### B. I am wrapping existing user code into a bundle

Read in this order:

1. [how-to-write-bundle-README.md](how-to-write-bundle-README.md)
2. [../bundle-platform-integration-README.md](../bundle-platform-integration-README.md)
3. [../../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../../configuration/bundle-runtime-configuration-and-secrets-README.md)
4. [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md)
5. [../versatile-reference-bundle-README.md](../versatile-reference-bundle-README.md)
6. [how-to-test-bundle-README.md](how-to-test-bundle-README.md)

Practical rule:

- keep the existing business logic in reusable helpers or a delegated service
- make the bundle layer a thin KDCube adapter
- map the existing surface to the right decorator contract:
  - webhook -> `@api(route="public")`
  - admin/backend action -> `@api(route="operations")`
  - iframe frontend -> `ui.main_view` or widget + operations
  - MCP server -> `@mcp(...)`
  - background sync -> `@cron(...)`
  - assistant workflow -> `@agentic_workflow` / `@on_message`

### C. I am integrating a bundle into a KDCube environment

Read in this order:

1. [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md)
2. [../../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../../configuration/bundle-runtime-configuration-and-secrets-README.md)
3. [../../../configuration/bundles-descriptor-README.md](../../../configuration/bundles-descriptor-README.md)
4. [../../../configuration/bundles-secrets-descriptor-README.md](../../../configuration/bundles-secrets-descriptor-README.md)
5. [../bundle-delivery-and-update-README.md](../bundle-delivery-and-update-README.md)
6. [how-to-test-bundle-README.md](how-to-test-bundle-README.md)

This is the right path if your main questions are:

- how do I point the runtime at the bundle code
- where do bundle props and bundle secrets live
- when do I rerun install vs reload
- what is the real local runtime authority
- how do I export live deployment-scoped bundle state

### D. I just need to browse the docs efficiently

Use:

1. [../bundle-index-README.md](../bundle-index-README.md)
2. this page

Then jump only to the row that matches your question.

## 3. Question-To-Doc Map

| Question | Read this doc first | Why |
| --- | --- | --- |
| What is a bundle? | [how-to-write-bundle-README.md](how-to-write-bundle-README.md) | It defines bundle as the application unit and `tenant/project` as the environment boundary. |
| I have existing code. How do I wrap it? | [how-to-write-bundle-README.md](how-to-write-bundle-README.md) | It contains the design matrix and process-boundary guidance. |
| How do I run a bundle locally? | [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md) | It documents the current local runtime contract and staged descriptor model. |
| Where do props and secrets belong? | [../../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../../configuration/bundle-runtime-configuration-and-secrets-README.md) | It is the canonical author-facing configuration page. |
| How do I expose widget, API, MCP, or cron? | [../bundle-platform-integration-README.md](../bundle-platform-integration-README.md) | It is the exact decorator and surface contract. |
| What runtime helpers exist inside bundle code? | [../bundle-runtime-README.md](../bundle-runtime-README.md) | It explains the bundle runtime objects and capabilities. |
| How do I talk to the browser correctly? | [../bundle-client-ui-README.md](../bundle-client-ui-README.md) | It routes you to widget, browser, and transport-facing docs. |
| How do I test the bundle correctly? | [how-to-test-bundle-README.md](how-to-test-bundle-README.md) | It is the operational validation playbook. |
| How do I study a known-good bundle? | [../versatile-reference-bundle-README.md](../versatile-reference-bundle-README.md) | It points to the working reference bundle and what to mine from it. |
| How do I reload or ship a changed bundle? | [../bundle-delivery-and-update-README.md](../bundle-delivery-and-update-README.md) | It explains reload, delivery mode, and deployment-side update flow. |

## 4. Fast Reading Plans

### 15-minute plan

Use this when you need enough context to start coding:

1. this page
2. [how-to-write-bundle-README.md](how-to-write-bundle-README.md)
3. [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md)
4. [../../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../../configuration/bundle-runtime-configuration-and-secrets-README.md)

### 45-minute plan

Use this when you are about to implement a real bundle:

1. this page
2. [how-to-write-bundle-README.md](how-to-write-bundle-README.md)
3. [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md)
4. [../../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../../configuration/bundle-runtime-configuration-and-secrets-README.md)
5. [../bundle-platform-integration-README.md](../bundle-platform-integration-README.md)
6. [../bundle-runtime-README.md](../bundle-runtime-README.md)
7. [../versatile-reference-bundle-README.md](../versatile-reference-bundle-README.md)
8. [how-to-test-bundle-README.md](how-to-test-bundle-README.md)

## 5. When To Stop Reading And Start Building

Stop reading and start coding once you can answer these clearly:

- what bundle surface am I exposing
- what runtime path will execute the code
- what values belong in platform settings vs bundle props/secrets vs user state
- what storage tier owns my mutable state
- how the bundle will be run locally
- how I will validate it before calling it done

If one of those answers is still fuzzy, jump to the corresponding deeper doc
from the table above instead of reading random pages.
