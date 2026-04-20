---
id: ks:docs/service/cicd/descriptors-README.md
title: "Descriptors"
summary: "Descriptor files, their authority model, and how they differ between CLI compose, direct local service runs, and AWS deployment."
tags: ["service", "cicd", "descriptors", "configuration"]
keywords: ["assembly.yaml", "bundles.yaml", "bundles.secrets.yaml", "secrets.yaml", "gateway.yaml", "CLI", "AWS", "direct run"]
see_also:
  - ks:docs/service/configuration/assembly-descriptor-README.md
  - ks:docs/service/configuration/bundles-descriptor-README.md
  - ks:docs/service/configuration/bundles-secrets-descriptor-README.md
  - ks:docs/service/configuration/secrets-descriptor-README.md
  - ks:docs/service/configuration/gateway-descriptor-README.md
  - ks:docs/service/configuration/service-config-README.md
---
# Descriptors

These are the supported deployment descriptors:

- `assembly.yaml`
- `bundles.yaml`
- `bundles.secrets.yaml`
- `secrets.yaml`
- `gateway.yaml`

Descriptor documentation lives in `docs/service/configuration/`, one page per
descriptor. Each page now includes:

- direct runtime access API (`get_settings()`, `get_plain()`, `get_secret()`) where applicable
- supported env vars
- YAML path mapping
- mode-specific authority notes

Descriptor pages:

- [assembly-descriptor-README.md](../configuration/assembly-descriptor-README.md)
- [bundles-descriptor-README.md](../configuration/bundles-descriptor-README.md)
- [bundles-secrets-descriptor-README.md](../configuration/bundles-secrets-descriptor-README.md)
- [secrets-descriptor-README.md](../configuration/secrets-descriptor-README.md)
- [gateway-descriptor-README.md](../configuration/gateway-descriptor-README.md)

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

## Rules that should stay stable

- `assembly.yaml` is for platform-level non-secret configuration
- `bundles.yaml` is for bundle registry and non-secret bundle config
- `bundles.secrets.yaml` is for bundle secrets
- `secrets.yaml` is for platform/global secrets
- `gateway.yaml` is for gateway config only
- local host paths belong only to local run modes
- do not copy local `assembly.paths.*` values into AWS descriptors

## Where to continue

- Descriptor fields and examples:
  - [assembly-descriptor-README.md](../configuration/assembly-descriptor-README.md)
  - [bundles-descriptor-README.md](../configuration/bundles-descriptor-README.md)
  - [bundles-secrets-descriptor-README.md](../configuration/bundles-secrets-descriptor-README.md)
  - [secrets-descriptor-README.md](../configuration/secrets-descriptor-README.md)
  - [gateway-descriptor-README.md](../configuration/gateway-descriptor-README.md)
- Runtime env and descriptor mapping:
  - [service-config-README.md](../configuration/service-config-README.md)
