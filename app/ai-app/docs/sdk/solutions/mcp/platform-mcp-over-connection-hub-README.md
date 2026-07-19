---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/mcp/platform-mcp-over-connection-hub-README.md
title: "Platform MCP over Connection Hub"
summary: "How a KDCube app exposes its services as an MCP door that Connection Hub guards underneath, so ONE governed endpoint serves both an external app (Claude Code, over OAuth) and a resident agent (a ReAct/LangGraph agent, over a per-agent grant). The built-in kdcube-services app is the worked example: it publishes the named_services and conversations doors, and every caller reaches them as the signed-in user under a delegated credential the guard authorizes per call."
status: active
tags: ["sdk", "mcp", "connection-hub", "delegated-credentials", "named-services", "governance", "agents", "external-app", "kdcube-services"]
updated_at: 2026-07-19
keywords: ["platform MCP", "public MCP door", "managed auth", "delegated_client", "named_services door", "kdcube-services", "external app", "resident agent"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/agent-acting-for-user/agent-acting-for-user-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/configuring-agent-service-access/configuring-agent-service-access-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/quickstart/explore-how-agents-connect-to-kdcube-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/kdcube_for_agents/expose-mcp-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/protect-bundle-mcp-with-managed-credentials-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/mcp-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
---
# Platform MCP over Connection Hub

A KDCube app can expose its own services as an **MCP door** — an authenticated
`@mcp` surface other callers reach as the signed-in user. Underneath that door
sits **Connection Hub**: a managed guard authorizes every call by a *delegated
credential*, never a raw session. The payoff is that **one governed door serves
every caller** — an external app like Claude Code and a resident agent like a
ReAct/LangGraph agent authenticate to the *same* endpoint, differing only in how
they obtained their credential.

This page is the server/solution view — the door and the broker under it. The
caller/identity model (every caller is a per-caller delegated client, consent per
caller, the bound token reused each turn) is
[Agents Acting On Behalf Of The User](../connections/agent-acting-for-user/agent-acting-for-user-README.md);
the end-to-end wiring is
[Configuring Agent Access to Services and Accounts](../connections/configuring-agent-service-access/configuring-agent-service-access-README.md);
consuming an MCP door as agent tools is [MCP](../../tools/mcp-README.md).

## The door and the guard

Declare an MCP surface with `@mcp`, and point its `auth_config` at a
descriptor-owned auth policy:

```python
@mcp(alias="named_services", route="public", transport="streamable-http",
     auth_config="surfaces.as_provider.mcp.named_services.auth")
def named_services_mcp(self, request=None, **kwargs):
    ...
```

```yaml
# the app's descriptor
surfaces:
  as_provider:
    mcp:
      named_services:
        auth:
          mode: managed              # Connection Hub guards the door
          authority_id: delegated_client
          selected_tool_grants: true # the grant's claims gate per-tool access
```

`mode: managed` hands the door's authorization to Connection Hub. On each call
the guard resolves the presented bearer to its **grant record** and checks the
call against it — resource, operation, the granted claims, identity scope, and
expiry — before the tool runs. The app code never sees a session or a provider
key; it sees an authorized request for the resolved user. Full mechanics:
[Protect App MCP with Managed Credentials](../../../recipes/connections/protect-bundle-mcp-with-managed-credentials-README.md).

## One door, two client families

The guard authorizes a *delegated credential*. It does not care how the caller
obtained one — so the same door serves both families:

```text
                 the app's public MCP door
   https://<host>/api/integrations/bundles/<t>/<p>/<app>/public/mcp/<alias>
                            │
         ┌──────────────────┴──────────────────┐
         │                                      │
   external app                          resident agent
   (Claude Code, an OAuth MCP app)       (a ReAct/LangGraph agent the app hosts)
   consent: OAuth authorize + screen     consent: one-click card in chat, per agent
   credential: its editable card         credential: its per-agent grant
         └──────────────────┬──────────────────┘
                            │  each presents a delegated bearer
                     Connection Hub managed guard
                            │  authorizes by the grant record
                            ▼
                     the app's MCP tools run as the user
```

- **External app** — connects over OAuth; its grant is an editable card in
  Connection Hub. See
  [Delegate a KDCube Service to an External Client](../../../recipes/connections/delegate-kdcube-service-to-external-client-README.md).
- **Resident agent** — declares a `delegated: true` MCP connection to the door;
  a missing grant surfaces as a one-click consent card in chat, per agent. See
  [Connect an MCP Service to a KDCube Agent](../../../recipes/kdcube_for_agents/consume-mcp-service-README.md).

Both authenticate to the identical URL and pass the identical guard. Whatever a
door exposes is reachable by either family the moment the user grants it.

## The built-in example: kdcube-services

The `kdcube-services@1-0` app ships built-in and is the worked example of this
pattern. It publishes two managed doors on the `public` route:

| Door | Public URL (fill host/tenant/project) | Serves |
| --- | --- | --- |
| `named_services` | `…/kdcube-services@1-0/public/mcp/named_services` | the named-service namespaces the deployment registers (mail, Slack, conversations, tasks, …) as generic search/schema/get/action tools |
| `conversations` | `…/kdcube-services@1-0/public/mcp/conversations` | read access to the user's conversation history |

Both use `mode: managed`, `authority_id: delegated_client`,
`selected_tool_grants: true`. Because the app is built-in and the connection-hub
delegated-OAuth config carries these doors as delegable resources, a fresh
install can point Claude Code at the `named_services` door and — after the user
grants it — reach the same namespaces a resident agent reaches. The app's own
surfaces and construction are in its bundle README
(`…/examples/bundles/kdcube-services@1-0/README.md`); how namespace operations
become model-callable tools is [Named Services over MCP](../../../recipes/kdcube_for_agents/named-services-mcp-README.md).

## What each side owns

- **The app (server)** — declares the door and its `auth_config`; writes the
  domain tools. It owns nothing about credentials.
- **Connection Hub (guard)** — authorizes each call by the caller's grant record;
  resolves connected-account credentials at trusted boundaries; keeps provider
  tokens out of the door, the model, and the sandbox.
- **The caller** — holds a delegated bearer (an external app's OAuth grant or a
  resident agent's per-agent grant) and reaches the door as the user, within the
  claims the user granted.

Exposing a door and consuming one are their own recipes:
[Expose an MCP Service from a KDCube App](../../../recipes/kdcube_for_agents/expose-mcp-service-README.md)
and [Connect an MCP Service to a KDCube Agent](../../../recipes/kdcube_for_agents/consume-mcp-service-README.md).
The whole Connection Hub picture is
[Connection Hub Solution](../connections/connection-hub-solution-README.md).
