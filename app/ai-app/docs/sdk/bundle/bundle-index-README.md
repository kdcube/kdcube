---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-index-README.md
title: "Bundle Index"
summary: "Navigation index for bundle documentation: pick the right authoring, runtime, integration, configuration, reference, delivery, and storage docs without reading the whole SDK tree."
tags: ["sdk", "bundle", "docs", "index", "reference"]
keywords: ["bundle docs navigation", "authoring doc index", "runtime doc index", "integration doc index", "configuration doc index", "reference bundle entrypoint", "delivery and storage docs"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/how-to-integrate-with-kdcube-apps-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-developer-guide-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-subsystem-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-write-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-avoid-common-bundle-integration-failures-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-understand-conversation-events-and-react-turns-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-test-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/versatile-reference-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-delivery-and-update-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-agent-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-conversation-events-and-react-output-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-entrypoint-classes-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-properties-and-secrets-lifecycle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-platform-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-transports-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-event-recording-and-sinks-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/providers-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/data-bus-README.md
---
# Bundle Docs Index

This is the bundle-developer start page.

The bundle docs now use one reference bundle only:

- `versatile@2026-03-31-13-36`

Older `docs/sdk/example-bundle` sample docs are no longer part of the bundle path.

For the product-level client integration map — KDCube app UI iframes, embedded
control plane, direct host browser clients, host-server clients, and
backend-only apps — start with
[How To Integrate With KDCube Apps](../../how-to-integrate-with-kdcube-apps-README.md).

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
6. [build/how-to-avoid-common-bundle-integration-failures-README.md](build/how-to-avoid-common-bundle-integration-failures-README.md)
   - recurring implementation recipes: imports, widget origins/assets, visibility, live events, Data Bus, authored events, and resolvers
7. [bundle-subsystem-integration-README.md](bundle-subsystem-integration-README.md)
   - how to mount an existing SDK subsystem as one complete surface: entrypoint, config, widgets, APIs, tools, events, resolvers, storage, and tests
8. [build/how-to-test-bundle-README.md](build/how-to-test-bundle-README.md)
   - builder playbook for validation and runtime checks
9. [versatile-reference-bundle-README.md](versatile-reference-bundle-README.md)
   - the concrete reference bundle to study
10. [bundle-runtime-README.md](bundle-runtime-README.md)
   - runtime surfaces available to bundle code
11. [bundle-event-recording-and-sinks-README.md](bundle-event-recording-and-sinks-README.md)
   - how bundles record selected comm events and send bounded batches to sinks
12. [bundle-conversation-events-and-react-output-README.md](bundle-conversation-events-and-react-output-README.md)
   - how bundles submit `external_events[]` into the conversation lane and consume ReAct timeline output for non-browser delivery
13. [bundle-events-README.md](bundle-events-README.md)
   - bundle-authored events, tool-backed event sources, ReAct policy bindings, UI story events, snapshots, and custom artifact namespace rehosters
14. [build/how-to-understand-conversation-events-and-react-turns-README.md](build/how-to-understand-conversation-events-and-react-turns-README.md)
   - sharp architecture view for conversation event lane, wake queue, bundle-load/on-message fence, ContextBrowser consumer, timeline projection, and stale-owner rollback
15. [bundle-agent-integration-README.md](bundle-agent-integration-README.md)
   - React tools/skills, MCP connector/server patterns, and Claude Code subagent requirements
16. [bundle-entrypoint-classes-README.md](bundle-entrypoint-classes-README.md)
   - which SDK entrypoint base to use: base, economics, memory, or both
17. [bundle-properties-and-secrets-lifecycle-README.md](bundle-properties-and-secrets-lifecycle-README.md)
   - how code defaults, descriptor/admin props, effective bundle props, and bundle secrets flow at runtime
18. [bundle-platform-integration-README.md](bundle-platform-integration-README.md)
   - exact decorator and route contract
19. [bundle-transports-README.md](bundle-transports-README.md)
   - canonical inbound/outbound protocol and transport map
20. [../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../configuration/bundle-runtime-configuration-and-secrets-README.md)
   - platform/global, bundle-scoped, and user-scoped configuration and secrets
21. [bundle-delivery-and-update-README.md](bundle-delivery-and-update-README.md)
   - local reload, registry updates, delivery modes, and deployment-side changes

## Core Doc Map

| Concern | Primary doc |
| --- | --- |
| Fastest Tier 1 reading order | [build/how-to-navigate-kdcube-docs-README.md](build/how-to-navigate-kdcube-docs-README.md) |
| Build a new bundle | [bundle-developer-guide-README.md](bundle-developer-guide-README.md) |
| Configure local runtime, descriptors, and CLI loop | [build/how-to-configure-and-run-bundle-README.md](build/how-to-configure-and-run-bundle-README.md) |
| Builder playbook | [build/how-to-write-bundle-README.md](build/how-to-write-bundle-README.md) |
| Reusable SDK/platform building blocks for bundle assembly | [build/how-to-assemble-bundle-with-sdk-building-blocks-README.md](build/how-to-assemble-bundle-with-sdk-building-blocks-README.md) |
| Recurring bundle implementation recipes and failure modes | [build/how-to-avoid-common-bundle-integration-failures-README.md](build/how-to-avoid-common-bundle-integration-failures-README.md) |
| Mount an existing SDK subsystem as a complete bundle surface | [bundle-subsystem-integration-README.md](bundle-subsystem-integration-README.md) |
| Testing playbook | [build/how-to-test-bundle-README.md](build/how-to-test-bundle-README.md) |
| Study the reference bundle | [versatile-reference-bundle-README.md](versatile-reference-bundle-README.md) |
| Choose the SDK entrypoint base class | [bundle-entrypoint-classes-README.md](bundle-entrypoint-classes-README.md) |
| Runtime surfaces | [bundle-runtime-README.md](bundle-runtime-README.md) |
| Record and sink selected comm events | [bundle-event-recording-and-sinks-README.md](bundle-event-recording-and-sinks-README.md) |
| Conversation agent lanes and Data Bus partitions | [../../service/comm/bus-routing-and-partitioning-README.md](../../service/comm/bus-routing-and-partitioning-README.md) |
| Named provider/client surfaces for namespaced services, object actions, relations, API/MCP/Data Bus adapters | [../namespace-services/providers-README.md](../namespace-services/providers-README.md) |
| Durable bundle-scoped inbound Data Bus messages | [../../service/comm/data-bus-README.md](../../service/comm/data-bus-README.md), [Client Transport Protocols: Data Bus Contract](../../service/comm/client-transport-protocols-README.md#7-data-bus-contract) |
| Submit conversation events and reduce ReAct timeline output for adapters | [bundle-conversation-events-and-react-output-README.md](bundle-conversation-events-and-react-output-README.md) |
| Sharp architecture path for conversation events, wakeups, bundle load/on-message, ReAct ContextBrowser, timeline projection, and stale-owner rollback | [build/how-to-understand-conversation-events-and-react-turns-README.md](build/how-to-understand-conversation-events-and-react-turns-README.md) |
| Bundle-authored events, event sources, ReAct policies, snapshots, and custom namespace rehosters | [bundle-events-README.md](bundle-events-README.md) |
| React, tools/skills, MCP, Claude Code, and file-producing tool integration | [bundle-agent-integration-README.md](bundle-agent-integration-README.md) |
| Decorators, widget/API/public integration, Data Bus handlers, `@on_job` | [bundle-platform-integration-README.md](bundle-platform-integration-README.md) |
| Inbound/outbound transports and protocols | [bundle-transports-README.md](bundle-transports-README.md) |
| Bundle props/secrets lifecycle and merge rules | [bundle-properties-and-secrets-lifecycle-README.md](bundle-properties-and-secrets-lifecycle-README.md) |
| Full props, secrets, raw descriptor reads across all scopes | [../../configuration/bundle-runtime-configuration-and-secrets-README.md](../../configuration/bundle-runtime-configuration-and-secrets-README.md) |
| Reserved platform-owned prop paths | [bundle-reserved-platform-properties-README.md](bundle-reserved-platform-properties-README.md) |
| Bundle lifecycle and instance model | [bundle-lifecycle-README.md](bundle-lifecycle-README.md) |
| Widgets, streaming, operations, background job surface | [bundle-interfaces-README.md](bundle-interfaces-README.md) |
| Bundle-facing browser/UI entry | [bundle-client-ui-README.md](bundle-client-ui-README.md) |
| Product-level KDCube app/client integration modes | [../../how-to-integrate-with-kdcube-apps-README.md](../../how-to-integrate-with-kdcube-apps-README.md) |
| Browser/UI transport contract | [client-transport-protocols-README.md](../../service/comm/client-transport-protocols-README.md) |
| Chat stream event catalog for bundle-facing clients | [chat-stream-events-README.md](../solutions/chat/chat-stream-events-README.md) |
| Frontend behavior under drains/rate limits | [bundle-frontend-awareness-README.md](bundle-frontend-awareness-README.md) |
| Local reload and deployed registry updates | [bundle-delivery-and-update-README.md](bundle-delivery-and-update-README.md) |
| Bundle storage and cache | [bundle-storage-and-cache-README.md](bundle-storage-and-cache-README.md) |
| Scheduled jobs (`@cron`) and job handoff (`@on_job`) | [bundle-scheduled-jobs-README.md](bundle-scheduled-jobs-README.md) |
| Background jobs stream | [../../service/streams/background-jobs-README.md](../../service/streams/background-jobs-README.md) |
| Mixin job dispatch (`super().handle_job(...)`) | [bundle-interfaces-README.md](bundle-interfaces-README.md), [bundle-platform-integration-README.md](bundle-platform-integration-README.md) |
| SDK integrations | [../integrations/README.md](../integrations/README.md) |
| Automations SDK solution | [../solutions/automations/automations-sdk-solution-README.md](../solutions/automations/automations-sdk-solution-README.md) |
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
