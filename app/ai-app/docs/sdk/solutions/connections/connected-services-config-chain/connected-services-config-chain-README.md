---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connected-services-config-chain/connected-services-config-chain-README.md
title: "The Connected-Services Config Chain"
summary: "A map of every place config lives to take a KDCube deployment from zero to: connectable provider accounts (Gmail, Slack, LinkedIn, a custom third-party), an MCP service that declares which connected-account consents each operation needs, and an agent or external app that consumes it — with read/write governed per account. Names the descriptor path for each link and the one rule that ties them together."
status: active
tags: ["sdk", "connections", "connection-hub", "delegated-credentials", "named-services", "mcp", "configuration", "providers", "governance"]
updated_at: 2026-07-19
keywords: ["delegated_to_kdcube", "delegated_credentials.oauth", "connected_accounts", "as_provider.mcp", "as_consumer", "named_services:use", "account_scope", "config chain"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/configuring-agent-service-access/configuring-agent-service-access-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/mcp/platform-mcp-over-connection-hub-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-accounts/delegated-accounts-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-accounts/custom-oauth-oidc-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/quickstart/explore-how-agents-connect-to-kdcube-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/named-services-tools-README.md
---
# The Connected-Services Config Chain

To take a deployment from zero to *an MCP service that acts on a user's Gmail,
Slack, LinkedIn, and a custom third-party — governed per account*, six things get
configured, in three different places, plus one runtime step the user performs.
This page is the map: what each link is, and the exact descriptor path it lives
at. The agent-side detail is
[Configuring Agent Access to Services and Accounts](../configuring-agent-service-access/configuring-agent-service-access-README.md);
the door/broker view is
[Platform MCP over Connection Hub](../../mcp/platform-mcp-over-connection-hub-README.md).

```text
  ① PROVIDERS          connection-hub@1-0 · config.connections.delegated_to_kdcube.providers
     (connectable         → the accounts a user CAN connect: OAuth app + claims per provider
      accounts)
                                     │
  ② DELEGABLE          connection-hub@1-0 · config.connections.delegated_credentials.oauth
     RESOURCES            → resources[] (which MCP doors may be delegated) + public_clients[]
     + CLIENTS              (which external apps may connect)
                                     │
  ③ THE MCP DOOR       my-service@1-0 · config.surfaces.as_provider.mcp.<door>.auth
     (service app)        → the @mcp door, guarded by Connection Hub (mode: managed)
                                     │
  ④ REQUIRED           my-service@1-0 · the tool/namespace CODE
     CONSENTS             → connected_accounts: [{provider_id, claims, claims_by_operation}]
     (the declaration)      "this operation needs gmail:send / slack:post / linkedin:post …"
                                     │
  ⑤ THE CONSUMER       my-agent@1-0 · config.surfaces.as_consumer.agents.<a>.tools[]
     (agent OR             → delegated:true MCP connection to the door (+ named_service roster)
      external app)         (an external app connects over OAuth instead — see ②)
                                     │
  ⑥ RUNTIME (no config)  user connects accounts (Delegated to KDCube)
                         + grants the agent per account (Delegated by KDCube · account_scope)
```

## ① Providers — the connectable accounts

`connection-hub@1-0` → `config.connections.delegated_to_kdcube.providers`. One
entry per provider the user may connect.

```yaml
providers:
  google:                              # built-in adapter
    adapter: google.oauth
    connector_apps:
      gmail: { client_id: "…", client_secret_ref: "connections…gmail.client_secret",
               allowed_claims: [gmail:read, gmail:send] }
    claims:
      gmail:read: { provider_scopes: [openid, email, …/gmail.readonly] }
      gmail:send: { provider_scopes: [openid, email, …/gmail.send] }
  slack:                               # built-in adapter
    adapter: slack.oauth_user_token
    connector_apps: { demo: { client_id: "…", client_secret_ref: "…", allowed_claims: [slack:search, slack:post] } }
    claims: { slack:search: { provider_scopes: […] }, slack:post: { provider_scopes: […] } }
  linkedin:                            # NEEDS a custom OAuth/OIDC adapter
    adapter: custom.oauth
    connector_apps: { default: { client_id: "…", client_secret_ref: "…", allowed_claims: [linkedin:read, linkedin:post] } }
    claims: { linkedin:read: { provider_scopes: […] }, linkedin:post: { provider_scopes: […] } }
  acme:                                # the hypothetical third party — identical shape
    adapter: custom.oauth
    connector_apps: { default: { client_id: "…", client_secret_ref: "…", allowed_claims: [acme:read, acme:write] } }
    claims: { acme:read: { provider_scopes: […] }, acme:write: { provider_scopes: […] } }
```

- `allowed_claims` is the **per-provider ceiling**; `claims.*.provider_scopes`
  maps each claim to the OAuth scopes requested at connect time.
- **Gmail and Slack use built-in adapters** (`google.oauth`,
  `slack.oauth_user_token`). **LinkedIn and a third-party need a custom
  adapter** — see
  [Custom OAuth/OIDC Provider Accounts](../delegated-accounts/custom-oauth-oidc-service-README.md).
- Secrets are `*_ref` pointers into `bundles.secrets.yaml`, never inline.

## ② Delegable resources and external clients

`connection-hub@1-0` → `config.connections.delegated_credentials.oauth`.

```yaml
oauth:
  issuer: ""
  public_clients:                      # external OAuth apps (Claude Code) allowed to connect
    - { client_id: "…", redirect_uris: […] }
  resources:                           # which MCP doors may be delegated at all
    - resource: "*/api/integrations/bundles/*/*/my-service@1-0/public/mcp/<door>*"
      label: "My service MCP"
      tools: { … per-operation grants … }   # named_services:use for account-backed ops
```

The `resource` pattern here must byte-match the connection's `resource` in ⑤ and
the door's URL in ③.

## ③ The MCP door

`my-service@1-0` → `config.surfaces.as_provider.mcp.<door>.auth`, plus the `@mcp`
declaration in code.

```yaml
surfaces:
  as_provider:
    mcp:
      <door>: { auth: { mode: managed, authority_id: delegated_client, selected_tool_grants: true } }
```
```python
@mcp(alias="<door>", route="public", transport="streamable-http",
     auth_config="surfaces.as_provider.mcp.<door>.auth")
def my_door(self, request=None, **kwargs): ...
```

`mode: managed` hands authorization to Connection Hub — every call is checked
against the caller's delegated grant before the tool runs.

## ④ Required consents — the service declares what it needs

In the tool/namespace **code**, the service declares which connected-account
claim each operation needs. This is the machine-readable source the proactive
consent picker reads.

```python
MY_SERVICE_CONNECTED_ACCOUNT_REQUIREMENTS = [
  { "provider_id": "linkedin", "connector_app_id": "default",
    "claims": ["linkedin:read", "linkedin:post"],
    "claims_by_operation": {                 # ← the REAL per-account claims
        "object.search":      ["linkedin:read"],
        "object.action.post": ["linkedin:post"],
    }},
  # one block per provider the service touches (gmail, slack, acme) …
]
```

For an account-backed service, **read/write lives only here** (the per-account
provider claim). The door does not carry a parallel namespace-level
`linkedin:read` scope — it admits the operation on `named_services:use` and the
per-account claim is the read/write gate.

## ⑤ The consumer — an agent (or an external app)

`my-agent@1-0` → `config.surfaces.as_consumer.agents.<agent>.tools[]`.

```yaml
tools:
  - name: my_service
    kind: mcp
    delegated: true
    url: "https://<host>/api/integrations/bundles/<t>/<p>/my-service@1-0/public/mcp/<door>"
    resource: "*/api/integrations/bundles/*/*/my-service@1-0/public/mcp/<door>*"
    scopes: [named_services:use]        # + any NON-account namespace claims (see below)
```

An **external app** (Claude Code) needs no consumer block — it connects to the
same door over OAuth, allowed by `public_clients` in ②.

## ⑥ Runtime — the user, in Connection Hub (no config)

1. **Connect accounts** — *Delegated to KDCube*: the user OAuths each provider,
   approving its claims.
2. **Grant the caller** — *Delegated by KDCube*: the first call raises a consent
   card; the user grants **per account, per claim** (`account_scope`) — e.g.
   LinkedIn read+post via account A, read-only via account B.

## The one rule

- **Door admission** = `named_services:use` (in the connection `scopes` and the
  resource grants).
- **Which namespaces** = the roster (consumer side) and what the door serves.
- **Read/write on an account-backed service** (Gmail, Slack, LinkedIn, a
  third-party) = the **real provider claim, consented per account** — never a
  namespace-level door scope.
- **The claims must be REAL** — the exact permissions the service needs at its
  API. A single provider (Slack, LinkedIn, your own OAuth service) declares its
  own real claims (`slack:search`, `slack:post`, …), never an invented
  `slack:read`/`slack:write`. A provider-agnostic namespace claim is used ONLY
  when a realm genuinely spans providers with no shared vocabulary — **mail**
  (Gmail OAuth *and* IMAP/password) keeps `mail:read`/`mail:send`, with the real
  `gmail:read`/`gmail:send` per account. A claim lives in up to four layers (grant
  vocabulary, access map, connection scope, per-account) that must agree — the
  full map is [Expose a Governed Service over MCP](../../../../recipes/quickstart/expose-governed-service-mcp-README.md)
  → "The claim layering".

## Built-in named services ride one door

Some namespaces have **no external account** — the conversation namespace
(`conv`) and user memory (`mem`) are the user's own KDCube data. They are
INTERNAL named services served by the same `named_services` door, so they need no
provider (①) and no per-account consent (⑥); their read/write **is** a
namespace-level claim (e.g. `conv:read`), which is why those — and only those —
appear in the connection `scopes` alongside `named_services:use`:

```yaml
scopes: [named_services:use, conv:read]     # + mem's claim, to reach memory via this door
```

Memory can be reached **two ways**, and both are valid — the point is that a
delegated MCP binding is not only ever "the named-services door":

- as the `mem` namespace on this shared `named_services` door (consistent with
  `conv` — one connection, one consent for the whole surface); or
- through the user-memories app's **own dedicated** `/public/mcp/memories` door —
  a separate `delegated: true` connection with its own consent and its own picker
  toggle.

The built-in react-lg example **deliberately uses the dedicated door for memory**
while using the shared door for `conv`, so a reader sees *both* binding shapes
side by side: a service on its own MCP door, and a namespace on the shared door.
Choose the dedicated door when a capability should be granted and revoked
independently; choose the shared door to fold a namespace into one surface.
