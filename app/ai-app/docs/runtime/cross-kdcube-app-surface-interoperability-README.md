---
id: repo:kdcube-ai-app/app/ai-app/docs/runtime/cross-kdcube-app-surface-interoperability-README.md
title: "Cross-KDCube App Surface Interoperability"
summary: "Design and implementation baseline for composing apps across independent KDCube deployments, including browser identity, managed MCP and REST authorization, delegated credentials, consent, retry, refresh, revocation, and the source-side adapter still to build."
status: design
tags: ["runtime", "apps", "interoperability", "federation", "mcp", "rest", "oauth", "pkce", "identity", "connection-hub"]
keywords:
  [
    "cross KDCube app call",
    "multi cluster app composition",
    "remote managed MCP",
    "remote managed REST",
    "cross KDCube OAuth",
    "cross KDCube consent",
    "shared parent domain cookie",
    "multi Cognito",
    "remote delegated credential",
    "per agent revocation",
  ]
updated_at: 2026-09-12
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/cross-app-surface-interoperability-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/cross-runtime-context-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/tenant-project-user-and-execution-boundaries-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/embedding-kdcube-in-a-host-app-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authenticated-mcp/authenticated-mcp-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/claim-driven-consent/claim-driven-consent-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-accounts/delegated-accounts-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/auth-bundle-federated-README.md
---
# Cross-KDCube App Surface Interoperability

This document records both the implemented baseline and the design for an app
in one KDCube to consume a governed surface owned by another KDCube.

It uses **KDCube A** for the caller's deployment and **KDCube B** for the
deployment that owns the protected resource:

```text
KDCube A                                             KDCube B
  app A / agent A                                      app B
  source user identity                                 protected MCP or REST
  source conversation                                  target Connection Hub
  source-side credential storage                       target grant card
```

The central rule is:

```text
identity at A is context for A;
authority at B is decided and enforced by B.
```

Portable KDCube runtime context is not a cross-deployment credential. A call
to B must carry a proof B accepts, and B creates a new local request context
after validating that proof.

For implemented composition inside one tenant/project runtime, read
[Cross-App Surface Interoperability](cross-app-surface-interoperability-README.md).

## Status At A Glance

| Capability | Status | What that status means |
| --- | --- | --- |
| Mount widgets from more than one configured KDCube in one browser scene | Implemented | The scene can address several runtimes. Each widget calls its owning runtime. |
| Reuse one browser login across same-site KDCube subdomains | Implemented when configured | Shared parent-domain cookies plus target-side Multi-Cognito trust let both runtimes authenticate the same browser request. |
| Authenticate on independent KDCube domains | Implemented as ordinary target login | The browser establishes a B-local session, potentially through a common upstream identity provider. A's host-only cookie is not copied to B. |
| Call B's REST or MCP with a credential already accepted by B | Implemented | Normal HTTP/MCP transport works through B's ingress. B authenticates and authorizes the request. |
| Expose B's managed MCP and REST with OAuth delegated credentials | Implemented at B | B publishes resource/auth metadata, supports public-client OAuth with PKCE, creates Connection Hub grant cards, issues/refreshes/revokes credentials, and checks current authority on calls. |
| Let an ordinary external MCP client authorize against B | Implemented at B | Pre-registration, Client ID Metadata Documents (CIMD), and retained DCR enter the same consent and token flow. |
| Let a hosted agent in A automatically complete B's challenge and consent flow | **Not implemented** | A lacks the remote authorization client that joins challenge discovery, client identity, PKCE, callback, credential storage, event notification, refresh, and retry. |
| Copy A's `RequestContext` or platform session to B | Not a supported contract | B must authenticate a B-accepted proof and reconstruct its own context. |
| Use one shared Data Bus, job stream, or conversation lane across A and B | Not a supported contract | Those stores are local to a KDCube. A remote command enters B through an explicit authenticated adapter, which then submits local work. |

The missing feature is therefore not B's OAuth authorization server or B's
managed MCP guard. It is the **source-side remote KDCube authorization client
in A**. Implementation is tracked in
[kdcube/kdcube#223](https://github.com/kdcube/kdcube/issues/223).

## Implemented Baseline

### 1. The Network Boundary Already Works

A configured app can call a remote HTTP or MCP endpoint today when it already
has a credential the target accepts:

```text
app A trusted runtime
  -> HTTPS request
     Host: b.example.com
     Authorization: Bearer <credential accepted by B>
  -> B edge / load balancer / OpenResty
  -> B integrations router
  -> authenticate bearer under B authority
  -> build B-local RequestContext
  -> app B @api or @mcp
  -> endpoint policy + resource grant + domain checks
  -> response to A
```

The call follows B's external path even when A and B happen to run in the same
cloud network. They do not share an app registry or a local operation caller.

This baseline is useful for preconfigured service credentials and for clients
that already implement B's OAuth flow. It does not yet give a hosted A agent a
demand-driven way to obtain its own B credential.

### 2. Browser Composition Across KDCubes Already Works

A scene can mount widgets whose owning runtimes differ:

```text
host page
  |
  +-> widget owned by KDCube A
  |     -> A origin -> A authenticates -> A app surface
  |
  +-> widget owned by KDCube B
        -> B origin -> B authenticates -> B app surface
```

The host chooses the runtime and app route. It does not become the authority
server for the mounted widget. The widget's owning KDCube authenticates every
backend request.

#### Same Parent Domain

For deployments under one parent domain, the configured login can set the
platform cookies for that parent:

```text
top-level login at auth.example.com
  -> Set-Cookie: __Secure-LATC=...; Domain=example.com; Secure; SameSite=Lax
  -> Set-Cookie: __Secure-LITC=...; Domain=example.com; Secure; SameSite=Lax

browser loads scene at www.example.com
  |
  +-> https://a.example.com/...  browser sends the two cookies
  |     -> A validates their Cognito/OIDC issuer and subject
  |
  +-> https://b.example.com/...  browser sends the same two cookies
        -> B Multi-Cognito accepts that issuer/client because it is in
           B's descriptor-owned trusted provider list
```

The browser transport is cookies. Their values carry the access and identity
credentials that each runtime validates. B does not accept them merely because
the DNS names share a parent; B accepts them because its authenticator trusts
their issuer and client.

This is the topology used by a mixed scene that mounts configured `demo` and
`dev` runtimes while choosing one runtime as the browser auth provider. It is a
deployment convenience for related subdomains, not a general federation
protocol.

#### Independent Domains Or Host-Only Cookies

A host-only A cookie is never sent to B:

```text
browser has A cookie for a.example.net
  -X-> browser request to b.example.org does not contain that cookie

browser opens B authorization page
  -> B finds no B-local session
  -> B starts its configured login flow
  -> common/trusted upstream identity provider may reuse its own SSO session
  -> B sets a B-local cookie
  -> B resumes authorization
```

The user may see no credential prompt when the upstream identity provider has
an active SSO session, but B still establishes and validates its own platform
session. If B does not trust a provider through which the user can authenticate,
the consent flow cannot proceed until that deployment relationship is
configured.

### 3. KDCube B Already Owns The Target Authorization Flow

For managed MCP and managed REST resources, B already supplies the protected
resource and OAuth authorization-server side:

```text
external client
  -> call protected B resource without a valid bearer
  <- 401 WWW-Authenticate with protected-resource metadata

external client
  -> discover B resource metadata and authorization-server metadata
  -> resolve client by:
       descriptor pre-registration
       or HTTPS Client ID Metadata Document
       or retained Dynamic Client Registration
  -> create PKCE verifier + S256 challenge
  -> open B /oauth/authorize in browser

B authorize
  -> authenticate B platform user
  -> show client, resource, claims, operations, and account choices
  -> explicit consent protected by single-use CSRF
  -> create/update B Connection Hub card
  -> redirect authorization code to validated client callback

external client
  -> exchange code + PKCE verifier at B /oauth/token
  <- B access token + rotating refresh token

external client
  -> retry original resource call with B access token
  -> B resolves the current card and checks live authority
```

B's card is already specific to its grantor and client. A hosted agent must
use a stable client identity that distinguishes its application and agent so
B's existing per-client card and revocation semantics remain per agent.

### 4. KDCube A Already Has Reusable Pieces

A has two relevant but separate mechanisms:

```text
A: delegated provider accounts
  Connection Hub start_oauth
    -> signed state stored server-side
    -> provider browser authorization
    -> callback and code exchange
    -> credential in the user secret store
    -> non-secret connected-account metadata
    -> passive connections.consent.granted event for pending conversations
```

```text
A: hosted-agent MCP resolution
  resolve_mcp_server_map(...)
    -> read configured kind:mcp connection
    -> derive stable kdcube-agent:<application>:<agent_id>
    -> ask injected bearer_provider for this user + connection
    -> put bearer in MCP Authorization header
    -> framework-neutral MCP client uses the resulting server map
```

These are implementation seams, not the completed cross-KDCube feature:

- the generic provider connector expects a confidential client secret and
  does not currently carry a PKCE verifier;
- its state shape does not bind a remote KDCube issuer, protected resource,
  application, agent, and conversation as one demand;
- the hosted-agent MCP bearer provider resolves a grant in the **local**
  Connection Hub rather than a credential issued by B;
- it does not parse B's protected-resource challenge, register or publish a
  remote OAuth client identity, exchange B's code, refresh B's token, or react
  to B revocation.

The existing `connections.consent.granted` event path is directly reusable:
it emits a passive event into the original conversation, never starts a turn,
and never replays the denied call.

### 5. Adjacent Federated Tokens Have A Narrow Contract

KDCube also has app-issued federated Data Bus session tokens. They authorize a
scoped Socket.IO/Data Bus client after an app verifies its upstream proof:

```text
upstream client proof
  -> target app verifies proof
  -> target Connection Hub federated_data_bus_claim
  -> short-lived target-issued Data Bus token
  -> target Socket.IO/Data Bus ingress
```

That token is a Data Bus transport capability. It is not a general credential
for cross-KDCube MCP, REST, jobs, or copied request context.

A caller that already holds a delegated Card for a target service has a
different path: it can present the same Card bearer directly when opening the
target Data Bus connection. The Card remains the revocable delegation edge;
the caller also names its selected target resource, and the target app checks
the same service-owned canonical operation IDs whether they arrive through
Data Bus or MCP. No federated-session token is minted for this path. See
[Data Bus: Delegated Card Clients](../service/comm/data-bus-README.md#delegated-card-clients).

## Missing Source-Side Contract

The missing A-side adapter must turn a governed denial from B into a complete,
per-user, per-application, per-agent delegated connection:

```text
B can already be the protected resource + authorization server.
A can already run an MCP client and store connected credentials.

missing:
  B challenge
    -> A remote client discovery
    -> A stable client identity at B
    -> A PKCE authorization demand
    -> B browser consent
    -> callback at A
    -> server-side code exchange
    -> A credential storage
    -> passive event into A conversation
    -> agent retry with B bearer
```

This adapter is required for managed MCP and is reusable for managed REST.
Named services exposed across deployments use an explicit MCP or REST adapter;
they do not acquire a new network bridge implicitly.

## Proposed End-To-End Flow

### Three Participants And Three Channels

```text
                 BROWSER CONSENT CHANNEL

A conversation ---- recovery URL ----> browser
                                          |
                                          v
                                   B authorize + consent
                                          |
                              authorization code redirect
                                          |
                                          v
                                      A callback


                 SERVER AUTHORIZATION CHANNEL

A callback/runtime -------------------------> B token endpoint
        code + PKCE verifier                    |
                                                v
A server-side credential store <---------- access + refresh


                 GOVERNED CALL CHANNEL

A agent -> A trusted bearer provider -> MCP/REST request -> B protected resource
                                                        -> B live card check
                                                        -> app B operation
```

The browser never receives B's refresh token. The agent and generated code
never receive either provider credentials or B's refresh token. Trusted A
runtime code adds the current B access token at the transport boundary.

### Detailed First-Use Sequence

```text
1. Agent A selects a configured B tool.

2. A MCP/REST client calls B without a usable B credential.

3. B returns a structured authorization failure:
     target issuer
     protected resource
     required claims/scopes
     failed operation when available
     protected-resource metadata URL

4. A remote-KDCube adapter:
     validates the target against its descriptor-owned connection
     discovers B metadata
     resolves a stable client identity for application A + agent A
     creates a PKCE verifier and challenge
     stores one short-lived, atomically consumable pending record
     returns a browser authorization URL to the conversation UI

5. Browser opens B /oauth/authorize.
     same-parent deployment:
       browser may already send a cookie B trusts
     independent deployment:
       B establishes a B-local session through its normal login flow

6. B shows the target resource and A's client identity.
     The B user explicitly grants a bounded resource/claim/operation set.
     B creates or updates the per-client Connection Hub card.

7. B redirects to A's registered callback:
     code=<short-lived authorization code>
     state=<A opaque state>
     iss=<B issuer>

8. A callback atomically consumes its pending state and validates:
     expected B issuer
     expected B resource
     exact redirect URI
     source A user
     source application + agent
     source conversation address
     PKCE verifier association

9. A exchanges code + PKCE verifier with B server-to-server.
     B validates code, client, redirect URI, and PKCE.

10. A stores the B credential under the initiating A user and the exact
    application/agent/issuer/resource/client tuple.

11. A emits passive connections.consent.granted into the original
    conversation. The event says authority changed; it grants no authority,
    starts no turn, and replays no operation.

12. Agent A retries only if the operation remains relevant.
    A's trusted bearer provider reads/refreshes the B credential and adds it
    to the request. B rechecks its current card and app policy.
```

### What The Browser Login Proves

The A user and B user do not have to share an internal database id. The flow
creates an explicit relationship:

```text
A pending demand
  A user + application + agent + conversation
        |
        | state-correlated OAuth flow
        v
B grant card
  B authenticated grantor + A client identity + B resource authority
```

When both KDCubes trust the same identity provider, their verified subject may
match. When they use different providers, the OAuth consent itself links the
initiating A user to the B account that approved the card. A must not guess
that link from email, display name, or an unverified context payload.

## State And Storage Ownership

Three records have different owners:

```text
KDCube A pending authorization record          short lived, single use
  state_id
  source_user_id
  source_application
  source_agent_id
  source_conversation address
  target_issuer
  target_resource
  requested claims/operations
  client_id
  redirect_uri
  PKCE verifier
  created_at / expires_at

KDCube A remote delegated credential           server-side secret storage
  source_user_id
  source_application + source_agent_id
  target_issuer + target_resource + client_id
  B access token
  B rotating refresh token
  access expiry
  non-secret connection status and labels

KDCube B delegated grant card                  B authority store
  B grantor subject
  A client identity
  B protected resource
  approved claims/scopes/operations/accounts
  current/revoked/expired state
```

B's card is the authority record. A's credential is the proof used to ask B.
Deleting A's credential disconnects A; narrowing or revoking B's card removes
authority even if A still holds an unexpired token snapshot.

The pending record must be consumed atomically. The existing provider OAuth
state/callback shape can supply structure, but the cross-KDCube implementation
must use a compare-and-consume transition rather than a separate read and
delete.

## Stable Client Identity

Per-agent revocation already exists on the target when each hosted agent has a
distinct client identity. The cross-KDCube client must preserve that property.

Preferred modern path:

```text
A publishes one HTTPS Client ID Metadata Document identity per
application + agent (or another stable descriptor-owned mapping).

B resolves that URL as client_id
  -> exact callback set
  -> human-readable application and agent identity
  -> public client, PKCE required
  -> stable B card across reconnects
```

Supported compatibility paths:

- descriptor pre-registration at B for a known A client;
- DCR at B when enabled and A's exact callback is allowed.

DCR remains supported for existing clients, while CIMD is the preferred path
for MCP 2026-07-28 clients. Client registration identifies the client and its
callbacks; it never grants a resource.

## Runtime Calls After Consent

### MCP

The existing framework-neutral resolver remains the insertion point:

```text
agent framework
  -> resolve_mcp_server_map(connection, user, client_id, bearer_provider)
  -> remote bearer provider key:
       A user + application + agent + B issuer + B resource + B client
  -> refresh when required
  -> {url: B MCP URL, Authorization: Bearer <B access token>}
  -> existing MCP client transport
```

No LangGraph-, CrewAI-, or resident-agent-specific authorization path is
required.

### Managed REST

The same credential service can supply an Authorization header to a declared
managed REST connection:

```text
app A HTTP adapter
  -> resolve B credential for exact resource and operation
  -> HTTPS request to B managed REST surface
  -> B shared delegated-credential guard
  -> app B @api
```

This is a network call. `call_bundle_operation(...)` remains same-KDCube only.

### Data Bus, Jobs, And Conversations

Cross-KDCube composition uses an explicit target ingress:

```text
A -> B authenticated REST/MCP command
       -> B app adapter
          -> B local Data Bus publish
          -> or B local background-job enqueue
          -> or B local conversation submission
```

This preserves B's local ordering, retry, and ownership contracts. A does not
write B's Redis streams directly.

## Refresh, Revocation, And Disconnect

```text
normal refresh
  A trusted credential service
    -> B token endpoint with current refresh token
    -> B validates current live card
    -> B atomically rotates refresh token
    -> A atomically replaces stored credential

B card narrowed or revoked
  next B call or refresh
    -> B denies under current card
    -> A marks connection revoked/reconnect_required
    -> A emits a passive status event when a conversation is waiting

A user disconnects
  A calls B revocation endpoint when a token is available
  -> B retires token/card according to B's revocation contract
  -> A deletes its local credential and connection projection
```

A must not turn temporary B unavailability into revocation. Storage/network
unavailability is retryable; `invalid_grant`, a revoked card, or a definitive
authorization denial is not.

## Required Failure Contract

| Failure | Required result |
| --- | --- |
| Target URL is not the descriptor-declared B connection | Fail before discovery; do not follow arbitrary authorization metadata. |
| B metadata or CIMD violates URL, redirect, size, or network policy | Fail closed with a non-secret configuration/security reason. |
| B requires login | Open B's normal browser login/authorize path; never send A's server-side cookie. |
| A state is absent, expired, already consumed, or mismatched | Reject callback; do not exchange code. |
| Issuer or redirect differs from the pending record | Reject callback. |
| Code exchange or PKCE fails | Keep no credential; report reconnect required. |
| B is temporarily unavailable | Return retryable `temporarily_unavailable`; retain valid A credential state. |
| No grant exists yet | Return structured consent demand with B identity, resource, requested claims/operation, A app/agent, and recovery URL. |
| B card is narrowed or revoked | B denies immediately; A marks the connection accordingly and requires a new explicit grant for missing authority. |
| Consent succeeds | Emit a passive event; do not replay the blocked operation. |
| Agent retries | Resolve current credential and let B perform fresh authorization. |

## Security Invariants

1. A descriptor names every remote KDCube resource and allowed client path.
   Discovery refines a configured target; it does not authorize arbitrary
   destinations.
2. Browser cookies authenticate only at the runtime that receives and trusts
   them. A never forwards its ambient browser cookie in a server request to B.
3. The client identity is stable and specific enough for per-application,
   per-agent consent and revocation.
4. OAuth state is short-lived, issuer/resource/redirect/user/app/agent bound,
   and atomically single use.
5. Public clients use PKCE S256. Client registration creates no authority.
6. Access and refresh tokens remain in A's trusted credential store and
   transport adapter. They do not enter model context, generated code, browser
   event payloads, or app logs.
7. B's current Connection Hub card and current app policy decide every call.
   A's cached credential never overrides B revocation.
8. Consent completion is a signal. It never executes or replays the denied
   operation.
9. Separate KDCubes never treat serialized portable runtime context as proof.
10. Logs correlate state id, client, issuer, resource, source app/agent, and
    outcome without recording codes, verifiers, cookies, or tokens.

## Descriptor-Owned Configuration

The implementation must extend the app's existing consumer connection model.
The exact field names belong to the configuration design, but the descriptor
must supply or bound these facts:

| Fact | Why it is configuration |
| --- | --- |
| B MCP/REST URL and protected resource id | Defines the remote surface the app may contact. |
| Expected B issuer / metadata origin | Prevents authorization-server substitution. |
| Registration mode: pre-registered, CIMD, or DCR compatibility | Defines how A proves its client identity. |
| A callback URL or callback alias | Must exactly match B's validated client metadata. |
| Application/agent client identity mapping | Preserves distinct cards and revocation. |
| Requested claim/scope ceiling and optional operation ceiling | Limits what the agent may ask the user to delegate. |
| Browser recovery route and allowed return origin | Keeps consent UI and callback routing bounded. |

Secrets remain `*_ref` values resolved server-side. The modern CIMD/public
client path uses PKCE and does not introduce a client secret into the agent.

## Implementation Work Packages

1. **Remote target discovery.** Parse B's managed-resource challenge, validate
   the configured target, and resolve protected-resource and authorization
   metadata.
2. **Stable A client identity.** Serve or configure per-app-agent CIMD,
   support pre-registration, and retain DCR as an explicit compatibility mode.
3. **PKCE pending-flow store.** Add the bound state record and atomic
   compare-and-consume callback transition.
4. **A callback and token exchange.** Validate state/issuer/redirect, exchange
   code with PKCE, and persist the returned B credential server-side.
5. **Remote credential provider.** Supply and refresh B credentials through
   the existing MCP bearer-provider seam and a shared managed-REST seam.
6. **Demand and completion UX.** Register the original conversation demand,
   return a structured recovery action, and emit a passive completion or
   failure event without replay.
7. **Disconnect and revocation.** Call B revocation when possible, delete A's
   local credential, and preserve distinct temporary-unavailable versus
   revoked states.
8. **Tracing and tests.** Cover shared-domain and independent-domain browser
   auth, multi-worker callback races, per-agent card separation, refresh
   rotation, revocation, retries, and secret non-disclosure.

## Acceptance Scenarios

### First Consent From A To B

```text
given agent A has a descriptor-declared B MCP connection
and no B credential exists for A user + app + agent
when the agent selects a protected B tool
then A returns one structured consent demand
and B authenticates the browser and shows the exact client/resource request
and approval creates one B per-client card
and B redirects a code to A
and A exchanges it with PKCE and stores the credential server-side
and A emits one passive completion event
and no blocked tool call is replayed
and an explicit retry succeeds under B's live card
```

### Per-Agent Revocation

```text
given agent A1 and agent A2 have distinct B client identities and cards
when the B user revokes A1
then A1's next call and refresh fail under B authority
and A2 remains authorized
```

### Shared Parent-Domain Browser

```text
given A and B are same-site subdomains
and the auth cookies are scoped to the parent domain
and B trusts their issuer/client
when B authorize opens
then the browser sends the cookies to B
and B resolves the same authenticated subject without a second login
```

### Independent Domains

```text
given A's cookie is host-only and B has no local cookie
when B authorize opens
then A's cookie is absent at B
and B runs its configured login flow
and an upstream SSO session may complete that login silently
and consent continues only after B has a valid local platform session
```

### Multi-Worker Callback Race

```text
given two A workers receive the same callback state
when both attempt completion concurrently
then one atomic consume wins
and only that worker exchanges and stores the code
and the other receives an already-used/invalid-state result
```

### Target Revocation And Temporary Failure

```text
given A holds a B credential
when B revokes the card
then B denies the next call and refresh and A marks reconnect required

when B is only unavailable
then A returns a retryable availability error
and does not erase or silently expand the credential
```

## Implementation Anchors

| Existing responsibility | Current anchor |
| --- | --- |
| Same-KDCube route selection | [Cross-App Surface Interoperability](cross-app-surface-interoperability-README.md) |
| Browser cookie and token-handoff topologies | [Embedding KDCube In A Host App](../service/cicd/embedding-kdcube-in-a-host-app-README.md) |
| Multi-Cognito trusted issuer/client validation | [Auth](../service/auth/auth-README.md) and `kdcube_ai_app/auth/implementations/multi_cognito.py` |
| B OAuth/resource server, PKCE, cards, refresh, and revocation | [OAuth Delegated Credential Protocol Adapter](../sdk/solutions/connections/delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md) |
| B managed MCP concept | [Authenticated MCP](../sdk/solutions/connections/authenticated-mcp/authenticated-mcp-README.md) |
| A generic provider-account OAuth flow | `repo:app-ecosystem/products/connection-hub/packages/connection-hub/src/connection_hub/delegated_to_kdcube/operations.py` and `oauth.py` |
| A framework-neutral MCP bearer seam | `repo:app-ecosystem/products/connection-hub/packages/connection-hub/src/connection_hub/delegated_mcp.py` |
| Passive consent-completion events | Connection Hub's event contract plus `kdcube_ai_app/apps/chat/sdk/integrations/connection_hub/delegated_to_kdcube/consent_demand.py` |
| Narrow app-issued Data Bus federation | [App Federated Auth For Data Bus](../sdk/bundle/auth-bundle-federated-README.md) |
| Portable context boundary | [Cross-Runtime Context](cross-runtime-context-README.md) |
