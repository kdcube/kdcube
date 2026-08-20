---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-cards/delegated-cards-README.md
title: "Delegated Access Cards: Storage, Rendering, And Enforcement"
summary: "Canonical lifecycle of Connection Hub Delegated by KDCube cards: what each card stores, which live catalogs render its editor, how changes reach runtime enforcement, and how descriptor drift must be reconciled."
status: active
tags: ["sdk", "solutions", "connections", "connection-hub", "delegated-access", "cards", "grants", "mcp", "named-services"]
keywords: ["Delegated by KDCube", "AutomationAccessRecord", "resource_grants", "named_service_operations", "account_scope", "registry_access_id", "card authority", "descriptor drift", "grant lifecycle"]
updated_at: 2026-08-11
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-connections/delegated-connections-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-token-storage-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/configuring-agent-service-access/configuring-agent-service-access-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-accounts/delegated-accounts-README.md
---
# Delegated Access Cards

A card under Connection Hub **Delegated by KDCube** is the user-visible form of
one server-side delegated-access record. It answers:

- which user granted access;
- which agent, connected app, or manual automation received it;
- which KDCube resources, grants, and operations that caller may use;
- which connected provider accounts and claims that caller may use;
- when the grant was created, when it expires, and how it is revoked.

The card is the user's live delegated-authority record, not a cached
illustration of a token. Pointer-backed credentials identify the card, and the
managed guard resolves its current contents on each call. Editing or revoking
the card therefore changes the next call made with an already-issued bearer.
The deployment descriptor, published as the delegated catalog, is the ceiling
around that user decision: every governed call intersects the card with the
active catalog, so a withdrawn capability is denied without editing the card.

The card is also not a copy of the entire current service catalog. It stores
the user's selected authority. Connection Hub separately reads the live
descriptor and provider catalogs to render choices around that stored
selection.

This page covers every card in **Delegated by KDCube**: manual automations,
hosted agents, and external OAuth/MCP clients. Cards in **Delegated to
KDCube** represent connected provider accounts and use a different storage
lifecycle; see [Delegated Accounts](../delegated-accounts/delegated-accounts-README.md)
and [Connection Hub Token Storage](../connection-hub-token-storage-README.md).
Connection-edge and authenticator administration cards likewise project their
own stores; they are not delegated-access records.

```text
stored card selection                 current deployment catalogs
  what this user granted                what can be granted now
  resource_grants                       resources and grants
  named_service_operations              namespaces and operations
  account_scope                         connected accounts and claims
             \                           /
              +---- Connection Hub -----+
                         |
                         +-- read-only card: stored decision, live labels
                         +-- create/edit form: live choices + stored selection
                         +-- runtime guard: current card authority
```

## Card Families

All three families use `AutomationAccessRecord`, the same per-user index, and
the same **Delegated by KDCube** list. Their issuance and credential-retention
rules differ.

| `source` | Represents | How it is created | Credential material retained in the card record |
| --- | --- | --- | --- |
| `manual` | A script, service, or external automation whose operator copies a bearer. | `delegated_access_create`. | The raw bearer is returned once and is not retained. The record keeps `session_id` and `last_four` for revocation and identification. |
| `agent` | A hosted agent with deterministic identity `kdcube-agent:<app>:<agent>`. | Demand-driven consent or `delegated_agent_grant_create`, backed by `create_access(client_id=...)`. | The reusable access token is retained server-side so each turn can resolve the same consented bearer. It is never returned by list. |
| `oauth` | An external OAuth/MCP client. | Automatically on initial token issuance and every refresh rotation. | Current access- and refresh-token handles are retained server-side so revoke can invalidate both. They are never returned by list. |

An OAuth client and a hosted agent are both delegated callers. The source
field records how their credential lifecycle is managed; it does not create a
different authorization model.

## Stored Record

### Durable record and live cache

Connection Hub bundle storage is the durable source of truth. Redis is the
TTL-managed live projection of the latest committed card revision:

```text
delegated-cards/v1/
  grantors/<subject_hash>/
    cards/<access_id>/
      revisions/card_revision_2026-08-11-14-32-07-123_00000001_8f21c47a93bd.json
      revisions/card_revision_2026-08-11-15-04-19-881_00000002_b9d06ee7124a.json
      current.json

Redis delegated-access:automation:<access_id>
  latest committed live projection, or a short-lived updating/revoked marker

Redis delegated-access:automation-by-grantor:<subject_hash>
  sorted set of access_id -> expires_at for the active-card list
```

Each immutable revision contains the complete authorization decision,
`card_revision`, `catalog_version`, lifecycle state, timestamps, and non-secret
fingerprints. It never contains raw access tokens, refresh tokens, provider
credentials, or reusable session secrets. Those remain only in their bounded
live stores. The reconstructable Redis card projection likewise contains the
non-secret authority and lifecycle fields. A durable cache restore does not
reconstruct a credential handle; the OAuth, grant, or session owner resolves
that bounded state separately and returns its normal reconnect/reissue denial
when it is gone. `current.json` points to the latest committed revision and
carries its filename, integer `card_revision`, and full content hash.

Revision filenames follow the same timestamped, content-addressed convention
as catalog versions:

```text
card_revision_<UTC timestamp with milliseconds>_<8-digit card_revision>_<first 12 hash chars>.json
```

The timestamp and hash are mandatory. The zero-padded integer mirrors the
record field used for optimistic concurrency; it supplements rather than
replaces the common versioned-resource naming convention.

Create, edit, and revoke use a per-card shared critical section. Before writing
a new durable revision, the writer replaces the Redis live value with an
updating marker so requests cannot continue under the previous authority. It
then writes and validates the immutable revision, advances `current.json`, and
installs the new Redis projection. A cache miss loads the durable current
revision and repopulates Redis only when the card is active and unexpired. The
conditional Redis installer compares `card_revision`: delayed recovery cannot
overwrite a newer revision, an updating marker, or a revoked tombstone. The
mutation finalizer may replace only the marker carrying its own mutation id.

Expiration deletes only the Redis projection. The durable revision remains and
`expires_at` prevents cache restoration or use. Revocation commits a new
durable `revoked` revision before live credential cleanup; it does not delete
history. Normal active list/open is Redis-first: it reads the expiry-scored
grantor index and current live-card projections, then computes drift against
Redis-cached `active.json`. The grantor index has no fixed seven-day expiry;
expired members are pruned by score. If the index or a projection is missing,
Connection Hub rebuilds it from durable `current.json` and the referenced
timestamped revision, repopulating only active, unexpired cards. Retention of
card history is an explicit administrative policy separate from authorization
TTL.

The relevant cache lifetimes have different meanings:

| Projection | Lifetime | Cache hit | Expiry or eviction |
| --- | --- | --- | --- |
| Live card | Remaining authorization lifetime, `expires_at - now`. | Does not extend authorization. | Read durable current revision; re-cache only when active and unexpired. |
| Grantor card index | No fixed whole-key TTL; each sorted-set member is scored by `expires_at`. | Prune expired members. | Rebuild active members from durable current revisions. |
| Updating/revoked marker | Short descriptor-owned safety or negative-cache TTL. | Deny or return temporary unavailability as appropriate. | Resolve durable current state; never infer authority from marker expiry. |

Redis outage is not a reason to bypass this serving and coordination layer with
process memory. Requests return structured unavailability. Durable-storage
outage blocks cache-miss recovery and every mutation; a governed request whose
validated card and active catalog are already hot in Redis does not perform a
durable read.

### Record fields

| Field | Meaning | Public list response |
| --- | --- | --- |
| `access_id` | Stable id of this card. | yes |
| `label`, `client_id` | Human name and delegated caller identity. | yes |
| `grantor_subject`, `delegate_subject` | User who granted and integration principal that acts. | yes; list is also owner-scoped to the authenticated grantor. |
| `operations` | Allowed outer API/MCP operation names derived at issuance or save. | yes |
| `resource_grants` | Exact selected KDCube claims per resource. | yes |
| `named_service_operations` | The card's selection: `"*"`, `{}`, or an exact resource -> namespace -> operation map. | yes, verbatim, including `{}` and `"*"` |
| `named_services` | Materialized boundary tree derived from the descriptor and `named_service_operations`. The proc-side bridge consumes it. | no; its expansion surfaces as `effective_named_service_operations` |
| `effective_named_service_operations` | The selection expanded under the catalog version the card was saved against. Derived, never authority. | yes when the card covers any operation |
| `catalog_version`, `card_revision` | The catalog generation this card was last saved against, and its monotonic revision. | yes |
| `account_scope` | Provider -> account -> exact connected-account claims this caller may use. | yes when non-empty |
| `identity_scope` | Which identity boundary the delegated resource uses. | yes |
| `created_at`, `expires_at`, `last_issued_at` | Lifecycle timestamps. | yes when present |
| `last_four`, `source` | Token fingerprint and card family. | yes |
| `session_id`, `access_token`, `refresh_token` | Internal credential/revocation handles, according to source. | no |

Every authority and lifecycle field above is copied into the immutable durable
revision except `session_id`, `access_token`, and `refresh_token`. Those three
live only in dedicated TTL-managed stores, separate from the reconstructable
card-authority projection.

`to_public_dict()` removes all token material, `session_id`, and the internal
`named_services` boundary. A public card exposes the selection, never the
provider credential or the materialized enforcement tree.

The selection is retained verbatim, so a refetch preserves the difference
between the four states:

| Stored | Meaning |
| --- | --- |
| `"*"` | Every named-service operation present in the referenced `catalog_version`. Operations added later are not included. |
| `{}` | No named-service operation. |
| exact map | That resource -> namespace -> operation selection. |
| field absent | A record written before this encoding. Its prior set is derived from the materialized boundary. |

Connected provider credentials are stored by the delegated-to-KDCube account
system, not in these cards. `account_scope` contains ids and claims only.

## What List And Rendering Read

### Configuration vocabulary

The two top-level collections under
`connections.delegated_credentials.oauth` answer different questions:

| Descriptor node | Question it answers | Card effect |
| --- | --- | --- |
| `capabilities[]` | **Which grant tokens exist, and may this signed-in user delegate each one?** Each row defines a `grant`, its display metadata, delegation roles/permissions, and optional connected-account requirements. | The current user's authority is evaluated against these definitions to produce `grant_options`. A capability is primarily vocabulary and a delegation rule; it is not itself an endpoint or callable operation. |
| `resources[]` | **Which protected doors exist, and what may be reached through each door?** Each row defines a resource URL/pattern, its grants, outer tools, optional named-service boundary, identity scope, and admin restriction. | The live resource catalog supplies the selectable doors and the claims, outer tools, namespaces, and inner operations beneath them. Selected claims persist in `resource_grants`. |

`resources[].grants` is the claim ceiling shown for that door. When it is not
written explicitly, the parser derives it from the grants required by the
resource's outer tools and nested named-service operations. Every grant token
used there should have a matching `capabilities[]` definition so Connection
Hub can decide whether the current user may delegate it and provide its label.
`capabilities[].tools` remains a compatibility/fallback source of outer tool
metadata when no resource-specific tool catalog matches; current Connection
Hub descriptors define protected operations under `resources[].tools`.

There are also two different operation layers:

| Layer | Canonical descriptor shape | Example | List/card representation |
| --- | --- | --- | --- |
| Outer surface operation | `resources[].tools.<name>` | `named_services_schema` | The list response calls these `resources[].operations`; the card stores allowed names in `operations`. They are API/MCP entry operations at the protected resource. The parser also accepts `operations`, `allowed_tools`, and `actions` as input aliases, but `tools` is the canonical descriptor spelling. |
| Inner named-service operation | `resources[].named_services.namespaces.<namespace>.tools.<tool>.operation`, or the nested `<tool>.operations.<operation>` map | namespace `linkedin`, operation `object.schema` or `object.action.publish_post` | The exact user selection is stored in `named_service_operations`; the derived bridge policy is stored internally in `named_services`. These are the ontologic operations inside the named-services door. |

The word `capabilities` can also occur as a named-service tool key, for
example `tools.capabilities.operation: provider.capabilities`. That is merely
an inner callable operation. It is unrelated to the top-level
`oauth.capabilities[]` grant vocabulary.

### Live-services source fusion

The card editor does not obtain one preassembled "live services" object from
one store. Connection Hub joins configured ceilings, current user authority,
live provider requirements, connected-account state, and any stored card
selection:

```text
CONFIGURED DELEGATION CEILING                         CURRENT USER FACTS
connections.delegated_credentials.oauth
|
+-- capabilities[]                                   signed-in roles/permissions
|     grant + label                                             |
|     delegable_roles/permissions                               v
|     optional connected_accounts -----------> PlatformAuthorityInventoryProvider
|                                                          |
|                                                          +--> grant_options[]
|                                                               grants this user
|                                                               may delegate now
|
+-- resources[] ------------------------------------------> resources[] (doors)
      resource URL/pattern
      label / identity_scope / admin_only
      grants[] -------------------------------------------> claim rows
      tools{} --------------------------------------------> outer operation rows
        named_services_schema                                 |
          grants: [named_services:use]                        +--> card.operations
      named_services
        namespaces
          linkedin
            tools
              schema.operation: object.schema --------+
              call.operations:                        |
                object.action.publish_post -----------+--> NamedServiceBoundaryCatalog
                                                            |
                                                            +--> namespace/operation rows
                                                                 |
                                                                 +--> selected exact subset
                                                                      card.named_service_operations
                                                                      card.named_services
                                                                      (derived internal tree)

LIVE NAMED-SERVICE PROVIDER DISCOVERY
provider spec.metadata.connected_accounts ----------------> requirement annotations
  provider_id / connector_app_id / claims                    for descriptor namespaces
  claims_by_operation                                        only; adds no operation
                                                             and grants no authority

DELEGATED-TO-KDCUBE CONFIG + ACCOUNT STORE
provider and connector-app labels -------------------------> account-picker vocabulary
signed-in user's connected account rows -------------------> accounts, status, held claims
                                                                 |
                                                                 +--> selected exact binding
                                                                      card.account_scope

EXISTING CARD items[] --------------------------------------> seeds edit selections
PENDING DEMAND / DEEP LINK ---------------------------------> focuses requested rows only
                                                             (never grants by itself)
```

The namespace and operation tree is descriptor-owned. Specifically,
`NamedServiceBoundaryCatalog` projects only
`resources[].named_services.namespaces`; provider discovery does not invent a
namespace, add an operation, or make an operation callable. It contributes
the live provider's `metadata.connected_accounts` requirements, including
operation-specific provider claims such as `claims_by_operation`, so the UI
can explain which connected account is needed for a selected operation.

Those requirements are not impossible to express in configuration. They are
currently provider-owned runtime metadata because they describe what the
registered provider implementation needs, and sourcing them there avoids
copying the same provider contract into every delegated-resource descriptor.
This is distinct from `capabilities[].connected_accounts`, which is a
descriptor-owned mapping from a door grant such as `mail:read` to the provider
claim that satisfies that grant. If provider discovery is unavailable, the
descriptor namespace/operation rows still render, but the account-requirement
guidance is absent; no authority is broadened.

The delegated-to-KDCube catalog then contributes provider and connector-app
labels plus the signed-in user's current account rows, held claims, and
connection status. It powers account selection and connect/reconnect/approve
guidance. Provider credentials never enter the card or its list response.

### Projection into a saved card

At create or edit time, the server combines the selections with the current
catalogs in this order:

```text
selected door + claims
  -> resource exists in current resources[]
  -> every claim belongs to that door
  -> every claim is currently delegable by this user via capabilities[]

selected namespace operations
  -> namespace and operation exist in that door's NamedServiceBoundaryCatalog
  -> operation-required grants are present in the selected door claims

selected accounts
  -> normalize explicit provider -> account -> provider-claim binding
  -> live broker later verifies the account and held claim on provider use

successful projection
  -> resource_grants             exact selected door claims
  -> operations                  eligible outer surface tool names
  -> named_service_operations    exact selected inner operations
  -> named_services              narrowed descriptor tree for the bridge
  -> account_scope               exact connected-account binding
```

Resource, grant, and named-operation membership are validated during this
projection. `account_scope` is shape-normalized here; current account
existence and provider-claim possession are enforced by the connected-account
broker when the provider operation is attempted.

The manual create form sends an explicit namespace map for every selected
resource, including `{}` when no named-service operation was checked. Its UI
therefore starts named-service access closed. At the service API level,
omitting `named_service_operations` still has legacy/full-policy semantics;
that distinction, and the current failure to persist explicit-empty durably,
are described under edit semantics below.

`delegated_access_list` is a composite response. Its three top-level parts
come from different sources:

| Response field | Source | Purpose |
| --- | --- | --- |
| `items` | Persisted `AutomationAccessRecord` rows for the authenticated grantor. | What the user already granted. |
| `grant_options` | Live `PlatformAuthorityInventoryProvider` over configured capabilities and the current user's delegable authority. | Claims the user may grant now, with labels and descriptions. |
| `resources` | Live `connections.delegated_credentials.oauth.resources` descriptor parsed by `OAuthDelegatedClientConfig`. | Resources, top-level tools, grants, admin restrictions, and named-service catalogs available now. |

For each resource with a `named_services` block, Connection Hub projects the
current namespace and operation tree through `NamedServiceBoundaryCatalog`.
It enriches namespace rows with connected-account prerequisites from live
named-service provider discovery. The separate delegated-to-KDCube catalog
supplies the user's currently connected accounts and their claims to the
account picker.

This produces two deliberately different views:

### Read-only card

- Doors and access claims come from stored `resource_grants`.
- The Services row comes from stored `named_service_operations`.
- Account bindings come from stored `account_scope`.
- Current catalogs provide friendly labels when a match still exists.
- A missing connected account is displayed as a stale binding; no provider
  token is fetched merely to render the card.

### Create or edit form

- Available resources, claims, namespaces, and operations come from the live
  descriptor-backed catalogs.
- Check states begin from the stored card selection during edit.
- Claim editing unions the card's stored claims with the current resource claim
  catalog, so a removed stored claim remains visible and can be unchecked.
- Named-service operation rows come from the live catalog, ticked from the
  card's coverage (`effective_named_service_operations`) intersected with what
  the catalog still offers. An operation the catalog added after the card was
  saved appears unchecked.
- The picker is offered for every card family.
- Account rows come from current connected accounts. A disconnected account is
  visible in read-only mode but is not seeded into the edit picker.

The direct answer to "where does the Services list come from?" is therefore:

```text
read-only Services row    -> card.effective_named_service_operations (coverage)
create/edit Services rows -> resources[].named_services (live descriptor catalog)
edit check states         -> coverage ∩ what the catalog still offers
provider prerequisites    -> live named-service discovery metadata
account choices           -> live delegated-to-KDCube account catalog
```

## Creation Lifecycle

### Manual automation

```text
authenticated user opens create form
  -> list returns live resources and grant choices
  -> user selects resource grants, namespace operations, and account scope
  -> create validates every selection against current config and authority
  -> selected named-service operations narrow the descriptor policy
  -> bearer and access-grant binding are minted with registry_access_id
  -> card record is stored and indexed
  -> raw bearer is returned once
```

### Hosted agent

```text
agent attempts a governed operation
  -> denial names the deterministic kdcube-agent:<app>:<agent> client, the
     namespace and the operation it was refused
  -> user approves the demand in Connection Hub, with that operation pre-selected
  -> create_access deduplicates by grantor + client + resources
  -> approval merges exactly the approved authority
  -> explicit edit uses replace semantics
  -> reusable agent bearer and card are stored server-side
```

### OAuth/MCP client

```text
external client completes OAuth consent
  -> consent submits its contract version, the catalog version it was rendered
     from, and the operation selection
  -> authorization code carries that selection
  -> token endpoint issues access/refresh material
  -> record_oauth_grant writes one card per grantor + client + resource, born
     with the submitted selection and the catalog version
  -> refresh rotation updates token handles, expiry and last_issued_at only
  -> user edits or revokes the same visible card later
```

A reconnecting dynamic client may receive a new client id. Connection Hub can
supersede a matching older card, carry its account binding forward, and revoke
the old token handles.

### Consent view model

Connection Hub builds one view model per authorize request and hands it to
whichever renderer serves the page — the built-in one or a bundle-hosted
`consent_ui`. A custom renderer changes presentation, never the authorization
contract.

```text
consent_contract.version
catalog_version
platform_grants
tools                       (outer operations)
named_service_operations    (namespace, operation, required claims)
connected_accounts
seeded_account_scope
request / oauth_request     (client and PKCE metadata)
```

A renderer returns the contract version it implements alongside its HTML. An
absent or mismatched version is refused with `consent_ui_contract_mismatch`
before the page is shown, so a page that never rendered a dimension cannot
report a choice about it.

The submitted form carries `consent_contract_version`,
`expected_catalog_version`, and the operation selection:

| Submitted | Result |
| --- | --- |
| Every offered operation selected | `"*"`, bound to the catalog version shown on the page. |
| Some operations selected | That exact selection, re-validated against the catalog. |
| None selected | `{}`. The connection is created without named-service access and can be widened later in Connection Hub. |
| `consent_contract_version` absent | No authorization code and no card. |
| `expected_catalog_version` no longer active | `consent_catalog_changed`; authorization restarts rather than reinterpreting the selection. |

## Edit Semantics

The card's selected authority is changed in place. No new manual token is
issued, and a pointer-backed caller observes the change on its next request.

Authority is changed by the grantor, under their KDCube identity, wherever the
edit was initiated from. A caller authenticated as the delegate
(`integration:<client>:<grantor>`) is refused with
`delegated_access_requires_grantor`.

### Any card: `delegated_access_update`

One source-neutral edit serves all three families. The card keeps its
`access_id` and its credential material — an agent's reusable bearer, an OAuth
client's token handles, a manual card's client-side token — and the new
authority applies on the caller's next request, with no re-mint and no
re-authorization.

`delegated_access_update` validates and rewrites the record with this contract:

| Input | Meaning |
| --- | --- |
| `resource_grants` | Required full replacement. No remaining grant means revoke, not update. |
| `named_service_operations` omitted | Preserve the stored narrowing. |
| `named_service_operations: {}` | Disable all named-service operations for the selected resources. |
| `named_service_operations: "*"` | Select every named-service operation in the current catalog; the stored wildcard is bound to the catalog version saved with the card. |
| `named_service_operations` with content | Replace with that exact resource -> namespace -> operation selection. |
| `account_scope` omitted | Preserve the current account binding. |
| `account_scope: {}` | Bind no provider account; provider-backed use is default-closed. |
| `account_scope` with content | Replace with the exact provider -> account -> claim selection. |
| `label` | Rename without changing omitted dimensions. |

The update recomputes outer operations and the materialized `named_services`
tree from the active catalog available to Connection Hub. For a stored `"*"`,
that materialized tree contains the exact operation set present at Save time;
governed execution can therefore intersect the card with current
`active.json.connections` without loading catalog history.

The persisted `named_service_operations` field carries the complete policy:

| Stored value | Meaning |
| --- | --- |
| `"*"` | Permit every named-service operation reachable through the card's selected resources in its referenced `catalog_version`. Later additions are not included. |
| `{}` | Permit no named-service operation. |
| Resource/namespace/operation map | Permit exactly the named entries. |

The list response retains both `"*"` and `{}`. New records always persist one of
the three forms.

Omission means different things per direction:

| Direction | Omitted `named_service_operations` |
| --- | --- |
| create | `{}`. Claims do not select operations; `"*"` is stored only when the user chose every operation the current catalog offers. |
| update | Preserve the card's own policy. A preserved `"*"` is written out as the exact set it already meant, so an unrelated save cannot re-pin it to a newer catalog. |

A **pre-migration card record** is an ordinary card written before
`catalog_version`, `card_revision`, and this encoding were introduced. Its prior
set is derived from the persisted materialized `named_services` boundary; GET
does not mutate it. Where that derivation would change authority silently, the
server reports `migration_confirmation_required` and refuses a save that carries
no explicit selection:

| Condition | Why it is not derivable |
| --- | --- |
| The boundary names no operation. | Deriving yields `{}`, indistinguishable from a deliberate empty selection. |
| The derived set is empty against the active catalog. | The migration would revoke without an operator decision. |
| The card holds more than one named-service resource. | The boundary is stored as one merged tree; per-resource attribution is not recoverable. |

When an existing `"*"` card is saved after catalog drift without an explicit
new wildcard choice, the backend expands the wildcard against the saved
catalog version and persists the surviving exact set before advancing
`catalog_version`. An explicitly submitted `"*"` selects all operations shown
from the current catalog and binds that wildcard to the new version.

### Agent card

An agent card has a second entrance: the consent demand. It MERGES exactly the
capability that was refused — the namespace and operation the demand names, with
the claims and account binding they need — into the card's existing selection.
The card's own side is frozen into what it already meant before the merge, so a
one-click approval never re-pins a `"*"` to a newer catalog.

`delegated_agent_grant_create` merges by default, so separate approved demands
accumulate. With `replace: true` the submitted resource claims replace that
resource's selection; omitted `account_scope` and `named_service_operations`
preserve their stored values, and explicit empty maps clear those dimensions.

### OAuth card

`extend_client_access` merges a one-click extension or replaces one resource's
claims, and merges or replaces `account_scope`. It resolves authority through
the same path as every other save, so it also rewrites
`named_service_operations`, the materialized boundary, and the catalog stamp.

Re-authorization is not required to change an OAuth card: the edit rewrites the
live card and the existing pointer-backed bearer sees the new authority on its
next call. A refresh rotation updates credential handles, expiry, and
`last_issued_at` only — it neither restores an older operation selection nor
widens one.

## Runtime Enforcement Lifecycle

Pointer-backed credentials carry `registry_access_id` in the access-grant
binding. The managed guard treats that id as a pointer, not as authority by
itself:

```text
request bearer
  -> authenticate bundle/OAuth token
  -> load access-grant binding
  -> read registry_access_id
  -> resolve card from delegated-access store
  -> verify expected client, grantor, delegate, expiry, and record shape
  -> copy current card facts into the request-local grant
       resource_grants
       flattened grants/scopes
       operations
       account_scope
       named_services materialized boundary, always set
  -> enforce outer resource/tool gate
  -> enforce named-service namespace/operation boundary
  -> resolve a permitted connected account and its claims, when required
  -> call provider
```

Missing, expired, malformed, unavailable, or identity-mismatched pointer
authority fails closed. Revocation deletes the card and invalidates the
source-specific session/token records, so an in-flight bearer cannot recover
authority from its older embedded snapshot.

Legacy bindings without `registry_access_id` retain embedded-snapshot semantics
for the card's own facts; the active-catalog intersection still applies to them.

The named-service boundary is always carried, including when it is empty. An
absent boundary would leave the descriptor as the only ceiling, so a card that
materialized nothing permits nothing.

## Descriptor And Catalog Drift

Stored consent and current deployment policy are different facts:

```text
stored selection       = what the user approved
current catalog        = what the deployment offers now
effective authority    = never more than both permit
```

### Behavior on a descriptor change

| Descriptor change | Card/list behavior | Runtime behavior |
| --- | --- | --- |
| A claim or operation is added | It appears in the live create/edit catalog, unchecked. Existing cards do not select it automatically. | Existing selections do not gain it; a card holding `"*"` is bound to the catalog version it was saved against. |
| A stored top-level claim is removed | Drift marks it removed; the claim editor still renders it so it can be unticked, and Save prunes it. | The governed call intersects the card with the active catalog and denies it. |
| A stored named-service operation is removed | Drift marks it removed and states that it is already ineffective. | Denied immediately, even while provider code still implements the operation. |
| A whole stored resource is removed | Drift marks the resource removed; Save prunes it, and a card left with no authority is revoked rather than kept. | Denied immediately. |

Two mechanisms hold that together:

1. **Enforcement clamps immediately.** A governed call reads complete cached
   `active.json` from Redis and intersects its embedded `connections` mapping
   with the card. A Redis TTL miss reads through to the committed durable
   `active.json` and repopulates Redis with the configured fixed TTL. It returns
   `503 temporarily_unavailable` when that document cannot be obtained or its
   `content_hash` does not match its embedded mapping.
2. **The editor explains and repairs stored drift.** List and edit return a
   server-computed drift projection. The UI shows **Service access changed since
   this grant was last saved**, with details: removed selections are already
   ineffective and are removed on save; newly available choices stay unchecked
   until explicitly granted.

Save reconciles in this order:

```text
stored selection
  -> retain stale entries long enough to explain them
  -> prune entries absent from the current catalog
  -> validate every remaining selection strictly
  -> persist the reconciled explicit selection
  -> rebuild the materialized boundary
  -> stamp the active catalog_version
  -> clear the warning
```

GET must not silently rewrite the user's record. Enforcement may narrow
immediately, while the stale stored value remains available as evidence until
the user saves or revokes.

### Catalog ownership and versioning

Connection Hub owns one central history of the delegable catalog. A card never
stores a catalog copy. It stores only `catalog_version`, referring to the
immutable catalog version active when that card was created or last saved.

The catalog source is the existing effective, non-secret `connections` mapping
from Connection Hub bundle props:

```python
connections = copy.deepcopy(entrypoint.bundle_props.get("connections") or {})
```

Each immutable version document stores that parsed mapping under its
`connections` field in the existing shape. The surrounding object adds only
`version`, `content_hash`, and `created_at`. It is not normalized, flattened,
or enriched. Existing configuration readers remain responsible for
interpreting OAuth capabilities and resources, outer operations, named-service
boundaries, and delegated-to-KDCube configuration. Current provider discovery,
user accounts, roles, held provider claims, and connection status remain live
rendering and enforcement inputs; they are not copied into catalog history.

The shared store contains immutable versions and one self-contained active
document:

```text
delegated-catalog/v1/
  versions/<version>.json   immutable catalog document
  active.json               complete active catalog document
```

Both document forms contain:

```json
{
  "version": "delegated_catalog_2026-08-11-10-30-00-123_d4e5f6a7b8c9",
  "content_hash": "<full lowercase SHA-256>",
  "created_at": "2026-08-11T10:30:00.123Z",
  "connections": {"...": "exact effective props mapping"}
}
```

Canonical JSON is used only to calculate `content_hash` from `connections` for
change detection, deduplication, and integrity validation. It does not create a
second catalog shape. Publication copies current `connections`, enters a shared
critical section, rereads and rehashes the mapping inside that section, writes
the immutable version, and atomically replaces complete `active.json`. The
version name combines a sortable UTC timestamp with a content-hash suffix.

Durable storage and request serving have one clear boundary:

```text
durable Connection Hub bundle storage
  versions/<version>.json + complete active.json
  authoritative history and recovery source
                         |
                         v
Redis serving projection
  delegated card:<access_id>             card + catalog_version
  delegated catalog:active               complete active.json, with TTL
  delegated catalog:version:<version>    one key per historical version, with TTL
```

There is no process catalog cache. `on_app_deploy` already owns the effective
`connections` object in memory. It writes the durable version and complete
`active.json`, caches the immutable version in Redis, and atomically caches the
complete active document. A matching durable version is not enough to declare
deployment ready: the Redis active document must also be present. If a cache
entry later expires, the relevant request uses the validated durable
read-through and stores it again with the configured TTL.

Many immutable `catalog:version:<version>` keys can coexist. Every write or
read-through restoration assigns the configured historical-catalog cache TTL;
a cache hit does not extend it. When an entry expires, the next list/open that
needs that exact version validates its durable version document and caches it
again. Catalog-cache lifetime is independent of card lifetime.

The active catalog uses the same fixed-residency rule: publication or validated
read-through assigns its descriptor-owned TTL, while ordinary request hits do
not extend it. Catalog TTL expiry is cache eviction, not a catalog change. A
new `on_app_deploy` publication atomically replaces `catalog:active` and leaves
all immutable historical keys untouched.

Connection Hub publishes through the fleet-coordinated `on_app_deploy`
readiness barrier. That barrier reconciles every deployment-scoped resource for
the current source/effective-props generation, including app-owned catalog and
index builders plus the platform-owned deployed UI inventory and artifacts. It
commits the app generation only after every required resource is ready. Each
resource family has its own signature, so repeated deployment reuses unchanged
artifacts while props can still attach, remove, or reconfigure UI components.
There is one deployed widget-delivery contract and no delivery-mode branch.

The lifecycle rule is independent of resource type: `on_app_deploy` ensures
that all resources of the app generation are ready. App hooks and platform
reconcilers are implementation participants beneath that single contract.

A props-update event may separately call `on_props_changed` on a cached
instance for process-local reconciliation; `on_bundle_load` remains
per-process initialization and does not publish catalog state. The same event
also invokes coordinated `on_app_deploy` regardless of singleton mode.

Card list/create/update and governed-operation paths never publish or modify
durable catalog history. They may restore an expired Redis cache entry from an already
committed document. They consume the registered active catalog represented by
cached `active.json`.
Effective props participate only in `on_app_deploy` alignment; they are not a
request-time authorization or drift input.

Every card and governed request reads the live card and TTL-cached
complete `active.json` from Redis. Governed execution validates
`content_hash == hash(connections)` and computes
`intersect(active.json.connections, card)`. It does not load another active
body, inspect effective props, or consult catalog history.

If cached `active.json` expired, the request may read complete committed
durable `active.json`, validate it, and atomically cache that complete document
with a TTL. This restores the Redis serving projection; it does not create a
version or change durable catalog history. The Redis update refuses to replace
a newer sortable catalog version with an older delayed restoration.

That no-downgrade comparison protects only the single Redis `catalog:active`
key. It does not compare a card's saved version with the active version.
Governed execution never loads the card's historical catalog; list/open uses
the separate `catalog:version:<card.catalog_version>` key only for drift.

List/open computes `diff(active.json, card)`. When it must distinguish a newly
added option from one that already existed but was left unselected, it reads
the historical Redis document named by `card.catalog_version`. If that cache
entry expired, it reads exactly the corresponding durable
`versions/<card.catalog_version>.json` and repopulates Redis. This historical
read is only for drift explanation; governed execution uses the exact
materialized authority already stored in the card.

The historical key is shared by every card on that version and its TTL is
fixed when the key is populated. A miss performs the durable read-through and
repopulates the key with a fresh configured TTL; requests after repopulation
reuse it until the next expiry. Multiple historical versions remain cached
under distinct keys.

Failure to obtain or validate a required active or historical document returns
structured unavailability. No coroutine or lock exists per catalog entry; the
async request simply awaits Redis and, on TTL miss, durable storage.

To say that an operation is **new since the last grant**, list compares the
card's referenced immutable version with complete `active.json`. Comparing only
the current catalog with selected operations can find removed selected values,
but cannot distinguish a newly added operation from one that was already
available and deliberately left unchecked.

### Drift projection and Save concurrency

`delegated_access_list` computes drift on the server and returns one status per
card:

| Status | Meaning |
| --- | --- |
| `current` | The card references the active catalog version. |
| `changed` | A relevant resource, claim, outer operation, or named operation changed. |
| `no_relevant_change` | The global catalog advanced without changing anything represented by this card. |
| `baseline_missing` | The card predates `catalog_version`, or its referenced durable version document is confirmed absent, so exact additions since the previous Save cannot be identified. |
| `unavailable` | Complete cached `active.json`, or a historical version required for drift, cannot be obtained from Redis or restored from committed durable state; or a catalog document fails content-hash validation. Editing authority is disabled; create/update and governed calls return `503 temporarily_unavailable` when the unavailable document is required for their decision. |

The drift object contains ready-to-render `removed` and `added` entries.
Removed selected entries stay visible as disabled/stale rows and are already
ineffective. Added entries are current options but remain unchecked. Current
provider/account requirements are rendered from live discovery instead of
catalog history.

An edit submits `expected_card_revision` and
`expected_catalog_version`. Save returns `409` with a refreshed projection
if either the card or catalog changed after the editor loaded. Otherwise the
server prunes stale selections, validates every survivor against the active
catalog, rebuilds `operations` and `named_services`, increments
`card_revision`, and stamps the active `catalog_version` atomically.

Runtime applies the same ceiling before Save:

```text
effective resource claims       = stored claims intersect active claims
effective outer operations      = stored operations intersect active operations
effective named operations      = stored selections intersect active namespace operations
effective account-backed access = stored account_scope checked against current requirements
```

A governed request returns different structured outcomes for policy change and
catalog failure:

| Condition | Response |
| --- | --- |
| Current `active.json` cannot be obtained or validated. | HTTP `503`, `temporarily_unavailable`, retryable after shared-state recovery. |
| The requested capability is present in the card's exact stored authority but absent from current `active.json.connections`. | HTTP `403`, `delegated_capability_no_longer_available`, non-retryable until discovery, card, or service configuration changes. |
| The capability is current but absent from the card. | HTTP `403` using the existing missing-grant/consent denial. |

The removed-capability response names the failed dimension, resource,
namespace/operation where applicable, `card_catalog_version`,
`active_catalog_version`, and a recovery action to refresh discovery or review
delegated access. It does not emit a consent action because additional user
consent cannot restore a capability removed by current service configuration.

Its structured `requested_capability` object contains the complete path:

| `kind` | Required fields |
| --- | --- |
| `resource` | Configured `resource`; concrete `request_resource` when available. |
| `resource_claim` | `resource`, `claim`; `request_resource` when available. |
| `outer_operation` | `resource`, `surface`, `outer_operation`; `request_resource` when available. |
| `named_service_namespace` | `resource`, `surface`, `namespace`; outer/request fields when applicable. |
| `named_service_operation` | `resource`, `surface`, `namespace`, `operation`; outer/request fields when applicable. |

`resource` is the matched card/catalog selector. `request_resource` is the
concrete URL or resource identifier supplied by the transport. Both are
returned when wildcard matching was involved.

The response also carries `reason`, opaque `access_id`, `card_revision`, both
catalog version ids, and structured recovery with `retry_same_request: false`.
An operation name alone is insufficient because the same operation can exist
under multiple resources or namespaces.

Those fields come from three request-time inputs:

| Field | Source |
| --- | --- |
| Card ids/revision/saved catalog version | The resolved live card. |
| Active catalog version and current resource policy | Complete validated `active.json`. |
| Configured resource selector | Canonical matching across card and active catalog. |
| Concrete request resource and outer operation | REST/MCP request target and operation dispatch. |
| Named-service namespace and inner operation | Parsed `NamedServiceRequest`, before provider invocation. |
| Claim | The exact current resource/operation policy check that failed. |

The managed REST/MCP guard checks resource, claims, and outer operation. The
inner resource/namespace/operation check runs after request decoding and before
provider selection, at each entrance a delegated call can take:

| Entrance | Where the inner check runs |
| --- | --- |
| MCP door | `kdcube-services@1-0` named-services bridge, on the parsed `NamedServiceRequest`. |
| Native agent turn | The per-agent gate, on the namespace and operation the tool call names. |

Neither infers namespace or inner operation by parsing a tool name.

No historical catalog body is required on this request path. Exact membership
in the card proves the capability belonged to its stored selection; absence
from complete current `active.json.connections` proves it is no longer exposed.
The version ids provide provenance. Historical catalog content is loaded only
for list/open drift details.

The clamp applies to pointer-backed card records and to older managed grant
bindings still accepted by compatibility paths. The generic guard consumes a
catalog-resolver interface and does not know which storage technology or bundle
produced the current projection.

There is no delegated-card selector meaning "all current and future operations
in this namespace." New operations therefore remain ungranted. Platform
administrator authority is a separate concept and must not be inferred as a
future-operation wildcard on a granular card.

Immutable card revisions are required for durable authority and provenance.
They do not expose rollback: restoring old authority would be a new explicit
grant operation, not a pointer move to a historical revision.

## Revocation And Expiry

- Expired cards disappear from the normal active list and cease to resolve.
  Redis removes their live projection, while their durable revisions remain
  available to history/audit views.
- Manual revoke commits a durable `revoked` revision, logs out the bound
  platform session, and removes live access-grant state.
- Agent revoke commits the same durable state and removes its reusable
  server-side bearer authority.
- OAuth revoke commits the durable state and removes current access-grant and
  refresh-token state.
- Every mutation publishes a best-effort
  `connection_hub.delegated_access.changed` event so open Connection Hub
  widgets refetch the authoritative list.

## Implementation Map

| Concern | Implementation |
| --- | --- |
| Capability/resource vocabulary and parser aliases | `kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.config` |
| Card operations and authority orchestration | `kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.automation_access` |
| Authority resolution shared by every save | `automation_access.AutomationAccessService._resolve_card_authority` |
| Durable revisions and current pointer | `...delegated_credentials.cards.store.DelegatedCardStore` over Connection Hub bundle storage |
| Persistence port, TTL live projection and read-through | `...delegated_credentials.cards.persistence`, `.cache`, `.handles`, `.resolver` |
| Stored selection states and card model | `...delegated_credentials.cards.model` |
| Pre-encoding record interpretation | `...delegated_credentials.cards.migration` |
| Durable catalog history and publication | `...delegated_credentials.catalog.store` and `.publisher`, immutable version documents and complete `active.json` |
| Current catalog resolution and read-through recovery | `...delegated_credentials.catalog.resolver` |
| Current-capability denial shaping | `...delegated_credentials.catalog.authorization.authorize_current_capability` and `card_boundary_denial` |
| Save-time drift and reconciliation | `...delegated_credentials.catalog.drift`, `.reconcile` |
| OAuth consent view model and contract | `...delegated_credentials.oauth.consent`, `.http.routes` |
| Named-service strict narrowing and materialization | `...delegated_credentials.named_service_policy` |
| Descriptor namespace/operation projection | `...solutions.named_services_providers.boundary_policy.NamedServiceBoundaryCatalog` |
| Live provider requirement enrichment | `automation_access.AutomationAccessService._named_service_options` plus provider discovery `spec.metadata.connected_accounts` |
| Pointer-backed live card resolution | `...delegated_credentials.live_grant` |
| Managed request projection | `...delegated_credentials.oauth.surface_guard._live_grant_record` |
| Connection Hub operations and descriptor binding | `connection-hub@1-0/entrypoint.py` |
| List/create/edit UI and account-requirement fusion | `connection-hub@1-0/ui/widgets/connections/src/features/delegatedAccess` |
| Connected-account credential resolution | delegated-to-KDCube broker and provider adapters |
