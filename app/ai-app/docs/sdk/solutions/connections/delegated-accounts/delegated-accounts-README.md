---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-accounts/delegated-accounts-README.md
title: "Delegated Provider Accounts"
summary: "How KDCube stores user-granted external provider claims and resolves them through per-caller, per-account bindings for apps, agents, and automations."
status: active
tags: ["sdk", "connections", "connection-hub", "delegated-connections", "delegated-accounts", "oauth", "gmail", "slack", "linkedin", "icloud"]
updated_at: 2026-08-07
keywords: ["connected provider account", "account_scope", "account_required", "agent_account_binding_required", "provider credential", "outcome_unknown", "Connection Hub deep link"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-connections/delegated-connections-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-edges/connection-edges-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/custom-oauth-oidc-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/integrations/custom-oauth-oidc-service-README.md
---
# Delegated Provider Accounts

Delegated provider accounts are a subtype of
[Delegated Connections](../delegated-connections/delegated-connections-README.md).
They answer this question:

```text
Can an app or automation act on this user's external account?
```

Examples:

```text
platform user 02e...
  -> Gmail OAuth token
  -> Slack OAuth token
  -> iCloud app-specific password
```

In delegated-connection terms:

```text
grantor principal
  platform user 02e...
      |
      v
delegated representative
  KDCube automation/app using provider account
      |
      v
credential plus approved claims
  Gmail OAuth token / Slack OAuth token / iCloud app password
      |
      v
resource surface
  Gmail / Slack / iCloud
```

## Boundary

Delegated provider account connections are provider credentials plus approved
claims. They are not identity proof and they are not platform roles.

```text
Identity link:
  telegram:100200300 -> platform_user_id

Delegated account:
  platform_user_id -> Gmail OAuth token

Delegated external client:
  Claude/KDCube-issued delegated_client token -> KDCube MCP resource
```

An app may use a delegated token only after normal platform/request authority is
established for the current execution.

Delegated accounts are outbound claims: KDCube uses a provider account on
behalf of the user. Managed delegated-client credentials are inbound
delegations: an external client calls a KDCube resource with a KDCube-issued
credential. Both are Connection Hub delegations, but they store and enforce
different token directions.

## Three-Level Model

```text
provider
  OAuth mechanics, no credentials
      |
      v
connector app
  connector_app_id, client_id, allowed_claims ceiling, client_secret in secrets
      |
      v
user account
  account id, access/refresh token, connected through one connector_app_id,
  approved claims for that external account/workspace
```

Multiple connector apps can exist for one provider.

```text
google
  -> connector_app_id=gmail.personal
  -> connector_app_id=gmail.enterprise

slack
  -> connector_app_id=slack.workspace_a
  -> connector_app_id=slack.workspace_b
```

## Current Providers

```text
Gmail
  generic connections framework
  OAuth through shared Connection Hub callback

Slack
  generic connections framework
  OAuth through shared Connection Hub callback

LinkedIn
  Connection Hub connected-account adapter
  OAuth through shared Connection Hub callback

Generic OAuth/OIDC
  generic connections framework
  endpoints and profile mapping configured in Connection Hub

iCloud
  email integration
  app-specific password, no OAuth
```

Gmail should not be documented as an `email_*` app-password integration. Gmail
rides the generic delegated account framework.

For a standard service such as `S1`, prefer `adapter: oauth2.generic` or
`adapter: oidc.generic` before adding provider-specific code. The provider row
supplies the external authorization URL, token URL, optional userinfo URL,
default scopes, authorize parameters, and profile mapping. The connector app
still supplies the OAuth client id and secret reference.

See [Custom OAuth/OIDC Provider Accounts](../../../integrations/custom-oauth-oidc-service-README.md)
for the full configuration, runtime resolver pattern, named-service pattern, and
custom-service verification checklist.

```yaml
connections:
  delegated_to_kdcube:
    providers:
      s1:
        label: S1
        adapter: oidc.generic
        oauth:
          authorize_url: https://s1.example.com/oauth2/authorize
          token_url: https://s1.example.com/oauth2/token
          userinfo_url: https://s1.example.com/oauth2/userInfo
          default_scopes: [openid, email, profile]
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
            client_id: <S1_CLIENT_ID>
            client_secret_ref: connections.delegated_to_kdcube.providers.s1.connector_apps.default.client_secret
            allowed_claims: [s1:read, s1:write]
        claims:
          s1:read:
            label: Read S1
            provider_scopes: [s1.read]
          s1:write:
            label: Write S1
            provider_scopes: [s1.write]
```

## Runtime Use

```text
app/agent has authorized execution context
       |
       v
ConnectionsClient / named-service provider
       |
       v
Connection Hub resolves user's delegated account
       |
       v
provider credential handle returned to app-side integration code
```

The app should not ask delegated provider accounts for platform roles. Roles
come from platform authority projection.

## Credential Health

Provider credentials stay in user secrets. Public account records expose only
metadata and health fields:

```text
credential_status        active | expires_soon | refreshable |
                         reconnect_required | missing | revoked
credential_kind          oauth | static_secret | missing
credential_expires_at
credential_refreshable
reconnect_required
credential_message
credential_status_at     when the persisted status last changed
last_error               most recent provider symptom, verbatim
last_error_at
```

Two health signals combine on account listings:

- **timestamp-derived** — computed from the stored credential's expiry and
  refreshability on every listing;
- **persisted** — written on health transitions (`store.set_account_status`)
  by the broker and the tool-side facade, together with `last_error`.

A persisted `reconnect_required` / `missing` / `revoked` outranks the
timestamp view: a provider may reject a token whose stored expiry still looks
valid, and Connection Hub must show that truth. A later successful refresh
writes the persisted status back to `active`.

OAuth access tokens are short-lived. If a stored credential has a refresh
token, the delegated-account broker refreshes the access token before
returning a credential handle to application code and writes the refreshed
token back to user secrets.

## Resolution Reasons

`broker.ensure_claim()` returns a `ClaimResolution` whose `error` names WHY
the claim did not resolve. The reason flows verbatim through the consent
payload into tool envelopes, named-service errors, and MCP results:

| reason | meaning | user action | retry_hint |
| --- | --- | --- | --- |
| `connect_required` | no eligible connected account | connect the provider | true |
| `claim_upgrade_required` | account exists, claim not approved | approve the claim | true |
| `reconnect_required` | credential missing / unrefreshable / provider rejected it | reconnect the account | true |
| `account_required` | several eligible accounts | pick an `account_id` and resend | true |
| `agent_grant_required` | capable accounts exist, but this delegated caller's default-closed binding does not cover the requested claim | tick the claim for an account on the caller's grant card (Delegated by KDCube) | true |
| `agent_account_binding_required` | a concrete account was named and holds the claim, but this caller is not bound to use that claim on that account | tick the named claim on the named account in the caller's grant card | true |
| `claim_not_configured`, `connector_app_not_configured`, `claim_outside_connector_app` | operator configuration errors | admin action | false |

`retry_hint` says whether retrying the same operation after the user
completes the Connection Hub action should succeed. It is not an instruction
for the runtime to open the URL or replay the operation automatically.

## Multiple Accounts

A user can connect several accounts of one provider (two Slack workspaces,
two Gmail addresses). Resolution never picks one silently: with no
`account_id` and several eligible accounts it returns `account_required`
with **labeled candidates**:

```text
candidates: [{account_id, label, email, workspace, status, claims}]
```

Chat, named services, and MCP clients render this as a real choice list and
resend with the chosen `account_id`. Every provider tool accepts an optional
`account_id` parameter, and successful results carry the `account_id` they
used so multi-account output stays attributable. Requirement preflight receives
the same selector as the provider operation; selecting an account therefore
cannot pass authorization for one account and execute against another.

## Editing A Caller's Account Binding

`account_scope` is the caller's full provider/account/claim binding. Update
operations distinguish omission from an explicit empty map:

```text
account_scope omitted  -> preserve the caller's existing account bindings
account_scope: {}      -> intentionally clear every account binding
```

This distinction prevents an unrelated grant edit from erasing working
provider access while still allowing an explicit revoke-all operation.

## Consent Payload And Hub Deep-Link

Failed resolutions become one consent block
(`connected_account_consent_payload`, envelope code
`needs_connected_account_consent`). The block names ONE provider action:

```text
consent:
  reason           the resolution reason, verbatim
  retry_hint
  provider_id / connector_app_id
  claims           scoped to the named provider only
  account_id       when the failure targets an existing account
  candidates       labeled, for account_required
  url              Connection Hub widget deep-link
  action_label     Connect account / Approve access / Reconnect account /
                   Choose account / Grant this agent access
```

For the connect-family reasons the `url` carries `tab=delegated_to_kdcube`,
`provider_id`, `connector_app_id`, `claims`, and `account_id` query
parameters. The Connection Hub widget turns them into a consent plan: a step
list (account connected → access working → requested approvals as per-claim
chips) with a single primary button for the first unmet step, and highlights
the affected account card.

`agent_grant_required` and `agent_account_binding_required` route differently:
the provider side is already capable, so the `url` deep-links the CALLER'S own
grant card (`tab=delegated_by_kdcube`) with the client or manual access id,
resource, and requested account/claim. `resource` is the protected KDCube
surface through which the caller reaches the provider-backed service; it is
not the provider account or provider object. The card explains why the user
landed there and opens the provider's account section; the user ticks the
claim for an account — nothing is pre-checked. It never points at the
provider-connect tab, whose guided plan would truthfully report "all set" and
strand the user.

The payload only returns the URL. Hosted chat may render it as an action and an
external MCP client may relay it to the user; a background automation can log
or surface it through its own operator channel. Connection Hub does not open
itself, change a grant, or replay the failed provider operation.

Consent is DEMAND-DRIVEN: which tools a turn needs only becomes clear as the
agent works, so claim-gated tools stay in the agent's set and the consent
block raises at the tool ATTEMPT (`resolve_connected_account_claim`) — scoped
to that tool's claims. The attempt returns the envelope to the agent
(`consent_required` plus a short keep-working instruction), emits the chat
consent event once per (provider, claims, tool) demand per conversation, and
records the pending demand; the next turn after the user approves announces
`[CONNECTED ACCOUNTS UPDATE]` for exactly those tools
(`delegated_to_kdcube/consent_demand.py`).

## Live Provider Rejection (Refresh-Retry-Once)

A provider can reject a token mid-call even when stored timestamps look
valid. Tool code follows one contract, packaged by
`kdcube_ai_app.apps.chat.sdk.integrations.connected_accounts`:

```text
run_with_connected_account_retry(source, where=..., run=tool_body)

tool body:
  resolves its own credentials (resolve_connected_account_claim)
  on provider auth rejection returns
    connected_account_auth_failure(credential, provider_error)

runner:
  1. run once
  2. on the failure marker: force-refresh THAT credential
     (refresh_connected_account_claim -> broker force_refresh=True;
     the refreshed token lands in user secrets)
  3. re-run once (the body re-resolves and picks up the fresh token)
  4. second rejection: provider_auth_failed() marks the account
     reconnect_required (+ last_error) and returns the reconnect envelope
```

The marker carries the failing credential, so tools that use several
credentials in one operation (for example Gmail forward = read + send)
refresh exactly the one the provider rejected. The Gmail and Slack tool
suites run entirely under this runner; auth-class provider errors
(HTTP 401/403, Slack `invalid_auth`/`token_revoked`/…) never reach users as
raw API errors.

## Unknown Mutation Outcomes

A provider may return success without the identifier required to prove which
post, comment, or other object was created. The integration returns a managed
incomplete-response error with the real provider status and
`outcome_unknown: true`. KDCube does not automatically replay that mutation;
the caller must inspect or reconcile provider state before deciding whether to
retry. This flag is not a provider-side idempotency key and cannot prevent an
external client from ignoring the warning. The shared contract is
[Provider Error And Observability Contract](../../../integrations/provider-error-contract-README.md).
