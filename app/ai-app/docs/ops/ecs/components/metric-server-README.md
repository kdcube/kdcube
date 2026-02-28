# Metrics Service (ECS) – Sample Task Definitions

This folder contains **sample ECS task definitions** for the metrics service.
They are templates — replace placeholders (cluster, subnets, security groups, image).

## Files

- `metrics-task-definition.json`  
  Long‑running metrics service (exposes `/metrics`, `/metrics/combined`, etc.).

- `metrics-scheduled-task.json`  
  One‑shot exporter task (uses `METRICS_RUN_ONCE=1`) for EventBridge scheduling.

- `metrics-eventbridge-rule.json`  
  Example EventBridge rule (cron/rate).

## Usage outline (AWS CLI)

1) Register task definition:
```bash
aws ecs register-task-definition --cli-input-json file://metrics-task-definition.json
```

2) Create an ECS Service (long‑running):
```bash
aws ecs create-service \
  --cluster <cluster> \
  --service-name kdcube-metrics \
  --task-definition kdcube-metrics \
  --desired-count 1 \
  --launch-type FARGATE \
  --network-configuration file://network-config.json
```

3) Schedule one‑shot exporter (optional):
```bash
aws ecs register-task-definition --cli-input-json file://metrics-scheduled-task.json
aws events put-rule --cli-input-json file://metrics-eventbridge-rule.json
aws events put-targets --rule kdcube-metrics-export --targets file://metrics-eventbridge-targets.json
```

Notes:
- The **long‑running service** already has an internal scheduler.
- The **scheduled task** is only needed if you prefer EventBridge‑driven exports.
