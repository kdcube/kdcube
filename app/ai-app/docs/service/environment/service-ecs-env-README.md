---
id: ks:docs/service/environment/service-ecs-env-README.md
title: "Service ECS Env"
summary: "Common AWS env vars used by ECS tasks (CloudWatch, AWS APIs)."
tags: ["service", "environment", "ecs", "aws"]
keywords: ["AWS_REGION", "AWS_ROLE_ARN", "CloudWatch", "task env"]
see_also:
  - ks:docs/service/environment/service-compose-env-README.md
  - ks:docs/service/environment/service-dev-env-README.md
  - ks:docs/service/environment/setup-dev-env-README.md
---
# Service ECS Env (AWS Runtime)

Common AWS env vars used by services that export to CloudWatch or access AWS APIs.

## Required (all AWS)
| Variable             | Purpose                                     | Default | Scope         |
|----------------------|---------------------------------------------|---------|---------------|
| `AWS_REGION`         | AWS region for SDK calls                    | —       | all services  |
| `AWS_DEFAULT_REGION` | Alternate region variable recognized by SDK | —       | all services  |

## When running on ECS/EC2 with IAM role (recommended)
| Variable                      | Purpose                                                        | Default                               | Scope |
|-------------------------------|----------------------------------------------------------------|---------------------------------------|---|
| `AWS_EC2_METADATA_DISABLED`   | Must be `false` (or unset) so SDK can use instance/Task role   | `false`                               | all services |
| `NO_PROXY`                    | Ensure IMDS is reachable if proxy is used                      | `169.254.169.254,localhost,127.0.0.1` | all services |

## When running locally / outside AWS
Use one of these credential sources:

**Option A: environment keys**
| Variable                | Purpose                               |
|-------------------------|---------------------------------------|
| `AWS_ACCESS_KEY_ID`     | Access key                            |
| `AWS_SECRET_ACCESS_KEY` | Secret key                            |
| `AWS_SESSION_TOKEN`     | Session token (if using temporary creds) |

**Option B: shared config/profile**
| Variable | Purpose |
|---|---|
| `AWS_SDK_LOAD_CONFIG` | Enable shared config parsing (`1`) |
| `AWS_PROFILE` | Named profile in `~/.aws/config` |

## Notes
- For CloudWatch export, services must have `cloudwatch:PutMetricData` permissions.
- Prefer IAM roles in ECS/EC2 instead of static keys.
- To enforce env gateway config on every deploy, set `GATEWAY_CONFIG_FORCE_ENV_ON_STARTUP=1`
  on ingress/proc/metrics tasks.

## Secrets providers on ECS

Supported runtime providers are:
- `aws-sm`
- `secrets-service`
- `secrets-file`
- `in-memory`

For descriptor-backed secrets on ECS:

```bash
SECRETS_PROVIDER=secrets-file
GLOBAL_SECRETS_YAML=s3://<bucket>/<prefix>/secrets.yaml
BUNDLE_SECRETS_YAML=s3://<bucket>/<prefix>/bundles.secrets.yaml
```

You can also use `file://...` when descriptors are mounted from EFS or baked
into the image.

`secrets-file` persists updates back into the configured descriptors. For `s3://...`,
the task role needs object write permissions. For `file://...`, the mounted path
must be writable by the service.

## Proc Git Bundles (ECS)

If you use git‑defined bundles in **chat‑proc**:

| Variable                           | Purpose                                                  |
|------------------------------------|----------------------------------------------------------|
| `BUNDLE_GIT_RESOLUTION_ENABLED`    | Enable git clone/pull for bundles with `repo`            |
| `BUNDLE_GIT_ALWAYS_PULL`           | Always pull (useful for branch refs)                     |
| `BUNDLE_GIT_ATOMIC`                | Atomic checkout (clone to temp dir then rename)          |
| `BUNDLE_GIT_*`                     | Shallow/keep/ttl/lock settings (see bundle docs)         |
| `GIT_SSH_KEY_PATH`                 | Path to SSH private key (mount from Secrets Manager/SSM) |
| `GIT_SSH_KNOWN_HOSTS`              | Known hosts file (mount)                                 |
| `GIT_SSH_STRICT_HOST_KEY_CHECKING` | `yes`/`no`                                               |
| `GIT_SSH_COMMAND`                  | Full SSH command override (optional)                     |
| `GIT_HTTP_TOKEN`                   | HTTPS token for private repos (uses GIT_ASKPASS)         |
| `GIT_HTTP_USER`                    | HTTPS username (defaults to `x-access-token`)            |
| `AGENTIC_BUNDLES_ROOT`             | Bundles root inside container (e.g. `/bundles`)          |
