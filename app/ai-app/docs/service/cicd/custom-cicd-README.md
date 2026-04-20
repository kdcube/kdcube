---
id: ks:docs/service/cicd/custom-cicd-README.md
title: "Custom CICD"
summary: "Two‑repo CI/CD plan covering build outputs, image mapping, assembly.yaml + bundles.yaml flow, and ECS/EC2 deployment steps."
tags: ["service", "cicd", "deployment", "ecs", "ec2", "docker-compose", "images", "bundles", "release", "frontend"]
keywords: ["two-repo", "platform repo", "custom app repo", "assembly.yaml", "bundles.yaml", "bundle packaging", "image mapping", "ecs task definitions", "compose envs", "BUNDLES_YAML_DESCRIPTOR_PATH"]
see_also:
  - ks:docs/service/cicd/release-bundle-README.md
  - ks:docs/service/cicd/descriptors-README.md
  - ks:docs/service/configuration/assembly-descriptor-README.md
  - ks:docs/service/configuration/secrets-descriptor-README.md
  - ks:docs/service/cicd/release-README.md
  - ks:docs/service/configuration/service-config-README.md
---
# Custom CI/CD (Open Source + Custom App Repo)

This document is a practical plan for a **two‑repo** CI/CD setup:

- **Platform repo (open source):** `kdcube-ai-app`
- **Custom app repo (private):** bundles + frontend + optional UI/nginx templates

Goal: keep **docker‑compose (EC2)** working during transition, while preparing **ECS** deployment.

---

## Product CI Prerequisites (Platform Repo)

Before GitHub Actions can build and publish releases, ensure these are set:

1. **DockerHub account + repo namespace**
   - Create a DockerHub user (or org) and repositories
   - Add secrets in GitHub:
     - `DOCKERHUB_USERNAME`
     - `DOCKERHUB_TOKEN`
     - `DOCKERHUB_NAMESPACE` (optional; defaults to username)

2. **PyPI publishing**
   - Create a PyPI project: `kdcube-cli`
   - Configure **Trusted Publisher** (recommended) for this repo/workflow  
     (workflow file: `.github/workflows/publish-kdcube-cli.yml`)
   OR set a `PYPI_API_TOKEN` secret if using API token auth

3. **GitHub Actions permissions**
   - `contents: write` (to create tags)
   - `id-token: write` (for PyPI trusted publisher)

4. **Assembly + Bundles files**
   - `assembly.yaml` must exist and contain:
     - `platform.ref` (PEP440‑compatible; used as tag + CLI version)
  - `bundles.yaml` should exist in the custom app repo to define bundle list.

---

## TL;DR (1‑page CI/CD Flow)

**Inputs (custom app repo):**
- `assembly.yaml` (pins platform tag + frontend tag)
- `bundles.yaml` (bundle list + refs)
- `bundles.secrets.yaml` (optional; bundle secrets for local/dev)

**CI (build phase):**
1. Checkout **platform repo** at `platform.ref`.
2. Checkout **custom app repo** at `bundles.yaml` refs + `frontend.ref`.
3. For each bundle entry → copy `<subdir>/<id>` into `/bundles/<id>`.
4. Generate `bundles.yaml` entries with `path=/bundles` and `module=...entrypoint`.
5. Build + push images:
   - `kdcube-chat-ingress`
   - `kdcube-chat-proc` (or `kdcube-chat-proc-bundled`)
   - `py-code-exec` (or `kdcube-exec-bundled`)
   - `kdcube-metrics`
   - `kdcube-web-ui`
   - `proxylogin` / `web-proxy` (if used)

**CD (deploy phase):**
- **ECS:** update task definitions with new image tags + envs.
- **EC2 compose:** update `.env.*` files and `docker compose up -d`.

```mermaid
flowchart LR
  R["assembly.yaml + bundles.yaml<br/>private repo"] --> CI[CI build]
  S["secrets.yaml<br/>(deploy-time)"] --> CI
  G["gateway.yaml<br/>(deploy-time)"] --> CI
  BS["bundles.secrets.yaml<br/>(local/dev)"] --> CI
  CI --> I[kdcube-chat-ingress]
  CI --> P[kdcube-chat-proc-bundled]
  CI --> E[kdcube-exec-bundled]
  CI --> M[kdcube-metrics]
  CI --> U[kdcube-web-ui]
  CI --> PX[proxylogin/web-proxy]
  I --> CD[Deploy ECS/Compose]
  P --> CD
  E --> CD
  M --> CD
  U --> CD
  PX --> CD
```

**Descriptor usage (CD/CLI):**
- `assembly.yaml` → platform/frontend/auth/infra defaults (used by CLI/CI).
- `assembly.yaml` may also be mounted into runtime services for plain reads via `read_plain(...)`.
- `bundles.yaml` → bundle registry authority for proc.
- `bundles.yaml` may also be mounted for plain reads via `read_plain("b:...")`.
- `bundles.secrets.yaml` → bundle secrets (local/dev; injected into secrets sidecar).
- `secrets.yaml` → platform/infra secrets (CI/ops; injected into secrets manager).
- `gateway.yaml` → gateway policy JSON (rendered into `GATEWAY_CONFIG_JSON`).

The bundle-registry source and the descriptor-mount contract are related but not the same:

- proc may seed the bundle registry from `/config/bundles.yaml`
- ingress, proc, or metrics may read `/config/assembly.yaml` and `/config/bundles.yaml` directly through `read_plain(...)`

See:
[docs/service/configuration/service-config-README.md](../configuration/service-config-README.md)

**Runtime config (where env lives):**
- ECS: task definitions (ingress/proc/metrics)
- Compose: `.env.ingress`, `.env.proc`, `.env.metrics`
- UI config: `config.json` baked or mounted at runtime

**Bundle runtime toggles (proc):**
- `BUNDLES_FORCE_ENV_ON_STARTUP=1` for one rollout to overwrite Redis registry.
- `BUNDLES_INCLUDE_EXAMPLES=0` to disable example bundles.
- `BUNDLE_GIT_RESOLUTION_ENABLED=0` when using mounted paths only.
- `BUNDLE_GIT_REDIS_LOCK=1` to serialize git pulls per instance.
- `GIT_SSH_KEY_PATH` / `GIT_SSH_KNOWN_HOSTS` for private git repos.

---

## 1) Build Outputs (Images)

**Platform images (built from open‑source repo):**

- `kdcube-chat-ingress` (thin ingress service)
- `kdcube-chat-proc` (processor service)
- `kdcube-metrics` (metrics service)
- `kdcube-postgres-setup` (db schema provisioning)
- `kdcube-web-proxy` (nginx/openresty proxy)
- `proxylogin` (auth proxy for delegated flows)

**Custom app images (built from custom app repo):**

- `kdcube-web-ui` (frontend bundle)
- `kdcube-chat-proc-bundled` (processor image with bundles baked in)
- `kdcube-exec-bundled` (code‑exec image with bundles baked in)

---

## 1.1) Image → Dockerfile Mapping

**Platform repo (open source):**  
Use Dockerfiles from `deployment/docker/custom-ui-managed-infra`.  
(They are identical to the local‑infra variants; keep one source of truth for CI.)

| Image | Dockerfile |
| --- | --- |
| `kdcube-chat-ingress` | `app/ai-app/deployment/docker/custom-ui-managed-infra/Dockerfile_Ingress` |
| `kdcube-chat-proc` | `app/ai-app/deployment/docker/custom-ui-managed-infra/Dockerfile_Chatproc` |
| `kdcube-metrics` | `app/ai-app/deployment/docker/custom-ui-managed-infra/Dockerfile_Metricservice` |
| `kdcube-postgres-setup` | `app/ai-app/deployment/docker/custom-ui-managed-infra/Dockerfile_PostgresSetup` |
| `kdcube-web-proxy` | `app/ai-app/deployment/docker/custom-ui-managed-infra/Dockerfile_ProxyOpenResty` |
| `proxylogin` | `app/ai-app/deployment/docker/custom-ui-managed-infra/Dockerfile_ProxyLogin` |
| `py-code-exec` | `app/ai-app/deployment/docker/custom-ui-managed-infra/Dockerfile_Exec` |

**Custom app repo (private):**

| Image | Dockerfile |
| --- | --- |
| `kdcube-web-ui` | `<custom-app-repo>/ops/.../Dockerfile_UI` |
| `kdcube-chat-proc-bundled` | `<custom-app-repo>/ops/.../Dockerfile_ChatProcBundled` (base = `kdcube-chat-proc`) |
| `kdcube-exec-bundled` | `<custom-app-repo>/ops/.../Dockerfile_ExecBundled` (base = `py-code-exec`) |

---

## 2) Recommended Tagging Strategy

**Platform repo:**

- `:git-sha` (immutable)
- `:branch` (moving tag for dev/staging)
- `:vX.Y.Z` (optional releases)

**Customer repo:**

- `:customer-sha`
- `:customer-branch`

---

## 3) Assembly Descriptor (Platform + Frontend)

See: [docs/service/configuration/assembly-descriptor-README.md](../configuration/assembly-descriptor-README.md)

This file (`assembly.yaml` in the private repo) pins platform + frontend.
Bundle list and refs live in `bundles.yaml` (same repo).

For **sensitive values** (LLM keys, Git HTTPS token, proxylogin client secret,
infra passwords), use a separate `secrets.yaml` and keep it out of the repo.
See: [docs/service/configuration/secrets-descriptor-README.md](../configuration/secrets-descriptor-README.md)

For local compose, the CLI can also use `platform.ref` from `assembly.yaml`
to pull the matching platform images (install source: **assembly-descriptor**).

### Frontend mode (build vs image)

The `frontend` section supports **build mode** (clone + build UI) and **image mode**
(use a prebuilt UI image). When `image` is set, it takes precedence over `build`.

Minimal example:

```yaml
frontend:
  build:
    repo: "git@github.com:org/private-app.git"
    ref: "ui-v2026.02.22"
    dockerfile: "ops/docker/Dockerfile_UI"
    src: "ui/chat-web-app"
  image: "registry/private-app-ui:2026.02.22"  # optional; if set, CLI uses this image
  frontend_config: "ops/docker/config.delegated.json"  # optional
  nginx_ui_config: "ops/docker/nginx_ui.conf"          # optional
```

For local compose (custom‑ui‑managed‑infra), the CLI will:
- use `image` if present (skip UI build),
- otherwise clone the UI repo and build it,
- generate runtime `config.json` from `frontend_config` if provided.

If `frontend_config` is omitted, the CLI falls back to a built-in template:
- `simple` -> `config.hardcoded.json`
- `cognito` -> `config.cognito.json`
- `delegated` -> `config.delegated.json`

If `nginx_ui_config` is omitted, the CLI falls back to the built-in `nginx_ui.conf`.

When `proxy.ssl: true` and `domain` is set in `assembly.yaml`, the CLI also patches
the runtime nginx SSL config so `YOUR_DOMAIN_NAME` is replaced in `server_name`
and the default Let’s Encrypt cert paths under `/etc/letsencrypt/live/<domain>/...`.

Details are documented in:  
[docs/service/configuration/assembly-descriptor-README.md](../configuration/assembly-descriptor-README.md)

---

## 4) Bundle Descriptor (Derived by CI)

CI stages `bundles.yaml` for proc.
See: [docs/service/configuration/bundles-descriptor-README.md](../configuration/bundles-descriptor-README.md)

**Optional shortcut (EC2/dev):** mount `bundles.yaml` directly so it is
available inside proc at `/config/bundles.yaml`.

---

## 5) Environment Files (Compose‑compatible)

We now split env per component:

- `.env` (compose paths + build contexts)
- `.env.ingress`
- `.env.proc`
- `.env.metrics`
- `.env.postgres.setup`
- `.env.proxylogin` (optional)
- `.env.frontend` (optional)

**Important:** Use the **same effective gateway config** for ingress/proc/metrics
(tenant/project is shared). In the current CI/CD packages this is commonly
realized as a shared `GATEWAY_CONFIG_JSON`.
For CI/CD, set `GATEWAY_CONFIG_FORCE_ENV_ON_STARTUP=1` to enforce the
env/descriptor-sourced gateway config on each deploy.

---

## 6) EC2 Docker‑Compose (Transition Phase)

**Current flow (recommended while migrating):**

1. CI builds images and pushes to registry.
2. Ops updates `.env.*` files on EC2.
3. Compose pulls new images.

Example command sequence:

```bash
docker compose -f docker-compose-decentralized-infra-data.yaml pull
docker compose -f docker-compose-decentralized-infra-data.yaml up -d
```

**Notes:**
- `chat-ingress` + `chat-proc` are now separate services.
- `metrics` can be enabled without user impact.
- Use `.env.postgres.setup` for `postgres-setup`, not `.env.ingress/.env.proc`.

### Git bundle auth (EC2 / docker‑compose)

If you use git‑defined bundles from **private repos**, ops must provide:

1. A private SSH key + known_hosts on the **host**.
2. Host paths in the compose `.env`:
   - `HOST_GIT_SSH_KEY_PATH=/path/to/id_ed25519`
   - `HOST_GIT_KNOWN_HOSTS_PATH=/path/to/known_hosts`
3. Proc env (inside container) to point at the mounted files:
   - `GIT_SSH_KEY_PATH=/run/secrets/git_ssh_key`
   - `GIT_SSH_KNOWN_HOSTS=/run/secrets/git_known_hosts`
   - `GIT_SSH_STRICT_HOST_KEY_CHECKING=yes`

These are mounted into the proc container via docker‑compose.

---

## 7) ECS (Target State)

**Split into services:**

1. **Ingress service**
2. **Processor service**
3. **Metrics service**
4. **Proxy/UI (optional split)**

**Routing (example):**

- `/chatbot/api/*` → ingress
- `/chatbot/api/integrations/*` → processor
- `/metrics/*` (if exposed) → metrics (usually internal)

### Git bundle auth (ECS)

If using private git repos:

- Store SSH key + known_hosts in **AWS Secrets Manager** (or SSM).
- Mount them into the **proc task** (e.g. `/run/secrets/git_ssh_key`, `/run/secrets/git_known_hosts`).
- Set:
  - `GIT_SSH_KEY_PATH=/run/secrets/git_ssh_key`
  - `GIT_SSH_KNOWN_HOSTS=/run/secrets/git_known_hosts`
  - `GIT_SSH_STRICT_HOST_KEY_CHECKING=yes`
  - `BUNDLE_GIT_RESOLUTION_ENABLED=1`

**Ownership:** DevOps owns secret provisioning and task definition wiring. Release manager owns
`assembly.yaml` (platform/frontend) and `bundles.yaml` (bundle list + refs).

---

## 8) CI Pipeline (Suggested Structure)

### Platform repo pipeline

- Build + push:
  - `kdcube-chat-ingress`
  - `kdcube-chat-proc`
  - `kdcube-metrics`
  - `kdcube-postgres-setup`
  - `kdcube-web-proxy` (if used)
  - `proxylogin`
- Publish release artifacts (optional)

### Customer repo pipeline

- Build + push:
  - `kdcube-web-ui`
- Build + push (bundles baked into processor):
  - `kdcube-chat-proc-bundled`
- Build + push (bundles baked into exec image):
  - `kdcube-exec-bundled`
- Publish updated nginx config + config.json

---

## 8.1) Bundle Descriptor Generation

CI stages `bundles.yaml` from the custom app repo.
See: [docs/service/configuration/bundles-descriptor-README.md](../configuration/bundles-descriptor-README.md)

---

## 8.2) CD: Where Env Vars Live (Ingress / Proc / Metrics)

**ECS (recommended):**
- Each service has its own **task definition** with env vars + secrets.
- Use the **same `GATEWAY_CONFIG_JSON`** across ingress/proc/metrics.
- For deterministic deploys, set `GATEWAY_CONFIG_FORCE_ENV_ON_STARTUP=1`.
- Mount `bundles.yaml` **only** for the processor service if that service owns bundle loading.

**Compose (transition):**
- `.env.ingress`, `.env.proc`, `.env.metrics`, `.env.postgres.setup`
- Compose file references each env file explicitly.

---

## 8.3) Frontend Config (Runtime)

The frontend requires a **runtime config file** (e.g. `config.json`).
This file must be present **inside the UI container at runtime**.

Options:
1. **Bake into the UI image** during build (Dockerfile copies it).
2. **Mount at runtime** (volume/config/sidecar) in ECS.

See the customer Dockerfile and config example:
- `.../ops/.../Dockerfile_UI`
- `.../ops/.../prod/config.json`

For ECS, the recommended approach is to bake `config.json` into the UI image
or provide it via a mounted volume at the path expected by the UI build.

---

## 9) Bundles in Custom App Repo (No Git Bundles Yet)

Because Git‑based bundle fetching is not finished yet, **bundles must be baked into a processor image** for ECS.

Recommended approach:

1. Use the platform `kdcube-chat-proc` image as the base.
2. Copy bundles from the custom app repo into `/bundles`.
3. Set `BUNDLES_ROOT=/bundles`.

Example Dockerfile (custom app repo):

```dockerfile
FROM <registry>/kdcube-chat-proc:<version>

WORKDIR /app

# Copy bundles into container
COPY ./bundles /bundles

ENV BUNDLES_ROOT=/bundles
```

This produces `kdcube-chat-proc-bundled`, which ECS can run without host mounts.

### Bundle Descriptor Ownership + Versioning (Now)

**Owner:** the custom app repo owns the **bundle descriptor** (`bundles.yaml`) and controls bundle versions.
**Platform repo** only provides the loader + runtime.

**Recommended (today):**

- Mount `bundles.yaml` into the processor and keep it authoritative there.
- Keep `path` pointing to `/bundles/...` (because bundles are baked).
- Also include **git metadata** for traceability (`repo`, `ref`, `subdir`).

Example entry (works today, tracks version):

```json
{
  "default_bundle_id": "my-bundle@2.0.0",
  "bundles": {
    "my-bundle@2.0.0": {
      "id": "my-bundle@2.0.0",
      "name": "My Bundle",
      "path": "/bundles",
      "module": "my-bundle@2.0.0.entrypoint",
      "repo": "https://git.example.com/org/my-bundle.git",
      "ref": "v2.0.0",
      "subdir": "bundles"
    }
  }
}
```

**How version is defined today:**

- The bundle ID (e.g. `my-bundle@2.0.0`) is the visible version.
- `ref` is the **true source version** (tag or commit SHA).
- The baked processor image must contain the matching `/bundles/...` folder.

**Important (until Git bundles are ready):**

- Keep `repo/ref` for traceability **only**.
- Set `BUNDLE_GIT_RESOLUTION_ENABLED=0` to prevent git cloning on startup.

**When Git bundles are enabled:**

- Use pinned `ref` (tag/commit) for deterministic updates.
- If you must track a branch head, set `BUNDLE_GIT_ALWAYS_PULL=1`.
- Set `BUNDLE_GIT_REDIS_LOCK=1` so each replica pulls **once** on startup.

**Apply env bundles on deploy (no admin auth):**

- Set `BUNDLES_FORCE_ENV_ON_STARTUP=1` in proc env for the deploy.
- Restart the processor → Redis registry is overwritten from env.

### Code‑Exec Image (Docker/Fargate)

The processor spawns the **code‑exec** image on demand. The exec image needs access to bundle tools/code.

**Current (no Git bundles):**

- Build a **bundled exec image**:
  - Base: `py-code-exec` (platform)
  - Copy bundles into `/bundles`
  - Set `PY_CODE_EXEC_IMAGE` to this bundled tag

Example Dockerfile (custom app repo):

```dockerfile
FROM <registry>/py-code-exec:<version>
COPY ./bundles /bundles
ENV BUNDLES_ROOT=/bundles
```

**Fargate path:**  
The processor uploads bundles to S3 before starting Fargate exec. This still requires bundles to be present locally in the processor (hence bundled proc image remains needed until Git bundles).

**Note (upcoming):**  
We are adding **bundle‑from‑git** support. Once enabled, you will no longer need a fat processor image.  
Bundles will be configured via env/admin (bundle descriptor) and fetched at runtime.

**Bundle‑from‑git checklist (future):**
- Stop building `kdcube-chat-proc-bundled`
- Stop building `kdcube-exec-bundled`
- Set bundle git descriptors in env/admin
- Ensure processor can fetch bundles at runtime

---

## 10) Exec Flow (Diagram)

```mermaid
flowchart LR
  subgraph Processor
    P[kdcube-chat-proc-bundled]
  end

  subgraph Exec
    E[py-code-exec or kdcube-exec-bundled]
  end

  subgraph Storage
    S3[(S3 bundles snapshot)]
  end

  P -->|local bundles /bundles| E
  P -->|Fargate: upload bundles| S3 -->|download| E

  classDef emphasis fill:#f7f7f7,stroke:#444,stroke-width:1px;
  class P,E,S3 emphasis;
```

---

## 11) Minimal Inputs Required From Ops

- Container registry (ECR) names
- IAM policies for CI push
- ECS cluster + VPC + subnets
- ALB routing rules
- Secrets storage (SSM or Secrets Manager)

---

## 12) Common Failure Points (Checklist)

- `GATEWAY_CONFIG_JSON` missing tenant/project
- Redis URL mismatch between ingress/proc/metrics
- Postgres `max_connections` too low for processor concurrency
- ALB idle timeout too low for SSE
- Missing host mount for `/exec-workspace` when running locally

---

## 13) Next Steps

1. Align image names + ECR repos.
2. Decide ECS task layout (1 service per component).
3. Migrate compose → ECS one service at a time.
4. Enable metrics export + autoscaling rules.
