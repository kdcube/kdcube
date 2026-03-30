---
name: kdcube
id: kdcube
description: |
  Knowledge about the KDCube platform — a multi‑tenant, self‑hosted runtime + SDK
  for building AI assistants, copilots, and agentic apps. Covers bundles/workflows,
  streaming + timeline, tools/skills, isolated execution, economics/accounting,
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
- Do not write platform-integrated code from skills alone. Before writing code, read the relevant current
  docs/examples/source that define the requested SDK pattern.
- Use only platform symbols, import paths, runtime types, and helper APIs that you have confirmed from
  current docs or source. Do not invent them.
- Be economical: read the smallest relevant set of exact docs/source/example files that can confirm the requested pattern.
- If docs mention candidate code paths, treat those paths as the next files to read.
- If the exact file is still unclear, browse a small relevant subtree in exec and then read the exact discovered files.

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
- `react.search_knowledge` primarily indexes docs metadata, not the whole source tree.
- Source, deployment, and test files are readable by exact `ks:<real-relative-path>` only when the correct path is known.
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
- `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/tests`
  - reusable test fixtures
  - not indexed for search
  - exact-readable when the concrete path is known
  - browseable in exec if you resolve it

When a doc references files and the exact `ks:` path is unclear:
1. If the mapping is obvious, derive the exact logical path and `react.read` it directly.
2. If the mapping is not obvious or you need to browse descendants, use isolated exec with `execute_code_python(...)`.
3. Inside generated code, call `bundle_data.resolve_namespace(...)` on a real subtree such as `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk`, `ks:deployment`, or another exact `ks:` base relevant to the task.
4. Browse the returned exec-local `physical_path`.
5. Emit exact logical refs such as `ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/runtime/execution.py` or `ks:deployment/docker/local-infra-stack/docker-compose.yml` into an OUTPUT_DIR file or short `user.log` note.
6. Back in the React loop, call `react.read(...)` on those emitted logical refs.

For implementation tasks:
- prefer current source/examples over prose when exact symbol names matter
- if a requested integration is still uncertain after docs, read one or more current example/source files before coding
- if examples differ, start from the smallest implementation that matches the confirmed contract and extend only after validation

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
- Streaming chat apps with live widgets and provenance
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
