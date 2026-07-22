---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/configuring-agent-service-access/configuring-agent-service-access-README.md
title: "Configuring Agent Access To Services And Accounts"
summary: "End-to-end configuration for letting an agent act on the user's behalf against KDCube named services and the user's connected external accounts: the provider side (which accounts the app can hold), the consuming-agent side (the delegated MCP connection, scopes, and namespace roster), and the runtime per-account binding (account_scope) that pins a granted agent to specific connected accounts. Names the canonical descriptor references to copy from."
status: active
tags: ["sdk", "connections", "configuration", "delegated-credentials", "named-services", "connected-accounts", "account-scope", "consent", "connection-hub", "agents", "mcp"]
updated_at: 2026-07-19
keywords: ["account_scope", "delegated: true", "named_services", "named_services_roster", "scopes", "conv:read", "delegated_to_kdcube", "connector_apps", "allowed_claims", "consent_ui", "authority_provider", "resource_grants", "kdcube-agent", "per-account scoping", "bundles.yaml", "bundles.template.yaml"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/quickstart/explore-how-agents-connect-to-kdcube-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/mcp/platform-mcp-over-connection-hub-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/agent-acting-for-user/agent-acting-for-user-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-accounts/delegated-accounts-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/claim-driven-consent/claim-driven-consent-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/named-services-tools-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/ported-langgraph-agents@2026-07-13/docs/agents/lg-react-named-services-config.md
---
# Configuring Agent Access To Services And Accounts

An agent that works on the user's behalf — reads their mail, posts to their
Slack, recalls their memory — needs two independent things configured, and they
sit in two different places:

1. **Which capability the agent may reach** — a delegated connection on the
   consuming agent, plus the app that publishes the service. This is
   **descriptor** config (`bundles.yaml`).
2. **Which of the user's connected accounts the agent may use for a provider**
   — the per-account binding (`account_scope`) on the agent's grant. This is a
   **runtime** decision the user makes in Connection Hub, not descriptor config.

The identity and consent model behind this — every agent is a per-agent
Delegated-By-KDCube client, consent is per agent, the bound token is reused each
turn, a missing grant surfaces as a one-click chat demand — is
[Agents Acting On Behalf Of The User](../agent-acting-for-user/agent-acting-for-user-README.md).
This page is the **configuration companion**: what to declare, where, and how
the account binding narrows it.

## The two consent gates (why two things are configured)

A single tool call crosses two sequential gates. Configuring "agent can use
mail" means configuring both:

```text
agent tool call
   |
   v
[Gate 1] Delegated-BY-KDCube    does THIS agent have a grant for this service?
   |  kdcube-agent:<app>:<agent>   (authored per agent in Connection Hub)
   |  passes
   v
[Gate 2] Delegated-TO-KDCube    does a connected account authorize this claim,
   |  provider account claims       AND is it one this agent is bound to?
   |  passes
   v
   the provider API call runs on the user's account
```

Gate 1 is the **agent connection + grant** (sections below, provider-agnostic).
Gate 2 is the **connected account** the user linked under Delegated-to-KDCube,
plus the agent's `account_scope` binding. The two are configured independently
and enforced in order — a granted agent still cannot touch a provider the user
has not connected, and a connected account is never enough on its own to admit
an agent.

## Surface A — publish the service (the Connection Hub app)

The app that owns Connection Hub (`connection-hub@1-0`) declares which external
providers the user can connect, under `connections.delegated_to_kdcube`:

```yaml
delegated_to_kdcube:
  enabled: true
  oauth:
    public_base_url: https://<PUBLIC_HOST>
  providers:
    google:
      label: Google
      adapter: google.oauth
      enabled: true
      connector_apps:
        gmail:
          label: Gmail
          enabled: true
          client_id: <oauth-client-id>
          client_secret_ref: connections.delegated_to_kdcube.providers.google.connector_apps.gmail.client_secret
          allowed_claims: [gmail:read, gmail:send]
      claims:
        gmail:read:  { label: Read Gmail,  provider_scopes: [..., gmail.readonly] }
        gmail:send:  { label: Send Gmail,  provider_scopes: [..., gmail.send] }
```

`allowed_claims` is the ceiling of what a connected Google account may grant;
`claims.*.provider_scopes` maps each claim to the OAuth scopes requested at
connect time. Provider/account setup itself is
[Delegated Provider Accounts](../delegated-accounts/delegated-accounts-README.md).

**Consent surface (`consent_ui`).** Optional. Absent → Connection Hub's built-in
consent renderer. Present with `mode: authority_provider` (or the equivalent
`authority_ref:` nested form) → consent is rendered through a platform authority
provider. Both are valid; pick per deployment. This is a per-environment
behavioral choice, not something to normalize across environments.

## Surface B — connect the agent to the service (the consuming agent)

The agent that consumes the service declares a delegated connection in its
`tools` list. For KDCube named services this is one connection to the
`kdcube-services@1-0` named-services MCP door, `delegated: true`, with `scopes`
as the fine-grained ceiling:

```yaml
- name: named_services
  kind: mcp
  server_id: named_services
  alias: named_services
  url: https://<PUBLIC_HOST>/api/integrations/bundles/<TENANT>/<PROJECT>/kdcube-services@1-0/public/mcp/named_services
  resource: "*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/named_services*"
  transport: streamable_http
  delegated: true
  scopes: [named_services:use, conv:read]   # conv is built-in (no account); mail/slack read/write is per-account
- name: named_services_roster       # binds no tools; teaches the prompt the realms
  kind: named_service
  alias: named_services
  namespaces:
    slack: { allowed: [provider.about, object.schema, object.search, object.action] }
    conv:  { allowed: [provider.about, object.search, object.get] }
```

Key fields:

- `delegated: true` — the connection carries the agent's consented bearer; an
  unbound connection is dropped, never called with the raw session.
- `resource` — the grant key. It must byte-match the deployment's configured
  delegated-resource id (commonly the wildcard pattern above) so grant creation,
  validation, and per-turn lookup all agree.
- `scopes` — the per-call ceiling the door's `@mcp` guard enforces; adding
  `conv:read` admits the conversation namespace and summons the prompt's
  conversation-recovery block.

This door has **two valid connection shapes** (whole-surface vs per-service) that
differ in governance granularity, consent UX, and what the prompt teaches. That
choice and the roster/scope mechanics are worked in full in the bundle's own
guide — *lg-react: Connecting Named Services — Two Config Shapes*
(`…/ported-langgraph-agents@2026-07-13/docs/agents/lg-react-named-services-config.md`,
in `see_also`) — do not re-derive it here. How namespace operations become
model-callable tools and how the agent sees the scope in its catalog is
[Named Service Tools](../../../tools/named-services-tools-README.md).

A plain (non-named-service) tool that needs a provider claim declares the
requirement inline instead, under the operation's `connections`:

```yaml
send_gmail:
  connections:
    delegated_to_kdcube:
      connected_accounts:
        - { provider_id: google, connector_app_id: gmail, claims: [gmail:send] }
```

Either way the axis the runtime resolves on is the **provider** — which is why
the account binding below keys by provider, not by namespace.

## Surface C — bind the agent to specific accounts and permissions (`account_scope`)

Surfaces A and B decide *whether* an agent may use a provider. `account_scope`
decides *which of the user's connected accounts* it may use AND, **per account,
which claims** — so "read and write from account 1, read-only from account 2" is
expressed directly on the grant, not left to the accounts' own capability.

> Why per-account claims, not just per-account. An account's own claims are
> *shared* — they are the account's capability for the user and for every agent.
> If account 2 was connected with write (because the user or another agent needs
> it), an agent merely *bound to* account 2 with a write claim could still write.
> Restricting *this* agent to read-only on account 2 has to live on the grant.

This is **not** descriptor config. It lives on the agent's grant record (the
"Delegated by KDCube" card) and the user authors it in Connection Hub:

```text
account_scope: { "<provider_id>": { "<account_id>": ["<claim>", ...] } }
             |  { "<provider_id>": { "<account_id>": ["*"] } }   # any claim on that account
             |  { "<provider_id>": { "*": ["*"] } }              # any account, any claim (explicit)
             |   (provider key absent)                           # NOTHING granted there (default)
```

Semantics:

- **Keyed by `provider_id`, then `account_id`** — provider is the universal axis
  the account broker resolves on (a non-namespaced MCP tool and a named-service
  namespace both end at the same `provider_id`); the per-account claim list is
  the exact set this agent may use on that account.
- **Default-closed.** For a delegated caller (an external app or a
  `kdcube-agent:*` client), an absent provider key means **no account access on
  that provider**: the broker refuses with the agent-grant reason
  (`agent_grant_required`) until the user binds an account. An agent never
  inherits the accounts the user connected — the binding *is* the grant.
  "Any account" exists only as the explicit `"*"` entry the user deliberately
  sets. (Non-delegated turns — the user's own trusted tools running as the
  user — carry no binding and are unrestricted; the second gate exists for
  delegated callers.)
- **account `"*"` = any account; claim `"*"` = any claim.** The legacy list form
  `{provider: [account_ids]}` migrates to `{account_id: ["*"]}` (bound accounts,
  any claim), and single-account providers resolve with no friction.
- **The binding decides, not the account's capability.** An account may satisfy a
  claim only when it is bound AND the binding covers that claim — so an agent
  bound read-only to an account that is itself write-capable still cannot write.
  The claims offered per account are that account's own approved claims (you
  cannot grant an agent a claim the account never approved).
- **One source of truth for read/write on account-backed services.** For a
  namespace that runs on a connected account (mail, Slack), read/write is *only*
  the real provider claim resolved per account — there is no parallel
  namespace-level `slack:read`/`slack:write` claim on the door. The connection's
  `scopes` for such a namespace carry just `named_services:use` (door admission);
  the door admits the operation and the per-account provider claim is the read/
  write gate. Only namespaces with no account (conv, memories, tasks) carry a
  namespace-level claim in `scopes`, because there is no per-account layer for
  them. This is also what the proactive consent picker shows: the real per-account
  provider claims, so the user can consent before the agent calls.

Where the user sets it:

- **At consent** — the chat consent card's account picker. A new agent's provider
  claim presents the user's connected accounts, each with its own claim
  checkboxes; the picks are written into `account_scope` as part of the one-click
  grant. Zero connected accounts for the provider → connect one first, then pick.
- **Later** — the Edit control on the agent's "Delegated by KDCube" card, same
  per-account checkboxes, `replace` semantics (un-ticking a provider's accounts
  clears its binding — back to nothing granted there). Because the grant card is
  the live authority the guard resolves each call, an edit applies on the
  agent's next call — no re-mint, no reconnect.

Enforcement is one place — the account broker filters the candidate accounts to
those the binding permits *for the claim being resolved* before its 0/1/many
decision; an explicit account the binding does not cover for that claim is
refused, naming the allowed one. It is applied uniformly on both the MCP-door
path and the native named-service path, so the same binding holds however the
claim is resolved. Per-claim given/pending state across all of this is the
[Claim-Driven Consent](../claim-driven-consent/claim-driven-consent-README.md)
surface.

## Canonical references — copy the shape from here

Three tracked files carry the authoritative shapes. Treat the first as the
reference to copy from when wiring a new deployment or a new agent:

- **The default-install descriptor** — `app/ai-app/deployment/bundles.yaml`, the
  shipped reference (placeholder values, `<PUBLIC_HOST>/<TENANT>/<PROJECT>`). Its
  `connection-hub@1-0` entry carries Surface A; its
  `ported-langgraph-agents@2026-07-13` agent carries the Surface B connection as
  a documented, off-by-default template (both connection shapes shown inline).
- **The Connection Hub bundle template** —
  `…/examples/bundles/connection-hub@1-0/config/bundles.template.yaml`, the
  canonical `delegated_to_kdcube` provider/connector/claim block and the
  `consent_ui` options.
- **The agent's named-services config guide** (worked example, both shapes) —
  the bundle-local doc in `see_also`.

Deployed environments keep their own descriptor sets that must stay coherent
with the default-install shape on these connection points (adapt only host,
tenant, project, and per-environment secrets). Descriptor changes reach a
running environment through the platform apply path — the runtime `bundles.yaml`
and Redis views are derived, never hand-edited.

## Verify

1. **Publish** — the provider shows under *Provider connections* / *Delegated to
   KDCube* in Connection Hub, and the user can connect an account.
2. **Grant** — ask the agent to use the capability; the chat consent card
   appears with the claims and (if accounts exist) the account picker. Grant it;
   the card lands under *Delegated by KDCube* with an *agent* badge.
3. **Scope** — connect two accounts of the same provider, both write-capable. In
   the picker, tick read+write on the first account and read-only on the second.
   Confirm reads succeed on both, a write routes to the first account, and an
   explicit write via the second is refused (naming the allowed account) — even
   though the second account is itself write-capable. Re-tick write on the second
   via Edit and confirm the write now succeeds on the next call.
