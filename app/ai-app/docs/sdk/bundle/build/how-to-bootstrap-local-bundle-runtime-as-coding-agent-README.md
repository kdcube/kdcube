---
id: ks:docs/sdk/bundle/build/how-to-bootstrap-local-bundle-runtime-as-coding-agent-README.md
title: "How To Bootstrap A Local Bundle Runtime As A Coding Agent"
summary: "Tier 1 coding-agent runbook for Claude Code, Codex, or a build-with-KDCube plugin to configure a local KDCube runtime, wire a bundle through the CLI, start ngrok when public callbacks are needed, set bundle props and secrets, register Telegram webhooks, prepare Gmail OAuth settings, and report only the external steps the agent cannot complete."
tags: ["sdk", "bundle", "tier-1", "agents", "local-runtime", "cli", "ngrok", "telegram", "gmail", "oauth"]
keywords: ["agent local bundle setup", "configure bundle with cli", "run kdcube local runtime", "telegram webhook setup", "gmail oauth local setup", "ngrok local kdcube", "bundles yaml staged descriptors", "bundles secrets yaml", "kdcube bundle command", "autonomous runtime smoke test"]
updated_at: 2026-05-21
see_also:
  - ks:docs/sdk/bundle/build/how-to-navigate-kdcube-docs-README.md
  - ks:docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-test-bundle-README.md
  - ks:docs/sdk/bundle/build/how-to-assemble-bundle-with-sdk-building-blocks-README.md
  - ks:docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - ks:docs/service/cicd/ngrok-README.md
  - ks:docs/sdk/integrations/telegram/telegram-README.md
  - ks:docs/sdk/integrations/telegram/telegram-external-prereq-README.md
  - ks:docs/sdk/integrations/email/email-README.md
  - ks:docs/sdk/integrations/email/email-external-prereq-README.md
  - ks:docs/sdk/tools/custom-tools-README.md
  - ks:docs/sdk/tools/tool-subsystem-README.md
  - ks:docs/sdk/bundle/build/design/bundle-loader-import-isolation-README.md
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

Critical Python import rule:

- when this setup exposes bundle import failures, check that bundle-local code
  uses package-relative imports such as `from .services.storage import ...`
- do not patch around import failures by adding top-level `services`, `apps`,
  `tools`, or `resources` imports for bundle-local modules
- for bundle-local tools, check `tools_descriptor.py`: local tools should be
  registered with `ref: "tools/name.py"` and imported with package-relative
  imports such as `from ..services.storage import ...`; `module` is for
  installed SDK/external modules
- see [bundle-runtime-README.md#critical-bundle-local-import-rule](../bundle-runtime-README.md#critical-bundle-local-import-rule)

## Agent Contract

The agent should first infer values from the shell, repository layout, existing
runtime, and descriptors. Ask the user only for values that cannot be inferred
or safely generated.

The agent may do these without asking again when the user has asked for local
setup:

- inspect local files and git status
- create a local virtual environment for the KDCube CLI if it is missing
- install the editable local KDCube CLI from the selected KDCube checkout
- run `kdcube info`, `init`, `start`, `stop`, `bundle`, and `reload`
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

If the user gives a base workdir such as `~/.kdcube/kdcube-runtime`, remember
that `kdcube init` may resolve the concrete runtime to:

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
- `kdcube reload <bundle_id>` applies staged bundle descriptor changes
- editing seed descriptors after init does nothing until init is run again
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

## Initialize Or Refresh The Runtime

Use this for descriptor-backed local proof with the selected KDCube checkout:

```bash
"$KDCUBE" init \
  --path "$KDCUBE_REPO" \
  --descriptors-location "$DESCRIPTORS" \
  --workdir "$WORKDIR" \
  --build
```

If public HTTPS callbacks are needed and the ngrok domain is already known,
stage CORS during init:

```bash
"$KDCUBE" init \
  --path "$KDCUBE_REPO" \
  --descriptors-location "$DESCRIPTORS" \
  --workdir "$WORKDIR" \
  --cors-origin "https://$NGROK_DOMAIN" \
  --build
```

Use `--set-secret` during init only for platform/global secrets you want to set
or replace in this runtime:

```bash
"$KDCUBE" init \
  --path "$KDCUBE_REPO" \
  --descriptors-location "$DESCRIPTORS" \
  --workdir "$WORKDIR" \
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

Start or restart:

```bash
"$KDCUBE" start --workdir "$WORKDIR"
"$KDCUBE" info --workdir "$WORKDIR"
```

Use `stop/start` when runtime descriptors changed. Use `init --build` again
after platform source changes that must be baked into local Docker images.

### Re-Running Init

Before re-running `kdcube init` against an existing workdir, decide whether the
staged runtime descriptors under `<WORKDIR>/config` should be preserved or
reseeded.

To keep local staged patches made by `kdcube bundle --set-config`,
`kdcube bundle --set-secret`, admin UI edits, or manual runtime descriptor
edits, do not point init back at the original seed descriptor directory. Use the
staged runtime config itself as the descriptor source:

```bash
"$KDCUBE" init \
  --path "$KDCUBE_REPO" \
  --descriptors-location "$WORKDIR/config" \
  --workdir "$WORKDIR" \
  --build
```

Use this form when the runtime needs regenerated env files, refreshed compose
files, rebuilt images, or an added CORS origin, but the current staged bundle
props/secrets should remain the authority.

To intentionally discard staged local patches and return to seed descriptors,
run init with the seed descriptor directory:

```bash
"$KDCUBE" init \
  --path "$KDCUBE_REPO" \
  --descriptors-location "$DESCRIPTORS" \
  --workdir "$WORKDIR" \
  --build
```

This restages descriptor files from `$DESCRIPTORS` into `<WORKDIR>/config`.
Any staged-only changes not copied back to the seed descriptors can be
overwritten. Use this only when reseeding is the intended outcome.

Agent rule:

- if the user says "keep my current runtime config", use
  `--descriptors-location "$WORKDIR/config"` or avoid init and use
  `kdcube bundle`, `kdcube reload`, `kdcube stop`, and `kdcube start`
- if the user says "start from descriptors", use the seed descriptor directory
- if unsure, inspect `git diff` or compare `<WORKDIR>/config` with the seed
  descriptors and ask before reseeding

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

"$KDCUBE" reload "$BUNDLE_ID" --workdir "$WORKDIR"
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

"$KDCUBE" reload "$BUNDLE_ID" --workdir "$WORKDIR"
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
  --set-config enabled.api.<api_alias>.<METHOD> true \
  --set-config <bundle_setting_path> <value>

"$KDCUBE" reload "$BUNDLE_ID" --workdir "$WORKDIR"
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

"$KDCUBE" reload "$BUNDLE_ID" --workdir "$WORKDIR"
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

"$KDCUBE" reload "$BUNDLE_ID" --workdir "$WORKDIR"
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
"$KDCUBE" info --workdir "$WORKDIR"
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
grep -n "$BUNDLE_ID" -A40 "$WORKDIR/config/bundles.yaml"
grep -n "$BUNDLE_ID" -A40 "$WORKDIR/config/bundles.secrets.yaml"
```

Use `kdcube info` and runtime logs to verify the runtime actually loaded the
staged descriptor.

### Bundle Reload

```bash
"$KDCUBE" reload "$BUNDLE_ID" --workdir "$WORKDIR"
```

Then check proc logs for bundle discovery/import/build errors:

```bash
docker logs --since 20m "$(docker ps --format '{{.Names}}' | grep chat-proc | head -1)"
```

Use focused `rg` filters only after confirming the logs exist.

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
- editing seed descriptors and expecting a running runtime to see them without
  `kdcube init`
- re-running `kdcube init --descriptors-location <seed>` when the intent was to
  preserve staged runtime config under `<WORKDIR>/config`
- patching staged descriptors and forgetting `kdcube reload`
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
