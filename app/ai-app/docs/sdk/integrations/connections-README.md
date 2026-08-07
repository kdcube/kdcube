---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/connections-README.md
title: "Connections Framework (OAuth integrations)"
summary: "The implemented registry, OAuth, account-store, and settings primitives used by Connection Hub to connect provider accounts while keeping credentials inside trusted server-side integration code."
status: active
tags: ["sdk", "integrations", "connections", "oauth", "connection-hub", "connected-accounts", "named-services"]
updated_at: 2026-08-07
keywords: ["connections framework", "OAuth integration", "ConnectionProvider registry", "ConnectionStore", "user-scoped credential", "connected account", "trusted credential resolver"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/email/email-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/email/email-external-prereq-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/providers-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-accounts/delegated-accounts-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/linkedin-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/search-operations-README.md
---

# Connections Framework (OAuth integrations)

## Goal

Let a user open **Settings → Connections** and connect an external system —
Slack, Gmail, LinkedIn, or any future one — to **their own account** via OAuth,
once, with consent. The connection records the provider capability ceiling and
keeps the provider credential in trusted server-side storage. It does not by
itself grant every agent or external client access: delegated callers also pass
the resource and per-account checks described in
[Delegated Provider Accounts](../solutions/connections/delegated-accounts/delegated-accounts-README.md).
When a named-service provider exists, connected systems can become searchable
or actionable domain objects alongside internal realms.

## Two layers — keep them separate

```
   ┌───────────────────────────  LAYER 2 · CONTEXT  ──────────────────────────┐
   │  named-service providers → canvas resolvers → pins                        │
   │  mem:record  task:issue  conv:…     ⟵ internal (already exist)            │
   │  slack:thread  gmail:msg  li:post   ⟵ external, powered by a connection   │
   └───────────────────────────────────────────────────────────────────────────┘
                                     ▲  provider asks Layer 1 for the user token
   ┌───────────────────────────  LAYER 1 · CONNECTION  ───────────────────────┐
   │  consent + OAuth + user-scoped tokens                                     │
   │  Settings → Connections:  [Slack ○] [Gmail ✓] [LinkedIn ○] [+ …]         │
   └───────────────────────────────────────────────────────────────────────────┘
```

- **Layer 1 (this framework)** answers *"is this user connected to system X, and
  can trusted integration code resolve a credential for this authorized
  operation?"* — generalizes the provider OAuth/account pattern.
- **Layer 2 ([named services](../namespace-services/providers-README.md) +
  [canvas resolvers](../solutions/canvas/search-operations-README.md))** answers
  *"give me searchable / retrievable objects from system X."*

They meet at one seam: a Layer-2 provider asks Layer 1 for a credential handle
inside trusted server-side code after current authority has been checked.
An external system "appears on the pinboard" only when **both** exist. The layers
ship independently — you can connect Slack (Layer 1) before any Slack context
exists (Layer 2).

## Three levels: provider · client app · account

Connections have **three** distinct levels — do not conflate the middle one:

```
Provider TYPE        slack / gmail / telegram          CODE — a ConnectionProvider:
                                                        OAuth URLs, default scopes,
                                                        fetch_profile. NO credentials.
   └─ Client app(s)  "Acme Slack app" (client_id…)     ADMIN data — the platform's OAuth
        many per                                        application clients for that provider.
        provider                                        Multiple per provider. Descriptor-owned
                                                        deployment configuration.
        └─ Account(s) alice @ workspace-A, B, …         USER data — a user connects an account
             many per                                   THROUGH a client app; the account
             user                                       records `app_id`; tokens user-scoped.
```

- **Provider type** is code: the OAuth *mechanics*. It carries no `client_id`/secret.
- **Client app** (a.k.a. connector / application client) is **admin-managed data**:
  `{app_id, provider, label, client_id, client_secret, redirect_uri, scopes,
  enabled}`. There can be **many per provider**. The platform/admin keeps them.
  - Deploy-time config in the connection-hub bundle —
    `connections.providers.<provider>.apps: [{app_id, label, client_id, scopes,
    enabled}]`, with `client_secret` + `oauth_state_secret` in bundle secrets.
- **Account** is user data: connected through one client app, so the account record
  carries `app_id` (needed to refresh the token with that app's credentials).

### How `app_id` threads through the operations
- `connection.catalog` → providers, each listing its **client apps** (the user may
  connect through) and the user's **accounts** (each tagged with its `app_id`).
- `oauth.start(provider, app_id, scopes?)` → uses that client app's credentials.
  `app_id` is required when a provider has more than one app; defaulted when it has
  exactly one. **`scopes` (optional)** is a per-connect override: a scenario can
  request a **subset** of the client app's configured scopes (the admin **ceiling**)
  — so the same client app serves different consent for different scenarios. The
  request is clamped to the ceiling (asking for more requires the admin to widen the
  app); the granted scopes land on the account, and a consumer that needs more can
  re-consent (incremental authorization).
- Trusted credential resolution uses the account's `app_id` to select the app
  credentials when refresh is required. Agents and external clients receive
  operation results or structured consent demands, not this provider token.

## What exists today

The generic `integrations/connections` package supplies the registry, client-app
catalog, account store, OAuth flow, refresh path, and settings operations. Google
and Slack use its built-in `ConnectionProvider` declarations. Provider-specific
integrations such as LinkedIn retain adapters where their API or migration path
requires one, while using the same connected-account broker and delegated-access
contracts at the operation boundary.

Account metadata lives under the bundle storage root; provider tokens live as
user-scoped KDCube secrets and account records carry only a `has_token` flag.
The OAuth callback uses a bundle route under
`…/api/integrations/bundles/<tenant>/<project>/<bundle>/public/<alias>`.

## The framework

```
integrations/connections/
  registry.py   ConnectionProvider interface + register()/resolve()
  apps.py       descriptor-owned OAuth client-app catalog + secret lookup
  store.py      ConnectionStore  (provider-neutral; was *AccountStore)
                keyed by (user_id, provider, account_id); tokens via user-secret API
  oauth.py      generic authorize-url / code-exchange / refresh, state-signed
  settings.py   generic status / start_oauth / callback / disconnect,
                dispatching by provider from the registry
```

### `ConnectionProvider` — the per-provider declaration

Everything that varies between providers, and nothing that doesn't:

```python
@connection_provider("slack")
class SlackConnection(ConnectionProvider):
    provider      = "slack"
    label         = "Slack"
    authorize_url = "https://slack.com/oauth/v2/authorize"
    token_url     = "https://slack.com/api/oauth.v2/access"
    scopes        = ["search:read"]

    async def fetch_profile(self, *, access_token: str) -> dict:
        """Identify the connected user → maps to the account record
        (display_name, external_user_id, workspace?, scope). `external_user_id`
        is THE USER's id in the external system (Slack user, LinkedIn `sub`,
        Gmail address); `workspace`/`team` is a separate dimension when present."""

    # Optional provider-specific token normalization and authorize parameters.
    # Defaults cover the standard authorization-code flow.
```

The registry resolves a provider by name for the generic settings ops. A
provider with no class-level overrides gets the **standard authorization-code
flow. OAuth client ids, secrets, redirect URIs, and per-app scope ceilings come
from `apps.py` and descriptor-owned configuration, not the provider class.

### `ConnectionStore` — provider-neutral, user-scoped

The current `LinkedInAccountStore` / `EmailAccountStore`, generalized:

```python
store = ConnectionStore(storage_root, user_id=user_id, bundle_id=bundle_id)
await store.upsert_account_async({"provider": "slack", "account_id": …,
                                  "display_name": …, "external_user_id": …,
                                  "workspace": …,            # provider org/team, optional
                                  "status": "connected", "scope": [...]})
await store.set_tokens_async(account_id, token)        # → USER-scoped secret (cross-bundle)
accounts = await store.list_accounts_async(provider="slack")
await store.consume_oauth_state_async(state=…, secret=…)
```

Invariants:
- Account JSON holds **metadata + `has_token`** only — never tokens.
- `external_user_id` is the **connected user's id in the external system**, not an
  opaque blob; `workspace`/`team` is a separate field when the provider has one.
- Tokens / refresh tokens live in the **user-secret API at USER scope**
  (`users.<user_id>.secrets…`, i.e. `bundle_id=None`) — **not** in account
  metadata or model-visible objects. See *Connection scope* below.
- `consume_oauth_state` verifies the signed `state` (carries `user_id`,
  `account_id`, `provider`, `source`) — single-use, anti-CSRF.

### Generic settings operations

Identical surface to `linkedin/settings.py`, but **provider-parameterized**:

```python
await connections.status(entrypoint, provider="slack", user_id=…)
await connections.start_oauth(entrypoint, provider="slack", request=…, user_id=…)
await connections.callback(entrypoint, request=…, code=…, state=…)   # provider read from state
await connections.disconnect(entrypoint, provider="slack", account_id=…, user_id=…)
# Telegram-Mini-App variants resolve identity from initData, then delegate.
```

`callback` does **not** need the provider in its signature — the signed `state`
carries it, so a single callback alias serves every provider.

## OAuth flow (one route for all providers)

```
Settings UI ──start_oauth(provider="slack")──▶ connections.start_oauth
     │                       └─ authorize_url + signed state{user,account,provider,source}
     ▼
 Slack consent screen ──user approves──▶ redirect to
   /api/integrations/bundles/<tenant>/<project>/<bundle>/public/connection_oauth_callback?code&state
     │
     ▼
 connections.callback ─ verify state ─ provider = state.provider ─ exchange code ─ fetch_profile
     │                                              └─ store token (user-scoped secret)
     ▼
 ConnectionStore: { user, provider:"slack", account, has_token:true, scopes, external_id }
```

One generic callback alias (`connection_oauth_callback`) dispatches by the
`provider` baked into the signed `state` — **no route per system**. The bundle
that hosts Connections registers that one public alias. External provider setup
(OAuth client, redirect URI, scopes, secrets) follows the same checklist as
[Email External Prerequisites](email/email-external-prereq-README.md), per
provider.

## Settings → Connections UI contract

The UI is generic and driven by the registry:

```
GET  connections.catalog            → [{provider, label, enabled, connected, accounts[]}]
POST connections.start_oauth        → {authorize_url}     (UI opens it)
POST connections.disconnect         → {ok, accounts[]}
```

Each row = one registered provider; `connected` comes from the user's
`ConnectionStore`. "Connect" opens `authorize_url`; on return the row flips to
connected. This is the only new UI surface needed.

## Connection → context (Layer 2)

A connected provider becomes pinnable context by adding **one** named-service
provider that reads through the connection token. Mapping mirrors the memory
provider's object shape (see
[namespace-services/providers](../namespace-services/providers-README.md)):

```
SlackContextProvider (namespace "slack")
  search(query) → Slack search.messages          (token from the user's connection)
  get(ref)      → Slack conversations.replies
  → object.body{ title:"#chan · @author", description: thread text }, capabilities{open}
        │
        ▼  enable namespace "slack" in the scene's canvas resolvers
   slack:thread:<channel>.<ts>  ── drag from search/widget ──▶ pin on the board
```

The pin is a **proxy ref**; per the
[pin search contract](../solutions/canvas/search-operations-README.md) the index
stores a **text snapshot** at pin/update time, while `open`/`get` resolve the
**live** thread. So a Slack thread is searchable next to `task:` and `mem:` pins
with no canvas changes.

## What a new provider must supply

| Piece | Where | Generic? |
| --- | --- | --- |
| OAuth URLs, defaults, and provider-specific token behavior | `ConnectionProvider` subclass | per-provider (small) |
| OAuth client id, secret reference, redirect URI, and scope ceiling | descriptor client-app entry | per deployment |
| `fetch_profile` → account fields | `ConnectionProvider` subclass | per-provider (small) |
| Token storage, state signing, callback route | `connections/{store,oauth,settings}` | **generic** |
| Settings → Connections row | registry-driven UI | **generic** |
| Searchable/retrievable objects (Layer 2) | a named-service provider | per-provider, only if you want it as context |
| Canvas resolver enablement | scene `surfaces…canvas.resolvers` config | **generic** |

## Connection scope & cross-bundle access (the hub model)

A connection belongs to the **user within the tenant/project**, not to one bundle.
One **Connections bundle** owns the lifecycle; other bundles consume.

```
              ┌────────────────────────────────────────────────┐
              │  Connections bundle  (the OWNER / manager)       │
              │   • Settings → Connections widget                │
              │   • OAuth callback alias                         │
              │   • connect / disconnect                         │
              └──────────────────┬─────────────────────────────┘
                                 │ writes at USER scope
                                 ▼
        users.<user_id>.secrets.connections.<provider>… (token)   ← bundle_id=None
        + connection metadata (accounts list, per user)
                                 ▲
       trusted resolution ───────┴───────────────┬───────────────┐
        ┌──────────────────┐       ┌──────────────────┐   ┌──────────────────┐
        │ guarded MCP tool │       │ named service    │   │ trusted app code │
        │ provider call    │       │ provider call    │   │ provider call    │
        └──────────────────┘       └──────────────────┘   └──────────────────┘
```

- **Owner writes at user scope.** The Connections bundle stores the token via the
  user-secret API with `bundle_id=None` → `users.<user_id>.secrets…`. User scope
  lets trusted integration code find the user's account across bundle boundaries;
  it is a storage scope, not delegated authority for every caller.
- **Consumers resolve through the guard.** Named-service providers and plain MCP
  tools declare their provider claims and resolve them under the current request
  identity. The broker checks provider consent, the delegated caller's resource
  grant, and its per-account binding before trusted code receives a credential.
  Agents and external clients never receive the raw provider token.
- **Centralized lifecycle.** Consent, refresh, and revoke live in one place (the
  owner) — a consumer always sees the current token, and disconnect cuts off every
  consumer at once.
- **Delegated governance is default-closed.** A connected account establishes what
  the provider may allow. An agent, automation, or external MCP client also needs
  an explicit caller grant and `account_scope` binding. Missing authority returns
  a structured demand such as `agent_grant_required`,
  `agent_account_binding_required`, or `account_required`.

> This is why the framework's store/tokens are **user-scoped**, not per-bundle. A
> bundle that only wants to *connect its own* account can still pass a `bundle_id`
> for the legacy per-bundle scope, but the hub model is the default for shareable
> connections.

## Compatibility

Provider-specific integrations can retain their public bundle-facing symbols and
adapt to the generic connected-account broker incrementally. The operation
boundary remains the compatibility contract: the same provider claim, account
selector, consent reason, and delegated account binding must be enforced whether
the underlying provider uses the generic registry or a specialized adapter.

## Security & scope

- Tokens are **user-scoped secrets**, never in descriptors, never in account
  metadata, never in logs.
- A connection is **explicit consent** by that user (the OAuth consent screen +
  the "Connect" action); disconnect deletes the account record and revokes the
  stored secret.
- A Layer-2 provider resolves the **connecting user's** account under the current
  request and delegated caller authority. Storage alone does not authorize use.
- When multiple eligible accounts exist, `account_required` asks the caller to
  choose. The selected `account_id` must be passed through preflight and provider
  execution so both checks address the same account.

## Adding a provider

1. Implement and register a small `ConnectionProvider` for the provider's OAuth
   mechanics and profile normalization.
2. Declare one or more client apps in the connection-owner descriptor and place
   each client secret behind its `*_ref`.
3. Expose the provider through Connection Hub's catalog and verify connect,
   refresh, disconnect, and multiple-account selection.
4. For agent use, declare provider claims on a guarded MCP tool or named-service
   provider and test provider consent plus the caller's per-account binding.
5. Add a provider recipe under
   `docs/recipes/connections/integrations/` with live-transport regression cases.

The OAuth connection and the agent-facing domain surface remain separate. A
provider can be connected before its named-service interface exists.
