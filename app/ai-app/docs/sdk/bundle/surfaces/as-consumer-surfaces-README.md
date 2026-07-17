---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/surfaces/as-consumer-surfaces-README.md
title: "Consumer Surfaces (surfaces.as_consumer)"
summary: "The surface model for what a KDCube app CONSUMES: the MCP service registry, the per-agent tool inventory that governs which agent sees which tools with which identity (static app credential vs delegated per-user, per-agent consent), and scene UI consumption."
status: active
tags: ["sdk", "bundle", "surfaces", "as-consumer", "agents", "tools", "mcp", "delegated", "governance"]
updated_at: 2026-07-18
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/surfaces/as-provider-surfaces-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/kdcube_for_agents/consume-mcp-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/agent-acting-for-user/agent-acting-for-user-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/claim-driven-consent/claim-driven-consent-README.md
---
# Consumer Surfaces (`surfaces.as_consumer`)

The mirror of [Provider Surfaces](as-provider-surfaces-README.md): what a
KDCube **app** (the deployable unit still named a **bundle** in platform
identifiers) reaches out to. The `surfaces.as_consumer` descriptor family is
the app's outbound contract — connections it may open, and which of its agents
may use them.

```text
surfaces.as_consumer
  mcp.services            connection registry: where servers are, how to authenticate
  default_agent           which agent answers when none is addressed
  agents.<agent_id>.tools per-agent inventory: what THIS agent may see and call
  ui.scene                scene surfaces this app's UI summons from hosts
```

## The two-level governance split

Registering a connection and granting an agent access to it are separate
declarations, and that separation is the governance boundary:

```text
mcp.services         answers  "where is it, how does the APP authenticate?"
agents.<id>.tools    answers  "may THIS agent know and call it, as whom?"
```

One registered server can serve several agents with different allow-lists; an
agent sees only its own inventory. The runtime resolves both into an
agent-scoped tool catalog (`agent_tool_config_from_bundle_props`), and the
user's capability picker narrows it further — the effective set is always
*admin ceiling ∩ user selection*. Full descriptor shapes and the runtime
journey: [Connect An MCP Service To A KDCube Agent](../../../recipes/kdcube_for_agents/consume-mcp-service-README.md).

## Identity: three ways a consumed call is authenticated

Every consumed tool call runs under one of three identities, chosen per
connection:

| Mode | Declaration | The call acts as |
| --- | --- | --- |
| App credential | `auth: {type: bearer/api_key/header, secret: b:…}` | the app itself (one shared credential) |
| Delegated, per user per agent | `delegated: true` + `scopes: [claims]` | the signed-in user, under THIS agent's consent grant |
| Connected account | tool-level `connected_accounts` claims | the user's external provider account (Slack, Gmail) |

The delegated mode makes the agent a Delegated-By-KDCube client entity with a
per-agent grant the user gives and revokes in Connection Hub; while consent is
pending the connection stays unbound and a consent demand rises in chat —
[Agents Acting On Behalf Of The User](../../solutions/connections/agent-acting-for-user/agent-acting-for-user-README.md).
The consent states each mode surfaces in the picker are unified claim-first:
[Claim-Driven Consent](../../solutions/connections/claim-driven-consent/claim-driven-consent-README.md).

## Scene UI consumption

`surfaces.as_consumer.ui.scene` declares the scene surfaces an app's UI may
summon from a hosting scene (windows, panels of other apps). It is the UI
counterpart of the tool inventory: consumption is declared, not assumed. Scene
mechanics live with the widget docs —
[Bundle Widget Integration](../bundle-widget-integration-README.md).
