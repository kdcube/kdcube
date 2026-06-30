# Connection Hub Interface

`connection-hub@1-0` exposes authenticated `operations` aliases, public
proof/auth callback routes, one `connections` named-service provider, and one widget.
The OpenAPI file
[connection-hub.openapi.yaml](connection-hub.openapi.yaml) documents the
operations and callback routes in detail; this README is the human contract.

Every POST `operations` body is wrapped as `{ "data": { ... } }` by frontend
callers. Responses are platform-wrapped; unwrap the property named by the
operation alias.

## Browser surfaces

The Connections settings widget is a separate React/Redux browser app served from
`ui/widgets/connections` and controlled by `ui.widgets.connections_settings`:

```text
/api/integrations/bundles/{tenant}/{project}/connection-hub@1-0/widgets/connections_settings
```

The widget reads/writes through the operations below. It shows connection edges
and connected accounts in one place, while keeping their semantics separate. It
never holds provider credentials; it only kicks off the hub-owned OAuth /
app-password flows.

## Connection-edge vs delegated account contract

```text
connection edge
  provider + provider_subject -> platform_user_id
  purpose: prove and route the platform principal

delegated account connection
  platform_user_id + provider account -> token/capability
  purpose: let automation act for the user
```

`identity_resolve` returns a principal envelope. The `role_resolution` field is
reserved for a platform principal/role resolver. The app's configured
`identity.role_bindings` mode is a local fixture only; consumers must not treat
Connection Hub as the final role authority.

The current manual connection-edge form is a development/onboarding fixture. A
production auth bridge must first verify the external proof (OAuth profile,
Telegram login signature, signed app webhook, etc.) and only then call
`identity_resolve` for that verified `provider + provider_subject`.

## API aliases (operations route)

All are authenticated (`PlatformAuth`) and visibility-gated by
`visibility.api.<alias>.{user_types,roles}`.

| Alias | Method | Route | Purpose |
| --- | --- | --- | --- |
| `named_service` | POST | operations | Serves the whole `connections` named-service contract (provider/about/capabilities, object list/get/action/resolve, `connection.get_token`, start/disconnect). |
| `connection_edges_list` | GET | operations | List external identities linked to the current platform user. |
| `connection_edge_upsert` | POST | operations | Link a verified external identity to the current platform user. User-facing route; does not grant roles. |
| `connection_edge_remove` | POST | operations | Remove one external connection edge from the current platform user. |
| `connection_edge_challenge_create` | POST | operations | Create a short-lived one-time proof challenge for the current platform user. |
| `connection_edge_challenge_status` | POST | operations | Read a proof challenge without claiming it. Platform-first challenges are limited to their platform user; provider-first pending challenges can be previewed by the currently signed-in platform user before explicit confirmation. |
| `identity_resolve` | POST | operations | Resolve a verified external identity (`provider`, `provider_subject`) to a platform principal envelope. |
| `identity_family_resolve` | POST | operations | Resolve the current actor/platform user to the linked identity family: platform authority identity, provider/integration identities, and canonical user ids for aggregation. |
| `delegated_identity_scope_resolve` | POST | operations | Resolve a verified delegated credential envelope to the grantor user ids allowed by its delegation edge and identity scope. |
| `authenticators_list` | GET | operations | List Connection Hub authenticator modules/configured rows and secret-reference status. Secret values are never returned. |
| `authenticators_upsert` | POST | operations | Create/update a Postgres-backed authenticator metadata row. Accepts authenticator selector metadata, `role_providing`, and `secret_ref`; rejects secret values. |
| `authenticators_remove` | POST | operations | Soft-delete a Postgres-backed authenticator metadata row. Descriptor-defined rows are not removed through this API. |
| `connections_catalog` | GET | operations | Catalog of providers → client apps → user accounts, with connected/configured flags. |
| `connections_start_oauth` | POST | operations | Begin OAuth for `provider` + `app_id`; optional per-connect `scopes` subset (list or space/comma string). Returns an authorize URL. |
| `connections_disconnect` | POST | operations | Disconnect a user account (`provider`, `account_id`). |
| `email_accounts_status` | GET | operations | iCloud account status (the email integration serves iCloud only; Gmail is a connections provider). |
| `email_connect_app_password` | POST | operations | Connect an **iCloud** app-password account (`provider="icloud"`, `email`, `app_password`, …). |
| `email_disconnect_account` | POST | operations | Disconnect an iCloud account (`account_id`). |
| `connections_settings` | GET | operations | Thin widget data alias (also the `@ui_widget` alias). |

> Gmail OAuth is **not** here — it rides the connections ops (`connections_start_oauth`
> / `connection_oauth_callback`) like Slack. The email integration is iCloud-only
> (app-password), so there is no email OAuth start/callback.

### Public callback route

A browser redirect target, not a JSON op — reached by the external provider after
the user authorizes. One shared callback for **all** OAuth providers (Gmail, Slack):

| Alias | Method | Route | Redirect URI to register |
| --- | --- | --- | --- |
| `connection_oauth_callback` | GET | public | `…/connection-hub@1-0/public/connection_oauth_callback` |

It accepts `code`, `state`, `error` and completes the hub-owned flow, validating
`state` with `connections.oauth_state_secret`. The provider + client app are read
from the signed `state`.

### Public Telegram proof route

Telegram proof routes are not platform-login routes. They validate Telegram
Mini App `initData`; the platform user is supplied only by an authenticated
KDCube session.

There are two directions:

```text
Platform-first:
  KDCube session creates challenge -> host-specific provider proof surface completes it.

Telegram-first:
  host-specific provider surface creates provider proof -> KDCube session claims it.
```

| Alias | Method | Route | Purpose |
| --- | --- | --- | --- |
| `telegram_connection_edge_start` | POST | public | Validate Telegram Mini App `initData`, create a pending provider proof, and return `platform_claim_url`. |
| `telegram_connection_edge_status` | GET | public | Validate Telegram Mini App `initData` and report whether that Telegram subject is already linked. |
| `telegram_connection_edge_remove` | POST | public | Validate Telegram Mini App `initData` and unlink that Telegram subject from its platform user. |
| `telegram_connection_edge_complete` | POST | public | Validate Telegram Mini App `initData` and complete a pending Telegram connection-edge challenge. |
| `federated_data_bus_claim` | POST | public | Validate promoted request auth context and issue a short-lived Socket.IO token for the Connection Hub widget live channel. |
| `request_authenticate` | POST | public | Gateway/app selector endpoint: authenticate a request envelope through configured authenticators and return linked authority. |

The caller must send signed Telegram Mini App initData in
`X-Telegram-Init-Data`. KDCube-controlled callers should also send the
non-secret selector headers `X-KDCube-Auth-Authority-ID: <authority_id>` and
`X-KDCube-Auth-Authenticator-ID: <authenticator_id>`.
`telegram_connection_edge_complete` also requires body
`{ "data": { "challenge_id": "..." } }`.

`request_authenticate` is the generic request-authenticator operation. It is
not user-facing login UI. Gateway/middleware and app/channel handlers call it
with a normalized request envelope. The provider proof implementations are
Connection Hub modules: they can read Connection Hub connection-edge storage,
app config, and app secrets, while callers stay provider-neutral.

Authenticator admin APIs store metadata only. A row can say
`id: telegram.support` and
`secret_ref: identity.authenticators.telegram_support.bot_token`, but the token itself must be
stored through `bundles.secrets.yaml` or the configured bundle secrets provider.
Posting fields such as `secret_value`, `bot_token`, `client_secret`,
`signing_secret`, or `api_key` is rejected. `role_providing` is false for
linked external providers such as Telegram; platform roles come from the linked
platform principal.

```json
{
  "data": {
    "request": {
      "method": "POST",
      "path": "/api/integrations/...",
      "headers": {
        "x-telegram-init-data": "<Telegram.WebApp.initData>"
      },
      "query": {},
      "cookies": {}
    }
  }
}
```

Example response:

```json
{
  "ok": true,
  "authenticated": true,
  "provider": "telegram",
  "provider_subject": "314062490",
  "actor_user_id": "telegram_314062490",
  "platform_user_id": "02e...",
  "identity_authority": {
    "actor_user_id": "telegram_314062490",
    "platform_user_id": "02e...",
    "economics_user_id": "02e...",
    "platform_roles": ["kdcube:role:super-admin"],
    "budget_bypass": true
  }
}
```

## Request payload shapes

`connections_start_oauth`:

```json
{
  "data": {
    "provider": "slack",
    "app_id": "acme-search",
    "scopes": ["search:read", "channels:history", "groups:history"],
    "return_hint": ""
  }
}
```

`connections_disconnect`:

```json
{ "data": { "provider": "slack", "account_id": "<workspace_account_id>" } }
```

`email_connect_app_password` (iCloud):

```json
{
  "data": {
    "provider": "icloud",
    "email": "user@icloud.com",
    "app_password": "<apple-app-specific-password>",
    "display_name": "User",
    "username": "user@icloud.com"
  }
}
```

`named_service` (standard envelope; serves the whole `connections` contract):

```json
{
  "data": {
    "operation": "connection.get_token",
    "namespace": "connections",
    "payload": { "provider": "slack" }
  }
}
```

`connection_edge_upsert`:

```json
{
  "data": {
    "provider": "google",
    "provider_subject": "user@example.com",
    "label": "Google account"
  }
}
```

`connection_edge_challenge_create`:

```json
{ "data": { "provider": "telegram" } }
```

Example response:

```json
{
  "ok": true,
  "challenge": {
    "challenge_id": "one-time-token",
    "provider": "telegram",
    "platform_user_id": "02e...",
    "status": "pending",
    "expires_at": 1780000000
  }
}
```

This operation creates a server-side challenge only. The host surface that owns a
specific Telegram Mini App is responsible for opening that Mini App and carrying
the challenge id; Connection Hub does not derive a generic Telegram destination.

`telegram_connection_edge_start`:

```http
POST /api/integrations/bundles/{tenant}/{project}/connection-hub@1-0/public/telegram_connection_edge_start
X-Telegram-Init-Data: <Telegram.WebApp.initData>
Content-Type: application/json

{ "data": {} }
```

Example response:

```json
{
  "ok": true,
  "provider": "telegram",
  "provider_subject": "314062490",
  "challenge": {
    "challenge_id": "one-time-token",
    "provider": "telegram",
    "provider_subject": "314062490",
    "status": "pending_platform_claim",
    "expires_at": 1780000000
  },
  "platform_claim_url": "https://.../public/widgets/connections_settings?claim_challenge=one-time-token"
}
```

When the caller wants a no-poll completion signal, include the Connection Hub
live session id returned by `federated_data_bus_claim`:

```json
{ "data": { "live_event_session_id": "socket-session-id" } }
```

Connection Hub stores that session id on the challenge and emits
`connection_hub.edge.changed` to it after
`connection_edge_challenge_claim` succeeds.

`federated_data_bus_claim`:

```http
POST /api/integrations/bundles/{tenant}/{project}/connection-hub@1-0/public/federated_data_bus_claim
X-Telegram-Init-Data: <Telegram.WebApp.initData>
X-KDCube-Auth-Authority-ID: telegram.kdcube_ref
X-KDCube-Auth-Authenticator-ID: telegram.kdcube_ref.init_data
Content-Type: application/json

{ "data": {} }
```

Example response:

```json
{
  "ok": true,
  "schema": "kdcube.federated_token_claim.v1",
  "federated_token": "kst-fed...",
  "session_id": "federated-session-id",
  "expires_at": 1780000000,
  "bundle_id": "connection-hub@1-0"
}
```

`connection_edge_challenge_claim`:

```http
POST /api/integrations/bundles/{tenant}/{project}/connection-hub@1-0/operations/connection_edge_challenge_claim
Content-Type: application/json

{ "data": { "challenge_id": "one-time-token", "confirmed": true } }
```

This route requires the user to be authenticated in KDCube and requires
`confirmed=true`. The browser claim page must call
`connection_edge_challenge_status` first, show the Telegram identity and current
KDCube user, and only then call this route after the user explicitly confirms.
It links the verified Telegram identity from the pending challenge to the
current platform user.

`telegram_connection_edge_complete`:

```http
POST /api/integrations/bundles/{tenant}/{project}/connection-hub@1-0/public/telegram_connection_edge_complete
X-Telegram-Init-Data: <Telegram.WebApp.initData>
Content-Type: application/json

{ "data": { "challenge_id": "one-time-token" } }
```

`identity_resolve`:

```json
{
  "data": {
    "provider": "telegram",
    "provider_subject": "314062490"
  }
}
```

Example response:

```json
{
  "ok": true,
  "connection_edge": {
    "provider": "telegram",
    "provider_subject": "314062490",
    "platform_user_id": "02e..."
  },
  "principal": {
    "platform_user_id": "02e...",
    "roles": [],
    "permissions": [],
    "role_resolution": {
      "status": "platform_resolver_not_wired",
      "source": "platform.principal_role_resolver"
    }
  }
}
```

`identity_family_resolve`:

```json
{
  "data": {
    "input_user_id": "telegram_314062490"
  }
}
```

Example response:

```json
{
  "ok": true,
  "schema": "connection_hub.identity_family.v1",
  "linked": true,
  "platform_user_id": "02e...",
  "authority": {
    "kind": "authority",
    "authority_id": "platform",
    "provider": "platform",
    "user_id": "02e..."
  },
  "identities": [
    {
      "kind": "authority",
      "authority_id": "platform",
      "provider": "platform",
      "user_id": "02e..."
    },
    {
      "kind": "integration",
      "provider": "telegram",
      "provider_subject": "314062490",
      "user_id": "telegram_314062490",
      "integration_id": "telegram.kdcube_ref"
    }
  ],
  "memory_user_ids": ["02e...", "telegram_314062490"]
}
```

Consumers such as the memories app should use `memory_user_ids` server-side
when aggregating records across linked identities. The widget/client must not
provide arbitrary memory owner ids.

`delegated_identity_scope_resolve`:

```json
{
  "data": {
    "credential": {
      "schema": "kdcube.credential.v1",
      "credential_kind": "delegated_client_access",
      "issuer_authority_id": "delegated_client",
      "issuer_authenticator_id": "delegated_client.bearer",
      "subject": "integration:claude:02e...",
      "audience": "kdcube:delegated_client",
      "attrs": {
        "grantor_subject": "02e...",
        "client_id": "claude",
        "resource": "https://runtime/api/integrations/bundles/demo/demo/user-memories@2026-06-26/public/mcp/memories",
        "scopes": ["memories:read"],
        "tools": ["memory_search", "memory_get"],
        "identity_scope": "grantor_identity_family"
      }
    }
  }
}
```

Example response:

```json
{
  "ok": true,
  "schema": "connection_hub.delegated_identity_scope.v1",
  "delegate_identity": "integration:claude:02e...",
  "grantor_user_id": "02e...",
  "identity_scope": "grantor_identity_family",
  "memory_user_ids": ["02e...", "telegram_314062490"]
}
```

This is for already-verified delegated credentials. Product surfaces should use
it instead of parsing `grantor_subject` directly.

## Config keys that control these surfaces

Non-secret deploy props (see [../config/bundles.template.yaml](../config/bundles.template.yaml)):

- `connections.oauth.public_base_url` — public base for the shared connection
  callback (empty → derived from request host).
- `connections.providers.<provider>.apps` — client apps, MANY per provider:
  `[{app_id, label, client_id, scopes, enabled}]`. This is the only place a
  provider (incl. **Gmail** under `google` and **Slack** under `slack`) becomes
  usable; with no enabled app the provider is unconfigured.
- `identity.role_resolver.mode` — usually `platform`; `configured` is only for
  local demos until a platform principal/role resolver is wired.
- `identity.role_bindings` — optional local fixture used only when
  `identity.role_resolver.mode=configured`.
- `integrations["email.connection_hub"].enabled` — turn the iCloud
  (app-password) email ops on.
- `ui.widgets.connections_settings.{enabled, src_folder, build_command}` — the
  widget build.
- `visibility.api.<alias>.*` / `visibility.widget.connections_settings.*` — access.

Deploy secret KEYS (see [../config/bundles.secrets.template.yaml](../config/bundles.secrets.template.yaml)):

- `connections.oauth_state_secret` — hub-level, signs the OAuth `state` for all
  connections providers (Gmail, Slack).
- `connections.providers.<provider>.apps.<app_id>.client_secret` — per client app
  (e.g. `connections.providers.google.apps.gmail.client_secret`,
  `connections.providers.slack.apps.<app_id>.client_secret`).
- iCloud needs no deploy secret (the user's app-password is user-scoped state).

No user credentials live in any descriptor: user OAuth tokens / app-passwords are
user-scoped app state created through these flows.

## Prerequisites

The hub requires external operator/user setup before a connection can work.
**Full step-by-step setup (Google Cloud Console, Slack app, iCloud) is in
[docs/integrations/](../docs/integrations/README.md) (one article per provider).**
A summary follows:

### Slack (connections provider)

- A workspace admin creates an OAuth app in the Slack workspace.
- Config the resulting client into a client app:
  - Client ID → `connections.providers.slack.apps.<app_id>.client_id`
  - Client Secret → secret `connections.providers.slack.apps.<app_id>.client_secret`
- Add the redirect URI to the Slack app:
  `…/connection-hub@1-0/public/connection_oauth_callback`
- Request scopes: `search:read`, `channels:history`, `groups:history`.
- Set `connections.oauth_state_secret` (hub-level) so OAuth `state` can be signed.

### Gmail (connections provider)

Gmail rides the **connections framework** (Google OAuth), same as Slack — it gets
per-connect scopes, token refresh, and cross-app `connection.get_token`.

- The Google OAuth client must have the **shared** connections callback added as
  an authorized redirect URI: `…/connection-hub@1-0/public/connection_oauth_callback`.
- Config the client into a Google client app:
  - Client ID → `connections.providers.google.apps.<app_id>.client_id`
  - Client Secret → secret `connections.providers.google.apps.<app_id>.client_secret`
- Scopes: `openid email profile gmail.readonly gmail.send` (send is needed for
  task email delivery). `connections.oauth_state_secret` signs the state.

### iCloud (email integration — app-password)

- No admin/OAuth setup. The user creates an Apple **app-specific password** and
  enters it via `email_connect_app_password`. The hub's `integrations.email` now
  serves iCloud only.

### Runtime

- The runtime must be refreshed so the new app loads, registers its
  named-service provider in discovery, and builds the `connections_settings`
  widget.

> Full step-by-step provider setup is in
> [docs/integrations/](../docs/integrations/README.md) (one article per provider).
