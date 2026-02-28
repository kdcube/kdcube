# ECS Deployment Templates

This folder contains **environment templates** for ECS tasks/services.
They are intended as starting points for task definitions or parameter stores.

## Templates
- `deployment/ecs/ingress/env.template` — Ingress (SSE/API entrypoint).
- `deployment/ecs/proc/env.template` — Processor (queue worker + integrations API).
- `deployment/ecs/metrics/env.template` — Metrics service (CloudWatch/Prometheus export).
- `deployment/ecs/frontend/env.template` — UI service (runtime config injection).

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
- Metrics scheduled task example: `docs/ops/ecs/components/metric-server-README.md`

## Notes
- These templates intentionally use **placeholders**. Replace them in your task
  definition or inject via parameter store/secrets manager.
