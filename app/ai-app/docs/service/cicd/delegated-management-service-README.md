---
id: repo:kdcube-ai-app/app/ai-app/docs/service/cicd/delegated-management-service-README.md
title: "Delegated KDCube Management Service"
summary: "Documents KDCube's deployment-scoped management service: Card-governed automation, exact secret operations, and request-bound human approval through platform login or WebAuthn."
status: current-source; live-deployment-acceptance-pending
tags: ["service", "cicd", "management", "connection-hub", "delegated-authority", "idempotency"]
keywords: ["KDCube management resource", "application reload", "request-bound permit", "secret provider", "secret descriptor export", "human approval", "Cognito Managed Login", "Google auth_time", "WebAuthn", "passkey", "PKCE", "effect ledger", "OAuth protected resource"]
updated_at: 2026-09-11
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/deployment-target-control-api-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/delegated-authority-and-admission-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/security-and-trust-model-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/secrets/secret-management-cli-README.md
  - https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/package/delegated-authority-and-admission.md
  - https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/package/delegated-secret-administration.md
  - https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/recipes/direct-protected-service.md
  - https://docs.aws.amazon.com/cognito/latest/developerguide/authorization-endpoint.html
  - https://developers.google.com/identity/openid-connect/reference
  - https://www.w3.org/TR/webauthn-3/
---
# Delegated KDCube Management Service

The delegated management service exposes a bounded public API for one running
KDCube tenant/project deployment. A caller authenticated through Connection
Hub can inspect deployment state, discover one application's declared
surfaces, reload one exact declared application, and manage exact secrets
through the deployment-selected provider when its live delegated card grants
the corresponding operation and an exact or matching broad selector.

The approving user provides the Card-bound authority to an agent or operator.
The canonical `kdcube secrets` commands accept that bearer through a hidden
prompt or stdin, never through a command-line argument. The Connection Hub
host CLI obtains and stores its bearer through `connection-hub host authorize`
using OAuth Authorization Code plus PKCE, then calls the same KDCube management
library. Another API client may present an opaque bearer issued through its
approved caller flow. In every case, the
caller is a delegated KDCube administrator for the Card's resources and
operations: the token is the user's explicit authority to administer those API
surfaces. KDCube preserves the external actor, `access_id`, grantor, Card
revision, and invocation policy on every call. This authority does not grant
host login, Docker control, descriptor-file access, or a secret-provider
workload identity.

The same service exposes a separate administrator-performed export ceremony.
It reads either an explicit list or the complete frozen provider inventory
after browser confirmation and returns the values once to a PKCE-bound
loopback CLI. This ceremony creates no delegated card grant.

These routes operate inside the running chat processor. Starting or recovering
a stopped deployment remains a local Docker, orchestrator, or infrastructure
operation because the stopped deployment cannot serve login, Connection Hub,
card resolution, or its management API.

## Protected Resource And Operations

The service constructs its resource from descriptor-owned deployment
coordinates:

```text
urn:kdcube:management:deployment:<RFC3986 tenant>:<RFC3986 project>
```

Each secret operation constructs a concrete resource from the server-owned
coordinates and validated target:

```text
urn:kdcube:management:secret:<tenant>:<project>:<scope>:<scope-id>:<key>
```

No operation accepts a tenant or project override.

| Operation | Method and route | Result |
| --- | --- | --- |
| `kdcube.management.deployment.inspect` | `GET /api/integrations/management/v1/deployment` | Bounded platform release, aggregate readiness, and declared-application preparation state. |
| `kdcube.management.application.surfaces.read` | `GET /api/integrations/management/v1/applications/{application_id}/surfaces` | Enabled API, MCP, widget, job, and messaging declarations for one exact application. |
| `kdcube.management.application.reload` | `POST /api/integrations/management/v1/applications/{application_id}/reload` | Reload evidence for one exact application from current descriptor authority. |
| `kdcube.management.secret.metadata.read` | `POST /api/integrations/management/v1/secrets/metadata/read` | Existence, provider, and write capability for one exact platform, bundle, user, or user-bundle key. |
| `kdcube.management.secret.value.read` | `POST /api/integrations/management/v1/secrets/value/read` | One exact plaintext value for an admitted caller. Responses use `Cache-Control: no-store`. |
| `kdcube.management.secret.value.write` | `POST /api/integrations/management/v1/secrets/value/write` | Set one exact value through the selected provider. |
| `kdcube.management.secret.delete` | `POST /api/integrations/management/v1/secrets/delete` | Delete one exact value through the selected provider. |

Every call requires an opaque Connection Hub bearer in `Authorization` and a
printable 1..256-character `Idempotency-Key`. Reload accepts the exact JSON
body `{}`. Inspect and surface discovery accept no body. Application IDs are
single exact identifiers; wildcard, path, URL, and reload-all forms are
rejected.

Secret operation requests accept one exact `platform`, `bundle`, or `user`
target. Bundle and user-bundle targets include one exact application id.
Wildcards, metadata keys, path escapes, and cross-scope namespace escapes are
rejected before admission. The Card may authorize that exact resource or a
matching namespace/whole-scope selector; broad selectors require `Always`.
A secret-operation request digest is a keyed HMAC over the
full validated request, including a write value. It binds approval and replay
to the exact value without exposing a reusable plaintext hash. Secret values
stay outside recovery URLs, logs, Card records, and effect-ledger records.

Protected-resource discovery is available at:

```text
GET /api/integrations/management/v1/.well-known/oauth-protected-resource
```

The document publishes the concrete resources, the deployment's Connection Hub
OAuth issuer, and the delegated operation routes. A missing bearer receives `401`
with that metadata URL in `WWW-Authenticate`.

## Two Authority Journeys

```text
delegated automation

agent or operator CLI
  -> user-approved OAuth/PKCE session or issued Card-bound bearer
  -> exact request matched against Card resource + operation + invocation policy
  -> Connection Hub admission on every call
  -> KDCube selected secret provider
  -> bounded result

administrator-performed descriptor export

KDCube CLI or a product CLI using its management library
  -> exact non-secret key manifest or whole-inventory selection
     + PKCE challenge + loopback callback
  -> KDCube browser page
  -> current platform admin session + explicit Export once decision
  -> one-use authorization code bound to manifest digest and PKCE verifier
  -> KDCube selected secret provider
  -> new local secrets.yaml + bundles.secrets.yaml directory
```

For delegated automation, choosing `Once` on one exact key updates the caller's
Card and its invocation policy. The Card continues to identify who may attempt
the operation, while the policy consumes the one admitted invocation.
Choosing `Always` records reusable exact or broad Card authority.
Every secret operation requires one of these explicit policies. A matching
Card resource with no policy fails closed instead of receiving the generic
admission default.

The selected provider is reached by trusted KDCube code after admission. For
the local host-vault backend, the internal `kdcube-secrets` broker uses its own
deployment mTLS identity; the caller's bearer is neither forwarded to nor
recognized by the vault. For AWS Secrets Manager, the trusted workload uses
its configured task or deployment identity. Provider choice therefore changes
storage and workload authentication, not the user's delegated API contract.

For human export, the person performs the action directly. The transaction
therefore stands alone: it is short-lived, frozen-manifest-bound, and consumed
once, with no Card mutation and no reusable export credential.

## Human Secret Export

The CLI can name requested keys explicitly or select the complete deployment
inventory. This works uniformly with file, Host Vault, and cloud secret
providers. For `--all`, KDCube freezes provider inventory before the browser
decision so the approved digest identifies every exported target. The public
start response exposes the count and digest, not the inventory names. An
authenticated administrator sees the names on the approval page; the CLI
receives and verifies them only in the one-use exchange.

```bash
kdcube secrets export \
  --platform-key platform.services.brave.api_key \
  --bundle-key connection-hub@1-0=connections.oauth_state_secret \
  --output-directory ./kdcube-secret-export-20260905
```

```bash
kdcube secrets export \
  --all \
  --output-directory ./kdcube-secret-export-all
```

`connection-hub secrets host ...` remains the Connection Hub convenience
surface. It supplies the selected host and native-store OAuth session while
reusing KDCube's management protocol, validation, and private writers. The
canonical command contract is documented in
[Manage KDCube Secrets](../secrets/secret-management-cli-README.md).

The output directory must be new. It contains canonical `secrets.yaml` and
`bundles.secrets.yaml` documents. POSIX hosts create the directory with mode
`0700` and both files with mode `0600`; Windows uses the ACL inherited from
the selected parent directory. The CLI prints paths, counts, digest, and
approval evidence while keeping values out of terminal output.

The protocol has three server routes:

| Route | Purpose |
| --- | --- |
| `POST /secrets/export/start` | Validate exact targets or freeze whole provider inventory, then persist that manifest, loopback callback, state, and S256 challenge with a short TTL. A whole-export response returns only count and digest. |
| `GET/POST /secrets/export/authorize` | Resolve a human platform session, display every exact target, and record one approve or deny decision. |
| `POST /secrets/export/exchange` | Atomically consume the code and verifier, then read exactly the approved keys. |

Redis stores a hash-addressed transaction containing non-secret targets,
digests, state, status, approval evidence, and the assurance, evidence-age,
and result-size policy captured when the request starts. Later descriptor
changes cannot weaken those pinned limits; disabling export still stops an
in-flight request. Redis stores the authorization code only as a SHA-256
digest and never stores exported values. Atomic compare and swap permits one
approval transition and one exchange. A failed provider read consumes that
attempt; the operator starts a new visible approval rather than replaying a
partly completed export.

### Human assurance

`management.secret_export.required_assurance` selects the minimum evidence:

| Value | Evidence contract | Built-in state |
| --- | --- | --- |
| `session_confirmation` | Current KDCube platform admin browser cookie plus the exact form decision. | Implemented through the deployment's configured KDCube browser authority. |
| `fresh_authentication` | A recent signed authentication event for the same KDCube subject. | Implemented for Cognito Managed Login and direct Google OIDC. |
| `user_verification` | A fresh WebAuthn assertion whose signed authenticator data has the user-verification flag. | Implemented with passkey enrollment bootstrapped by `fresh_authentication`. |

Explicit `Authorization` and ID-token headers are rejected at this boundary,
and Connection Hub delegated authentication is disabled in the verifier. A
delegated admin bearer therefore cannot become human approval.

The verifier receives a `HumanApprovalContext` containing the exact action,
deployment, transaction id, request digest, required assurance, maximum
evidence age, and a validated relative return URL. Each evaluation also names
one explicit phase:

- `present` authorizes showing the exact decision form. An adapter may redirect
  to its challenge; after that challenge succeeds, it may return evidence while
  retaining the request-bound proof for the decision step.
- `commit` authorizes recording the submitted decision. An adapter must return
  evidence directly and should atomically consume its completed proof. A new
  challenge at this phase fails closed and the user starts review again.

For either phase, the verifier returns:

- `HumanApprovalEvidence`, bound to that digest with the verified subject,
  assurance method, and verification time; or
- `HumanApprovalChallenge`, containing a validated relative or HTTPS URL for
  an authority-owned browser ceremony.

The authorize `GET` may follow that challenge. The final decision `POST` never
turns a newly returned challenge into approval; it fails with
`human_approval_restart_required`, and the browser must complete the challenge
before presenting the decision again. KDCube independently checks the evidence
type, digest, age, clock skew, and assurance level before recording approval.

The built-in adapters use shared Redis state so a callback and the final
decision may reach different processor workers. A random state or WebAuthn
challenge is bound to the exact action, transaction id, request digest,
tenant/project, KDCube subject, platform session id, hash of the active browser
cookies, return URL, and required assurance. Provider tokens are verified and
discarded. Redis retains only bounded challenge data, public WebAuthn
credentials, and short-lived proof. The proof is consumed at `commit`, before
the one-use export code is issued.

### Fresh authentication adapters

The provider is selected from the active platform session. `auto` follows a
Google-backed platform identity or the exact Cognito issuer and app-client
audience. A deployment with both provider families and no trustworthy session
hint fails closed instead of choosing one by configuration order.

**Cognito Managed Login** uses Authorization Code with S256 PKCE,
`prompt=login`, `openid`, state, and nonce. KDCube validates the returned ID
token's signature, issuer, app-client audience, token use, nonce, exact subject,
and signed `auth_time`. The authentication time must be after the challenge,
allowing bounded clock skew. Set `cognito.managed_login: true` only after
confirming that the configured domain serves Managed Login: Cognito's classic
Hosted UI does not provide the required `prompt=login` behavior. The app client
must permit this exact callback:

```text
https://<kdcube-host>/api/integrations/management/v1/human-approval/oidc/callback
```

The implementation is a public-client PKCE flow and does not read or send a
Cognito client secret.

**Google OIDC** requests an ID token with `nonce`, `form_post`, account
selection, and the signed `auth_time` claim. KDCube validates Google's
signature, issuer, client audience, authorization-response issuer, nonce,
exact `google:<sub>` continuity, and `auth_time`. A new token's `iat` is never
treated as evidence that the user authenticated again. Google documents
account selection, consent, and silent prompt values, but not a portable
password-forcing prompt or `max_age`; consequently this adapter accepts only a
Google-signed authentication time within the configured evidence age. A stale
or absent claim fails with a structured restart/step-up error. Enable the
`auth_time` claim for the Google client and register the same exact callback
shown above. Use WebAuthn when the operation requires a reliable new local
user-verification gesture.

### WebAuthn user verification

`user_verification` requires `navigator.credentials.get()` with
`userVerification: required`. KDCube verifies the RP id, exact HTTPS origin
(local HTTP is accepted only for loopback), random challenge, credential id,
signature, sign counter, backup flags, and signed UV bit. A successful
assertion creates one proof for one pending management action; replay cannot
approve a second action.

When the subject has no credential compatible with current policy, KDCube
starts passkey registration. Registration first completes the configured
fresh-authentication adapter, then requires WebAuthn user verification and
stores only credential id, public key, counter, AAGUID, attestation metadata,
backup state, label, and creation time. Private key material stays with the
authenticator.

| `credential_policy` | Accepted credential | Security meaning |
| --- | --- | --- |
| `verified_passkey` | Any credential with a valid signed UV bit, including synced passkeys. | Portable user verification; not a hardware-bound claim. |
| `single_device` | UV credential whose WebAuthn backup-eligibility state identifies a single-device credential. | Device-bound credential; it may still be a platform authenticator rather than a separate hardware key. |
| `attested_hardware` | Credential enrolled under direct attestation whose format and certificate chain validate against operator-supplied roots. | Explicit deployment trust in approved authenticator hardware and attestation policy. |

Changing to a stronger policy does not upgrade older credentials. They become
ineligible and the owner enrolls a qualifying credential. An
`attested_hardware` descriptor without absolute PEM trust-root paths fails
validation.

Credential listing, naming, and revocation are required before
`user_verification` becomes a deployment default. Until that lifecycle surface
is enabled, deployments use `session_confirmation` or `fresh_authentication`
for administrator export and may exercise WebAuthn in controlled acceptance runs.

### Descriptor configuration

```yaml
management:
  human_approval:
    fresh_authentication_provider: auto  # auto | cognito | google
    challenge_ttl_seconds: 180
    http_timeout_seconds: 10
    cognito:
      managed_login: false
      hosted_ui_domain: ""  # inherit the selected Cognito authority
    google:
      client_id: ""         # inherit the selected Google authenticator
      jwks_url: https://www.googleapis.com/oauth2/v3/certs
    webauthn:
      enabled: true
      rp_id: ""              # derive current public host, or pin a stable host
      rp_name: KDCube
      allowed_origins: []    # derive current public origin, or list exact origins
      credential_policy: verified_passkey
      trusted_attestation_root_files: {}
      timeout_milliseconds: 60000
      max_credentials_per_user: 8
  secret_export:
    required_assurance: user_verification
```

For local development, a stable HTTPS tunnel origin should be pinned when a
passkey must survive tunnel changes. For ECS, set `rp_id` to the deployment
domain and `allowed_origins` to its exact HTTPS origin. Proof and public-key
state stay in deployment Redis; secret values remain in AWS Secrets Manager.
The Cognito app client must include the management callback above. These
settings do not introduce Host Vault into ECS.

Selecting a stronger assurance while its provider, callback, claim, RP, or
attestation policy is unavailable fails closed. The current browser-cookie
verifier is never promoted to fresh-authentication or WebAuthn evidence.

## End-To-End Authority Flow

```text
 Connection Hub CLI                 running KDCube                  Connection Hub
 ------------------                 --------------                  --------------
 select endpoint
 discover protected resource ------> metadata route
 OAuth Authorization Code + PKCE ---------------------------------> OAuth/card flow
 receive opaque card-bound bearer <-------------------------------- user approval

 create exact request
 application_id + {}
 Idempotency-Key
        |
        v
 public management route
        |
        +-- contracts.py
        |   validate exact input
        |   construct deployment resource
        |   hash canonical request document
        |
        +-- admission.py
        |   keep bearer opaque
        |   sign resource + operation + invocation + digest
        |   with the management service HMAC secret
        |---------------------------------------------------------> direct admission
        |                                                           verify service proof
        |                                                           resolve current bearer
        |                                                           resolve live card/catalog
        |                                                           resolve invocation policy
        |<--------------------------------------------------------- allow or bounded denial
        |
        +-- service.py
        |   admission must allow before any runtime effect
        |
        +-- effect_ledger.py
        |   atomically reserve resource + operation + invocation
        |   same digest: replay/pending/unknown
        |   changed digest: conflict
        |
        +-- runtime.py
            inspect registry/readiness
            OR project enabled manifest surfaces
            OR call exact internal reload authority for application_id
        |
        +-- settle terminal result in shared Redis ledger
        v
 bounded result + authority revisions + replay evidence
```

Connection Hub resolves the current card and active catalog on every call.
Card edits, catalog changes, expiry, and revocation therefore affect the next
request without restarting either service.

The management runtime returns declared public coordinates only. It omits
source paths, repository credentials, secret references, environment values,
container identities, internal hostnames, and raw exceptions. Widget
coordinates use the authenticated `/widgets/{alias}` route.

## Request-Bound Reload Approval

Reload is configured as request-bound. `Always` remains reusable while the
live card and catalog authorize the operation. `Once` is valid only for the
exact browser-approved invocation and request digest.

```text
 unchanged reload request                     Connection Hub browser surface
 ------------------------                     ------------------------------
 direct admission denies
        |
        +-- Connection Hub signs a short-lived approval ticket containing:
        |     service, caller/card, resource, operation,
        |     invocation id, request digest, application id,
        |     card/catalog revisions, issued_at, expires_at
        |
        +-- management response returns consent_required,
            exact request fields, absolute ticket expiry, and authorization URL
                                                   |
 CLI opens the URL -------------------------------> user signs in as card owner
                                                   UI carries ticket opaquely
                                                   server verifies signature,
                                                   expiry, owner, displayed fields,
                                                   request and live revisions
                                                   before changing card/policy
                                                   |
                                                   +-- allow once:
                                                   |   issue exact request permit
                                                   +-- allow always:
                                                       commit reusable policy
 unchanged path/body/bearer/Idempotency-Key
 retries ----------------------------------------> exact authority is consumed
```

The approval ticket authenticates the browser handoff. It is not the permit
used at operation time. The request permit is durable Connection Hub authority
bound to the card and exact request. The KDCube effect ledger separately
prevents an admitted retry from performing the reload twice.

For a browser-resolvable reload denial, the public recovery has stable reason
`delegated_request_permit_required`. The management admission client extracts
the ticket from the returned URL, verifies it with the registered service
secret, compares it with the exact request and recovery fields, and derives
`expires_at` from that authenticated ticket. The UI treats the ticket as
opaque, and the Connection Hub server verifies it again before any card or
invocation-policy mutation.

## Idempotency And Failure States

The canonical request digest covers schema, concrete deployment resource,
exact operation, exact application ID, and body. Ordinary operations use
SHA-256; secret operations use HMAC-SHA-256 with the protected-service secret
so a low-entropy value cannot be guessed from the digest offline. The shared
Redis effect ledger is reserved only after current admission succeeds and
before the runtime adapter executes.

| Ledger state | Same invocation and digest | Runtime effect |
| --- | --- | --- |
| absent | reserve `effect_started` | execute once |
| `effect_started`, still within pending interval | `409 effect_outcome_pending` | do not execute |
| `effect_started`, older than pending interval | `409 effect_outcome_unknown` | do not execute automatically |
| `effect_completed` or `effect_failed` | return stored response with replay evidence | do not execute |
| any state with another digest | `409 invocation_id_conflict` | do not execute |

Terminal records have no time-based expiry in this first contract. This keeps
an accepted invocation replayable across processor restarts. Operators should
monitor ledger growth as part of the deployment's Redis capacity policy.

Admission and ledger failures stop before the runtime operation. A runtime
failure is settled as a terminal failed outcome so an uncertain retry cannot
repeat the effect. If the effect completed but settlement failed, the caller
receives `effect_outcome_unrecorded`; a later attempt resolves the retained
started record as pending or unknown rather than applying the effect again.

## Descriptor Configuration

The processor owns the management route and direct-admission client:

```yaml
management:
  delegated:
    enabled: true
    effect_pending_seconds: 120
    connection_hub:
      app_id: connection-hub@1-0
      service_id: kdcube-management
      service_secret_ref: connections.delegated_credentials.admission.services.kdcube-management.signing_secret
      admission_url: ""
      timeout_seconds: 10
  secret_export:
    enabled: true
    required_assurance: session_confirmation
    max_evidence_age_seconds: 300
    transaction_ttl_seconds: 180
    consumed_tombstone_seconds: 600
    max_targets: 4096
    max_total_value_bytes: 1048576
```

An empty `admission_url` resolves to the Connection Hub app operation on the
same descriptor-owned processor port. A different URL is an explicit
descriptor choice.

The Connection Hub app descriptor registers the workload and both protected
resource families. Reload is request-bound; secret operations use Card
`Once`/`Always` invocation policy and exact or broad secret resources:

```yaml
connections:
  delegated_credentials:
    admission:
      enabled: true
      services:
        kdcube-management:
          secret_ref: connections.delegated_credentials.admission.services.kdcube-management.signing_secret
          resources:
            - "urn:kdcube:management:deployment:*:*"
            - "urn:kdcube:management:secret:*:*:*:*:*"
          request_bound_operations:
            - kdcube.management.application.reload
          request_permit_ttl_seconds: 600
```

The active delegated catalog declares the same resource selector and exact
operations. The maintained default marks the resource admin-only. The shared
HMAC value exists only in the Connection Hub app secret provider under the
referenced path and contains at least 32 random bytes.

## Module Ownership

| Module | Responsibility |
| --- | --- |
| `management/contracts.py` | Resource grammar, exact input validation, canonical digest, result/error envelopes. |
| `management/routes.py` | Delegated HTTP methods, bearer/idempotency requirements, discovery, body rules, dependency assembly, and export-router composition. |
| `management/admission.py` | Fresh signed direct-admission call to Connection Hub, plus signed recovery-ticket verification; bearer remains opaque. |
| `management/service.py` | Admission-first orchestration, recovery projection, audit evidence, replay behavior. |
| `management/effect_ledger.py` | Shared atomic reservation and guarded terminal settlement. |
| `management/runtime.py` | Bounded registry/readiness projection, enabled surface discovery, and exact application reload adapter. |
| `management/secret_contracts.py` | Exact platform/bundle target grammar, secret resources, and operation constants. |
| `management/secret_runtime.py` | Provider-neutral metadata, read, write, and delete through the configured `ISecretsManager`. |
| `management/human_approval.py` | Request-bound evidence/challenge interface, independent assurance validation, and built-in platform-browser-session verifier. |
| `management/secret_export.py` | Exact export request digest, Redis transaction state, PKCE binding, and atomic one-use transitions. |
| `management/secret_export_routes.py` | Start, browser approval, and one-use exchange HTTP ceremony. |
| Connection Hub `delegated_admission.py` | Workload verification, live card/catalog evaluation, signed browser recovery, request-permit consumption. |
| Connection Hub `invocation_policy` | Durable `Once`, `Always`, request-bound permit, and invocation replay authority. |

## Audit And Secret Boundary

Effect audit evidence includes decision ID, pairwise caller profile,
`access_id`, card/catalog/policy revisions, target, operation, exact
application, invocation ID, digest, and runtime instance. These identifiers
support diagnosis without carrying credentials.

Responses, logs, errors, consent URLs, and ledger records exclude bearer and
refresh tokens, authorization codes, PKCE verifiers, HMAC secrets and
signatures, provider credentials, session cookies, descriptor secret values,
and internal localhost authorization values. The signed approval ticket is a
short-lived authorization handoff and must not be logged.

The source contract is covered by focused route, runtime, service, ledger,
Connection Hub admission, request-ticket, request-permit, and widget tests.
Live acceptance still requires a staged runtime that loaded the exact tested
KDCube and Connection Hub sources, followed by one real approved reload and
its no-second-effect replay proof.
