---
id: ks:docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md
title: "How To Navigate KDCube Bundle Docs"
summary: "Tier 1 navigation guide for bundle creators, integrators, configurators, deployers, local QA, integration QA, and document readers who need the shortest path through KDCube docs without reading the whole tree."
tags: ["sdk", "bundle", "docs", "navigation", "tier-1", "authoring"]
keywords: ["bundle docs navigation", "tier 1 reading order", "new bundle path", "wrap existing app into bundle", "bundle integrator path", "bundle configurator path", "bundle deployer path", "bundle qa path", "integration qa path", "kdcube docs reading strategy", "which doc to read next"]
see_also:
  - ks:docs/sdk/bundle/bundle-index-README.md
  - ks:docs/sdk/bundle/build/how-to-write-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-test-bundle-README.md
  - ks:docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - ks:docs/sdk/bundle/versatile-reference-bundle-README.md
  - ks:docs/sdk/bundle/bundle-storage-and-cache-README.md
  - ks:docs/sdk/storage/cache-README.md
  - ks:docs/sdk/storage/git-store-README.md
  - ks:docs/sdk/storage/sdk-store-README.md
---
# How To Navigate KDCube Bundle Docs

This page is the Tier 1 entrypoint for people who do not want to read the whole
SDK tree.

Use it when you are:

- creating a new bundle from scratch
- wrapping an existing backend, UI, webhook, cron job, or tool into a bundle
- mapping existing app configuration into KDCube scopes
- wiring a bundle into a KDCube environment and operating it locally
- validating a bundle with local tests or runtime integration tests
- integrating an existing bundle into a KDCube environment
- trying to find the right doc fast without guessing from filenames

Important:

- these roles are not separate "brains"
- they are task facets used to choose the next best document
- one real task often combines several roles in sequence, for example:
  creator -> configurator -> deployer -> local QA -> integration QA
- the same agent should be able to plan across those roles without resetting
  context

## 1. The Short Answer

Do not start by reading every bundle doc.

Do treat Tier 1 as one compact pack.

Start with these five Tier 1 pages in this order:

1. this page
2. [how-to-test-bundle-README.md](how-to-test-bundle-README.md)
3. [how-to-write-bundle-README.md](how-to-write-bundle-README.md)
4. [../../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../../configuration/bundle-runtime-configuration-and-secrets-README.md)
5. [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md)

Read those five together as one bundle-authoring baseline.

The order helps, but the important rule is:

- do not stop after only one Tier 1 page
- do not replace the Tier 1 pack with random deeper docs
- branch to deeper docs only after the Tier 1 pack is understood

Correction:

- the test guide is not only an end-of-task validation page
- it also tells the agent what the bundle must prove in the real runtime
- reading it early usually improves design and reduces wasted implementation

So the practical Tier 1 reading order is:

1. navigation
2. test expectations
3. bundle design
4. configuration ownership
5. local runtime and deployment wiring

## 2. Which Path Fits Your Job

Use the sections below as entry paths, not as mutually exclusive personas.

Normal bundle work often crosses several of them in one task.

Use them to choose the best first page when orientation is needed.

They do not replace the rule above:

- the agent should still read the whole Tier 1 pack for serious bundle work

Example:

- when wrapping an existing app, the same agent may need to act as:
  integrator, configurator, deployer, and QA
- when creating a new bundle, the same agent may need to act as:
  creator, configurator, deployer, and integration QA

### A. I am creating a new bundle

Start here, then complete the rest of the Tier 1 pack:

1. [how-to-test-bundle-README.md](how-to-test-bundle-README.md)
2. [how-to-write-bundle-README.md](how-to-write-bundle-README.md)
3. [../../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../../configuration/bundle-runtime-configuration-and-secrets-README.md)
4. [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md)
5. [../bundle-platform-integration-README.md](../bundle-platform-integration-README.md)
6. [../bundle-runtime-README.md](../bundle-runtime-README.md)
7. [../versatile-reference-bundle-README.md](../versatile-reference-bundle-README.md)

Interpretation:

- `how-to-test` tells you what the bundle must prove and which runtime paths
  must exist
- `how-to-write` tells you what to build
- `bundle-runtime-configuration-and-secrets` tells you where values belong
- `how-to-configure-and-run` tells you how the runtime is staged and wired
- `bundle-platform-integration` tells you how to expose the surfaces
- `bundle-runtime` tells you what runtime helpers exist
- `versatile` shows a working bundle shape

### B. I am wrapping existing user code into a bundle

Start here, then complete the rest of the Tier 1 pack:

1. [how-to-test-bundle-README.md](how-to-test-bundle-README.md)
2. [how-to-write-bundle-README.md](how-to-write-bundle-README.md)
3. [../bundle-platform-integration-README.md](../bundle-platform-integration-README.md)
4. [../../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../../configuration/bundle-runtime-configuration-and-secrets-README.md)
5. [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md)
6. [../versatile-reference-bundle-README.md](../versatile-reference-bundle-README.md)

Practical rule:

- read the test page early so you know the bundle contract you must satisfy
- keep the existing business logic in reusable helpers or a delegated service
- make the bundle layer a thin KDCube adapter
- map the existing surface to the right decorator contract:
  - webhook -> `@api(route="public")`
  - admin/backend action -> `@api(route="operations")`
  - iframe frontend -> `ui.main_view` or widget + operations
  - MCP server -> `@mcp(...)`
  - background sync -> `@cron(...)`
  - assistant workflow -> `@agentic_workflow` / `@on_message`

### C. I am configuring a bundle or translating an existing app config

Start here, then complete the rest of the Tier 1 pack:

1. [../../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../../configuration/bundle-runtime-configuration-and-secrets-README.md)
2. [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md)
3. [../../../configuration/bundles-descriptor-README.md](../../../configuration/bundles-descriptor-README.md)
4. [../../../configuration/bundles-secrets-descriptor-README.md](../../../configuration/bundles-secrets-descriptor-README.md)
5. [../../../configuration/assembly-descriptor-README.md](../../../configuration/assembly-descriptor-README.md)

This is the right path if your main questions are:

- which values belong to platform settings vs bundle props/secrets vs user state
- how to model an existing app setting in KDCube terms
- which values are deployment-scoped and exportable
- which values are operational user state and not descriptor-backed

### D. I am integrating or deploying a bundle into a KDCube environment

Start here, then complete the rest of the Tier 1 pack:

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

### E. I am doing local QA for a bundle

Start here, then complete the rest of the Tier 1 pack:

1. [how-to-test-bundle-README.md](how-to-test-bundle-README.md)
2. [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md)

Use this when the main job is:

- syntax and import validation
- shared suite execution
- bundle-local pytest execution
- direct verification of cron helpers, serializers, builders, and other local code

### F. I am doing integration QA for a bundle

Start here, then complete the rest of the Tier 1 pack:

1. [how-to-test-bundle-README.md](how-to-test-bundle-README.md)
2. [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md)
3. [../bundle-platform-integration-README.md](../bundle-platform-integration-README.md)

Use this when the main job is:

- widget/browser validation
- API validation
- MCP validation
- reload/reconcile validation
- cron/integration runtime validation inside a real KDCube environment

### G. I just need to browse the docs efficiently

Use:

1. [../bundle-index-README.md](../bundle-index-README.md)
2. this page

Then jump only to the row that matches your question.

## 3. Question-To-Doc Map

| Question | Read this doc first | Why |
| --- | --- | --- |
| What is a bundle? | [how-to-write-bundle-README.md](how-to-write-bundle-README.md) | It defines bundle as the application unit and `tenant/project` as the environment boundary. |
| I have existing code. How do I wrap it? | [how-to-write-bundle-README.md](how-to-write-bundle-README.md) | It contains the design matrix and process-boundary guidance. |
| How do I map existing app settings into KDCube settings, bundle props, and user state? | [../../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../../configuration/bundle-runtime-configuration-and-secrets-README.md) | It is the Tier 1 configuration model and ownership map. |
| How do I run a bundle locally? | [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md) | It documents the current local runtime contract and staged descriptor model. |
| Can I run multiple KDCubes on one machine? | [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md) | It explains the difference between many runtime snapshots on disk and one active local compose-backed deployment by default. |
| Where do props and secrets belong? | [../../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../../configuration/bundle-runtime-configuration-and-secrets-README.md) | It is the canonical author-facing configuration page. |
| How do I start, stop, reload, and descriptor-wire a bundle into a project? | [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md) | It is the Tier 1 deployer and integrator page for current local runtime operations. |
| How do I expose widget, API, MCP, or cron? | [../bundle-platform-integration-README.md](../bundle-platform-integration-README.md) | It is the exact decorator and surface contract. |
| What runtime helpers exist inside bundle code? | [../bundle-runtime-README.md](../bundle-runtime-README.md) | It explains the bundle runtime objects and capabilities. |
| How do I use storage, cache, local bundle storage, or git-backed helpers? | [how-to-write-bundle-README.md](how-to-write-bundle-README.md) | It now contains the compact SDK cheat sheet and points to the deeper storage docs only when needed. |
| How do I talk to the browser correctly? | [../bundle-client-ui-README.md](../bundle-client-ui-README.md) | It routes you to widget, browser, and transport-facing docs. |
| How do I run local bundle QA? | [how-to-test-bundle-README.md](how-to-test-bundle-README.md) | It covers local test order, shared suite, and bundle-local tests. |
| How do I run bundle integration QA? | [how-to-test-bundle-README.md](how-to-test-bundle-README.md) | It also covers browser, API, MCP, reload, and cron/runtime validation. |
| How do I study a known-good bundle? | [../versatile-reference-bundle-README.md](../versatile-reference-bundle-README.md) | It points to the working reference bundle and what to mine from it. |
| How do I reload or ship a changed bundle? | [../bundle-delivery-and-update-README.md](../bundle-delivery-and-update-README.md) | It explains reload, delivery mode, and deployment-side update flow. |

## 4. Fast Reading Plans

### 15-minute plan

Use this when you need enough context to start coding:

1. this page
2. [how-to-test-bundle-README.md](how-to-test-bundle-README.md)
3. [how-to-write-bundle-README.md](how-to-write-bundle-README.md)
4. [../../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../../configuration/bundle-runtime-configuration-and-secrets-README.md)

### 45-minute plan

Use this when you are about to implement a real bundle:

1. this page
2. [how-to-test-bundle-README.md](how-to-test-bundle-README.md)
3. [how-to-write-bundle-README.md](how-to-write-bundle-README.md)
4. [../../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../../configuration/bundle-runtime-configuration-and-secrets-README.md)
5. [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md)
6. [../bundle-platform-integration-README.md](../bundle-platform-integration-README.md)
7. [../bundle-runtime-README.md](../bundle-runtime-README.md)
8. [../versatile-reference-bundle-README.md](../versatile-reference-bundle-README.md)

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
