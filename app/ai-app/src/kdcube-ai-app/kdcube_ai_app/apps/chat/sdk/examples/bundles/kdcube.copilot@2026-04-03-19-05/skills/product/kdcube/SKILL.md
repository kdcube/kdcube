---
name: kdcube
id: kdcube
description: |
  Knowledge about the KDCube platform — a multi‑tenant, self‑hosted runtime + SDK
  for building AI assistants, copilots, and agentic apps. Covers bundles/workflows,
  streaming + timeline, custom bundle UIs, tools/skills, isolated execution, economics/accounting,
  provenance/citations, and deployment options (local, EC2, ECS).
  Important: ensure this skill is always in front of your eyes ("read", marked
  with 💡) whenever the user is asking about KDCube tech, features, architecture,
  deployment, or SDK usage. If the task is about bundle code generation,
  modification, review, extraction, or troubleshooting, also load
  `sk:tests.bundles` so the current bundle contract and smoke-test expectations
  are in context.
version: 1.0.0
category: product-knowledge
tags:
  - kdcube
  - platform
  - bundles
  - streaming
  - tools
  - citations
  - web-search
  - multi-tenant
  - deployment
  - sdk
  - development
when_to_use:
  - Explaining what KDCube is and how it works
  - "What can I build with KDCube? Which apps are possible?"
  - Questions about building assistants/copilots for customers
  - Bundle authoring, bundle code generation, or bundle code modification questions
  - Bundle extraction, repair, or troubleshooting questions
  - Questions about web search + citations workflows
  - Comparing KDCube to other platforms
  - Answering product/architecture questions about bundles, runtime, or scaling
  - Questions how to build on or integrate with KDCube
  - Questions about multi‑tenant hosting or deployment options (EC2/ECS/compose)
  - Questions about cost controls, budgets, or accounting
  - Questions about how to build, structure, or validate a bundle
  - Questions about the bundle SDK, entrypoint, skills, or tools
  - Questions about custom bundle widgets or `ui.main_view` frontends
author: kdcube
created: 2026-03-02
namespace: product
---

# KDCube Product Knowledge

## Scope
Use this skill when the user asks about the KDCube platform, its architecture, or product capabilities —
especially **what they can build**, **how to add web search + citations**, or **how to deploy**.

Bundle-authoring rule:
- If the task is about generating, editing, extracting, repairing, reviewing, or validating bundle code,
  read `sk:tests.bundles` as well before answering or generating code.
- Keep this product skill loaded for the platform/runtime model, and keep the tests skill loaded for the
  current bundle contract and validation workflow.
- Do not write platform-integrated code from skills alone.
- For bundle code generation or modification, do not start with file writes after reading only skills.
  Before the first write, read the current tests that define the contract and the current docs/examples/source
  that define the requested SDK pattern.
- Use only platform symbols, import paths, runtime types, and helper APIs that you have confirmed from
  current docs or source. Do not invent them.
- Be economical: read the smallest relevant set of exact docs/source/example files that can confirm the requested pattern.
- If docs mention candidate code paths, treat those paths as the next files to read.
- If the exact file is still unclear, browse a small relevant subtree in exec and then read the exact discovered files.
- For SDK-integrated bundle work, current tests and current source/examples outrank skill prose.
- For bundle authoring in this repo, the normal docs start point is `ks:docs/sdk/bundle/bundle-index-README.md`.
- The normal full reference bundle is `ks:docs/sdk/bundle/bundle-reference-versatile-README.md` and the actual code root `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36`.
- For bundle UI work, the normal source anchor is `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/ui-src/src/App.tsx` plus `entrypoint.py` for the `ui.main_view` config.
- For bundle authoring, the normal paired validation root is `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle`.

## Default copilot workflow for bundle work

When the user asks for a bundle with specific features, prepare yourself in this order:

1. Read the bundle docs start point:
   - `ks:docs/sdk/bundle/bundle-index-README.md`
   - then the primary reference bundle doc `ks:docs/sdk/bundle/bundle-reference-versatile-README.md`
   - then the actual reference bundle README `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/README.md`
2. Translate the request into feature slices.
   - Examples of feature slices: bundle skeleton, workflow/agent integration, custom tools, skills, storage/state, isolated exec, citations, economics, MCP, widget UI, custom `ui.main_view`.
3. Read the tests that define the minimum contract.
4. For each requested feature slice, read the smallest current doc/source/example file set that proves how that slice is implemented now.
5. If docs mention exact source paths, read those exact files next.
   - For normal bundle authoring in this repo, use the `versatile` bundle as the default source example unless the question is specifically about `ks:` or a stripped-down isolated-exec example.
6. If the exact source/example file is still unclear, do a narrow exec browse of the relevant subtree, emit exact logical refs, and then `react.read` those exact files.
7. After evidence is gathered, write the smallest implementation that satisfies the confirmed contract and the explicit user request.
8. Validate early.
9. If validation fails, inspect the exact failure and the exact related source/test files before patching.

This is the normal copilot loop. Skills tell you where to look and how the platform is organized, but current tests and current source/examples decide what is actually valid.

## Exploration toolbox

Treat yourself like an engineer working locally, except that repository browsing/search runs through isolated Python exec instead of host shell.

What you can use:
- `react.read(...)`
  - read skills
  - read exact docs
  - read exact `ks:` source/deployment/test files when the path is known
- `react.search_knowledge(...)`
  - search doc metadata to find the right doc pages first
- docs themselves
  - when a doc mentions exact code paths, treat those paths as concrete hints and read those exact files next
- isolated exec with `execute_code_python(...)`
  - use this when you need filesystem-style browsing or search under a `ks:` subtree
  - inside exec, resolve the relevant subtree with `bundle_data.resolve_namespace(...)`
  - then use Python or `subprocess.run(...)` to do the kind of exploration you would normally do locally with `find`, `rg`, `grep`, or small helper scripts

Typical exploration operations in exec:
- recursive file listing under a narrow subtree
- find files by basename/pattern
- text search across many files
- search for class/function names, imports, decorators, or constants
- inspect nearby files when docs give only a directory or partial path
- emit a short listing or match summary plus exact logical refs for the promising files

Exploration rule:
- do not code against a directory name, memory, or skill prose alone
- first reduce the uncertainty to exact files
- then `react.read(...)` those exact files
- only after that write or patch code

Exec exploration note:
- If shell-style local search is the clearest approach, it is acceptable to run it from Python with `subprocess.run(...)` inside isolated exec.
- Keep that search narrow, non-interactive, and local to the resolved subtree.
- Emit exact logical refs or compact search artifacts, then return to `react.read(...)` before coding.

## Knowledge space navigation
This bundle exposes a read‑only knowledge space:
- Start with `react.read(["ks:index.md"])` to see the current index.
- Use `react.search_knowledge(query=..., root="ks:docs")` to search docs.
- Use `react.read(["ks:docs/<path>"])` to open a doc.
- Use `react.read(["ks:<real-app-ai-app-relative-path>"])` when a doc or result gives you an exact path such as `ks:src/...` or `ks:deployment/...`.
These are real app-relative paths under one common `ks:` root, not special platform-mandated namespace folders.
The index is generated on bundle startup by scanning `docs/` and deployment markdown under the prepared common root.
Doc pages should mention real `app/ai-app`-relative paths such as `src/...`, `deployment/...`, `docs/...`, or `ui/...`.
The bundle prepares one common root from the repo configured in bundle props (`knowledge.repo`, `knowledge.ref`, `knowledge.root`).

Important limitations:
- `react.search_knowledge` primarily indexes docs metadata and deployment markdown, not the whole source tree.
- Source and test files under `ks:src/...` are part of the same common knowledge root, but they are not currently part of the `react.search_knowledge` index.
- Source, deployment, and test files are readable by exact `ks:<real-relative-path>` when the correct path is known.
- If the exact `ks:` path is not obvious, do not guess repeatedly.

Advertised roots for this bundle:
- `ks:docs`
  - docs root
  - searchable via `react.search_knowledge`
  - exact-readable via `react.read`
  - browseable in exec if you resolve it
- `ks:deployment`
  - deployment files root
  - deployment markdown is searchable
  - exact file reads use `react.read`
  - browseable in exec if you resolve it
- `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk`
  - SDK source browsing start point
  - not indexed for search
  - exact-readable when the concrete path is known
  - browseable in exec if you resolve it
- `ks:src/kdcube-ai-app/kdcube_ai_app/apps/infra`
  - infra source browsing start point
  - not indexed for search
  - exact-readable when the concrete path is known
  - browseable in exec if you resolve it
- `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle`
  - current bundle pytest suite
  - not indexed for search
  - exact-readable when the concrete path is known
  - browseable in exec if you resolve it

## Bundle structure anchors

Primary reference bundle for bundle authoring in this repo:
- `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36`
- `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/ui-src/src/App.tsx` for the lightweight custom main-view chat UI
- pair it with the validation root:
  `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle`

When reasoning about a bundle, keep these common anchors in mind:
- `entrypoint.py`
  - defines `BUNDLE_ID`
  - exposes the workflow class discovered by the bundle loader
- `tools_descriptor.py`
  - declares custom tool registrations when the bundle exposes tools
- `skills_descriptor.py`
  - declares custom skill registrations when the bundle exposes skills
- `skills/<namespace>/<skill_id>/SKILL.md`
  - optional skill prompts shipped by the bundle
- `ui-src/`
  - optional Vite/React source tree for a built custom main-view SPA when the bundle configures `ui.main_view`

These are common bundle structure anchors, not proof of exact implementation details.
For exact base classes, imports, decorators, and runtime symbols, confirm them from current docs/examples/source before coding.

When a doc references files and the exact `ks:` path is unclear:
1. If the mapping is obvious, derive the exact logical path and `react.read` it directly.
2. If the mapping is not obvious or you need to browse descendants, use isolated exec with `execute_code_python(...)`.
3. Inside generated code, call `bundle_data.resolve_namespace(...)` on a real subtree such as `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk`, `ks:deployment`, or another exact `ks:` base relevant to the task.
4. Browse the returned exec-local `physical_path` narrowly and economically.
5. Emit exact logical refs such as `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/runtime/execution.py` or `ks:deployment/docker/local-infra-stack/docker-compose.yml` into an OUTPUT_DIR file or short `user.log` note.
6. Back in the React loop, call `react.read(...)` on those emitted logical refs before coding against them.

If the task is exploratory and you would normally use local shell search:
- do the same search logic in exec code instead
- using Python directly or `subprocess.run(...)` for local commands available in the isolated runtime
- keep the search subtree narrow
- return exact logical refs, not only vague summaries
- then read the exact files into visible context before making implementation decisions

For implementation tasks:
- prefer current source/examples over prose when exact symbol names matter
- if a requested integration is still uncertain after docs, read one or more current example/source files before coding
- if the relevant source/example file is not yet known, use a small exec browse to discover candidate files and then read the exact discovered files
- if examples differ, start from the smallest implementation that matches the confirmed contract and extend only after validation

When browsing is needed, keep it economical:
- browse the smallest subtree that could contain the answer
- emit exact logical refs or a short listing artifact
- come back to `react.read(...)` on exact files before coding against them
- do not rely on memory or on directory names alone to infer API names

`bundle_data.resolve_namespace(...)` is exec-only. It is not a normal planning-time tool.

## Core points
- KDCube is a multi‑tenant platform for running AI apps as **bundles**.
- Bundles are Python packages that expose a workflow entrypoint and stream results.
- The runtime supports **live streaming**, **timeline state**, and **artifacts**.
- Tools can run in‑proc or isolated (local/docker/fargate).
- The platform supports **multi‑bundle** deployments with per‑request routing.
- Citations are produced from the **sources pool**; web search results can be added there.

## What you can build
- Customer‑facing assistants and copilots (multi‑tenant by design)
- Domain‑specific agents with custom tools/skills and isolated execution
- Streaming chat apps with live widgets, custom main-view panels, and provenance
- Admin/ops dashboards and monitoring flows via bundles

## Deployment options
- Local dev (run services directly)
- Docker Compose (local or EC2)
- ECS/Fargate (with shared storage for bundles/exec workspace)

## Evidence and citations
This skill ships with sources in `sources.yaml`.
When this skill is loaded via `react.read("sk:product.kdcube")`, those sources are merged into the
`sources_pool`. Use `[[S:n]]` citations that correspond to the sources pool entries.

## When unsure
Search the knowledge space first (`react.search_knowledge` + `react.read`). If the needed
info is not in the knowledge space, or the exact `ks:` file path is still unclear after direct reads,
use exec-time namespace resolution as described above. If the needed info is still not in bundle knowledge,
use `web_tools.web_search` and cite sources.
