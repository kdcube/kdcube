# KDCube Kubernetes Setup

This directory contains the Helm-based Kubernetes deployment for KDCube.

The recommended deployment flow is descriptor-driven:
- keep environment-specific configuration in `descriptors/*.yaml`
- install infrastructure charts first
- run the Postgres bootstrap job
- install the platform chart with descriptor overlays

Everything else comes from chart defaults.

This is the current flow that matches the charts in this repository.

## What Gets Installed

Charts in this directory:
- `charts/postgres-db`: PostgreSQL with pgvector
- `charts/redis`: Redis with password auth
- `charts/postgres-setup`: one-shot schema/bootstrap job
- `charts/kdcube-platform`: application services

Services installed by `kdcube-platform`:
- `kdcube-secrets`
- `chat-ingress`
- `chat-proc`
- `metrics`
- `web-ui`
- `web-proxy`

PersistentVolumeClaims created by `kdcube-platform` by default:
- `bundles`
- `kdcube-storage`
- `bundle-storage`
- `exec-workspace`
- `kdcube-logs`

## Prerequisites

### 1. Kubernetes cluster

You need a working cluster that supports:
- PersistentVolumeClaims
- standard `Deployment`, `Service`, and `Job` resources
- pulling images from Docker Hub

Examples:
- `k3d`
- `k3s`
- `minikube`
- managed clusters such as EKS

### 2. Local tools

Required:
- `kubectl`
- `helm`

Useful for validation:
- `curl`
- `jq`

### 3. Container image access

The cluster must be able to pull these images:
- `kdcube/kdcube-chat-ingress`
- `kdcube/kdcube-chat-proc`
- `kdcube/kdcube-metrics`
- `kdcube/kdcube-secrets`
- `kdcube/kdcube-web-ui`
- `kdcube/kdcube-web-proxy`
- `kdcube/kdcube-postgres-setup`
- `pgvector/pgvector`
- `redis`

### 4. Descriptor files

The chart-based deployment expects these files:
- `descriptors/assembly.yaml`
- `descriptors/gateway.yaml`
- `descriptors/bundles.yaml`
- `descriptors/secrets.yaml`

These files are the source of truth for environment-specific deployment settings.

Expected responsibilities:

`assembly.yaml`
- `config.version` and/or `platform.ref`
- `context.tenant`
- `context.project`
- `auth.type`
- `proxy.route_prefix`
- `storage.*`
- `platform.*`

`gateway.yaml`
- runtime gateway configuration such as limits, throttling, pools, and profile

`bundles.yaml`
- bundle registry content and the default bundle id

`secrets.yaml`
- model/API keys
- AWS credentials if S3 is used
- Cognito client secret if delegated auth is used

### 5. Storage planning

Before installation, decide:
- which storage class to use for PVCs
- whether `storage.kdcube` and `storage.bundles` point to PVC-backed filesystems or S3
- whether the cluster has access to your object storage endpoints

### 6. Auth planning

Set auth mode only in `assembly.yaml`:
- `auth.type: simple`
- `auth.type: cognito`
- `auth.type: delegated`

Current chart behavior:
- `simple` -> backend `AUTH_PROVIDER=simple`, frontend `authType=hardcoded`
- `cognito` -> backend `AUTH_PROVIDER=cognito`, frontend `authType=cognito`
- `delegated` -> backend `AUTH_PROVIDER=cognito`, frontend `authType=cognito`

Do not configure auth mode in bundles.
Do not manually edit generated env vars for the chart flow.

## Fresh Install Order

### 1. Create namespace

```bash
kubectl create namespace kdcube-ai-app
```

### 2. Install PostgreSQL

```bash
cd /Users/viacheslav/work/NestLogic/kdcube/kdcube-ai-app/app/ai-app/deployment/kubernetes-setup

helm upgrade --install postgres-db ./charts/postgres-db \
  -n kdcube-ai-app \
  -f <DESCRIPTORS_HOST_PATH>/secrets.yaml
```

### 3. Install Redis

```bash
helm upgrade --install redis ./charts/redis \
  -n kdcube-ai-app \
  -f <DESCRIPTORS_HOST_PATH>/secrets.yaml
```

### 4. Run Postgres bootstrap job

This creates tenant/project-specific schemas and tables based on `assembly.yaml`.

```bash
helm upgrade --install postgres-setup ./charts/postgres-setup \
  -n kdcube-ai-app \
  -f <DESCRIPTORS_HOST_PATH>/assembly.yaml
```

Wait for completion:

```bash
kubectl -n kdcube-ai-app wait --for=condition=complete job/postgres-setup-postgres-setup --timeout=240s
```

### 5. Install platform services

Use chart defaults plus descriptor overlays:

```bash
helm upgrade --install kdcube-platform ./charts/kdcube-platform \
  -n kdcube-ai-app \
  -f <DESCRIPTORS_HOST_PATH>/assembly.yaml \
  -f <DESCRIPTORS_HOST_PATH>/gateway.yaml \
  -f <DESCRIPTORS_HOST_PATH>/bundles.yaml \
  -f <DESCRIPTORS_HOST_PATH>/secrets.yaml
```

### 6. Restart pods after descriptor changes

This is important after config changes because some generated files are mounted via `subPath`.

```bash
kubectl -n kdcube-ai-app rollout restart deploy/web-proxy deploy/web-ui deploy/chat-ingress deploy/chat-proc deploy/metrics
kubectl -n kdcube-ai-app rollout status deploy/web-proxy
kubectl -n kdcube-ai-app rollout status deploy/web-ui
kubectl -n kdcube-ai-app rollout status deploy/chat-ingress
kubectl -n kdcube-ai-app rollout status deploy/chat-proc
kubectl -n kdcube-ai-app rollout status deploy/metrics
```

## What Each Install Consumes

### `postgres-db`

Consumes:
- chart defaults from `charts/postgres-db/values.yaml`
- descriptor overlay `descriptors/secrets.yaml` for password fields

Provides:
- PostgreSQL service
- secret `postgres-db-credentials`
- persistent database volume

### `redis`

Consumes:
- chart defaults from `charts/redis/values.yaml`
- descriptor overlay `descriptors/secrets.yaml` for password fields

Provides:
- Redis service
- secret `redis-auth`
- persistent Redis volume

### `postgres-setup`

Consumes:
- defaults from `charts/postgres-setup/values.yaml`
- descriptor values from `assembly.yaml`

Uses:
- `context.tenant`
- `context.project`
- `platform.ref` / `platform.config.version` / `config.version` for image tag resolution

Provides:
- tenant/project schema bootstrap
- tables such as `conv_messages`

### `kdcube-platform`

Consumes:
- chart defaults from `charts/kdcube-platform/values.yaml`
- descriptor overlays from:
  - `assembly.yaml`
  - `gateway.yaml`
  - `bundles.yaml`
  - `secrets.yaml`

Generates internally:
- backend env ConfigMap
- frontend runtime ConfigMap
- frontend nginx ConfigMap
- proxy nginx ConfigMap
- runtime descriptors ConfigMap
- platform Secret

For the current chart flow, you do not need to manually apply runtime manifests.
You also do not need local `values/*.yaml` files for a fresh install.

## First-Run Validation

Check pods and services:

```bash
kubectl -n kdcube-ai-app get pods
kubectl -n kdcube-ai-app get svc
kubectl -n kdcube-ai-app get jobs
```

Check bootstrap success:

```bash
kubectl -n kdcube-ai-app logs job/postgres-setup-postgres-setup --tail=100
```

Check application logs:

```bash
kubectl -n kdcube-ai-app logs deployment/chat-ingress --tail=100
kubectl -n kdcube-ai-app logs deployment/chat-proc --tail=100
kubectl -n kdcube-ai-app logs deployment/web-proxy --tail=100
```

Check generated runtime config:

```bash
kubectl -n kdcube-ai-app get configmap backend-env -o jsonpath='{.data.GATEWAY_CONFIG_JSON}'
kubectl -n kdcube-ai-app get configmap frontend-runtime-config -o jsonpath='{.data.config\.json}'
```

## Browser Access

Port-forward the proxy:

```bash
kubectl -n kdcube-ai-app port-forward svc/web-proxy 8080:80
```

Open:

```bash
open http://127.0.0.1:8080/chatbot/chat
```

## Common Failure Modes

### Missing tables such as `conv_messages`

Cause:
- `postgres-setup` was not run
- or it ran with the wrong tenant/project

Fix:
- verify `context.tenant` and `context.project` in `assembly.yaml`
- rerun `postgres-setup`

### Blank page in browser

Cause:
- `web-proxy` did not reload updated config

Fix:
- rerun Helm upgrade
- restart `web-proxy` and `web-ui`

### S3 access errors

Cause:
- missing `aws.access_key_id` / `aws.secret_access_key` in `secrets.yaml`
- or storage path points to S3 without working credentials

Fix:
- update `secrets.yaml`
- rerun Helm upgrade
- restart platform deployments

### Wrong auth mode

Cause:
- `auth.type` in `assembly.yaml` does not match intended deployment

Fix:
- update `assembly.yaml`
- rerun Helm upgrade for `kdcube-platform`
- restart `web-ui`, `web-proxy`, `chat-ingress`, `chat-proc`, `metrics`

## Minimal Checklist

1. Create a cluster.
2. Make sure `kubectl` and `helm` work against it.
3. Prepare descriptor files.
4. Install `postgres-db`.
5. Install `redis`.
6. Run `postgres-setup`.
7. Install `kdcube-platform` with descriptor overlays.
8. Port-forward `web-proxy` and open `/chatbot/chat`.
