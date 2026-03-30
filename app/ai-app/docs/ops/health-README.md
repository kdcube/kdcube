---
id: ks:docs/ops/health-README.md
title: "Health"
summary: "Liveness/readiness endpoints for deployers and autoscalers (ingress, proc, metrics)."
tags: ["ops", "health", "readiness", "liveness", "endpoints", "autoscaling"]
keywords: ["health endpoint", "readiness", "liveness", "ingress", "proc", "metrics", "draining", "HTTP 200"]
see_also:
  - ks:docs/ops/deployment-options-index-README.md
  - ks:docs/ops/ops-overview-README.md
  - ks:docs/ops/s3-README.md
---
# Health Endpoints (Liveness/Readiness)

This doc lists the **health endpoints intended for deployers/autoscalers/compose**.
Only include endpoints that should be used for **liveness/readiness checks**.

---

## Chat Ingress (chat‑ingress)

Endpoint:
- `GET /health`

Checks:
- Service is up
- `draining` flag (returns 503 when draining)
- Socket.IO enabled
- SSE enabled
- Instance id + port

Readiness:
- `200` when healthy
- `503` when draining

Code:
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/web_app.py`

---

## Chat Processor (chat‑proc)

Endpoint:
- `GET /health`

Checks:
- Service is up
- `draining` flag (returns 503 when draining)
- Git bundle readiness (`bundles_git_ready`)
- Git bundle errors (`bundles_git_errors`)
- Instance id

Readiness:
- `200` when healthy and bundles are ready
- `503` when draining or bundles not ready

Code:
- `src/kdcube-ai-app/kdcube_ai_app/apps/chat/proc/web_app.py`

---

## Metrics Service

Endpoint:
- `GET /health`

Checks:
- Service is up (returns `{status: "ok"}`)

Readiness:
- `200` when healthy

Code:
- `src/kdcube-ai-app/kdcube_ai_app/apps/metrics/web_app.py`

---

## Knowledge Base (KB)

Endpoints:
- `GET /api/kb/health`
- `GET /api/kb/health/process`

Checks:
- KB stats
- Orchestrator health + queue stats
- Storage path
- Per‑process capacity (process endpoint)

Readiness:
- `200` when healthy
- `503` when unavailable

Code:
- `src/kdcube-ai-app/kdcube_ai_app/apps/knowledge_base/api/web_app.py`

---

## Notes

- `draining` indicates the instance should be removed from load‑balancers.
