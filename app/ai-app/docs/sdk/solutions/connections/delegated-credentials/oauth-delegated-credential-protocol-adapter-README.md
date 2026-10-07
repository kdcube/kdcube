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
The current authorization-code HTTP handler is not yet wired to this adapter;
original refresh preparation, complete original-pair recovery, configured
encrypted-provider qualification, and installed/live acceptance remain open.
Ordinary human login and the existing delegated minter are unchanged.

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
