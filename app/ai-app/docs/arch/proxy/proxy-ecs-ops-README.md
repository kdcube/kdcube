---
id: ks:docs/arch/proxy/proxy-ecs-ops-README.md
title: "Proxy ECS Ops"
summary: "Ops guide for the OpenResty reverse proxy on AWS ECS/Fargate: ALB setup, real IP recovery, SSL offload, service discovery, IAM, ECR, and EFS."
tags: ["proxy", "openresty", "ops", "ecs", "fargate", "aws", "alb", "ecr", "efs", "nginx"]
keywords: ["OpenResty", "ECS", "Fargate", "ALB", "ACM", "real_ip_header", "set_real_ip_from", "service discovery", "Cloud Map", "ECR", "EFS", "awsvpc", "task IAM role", "nginx_proxy_ssl_cognito.conf"]
see_also:
  - ks:docs/arch/proxy/proxy-ops-README.md
  - ks:docs/arch/proxy/proxy-local-ops-README.md
  - ks:docs/sdk/bundle/bundle-ops-README.md
---
# Proxy ECS Ops Guide (OpenResty on AWS ECS/Fargate)

This guide covers deploying the OpenResty proxy on **AWS ECS with Fargate**, where SSL is offloaded to an Application Load Balancer (ALB) and all services run as ECS tasks in a shared VPC.

For other deployments see:
- [EC2 + SSL + proxylogin](proxy-ops-README.md)
- [Local / all-in-one Docker Compose](proxy-local-ops-README.md)

---

## Architecture overview

```
Internet
  │ HTTPS :443
  ▼
ALB  (ACM certificate, SSL termination)
  │ HTTP :80  (within VPC only)
  ▼
web-proxy ECS service  (OpenResty, this proxy)
  │ HTTP  (awsvpc, service discovery DNS)
  ├─▶ web-ui
  ├─▶ chat-ingress
  ├─▶ chat-proc
  ├─▶ proxylogin   (if auth mode = delegated)
  └─▶ kb           (if KB service enabled)
```

On ECS, TLS is terminated at the ALB using an ACM certificate. The proxy container runs on **port 80 only** — the `EXPOSE 443` line in the Dockerfile is commented out by design. Traffic between the ALB and the proxy travels over plain HTTP inside the private VPC subnet.

---

## Key differences from EC2 and local deployments

|                   | ECS (this doc) | EC2 / SSL                                        | Local |
|-------------------|---|--------------------------------------------------|---|
| SSL/TLS           | ALB + ACM (not in proxy) | Proxy-level (Let's Encrypt)                      | None |
| Proxy port        | `:80` only | `:80` + `:443`                                   | `:80` only |
| Real client IP    | Must recover from `X-Forwarded-For` | Direct                                           | Direct |
| `server_name`     | Explicit domain or `_` | Explicit domain                                  | `_` |
| Compose           | No — ECS task definitions | `docker-compose.yml` (custom-ui-managed-infra)   | `docker-compose.yml` (all_in_one_kdcube) |
| Service discovery | AWS Cloud Map DNS | Docker network DNS                               | Docker network DNS |
| Config delivery   | ECR image (baked in) or EFS mount | Bind mount / baked in image                      | Bind mount / baked in image |
| AWS credentials   | Task IAM role | `~/.aws` bind mount                              | `~/.aws` bind mount |
| Let's Encrypt     | Not needed | `/etc/letsencrypt` bind mount                    | Not needed |
| Shared storage    | EFS (`uid=1000`, `gid=1000`) | Host bind mount                                  | Host bind mount |
| proxylogin        | Separate ECS service | Separate container                               | Commented out |

---

## Critical config change: real IP recovery

The EC2 nginx config contains this commented-out block, explicitly labelled `only if under LB`:

```nginx
#     real_ip_header X-Forwarded-For;
#     set_real_ip_from <LB_CIDR>;
#     real_ip_recursive on;
```

**This block must be uncommented for ECS behind an ALB.** Without it, `$binary_remote_addr` resolves to the ALB's private IP, causing every client to share a single rate-limiting bucket — rendering rate limiting useless and breaking per-client `limit_conn` counts.

In your ECS nginx config, replace `<LB_CIDR>` with the CIDR of your ALB subnet(s). For a typical ALB in two AZs:

```nginx
# In http {} — only when behind ALB
real_ip_header    X-Forwarded-For;
set_real_ip_from  10.0.0.0/8;      # replace with your VPC/ALB CIDR
real_ip_recursive on;
```

`real_ip_recursive on` strips any client-supplied `X-Forwarded-For` spoofing by walking the chain from right to left and stopping at the first non-trusted IP.

---

## SSL offload: proxy config for ECS

Because the ALB terminates TLS, the proxy does not need to handle certificates or HTTPS redirects. Use a simplified server block:

```nginx
# No default_server IP block needed — ALB never sends bare IP requests
# No ACME location needed — ACM handles renewals

server {
    listen 80;
    server_name YOUR_DOMAIN_NAME;

    # Security headers (HSTS omitted — add at ALB level or keep here)
    more_set_headers "X-Content-Type-Options: nosniff";
    more_set_headers "X-Frame-Options: DENY";
    more_set_headers "X-XSS-Protection: 1; mode=block";
    more_set_headers "Referrer-Policy: strict-origin-when-cross-origin";
    # HSTS: add here or at ALB via a custom response header rule
    more_set_headers "Strict-Transport-Security: max-age=31536000; includeSubDomains";

    # Forward the original scheme so backends know the request arrived over HTTPS
    proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;

    # ... rest of location blocks unchanged from EC2 config ...
}
```

If `proxylogin` is in use, the `ssl_certificate` directives in the EC2 config are removed entirely — the `init_by_lua_block` and `access_by_lua_block` auth logic is unchanged.

The ALB listener rules:
- `:443` → forward to target group (proxy ECS service, port 80)
- `:80` → redirect to `https://#{host}:443/#{path}?#{query}` (HTTP 301)

---

## ALB health check

Configure the ALB target group health check to hit a lightweight endpoint. The SPA redirect is suitable:

| Setting | Value |
|---|---|
| Protocol | HTTP |
| Port | 80 |
| Path | `/` |
| Expected codes | 200, 301 |
| Healthy threshold | 2 |
| Unhealthy threshold | 3 |
| Timeout | 5 s |
| Interval | 30 s |

The proxy returns `301 /chatbot/chat` for `GET /`, which counts as a 3xx success in ALB health checks when `200,301` is the matcher.

---

## ECS task definition

The proxy runs as its own ECS service (not a sidecar). Suggested task definition parameters:

```json
{
  "family": "kdcube-web-proxy",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "256",
  "memory": "512",
  "executionRoleArn": "arn:aws:iam::<account>:role/ecsTaskExecutionRole",
  "taskRoleArn": "arn:aws:iam::<account>:role/kdcube-proxy-task-role",
  "containerDefinitions": [
    {
      "name": "web-proxy",
      "image": "<account>.dkr.ecr.<region>.amazonaws.com/kdcube-web-proxy:latest",
      "portMappings": [{ "containerPort": 80, "protocol": "tcp" }],
      "essential": true,
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/kdcube-web-proxy",
          "awslogs-region": "<region>",
          "awslogs-stream-prefix": "ecs"
        }
      },
      "mountPoints": [
        {
          "sourceVolume": "nginx-config",
          "containerPath": "/usr/local/openresty/nginx/conf/nginx.conf",
          "readOnly": true
        }
      ]
    }
  ],
  "volumes": [
    {
      "name": "nginx-config",
      "efsVolumeConfiguration": {
        "fileSystemId": "<efs-fs-id>",
        "rootDirectory": "/proxy/nginx.conf",
        "transitEncryptionPort": 2999
      }
    }
  ]
}
```

Using an EFS mount for the config file lets you update the nginx config and force a new task deployment without rebuilding the Docker image.

Alternatively — and more reproducibly — bake the config into the image at build time (the Dockerfile `COPY` arg approach) and use ECR image tags as the versioning mechanism.

---

## Service discovery (upstream DNS)

On ECS with `awsvpc` networking, containers cannot reach each other by container name the way Docker Compose does. You have two options:

**Option A — AWS Cloud Map (recommended)**

Register each ECS service with Cloud Map. Each service gets a DNS name like `web-ui.kdcube.local`. Update your nginx `upstream` blocks:

```nginx
upstream web_ui      { server web-ui.kdcube.local:80; }
upstream chat_api    { server chat-ingress.kdcube.local:8010; }
upstream chat_proc   { server chat-proc.kdcube.local:8020; }
upstream proxy_login { server proxylogin.kdcube.local:80; }
```

**Option B — ALB per service (simpler for fewer services)**

Place each backend behind its own internal ALB or NLB. Use the ALB DNS name in the nginx upstream. Adds latency but avoids Cloud Map setup.

**Important for Lua subrequests:** if `unmask_token()` resolves `proxy_login` via Cloud Map DNS, ensure the Lua resolver is configured:

```nginx
# In http {}
resolver 169.254.169.253 valid=10s;   # VPC DNS resolver (always this IP on AWS)
resolver_timeout 5s;
```

Without this, OpenResty's Lua DNS lookups use the system resolver, which may not honour TTLs correctly and can cause stale upstream addresses after a `proxylogin` task replacement.

---

## ECR: building and pushing the proxy image

```bash
# Authenticate
aws ecr get-login-password --region <region> | \
  docker login --username AWS --password-stdin <account>.dkr.ecr.<region>.amazonaws.com

# Build (same Dockerfile as local/EC2)
docker build \
  --build-arg NGINX_CONFIG_FILE_PATH=deployment/proxy/nginx_proxy_ssl_cognito.conf \
  -t kdcube-web-proxy:latest \
  -f deployment/docker/Dockerfile_ProxyOpenResty \
  .

# Tag and push
docker tag kdcube-web-proxy:latest \
  <account>.dkr.ecr.<region>.amazonaws.com/kdcube-web-proxy:<tag>

docker push \
  <account>.dkr.ecr.<region>.amazonaws.com/kdcube-web-proxy:<tag>
```

Use a release tag (e.g. `v1.2.3`) rather than `latest` for ECS task definitions in production — `latest` will not trigger a redeployment if the digest hasn't changed.

---

## IAM: task role

The proxy task itself needs no AWS API access. The execution role (`ecsTaskExecutionRole`) needs:

```json
{
  "Effect": "Allow",
  "Action": [
    "ecr:GetAuthorizationToken",
    "ecr:BatchCheckLayerAvailability",
    "ecr:GetDownloadUrlForLayer",
    "ecr:BatchGetImage",
    "logs:CreateLogStream",
    "logs:PutLogEvents"
  ],
  "Resource": "*"
}
```

If you are using EFS for the config mount, also add:

```json
{
  "Effect": "Allow",
  "Action": [
    "elasticfilesystem:ClientMount",
    "elasticfilesystem:ClientWrite"
  ],
  "Resource": "arn:aws:elasticfilesystem:<region>:<account>:file-system/<efs-fs-id>"
}
```

The `~/.aws` bind mount used in the EC2 and local deployments is **absent** on ECS. Services that need AWS API access (ingress, proc) use their own task IAM roles instead.

---

## Security groups

| SG | Inbound | Source |
|---|---|---|
| `kdcube-proxy-sg` | TCP 80 | ALB security group |
| `kdcube-proxy-sg` | TCP 443 | — (not needed, ALB terminates) |
| `kdcube-app-sg` (ingress, proc, ui) | TCP 8010, 8020, 80 | `kdcube-proxy-sg` |

Do not open proxy port 80 to `0.0.0.0/0`. All external traffic must flow through the ALB.

---

## EFS for shared bundle storage

If `chat-proc` uses bundles with `ks:` (shared local storage), mount EFS as described in the bundle ops guide:

```
BUNDLE_STORAGE_ROOT=/bundle-storage
```

EFS access point configuration:
- `uid=1000`, `gid=1000` (matches the `appuser` inside the container)
- Mount target in each AZ your ECS tasks run in
- Transit encryption enabled

The proxy itself does not use EFS for storage — only for the optional config file mount described above.

---

## Forced config updates without image rebuild

If you mount the config via EFS rather than baking it into the image:

1. Update the config file on EFS.
2. Force a new ECS deployment: `aws ecs update-service --cluster <cluster> --service kdcube-web-proxy --force-new-deployment`.
3. ECS drains existing tasks and replaces them — the new tasks pick up the updated config.

If you bake the config into the image (recommended for production stability):

1. Rebuild and push a new image tag to ECR.
2. Update the task definition to reference the new image tag.
3. Update the ECS service to use the new task definition revision.

---

## Logging

Nginx access and error logs go to `stdout`/`stderr` in the container. On ECS with the `awslogs` driver they land in CloudWatch Logs under `/ecs/kdcube-web-proxy`. Useful queries:

```
# 4xx/5xx count by status
fields @timestamp, status
| filter status >= 400
| stats count(*) by status
| sort count desc

# Rate-limited requests (429)
fields @timestamp, @message
| filter status = 429

# Slow upstream responses
fields @timestamp, upstream_response_time
| filter upstream_response_time > 10
| sort upstream_response_time desc
```

To include `upstream_response_time` in the log, extend the `log_format` in `nginx.conf`:

```nginx
log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                '$status $body_bytes_sent "$http_referer" '
                '"$http_user_agent" "$http_x_forwarded_for" '
                'rt=$request_time uct=$upstream_connect_time urt=$upstream_response_time';
```

---

## Rollout checklist

- [ ] Uncomment and configure `real_ip_header` / `set_real_ip_from` with ALB CIDR
- [ ] Remove `ssl_certificate` / `ssl_certificate_key` directives (ACM handles TLS)
- [ ] Remove ACME challenge location (not needed with ACM)
- [ ] Remove `listen 443 ssl http2` block (proxy only listens on `:80`)
- [ ] Add VPC DNS `resolver 169.254.169.253` directive if using Lua auth (`unmask_token`)
- [ ] Update nginx `upstream` blocks with Cloud Map DNS names
- [ ] Build and push image to ECR
- [ ] Register task definition with ECS, attach execution role and (if EFS) task role
- [ ] Create ALB target group pointing to port 80, health check path `/`
- [ ] Configure ALB listener: `:443` → forward, `:80` → redirect 301
- [ ] Set security groups: proxy accepts only from ALB SG; backends accept only from proxy SG
- [ ] Enable CloudWatch log group `/ecs/kdcube-web-proxy`

---

## References (code)

- Proxy config (EC2/ECS base): `deployment/docker/custom-ui-managed-infra/nginx_proxy_ssl_cognito.conf`
- Proxy Dockerfile: `deployment/docker/Dockerfile_ProxyOpenResty`
- Bundle storage (EFS): `ks:docs/sdk/bundle/bundle-ops-README.md` — Shared bundle local storage section
- Chat processor task def: `deployment/ecs/task-definitions/chat-proc.json`
- Chat ingress task def: `deployment/ecs/task-definitions/chat-ingress.json`