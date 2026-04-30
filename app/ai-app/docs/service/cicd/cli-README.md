---
id: ks:docs/service/cicd/cli-README.md
title: "Current KDCube CLI"
summary: "Current implemented CLI surface for local environment bootstrapping, workdir preparation, Docker Compose startup, descriptor validation, and the practical rule that multiple namespaced runtime snapshots may exist on one machine while local compose-backed execution remains one active deployment at a time."
tags: ["service", "cicd", "cli", "env", "deployment"]
keywords: ["kdcube cli", "local environment bootstrap", "workdir setup", "docker compose control", "descriptor validation", "current cli contract", "local deployment tooling", "multiple local runtime snapshots", "single active local deployment", "tenant project workdir namespace"]
see_also:
  - ks:docs/service/cicd/release-README.md
  - ks:docs/service/cicd/descriptors-README.md
  - ks:docs/service/cicd/design/cli--as-control-plane-README.md
  - ks:docs/configuration/assembly-descriptor-README.md
  - ks:docs/configuration/secrets-descriptor-README.md
  - ks:docs/configuration/bundles-descriptor-README.md
  - ks:docs/configuration/service-runtime-configuration-mapping-README.md
  - ks:docs/configuration/gateway-descriptor-README.md
  - ks:docs/service/environment/setup-dev-env-README.md
  - ks:docs/service/environment/setup-for-dockercompose-README.md
---
# KDCube CLI (Design)

This document defines the **initial CLI surface** and behavior. The CLI is for:

- **Platform developers** running services on host (PyCharm/IntelliJ or shell).
- **Compose users** running the all‑in‑one stack.
- **Release tooling** (validate and render assembly descriptors).

CLI root (code): `src/kdcube-ai-app/kdcube_cli`

---

## Current vs Planned

This page documents the current implemented CLI contract.

Use it for:

- commands that exist now
- current runtime/workdir behavior
- current descriptor-driven local install flow

Do not read it as the future deployment-first CLI contract.

That future model is tracked separately here:

- [cli--as-control-plane-README.md](design/cli--as-control-plane-README.md)

Short version of the difference:

- current CLI: centered on bootstrapping and reusing a concrete local runtime workdir
- planned CLI: centered on deployment identity first (`tenant/project`, defaults, profiles, start/stop/reload/export as first-class operations)

In both models, the deployment boundary is the same:

- one `tenant/project` = one isolated environment
- use separate `tenant/project` values for customer isolation or lifecycle
  stages such as `dev`, `staging`, and `prod`
- keep multiple bundles inside one `tenant/project` when they belong to the
  same environment

So `tenant/project` is the environment boundary, not the bundle boundary.

Important local-runtime rule:

- one machine may contain many local runtime snapshots under different
  `tenant/project` namespaces
- the current compose-backed local runtime should still be treated as a
  one-active-deployment-at-a-time model by default
- do not assume that two different runtime workdirs imply two safely concurrent
  local KDCube stacks

## 1) Immediate use cases

1) **Generate local env files (platform dev)**
   - Create `.env` files in service locations
   - Create required local directories
   - Merge with existing `.env` values

2) **Generate compose env files (all‑in‑one)**
   - Produce `.env.*` files in `deployment/docker/all_in_one_kdcube`
   - Create data folders

2b) **Compose with custom UI (advanced)**
   - Use an `assembly.yaml` that includes a `frontend` section
   - If `frontend.image` is set, the UI build is skipped
   - If `frontend.build` is set, the UI repo is cloned and built
   - Switches compose mode to `custom‑ui‑managed‑infra`

3) **Validate assembly descriptor**
   - Validate schema + refs

4) **Apply bundle descriptors**
   - Use `bundles.yaml` (+ `bundles.secrets.yaml`) to seed runtime bundles + secrets

---

## 2) Commands (initial)

Note for Debian/Ubuntu operators: recent system Python builds may block direct
`pip install` into the system environment with `externally-managed-environment`
(PEP 668). In that case, install the CLI into a dedicated virtual environment,
for example:

```bash
sudo apt-get update
sudo apt-get install -y python3-pip python3-venv
python3 -m venv ~/.venvs/kdcube-cli
~/.venvs/kdcube-cli/bin/pip install -e /path/to/kdcube_cli
~/.venvs/kdcube-cli/bin/kdcube --help
```

### 2.1 `kdcube env init` (platform dev, host)

**Goal:** Create envs for running ingress/proc on host + infra via `local-infra-stack`.

```
kdcube env init \
  --mode dev-host \
  --repo /path/to/kdcube-ai-app
```

**Sources:**
- `deployment/docker/local-infra-stack/sample_env/*`

**Targets (default paths):**
- `deployment/docker/local-infra-stack/.env`
- `deployment/docker/local-infra-stack/.env.postgres.setup`
- `deployment/docker/local-infra-stack/.env.proxylogin`
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/.env.ingress`
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/proc/.env.proc`
- `src/kdcube-ai-app/kdcube_ai_app/apps/metrics/.env.metrics`
- `ui/chat-web-app/public/private/config.hardcoded.json` (generated UI config)

**Folders created (default set):**
- `deployment/docker/local-infra-stack/data/postgres`
- `deployment/docker/local-infra-stack/data/redis`
- `deployment/docker/local-infra-stack/data/clamav-db`
- `deployment/docker/local-infra-stack/data/bundles`
- `deployment/docker/local-infra-stack/data/bundle-local-storage`
- `deployment/docker/local-infra-stack/data/exec-workspace`
- `deployment/docker/local-infra-stack/logs`

### 2.2 `kdcube env init` (compose)

```
kdcube env init \
  --mode compose \
  --repo /path/to/kdcube-ai-app
```

**Sources:**
- `deployment/docker/all_in_one_kdcube/sample_env/*`

**Targets:**
- `<workdir>/config/.env`
- `<workdir>/config/.env.ingress`
- `<workdir>/config/.env.proc`
- `<workdir>/config/.env.metrics`
- `<workdir>/config/.env.postgres.setup`
- `<workdir>/config/.env.proxylogin` (optional)
- `<workdir>/config/frontend.config.<mode>.json`

**Folders created:**
- `<workdir>/data/*` (same subfolders as above)
- `<workdir>/logs/*`

### 2.3 Custom UI via assembly descriptor (compose)

When `assembly.yaml` contains a `frontend` section, the CLI uses
**custom‑ui‑managed‑infra** compose mode:

- `frontend.image` → use a prebuilt UI image (skip build)
- `frontend.build` → clone repo and build UI

The CLI also generates runtime `config.json` from `frontend_config`.
If `frontend_config` is omitted, it falls back to a built-in template based on auth mode:
- `simple` -> `config.hardcoded.json`
- `cognito` -> `config.cognito.json`
- `delegated` -> `config.delegated.json`

If `nginx_ui_config` is omitted, the CLI falls back to the built-in `nginx_ui.conf`.

When `proxy.ssl: true` and `assembly.domain` is set, the CLI also patches the
runtime nginx SSL config so `YOUR_DOMAIN_NAME` is replaced in `server_name` and
default Let’s Encrypt cert paths under `/etc/letsencrypt/live/<domain>/...`.
See: [docs/configuration/assembly-descriptor-README.md](../../configuration/assembly-descriptor-README.md)

If `platform.ref` is present in the descriptor, the install source selector
adds **assembly-descriptor**, which pulls that tag from DockerHub.

The wizard asks whether to apply the descriptor to **frontend**
and/or **platform** (these can be enabled independently).

If no path is provided, the wizard uses `config/assembly.yaml` in the workdir
and seeds it from `deployment/assembly.yaml`.

When an assembly descriptor is provided, the wizard writes non‑secret values
back into `assembly.yaml` (tenant/project, auth, infra, paths) and then renders
`.env*` from it. The assembly file becomes the source of truth for local config.

The CLI also mounts descriptor files into runtime services so code can read
plain non-secret descriptor values directly:

- `/config/assembly.yaml`
- `/config/bundles.yaml`

This is the runtime contract behind `read_plain(...)` / `get_plain(...)`.
See:
[docs/configuration/service-runtime-configuration-mapping-README.md](../../configuration/service-runtime-configuration-mapping-README.md)

The same descriptor also controls workspace/session bootstrap settings for agent runtimes:

- `storage.workspace.type` -> `REACT_WORKSPACE_IMPLEMENTATION`
- `storage.workspace.repo` -> `REACT_WORKSPACE_GIT_REPO`
- `storage.claude_code_session.type` -> `CLAUDE_CODE_SESSION_STORE_IMPLEMENTATION`
- `storage.claude_code_session.repo` -> `CLAUDE_CODE_SESSION_GIT_REPO`

Repo field contract:

- `platform.repo` and `frontend.build.repo` should use a cloneable repo spec:
  - `git@github.com:org/repo.git`
  - `https://github.com/org/repo.git`
  - `org/repo`
- older single-name values such as `kdcube-ai-app` are still accepted for
  backward compatibility, but new descriptors should use one of the cloneable
  forms above

### 2.3a Descriptor folder fast path

The CLI now supports a descriptor-folder driven install path:

```bash
kdcube init \
  --descriptors-location /path/to/descriptors \
  --workdir ~/.kdcube/kdcube-runtime
```

`--workdir` controls where the runtime is installed. In descriptor mode the CLI
reads `assembly.yaml -> context.tenant/project` and creates the concrete runtime
under:

```text
<workdir>/<safe_tenant>__<safe_project>
```

`--path` controls the local platform source tree only when you explicitly pass
it to `init`. Without `--upstream`, `--latest`, or `--release`, explicit
`--path` means: copy this dirty local checkout into the concrete runtime and
use that staged copy. The copy includes tracked files plus untracked files that
are not ignored by git, and excludes `.git` plus gitignored runtime/data files.

```bash
kdcube init \
  --descriptors-location /path/to/descriptors \
  --workdir ~/.kdcube/kdcube-runtime \
  --path /path/to/kdcube-ai-app \
  --build
```

If `--upstream`, `--latest`, or `--release` is provided, that version selector
wins and `--path` is only the local repo/cache path for the selected source.

Expected folder contents:

- `assembly.yaml`
- `secrets.yaml`
- `gateway.yaml`
- optional `bundles.yaml`
- optional `bundles.secrets.yaml`

If the descriptor set is complete enough, the CLI skips interactive questions
and proceeds directly to a **release install**.

Use `--latest` to resolve the platform release from the platform repo instead
of using `assembly.yaml -> platform.ref`:

```bash
kdcube init \
  --descriptors-location /path/to/descriptors \
  --latest
```

Use `--upstream` to initialize from the latest upstream repo state
(`origin/main`) instead of a released ref:

```bash
kdcube init \
  --descriptors-location /path/to/descriptors \
  --upstream
```

Use `init --build` when you also want the runtime prepared with freshly built
local images before the stack is started:

```bash
kdcube init \
  --descriptors-location /path/to/descriptors \
  --upstream \
  --build
```

Use `--release` to pin a specific released platform ref explicitly:

```bash
kdcube init \
  --descriptors-location /path/to/descriptors \
  --release 2026.4.11.012
```

Choose exactly one source selector:

- `--upstream` for the latest upstream repo state
- `--latest` for the latest released platform ref
- `--release <ref>` for a specific released ref
- explicit `--path <repo>` without the selectors above for dirty local source
  staging
- otherwise `assembly.yaml -> platform.ref`

Build rule:

- `init --build` builds images after staging the runtime, but does not start containers
- `start --build` is a convenience rebuild before starting an already initialized runtime

Fast-path requirements:

- `assembly.yaml` exists
- `secrets.yaml` exists
- `gateway.yaml` exists
- `assembly.secrets.provider == "secrets-file"`
- `assembly.context.tenant` and `assembly.context.project` are set
- `assembly.paths.host_bundles_path` is set
- `assembly.platform.ref` is set unless `--latest`, `--upstream`, or `--release` is used
- if `proxy.ssl: true`, `assembly.domain` is set
- if `storage.workspace.type == git`, `storage.workspace.repo` is set
- if `storage.claude_code_session.type == git`, `storage.claude_code_session.repo` is set
- if `auth.type` is `cognito` or `delegated`, the required Cognito fields are set
- if `frontend` is present and no `frontend.image` is set, the required
  `frontend.build.*` fields and `frontend.frontend_config` are set

If any of those are missing, the CLI falls back to the guided setup and prints
the missing reasons.

### 2.3b Export live effective bundle descriptors

For ECS / `aws-sm` deployments, the current effective live deployment-scoped
bundle state can be exported directly from AWS Secrets Manager:

```bash
kdcube export \
  --tenant <tenant> \
  --project <project> \
  --aws-region <region> \
  --out-dir /tmp/kdcube-export
```

Optional:

- `--aws-profile <profile>`
- `--aws-sm-prefix <prefix>`

This reconstructs:

- `bundles.yaml`
- `bundles.secrets.yaml`

from the authoritative grouped AWS SM documents:

- `<prefix>/bundles-meta`
- `<prefix>/bundles/<bundle_id>/descriptor`
- `<prefix>/bundles/<bundle_id>/secrets`

This is the correct export path for current live ECS state. It does not read:

- Redis
- mounted `/config/bundles.yaml`
- GitHub secrets blobs

Operational rule for `aws-sm` deployments:

1. export the current live bundle state before the next provision
2. reconcile that export into the private descriptor source-of-truth files
3. copy the approved file contents into the GitHub Environment secrets
4. run provision

If you skip that step, a later provision can replay stale `BUNDLES_YAML` or
`BUNDLES_SECRETS_YAML` and overwrite runtime bundle changes.

### 2.4 Bundles descriptor (optional)

You can provide a **bundles descriptor** (`bundles.yaml`) and an optional
**bundles secrets** file (`bundles.secrets.yaml`). This is the preferred way
to configure bundles and bundle secrets.

When provided, the CLI:
- mounts the runtime workspace `config/` directory at `/config`
- injects secrets from `bundles.secrets.yaml` into the secrets sidecar

Current proc behavior:

- `config/bundles.yaml` is the normal bundle descriptor authority
- proc can seed/reset from that descriptor directly

Local bundle root contract:

- `assembly.paths.host_kdcube_storage_path` becomes `HOST_KDCUBE_STORAGE_PATH`
- `assembly.paths.host_bundle_storage_path` becomes `HOST_BUNDLE_STORAGE_PATH`
- `assembly.paths.host_exec_workspace_path` becomes `HOST_EXEC_WORKSPACE_PATH`
- if `assembly.storage.kdcube` is a local host `file://...` URI, init uses that host path as `HOST_KDCUBE_STORAGE_PATH` and rewrites the staged runtime descriptor to `file:///kdcube-storage`
- if `assembly.storage.bundles` is a local host `file://...` URI, init uses that host path as `HOST_BUNDLE_STORAGE_PATH` and rewrites the staged runtime descriptor to `file:///bundle-storage`
- compose mounts `HOST_KDCUBE_STORAGE_PATH` into proc/ingress/metrics as `/kdcube-storage`
- `assembly.paths.host_bundles_path` is installer-facing config for non-managed local path bundles and is written to `HOST_BUNDLES_PATH`
- compose mounts `HOST_BUNDLES_PATH` into proc as `BUNDLES_ROOT` (normally `/bundles`)
- non-managed local bundle entries in `bundles.yaml` must therefore use the container-visible path, for example:
  - host folder: `/Users/you/dev/bundles/my.bundle`
  - descriptor path: `/bundles/my.bundle`

- `assembly.paths.host_managed_bundles_path` becomes `HOST_MANAGED_BUNDLES_PATH`
- compose mounts `HOST_MANAGED_BUNDLES_PATH` into proc as `MANAGED_BUNDLES_ROOT` (normally `/managed-bundles`)
- runtime code now reads these promoted values via `get_settings()` when it needs host/container path translation

Managed bundle materialization uses the dedicated managed root:

- non-managed local path bundles continue to use `HOST_BUNDLES_PATH` and `/bundles/...`
- git bundles are cloned under `HOST_MANAGED_BUNDLES_PATH` and resolved inside proc as `/managed-bundles/...`
- built-in example bundles are also materialized under the managed root

Symlink note:

- if you symlink a bundle into `HOST_BUNDLES_PATH`, proc sees the symlink through the `/bundles` mount
- this works only if Docker can also access the symlink target on the host
- safest local pattern: keep the real bundle folder inside `HOST_BUNDLES_PATH`, or symlink only to another host path that is already accessible through the same Docker file-sharing scope

The active bundle descriptor authority controls proc bundle-registry seeding.
It is separate from the broader descriptor mounts used by `read_plain(...)`.

In `aws-sm` deployments, `bundles.yaml` is the descriptor/export shape, but the
live authoritative deployment-scoped bundle state is stored in grouped AWS SM
documents and can be exported back with `kdcube export`. In that mode,
mounted `/config/bundles.yaml` is only a deploy snapshot, not the live
authoritative store.

Templates:
- [`deployment/bundles.yaml`](../../../deployment/bundles.yaml)
- [`deployment/bundles.secrets.yaml`](../../../deployment/bundles.secrets.yaml)

### 2.5 Secrets descriptor (optional)
If you provide a `secrets.yaml`, the CLI stages it into the workdir and can use
it in two ways:

- to prefill runtime secrets and sensitive infra passwords during guided setup
- as the runtime secrets source when `assembly.yaml -> secrets.provider` is
  `secrets-file`

In `secrets-file` mode, the CLI mounts:

- `/config/secrets.yaml`
- `/config/bundles.secrets.yaml`

Runtime resolves those files from the staged workspace descriptor directory via
`PLATFORM_DESCRIPTORS_DIR=/config`.

See: [docs/configuration/secrets-descriptor-README.md](../../configuration/secrets-descriptor-README.md)

### 2.6 Gateway config descriptor (optional)
If you provide a `gateway.yaml`, the CLI stages it into `workdir/config` and
uses it as the runtime descriptor authority for gateway policy.

In current descriptor mode:

- compose mounts the runtime workspace `config/` directory at `/config`
- `.env.ingress`, `.env.proc`, and `.env.metrics` point runtime to `/config`
  via `PLATFORM_DESCRIPTORS_DIR=/config`

If a platform later injects `GATEWAY_CONFIG_JSON`, that JSON still wins at
runtime because gateway loader precedence prefers it over `gateway.yaml`.

Template: [`deployment/gateway.yaml`](../../../deployment/gateway.yaml)

You can skip the prompt by setting:
```
KDCUBE_GATEWAY_DESCRIPTOR_PATH=/path/to/gateway.yaml
```

See: [docs/configuration/gateway-descriptor-README.md](../../configuration/gateway-descriptor-README.md)

### 2.7 `kdcube release validate`

```
kdcube release validate --file assembly.yaml
```

Validates assembly descriptor schema and prints errors with line numbers.

### 2.8 `kdcube release render-bundles`

```
kdcube release render-bundles --file bundles.yaml --out bundles.json
```

Stages `bundles.yaml` as the runtime bundle descriptor authority for proc.

---

## 3) CLI env overrides

You can also pre‑seed paths and flags via environment variables:

| Variable | Description |
| --- | --- |
| `KDCUBE_ASSEMBLY_DESCRIPTOR_PATH` | Path to `assembly.yaml` (copied into workdir config). |
| `KDCUBE_ASSEMBLY_USE_FRONTEND` | `1/0` to apply assembly frontend config. |
| `KDCUBE_ASSEMBLY_USE_PLATFORM` | `1/0` to apply assembly platform config. |
| `KDCUBE_BUNDLES_DESCRIPTOR_PATH` | Path to `bundles.yaml` (copied into workdir config). |
| `KDCUBE_BUNDLES_SECRETS_PATH` | Path to `bundles.secrets.yaml` (used to inject secrets). |
| `KDCUBE_USE_BUNDLES_DESCRIPTOR` | `1/0` to apply bundles descriptor. |
| `KDCUBE_USE_BUNDLES_SECRETS` | `1/0` to apply bundles secrets. |
| `KDCUBE_GATEWAY_DESCRIPTOR_PATH` | Path to `gateway.yaml` (copied into workdir config). |
| `KDCUBE_CLI_NONINTERACTIVE` | Internal installer flag. Prompt helpers use defaults instead of asking. The CLI sets this automatically for the descriptor fast path. |

---

## 4) Env merge semantics

The CLI **never overwrites existing values** by default.

Rules (default):
1) If a key exists in target `.env` and is non‑empty → **keep**.
2) If a key exists but is empty → fill from template if available.
3) If a key is missing → add from template.

**Secrets are never printed**, but the CLI reports missing values as:
```
MISSING (secret)
```

### 4.1 Update mode (explicit)

To overwrite existing values, use:

```
kdcube env init --mode dev-host --repo ... --update
```

`--update` will:
- overwrite non‑secret values
- **never** overwrite secrets unless `--update-secrets` is explicitly provided

---

## 5) Secret handling (default)

Keys treated as secrets by default (pattern‑based):
- `*_SECRET`, `*_TOKEN`, `*_KEY`, `*_PASSWORD`
- `AWS_*`, `OPENAI_*`, `ANTHROPIC_*`, `STRIPE_*`

Secrets are written to env files if provided by templates or overrides, but **never printed**.

---

## 6) Overrides

You can override any value:

```
kdcube env init --mode dev-host --repo ... \
  --set EXEC_WORKSPACE_ROOT=/path/to/exec \
  --set BUNDLES_ROOT=/bundles
```

Overrides apply **after** merge rules.

---

## 7) Sample bundles (local only)

If `bundles.yaml` is empty, the CLI can seed sample bundles:

```
kdcube bundles seed --preset samples
```

This is intended for **local development** only.

---

## 8) Future commands (next phase)

- `kdcube doctor` (validate env + filesystem + runtime dependencies)
- `kdcube compose up` (wrapper around docker compose)
- `kdcube release tag` (tag + VERSION validation)

## 9) Operational commands

Inspect global CLI state (defaults + running deployment):

```bash
kdcube --info
```

Inspect a specific workdir deployment:

```bash
kdcube --info --workdir ~/.kdcube/kdcube-runtime/acme__prod
```

Initialize a workdir without starting Docker:

```bash
kdcube init \
  --workdir ~/.kdcube/kdcube-runtime \
  --descriptors-location /path/to/descriptors \
  --latest
```

Initialize and build images without starting Docker:

```bash
kdcube init \
  --workdir ~/.kdcube/kdcube-runtime \
  --descriptors-location /path/to/descriptors \
  --upstream \
  --build
```

Initialize and build from dirty local platform sources:

```bash
kdcube init \
  --workdir ~/.kdcube/kdcube-runtime \
  --descriptors-location /path/to/descriptors \
  --path /path/to/kdcube-ai-app \
  --build
```

Use this for uncommitted platform changes. Do not combine this flow with
`--upstream`, `--latest`, or `--release`.

Start the stack for an already-initialized workdir:

```bash
kdcube start --workdir ~/.kdcube/kdcube-runtime/acme__prod
```

Reload a bundle after descriptor changes:

```bash
kdcube reload <bundle_id> --workdir ~/.kdcube/kdcube-runtime/acme__prod
```

Export live bundle descriptors:

```bash
kdcube export --workdir ~/.kdcube/kdcube-runtime/acme__prod --out-dir /tmp/export
```

Stop the local workdir stack:

```bash
kdcube stop --workdir ~/.kdcube/kdcube-runtime
```

Stop and remove volumes too:

```bash
kdcube stop --workdir ~/.kdcube/kdcube-runtime --remove-volumes
```

Save operator defaults:

```bash
kdcube defaults \
  --default-workdir ~/.kdcube/kdcube-runtime \
  --default-tenant acme \
  --default-project prod
```

---

## 10) Operator defaults (`kdcube defaults`)

`kdcube defaults` persists values to `~/.kdcube/cli-defaults.json`:

| Field | Flag | Purpose |
|---|---|---|
| `default_workdir` | `--default-workdir` | Fallback workdir when `--workdir` is omitted from a subcommand |
| `default_tenant` | `--default-tenant` | Displayed in global `--info`; used by `kdcube export` as fallback tenant |
| `default_project` | `--default-project` | Displayed in global `--info`; used by `kdcube export` as fallback project |

`kdcube start`, `kdcube stop`, `kdcube reload`, and `kdcube export` resolve the
target workdir with the following precedence:

1. `--workdir` passed explicitly → use it.
2. `--workdir` omitted, `default_workdir` present in `cli-defaults.json` → use
   that.
3. Neither provided → error:
   ```
   No target workdir specified.
   Pass --workdir explicitly or configure a default:
     kdcube defaults --default-workdir <path>
   ```

`kdcube --info` (without `--workdir`) reads `cli-defaults.json` and displays the
configured values, or reports that no defaults are set.

---

## 11) Single-deployment guard (`cli-lock.json`)

The file `~/.kdcube/cli-lock.json` is a per-machine deployment lock.

It is written when a deployment starts and cleared when it stops.

Format:

```json
{
  "tenant": "...",
  "project": "...",
  "workdir": "...",
  "docker_dir": "...",
  "env_file": "..."
}
```

### Guard at start (`kdcube start`)

Before starting, the CLI reads the lock and runs `docker compose ps` against it:

- No lock → proceed normally.
- Lock matches the target `tenant/project` → proceed (same deployment restart).
- Lock points to a **different** deployment and services are **live** → abort with
  a message showing which deployment is running and how to stop it first.
- Lock points to a different deployment but services are **not live** (stale) →
  lock is cleared automatically, start proceeds.

### Guard at stop (`kdcube stop`)

Before stopping, the CLI checks:

1. `docker compose ps` for the target workdir — if nothing is running →
   `"Deployment is not running"`.
2. If something is running and the lock matches the target `tenant/project` →
   stop and clear the lock.
3. If something is running but the lock points to a **different** deployment →
   abort with a message identifying the running deployment.

### `kdcube --info` and stale locks

Global `kdcube --info` (without `--workdir`) verifies the recorded lock via
`docker compose ps`. If the recorded deployment is no longer running, the lock
is considered stale, reported as such, and cleared automatically.
