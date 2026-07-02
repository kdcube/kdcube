---
id: repo:kdcube-ai-app/app/ai-app/docs/service/cicd/descriptors-README.md
title: "Deployment Descriptors Overview"
summary: "Overview of the deployment descriptor set, what each file owns, and how descriptor authority differs between current local CLI runs, direct local service runs, and AWS deployment."
tags: ["service", "cicd", "descriptors", "configuration"]
keywords: ["deployment descriptors", "platform descriptor ownership", "local versus aws authority", "assembly bundles gateway secrets files", "descriptor-driven deployment contract", "runtime configuration entry files"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/cli-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/runtime-read-write-contract-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/runtime-configuration-and-secrets-store-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/assembly-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundles-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundles-secrets-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/secrets-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/gateway-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/service-runtime-configuration-mapping-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/economics/economics-descriptor-README.md
---
# Descriptors

These are the supported deployment descriptors:

- `assembly.yaml`
- `bundles.yaml`
- `bundles.secrets.yaml`
- `secrets.yaml`
- `gateway.yaml`
- `economics.yaml`

Descriptor documentation lives in `docs/configuration/`, one page per
descriptor. Each page now includes:

- direct runtime access API (`get_settings()`, `get_plain()`, `get_secret()`) where applicable
- supported env vars
- YAML path mapping
- mode-specific authority notes

For the cross-helper one-page contract, start with:

- [runtime-read-write-contract-README.md](../../configuration/runtime-read-write-contract-README.md)
- [runtime-configuration-and-secrets-store-README.md](../../configuration/runtime-configuration-and-secrets-store-README.md)

Descriptor pages:

- [runtime-read-write-contract-README.md](../../configuration/runtime-read-write-contract-README.md)
- [runtime-configuration-and-secrets-store-README.md](../../configuration/runtime-configuration-and-secrets-store-README.md)
- [assembly-descriptor-README.md](../../configuration/assembly-descriptor-README.md)
- [bundles-descriptor-README.md](../../configuration/bundles-descriptor-README.md)
- [bundles-secrets-descriptor-README.md](../../configuration/bundles-secrets-descriptor-README.md)
- [secrets-descriptor-README.md](../../configuration/secrets-descriptor-README.md)
- [gateway-descriptor-README.md](../../configuration/gateway-descriptor-README.md)
- [economics-descriptor-README.md](../../economics/economics-descriptor-README.md)

This page explains the part that differs by run mode: what the descriptors mean,
which files are authoritative, and which `assembly.yaml` sections matter.

## Three supported run modes

### 1. CLI local compose (`kdcube`)

This is the `docker compose` path started by the CLI installer.

Authority:

- `assembly.yaml` and `gateway.yaml` are staged into `workdir/config`
- `bundles.yaml` is staged into `workdir/config`
- `bundles.secrets.yaml` and `secrets.yaml` are used only when the chosen
  secrets provider needs file-backed authority

Runtime contract:

- proc, ingress, and metrics read `/config/assembly.yaml`
- proc, ingress, and metrics read `/config/bundles.yaml`
- proc usually seeds the bundle registry from `/config/bundles.yaml`

`assembly.paths.*` relevance:

- relevant
- these keys drive host directory mounts and runtime host-path settings

Typical use:

- local path bundles under a host root mounted as `/bundles`
- managed bundles resolved locally into `/managed-bundles`
- local file-backed bundle storage and exec workspace

When only `bundles.yaml`, `bundles.secrets.yaml`, or mounted bundle source
changes, apply the bundle reload path instead of refreshing the platform
runtime:

- [cli-README.md#bundle-reload-flow](cli-README.md#bundle-reload-flow)

### 2. Direct local service run

This is the host-run path where you start proc or ingress directly with
`python .../web_app.py`.

Authority:

- the descriptor files you point the process to explicitly

Runtime contract:

- `ASSEMBLY_YAML_DESCRIPTOR_PATH` points to `assembly.yaml`
- `BUNDLES_YAML_DESCRIPTOR_PATH` points to `bundles.yaml`
- proc can seed or reset directly from the bundle descriptor authority

`assembly.paths.*` relevance:

- optional
- only relevant if the process itself must use those host directories
- these keys are not mount instructions in this mode

Typical use:

- local proc debug against a real descriptor set
- cron or bundle-prop debug using `read_plain(...)`
- direct bundle code iteration without `docker compose`

### 3. AWS deployment

This is the ECS / cloud deployment path.

Authority:

- descriptors are deployment input
- in `aws-sm` mode, live deployment-scoped bundle authority is:
  - `bundles-meta`
  - `bundles/<bundle_id>/descriptor`
  - `bundles/<bundle_id>/secrets`

Runtime contract:

- `/config/assembly.yaml` and `/config/bundles.yaml` may still be mounted as
  runtime-readable snapshots
- those files are not the live deployment-scoped bundle authority in `aws-sm`

`assembly.paths.*` relevance:

- not relevant
- AWS deployment owns storage and mounts via ECS/EFS/S3/Terraform, not via
  local host paths from `assembly.yaml`

Typical use:

- bundles defined from git only
- no local host-path bundles
- storage and runtime topology controlled by the deployment stack

## Descriptor authority by file

| Descriptor | CLI local compose | Direct local service run | AWS deployment |
|---|---|---|---|
| `assembly.yaml` | staged into `workdir/config/assembly.yaml`; runtime-readable | pointed to by `ASSEMBLY_YAML_DESCRIPTOR_PATH` | deployment input; runtime-readable snapshot |
| `bundles.yaml` | staged into `workdir/config/bundles.yaml`; often live local authority | pointed to by `BUNDLES_YAML_DESCRIPTOR_PATH`; proc can seed directly from it | deployment input and export format; in `aws-sm` it is not the live deploy-scoped authority |
| `bundles.secrets.yaml` | file authority only in `secrets-file` mode | file authority only in `secrets-file` mode | export/import format; in `aws-sm` live bundle secrets authority is grouped AWS SM docs |
| `secrets.yaml` | file authority only in `secrets-file` mode; otherwise installer input | file authority only in `secrets-file` mode | deployment input; actual live authority depends on provider |
| `gateway.yaml` | staged and rendered into runtime gateway config | pointed to explicitly if the process should load/render it | deployment input rendered into runtime config |
| `economics.yaml` | staged into `workdir/config/economics.yaml`; seeded into the project economics tables and runtime-readable | pointed to by `ECONOMICS_YAML_DESCRIPTOR_PATH` | deployment input on EFS `/config`; seeded and runtime-readable |

## Rules that should stay stable

- `assembly.yaml` is for platform-level non-secret configuration
- `bundles.yaml` is for bundle registry and non-secret bundle config
- `bundles.secrets.yaml` is for bundle secrets
- `secrets.yaml` is for platform/global secrets
- `gateway.yaml` is for gateway config only
- `economics.yaml` is for per tenant/project economics defaults (seeded into the
  economics tables; reservation floor read at runtime)
- local host paths belong only to local run modes
- do not copy local `assembly.paths.*` values into AWS descriptors

## Auth Descriptor Modes

`auth.type` describes the deployment/routing shape. `auth.connection_hub`
selects the platform authority/provider. Provider details live under
`connection-hub@1-0.config.authority_registry`.

| `auth.type` | `auth.connection_hub.provider_id` | Provider behavior |
|---|---|---|
| `simple` | n/a | SimpleIDP token registry. |
| `cognito` | `cognito` | Cognito or multi-Cognito JWT validation using the selected Connection Hub provider. |
| `delegated` | `cognito` | Proxy-login/delegated deployment shape with Cognito backend validation from the selected provider. |
| `bundle` | bundle-session provider id | Bundle/front shell validates external identity and issues platform-recognized bundle session cookies. |

Bundle session auth requires the platform/global secret
`services.session_token.secret` in `secrets.yaml` or the configured secret
provider. See [Bundle Session Auth](../auth/bundle-session-auth-README.md).

For mixed runtime scenes, keep the selector in `assembly.yaml` and place the
Cognito trust list in `bundles.yaml`:

```yaml
# assembly.yaml
auth:
  type: cognito
  connection_hub:
    bundle_id: connection-hub@1-0
    authority_id: kdcube.platform
    provider_id: cognito
```

```yaml
# bundles.yaml
items:
  - id: connection-hub@1-0
    config:
      authority_registry:
        authorities:
          kdcube.platform:
            platform: true
            providers:
              cognito:
                type: multi_cognito
                authenticator:
                  region: eu-west-1
                  user_pool_id: eu-west-1_PRIMARY
                  app_client_id: primary-client
                  trusted_providers:
                    - alias: primary
                      kind: cognito
                      region: eu-west-1
                      user_pool_id: eu-west-1_PRIMARY
                      app_client_id: primary-client
                    - alias: peer
                      kind: cognito
                      region: eu-west-1
                      user_pool_id: eu-west-1_PEER
                      app_client_id: peer-client
```

The verifier selects a provider from the token `iss` and `client_id`/`aud`
claims, then performs normal JWKS verification for that provider.

## Where to continue

- Descriptor fields and examples:
  - [assembly-descriptor-README.md](../../configuration/assembly-descriptor-README.md)
  - [bundles-descriptor-README.md](../../configuration/bundles-descriptor-README.md)
  - [bundles-secrets-descriptor-README.md](../../configuration/bundles-secrets-descriptor-README.md)
  - [secrets-descriptor-README.md](../../configuration/secrets-descriptor-README.md)
  - [gateway-descriptor-README.md](../../configuration/gateway-descriptor-README.md)
  - [economics-descriptor-README.md](../../economics/economics-descriptor-README.md)
- Runtime env and descriptor mapping:
  - [service-runtime-configuration-mapping-README.md](../../configuration/service-runtime-configuration-mapping-README.md)
