# Deployment options (index)

Choose the path that matches your workflow.

## 1. Platform dev (run services locally)
**Folder:** [deployment/devenv/](../../deployment/devenv)

Use this when developing the platform or SDK. You run ingress/proc/metrics/frontend directly on your host, while infra runs elsewhere (e.g. `local-infra-stack`).

## 2. Local infra only (Postgres/Redis/ClamAV/proxylogin)
**Folder:** [deployment/docker/local-infra-stack/](../../deployment/docker/local-infra-stack)

Use this when you want a local infra stack but run services on your host (DevEnv).

## 3. All-in-one KDCube (local compose)
**Folder:** [deployment/docker/all_in_one_kdcube/](../../deployment/docker/all_in_one_kdcube)

Runs Postgres/Redis/ClamAV + ingress/proc/metrics + UI + proxy in a single compose. Best for bundle development and quick evaluation.

## 4. Custom UI + managed infra
**Folder:** [deployment/docker/custom-ui-managed-infra/](../../deployment/docker/custom-ui-managed-infra)

Runs KDCube services with a **custom frontend** while Postgres/Redis are managed externally. Includes OpenResty templates (hardcoded/cognito/delegated auth).
