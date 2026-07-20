---
id: connection-hub@1-0/integrations/README
title: "Connection Hub — Integrations Setup (overview)"
summary: "Common setup shared by every connection-hub provider — the single OAuth callback URL, the hub-level state secret, and the apply/refresh step — plus links to the per-provider setup articles (Google, Slack, iCloud, and generic OAuth/OIDC)."
status: "active"
tags: ["integration", "connections", "oauth", "admin", "operator-setup", "prerequisites", "mcp", "named-services", "delegated-credentials"]
keywords: ["connection hub setup", "delegated_to_kdcube_oauth_callback", "oauth_state_secret", "connector app", "provider setup"]
see_also:
  - ./google.md
  - ./slack.md
  - ./icloud.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-accounts/custom-oauth-oidc-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/custom-oauth-oidc-service-README.md
  - ../../interface/README.md
  - ../../config/bundles.template.yaml
  - ../../config/bundles.secrets.template.yaml
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/connections-README.md
---

# Connection Hub — Integrations Setup (overview)

The connection-hub **owns** external connections. Before a user can connect an
account, an operator/admin registers the **connector app** for each provider.
For OAuth providers, that connector app points to an external OAuth application
such as a Google OAuth client or Slack app. This is the work that happens
**outside** KDCube; the SDK cannot create Google/Slack apps for you. Each user
still connects their own account afterwards through the **Connections** widget.

Per-provider steps:

| Provider | Mechanism | Article |
| --- | --- | --- |
| Google (Gmail) | OAuth connector app | [google.md](./google.md) |
| Slack | OAuth connector app | [slack.md](./slack.md) |
| iCloud | App-specific password (no OAuth) | [icloud.md](./icloud.md) |
| Standard OAuth/OIDC service | `oauth2.generic` or `oidc.generic` connector app | See `repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-accounts/custom-oauth-oidc-service-README.md` and `repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/custom-oauth-oidc-service-README.md`. |

Set these once for the commands in the per-provider articles:

```bash
export TENANT="demo-tenant"
export PROJECT="demo-project"
export BUNDLE_ID="connection-hub@1-0"
export PUBLIC_HOST="https://YOUR_PUBLIC_HTTPS_HOST"     # e.g. your ngrok host
```

## The one delegated to KDCube OAuth callback URL

The delegated to KDCube hub uses a **single** redirect URI for every OAuth
provider/connector app:

```bash
echo "$PUBLIC_HOST/api/integrations/bundles/$TENANT/$PROJECT/$BUNDLE_ID/public/delegated_to_kdcube_oauth_callback"
```

Register this exact URL as an authorized redirect URI on **each** OAuth provider
(Google, Slack). iCloud is not OAuth and needs no redirect URI.

## Hub-level state secret

One secret signs the OAuth `state` for **all** delegated to KDCube OAuth providers:

```bash
printf '%s\n' "$(openssl rand -hex 32)"   # → connections.delegated_to_kdcube.oauth_state_secret
```

```yaml
# bundles.secrets.yaml
secrets:
  connections:
    delegated_to_kdcube:
      oauth_state_secret: <RANDOM_HEX>
```

## Client apps are admin data

Each provider can have **multiple connector apps**
(`connections.delegated_to_kdcube.providers.<provider>.connector_apps`), each
with its own connector-app id + `client_id` (config) and `client_secret`
(secret) when OAuth is used. The Connections widget lets the user pick which
connector app to connect through. The per-provider articles show the exact keys.

This section configures connector apps only. Application tools that need Gmail,
Slack, iCloud, or another provider declare their required provider claims in the
application bundle/tool config, under that tool's
`connections.delegated_to_kdcube.connected_accounts` block.

## Standard OAuth/OIDC provider

If the provider follows ordinary OAuth 2.0 or OIDC mechanics, register it with
the generic adapter instead of writing provider-specific SDK code. The
canonical SDK guide is
repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-accounts/custom-oauth-oidc-service-README.md,
and the task recipe is
repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/custom-oauth-oidc-service-README.md.
This is the shape for an `S1` service whose authority may be Cognito, Auth0,
Okta, or any service-owned OAuth/OIDC server:

```yaml
connections:
  delegated_to_kdcube:
    providers:
      s1:
        label: S1
        adapter: oidc.generic
        enabled: true
        oauth:
          authorize_url: https://s1.example.com/oauth2/authorize
          token_url: https://s1.example.com/oauth2/token
          userinfo_url: https://s1.example.com/oauth2/userInfo
          default_scopes:
            - openid
            - email
            - profile
          authorize_params:
            audience: s1-api
          profile:
            subject: sub
            email: email
            display_name: name
            workspace: custom.tenant
        connector_apps:
          default:
            label: S1 connector
            enabled: true
            client_id: <S1_CLIENT_ID>
            client_secret_ref: connections.delegated_to_kdcube.providers.s1.connector_apps.default.client_secret
            allowed_claims:
              - s1:read
              - s1:write
        claims:
          s1:read:
            label: Read S1
            description: Read S1 data for the approving user.
            provider_scopes:
              - s1.read
          s1:write:
            label: Write S1
            description: Write S1 data for the approving user.
            provider_scopes:
              - s1.write
```

The provider's OAuth app must register the shared callback URL shown above.
The secret value goes in `bundles.secrets.yaml` at the configured
`client_secret_ref`.

The reference SDK includes first provider-backed tool modules:

| Module | Tools | Required claims |
| --- | --- | --- |
| `kdcube_ai_app.apps.chat.sdk.integrations.google.gmail_tools` | `search_gmail`, `read_gmail_message`, `download_gmail_attachments`, `send_gmail`, `forward_gmail_message` | `gmail:read`, `gmail:send` |
| `kdcube_ai_app.apps.chat.sdk.integrations.slack.tools` | `search_slack`, `list_slack_channels`, `read_slack_channel_history`, `download_slack_file`, `upload_slack_file`, `slack_assistant_search_info`, `slack_assistant_search`, `post_slack_message` | `slack:search`, `slack:channels`, `slack:history`, `slack:files:read`, `slack:files:write`, `slack:assistant:search`, `slack:post` |

The same provider claims can also back named-service providers. The reference
SDK includes:

- `kdcube_ai_app.apps.chat.sdk.integrations.mail.named_service`, which exposes
  one provider-neutral `mail` namespace over connected mail accounts.
- `kdcube_ai_app.apps.chat.sdk.integrations.slack.named_service`, which exposes
  one `slack` namespace over connected Slack workspaces.

External agents reach these namespaces through
`kdcube-services@1-0/public/mcp/named_services`. Door admission is
`named_services:use`; the per-operation claim is the **real provider claim**,
consented per connected account. Slack is a single provider, so those claims
are its own — `slack:search`, `slack:channels`, `slack:history`,
`slack:files:read`, `slack:files:write`, `slack:post`, `slack:assistant:search`
— the SAME tokens at the delegated layer and on the account. Mail spans
providers (Gmail OAuth and IMAP/password), so it keeps a provider-agnostic
`mail:read`/`mail:send` at the delegated layer, resolving to the real
`gmail:read`/`gmail:send` per account.

When a user creates manual automation access, the same named-service provider
metadata appears under **Delegated by KDCube**:

```text
configured resource catalog
  -> selectable namespace operations and KDCube grants

provider discovery metadata
  -> connected-account and claim prerequisites
```

The selected namespace operations are sent as
`named_service_operations[resource][namespace][]` and narrow the policy stored
with the KDCube automation grant. Provider prerequisites remain in **Delegated
to KDCube**. They may be completed from the existing deep link, but the
provider token is never placed in the automation bearer or grant record.

Example application tool declaration:

```yaml
surfaces:
  as_consumer:
    agents:
      main:
        tools:
          - kind: python
            alias: report
            module: my_app.report_tools
            allowed:
              - post_to_slack
            tool_claims:
              post_to_slack:
                connections:
                  delegated_to_kdcube:
                    connected_accounts:
                      - provider_id: slack
                        connector_app_id: demo
                        claims:
                          - slack:post
```

There is no intermediate capability registry for this. The connector app says
which provider claims may be requested through that app. The tool says which
claims it needs. The SDK preflight checks the current user before the agent uses
the configured tool set and returns a Connection Hub URL when the account or
claim is missing.

## Apply

After editing `bundles.yaml` / `bundles.secrets.yaml`, **refresh the runtime** so
the bundle reloads and the Connections widget builds. Then open:

```text
$PUBLIC_HOST/api/integrations/bundles/$TENANT/$PROJECT/$BUNDLE_ID/widgets/connections_settings
```

Connected tokens are **user-scoped**, so any other bundle acting for that user
(e.g. `user-automation@1-0` for email delivery) can use them without re-connecting.

## Credential health

Connection Hub stores connected-account metadata as user properties and provider
credentials as user secrets. OAuth access tokens are expected to expire. For
OAuth providers that return a refresh token, the SDK broker refreshes the access
token before returning the credential to application code.

The Connections widget should not treat every stored account as equally healthy.
It receives these non-secret fields from the catalog:

| Field | Meaning |
| --- | --- |
| `credential_status` | `active`, `expires_soon`, `refreshable`, `reconnect_required`, or `missing`. |
| `credential_expires_at` | Provider access-token expiry timestamp when known. |
| `credential_refreshable` | Whether a refresh token is stored for this OAuth credential. |
| `reconnect_required` | True when the user must reconnect the provider account. |
| `credential_message` | Human-readable status for the UI. |

If an application gets a provider error such as invalid OAuth credentials, first
check the widget:

- **Refreshes automatically** means retrying the operation should let the backend
  refresh on use.
- **Reconnect required** means the user should start the provider connect flow
  again for the required claims.

Do not commit client secrets, the OAuth state secret, user tokens, or iCloud
app-specific passwords to source control.
