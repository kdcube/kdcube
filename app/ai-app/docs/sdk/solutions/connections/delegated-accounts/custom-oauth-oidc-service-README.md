---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-accounts/custom-oauth-oidc-service-README.md
title: "Custom OAuth/OIDC Provider Accounts"
summary: "How to connect a custom external service to KDCube as a delegated-to-KDCube provider account, so tools and named services can resolve the user's provider token at runtime."
status: active
tags: ["sdk", "connections", "connection-hub", "delegated-accounts", "delegated-to-kdcube", "oauth", "oidc", "custom-service", "connector-apps"]
updated_at: 2026-07-12
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-accounts/delegated-accounts-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/custom-oauth-oidc-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/google-gmail-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/slack-README.md
---
# Custom OAuth/OIDC Provider Accounts

Use this pattern when a KDCube application, tool, or named service needs to call
an external service on behalf of the current KDCube user.

Example:

```text
KDCube user asks an agent to process S1 data
       |
       v
agent calls a KDCube tool
       |
       v
tool needs an S1 access token for this KDCube user
       |
       v
Connection Hub resolves the user's connected S1 account
       |
       v
tool calls S1 with the user's S1 token
```

This is the **delegated to KDCube** direction:

```text
external provider account
  S1 user / Gmail account / Slack workspace
       |
       | user consented provider token
       v
KDCube stores and uses the provider credential for that platform user
```

It is not platform login, and it is not delegated automation access.

```text
Platform login:
  external identity proves who the KDCube user is

Delegated automation access:
  KDCube issues a bearer token so automation can enter KDCube

Delegated provider account:
  external provider issues a token so KDCube can enter that provider
  for the current KDCube user
```

## Who Implements What

For a normal OAuth 2.0 or OIDC provider, the external service owner provides
the identity/API surface and OAuth app registration:

```text
S1 service owner
  authorization endpoint
  token endpoint
  optional userinfo endpoint
  scopes
  client id/client secret
  redirect URI registration
  provider API
```

The KDCube operator configures a Connection Hub provider and connector app:

```text
KDCube operator
  provider_id
  adapter type: oauth2.generic or oidc.generic
  endpoint URLs
  profile mapping
  connector_app_id
  client_id
  client_secret_ref
  claims and provider_scopes
```

The KDCube application/tool developer declares the claims that a tool needs and
uses the SDK resolver:

```text
KDCube developer
  tool_claims for S1 operations
  resolve_connected_account_claim(...)
  provider API client call with the resolved access token
```

Use the generic adapter first. Add provider-specific SDK code only when the
provider does not fit normal OAuth/OIDC mechanics, returns a non-standard token
shape, needs a custom refresh flow, or needs provider-specific account metadata
that cannot be mapped from token/userinfo JSON.

## Provider Requirements

The generic adapter expects the external service to provide:

| Requirement | Notes |
| --- | --- |
| Authorization URL | Browser endpoint where the user consents. |
| Token URL | Server-side endpoint where Connection Hub exchanges the OAuth code. |
| Client id and secret | The client id lives in config; the client secret lives in bundle secrets. |
| Scopes | Provider-side scopes that the service understands. |
| Stable subject | A stable account id, usually `sub`, `id`, or user id from userinfo/token claims. |
| Optional userinfo URL | Strongly recommended for OIDC and for display labels/email/workspace fields. |
| Refresh token support | Recommended for long-lived automation; otherwise the user may need to reconnect often. |

Register this redirect URI in the external service's OAuth app:

```text
https://<PUBLIC_HOST>/api/integrations/bundles/<TENANT>/<PROJECT>/connection-hub@1-0/public/delegated_to_kdcube_oauth_callback
```

The URL must match exactly. The host, tenant, project, bundle id, route, and
callback path are all part of the registered OAuth redirect URI.

## Configuration Shape

`bundles.yaml`:

```yaml
bundles:
  version: "1"
  items:
    - id: connection-hub@1-0
      config:
        connections:
          delegated_to_kdcube:
            enabled: true
            oauth:
              public_base_url: "https://<PUBLIC_HOST>"
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
                    client_id: "<S1_CLIENT_ID>"
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

`bundles.secrets.yaml`:

```yaml
bundles:
  version: "1"
  items:
    - id: connection-hub@1-0
      secrets:
        connections:
          delegated_to_kdcube:
            oauth_state_secret: "<RANDOM_HEX_32_BYTES>"
            providers:
              s1:
                connector_apps:
                  default:
                    client_secret: "<S1_CLIENT_SECRET>"
```

`oauth_state_secret` signs the OAuth state for delegated-to-KDCube provider
connect flows. Generate it once per environment and keep it in secrets:

```bash
openssl rand -hex 32
```

## Adapter Choice

Use `oidc.generic` when the provider has OIDC-style identity material:

```text
openid scope
id_token and/or userinfo endpoint
stable sub claim
```

Use `oauth2.generic` when the provider is OAuth-only:

```text
access token
optional refresh token
provider API or token response carries account metadata
```

Both adapter types use the same Connection Hub registry shape. The difference
is how strict the adapter is about identity/profile fields and which response
fields are expected.

## Profile Mapping

The generic adapter must know how to display and distinguish connected accounts.
Map provider JSON fields into KDCube account metadata:

```yaml
profile:
  subject: sub
  email: email
  display_name: name
  workspace: custom.tenant
```

The `subject` field is required. Other fields are optional, but the user
experience is better when the widget can show a recognizable account label.

Nested fields use dot paths:

```yaml
profile:
  subject: user.id
  email: user.email
  display_name: user.full_name
  workspace: tenant.name
```

Multiple connected accounts are allowed. A user can connect several S1 accounts
through the same connector app, and each account stores its own subject,
display label, claims, credential reference, and health state.

## Claim Model

KDCube claims are not provider scopes. A claim is a KDCube consent unit that
maps to one or more provider scopes.

```text
KDCube claim:
  s1:read

Provider scopes:
  s1.read
  offline_access
```

The connector app declares the maximum claims it may request:

```yaml
connector_apps:
  default:
    allowed_claims:
      - s1:read
      - s1:write
```

The tool declares what it needs:

```yaml
tool_claims:
  read_s1_object:
    connections:
      delegated_to_kdcube:
        connected_accounts:
          - provider_id: s1
            connector_app_id: default
            claims:
              - s1:read
```

At runtime, Connection Hub verifies:

```text
tool asked for s1:read
  |
  +-- provider exists
  +-- connector app exists
  +-- connector app allows s1:read
  +-- current user has a connected S1 account
  +-- account approved s1:read
  +-- credential is usable or refreshable
```

There is no intermediate capability registry between the tool and provider
claims. The connector app defines the allowed provider claim universe; each
tool says which claims it needs.

## Runtime Tool Pattern

Tools should resolve connected accounts through the SDK. They should not read
Connection Hub storage or provider secrets directly.

```python
from kdcube_ai_app.apps.chat.sdk.integrations.connected_accounts import (
    connected_account_auth_failure,
    resolve_connected_account_claim,
    run_with_connected_account_retry,
)


async def read_s1_object(source, object_id: str, account_id: str | None = None):
    async def _run():
        credential = await resolve_connected_account_claim(
            source,
            provider_id="s1",
            connector_app_id="default",
            claim="s1:read",
            tool_name="s1.read_s1_object",
            account_id=account_id,
        )
        if not credential.ok:
            return credential.error_envelope(where="s1.read_s1_object")

        response = await call_s1_api(
            access_token=credential.access_token,
            object_id=object_id,
        )
        if response.status_code in (401, 403):
            return connected_account_auth_failure(
                credential,
                "S1 rejected the access token",
            )
        return response.json()

    return await run_with_connected_account_retry(
        source,
        where="s1.read_s1_object",
        run=_run,
    )
```

The retry wrapper refreshes a rejected OAuth credential once, retries the tool
body once, and marks the account `reconnect_required` if the provider still
rejects the credential.

## Named Service Pattern

A custom service can also be exposed as a named-service namespace:

```text
integrations/s1/named_service.py
  namespace: s1
  operations:
    object.search
    object.get
    object.action
```

The named-service operation uses the same resolver before calling S1:

```text
named-service call
  |
  v
resolve_connected_account_claim(provider=s1, claim=s1:read)
  |
  +-- success -> call S1 API
  |
  +-- missing/expired/unapproved -> return managed consent envelope
```

When this namespace is exposed through
`kdcube-services@1-0/public/mcp/named_services`, there are two authorization
layers:

```text
External client -> KDCube:
  delegated MCP token grants access to the KDCube named-service namespace

KDCube -> S1:
  current platform user must have connected S1 with the requested provider claim
```

## User Experience

The normal user path is demand-driven:

```text
1. User asks an agent to use S1.
2. Tool attempts to resolve the required connected account claim.
3. If missing, the SDK returns a managed connected-account consent envelope.
4. Chat/named-service/MCP client shows a Connection Hub URL.
5. User opens the URL and signs into S1.
6. S1 redirects to Connection Hub.
7. Connection Hub stores account metadata in user properties and credentials in
   user secrets.
8. User retries the operation.
9. Tool resolves the credential and calls S1.
```

The Connection Hub deep link carries:

```text
tab=delegated_to_kdcube
provider_id=s1
connector_app_id=default
claims=s1:read
account_id=<optional existing account>
```

The widget turns that into a concrete action: connect account, approve a claim,
choose an account, or reconnect an unhealthy credential.

## Storage Boundary

Provider account metadata is stored in user properties. Provider credentials
are stored in user secrets.

```text
user properties:
  provider_id
  connector_app_id
  account_id
  display label/email/workspace
  approved claims
  credential health

user secrets:
  access token
  refresh token
  expiry
  token type
  provider-specific private credential fields
```

Application code sees a credential handle from the SDK resolver. It should not
read user secrets directly.

## Using Cognito As S1

If S1 uses Cognito as its OAuth/OIDC authority, configure Cognito as the
external provider behind `oidc.generic`.

In Cognito:

1. Create or choose an app client for the S1 connector.
2. Enable authorization code grant.
3. Enable the scopes S1 requires, such as `openid`, `email`, `profile`, and any
   custom resource-server scopes like `s1.read`.
4. Register the delegated-to-KDCube callback URL.
5. Copy the client id and client secret.

In Connection Hub:

```yaml
providers:
  s1:
    label: S1
    adapter: oidc.generic
    oauth:
      authorize_url: https://<COGNITO_DOMAIN>/oauth2/authorize
      token_url: https://<COGNITO_DOMAIN>/oauth2/token
      userinfo_url: https://<COGNITO_DOMAIN>/oauth2/userInfo
      default_scopes: [openid, email, profile]
      profile:
        subject: sub
        email: email
        display_name: username
```

If S1 needs API scopes, define KDCube claims that map to Cognito resource-server
scopes:

```yaml
claims:
  s1:read:
    provider_scopes:
      - https://s1.example.com/read
```

Cognito as S1 is still a provider-account flow. It does not make Cognito the
KDCube platform authority unless the deployment also configures Cognito under
the platform authority registry.

## Verification Checklist

After configuring S1:

1. Refresh the runtime so Connection Hub reads the descriptor changes.
2. Open the Connections widget.
3. Confirm S1 appears under **Delegated to KDCube**.
4. Start the S1 connection.
5. Confirm the provider OAuth page shows the expected S1 app and scopes.
6. Confirm the callback returns to Connection Hub without redirect errors.
7. Confirm the connected account appears with the expected label and claims.
8. Run a tool that requires `s1:read`.
9. Revoke or expire the provider token and verify the tool returns a reconnect
   action instead of a raw provider error.

## Failure Modes

| Symptom | Cause | Fix |
| --- | --- | --- |
| Provider says `redirect_uri_mismatch` | Callback URL not registered exactly. | Register the exact delegated-to-KDCube callback URL. |
| Connection Hub says claim is outside connector app | Tool requested a claim not listed in `allowed_claims`. | Add the claim to the connector app or change the tool claim. |
| Tool says connect required | User has no connected S1 account. | Open the Connection Hub URL and connect S1. |
| Tool says claim upgrade required | Account exists but was not approved for that claim. | Approve the missing claim in Connection Hub. |
| Tool says reconnect required | Credential is missing, revoked, expired without refresh, or rejected by S1. | Reconnect S1. |
| Several accounts are candidates | User connected multiple S1 accounts. | Select/pass an `account_id`. |
