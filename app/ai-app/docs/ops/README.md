# Ops Overview

This section is the **operations entry point** for KDCube: deployment options, service topology, and near‑term plans (ECS/EKS).

## Deployment options

See `deployment/index-README.md` for a quick map. Key options:

- **DevEnv (run services locally)**: `deployment/devenv/`
- **Local infra only**: `deployment/docker/local-infra-stack/`
- **All‑in‑one compose** (everything local): `deployment/docker/all_in_one_kdcube/`
- **Custom UI + managed infra**: `deployment/docker/custom-ui-managed-infra/`

## Service topology (current)

```mermaid
flowchart TB
  user[Users / Client Apps]
  user --> ui[Web UI]
  user --> sse[SSE / REST]

  subgraph edge[Edge]
    proxy[OpenResty]
    proxylogin["ProxyLogin (optional)"]
  end

  ui --> proxy
  sse --> proxy
  proxylogin --> proxy

  subgraph platform[KDCube Platform]
    ingress[Chat Ingress]
    proc[Chat Processor]
    metrics[Metrics Service]
  end

  proxy --> ingress
  proxy --> proc

  ingress --> redis[(Redis)]
  proc --> redis
  metrics --> redis

  ingress --> pg[(Postgres)]
  proc --> pg

  ingress --> av[ClamAV]

  proc --> exec["Exec runtime (Docker/Fargate)"]
  proc --> bundles["Bundles (mounted or git)"]

  ingress --> storage["(KDCUBE_STORAGE_PATH)"]
  proc --> storage

  storage --> s3["S3 (optional)"]
```

**Storage**
- `KDCUBE_STORAGE_PATH` can point to **local FS** or **S3**.
- For S3 usage and bucket layout, see `docs/ops/s3.md`.

## Config entry points

- Service configuration: `docs/service/service-config-README.md`
- Gateway configuration and capacity logic: `docs/service/gateway-README.md`
- Metrics & autoscaling: `docs/service/scale/metric-server-README.md` and `docs/service/scale/metrics-README.md`

## Roadmap (ops‑facing)

- **ECS**: target runtime for prod (managed infra, autoscaling, task roles).
- **EKS**: optional Kubernetes path (for teams already on k8s).
- **Bundle‑from‑git**: remove “fat image” requirement for processors.
- **Fargate exec**: complete adapter for isolated tool/code execution.

