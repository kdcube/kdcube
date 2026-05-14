---
id: ks:docs/configuration/bundles-secrets-descriptor-README.md
title: "Bundles Secrets Descriptor"
summary: "Deployment-scoped bundle secret configuration in bundles.secrets.yaml: per-bundle credentials, integration tokens, provider service key overrides, and bundle secrets across local file mode and AWS."
tags: ["service", "configuration", "bundle", "secrets", "deployment", "descriptor"]
keywords: ["bundle secret inventory", "per-bundle credentials", "integration tokens", "bundle api keys", "deployment-scoped bundle secrets", "local secrets file mode", "aws secrets manager bundle secrets", "bundle secret provider", "bundle secret export path", "service key override", "per-bundle provider key", "get_service_secret", "bundle openai key", "bundle anthropic key", "bundle stripe key", "bundle git token", "services namespace override"]
see_also:
  - ks:docs/service/cicd/descriptors-README.md
  - ks:docs/configuration/bundles-descriptor-README.md
  - ks:docs/configuration/secrets-descriptor-README.md
  - ks:docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - ks:docs/configuration/runtime-configuration-and-secrets-store-README.md
---
# Bundles Secrets Descriptor

`bundles.secrets.yaml` stores deployment-scoped bundle secrets.

Example:

```yaml
bundles:
  version: "1"
  items:
    - id: "user-mgmt@1-0"
      secrets:
        user_management:
          cognito_user_pool_id: null
          sheets_integration_credentials_file_content: null
```

## What it is for

Use this file for bundle-specific secrets such as:

- API keys
- tokens
- private credentials
- integration secrets that belong to one bundle

Do not put these values into:

- `bundles.yaml`
- `assembly.yaml`

## Reserved `services.*` keys for provider overrides

The platform recognises a reserved key namespace inside each bundle's secrets:
`services.<provider>.<key>`.

These paths mirror the canonical keys in `secrets.yaml`.
When a bundle sets one of these keys, the platform resolves it before the
platform/global value for requests executing inside that bundle's context.

Overridable keys:

| Key in `bundles.secrets.yaml` | Platform/global counterpart |
|---|---|
| `services.openai.api_key` | `services.openai.api_key` |
| `services.anthropic.api_key` | `services.anthropic.api_key` |
| `services.anthropic.claude_code_key` | `services.anthropic.claude_code_key` |
| `services.google.api_key` | `services.google.api_key` |
| `services.openrouter.api_key` | `services.openrouter.api_key` |
| `services.brave.api_key` | `services.brave.api_key` |
| `services.huggingface.api_key` | `services.huggingface.api_key` |
| `services.stripe.secret_key` | `services.stripe.secret_key` |
| `services.stripe.webhook_secret` | `services.stripe.webhook_secret` |
| `services.git.http_token` | `services.git.http_token` |

Example — giving one bundle its own OpenAI key and git credentials:

```yaml
bundles:
  version: "1"
  items:
    - id: "my-bundle@1-0"
      secrets:
        services:
          openai:
            api_key: "sk-bundle-specific-key"
          git:
            http_token: "ghp_bundle_pat"
            http_user: "x-access-token"
```

### How to read a service key in bundle code

Use `get_service_secret` / `get_service_secret_async` instead of `get_settings()`
or raw `get_secret_async`:

```python
from kdcube_ai_app.apps.chat.sdk.config import (
    get_service_secret,
    get_service_secret_async,
)

api_key = get_service_secret("openai.api_key")
api_key = await get_service_secret_async("openai.api_key")
```

Resolution order:

1. `bundles.<current_bundle_id>.secrets.services.<key>` — read from `bundles.secrets.yaml`
2. `services.<key>` — read from `secrets.yaml` (platform/global fallback)

The current bundle id is resolved from the async-task-local request context
(`BUNDLE_ID_CV`), so isolation between concurrent bundle requests is automatic.

## Direct runtime contract from this descriptor

### Supported access APIs

| Need | API | Notes |
|---|---|---|
| secret for the current bundle in async code | `await get_secret_async("b:group.key")` | Expands to `bundles.<current_bundle_id>.secrets.group.key` |
| explicit bundle-scoped secret in async code | `await get_secret_async("bundles.<bundle_id>.secrets.group.key")` | Use when bundle id is known explicitly |
| compatibility sync reads | `get_secret("b:group.key")` | Keep for old sync-only code |
| write current bundle secret | `await set_bundle_secret("group.key", value)` | Persists into the configured secrets provider |

### File-resolution env vars

| Env var | Meaning | Modes |
|---|---|---|
| `BUNDLE_SECRETS_YAML` | Explicit file URI or path for `bundles.secrets.yaml` in `secrets-file` mode | direct local service run |
| `HOST_BUNDLES_SECRETS_YAML_DESCRIPTOR_PATH` | Host file staged into `/config/bundles.secrets.yaml` by the CLI installer | CLI local compose |

### Descriptor fields that matter to runtime

| `bundles.secrets.yaml` field | Used by | Notes |
|---|---|---|
| `bundles.items[].id` | bundle-scoped secret path resolution | must match the bundle id in `bundles.yaml` |
| `bundles.items[].secrets.*` | `get_secret_async("b:...")` in async code | values are bundle-scoped secret leaves |

## Authority by mode

### CLI local compose

If `assembly.secrets.provider == secrets-file`:

- `bundles.secrets.yaml` is the live file authority
- it is mounted into the runtime

If you use another provider:

- the file is installer input only

### Direct local service run

If `SECRETS_PROVIDER=secrets-file`:

- point the process to the file with `BUNDLE_SECRETS_YAML`
- the file is the live authority

### AWS deployment

In `aws-sm`:

- `bundles.secrets.yaml` is deployment input and export format
- live deployment-scoped bundle secret authority is:
  - `<prefix>/bundles/<bundle_id>/secrets`

## Key rule

- local file mode: `bundles.secrets.yaml` can be the live authority
- AWS `aws-sm` mode: grouped AWS SM bundle docs are the live authority
