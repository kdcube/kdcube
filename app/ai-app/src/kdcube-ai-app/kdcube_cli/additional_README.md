# KDCube CLI

![KDCube CLI](https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/src/kdcube-ai-app/kdcube_cli/pixel-cubes.png)

Bootstrap installer for the KDCube platform stack. This package clones the
repository (if needed) and launches the guided setup wizard.

This README describes the current implemented CLI behavior.

Short version of the current model:

- the CLI bootstraps or reuses a concrete runtime snapshot under a namespaced
  workdir
- the namespace is usually derived from `assembly.yaml -> context.tenant` and
  `context.project`
- each local runtime snapshot keeps its own staged descriptors, platform
  snapshot, and runtime data

So current local CLI usage is still deployment-isolated, but workdir-first.

Operationally, that means:

- one `tenant/project` = one isolated environment
- use separate `tenant/project` values for different customers or different
  stages such as `dev`, `staging`, and `prod`
- keep multiple bundles inside one `tenant/project` when they belong to the
  same environment

So the current CLI does not create one runtime per bundle.
It creates one runtime per environment, and that environment may host many
bundles.

One more practical rule is easy to miss:

- one machine can hold many local runtime snapshots under different
  `tenant/project` namespaces
- that does not mean the current compose-backed local workflow supports many
  concurrently running local KDCube stacks as a normal mode
- treat the current local runtime as one active deployment at a time unless a
  future explicit multi-instance local mode is introduced

----

## Prerequisites

### macOS
- Python 3.9+ (Homebrew recommended)
- Git (Xcode Command Line Tools or Homebrew)
- Docker Desktop (includes Docker Compose)

### Windows
- Python 3.9+
- Git for Windows
- Docker Desktop (enable WSL2 backend)

### Linux
- Python 3.9+
- Git
- Docker Engine + Docker Compose plugin

## Install

```bash
pipx install kdcube-cli
```

Alternative (pip):

```bash
python -m pip install --user kdcube-cli
```

On Debian/Ubuntu hosts that enforce PEP 668 (`externally-managed-environment`),
install the CLI into a dedicated virtual environment instead of the system
Python:

```bash
sudo apt-get update
sudo apt-get install -y python3-pip python3-venv
python3 -m venv ~/.venvs/kdcube-cli
~/.venvs/kdcube-cli/bin/pip install -e /path/to/kdcube_cli
~/.venvs/kdcube-cli/bin/kdcube --help
```

## Run

```bash
kdcube init
```

## Quick start (new users)

1) Run `kdcube`  
2) Choose **release-latest** (pull prebuilt images)  
3) Answer **yes** to “Run docker compose now?”  

That brings up the stack with no local build required.

We aim for a setup that is simple to try, and easy to explore further using the
installed admin assistant and bundled tools.

## Quick start (prepared descriptors)

If you already have a descriptor folder, you can skip the wizard:

```bash
kdcube init \
  --descriptors-location /path/to/descriptors \
  --workdir /path/to/workspace
```

With descriptor-driven installs, `--workdir` is the base workspace root. The
effective runtime is created under:

```text
<workspace>/<safe_tenant>__<safe_project>/
```

using `assembly.yaml -> context.tenant` and `context.project`.

If `--path` is omitted, the CLI clones or reuses the platform checkout under:

```text
<workspace>/<safe_tenant>__<safe_project>/repo
```

You can still pass `--path` explicitly if you want to force a specific local
checkout. In descriptor-driven `init`, explicit `--path` means “stage this local
source tree into the runtime workdir and use that staged copy”. The CLI copies
tracked files plus untracked files that are not ignored by git, so dirty local
source changes can be tested without copying `.git`, local runtime data, or
other gitignored paths.

```bash
kdcube init \
  --descriptors-location /path/to/descriptors \
  --workdir /path/to/workspace \
  --path /path/to/kdcube-ai-app \
  --build
```

`--workdir` answers “where should this runtime live?”. `--path` answers “which
local platform source tree should this runtime use?”. If `--upstream`,
`--latest`, or `--release` is also provided, that version selector wins and
`--path` is only the local repo/cache location for the selected source.

Or pull the latest platform release from the platform repo instead of
`assembly.yaml -> platform.ref`:

```bash
kdcube init \
  --descriptors-location /path/to/descriptors \
  --workdir /path/to/workspace \
  --latest
```

Or initialize from the latest upstream repo state instead of a released ref:

```bash
kdcube init \
  --descriptors-location /path/to/descriptors \
  --workdir /path/to/workspace \
  --upstream
```

If you also want the runtime ready with freshly built local images, build during
init:

```bash
kdcube init \
  --descriptors-location /path/to/descriptors \
  --workdir /path/to/workspace \
  --upstream \
  --build
```

Use `--upstream` when you want the deployment assets from the latest GitHub
`origin/main`, including:

- compose files
- nginx templates
- installer-side deployment templates

If the runtime was already initialized earlier, you can omit
`--descriptors-location` and reuse the staged descriptors from `workdir/config`
instead:

```bash
kdcube init \
  --workdir /path/to/workspace/<safe_tenant>__<safe_project> \
  --upstream
```

This reuse path requires:

- `workdir/config/install-meta.json`
- the canonical descriptor set already present under `workdir/config`
  - `assembly.yaml`
  - `secrets.yaml`
  - `bundles.yaml`
  - `bundles.secrets.yaml`
  - `gateway.yaml`

When those files exist, the CLI treats `workdir/config` as the descriptor
authority and reuses the repo recorded in `install-meta.json` when possible.

Important reused-runtime rule:

- the CLI reuses `workdir/config/assembly.yaml`, `bundles.yaml`, and `bundles.secrets.yaml` as they already exist
- it does not reseed default descriptors
- if `bundles.yaml` already contains local path entries under `/bundles/...`, those are treated as container paths and preserved
- the matching host root is taken from `assembly.yaml -> paths.host_bundles_path`
- the CLI does not reinterpret `/bundles/...` as a host filesystem path

So for an initialized runtime, change bundle topology by editing:

- `workdir/config/assembly.yaml`
- `workdir/config/bundles.yaml`

Do not change local path bundles by putting host paths directly into `bundles.yaml`.

Correct split:

```yaml
# workdir/config/assembly.yaml
paths:
  host_bundles_path: "/Users/you/src"
```

```yaml
# workdir/config/bundles.yaml
bundles:
  items:
    - id: "my.bundle@1-0"
      path: "/bundles/my-repo/src/my_bundle"
      module: "entrypoint"
```

`host_bundles_path` is the host parent root.
`path` in `bundles.yaml` is the container-visible bundle root.

`--latest` is different: it resolves the latest release ref for release-image
installs. It does not mean “latest source templates from GitHub main”.

Or pin a specific release explicitly:

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

## Local public HTTPS origin with ngrok

For Telegram webhooks, Telegram Mini Apps, OAuth redirects, or Cognito callback
testing, expose the local CLI runtime through one stable ngrok HTTPS origin.

Full operational recipe:

```text
app/ai-app/docs/service/cicd/ngrok-README.md
```

Short CLI procedure:

```bash
kdcube init \
  --path /path/to/kdcube-ai-app \
  --descriptors-location /path/to/descriptors \
  --workdir ~/.kdcube/kdcube-runtime \
  --cors-origin https://<stable-ngrok-domain> \
  --build

kdcube start --workdir ~/.kdcube/kdcube-runtime/<tenant>__<project>
```

Use the local proxy port printed by `kdcube start`, then start ngrok with the
stable assigned domain:

```bash
ngrok http 5173 --url https://<stable-ngrok-domain> --host-header=rewrite
```

If `kdcube start` prints another proxy port, replace `5173` with that port.

For ngrok, keep the local proxy HTTP-only:

```yaml
domain: ""
proxy:
  ssl: false
  route_prefix: "/platform"
```

Ngrok terminates public HTTPS and forwards plain HTTP to the local KDCube web
proxy. Do not use `domain` as the local ngrok public origin. Put the stable
HTTPS origin in the descriptor fields that actually consume it.

For CORS, pass the origin during init:

```bash
kdcube init \
  --workdir ~/.kdcube/kdcube-runtime \
  --cors-origin https://<stable-ngrok-domain>
```

That appends to the staged assembly descriptor:

```yaml
cors:
  allow_origins:
    - "https://<stable-ngrok-domain>"
```

For bundle integrations, configure public URLs in `bundles.yaml` or in the
staged bundle config under the runtime workdir. Telegram webhooks and any OAuth
integration with redirect/public-base URLs follow this pattern. Example:

```yaml
integrations:
  telegram:
    webhook_url: "https://<stable-ngrok-domain>/api/integrations/bundles/<tenant>/<project>/<bundle_id>/public/telegram_webhook"
  email:
    oauth:
      public_base_url: "https://<stable-ngrok-domain>"
```

After changing `assembly.yaml`, restart the runtime. After changing only bundle
config, reload the bundle.

For `aws-sm` deployments, you can also export the current effective live
deployment-scoped bundle descriptors directly from AWS Secrets Manager:

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

from the authoritative grouped AWS SM bundle documents, not from Redis or the
currently mounted `/config/bundles.yaml`.

Expected descriptor folder:

```text
descriptors/
  assembly.yaml
  secrets.yaml
  gateway.yaml
  bundles.yaml            # optional
  bundles.secrets.yaml    # optional
```

When the descriptor set is complete, the CLI:
- resolves the effective runtime as `<workspace>/<safe_tenant>__<safe_project>`
- stages the descriptors into `<runtime>/config`
- clones or reuses the platform repo under `<runtime>/repo` when `--path` is omitted
- copies the explicit local `--path` repo into `<runtime>/repo` when no version selector is used
- skips interactive prompts
- runs a release install directly

The same non-interactive path is also used when:

- `--descriptors-location` is omitted
- `--workdir` points to an existing runtime
- `config/install-meta.json` exists
- the canonical descriptor set already exists under `config/`

In that case the CLI reuses the staged runtime descriptors and the repo path
recorded in `install-meta.json`.

If required fields are missing, it falls back to the guided setup and prints
what is incomplete.

## What it installs (default)
- Base workspace: `~/.kdcube/kdcube-runtime`
- Runtime namespace: `~/.kdcube/kdcube-runtime/<safe_tenant>__<safe_project>`
- Repo clone default: `~/.kdcube/kdcube-runtime/<safe_tenant>__<safe_project>/repo`
- Docker images: prepared by `init --build` or rebuilt as a convenience by `start --build`

### Subcommands

| Subcommand | Purpose |
|---|---|
| `kdcube init [--workdir <path>] [--path <repo>] [--descriptors-location <dir>] [--latest\|--upstream\|--release <ref>] [--build] [-i] [--reset-config] [--prompt-secrets] [--set-secret KEY VALUE]... [--cors-origin ORIGIN]...` | Initialize a workdir (stage descriptors, generate env files). Explicit `--path` stages that local source tree unless a version selector is used. With `--build`, also build images **without** starting containers. `--cors-origin` appends an allowed origin to staged `config/assembly.yaml`. |
| `kdcube start [--workdir <path>] [--build]` | Start the Docker Compose stack for an already-initialized workdir. `--build` is a convenience rebuild before start, not required if `init --build` was already run. |
| `kdcube stop [--workdir <path>] [--remove-volumes]` | Stop the local Docker Compose stack. |
| `kdcube reload <bundle_id> [--workdir <path>]` | Reapply `bundles.yaml` from the active runtime and clear proc bundle caches. |
| `kdcube export [--workdir <path>] [--tenant <id>] [--project <id>] [--out-dir <dir>] [--aws-region <region>]` | Export effective live `bundles.yaml` and `bundles.secrets.yaml`. |
| `kdcube info [--workdir <path>] [--tenant <t>] [--project <p>] [--show-defaults] [--show-current-running-runtime]` | Show CLI defaults, currently running deployment, and runtime info from defaults when called with no arguments. `--workdir` shows runtime info for a specific workdir; `--tenant`/`--project` disambiguate when multiple runtimes exist under `--workdir`, or construct the target runtime from the default runtime base when `--workdir` is omitted. `--show-defaults` prints only the stored CLI defaults. `--show-current-running-runtime` prints only the currently running deployment. |
| `kdcube clean` | Clean local Docker cache and unused KDCube images. |
| `kdcube defaults [--default-workdir <path>] [--default-tenant <t>] [--default-project <p>]` | Save persistent operator defaults to `~/.kdcube/cli-defaults.json`. |

### Operator defaults (`kdcube defaults`)

`kdcube defaults` persists values to `~/.kdcube/cli-defaults.json`:

| Field | Flag | Purpose |
|---|---|---|
| `default_workdir` | `--default-workdir` | Fallback workdir when `--workdir` is omitted from a subcommand |
| `default_tenant` | `--default-tenant` | Used by `kdcube info` for workdir resolution and display; used by `kdcube export` as fallback tenant |
| `default_project` | `--default-project` | Used by `kdcube info` for workdir resolution and display; used by `kdcube export` as fallback project |

`kdcube start`, `kdcube stop`, `kdcube reload`, and `kdcube export` resolve the
target workdir with the following precedence:

1. `--workdir` passed explicitly → use it.
2. `--workdir` omitted, `default_workdir` present in `cli-defaults.json` → use that.
3. Neither provided → error with a hint to run `kdcube defaults --default-workdir <path>`.

`kdcube info` (no arguments) shows three things in sequence:

1. Stored CLI defaults (`default_workdir`, `default_tenant`, `default_project`).
2. Currently running deployment from the lock file.
3. Runtime info for the workdir resolved from defaults — if any of `default_workdir`,
   `default_tenant`, or `default_project` are set, the CLI resolves the namespaced
   workdir (using `DEFAULT_WORKDIR` as base when `default_workdir` is not set) and
   prints full descriptor/mount info for that runtime, if it is initialized.

### Single-deployment guard (`cli-lock.json`)

`~/.kdcube/cli-lock.json` is a per-machine deployment lock written on start and
cleared on stop.

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

**Guard at start (`kdcube start`)** — reads the lock and runs `docker compose ps`:

- No lock → proceed.
- Lock matches the target `tenant/project` → proceed (same deployment restart).
- Lock points to a **different** deployment and services are **live** → abort with
  a message showing what is running and how to stop it first.
- Lock exists but services are **not live** (stale) → lock cleared automatically,
  start proceeds.

**Guard at stop (`kdcube stop`)** — before stopping:

1. Runs `docker compose ps` for the target workdir — nothing running →
   `"Deployment is not running"`.
2. Something running and lock matches the target `tenant/project` → stop and
   clear the lock.
3. Something running but lock points to a **different** deployment → abort.

**`kdcube info` and stale locks** — `kdcube info` verifies the lock via
`docker compose ps`. If the recorded deployment is no longer running, the lock is
reported as stale and cleared automatically.

### Use a local checkout (dev)

```bash
kdcube init --path /Users/you/src/kdcube/kdcube-ai-app
```

When `--path` is provided, the wizard **uses that repo for templates and local builds**
and **does not show the Install source menu**.

For the descriptor `init` path, use `--path` when you need to test uncommitted
platform changes:

```bash
kdcube init \
  --descriptors-location /path/to/descriptors \
  --workdir ~/.kdcube/kdcube-runtime \
  --path /Users/you/src/kdcube/kdcube-ai-app \
  --build
```

This copies the dirty local checkout into the namespaced runtime workdir and
builds from the staged copy.

Re-run prompts (edit existing values):

```bash
kdcube init --reset-config
```

Clean local Docker images/cache:

```bash
kdcube clean
```

Stop the local stack:

```bash
kdcube stop --workdir ~/.kdcube/kdcube-runtime
```

Stop and remove volumes:

```bash
kdcube stop --workdir ~/.kdcube/kdcube-runtime --remove-volumes
```

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
kdcube info --workdir ~/.kdcube/kdcube-runtime/acme__prod_demo
```

Disambiguate when multiple runtimes exist under the base workdir:

```bash
kdcube info --workdir ~/.kdcube/kdcube-runtime --tenant acme --project prod
```

Show runtime info for a tenant/project using the default runtime base (no `--workdir` needed):

```bash
kdcube info --tenant acme --project prod
```


Tip: if `kdcube` is not on your PATH, run `python -m pipx ensurepath`
or re-open your shell after installation.

## What the wizard does (today)

When you run `kdcube`, the **wizard** performs the steps below:
1) Creates a **workdir** with `config/`, `data/`, and `logs/` folders.
2) Writes compose env files into `config/` (only if missing; it won’t overwrite existing files).
3) Copies nginx configs into `config/` for runtime overrides:
   - `nginx_ui.conf`
   - runtime proxy config (based on selected auth mode)
4) Selects **auth mode** (simple, cognito, or delegated) and writes:
   - `AUTH_PROVIDER` in `.env.ingress` + `.env.proc`
   - Cognito fields when applicable (see below)
5) Generates frontend runtime config based on auth mode or descriptor template.
6) Creates local data folders for Postgres/Redis/exec workspace/bundle storage.
7) Optionally builds images and starts `docker compose up -d`.

### Authentication modes
The wizard prompts for an auth mode and updates both backend and frontend config.

**Simple (hardcoded)**
- `AUTH_PROVIDER=simple`
- Frontend config: `frontend.config.hardcoded.json`
- Uses a hardcoded admin token in config (local dev only)

**Cognito**
- `AUTH_PROVIDER=cognito`
- Frontend config: `frontend.config.cognito.json`
- Required fields:
  - `COGNITO_REGION`
  - `COGNITO_USER_POOL_ID`
  - `COGNITO_APP_CLIENT_ID`
  - `COGNITO_SERVICE_CLIENT_ID`
- The frontend `authority` is composed as:
  - `https://cognito-idp.<COGNITO_REGION>.amazonaws.com/<COGNITO_USER_POOL_ID>`

**Delegated**
- `AUTH_PROVIDER=cognito`
- Frontend config: `frontend.config.delegated.json`
- Uses the delegated proxy template (proxylogin) while still validating tokens via Cognito.
- If `assembly.yaml` provides `company`, the generated delegated config uses it for:
  - `auth.totpAppName`
  - `auth.totpIssuer`

### Routes prefix & nginx proxy
The frontend config includes `routesPrefix` (default: `/chatbot`).
The wizard patches the **runtime proxy config** in `config/` so nginx uses the
same prefix. This keeps `/chatbot` (or any custom prefix) consistent between UI and proxy.

If `proxy.ssl: true` and `assembly.domain` is set, the wizard also patches the
runtime nginx SSL config so `YOUR_DOMAIN_NAME` becomes the configured domain in:
- `server_name`
- `/etc/letsencrypt/live/<domain>/fullchain.pem`
- `/etc/letsencrypt/live/<domain>/privkey.pem`

### Secrets (third services tokens)
The wizard **does not** write OpenAI/Anthropic/Git token values to `.env` files.
During init, secret values are staged into `config/secrets.yaml` with dotted
descriptor keys.

Prompt for the standard local secret set:

```bash
kdcube init --prompt-secrets
```

Stage explicit values without prompts:

```bash
kdcube init \
  --set-secret services.openai.api_key "sk-..." \
  --set-secret services.anthropic.api_key "sk-ant-..." \
  --set-secret services.git.http_token "ghp_..."
```

The full interactive wizard (`kdcube init -i`) also includes the standard
secret prompts.

If you set LLM keys in env files for custom setups, those env values still work
and take precedence.

### First-run defaults
Plain `kdcube init` seeds the full canonical descriptor set into `workdir/config/`
from the tracked reference descriptors:

- `assembly.yaml`
- `secrets.yaml`
- `bundles.yaml`
- `bundles.secrets.yaml`
- `gateway.yaml`

That first local bootstrap is descriptor-first and local-first:

- `storage.workspace.type=local`
- `storage.claude_code_session.type=local`
- `storage.kdcube=/kdcube-storage`
- `storage.bundles=/bundle-storage`
- `bundles.default_bundle_id=versatile@2026-03-31-13-36`

When a seed descriptor sets `storage.kdcube` or `storage.bundles` to a host
`file://...` path, `init` treats that as a host-side storage root. The staged
runtime descriptor is rewritten to the container paths `file:///kdcube-storage`
and `file:///bundle-storage`, and compose mounts the selected host roots there.
This keeps descriptors portable while still allowing local host storage during
CLI testing.

On a fresh default run, the installer asks only for:

- `Host bundles root (local path bundles)`
- `OpenAI API key` (optional)
- `Anthropic API key` (optional)
- `Git HTTPS token` (optional)

It does not ask for OpenRouter. It does not ask for Brave. It auto-seeds local
defaults for Postgres, Redis, auth mode, UI port, managed bundle cache paths,
bundle storage, and exec workspace.

### Git HTTPS token (private bundles)
If you choose or provide a Git HTTPS token for private bundles, the token is
treated as a secret and is **not stored** in `.env.proc`. It is staged into
`config/secrets.yaml` and reused on later local runs.

More details:
https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/ops/local/local-setup-README.md

Current scope: the wizard is **optimized for docker‑compose** (all‑in‑one).
It creates a workdir (default: `~/.kdcube/kdcube-runtime`) and lets you:
- generate config/data/log folders
- choose where **all artifacts** come from (templates + images)
- start `docker compose` (optional)

Install source menu (shown only when `--path` is **not** provided):
- **upstream**: clone/pull the repo into the workspace; use `init --build` to prebuild images before start
- **release-latest**: pull prebuilt images for the latest release
- **release-tag**: pull prebuilt images for a specific version (platform.ref)
- **local**: use a local repo path you provide (build locally)
- **workspace**: use the repo already cloned in the workspace (build locally)

Defaults:
- If the workspace repo exists → default is **workspace**
- If it does not exist → default is **upstream**

The **workspace** option only appears when a repo is already cloned there.

Only **release-latest** and **release-tag** pull images. All other choices build locally.

Tip: you can select the install source using the ↑/↓ arrow keys and Enter.

Example workdir layout:

```
~/.kdcube/kdcube-runtime
├─ config/
│  ├─ .env
│  ├─ .env.ingress
│  ├─ .env.proc
│  ├─ .env.metrics
│  ├─ .env.postgres.setup
│  ├─ .env.proxylogin
│  ├─ frontend.config.<mode>.json
│  ├─ nginx_ui.conf
│  └─ nginx_proxy*.conf
├─ data/
│  ├─ postgres/
│  ├─ redis/
│  ├─ kdcube-storage/
│  │  ├─ cb/
│  │  │  └─ tenants/<tenant>/projects/<project>/
│  │  │     ├─ conversation/<user>/<conversation>/<turn>/
│  │  │     └─ executions/<user>/<conversation>/<turn>/<exec_id>/
│  │  ├─ accounting/<tenant>/project/<YYYY.MM.DD>/<service>/<bundle_id>/
│  │  └─ analytics/<tenant>/project/accounting/{daily,weekly,monthly}/
│  ├─ exec-workspace/
│  └─ bundle-storage/
└─ logs/
   ├─ chat-ingress/
   └─ chat-proc/
```

## Advanced usage

### Assembly descriptor (platform / frontend / infra)
You can point the CLI to an **assembly descriptor YAML** (`assembly.yaml`) that defines
platform metadata, frontend settings, auth, infra, and proxy defaults.

The wizard prompts for this as **Assembly descriptor path**.
**Wizard flow (descriptor usage):**
1) Provide the `assembly.yaml` path (defaults to `workdir/config/assembly.yaml`).
   If you provide another path, the CLI copies it into `workdir/config/assembly.yaml`
   and uses the copied file as the source of truth.
2) Choose whether to apply the **Frontend** section (build or image).

Repo field contract:
- `platform.repo` and `frontend.build.repo` should use a cloneable repo spec:
  - `git@github.com:org/repo.git`
  - `https://github.com/org/repo.git`
  - `org/repo`
- older single-name values such as `kdcube-ai-app` are still accepted for
  backward compatibility, but new descriptors should use one of the cloneable
  forms above

When an assembly descriptor is provided, the wizard **writes non‑secret values back**
into `assembly.yaml` (tenant/project, auth, infra, paths) and then renders `.env*`
from it. This makes `assembly.yaml` the source of truth for install‑time config.

The same assembly descriptor also configures runtime workspace/session bootstrap policy:

- `storage.workspace.type` -> `REACT_WORKSPACE_IMPLEMENTATION`
- `storage.workspace.repo` -> `REACT_WORKSPACE_GIT_REPO`
- `storage.claude_code_session.type` -> `CLAUDE_CODE_SESSION_STORE_IMPLEMENTATION`
- `storage.claude_code_session.repo` -> `CLAUDE_CODE_SESSION_GIT_REPO`

### Descriptor fast path requirements

The descriptor-folder fast path is used only when the descriptor set is complete
enough for a non-interactive install. At minimum:

- `assembly.yaml`, `secrets.yaml`, and `gateway.yaml` exist
- `assembly.secrets.provider == "secrets-file"`
- `assembly.context.tenant` and `assembly.context.project` are set
- `assembly.paths.host_bundles_path` is set
- `assembly.platform.ref` is set unless `--latest`, `--upstream`, or `--release` is used
- `assembly.domain` is set when `proxy.ssl: true`
- any git-backed workspace/session config includes its repo URL
- Cognito auth fields are present when `auth.type` requires them
- frontend build fields are present when `frontend.build` is set without `frontend.image`

If any of those are missing, the CLI falls back to the normal guided flow.

Reusing an initialized runtime without `--descriptors-location` requires the
same canonical descriptor set to already exist under `workdir/config`, plus
`workdir/config/install-meta.json` so the CLI can recover the repo context.

Template:
- [`app/ai-app/deployment/assembly.yaml`](../../../deployment/assembly.yaml) (copied into the workdir if no path is provided)

References:
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/service/cicd/descriptors-README.md
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/configuration/assembly-descriptor-README.md

### Bundles descriptor (optional)
You can provide a **bundles descriptor** (`bundles.yaml`) and an optional
**bundles secrets** file (`bundles.secrets.yaml`). This is the preferred way
to define bundles and their non‑secret config, with secrets kept separate.

If `workdir/config/bundles.yaml` already exists, the wizard pre-fills the prompt
with that path so it is reused on subsequent runs.

Note: secrets descriptors are **not** prefilled or cached.

The CLI stages `bundles.yaml` into the workdir and, when enabled:
- mounts the runtime workspace `config/` directory at `/config`
- sets `BUNDLES_PRELOAD_ON_START=1` in `.env.proc` by default
- enables bundle git resolution and env sync on startup

Current proc behavior:

- `config/bundles.yaml` is the normal bundle descriptor authority
- proc can seed/reset from that descriptor directly

Local bundle root contract:

- `assembly.paths.host_bundles_path` is installer-facing config for non-managed local path bundles and becomes `HOST_BUNDLES_PATH`
- compose mounts `HOST_BUNDLES_PATH` into proc as `BUNDLES_ROOT` (normally `/bundles`)
- non-managed local bundle entries in `bundles.yaml` must use the container-visible path, for example:
  - host folder: `/Users/you/dev/bundles/my.bundle`
  - descriptor path: `/bundles/my.bundle`

- `assembly.paths.host_managed_bundles_path` becomes `HOST_MANAGED_BUNDLES_PATH`
- compose mounts `HOST_MANAGED_BUNDLES_PATH` into proc as `MANAGED_BUNDLES_ROOT` (normally `/managed-bundles`)

Managed bundle materialization uses the dedicated managed root:

- non-managed local path bundles continue to use `HOST_BUNDLES_PATH` and `/bundles/...`
- git bundles are cloned under `HOST_MANAGED_BUNDLES_PATH` and resolved inside proc as `/managed-bundles/...`
- built-in example bundles are also materialized under the managed root

Symlink note:

- if you symlink a bundle into `HOST_BUNDLES_PATH`, proc sees the symlink through the `/bundles` mount
- this works only if Docker can also access the symlink target on the host
- safest local pattern: keep the real bundle folder inside `HOST_BUNDLES_PATH`, or symlink only to another host path that is already accessible through the same Docker file-sharing scope

`bundles.secrets.yaml` is staged into the workdir only when it is provided.
If `assembly.yaml -> secrets.provider == "secrets-file"`, runtime resolves it
from `/config/bundles.secrets.yaml` via `PLATFORM_DESCRIPTORS_DIR=/config`.

Example (`bundles.yaml`):
```yaml
bundles:
  version: "1"
  default_bundle_id: "react@2026-02-10-02-44"
  items:
    - id: "react@2026-02-10-02-44"
      name: "ReAct (example)"
      repo: "git@github.com:kdcube/kdcube-ai-app.git"
      ref: "v0.3.2"
      subdir: "app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles"
      module: "react@2026-02-10-02-44.entrypoint"
      config:
        embedding:
          provider: "openai"
          model: "text-embedding-3-small"
        role_models:
          solver.react.v2.decision.v2.strong:
            provider: "anthropic"
            model: "claude-sonnet-4-6"
```

Example (`bundles.secrets.yaml`):
```yaml
bundles:
  version: "1"
  items:
    - id: "react@2026-02-10-02-44"
      secrets:
        openai:
          api_key: null
```

Templates:
- [`app/ai-app/deployment/bundles.yaml`](../../../deployment/bundles.yaml)
- [`app/ai-app/deployment/bundles.secrets.yaml`](../../../deployment/bundles.secrets.yaml)

For local host-edited bundle development:

- define the bundle with `path: /bundles/...`
- set `assembly.paths.host_bundles_path` to the matching host root
- run KDCube through the CLI compose path
- use `kdcube reload <bundle_id>` after code changes

For AWS deployment:

- use git bundle descriptors only
- do not use local `path:` bundle entries
- do not carry local `assembly.paths.host_bundles_path` values into the AWS descriptor set

References:
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/configuration/bundles-descriptor-README.md
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/configuration/bundles-secrets-descriptor-README.md

### Secrets descriptor (optional)
You can provide a `secrets.yaml` path in the wizard (or via `KDCUBE_SECRETS_DESCRIPTOR_PATH`).
The CLI stages this file into the runtime workspace `config/` directory.

It is used:
- to prefill runtime secrets (OpenAI/Anthropic/Git HTTP token and delegated Cognito client secret) during guided setup
- or as the runtime secrets source when `assembly.yaml -> secrets.provider == "secrets-file"`

In `secrets-file` mode runtime resolves `/config/secrets.yaml` via
`PLATFORM_DESCRIPTORS_DIR=/config`.

Values injected through the `secrets-service` flow are still **not** written to `.env.proc`.

Secrets are keyed by **dot‑path** (e.g. `services.openai.api_key`).

Template:
- [`app/ai-app/deployment/secrets.yaml`](../../../deployment/secrets.yaml)

Reference:
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/configuration/secrets-descriptor-README.md

### Gateway config descriptor (optional)
You can provide a `gateway.yaml` path in the wizard (or via `KDCUBE_GATEWAY_DESCRIPTOR_PATH`).
The CLI stages it into `workdir/config` and points runtime at `/config` via
`PLATFORM_DESCRIPTORS_DIR`.

In current descriptor mode, gateway policy authority is therefore the staged
workspace descriptor, not a copied field-by-field block in the service env
files.

If another platform also injects `GATEWAY_CONFIG_JSON`, that JSON still wins at
runtime because gateway loader precedence prefers it over `gateway.yaml`.

If `workdir/config/gateway.yaml` already exists, the wizard pre-fills the prompt
with that path so it is reused on subsequent runs.

Template:
- [`app/ai-app/deployment/gateway.yaml`](../../../deployment/gateway.yaml)

Reference:
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/configuration/gateway-descriptor-README.md

### Custom UI via assembly descriptor (build or image)
If your `assembly.yaml` includes `frontend.build` or `frontend.image`, the CLI will switch to
**custom‑ui‑managed‑infra** compose mode.

`frontend.config` alone (auth type, routes prefix, debug flags) is runtime metadata used in
both modes — it does **not** trigger `custom‑ui‑managed‑infra`.

Minimal example:
```yaml
frontend:
  build:
    repo: "git@github.com:org/private-app.git"
    ref: "ui-v2026.02.22"
    dockerfile: "ops/docker/Dockerfile_UI"
    src: "ui/chat-web-app"
  image: "registry/private-app-ui:2026.02.22"  # optional prebuilt UI image; if set, CLI writes KDCUBE_UI_IMAGE and skips the local web-ui build
  frontend_config: "ops/docker/config.delegated.json"  # optional
  nginx_ui_config: "ops/docker/nginx_ui.conf"          # optional
```

Frontend/runtime config behavior:
- `frontend.image` is optional. When present, the CLI writes `KDCUBE_UI_IMAGE` and treats the UI as a prebuilt image override.
- If `frontend.image` is omitted but `frontend.build` is present, the CLI clones/uses the frontend source repo and builds `web-ui` locally from `build.repo`, `build.ref`, `build.dockerfile`, and `build.src`.
- `frontend.build.repo` accepts SSH URLs, HTTPS URLs, and `owner/repo` shorthand.
- `frontend.build.image_name` is not used by the CLI installer. That field belongs to the ECS CI/CD flow, not the local docker-compose flow.

- If `frontend.frontend_config` is provided, the CLI uses it as the template for the
  generated runtime `config.json` and patches tenant/project/auth/routesPrefix values.
- If it is omitted, the CLI falls back to a built-in template by auth mode:
  - `simple` -> `config.hardcoded.json`
  - `cognito` -> `config.cognito.json`
  - `delegated` -> `config.delegated.json`
- If `frontend.nginx_ui_config` is omitted, the CLI falls back to the built-in `nginx_ui.conf`.

How to activate:
1) Run `kdcube`
2) Choose **Use an assembly descriptor** → provide `assembly.yaml`
3) Confirm **Frontend** usage when prompted.
4) The CLI selects `deployment/docker/custom-ui-managed-infra/docker-compose.yaml`.

Full details:
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/service/cicd/descriptors-README.md
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/configuration/assembly-descriptor-README.md
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/service/cicd/custom-cicd-README.md
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/service/cicd/cli-README.md

## Where data is stored
- **Config:** `workdir/config/` (env files, nginx config, UI config)
- **Data:** `workdir/data/` (postgres/redis storage, bundle storage, exec workspace)
- **Logs:** `workdir/logs/`

Infra credentials (Postgres/Redis) are stored in `config/.env*` for local compose.
LLM keys are staged in `config/secrets.yaml` and loaded through the descriptor
secret flow.

## Notes

- The wizard **does not overwrite** existing config files in your workdir. It only fills
  placeholders in newly created files.
- Use `kdcube init --reset-config` to re-enter values without deleting files.
- Config upgrades/migrations will be added later when configs are versioned.
- The wizard auto‑saves after major sections, so if you exit early (Ctrl+C) most
  values entered so far are preserved in `config/` and will appear as defaults next run.

Tip: you can edit `workdir/config/nginx_ui.conf` and the selected `workdir/config/nginx_proxy*.conf`
without rebuilding images (they are mounted into the containers at runtime).

## UI config source of truth

The web UI loads its runtime config from `/config.json` inside the `web-ui`
container. Docker compose mounts the host file defined by
`PATH_TO_FRONTEND_CONFIG_JSON` to:

`/usr/share/nginx/html/config.json`

## Clean / reset
Clean local Docker cache and unused KDCube images:
```bash
kdcube clean
```

Reset prompts without deleting files:
```bash
kdcube init --reset-config
```

Full reset (delete workdir):
```bash
rm -rf ~/.kdcube/kdcube-runtime
```

If the UI is calling the wrong tenant/project, check:
- `PATH_TO_FRONTEND_CONFIG_JSON` in the generated `.env`
- `curl http://localhost:<ui_port>/config.json`

See the full local setup flow on GitHub:
https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/ops/local/local-setup-README.md

More documentation:
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/service/cicd/descriptors-README.md
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/configuration/assembly-descriptor-README.md
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/configuration/bundles-descriptor-README.md
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/configuration/service-runtime-configuration-mapping-README.md

## License
MIT License. See `app/ai-app/src/kdcube-ai-app/LICENSE`.
