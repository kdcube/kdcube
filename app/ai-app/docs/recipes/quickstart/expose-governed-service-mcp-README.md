---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/quickstart/expose-governed-service-mcp-README.md
title: "Expose a Governed Service over MCP"
summary: "Build a KDCube app that exposes its own functionality over an MCP door where Connection Hub owns the governance — the app's tools act on a user's third-party accounts (Gmail) and on your own OAuth-protected server, and every call is authorized against the caller's delegated grant. Minimal chain first, then the full setup: configure the Gmail provider and its claims, configure your own external OAuth/OIDC service, write the app's domain + MCP modules, guard the door with managed auth, make it delegable, and consume it from an agent or an external app."
status: active
tags: ["quickstart", "recipe", "mcp", "connection-hub", "delegated-credentials", "named-services", "oauth", "governance", "app-authoring"]
keywords: ["expose governed mcp", "as_provider.mcp", "delegated_to_kdcube", "custom oauth provider", "connected_accounts", "managed auth", "delegation edges", "connection cards"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/kdcube_for_agents/expose-mcp-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/kdcube_for_agents/consume-mcp-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connected-services-config-chain/connected-services-config-chain-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/mcp/platform-mcp-over-connection-hub-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-accounts/custom-oauth-oidc-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/configuring-agent-service-access/configuring-agent-service-access-README.md
---
# Expose a Governed Service over MCP

You want to write an app whose functionality reaches a user's **third-party
accounts** (Gmail) **and your own OAuth-protected server**, exposed over an
**MCP door** — and you want KDCube to own the governance so your code never
touches a token or a session. This recipe builds exactly that, minimal chain
first, then the full setup.

Current code and descriptors say **bundle** in names such as `bundle_id` and
`bundles.yaml`. Here, **app = bundle**: one deployable KDCube runtime unit.

Two existing recipes go deep on the two halves and are the companions to this
one — [Expose an MCP Service from a KDCube App](../kdcube_for_agents/expose-mcp-service-README.md)
(the door) and [Connect an MCP Service to a KDCube Agent](../kdcube_for_agents/consume-mcp-service-README.md)
(the consumer). This recipe is the **end-to-end assembly with governance**.

## Who owns what

```text
  YOUR APP  my-service@1-0                         CONNECTION HUB  connection-hub@1-0
  ─────────────────────────────                    ───────────────────────────────────
  services/   domain logic (Gmail calls,           providers          which accounts a user
              your-server calls) — token-free         (delegated_to_kdcube)   can connect + claims
  surfaces/   @mcp door (managed auth)              delegable resources  which doors may be
  tools/      connected_accounts requirements         (delegated_credentials.oauth)  delegated
              ("this op needs gmail:send")          THE GOVERNANCE STORE
                     │                                connection cards   one per (user, caller,
                     │  a call arrives                                   resource): the grant
                     ▼                                delegation edges   the authority chain a
              Connection Hub authorizes  ◀───────────                    grant was minted through
              by the caller's grant, then
              resolves the account credential
              only at the trusted boundary
```

Your app declares the door and the required consents; **Connection Hub is the
authority** — it captures the **connection cards** (the per-caller grants the user
approves) and the **delegation edges** (the authority chain each grant was minted
through), and it resolves the actual provider credential only after the call
passes. Your tool code sees an authorized request for a resolved user, never a
credential.

## The minimal chain (read this first)

The smallest working shape — one third-party (Gmail), one door, one consumer:

```text
① connection-hub@1-0 · connections.delegated_to_kdcube.providers.google   → the Gmail app + claims
② connection-hub@1-0 · connections.delegated_credentials.oauth.resources  → make your door delegable
③ my-service@1-0     · surfaces.as_provider.mcp.<door>.auth (managed)      → the guarded door
④ my-service@1-0     · tool code: connected_accounts: [{provider_id, claims, claims_by_operation}]
⑤ my-agent@1-0       · surfaces.as_consumer … tools[] (delegated: true)    → the consumer
⑥ runtime            · user connects Gmail + grants the caller (per account)
```

Everything below fills in these six, and adds your own OAuth service as a second
provider. The full map with every descriptor path is
[The Connected-Services Config Chain](../../sdk/solutions/connections/connected-services-config-chain/connected-services-config-chain-README.md).

## Step 1 — Configure the Gmail provider and its claims

In `connection-hub@1-0`, declare Google as a connectable provider. Gmail uses the
**built-in** `google.oauth` adapter, so you only supply the OAuth client and the
claims.

```yaml
# connection-hub@1-0 · config.connections.delegated_to_kdcube
delegated_to_kdcube:
  enabled: true
  oauth: { public_base_url: "https://<host>" }   # where Google redirects back
  providers:
    google:
      adapter: google.oauth
      enabled: true
      connector_apps:
        gmail:
          client_id: "…apps.googleusercontent.com"
          client_secret_ref: "connections.delegated_to_kdcube.providers.google.connector_apps.gmail.client_secret"
          allowed_claims: [gmail:read, gmail:send]        # the ceiling for this app
      claims:
        gmail:read: { label: Read Gmail,  provider_scopes: [openid, email, profile, "https://www.googleapis.com/auth/gmail.readonly"] }
        gmail:send: { label: Send Gmail,  provider_scopes: [openid, email, profile, "https://www.googleapis.com/auth/gmail.send"] }
```

- **`allowed_claims`** is the per-provider ceiling — the most a connected Gmail
  account may ever grant here.
- **`claims.*.provider_scopes`** maps each KDCube claim to the Google OAuth scopes
  requested at connect time.
- The secret is a **`*_ref` pointer** into `bundles.secrets.yaml`, never inline:
  ```yaml
  # bundles.secrets.yaml · items[id=connection-hub@1-0].secrets
  connections.delegated_to_kdcube.providers.google.connector_apps.gmail.client_secret: "<google-client-secret>"
  ```

Provider/account setup in depth:
[Delegated Provider Accounts](../../sdk/solutions/connections/delegated-accounts/delegated-accounts-README.md).

## Step 2 — Configure your own external OAuth/OIDC service

Your own server is a second provider. Unlike Google/Slack it has **no built-in
adapter**, so you register it as a custom OAuth/OIDC provider. The full walkthrough
(authorize/token/userinfo endpoints, claim mapping, the adapter contract) is
[Custom OAuth/OIDC Provider Accounts](../../sdk/solutions/connections/delegated-accounts/custom-oauth-oidc-service-README.md);
the shape mirrors Step 1:

```yaml
# connection-hub@1-0 · config.connections.delegated_to_kdcube.providers
    my_server:
      adapter: custom.oauth          # the OIDC/OAuth adapter — see the custom-oauth recipe
      enabled: true
      connector_apps:
        default:
          client_id: "<your-client-id>"
          client_secret_ref: "connections.delegated_to_kdcube.providers.my_server.connector_apps.default.client_secret"
          redirect_uri: "https://<host>/api/…/delegated_to_kdcube_oauth_callback"
          allowed_claims: [my_server:read, my_server:write]
      claims:
        my_server:read:  { label: "Read your server",  provider_scopes: [read] }
        my_server:write: { label: "Write your server", provider_scopes: [write] }
```

Now a user can connect **both** a Gmail account and a "my_server" account under
*Delegated to KDCube*.

## Step 3 — Write the app: domain service + MCP surface + required consents

Keep the app modular. Put the third-party calls in a **token-free domain
service** (it receives a resolved credential, it never fetches one), expose them
through an **MCP surface**, and declare, per operation, **which connected-account
claim it needs**.

```text
my-service@1-0/
  entrypoint.py                 thin composition root; declares the @mcp door
  services/
    gmail_ops.py                domain logic that calls Gmail with a passed-in token
    my_server_ops.py            domain logic that calls your server with a passed-in token
  surfaces/
    mcp/service.py              the MCP tool definitions (the door body)
  config/ interface/ docs/ tests/
```

```python
# entrypoint.py — declare the door (managed auth)
@mcp(alias="ops", route="public", transport="streamable-http",
     auth_config="surfaces.as_provider.mcp.ops.auth")
def ops_mcp(self, request=None, **kwargs):
    return build_ops_mcp_app(request=request, ...)
```

Declare what each operation needs from the connected accounts — this is the
machine-readable source the **proactive consent picker** reads, so a user can
consent *before* the agent calls:

```python
OPS_CONNECTED_ACCOUNT_REQUIREMENTS = [
  { "provider_id": "google", "connector_app_id": "gmail",
    "claims": ["gmail:read", "gmail:send"],
    "claims_by_operation": { "object.search": ["gmail:read"], "object.action.send": ["gmail:send"] } },
  { "provider_id": "my_server", "connector_app_id": "default",
    "claims": ["my_server:read", "my_server:write"],
    "claims_by_operation": { "object.search": ["my_server:read"], "object.action.push": ["my_server:write"] } },
]
```

> Read/write is the **per-account provider claim** declared here — not a door
> scope. The door admits on `named_services:use`; the per-account claim (resolved
> by Connection Hub's broker) is the read/write gate. Keep it here, once.

## Step 4 — Guard the door and make it delegable

Two descriptor edits. First, guard the door with **managed** auth in your app:

```yaml
# my-service@1-0 · config.surfaces.as_provider.mcp
mcp:
  ops:
    auth: { mode: managed, authority_id: delegated_client, selected_tool_grants: true }
```

`mode: managed` hands authorization to Connection Hub — it checks every call
against the caller's grant (resource, operation, claims, identity, expiry) before
your tool runs.

Second, declare your door as a **delegable resource** in Connection Hub, and (for
external callers) whitelist their client:

```yaml
# connection-hub@1-0 · config.connections.delegated_credentials.oauth
oauth:
  public_clients:                                  # external apps (Claude Code) allowed to connect
    - { client_id: "<external-app>", redirect_uris: [...] }
  resources:
    - resource: "*/api/integrations/bundles/*/*/my-service@1-0/public/mcp/ops*"
      label: "My service MCP"
      tools: { … per-operation grants … }          # named_services:use for account-backed ops
```

The `resource` pattern must byte-match the door URL and the consumer's `resource`
in Step 5.

Door mechanics and the managed-credential guard in depth:
[Expose an MCP Service from a KDCube App](../kdcube_for_agents/expose-mcp-service-README.md)
and [Protect App MCP with Managed Credentials](../connections/protect-bundle-mcp-with-managed-credentials-README.md).

## The claim layering — where every claim lives, and which are real

This is the part that makes or breaks a governed service, and it is easy to get
wrong: **one claim** (say `slack:search` or `mail:read`) appears in up to four
places, and they must agree. Here is each layer, its exact descriptor path, and
what it does.

| Layer | Where (descriptor path) | Declares |
| --- | --- | --- |
| **1 · Grant vocabulary** | `connection-hub@1-0 · connections.delegated_credentials.oauth.capabilities` — the `- grant: <claim>` entries with `label`, `delegable_roles`, `delegable_permissions` | DEFINES each delegatable claim. A claim used anywhere else MUST exist here, or it has no label and cannot be delegated. |
| **2 · Access map** | `connection-hub@1-0 · connections.delegated_credentials.oauth.resources[].named_services.<ns>.tools.<op>.grants` | Which grants each OPERATION of a namespace requires — the door's per-call enforcement. `named_services:use` (door entry) **plus** the operation's specific claim. |
| **3 · Connection scope** | `<consumer> · surfaces.as_consumer.agents.<a>.tools[].scopes` | The agent's CEILING — the most it may be granted at this door. |
| **4 · Per-account binding** | `account_scope` on the grant (runtime — Connection Hub UI, no descriptor) | Which connected account, and which of its real claims, this caller may use. |

A Slack `object.search` call must be covered at layer 2 (the op requires
`slack:search`), layer 1 (`slack:search` is a defined grant), and — because Slack
is account-backed — layer 4 (the account is bound for `slack:search`). Layers 1
and 2 are **authored in the connection-hub config**; the access map is a
projection of it, invented nowhere else. This layering is why editing only one
place (e.g. an agent's `scopes`) does not change what a demand asks for — the
operation's requirement lives in the access map.

### Which claims are REAL — the rule that avoids invented "wrapper" claims

The claims in layers 1–2 must be the **real permissions the underlying service
needs at its API**, not an invented namespace wrapper:

- **A single provider** — Slack, LinkedIn, your own OAuth service — declares its
  OWN real claims. Slack uses `slack:search`, `slack:post`, `slack:channels`,
  `slack:history`, `slack:files:read`, `slack:files:write`,
  `slack:assistant:search` (the exact Slack capabilities), **never** an invented
  `slack:read`/`slack:write`. These are the SAME tokens the connected account
  authorizes at layer 4, so all four layers speak one vocabulary.
- **A multi-provider realm** — mail is the example: it serves Gmail (OAuth) AND
  IMAP/SMTP (username/password, which has no OAuth scopes at all). There is no
  single real claim across providers, so mail keeps a **provider-agnostic**
  namespace claim `mail:read`/`mail:send` at layers 1–2, with the real provider
  claim (`gmail:read`/`gmail:send`) resolved per account at layer 4.

So: real provider claims for a single-provider service; a provider-agnostic
namespace claim ONLY when the realm genuinely spans providers with no shared
claim vocabulary (mail). Your own OAuth service is single-provider — it declares
its own real claims (Step 2's `my_server:read`/`my_server:write`).

### The worked reference, in the built-in apps

Read these — they implement exactly this layering and ship in the repo:

- `…/examples/bundles/kdcube-services@1-0/` — the named-services door + the
  provider code (mail, slack, conv, tasks) with `claims_by_operation`.
- `…/examples/bundles/connection-hub@1-0/config/bundles.template.yaml` — the grant
  **vocabulary** (`oauth.capabilities`) and the **access map**
  (`oauth.resources[].named_services`), showing mail (provider-agnostic
  `mail:read`) beside slack (real `slack:search`/`slack:post`/…).

## Step 5 — Consume it

A **resident agent** declares a delegated connection to the door:

```yaml
# my-agent@1-0 · config.surfaces.as_consumer.agents.<agent>.tools[]
- name: ops
  kind: mcp
  delegated: true
  url: "https://<host>/api/integrations/bundles/<t>/<p>/my-service@1-0/public/mcp/ops"
  resource: "*/api/integrations/bundles/*/*/my-service@1-0/public/mcp/ops*"
  scopes: [named_services:use]        # + any non-account namespace claims
```

An **external app** (Claude Code) needs no consumer block — it connects to the
same door over OAuth via the `public_clients` entry from Step 4. Consumer detail:
[Connect an MCP Service to a KDCube Agent](../kdcube_for_agents/consume-mcp-service-README.md).

## Step 6 — Runtime: the user connects and grants

No config — the user acts in Connection Hub:

1. **Connect accounts** (*Delegated to KDCube*): OAuth a Gmail account and a
   "my_server" account, approving each provider's claims.
2. **Grant the caller** (*Delegated by KDCube*): the first call raises a consent
   card; the user grants **per account, per claim** (`account_scope`) — e.g.
   Gmail read+send via account A, read-only via account B.

At this moment Connection Hub writes a **connection card** for `(user, this
caller, this resource)` and records the **delegation edge** (the authority chain
the grant was minted through). Both are inspectable and revocable in the hub; the
card is the live authority the guard resolves on every subsequent call.

## Verify

- **Publish** — Gmail and "my_server" appear under *Delegated to KDCube*; a user
  can connect each.
- **Guard** — a call with no grant raises the consent card; with a grant it runs;
  revoking the card stops it on the next call.
- **Per-account** — bind read-only on one Gmail account and read+write on another;
  the write is refused on the read-only one, naming the allowed account — even
  though that account is itself write-capable.
- **No credentials leak** — grep prompts, generated code, logs, and the executor
  environment for the Google/my_server secret values; they must be absent.

## Read more

- [Expose an MCP Service from a KDCube App](../kdcube_for_agents/expose-mcp-service-README.md)
- [Connect an MCP Service to a KDCube Agent](../kdcube_for_agents/consume-mcp-service-README.md)
- [The Connected-Services Config Chain](../../sdk/solutions/connections/connected-services-config-chain/connected-services-config-chain-README.md)
- [Platform MCP over Connection Hub](../../sdk/solutions/mcp/platform-mcp-over-connection-hub-README.md)
- [Custom OAuth/OIDC Provider Accounts](../../sdk/solutions/connections/delegated-accounts/custom-oauth-oidc-service-README.md)
- [How Agents Connect to KDCube](explore-how-agents-connect-to-kdcube-README.md)
