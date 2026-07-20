---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/kdcube_for_agents/consume-mcp-service-README.md
title: "Connect An MCP Service To A KDCube Agent"
summary: "Builder recipe for registering an MCP server once, exposing an allow-listed tool view to each KDCube agent through surfaces.as_consumer, resolving secrets, and verifying the resulting mcp.<alias>.<tool> catalog and runtime calls."
status: active
tags: ["recipes", "kdcube-for-agents", "mcp", "as-consumer", "agents", "tools", "governance"]
updated_at: 2026-07-18
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/kdcube_for_agents/expose-mcp-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/agent-acting-for-user/agent-acting-for-user-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/mcp-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/tool-subsystem-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/multi-action/tool-strategy-traits-README.md
---
# Connect An MCP Service To A KDCube Agent

Use this recipe when a KDCube app needs one or more of its agents to call tools
from an MCP server.

In builder-facing language, a KDCube **app** is the deployable unit still named a
**bundle** in current routes, descriptors, and SDK identifiers. This recipe says
"app" except where `bundle_id`, `bundles.yaml`, or `/bundles/` is the literal
platform contract.

The integration has two declarations:

```text
surfaces.as_consumer.mcp.services
  how this app reaches and authenticates to MCP servers

surfaces.as_consumer.agents.<agent_id>.tools
  which of those servers and tools this particular agent may see
```

Registering a server does not expose it to every agent. Attaching a server to one
agent does not expose it to another. That separation is the governance boundary.

```text
                           app config
                               |
              +----------------+----------------+
              |                                 |
              v                                 v
     MCP connection registry             per-agent tool policy
     endpoint / transport / auth          server / alias / allow-list
              |                                 |
              +----------------+----------------+
                               |
                               v
                   KDCube MCP tool subsystem
                               |
                               v
                    mcp.<alias>.<tool_name>
```

## 1. Register The MCP Server

Put the connection under `surfaces.as_consumer.mcp.services.mcpServers` in the
app's item in `bundles.yaml`:

```yaml
bundles:
  version: "1"
  items:
    - id: research-assistant@1-0
      config:
        surfaces:
          as_consumer:
            mcp:
              services:
                mcpServers:
                  docs:
                    transport: streamable-http
                    url: https://mcp.example.com/docs
```

The key `docs` is the `server_id` used by agent tool declarations.

Supported consumer transports:

| Transport | Required connection fields | Where it runs |
| --- | --- | --- |
| `streamable-http` or `http` | `url` | The KDCube process that owns the MCP tool subsystem |
| `sse` | `url` | The KDCube process that owns the MCP tool subsystem |
| `stdio` | `command`, optional `args` and `env` | A child process started by that runtime |

The URL must be reachable from the runtime process, not only from the builder's
browser. In Docker, `127.0.0.1` points to the current container. Use service DNS
or a reachable internal/public host when the MCP server runs elsewhere.

## 2. Attach An Allow-Listed View To An Agent

Attach the server under the consuming agent:

```yaml
surfaces:
  as_consumer:
    default_agent: main
    agents:
      main:
        tools:
          - name: documentation
            kind: mcp
            server_id: docs
            alias: docs
            allowed:
              - search
              - read_document
            tool_traits:
              search:
                strategy: [exploration]
              read_document:
                strategy: [exploration]
```

The model-facing tool ids become:

```text
mcp.docs.search
mcp.docs.read_document
```

Rules:

- `server_id` must match a configured MCP server key.
- `alias` must be one segment and becomes the middle segment of the tool id.
- `allowed: ["*"]` exposes every tool returned by `tools/list`.
- A concrete `allowed` list is the safer production default.
- `tool_traits` describes runtime strategy/execution policy; it is not an auth
  token and does not replace the allow-list.

## 3. Give Different Agents Different Views

One connection can serve several agents without giving them equal authority:

```yaml
surfaces:
  as_consumer:
    mcp:
      services:
        mcpServers:
          crm:
            transport: streamable-http
            url: https://mcp.example.com/crm
    agents:
      analyst:
        tools:
          - kind: mcp
            server_id: crm
            alias: crm
            allowed: [search_customers, read_customer]
            tool_traits:
              "*":
                strategy: [exploration]
      operator:
        tools:
          - kind: mcp
            server_id: crm
            alias: crm
            allowed: [search_customers, read_customer, update_customer]
            tool_traits:
              search_customers:
                strategy: [exploration]
              read_customer:
                strategy: [exploration]
              update_customer:
                strategy: [exploitation]
```

```text
same MCP server
     |
     +-> analyst catalog: read/search only
     |
     `-> operator catalog: read/search/update
```

This is why the server registry and agent inventory are separate. Connection
configuration answers "where is it?" Agent configuration answers "may this
agent know and call it?"

## 4. Authenticate Without Putting Secrets In App Config

For bearer auth, reference an app secret:

```yaml
# bundles.yaml
surfaces:
  as_consumer:
    mcp:
      services:
        mcpServers:
          private_docs:
            transport: streamable-http
            url: https://mcp.example.com/private-docs
            auth:
              type: bearer
              secret: b:mcp.private_docs.token
```

```yaml
# bundles.secrets.yaml, under the same app item
bundles:
  version: "1"
  items:
    - id: research-assistant@1-0
      secrets:
        mcp:
          private_docs:
            token: replace-through-the-secret-provider
```

Other supported non-interactive client auth shapes:

```yaml
auth:
  type: api_key
  header: X-API-Key
  secret: b:mcp.vendor.api_key
```

```yaml
auth:
  type: header
  header: X-Partner-Token
  secret: b:mcp.partner.token
```

The runtime resolves the secret when it creates the MCP client. Secret values
must not be placed in `bundles.yaml`, tool parameters, logs, generated code, or
the model prompt.

Interactive browser/device authentication (`oauth_gui`, `device_code`, and
similar profiles) is not a headless agent connection contract. Such a server is
omitted from the tool catalog. Use a non-interactive credential provisioned for
the app, or build a user-connected-account adapter that resolves a server-side
credential before the tool call.

### Delegated: call a KDCube service as the signed-in user

A static bearer represents the app. When the target is a KDCube `@mcp` surface
that serves the user's own data (memories, tasks), the agent must act **as the
user, per agent** — mark the connection `delegated` and declare the claims it
needs instead of configuring a credential:

```yaml
- name: memories
  kind: mcp
  server_id: memories
  url: https://runtime.example/api/integrations/bundles/<T>/<P>/user-memories@2026-06-26/public/mcp/memories
  resource: "*/api/integrations/bundles/*/*/user-memories@2026-06-26/public/mcp/memories*"
  transport: streamable_http
  delegated: true
  scopes: [memories:read]
```

`url` is the concrete endpoint the client dials. `resource` is the
delegated-resource id from the deployment's
`delegated_credentials.oauth.resources` catalog — commonly a wildcard pattern.
The consent grant is created, validated, and looked up under that exact id
(the guard matches the request URL against it), so `resource` must byte-match
the catalog entry; with no `resource`, the `url` is used and must then itself
be the configured id.

At bind time the runtime injects the bearer bound by the user's per-agent
consent grant (the agent is a Delegated-By-KDCube client entity,
`kdcube-agent:<app>:<agent>`). While the user has not granted this agent, the
connection binds a consent-gated stub instead: when the model calls it — i.e.
when the user's request actually needs the capability — that connection's
consent demand rises in chat with a one-click grant. Identity model, grant
round-trip, and the consent middleware:
[Agents Acting On Behalf Of The User](../../sdk/solutions/connections/agent-acting-for-user/agent-acting-for-user-README.md).

The same shape reaches the user's EXTERNAL accounts through the named-services
bridge — one delegated connection to the `kdcube-services` `named_services`
surface, claims naming the namespaces in play:

```yaml
- name: slack
  kind: mcp
  server_id: slack
  alias: slack
  url: https://runtime.example/api/integrations/bundles/<T>/<P>/kdcube-services@1-0/public/mcp/named_services
  resource: "*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/named_services*"
  transport: streamable_http
  delegated: true
  scopes: [named_services:use]   # Slack's real claims consent per account (Delegated to KDCube), not in scopes
```

Two consents chain here: the per-agent grant admits the agent into the KDCube
namespace boundary, and the user's own connected-account consent (Slack in
Delegated to KDCube) is checked by the provider adapter at every call —
revoking either stops the tool immediately. What the bridge's operations
should return so an agent can use them:
[Make A Named Service Agent-Friendly (MCP)](named-services-mcp-README.md).

Consent stays demand-driven after the first grant lands. Once the agent holds
SOME grants on the door (slack), a call into a namespace outside them (mail)
denies per operation INSIDE the door, naming exactly the missing grants; the
tool wrap raises that as the same scoped chat demand, and the one-click
approval MERGES the new claims into the agent's existing grant record.
Connect-time consent can only ask with a connection's whole declared scope
list — this leg asks with the single grant the operation missed.

## 5. How A KDCube Agent Receives The Tools

KDCube's agent configuration parser turns the descriptor into an agent-scoped
tool inventory:

```python
from kdcube_ai_app.apps.chat.sdk.runtime.tool_config import (
    agent_tool_config_from_bundle_props,
)

tool_config = agent_tool_config_from_bundle_props(
    self.bundle_props,
    self.runtime_ctx.agent_id,
    bundle_root=BUNDLE_ROOT,
)
```

For a KDCube ReAct agent, pass that inventory into the normal builder:

```python
react = self.build_react(
    mod_tools_spec=tool_config.tool_specs,
    mcp_tools_spec=tool_config.mcp_tool_specs,
    tools_runtime=tool_config.tool_runtime,
    tool_traits=tool_config.tool_traits,
)

result = await react.run(
    allowed_plugins=tool_config.allowed_plugins,
    allowed_tool_names_by_alias=tool_config.allowed_tool_names_by_alias,
)
```

The app's effective `surfaces.as_consumer.mcp.services` block is resolved by the
workflow and supplied to the MCP tool subsystem. The model sees only the tools
in this agent's inventory.

For another agent framework hosted inside KDCube, combine the same two
descriptor-owned inputs:

```text
AgentToolConfig.mcp_tool_specs
  server_id + model-facing alias + allowed tool names for this agent

surfaces.as_consumer.mcp.services
  transport + endpoint + credential reference for each server_id
```

Adapt that combined view into the framework's MCP client/tool registry, then
filter the discovered tools to the resolved `mcp_tool_specs`. Do not put a URL or
secret into the agent declaration, and do not maintain a second hand-written
allow-list in framework code. The KDCube app descriptor remains the authority
for which server and tools that agent receives.

For Claude Code, the process is separate: Claude Code reads its own `.mcp.json`.
Use the KDCube Claude workspace adapter to generate that file from the app's
selected MCP configuration. `surfaces.as_consumer` does not directly reconfigure
an already-running Claude CLI process.

## 6. Runtime Journey

```text
app props
  surfaces.as_consumer.mcp.services
  surfaces.as_consumer.agents.<agent>.tools
        |
        v
agent_tool_config_from_bundle_props(...)
        |
        +-> mcp_tool_specs: server_id + alias + allow-list
        +-> tool_traits / runtime policy
        |
        v
MCPToolsSubsystem
        |
        +-> tools/list (catalog; cache is auth-fingerprinted)
        +-> allow-list filtering
        +-> entries named mcp.<alias>.<tool>
        |
        v
agent chooses a complete call
        |
        v
io_tools.tool_call -> MCPToolsSubsystem.execute_tool
        |
        v
MCP transport -> remote/local server -> structured result
```

In split isolated execution, generated code does not receive the MCP credential.
The untrusted executor sends an approved tool request to the trusted supervisor;
the supervisor owns MCP routing and secret resolution.

## 7. Results, Files, And Size

An MCP result normally becomes a structured tool-result block. A remote MCP
server should return bounded JSON or an already-hosted reference for large data.

Do not return a large binary as base64 merely because MCP can carry JSON. If a
tool creates a file in the current ReAct workspace, it may use KDCube's strict
`ret.artifact_type == "files"` declaration. A remote server that cannot write to
that workspace should return a signed URL or product object ref instead.

For a KDCube-hosted agent running inside a chat turn, a result carrying a
signed-URL file object (`download: {encoding: "url", ...}`) does not reach the
model as a link: the tool gate delivers the file to the user as a chat file
card (the object ref, resolved at click time under the user's session) and the
model reads a delivery note instead. A signed URL a model re-types by hand
arrives broken — the card removes the courier role entirely. Turn-less
consumers keep the URL contract.

## 8. Economics: What Is And Is Not Automatic

The MCP call runs inside the current KDCube request/accounting context. Paid
work performed by instrumented KDCube services inside that context is attributed
normally.

The runtime cannot infer what an arbitrary remote MCP provider charged. If the
remote MCP call has a cost that must affect KDCube budgets, wrap or adapt it as a
self-tracked service, report a supported priced usage/cost, and run it under an
`EconomicsGuard`. MCP connectivity alone is not a price declaration.

## 9. Verify

1. Validate the descriptor resolves one MCP spec for the target agent:

```python
cfg = agent_tool_config_from_bundle_props(props, "main")
assert cfg.mcp_tool_specs == [
    {"server_id": "docs", "alias": "docs", "tools": ["search", "read_document"]}
]
```

2. Start the app and inspect proc logs for:

```text
MCP build_tool_entries: loading tools for server=docs
MCP build_tool_entries: total ... mcp.docs.search ...
```

3. Ask the agent to perform a task that requires one allowed tool. Confirm the
   stored tool call id is `mcp.docs.<tool>`.
4. Remove one tool from `allowed`, reload the app config, and confirm it is absent
   from the agent catalog.
5. Configure a second agent with a narrower list and confirm the two catalogs
   differ while both use the same server connection.
6. For authenticated servers, rotate the referenced secret and confirm no token
   appears in config output, cache keys, logs, or tool results.

## 10. Common Failures

| Symptom | Check |
| --- | --- |
| No MCP tools in the catalog | The agent has a `kind: mcp` entry, `server_id` matches the registry, and auth is non-interactive |
| `MCP server not configured` | The key under `mcpServers` exactly matches `server_id` |
| Connection works on the laptop but not in KDCube | The URL/command is reachable from the process or container that owns the MCP client |
| Tool exists but this agent cannot call it | The concrete tool is in this agent's `allowed` list and user-level selection did not disable it |
| Tool call succeeds but no cost is recorded | The provider call is not instrumented; add tracked usage and economics enforcement where charging is required |
| Large results consume the model context | Return bounded data plus refs/URLs instead of inline binary/base64 |

## Related Documentation

- [Expose An MCP Service From A KDCube App](expose-mcp-service-README.md)
- [MCP SDK Integration](../../sdk/tools/mcp-README.md)
- [Tool Subsystem](../../sdk/tools/tool-subsystem-README.md)
- [Tool Strategy Traits](../../sdk/solutions/multi-action/tool-strategy-traits-README.md)
- [Guard A Paid Surface](../economics/guard-paid-surface-and-enforce-economics-README.md)
