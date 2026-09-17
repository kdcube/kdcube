---
id: repo:kdcube-ai-app/app/ai-app/docs/service/secrets/host-vault-README.md
title: "Host Vault for Provider Secrets"
summary: "Durable host-owned secret storage for local KDCube: selector states, system and trust-boundary flows, deployment workload identity over mTLS, migration, activation, and recovery."
tags: ["service", "secrets", "security", "vault", "runtime"]
keywords: ["host vault", "kdcube-host-vault/1", "secrets.service.backend", "workload identity", "mTLS", "kdcube-secrets broker", "envelope encryption", "trust registry", "hostvaultctl", "shadow staging", "activation"]
updated_at: 2026-09-17
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/secrets/secrets-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/security-and-trust-model-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/cross-runtime-context-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/delegated-management-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/secrets/secret-management-cli-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/secrets-descriptor-README.md
---
# Host Vault for Provider Secrets

Provider credentials a deployment holds on a user's behalf (OAuth refresh
tokens, app passwords, API keys stored through Connection Hub) need a home
that survives service, Docker, and host restarts, that no copied string can
open, and that answers only the deployment they belong to.

The host vault is that home. It is a small service the host operator owns,
outside the KDCube runtime workdir. Deployments reach it over mutual TLS with
a key generated inside their own boundary, and the vault checks every request
against a live trust registry before it touches the store.

This page describes the protocol, local Compose integration, operator tool,
and proofs. The runtime still selects its secrets provider through the
descriptor-owned `secrets.provider` setting documented in
[secrets-service-README.md](secrets-service-README.md). `secrets-file` remains
the shipped default. The host-vault broker may be started beside that file
provider for shadow staging, then selected explicitly as the backing
implementation of `secrets-service` after acceptance. Selecting the broker
never migrates or deletes an existing secret descriptor.

## 1. Selection and operating states

The accepted backend name is `host-vault`. An active local host-vault setup
uses both selectors:

```yaml
secrets:
  provider: secrets-service
  service:
    backend: host-vault
    host_vault:
      address: host.docker.internal:7781
      server_name: host.docker.internal
      identity_dir: /absolute/service-owned/path/deployment-identity

platform:
  services:
    proc:
      exec:
        py_code_exec_network_mode: auto
```

| Field | Operational meaning |
| --- | --- |
| `provider` | Manager used by trusted KDCube consumers. `secrets-service` sends reads and mutations to `kdcube-secrets`. |
| `service.backend` | Program started inside `kdcube-secrets`. `host-vault` starts the stateless mTLS broker. |
| `host_vault.address` | `host:port` reached from the broker container. `host.docker.internal:7781` reaches the Docker host in the maintained local layouts. |
| `host_vault.server_name` | DNS name or IP verified against the vault server certificate's subject alternative names. It authenticates the destination independently of routing. |
| `host_vault.identity_dir` | Host directory containing the deployment client certificate, private key, and issuing CA certificate. The installer mounts only these files, read-only, into the broker. |
| `host_vault.local_service` | Optional. Declares that the vault is a source-operated process on this host (`home`, `python`, optional `bind`). Commands that start the runtime then start the vault when nothing listens on its port (macOS and Linux, refused on Windows). Absent or `null` means another owner runs the vault and the CLI only checks that it answers. |
| `py_code_exec_network_mode` | `auto` preserves the trusted supervisor route to the private secrets service while split generated execution stays restricted. |

`secrets.provider` routes trusted KDCube consumers. `service.backend` chooses
what the local `kdcube-secrets` container fronts. Setting the backend prepares
the broker; it does not provision a vault service, enroll the deployment, copy
values, or reroute consumers by itself. `identity_dir: null` is a template
placeholder and must become an absolute host path before the backend can start.

The supported transition is deliberately staged:

```text
initial local state
  provider: secrets-file
  backend:  ephemeral
          |
          | provision vault, create deployment key, enroll certificate
          | configure address, server_name, identity_dir, network mode
          v
shadow state
  provider: secrets-file       files remain the runtime source of truth
  backend:  host-vault         broker can receive staged copies
          |
          | kdcube secrets backend host-vault stage
          | kdcube secrets backend host-vault activate --yes
          v
active durable local state
  provider: secrets-service    consumers call kdcube-secrets
  backend:  host-vault         broker calls the durable vault over mTLS
```

Use `kdcube secrets backend host-vault activate` for the final switch. It coordinates
configuration, consumer quiescing, service recreation, real-read verification,
and rollback. Hand-editing `provider` skips those guarantees. The broader
provider matrix is in
[Secrets Manager Implementations](secrets-service-README.md#11-two-selectors-with-different-jobs).

This implementation is a local Compose topology. ECS deployments continue to
select `aws-sm`; changing these local fields does not alter ECS task IAM,
Terraform, or AWS Secrets Manager storage.

## 2. Connection to the rest of KDCube

### 2.1 Read and provider-call flow

```text
external client, resident agent, automation, or user
                |
                | authenticated request / delegated bearer
                v
KDCube or Connection Hub operation surface
                |
                | live admission and operation policy where required
                v
trusted KDCube app or provider adapter
                |
                | resolves one exact internal secret key
                v
SecretsServiceSecretsManager
                |
                | private HTTP + deployment read token
                v
kdcube-secrets broker
  - receives no caller-selected vault namespace
  - owns the deployment certificate and private key
                |
                | mTLS + protocol-bound reference
                v
host vault
  - authenticates the broker certificate
  - checks live deployment registration and namespace ACL
  - decrypts the exact value and records a value-free audit event
                |
                | plaintext returns through the protected path
                v
trusted app/provider adapter performs the provider call
                |
                v
bounded operation result returns to the original caller
```

For the Connection Hub external-MCP proxy, the trusted app resolves the
user-owned connector credential, calls the upstream MCP server itself, and
returns the tool result. The external agent receives the proxy's OAuth or
delegated credential and never receives the upstream provider credential.

The secret value necessarily exists briefly in the trusted implementation
that uses it. The vault authenticates the deployment broker, not an individual
installed bundle. In the current deployment-wide manager, installed KDCube
apps are administrator-approved trusted code and the broker binds them under
the logical `kdcube-runtime` application namespace. Per-bundle process
isolation is a separate platform boundary.

### 2.2 Runtime boundaries

```text
agent/model/generated code
        |
        | ordinary tool arguments; no secret value or vault credential
        v
trusted supervisor-side tool implementation
        |
        | same selected ISecretsManager contract in each execution mode
        +-- in-process / venv
        +-- local subprocess supervisor
        `-- Docker/Fargate supervisor
                |
                | private secrets-service route
                v
          kdcube-secrets broker

restricted split executor
  - minimal environment
  - supervisor socket
  - no SECRETS_TOKEN
  - no descriptor payload
  - no deployment certificate or key
  - no direct host-vault route
```

With `py_code_exec_network_mode: auto`, a host-launched trusted supervisor uses
the host network. Under Docker-in-Docker it shares the processor network
namespace and can reach the private secrets-service network. The restricted
executor remains networkless and asks the trusted supervisor to perform
approved tools. This preserves provider-backed tools across in-process,
subprocess, and isolated execution without placing secret material in
generated code. See
[Cross-Runtime Context](../../runtime/cross-runtime-context-README.md).

### 2.3 Write, delete, import, and administrator export

```text
authenticated admin settings surface
                  OR
delegated agent or operator with a user-provided live Card bearer
        |
        | metadata.read / value.read / value.write / delete
        v
KDCube secret management boundary
        |
        | selected ISecretsManager
        v
kdcube-secrets broker -> host vault commit

owner-controlled descriptor export
        |
        | current admin browser session + explicit one-use approval + PKCE
        v
selected ISecretsManager -> new owner-controlled YAML files
```

After activation, ordinary mutations commit to the host vault and update
provider-derived inventory; they do not rewrite the old plaintext descriptor
files. A delegated operator or agent can act only through the resources and
operations approved on its live Card. An exact key may be `Once` or `Always`;
a namespace or whole-scope selector is reusable `Always` authority. Every
effect remains exact and value-free in audit. Reconstructing descriptor files
is an administrator-performed export ceremony rather than a Card grant. See
[Delegated KDCube Management Service](../cicd/delegated-management-service-README.md).

The delegated bearer and the vault workload identity solve different hops:

```text
user grants Card authority to agent
        |
        | opaque bearer: who may request this KDCube operation?
        v
KDCube management API + Connection Hub admission
        |
        | selected internal secret reference; no caller bearer forwarded
        v
kdcube-secrets broker
        |
        | deployment certificate: which enrolled KDCube may use this vault?
        v
host vault
```

The agent can administer KDCube because the user delegated the exact
management operations to it. It does not become the vault service, deployment
workload, Docker operator, or host administrator. Conversely, the broker's
deployment certificate cannot be used as a Connection Hub Card or user
session. If the Card grants `secret.value.read`, returning that one plaintext
value is the intended operation; omitting that grant keeps reads closed while
still allowing metadata, write, or delete independently.

### 2.4 What an owner can inspect

Host Vault has four distinct inspection surfaces:

| Question | Command or surface | Result |
| --- | --- | --- |
| Which KDCube deployments may use this vault? | `hostvaultctl.py list` on the vault host | The deployment trust registry: identities, namespaces, certificate fingerprints, status, and ACLs. |
| Which storage is configured for this local runtime? | `kdcube secrets backend status --json` | Descriptor-selected provider, authoritative-store kind, retained descriptor-file presence, migration state, and evidence source. |
| Does one exact secret exist and which provider serves it? | `kdcube secrets metadata KEY --scope platform` or the same command with `--scope bundle --bundle-id APP_ID` | Non-secret metadata only. The live Card must grant metadata read for that exact secret resource. |
| What is one exact stored value? | `kdcube secrets get KEY --scope ... --output PRIVATE_FILE` | One admitted plaintext value written atomically to the named file. The CLI does not print it. The live Card must grant value read for that exact resource. |
| How can an administrator reconstruct selected descriptor entries? | `kdcube secrets export` with repeated exact platform, bundle, user, or user-bundle targets | A new private directory containing only the approved keys, after a fresh one-use browser ceremony. |
| How can an administrator reconstruct the complete private descriptor pair? | `kdcube secrets export --all` or full `kdcube config export --include-platform-descriptors` | Literal `secrets.yaml` and `bundles.secrets.yaml` containing all current platform, bundle, user, and user-bundle values. |

For example:

```bash
# Presence and provider capability, without disclosing the value.
kdcube secrets metadata platform.services.brave.api_key \
  --scope platform

# Deliberately disclose one exact value to a mode-0600 local file.
umask 077
kdcube secrets get platform.services.brave.api_key \
  --scope platform \
  --output ./brave-api-key.txt

# Reconstruct only the explicitly named descriptor entries.
kdcube secrets export \
  --platform-key platform.services.brave.api_key \
  --bundle-key connection-hub@1-0=connections.oauth_state_secret \
  --output-directory ./kdcube-secret-export-20260905
```

The management commands are also available to an agent or operator when the
administrator grants its Card the corresponding operation and exact or broad
resource selector. Descriptor export is administrator-performed: its one-use
approval is bound to the frozen manifest and remains outside reusable Card
authority. Host Vault's internal `secret.list` protocol supports trusted
provider inventory and migration. The administrator-facing export boundary
supports either named targets or the complete frozen inventory.

The KDCube CLI prompts for a delegated bearer or reads it from the first stdin
line. The Connection Hub CLI remains a convenience client around the same
KDCube management library and can supply its native-store OAuth session. The
complete command and input contract is in
[Manage KDCube Secrets](secret-management-cli-README.md).

User-owned Connection Hub connector credentials use the same selected
`ISecretsManager` through the user-secret scope. Their owner surface is the
Connections interface: it reports whether a credential is present and allows
the owner to replace or remove it without revealing plaintext. Explicit user-
scope management authority can administer them, and complete administrator
export includes them in `secrets.yaml`.

## 3. Trust model in one pass

```text
KDCube service ──(internal secrets-service HTTP, in-deployment)──▶ kdcube-secrets broker
                                                                        │
                                       deployment certificate + key (mTLS)
                                                                        ▼
                                                              host vault service
                                                                        │
                                                    trust registry: fingerprint → deployment, ACL, status
                                                    envelope-encrypted durable store
                                                    append-only audit log
```

Identity is the presented client certificate and nothing else. The transport
verifies the chain and key possession. The service then looks the
certificate's fingerprint up in the registry: the record must exist, be
active, and be unexpired at that moment. A bearer header, a deployment id in
the body, the socket address, or a process name never establish authority.
The registry's namespace ACL decides which `tenant/project/application`
references that identity may read or write. KDCube's current secrets manager
is deployment-wide, so its broker binds the exact logical application
`kdcube-runtime`. Platform, bundle, and per-user keys remain distinguished by
their existing internal key grammar inside that namespace. A remote caller
cannot choose either the namespace or a key.

Enrollment is a one-use ticket: the deployment generates its private key in
place, hands out only a CSR, and the host CA issues a certificate whose
subject is the deployment id the host assigned (the CSR's own subject is
discarded). Rotation issues a new certificate that overlaps the old for a
bounded interval. Revocation is a registry edit and blocks the next
connection: the running server reloads the registry when the file changes.

### 3.1 Physical placement and threat boundary

The same protocol supports a host-local service and a separate vault machine.

```text
single machine

ordinary desktop user processes             dedicated vault service identity
  agents and local clients                   /var/lib/kdcube-host-vault
            |                                          ^
            v                                          | mTLS :7781
  Docker KDCube deployment                             |
    chat-proc / ingress -> kdcube-secrets broker ------'
                              |
                              `-- read-only deployment identity mount
```

```text
two machines

KDCube machine                                      vault machine
  Docker deployment                                  dedicated service
  kdcube-secrets broker ===== mutually authenticated TLS =====> encrypted store
  deployment private key                             CA, trust registry, root keys
```

| Deployment situation | Security property |
| --- | --- |
| Vault runs as the desktop user and agents have the same filesystem or Docker-administrator authority | Durable encrypted storage and removal of provider values from active descriptors; those agents remain inside the administrative trust boundary. |
| Vault runs under a dedicated OS account and agents cannot use its account, vault home, Docker daemon, or broker identity mount | The broker certificate and service ACL create a meaningful local process boundary. |
| Vault runs on a separate machine or appliance and the KDCube host protects Docker control plus the broker identity | Physical placement separates caller-side agents from storage; the enrolled deployment certificate is the only KDCube credential accepted by the vault protocol. |
| KDCube runs on ECS | AWS Secrets Manager and task IAM provide the cloud boundary; the local host-vault path is not selected. |

A principal with host root, the vault service account, the KDCube Docker
daemon, or the broker's mounted private key can act within this deployment
boundary. The host vault makes that boundary explicit; it does not relabel an
already-administrative local process as untrusted.

### 3.2 What is stored where

| Location | Contents and lifetime |
| --- | --- |
| `assembly.yaml` | Non-secret provider/backend choice, route, TLS server name, and identity-directory path. |
| `secrets.yaml` and `bundles.secrets.yaml` during migration | Plaintext bootstrap source retained through shadow staging, activation, and rollback acceptance. |
| Deployment `identity_dir` outside the workdir | Client certificate, owner-only client private key, and vault CA certificate. |
| `kdcube-secrets` container | Read-only identity mounts and internal door tokens; broker state is stateless and its runtime directory is `tmpfs`. |
| Host-vault home | Issuing CA, root keys, encrypted records, live trust registry, and value-free audit log under a dedicated service identity. |
| Redis | Provider-derived key inventory and management/export coordination metadata; no provider secret values. |
| Trusted consumer memory | The exact plaintext value while an approved implementation uses it for a provider operation. |

Activation changes the active source of truth; it intentionally retains the
bootstrap files for verified rollback. Plaintext cleanup is a later explicit
operator action after restart and connector regression acceptance.

## 4. Protocol `kdcube-host-vault/1`

One JSON request per POST to `/v1/vault`. Fields:

| field | meaning |
| --- | --- |
| `protocol` | `kdcube-host-vault/1` |
| `operation` | `health`, `secret.get`, `secret.list`, `secret.set`, `secret.delete`, `secret.rotate` |
| `reference` | `kdv1:<tenant>/<project>/<application>/<name>`; namespace segments match `[A-Za-z0-9][A-Za-z0-9._@-]{0,127}` and the bounded name uses the protocol's dotted/slashed internal-key grammar |
| `request_id` | 8 to 64 chars of `[A-Za-z0-9-]`, unique per request |
| `issued_at` | Unix seconds; the server rejects requests outside a 300 s skew window |
| `value` | present for set and rotate, at most 64 KiB |
| `expected_generation` | optional optimistic guard for set, rotate, delete |

Responses carry `ok`, `code`, `message`, `request_id`, and for successes
`value` (get), `names` (list), and `generation` (mutations). Codes: `ok`, `invalid_request`,
`unauthenticated`, `forbidden`, `not_found`, `conflict`, `replay_rejected`,
`too_large`, `corrupt_record`, `backend_unavailable`, `internal`. Failure
messages are fixed phrases per code, so backend or TLS exception text never
reaches a caller.

Replay: during one host-vault service process, a bounded cache remembers
`(deployment_id, request_id)` with the body digest. The same cached request
returns the original result without a second commit; the same id with a
different body is `replay_rejected`. This receipt cache is intentionally not a
durable effect ledger and is empty after service restart. Trusted callers use
generation guards for cross-restart mutation conflicts. Card-governed KDCube
management calls additionally use the Redis effect ledger documented in
[Delegated KDCube Management Service](../cicd/delegated-management-service-README.md)
for durable one-effect semantics.

Generations: every committed change to a reference increments its
generation, deletions included (a deletion is a tombstone record). A caller
that passes `expected_generation` gets `conflict` when the store moved on.

## 5. Persistence

Records live under `<home>/store/<digest[:2]>/<digest>.json`, keyed by a
SHA-256 digest of the reference so plaintext names never appear on disk. Each
new live record holds the encrypted name used for scoped inventory, the
generation, an integrity digest over its metadata, and the sealed value:
AES-256-GCM under a fresh data key, the data key wrapped by the current root
key, with additional authenticated data binding the record to its reference
digest and generation. Legacy records without an encrypted name remain
readable; their old `.__keys` metadata is accepted only as a bounded hint and
each referenced value is verified live before it appears in inventory.

Writes are atomic: candidate file, fsync, `os.replace`, directory fsync. A
crash between candidate and commit leaves the previous value in place, and
the server removes stale candidates on start.

Root keys come from a `RootKeyProvider`. The shipped `FileRootKeyProvider`
keeps raw 32-byte keys as `0400` files in a `0700` directory with a `CURRENT`
marker, and refuses to serve a key file that is group- or other-readable.
Rotation makes a new key current and rewraps every record's data key (the
ciphertext itself is untouched). Old versions remain readable until rewrap
completes. A hardware- or OS-keychain-backed provider can replace this
class behind the same three calls (`current_key_id`, `key`, `rotate`).

Assumptions this phase makes and states: the vault home is owned by a
dedicated service user on the host, the root-key directory is not on a
KDCube-managed volume, and the appliance boundary around the deployment
private key is the operator's host isolation (the code does not claim a
production local boundary until a service-owned appliance or VM is
exercised).

## 6. Modules

```text
kdcube_ai_app/infra/secrets/host_vault/
  protocol.py   references, requests, responses, error codes, sanitizer
  keys.py       RootKeyProvider, FileRootKeyProvider, envelope seal/open/rewrap
  storage.py    FileDurableSecretStore (atomic commit, tombstones, recover)
  audit.py      AuditEvent, MemoryAuditSink, FileAuditSink
  identity.py   HostIssuingCA, DeploymentKey, TrustRegistry, enrollment, rotation, revocation
  service.py    HostVaultService.handle(body, peer_cert_pem)
  transport.py  ServerTLS, ClientTLS, HostVaultServer, HostVaultClient (stdlib ssl)
  broker.py     SecretsBroker: get/set/rotate/delete/health over a VaultTransport
  tests/        the proofs in section 9
```

The broker derives the vault reference from the deployment's canonical
tenant and project plus the trusted logical application the platform binds
and the internal secrets-manager key. The shipped HTTP broker always binds
`kdcube-runtime`, because it fronts KDCube's deployment-wide secrets manager;
it does not claim per-application process isolation. No remote caller names a
vault path. The broker caches nothing and returns `ok` for a mutation only
when the vault committed it. Reads that are forbidden or unreachable come
back as `None`, the same shape the existing
`SecretsServiceSecretsManager.get_secret` returns.

## 7. Deployment files

```text
app/ai-app/deployment/docker/all_in_one_kdcube/secrets/host_vault/
  vault_server.py    host service entrypoint (KDCUBE_HOST_VAULT_HOME, _BIND, _PORT)
  hostvaultctl.py    operator tool: init, enroll, rotate-identity, revoke, list,
                     rotate-root-key, deployment-keygen, deployment-install
  broker_server.py   kdcube-secrets broker with the existing HTTP shape
  requirements.txt   cryptography (all), fastapi/uvicorn/pydantic (broker only)
```

Vault home layout (`KDCUBE_HOST_VAULT_HOME`, default
`/var/lib/kdcube-host-vault`):

```text
ca/ca.key        issuing CA private key, 0400, service custody
tls/ca.crt       issuing CA certificate (also shipped to deployments)
tls/server.crt   vault server certificate for the names given at init
tls/server.key   0400
root-keys/       FileRootKeyProvider directory
store/           FileDurableSecretStore root
trust.json       TrustRegistry (atomically replaced on every change)
audit.log        append-only JSON lines
```

### Provision and enroll

This phase is source-operated. The host service package/installer and
service-manager unit remain release gates in section 10. Use two filesystem
locations with different ownership:

- `VAULT_HOME`, owned only by the dedicated vault service identity
- `IDENTITY_DIR`, owned by the KDCube deployment identity, outside its runtime
  workdir and inaccessible to ordinary agent processes

Until the host service is packaged, run it from a matching KDCube source
checkout. Prepare a dedicated virtual environment and explicit source origin:

```bash
export KDCUBE_SOURCE=/path/to/kdcube-ai-app
export HOST_VAULT_TOOLS="$KDCUBE_SOURCE/app/ai-app/deployment/docker/all_in_one_kdcube/secrets/host_vault"
export HOST_VAULT_PYTHON=/path/to/host-vault-venv/bin/python
export PYTHONPATH="$KDCUBE_SOURCE/app/ai-app/src/kdcube-ai-app"

python3 -m venv /path/to/host-vault-venv
/path/to/host-vault-venv/bin/pip install \
  -r "$HOST_VAULT_TOOLS/requirements.txt"
```

Keep `PYTHONPATH` and the scripts from the same source revision used to stage
the KDCube runtime. `hostvaultctl.py --help` prints the operator/deployment
subcommands without reading secret values.

The provisioning sequence is:

```bash
# 1. Vault host, as the dedicated vault service identity.
export KDCUBE_HOST_VAULT_HOME=/var/lib/kdcube-host-vault
"$HOST_VAULT_PYTHON" "$HOST_VAULT_TOOLS/hostvaultctl.py" init \
  --server-name host.docker.internal \
  --server-name 10.0.0.5

# 2. KDCube deployment boundary. This is the host source directory that
# Compose later mounts read-only into kdcube-secrets.
export IDENTITY_DIR=/var/lib/kdcube-deployments/demo/host-vault-identity
"$HOST_VAULT_PYTHON" "$HOST_VAULT_TOOLS/hostvaultctl.py" \
  deployment-keygen --dir "$IDENTITY_DIR"
# Transfer only $IDENTITY_DIR/host-vault-client.csr to the vault operator.

# 3. Vault host. Register only this deployment namespace and issue its cert.
"$HOST_VAULT_PYTHON" "$HOST_VAULT_TOOLS/hostvaultctl.py" \
  enroll --deployment-id dep-prod-1 \
  --namespace demo-tenant/demo-project/kdcube-runtime \
  --csr /trusted-transfer/host-vault-client.csr \
  --out /trusted-transfer/dep-prod-1.crt
# Transfer dep-prod-1.crt and $KDCUBE_HOST_VAULT_HOME/tls/ca.crt back to
# the deployment boundary. Neither file is secret.

# 4. KDCube deployment boundary. Install public certificate material beside
# the private key that never left IDENTITY_DIR.
"$HOST_VAULT_PYTHON" "$HOST_VAULT_TOOLS/hostvaultctl.py" \
  deployment-install --dir "$IDENTITY_DIR" \
  --cert /trusted-transfer/dep-prod-1.crt \
  --ca /trusted-transfer/ca.crt

# 5. Vault host. Bind to an interface reachable from the broker container;
# restrict port 7781 to enrolled deployment networks at the host firewall.
export KDCUBE_HOST_VAULT_BIND=0.0.0.0
export KDCUBE_HOST_VAULT_PORT=7781
"$HOST_VAULT_PYTHON" "$HOST_VAULT_TOOLS/vault_server.py"
```

The server certificate must contain the descriptor's `server_name`. The
descriptor `address` chooses where the broker connects; it may be a different
routing name or IP. mTLS authenticates the server certificate and the enrolled
deployment certificate on every new connection.

The broker reads `KDCUBE_HOST_VAULT_ADDR`, `KDCUBE_HOST_VAULT_SERVER_NAME`,
`KDCUBE_HOST_VAULT_IDENTITY_DIR`, `KDCUBE_SECRETS_TENANT`, and
`KDCUBE_SECRETS_PROJECT`. It preserves `/health`, `GET /secret/{key}`, `POST
/set`, and `DELETE /secret/{key}` from `secrets/secrets_server.py`. The host
vault implementation additionally accepts an optional generation guard on
`POST /set` and exposes admin-only `POST /verify` for secret-free migration
comparison. The old `X-KDCUBE-SECRET-TOKEN` and `X-KDCUBE-ADMIN-TOKEN` headers
stay an in-deployment door gate. They are never forwarded, and the vault never
sees them.

### Local Compose selection

The KDCube descriptor owns the selection. Shadow staging keeps the file
provider authoritative:

```yaml
secrets:
  provider: secrets-file
  service:
    backend: host-vault
    host_vault:
      address: host.docker.internal:7781
      server_name: host.docker.internal
      identity_dir: /absolute/service-owned/path/deployment-identity

platform:
  services:
    proc:
      exec:
        py_code_exec_network_mode: auto
```

After staging and regression acceptance, the activation command changes
`provider` to `secrets-service` and selects the already populated broker. That
cutover is a separate, confirmed operator action.

`identity_dir` is a host path outside the KDCube workdir containing exactly:

```text
host-vault-client.crt
host-vault-client.key
host-vault-ca.crt
```

`kdcube secrets backend host-vault prepare` projects this non-secret topology into
Compose for an already running file-backed deployment and recreates only the
`kdcube-secrets` broker. Its dry-run validates the descriptor, enrolled
identity, running broker, and generated configuration without changing a file
or container. Both maintained local
Compose layouts run the same `kdcube-secrets` image. Its default
`secrets.service.backend` is `ephemeral`, which runs the existing temporary
sidecar. `host-vault` runs the mTLS broker instead. Only that broker receives
read-only mounts for the three identity files and network access to the host;
ingress, proc, metrics, and generated executors receive none of them.

`prepare` and every later `kdcube start` verify that the provider/backend
combination is coherent, the identity directory is outside the workdir, all
three files exist and are regular files, and the private key is owner-only on
POSIX. Preparation restores the exact previous generated environment and
broker if the shadow broker does not become healthy.
`host.docker.internal` is mapped to the Docker host on Linux as well as Docker
Desktop. The vault service must bind an address reachable from Docker; mTLS
still authenticates both ends.

### The vault is a startup dependency, and the CLI ensures it

With `service.backend: host-vault`, the broker's `/health` answers
`503 {"detail":"backend_unavailable"}` while the vault is unreachable. Compose
gates `chat-ingress` and `chat-proc` on that health check, so an unreachable
vault leaves ingress, proc, `web-ui` and `web-proxy` in `Created` and prints no
error. This holds in shadow staging too: the file provider is still
authoritative, and the runtime still does not start without the vault. A host
reboot is the ordinary way to get there, because a source-operated vault is a
plain process that nothing restarts.

A configured dependency is ensured by the command that needs it. `kdcube
start`, `kdcube refresh`, and `kdcube init` therefore check the vault after the
identity preflight and before Compose runs.

Two conditions gate all of it, and both come from the descriptor:

1. `secrets.service.backend` is `host-vault`. With any other backend the step
   reads nothing, probes nothing, and starts nothing, on every operating system.
   The shipped default is `ephemeral`.
2. `host_vault.local_service` is declared. Without it the CLI never starts a
   process. It only checks that the vault answers.

| Descriptor | Vault answers | Nothing listens |
| --- | --- | --- |
| `local_service` declared | Left alone. | The CLI starts `vault_server.py` detached from the source checkout given by `--path`, with `KDCUBE_HOST_VAULT_HOME`, `_BIND`, `_PORT` from the descriptor, appends to `<home>/service.log`, writes `<home>/service.pid`, and waits for the port. A vault that does not come up fails the command with the last lines of its log. |
| `local_service` absent or `null`, `address` names this host | Left alone. | The command fails before Compose with the address it probed and the consequence (which services would wait in `Created`). It names both ways out: start the vault, or declare `local_service`. |
| `local_service` absent or `null`, `address` names another machine | Left alone. | The command warns and continues. The CLI sees the network from the host and the broker sees it from a container, so a failed probe is not proof. The warning names the vault as the cause to check if services stay in `Created`. |

```yaml
secrets:
  service:
    backend: host-vault
    host_vault:
      address: host.docker.internal:7781
      server_name: host.docker.internal
      identity_dir: /absolute/service-owned/path/deployment-identity
      local_service:
        home: /absolute/path/to/vault-home          # initialized by hostvaultctl.py init
        python: /absolute/path/to/host-vault-venv/bin/python
        bind: "127.0.0.1"                           # macOS default. Required on Linux (see the table below)
```

The probe runs from the host that runs the CLI. A Docker-host alias in
`address` (`host.docker.internal`, `gateway.docker.internal`, `localhost`,
`127.0.0.1`) is probed on loopback, and any other host is probed as written.
The probe is a TCP connect. It presents no certificate, reads nothing, and
leaves no line in the vault's service log.

Operating systems:

| Host | `local_service` | `bind` | State |
| --- | --- | --- | --- |
| macOS | Supported. | Defaults to `127.0.0.1`. Containers reach a loopback listener through `host.docker.internal`. | Verified on macOS with Docker Desktop: the vault was stopped, the CLI step started it detached, a second run left it alone, and the broker stayed healthy. |
| Linux | Supported. | Required, no default. Compose maps `host.docker.internal` to `host-gateway`. Under Docker Engine that is the bridge gateway (commonly `172.17.0.1`), which a loopback listener does not answer: use that address, or `0.0.0.0` with port 7781 restricted at the firewall. Under Docker Desktop for Linux use `127.0.0.1`. | Covered by unit tests. Not yet run on a Linux host. |
| Windows | Refused with a configuration error before anything starts. The vault's root-key custody check reads POSIX file modes, so the vault service does not run natively on Windows. | Not applicable. | The reachability check still runs for a vault that lives elsewhere (WSL, another machine). Covered by unit tests. Not run on a Windows host. |

A loopback default on Linux would be the unsafe choice: the CLI's own probe
would succeed, it would report the vault started, and the broker would still
be unable to reach it. The bind is therefore the operator's written decision
there.

`kdcube stop` leaves the vault running. It is durable storage that outlives the
Compose stack, and a second local deployment may share it. `local_service`
names machine paths, so a portable descriptor export nulls it together with
`identity_dir`.

`local_service` fits the same-user functional topology in section 3, where the
vault already runs as the desktop user. A vault under a dedicated OS account or
on another machine is kept up by that owner's service manager, the descriptor
leaves `local_service` out, and the CLI's part is the fail-fast check.

The `auto` trusted-runtime network setting keeps host-launched supervisors on
Docker's host network. Under local Docker-in-Docker it shares the processor's
network namespace, which already contains the normal internal-service network
and the private secrets-service network. This gives the trusted supervisor a
route to `kdcube-secrets` without publishing the broker. A split generated-code
executor remains a separate container with `--network none`, no broker token,
no descriptor payload, and no deployment identity. Host-vault preflight rejects
another network mode because a secret-using trusted tool would otherwise fail
only when invoked.

### Shadow-stage existing file secrets

With the host vault enrolled and the file-backed runtime running, project the
shadow configuration and recreate only the broker:

```bash
kdcube secrets backend host-vault prepare \
  --tenant demo-tenant --project demo-project \
  --dry-run --json

kdcube secrets backend host-vault prepare \
  --tenant demo-tenant --project demo-project \
  --json
```

Preparation never reads or copies a provider secret, changes the provider, or
restarts `chat-ingress` or `chat-proc`. With the shadow broker healthy and
`secrets-file` still selected, inspect the destination without writing:

```bash
kdcube secrets backend host-vault stage \
  --tenant demo-tenant --project demo-project \
  --dry-run --json
```

Then stage and verify all configured non-placeholder values:

```bash
kdcube secrets backend host-vault stage \
  --tenant demo-tenant --project demo-project \
  --json
```

The CLI reads the owner-only `secrets.yaml` and `bundles.secrets.yaml` in its
trusted process. Values travel to `secretsctl` over stdin and never appear in
process arguments. Verification hashes the candidate in the CLI and compares
it with the stored value inside the broker; neither value is returned. The
result contains counts only.

Staging is idempotent and default-closed:

- every destination value is checked before the first write;
- an existing different value aborts the whole run before writes;
- an absent value is created with `expected_generation: 0`, so a racing or
  previously tombstoned record is not overwritten;
- each create is read back and compared, followed by a complete final pass;
- a failure leaves successful copies in place and leaves the source provider
  untouched, so the next run resumes by accepting exact matches;
- placeholders are counted and skipped;
- no command changes `secrets.provider` or deletes plaintext source files.

The shadow stage establishes destination parity. It is not activation. Keep
`secrets-file` selected until the secret-using runtime regression suite passes
against an explicitly activated test deployment. Plaintext cleanup remains a
later, separately confirmed operator action.

### Activate the staged provider

Check parity and runtime readiness without changing a file or container:

```bash
kdcube secrets backend host-vault activate \
  --tenant demo-tenant --project demo-project \
  --dry-run --json
```

Then perform the explicit provider switch:

```bash
kdcube secrets backend host-vault activate \
  --tenant demo-tenant --project demo-project \
  --yes --json
```

Without `--yes`, an interactive terminal asks for confirmation. Automation
and JSON mode require `--yes`. `--wait-seconds` bounds each broker and
consumer readiness check from 5 to 600 seconds; the default is 120.

Activation is a bounded local-Compose transaction:

1. It requires `kdcube-secrets`, `chat-ingress`, and `chat-proc` to be
   running and verifies that every current file-backed value already matches
   the host vault.
2. It durably creates `config/.host-vault-activation.pending.json`. The
   owner-only marker contains only schema, phase, and recovery-mode metadata;
   it contains no value, key name, digest, token, path, or identity material.
3. It quiesces ingress and proc so neither can write a file-backed secret
   while the final inventory is checked.
4. It changes `assembly.secrets.provider` and the two generated consumer
   environments to `secrets-service`.
5. It recreates the broker first and both consumers second with one temporary
   token overlay. Tokens remain out of process arguments and are not persisted
   by activation.
6. It resolves one staged value independently inside ingress and proc and
   compares digests without returning the value. It also rejects a source
   inventory change observed during the switch, then durably removes the
   marker.

Any ordinary failure after quiescing restores the exact prior configuration,
recreates the shadow-mode runtime, and verifies file-backed reads. If the host
vault itself prevents that exact restart, recovery selects the ephemeral
sidecar with `secrets-file`, preserving availability and the plaintext source.

If the CLI process or host stops after the marker is durable and before it is
removed, ordinary `kdcube start` refuses the unresolved transaction. Recover
to a known file-backed state before retrying activation:

```bash
kdcube secrets backend host-vault recover \
  --tenant demo-tenant --project demo-project \
  --yes --json
```

Recovery is repeatable. It selects `secrets-file` with the ephemeral sidecar,
recreates the broker, ingress, and proc, verifies a real file-backed read in
both consumers, and only then removes the marker. It does not depend on marker
contents to reconstruct configuration and never deletes the plaintext source.
If recovery fails, the marker remains and startup continues to fail closed.
This covers interrupted configuration activation; service, Docker, and host
restart durability of the active vault is still a separate acceptance gate.

### Normal operation after activation

The provider contract remains uniform after cutover:

| Intent | Surface | Durable effect |
| --- | --- | --- |
| Resolve a secret for Brave, Slack, an external MCP connector, or another trusted provider adapter | Runtime `get_secret()` through `SecretsServiceSecretsManager` | Read the exact host-vault record; no descriptor mutation. |
| Add or replace a bundle/user secret from an existing authenticated settings surface | Existing KDCube secret mutation API | Commit the value through the broker and refresh provider-derived inventory. |
| Let an approved agent or operator inspect, set, retrieve, or delete one exact secret | Delegated KDCube management API through `kdcube secrets ...`; Connection Hub may supply its stored OAuth session | Enforce the live Card operation and invocation policy, then use the same broker. |
| Reconstruct selected or whole descriptor files for the administrator | `kdcube secrets export` plus browser approval | Create a new administrator-controlled output directory once; active vault records remain unchanged. |
| Restore an administrator's literal private pair | `connection-hub secrets host import` with its stored OAuth session, or `kdcube config import` with an issued delegated bearer | Upsert present values through live Card authority; omitted keys remain unchanged. |
| Revoke the deployment's vault access | `hostvaultctl revoke` as the vault operator | Reject the broker certificate on its next connection. |
| Rotate deployment identity or the root key | `hostvaultctl rotate-identity` or `rotate-root-key` | Replace identity with bounded overlap, or rewrap data keys without changing secret values. |

There is no automatic vault-to-descriptor synchronization. The old source
files are rollback material until the operator explicitly cleans them up.
Later writes change the selected provider only. Human export is the explicit
reverse path: exact export names every requested key, while whole export
freezes the complete provider inventory before approval. Whole output includes
platform, deployment-bundle, user, and user-bundle values and reconstructs the
ordinary `secrets.yaml` and `bundles.secrets.yaml` pair in the configuration
export directory.

Broker verification retries a small number of transient connection and
`502`/`503`/`504` failures. Permission, conflict, and malformed-request errors
remain immediate failures.

This selection is local-Compose-specific. ECS descriptors continue to select
`aws-sm`; no ECS task definition, IAM policy, or Terraform path is changed by
the host-vault switch.

## 8. Audit

Every handled request appends one event: time, deployment id, certificate
fingerprint, operation, application, reference digest, request id, result
code, generation, and expected generation. Names and values never appear.
`FileAuditSink` opens the log with `O_APPEND` and fsyncs each line.

## 9. Proofs

`kdcube_ai_app/infra/secrets/host_vault/tests/test_host_vault.py` runs with
fake certificates and the labeled in-memory root-key provider and covers:

- exact namespace authorization with cross-tenant, cross-project, and cross-application denial
- certificate identity against the live registry, including a same-id certificate from a foreign CA
- one-use and expiring enrollment tickets
- revoked and expired identities, rotation overlap and lapse
- revocation from another process landing on the next identification
- process-lifetime replay with the same and with a different body, a stale
  `issued_at`, and an explicit proof that the receipt cache resets on restart
- generation conflicts on set, rotate, and delete
- partial OS writes and a crash between candidate write and commit
- restart durability of values and deletions
- tampered ciphertext, metadata, and root-key version failing closed,
  including rotation refusing to rewrite a record that fails normal decode
- audit fields without secret bytes
- backend exceptions with canaries sanitized
- a stateless broker that acknowledges only committed writes
- root-key rotation with rewrap, and identity file modes
- a real mTLS round trip, a bearer without a client certificate refused at the handshake and at the service, and sanitized TLS failures

Run from the repository root with the platform venv interpreter:

```bash
PYTHONPATH=app/ai-app/src/kdcube-ai-app \
app/venvs/ai-app/chat-processor/bin/python -m pytest \
  app/ai-app/src/kdcube-ai-app/kdcube_ai_app/infra/secrets/host_vault/tests \
  --import-mode=importlib -q
```

`--import-mode=importlib` matters: `infra/` has no `__init__.py`, and the
default import mode would put `kdcube_ai_app/infra` on `sys.path`, where
the `secrets` package shadows the standard library module.

## 10. Current gates

The protocol, opt-in local Compose broker, focused shadow preparation,
idempotent file-to-vault shadow stage, operator-confirmed activation,
automatic ordinary-failure rollback, and explicit interrupted-activation
recovery are implemented. The remaining gates are deliberately separate:

- run Connection Hub connector create, invoke, replace, and delete plus Brave
  and connected-account regressions against the broker-backed provider
- prove service, Docker, and host restart durability and deployment-identity
  revocation on a real machine
- prove a complete split-runtime secret-using tool call with the broker enabled;
  parent-network routing and the networkless executor are covered separately
- package the host vault as a dedicated service-owned appliance or VM and add
  its enrollment/install lifecycle
- run the CLI-ensured `local_service` start on a Linux host under Docker Engine
  and under Docker Desktop, and make the vault's root-key custody check
  portable before any native Windows support is claimed
- remove plaintext source values only through a later explicit cleanup action
  after activation and durability acceptance

Generated or isolated code never receives vault credentials, the deployment
private key, unrestricted references, or a general vault client. Trusted
supervisor services project only the values and operations they choose.
