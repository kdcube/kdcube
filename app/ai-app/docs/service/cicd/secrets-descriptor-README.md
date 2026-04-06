---
id: ks:docs/service/cicd/secrets-descriptor-README.md
title: "Secrets Descriptor"
summary: "Optional secrets.yaml schema and CLI handling for runtime secrets and infra passwords."
tags: ["service", "cicd", "secrets", "descriptor", "schema", "cli"]
keywords: ["secrets.yaml", "secrets descriptor", "openai", "anthropic", "brave", "git http token", "proxylogin", "cognito client secret", "redis password", "postgres password"]
see_also:
  - ks:docs/service/cicd/assembly-descriptor-README.md
  - ks:docs/service/cicd/cli-README.md
  - ks:docs/service/configuration/service-config-README.md
---
# Secrets Descriptor (secrets.yaml)

The secrets descriptor is an **optional** YAML file used by the CLI to prefill
runtime secrets and sensitive infra passwords. It is **not copied** into the
workdir and is **not persisted** by the CLI. You provide a path when running
the wizard (or via `KDCUBE_SECRETS_DESCRIPTOR_PATH`).

**Template:** [`app/ai-app/deployment/secrets.yaml`](../../../deployment/secrets.yaml)

Bundle‑specific secrets live in **`bundles.secrets.yaml`** (separate file). See:
[docs/service/configuration/bundle-configuration-README.md](../configuration/bundle-configuration-README.md).

Secrets are keyed by **dot‑path** (e.g. `services.openai.api_key`).

There are two runtime consumption models:
- CLI provisioning into a writable provider such as `secrets-service` or `aws-sm`
- direct runtime loading via `SECRETS_PROVIDER=secrets-file`

## 1) Schema (recommended)

```yaml
services:
  openai:
    api_key: null
  google:
    api_key: null
  anthropic:
    api_key: null
    claude_code_key: null
  brave:
    api_key: null
  openrouter:
    api_key: null
  huggingface:
    api_key: null
  stripe:
    secret_key: null
    webhook_secret: null

git:
  http_token: null

infra:
  postgres:
    password: null
  redis:
    password: null

auth:
  cognito:
    client_secret: null

aws:
  access_key_id: null
  secret_access_key: null
```

## 2) How the CLI uses it

**Priority:** `secrets.yaml` → user input.

When `secrets.yaml` is provided:
- LLM/search keys (OpenAI/Anthropic/Google/Gemini/Brave/OpenRouter/HuggingFace) are injected into the **secrets sidecar** at runtime.
- Stripe secrets (`services.stripe.*`) are injected into the sidecar.
- `git.http_token` is used as the Git HTTPS token (runtime‑only).
- `auth.cognito.client_secret` is used for **proxylogin** (delegated auth).
- `aws.access_key_id` and `aws.secret_access_key` are injected into the secrets sidecar
  for services that need AWS SDK access.
- `infra.postgres.password` and `infra.redis.password` override assembly values
  and are written into the compose envs (required by local infra containers).

If a value is missing (or `null`), the wizard prompts you for it (unless you skip).

## 3) Direct runtime use (`secrets-file`)

Runtime can also read this descriptor directly:

```bash
SECRETS_PROVIDER=secrets-file
GLOBAL_SECRETS_YAML=file:///absolute/path/to/secrets.yaml
BUNDLE_SECRETS_YAML=file:///absolute/path/to/bundles.secrets.yaml
```

Supported URI schemes:
- `file://...`
- `s3://...`

The runtime reads those files through the storage backend abstraction.

Important:
- `secrets-file` reads and writes the referenced descriptors through the storage backend
- for `file://...`, the target path must be writable by the service
- for `s3://...`, the runtime identity must have object write permissions

## 4) Location & persistence

- The CLI **does not copy** `secrets.yaml` into the workdir.
- Provide a path at install time, or set `KDCUBE_SECRETS_DESCRIPTOR_PATH`.
- Keep this file outside the workspace and **never commit it**.
