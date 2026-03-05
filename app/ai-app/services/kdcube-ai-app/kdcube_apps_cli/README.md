# KDCube CLI

![KDCube CLI](https://raw.githubusercontent.com/kdcube/kdcube-ai-app/master/app/ai-app/services/kdcube-ai-app/kdcube_apps_cli/pixel-cubes.png)

Bootstrap installer for the KDCube platform stack. This package clones the
repository (if needed) and launches the guided setup wizard.

CLI source: `services/kdcube-ai-app/kdcube_ai_app/ops/cli`
Packaged assets:
- `kdcube_apps_cli/assets/pixel-cubes.svg`
- `kdcube_apps_cli/assets/pixel-cubes.png`

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
pipx install kdcube-apps-cli
```

Alternative (pip):

```bash
python -m pip install --user kdcube-apps-cli
```

## Run

```bash
kdcube-apps-cli
```

Tip: if `kdcube-apps-cli` is not on your PATH, run:

```bash
python -m kdcube_apps_cli.cli
```

## What the wizard does

- Creates a **workdir** with `config/`, `data/`, and `logs/` folders
- Writes the compose env files into `config/` (only if missing; it won’t overwrite existing files)
- Copies nginx configs into `config/` for runtime overrides:
  - `nginx_ui.conf`
  - `nginx_proxy.conf`
- Generates frontend runtime config
- Creates local data folders for Postgres/Redis/exec workspace/bundle storage
- Optionally builds images and runs `docker compose up -d --build`

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

## Dev‑host usage (run services on host)

You can still use the CLI to bootstrap a workdir and then run services locally:

1) Run the wizard and choose a workdir.
2) Point your IDE/run configs to the generated env files in `workdir/config`.
3) The CLI also writes a dev UI config to:
   - `app/ai-app/ui/chat-web-app/public/private/config.hardcoded.json`
4) Start local infra via `deployment/docker/local-infra-stack` if needed.

Tip: you can edit `workdir/config/nginx_ui.conf` and `workdir/config/nginx_proxy.conf`
without rebuilding images (they are mounted into the containers at runtime).

See `app/ai-app/docs/service/environment/setup-dev-env-README.md` for the full
dev‑host flow.
