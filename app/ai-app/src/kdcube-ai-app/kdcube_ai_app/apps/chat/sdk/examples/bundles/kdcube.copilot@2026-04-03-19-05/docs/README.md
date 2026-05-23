---
id: ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/kdcube.copilot@2026-04-03-19-05/docs/README.md
title: "KDCube Copilot Bundle Docs"
summary: "Documentation index for the KDCube Copilot reference bundle, including Telegram setup, telemetry sink, and operator integration notes."
tags: ["bundle", "copilot", "docs", "index", "telegram", "integrations", "telemetry"]
keywords: ["kdcube copilot docs", "copilot bundle docs", "telegram setup", "telegram webhook", "telegram mini app", "copilot_webapp", "telemetry sink", "comm.record"]
updated_at: 2026-05-22
see_also:
  - ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/kdcube.copilot@2026-04-03-19-05/doc-reader-README.md
  - ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/kdcube.copilot@2026-04-03-19-05/docs/integrations/telegram-setup.md
  - ks:docs/sdk/bundle/bundle-event-recording-and-sinks-README.md
  - ks:docs/sdk/integrations/telegram/telegram-README.md
  - ks:docs/sdk/bundle/bundle-widget-integration-README.md
---

# KDCube Copilot Bundle Docs

- [Telegram setup](integrations/telegram-setup.md) - webhook, Mini App,
  commands, and the `/start` admin approval flow.
- [Doc reader flow](../doc-reader-README.md) - knowledge-space setup, MCP doc
  reader endpoints, and telemetry sink wiring for on-message and MCP events.
- [Bundle config template](../config/bundles.template.yaml) and
  [secrets template](../config/bundles.secrets.template.yaml) show the expected
  deploy-time shape.
- The copilot WebApp inherits the SDK durable-memory operations through
  `BaseEntrypointWithEconomicsAndMemory`. The memory maintenance contract is
  two phase:
  - `memories_widget_reconcile_run` queues a dry-run proposal job and does not
    mutate memory records. It accepts `agent_type: lite | regular | strong`
    and stores the selected reconciler strength with the background job.
    It also accepts optional JSON-safe `reconciliation_context`, which is
    persisted, enqueued, and rebound under
    `bundle_call_context.memory.reconciliation.context` when the job runs.
    Bundles can override `on_memory_reconciliation_request(request=...)` to
    validate or augment request-local reconciliation controls.
  - `memories_widget_reconcile_export` exposes proposal artifacts for review.
  - `memories_widget_reconcile_apply` applies a succeeded proposal only with
    `confirm: true` and creates a safety snapshot before retire/weaken/merge
    changes.
  - Telegram Mini App wrappers expose the same flow through
    `telegram_memories_widget_reconcile_*` public APIs.

## Telemetry Sink

The copilot bundle can forward selected `comm.record(...)` events to an
external POST endpoint. It does not persist telemetry locally and does not
construct receiver URLs.

Bundle config:

```yaml
telemetry_sink:
  endpoint_url: "https://stats.example.internal/telemetry/events"
```

Bundle secret:

```yaml
telemetry_sink:
  auth:
    token: "<bearer-token>"
```

Runtime behavior:
- On chat turns, `pre_run_hook` configures `comm.record(...)` with a React
  scope and `post_run_hook` sends the recorded batch through the SDK
  `StatsTelemetrySink`.
- On doc-reader MCP calls, the MCP tool callback uses an async scoped recorder
  and sends only the `kdcube.copilot.mcp.call` event for that call.
- If either the endpoint URL or token is missing, the sink is disabled and no
  local fallback file is written.
