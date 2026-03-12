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
Runtime services do **not** read assembly.yaml directly.
Bundle configuration is handled via **`bundles.yaml`** (and secrets via
`bundles.secrets.yaml`). See:
[docs/service/configuration/bundle-configuration-README.md](../configuration/bundle-configuration-README.md)

The **CLI** can use the `frontend` section to build and run a
custom UI in the **custom‑ui‑managed‑infra** compose mode.
During setup, the wizard lets you choose whether the descriptor applies to
**frontend** and/or **platform**.

Sensitive values (LLM keys, tokens, passwords) should live in `secrets.yaml`
instead of `assembly.yaml`. See: [docs/service/cicd/secrets-descriptor-README.md](secrets-descriptor-README.md)

Bundles are not defined in assembly.yaml. Use `bundles.yaml` for bundle items
and `bundles.secrets.yaml` for bundle secrets.

---

## 1) Descriptor schema (recommended)

```yaml
release_name: "prod-2026-02-22"

platform:
  repo: "kdcube-ai-app"
  ref: "v0.3.2"          # tag or commit

frontend:
  build:
    repo: "private-ui-repo"
    ref: "ui-v2026.02.22"  # tag or commit
    dockerfile: "ops/docker/Dockerfile_UI"
    src: "ui/chat-web-app"
  image: "registry/private-ui:2026.02.22"         # optional; if set, CLI uses this image
  frontend_config: "ops/docker/config.cognito.json"
  nginx_ui_config: "ops/docker/nginx_ui.conf"     # optional
  ui_env_build_relative: "ui/chat-web-app/.env.ui.build"  # optional
  domain: "chat.example.com"                      # optional

domain: "chat.example.com"                        # optional root domain (used for proxylogin URLs)

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
  ssl: false                # when true, CLI picks SSL nginx templates
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

paths:
  host_kdcube_storage_path: "/srv/kdcube/data/kdcube-storage"
  host_bundles_path: "/srv/kdcube/data/bundles"
  host_bundle_storage_path: "/srv/kdcube/data/bundle-storage"
  host_exec_workspace_path: "/srv/kdcube/data/exec-workspace"

```

Bundle definitions moved to `bundles.yaml`.
See: [docs/service/configuration/bundle-configuration-README.md](../configuration/bundle-configuration-README.md)

### Frontend section (CLI usage)
The `frontend` section is used by `kdcube-setup` to build a customer UI when
you run **custom‑ui‑managed‑infra** compose. It is ignored by runtime services.

Required (build mode):
- `build.repo`: frontend git repo (SSH or HTTPS)
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
- `paths.*`: local-only host path overrides. The CLI will write these into the
  workdir copy of the descriptor when needed; they are not required in the
  public template.
- `aws.region`: used to set `AWS_REGION`/`AWS_DEFAULT_REGION` in services.
- `aws.profile`: used to set `AWS_PROFILE` in services.
- `aws.ec2`: when true, the CLI sets EC2-safe defaults (`AWS_SDK_LOAD_CONFIG=1`,
  `AWS_EC2_METADATA_DISABLED=false`, and `NO_PROXY=169.254.169.254,localhost,127.0.0.1`).

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

### Context / infra / paths (CLI usage)
When you run the wizard with an assembly descriptor, it will:
- Use `context.tenant` and `context.project` as defaults for prompts.
- Use `infra.postgres` and `infra.redis` values as defaults.
- Use `paths.*` as defaults for local host paths.
- Write back any values you enter, keeping `assembly.yaml` as the source of truth.

`infra.postgres.ssl` maps to `POSTGRES_SSL` in ingress/proc.

For infra, the wizard will explicitly ask whether to use Postgres/Redis settings
from the descriptor or override them. If you choose to use the descriptor, it
will not prompt for those values.

These sections are used by the CLI to **render .env files** for docker-compose.

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
