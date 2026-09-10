---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/custom-mcp-connector-README.md
title: "Use A Custom MCP Server From KDCube Agents And External Clients"
summary: "Step by step: register your own MCP server in Connection Hub, hand its tools to Claude Desktop or Claude Code through one URL, and let a hosted KDCube agent take them through the application's delegated_resource_families ceiling."
status: active
tags: ["recipes", "connections", "connection-hub", "custom-mcp-connector", "external-mcp", "claude-desktop", "claude-code", "resident-agent", "descriptor"]
updated_at: 2026-09-10
keywords: ["custom MCP connector", "External MCP", "remote MCP proxy", "delegated_resource_families", "resident agent", "Claude Desktop custom connector", "governed tool list", "once or always"]
see_also:
  - https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/custom-mcp-connector.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/surfaces/as-consumer-surfaces-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/apps/consume-mcp-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/delegate-kdcube-service-to-external-client-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/quick-start-local.md
  - https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/testing/end-to-end-acceptance.md
---

# Use A Custom MCP Server From KDCube Agents And External Clients

You have an MCP server (yours, or a paid API's) and you want two things: a
coding agent on your laptop should call a few of its tools, and a hosted
KDCube agent should call them too, without either of them ever holding the
server's key. Connection Hub does that by registering the server once as a
**custom MCP connector**, holding its credential, and putting one governed
proxy between every caller and the server. The concept, the proxy, and the
ceiling are explained in
[Custom MCP Connectors And Governed Invocation](https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/custom-mcp-connector.md).
This page is the procedure.

```text
 you                Connection Hub                     callers
 ----------------   -------------------------------    -----------------------------
 register server -> connector + credential + tools
 grant per caller -> card: resource + tools + policy
                     governed proxy  <---------------- Claude Desktop / Claude Code
                       tools/list, tools/call  <------ hosted KDCube agent
                       credential injected here
                     -> your MCP server
```

## Before you start

- The deployment runs the Connection Hub application (`connection-hub@1-0`)
  and you can sign in to KDCube.
- Your server speaks MCP over streamable HTTP. Connection Hub does not start
  local processes or stdio servers.
- The server is reachable **from the KDCube chat processor**, not from your
  browser. Loopback, private and link-local addresses are refused unless the
  deployment lists them. For a server on the Docker host of a local runtime,
  the endpoint is `http://host.docker.internal:<port>/mcp` and the Connection
  Hub app's `connections.remote_mcp.outbound.allowed_hosts` must contain
  `host.docker.internal`:

  ```yaml
  connections:
    remote_mcp:
      outbound:
        allow_http: true              # local only; keep false on a public deployment
        allow_private_networks: false
        allowed_hosts:
        - host.docker.internal
  ```

- Know the deployment's public origin. The examples use
  `https://<host>`, tenant `<tenant>`, project `<project>`.

## Step 1. Register the server

1. Open Connection Hub (the standalone site at `/sites/connections/` or the
   Connections panel in the chat) and sign in.
2. Open **External MCP**, then **Connect MCP server**.
3. Enter a name and the streamable-HTTP endpoint.
4. Choose the upstream authentication:
   - **No credential** for a public endpoint;
   - **Bearer token** or **Custom header** for a static secret (paste it
     once; it is stored in your server-side secret store);
   - **OAuth browser login** for an OAuth-protected server, with automatic
     client registration or a client you created in the provider console.
     The button reads **Authorize MCP server** in that case.
5. Confirm.

What you should see: the connector enabled, its discovered tools listed with
a descriptor revision, and a credential-presence marker rather than the
value. Nothing has been granted to anyone yet: the connector is a resource,
`urn:connection-hub:remote-mcp:<connector-id>`, that cards can now name.

## Step 2. Use it from an external client

The client needs the door URL:

```text
https://<host>/api/integrations/bundles/<tenant>/<project>/connection-hub@1-0/public/mcp/remote_mcp_proxy
```

This door lists custom connectors only. Use
`.../public/mcp/delegated_mcp_gateway` instead when the client should also
see managed KDCube MCP surfaces granted on the same card, as one list.

**Claude Desktop.** Settings, Connectors, **Add custom connector**. Give it a
name, paste the URL, confirm. The browser opens Connection Hub's consent:
sign in, and you see only your own connectors.

**Claude Code.**

```bash
claude mcp add --scope user --transport http my-kdcube-hub "<the URL>"
claude mcp login my-kdcube-hub
```

Codex, Hermes and OpenClaw follow the same shape; the
[local client helper](https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/local-client-helper.md)
has their commands.

**At consent.** Select the connector, tick the exact tools, and choose a
policy per tool: **Always** for reusable, **Once** for a single invocation.
Approve. Connection Hub creates a delegated card for this client with its
own `access_id`. Find it under **Delegated by KDCube**; the card shows which
door the client entered through.

**Verify.** Give the client this task:

```text
Use only the Connection Hub MCP server configured for this client.
List its available tools. Report their exact names.
Call <one granted tool> once. Report the complete structured result.
```

The list contains only the tools you ticked, under names of the form
`<connector-id>__<tool>_<10 hex>`. Untick a tool on the card, ask again: it
is gone on the next call, with no new login.

## Step 3. Use it from a hosted KDCube agent

A hosted agent belongs to an application. Before any user can grant it a
custom connector, the application must say which user-owned resources that
agent may consume. That declaration is the ceiling.

### 3a. Declare the ceiling in the application descriptor

In `bundles.yaml`, on the agent, under `surfaces.as_consumer.agents.<agent_id>`:

```yaml
surfaces:
  as_consumer:
    agents:
      main:                                   # the agent id as declared by the app
        delegated_resource_families:          # zero or more families; a list
        - id: user_external_mcp               # name shown in capability views and refusals
          resource_kinds:
          - remote_mcp                        # custom connectors have this kind
          authority_sources:
          - delegated_card                    # granted by the user on the agent's card
          transports:
          - streamable-http                   # the transport custom connectors use
          resource_patterns:
          - "urn:connection-hub:remote-mcp:*" # any of the user's connectors
          allowed_tools:
          - "*"                               # any tool name; or list globs like read_*
          max_resources: 8                    # at most 8 connectors bind in one turn
          max_tools_per_resource: 64          # at most 64 tools from each
```

To confine the agent to one server or one tool family, narrow the globs:

```yaml
          resource_patterns:
          - "urn:connection-hub:remote-mcp:<one connector id>"
          allowed_tools:
          - search
          - read_*
          endpoint_hosts:
          - "*.example.com"
```

Every field and its values are in the
[field reference](https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/custom-mcp-connector.md#33-the-fields-of-one-family).
The declaration is a ceiling: it grants nothing. A grant outside it is
refused as `resource_outside_ceiling` or `tool_outside_ceiling`.

### 3b. Reload the application

A local runtime picks the descriptor up with the CLI refresh:

```bash
kdcube refresh --tenant <tenant> --project <project> --path <kdcube checkout>
```

Managed deployments publish the descriptor set through their own pipeline.

### 3c. Grant the connector to the agent

Either path ends on the same card, the agent's card
`kdcube-agent:<application>:<agent>` under **Delegated by KDCube**:

- **From the chat.** Ask the agent to use the server. It attempts the tool it
  does not hold; the consent banner opens the card screen prefilled with
  exactly that connector and tool. Choose Once or Always, approve, retry.
- **From Connection Hub.** Open the agent's card, add the connector, tick
  the tools, choose the policy, save.

### 3d. Verify

- In the chat, open the capability picker (Extensions). The connector's
  tools are listed. A refused one carries its reason, for example
  `resource_outside_ceiling` when the card grants a tool the ceiling does not
  admit.
- Ask the agent to call one tool. The call goes through Connection Hub's
  proxy with the injected credential; the agent never sees the key.
- Untick a tool in the picker. It disappears for this conversation only; the
  card is unchanged.

The same descriptor line works for every hosted runtime, native ReAct,
hosted LangGraph, or Claude Code, because the projection is built by the
shared runtime at the start of each turn.

## Step 4. Change, suspend, remove

| You do | Every caller sees on its next call |
| --- | --- |
| Edit a card: remove a tool, switch Always to Once | The tool gone, or refused after one use |
| Revoke a card | Every tool of that caller refused |
| Disable the connector | Its tools refused for every caller |
| Rediscover and a tool's descriptor changed | That tool suspended until you accept the change |
| Delete the connector | Its resource gone from every card; an OAuth upstream grant revoked first |

No caller logs in again after any of these. There is one record, read on
every call.

## Troubleshooting

- **The client fails to parse JSON at `/.well-known/oauth-protected-resource`.**
  The front proxy answers that root path with the site's HTML. The proxy
  templates carry generated discovery routes; regenerate the proxy from the
  current template.
- **The endpoint is refused before discovery.** It resolves to a private,
  loopback or link-local address, or uses plain HTTP, and the deployment's
  `connections.remote_mcp.outbound` policy does not allow it. Ask the
  operator to list the host; do not add a private-network exception through
  a user connector.
- **The hosted agent shows `resource_outside_ceiling`.** The card grants a
  resource the application's `delegated_resource_families` does not admit.
  Widen the pattern, kind, or tool glob on the agent, then reload.
- **`credential_missing` or `connector_disabled`.** Reconnect or re-enable
  the connector in External MCP.
- **`operation_descriptor_changed`.** The server changed a tool's
  description or schema. Review and accept it in External MCP.
- **`once_exhausted`.** The single invocation was used. Grant again, or
  switch the tool to Always.

## Read next

- [Custom MCP Connectors And Governed Invocation](https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/custom-mcp-connector.md):
  the concept, the doors, the flattening, the ceiling and its fields.
- [Consumer Surfaces](../../sdk/bundle/surfaces/as-consumer-surfaces-README.md):
  the descriptor model this ceiling belongs to.
- [Delegate A KDCube Service To An External Client](delegate-kdcube-service-to-external-client-README.md):
  the same consent and card for a KDCube-managed service.
- [End-To-End Acceptance](https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/testing/end-to-end-acceptance.md):
  the measurable proof with the fixture server.
