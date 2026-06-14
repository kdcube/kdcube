---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-bootstrap-local-bundle-runtime-as-coding-agent-README.md
title: "How To Bootstrap A Local Bundle Runtime As A Coding Agent"
summary: "Tier 1 coding-agent runbook for Claude Code, Codex, or a build-with-KDCube plugin to configure a local KDCube runtime, wire a bundle through the CLI, start ngrok when public callbacks are needed, set bundle props and secrets, register Telegram webhooks, prepare Gmail OAuth settings, validate bundle events, and report only the external steps the agent cannot complete."
tags: ["sdk", "bundle", "tier-1", "agents", "local-runtime", "cli", "ngrok", "telegram", "gmail", "oauth"]
keywords: ["agent local bundle setup", "configure bundle with cli", "run kdcube local runtime", "telegram webhook setup", "gmail oauth local setup", "ngrok local kdcube", "bundles yaml staged descriptors", "bundles secrets yaml", "bundle events", "event sources", "artifact rehosters", "kdcube bundle command", "autonomous runtime smoke test"]
updated_at: 2026-06-11
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/how-to-integrate-with-kdcube-apps-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-test-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-avoid-common-bundle-integration-failures-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-subsystem-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-client-communication-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-transports-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-properties-and-secrets-lifecycle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/ngrok-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/telegram-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/telegram/telegram-external-prereq-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/email/email-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/integrations/email/email-external-prereq-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/custom-tools-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/tool-subsystem-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/event-subsystem-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/event-source/event-source-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/design/bundle-loader-import-isolation-README.md
---
# How To Bootstrap A Local Bundle Runtime As A Coding Agent

Use this page when you are Claude Code, Codex, or a build-with-KDCube plugin
agent and you are asked to do the repeated local setup work:

- find or prepare the KDCube platform checkout
- find descriptor authority
- initialize or refresh the local runtime
- configure one bundle by local path or git ref
- set bundle props and secrets through the CLI
- start or verify the local KDCube stack
- start or verify ngrok when public callbacks are needed
- configure Telegram webhook or Mini App values when requested
- configure Gmail OAuth values when requested
- run realistic checks and report only the missing external prerequisites

This page is intentionally written as instructions for the coding agent that is
performing the setup. It is not a checklist for the user to execute by hand.
The agent should do the routine work itself and ask the user only for values or
external provider actions it cannot infer or perform.

It does not replace:

- [how-to-configure-and-run-bundle-README.md](how-to-configure-and-run-bundle-README.md)
  for the full runtime model
- [how-to-test-bundle-README.md](how-to-test-bundle-README.md) for QA depth
- [telegram-external-prereq-README.md](../../integrations/telegram/telegram-external-prereq-README.md)
  for BotFather and Telegram provider setup
- [email-external-prereq-README.md](../../integrations/email/email-external-prereq-README.md)
  for Google Cloud and Gmail OAuth provider setup
- [bundle-subsystem-integration-README.md](../bundle-subsystem-integration-README.md)
  for the checklist used when a local bundle mounts existing SDK subsystems
  such as memory or canvas

Before running or changing CLI commands, use the canonical schemas in
[how-to-configure-and-run-bundle-README.md#canonical-cli-flow-schemas](how-to-configure-and-run-bundle-README.md#canonical-cli-flow-schemas).
They define the distinction between `init`, `refresh`, `bundle config apply`,
`bundle reload`, and `export`. This page is the agent runbook that applies
those flows.

If the local runtime is being prepared for an external website, scene, host
server, or app-specific client, first identify the integration mode in
[How To Integrate With KDCube Apps](../../../how-to-integrate-with-kdcube-apps-README.md).
That decision controls whether the agent must validate iframe headers,
browser CORS/auth, chat streams, Data Bus, direct operations, or backend-only
surface calls.

Common failure smoke checks:

- when setup exposes bundle import, widget, live-event, Data Bus, authored
  event, or resolver failures, route the agent to
  [how-to-avoid-common-bundle-integration-failures-README.md](how-to-avoid-common-bundle-integration-failures-README.md)
  instead of patching around symptoms
- when Data Bus publish calls are rejected before the handler runs, inspect
  `gateway.data_bus.ingress.publish_limits` in the active `gateway.yaml`;
  these are platform ingress limits, not bundle props
- if a bundle mounts memory, canvas, tasks, Telegram, delivery, or another
  reusable SDK subsystem, use
  [bundle-subsystem-integration-README.md](../bundle-subsystem-integration-README.md)
  as the smoke checklist

## Agent Contract

The agent should first infer values from the shell, repository layout, existing
runtime, and descriptors. Ask the user only for values that cannot be inferred
or safely generated.

The agent may do these without asking again when the user has asked for local
setup:

- inspect local files and git status
- create a local virtual environment for the KDCube CLI if it is missing
- install the editable local KDCube CLI from the selected KDCube checkout
- run `kdcube info`, `init`, `start`, `stop`, `bundle`, and
  `bundle reload`
- patch staged runtime bundle descriptors with `kdcube bundle`
- generate local random secrets for webhook or OAuth state when the user has
  not supplied a value
- start ngrok if a stable ngrok domain is known and ngrok is installed
- call Telegram Bot API `setWebhook` when bot token, webhook URL, and webhook
  secret are available

The agent must ask or report a blocker for:

- missing KDCube checkout when it is not allowed to clone or update one
- destructive cleanup, such as deleting runtime data or Docker volumes
- pulling from network when the user has not allowed it
- creating a Telegram bot in BotFather
- configuring BotFather Mini App or menu-button UX if Bot API settings are not
  enough for the desired product flow
- creating or modifying a Google Cloud project
- enabling Gmail API in Google Cloud
- creating a Google OAuth client
- adding OAuth test users or publishing OAuth consent
- adding authorized redirect URIs in Google Cloud
- providing private provider credentials that are not already available in env,
  files, or the active secret store

## Minimum Questions

Ask these only if they are not already known.

```text
1. Where is the KDCube repo, or should I fetch/use a default checkout?
2. Where is the bundle repo/path and what is the bundle id?
3. Which tenant/project/workdir should I use?
4. Do you have seed descriptors, or should I use the local oss-cli descriptors?
5. Do you need public HTTPS callbacks through ngrok?
6. If ngrok is needed, what is the stable ngrok domain?
7. Which integrations should I configure now: Telegram webhook, Telegram Mini App,
   Gmail OAuth/send, or none?
8. If provider secrets are missing, should I generate local signing secrets and
   ask only for provider-issued credentials?
```

Do not ask the user to choose between internal implementation details. Infer
them and show the final plan before changing runtime state.

## Discovery Order

Use this order to avoid unnecessary questions.

Before asking the user where the runtime is, try the machine-readable runtime
summary when a workdir is known or can be guessed:

```bash
"$KDCUBE" info --json --workdir "$WORKDIR" | python3 -m json.tool
"$KDCUBE" bundle status "$BUNDLE_ID" --json --workdir "$WORKDIR" | python3 -m json.tool
```

Use `kdcube info --json` to discover the concrete staged workdir, descriptor
directory, local proxy ports, and local-path bundle mount mapping such as
`host_bundles_path` and `container_bundles_root`. If the proc container sees
bundles under `/bundles`, do not hand-write that container path into source or
seed descriptors; configure the host path through the CLI and let the runtime
stage the container-visible path.

### 1. KDCube Repo

Prefer:

1. current working directory if it is inside `kdcube-ai-app`
2. `KDCUBE_REPO`
3. `$HOME/src/kdcube/kdcube-ai-app` when that local convention exists
4. `<workdir>/repo` when an initialized runtime already has a staged source copy
5. ask whether to clone or use a different path

The expected platform root is the repository that contains:

```text
app/ai-app/docs/sdk/bundle/build
app/ai-app/src/kdcube-ai-app
app/ai-app/deployment/cicd/kdcube/descriptors
```

### 2. Bundle Source

Prefer:

1. an explicit bundle path from the user
2. current working directory if it contains `entrypoint.py` and `release.yaml`
3. a path in a known applications repository
4. ask for the bundle path

The bundle id usually comes from one of:

- `release.yaml`
- `config/bundles.template.yaml`
- `entrypoint.py`
- the final directory name, when it is already the bundle id

Do not guess aliases from folder names when the bundle has interface docs or
decorators. Read `entrypoint.py`, `interface/README.md`, OpenAPI files, and
bundle docs.

### 3. Tenant, Project, Workdir

Prefer:

1. `TENANT`, `PROJECT`, and `WORKDIR`
2. descriptor values from the selected descriptor set
3. existing runtime directory under `~/.kdcube/kdcube-runtime`
4. defaults:

```bash
export TENANT="demo-tenant"
export PROJECT="demo-project"
export WORKDIR="$HOME/.kdcube/kdcube-runtime/${TENANT}__${PROJECT}"
```

For every subcommand other than `kdcube init`'s `--workdir-base` form,
`--workdir` is the **fully-qualified namespaced runtime path**
(`<base>/<tenant>__<project>`). If the user gives a base workdir such as
`~/.kdcube/kdcube-runtime` with no tenant/project, derive the namespaced
runtime explicitly:

```text
<base_workdir>/<tenant>__<project>
```

Always verify with:

```bash
"$KDCUBE" info --workdir "$WORKDIR"
```

### 4. Descriptor Authority

Prefer this order:

1. explicit user-provided seed descriptors
2. `KDCUBE_DESCRIPTORS`
3. local OSS CLI descriptors:

```text
<KDCUBE_REPO>/app/ai-app/deployment/cicd/kdcube/descriptors/local/oss-cli
```

4. existing staged descriptors under:

```text
<WORKDIR>/config
```

Use source/seed descriptors for repeatable initial install. Use staged runtime
descriptors for targeted local changes after the runtime exists.

Important:

- `kdcube init --descriptors-location ...` stages seed descriptors into
  `<WORKDIR>/config`
- `kdcube bundle ... --set-config/--set-secret` patches staged runtime
  descriptors
- `kdcube bundle reload <bundle_id>` applies staged bundle descriptor changes
- editing seed descriptors after init does nothing by itself; the user can
  intentionally reapply bundle descriptors with `kdcube bundle config apply`,
  but agents should not do this as routine bootstrap
- if staged descriptor changes should become reusable, export or copy them back
  deliberately; do not assume it happened

## Preflight Commands

Set variables first:

```bash
export KDCUBE_REPO="/abs/path/to/kdcube-ai-app"
export CLI_VENV="$KDCUBE_REPO/app/venvs/ai-app/kdcube-cli"
export KDCUBE="$CLI_VENV/bin/kdcube"
export DESCRIPTORS="$KDCUBE_REPO/app/ai-app/deployment/cicd/kdcube/descriptors/local/oss-cli"
export TENANT="demo-tenant"
export PROJECT="demo-project"
export WORKDIR="$HOME/.kdcube/kdcube-runtime/${TENANT}__${PROJECT}"
export KDCUBE_LOCAL_PORT="5173"
export BUNDLE_ID="my.bundle@1-0"
export BUNDLE_REPO="/abs/path/to/repo-that-contains-the-bundle"
export BUNDLE_PATH="/abs/path/to/my.bundle@1-0"
```

Install or refresh the CLI only when it is missing or the selected KDCube
checkout changed:

```bash
cd "$KDCUBE_REPO"
mkdir -p app/venvs/ai-app
python3 -m venv "$CLI_VENV"
source "$CLI_VENV/bin/activate"
python -m pip install --upgrade pip setuptools wheel
pip install -e "$KDCUBE_REPO/app/ai-app/src/kdcube-ai-app/kdcube_cli"
"$KDCUBE" --help
deactivate
```

Check whether a runtime already exists:

```bash
test -f "$WORKDIR/config/assembly.yaml"
test -f "$WORKDIR/config/bundles.yaml"
test -f "$WORKDIR/config/bundles.secrets.yaml"
"$KDCUBE" info --workdir "$WORKDIR"
```

Missing files mean the runtime needs `init`.

## Initialize The Runtime (first-time)

`kdcube init` is **first-time setup only**. It creates a fresh namespaced
runtime workdir and refuses if the target already has `install-meta.json`.
For re-init (rebuild images, refresh env files after platform changes), use
[`kdcube refresh`](#refresh) — see the next section.

Primary form — pass `--tenant`/`--project`; the CLI creates the runtime
under the platform default base (`~/.kdcube/kdcube-runtime/<tenant>__<project>/`):

```bash
"$KDCUBE" init \
  --path "$KDCUBE_REPO" \
  --descriptors-location "$DESCRIPTORS" \
  --tenant "$TENANT" --project "$PROJECT" \
  --build
```

If public HTTPS callbacks are needed and the ngrok domain is already known,
stage CORS during init:

```bash
"$KDCUBE" init \
  --path "$KDCUBE_REPO" \
  --descriptors-location "$DESCRIPTORS" \
  --tenant "$TENANT" --project "$PROJECT" \
  --cors-origin "https://$NGROK_DOMAIN" \
  --build
```

Use `--set-secret` during init only for platform/global secrets you want to set
or replace in this runtime:

```bash
"$KDCUBE" init \
  --path "$KDCUBE_REPO" \
  --descriptors-location "$DESCRIPTORS" \
  --tenant "$TENANT" --project "$PROJECT" \
  --cors-origin "https://$NGROK_DOMAIN" \
  --build \
  --set-secret services.openai.api_key "$OPENAI_API_KEY" \
  --set-secret services.anthropic.api_key "$ANTHROPIC_API_KEY" \
  --set-secret services.brave.api_key "$BRAVE_API_KEY" \
  --set-secret services.git.http_token "$GIT_HTTP_TOKEN" \
  --set-secret git.http_token "$GIT_HTTP_TOKEN"
```

Do not invent placeholder secrets. If provider keys are missing, report the
missing key and continue with the parts of the setup that do not require it.

Start, then verify:

```bash
"$KDCUBE" start --tenant "$TENANT" --project "$PROJECT"
"$KDCUBE" info  --workdir "$WORKDIR"
```

## Refresh An Already-Initialized Runtime<a id="refresh"></a>

Use `kdcube refresh` whenever the **descriptors should be preserved** but the
platform side needs to pick up changes (rebuild Docker images after editing
platform source, regenerate env files, restart):

```bash
"$KDCUBE" refresh --tenant "$TENANT" --project "$PROJECT" --build
```

Use the same source selectors as `init` when the already-initialized runtime
should move to another platform ref while preserving staged descriptors:

```bash
"$KDCUBE" refresh --tenant "$TENANT" --project "$PROJECT" --latest --build
"$KDCUBE" refresh --tenant "$TENANT" --project "$PROJECT" --upstream --build
"$KDCUBE" refresh --tenant "$TENANT" --project "$PROJECT" --release <ref> --build
```

Explicit `--path "$KDCUBE_REPO"` without one of those selectors means "restage
this dirty local platform source into `<WORKDIR>/repo` before rebuilding".
With a selector, refresh checks out that selected ref and uses the staged
runtime repo copy so all compose build contexts agree.

If a deployment is currently recorded as running (`~/.kdcube/cli-lock.json`),
`kdcube refresh --build` with no flags targets it automatically.

`refresh` never modifies `assembly.yaml`, `secrets.yaml`, `bundles.yaml`,
`bundles.secrets.yaml`, or `gateway.yaml`. To change one value, use
`kdcube bundle --set-config / --set-secret` and then call
`kdcube bundle reload <bundle_id>` — no platform refresh is required for bundle
descriptor changes.

### User Flow: Apply Seed Bundle Descriptors

`kdcube bundle config apply` is a user/operator flow, not an autonomous agent
bootstrap step. Use it only when the user has intentionally edited a seed
descriptor directory and wants to reapply only `bundles.yaml` /
`bundles.secrets.yaml` to an existing runtime.

Agent boundary:

- the agent may explain that this capability exists
- the agent may prepare or run the `--dry-run` command for inspection
- the agent may run the write/reload form only when explicitly granted by the
  user to apply the selected seed descriptors on the user's behalf

```bash
"$KDCUBE" bundle config apply \
  --tenant "$TENANT" \
  --project "$PROJECT" \
  --descriptors-location "$DESCRIPTORS" \
  --dry-run

"$KDCUBE" bundle config apply \
  --tenant "$TENANT" \
  --project "$PROJECT" \
  --descriptors-location "$DESCRIPTORS" \
  --reload
```

`bundle config apply` stages only `bundles.yaml` and, when present in the seed
directory, `bundles.secrets.yaml`. It preserves platform descriptors and does
not rebuild images or restart Docker. Host local bundle paths in the seed
descriptor are translated to runtime-visible `/bundles/...` paths before the
runtime copy is written.

Agent rule:

- if the user wants to **rebuild platform images** or **restart** after editing
  platform source: `kdcube refresh --tenant T --project P --build`.
- if the user wants refresh to **copy a specific local platform checkout first**:
  add `--path /path/to/kdcube-ai-app` to the refresh command.
- if the user wants to **move an existing runtime to a different platform
  source** while preserving descriptors: add exactly one of `--latest`,
  `--upstream`, or `--release <ref>` to `kdcube refresh`.
- if the user explicitly wants to **reapply bundle descriptors** from a seed
  directory: offer `kdcube bundle config apply --tenant T --project P
  --descriptors-location <seed> --dry-run`, then apply with `--reload` only
  after the user accepts that descriptor source as authority.
- if the user wants to **reseed platform/runtime descriptors** such as
  `assembly.yaml`, `gateway.yaml`, or platform `secrets.yaml`: create a new
  runtime or intentionally replace the runtime descriptor files; do not hide
  that behind bundle reload.
- if the user wants to **change a single bundle's config / secrets**:
  `kdcube bundle <id> --tenant T --project P --set-config k v --set-secret k v`,
  then `kdcube bundle reload <id> --tenant T --project P`.
- never re-run `kdcube init` on an existing initialized workdir — it refuses
  with a clear error pointing at the right command.

## Configure The Bundle

Prefer `kdcube bundle` over hand-editing YAML.

### Local Path Bundle

Use this when developing a bundle from a local checkout:

```bash
"$KDCUBE" bundle "$BUNDLE_ID" \
  --workdir "$WORKDIR" \
  --local-path "$BUNDLE_PATH" \
  --module entrypoint \
  --no-singleton

"$KDCUBE" bundle reload "$BUNDLE_ID" --workdir "$WORKDIR"
```

During init, local host paths are mounted into proc under `/bundles`. The CLI
normalizes staged runtime descriptors to the runtime-visible path.

### Git-Backed Bundle

Use this when validating a released content ref:

```bash
"$KDCUBE" bundle "$BUNDLE_ID" \
  --workdir "$WORKDIR" \
  --git-repo "$BUNDLE_GIT_REPO" \
  --git-ref "$BUNDLE_GIT_REF" \
  --git-subdir "$BUNDLE_GIT_SUBDIR" \
  --module entrypoint \
  --no-singleton

"$KDCUBE" bundle reload "$BUNDLE_ID" --workdir "$WORKDIR"
```

If the bundle has `config/bundles.template.yaml`, read it and translate the
required deployment props into `--set-config` commands. If the bundle has
`config/bundles.secrets.template.yaml`, translate deployment secrets into
`--set-secret` commands.

Example:

```bash
"$KDCUBE" bundle "$BUNDLE_ID" \
  --workdir "$WORKDIR" \
  --set-config enabled.widget.<widget_alias> true \
  --set-config enabled.api.<route>.<api_alias>.<METHOD> true \
  --set-config <bundle_setting_path> <value>

"$KDCUBE" bundle reload "$BUNDLE_ID" --workdir "$WORKDIR"
```

The exact keys are bundle-specific. Read the bundle's templates, interface
docs, and decorators before patching. Replace the angle-bracket placeholders
with real descriptor keys before running the command.

## Configure Ngrok When Public HTTPS Is Needed

Ngrok is needed when an external provider must reach a local runtime, for
example:

- Telegram webhook
- Telegram Mini App public read/API calls
- Gmail OAuth callback
- Cognito callback

Do not start ngrok if no provider callback or public remote access is needed.

Find or ask for a stable domain:

1. `NGROK_DOMAIN`
2. existing ngrok API:

```bash
curl -s http://127.0.0.1:4040/api/tunnels
```

3. active descriptors containing `ngrok`
4. ask the user

Verify ngrok is installed:

```bash
command -v ngrok
```

If it is missing, tell the user to install ngrok and stop provider callback
validation until it is available.

Find the local KDCube proxy/UI port from `kdcube start` output or staged
`assembly.yaml`. Common local descriptor sets use `5173`.

Start ngrok in a separate long-lived terminal/session:

```bash
ngrok http "$KDCUBE_LOCAL_PORT" \
  --url "https://$NGROK_DOMAIN" \
  --host-header=rewrite
```

Verify:

```bash
curl -I "https://$NGROK_DOMAIN/platform/chat"
curl -s http://127.0.0.1:4040/api/tunnels
```

For CLI-started runtime, do not add Caddy. The KDCube local web proxy already
routes frontend, ingress, and proc under one local port.

If ngrok is started after init, add the public origin to the runtime by
rerunning init with `--cors-origin` or by updating the staged descriptor through
the supported flow for that deployment.

## Configure Telegram

Read the bundle docs first and determine which Telegram surfaces exist.

Common surfaces:

```text
telegram_webhook     public POST webhook from Telegram Bot API
telegram_*           public Mini App operations that verify initData in handler
<bundle>_data        public read operation for a Mini App style widget
```

External prerequisites the agent cannot create alone:

- BotFather bot creation
- bot username/display name choice
- bot token if it is not already available
- BotFather command list, Mini App, or menu-button UX when the product needs it

The agent can generate a local webhook secret:

```bash
openssl rand -base64 32 | tr '+/' '-_' | tr -d '='
```

Configure bundle props and secrets:

```bash
export PUBLIC_BASE_URL="https://$NGROK_DOMAIN"
export TELEGRAM_WEBHOOK_URL="$PUBLIC_BASE_URL/api/integrations/bundles/$TENANT/$PROJECT/$BUNDLE_ID/public/telegram_webhook"

"$KDCUBE" bundle "$BUNDLE_ID" \
  --workdir "$WORKDIR" \
  --set-config integrations.telegram.enabled true \
  --set-config integrations.telegram.webhook_url "$TELEGRAM_WEBHOOK_URL" \
  --set-config integrations.telegram.web_app_auth_max_age_seconds 86400 \
  --set-secret integrations.telegram.bot_token "$TELEGRAM_BOT_TOKEN" \
  --set-secret integrations.telegram.webhook_secret "$TELEGRAM_WEBHOOK_SECRET"

"$KDCUBE" bundle reload "$BUNDLE_ID" --workdir "$WORKDIR"
```

If the bundle only has a Telegram Mini App public-read surface and no bot
webhook, set only the config/secrets it documents. Do not invent a webhook alias
that the bundle does not expose.

Register the webhook only when:

- the bundle exposes the public webhook alias
- KDCube is running
- ngrok is reachable
- bot token and webhook secret are available

```bash
curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -d "url=$TELEGRAM_WEBHOOK_URL" \
  -d "secret_token=$TELEGRAM_WEBHOOK_SECRET"
```

Verify:

```bash
curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo"
```

Report as external follow-up when BotFather Mini App or menu settings are
needed. Include the exact public widget URL the user should paste.

## Configure Gmail OAuth And Email Sending

Read the bundle docs first and determine which email surfaces exist.

Common surfaces:

```text
email_oauth_start       operations or public route that starts OAuth
email_oauth_callback    public callback route
email_account_status    operations or Mini App account status route
delivery sink           job/report delivery through a connected account
```

External prerequisites the agent cannot create alone:

- Google Cloud project
- Gmail API enablement
- OAuth consent screen and test users
- Web application OAuth client
- authorized redirect URI
- provider-issued client id and client secret if not already available

Generate local OAuth state secret if missing:

```bash
openssl rand -base64 32 | tr '+/' '-_' | tr -d '='
```

Set deployment config and secrets:

```bash
export PUBLIC_BASE_URL="https://$NGROK_DOMAIN"
export EMAIL_REDIRECT_URI="$PUBLIC_BASE_URL/api/integrations/bundles/$TENANT/$PROJECT/$BUNDLE_ID/public/email_oauth_callback"

"$KDCUBE" bundle "$BUNDLE_ID" \
  --workdir "$WORKDIR" \
  --set-config integrations.email.enabled true \
  --set-config integrations.email.google.client_id "$GOOGLE_CLIENT_ID" \
  --set-config integrations.email.google.scopes "openid email profile https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send" \
  --set-config integrations.email.oauth.public_base_url "$PUBLIC_BASE_URL" \
  --set-config integrations.email.oauth.redirect_uri "$EMAIL_REDIRECT_URI" \
  --set-secret integrations.email.google.client_secret "$GOOGLE_CLIENT_SECRET" \
  --set-secret integrations.email.oauth_state_secret "$EMAIL_OAUTH_STATE_SECRET"

"$KDCUBE" bundle reload "$BUNDLE_ID" --workdir "$WORKDIR"
```

The redirect URI must also be added in Google Cloud exactly as:

```text
https://<PUBLIC_HOST>/api/integrations/bundles/<TENANT>/<PROJECT>/<BUNDLE_ID>/public/email_oauth_callback
```

Deployment config only prepares the OAuth client. Each user still connects a
Gmail account through the bundle's Settings UI, Telegram Mini App, or another
route that calls the Email SDK settings operations. User refresh tokens are
user-scoped secrets; they do not belong in `bundles.secrets.yaml`.

If the product needs sending email, include:

```text
https://www.googleapis.com/auth/gmail.send
```

in the configured scopes and in the Google OAuth consent configuration.

## Verification Checklist

Do not claim live validation unless each required layer was checked.

### Runtime

```bash
"$KDCUBE" info --json --workdir "$WORKDIR" | python3 -m json.tool
docker ps
curl -I "http://localhost:${KDCUBE_LOCAL_PORT}/platform/chat"
```

For public callback flows:

```bash
curl -I "https://$NGROK_DOMAIN/platform/chat"
curl -s http://127.0.0.1:4040/api/tunnels
```

### Bundle Descriptor

```bash
"$KDCUBE" bundle status "$BUNDLE_ID" --json --workdir "$WORKDIR" | python3 -m json.tool
grep -n "$BUNDLE_ID" -A40 "$WORKDIR/config/bundles.yaml"
grep -n "$BUNDLE_ID" -A40 "$WORKDIR/config/bundles.secrets.yaml"
```

Use `kdcube info` and runtime logs to verify the runtime actually loaded the
staged descriptor.

### Bundle Reload

```bash
"$KDCUBE" bundle reload "$BUNDLE_ID" --workdir "$WORKDIR"
```

Then check proc logs for bundle discovery/import/build errors:

```bash
docker logs --since 20m "$(docker ps --format '{{.Names}}' | grep chat-proc | head -1)"
```

Use focused `rg` filters only after confirming the logs exist.

### Bundle Smoke Probes

After reload, run the route-level smoke probes from
[how-to-test-bundle-README.md#e-bundle-smoke-probes](how-to-test-bundle-README.md#e-bundle-smoke-probes).
Do not stop at `npm run build`, `docker ps`, or a successful CLI reload if the
user asked for live widget/API validation.

### Backend Tests

Use the project venv from the active KDCube checkout:

```bash
cd "$KDCUBE_REPO"
PY="$KDCUBE_REPO/app/venvs/ai-app/chat-processor/bin/python"
PYTHONPATH="$KDCUBE_REPO/app/ai-app/src/kdcube-ai-app" \
  "$PY" -m pytest -q \
  "$KDCUBE_REPO/app/ai-app/src/kdcube-ai-app/kdcube_ai_app/apps/chat/tests/test_bundle_interfaces.py"
```

For a bundle-local pytest suite:

```bash
cd "$BUNDLE_REPO"
PYTHONPATH="$KDCUBE_REPO/app/ai-app/src/kdcube-ai-app" \
  "$PY" -m pytest -q "$BUNDLE_PATH/tests"
```

For the shared bundle suite:

```bash
PYTHONPATH="$KDCUBE_REPO/app/ai-app/src/kdcube-ai-app" \
  "$PY" -m kdcube_ai_app.apps.chat.sdk.tests.bundle.run_bundle_suite \
  --bundle-path "$BUNDLE_PATH"
```

### Telegram

If a webhook exists:

```bash
curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo"
```

Expected:

- `url` matches the active ngrok URL and bundle id
- no stale old domain
- no provider-side error for unreachable endpoint

For Mini App/public read APIs, verify invalid or missing `initData` is rejected
or treated as unauthenticated according to the bundle contract, and valid
Telegram `initData` succeeds when launched from Telegram.

### Gmail

Verify:

- Google Cloud authorized redirect URI equals the active `EMAIL_REDIRECT_URI`
- Gmail API is enabled in the selected Google project
- configured scopes include `gmail.send` when delivery sends email
- OAuth start route produces a Google authorize URL
- OAuth callback stores user-scoped account tokens
- send path uses the connected user account or configured sender policy

Do not claim Gmail send validation until a user account has completed OAuth and
the send operation was exercised.

## Final Agent Report

The final report should be concrete:

```text
Configured:
- KDCube repo: <path/ref>
- runtime: <workdir>
- descriptors: <seed path or staged runtime config>
- bundle: <id> from <local path or git ref>
- public origin: <ngrok URL or none>
- Telegram: configured/not configured, webhook registered yes/no
- Gmail: configured/not configured, OAuth external steps remaining

Verified:
- kdcube info: ok/fail
- containers: ok/fail
- bundle reload: ok/fail
- bundle tests: ok/fail
- ngrok: ok/not needed/fail
- provider callback: ok/not attempted/fail

Needs user action:
- <only external or missing items>
```

Do not say "open the widget and test" unless a running KDCube instance is
confirmed, the bundle is loaded, and the widget URL/surface is known. If those
are missing, report the exact setup remaining before browser validation can be
meaningful.

## Common Mistakes

- asking the user for paths that are already inferable from cwd, env, or
  descriptors
- editing seed descriptors and expecting a running runtime to see them
  automatically — staged descriptors under `<WORKDIR>/config/` are
  authoritative once init has run; bundle seed descriptors can be reapplied
  only through the explicit user/operator `kdcube bundle config apply` flow
- re-running `kdcube init` on an existing workdir to "refresh" — it now
  refuses; use `kdcube refresh --tenant T --project P --build` instead, with
  `--latest`, `--upstream`, or `--release <ref>` when changing platform source
- patching staged descriptors and forgetting `kdcube bundle reload`
- hardcoding `/Users/...` host paths into Docker-runtime descriptors by hand
- registering a Telegram webhook before ngrok and the bundle public route are
  reachable
- using a new random ngrok hostname when Telegram, OAuth, or CORS descriptors
  expect a stable domain
- storing user OAuth refresh tokens in deployment bundle secrets
- claiming Gmail send works before a user completed OAuth with `gmail.send`
- claiming Telegram Mini App auth works without validating real Telegram
  `initData`
- treating local tests as a substitute for runtime bundle reload
