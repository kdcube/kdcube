---
id: repo:kdcube-ai-app/app/ai-app/docs/service/secrets/secrets-service-README.md
title: "Secrets Manager Implementations"
summary: "System map for KDCube secret resolution: descriptor selectors, trusted-runtime read and write flows, persistence choices, and provider-specific behavior."
tags: ["service", "secrets", "configuration", "aws", "runtime"]
keywords: ["SECRETS_PROVIDER", "secrets.service.backend", "secrets-service", "host-vault", "aws-sm", "secrets-file", "in-memory", "user secrets", "bundle secrets", "secret flow"]
updated_at: 2026-10-06
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/service-runtime-configuration-mapping-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/secrets-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/security-and-trust-model-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/cross-runtime-context-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/delegated-management-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/secrets/secret-management-cli-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/environment/setup-dev-env-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/environment/setup-for-ecs-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/secrets/host-vault-README.md
---
# Secrets Manager Implementations

This document is the system-level map of secret resolution in KDCube. It
describes the runtime secrets manager implementations and how they behave for:

- platform secrets
- bundle-shared secrets
- user-scoped bundle secrets

It also explains which component selects and stores a value, which trusted
component may receive it, and what survives a restart. The
[Host Vault for Provider Secrets](host-vault-README.md) page owns the local
host-vault protocol, enrollment, migration, and storage details.

This document is only about secrets. For non-secret descriptor-backed runtime
reads and descriptor/env mapping, see:
[docs/configuration/service-runtime-configuration-mapping-README.md](../../configuration/service-runtime-configuration-mapping-README.md)

## 1. Runtime contract

The runtime chooses an `ISecretsManager` provider from descriptor-owned
`secrets.provider`. The installer projects that choice into
`SECRETS_PROVIDER`; operators configure the descriptor rather than the
generated environment file.

Supported providers:

- `secrets-service`
- `aws-sm`
- `secrets-file`
- `in-memory`

Legacy aliases:

- `local` -> `secrets-service`
- `service` -> `secrets-service`
- `file` / `yaml` -> `secrets-file`

The runtime entrypoint is the secrets manager in
[manager.py](../../../src/kdcube-ai-app/kdcube_ai_app/infra/secrets/manager.py).

### 1.1 Expiring runtime-secret custody

Portable platform packages can bind an expiring, opaque secret-record
contract through `KDCubeEphemeralSecretStore`. The adapter gives the package
one namespace and delegates storage to the selected host provider. This lets
protocols persist a short-lived bearer outside their durable metadata while
keeping provider choice inside KDCube.

`create(secret_ref, value, expires_at)` is an atomic create-only operation:

- `True` means this call created the requested record.
- `False` means the reference already existed, including an identical replay;
  its original value, identity and deadline remain intact.
- an exception means the outcome is unknown. The record remains eligible for
  its provider expiry purge, and callers never infer that it is absent.

The dedicated runtime service path uses create-only records, separate from
configuration descriptor storage and the legacy generic write endpoint.
The file runtime store uses an OS lock; the host-vault runtime path uses a
fixed reference binding and generation-zero creation. In-memory and legacy
raw AWS operations do not establish the common custody guarantees merely
because they support writes. In particular, a cloud idempotency token alone
does not enforce read expiry or provide a terminal deletion fence.

Recoverable session issuance uses `issuance_secret_custody` from
`infra.secrets.issuance`. It wraps the original bearer in a versioned,
reference-bound JSON envelope and refuses malformed or expired reads with
finite `issuance_custody_invalid` / `issuance_custody_expired` reasons. Missing
records return `None`; provider failures remain unavailable, not absence.
Its purge accepts a non-future timestamp and a limit from 1 to 1000. The issuer
still trusts only readback matching the digest captured in its reservation,
not the create Boolean. Recovery reads the original after a collision; it never
overwrites that reference or moves to a fresh reference after an unknown result.

The factories use the same mode-neutral qualification operation,
`qualify_runtime_custody(namespace=...)`, rather than a provider-name allowlist
or `/health` response. The compatibility `durability_required` constructor
argument is not an authorization decision. The wrapper's `namespace` is its
bound address; `declared_backend` and the compatibility `effective_backend`
are configured provider telemetry, not authority. Direct construction requires
the exact host store type. Composition must require the exact wrapper and
namespace, never default a missing namespace or trust a provider label.

Call `await custody.qualify()` before composing production issuance. Every
create, get and purge repeats the check before value I/O; it is not cached.
The running service must attest all five guarantees for the exact namespace:
create-only writes, restart persistence, expiry-enforced reads, bounded atomic
purge and scope authorization. Its authenticated qualification endpoint is
`/runtime-secrets/{namespace}/qualification`. Host-owned policy grants exact
namespaces to read and write credential hashes; neither a namespace supplied
in a request nor a shared application token is itself a grant. Malformed or
missing policy denies access. The AWS lane currently returns unqualified.
The host must still verify real replacement persistence and second-identity
denial: unit fixtures and configured labels are not deployment evidence.

For a cloud backend, bounded atomic purge means atomic logical retirement
plus durable, bounded cleanup claims. It does not mean synchronous physical
cloud erasure. A terminal record blocks reads and late publication before
cleanup starts. Deletion acceptance and physical deletion confirmation are
separate states, and unresolved create outcomes remain reconciliation work.

#### Dedicated file-runtime records

The file manager has a separate runtime-record primitive. A trusted host may
construct `SecretsManagerConfig` with `runtime_secrets_root` (an absolute,
dedicated persistent directory) and `runtime_secret_namespaces` (the exact
authorized namespace set). These records never enter configuration descriptor
YAML. Creation, reads, deletion and bounded expiry purge share a cross-process
OS file lock; commits use private files, atomic rename and file/directory fsync.
Reads enforce expiry, and create-only collisions preserve the original value.
No runtime root or namespace authorization is inferred from a descriptor path.

The file runtime store participates in the common qualification contract;
there is no separate issuance-only provider allowlist. Private local files prove
neither a container mount's persistence through replacement nor isolation from
co-located code running as the same OS identity. The host owns those deployment
guarantees.

#### Service-owned PostgreSQL cloud metadata (supporting layer)

The common runtime HTTP boundary accepts synchronous or asynchronous trusted
store factories and operations. Sync file/native work remains in a threadpool;
async pool/cloud work is awaited on the service event loop. Authorization and
bounded input validation precede factory invocation. Each request requires an
exact successful storage qualification before any record operation, not just
at the qualification endpoint. Bootstrap constructs stores without a duplicate
qualification check. Finite errors, exact result types and no-store responses
are shared across providers. In particular, directly supplying the still
unqualified AWS component cannot bypass this gate; this bridge does not
select an AWS backend, open a pool, provision a key or lift qualification.

`runtime_pg_schema` provides an explicit migrator, and
`PostgresRuntimeCustodyMetadata` supplies non-secret reservations, original
full-ARN/version pins, terminal tombstones and leased cleanup claims. Values
do not enter these tables. A trusted service supplies a dedicated pool,
schema, namespace enrollment and cloud prefix; these are not request JSON or
Card/session-table authority. Normal operations need schema USAGE and table
SELECT/INSERT/UPDATE, not migration privileges or DELETE.

Reserve before cloud I/O. The immutable incarnation, request commitment,
creation token and original deadline survive recovery. Only the original
creation version may be published, never an arbitrary `AWSCURRENT` version.
Read authorization and a live active metadata record must precede a pinned
value fetch, followed by a state/deadline/pin recheck before returning it.
Retirement and its cleanup jobs commit in one transaction. Late create
acknowledgements cannot resurrect a terminal reference; their exact full ARNs
become cleanup work. Once positively pinned, a different full ARN cannot
replace that pin or become deletion authority for this single-dispatch lane.
Cleanup settlement compares the incarnation, full pins and unexpired claim
token; a stale worker cannot settle a newer claim.

The value commitment is HMAC-SHA256 using a persistent service-held key of
at least 32 bytes. No key is stored in these PostgreSQL tables or derived
from public coordinates. This prevents metadata-only readers from checking
low-entropy value guesses against a plain hash; the opaque-value contract
does not require callers to supply high-entropy strings. Missing/invalid
keys refuse. Service restart must retain the same protected key; changing
it makes original commitment verification fail closed, not mint or replace
the original. Secure key provisioning, preservation and any reviewed key
migration are trusted startup/deployment dependencies still to be wired.

The migrator installs a terminal-state guard trigger. Even the service's DML
role cannot change a terminal record back to reserved or active, reset an
unknown/observed dispatch to unstarted, or promote pre-ledger attempts into
single-dispatch evidence. Publication also requires a reserved-state UPDATE
precondition.

`RuntimeAwsStore` adds asynchronous cloud operations over these coordinates.
Every existing-reference create collision returns False without another
CreateSecret call. Recovery reads the same reserved creation version and can
publish its verified original, but cannot choose another incarnation or
deadline. The reservation owner commits an unstarted-to-unknown dispatch
fence before the only cloud CreateSecret call. Its actual SDK client must
have `total_max_attempts=1`; the trusted `RuntimeAwsClientFactory` supplies
that configuration. An unknown attempt is never rearmed, even after timeout,
process loss or a negative resource lookup. This inference relies on the
single-dispatch fence and the SDK's no-retry contract, not on the creation
token alone. See [CreateSecret](https://docs.aws.amazon.com/secretsmanager/latest/apireference/API_CreateSecret.html)
and [botocore retry configuration](https://docs.aws.amazon.com/botocore/latest/reference/config.html).
Active reads send the pinned full ARN and explicit original VersionId, verify
the value commitment, then recheck metadata before returning the value.
Omitting VersionId would select the current cloud version rather than prove
the original. See the [AWS GetSecretValue contract](https://docs.aws.amazon.com/secretsmanager/latest/apireference/API_GetSecretValue.html).

Nonempty strings use SecretString without envelope overhead, up to its 65536
byte limit. An empty public string uses a one-byte SecretBinary marker; the
original commitment must still match the empty string before decoding it.
Other binary values and responses containing both fields are refused. These
are internal service encodings, not a format imposed on caller values.

Deletion and purge retire metadata first, without claiming physical erasure.
`drain_cleanup(limit=...)` is a trusted maintenance operation, not a public
job endpoint. It deletes only a full ARN from a durable claim. An accepted
delete advances to a separate full-ARN confirmation phase. Name lookup is
restricted to unresolved-creation discovery with the original VersionId;
it never authorizes deletion by name. Cloud exception text is not exposed.
If a delete response is lost and the pinned ARN is later absent, a separate
confirmation is still required. `confirm_pending` is neither a delete ACK
nor physical completion; `reconciled` closes discovery only, not deletion.

An unstarted reservation retired before dispatch needs no cloud discovery
job and cannot later acquire dispatch rights. A positively acknowledged or
recovered sole dispatch is observed; its known ARN can be deleted and
confirmed without permanent discovery work. A truly unknown attempt remains
durable: absence cannot close it because that one request may still commit.
This includes a process lost between committing the dispatch fence and
sending its request. Such an attempt can remain unresolved indefinitely;
the service does not mint a replacement or claim successful recovery.

Cleanup retries use database-clock exponential backoff capped at five
minutes and bounded claim batches. New unresolved reservations are admitted
under a cross-process namespace lock, with a trusted `max_unresolved` limit
(default 1000, maximum 10000). At capacity, new references refuse with a
finite unavailable reason while existing-reference recovery remains usable.
An unknown terminal attempt still consumes a slot. An observed attempt or
undispatched retirement frees it; a negative cloud lookup never does. These
limits bound unknown-attempt load, not total historical tombstone storage.

The explicit migration classifies pre-ledger records as `legacy_unknown`.
They cannot be relabeled observed merely by reading one original version:
earlier code may already have issued multiple requests. They remain durable
and consume capacity. Migration occurs before opening the service pool;
existing pre-ledger deployments require separately reviewed reconciliation
and rollout evidence, not automatic activation of this new lane.

A received, definite CreateSecret refusal has its own terminal attempt state,
`refused`. The adapter requires an actual botocore `ClientError` for
`CreateSecret`, a received 4xx HTTP status, zero SDK retries, and a narrow
authorization/throttling/validation/quota error allowlist. The distinction is
based on the [AWS common errors](https://docs.aws.amazon.com/secretsmanager/latest/apireference/CommonErrors.html)
and [CreateSecret error contract](https://docs.aws.amazon.com/secretsmanager/latest/apireference/API_CreateSecret.html).
It is evaluated only around the actual SDK operation, not client-enter/exit.
ResourceExists, crypto/internal errors, 5xx, transport loss, cancellation,
missing/malformed status or a code-only arbitrary exception stay unknown.

Recording the refusal atomically retires the exact immutable original and
closes pending/claimed discovery work. This releases admission capacity while
keeping the reference permanently unavailable for replay; stale cleanup claims
lose their CAS rights. Refused rows cannot rearm, revive or acquire a late pin.
Migration explicitly adds this state and its terminal/no-pin constraint. A
crash or database failure before the refusal receipt commits conservatively
leaves unknown: a later negative read still cannot supply the lost evidence.

`RuntimeAwsService.admission_status(namespace)` exposes a trusted, value-free
collector input: unstarted, unknown, legacy-unknown and total unresolved counts,
capacity and saturation. It uses the dedicated pool's acquisition checks and
command timeout and emits no secret, reference, incarnation, ARN or error text.
It installs no public HTTP route; operator/metrics collector deployment remains
a separate wiring step. Status and startup success never qualify custody.

`RuntimeAwsService` now composes this store with a dedicated, bounded asyncpg
pool and the common HTTP boundary through an explicit ASGI lifespan. Its
trusted composition input is `RuntimeAwsServiceConfig`: exact namespaces,
metadata schema/login role, AWS account/region/partition/prefix, capacity
bounds, and two `RuntimeBootstrapSecretRef` values. Each bootstrap reference
pins a complete AWS ARN plus an explicit VersionId. The database DSN is a
SecretString; the commitment key is a 32–4096 byte SecretBinary. Their ARNs
are distinct and outside the runtime-record prefix. The service reads them
using its own AWS principal, checks response pins/types, and never uses
arbitrary CURRENT. References may be descriptor data; values may not.

Import, construction and route installation perform no resource I/O. Lifespan
startup loads the pinned inputs and opens its own pool with verified TLS,
explicit connection/command timeouts and bounded size. The resolved DSN must
name the configured login role and explicit host, port and database; ambient
connection defaults, query options and role overrides are refused. Normal
startup performs no migration or role grants. Pool shutdown is bounded and
terminates the owned pool on failure. Service-owned references to the key are
released on close; this is not a claim of Python memory zeroization.

Every pool acquisition runs read-only checks against the actual logged-in
principal and migrated relations. Startup refuses superuser/admin attributes,
role membership, schema/table ownership, DDL/DELETE capabilities, missing
required DML access, transient relations, disabled terminal guards, stale
columns, or access to unrelated user tables. Subsequent permission drift also
refuses acquisition. These checks are bounded structural/access evidence;
they do not attest every constraint/trigger body, SECURITY DEFINER function,
database persistence or the complete deployed database/IAM boundary.

The disposable PostgreSQL lifecycle tests explicitly replace the production
verified-TLS setting with loopback trust/SSL-disabled input. They exercise a
real separate login role, permission drift, failure cleanup, and restart with
the same pinned key/original credential/deadline. AWS transport is synthetic.
Common ASGI routes remain 503 with zero runtime-record I/O because
`RuntimeAwsStore.qualify()` remains closed; startup success is not custody
qualification. Persistent key provisioning, entropy and denied-reader IAM
evidence remain operator/deployment-owned. Key rotation or legacy-row rollout
requires a separately reviewed migration preserving existing commitments.

These supporting components are not yet selected by the deployment entrypoint
or SDK runtime consumer, and do not make AWS qualification true. The new
attempt/closure protocol requires independent review and configured-provider
qualification, including its conservative pre-dispatch-loss and legacy-row
behavior. Service wiring, a deployed least-privilege pool
and role, cloud operations, IAM isolation and the complete guarantee matrix
must be established separately before production custody can use this lane.

The real-PG tests read `KDCUBE_TEST_POSTGRES_DSN`. For the whole `infra/secrets`
pytest importlib gate, use a disposable trust-authenticated loopback or local
socket fixture. SCRAM calls stdlib `secrets.token_bytes`, which can be shadowed
by this package's name during whole-directory collection; a focused-file SCRAM
run is a different input. This test-only requirement is not a recommendation
to change production database authentication. Synthetic AWS actors and the
installed SDK response parser tests do not attest live AWS behavior or IAM.

### 1.2 Two selectors with different jobs

Local Compose has two independent selectors:

```yaml
secrets:
  provider: secrets-service
  service:
    backend: host-vault
```

- `secrets.provider` selects the manager used by `chat-ingress`, `chat-proc`,
  and trusted supervisor-side tool implementations.
- `secrets.service.backend` selects the implementation run by the local
  `kdcube-secrets` container.

The accepted service backend values are `ephemeral` and `host-vault`. The
active durable local combination is exactly `provider: secrets-service` plus
`backend: host-vault`. The name `secret-vault` is not a configured backend.

When the dedicated runtime root is unset, Compose binds `/dev/null` with
`create_host_path: false`; it cannot qualify as a persistent directory.
Runtime API 503 then means custody is not configured. The ordinary secrets
service can still start and serve its separately configured storage.

Changing only `service.backend` prepares the service side. It does not reroute
runtime reads away from the provider named by `secrets.provider`. This
separation creates a safe shadow-staging state in which files remain
authoritative while their values are copied and verified in the host vault.

| `secrets.provider` | `secrets.service.backend` | Runtime source of truth | Intended state |
| --- | --- | --- | --- |
| `secrets-file` | `ephemeral` | `secrets.yaml` and `bundles.secrets.yaml` | Shipped local default and direct descriptor debugging. |
| `secrets-file` | `host-vault` | Secret descriptor files | Host vault enrolled and populated in shadow mode; runtime calls still read files. |
| `secrets-service` | `ephemeral` | Memory inside `kdcube-secrets` | Temporary local multi-service testing; service restart loses values. |
| `secrets-service` | `host-vault` | Durable host-vault store | Active durable local service path after verified activation. |
| `aws-sm` | either | AWS Secrets Manager | ECS/AWS path; local service backend is outside the consumer read path. |
| `in-memory` | either | Memory in each consumer process | Unit tests and intentionally temporary single-process work. |

The checked-in `assembly.yaml` ships with `secrets-file` and `ephemeral`.
For a host-vault migration, configure `secrets-file` plus `host-vault`, run the
shadow stage, and then use `kdcube secrets backend host-vault activate`. Activation
transactionally changes the provider to `secrets-service`, recreates the
affected services, and verifies real reads. It leaves the backend set to
`host-vault`.

## 2. End-to-end secret flow

### 2.1 A trusted KDCube operation reads a secret

```text
user or delegated caller
        |
        | request contains an operation and ordinary arguments
        v
KDCube ingress / agent harness / MCP surface
        |
        | admitted tool or application call
        v
trusted KDCube tool or provider adapter
        |
        | get_secret(exact internal key)
        v
ISecretsManager selected by secrets.provider
        |
        +-- secrets-file ------> YAML through KDCube storage
        +-- aws-sm ------------> AWS Secrets Manager
        +-- in-memory ---------> current process memory
        `-- secrets-service ---> private internal HTTP service
                                      |
                                      +-- ephemeral memory
                                      `-- host-vault mTLS broker
        |
        | value returns only to the trusted implementation that needs it
        v
provider request (for example Brave, Slack, or an external MCP server)
        |
        v
bounded provider result returns to the caller
```

The secret is data used by trusted runtime code. It is not part of an agent
Card, MCP tool schema, prompt, model-visible context, or generated-code
payload. Trusted code can necessarily see a value it must use, so installing a
bundle remains an administrator trust decision. In split execution, the
trusted supervisor resolves account credentials and handles the approved tool
call; the restricted executor receives a narrow socket and no descriptor
payload, broker credential, or vault identity. See
[Cross-Runtime Context](../../runtime/cross-runtime-context-README.md).

### 2.2 The active local host-vault read path

```text
Docker / KDCube deployment

chat-ingress       chat-proc              trusted supervisor
  read token       read + admin token      trusted read path
     |                 |                         |
     `-----------------+-------------------------'
                       |
                       | private HTTP on the internal secrets network
                       v
                 kdcube-secrets
                 stateless broker
                 - validates read/admin door token
                 - owns deployment mTLS identity
                 - binds tenant/project/kdcube-runtime namespace
                       |
                       | mutually authenticated TLS
                       v
                 host vault service
                 - verifies certificate and live trust record
                 - reads encrypted durable record
                 - audits digest and result, never value
```

The `SECRETS_TOKEN` and `SECRETS_ADMIN_TOKEN` values gate the private
in-deployment HTTP door. They are not host-vault identities and never cross
the mTLS hop. Only `kdcube-secrets` receives the client certificate and private
key. The vault uses that certificate fingerprint, its live trust registry, and
the registered namespace to authorize each request.

### 2.3 A user or automation changes a secret

There are two supported write surfaces. Both end at the same selected
`ISecretsManager` and therefore work with files, the host vault, or AWS:

```text
platform admin UI / existing bundle-secret API
        |
        | authenticated platform authority
        v
exact bundle or user secret mutation
        |
        v
selected ISecretsManager

delegated operator or agent
        |
        | user-provided Card-bound bearer
        | + exact request + granted exact/namespace/scope selector
        v
KDCube delegated management API
        |
        | Connection Hub admission on every call
        | Once or Always invocation policy
        v
selected ISecretsManager
```

The approving user can give that bearer to an agent or operator CLI. The agent
is then a delegated KDCube administrator for exactly the resources and
operations on the live Card. Delegated management defines separate grants for
metadata read, plaintext value read, value write, and delete; granting
plaintext read intentionally discloses that exact value to the caller. An
exact target can use `Once` or `Always`. A namespace or whole-scope selector is
standing `Always` authority; the resulting provider operation is still exact.

This API authority is distinct from host administration. The bearer is
accepted by KDCube's public management boundary and is never accepted by the
host vault. The `kdcube-secrets` broker alone presents the deployment mTLS
identity to the vault, after KDCube has admitted the caller and selected the
exact internal secret reference. The delegated agent therefore needs no
Docker access, vault filesystem access, or deployment private key.

Human descriptor export is a separate CLI-initiated,
browser-confirmed, one-use ceremony. Selected export returns named targets;
whole export freezes and returns all current platform, bundle, user, and user-
bundle values. Both create the normal private `secrets.yaml` and
`bundles.secrets.yaml` and do not add reusable export authority to a Card. See
[Delegated KDCube Management Service](../cicd/delegated-management-service-README.md).

## 3. Supported secret scopes

### Platform secrets

Examples:

- `platform.services.openai.api_key`
- `platform.services.anthropic.api_key`
- `platform.services.git.http_token`

These are used as shared service-wide defaults.

### Bundle-shared secrets

Examples:

- `bundles.rms@06-04-26-156.secrets.git.http_token`
- `bundles.rms@06-04-26-156.secrets.anthropic.api_key`

These are shared by all users of the same bundle within the same tenant/project.

### User-scoped bundle secrets

Examples:

- `users.alice.bundles.rms@06-04-26-156.secrets.git.http_token`
- `users.alice.bundles.rms@06-04-26-156.secrets.anthropic.api_key`

These are intended for current-user credentials such as:

- per-user Claude / Anthropic keys
- per-user Git PATs

Bundles should not build these flat keys manually. Runtime now provides:

- `await get_secret("u:...")`
- `await set_user_secret(...)`
- `await delete_user_secret(...)`

in
[config.py](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/config.py).

## 4. Provider behaviors

### `in-memory`

Implementation:

- [InMemorySecretsManager](../../../src/kdcube-ai-app/kdcube_ai_app/infra/secrets/manager.py)

Behavior:

- stores all secrets in process memory only
- supports writes
- does not synchronize across replicas
- does not survive service restart

Use only for:

- tests
- very local temporary runs

Do not use for:

- persistent environments
- multi-worker correctness

### `secrets-service`

Implementation:

- [SecretsServiceSecretsManager](../../../src/kdcube-ai-app/kdcube_ai_app/infra/secrets/manager.py)

Behavior:

- reads and writes secrets through the configured `SECRETS_URL`
- uses `SECRETS_TOKEN` for reads
- uses `SECRETS_ADMIN_TOKEN` for writes
- the proc/ingress service itself is not the storage of record
- persistence depends on the backing store used by the secrets service

Local Compose provides two descriptor-selected service implementations:

- `secrets.service.backend: ephemeral` runs the existing temporary sidecar
- `secrets.service.backend: host-vault` runs the stateless mTLS broker backed
  by the durable host-owned vault

The host-vault broker keeps the same internal HTTP contract, so trusted
KDCube callers do not change. Its deployment certificate is mounted only into
the broker. The broker can run in shadow mode while `secrets-file` remains the
active provider, allowing `kdcube secrets backend host-vault stage` to copy and compare
values before cutover. `kdcube secrets backend host-vault activate` then quiesces the
two secret-consuming services, switches them together, verifies real reads,
and restores file authority on an ordinary failure. An interrupted activation
blocks ordinary startup until `kdcube secrets backend host-vault recover --yes`
recreates and verifies the retained file-backed path. See
[Host Vault for Provider Secrets](host-vault-README.md) for the descriptor,
workload identity, enrollment, staging, activation, and durability contracts.

Restart behavior follows `secrets.service.backend`:

- `ephemeral`: recreating `kdcube-secrets` loses its values
- `host-vault`: the broker is stateless and reloads values from the durable
  host-vault store after broker, Docker, or KDCube service restart

User-scoped secrets:

- stored under the same canonical provider key namespace
- for example:
  - `users.alice.bundles.rms@06-04-26-156.secrets.anthropic.api_key`

### `aws-sm`

Implementation:

- [AwsSecretsManagerSecretsManager](../../../src/kdcube-ai-app/kdcube_ai_app/infra/secrets/manager.py)

Behavior:

- reads and writes to AWS Secrets Manager
- `SECRETS_AWS_SM_PREFIX` or `SECRETS_SM_PREFIX` defines the namespace root
- if no explicit prefix is set, runtime derives:
  - `kdcube/<tenant>/<project>`

Secret id mapping examples:

- `platform.services.openai.api_key`
  - `kdcube/<tenant>/<project>/platform/services/openai/api_key`
- `bundles.rms@06-04-26-156.secrets.git.http_token`
  - `kdcube/<tenant>/<project>/bundles/rms@06-04-26-156/secrets/git/http_token`
- `users.alice.bundles.rms@06-04-26-156.secrets.anthropic.api_key`
  - `kdcube/<tenant>/<project>/users/alice/bundles/rms@06-04-26-156/secrets/anthropic/api_key`

Restart behavior:

- fully persistent
- service restart has no effect on stored values

### `secrets-file`

Implementation:

- [SecretsFileSecretsManager](../../../src/kdcube-ai-app/kdcube_ai_app/infra/secrets/manager.py)

Behavior:

- reads and writes YAML descriptors through the storage abstraction in
  [storage.py](../../../src/kdcube-ai-app/kdcube_ai_app/storage/storage.py)
- supports:
  - `file://...`
  - `s3://...`

Configured URIs:

- `GLOBAL_SECRETS_YAML`
- `BUNDLE_SECRETS_YAML`

Descriptor placement:

- `secrets.yaml` contains only the `platform` and `users` top-level roots
- `bundles.secrets.yaml` contains deployment-bundle values
- no separate `USER_SECRETS_YAML` is needed; whole administrator export
  reconstructs user values under `users` in the ordinary `secrets.yaml`

Restart behavior:

- persistent if the configured YAML location is persistent
- `file://...` survives restart if the file is on durable local/EFS storage
- `s3://...` survives restart because the source of truth is S3

Read behavior:

- rereads YAML on every `get_secret()`
- no in-memory secret-value cache

Write behavior:

- writes are serialized with a distributed Redis lock when Redis is configured
- reads do not rely on Redis

So after restart:

- the service simply rereads the YAML descriptor again
- values remain as long as the file/object still exists

## 5. `secrets-file` YAML layouts

### Global service secrets

Example:

```yaml
services:
  openai:
    api_key: sk-openai
  anthropic:
    api_key: sk-anthropic
```

### Bundle-shared secrets

Example:

```yaml
bundles:
  version: "1"
  items:
    - id: "rms@06-04-26-156"
      secrets:
        git:
          http_token: ghp_xxx
          http_user: x-access-token
        anthropic:
          api_key: sk-ant-xxx
```

### User-scoped bundle secrets

Current `secrets-file` implementation stores them in `GLOBAL_SECRETS_YAML`.

After one RMS user saves:

- Anthropic API key
- Git PAT

for bundle `rms@06-04-26-156`, the YAML will look like:

```yaml
users:
  alice:
    bundles:
      rms@06-04-26-156:
        secrets:
          anthropic:
            api_key: sk-ant-user
          git:
            http_token: ghp_user_pat
            http_user: x-access-token
```

That is the state that survives restart.

## 6. Multiple workers / replicas

### `in-memory`

- each worker has its own copy
- no cross-worker visibility
- no persistence

### `secrets-service`

- source of truth is remote
- all workers read the same backing store
- persistence depends on that remote service

### `aws-sm`

- source of truth is AWS Secrets Manager
- all workers read the same remote store
- fully persistent

### `secrets-file`

- source of truth is the YAML descriptor
- all workers see the same values if they point to the same file/object
- reads reread YAML directly, so restart is not special
- write races are serialized by Redis lock when Redis is configured

Redis is not the value store here. It is only:

- write coordination
- metadata/key tracking

## 7. API exposure rules

### Bundle-shared secrets

Admin UI/API may manage bundle-shared secrets.

### User-scoped secrets

Current rule:

- user secrets are write-only over REST
- runtime can list internal metadata, but user-facing REST does not return values
- current user write route does not return key names either

These ordinary settings surfaces keep current-user values out of browser
responses.

### Delegated deployment management

The deployment management API is a distinct, operator-oriented surface. It
supports exact metadata read, value read, value write, and delete operations.
Each request requires a live Connection Hub Card grant for its concrete secret
resource and operation; `Once` and `Always` policies are enforced at call
time. Plaintext read responses use `Cache-Control: no-store`. The canonical
operator surface is `kdcube secrets metadata|get|set|delete`; Connection Hub's
host CLI supplies its stored OAuth session around the same KDCube library.

### Human descriptor export

An administrator can reconstruct selected `secrets.yaml` and
`bundles.secrets.yaml` files through `kdcube secrets export`. The browser
ceremony displays the exact manifest and produces one PKCE-bound exchange.
It is independent of delegated Card authority. This is the deliberate path
from a non-file provider back to owner-controlled descriptor files. See
[Manage KDCube Secrets](secret-management-cli-README.md) for the complete
command and authority contract.

## 8. RMS bundle behavior

RMS now prefers credentials in this order:

### Git

1. `users.<user_id>.bundles.rms@06-04-26-156.secrets.git.http_token`
2. `bundles.rms@06-04-26-156.secrets.git.http_token`
3. `platform.services.git.http_token`
4. process / machine git auth

### Claude

1. `users.<user_id>.bundles.rms@06-04-26-156.secrets.anthropic.api_key`
2. `bundles.rms@06-04-26-156.secrets.anthropic.api_key`
3. `platform.services.anthropic.api_key`
4. process / machine Claude auth

This means:

- per-user override is possible
- shared team default is still possible
- existing env-based deployments still work as fallback

## 9. Choosing a provider

| Situation | Descriptor choice | Persistence and boundary |
| --- | --- | --- |
| First local run, direct processor debugging, or source-controlled secret-reference development | `provider: secrets-file`, `backend: ephemeral` | Values live in owner-protected YAML on the configured durable storage path. |
| Temporary local multi-service test that starts empty | `provider: secrets-service`, `backend: ephemeral` | Shared within the running sidecar; recreated sidecar starts empty. |
| Prepare a local durable vault without changing active consumers | `provider: secrets-file`, `backend: host-vault` | Files remain authoritative while staging copies and verifies values. |
| Durable local deployment with an enrolled host service | `provider: secrets-service`, `backend: host-vault` | Values live encrypted in the host vault; only the broker holds deployment mTLS identity. |
| AWS ECS deployment | `provider: aws-sm` | Values live in AWS Secrets Manager and access follows task IAM plus the KDCube key namespace. |
| Unit test or disposable single-process experiment | `provider: in-memory` | Values exist only in that process. |

The local host vault provides a meaningful isolation boundary when its service
account, vault home, and the broker's deployment identity are inaccessible to
agent processes. A process with Docker-administrator access, host root access,
or access to the vault service account remains inside the deployment trust
boundary. Running the vault under a dedicated OS identity or on a separate
machine makes that boundary concrete. ECS uses `aws-sm` rather than the local
host-vault topology.

## 10. Summary

Persistence across service restart depends entirely on the chosen provider:

- `in-memory`: no
- `secrets-service` with `ephemeral`: no
- `secrets-service` with `host-vault`: yes
- `aws-sm`: yes
- `secrets-file`: yes, if the referenced YAML location persists

For `secrets-file`, user-scoped secrets currently survive restart by being written
into `GLOBAL_SECRETS_YAML` under the `users:` tree.
