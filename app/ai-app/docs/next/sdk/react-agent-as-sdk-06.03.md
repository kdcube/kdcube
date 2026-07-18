---
id: ks:docs/next/sdk/react-agent-as-sdk-06.03.md
title: "Draft: React Agent as Standalone SDK"
summary: "Draft design for exposing a minimal standalone React-turn SDK that reuses communicator events and key-value configuration without requiring the full platform."
draft: true
status: proposal
tags: ["next", "sdk", "react", "agents", "design", "standalone"]
keywords: ["react standalone sdk", "single turn sdk surface", "communicator event contract", "standalone configuration model", "react runtime extraction", "draft sdk design"]
see_also:
  - ks:docs/sdk/agents/react/flow-README.md
  - ks:docs/sdk/agents/react/react-context-README.md
  - ks:docs/configuration/bundle-runtime-configuration-and-secrets-README.md
---
# React agent as SDK (draft)

Date: 2026-03-07

## Goals
- Provide a minimal SDK surface to run a single React turn without the full platform.
- Keep configuration key-value based (config + secrets), so the SDK can run standalone.
- Use the same communicator event stream the platform uses, so clients can replay or fetch artifacts later.

## Event surface (communicator)
The SDK should emit all turn-related events via the communicator. These are required for any client to render the turn correctly.

Must emit (existing are marked with "already in communicator"):
- Widget lifecycle
  - Code widget: code streamed, execution started, execution result ready (already in communicator)
  - Canvas widget: canvas data streamed, timeline text streamed (already in communicator)
  - Web search / fetch widget: status + results (already in communicator)
- File produced (already in communicator)
- Sources used in generation
- File read request (pull)
- Source read
- Skill read
- Final answer (assistant completion) (already in communicator)

Notes:
- The communicator already records outbound events, which enables replay or fetch later.
- We can reuse the same "fetch turn" path used in platform, e.g. ctx_rag fetch_conversation_artifacts.
  - See: kdcube_ai_app/apps/chat/sdk/solutions/conversation/ctx_rag.py

## SDK pseudo code
```python
# Simple example demonstrating the events that happen in React.

with react("turn_id", "conversation_id", "user_id", bundle_descriptor) as agent:
    agent.subscribe("file_produced", fn_file_produced)
    agent.subscribe("code_widget", fn_code_widget)
    agent.subscribe("canvas_widget", fn_canvas_widget)
    agent.subscribe("web_search", fn_web_search)
    agent.subscribe("source_used", fn_source_used)
    agent.subscribe("source_read", fn_source_read)
    agent.subscribe("file_read", fn_file_read)
    agent.subscribe("skill_read", fn_skill_read)
    agent.subscribe("completion", fn_completion)

    await agent.prompt(user_prompt, user_attachments)

# Assess agent state
agent.plan
agent.sources_pool
agent.files
agent.errors
agent.user.prompt
agent.assistant.completion
agent.citations
```

## SDK surface (draft)
- react(turn_id, conversation_id, user_id, *, config=None, secrets=None, communicator=None)
- agent.subscribe(event_name, handler)
- agent.prompt(prompt, attachments=None)
- agent.plan
- agent.sources_pool
- agent.files
- agent.errors
- agent.user.prompt
- agent.assistant.completion
- agent.citations

## Recording and retrieval
- All events are streamed through the communicator.
- The communicator also persists the stream and can be queried later.
- The SDK can expose a "fetch turn" function that calls ctx_rag fetch_conversation_artifacts.

## Minimal settings scope (SDK)
The SDK must run with a minimal key-value config set. Example (non-secret values):
- TENANT
- PROJECT
- INSTANCE_ID
- STORAGE_PATH
- BUNDLE_STORAGE_PATH
- REDIS_URL
- PG_DSN
- BUNDLE_REGISTRY_PATH (optional)
- HOST_BUNDLES_PATH (optional, local dev)
- LOG_DIR
- COMMUNICATOR_BACKEND (redis or in-memory)

Secrets (see next section):
- LLM provider API keys
- DB / Redis credentials
- Git credentials for private bundles

## Settings integration (draft)
- Current Settings usage lives in:
  - kdcube_ai_app/apps/chat/sdk/config.py
  - kdcube_ai_app/apps/chat/ingress/resolvers.py
- The SDK should allow swapping the Settings backend so it can read:
  - Key-value config (values.yaml)
  - Secret manager provider (secrets.yaml or external)
  - Environment variables (fallback)

## Configuration model (key-value)
Goal: same semantics as GitHub Actions secrets and vars.

Proposal:
- Two files, both key-value:
  - values.yaml (non-secret)
  - secrets.yaml (secret)
- The SDK can accept both as dictionaries or paths.
- The same schema should be usable by docker-compose and by a local process.

## Secret storage (design notes)
Problem:
- Plain env files and docker env are readable by any process with access to the container or host.
- This is weak for local installs and for multi-user machines.

Proposed direction:
- Use a dedicated "secret manager" module for local installs.
- Keep it non-transparent and separate from the open-source runtime code path.
- Processes obtain secrets by key at runtime, not by direct file/env reads.

Ideas to explore:
- Linux peer credentials to validate a caller.
- A local secret daemon that only answers on a UNIX domain socket.
- A bootstrap flow in the installer that stores secrets once and only returns handles.

Note:
- This is an architectural direction, not yet implemented.

## Q&A
- Q: Which minimal communicator implementation should we ship for SDK-only use?
  A: An in-memory communicator or redis run as infra stack.

- Q: What is the minimal storage backend to keep "fetch turn" working without full platform? 
  A: Both local fs for single-node deployment and s3 otherwise as storage backend and Postgres for indices, metadata and semantic/hybrid search.


We want to be able to construct and run react agent which will be able to use custom tools defined in the bundle defined by bundle_descriptor, i.e. 
something like 
