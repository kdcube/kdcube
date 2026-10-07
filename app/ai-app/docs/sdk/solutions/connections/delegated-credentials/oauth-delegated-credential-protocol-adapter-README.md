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

A known `CardConflict` from the Hub after minting causes the host to withhold
the response and attempt revocation of both the portable access grant and
refresh family. This applies to both generic refusals and stale-revision
replacement conflicts. Each revocation is attempted independently, so a
failure of one does not prevent the other. Cleanup failures retain the original
refusal and log only the credential kind and client identity, without bearer or
exception text. A failed backing-store call is not proof of revocation.

This cleanup operates on delegated grant bindings and refresh families. It
does not change ordinary platform-login authentication or claim to revoke the
underlying platform session. Initial refresh-family cap/revision stamping and
uncertain Card-commit recovery remain separate issuance boundaries.

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

The release ledger must record a Connection Hub package containing commit
`db5a7f3406857c62b04a4a6fbe5bb54e8ac06d57` or a qualified descendant in the
same runtime build as this host. The compatibility refusal protects mixed
versions; it does not make an older package capable of serving Card refresh.
