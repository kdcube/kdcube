---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/custom-oauth-oidc-service-README.md
title: "Custom OAuth/OIDC Service Integration"
summary: "Recipe for connecting a custom OAuth/OIDC service to KDCube so tools, named services, and agents can request the user's external-service token through Connection Hub."
status: active
tags: ["recipes", "connections", "connection-hub", "integrations", "delegated-to-kdcube", "oauth", "oidc", "custom-service", "connector-apps"]
updated_at: 2026-07-12
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-accounts/custom-oauth-oidc-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-accounts/delegated-accounts-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/google-gmail-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/slack-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/mail-named-service-README.md
---
# Custom OAuth/OIDC Service Integration

Use this recipe when an application tool or named service needs to call a
service `S1` with a token that belongs to the current KDCube user.

```text
User in KDCube
  asks agent to use S1
       |
       v
KDCube tool needs S1 token
       |
       v
Connection Hub asks user to connect S1
       |
       v
S1 OAuth/OIDC consent
       |
       v
Connection Hub stores S1 credential for this KDCube user
       |
       v
Tool resolves token and calls S1
```

## 1. Decide The Provider Claims

Write the KDCube claims that tools will request. Keep them close to product
actions, not raw provider endpoints:

```text
s1:read
s1:write
s1:admin
```

Each KDCube claim maps to provider scopes:

```text
s1:read  -> s1.read
s1:write -> s1.write
```

The connector app is the ceiling: it says which KDCube claims can ever be
requested through this OAuth/OIDC app. Individual tools then request only the
claims they need.

## 2. Create The OAuth/OIDC App In S1

In the external service:

1. Create a web/server OAuth client or OIDC app.
2. Enable authorization code flow.
3. Enable refresh tokens if the service should work for long-running
   automation.
4. Enable the provider scopes needed by the claims.
5. Register the KDCube callback URL:

```text
https://<PUBLIC_HOST>/api/integrations/bundles/<TENANT>/<PROJECT>/connection-hub@1-0/public/delegated_to_kdcube_oauth_callback
```

6. Copy the client id and client secret.

If S1 is backed by Cognito, use the Cognito hosted UI/OIDC app client:

```text
authorize_url = https://<COGNITO_DOMAIN>/oauth2/authorize
token_url     = https://<COGNITO_DOMAIN>/oauth2/token
userinfo_url  = https://<COGNITO_DOMAIN>/oauth2/userInfo
```

## 3. Configure Connection Hub

Add the provider under the Connection Hub bundle config.

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
                  profile:
                    subject: sub
                    email: email
                    display_name: name
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

Use `adapter: oauth2.generic` for OAuth-only services. Use
`adapter: oidc.generic` when the service has OIDC identity fields such as `sub`
and a userinfo endpoint.

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

Generate the state secret once per environment:

```bash
openssl rand -hex 32
```

Do not commit client secrets, state secrets, access tokens, refresh tokens, or
provider app passwords.

## 4. Declare Tool Claims

The application tool declares its connected-account dependency in the tool
config. The tool does not own the connector app secret.

```yaml
surfaces:
  as_consumer:
    agents:
      main:
        tools:
          - name: s1
            kind: python
            alias: s1
            module: my_app.s1_tools
            allowed:
              - read_s1_object
              - write_s1_object
            tool_claims:
              read_s1_object:
                connections:
                  delegated_to_kdcube:
                    connected_accounts:
                      - provider_id: s1
                        connector_app_id: default
                        claims:
                          - s1:read
              write_s1_object:
                connections:
                  delegated_to_kdcube:
                    connected_accounts:
                      - provider_id: s1
                        connector_app_id: default
                        claims:
                          - s1:write
```

When the user asks the agent to use the tool, the SDK can preflight or enforce
the claims. If the user has not connected S1, the tool returns a managed
Connection Hub action instead of a raw provider error.

## 5. Resolve The Credential In Tool Code

Tool code resolves the user's connected account through the SDK:

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

The retry wrapper handles the common OAuth case:

```text
first provider call rejected token
  -> force-refresh the credential
  -> retry once
  -> if still rejected, mark account reconnect_required
  -> return a Connection Hub reconnect link
```

## 6. Optional: Expose S1 As A Named Service

If external agents should use S1 through the generic named-services MCP surface,
wrap the same provider logic in a named-service namespace:

```text
namespace: s1
operations:
  object.search
  object.get
  object.action
```

The named-service provider still resolves the connected S1 account before each
provider call. The external client's MCP consent controls access to the KDCube
namespace; the user's S1 connected account controls access to S1.

```text
Claude / external client
  -> KDCube delegated credential for named_services:use and S1 grants
  -> named-service operation
  -> connected-account resolver for s1:read or s1:write
  -> S1 API call with provider token
```

## 7. Test The Flow

After descriptor changes, refresh the runtime and open the Connection Hub
Connections widget.

Expected test path:

```text
1. S1 appears under Delegated to KDCube.
2. User clicks Connect.
3. S1 OAuth page opens.
4. User approves.
5. Callback returns to Connection Hub.
6. S1 account appears with approved claims.
7. Agent tool can use S1.
```

Test prompts for a connected agent:

```text
Use S1 to list the first 5 objects I can read.
```

```text
Use S1 to read object <object-id> and summarize it.
```

```text
Use S1 to write a test note to object <object-id>.
```

Expected failure behavior:

| Situation | Expected result |
| --- | --- |
| User has no S1 account | Tool returns connect-required with Connection Hub URL. |
| Account lacks `s1:write` | Tool returns claim-upgrade-required with Connection Hub URL. |
| Token is expired but refreshable | SDK refreshes before returning the credential. |
| Token is rejected and cannot refresh | Tool returns reconnect-required with Connection Hub URL. |
| User has several S1 accounts | Tool returns account-required candidates or accepts `account_id`. |

## 8. Operator Checklist

- `connections.delegated_to_kdcube.enabled` is true.
- `connections.delegated_to_kdcube.oauth.public_base_url` matches the public
  runtime host.
- `oauth_state_secret` exists in secrets.
- Provider `adapter` is `oauth2.generic` or `oidc.generic`.
- Provider OAuth URLs are reachable from the runtime.
- Provider profile mapping includes a stable `subject`.
- Connector app `client_secret_ref` points to a real secret.
- Connector app `allowed_claims` includes every claim that tools may request.
- Provider-side OAuth app has the exact KDCube callback URL registered.
- Tools declare `tool_claims`.
- Tool code uses the SDK resolver and retry wrapper.
