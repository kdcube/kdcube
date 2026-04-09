---
id: ks:docs/service/cicd/cli-README.md
title: "CLI (kdcube)"
summary: "CLI design for local env bootstrapping, compose setup, and assembly descriptor validation."
tags: ["service", "cicd", "cli", "env", "deployment"]
keywords: ["kdcube cli", "env init", "docker compose", "local dev", "bundles.yaml", "assembly.yaml"]
see_also:
  - ks:docs/service/cicd/release-README.md
  - ks:docs/service/cicd/assembly-descriptor-README.md
  - ks:docs/service/cicd/secrets-descriptor-README.md
  - ks:docs/service/configuration/bundle-configuration-README.md
  - ks:docs/service/configuration/descriptor-plain-config-README.md
  - ks:docs/service/cicd/gateway-config-README.md
  - ks:docs/service/environment/setup-dev-env-README.md
  - ks:docs/service/environment/setup-for-dockercompose-README.md
---
# KDCube CLI (Design)

This document defines the **initial CLI surface** and behavior. The CLI is for:

- **Platform developers** running services on host (PyCharm/IntelliJ or shell).
- **Compose users** running the all‑in‑one stack.
- **Release tooling** (validate and render assembly descriptors).

CLI root (code): `src/kdcube-ai-app/kdcube_cli`

---

## 1) Immediate use cases

1) **Generate local env files (platform dev)**
   - Create `.env` files in service locations
   - Create required local directories
   - Merge with existing `.env` values

2) **Generate compose env files (all‑in‑one)**
   - Produce `.env.*` files in `deployment/docker/all_in_one_kdcube`
   - Create data folders

2b) **Compose with custom UI (advanced)**
   - Use an `assembly.yaml` that includes a `frontend` section
   - If `frontend.image` is set, the UI build is skipped
   - If `frontend.build` is set, the UI repo is cloned and built
   - Switches compose mode to `custom‑ui‑managed‑infra`

3) **Validate assembly descriptor**
   - Validate schema + refs

4) **Apply bundle descriptors**
   - Use `bundles.yaml` (+ `bundles.secrets.yaml`) to seed runtime bundles + secrets

---

## 2) Commands (initial)

Note for Debian/Ubuntu operators: recent system Python builds may block direct
`pip install` into the system environment with `externally-managed-environment`
(PEP 668). In that case, install the CLI into a dedicated virtual environment,
for example:

```bash
sudo apt-get update
sudo apt-get install -y python3-pip python3-venv
python3 -m venv ~/.venvs/kdcube-cli
~/.venvs/kdcube-cli/bin/pip install -e /path/to/kdcube_cli
~/.venvs/kdcube-cli/bin/kdcube-setup --help
```

### 2.1 `kdcube env init` (platform dev, host)

**Goal:** Create envs for running ingress/proc on host + infra via `local-infra-stack`.

```
kdcube env init \
  --mode dev-host \
  --repo /path/to/kdcube-ai-app
```

**Sources:**
- `deployment/docker/local-infra-stack/sample_env/*`

**Targets (default paths):**
- `deployment/docker/local-infra-stack/.env`
- `deployment/docker/local-infra-stack/.env.postgres.setup`
- `deployment/docker/local-infra-stack/.env.proxylogin`
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/.env.ingress`
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/proc/.env.proc`
- `src/kdcube-ai-app/kdcube_ai_app/apps/metrics/.env.metrics`
- `ui/chat-web-app/public/private/config.hardcoded.json` (generated UI config)

**Folders created (default set):**
- `deployment/docker/local-infra-stack/data/postgres`
- `deployment/docker/local-infra-stack/data/redis`
- `deployment/docker/local-infra-stack/data/clamav-db`
- `deployment/docker/local-infra-stack/data/bundles`
- `deployment/docker/local-infra-stack/data/bundle-local-storage`
- `deployment/docker/local-infra-stack/data/exec-workspace`
- `deployment/docker/local-infra-stack/logs`

### 2.2 `kdcube env init` (compose)

```
kdcube env init \
  --mode compose \
  --repo /path/to/kdcube-ai-app
```

**Sources:**
- `deployment/docker/all_in_one_kdcube/sample_env/*`

**Targets:**
- `<workdir>/config/.env`
- `<workdir>/config/.env.ingress`
- `<workdir>/config/.env.proc`
- `<workdir>/config/.env.metrics`
- `<workdir>/config/.env.postgres.setup`
- `<workdir>/config/.env.proxylogin` (optional)
- `<workdir>/config/frontend.config.<mode>.json`

**Folders created:**
- `<workdir>/data/*` (same subfolders as above)
- `<workdir>/logs/*`

### 2.3 Custom UI via assembly descriptor (compose)

When `assembly.yaml` contains a `frontend` section, the CLI uses
**custom‑ui‑managed‑infra** compose mode:

- `frontend.image` → use a prebuilt UI image (skip build)
- `frontend.build` → clone repo and build UI

The CLI also generates runtime `config.json` from `frontend_config`.
If `frontend_config` is omitted, it falls back to a built-in template based on auth mode:
- `simple` -> `config.hardcoded.json`
- `cognito` -> `config.cognito.json`
- `delegated` -> `config.delegated.json`

If `nginx_ui_config` is omitted, the CLI falls back to the built-in `nginx_ui.conf`.

When `proxy.ssl: true` and `assembly.domain` is set, the CLI also patches the
runtime nginx SSL config so `YOUR_DOMAIN_NAME` is replaced in `server_name` and
default Let’s Encrypt cert paths under `/etc/letsencrypt/live/<domain>/...`.
See: [docs/service/cicd/assembly-descriptor-README.md](assembly-descriptor-README.md)

If `platform.ref` is present in the descriptor, the install source selector
adds **assembly-descriptor**, which pulls that tag from DockerHub.

The wizard asks whether to apply the descriptor to **frontend**
and/or **platform** (these can be enabled independently).

If no path is provided, the wizard uses `config/assembly.yaml` in the workdir
and seeds it from `deployment/assembly.yaml`.

When an assembly descriptor is provided, the wizard writes non‑secret values
back into `assembly.yaml` (tenant/project, auth, infra, paths) and then renders
`.env*` from it. The assembly file becomes the source of truth for local config.

The CLI also mounts descriptor files into runtime services so code can read
plain non-secret descriptor values directly:

- `/config/assembly.yaml`
- `/config/bundles.yaml`

This is the runtime contract behind `read_plain(...)` / `get_plain(...)`.
See:
[docs/service/configuration/descriptor-plain-config-README.md](../configuration/descriptor-plain-config-README.md)

The same descriptor also controls workspace/session bootstrap settings for agent runtimes:

- `storage.workspace.type` -> `REACT_WORKSPACE_IMPLEMENTATION`
- `storage.workspace.repo` -> `REACT_WORKSPACE_GIT_REPO`
- `storage.claude_code_session.type` -> `CLAUDE_CODE_SESSION_STORE_IMPLEMENTATION`
- `storage.claude_code_session.repo` -> `CLAUDE_CODE_SESSION_GIT_REPO`

Repo field contract:

- `platform.repo` and `frontend.build.repo` should use a cloneable repo spec:
  - `git@github.com:org/repo.git`
  - `https://github.com/org/repo.git`
  - `org/repo`
- older single-name values such as `kdcube-ai-app` are still accepted for
  backward compatibility, but new descriptors should use one of the cloneable
  forms above

### 2.4 Bundles descriptor (optional)

You can provide a **bundles descriptor** (`bundles.yaml`) and an optional
**bundles secrets** file (`bundles.secrets.yaml`). This is the preferred way
to configure bundles and bundle secrets.

When provided, the CLI:
- mounts `bundles.yaml` as `/config/bundles.yaml`
- sets `AGENTIC_BUNDLES_JSON=/config/bundles.yaml`
- injects secrets from `bundles.secrets.yaml` into the secrets sidecar

`AGENTIC_BUNDLES_JSON` controls proc bundle-registry seeding.
It is separate from the broader descriptor mounts used by `read_plain(...)`.

Templates:
- [`deployment/bundles.yaml`](../../../deployment/bundles.yaml)
- [`deployment/bundles.secrets.yaml`](../../../deployment/bundles.secrets.yaml)

### 2.5 Secrets descriptor (optional)
If you provide a `secrets.yaml`, the CLI will use it to prefill runtime secrets
and sensitive infra passwords. The file is **not copied** into the workdir.

See: [docs/service/cicd/secrets-descriptor-README.md](secrets-descriptor-README.md)

### 2.6 Gateway config descriptor (optional)
If you provide a `gateway.yaml`, the CLI will replace `GATEWAY_CONFIG_JSON`
in `.env.ingress`, `.env.proc`, and `.env.metrics` with the descriptor content.
The wizard still patches `tenant` and `project` from your prompts.

Template: [`deployment/gateway.yaml`](../../../deployment/gateway.yaml)

You can skip the prompt by setting:
```
KDCUBE_GATEWAY_DESCRIPTOR_PATH=/path/to/gateway.yaml
```

See: [docs/service/cicd/gateway-config-README.md](gateway-config-README.md)

### 2.7 `kdcube release validate`

```
kdcube release validate --file assembly.yaml
```

Validates assembly descriptor schema and prints errors with line numbers.

### 2.8 `kdcube release render-bundles`

```
kdcube release render-bundles --file bundles.yaml --out bundles.json
```

Renders `bundles.items` to a runtime registry payload for `AGENTIC_BUNDLES_JSON`.

---

## 3) CLI env overrides

You can also pre‑seed paths and flags via environment variables:

| Variable | Description |
| --- | --- |
| `KDCUBE_ASSEMBLY_DESCRIPTOR_PATH` | Path to `assembly.yaml` (copied into workdir config). |
| `KDCUBE_ASSEMBLY_USE_FRONTEND` | `1/0` to apply assembly frontend config. |
| `KDCUBE_ASSEMBLY_USE_PLATFORM` | `1/0` to apply assembly platform config. |
| `KDCUBE_BUNDLES_DESCRIPTOR_PATH` | Path to `bundles.yaml` (copied into workdir config). |
| `KDCUBE_BUNDLES_SECRETS_PATH` | Path to `bundles.secrets.yaml` (used to inject secrets). |
| `KDCUBE_USE_BUNDLES_DESCRIPTOR` | `1/0` to apply bundles descriptor. |
| `KDCUBE_USE_BUNDLES_SECRETS` | `1/0` to apply bundles secrets. |
| `KDCUBE_GATEWAY_DESCRIPTOR_PATH` | Path to `gateway.yaml` (used for GATEWAY_CONFIG_JSON). |

---

## 4) Env merge semantics

The CLI **never overwrites existing values** by default.

Rules (default):
1) If a key exists in target `.env` and is non‑empty → **keep**.
2) If a key exists but is empty → fill from template if available.
3) If a key is missing → add from template.

**Secrets are never printed**, but the CLI reports missing values as:
```
MISSING (secret)
```

### 4.1 Update mode (explicit)

To overwrite existing values, use:

```
kdcube env init --mode dev-host --repo ... --update
```

`--update` will:
- overwrite non‑secret values
- **never** overwrite secrets unless `--update-secrets` is explicitly provided

---

## 5) Secret handling (default)

Keys treated as secrets by default (pattern‑based):
- `*_SECRET`, `*_TOKEN`, `*_KEY`, `*_PASSWORD`
- `AWS_*`, `OPENAI_*`, `ANTHROPIC_*`, `STRIPE_*`

Secrets are written to env files if provided by templates or overrides, but **never printed**.

---

## 6) Overrides

You can override any value:

```
kdcube env init --mode dev-host --repo ... \
  --set EXEC_WORKSPACE_ROOT=/path/to/exec \
  --set AGENTIC_BUNDLES_ROOT=/bundles
```

Overrides apply **after** merge rules.

---

## 7) Sample bundles (local only)

If `bundles.yaml` is empty, the CLI can seed sample bundles:

```
kdcube bundles seed --preset samples
```

This is intended for **local development** only.

---

## 8) Future commands (next phase)

- `kdcube doctor` (validate env + filesystem + runtime dependencies)
- `kdcube compose up` (wrapper around docker compose)
- `kdcube release tag` (tag + VERSION validation)
