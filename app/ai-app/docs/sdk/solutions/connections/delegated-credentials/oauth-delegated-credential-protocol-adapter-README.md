---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md
title: "OAuth Delegated Credential Protocol Adapter"
summary: "Points from KDCube's OAuth host routes to Connection Hub's canonical delegated-credential protocol contract."
status: active
tags: ["sdk", "connections", "connection-hub", "oauth", "delegated-credentials"]
keywords: ["OAuth2 authorization server", "PKCE", "CIMD", "dynamic client registration", "client metadata", "Connection Hub"]
updated_at: 2026-10-07
see_also:
  - https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/package/oauth-delegated-credential-protocol.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/auth-README.md
---

# OAuth Delegated Credential Protocol Adapter

Connection Hub owns the canonical client resolution, PKCE, authorization, consent,
credential issuance, card lookup, and revocation contract. Read
[OAuth Delegated Credential Protocol](https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/package/oauth-delegated-credential-protocol.md).

KDCube remains the first protocol host. Its authentication, descriptor, and MCP
recipes describe how the Connection Hub state machine is mounted on KDCube public
operations and guarded application surfaces.

Portable callers import consent and protocol policy from
`connection_hub.delegated_credentials.oauth`. The KDCube module
`kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.consent`
is a compatibility alias for split-era consumers; both paths resolve to the same
canonical module object.

The KDCube DCR host accepts the bounded public registration metadata defined by
that canonical contract, persists it with the client snapshot, and passes it to
the resulting Card. The fields are owner-facing identification only; KDCube
does not project them into caller identity or policy.

## Human consent admission

The public host helper
`oauth.card_labels.oauth_card_label(client_metadata, resource=..., explicit=...)`
derives the same owner-visible Card label used by consent and issuance. Its
companion `consent_label` preserves a person-chosen label on reconnect. Hosting
factories import this owning derivation to keep naming consistent with the
normal consent path.

The KDCube host admits verified human platform sessions to authorization,
device verification, consent draft, consent decision, and consent submission.
An authenticated external-client principal whose verified identity starts with
`integration:` receives HTTP 403 with `oauth_human_consent_required` at their
shared admission boundary, before any consent draft is read or consumed, device
request approved, authorization code created, or credential minted. Both
Card-bound and unbound integration credentials receive that refusal.

This check uses the authenticator's subject fields (`sub`, `user_id`, `id`).
Display names and caller-supplied request data do not classify a principal.
Ordinary human login and delegated service authentication retain their existing
contracts; a client asks its human operator to complete authorization in their
own session. The host's broader platform-login policy remains a separate
boundary.

### Consent form Referer recovery

The host can recover missing hidden authorization fields from an authorize-page
Referer at the authorization server's public origin. It uses the existing issuer
resolution order: the request-local bundle mount's issuer, the configured app
issuer, then the ASGI request origin for an unconfigured local/dev run. A
configured public issuer supports consent through a proxy whose internal request
origin differs. Origin comparison includes scheme, hostname and effective port.

The recovery helper treats request `X-Forwarded-Host` and `X-Forwarded-Proto` as
untrusted input; they cannot supply its expected origin. A foreign Referer
supplies no fields, and the ordinary authorization validation refuses a form
missing its required PKCE challenge. Complete forms retain the existing subject-
bound, single-use CSRF and client-metadata checks. The deployment's issuer and
trusted-proxy configuration remain host-owned.

## Late Card refusal cleanup

A known `CardConflict` or `CallerWriteRefused` from the Hub after minting
causes the host to withhold the response and attempt revocation of both the
portable access grant and refresh family. This applies to generic refusals,
stale-revision replacement conflicts, and the caller-writer gate's explicit
before-effect refusals. Its optional outcome-confirmation flag describes
policy finalization; it does not grant issuance. Each revocation is attempted independently, so a
failure of one does not prevent the other. Cleanup failures retain the original
refusal and log only the credential kind and client identity, without bearer or
exception text. A failed backing-store call is not proof of revocation.

This cleanup operates on delegated grant bindings and refresh families. It
does not change ordinary platform-login authentication or claim to revoke the
underlying platform session. Initial refresh-family cap/revision stamping and
uncertain Card-commit recovery remain separate issuance boundaries.

## Planned original access preparation

The SDK's `prepare_delegated_client_access_token` adapter accepts only a
host-authenticated original Hub `OAuthIssuancePlan` and the host's first captured
absolute access expiry. It derives the integration subject and grant permissions
from that plan, resolves the session authority in the plan's tenant/project, and
calls `prepare_bound_session`. The original remains inactive; the return value
contains only its immutable context and opaque receipt, not bearer material.
Retries must retain the captured expiry rather than recomputing a fresh TTL.

The host can retain that instant in its existing validated exchange ledger with
`PostgresOriginalExchangeStore.capture_access_expiry(proof, ttl_seconds=3600)`.
After the original plan is pinned, the first call captures PostgreSQL's current
clock under the exchange row lock and caps the access expiry by the original
Card deadline. Subsequent calls return the same stored expiry, including after
a new process starts; changing the original TTL refuses. The explicit integer
TTL is bounded by the portable access-token policy, currently one hour. Missing
validation, an unplanned exchange or a different retry proof cannot capture it.

Call this before any access preparation and pass its `access_expires_at` to the
adapter. The original delivery deadline remains separate. Once the access
deadline passes, ledger read, begin, plan pinning and expiry capture refuse
without replacing the mapping, even if the delivery window is still open.
The schema change is additive; existing rows retain their plan and have unknown
access-expiry fields until a new workflow captures them before minting. A NULL
field does not prove that an older credential was never minted and must not be
used to renew a previously minted credential. The ledger grants no authority,
creates no credential and completes no Hub decision.

`activate_prepared_delegated_client_access_token` accepts the host-authenticated
original result only when its access slot is committed and applied, matches the
prepared context, and names the original bearer digest. The public session
authority verifies original custody and activates under its PostgreSQL lock.
The adapter also checks that the returned session, custody reference, and bearer
digest remain the prepared original. Pending, aborted, superseded, mismatched,
unbound, or older-authority cases never fall back to ordinary login/minting.

These typed values are data, not authentication. The hosting app still owns
authenticated plan/result readers, immutable exchange binding, and live-target
fencing. This adapter neither completes the Hub decision nor publishes tokens.
The authorization-code HTTP handler now accepts a hosting app's server-owned
`oauth_original_exchange_factory`, on request state or application state. It
returns an `OriginalCodeExchangeHandler` bound to the same tenant/project and
an async `exchange(proof=..., code=...)` composition. The selected path receives
only the client's code/redirect/PKCE proof; plans, results and provider choices
remain host-owned. Its `OriginalTokenPair` retains the original absolute access
expiry and delivery deadline, publishes both bearers only in a no-store HTTP
response, and omits bearers from its representation. Its type is delivery data,
not authorization evidence.

A configured missing, malformed or unavailable capability fails closed. It
does not fall back to ordinary consume/mint. An absent binding retains the
existing workflow during source adoption. The host composition must connect
the durable original mapping, full authenticated plan/result readers, both
original reservations, live target fence and qualified custody. Those concrete
provider binding, configured encrypted-provider qualification, and installed/live
acceptance remain host integration gates; transport selection alone proves none of them.
Ordinary human login and the existing delegated minter are unchanged.

`read_prepared_delegated_client_access_token` recovers the existing original
access receipt and signing time through the public session authority's
`read_prepared_bound_session`. That reader has no signing or custody dependency
and uses only a stored issuance lookup. It checks the complete original plan,
canonical user/role/permission inputs, delivery/access expiry and stored signing
time. Missing, malformed, terminal or mismatched originals refuse without
preparation, user changes or repair. A committed replay may read after the
preparation-reservation deadline, within the original delivery/access deadline;
the activation and publication fences remain separate.

`OriginalCodeExchangeFlow` provides the host composition's code-to-pair
orchestration. It consumes a validated code once, captures the canonical
candidate-input digest, and asks Hub to begin the original request. Its
`pin_plan(..., access_ttl_seconds=3600)` call stores the complete plan and first
absolute access expiry in one PostgreSQL transaction. Replays preserve both;
an older pinned plan with unknown expiry refuses this workflow.

The consumed client/redirect/PKCE proof is checked before the host's candidate
builder runs. The flow freezes that server payload and normalizes the builder's
argument mapping through `oauth_issuance_arguments`, filling every default in
Hub's public `begin_oauth_issuance` contract before computing the digest. This
keeps a sparse host mapping and Hub's stored original input identical. Unknown
arguments, a caller-selected original request id and ambiguous scope/operation
containers refuse. Grant selection and live authority remain host/Hub checks;
this pure argument normalization performs neither. Source tests compare the
digest with the real Hub method using synthetic persistence ports.

Before a local pin exists, recovery selects Hub's read-only
`read_oauth_issuance_plan_by_request(decision_request_id=...)`. Once pinned,
recovery selects the transaction-id plan reader and compares the complete
authenticated plan with its durable snapshot. A missing request-reader
capability returns a finite unavailable response; unknown or unbound plans
never authorize a replacement begin or mint.

The flow reads Hub's original result before selecting the credential provider.
A committed replay selects `read_pair`, which must recover existing receipts
without preparation. A pending result selects idempotent `prepare_pair`,
reserves both original receipt digests, and completes Hub's existing decision.
When a pending result already names any original slot digest, it instead reads
the existing pair. It compares every held digest before reserving any absent
slot, skips existing reservations and resumes that same decision's completion.
This permits partial FINISH recovery after the decision committed, when a new
reservation would correctly refuse. Missing or changed original issuer metadata
stays closed and never becomes permission to prepare a replacement.

Refresh metadata retains a canonical commitment to its first signing claims and
custody reference. Sealing checks that the supplied originals still match those
stored inputs. Existing rows without this commitment are unknown originals and
refuse recovery; schema migration adds the nullable field without inventing
historical claims or a replacement token. Terminal tombstones may have no claims.

Source recovery gates terminate an issuer process after confirmed refresh
reservation, digest sealing, custody creation and ready-state commit, then
recover with a fresh process against the same PostgreSQL and private fsynced
file service. They also lose confirmed PostgreSQL replies and race ABORT with
an in-flight create. These gates qualify original metadata/reference recovery,
not encryption, production signing keys, deployed mount lifecycle or a running
Hub composition; those require the host's separate provider acceptance.

The flow checks both applied slot digests, re-reads the local pin and invokes the
host's live-target fence before access activation and again after custody
reads, before publishing either bearer. The provider must keep refresh
artifacts distinct from Bundle access sessions. The host still supplies the
concrete candidate builder, authenticated Hub, qualified custody and live fence;
their real deployment qualification remains open.

## Original credential pair provider

`OriginalCredentialPairProvider` composes the public Bundle access authority
with `OriginalRefreshIssuer` and `PostgresOriginalRefreshStore`. Its host-only
constructor binds the tenant/project, exact expected custody namespace,
original Card kind, refresh TTL and protected signing-key resolver. Preparation
first qualifies `KDCubeIssuanceSecretCustody`; receipt recovery reads only
existing access and refresh metadata. The host binds this provider to its
authenticated Hub readers and live-target fence.

The refresh metadata table retains one original signing input, opaque custody
reference and bearer digest. It contains no bearer or signing key. PostgreSQL
row locks preserve the first identifier, signing time and complete plan;
conflicting retries refuse. `HmacOriginalRefreshSigner` signs that original
input with the distinct `kdcube.oauth.original_refresh.v1` purpose and `krt1`
artifact format. The digest is sealed before create-only external custody, so
a lost create response recovers the same artifact rather than a new generation.
A changed key cannot replace an already sealed original.

The artifact's signed expiry and custody deadline retain the original Card cap.
Hub separately computes the active refresh family's expiry from its first
reservation time plus the original refresh TTL, capped by that Card deadline.
The signed cap does not extend the family's usable lifetime or authorize it.
Hub remains the authority that activates the refresh family.

Before Bundle access activation, the provider records the authenticated applied
refresh receipt. That protects the applied original against retirement; it
records the Hub outcome, not recipient delivery. An authenticated aborted flow
retires both original slots, committing their terminal metadata before purging
the exact custody references. Each cleanup is attempted independently.
A committed result containing a superseded slot withholds the whole pair and
retires only the superseded original. An applied refresh slot records its
protection receipt; an applied access slot is preserved without activation or
retirement on this path. The complete original result is checked before either
cleanup, and a missing cleanup capability gives a finite unavailable response.

Source tests exercise PostgreSQL metadata and synthetic signing/custody
capabilities. Qualification of the configured secrets-file, host-vault or AWS
backend, recovery across process termination, host composition and live
recipient delivery remain separate deployment gates. Backend selection stays
behind the configured SecretsService abstraction.

## Refresh lifetime forwarding

When a refresh record has a server-stored Card pointer, the host resolves that
live Card before rotation and passes its revision and nonzero absolute deadline
to the portable grant store. The SQL authority combines that deadline with the
family's stored cap and checks its revision in the locked rotation transaction.
A deadline is not renewed from the current time plus a TTL, and request fields
cannot supply a Card pointer or override these limits. A revoked or expired
Card is refused before rotation; a store refusal produces no access credential.

Records without a stored Card pointer retain their existing refresh behavior
and receive no Card-limit arguments. The Redis fallback bounds the successor's
TTL but has no SQL family revision fence. This host forwarding requires a
portable grant store that accepts `expires_at_cap` and `card_incarnation`.

Before consuming a Card-bound refresh token, the host checks that both the
portable facade and any configured SQL authority expose those two keyword
parameters. An older, uninspectable, or generic `**kwargs` API is not qualified:
the host returns a retryable HTTP 503 (`temporarily_unavailable`, `Retry-After:
30`) without rotating the token or minting an access credential. There is no
fallback that drops the Card limits. Transparent wrappers must preserve the
underlying signature, for example with `functools.wraps`.

A Card revision change between the host's read and locked rotation raises the
portable `RefreshCardIncarnationMoved` refusal before any generation is
consumed. The host returns HTTP 503 (`temporarily_unavailable`, `Retry-After:
30`); the client retries with its same refresh token, and the next request
resolves the live Card again. The original family's absolute cap is retained.
Terminal expiry or revocation remains `invalid_grant`. The host logs a fixed
reason without disclosing a bearer or exception detail, and performs no
additional old-generation lookup to classify this refusal.

Card-bound refresh also requires the portable package's named refusal type.
A package without it is refused before rotation; unbound refresh retains its
existing behavior. Qualification requires Connection Hub commit
`12580623294816688f5a9c799d860988af1be7b2` or its qualified descendant,
alongside the cap/revision API below.

The release ledger must record a Connection Hub package containing commit
`db5a7f3406857c62b04a4a6fbe5bb54e8ac06d57` or a qualified descendant in the
same runtime build as this host. The compatibility refusal protects mixed
versions; it does not make an older package capable of serving Card refresh.
