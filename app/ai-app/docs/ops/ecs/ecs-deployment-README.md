---
id: ks:docs/ops/ecs/ecs-deployment-README.md
title: "ECS Deployment"
summary: "Current AWS ECS deployment model for KDCube: descriptor-driven provisioning, mixed-capacity runtime, proc on EC2, autoscaling, and rollout behavior."
tags: ["ops", "ecs", "deployment", "aws", "autoscaling"]
keywords: ["ecs", "terraform", "github actions", "proc ec2", "fargate", "cloudwatch", "autoscaling", "deployment descriptors"]
see_also:
  - ks:docs/ops/health-README.md
  - ks:docs/service/scale/metric-server-README.md
  - ks:docs/service/scale/metrics-README.md
  - ks:docs/service/ecs/custom-ecs-README.md
---
# ECS Deployment

This document describes the current AWS ECS deployment model for KDCube.
It focuses on the runtime shape that operators and developers need to understand:
what runs where, how proc differs from the other services, how autoscaling works,
and what graceful rollout behavior can and cannot guarantee.

## Deployment Model

The AWS deployment is descriptor-driven.
Provisioning automation reads a set of YAML deployment descriptors from GitHub
Secrets and applies Terraform from them.

The key inputs are:

- `AWS_DEPLOYMENT_YAML` for AWS sizing, networking, image registry, and proc EC2 settings
- `ASSEMBLY_YAML` for platform configuration and platform image version
- `GATEWAY_YAML` for gateway and capacity configuration
- `SECRETS_YAML` for service credentials and API keys
- `BUNDLES_YAML` and `BUNDLES_SECRETS_YAML` for bundle registry and bundle secrets

In practice:

- infrastructure and ECS task definitions come from Terraform
- service runtime behavior comes from this application codebase
- deployment changes are applied by updating descriptor values and rerunning the provision workflow

## Runtime Topology

Current runtime on ECS:

- `web-proxy` is the only ALB-facing service
- `web-ui` serves the SPA
- `chat-ingress` handles REST and SSE gateway traffic
- `chat-proc` handles queue processing, bundle execution, and integrations endpoints
- `metrics` exports autoscaling metrics in Redis mode
- `proxylogin` is used only for delegated-auth deployments
- `exec` is an on-demand task launched by proc, not a steady ECS service

Routing is roughly:

- `/sse/*`, `/api/chat/*`, `/api/cb/*`, `/admin/*` -> `chat-ingress`
- `/api/integrations/*` -> `chat-proc`
- `/auth/*` -> `proxylogin` when delegated auth is enabled
- `/*` -> `web-ui`

All of these services are private inside ECS and Cloud Map.
External traffic enters through ALB and `web-proxy`.

## Mixed-Capacity Runtime

The ECS deployment is mixed-capacity:

- most services stay on Fargate
- `chat-proc` can run on an EC2-backed ECS capacity provider when `proc_ec2.enabled: true`
- the on-demand `exec` task can still run on Fargate even when proc itself runs on EC2

This means there are two distinct scaling layers for proc:

- proc service scaling: ECS service desired task count
- proc host scaling: EC2 instance count behind the proc capacity provider

These are related but not identical.
Because proc uses `distinctInstance`, each proc task needs its own ECS container instance.
So `proc_ec2.max_size` must cover both real proc autoscaling and rolling replacement of proc hosts.

## Proc On EC2

When `proc_ec2.enabled: true`, proc changes from a pure Fargate task into a task
that runs on ECS/EC2 and can launch Docker-based exec locally on the host.

### Host bootstrap

Each proc EC2 host is bootstrapped to:

- join the ECS cluster
- mount the required EFS access points on the host under:
  - `/opt/kdcube/efs/kdcube-storage`
  - `/opt/kdcube/efs/bundle-storage`
  - `/opt/kdcube/efs/bundles`
  - `/opt/kdcube/efs/exec-workspace`
- maintain Docker registry auth under `/opt/kdcube/docker-auth`
- refresh ECR auth periodically so long-running proc hosts can keep pulling private images
- prewarm the exec image without blocking ECS registration

### Proc container wiring

The proc task receives:

- the host Docker socket at `/var/run/docker.sock`
- host Docker auth mounted into `/home/appuser/.docker`
- `PY_CODE_EXEC_IMAGE`
- `EXEC_RUNTIME_MODE=docker` by default on EC2
- host-path env vars used by nested Docker execution:
  - `HOST_KDCUBE_STORAGE_PATH`
  - `HOST_BUNDLE_STORAGE_PATH`
  - `HOST_BUNDLES_PATH`
  - `HOST_MANAGED_BUNDLES_PATH`
  - `HOST_EXEC_WORKSPACE_PATH`

This lets proc start nested Docker exec containers that bind the same stable
host-mounted EFS paths that the proc task itself sees.

### Fargate exec remains available

Even when proc runs on EC2, the deployment still injects the `FARGATE_EXEC_*`
settings. That keeps the isolated ECS/Fargate exec path available for bundle-
selected or runtime-selected Fargate execution.

## Images And Rollout Inputs

For AWS ECS deploys:

- the image registry comes from `AWS_DEPLOYMENT_YAML -> image_registry`
- the image version comes from `ASSEMBLY_YAML -> platform.ref`

So pushing an image to ECR is not enough by itself.
To deploy a new image, the platform release ref must be updated and the
provision workflow rerun.

This applies to:

- `kdcube-chat-proc`
- `py-code-exec`
- the rest of the platform images

## Shared Storage And Bundles

The ECS deployment relies on EFS for shared mutable state.
Important mounted areas include:

- `/kdcube-storage`
- `/bundle-storage`
- `/bundles`
- `/config`
- `/exec-workspace`

In practice:

- bundle registry config is written to EFS and consumed from `/config/bundles.yaml`
- assembly descriptor may also be exposed under `/config/assembly.yaml`
- proc uses `/bundles` as the bundle root
- proc and nested exec containers share storage through EFS-backed paths
- bundle config updates can be applied without full infrastructure reprovision

If ingress, proc, or metrics code uses `read_plain(...)`, those services must
receive the shared `/config` mount so runtime can read:

- `/config/assembly.yaml`
- `/config/bundles.yaml`

See:
- [docs/service/configuration/service-config-README.md](../../service/configuration/service-config-README.md)

## Autoscaling Model

The metrics service is the autoscaling signal source.
It runs in Redis mode and publishes CloudWatch metrics for ECS service autoscaling.

### Exported CloudWatch signals

The deployment maps the main autoscaling signals to stable CloudWatch-style names:

- `chat/ingress/sse/saturation`
- `chat/proc/queue/utilization`
- `chat/proc/queue/wait/p95`
- `chat/proc/exec/p95`

The metrics service also computes stronger proc queue signals internally,
including queue pressure and wait metrics, as described in:

- [metric-server-README.md](../../service/scale/metric-server-README.md)
- [metrics-README.md](../../service/scale/metrics-README.md)

### Current scaling shape

Ingress:

- scales from SSE saturation

Proc:

- scales out when queue utilization or queue p95 wait breaches configured thresholds
- scales in only when both queue utilization and queue p95 wait are low enough for the configured window

Proc scale-in is intentionally stricter than scale-out.
The infrastructure uses a metric-math AND alarm for scale-in so proc is not
scaled down just because one signal is temporarily low.

## Rollouts And Graceful Updates

### What infrastructure updates do

Provisioning changes can update:

- task definitions
- ECS services
- proc EC2 launch template and userdata
- proc host fleet through Auto Scaling Group instance refresh

That means task-definition changes and proc host bootstrap changes are both part
of the deployment contract.

### Host protection during scale-in

The proc capacity provider uses:

- ASG scale-in protection on proc instances
- ECS managed termination protection for busy proc hosts

This reduces the risk of ASG scale-in killing a proc EC2 host that is still
running ECS tasks.
It does not make task shutdown unbounded.

### Proc drain contract

Proc participates in graceful drain:

- `/health` returns `503` while draining
- the processor stops accepting new work and waits for active work to settle
- claimed-but-not-started work can be requeued during drain

But task shutdown is still bounded by ECS stop timing.

Current stop contract:

- ECS task `stopTimeout`: `120s`
- proc app target drain budget: about `110s`

Operational consequence:

- if active work finishes inside that window, the task can stop cleanly
- if active work exceeds that window, ECS can still hard-kill the container
- a started task is not guaranteed to run forever just because the host is protected from scale-in

## Health And Readiness

For deployment and autoscaling, use the documented health endpoints rather than
assuming generic container readiness.
See [health-README.md](../health-README.md).

In particular:

- proc readiness depends on both service health and bundle readiness
- a draining proc intentionally becomes unready

## What This Repo Covers

This repo is the right place to understand:

- service runtime behavior
- health and readiness contracts
- metrics semantics
- queue and drain behavior
- proc exec runtime behavior

For operators, the practical deployment model is:

- descriptor-driven provisioning
- mixed-capacity ECS runtime
- proc optionally on EC2, other services mostly on Fargate
- EFS-backed shared storage for bundles and exec workspace
- autoscaling driven from the metrics service rather than ad hoc task env templates

## Summary

The relevant ECS model today is:

- AWS ECS provisioned from deployment descriptors
- `chat-proc` optionally running on EC2 with host Docker and EFS integration
- `exec` remaining an on-demand isolated task
- autoscaling driven by Redis-mode metrics exported to CloudWatch
- graceful drain supported, but still bounded by the ECS stop window
