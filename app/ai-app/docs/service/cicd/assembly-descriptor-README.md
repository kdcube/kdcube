---
id: ks:docs/service/cicd/assembly-descriptor-README.md
title: "Assembly Descriptor"
summary: "Assembly descriptor schema and CLI usage: platform/frontend/auth/infra/proxy/paths and deployment wiring."
tags: ["service", "cicd", "assembly", "descriptor", "schema", "git"]
keywords: ["assembly.yaml", "release.yaml", "platform.ref", "frontend.build", "repo", "ref", "subdir", "module", "env:", "file:"]
see_also:
  - ks:docs/service/cicd/release-bundle-README.md
  - ks:docs/service/cicd/custom-cicd-README.md
  - ks:docs/service/cicd/release-README.md
  - ks:docs/sdk/bundle/bundle-ops-README.md
  - ks:docs/service/cicd/secrets-descriptor-README.md
  - ks:docs/service/configuration/bundle-configuration-README.md
  - ks:docs/service/configuration/descriptor-plain-config-README.md
---
# Assembly Descriptor (assembly.yaml)

**Where it lives:**
- Assembly file (recommended name: `assembly.yaml`)
- Default template: [`app/ai-app/deployment/assembly.yaml`](../../../deployment/assembly.yaml) (copied into the workdir as `config/assembly.yaml`)

**Filename:** the CLI accepts any path; `assembly.yaml` is the recommended name.
If you do not provide a path, the wizard uses `config/assembly.yaml` in the workdir
and seeds it from `deployment/assembly.yaml`. If you provide a path, the CLI copies
it into `config/assembly.yaml` and uses that file as the source of truth.
Older setups may still use `release.yaml`; rename it if needed.

The CLI uses this file to render `.env*` and compose settings.
Runtime services may also read the mounted descriptor directly via
`read_plain(...)` / `get_plain(...)` when the deployment mounts
`/config/assembly.yaml`.
Bundle configuration is handled via **`bundles.yaml`** (and secrets via
`bundles.secrets.yaml`). See:
[docs/service/configuration/bundle-configuration-README.md](../configuration/bundle-configuration-README.md)

For the runtime code contract, see:
[docs/service/configuration/descriptor-plain-config-README.md](../configuration/descriptor-plain-config-README.md)

The **CLI** can use the `frontend` section to build and run a
custom UI in the **custom‑ui‑managed‑infra** compose mode.
During setup, the wizard lets you choose whether the descriptor applies to
**frontend** and/or **platform**.

Sensitive values (LLM keys, tokens, passwords) should live in `secrets.yaml`
instead of `assembly.yaml`. See: [docs/service/cicd/secrets-descriptor-README.md](secrets-descriptor-README.md)

Non-secret secrets backend selection belongs in `assembly.yaml`:
- `secrets.provider` chooses the runtime provider
- secret values still live in `secrets.yaml` / `bundles.secrets.yaml`
- gateway config must not carry secrets-provider settings

Bundles are not defined in assembly.yaml. Use `bundles.yaml` for bundle items
and `bundles.secrets.yaml` for bundle secrets.

Descriptor mount contract:

- `/config/assembly.yaml` is for plain runtime reads from `assembly.yaml`
- `/config/bundles.yaml` is for bundle registry input and plain runtime reads from `bundles.yaml`
- `AGENTIC_BUNDLES_JSON` still separately selects which descriptor proc uses as the registry seed

---

## 1) Descriptor schema (recommended)

```yaml
release_name: "prod-2026-02-22"

platform:
  repo: "git@github.com:kdcube/kdcube-ai-app.git"
  ref: "v0.3.2"          # tag or commit

frontend:
  build:
    repo: "git@github.com:org/private-ui.git"
    ref: "ui-v2026.02.22"  # tag or commit
    dockerfile: "ops/docker/Dockerfile_UI"
    src: "ui/chat-web-app"
  image: "registry/private-ui:2026.02.22"         # optional; if set, CLI uses this image
  frontend_config: "ops/docker/config.cognito.json"
  nginx_ui_config: "ops/docker/nginx_ui.conf"     # optional
  ui_env_build_relative: "ui/chat-web-app/.env.ui.build"  # optional
  domain: "chat.example.com"                      # optional

domain: "chat.example.com"                        # required when proxy.ssl=true; used for proxylogin URLs and nginx SSL server_name/cert paths
company: "Example Inc."                           # optional; used for delegated frontend defaults and proxylogin/password-reset metadata
auth:
  type: "delegated"  # "simple" | "cognito" | "delegated"
  cognito:
    region: "eu-west-1"
    user_pool_id: "eu-west-1_AbCdEf123"
    app_client_id: "4m7u93umj6net8kng4cbe2gukn"
    service_client_id: "service-client-id"        # optional
  proxy_login:
    redis_key_prefix: "proxylogin:<TENANT>:<PROJECT>:"
    token_masquerade: false
    password_reset:
      company: "YourCompany"
      sender: "noreply@yourcompany.com"
      template_name: "YourCompanyPasswordResetTemplate"
      redirect_url: "http://localhost:5174/chatbot/reset-password?user=%[1]s"
    http_urlbase: "http://localhost:5174/auth"

proxy:
  ssl: false                # when true, CLI picks SSL nginx templates and applies `domain` to nginx server_name/cert paths
  route_prefix: "/chatbot"  # overrides frontend routesPrefix + nginx routing prefix

ports:
  ingress: "8010"
  proc: "8020"
  metrics: "8090"
  ui: "80"
  ui_ssl: "443"

context:
  tenant: "demo-tenant"
  project: "demo-project"

secrets:
  provider: "secrets-service"   # "secrets-service" | "aws-sm" | "in-memory"

infra:
  postgres:
    user: "postgres"
    password: "postgres"
    database: "kdcube"
    host: "postgres-db"
    port: "5432"
    ssl: false
  redis:
    password: "redispass"
    host: "redis"
    port: "6379"

aws:
  region: "eu-west-1"
  profile: "dev"
  ec2: false

storage:
  workspace:
    type: "custom"         # custom | git
    repo: ""               # required when type=git
  claude_code_session:
    type: "local"          # local | git
    repo: ""               # required when type=git
  kdcube: "s3://../data/kdcube"
  bundles: "s3://../data/kdcube"

paths:
  host_kdcube_storage_path: "/srv/kdcube/data/kdcube-storage"
  host_bundles_path: "/srv/kdcube/data/bundles"
  host_bundle_storage_path: "/srv/kdcube/data/bundle-storage"
  host_exec_workspace_path: "/srv/kdcube/data/exec-workspace"

notifications:
  email:
    enabled: true
    host: ""
    port: "587"
    user: ""
    from: ""
    to: "ops@example.com"
    use_tls: true

routines:
  economics:
    subscription_rollover_enabled: true
    subscription_rollover_cron: "15 * * * *"
    subscription_rollover_lock_ttl_seconds: 900
    subscription_rollover_sweep_limit: 500
  stripe:
    reconcile_enabled: true
    reconcile_cron: "45 * * * *"
    reconcile_lock_ttl_seconds: 900
  opex:
    agg_cron: "0 3 * * *"

```

`paths.*` is installer-facing host wiring, not runtime application config.
In particular:

- `paths.host_bundles_path` becomes `HOST_BUNDLES_PATH` in compose env
- proc mounts that host folder as `AGENTIC_BUNDLES_ROOT` (normally `/bundles`)
- local bundles placed there and git-resolved bundles cloned by proc share the same root
- bundle entries in `bundles.yaml` must still point to the container-visible path such as `/bundles/my.bundle`

Bundle definitions moved to `bundles.yaml`.
See: [docs/service/configuration/bundle-configuration-README.md](../configuration/bundle-configuration-README.md)

### Frontend section (CLI usage)
The `frontend` section is used by `kdcube` to build a customer UI when
you run **custom‑ui‑managed‑infra** compose. It is ignored by runtime services.

Required (build mode):
- `build.repo`: frontend git repo. Recommended forms:
  - `git@github.com:org/repo.git`
  - `https://github.com/org/repo.git`
  - `org/repo`
- `build.ref`: git tag or commit (or branch for dev)
- `build.dockerfile`: path to Dockerfile_UI (relative to repo root)
- `build.src`: path to UI source (relative to repo root)
- `frontend_config`: path to UI config template (relative to repo root)

Alternative (image mode):
- `image`: prebuilt UI image to use (if set, the CLI skips building `web-ui`).
  If both `build` and `image` are present, the image takes precedence.

Optional:
- `nginx_ui_config`: path to UI nginx config (relative to repo root)
- `ui_env_build_relative`: build‑time env file path (relative to repo root)
- `frontend.domain`: deployment domain (informational for now)
- `domain`: root domain used when expanding `YOUR_DOMAIN` in proxylogin URLs
- `domain`: root domain used when expanding `YOUR_DOMAIN` in proxylogin URLs.
  When `proxy.ssl: true`, the CLI also applies it to nginx `server_name` and
  Let’s Encrypt cert paths, assuming:
  - `/etc/letsencrypt/live/<domain>/fullchain.pem`
  - `/etc/letsencrypt/live/<domain>/privkey.pem`
- `paths.*`: local-only host path overrides. The CLI will write these into the
  workdir copy of the descriptor when needed; they are not required in the
  public template.
- `aws.region`: used to set `AWS_REGION`/`AWS_DEFAULT_REGION` in services.
- `aws.profile`: used to set `AWS_PROFILE` in services.
- `aws.ec2`: when true, the CLI sets EC2-safe defaults (`AWS_SDK_LOAD_CONFIG=1`,
  `AWS_EC2_METADATA_DISABLED=false`, and `NO_PROXY=169.254.169.254,localhost,127.0.0.1`).

`platform.repo` follows the same clone-source shape. Recommended forms:

- `git@github.com:kdcube/kdcube-ai-app.git`
- `https://github.com/kdcube/kdcube-ai-app.git`
- `kdcube/kdcube-ai-app`

Older single-name values such as `kdcube-ai-app` are still accepted by the CLI
for backward compatibility, but new descriptors should use a cloneable repo
specification.

**CLI behavior:**
- If `frontend.build` is provided, clones the frontend repo into
  `~/.kdcube/kdcube-runtime/frontend` (if needed).
- If `frontend.image` is set, uses that image for `web-ui` and skips building it.
- Sets `UI_BUILD_CONTEXT`, `UI_DOCKERFILE_PATH`, `UI_SOURCE_PATH`, and related
  env values in the workdir.
- Uses `frontend_config` as the template for `config.json`, then patches tenant,
  project, and auth values.
  If no frontend repo is cloned, the `frontend_config` path is resolved relative
  to the descriptor location on disk.
- If `frontend_config` is omitted, the CLI generates a built-in default based on
  auth mode:
  - `simple` -> `config.hardcoded.json`
  - `cognito` -> `config.cognito.json`
  - `delegated` -> `config.delegated.json`
- When `auth.type: delegated` and the built-in delegated config is used, the CLI
  also applies:
  - `proxy.route_prefix` -> frontend `routesPrefix`
  - root `company` -> delegated `totpAppName` / `totpIssuer` when present
- If `nginx_ui_config` is omitted, the CLI falls back to the built-in `nginx_ui.conf`.
- If `platform.ref` is present, the CLI offers **assembly-descriptor** as an
  install source (pulls that tag from DockerHub).

### Auth section (CLI usage)
The `auth` section lets you predefine authentication for the wizard.
If `auth.type` is `cognito` or `delegated`, the CLI will ask whether to
use the Cognito settings from the descriptor or re-enter them.

Supported keys:
- `auth.type`: `simple`, `cognito`, or `delegated`
- `auth.cognito.region`
- `auth.cognito.user_pool_id` (legacy aliases are normalized on save)
- `auth.cognito.app_client_id` (legacy aliases are normalized on save)
- `auth.cognito.service_client_id` (legacy aliases are normalized on save)
- `auth.proxy_login.redis_key_prefix` (tenant/project placeholders are expanded)
- `auth.proxy_login.token_masquerade`
- `auth.proxy_login.password_reset.company`
- `auth.proxy_login.password_reset.sender`
- `auth.proxy_login.password_reset.template_name`
- `auth.proxy_login.password_reset.redirect_url`
- `auth.proxy_login.http_urlbase`

If `domain` is set at the root of the descriptor, the CLI uses it to expand
`YOUR_DOMAIN` placeholders in proxylogin URLs. If not set, it falls back to
`localhost` (and includes the UI port if it’s not 80/443).

### Routines section (CLI usage)
The `routines` section configures background scheduler jobs. Values are applied
**non‑interactively** to `.env.ingress` when an assembly descriptor is provided.
All keys are optional — if omitted, service defaults apply.

Supported keys:
- `routines.economics.subscription_rollover_enabled`: enable/disable subscription rollover job (default: `true`)
- `routines.economics.subscription_rollover_cron`: rollover schedule in cron format (default: `15 * * * *`)
- `routines.economics.subscription_rollover_lock_ttl_seconds`: distributed lock TTL for rollover job (default: `900`)
- `routines.economics.subscription_rollover_sweep_limit`: max subscriptions processed per rollover run (default: `500`)
- `routines.stripe.reconcile_enabled`: enable/disable Stripe reconcile job (default: `true`)
- `routines.stripe.reconcile_cron`: Stripe reconcile schedule in cron format (default: `45 * * * *`)
- `routines.stripe.reconcile_lock_ttl_seconds`: distributed lock TTL for reconcile job (default: `900`)
- `routines.opex.agg_cron`: accounting aggregation schedule in cron format (default: `0 3 * * *`)

### Notifications section (CLI usage)
The `notifications.email` section configures SMTP for admin alert emails (Stripe events,
refunds, subscription changes). Values are applied **non‑interactively** to `.env.ingress`.
All keys are optional — if omitted, service defaults apply (email sending is silently skipped
when `host` is not set).

The SMTP password is **not** set here — put it in `secrets.yaml` under `services.email.password`.

Supported keys:
- `notifications.email.enabled`: enable/disable email sending entirely (default: `true`)
- `notifications.email.host`: SMTP server hostname (default: unset — disables sending)
- `notifications.email.port`: SMTP server port (default: `587`)
- `notifications.email.user`: SMTP login username (default: unset)
- `notifications.email.from`: sender address; falls back to `user` if omitted (default: unset)
- `notifications.email.to`: default recipient for admin alerts (default: `ops@example.com`)
- `notifications.email.use_tls`: enable STARTTLS (default: `true`)

### Context / infra / paths (CLI usage)
When you run the wizard with an assembly descriptor, it will:
- Use `context.tenant` and `context.project` as defaults for prompts.
- Use `secrets.provider` as the source of truth for `SECRETS_PROVIDER`.
- Use `infra.postgres` and `infra.redis` values as defaults.
- Use `storage.workspace` as the source of truth for React workspace bootstrap mode.
- Use `storage.claude_code_session` as the source of truth for Claude Code session-store bootstrap mode.
- Use `paths.*` as defaults for local host paths.
- Write back any values you enter, keeping `assembly.yaml` as the source of truth.

`infra.postgres.ssl` maps to `POSTGRES_SSL` in ingress/proc.

For infra, the wizard will explicitly ask whether to use Postgres/Redis settings
from the descriptor or override them. If you choose to use the descriptor, it
will not prompt for those values.

These sections are used by the CLI to **render .env files** for docker-compose.

Runtime env mapping:

- `storage.workspace.type` -> `REACT_WORKSPACE_IMPLEMENTATION`
- `storage.workspace.repo` -> `REACT_WORKSPACE_GIT_REPO`
- `storage.claude_code_session.type` -> `CLAUDE_CODE_SESSION_STORE_IMPLEMENTATION`
- `storage.claude_code_session.repo` -> `CLAUDE_CODE_SESSION_GIT_REPO`

---

## 2) Bundle module & subdir rules

Bundle module/subdir rules live in `bundles.yaml`. See:
[docs/service/configuration/bundle-configuration-README.md](../configuration/bundle-configuration-README.md)

---

## 3) Release ownership

**Who edits what:**
- Platform team → `VERSION` + platform tag
- Customer bundle team → bundle code + bundle tag
- Customer frontend team → UI code + UI tag
- Release manager → `assembly.yaml` (platform/frontend) and `bundles.yaml` (bundles)

---

## 4) Runtime env (bundles)

**Proc requires:**

- `AGENTIC_BUNDLES_JSON` (normally `/config/bundles.yaml`)
- `BUNDLES_FORCE_ENV_ON_STARTUP=1` (only for a single rollout if you need to overwrite Redis)

Bundle props are applied when `bundles.yaml` is used to seed or reset Redis.
If you do **not** reset Redis, existing runtime props remain unchanged.
