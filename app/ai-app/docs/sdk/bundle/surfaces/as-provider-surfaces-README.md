---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/surfaces/as-provider-surfaces-README.md
title: "Provider Surfaces (surfaces.as_provider)"
summary: "The surface model for what a KDCube app OFFERS: the decorator family (@on_reactive_event, @api, @mcp, @ui_widget, @cron, @on_job, data bus, public content) paired with the surfaces.as_provider descriptor family that declares intent, visibility, and authentication ownership per surface."
status: active
tags: ["sdk", "bundle", "surfaces", "as-provider", "api", "mcp", "widgets", "reactive-events", "governance"]
updated_at: 2026-07-18
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/surfaces/as-consumer-surfaces-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-transports-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-interfaces-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/kdcube_for_agents/expose-mcp-service-README.md
---
# Provider Surfaces (`surfaces.as_provider`)

A KDCube **app** (the deployable unit still named a **bundle** in
`bundle_id`, `bundles.yaml`, and `/bundles/` routes) meets the world through
**surfaces**. A surface is one offered capability with three coordinates:

```text
what code answers        an entrypoint method marked by a surface decorator
who may reach it         visibility + authentication, declared per surface
what the platform makes of it   product intent, declared in the descriptor
```

The entrypoint class is the runtime-discoverable container of surfaces; an app
exposes any subset. Declaring one surface implies nothing about the others —
an app with only widgets is as valid as one with only a chat workflow.

## The surface inventory

| Surface | Declared by | What it offers | Mechanics |
| --- | --- | --- | --- |
| Reactive events | `@on_reactive_event` (one per entrypoint) | The chat/agent turn path: every external event (user message, followup, channel ingress) enters here | [Bundle Transports § Chat Turn Path](../bundle-transports-README.md) |
| REST operations | `@api(method, alias, route)` | Authenticated `operations` and unauthenticated `public` HTTP endpoints | [Bundle Transports § REST Operations](../bundle-transports-README.md) |
| MCP endpoints | `@mcp(alias, route)` | An MCP server surface (streamable HTTP) other agents and external clients call | [Bundle Transports § MCP Endpoints](../bundle-transports-README.md), [Expose An MCP Service](../../../recipes/kdcube_for_agents/expose-mcp-service-README.md) |
| Widgets | `@ui_widget`, `@ui_main` | Served UI (iframe widgets, the app's main panel) | [Bundle Interfaces § Exposing a widget](../bundle-interfaces-README.md) |
| Background jobs | `@on_job`, `@cron` | Queue-driven and scheduled work | [Bundle Interfaces § Background job interface](../bundle-interfaces-README.md), [Scheduled Jobs](../bundle-scheduled-jobs-README.md) |
| Data bus | `@data_bus_handler` | Durable widget↔app message exchange | [Bundle Interfaces § Durable Data Bus](../bundle-interfaces-README.md) |
| Public content | `@public_content` | Discoverable published content | [Public Content Provider](../public-content-provider-README.md) |
| Named services | provider registration | Typed operations other apps call in-process or over API | [Bundle Subsystem Integration](../bundle-subsystem-integration-README.md) |

## The descriptor family: `surfaces.as_provider.*`

Code declares that a surface *exists*; the descriptor declares what it *means*
in this deployment. The keys attach per surface:

```yaml
surfaces:
  as_provider:
    bundle:
      default_chat: true                  # product intent: this app serves the
                                          # default chat surface
      visibility:
        allowed_roles: [kdcube:role:registered]
    api:
      operations:
        admin_data:                       # one @api alias
          visibility:
            user_types: [...]
            roles: [...]
    mcp:
      memories:                           # one @mcp alias
        auth: connection_hub_managed      # who authenticates this surface
    widget:
      <alias>:
        visibility: { ... }
```

Two rules keep this honest:

- **Intent lives in the descriptor, never inferred from code.** An app serves
  the default chat surface because `surfaces.as_provider.bundle.default_chat`
  says so — inheriting a base class that *could* serve chat declares nothing.
- **Each surface names its authentication owner.** An `@mcp` or `@api` surface
  is public, app-authenticated, or Connection Hub managed — a per-surface
  choice with different guard stacks, detailed in
  [Bundle Transports § Who authenticates MCP](../bundle-transports-README.md).

## Provider surfaces are consumed as surfaces

What one app provides, another app (or an external client) consumes: a
provided `@mcp` surface appears in a consumer's
`surfaces.as_consumer.mcp.services`; a provided widget mounts in a scene host;
a Connection Hub managed surface admits delegated clients (external connectors,
hosted agents acting for the user). The consuming half of the model is
[Consumer Surfaces](as-consumer-surfaces-README.md).
