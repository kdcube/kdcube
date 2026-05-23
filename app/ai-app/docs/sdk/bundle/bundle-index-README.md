---
id: ks:docs/sdk/bundle/bundle-index-README.md
title: "Bundle Index"
summary: "Navigation index for bundle documentation: pick the right authoring, runtime, integration, configuration, reference, delivery, and storage docs without reading the whole SDK tree."
tags: ["sdk", "bundle", "docs", "index", "reference"]
keywords: ["bundle docs navigation", "authoring doc index", "runtime doc index", "integration doc index", "configuration doc index", "reference bundle entrypoint", "delivery and storage docs"]
see_also:
  - ks:docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md
  - ks:docs/sdk/bundle/bundle-developer-guide-README.md
  - ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-write-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md
  - ks:docs/sdk/bundle/build/how-to-test-bundle-README.md
  - ks:docs/sdk/bundle/versatile-reference-bundle-README.md
  - ks:docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - ks:docs/sdk/bundle/bundle-delivery-and-update-README.md
  - ks:docs/sdk/bundle/bundle-agent-integration-README.md
  - ks:docs/sdk/bundle/bundle-entrypoint-classes-README.md
  - ks:docs/sdk/bundle/bundle-properties-and-secrets-lifecycle-README.md
  - ks:docs/sdk/bundle/bundle-platform-integration-README.md
  - ks:docs/sdk/bundle/bundle-transports-README.md
  - ks:docs/sdk/bundle/bundle-event-recording-and-sinks-README.md
---
# Bundle Docs Index

This is the bundle-developer start page.

The bundle docs now use one reference bundle only:

- `versatile@2026-03-31-13-36`

Older `docs/sdk/example-bundle` sample docs are no longer part of the bundle path.

## Read in this order

1. [build/how-to-navigate-kdcube-docs-README.md](build/how-to-navigate-kdcube-docs-README.md)
   - shortest path for bundle creators, wrappers, integrators, and readers
2. [bundle-developer-guide-README.md](bundle-developer-guide-README.md)
   - minimal authoring contract
   - local bundle layout
   - config/secrets/update loop
3. [build/how-to-configure-and-run-bundle-README.md](build/how-to-configure-and-run-bundle-README.md)
   - exact local runtime contract
   - `assembly.yaml`, `bundles.yaml`, `bundles.secrets.yaml`
   - `kdcube refresh --upstream --build` and `kdcube info`
   - sharp `path` / `module` rules
4. [build/how-to-write-bundle-README.md](build/how-to-write-bundle-README.md)
   - builder playbook for designing and structuring a bundle
5. [build/how-to-assemble-bundle-with-sdk-building-blocks-README.md](build/how-to-assemble-bundle-with-sdk-building-blocks-README.md)
   - reusable SDK/platform blocks: Tasks, Email, Telegram, Delivery, tools, storage, widgets, jobs, MCP, Claude Code
6. [build/how-to-test-bundle-README.md](build/how-to-test-bundle-README.md)
   - builder playbook for validation and runtime checks
7. [versatile-reference-bundle-README.md](versatile-reference-bundle-README.md)
   - the concrete reference bundle to study
8. [bundle-runtime-README.md](bundle-runtime-README.md)
   - runtime surfaces available to bundle code
9. [bundle-event-recording-and-sinks-README.md](bundle-event-recording-and-sinks-README.md)
   - how bundles record selected comm events and send bounded batches to sinks
10. [bundle-agent-integration-README.md](bundle-agent-integration-README.md)
   - React tools/skills, MCP connector/server patterns, and Claude Code subagent requirements
11. [bundle-entrypoint-classes-README.md](bundle-entrypoint-classes-README.md)
   - which SDK entrypoint base to use: base, economics, memory, or both
12. [bundle-properties-and-secrets-lifecycle-README.md](bundle-properties-and-secrets-lifecycle-README.md)
   - how code defaults, descriptor/admin props, effective bundle props, and bundle secrets flow at runtime
13. [bundle-platform-integration-README.md](bundle-platform-integration-README.md)
   - exact decorator and route contract
14. [bundle-transports-README.md](bundle-transports-README.md)
   - canonical inbound/outbound protocol and transport map
15. [../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../configuration/bundle-runtime-configuration-and-secrets-README.md)
   - platform/global, bundle-scoped, and user-scoped configuration and secrets
16. [bundle-delivery-and-update-README.md](bundle-delivery-and-update-README.md)
   - local reload, registry updates, delivery modes, and deployment-side changes

## Core Doc Map

| Concern | Primary doc |
| --- | --- |
| Fastest Tier 1 reading order | [build/how-to-navigate-kdcube-docs-README.md](build/how-to-navigate-kdcube-docs-README.md) |
| Build a new bundle | [bundle-developer-guide-README.md](bundle-developer-guide-README.md) |
| Configure local runtime, descriptors, and CLI loop | [build/how-to-configure-and-run-bundle-README.md](build/how-to-configure-and-run-bundle-README.md) |
| Builder playbook | [build/how-to-write-bundle-README.md](build/how-to-write-bundle-README.md) |
| Reusable SDK/platform building blocks for bundle assembly | [build/how-to-assemble-bundle-with-sdk-building-blocks-README.md](build/how-to-assemble-bundle-with-sdk-building-blocks-README.md) |
| Testing playbook | [build/how-to-test-bundle-README.md](build/how-to-test-bundle-README.md) |
| Study the reference bundle | [versatile-reference-bundle-README.md](versatile-reference-bundle-README.md) |
| Choose the SDK entrypoint base class | [bundle-entrypoint-classes-README.md](bundle-entrypoint-classes-README.md) |
| Runtime surfaces | [bundle-runtime-README.md](bundle-runtime-README.md) |
| Record and sink selected comm events | [bundle-event-recording-and-sinks-README.md](bundle-event-recording-and-sinks-README.md) |
| React, tools/skills, MCP, Claude Code, and file-producing tool integration | [bundle-agent-integration-README.md](bundle-agent-integration-README.md) |
| Decorators, widget/API/public integration, `@on_job` | [bundle-platform-integration-README.md](bundle-platform-integration-README.md) |
| Inbound/outbound transports and protocols | [bundle-transports-README.md](bundle-transports-README.md) |
| Bundle props/secrets lifecycle and merge rules | [bundle-properties-and-secrets-lifecycle-README.md](bundle-properties-and-secrets-lifecycle-README.md) |
| Full props, secrets, raw descriptor reads across all scopes | [../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../configuration/bundle-runtime-configuration-and-secrets-README.md) |
| Reserved platform-owned prop paths | [bundle-reserved-platform-properties-README.md](bundle-reserved-platform-properties-README.md) |
| Bundle lifecycle and instance model | [bundle-lifecycle-README.md](bundle-lifecycle-README.md) |
| Widgets, streaming, operations, background job surface | [bundle-interfaces-README.md](bundle-interfaces-README.md) |
| Bundle-facing browser/UI entry | [bundle-client-ui-README.md](bundle-client-ui-README.md) |
| Browser/UI transport contract | [bundle-client-communication-README.md](bundle-client-communication-README.md) |
| Chat stream event catalog for bundle-facing clients | [bundle-chat-stream-events-README.md](bundle-chat-stream-events-README.md) |
| Frontend behavior under drains/rate limits | [bundle-frontend-awareness-README.md](bundle-frontend-awareness-README.md) |
| Local reload and deployed registry updates | [bundle-delivery-and-update-README.md](bundle-delivery-and-update-README.md) |
| Knowledge space (`ks:`) | [bundle-knowledge-space-README.md](bundle-knowledge-space-README.md) |
| Bundle storage and cache | [bundle-storage-and-cache-README.md](bundle-storage-and-cache-README.md) |
| Scheduled jobs (`@cron`) and job handoff (`@on_job`) | [bundle-scheduled-jobs-README.md](bundle-scheduled-jobs-README.md) |
| Background jobs stream | [../../service/streams/background-jobs-README.md](../../service/streams/background-jobs-README.md) |
| Mixin job dispatch (`super().handle_job(...)`) | [bundle-interfaces-README.md](bundle-interfaces-README.md), [bundle-platform-integration-README.md](bundle-platform-integration-README.md) |
| SDK integrations | [../integrations/README.md](../integrations/README.md) |
| Tasks SDK solution | [../solutions/tasks-README.md](../solutions/tasks-README.md) |
| Cached subprocess virtualenv helpers (`@venv`) | [bundle-venv-README.md](bundle-venv-README.md) |
| Outbound event filtering | [bundle-firewall-README.md](bundle-firewall-README.md) |
| Python-to-Node backend bridge | [bundle-node-backend-bridge-README.md](bundle-node-backend-bridge-README.md) |

For files produced by bundle tools, start with
[bundle-agent-integration-README.md](bundle-agent-integration-README.md) and
[bundle-runtime-README.md](bundle-runtime-README.md). The strict tool result
contract is `ret.artifact_type: "files"` plus `ret.files[]`; trusted catalog
tools may also call `bundle_tool_context.host_files(...)`, including in isolated
supervisor/runtime execution. `host_files(...)` requires prepared tool context
from `BaseWorkflow.build_react(...)` or isolated `bootstrap_bind_all(...)`.

Runnable sidecar details:
- [node-backend-sidecar-README.md](../node/node-backend-sidecar-README.md)

## Reference Bundle

Reference bundle root:

`src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36`

Start with:

- the reference doc:
  [versatile-reference-bundle-README.md](versatile-reference-bundle-README.md)
- then the actual bundle README:
  `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/versatile@2026-03-31-13-36/README.md`

## Validation

Shared SDK bundle tests:

`src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/tests/bundle`

Default combined runner:

```bash
PYTHONPATH=app/ai-app/src/kdcube-ai-app \
python -m kdcube_ai_app.apps.chat.sdk.tests.bundle.run_bundle_suite \
  --bundle-path /abs/path/to/bundle
```
