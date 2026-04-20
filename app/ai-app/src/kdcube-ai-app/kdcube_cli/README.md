# KDCube CLI

![KDCube CLI](https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/src/kdcube-ai-app/kdcube_cli/pixel-cubes.png)

Bootstrap installer for the KDCube platform stack. This package clones the
repository (if needed) and launches the guided setup wizard.

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
kdcube
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
kdcube \
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
checkout.

Or pull the latest platform release from the platform repo instead of
`assembly.yaml -> platform.ref`:

```bash
kdcube \
  --descriptors-location /path/to/descriptors \
  --workdir /path/to/workspace \
  --latest
```

Or build from the latest upstream repo state instead of a released ref:

```bash
kdcube \
  --descriptors-location /path/to/descriptors \
  --workdir /path/to/workspace \
  --build \
  --upstream
```

Use `--build --upstream` when you want the deployment assets from the latest
GitHub `origin/main`, including:

- compose files
- nginx templates
- installer-side deployment templates

If the runtime was already initialized earlier, you can omit
`--descriptors-location` and reuse the staged descriptors from `workdir/config`
instead:

```bash
kdcube \
  --workdir /path/to/workspace/<safe_tenant>__<safe_project> \
  --build \
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

`--latest` is different: it resolves the latest release ref for release-image
installs. It does not mean “latest source templates from GitHub main”.

Or pin a specific release explicitly:

```bash
kdcube \
  --descriptors-location /path/to/descriptors \
  --release 2026.4.11.012
```

Choose exactly one source selector:
- `--upstream` with `--build` for the latest upstream repo state
- `--latest` for the latest released platform ref
- `--release <ref>` for a specific released ref
- otherwise `assembly.yaml -> platform.ref`

For `aws-sm` deployments, you can also export the current effective live
deployment-scoped bundle descriptors directly from AWS Secrets Manager:

```bash
kdcube \
  --export-live-bundles \
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
- Docker images: pulled (**release-latest**/**release-tag**) or built (**upstream**/**workspace**/**local**)

### CLI options (common)
| Option | Purpose |
|---|---|
| `--repo <url>` | Git repo URL (default: official kdcube repo). |
| `--path <repo>` | Use a specific local repo checkout for templates and builds. If omitted in descriptor mode, the checkout defaults to `<workspace>/<tenant>__<project>/repo`. |
| `--workdir <path>` | Base workspace root. In descriptor mode the effective runtime becomes `<workdir>/<tenant>__<project>`. If it already points to an initialized runtime with `config/install-meta.json` and the canonical descriptor set under `config/`, the CLI can reuse that runtime non-interactively. |
| `--descriptors-location <dir>` | Use a folder containing `assembly.yaml`, `secrets.yaml`, `gateway.yaml`, and optional bundle descriptors. |
| `--latest` | With `--descriptors-location`, resolve the latest platform release instead of using `assembly.yaml -> platform.ref`. |
| `--upstream` | With `--build`, use the latest upstream repo state (`origin/main`) instead of a released platform ref. Requires either `--descriptors-location` or an initialized runtime with the canonical descriptor set under `config/`. |
| `--release <ref>` | With `--descriptors-location`, use the given platform release instead of `assembly.yaml -> platform.ref`. |
| `--bundle-reload <bundle_id>` | Reapply `config/bundles.yaml` from the active runtime workspace and clear proc bundle caches for local development. |
| `--info` | Print resolved runtime info for the selected workdir, including descriptor paths, install metadata, and host/container bundle mount mappings. |
| `--export-live-bundles` | Export effective live `bundles.yaml` and `bundles.secrets.yaml` from the active bundle authority: workspace descriptors when present, otherwise AWS SM grouped bundle docs. |
| `--tenant <id>` / `--project <id>` | Scope for `--export-live-bundles` when exporting from AWS SM. Ignored when workspace descriptors are exported directly. |
| `--out-dir <dir>` | Output directory for `--export-live-bundles`. |
| `--aws-region <region>` | AWS region for `--export-live-bundles` when exporting from AWS SM. |
| `--aws-profile <profile>` | AWS profile for `--export-live-bundles` when exporting from AWS SM. |
| `--aws-sm-prefix <prefix>` | Explicit AWS SM prefix for `--export-live-bundles` when exporting from AWS SM. |
| `--stop` | Stop the local Docker Compose stack for the selected workdir. |
| `--remove-volumes` | With `--stop`, also remove local volumes. |
| `--reset-config` | Re‑prompt for config values without deleting files. |
| `--reset` | Alias for `--reset-config`. |
| `--clean` | Clean local Docker cache and unused KDCube images. |
| `--secrets-prompt` | Prompt for LLM keys (OpenAI/Anthropic/Brave/OpenRouter) and inject them at runtime (sidecar). |
| `--secrets-set KEY=VALUE` | Inject a secret value without prompting (repeatable). |
| `--proxy-ssl` | Force SSL proxy config (overrides assembly descriptor). |
| `--no-proxy-ssl` | Force non‑SSL proxy config (overrides assembly descriptor). |
| `--dry-run` | Generate env files and print their paths without running Docker. |
| `--dry-run-print-env` | With `--dry-run`, also print the full env file contents. |

### Use a local checkout (dev)

```bash
kdcube --path /Users/you/src/kdcube/kdcube-ai-app
```

When `--path` is provided, the wizard **uses that repo for templates and local builds**
and **does not show the Install source menu**.

Re-run prompts (edit existing values):

```bash
kdcube --reset
```

Clean local Docker images/cache:

```bash
kdcube --clean
```

Stop the local stack:

```bash
kdcube --workdir ~/.kdcube/kdcube-runtime --stop
```

Stop and remove volumes:

```bash
kdcube --workdir ~/.kdcube/kdcube-runtime --stop --remove-volumes
```

Inspect the resolved runtime, including how local non-git bundles are mounted:

```bash
kdcube --workdir ~/.kdcube/kdcube-runtime/acme__prod_demo --info
```

When `--workdir` points at the base workspace root, `--stop` resolves the
single matching runtime namespace automatically. If there are multiple runtime
namespaces under that base workspace, pass the concrete namespaced runtime path
explicitly.

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
The wizard **does not** write OpenAI/Anthropic/Brave keys to `.env` files.
If you provide them during setup, they are injected at runtime into the
`kdcube-secrets` sidecar (in‑memory only) when `assembly.yaml` uses
`secrets.provider: secrets-service`. If you restart the stack, you’ll be
prompted again to re‑inject keys.

Order (automatic):
1) Start `kdcube-secrets`
2) Wait for it to be ready
3) Inject keys
4) Start/restart ingress + proc (they fetch secrets)

Manual re‑inject:

```bash
kdcube --secrets-prompt --workdir ~/.kdcube/kdcube-runtime
```

Or pass explicit values:

```bash
kdcube --secrets-set OPENAI_API_KEY=... --secrets-set ANTHROPIC_API_KEY=...
```

You can also override the git HTTPS token this way:

```bash
kdcube --secrets-set GIT_HTTP_TOKEN=...
```

Note: re‑inject will **restart** `kdcube-secrets`, `chat-ingress`, and `chat-proc`
to refresh per‑run tokens (and the web proxy to keep upstreams in sync).
Per‑run tokens are generated by the CLI and are **not stored** in `config/`.

If you set LLM keys in env files (managed infra / custom setups), those env
values still work and take precedence. The secrets sidecar is only used when
env keys are missing.

### Git HTTPS token (private bundles)
If you choose **https-token** auth for git bundles, the token is treated as a
secret and is **not stored** in `.env.proc`. It is injected at runtime (same
flow as LLM keys). You will be prompted again on the next run unless you pass
it via `--secrets-set GIT_HTTP_TOKEN=...`.

More details:
https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/ops/local/local-setup-README.md

Current scope: the wizard is **optimized for docker‑compose** (all‑in‑one).
It creates a workdir (default: `~/.kdcube/kdcube-runtime`) and lets you:
- generate config/data/log folders
- choose where **all artifacts** come from (templates + images)
- start `docker compose` (optional)

Install source menu (shown only when `--path` is **not** provided):
- **upstream**: clone/pull the repo into the workspace and build images locally
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
- frontend build fields are present when `frontend` is used without `frontend.image`

If any of those are missing, the CLI falls back to the normal guided flow.

Reusing an initialized runtime without `--descriptors-location` requires the
same canonical descriptor set to already exist under `workdir/config`, plus
`workdir/config/install-meta.json` so the CLI can recover the repo context.

Template:
- [`app/ai-app/deployment/assembly.yaml`](../../../deployment/assembly.yaml) (copied into the workdir if no path is provided)

References:
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/service/cicd/descriptors-README.md
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/service/configuration/assembly-descriptor-README.md

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
- use `kdcube --bundle-reload <bundle_id>` after code changes

For AWS deployment:

- use git bundle descriptors only
- do not use local `path:` bundle entries
- do not carry local `assembly.paths.host_bundles_path` values into the AWS descriptor set

References:
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/service/configuration/bundles-descriptor-README.md
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/service/configuration/bundles-secrets-descriptor-README.md

### Secrets descriptor (optional)
You can provide a `secrets.yaml` path in the wizard (or via `KDCUBE_SECRETS_DESCRIPTOR_PATH`).
The CLI stages this file into the runtime workspace `config/` directory.

It is used:
- to prefill runtime secrets (OpenAI/Anthropic/Brave/Git HTTP token and delegated Cognito client secret) during guided setup
- or as the runtime secrets source when `assembly.yaml -> secrets.provider == "secrets-file"`

In `secrets-file` mode runtime resolves `/config/secrets.yaml` via
`PLATFORM_DESCRIPTORS_DIR=/config`.

Values injected through the `secrets-service` flow are still **not** written to `.env.proc`.

Secrets are keyed by **dot‑path** (e.g. `services.openai.api_key`).

Template:
- [`app/ai-app/deployment/secrets.yaml`](../../../deployment/secrets.yaml)

Reference:
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/service/configuration/secrets-descriptor-README.md

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
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/service/configuration/gateway-descriptor-README.md

### Custom UI via assembly descriptor (build or image)
If your `assembly.yaml` includes a `frontend` section, the CLI will switch to
**custom‑ui‑managed‑infra** compose mode.

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
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/service/configuration/assembly-descriptor-README.md
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/service/cicd/custom-cicd-README.md
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/service/cicd/cli-README.md

### Manual compose (advanced)

If you want to run compose manually, use the workdir env file:

```bash
docker compose --env-file ~/.kdcube/kdcube-runtime/config/.env up -d --build
```

Note: `--env-file` is a **Docker Compose** option (not a CLI flag).

## Where data is stored
- **Config:** `workdir/config/` (env files, nginx config, UI config)
- **Data:** `workdir/data/` (postgres/redis storage, bundle storage, exec workspace)
- **Logs:** `workdir/logs/`

Infra credentials (Postgres/Redis) are stored in `config/.env*` for local compose.
LLM keys are **not** stored in files; they live only in the secrets sidecar.

## Compose usage (recommended)

1) Run the wizard and choose a workdir (example: `/srv/kdcube-local`).
2) It will generate:
   - `/srv/kdcube-local/config/.env`
   - `/srv/kdcube-local/config/.env.ingress`
   - `/srv/kdcube-local/config/.env.proc`
   - `/srv/kdcube-local/config/.env.metrics`
   - `/srv/kdcube-local/config/.env.postgres.setup`
   - `/srv/kdcube-local/config/.env.proxylogin`
   - `/srv/kdcube-local/config/frontend.config.<mode>.json`
   - `/srv/kdcube-local/config/nginx_ui.conf`
   - `/srv/kdcube-local/config/nginx_proxy*.conf`
3) Start compose from `deployment/docker/all_in_one_kdcube`:

```bash
docker compose --env-file /srv/kdcube-local/config/.env up -d --build
```

Open the UI:
- `http://localhost[:port]<routesPrefix>/chat`

`routesPrefix` comes from the generated frontend `config.json`. When it is not
set explicitly, the default is `/chatbot`.
  (via proxy; if `KDCUBE_PROXY_HTTP_PORT` is unset, it falls back to `KDCUBE_UI_PORT`)

## Notes

- The wizard **does not overwrite** existing config files in your workdir. It only fills
  placeholders in newly created files.
- Use `kdcube --reset` to re-enter values without deleting files.
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
kdcube --clean
```

Reset prompts without deleting files:
```bash
kdcube --reset
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
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/service/configuration/assembly-descriptor-README.md
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/service/configuration/bundles-descriptor-README.md
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/service/configuration/service-config-README.md

## License
MIT License. See `app/ai-app/src/kdcube-ai-app/LICENSE`.
