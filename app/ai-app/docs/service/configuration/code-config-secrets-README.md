---
id: ks:docs/service/configuration/code-config-secrets-README.md
title: "Config & Secrets Usage (Code Maintainers)"
summary: "How platform and bundle code should read configuration and secrets, with dot‑path keys and sidecar usage."
tags: ["service", "configuration", "secrets", "developer", "code", "bundle"]
keywords: ["get_settings", "get_secret", "dot paths", "secrets sidecar", "assembly.yaml", "secrets.yaml"]
see_also:
  - ks:docs/service/configuration/service-config-README.md
  - ks:docs/service/configuration/bundle-configuration-README.md
  - ks:docs/service/configuration/descriptor-plain-config-README.md
  - ks:docs/service/cicd/assembly-descriptor-README.md
  - ks:docs/service/cicd/secrets-descriptor-README.md
---
# Config & Secrets Usage (Code Maintainers)

This document is for **platform and bundle maintainers**. It defines **how to read
configuration and secrets in code** and what to avoid.

## 1) Rules of thumb

- **Do not use `os.getenv()` directly** for secrets in platform or bundle code.
- **Use `get_settings()`** for non‑secret config values.
- **Use `get_plain()` / `read_plain()`** when code must inspect mounted
  `assembly.yaml` or `bundles.yaml` directly.
- **Use `get_secret()`** for secrets (keys, tokens, passwords).
- Secrets are stored in the secrets sidecar using **dot‑path keys** (see below).
- Env vars are **legacy compatibility only** and should not be referenced directly in new code.

## 2) Secrets (dot‑path keys)

Secrets are published to the sidecar using dot‑path keys (e.g. `services.openai.api_key`).
Use these keys in code:

```
services.openai.api_key
services.google.api_key        # Gemini
services.anthropic.api_key
services.anthropic.claude_code_key
services.brave.api_key
services.openrouter.api_key
services.huggingface.api_key
services.stripe.secret_key
services.stripe.webhook_secret
services.git.http_token
services.git.http_user
auth.cognito.client_secret
aws.access_key_id
aws.secret_access_key
```

### Example (platform/bundle code)

```python
from kdcube_ai_app.apps.chat.sdk.config import get_secret

api_key = get_secret("services.openai.api_key")
```

## 3) Non‑secret config

For config values (tenant/project, bundle paths, limits, model routing, etc.):

```python
from kdcube_ai_app.apps.chat.sdk.config import get_settings

settings = get_settings()
tenant = settings.TENANT
```

If code needs the descriptor value itself rather than the rendered env-backed
runtime setting, use:

```python
from kdcube_ai_app.apps.chat.sdk.config import read_plain

workspace_type = read_plain("storage.workspace.type", default="custom")
default_bundle_id = read_plain("b:default_bundle_id")
```

Namespace rules:

- no prefix or `a:` -> `assembly.yaml`
- `b:` -> `bundles.yaml`

See:
- [docs/service/configuration/descriptor-plain-config-README.md](descriptor-plain-config-README.md)

## 4) Where config comes from

- **`assembly.yaml`** holds non‑secret config (platform/frontend/infra).
- **`bundles.yaml`** holds bundle definitions and non‑secret bundle config.
- **`secrets.yaml`** holds sensitive secrets (LLM keys, tokens, passwords).
- **`bundles.secrets.yaml`** holds bundle‑specific secrets.
- **`gateway.yaml`** can be used to render `GATEWAY_CONFIG_JSON`.
- The CLI merges and stages these at install time and injects secrets via the sidecar.
- Secrets are **always** addressed via dot‑path keys; bundle config stays nested.

See:
- [assembly-descriptor-README.md](../cicd/assembly-descriptor-README.md)
- [secrets-descriptor-README.md](../cicd/secrets-descriptor-README.md)

## 5) Adding a new secret

When you add a new secret:

1) Add it to `deployment/secrets.yaml` (dot‑path key).
2) If you must support legacy env vars, add an alias mapping in
   `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/config.py` (`_SECRET_ALIASES`).
3) Use `get_secret("dot.path.key")` in code.
