---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authenticated-mcp/authenticated-mcp-README.md
title: "Authenticated MCP: The Full Configuration Chain"
summary: "The single authoritative reference for configuring authenticated MCP in KDCube: every configuration layer in order (managed door, delegated resource ceiling, delegable capabilities, OAuth client registration, namespace boundary grants), the provider connected-accounts self-description contract, the two consent gates, and the three scenarios this chain enables."
status: active
tags: ["sdk", "connections", "connection-hub", "mcp", "managed-auth", "delegated-credentials", "delegated-accounts", "named-services", "consent", "connected-accounts", "automation", "agents"]
updated_at: 2026-09-02
keywords: ["mode: managed", "authority_id", "delegated_client", "resources", "grants", "capabilities", "resource_operations", "invocation policy", "allow once", "allow always", "delegable_roles", "delegable_permissions", "Client ID Metadata Document", "dynamic_client_registration", "allowed_redirect_uris", "named_services.namespaces", "connected_accounts", "claims_by_operation", "claim_labels", "delegated_consent_required", "delegated_capability_not_granted", "needs_connected_account_consent", "connect_required", "agent_grant_required", "retry_hint", "candidates", "kdcube-agent", "automation access", "TTL", "enforce_tool_requirements", "plain mcp tools", "productivity"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/arch/delegated-authority-and-admission-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/configuring-agent-service-access/configuring-agent-service-access-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/protect-bundle-mcp-with-managed-credentials-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/apps/named-services-mcp-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-accounts/delegated-accounts-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/connections/delegate-kdcube-service-to-external-client-README.md
---
# Authenticated MCP: The Full Configuration Chain

This page is the whole authenticated-MCP chain in one place, in configuration
order: a builder starting from zero reads top to bottom and ends with a
protected MCP surface, delegable capabilities, per-operation boundary policy, a
self-describing provider, and working consent at call time.

The same chain enables three scenarios:

1. **A hosted agent** - an agent inside a KDCube app, with the client identity
   `kdcube-agent:<app>:<agent>`, acting on the user's connected accounts with
   demand-driven consent raised in chat.
2. **An external MCP connection** - Claude Code or another MCP-speaking app
   connects to a KDCube MCP URL through OAuth, identifies itself through a
   pre-registration, Client ID Metadata Document, or retained DCR path, and
   works under a KDCube-issued delegated credential.
3. **User-created automation access** - a bounded token minted in Connection
   Hub for scripts and integrations, narrowed to selected resources, grants,
   and named-service operations, with a TTL.

Each configuration layer below names its owner and shows the real shape from
the shipped reference descriptor
([`deployment/bundles.yaml`](../../../../../deployment/bundles.yaml)).
Neighbouring docs own their concepts in depth; this page links to them and
never restates their material.

## Layer 1 - the door: a managed MCP surface

The app that exposes the MCP endpoint declares the surface under
`surfaces.as_provider.mcp` with `mode: managed`. Managed means the platform's
delegated-credential guard authenticates the bearer before the app's MCP code
runs; the app never parses OAuth tokens itself:

```yaml
surfaces:
  as_provider:
    mcp:
      named_services:
        auth:
          mode: managed
          authority_id: delegated_client
          selected_tool_grants: true
```

`authority_id: delegated_client` names the managed authority accepted at this
boundary; `selected_tool_grants: true` requires the concrete MCP tool to be
present in the caller's grant record. The surface declares only how the
endpoint is protected - the tool/grant catalog lives in Connection Hub config
(next layers). Handler mechanics (the `@mcp` entrypoint, stateless MCP server,
`mode: bundle` for app-owned auth) are in
[Protect Bundle MCP With Managed Credentials](../../../../recipes/connections/protect-bundle-mcp-with-managed-credentials-README.md).

## Layer 2 - the delegated resource: the tool ceiling

The Connection Hub app (`connection-hub@1-0`) declares each protected resource
under `connections.delegated_credentials.oauth.resources[]`: the resource URL
pattern, a label, and per-tool grant requirements. This is the CEILING - no
issuance path (OAuth consent, hosted-agent grant, automation token) can exceed
it:

```yaml
connections:
  delegated_credentials:
    oauth:
      resources:
        - resource: '*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/named_services*'
          label: KDCube named services MCP
          tools:
            named_services_list:
              label: List named services
              description: List configured named-service namespaces and operation grants.
              grants:
              - named_services:use
            named_services_search:
              label: Named service search
              description: Search objects in a configured namespace.
              grants:
              - named_services:use
            named_services_action:
              label: Named service action
              description: Run a bounded provider action on one object.
              grants:
              - named_services:use
```

The `resource` pattern is also the grant key: a consuming connection's
`resource` field must byte-match it, because grant creation, validation, and
per-call lookup all key under it. A tool needing several grants lists the full
set on that one tool row.

## Layer 3 - delegable capabilities: who may delegate a grant

Each grant used anywhere in the catalog gets a capability row: the label and
description the consent screens show, plus **who may delegate it** - by role
AND by permission. Both axes exist; a user qualifies through either:

```yaml
connections:
  delegated_credentials:
    oauth:
      capabilities:
        - grant: mail:read
          label: Read connected mail
          description: Search and read messages and attachments from mail accounts connected to KDCube.
          delegable_roles:
          - kdcube:role:registered
          - kdcube:role:paid
          - kdcube:role:privileged
          - kdcube:role:super-admin
          delegable_permissions:
          - mail:read
        - grant: slack:post
          label: Post to Slack
          description: Post messages through connected Slack accounts.
          delegable_roles: [kdcube:role:registered, kdcube:role:paid, kdcube:role:privileged, kdcube:role:super-admin]
          delegable_permissions: [slack:post]
```

Consent screens show only capabilities the signed-in user may delegate; a
grant whose `delegable_roles`/`delegable_permissions` match nothing the user
holds is filtered out (and a direct automation request for it fails with
`delegated_access_grants_not_delegable` - see the troubleshooting table).

## Layer 4 - the OAuth client identity and callback fence

External MCP-speaking apps identify themselves through one of three paths:
descriptor `public_clients`, an HTTPS Client ID Metadata Document (CIMD), or
retained Dynamic Client Registration (DCR). Registration identifies the client
and its callbacks; it grants no resource authority.

DCR runs **before any user authenticates**, so its callback allowlist is a
mandatory deployment fence:

```yaml
connections:
  delegated_credentials:
    oauth:
      dynamic_client_registration:
        enabled: true
        allowed_redirect_uris:
        - https://claude.ai/api/mcp/auth_callback
        - http://localhost/callback
        - http://127.0.0.1/callback
```

Loopback entries match any port (RFC 8252); scheme, host, and path must match
exactly. CIMD instead uses the HTTPS metadata URL as the client id and requires
exact callback matches. Its resolver rejects private addresses, redirects,
oversized or malformed documents, and never caches failed fetches. The complete
registration precedence and CIMD controls are owned by
[OAuth Delegated Credential Protocol Adapter](../delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md).
The connect journey is
[Delegate A KDCube Service To An External Client](../../../../recipes/connections/delegate-kdcube-service-to-external-client-README.md).

All three registration paths enter the same authorization-code, consent,
refresh, revocation, resource, and per-operation grant machinery. Single-use
OAuth state changes are atomic across workers, and a pointer-backed credential
resolves its current Connection Hub card on every managed call and refresh.
For a resource marked `resource_selection: true`, such as the external MCP
proxy, anonymous discovery remains deployment-scoped. After platform login,
consent adds only the grantor's resource overlay and lets the user select exact
operations under those resources. The authorization code, token binding,
refresh record, and live card preserve that resource-qualified selection. The
full decision contract is owned by
[Delegated Authority And Admission](../../../../arch/delegated-authority-and-admission-README.md).
The protocol support boundary and conformance gates are owned by
[OAuth Delegated Credential Protocol Adapter](https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/package/oauth-delegated-credential-protocol.md#mcp-2026-07-28-support-boundary).

### Discovery through the front proxy

A guarded surface answers an unauthenticated request with `401` and
`WWW-Authenticate: Bearer resource_metadata="<issuer>/.well-known/oauth-protected-resource?resource=<surface url>"`,
where the issuer is the Connection Hub bundle's `public/oauth` path. That is
the discovery path the MCP specification names, and a client that follows it
never needs anything at the origin root.

Clients also probe the origin-root locations RFC 9728 and RFC 8414 define:
`/.well-known/oauth-protected-resource[/<surface path>]` and
`/.well-known/oauth-authorization-server[/<issuer path>]`. Claude Code checks
those first. The shipped proxy templates therefore carry a generated block
(`deployment/nginx/generate_application_site_routes.py`) that answers the
pathless forms with an exact JSON 404, rewrites the path-inserted forms onto
the bundle OAuth surface, and 404s every other `/.well-known/` path, so
nothing under `/.well-known/` reaches the application-site fallback. Without
that block the fallback answers `200` with the SPA's HTML, the client fails to
parse it as JSON, and discovery stops before the challenge is ever seen. A
deployment whose front proxy predates the block shows exactly that symptom;
regenerate the proxy from the current template.

## Layer 5 - namespace boundary policy: door claims per operation

The generic named-services bridge exposes namespace-agnostic tools
(`named_services_search`, `named_services_action`, ...), so the per-tool
grants of layer 2 only admit the caller to the bridge. Which grants each
NAMESPACE OPERATION consumes is the nested boundary tree
`resources[].named_services.namespaces.<ns>.tools.<tool>.grants`, checked by
the bridge on every call:

```yaml
resources:
  - resource: '*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/named_services*'
    named_services:
      namespaces:
        slack:
          label: Slack
          authority_id: delegated_client
          tools:
            search:
              operation: object.search
              label: Search Slack
              description: Search Slack messages/files through connected Slack accounts.
              grants:
              - named_services:use
              - slack:search
            get:
              operation: object.get
              label: Read Slack object
              description: Read Slack account/channel/file objects by ref.
              grants:
              - named_services:use
              - slack:history
            action:
              operation: object.action
              label: Slack action
              description: Post messages, upload files, download files, or inspect Slack assistant search availability.
              operations:
                object.action:
                  grants:
                  - named_services:use
                  - slack:post
                  - slack:files:write
                  - slack:files:read
                  - slack:assistant:search
```

**Every namespace operation lists `named_services:use`.** Door admission is
its own consent, distinct from the realm claim, and the gate checks the
tool's grants as one set - so every operation exposed through named services
lists `named_services:use` alongside its realm claim(s), making each tool
row state its complete requirement.

**The claim rule** - which vocabulary sits at the door:

- A **single-provider realm** (Slack is one provider) uses its REAL provider
  claims as door claims: `slack:search`, `slack:history`, `slack:post`, ... -
  the exact capabilities the Slack API needs per operation. The door demand,
  the connected-account consent, and the per-account binding all speak one
  vocabulary, checked twice.
- A **multi-provider realm** (mail spans Gmail OAuth today, with app-password
  mail providers reserved in the same catalog) uses a provider-neutral
  NAMESPACE claim at the door - `mail:read` / `mail:send` - and the real
  per-account claim (`gmail:read` / `gmail:send`) is resolved by the account
  broker behind the door.
- **Account-backed claims never sit in a connection `scope`.** A consuming
  connection's `scopes` carry door admission (`named_services:use`) plus
  namespace claims only for realms with no account behind them (conv,
  memories, tasks). Read/write on an account-backed realm is decided per
  account at gate 2, so the user consents per account, not per connection.

The external-client OAuth authorize page surfaces these same requirements up
front as an *Accounts this connection needs* panel — a single-provider claim as
a required row, a multi-provider door claim as a "connect one of" choice — and
lets the operator connect in place instead of discovering the gap at first call.
See [OAuth Delegated Credential Protocol Adapter](../delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md).

## The provider self-description contract: `connected_accounts`

A named-service provider that runs on the user's connected accounts declares
WHICH provider backs its operations, and which per-account claims each
operation needs, in its registration metadata:

```python
@named_service_provider(
    ...,
    metadata={
        "connected_accounts": [
            {
                "provider_id": ...,          # Delegated-to-KDCube provider id
                "provider_label": ...,       # human name for consent surfaces
                "claims": [...],             # the realm's full claim vocabulary
                "claim_labels": {...},       # human label per claim
                "claims_by_operation": {...} # optional: exact claims per operation
            }
        ],
    },
)
```

**Two ``provider_id`` fields, two registries - do not conflate them.** The
decorator's own ``provider_id`` names THIS SERVICE in the named-services
registry ("who I am" - mail's is ``sdk.integrations.mail``). The
``provider_id`` inside a ``connected_accounts`` entry names the
Delegated-to-KDCube ACCOUNT PROVIDER row whose accounts the service consumes
("whose accounts I use" - ``google``, ``slack``, ``acme-crm``). The
declaration reads as one sentence: "I, service X, serving namespace N, run my
operations on user accounts from account-provider P, needing these of its
claims per operation."

This is the contract every catalog consumer reads. Two real registrations ship
in the SDK.

**Mail - differentiated claims per operation.** The mail realm maps each
operation to exactly the claims it needs, so consumers can scope a shown ask
to the operations a configuration actually allows. From
[`sdk/integrations/mail/named_service.py`](../../../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/integrations/mail/named_service.py):

```python
MAIL_CONNECTED_ACCOUNT_REQUIREMENTS = [
    {
        "provider_id": GMAIL_PROVIDER_ID,
        "provider_label": "Google",
        "claims": [GMAIL_READ_CLAIM, GMAIL_SEND_CLAIM],
        "claim_labels": {
            GMAIL_READ_CLAIM: "read mail",
            GMAIL_SEND_CLAIM: "send mail",
        },
        "claims_by_operation": {
            "object.list": [GMAIL_READ_CLAIM],
            "object.search": [GMAIL_READ_CLAIM],
            "object.get": [GMAIL_READ_CLAIM],
            "object.action.download_attachments": [GMAIL_READ_CLAIM],
            "object.action.send": [GMAIL_SEND_CLAIM],
            "object.action.forward": [GMAIL_READ_CLAIM, GMAIL_SEND_CLAIM],
        },
    }
]
```

(the constants resolve to `provider_id="google"`, `gmail:read`, `gmail:send`), registered as:

```python
@named_service_provider(
    provider_id=PROVIDER_ID,            # "sdk.integrations.mail"
    namespace=MAIL_NAMESPACE,           # "mail"
    ...,
    metadata={
        "provider_catalog": MAIL_PROVIDER_CATALOG,
        "grant_hints": MAIL_GRANT_HINTS,
        "connected_accounts": MAIL_CONNECTED_ACCOUNT_REQUIREMENTS,
        ...
    },
)
class MailNamedServiceProvider(NamedServiceProvider):
```

**Slack - one flat claim set.** The Slack realm does not differentiate claims
per operation in this contract; consumers show the whole set. From
[`sdk/integrations/slack/named_service.py`](../../../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/integrations/slack/named_service.py):

```python
SLACK_CONNECTED_ACCOUNT_REQUIREMENTS = [
    {
        "provider_id": SLACK_PROVIDER_ID,
        "provider_label": "Slack",
        "claims": sorted(set(SLACK_CONNECTED_ACCOUNT_CLAIMS.values())),
        "claim_labels": {
            SLACK_SEARCH_CLAIM: "search messages",
            SLACK_CHANNELS_CLAIM: "list channels",
            SLACK_HISTORY_CLAIM: "read history",
            SLACK_FILES_READ_CLAIM: "read files",
            SLACK_FILES_WRITE_CLAIM: "upload files",
            SLACK_POST_CLAIM: "post messages",
            SLACK_ASSISTANT_SEARCH_CLAIM: "assistant search",
        },
    }
]
```

(the claim constants resolve to `slack:search`, `slack:channels`,
`slack:history`, `slack:files:read`, `slack:files:write`, `slack:post`,
`slack:assistant:search`).

**A custom service** - a namespace over your own OAuth/OIDC provider
(configured as a Delegated-to-KDCube provider row, see
[Delegated Provider Accounts](../delegated-accounts/delegated-accounts-README.md))
declares the same shape. Invented but realistic:

```python
# Example only - a custom CRM realm over its own OAuth provider.
ACME_CRM_CONNECTED_ACCOUNT_REQUIREMENTS = [
    {
        "provider_id": "acme-crm",
        "provider_label": "Acme CRM",
        "claims": ["acme:contacts:read", "acme:deals:write"],
        "claim_labels": {
            "acme:contacts:read": "read contacts",
            "acme:deals:write": "update deals",
        },
        "claims_by_operation": {
            "object.search": ["acme:contacts:read"],
            "object.get": ["acme:contacts:read"],
            "object.action.update_deal": ["acme:deals:write"],
        },
    }
]
```

**Who consumes this metadata.** Runtime named-service discovery publishes it
with the provider's spec, and every surface that reasons about
account-backed access reads the SAME contract:

- the composer menu's proactive consent (which claims to offer before the
  agent ever calls);
- the capabilities picker and agent inventory (which operations a selection
  implies, per `claims_by_operation`);
- the Create Automation Access screen (showing provider prerequisites and
  deep-linking Delegated to KDCube);
- the demand-ordering rule at denial time (next sections).

Declare it once, on the provider; nothing else should hardcode which provider
backs a namespace.

**Which connector app serves a provider is the guarded service's decision** -
never a user pick. A provider may configure several connector apps (OAuth
client registrations); the service declares which one its auth scenarios use,
one per provider type, in its named-services config block:

```yaml
named_services:
  connector_apps:
    slack: demo
    google: gmail
  namespaces:
    ...
```

The bridge binds this per request. One rule: the service's declaration, or
empty - which means provider-wide: any connector app's account qualifies.
Requirements metadata never names a connector app; which app serves a
provider is deployment configuration, not code.

## The two gates at call time

A single tool call crosses two sequential gates - the agent's own grant, then
the user's connected account plus the per-account binding. The gate diagram
and the configuration of each side are in
[Configuring Agent Access To Services And Accounts](../configuring-agent-service-access/configuring-agent-service-access-README.md);
here is the complete error vocabulary each gate answers with.

The managed MCP guard first checks the protected resource and its outer tool.
Card schema v2 stores those selections as
`resource_operations[resource][]`; the flat `operations` list is a derived
compatibility union. When the catalog still offers a tool but this card does
not select it, the guard returns `delegated_capability_not_granted` with the
exact resource and `outer_operation`. Its consent block carries a focused
Connection Hub route. For a hosted agent it also carries a one-click
`delegated_agent_grant_create` payload whose `resource_operations` map selects
only that tool. The focused view requires **Allow once** or **Allow always**;
the operation and initial invocation policy commit together. Grant completion
reports back to the matching conversation and does not replay the refused call.

**Gate 1 denies with `delegated_consent_required`** - the caller's delegated
credential lacks a grant the operation needs. The denial names the exact
grants, per operation:

```text
error = delegated_consent_required
  namespace / tool / operation
  required_grants / missing_grants / available_grants   exact, per operation
  code = connections.consent_needed                     (delegated-client callers)
  connection_hub_url  deep link landing on the caller's card, missing claims
                      pre-checked
  consent             the full grant block: kind delegated_agent_grant,
                      agent_client_id, resource, claims (= missing_grants);
                      for hosted agents also the one-click
                      delegated_agent_grant_create action
  next_step           Connection Hub grant extension for agent callers;
                      the hub link (or reconnect with incremental consent)
                      for external OAuth connections
```

A hosted agent's tool wrap raises this block as the standard scoped chat
demand; the approval merges into the agent's existing grant record. The
uniform denial builder is
[`consent_denial.py`](../../../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/integrations/connection_hub/delegated_credentials/consent_denial.py).

**Gate 2 denies with `needs_connected_account_consent`** (status 403) - the
caller holds the MCP grant, but the user-to-provider side cannot satisfy the
call:

```text
error.code = needs_connected_account_consent
error.details:
  reason               connect_required | claim_upgrade_required |
                       reconnect_required | account_required |
                       agent_grant_required
  retry_hint           true -> the same call succeeds after the user acts
                       (for account_required: when resent with account_id)
  provider_id / connector_app_id / claims / account_id
  candidates           labeled account summaries
                       [{account_id, label, email, workspace, status, claims}]
  connection_hub_url   open this to connect / approve / reconnect
  consent              the full Connection Hub consent block (action_label, ...)
```

How a caller acts on `reason`:

| reason | state | user action |
| --- | --- | --- |
| `connect_required` | no eligible account on the backing provider | connect the provider at `connection_hub_url` |
| `claim_upgrade_required` | an account exists, claim not approved | approve the listed claims |
| `reconnect_required` | the stored credential no longer works | reconnect that account |
| `account_required` | several accounts match | resend the SAME call with `account_id` from `candidates` |
| `agent_grant_required` | account connected and claim-capable, but THIS caller has no per-account binding (default-closed) | tick the claim for an account on the caller's grant card (Delegated by KDCube) |

The reasons originate in the account broker's `ensure_claim()` resolution and
flow verbatim into tool envelopes, named-service errors, and MCP results -
broker mechanics, credential health, and the refresh-retry-once contract are
[Delegated Provider Accounts](../delegated-accounts/delegated-accounts-README.md).

## Demand ordering: connect leads on zero accounts

Current behavior (in code:
[`consent_denial.connect_first_denial`](../../../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/integrations/connection_hub/delegated_credentials/consent_denial.py)).
When an operation is account-backed and the grantor has ZERO connected
accounts on the backing provider, fixing the gate-1 agent grant first is
meaningless - granting an agent access to a provider with no accounts binds
nothing. So the CONNECT demand leads: the gate-1 denial is replaced by a
gate-2 `needs_connected_account_consent` / `connect_required` denial carrying
the Connection Hub guided plan, scoped to the claims the attempt actually
needs, and the plan ends in the agent-grant hand-off. The agent-grant demand
(`agent_grant_required`) leads only when an account exists and the agent is
merely unbound.

Scoping of the connect ask, per the provider's `connected_accounts` contract:

- `claims_by_operation` present: the ask is that operation's claims.
- The operation is not listed in `claims_by_operation` (for example
  `provider.about` on an account-backed realm): connect still leads for the
  whole realm; the ask falls back to the realm's declared claims, deduped in
  declaration order - the user unticks what they keep.
- Flat `claims` only (Slack): the ask narrows to the claims the attempt is
  missing - the user approves the tool's need, never the provider's whole
  vocabulary.

## Declaration parity for plain MCP tools

A PLAIN ``@mcp`` tool - an MCP tool on a managed bundle surface with no
named-service registration behind it - participates in the same chain. The
tool declares which connected-account provider claims it needs, per tool, in
the existing application tool shape
(`ToolClaimPolicy.from_tool_config`), and enforces the declaration in the
tool body with one call:

```python
PRODUCTIVITY_TOOLS = {
    "productivity_slack_search": {
        "label": "Search Slack",
        "description": "Search Slack messages through the user's connected Slack account.",
        "connections": {
            "delegated_to_kdcube": {
                "connected_accounts": [
                    {"provider_id": "slack", "claims": ["slack:search"]},
                ],
            },
        },
    },
}

@mcp.tool(name="productivity_slack_search", ...)
async def _productivity_slack_search(query: str, ...) -> dict:
    denial = await enforce_tool_requirements(
        request,
        tool_name="productivity_slack_search",
        operation="search",
        requirements=tool_requirements("productivity_slack_search"),
    )
    if denial is not None:
        return denial
    return await slack.search_slack(query=query, ...)
```

The ``claims`` speak the PROVIDER's claim vocabulary - the claims a
connected account of that Delegated-to-KDCube provider row can hold
(`slack:search`, `gmail:read`). The enforcement helper
([`mcp_tool_enforcement.py`](../../../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/integrations/connection_hub/mcp_tool_enforcement.py))
resolves each declared claim through the same account broker the
named-services door uses and answers with the SAME demand ordering:
every claim resolves - the tool body proceeds; zero usable accounts on the
backing provider - the connect-first denial (the declared requirement is
passed to `connect_first_denial_for_identity` explicitly, no discovery
involved); an account exists but cannot satisfy the call - the account-level
consent (claim upgrade / agent grant / reconnect / account pick).

Two bindings complete the parity, per tool call: the surface's
connector-app declaration
(`bind_service_connector_apps_from_config` over the surface config's
`connector_apps` block) and the calling client's delegated identity plus
per-account claim scope from the request credential - so resolution stays
default-closed for delegated callers.

The worked example is the `productivity` MCP surface of the
`kdcube-services@1-0` example bundle
([`surfaces/mcp/productivity.py`](../../../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/kdcube-services@1-0/surfaces/mcp/productivity.py)):
a pure-MCP door wrapping Slack search plus mail search/read, with its own
layer-2 resource entry whose per-tool grants ARE the claims each tool needs
(gate-1 ceiling = the tool's need):

```yaml
- resource: '*/api/integrations/bundles/*/*/kdcube-services@1-0/public/mcp/productivity*'
  label: KDCube productivity MCP
  tools:
    productivity_slack_search:
      grants: [slack:search]
    productivity_mail_search:
      grants: [mail:read]
    productivity_mail_get:
      grants: [mail:read]
    productivity_mail_attachment:
      grants: [mail:read]
    productivity_mail_send:
      grants: [mail:send]
    productivity_mail_send_draft:
      grants: [mail:send]
```

Two send shapes, both under `mail:send`: `productivity_mail_send` composes
and transmits in one call, `productivity_mail_send_draft` transmits an
existing draft exactly as it sits in Drafts and removes it (Gmail
`drafts.send`; IMAP fetch → SMTP → expunge). The second is the one to use
when a person reviews before release: what they saw is what goes out.

`productivity_mail_attachment` returns one attachment's bytes as base64
inside the response (10 MB cap), selected by the stable `part_id` that
`productivity_mail_get` lists. The door runs outside a chat turn and has no
artifact workspace, which is why it hands bytes back instead of
materializing files the way the in-chat `download_gmail_attachments` does.

### Testing the productivity door

Three verifications prove enforcement on the door after a `refresh`:

1. Call `productivity_slack_search` as a caller whose grantor has NO Slack
   account: the result is the gate-2 `needs_connected_account_consent` /
   `connect_required` denial carrying the guided connect plan (agent
   hand-off included).
2. Connect a Slack account but leave the calling agent/connection unbound on
   it: the same call returns the account-level consent
   (`agent_grant_required`) deep-linking the caller's grant card.
3. Bind the account claim to the caller and retry: the call returns real
   Slack search results. The mail tools (`productivity_mail_search`,
   `productivity_mail_get`, `productivity_mail_attachment`) verify identically against the `google`
   provider row.

## Connecting an account: least privilege at the provider

When a user connects a provider account, KDCube requests from the provider the
OAuth scopes for exactly the claims the user ticked - no more. Each claim maps
to its `provider_scopes` in the provider config (`gmail:read` ->
`gmail.readonly`, `gmail:send` -> `gmail.send`), and the authorize request
carries only the selected claims' scopes. An empty selection is rejected, not
widened to the connector app's ceiling. Re-approving an existing account
defaults to `claims_mode="add"` (the union of held and newly-ticked claims, so
incremental consent never silently drops access); the hub's manage form uses
`claims_mode="replace"` to narrow. KDCube does not enable Google's
`include_granted_scopes`, so the provider grants exactly what this request
asks for rather than carrying a scope the OAuth client held before.

Independently of the provider token's scope, what a caller may DO is gated on
the KDCube claim plus the per-agent, per-account binding (default-closed): a
tool can send mail only if the account holds `gmail:send` AND an agent is
bound to send on it.

## The three scenarios, end to end

Each scenario is one issuance path over the same catalog, and each maps to an
exact Connection Hub surface.

**1. Hosted agent acting on the user's accounts.** The agent attempts an
account-backed tool; the denial (ordered as above) surfaces as a chat consent
banner. The banner opens the guided connect plan on the **Delegated to
KDCube** tab - account connected, access working, requested approvals as
per-claim chips, one primary button for the first unmet step. The plan ends
with the hand-off: "Continue - grant it to \<agent\>". That lands the agent's
card under **Delegated by KDCube**, where the user ticks claims per account -
default-closed, nothing pre-checked, an untouched account grants nothing.
Retrying the same call then succeeds. The per-account binding
(`account_scope`) semantics are in
[Configuring Agent Access To Services And Accounts](../configuring-agent-service-access/configuring-agent-service-access-README.md).

**2. External MCP connection.** The MCP-speaking app probes the URL, gets the
protected-resource challenge, resolves its OAuth client identity through
pre-registration, CIMD, or DCR (layer 4), and opens the OAuth authorize page:
a review of the client and resource ceiling with collapsible capability
sections, and per-account default-closed
binding for account-backed namespaces - with links into the hub for
connecting more accounts and to where the connection's card will live. After
approval the card sits under **Delegated by KDCube**, editable and revocable;
an edit or revocation applies on the connection's next call. Step-by-step
journey and identity-scope choices:
[Delegate A KDCube Service To An External Client](../../../../recipes/connections/delegate-kdcube-service-to-external-client-README.md).

**3. Automation access.** The **Delegated by KDCube -> Create automation
access** panel renders the same catalog: the user selects a resource, its
grants (only ones delegable to them, layer 3), narrows named-service
operations to an exact selection of existing operation ids, and sets a TTL.
The card stores the narrowed boundary tree, the managed guard carries it onto
the request, and the bridge prefers it over the deployment default - so the
token reaches exactly the selected operations and nothing else, and editing the
selection later applies without re-issuing the token. Provider-backed namespaces keep the
connected account as a separate upstream prerequisite; the panel shows it and
deep-links Delegated to KDCube. Details:
[Protect Bundle MCP With Managed Credentials](../../../../recipes/connections/protect-bundle-mcp-with-managed-credentials-README.md).

## Troubleshooting

| symptom | cause | fix |
| --- | --- | --- |
| `delegated_access_grants_not_delegable` | the signed-in user's roles/permissions match no `delegable_roles`/`delegable_permissions` on the requested grant's capability row | grant the user a qualifying role/permission, or extend the capability row (layer 3) |
| `needs_connected_account_consent` with `reason=connect_required` | no connected account on the provider backing the operation | connect the provider at `connection_hub_url` (the guided plan ends in the agent hand-off) |
| `needs_connected_account_consent` with `reason=agent_grant_required` | the account is connected and claim-capable; the calling agent/connection has no per-account binding (default-closed) | on the caller's Delegated-by-KDCube card, tick the claim for the account of choice |
| `delegated_access_requires_resource_grants` | an automation-access request submitted a resource without claims | select at least one grant for the resource before creating the token |
