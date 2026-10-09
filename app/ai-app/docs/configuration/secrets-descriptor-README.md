---
id: repo:kdcube-ai-app/app/ai-app/docs/configuration/secrets-descriptor-README.md
title: "Platform Secrets Descriptor"
summary: "Canonical platform and user secret configuration in secrets.yaml, deployment-bundle secrets in bundles.secrets.yaml, and the same portable shape across file, Host Vault, and cloud providers."
tags: ["service", "configuration", "platform", "secrets", "deployment", "descriptor"]
keywords: ["platform global secrets", "model provider credentials", "git transport credentials", "identity provider secrets", "cloud credentials", "email credentials", "local secrets file mode", "aws secrets manager global secrets", "canonical secret keys", "deployment secret inventory", "bundle service key override", "per-bundle api key"]
updated_at: 2026-09-06
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/descriptors-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundles-secrets-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/service-runtime-configuration-mapping-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/runtime-configuration-and-secrets-store-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/delegated-management-service-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/secrets/secret-management-cli-README.md
---
# Platform Secrets Descriptor

`secrets.yaml` is the portable private descriptor for platform and user
secrets. It has exactly two top-level namespaces:

```yaml
platform:
  services: {}
  infra: {}
  auth: {}
users: {}
```

Deployment-bundle values live in `bundles.secrets.yaml`. The selected runtime
provider may store the same logical keys in files, Host Vault, AWS Secrets
Manager, or memory, but it does not change their canonical identities.

An empty string or `null` is an unconfigured placeholder, not a stored secret.
Use the delete operation to remove a live value; delegated management rejects
an empty replacement so file, host-vault, and cloud providers retain the same
observable contract.

Typical platform keys:

- `platform.services.openai.api_key`
- `platform.services.google.api_key`
- `platform.services.anthropic.api_key`
- `platform.services.git.http_token`
- `platform.auth.cognito.client_secret`
- `platform.aws.access_key_id`
- `platform.aws.secret_access_key`


> **File backend:** app (bundle) and per-user secrets now live in the secrets
> folder (`config/secrets/<bundle>/<key>.json`,
> `config/secrets/<bundle>/users/<user>/<key>.json`), not in the YAML. See
> [File backend: app and per-user secrets in folders](../service/secrets/secrets-service-README.md#file-backend-app-and-per-user-secrets-in-folders)
> for the layout and the one-time move.

## Direct runtime contract from this descriptor

### Supported access APIs

| Need | API | Notes |
|---|---|---|
| platform secret | `await get_secret("platform.canonical.key")` | `platform.` is required |
| current bundle secret | `await get_secret("b:relative.key")` | explicit bundle-relative lookup |
| user secret | platform-owned user-secret APIs | exact user identity is part of the provider key |

### File-resolution env vars

| Env var | Meaning | Modes |
|---|---|---|
| `GLOBAL_SECRETS_YAML` | Explicit file URI or path for `secrets.yaml` in `secrets-file` mode | direct local service run |
| `HOST_SECRETS_YAML_DESCRIPTOR_PATH` | Host file staged into `/config/secrets.yaml` by the CLI installer | CLI local compose |

### Canonical secret keys and settings projections

The second column names compatibility settings fields used by platform
bootstrap code. These names are not accepted as secret keys. Runtime calls use
the canonical first column.

| Canonical key | Settings projection name(s) | Primary API |
|---|---|---|
| `platform.services.openai.api_key` | `OPENAI_API_KEY` | `get_secret(...)` |
| `platform.services.anthropic.api_key` | `ANTHROPIC_API_KEY` | `get_secret(...)` |
| `platform.services.anthropic.claude_code_key` | `CLAUDE_CODE_KEY` | `get_secret(...)` |
| `platform.services.brave.api_key` | `BRAVE_API_KEY` | `get_secret(...)` |
| `platform.services.brave.api_comm_mid_key` | `BRAVE_API_COMM_MID_KEY` | `get_secret(...)` |
| `platform.services.google.api_key` | `GOOGLE_API_KEY`, `GEMINI_API_KEY` | `get_secret(...)` |
| `platform.services.git.http_token` | `GIT_HTTP_TOKEN` | `get_secret(...)` |
| `platform.services.git.http_user` | `GIT_HTTP_USER` | `get_secret(...)` |
| `platform.services.openrouter.api_key` | `OPENROUTER_API_KEY` | `get_secret(...)` |
| `platform.services.serpapi.api_key` | `SERPAPI_API_KEY` | `get_secret(...)` |
| `platform.services.stripe.secret_key` | `STRIPE_SECRET_KEY`, `STRIPE_API_KEY` | `get_secret(...)` |
| `platform.services.stripe.webhook_secret` | `STRIPE_WEBHOOK_SECRET` | `get_secret(...)` |
| `platform.services.huggingface.api_key` | `HUGGING_FACE_KEY`, `HUGGINGFACE_API_KEY`, `HUGGING_FACE_API_TOKEN` | `get_secret(...)` |
| `platform.services.firecrawl.api_key` | `FIRECRAWL_API_KEY` | `get_secret(...)` |
| `platform.services.federated_token.secret` | none | `get_secret(...)` |
| `platform.services.session_token.secret` | none | `get_secret(...)` |
| `platform.services.email.password` | `EMAIL_PASSWORD` | `get_secret(...)` |
| `platform.auth.oidc.admin_email` | `OIDC_SERVICE_USER_EMAIL` | `get_secret(...)` |
| `platform.auth.oidc.admin_username` | `OIDC_SERVICE_ADMIN_USERNAME` | `get_secret(...)` |
| `platform.auth.oidc.admin_password` | `OIDC_SERVICE_ADMIN_PASSWORD` | `get_secret(...)` |

## What it is not for

Do not put bundle-scoped secrets here if they belong to a specific bundle.

Use `bundles.secrets.yaml` for bundle secrets.

The `platform.services.*` keys listed above can be overridden per bundle via
`bundles.secrets.yaml` using the same key path.
When bundle code wants bundle-first service-key lookup, use the explicit pattern
`await get_secret("b:services.<name>.<key>") or await get_secret("platform.services.<name>.<key>")`.
See [bundles-secrets-descriptor-README.md](bundles-secrets-descriptor-README.md)
for the override mechanism and the list of overridable keys.

`platform.services.federated_token.secret` and
`platform.services.session_token.secret` are
platform-wide service secrets. Keep each value consistent across ingress/proc
workers.

## Authority by mode

### CLI local compose

If `assembly.secrets.provider == secrets-file`:

- `secrets.yaml` can be mounted as the live file authority
- on POSIX hosts, the CLI creates, stages, applies, and exports canonical
  secret descriptors with owner-only `0600` permissions
- runtime atomic updates preserve `0600` instead of recreating a
  group/world-readable file through the process umask

Otherwise:

- it is installer input used to populate the active runtime secrets provider

An administrator can reconstruct selected keys or the whole provider
inventory, including every current user value, through the browser-approved
`kdcube secrets export` ceremony documented in
[Manage KDCube Secrets](../service/secrets/secret-management-cli-README.md#human-only-export).

Secret inventory is provider-derived. `.__keys` is a virtual compatibility
selector used by the runtime and must not be authored, staged, or mutated in a
descriptor. Legacy metadata may be read only as a hint whose candidates are
scope-checked and verified against current live values; stale entries are not
returned.

For an opt-in local host vault, set `secrets.service.backend: host-vault`. Keep
`secrets.provider: secrets-file` while the CLI shadow-stages and verifies the
existing values. `kdcube secrets backend host-vault activate` performs the
explicit local-Compose cutover to `secrets-service`, verifies reads in ingress
and proc, and restores file authority on an ordinary activation failure. A
durable, non-secret pending marker blocks ordinary startup after an interrupted
switch; `kdcube secrets backend host-vault recover --yes` recreates and verifies
the retained file-backed path before clearing it.
The descriptor also declares the vault address, TLS server name, and an
absolute deployment-identity directory outside the workdir. Set
`platform.services.proc.exec.py_code_exec_network_mode: auto` so a trusted
Docker-in-Docker supervisor shares the processor's private service networks;
the split generated-code executor remains networkless. The values remain
server-side; the descriptor contains paths and topology only. See
[Host Vault for Provider Secrets](../service/secrets/host-vault-README.md). The
older `kdcube secrets host-vault ...` spelling remains a compatibility alias.

### Direct local service run

If `SECRETS_PROVIDER=secrets-file`:

- point the process to the file with `GLOBAL_SECRETS_YAML`
- the file is the live authority

### AWS deployment

In `aws-sm`:

- `secrets.yaml` is deployment input
- live secret authority is AWS Secrets Manager, not the YAML file

The synchronized provider identities retain the same namespaces:
`platform.*`, `bundles.<bundle-id>.secrets.*`, and `users.<user-id>.*`.

## Migrate Existing Descriptors

Runtime reads do not infer an unqualified platform key. Before loading source
that uses this contract, migrate an existing private descriptor pair:

```bash
kdcube secrets namespace migrate \
  --workdir ~/.kdcube/kdcube-runtime/demo-tenant__demo-project \
  --dry-run

kdcube secrets namespace migrate \
  --workdir ~/.kdcube/kdcube-runtime/demo-tenant__demo-project \
  --yes
```

The migration moves every former top-level platform root under `platform` and
moves any misplaced `bundles.<id>.secrets.*` values into
`bundles.secrets.yaml`. It preserves `users`, refuses conflicting duplicate
values without writing, performs atomic owner-only file replacement, and
reports identities and counts without rendering values.

## Local isolation boundary

Owner-only mode is local filesystem hygiene. It prevents another OS account
from reading the descriptor, but it does not isolate a process that already
runs with the descriptor owner's account authority.

In local Docker Compose, trusted KDCube services read the descriptor through
the `/config` bind mount. For split isolated execution, proc supplies the
descriptor-backed settings and secrets to the trusted supervisor container.
The sibling container that executes generated Python receives no `/config`
mount, secret-descriptor payload, secret-provider material, or Docker socket.
See [README-iso-runtime.md](../exec/README-iso-runtime.md) for that boundary.

## Practical rule

- use `secrets.yaml` for `platform.*` and `users.*` secrets
- use `bundles.secrets.yaml` for bundle secrets
- treat YAML files as live authority only in `secrets-file` mode
- use the same literal pair for complete administrator export and bootstrap
