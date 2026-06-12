---
id: repo:kdcube-ai-app/app/ai-app/docs/service/ecs/metrics-autoscaling-README.md
title: "Metrics Autoscaling"
summary: "How metrics service supports autoscaling for ingress and proc."
tags: ["service", "ecs", "autoscaling", "metrics"]
keywords: ["CloudWatch", "HPA", "metrics endpoints", "scale policies"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/scale/metrics-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/ecs/custom-ecs-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/environment/setup-for-ecs-README.md
---
# Metrics + Autoscaling (ECS)

This document explains how the **metrics service** supports autoscaling for ingress and processor.

---

## 1) Metrics Service Role

Metrics service:

- Aggregates runtime stats from Redis (and optional PG)
- Exports to CloudWatch or Prometheus
- Provides a single source of truth for autoscaler signals

---

## 2) Required Env (Metrics)

```bash
METRICS_MODE=redis
REDIS_URL=redis://:<REDIS_PASSWORD>@<REDIS_HOST>:6379/0
GATEWAY_CONFIG_JSON='{
  "tenant": "<TENANT_ID>",
  "project": "<PROJECT_ID>"
}'

METRICS_EXPORT_CLOUDWATCH=1
METRICS_CLOUDWATCH_NAMESPACE=KDCube/Metrics
METRICS_CLOUDWATCH_REGION=eu-west-1
METRICS_CLOUDWATCH_DIMENSIONS_JSON='{"tenant":"<TENANT_ID>","project":"<PROJECT_ID>","env":"prod"}'
```

---

## 3) Suggested CloudWatch Metrics

These are logical signals. Exact names can be mapped via `METRICS_MAPPING_JSON`.

- `ingress.sse.total_connections`
- `ingress.throttling.rate_limit_429`
- `proc.queue.depth`
- `proc.queue.wait_ms_p95`
- `proc.capacity.utilization`
- `proc.active_tasks`

---

## 4) Autoscaling Strategy (Suggested)

### Processor service

Scale by **queue depth + wait time**:

- Scale out when `queue.depth` > threshold
- Scale out when `queue.wait_ms_p95` > threshold
- Scale in when both return below thresholds

### Ingress service

Scale by **SSE connections + 429 rate**:

- Scale out when `sse.total_connections` approaches per‑instance max
- Scale out when 429s spike

---

## 5) ECS Target Tracking (Example)

**Processor:**
- Target `proc.capacity.utilization` around 0.7‑0.8

**Ingress:**
- Target `sse.total_connections / max_sse_connections_per_instance` around 0.7‑0.8

---

## 6) Notes for Ops

- If the effective gateway config changes, **restart** services (or set `GATEWAY_CONFIG_FORCE_ENV_ON_STARTUP=1` to enforce the env/descriptor-sourced config on each start).
- Keep the same effective gateway config on ingress/proc/metrics. In the current ECS packages this is commonly a shared `GATEWAY_CONFIG_JSON`.
- Do not scale processor beyond Postgres/Redis connection limits.
