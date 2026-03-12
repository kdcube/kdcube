# KDCube CLI

![KDCube CLI](https://raw.githubusercontent.com/kdcube/kdcube-ai-app/main/app/ai-app/services/kdcube-ai-app/kdcube_cli/pixel-cubes.png)

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

## Run

```bash
kdcube-setup
```

## Quick start (new users)

1) Run `kdcube-setup`  
2) Choose **release-latest** (pull prebuilt images)  
3) Answer **yes** to “Run docker compose now?”  

That brings up the stack with no local build required.

We aim for a setup that is simple to try, and easy to explore further using the
installed admin assistant and bundled tools.

## What it installs (default)
- Repo clone: `~/.kdcube/kdcube-ai-app`
- Workdir: `~/.kdcube/kdcube-runtime`
- Docker images: pulled (**release-latest**/**release-tag**) or built (**upstream**/**workspace**/**local**)

### CLI options (common)
| Option | Purpose |
|---|---|
| `--repo <url>` | Git repo URL (default: official kdcube repo). |
| `--path <repo>` | Use a local repo checkout for templates and builds (skips install-source menu). |
| `--workdir <path>` | Use a specific workdir instead of `~/.kdcube/kdcube-runtime`. |
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
kdcube-setup --path /Users/you/src/kdcube/kdcube-ai-app
```

When `--path` is provided, the wizard **uses that repo for templates and local builds**
and **does not show the Install source menu**.

Re-run prompts (edit existing values):

```bash
kdcube-setup --reset
```

Clean local Docker images/cache:

```bash
kdcube-setup --clean
```

Tip: if `kdcube-setup` is not on your PATH, run `python -m pipx ensurepath`
or re-open your shell after installation.

## What the wizard does (today)

When you run `kdcube-setup`, the **wizard** performs the steps below:
1) Creates a **workdir** with `config/`, `data/`, and `logs/` folders.
2) Writes compose env files into `config/` (only if missing; it won’t overwrite existing files).
3) Copies nginx configs into `config/` for runtime overrides:
   - `nginx_ui.conf`
   - runtime proxy config (based on selected auth mode)
4) Selects **auth mode** (simple or cognito) and writes:
   - `AUTH_PROVIDER` in `.env.ingress` + `.env.proc`
   - Cognito fields when applicable (see below)
5) Generates frontend runtime config (hardcoded or cognito).
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
- Uses the delegated proxy template (proxylogin) while still validating tokens via Cognito.

### Routes prefix & nginx proxy
The frontend config includes `routesPrefix` (default: `/chatbot`).
The wizard patches the **runtime proxy config** in `config/` so nginx uses the
same prefix. This keeps `/chatbot` (or any custom prefix) consistent between UI and proxy.

### Secrets (third services tokens)
The wizard **does not** write OpenAI/Anthropic/Brave keys to `.env` files.
If you provide them during setup, they are injected at runtime into the
`kdcube-secrets` sidecar (in‑memory only). If you restart the stack, you’ll
be prompted again to re‑inject keys.

Order (automatic):
1) Start `kdcube-secrets`
2) Wait for it to be ready
3) Inject keys
4) Start/restart ingress + proc (they fetch secrets)

Manual re‑inject:

```bash
kdcube-setup --secrets-prompt --workdir ~/.kdcube/kdcube-runtime
```

Or pass explicit values:

```bash
kdcube-setup --secrets-set OPENAI_API_KEY=... --secrets-set ANTHROPIC_API_KEY=...
```

You can also override the git HTTPS token this way:

```bash
kdcube-setup --secrets-set GIT_HTTP_TOKEN=...
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
│  ├─ frontend.config.hardcoded.json
│  ├─ nginx_ui.conf
│  └─ nginx_proxy.conf
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

When an assembly descriptor is provided, the wizard **writes non‑secret values back**
into `assembly.yaml` (tenant/project, auth, infra, paths) and then renders `.env*`
from it. This makes `assembly.yaml` the source of truth for install‑time config.

Template:
- [`app/ai-app/deployment/assembly.yaml`](../../../deployment/assembly.yaml) (copied into the workdir if no path is provided)

References:
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/sdk/bundle/bundle-ops-README.md
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/service/cicd/assembly-descriptor-README.md

### Bundles descriptor (optional)
You can provide a **bundles descriptor** (`bundles.yaml`) and an optional
**bundles secrets** file (`bundles.secrets.yaml`). This is the preferred way
to define bundles and their non‑secret config, with secrets kept separate.

If `workdir/config/bundles.yaml` already exists, the wizard pre-fills the prompt
with that path so it is reused on subsequent runs.

Note: secrets descriptors are **not** prefilled or cached.

The CLI stages `bundles.yaml` into the workdir and, when enabled:
- mounts `bundles.yaml` as `/config/bundles.yaml`
- sets `AGENTIC_BUNDLES_JSON=/config/bundles.yaml`
- enables bundle git resolution and env sync on startup

`bundles.secrets.yaml` is **not copied** into the workdir. It is read from the
path you provide and used only to inject secrets at runtime.

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
      subdir: "app/ai-app/services/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles"
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

References:
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/service/configuration/bundle-configuration-README.md
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/sdk/bundle/bundle-dev-README.md

### Secrets descriptor (optional)
You can provide a `secrets.yaml` path in the wizard (or via `KDCUBE_SECRETS_DESCRIPTOR_PATH`).
The CLI **does not copy** this file into the workdir; it is used only to prefill runtime
secrets (OpenAI/Anthropic/Brave/Git HTTP token and delegated Cognito client secret).
Values are injected into the secrets sidecar at runtime and **not** written to `.env.proc`.

Secrets are keyed by **dot‑path** (e.g. `services.openai.api_key`).

Template:
- [`app/ai-app/deployment/secrets.yaml`](../../../deployment/secrets.yaml)

### Gateway config descriptor (optional)
You can provide a `gateway.yaml` path in the wizard (or via `KDCUBE_GATEWAY_DESCRIPTOR_PATH`).
The CLI loads it and replaces `GATEWAY_CONFIG_JSON` in `.env.ingress`, `.env.proc`,
and `.env.metrics`, then patches `tenant` and `project` from your prompts.

If `workdir/config/gateway.yaml` already exists, the wizard pre-fills the prompt
with that path so it is reused on subsequent runs.

Template:
- [`app/ai-app/deployment/gateway.yaml`](../../../deployment/gateway.yaml)

References:
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/service/cicd/gateway-config-README.md

### Custom UI via assembly descriptor (build or image)
If your `assembly.yaml` includes a `frontend` section, the CLI will switch to
**custom‑ui‑managed‑infra** compose mode.

Minimal example:
```yaml
frontend:
  build:
    repo: "git@github.com:org/private-ui.git"
    ref: "ui-v2026.02.22"
    dockerfile: "ops/docker/Dockerfile_UI"
    src: "ui/chat-web-app"
  image: "registry/private-ui:2026.02.22"  # optional; if set, CLI skips UI build
  frontend_config: "ops/docker/config.cognito.json"
```

How to activate:
1) Run `kdcube-setup`
2) Choose **Use an assembly descriptor** → provide `assembly.yaml`
3) Confirm **Frontend** usage when prompted.
4) The CLI selects `deployment/docker/custom-ui-managed-infra/docker-compose.yaml`.

Full details:
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/service/cicd/assembly-descriptor-README.md
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
   - `/srv/kdcube-local/config/frontend.config.hardcoded.json`
   - `/srv/kdcube-local/config/nginx_ui.conf`
   - `/srv/kdcube-local/config/nginx_proxy.conf`
3) Start compose from `deployment/docker/all_in_one_kdcube`:

```bash
docker compose --env-file /srv/kdcube-local/config/.env up -d --build
```

Open the UI:
- `http://localhost:${KDCUBE_UI_PORT}/chatbot/chat` (via proxy, omit `:${KDCUBE_UI_PORT}` if it is `80`)

## Notes

- The wizard **does not overwrite** existing config files in your workdir. It only fills
  placeholders in newly created files.
- Use `kdcube-setup --reset` to re-enter values without deleting files.
- Config upgrades/migrations will be added later when configs are versioned.
- The wizard auto‑saves after major sections, so if you exit early (Ctrl+C) most
  values entered so far are preserved in `config/` and will appear as defaults next run.

Tip: you can edit `workdir/config/nginx_ui.conf` and `workdir/config/nginx_proxy.conf`
without rebuilding images (they are mounted into the containers at runtime).

## UI config source of truth

The web UI loads its runtime config from `/config.json` inside the `web-ui`
container. Docker compose mounts the host file defined by
`PATH_TO_FRONTEND_CONFIG_JSON` to:

`/usr/share/nginx/html/config.json`

## Clean / reset
Clean local Docker cache and unused KDCube images:
```bash
kdcube-setup --clean
```

Reset prompts without deleting files:
```bash
kdcube-setup --reset
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
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/service/cicd/assembly-descriptor-README.md
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/service/cicd/custom-cicd-README.md
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/service/cicd/cli-README.md

## License
MIT License. See `app/ai-app/services/kdcube-ai-app/LICENSE`.
