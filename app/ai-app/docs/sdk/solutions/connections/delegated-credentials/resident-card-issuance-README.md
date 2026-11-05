---
id: sdk.connections.resident-card-issuance
title: Resident Card credential issuance
summary: Prepare inactive original access credentials and recover their committed bearer for resident Card custody.
tags: [sdk, connection-hub, credentials, resident, transactions]
keywords: [ResidentCredentialProvider, CardCredentialHandles, credential_issue, planned issuance, resident bearer, recovery]
see_also:
  - ./oauth-delegated-credential-protocol-adapter-README.md
  - https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/package/card-transactions.md
  - https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/package/delegated-cards.md
---

# Resident Card credential issuance

Hosted Agent Cards retain their owner's selections while a credential expires
or rotates. `ResidentCredentialProvider` adapts the existing original-session
issuance service so a Card transaction can prepare an inactive credential and
recover the same bearer after its decision commits.

The trusted Hub host supplies an access-only `OAuthIssuancePlan` from its stored
decision plan. The resident client and stable Card id must match its grantor.
The provider uses the plan's fixed expiry and the deployment's session
authority; the host owns the resident lease and recovery deadline policy.

- `prepare(plan=...)` prepares inactive fixed claims and returns only the
  receipt, context, non-secret grant record and captured lifetime.
- `read(plan=...)` reconstructs that original metadata without preparation,
  signing or secret-custody access.
- `activate(plan=..., result=...)` validates a committed, applied original
  result, activates the matching prepared session and re-signs its stored
  claims. The returned `CardCredentialHandles` contains the same bearer and
  session id for the credential effect to install through resident custody.
- `retire(plan=..., result=...)` retires an authenticated aborted or
  superseded inactive original while preserving the terminal decision. It
  never retires a previously activated session.
- `retire_previous(plan=..., result=..., session_id=..., token_sha256=...)`
  logs out only the pinned old activated session of a committed rotation.
  The Hub verifies its old-handle effect and actual decision, installs the
  new original first, then retires the old session and grant while the Card
  remains fenced. Retrying retirement does not invalidate its user or a
  sibling session, and never targets the new bearer's fingerprint.

The host authenticates the plan and result and fences the exact live Card
revision before activation. Constructing these typed values from request JSON
does not authorize a caller. The provider creates no Card decision and writes
no resident handle row. The Hub's existing `credential_issue` effect owns the
handle installation and reports applied only after custody succeeds; its Card
publication fence protects serving during interrupted FINISH.

Bearer bytes stay in trusted process memory and the selected resident secret
store. Card authority, decision intents, effect payloads and plan JSON carry
only non-secret coordinates and fingerprints. Replays use the original claims
and signing key; an unavailable or changed key produces a named refusal.

This adapter is one part of the host composition. Request and recovery paths
must bind the same provider, issuance store and resident handle store. The
Hub owns creation/rotation planning, Control authorization, group membership,
catalog reservations, decision recovery and serving fences.
