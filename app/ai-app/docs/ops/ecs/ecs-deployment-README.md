---
id: ks:docs/ops/ecs/ecs-deployment-README.md
title: "ECS Deployment"
summary: "ECS deployment templates: env files for ingress, proc, and metrics tasks."
tags: ["ops", "ecs", "deployment", "templates", "env", "tasks"]
keywords: ["task definition", "env.template", "ingress", "proc", "metrics", "CloudWatch", "EFS", "AWS"]
see_also:
  - ks:docs/ops/ec2/dockercompose-deployment-README.md
  - ks:docs/ops/ecs/components/metric-server-README.md
  - ks:docs/ops/ops-overview-README.md
---
# ECS Deployment Templates

This folder contains **environment templates** for ECS tasks/services.
They are intended as starting points for task definitions or parameter stores.

## Templates
- `deployment/ecs/ingress/env.template` — Ingress (SSE/API entrypoint).
- `deployment/ecs/proc/env.template` — Processor (queue worker + integrations API).
- `deployment/ecs/metrics/env.template` — Metrics service (CloudWatch/Prometheus export).
- `deployment/ecs/frontend/env.template` — UI service (runtime config injection).

## Descriptor flow (ECS + local/compose)

```mermaid
flowchart TB
  subgraph Inputs["Descriptors"]
    A[assembly.yaml]
    B[bundles.yaml]
    BS[bundles.secrets.yaml<br/>(local/dev)]
    S[secrets.yaml]
    G[gateway.yaml]
  end

  subgraph ECS["ECS / Terraform / CD"]
    T[Task definitions + params]
    SM[Secrets Manager / SSM]
    S3[Frontend config (S3) or task env]
  end

  subgraph Local["Local / one-node (CLI)"]
    ENV[.env.* in workdir]
    MOUNT[/config/bundles.yaml mount]
    SC[secrets sidecar injection]
  end

  A --> T
  B --> T
  G --> T
  A --> S3
  S --> SM

  A --> ENV
  G --> ENV
  B --> MOUNT
  S --> SC
  BS --> SC
```

## Required shared settings
All services must set:
- `REDIS_URL` (ingress/proc/metrics)
- `GATEWAY_CONFIG_JSON` (must include `tenant` + `project`)
- `GATEWAY_COMPONENT` (`ingress` | `proc` | `metrics`)

## AWS runtime (recommended on ECS)
If running on ECS/EC2 with IAM roles:
- `AWS_REGION` or `AWS_DEFAULT_REGION`
- `AWS_EC2_METADATA_DISABLED=false`
- `NO_PROXY=169.254.169.254,localhost,127.0.0.1` (only if proxy is used)

## UI Runtime Config (ECS)
The UI container supports runtime config injection. Use **one** of:

- `FRONTEND_CONFIG_JSON` — JSON string for `config.json`
- `FRONTEND_CONFIG_S3_URL` — S3 or HTTPS URL to fetch on startup

Use one of them in the frontend task definition.

**Example (inline JSON):**
```
FRONTEND_CONFIG_JSON='{"auth":{"authType":"delegated","apiBase":"/auth/"},"tenant":"<TENANT>","project":"<PROJECT>","routesPrefix":"/chatbot/api"}'
```

**Example (S3):**
```
FRONTEND_CONFIG_S3_URL=s3://<bucket>/<path>/config.json
AWS_REGION=eu-west-1
AWS_EC2_METADATA_DISABLED=false
NO_PROXY=169.254.169.254,localhost,127.0.0.1
```

## Component docs
- Metrics scheduled task example: [docs/ops/ecs/components/metric-server-README.md](components/metric-server-README.md)

## Bundles from Git (proc)

For git‑defined bundles, ensure:

- `git` binary is available in the proc image (included by default).
- `BUNDLE_GIT_RESOLUTION_ENABLED=1`
- `BUNDLE_GIT_REDIS_LOCK=1` (each replica pulls once)
- `AGENTIC_BUNDLES_JSON` can point to a JSON/YAML file path mounted into the task (recommended for readability).
  Mount it to `/config/bundles.yaml` and set:
  ```
  AGENTIC_BUNDLES_JSON=/config/bundles.yaml
  ```

**Rule:** set `subdir` to the **parent bundles directory** and use `module: "<bundle_folder>.entrypoint"`.

**Option A — EFS (recommended for git pulls)**

- Mount EFS to `/bundles`
- Use an EFS Access Point with:
  - `posix_user.uid = 1000`
  - `posix_user.gid = 1000`
- Set:
  ```
  AGENTIC_BUNDLES_ROOT=/bundles
  BUNDLE_GIT_RESOLUTION_ENABLED=1
  ```

**Bundle shared local storage (optional)**

If you use bundles that expose `ks:` (doc/knowledge or any shared local data),
mount a shared local store and set:

```
BUNDLE_STORAGE_ROOT=/bundle-storage
```

Recommended:
- Use the same EFS file system (or a separate EFS access point).
- Mount it to `/bundle-storage`.

**Private repos (SSH):**
Provide these envs and mount the key/known_hosts into the container:

```
GIT_SSH_KEY_PATH=/run/secrets/git_ssh_key
GIT_SSH_KNOWN_HOSTS=/run/secrets/git_known_hosts
GIT_SSH_STRICT_HOST_KEY_CHECKING=yes
```

**Bundles root:**

Set `AGENTIC_BUNDLES_ROOT=/bundles` in the proc task definition.  
Avoid setting `HOST_BUNDLES_PATH` in ECS unless the path is valid inside the container.

## Notes
- These templates intentionally use **placeholders**. Replace them in your task
  definition or inject via parameter store/secrets manager.
