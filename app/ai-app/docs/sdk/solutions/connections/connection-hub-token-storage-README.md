---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-token-storage-README.md
title: "Connection Hub Token Storage"
summary: "One-page answer for where Connection Hub stores Claude Code delegated OAuth tokens, manual Delegated by KDCube tokens, connected-account provider tokens, and deployment secrets."
status: active
tags: ["sdk", "solutions", "connections", "connection-hub", "tokens", "storage", "oauth", "delegated-credentials", "secrets", "redis"]
updated_at: 2026-07-16
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/storage-model/storage-model-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-connections/design/grant-storage-durability-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/bundle-session-auth-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/runtime-configuration-and-secrets-store-README.md
---
# Connection Hub Token Storage

This page is the single operational answer for "where did Connection Hub put
the token?" The answer depends on which token family is involved.

## At A Glance

```text
+--------------------------+       +----------------------------+
| Claude Code / MCP client |       | Connection Hub UI          |
| external caller          |       | "Delegated by KDCube"      |
+------------+-------------+       +-------------+--------------+
             |                                   |
             | OAuth authorize/token             | create manual access
             v                                   v
       +-----+-----------------------------------+-----+
       | KDCube delegated-client credential system     |
       | - mints kst1 integration tokens               |
       | - binds selected grants/tools                 |
       | - validates through Redis grant/session state |
       +-----+-----------------------------------+-----+
             |                                   |
             v                                   v
   Redis GrantStore + bundle-session records     Redis listing metadata


+--------------------------+       +----------------------------+
| User connects Gmail etc. |       | Operator config/secrets    |
| provider account         |       | OAuth app / bot / signing  |
+------------+-------------+       +-------------+--------------+
             |                                   |
             v                                   v
       +-----+------------------+          +-----+----------------+
       | ConnectionStore        |          | secrets provider     |
       | metadata: filesystem   |          | deployment secrets   |
       | tokens: user secrets   |          | not user tokens      |
       +------------------------+          +----------------------+
```

Another compact way to read the same model:

```text
token question
  |
  +-- "Claude Code got a token from /oauth/token"
  |       -> Redis OAuth GrantStore + Redis bundle-session authority
  |
  +-- "I copied a manual Delegated by KDCube token"
  |       -> Redis bundle-session + Redis access grant + Redis listing row
  |
  +-- "User connected Gmail/Slack/iCloud/provider"
  |       -> ConnectionStore metadata on filesystem
  |       -> provider token in user-scoped secrets
  |
  +-- "Where is the Google client secret / Telegram bot token?"
          -> deployment secrets provider
```

Do not treat these as one "connection token" store. They have different
lifetimes, revocation paths, and authority boundaries.

## Quick Matrix

| Token or credential | Example | Storage | Raw token exposed later? | Notes |
| --- | --- | --- | ---: | --- |
| Claude Code delegated OAuth access token | `kst1...` returned by `/public/oauth/token` | Redis bundle-session record plus Redis access-grant record | no | Client holds the bearer. Server verifies Redis session + grant record. |
| Claude Code delegated OAuth refresh token | token returned by `/public/oauth/token` | Redis `GrantStore` refresh record | no | Rotated on refresh. Redis loss fails closed and can require reconnect. |
| Claude Code dynamic client registration | `client_id=dcr-...` | Redis `GrantStore` client record | n/a | Long-lived but currently Redis-backed. |
| Manual Delegated by KDCube token | token copied from the Delegated Access UI | Redis bundle-session record plus Redis access-grant record | no, shown once | Listing stores metadata and last four chars, not the raw bearer. |
| OAuth-flow Delegated Access listing row | grant visible in Connection Hub UI after OAuth consent | Redis delegated-access registry | no public exposure | Internal row stores current token handles so revoke can kill refresh/access grants. |
| Connected provider token | Gmail/Slack access + refresh token | user-scoped secrets | no | `ConnectionStore` stores metadata in filesystem, raw provider tokens in secrets. |
| OAuth state for connected provider account | Google/Gmail callback `state` | app filesystem storage | n/a | Single-use anti-CSRF state, no raw provider token. |
| OAuth client secret / bot token | Google client secret, Telegram bot token | configured secrets provider | no | Referenced by `secret_ref` or bundle secret key. |

## Claude Code / External Client OAuth

This is the flow used when Claude Code or another MCP client connects to a
KDCube MCP/resource through Connection Hub OAuth.

```text
     browser consent                         token exchange
          |                                       |
          v                                       v
+---------+----------+                  +---------+----------+
| /oauth/authorize   |                  | /oauth/token       |
| platform session   |                  | PKCE/code verified |
| consent + CSRF     |                  | token minted       |
+---------+----------+                  +---------+----------+
          |                                       |
          | auth code                             | access_token = kst1
          | stores grant facts                    | refresh_token = rotating
          v                                       v
 +--------+----------+                 +----------+-----------+
 | Redis code record |                 | Redis refresh record |
 | short TTL         |                 | long TTL, rotating   |
 +-------------------+                 +----------+-----------+
                                                   |
                                                   v
                                      +------------+-------------+
                                      | Redis access grant       |
                                      | sha256(access_token)     |
                                      | selected operations      |
                                      | resource grants          |
                                      | delegation edges         |
                                      +------------+-------------+
                                                   |
                                                   v
                                      +------------+-------------+
                                      | Redis bundle session     |
                                      | kst1 session id          |
                                      | token_sha256             |
                                      | integration subject      |
                                      +--------------------------+
```

The access token is not a raw platform user session. It is an integration
session for a representative subject:

```text
integration:<client_id>:<grantor-sub>
```

Common Claude Code shape:

```text
integration:claude:<grantor-sub>
```

### Redis Records

The OAuth delegated credential adapter stores tenant/project-scoped state in
Redis:

```text
{tenant}:{project}:kdcube:oauth:code:<auth_code>
{tenant}:{project}:kdcube:oauth:csrf:<csrf_token>
{tenant}:{project}:kdcube:oauth:refresh:<refresh_token>
{tenant}:{project}:kdcube:oauth:client:<client_id>
{tenant}:{project}:kdcube:oauth:agrant:<sha256(access_token)>
```

The access token itself is a `kst1` bundle-session token. Its active session is
also backed by Redis:

```text
{tenant}:{project}:kdcube:auth:bundle-session:user:<sub>
{tenant}:{project}:kdcube:auth:bundle-session:session:<session_id>
{tenant}:{project}:kdcube:auth:bundle-session:user-sessions:<sub>
{tenant}:{project}:kdcube:auth:bundle-session:user-version:<sub>
```

The `session` record stores `token_sha256`, not only bearer claims. Validation
checks:

1. token signature using `services.session_token.secret`;
2. token expiry;
3. active Redis session record;
4. `token_sha256` match;
5. current user/version record;
6. delegated access-grant record for resource/tool enforcement.

### OAuth Grant Registry

When an OAuth-flow delegated grant is issued or refreshed, Connection Hub also
writes a delegated-access registry row so the grant is visible and revocable in
the Connection Hub UI:

```text
{tenant}:{project}:kdcube:delegated-access:automation:<access_id>
{tenant}:{project}:kdcube:delegated-access:automation-by-grantor:<subject_hash>
```

For OAuth-flow grants, this internal registry row includes the current
`access_token` and `refresh_token` handles so revoke can invalidate the active
access grant and refresh token. Public list responses remove those fields.

## Manual Delegated By KDCube Token

The Connection Hub "Delegated by KDCube" UI can create a manual token for an
external automation.

```text
+-------------------------+
| Connection Hub UI       |
| Delegated by KDCube tab |
+------------+------------+
             |
             | POST delegated_access_create
             v
+------------+--------------------------------+
| AutomationAccessService.create_access(...)  |
| - checks grantor platform authority         |
| - mints kst1 integration session            |
| - binds selected resource grants            |
+------------+--------------------+-----------+
             |                    |
             |                    |
             v                    v
+------------+-----------+   +----+------------------------+
| Redis bundle session   |   | Redis access grant          |
| session:<session_id>   |   | oauth:agrant:<sha256(tok)> |
+------------+-----------+   +----+------------------------+
             |                    |
             +----------+---------+
                        |
                        v
          +-------------+------------------+
          | Redis UI listing record        |
          | delegated-access:automation:*  |
          | public listing strips secrets  |
          +--------------------------------+
```

The raw bearer is returned once in the create response. The listing record
contains metadata for display and revocation:

```text
access_id
label
client_id
grantor_subject
delegate_subject
resource_grants
operations
identity_scope
session_id
created_at
expires_at
last_four
source=manual
```

Manual listing rows do not store the raw access token for later display.
Revocation uses the stored `session_id` and grant metadata to remove the active
bundle-session record and access-grant binding.

## Connected Provider Account Tokens

This is the `ConnectionStore` path. It is used for accounts that KDCube acts
through, such as Gmail, Slack, iCloud, LinkedIn, or a custom OAuth/OIDC
service.

`ConnectionStore` deliberately splits account metadata from token material.

```text
              provider OAuth callback
                       |
                       v
             +---------+----------+
             | ConnectionStore    |
             +---------+----------+
                       |
        +--------------+----------------+
        |                               |
        v                               v
+-------+-------------------+   +-------+--------------------+
| app filesystem metadata   |   | user-scoped secrets       |
| accounts.json             |   | raw provider token JSON   |
| _oauth_states/*.json      |   | access/refresh/app pass   |
+---------------------------+   +----------------------------+
```

### Filesystem Metadata

The account document lives under the app storage root passed to
`ConnectionStore`:

```text
<root>/connections/<safe_user_id>/accounts.json
```

It stores non-secret metadata:

```text
account_id
provider
app_id
external_user_id
workspace
email
display_name
status
scope
connected_at
updated_at
last_error
has_token
```

It must not store access tokens, refresh tokens, app passwords, client secrets,
or signing secrets.

The OAuth state file is also filesystem state:

```text
<root>/connections/_oauth_states/<sha256(state)>.json
```

That state is anti-CSRF and callback routing state. It is not the provider
token.

### User-Scoped Secret Key

The raw provider token bundle is stored through the SDK user-secret API:

```python
await set_user_secret(
    "connections.accounts.<safe_account_id>.tokens",
    token_json,
    user_id=user_id,
    bundle_id=None,
)
```

With the default `shared_tokens=True`, `ConnectionStore` passes
`bundle_id=None`. The secrets manager builds this key:

```text
users.<user_id>.secrets.connections.accounts.<safe_account_id>.tokens
```

With legacy/per-app `shared_tokens=False`, the key includes the app id:

```text
users.<user_id>.bundles.<bundle_id>.secrets.connections.accounts.<safe_account_id>.tokens
```

The physical backend is the configured secrets provider:

| Deployment mode | User-secret backend |
| --- | --- |
| CLI local `secrets-file` | local configured secrets file/provider |
| direct local service | configured secrets provider |
| AWS `aws-sm` | AWS Secrets Manager grouped/canonical provider contract |
| secrets-service | secrets service |
| in-memory | process memory, test/dev only |

User-scoped secrets are operational state. They are not exported to
`bundles.yaml` or `bundles.secrets.yaml` by config export.

## Deployment Secrets

Deployment-owned secrets are separate from connected account tokens and
delegated OAuth grants.

Examples:

```text
Google OAuth client secret
Slack OAuth client secret
Telegram bot token
services.session_token.secret
services.federated_token.secret
```

These live in the configured secrets provider:

```text
local secrets-file mode:
  secrets.yaml / bundles.secrets.yaml

AWS mode:
  AWS Secrets Manager

other modes:
  configured provider
```

Connection Hub metadata should reference these values by `secret_ref` or by
canonical secret key. Request-authenticator metadata in Postgres stores only
`secret_ref`, never the raw value.

## What Is Not Stored Where

| Misconception | Correct boundary |
| --- | --- |
| Claude Code OAuth token is stored in `ConnectionStore`. | No. Claude Code delegated OAuth uses Redis `GrantStore` + Redis bundle sessions. |
| Gmail/Slack provider tokens are stored in Redis. | No. Provider account tokens use user-scoped secrets. |
| Provider tokens are in `accounts.json`. | No. `accounts.json` is metadata only. |
| Manual Delegated by KDCube token can be displayed again later. | No. It is returned once; later listing shows metadata and `last_four`. |
| `bundles.yaml` or `bundles.secrets.yaml` owns user connected-account tokens. | No. User-scoped secrets are operational state, not descriptors. |
| Postgres stores Connection Hub provider tokens. | No. Postgres stores request-authenticator metadata, not raw verifier/provider tokens. |

## Code Map

| Concern | Code |
| --- | --- |
| Connected provider account metadata + token helper | `kdcube_ai_app.apps.chat.sdk.integrations.connections.store.ConnectionStore` |
| User secret helper | `kdcube_ai_app.apps.chat.sdk.config.get_secret/set_user_secret/delete_user_secret` |
| User secret key builder | `kdcube_ai_app.infra.secrets.manager.build_user_secret_key` |
| OAuth delegated credential grant store | `kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.store.GrantStore` |
| OAuth `/authorize` and `/token` routes | `kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.http.routes` |
| Manual Delegated by KDCube token service | `kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.automation_access.AutomationAccessService` |
| `kst1` bundle-session authority | `kdcube_ai_app.auth.bundle.sessions.BundleSessionAuthority` |

## Operational Checks

To identify which storage family you are looking at, ask:

1. Is this a Claude Code or MCP client OAuth access/refresh token?
   - Check Redis OAuth and bundle-session keys.
2. Is this a manual token copied from Delegated by KDCube?
   - Check Redis delegated-access registry plus bundle-session keys.
3. Is this a Gmail/Slack/iCloud/custom provider account token?
   - Check `ConnectionStore` metadata under app storage and user-scoped secrets.
4. Is this an OAuth client secret, bot token, or signing secret?
   - Check the configured deployment secrets provider.

If a Redis-backed delegated credential record is missing, the delegated path
fails closed. If a user-scoped provider token is missing, the connected account
still may appear as metadata, but `has_token` becomes false and the app must ask
the user to reconnect.
