---
id: repo:kdcube-ai-app/app/ai-app/docs/service/secrets/secrets-service-README.md
title: "Secrets Manager Implementations"
summary: "System map for KDCube secret resolution: descriptor selectors, trusted-runtime read and write flows, persistence choices, and provider-specific behavior."
tags: ["service", "secrets", "configuration", "aws", "runtime"]
keywords: ["SECRETS_PROVIDER", "secrets.service.backend", "secrets-service", "host-vault", "aws-sm", "secrets-file", "in-memory", "user secrets", "bundle secrets", "secret flow"]
updated_at: 2026-09-22
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

- `True` proves this create operation owns the requested record; an exact
  replay after a lost success response has the same result.
- `False` proves the reference already existed and its value remains intact.
- an exception means the outcome is unknown. The record remains eligible for
  its provider expiry purge, and callers never infer that it is absent.

The local secrets-service path sends `expected_generation: 0`. Both the
host-vault broker and the temporary sidecar enforce that precondition at the
storage mutation. AWS uses `CreateSecret` with the opaque reference as its
idempotency token, so a retry of the same operation returns its original
success; `ResourceExistsException` identifies a different operation that
already owns the name. The in-memory provider performs the test and insert
under its process lock. Existing `set` operations remain available for
protocols that do not consume the create outcome.

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
