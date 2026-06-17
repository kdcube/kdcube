---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/connections-README.md
title: "Connections Framework (OAuth integrations)"
summary: "A generic, registry-driven framework for letting a user connect external systems (Slack, Gmail, LinkedIn, …) to their account via OAuth in Settings, with user-scoped tokens — generalizing the per-integration accounts/settings pattern and feeding the named-service + canvas-pin context layer."
status: design
tags: ["sdk", "integrations", "connections", "oauth", "settings", "named-services", "pins"]
keywords: ["connections framework", "oauth integration", "connect external system", "user settings connections", "connection provider registry", "slack integration", "user-scoped tokens", "external context pins"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/email/email-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/email/email-external-prereq-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/providers-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/search-operations-README.md
---

# Connections Framework (OAuth integrations)

> Status: **design**. This article locks the registry interface and data flow so
> the implementation (extracting from `integrations/linkedin`) lands against a
> fixed contract. It is not yet implemented.

## Goal

Let a user open **Settings → Connections** and connect an external system —
Slack, Gmail, LinkedIn, or any future one — to **their own account** via OAuth,
once, with consent. A connection is user-scoped: it grants this user's agent the
ability to read that system on their behalf, and nothing more. Connected systems
then become **searchable, retrievable context** that can be pinned on the board
next to internal objects (memories, tasks, conversations).

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
  what is the token?"* — generalizes the email / linkedin / telegram pattern.
- **Layer 2 ([named services](../namespace-services/providers-README.md) +
  [canvas resolvers](../solutions/canvas/search-operations-README.md))** answers
  *"give me searchable / retrievable objects from system X."*

They meet at one seam: a Layer-2 provider asks Layer 1 for the connected token.
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
        provider                                        Multiple per provider. Deploy-time
                                                        config now; an admin view later.
        └─ Account(s) alice @ workspace-A, B, …         USER data — a user connects an account
             many per                                   THROUGH a client app; the account
             user                                       records `app_id`; tokens user-scoped.
```

- **Provider type** is code: the OAuth *mechanics*. It carries no `client_id`/secret.
- **Client app** (a.k.a. connector / application client) is **admin-managed data**:
  `{app_id, provider, label, client_id, client_secret, redirect_uri, scopes,
  enabled}`. There can be **many per provider**. The platform/admin keeps them.
  - **Now**: deploy-time config in the connection-hub bundle —
    `connections.providers.<provider>.apps: [{app_id, label, client_id, scopes,
    enabled}]`, with `client_secret` + `oauth_state_secret` in bundle secrets.
  - **Later**: an admin view (runtime CRUD) backed by a store; the contract and
    account model don't change — only where the app records come from.
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
- `connection.get_token(provider, account_id?)` → the account's token; the account's
  `app_id` selects the app credentials for refresh.
- Admin (later): `app.list/upsert/delete(provider, …)` to manage client apps at
  runtime; deploy-time config is the read-only source until then.

## What exists today, and the gap

`integrations/email`, `integrations/linkedin`, and `integrations/telegram` each
implement the **same** shape, copied per provider:

- `accounts.py` — an `AccountStore` (`upsert_account`, `set_tokens`,
  `list_accounts`, `delete_account`, `consume_oauth_state`) plus
  `build_<p>_authorize_url`, `exchange_<p>_code`, `fetch_<p>_profile`,
  `<p>_client_id` / `<p>_client_secret` / `<p>_scopes`, `oauth_state_secret`.
- `settings.py` — `configure_<p>_settings(...)`, then `status`, `start_oauth`,
  `callback`, `disconnect` (+ Telegram-Mini-App variants).

Account metadata lives under the bundle storage root; **tokens live as
user-scoped KDCube secrets** (account records carry only a `has_token` flag).
The OAuth callback uses the generic bundle route
`…/api/integrations/bundles/<tenant>/<project>/<bundle>/public/<alias>`.

**The gap:** there is no unifying registry or single Settings UI — adding a
provider means copying a module. This framework extracts the shared shape so a
new provider is a *declaration*, not a copy.

## The framework

```
integrations/connections/
  registry.py   ConnectionProvider interface + register()/resolve()
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
    scopes        = ["search:read", "channels:history", "groups:history"]
    # Non-secret config + secret keys, resolved through entrypoint.bundle_prop /
    # bundle-scoped secrets — same convention as email/linkedin.
    config_prefix = "integrations.slack"          # .enabled / .client_id / .oauth.redirect_uri
    secret_prefix = "integrations.slack"          # .client_secret / .oauth_state_secret

    async def fetch_profile(self, *, access_token: str) -> dict:
        """Identify the connected user → maps to the account record
        (display_name, external_user_id, workspace?, scope). `external_user_id`
        is THE USER's id in the external system (Slack user, LinkedIn `sub`,
        Gmail address); `workspace`/`team` is a separate dimension when present."""

    # Optional provider-specific token handling (Slack returns authed_user +
    # team; OAuth2 PKCE vs client_secret; token rotation). Defaults cover the
    # standard authorization-code flow.
```

The registry resolves a provider by name for the generic settings ops. A
provider with no class-level overrides gets the **standard authorization-code
flow** for free.

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
  (`users.<user_id>.secrets…`, i.e. `bundle_id=None`) — **not** per-bundle — so any
  bundle acting for that user can resolve them. See *Connection scope* below.
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
| OAuth URLs + scopes + config/secret prefixes | `ConnectionProvider` subclass | per-provider (small) |
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
            reads (same user) ───┴───────────────┬───────────────┐
        ┌──────────────────┐       ┌──────────────────┐   ┌──────────────────┐
        │ task-tracker     │       │ versatile scene  │   │ any other bundle │
        │ get_token(slack) │       │ SlackContextProv │   │ get_token(...)   │
        └──────────────────┘       └──────────────────┘   └──────────────────┘
```

- **Owner writes at user scope.** The Connections bundle stores the token via the
  user-secret API with `bundle_id=None` → `users.<user_id>.secrets…`. This is the
  platform primitive that makes a secret readable by **any** bundle acting for that
  user (the security boundary already restricts resolution to the *current request
  user*, so it never crosses users).
- **Consumers read, don't manage.** Other bundles call a thin SDK
  (`connections.get_token(entrypoint, provider, user_id=…)` /
  `connections.list_connections(...)`) that resolves the user-scoped secret +
  metadata. They never run OAuth, never store tokens, never show a connect UI.
- **Centralized lifecycle.** Consent, refresh, and revoke live in one place (the
  owner) — a consumer always sees the current token, and disconnect cuts off every
  consumer at once.
- **Governance (optional, later).** Default = any bundle for that user may read the
  connection ("simply access"). If you later want *"only bundles the user granted
  may use Slack,"* add a per-bundle **grant** record the owner checks — the store
  and consent flow don't change, only an allow-list is consulted on read.

> This is why the framework's store/tokens are **user-scoped**, not per-bundle. A
> bundle that only wants to *connect its own* account can still pass a `bundle_id`
> for the legacy per-bundle scope, but the hub model is the default for shareable
> connections.

## Migration

`integrations/email`, `integrations/linkedin`, `integrations/telegram` keep their
public bundle-facing symbols but become **thin adapters** over
`connections/` (their `accounts.py`/`settings.py` delegate to the generic store
and settings ops; provider specifics move into a `ConnectionProvider`). No bundle
route or config key needs to change during migration.

## Security & scope

- Tokens are **user-scoped secrets**, never in descriptors, never in account
  metadata, never in logs.
- A connection is **explicit consent** by that user (the OAuth consent screen +
  the "Connect" action); disconnect deletes the account record and revokes the
  stored secret.
- A Layer-2 provider may only use the **connecting user's** token, for that
  user's requests — scope checks live in the provider/economics guard, not in the
  store.

## First step

1. Extract `connections/{registry,store,oauth,settings}` from `integrations/linkedin`
   (the cleanest current copy).
2. Add `SlackConnection` (`ConnectionProvider`) — connect only, no context yet.
3. Add the **Settings → Connections** UI with Slack as the single live row.

That proves the consent/OAuth/token loop end-to-end. `SlackContextProvider`
(Layer 2) follows once the connection works.
