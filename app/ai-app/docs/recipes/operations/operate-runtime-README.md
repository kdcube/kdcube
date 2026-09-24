---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/operations/operate-runtime-README.md
title: "Operate A KDCube Runtime"
summary: "The daily run sheet: lifecycle, the four change loops (platform code, staged config, app descriptors, one app), export/import, and honest verification."
status: active
tags: ["operations", "cli", "kdcube-cli", "refresh", "reload", "export", "import"]
keywords: ["operate KDCube runtime", "platform refresh", "maintainer package source", "bundle reload", "runtime export", "runtime verification"]
updated_at: 2026-09-24
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/operations/install-clean-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/operations/install-from-descriptors-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/operations/move-authority-to-postgresql-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/cli-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/procedures/platform-source-testing-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/ngrok-README.md
---
# Operate A KDCube Runtime

Everything after install is four loops on one runtime. The staged
descriptors under `$WORKDIR/config/*.yaml` are the runtime authority; seed
descriptor folders are inputs you apply, never the thing services read.

Save defaults once so daily commands are short:

```shell
kdcube defaults --default-tenant "$TENANT" --default-project "$PROJECT" \
  --default-workdir "$HOME/.kdcube/kdcube-runtime/${TENANT}__${PROJECT}"
```

## Lifecycle

```shell
kdcube start                 # start the stack
kdcube stop                  # stop it (data volumes preserved)
kdcube info                  # running image IDs, source versions, mounts, URLs
kdcube stop --remove-volumes # DANGER: wipes local Postgres/Redis data
```

## The four change loops

**1. Platform code changed** (pulled or edited the checkout):

```shell
git -C "$REPO" pull --ff-only origin main
kdcube refresh --path "$REPO" --build   # copy checkout in, rebuild, restart
kdcube refresh --build                  # rebuild from already-staged source
kdcube refresh --upstream --build       # or move to a git source: --latest / --release <tag>
```

`refresh` preserves staged descriptors and never touches data volumes.
`--no-restart` builds without restarting.

Platform contributors testing coordinated local package source use the
[maintainer package-source refresh](../../service/cicd/cli-README.md#maintainer-local-python-package-sources).

**2. Staged config edited by hand** (`$WORKDIR/config/*.yaml`):

```shell
kdcube refresh                          # restart only; your edits survive
```

**3. App descriptors are the source of truth** (a seed
`bundles.yaml` + `bundles.secrets.yaml` folder to reapply):

```shell
kdcube bundle config apply --descriptors-location "$DESCRIPTORS" --dry-run
kdcube bundle config apply --descriptors-location "$DESCRIPTORS" --reload
```

Local host paths are normalized to `/bundles/...` on staging; `--reload`
tells the running proc to reload the changed apps — no Docker restart.

**4. One app changed** (config, secrets, or source on the mounted path):

```shell
kdcube reload <app-id>                  # the fast path, no Docker restart
kdcube bundle status <app-id> --live --json # descriptor vs proc vs widget source
```

Single keys without opening an editor:

```shell
kdcube bundle <app-id> --set-config integrations.telegram.webhook_url "https://<public-host>/api/…"
kdcube bundle <app-id> --set-secret integrations.telegram.bot_token "$TELEGRAM_BOT_TOKEN"
kdcube reload <app-id>
```

Never rerun `init` on an existing runtime — it refuses, by design.

Moving an existing runtime's session and Connection Hub authority from Redis
to PostgreSQL is a dry-run-first generation cutover. Follow
[Move Runtime Authority To PostgreSQL](move-authority-to-postgresql-README.md);
do not switch either descriptor backend before its activation receipt exists.

## Export / import (environment as an artifact)

```shell
OUT_DIR="$HOME/.kdcube/exports/${TENANT}__${PROJECT}-$(date +%Y%m%dT%H%M%S)"
kdcube config export --out-dir "$OUT_DIR" --include-platform-descriptors
# review/edit, then:
kdcube config import --descriptors-location "$OUT_DIR" --include-platform-descriptors --dry-run
kdcube config import --descriptors-location "$OUT_DIR" --include-platform-descriptors
kdcube refresh                          # services pick up env-level changes
```

Export resolves runtime paths back to host paths (git-backed apps keep
repo/ref/subdir); the export contains real secret values — sensitive.
This export/align/init triple is the reproduction path:
[Install From Descriptors](install-from-descriptors-README.md).

## Verification that tells the truth

Start with the source-attestation commands documented under
[bundle status](../../service/cicd/cli-README.md#status) and
[operational commands](../../service/cicd/cli-README.md#9-operational-commands):

```shell
kdcube info --workdir "$WORKDIR"
kdcube bundle status <app-id> --live --workdir "$WORKDIR"
```

Then verify the user door and inspect preparation/build errors:

```shell
curl -s -o /dev/null -w "chat UI -> HTTP %{http_code}\n" http://localhost:5173/platform/chat

PROC="$(docker ps --format '{{.Names}}' | grep chat-proc | head -1)"
docker logs --since 10m "$PROC" 2>&1 | \
  grep -iE "git.bundle|bundle.ui|widget:.*build (done|failed)|preload|ERROR|Traceback" | tail -40
```

## Public origin for webhooks and OAuth

Local runtimes that must be reachable from outside (Telegram webhooks,
OAuth callbacks) put ONE stable HTTPS origin in front of the local web
proxy — see `docs/service/cicd/ngrok-README.md`. Keep `domain: ""` and
`proxy.ssl: false` locally; the tunnel origin belongs in CORS
(`--cors-origin` at init) and in app public URLs, never in local proxy SSL.
Configuring the Telegram companion adds its `--external-https-url` origin to
CORS automatically.

## Shell paste rule

A trailing space after `\` escapes the space, not the newline — the next
line then runs as its own command (`command not found: --project`). Prefer
the one-line forms above, and keep command-scoped flags AFTER the
subcommand (`kdcube config export --tenant …`, never
`kdcube --tenant … config export`).
