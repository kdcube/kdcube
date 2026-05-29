---
id: ks:docs/service/cicd/cli-README.md
title: "Current KDCube CLI"
summary: "Current implemented CLI surface for local environment bootstrapping, workdir preparation, Docker Compose startup, descriptor validation, bundle config and secret patching, and the practical rule that multiple namespaced runtime snapshots may exist on one machine while local compose-backed execution remains one active deployment at a time."
tags: ["service", "cicd", "cli", "env", "deployment", "bundle"]
keywords: ["kdcube cli", "local environment bootstrap", "workdir setup", "docker compose control", "descriptor validation", "current cli contract", "local deployment tooling", "multiple local runtime snapshots", "single active local deployment", "tenant project workdir namespace", "bundle config patch", "bundle secret patch", "kdcube bundle command"]
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
- use separate `tenant/project` values for account isolation or lifecycle
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
   - Use an `assembly.yaml` that includes `frontend.build` or `frontend.image`
   - If `frontend.image` is set, the UI build is skipped
   - If `frontend.build` is set, the UI repo is cloned and built
   - Switches compose mode to `custom‑ui‑managed‑infra`
   - `frontend.config` alone does **not** trigger this mode

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
- `deployment/docker/local-infra-stack/data/react-debug`
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

When `assembly.yaml` contains `frontend.build` or `frontend.image`, the CLI uses
**custom‑ui‑managed‑infra** compose mode:

- `frontend.image` → use a prebuilt UI image (skip build)
- `frontend.build` → clone repo and build UI

`frontend.config` (auth type, routes prefix, debug flags) is runtime metadata consumed in
both modes and does **not** trigger `custom‑ui‑managed‑infra` on its own.

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

### 2.3a Plain init (no descriptor source required)

`kdcube init` is **first-time setup only**. It refuses if the target workdir
already has `install-meta.json`. To rebuild platform images / restart on an
existing workdir, use [`kdcube refresh`](#refresh) (see §2.4).

Primary form — pass a tenant and a project; the CLI creates the runtime under
the platform default base `~/.kdcube/kdcube-runtime/<tenant>__<project>/`:

```bash
kdcube init --tenant acme --project prod
```

`-i` enables interactive prompting for fields missing from the assembly:

```bash
kdcube init --tenant acme --project prod -i
```

To stage common local service secrets without editing YAML:

```bash
kdcube init --tenant acme --project prod --prompt-secrets
```

To add a public HTTPS origin, for example an ngrok origin, to
`assembly.yaml -> cors.allow_origins` during init:

```bash
kdcube init --tenant acme --project prod \
  --cors-origin https://<stable-ngrok-domain>
```

`--cors-origin` is repeatable and accepts either a full origin or a hostname.
If the scheme is omitted, the CLI assumes `https://`.

To provide known secret values non-interactively, use dotted descriptor keys:

```bash
kdcube init --tenant acme --project prod \
  --set-secret services.openai.api_key "sk-..." \
  --set-secret services.anthropic.api_key "sk-ant-..." \
  --set-secret services.git.http_token "ghp_..."
```

These values are written to the staged runtime `config/secrets.yaml`; they are
not written to `.env` files.

All version selectors work the same way without `--descriptors-location`:

```bash
kdcube init --tenant acme --project prod --latest
kdcube init --tenant acme --project prod --upstream
kdcube init --tenant acme --project prod --release 2026.4.30.317
kdcube init --tenant acme --project prod --build
```

`--latest`, `--upstream`, and `--release` are mutually exclusive — combining
any two raises an error.

#### Advanced placement: `--workdir` / `--workdir-base`

For runtimes that must live outside the default base
(`~/.kdcube/kdcube-runtime`), `init` and `refresh` accept two additional
forms:

```bash
# Explicit fully-qualified namespaced runtime path:
kdcube init --workdir /opt/kdcube/acme__prod

# Non-default base + tenant + project:
kdcube init --workdir-base /opt/kdcube --tenant acme --project prod
```

With `--workdir-base`, both `--tenant` and `--project` are required. With
`--workdir`, the trailing path segment must contain `__`; if
`--tenant`/`--project` are also passed they must match the trailing segment.
`--workdir` and `--workdir-base` are mutually exclusive.

### 2.3b Descriptor folder fast path

The CLI also supports a descriptor-folder driven install path. Primary form
uses the default base — just specify the tenant and project:

```bash
kdcube init --tenant <t> --project <p> \
  --descriptors-location /path/to/descriptors
```

In descriptor mode the CLI reads `assembly.yaml -> context.tenant/project`. If
those values are set, they must agree with the `--tenant`/`--project` you pass
(or with the trailing segment of `--workdir` when using the advanced form);
otherwise `init` refuses.

`--path` controls the local platform source tree only when you explicitly pass
it to `init`. Without `--upstream`, `--latest`, or `--release`, explicit
`--path` means: copy this dirty local checkout into the concrete runtime and
use that staged copy. The copy includes tracked files plus untracked files that
are not ignored by git, and excludes `.git` plus gitignored runtime/data files.

```bash
kdcube init --tenant <t> --project <p> \
  --descriptors-location /path/to/descriptors \
  --path /path/to/kdcube-ai-app \
  --cors-origin https://<stable-ngrok-domain> \
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
kdcube init --tenant <t> --project <p> \
  --descriptors-location /path/to/descriptors \
  --latest
```

Use `--upstream` to initialize from the latest upstream repo state
(`origin/main`) instead of a released ref:

```bash
kdcube init --tenant <t> --project <p> \
  --descriptors-location /path/to/descriptors \
  --upstream
```

Use `init --build` when you also want the runtime prepared with freshly built
local images before the stack is started:

```bash
kdcube init --tenant <t> --project <p> \
  --descriptors-location /path/to/descriptors \
  --upstream \
  --build
```

Use `--release` to pin a specific released platform ref explicitly:

```bash
kdcube init --tenant <t> --project <p> \
  --descriptors-location /path/to/descriptors \
  --release 2026.4.11.012
```

### 2.4 Refresh (re-init) — `kdcube refresh`<a id="refresh"></a>

`kdcube refresh` is the command to use when you want to **rebuild platform
images / restart the stack on an already-initialized workdir** without
touching any staged descriptors. Primary form:

```bash
kdcube refresh --tenant <t> --project <p> --build
```

In `custom-ui-managed-infra` mode, `--build` also rebuilds `proxylogin`
because delegated-auth behavior is part of the local platform surface.

Choose at most one source selector when the existing runtime should move to a
different platform source:

```bash
kdcube refresh --tenant <t> --project <p> --latest --build
kdcube refresh --tenant <t> --project <p> --upstream --build
kdcube refresh --tenant <t> --project <p> --release <ref> --build
```

Explicit `--path /path/to/kdcube-ai-app` without a selector means "restage this
local dirty source tree into `<workdir>/repo` before rebuilding". With a
selector, refresh checks out that selected ref and then uses the staged
`<workdir>/repo` copy so every compose build context points at the same source.

If a deployment is currently recorded as running (`~/.kdcube/cli-lock.json`),
`kdcube refresh --build` with no flags targets it automatically.

Behaviour:

- refuses if the workdir is not initialized (no `install-meta.json`);
- accepts `--latest`, `--upstream`, or `--release <ref>` as platform-source
  selectors;
- stops the stack if it is running;
- rebuilds platform Docker images when `--build` is given;
- restarts the stack (unless `--no-restart` is passed);
- never modifies `assembly.yaml`, `secrets.yaml`, `bundles.yaml`,
  `bundles.secrets.yaml`, or `gateway.yaml`.

This replaces the older pattern of re-running `kdcube init` on an existing
workdir, which used to silently reseed descriptors under some flag
combinations.

Choose at most one source selector (`--latest`, `--upstream`, `--release` are
mutually exclusive — combining any two raises an error):

- `--upstream` for the latest upstream repo state
- `--latest` for the latest released platform ref (also the default when no
  `--descriptors-location` is provided and no selector is specified)
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
- token transport names from `auth.*` are rendered into ingress/proc env and,
  for delegated auth, into the web-proxy env; defaults are used when descriptor
  fields are omitted
- if `frontend` is present and no `frontend.image` is set, the required
  `frontend.build.*` fields and `frontend.frontend_config` are set

If any of those are missing, the CLI falls back to the guided setup and prints
the missing reasons.

### 2.3b Apply bundle descriptors to an existing runtime

When a user intentionally edits seed `bundles.yaml` / `bundles.secrets.yaml`
and wants to reapply those bundle descriptors to an existing runtime, use the
bundle descriptor apply flow:

```bash
kdcube bundle config apply \
  --tenant <tenant> \
  --project <project> \
  --descriptors-location /path/to/descriptors \
  --dry-run

kdcube bundle config apply \
  --tenant <tenant> \
  --project <project> \
  --descriptors-location /path/to/descriptors \
  --reload
```

This is a user/operator descriptor-sync workflow, not a platform refresh and
not routine agent bootstrap. It stages only `bundles.yaml` and optional
`bundles.secrets.yaml` into the active runtime config directory. It does not
touch `assembly.yaml`, `gateway.yaml`, or platform `secrets.yaml`, and it does
not rebuild images or restart Docker.

Host local bundle paths in seed descriptors are translated to runtime-visible
`/bundles/...` paths before staging. With `--reload`, changed declared bundle
ids are reloaded after staging.

### 2.3c Export and import local runtime descriptors

For local descriptor-backed runtimes, use `kdcube config export` to snapshot
the reviewed descriptor source and `kdcube config import` to reapply it:

```bash
kdcube config export \
  --tenant <tenant> \
  --project <project> \
  --out-dir /tmp/kdcube-export \
  --include-platform-descriptors

kdcube config import \
  --tenant <tenant> \
  --project <project> \
  --descriptors-location /tmp/kdcube-export \
  --include-platform-descriptors \
  --dry-run

kdcube config import \
  --tenant <tenant> \
  --project <project> \
  --descriptors-location /tmp/kdcube-export \
  --include-platform-descriptors
```

With `--include-platform-descriptors`, export writes `assembly.yaml`,
`secrets.yaml`, `gateway.yaml`, `bundles.yaml`, and `bundles.secrets.yaml`.

Exported descriptors are shaped for review and re-seeding, not as a byte-for-byte
copy of the container runtime files:

| Descriptor area | Exported shape |
| --- | --- |
| `bundles.yaml` | Git bundles keep `repo` / `ref` / `subdir`; local path bundles are normalized back to host paths when possible. |
| `bundles.secrets.yaml` | Exported as the live descriptor secret overlay. |
| `infra.postgres.host`, `infra.redis.host` | Exported as descriptor-facing `localhost` for local runtimes, even when containers use `host.docker.internal`. |
| `paths.host_bundles_path` | Preserved. This is the host source root for unmanaged local bundles. |
| Other `paths.host_*` runtime mounts | Exported as `null` so the next `init` derives them from that runtime's workdir. |
| `storage.kdcube`, `storage.bundles` local container URIs | Exported as `null` when they point at CLI-generated `/kdcube-storage` or `/bundle-storage` mounts. During `init` or `config import`, `null` means "derive the local runtime default for this environment." |
| Bundle paths under `/managed-bundles` | Kept as container-visible managed cache paths. They are not folded into `paths.host_bundles_path`. |
| Service `log_dir: /logs` | Omitted. `/logs` is a generated container mount, so the next `init` derives it again from the workdir logs folder. |

Import treats the reviewed descriptor directory as authoritative for the local
runtime: platform descriptors are overwritten exactly, bundle descriptors are
path-normalized, and runtime env/config files are regenerated from the imported
platform descriptors. Restart the stack after importing platform descriptors so
running services pick up service-level env changes.

### 2.3d Export live effective bundle descriptors

For ECS / `aws-sm` deployments, the current effective live deployment-scoped
bundle state can be exported directly from AWS Secrets Manager:

```bash
OUT_DIR=/tmp/kdcube-export

kdcube config export \
  --tenant <tenant> \
  --project <project> \
  --aws-region <region> \
  --out-dir "$OUT_DIR"

ls -lh "$OUT_DIR"
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

For local descriptor-backed runtimes, prefer `kdcube config export`. It reads
the active runtime descriptor files and normalizes runtime paths back to
reusable descriptor shape:

- local non-git bundle paths such as `/bundles/...` are translated back to host
  paths using `assembly.yaml` / `.env` bundle mount mappings
- git-backed bundle descriptors keep `repo` / `ref` / `subdir`; an incidental
  materialized `path` is removed from the export

Use the printed output path or a saved `OUT_DIR` variable when inspecting the
export. Do not recompute a timestamped `$(date ...)` expression in a later
command; it will point at a different directory.

Operational rule for `aws-sm` deployments:

1. export the current live bundle state before the next provision
2. reconcile that export into the private descriptor source-of-truth files
3. copy the approved file contents into the GitHub Environment secrets
4. run provision

If you skip that step, a later provision can replay stale `BUNDLES_YAML` or
`BUNDLES_SECRETS_YAML` and overwrite runtime bundle changes.

### 2.3d Manage staged bundle entries

`kdcube bundle` creates, updates, or deletes entries in the staged descriptor
files in the active runtime workdir without a full reinstall or manual YAML edit.
All non-delete flag groups can be combined in one invocation — the command does a
single atomic read-modify-write of `bundles.yaml` / `bundles.secrets.yaml`.

When `--local-path` or `--git-repo` is provided and `<bundle_id>` does not yet
exist in `bundles.yaml`, the command **creates** a new entry (upsert).
For all other operations (identity, config, secrets) the bundle must already exist.

#### Source mode

Switch where proc loads the bundle from.

**Local path** — proc reads from a host-mounted directory.
Pass a host path under `assembly.yaml -> paths.host_bundles_path`; the CLI
validates it and writes the matching container-visible path under `/bundles/`
into `bundles.yaml`:

```bash
kdcube bundle <bundle_id> \
  --local-path /Users/you/src/my.bundle \
  --workdir ~/.kdcube/kdcube-runtime/<tenant_id>__<project_id>
```

If you already know the runtime-visible path, passing `/bundles/my.bundle` is
also accepted and preserved as-is.

Setting `--local-path` clears any `repo`/`ref`/`subdir` fields.

**Git repo** — proc clones the repo into `/managed-bundles/` on first `kdcube bundle reload`:

```bash
kdcube bundle <bundle_id> \
  --git-repo git@github.com:org/my-bundle.git \
  --git-ref 2026.4.30 \
  --workdir ~/.kdcube/kdcube-runtime/<tenant_id>__<project_id>
```

`--git-ref` is required with `--git-repo`.

If the bundle root is a subdirectory of the repo:

```bash
kdcube bundle <bundle_id> \
  --git-repo git@github.com:org/monorepo.git \
  --git-ref main \
  --git-subdir src/my.bundle \
  --workdir ~/.kdcube/kdcube-runtime/<tenant_id>__<project_id>
```

`--git-subdir` requires `--git-repo`.

After proc resolves a git bundle it writes the concrete resolved path back into
the descriptor (e.g. `/managed-bundles/org__my-bundle__2026.4.30`).
This is expected — the source-of-truth for the next reload is the
`repo`/`ref`/`subdir` fields.

Setting `--git-repo` clears any `path` field.

#### Identity fields

```bash
kdcube bundle <bundle_id> \
  --name "My Bundle" \
  --module entrypoint \
  --singleton \
  --workdir ~/.kdcube/kdcube-runtime/<tenant_id>__<project_id>
```

| Flag | Default | Description |
|---|---|---|
| `--name NAME` | bundle_id | Human-readable display name |
| `--module MODULE` | `entrypoint` | Python module used as the bundle entry point |
| `--singleton` | `false` | Only one instance may run at a time |
| `--no-singleton` | — | Explicitly set `singleton: false` |

#### Config and secrets patch

**Set a config value by dotted key path:**

```bash
kdcube bundle <bundle_id> \
  --set-config key.path value \
  --workdir ~/.kdcube/kdcube-runtime/<tenant_id>__<project_id>
```

**Delete a config key:**

```bash
kdcube bundle <bundle_id> \
  --del-config key.path \
  --workdir ~/.kdcube/kdcube-runtime/<tenant_id>__<project_id>
```

**Set a secret:**

```bash
kdcube bundle <bundle_id> \
  --set-secret key.path value \
  --workdir ~/.kdcube/kdcube-runtime/<tenant_id>__<project_id>
```

**Delete a secret key:**

```bash
kdcube bundle <bundle_id> \
  --del-secret key.path \
  --workdir ~/.kdcube/kdcube-runtime/<tenant_id>__<project_id>
```

Multiple patch operations can be chained:

```bash
kdcube bundle <bundle_id> \
  --set-config model.name claude-opus-4-7 \
  --set-config features.debug false \
  --del-config features.legacy_mode \
  --workdir ~/.kdcube/kdcube-runtime/<tenant_id>__<project_id>
```

Value coercion rules:

| CLI value | Stored as |
|---|---|
| `true` / `false` | bool |
| `42`, `3.14` | int / float |
| `hello world` | string (no quotes needed) |
| `'{"a": 1}'` (shell-quoted JSON) | string (the JSON literal) |

`--del-config` and `--del-secret` raise an error if the key does not exist.

#### Delete a bundle entry

Remove a bundle from `bundles.yaml` (and its secrets entry from
`bundles.secrets.yaml`, if present):

```bash
kdcube bundle <bundle_id> \
  --delete \
  --workdir ~/.kdcube/kdcube-runtime/<tenant_id>__<project_id>
```

`--delete` cannot be combined with any other flag. Exits with an error if the
bundle is not found.

#### Status

Inspect one explicit staged bundle entry:

```bash
kdcube bundle status <bundle_id> \
  --workdir ~/.kdcube/kdcube-runtime/<tenant_id>__<project_id>
```

By default the command reports descriptor/source/path diagnostics and whether
`chat-proc` is running. It does not list other bundles.

For local operator diagnostics, add `--live` to ask localhost `chat-proc` to
load/discover that same explicit bundle id and return the load result:

```bash
kdcube bundle status <bundle_id> \
  --live \
  --json \
  --workdir ~/.kdcube/kdcube-runtime/<tenant_id>__<project_id>
```

The live status path is not a user-facing discovery API. It is intended for
operators who already have access to the local runtime workdir and Docker
runtime. It does not emulate an end-user session or frontend visibility rules.

#### Apply changes

After patching, apply the change with:

```bash
kdcube bundle reload <bundle_id> --workdir ~/.kdcube/kdcube-runtime/<tenant_id>__<project_id>
```

`kdcube bundle` only patches the staged files under `workdir/config/`.
It does not push the change live on its own — `kdcube bundle reload` is still required.
The CLI prints the exact reload command after bundle source, config, or secret
mutations.

### 2.4 Bundles descriptor (optional)

You can provide a **bundles descriptor** (`bundles.yaml`) and an optional
**bundles secrets** file (`bundles.secrets.yaml`). This is the preferred way
to configure bundles and bundle secrets.

When provided, the CLI:
- mounts the runtime workspace `config/` directory at `/config`
- stages `bundles.secrets.yaml` alongside `bundles.yaml` for the proc runtime

Current proc behavior:

- `config/bundles.yaml` is the normal bundle descriptor authority
- proc can seed/reset from that descriptor directly

Local bundle root contract:

- service env files stay bootstrap-minimal: `.env.ingress`, `.env.proc`, and `.env.metrics` contain only `GATEWAY_COMPONENT` and `PLATFORM_DESCRIPTORS_DIR`; runtime settings under `assembly.platform.services.*` are read from the mounted descriptor, not copied into service env files
- `assembly.paths.host_kdcube_storage_path` becomes `HOST_KDCUBE_STORAGE_PATH`; if it is `null` or omitted, init uses `<workdir>/data/kdcube-storage`
- `assembly.paths.host_bundle_storage_path` becomes `HOST_BUNDLE_STORAGE_PATH`; if it is `null` or omitted, init uses `<workdir>/data/bundle-storage`
- `assembly.paths.host_exec_workspace_path` becomes `HOST_EXEC_WORKSPACE_PATH`; if it is `null` or omitted, init uses `<workdir>/data/exec-workspace`
- `assembly.paths.host_react_debug_path` becomes `HOST_REACT_DEBUG_PATH`; if it is `null` or omitted, init uses `<workdir>/data/react-debug`; proc mounts it as `/react-debug` and keeps only the latest `REACT_DEBUG_KEEP_FILES` timeline render dumps
- `assembly.ai.react.debug_timeline: false` mutes rendered ReAct prompt snapshots for normal runs; set it to `true` only while diagnosing prompt rendering
- `assembly.platform.services.proc.tools.web_search.web_favicon_enrich_enabled: false` disables favicon enrichment for web search/fetch results; favicon cache keys are domain-scoped, `web_favicon_enrich_timeout_s` caps lookup time, and `web_favicon_failure_cache_ttl_s` controls the short negative-cache TTL for failed domains
- if `assembly.storage.kdcube` is `null` or omitted, init uses the CLI-managed tenant/project local storage root and rewrites the staged runtime descriptor to `file:///kdcube-storage`
- if `assembly.storage.bundles` is `null` or omitted, init uses the CLI-managed tenant/project local bundle storage root and rewrites the staged runtime descriptor to `file:///bundle-storage`
- if `assembly.storage.kdcube` is a local host `file://...` URI, init uses that host path as `HOST_KDCUBE_STORAGE_PATH` and rewrites the staged runtime descriptor to `file:///kdcube-storage`
- if `assembly.storage.bundles` is a local host `file://...` URI, init uses that host path as `HOST_BUNDLE_STORAGE_PATH` and rewrites the staged runtime descriptor to `file:///bundle-storage`
- if `assembly.storage.kdcube` or `assembly.storage.bundles` is an explicit `s3://...` URI, init preserves that URI in the staged runtime descriptor instead of rewriting it to a local mount
- compose mounts `HOST_KDCUBE_STORAGE_PATH` into proc/ingress/metrics as `/kdcube-storage`
- `assembly.paths.host_bundles_path` is installer-facing config for non-managed local path bundles and is written to `HOST_BUNDLES_PATH`
- compose mounts `HOST_BUNDLES_PATH` into proc as `BUNDLES_ROOT` (normally `/bundles`)
- non-managed local bundle entries in `bundles.yaml` use the container-visible path; `kdcube bundle --local-path <host-path>` performs this translation for host paths under `HOST_BUNDLES_PATH`, for example:
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
documents and can be exported back with `kdcube config export`. In that mode,
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
- `kdcube release tag` (tag + VERSION validation)

## 9) Operational commands

Inspect global CLI state (defaults, running deployment, and runtime info from defaults):

```bash
kdcube info
```

Show only the stored CLI defaults:

```bash
kdcube info --show-defaults
```

Show only the currently running deployment:

```bash
kdcube info --show-current-running-runtime
```

Inspect a specific initialized workdir:

```bash
kdcube info --workdir ~/.kdcube/kdcube-runtime/<tenant_id>__<project_id>
```

Disambiguate when multiple runtimes exist under the base workdir:

```bash
kdcube info --workdir ~/.kdcube/kdcube-runtime --tenant <tenant_id> --project <project_id>
```

Show runtime info for a tenant/project using the default runtime base (no `--workdir` needed):

```bash
kdcube info --tenant <tenant_id> --project <project_id>
```

Initialize a workdir without starting Docker (clones platform repo automatically):

```bash
kdcube init --tenant acme --project prod
kdcube init --tenant acme --project prod --latest
kdcube init --tenant acme --project prod --prompt-secrets
kdcube init --tenant acme --project prod --cors-origin https://<stable-ngrok-domain>
```

Initialize from a prepared descriptor folder:

```bash
kdcube init --tenant acme --project prod \
  --descriptors-location /path/to/descriptors \
  --cors-origin https://<stable-ngrok-domain> \
  --latest
```

Initialize and build images without starting Docker:

```bash
kdcube init --tenant acme --project prod \
  --descriptors-location /path/to/descriptors \
  --upstream \
  --build
```

Initialize and build from dirty local platform sources:

```bash
kdcube init --tenant acme --project prod \
  --descriptors-location /path/to/descriptors \
  --path /path/to/kdcube-ai-app \
  --cors-origin https://<stable-ngrok-domain> \
  --build
```

Use this for uncommitted platform changes. The CLI copies the local checkout
into the concrete runtime workdir and builds from that staged copy.

Refresh after editing platform source (rebuilds images, restarts):

```bash
kdcube refresh --tenant acme --project prod --build
```

Refresh to a selected platform ref while preserving staged descriptors:

```bash
kdcube refresh --tenant acme --project prod --release 2026.5.22.001 --build
```

If a deployment is currently recorded as running, `kdcube refresh --build`
with no flags targets it automatically.

Start the stack for an already-initialized workdir:

```bash
kdcube start --tenant acme --project prod
```

Patch staged bundle config or secrets without editing YAML by hand:

```bash
kdcube bundle <bundle_id> \
  --tenant acme --project prod \
  --set-config key.path value

kdcube bundle <bundle_id> \
  --tenant acme --project prod \
  --set-secret key.path value

kdcube bundle <bundle_id> \
  --tenant acme --project prod \
  --del-config key.path
```

Multiple `--set-config`, `--set-secret`, `--del-config`, `--del-secret` flags can be combined in one call.
After patching, reload the bundle to apply the change.

Reload a bundle after descriptor changes:

```bash
kdcube bundle reload <bundle_id> --tenant acme --project prod
```

Normal reload output is concise and hides the inner Docker Compose call. Use
`--verbose` when debugging reload internals, or `--json` for scriptable output:

```bash
kdcube bundle reload <bundle_id> --tenant acme --project prod --verbose
kdcube bundle reload <bundle_id> --tenant acme --project prod --json
```

Use `--quiet` to suppress the banner and routine success chatter. The banner is
also suppressed automatically when stdout is not a TTY and when a command uses
`--json`.

`kdcube info --json` emits defaults, the running deployment lock, and runtime
mount details when a workdir is selected.

Apply seed bundle descriptors to an existing runtime:

```bash
kdcube bundle config apply \
  --tenant acme \
  --project prod \
  --descriptors-location /path/to/descriptors \
  --dry-run

kdcube bundle config apply \
  --tenant acme \
  --project prod \
  --descriptors-location /path/to/descriptors \
  --reload
```

This is for the user/operator case where the seed `bundles.yaml` /
`bundles.secrets.yaml` are the chosen descriptor source. It does not refresh the
platform runtime.

Export and import the reviewed local runtime descriptor set:

```bash
kdcube config export \
  --tenant acme \
  --project prod \
  --out-dir /tmp/kdcube-export \
  --include-platform-descriptors

kdcube config import \
  --tenant acme \
  --project prod \
  --descriptors-location /tmp/kdcube-export \
  --include-platform-descriptors \
  --dry-run

kdcube config import \
  --tenant acme \
  --project prod \
  --descriptors-location /tmp/kdcube-export \
  --include-platform-descriptors
```

With `--include-platform-descriptors`, `config export` includes
`assembly.yaml`, `secrets.yaml`, and `gateway.yaml` next to the bundle
descriptors. The exported descriptor set is shaped for review and re-seeding:
local Postgres/Redis hosts are written as descriptor-facing `localhost`,
`paths.host_bundles_path` is preserved, generated runtime mount paths are
written as `null`, and generated service `log_dir: /logs` entries are omitted.
`config import` treats the reviewed directory as authoritative:
platform descriptors are overwritten exactly, bundle descriptors are
path-normalized, and runtime env/config files are regenerated from the imported
platform descriptors. Restart the stack after importing platform descriptors so
running services pick up service-level env changes.

Export live bundle descriptors:

```bash
OUT_DIR=/tmp/export
kdcube config export --tenant acme --project prod --out-dir "$OUT_DIR"
ls -lh "$OUT_DIR"
```

For local descriptor-backed runtimes, exported local bundle paths are normalized
back to host paths. Git-backed entries keep repo/ref/subdir and do not keep the
materialized runtime path.

Stop the local workdir stack (no flags ⇒ stop whatever is currently recorded
as running):

```bash
kdcube stop
```

Or be explicit:

```bash
kdcube stop --tenant acme --project prod
kdcube stop --tenant acme --project prod --remove-volumes
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
| `default_tenant` | `--default-tenant` | Used by `kdcube info` for workdir resolution and display; used by descriptor export/import commands as fallback tenant |
| `default_project` | `--default-project` | Used by `kdcube info` for workdir resolution and display; used by descriptor export/import commands as fallback project |

`kdcube start`, `kdcube stop`, `kdcube bundle reload`,
`kdcube bundle config apply`, `kdcube config export`, and
`kdcube config import` resolve the target workdir with the following
precedence.

1. `--workdir` passed explicitly → use it.
2. `--workdir` omitted, `default_workdir` present in `cli-defaults.json` → use
   that.
3. Neither provided → error:
   ```
   No target workdir specified.
   Pass --workdir explicitly or configure a default:
     kdcube defaults --default-workdir <path>
   ```

`kdcube info` (no arguments) shows three things in sequence:

1. Stored CLI defaults (`default_workdir`, `default_tenant`, `default_project`).
2. Currently running deployment from the lock file.
3. Runtime info for the workdir resolved from defaults — if any of `default_workdir`,
   `default_tenant`, or `default_project` are set, the CLI resolves the namespaced
   workdir (using `~/.kdcube/kdcube-runtime` as base when `default_workdir` is not
   set) and prints full descriptor/mount info for that runtime, if it is initialized.

`--tenant` and `--project` flags on `kdcube info` also serve as workdir disambiguation:
when `--workdir` points to a base directory containing multiple runtimes, they identify
which one to show. When `--workdir` is omitted, they construct the target path from
the default runtime base.

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

### `kdcube info` and stale locks

`kdcube info` verifies the recorded lock via
`docker compose ps`. If the recorded deployment is no longer running, the lock
is considered stale, reported as such, and cleared automatically.
