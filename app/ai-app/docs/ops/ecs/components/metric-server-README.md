---
id: repo:kdcube-ai-app/app/ai-app/docs/ops/ecs/components/metric-server-README.md
title: "ECS Metric Server"
summary: "How the metrics service is deployed on ECS and how it feeds CloudWatch autoscaling metrics."
tags: ["ops", "ecs", "metrics", "cloudwatch", "autoscaling"]
keywords: ["metrics service", "metric server", "CloudWatch", "ECS", "autoscaling", "kdcube-metrics"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/scale/metric-server-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/scale/metrics-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/ops/ecs/ecs-deployment-README.md
---
# ECS Metric Server

This document describes the **current ECS deployment shape** of the Metrics service.

It is not the source of truth for metric semantics or exporter internals.
Those live in:

- [metric-server-README.md](../../../service/scale/metric-server-README.md)
- [metrics-README.md](../../../service/scale/metrics-README.md)

The ECS wiring described here comes from the internal-demo deployment repo,
specifically `ops/ecs/terraform/modules/ecs/task_remaining.tf`.

---

## 1. What Runs On ECS

The Metrics service is deployed as:

- ECS service name: `${name_prefix}-metrics`
- task family: `${name_prefix}-metrics`
- launch type: `FARGATE`
- desired count: `1`
- container name: `metrics`
- container port: `8002`

It also registers into Cloud Map as:

- service discovery name: `metrics`

Deployment settings:

- `deployment_minimum_healthy_percent = 100`
- `deployment_maximum_percent = 200`

So ECS can start a replacement before stopping the old task.

---

## 2. Runtime Mode In ECS

The current ECS deployment runs the Metrics service in:

- `METRICS_MODE=redis`

That means:

- it reads monitoring state from Redis rather than calling protected upstream monitoring endpoints
- it uses its own internal scheduler
- it exports a scalar subset of metrics to CloudWatch

Important ECS env currently set by Terraform:

- `METRICS_PORT=8002`
- `METRICS_MODE=redis`
- `METRICS_SCHEDULER_ENABLED=1`
- `METRICS_EXPORT_INTERVAL_SEC=60`
- `METRICS_EXPORT_ON_START=1`
- `METRICS_PROM_SCRAPE_TTL_SEC=120`
- `METRICS_EXPORT_CLOUDWATCH=1`
- `METRICS_CLOUDWATCH_NAMESPACE=kdcube/${name_prefix}`
- `METRICS_CLOUDWATCH_REGION=<aws_region>`
- `METRICS_CLOUDWATCH_DIMENSIONS_JSON={"Environment":"${name_prefix}"}`

Secrets currently injected:

- `REDIS_URL`
- `GATEWAY_CONFIG_JSON`

---

## 3. CloudWatch Export In ECS

The ECS deployment explicitly maps the main autoscaling signals to stable CloudWatch names:

- `ingress.sse.saturation_ratio` -> `chat/ingress/sse/saturation`
- `proc.queue.utilization_ratio` -> `chat/proc/queue/utilization`
- `proc.queue.wait.p95_ms` -> `chat/proc/queue/wait/p95`
- `proc.exec.p95_ms` -> `chat/proc/exec/p95`

Current namespace and dimensions:

- namespace: `kdcube/${name_prefix}`
- dimension: `Environment=${name_prefix}`

So in a staging deployment with `name_prefix=kdcube-staging`, the main metrics appear under:

- namespace: `kdcube/kdcube-staging`
- dimension: `Environment=kdcube-staging`

---

## 4. What This Doc Does Not Cover

This document does not define:

- the full metrics data model
- proc/ingress metric semantics
- percentile window logic
- autoscaling threshold design

Use instead:

- [metric-server-README.md](../../../service/scale/metric-server-README.md)
- [metrics-README.md](../../../service/scale/metrics-README.md)
- [ecs-deployment-README.md](../ecs-deployment-README.md)

---

## 5. Historical Note

Older versions of this doc described sample ECS task-definition JSON files.
That is no longer the current model in this repo.

The current source of truth is:

- Terraform ECS task/service definitions in `kdcube-internal-demo`
- service-scale metrics docs in this repo
