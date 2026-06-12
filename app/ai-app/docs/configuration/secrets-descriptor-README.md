---
id: repo:kdcube-ai-app/app/ai-app/docs/configuration/secrets-descriptor-README.md
title: "Platform Secrets Descriptor"
summary: "Platform-level secret configuration in secrets.yaml: model provider credentials, git and auth secrets, and other global deployment secrets across local file mode and AWS. Service keys under services.* can be overridden per bundle via bundles.secrets.yaml."
tags: ["service", "configuration", "platform", "secrets", "deployment", "descriptor"]
keywords: ["platform global secrets", "model provider credentials", "git transport credentials", "identity provider secrets", "cloud credentials", "email credentials", "local secrets file mode", "aws secrets manager global secrets", "canonical secret keys", "deployment secret inventory", "bundle service key override", "per-bundle api key"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/descriptors-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundles-secrets-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/service-runtime-configuration-mapping-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/runtime-configuration-and-secrets-store-README.md
---
# Platform Secrets Descriptor

`secrets.yaml` stores platform/global secrets.

Typical keys:

- `services.openai.api_key`
- `services.google.api_key`
- `services.anthropic.api_key`
- `services.git.http_token`
- `auth.cognito.client_secret`
- `aws.access_key_id`
- `aws.secret_access_key`

## Direct runtime contract from this descriptor

### Supported access APIs

| Need | API | Notes |
|---|---|---|
| platform/global secret | `await get_secret("canonical.key")` | canonical dot path is the stable contract |
| legacy env alias | `await get_secret("canonical.key")` | also accepts the documented env aliases below |

### File-resolution env vars

| Env var | Meaning | Modes |
|---|---|---|
| `GLOBAL_SECRETS_YAML` | Explicit file URI or path for `secrets.yaml` in `secrets-file` mode | direct local service run |
| `HOST_SECRETS_YAML_DESCRIPTOR_PATH` | Host file staged into `/config/secrets.yaml` by the CLI installer | CLI local compose |

### Canonical secret keys and accepted env aliases

| Canonical key | Accepted env alias(es) | Primary API |
|---|---|---|
| `services.openai.api_key` | `OPENAI_API_KEY` | `get_secret(...)` |
| `services.anthropic.api_key` | `ANTHROPIC_API_KEY` | `get_secret(...)` |
| `services.anthropic.claude_code_key` | `CLAUDE_CODE_KEY` | `get_secret(...)` |
| `services.brave.api_key` | `BRAVE_API_KEY` | `get_secret(...)` |
| `services.brave.api_comm_mid_key` | `BRAVE_API_COMM_MID_KEY` | `get_secret(...)` |
| `services.google.api_key` | `GOOGLE_API_KEY`, `GEMINI_API_KEY` | `get_secret(...)` |
| `services.git.http_token` | `GIT_HTTP_TOKEN` | `get_secret(...)` |
| `services.git.http_user` | `GIT_HTTP_USER` | `get_secret(...)` |
| `services.openrouter.api_key` | `OPENROUTER_API_KEY` | `get_secret(...)` |
| `services.serpapi.api_key` | `SERPAPI_API_KEY` | `get_secret(...)` |
| `services.stripe.secret_key` | `STRIPE_SECRET_KEY`, `STRIPE_API_KEY` | `get_secret(...)` |
| `services.stripe.webhook_secret` | `STRIPE_WEBHOOK_SECRET` | `get_secret(...)` |
| `services.huggingface.api_key` | `HUGGING_FACE_KEY`, `HUGGINGFACE_API_KEY`, `HUGGING_FACE_API_TOKEN` | `get_secret(...)` |
| `services.firecrawl.api_key` | `FIRECRAWL_API_KEY` | `get_secret(...)` |
| `services.federated_token.secret` | none | `get_secret(...)` |
| `services.session_token.secret` | none | `get_secret(...)` |
| `services.email.password` | `EMAIL_PASSWORD` | `get_secret(...)` |
| `auth.oidc.admin_email` | `OIDC_SERVICE_USER_EMAIL` | `get_secret(...)` |
| `auth.oidc.admin_username` | `OIDC_SERVICE_ADMIN_USERNAME` | `get_secret(...)` |
| `auth.oidc.admin_password` | `OIDC_SERVICE_ADMIN_PASSWORD` | `get_secret(...)` |

## What it is not for

Do not put bundle-scoped secrets here if they belong to a specific bundle.

Use `bundles.secrets.yaml` for bundle secrets.

The `services.*` keys listed above can be overridden per bundle via
`bundles.secrets.yaml` using the same key path.
When bundle code wants bundle-first service-key lookup, use the explicit pattern
`await get_secret("b:services.<name>.<key>") or await get_secret("services.<name>.<key>")`.
See [bundles-secrets-descriptor-README.md](bundles-secrets-descriptor-README.md)
for the override mechanism and the list of overridable keys.

`services.federated_token.secret` and `services.session_token.secret` are
platform-wide service secrets. Keep each value consistent across ingress/proc
workers.

## Authority by mode

### CLI local compose

If `assembly.secrets.provider == secrets-file`:

- `secrets.yaml` can be mounted as the live file authority

Otherwise:

- it is installer input used to populate the active runtime secrets provider

### Direct local service run

If `SECRETS_PROVIDER=secrets-file`:

- point the process to the file with `GLOBAL_SECRETS_YAML`
- the file is the live authority

### AWS deployment

In `aws-sm`:

- `secrets.yaml` is deployment input
- live secret authority is AWS Secrets Manager, not the YAML file

## Practical rule

- use `secrets.yaml` for platform/global secrets
- use `bundles.secrets.yaml` for bundle secrets
- treat YAML files as live authority only in `secrets-file` mode
