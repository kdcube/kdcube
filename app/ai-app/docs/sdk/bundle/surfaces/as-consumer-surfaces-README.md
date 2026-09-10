---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/surfaces/as-consumer-surfaces-README.md
title: "Consumer Surfaces (surfaces.as_consumer)"
summary: "The surface model for what a KDCube app consumes: fixed MCP connections, bounded families of user-owned resources, per-agent tool authority, and scene UI consumption."
status: active
tags: ["sdk", "bundle", "surfaces", "as-consumer", "agents", "tools", "mcp", "delegated", "governance"]
keywords: ["delegated resource families", "resident agent resources", "user-owned MCP", "effective authority intersection"]
updated_at: 2026-09-04
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/surfaces/as-provider-surfaces-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/apps/consume-mcp-service-README.md
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
  agents.<agent_id>.delegated_resource_families
                          ceiling for compatible user-owned resources
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
journey: [Connect An MCP Service To A KDCube Agent](../../../recipes/apps/consume-mcp-service-README.md).

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

## Dynamic User-Owned Resource Families

An app can let a resident agent use compatible resources that a user adds after
deployment. The descriptor declares a bounded family; the user's live
Connection Hub card selects the exact resource ids and operations inside that
family:

```yaml
surfaces:
  as_consumer:
    agents:
      main:
        delegated_resource_families:
          - id: user_external_mcp
            resource_kinds: [remote_mcp]
            authority_sources: [delegated_card]
            transports: [streamable-http]
            resource_patterns:
              - "urn:connection-hub:remote-mcp:*"
            allowed_tools: [search, read_*]
            max_resources: 4
            max_tools_per_resource: 20
```

`id`, `resource_kinds`, `transports`, and `resource_patterns` are required.
`authority_sources` defaults to `delegated_card`; `allowed_tools` defaults to
`["*"]`; `max_resources` and `max_tools_per_resource` default to 8 and 64.
Endpoint scheme and host patterns are optional additional ceilings.

The descriptor stores resource classes and limits. Connection Hub stores the
user's exact connector ids, selected operations, accepted descriptor evidence,
and Once/Always policies on the resident caller's one stable card. Fixed
managed resources and compatible user-owned resources coexist on that card.
Saving a connector makes
it available for delegation; the card grants it to the caller profile. A
conversation selection can then remove a resource or operation for that
conversation.

```text
effective tools
  = app and agent descriptor ceiling
  intersect current user-owned delegated card
  intersect current provider and connected-account readiness
  intersect conversation narrowing
```

Every field of a family, its allowed values, the refusal reasons, and the way
the ceiling meets the user's card are explained for readers in
[Custom MCP Connectors And Governed Invocation](https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/custom-mcp-connector.md);
the procedure is [Use A Custom MCP Server From KDCube Agents And External Clients](../../../recipes/connections/custom-mcp-connector-README.md).

The host supplies a current Card/Gateway facts loader and a trusted per-call
bearer resolver. KDCube rebuilds the projection for each turn and binds the
aggregate delegated MCP Gateway in the trusted supervisor. Provider credentials
and delegated bearers remain outside model context and generated-code runtime
globals.

Fixed app-owned MCP connections continue to use `mcp.services` plus an agent's
`tools` entry. Their `authority_source` is `application`; the capability view
keeps them separate from resources delegated by the user.

## Scene UI consumption

`surfaces.as_consumer.ui.scene` declares the scene surfaces an app's UI may
summon from a hosting scene (windows, panels of other apps). It is the UI
counterpart of the tool inventory: consumption is declared, not assumed. Scene
mechanics live with the widget docs —
[Bundle Widget Integration](../bundle-widget-integration-README.md).
