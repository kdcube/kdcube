---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/storage-model/storage-model-README.md
title: "Connection Hub Storage Model"
summary: "Storage map for Connection Hub data: descriptors, secrets, request-authenticator metadata, connection edges, link challenges, delegated account tokens, and runtime caches."
status: active
tags: ["sdk", "connections", "connection-hub", "storage", "postgres", "secrets", "connection-edges"]
updated_at: 2026-07-05
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-token-storage-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-properties-and-secrets-lifecycle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/secrets/secrets-service-README.md
---
# Connection Hub Storage Model

Connection Hub uses multiple storage surfaces. They are separate on purpose.
For the token-specific operational answer, start with
[Connection Hub Token Storage](../connection-hub-token-storage-README.md).

```text
descriptors / bundles.yaml
  non-secret deployment config

bundles.secrets.yaml / secrets service
  verifier secrets and OAuth client secrets

Postgres
  request-authenticator metadata

bundle-local filesystem state today
  connection edges
  connection-edge challenges
  connected-account metadata

Redis/cache
  discovery, event delivery, runtime caches
  bundle-session records
  delegated credential grant records

user-scoped secrets
  connected external-provider tokens
```

## Matrix

| Object | Current storage | Contains secrets? | Notes |
| --- | --- | ---: | --- |
| Provider/app config | `bundles.yaml` app props | no | Authority ids, authenticator ids, OAuth app definitions, descriptor authenticators. |
| Authority registry metadata | `connection-hub@1-0.config.authority_registry` | no | Authority ids, `platform: true`, provider instances, provider types, entrypoints, allowed grants, TTL metadata. |
| Bot token / OAuth client secret | `bundles.secrets.yaml` or secrets service | yes | Read through bundle secret lifecycle using `secret_ref`. |
| Request-authenticator metadata | Postgres | no | Stores provider, authority id, authenticator id, `secret_ref`, verifier metadata. |
| Connection edge | bundle-local JSON today | no | Delegates one authority identity to another authority identity. |
| Identity family | derived from connection edges | no | Resolver output for product aggregation; not persisted separately. |
| Connection-edge challenge | bundle-local JSON today | no | Short-lived proof state for writing an edge. |
| Connected account metadata | `ConnectionStore` under `bundle_storage_root()/connections/<user>/accounts.json` | no | Provider, account id, app id, display name, scope, and `has_token`; no raw tokens. |
| Connected external-provider token | user-scoped secrets, key `connections.accounts.<account_id>.tokens` | yes, user token | OAuth access/refresh token, app password, or equivalent provider credential for Gmail/Slack/LinkedIn-style integrations. |
| Connected account OAuth state | `bundle_storage_root()/connections/_oauth_states/<sha256(state)>.json` | no raw secret | Short-lived anti-CSRF state for provider OAuth callbacks. |
| Delegated OAuth code/CSRF/access grant | Redis `GrantStore`, keys `{tenant}:{project}:kdcube:oauth:{code,csrf,agrant}:...` | sensitive auth state | Access grant is keyed by `sha256(access_token)` and carries credential envelope, grantor authority, delegation edges, selected operations, and `resource_grants`. |
| Delegated OAuth refresh token / dynamic client registration | Redis `GrantStore`, keys `{tenant}:{project}:kdcube:oauth:{refresh,client}:...` | yes, auth token | Current implementation is Redis-backed; durable production backing is a strengthening target. |
| Delegated automation access listing | Redis, keys `{tenant}:{project}:kdcube:delegated-access:automation:*` | sensitive metadata | UI-visible records contain label, expiry, last four chars, session id, and `resource_grants`; raw token is shown only at creation. |
| Bundle-session record | Redis, keys `{tenant}:{project}:kdcube:auth:bundle-session:*` | sensitive auth state | Backs `kst1` platform/bundle-session tokens and delegated-client access tokens. |
| Live link update | Data Bus / event delivery | no | Signals original iframe after browser claim completes. |

## Bundle Storage Root Is Filesystem

`bundle_storage_root()` is a filesystem root selected by the runtime. It is not
S3.

```text
local Docker / CLI:
  local mounted filesystem under the runtime data directory

ECS / cloud:
  shared filesystem, normally EFS

shape:
  <BUNDLE_STORAGE_ROOT>/<tenant>/<project>/<safe(bundle_id)>
```

Use it for bundle-managed filesystem state such as UI builds, local indexes,
connection-edge JSON today, and connected-account metadata. Do not store raw
provider tokens there.

S3-backed or localfs-backed artifact storage is the separate
`BundleArtifactStorage` API. Bundle code should not confuse these two surfaces.

## Request-Authenticator Metadata

Request-authenticator metadata belongs in Postgres because it is admin/widget
managed operational metadata:

```text
connection_hub_request_authenticators
  authenticator_id
  authority_id
  provider
  enabled
  role_providing
  subject_namespace
  secret_ref
  selector
  verifier
  properties
```

Secret values must not be stored here. Only `secret_ref` is stored.

## Authority Registry Metadata

Authority registry metadata is descriptor-backed. It is configuration, not
proof and not a secret store:

```yaml
connection-hub@1-0:
  config:
    authority_registry:
      authorities:
        kdcube.platform:
          platform: true
          grants:
            subjects:
              google:<verified_sub>:
                roles: [kdcube:role:super-admin]
                permissions: [kdcube:*:*:*]
            bootstrap_rules:
              - id: bootstrap_admin_by_google_email
                when:
                  provider: google
                  claims:
                    email: owner@example.com
                    email_verified: true
                roles: [kdcube:role:super-admin]
                permissions: [kdcube:*:*:*]
          providers:
            workspace_google_session:
              type: bundle_session_login
              entrypoints:
                login:
                  bundle_id: workspace@2026-03-31-13-36
                  route: public
                  operation: platform_login
                session_issue:
                  bundle_id: workspace@2026-03-31-13-36
                  route: public
                  operation: auth_google_session
                consent:
                  bundle_id: workspace@2026-03-31-13-36
                  route: public
                  operation: delegated_consent
              input:
                authenticator_ref:
                  authority_id: google.accounts
                  provider_id: google_oidc
              issuer:
                type: kdcube_session_token
                ttl_seconds: 43200
              grants:
                default:
                  roles: [kdcube:role:registered]
                  permissions: []
                assignable:
                  roles: [kdcube:role:registered, kdcube:role:super-admin]
                  permissions: [kdcube:*:chat:*;read;write, kdcube:*:*:*]
        google.accounts:
          platform: false
          providers:
            google_oidc:
              type: google_id_token
              authenticator:
                client_id: <google-client-id>.apps.googleusercontent.com
```

The provider instance may reference a bundle-hosted operation, but roles,
permissions, TTL, authority id, and `platform` flag belong to Connection Hub.
The hosting bundle does not keep a separate platform-session policy branch; it
is resolved by `host.bundle_id`, `host.route`, and `host.operation`.

Verifier secrets still stay outside this registry and are reached by
`secret_ref` through the secrets lifecycle.

Descriptor-backed authority grants are a bootstrap/demo policy for
bundle-session platform subjects. They do not make a raw Telegram or Google
proof a platform user by themselves; they are applied only inside a registered
platform login provider flow that issues a platform session.

The canonical assignment key is the stable authority subject, for example
`google:<verified_sub>`. `grants.bootstrap_rules` may help bootstrap an
assignment from verified upstream claims, such as `email + email_verified`, but
email is not stored as the identity key.

The runtime that applies this descriptor shape lives in the Connection Hub SDK:

```text
kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_providers.bundle_session_login
```

## Delegated Credential Grant State Today

The current delegated-client OAuth adapter stores short-lived OAuth and access
grant records through `GrantStore`:

```text
code / csrf / access grant:
  Redis, short TTL, fail closed when missing

refresh token / dynamic client registration:
  currently Redis in the demo implementation
  target production storage is durable
```

The server-side grant records carry enforcement state:

```text
resource
selected tools
selected grants
identity_scope
grantor authority facts
delegation edges
nested named-service namespace catalog
```

Delegated automation access uses the same delegated-client credential model.
Creation mints a `kst1` delegated-client access token and writes two server-side
records:

```text
Bundle-session authority record:
  Redis
  {tenant}:{project}:kdcube:auth:bundle-session:session:<session_id>
  validates the opaque kst1 token and integration subject

Access grant record:
  Redis GrantStore
  {tenant}:{project}:kdcube:oauth:agrant:<sha256(access_token)>
  carries resource_grants and grantor/delegation authority facts
```

The bearer token itself is not the source of authority. Guards read the
server-side grant record and compute grants from matching `resource_grants`
entries for the current request resource.

The Connection Hub Delegated Access widget also stores a UI listing record:

```text
{tenant}:{project}:kdcube:delegated-access:automation:<access_id>
{tenant}:{project}:kdcube:delegated-access:automation-by-grantor:<subject_hash>
```

This record is for listing/revocation UX. It does not contain the raw access
token; the raw token is returned only once, at creation.

## Connected Account Token Storage Today

User-connected provider accounts, such as Gmail, Slack, LinkedIn, iCloud, or a
future provider, split metadata from secrets:

```text
metadata:
  <bundle_storage_root>/connections/<safe_user_id>/accounts.json

provider tokens:
  user-scoped secrets
  connections.accounts.<safe_account_id>.tokens
```

The metadata document records the provider, external user id, app id, scope,
workspace, display name, status, and `has_token`. It must not contain access
tokens, refresh tokens, app passwords, client secrets, or provider signing
secrets.

The provider token is read through the user-secret API. By default,
`ConnectionStore` uses shared user scope (`bundle_id=None`) so another
application acting for the same platform user can resolve the connected account
through Connection Hub without copying tokens into its own bundle storage.

MCP connector presentation metadata is not persisted as authority state. Server
icons, `website_url`, server instructions, and `ToolAnnotations` are advertised
by the MCP server on `initialize` / `tools/list`.

## Connection Edges Today

The current example implementation stores connection edges in bundle-local JSON:

```text
<bundle_storage_root>/connections/connection-edges.json
<bundle_storage_root>/connections/connection-edge-challenges.json
```

This is why Telegram/platform edges do not appear in Postgres yet.

For the local demo runtime, the path is:

```text
~/.kdcube/kdcube-runtime/<tenant>__<project>/data/bundle-storage/<tenant>/<project>/connection-hub-1-0/connections/
```

## Target Storage

For production shape, connection edges and challenges should move to a durable
database or platform identity service:

```text
connection_hub_edges
  edge_id
  relationship
  from_authority_id
  from_provider
  from_subject
  from_user_id
  to_authority_id
  to_provider
  to_subject
  to_user_id
  grants
  constraints
  proof
  status
  metadata

connection_hub_edge_challenges
  challenge_id
  provider
  provider_subject
  target_authority_id
  target_user_id
  grants
  live_event_session_id
  status
  expires_at
  metadata
```

The API and logical flow should not change when storage moves.

## Secret Boundary

Verifier secrets stay in descriptor-backed bundle secrets or the configured
secrets service:

```yaml
secrets:
  identity:
    authenticators:
      telegram_kdcube_ref:
        bot_token: "..."
```

Metadata rows reference secrets:

```yaml
secret_ref: identity.authenticators.telegram_kdcube_ref.bot_token
```

No widget/admin API should accept raw fields such as `bot_token`,
`client_secret`, `signing_secret`, `api_key`, or `secret_value`.
