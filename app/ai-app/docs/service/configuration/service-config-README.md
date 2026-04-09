---
id: ks:docs/service/service-config-README.md
title: "Service Config"
summary: "Runtime configuration for chat services: tenant/project, instance identity, bundles, gateway."
tags: ["service", "configuration", "env", "bundles", "gateway"]
keywords: ["INSTANCE_ID", "AGENTIC_BUNDLES_JSON", "gateway config", "tenant/project"]
see_also:
  - ks:docs/service/README-monitoring-observability.md
  - ks:docs/service/gateway-README.md
  - ks:docs/service/service-and-infrastructure-index-README.md
  - ks:docs/service/cicd/secrets-descriptor-README.md
  - ks:docs/service/configuration/code-config-secrets-README.md
  - ks:docs/service/configuration/descriptor-plain-config-README.md
---
# Service Configuration — Chat Platform

This document summarizes **runtime configuration** for the chat service.  
It focuses on tenant/project/bundle settings, instance identity, and parallelism.

For **sensitive values** (LLM keys, Git tokens, infra passwords, proxylogin client secret),
use the optional `secrets.yaml` workflow described in
[docs/service/cicd/secrets-descriptor-README.md](../cicd/secrets-descriptor-README.md).

For **code usage guidelines** (how to read config/secrets in platform/bundles),
see [docs/service/configuration/code-config-secrets-README.md](code-config-secrets-README.md).

For **runtime reads from descriptor files themselves** (`assembly.yaml` and
`bundles.yaml` mounted at `/config/...`), see
[docs/service/configuration/descriptor-plain-config-README.md](descriptor-plain-config-README.md).

**Secrets note:** Secrets are resolved by the configured runtime provider using
**dot‑path keys** (for example, `services.openai.api_key`). Env vars are legacy
compatibility only.

**Sample env files (per service)**

- Ingress: `deployment/docker/devenv/sample_env/.env.ingress`
- Proc: `deployment/docker/devenv/sample_env/.env.proc`
- Metrics: `deployment/docker/devenv/sample_env/.env.metrics`

## Full Environment Variable Reference (All Services)

This section enumerates **all env vars** used by the local/docker setup.
Source of truth: `deployment/docker/all_in_one_kdcube/sample_env/`.
Descriptions are condensed from the comments in those sample files.

### `.env`
| Key | Description |
|---|---|
| `KDCUBE_CONFIG_DIR` | Workdir layout (config + data + logs) If you keep configs in a separate workdir, point these to that root. |
| `KDCUBE_DATA_DIR` | n/a |
| `KDCUBE_LOGS_DIR` | n/a |
| `HOST_KDCUBE_STORAGE_PATH` | Path on host to mount as a KDCUBE STORAGE (local filesystem or S3). If using S3 in KDCUBE_STORAGE_PATH, not needed |
| `HOST_BUNDLES_PATH` | Host directory containing ALL custom agentic bundles (subfolders, wheels, zips) These extend the chat service with custom Python applications |
| `HOST_BUNDLE_STORAGE_PATH` | Host directory for shared bundle local storage (bundle data; used by ks: resolvers). This is mounted into chat-proc at BUNDLE_STORAGE_ROOT. |
| `HOST_ASSEMBLY_YAML_DESCRIPTOR_PATH` | Assembly descriptor host path. Mounted as `/config/assembly.yaml`. Runtime code may read it via `read_plain(...)`; the CLI also uses it as the local source of truth. |
| `HOST_BUNDLES_DESCRIPTOR_PATH` | Bundles descriptor host path. Mounted as `/config/bundles.yaml`. Proc may use it as `AGENTIC_BUNDLES_JSON`, and runtime code may read it via `read_plain("b:...")`. |
| `HOST_GIT_SSH_KEY_PATH` | Optional SSH key + known_hosts for git bundle pulls (private repos) These files are mounted into the chat-proc container at: /run/secrets/git_ssh_key /run/secrets/git_known_hosts |
| `HOST_GIT_KNOWN_HOSTS_PATH` | n/a |
| `HOST_EXEC_WORKSPACE_PATH` | Temporary workspace for code execution (Docker-in-Docker) |
| `AGENTIC_BUNDLES_ROOT` | Mount path for bundles inside the container DO NOT CHANGE unless you modify AGENTIC_BUNDLES_JSON references |
| `BUNDLE_STORAGE_ROOT` | Shared bundle storage root inside the container (knowledge space root) |
| `UI_BUILD_CONTEXT` | Root of the KDCube ai-app directory (contains deployment/, ui/, src/) This works on Linux/Mac. For Windows, use a Windows-safe path (e.g., D:/path/to/kdcube-ai-app/app/ai-app) |
| `UI_DOCKERFILE_PATH` | Path to Dockerfile_UI (relative to UI_BUILD_CONTEXT) |
| `UI_SOURCE_PATH` | Path to UI source code (relative to UI_BUILD_CONTEXT) This directory should contain package.json and your UI application |
| `NGINX_UI_CONFIG_FILE_PATH` | Path to nginx configuration for UI (relative to UI_BUILD_CONTEXT) This configures how nginx serves your built UI application |
| `PATH_TO_FRONTEND_CONFIG_JSON` | Runtime config is mounted into the UI container as /usr/share/nginx/html/config.json Suggested path (workdir): <workdir>/config/frontend.config.hardcoded.json |
| `SECRETS_ADMIN_TOKEN` | Secrets sidecar admin token (runtime-only). Used by **proc** to write bundle secrets via admin UI. Set in `.env.proc` as `${SECRETS_ADMIN_TOKEN}` so the runtime token is injected. |
| `SECRETS_READ_TOKENS` | Comma-separated list of read tokens accepted by the sidecar. |
| `SECRETS_TOKEN_INGRESS` | Per-service read tokens (runtime-only; leave blank in files). |
| `SECRETS_TOKEN_PROC` | n/a |
| `SECRETS_TOKEN_TTL_SECONDS` | Token lifetime (seconds). `0` = no expiry. |
| `SECRETS_TOKEN_MAX_USES` | Max uses per token. `0` = unlimited. |
| `PROXY_BUILD_CONTEXT` | Common parent directory that can reach both platform and frontend repos |
| `PROXY_DOCKERFILE_PATH` | Path to Dockerfile_ProxyOpenResty (relative to PROXY_BUILD_CONTEXT) This Dockerfile is provided by the platform (OpenResty-based) |
| `NGINX_PROXY_CONFIG_FILE_PATH` | Path to custom nginx proxy configuration (relative to PROXY_BUILD_CONTEXT) Use nginx/conf/nginx_proxy.conf for HTTP or nginx/conf/nginx_proxy_ssl.conf for HTTPS |
| `KDCUBE_UI_PORT` | KDCube Frontend port |
| `CHAT_APP_PORT` | KDCube services. All scaled horizontally via running multiple instances of each service. |
| `CHAT_PROCESSOR_PORT` | n/a |
| `METRICS_PORT` | n/a |
| `POSTGRES_USER` | Local infra (Postgres + Redis containers) |
| `POSTGRES_PASSWORD` | n/a |
| `POSTGRES_DATABASE` | n/a |
| `POSTGRES_PORT` | n/a |
| `PGUSER` | Optional aliases for tooling that expects PG* vars |
| `PGPASSWORD` | n/a |
| `PGDATABASE` | n/a |
| `POSTGRES_MAX_CONNECTIONS` | n/a |
| `REDIS_PASSWORD` | n/a |

## Secrets sidecar roles (setter vs getter)

Secrets resolution is provider-based:

- `in-memory` for process-local operational values
- `secrets-service` for the local `kdcube-secrets` sidecar
- `aws-sm` for AWS Secrets Manager
- `secrets-file` for descriptor-backed runtime loading from `secrets.yaml` and
  `bundles.secrets.yaml` via the storage backend (`file://...` or `s3://...`)

The local secrets sidecar provider supports two roles:

- **Getter (read)**: any service that calls `get_secret()` needs a read token.
  Set `SECRETS_URL` and `SECRETS_TOKEN` in the service env.
- **Setter (write)**: the **proc** service (bundle admin UI) writes secrets to
  the sidecar and therefore needs `SECRETS_ADMIN_TOKEN` in `.env.proc`
  (usually set to `${SECRETS_ADMIN_TOKEN}` so the CLI injects the runtime token).

`SECRETS_PROVIDER` is rendered from `assembly.yaml` (`secrets.provider`).
Gateway config must not carry secrets backend settings.

If `SECRETS_PROVIDER` is omitted and either `GLOBAL_SECRETS_YAML` or
`BUNDLE_SECRETS_YAML` is set, runtime auto-selects `secrets-file`.

`secrets-file` reads and writes the configured YAML descriptors through the
storage backend. The service therefore needs write access to the referenced
`file://...` path or `s3://...` object prefix if you want admin/UI updates to persist.

Token TTL/uses:
- `SECRETS_TOKEN_TTL_SECONDS=0` and `SECRETS_TOKEN_MAX_USES=0` mean **no expiry**.
- This is required if bundle secrets can be updated and read long after startup.

### `.env.ingress`
| Key | Description |
|---|---|
| `CHAT_APP_PORT` | n/a |
| `GATEWAY_COMPONENT` | n/a |
| `SECRETS_PROVIDER` | Secrets backend: `secrets-service`, `aws-sm`, `secrets-file`, or `in-memory`. Legacy `local` remains accepted as an alias for `secrets-service`. |
| `SECRETS_URL` | Base URL for the local `secrets-service` provider. |
| `SECRETS_TOKEN` | Read token for secrets sidecar (runtime-only; injected by CLI). |
| `SECRETS_ADMIN_TOKEN` | Admin token for **writing** secrets (bundle admin UI). Set to `${SECRETS_ADMIN_TOKEN}`. |
| `GLOBAL_SECRETS_YAML` | Read-only global secrets descriptor for `secrets-file`; accepts `file://...` or `s3://...`. |
| `BUNDLE_SECRETS_YAML` | Read-only bundle secrets descriptor for `secrets-file`; accepts `file://...` or `s3://...`. |
| `SECRETS_TOKEN_TTL_SECONDS` | Token lifetime (seconds). `0` = no expiry. |
| `SECRETS_TOKEN_MAX_USES` | Max uses per token. `0` = unlimited. |
| `SECRETS_ADMIN_TOKEN` | Optional admin token for writing secrets via the bundle admin UI. |
| `LINK_PREVIEW_ENABLED` | Enable link preview endpoint (ingress disables by default). |
| `GATEWAY_CONFIG_JSON` | Gateway config JSON (see Gateway Config section above). |
| `KDCUBE_GATEWAY_DESCRIPTOR_PATH` | Path to `gateway.yaml` used by the CLI to render `GATEWAY_CONFIG_JSON`. |
| `GATEWAY_CONFIG_FORCE_ENV_ON_STARTUP` | n/a |
| `POSTGRES_HOST` | n/a |
| `POSTGRES_PORT` | n/a |
| `POSTGRES_DATABASE` | n/a |
| `POSTGRES_USER` | n/a |
| `POSTGRES_PASSWORD` | n/a |
| `POSTGRES_SSL` | n/a |
| `REDIS_URL` | Managed Redis endpoint (reachable from containers) |
| `CB_RELAY_IDENTITY` | n/a |
| `UVICORN_RELOAD` | Dev-only: enable auto-reload when running web_app.py directly (0/1). |
| `HEARTBEAT_INTERVAL` | n/a |
| `KDCUBE_STORAGE_PATH` | Storage backend root (file:///... or s3://...). |
| `OPENAI_API_KEY` | Services credentials Ext services |
| `HUGGING_FACE_API_TOKEN` | n/a |
| `ANTHROPIC_API_KEY` | n/a |
| `BRAVE_API_KEY` | n/a |
| `GEMINI_CACHE_ENABLED` | n/a |
| `GEMINI_CACHE_TTL_SECONDS` | n/a |
| `DEFAULT_LLM_MODEL_ID` | n/a |
| `DEFAULT_EMBEDDING_MODEL_ID` | n/a |
| `AUTH_PROVIDER` | Auth Auth provider, simple|cognito |
| `ID_TOKEN_HEADER_NAME` | For non-simple auth, id token must be sent by client in addition to the access token in the auth header. |
| `STREAM_ID_HEADER_NAME` | Header carrying the connected peer/stream id for REST requests that need peer-targeted communicator delivery. |
| `AUTH_TOKEN_COOKIE_NAME` | n/a |
| `ID_TOKEN_COOKIE_NAME` | n/a |
| `COGNITO_REGION` | # Cognito specifics |
| `COGNITO_USER_POOL_ID` | n/a |
| `COGNITO_APP_CLIENT_ID` | n/a |
| `COGNITO_SERVICE_CLIENT_ID` | ideally, separate client for service users. can be the same as COGNITO_APP_CLIENT_ID |
| `JWKS_CACHE_TTL_SECONDS` | 24h JWKS cache. Not used |
| `OIDC_SERVICE_ADMIN_USERNAME` | # Service account settings |
| `OIDC_SERVICE_ADMIN_PASSWORD` | n/a |
| `ODIC_SERVICE_USER_EMAIL` | n/a |
| `APP_AV_SCAN` | AV 1 to enable, 0 to disable |
| `APP_AV_TIMEOUT_S` | scan timeout per file |
| `CLAMAV_HOST` | n/a |
| `CLAMAV_PORT` | n/a |
| `OPEX_AGG_CRON` | Analytics scheduled Analytics. Accounting events aggregation schedule |
| `STRIPE_RECONCILE_ENABLED` | Enable/disable Stripe reconcile job (default `true`) |
| `STRIPE_RECONCILE_CRON` | Stripe reconcile schedule (default `45 * * * *`) |
| `STRIPE_RECONCILE_LOCK_TTL_SECONDS` | Distributed lock TTL for reconcile job (default `900`) |
| `SUBSCRIPTION_ROLLOVER_ENABLED` | Enable/disable subscription rollover job (default `true`) |
| `SUBSCRIPTION_ROLLOVER_CRON` | Subscription rollover schedule (default `15 * * * *`) |
| `SUBSCRIPTION_ROLLOVER_LOCK_TTL_SECONDS` | Distributed lock TTL for rollover job (default `900`) |
| `SUBSCRIPTION_ROLLOVER_SWEEP_LIMIT` | Max subscriptions processed per rollover run (default `500`) |
| `LOG_LEVEL` | Log |
| `LOG_MAX_MB` | n/a |
| `LOG_BACKUP_COUNT` | n/a |
| `LOG_DIR` | n/a |
| `LOG_FILE_PREFIX` | n/a |
| `CORS_CONFIG` | to disable CORS - remove env var or set it empty all options are optional to enable CORS with all defaults CORS_CONFIG={} |
| `AWS_REGION` | AWS use AWS from the container AWS_PROFILE=... |
| `AWS_DEFAULT_REGION` | n/a |
| `AWS_SDK_LOAD_CONFIG` | optional: make boto3 read ~/.aws/config if present (harmless) |
| `NO_PROXY` | EC2 stuff. If you run dockercompose on EC2. Running with managed services don't proxy IMDS |
| `AWS_EC2_METADATA_DISABLED` | make sure SDKs don’t disable IMDS accidentally |

### `.env.proc`
| Key | Description |
|---|---|
| `CHAT_PROCESSOR_PORT` | n/a |
| `GATEWAY_COMPONENT` | n/a |
| `SECRETS_PROVIDER` | Secrets backend: `secrets-service`, `aws-sm`, `secrets-file`, or `in-memory`. Legacy `local` remains accepted as an alias for `secrets-service`. |
| `SECRETS_URL` | Base URL for the local `secrets-service` provider. |
| `SECRETS_TOKEN` | Read token for the configured `secrets-service` provider. |
| `GLOBAL_SECRETS_YAML` | Read-only global secrets descriptor for `secrets-file`; accepts `file://...` or `s3://...`. |
| `BUNDLE_SECRETS_YAML` | Read-only bundle secrets descriptor for `secrets-file`; accepts `file://...` or `s3://...`. |
| `GATEWAY_CONFIG_JSON` | Gateway config JSON (see Gateway Config section above). |
| `KDCUBE_GATEWAY_DESCRIPTOR_PATH` | Path to `gateway.yaml` used by the CLI to render `GATEWAY_CONFIG_JSON`. |
| `GATEWAY_CONFIG_FORCE_ENV_ON_STARTUP` | n/a |
| `POSTGRES_HOST` | n/a |
| `POSTGRES_PORT` | n/a |
| `POSTGRES_DATABASE` | n/a |
| `POSTGRES_USER` | n/a |
| `POSTGRES_PASSWORD` | n/a |
| `POSTGRES_SSL` | n/a |
| `REDIS_URL` | Managed Redis endpoint (reachable from containers) |
| `CB_RELAY_IDENTITY` | n/a |
| `CHAT_TASK_TIMEOUT_SEC` | Per-task timeout (seconds). |
| `PROC_CONTAINER_STOP_TIMEOUT_SEC` | Proc container/task stop window (seconds). Keep aligned with ECS `stopTimeout`; proc derives graceful shutdown budget from it. |
| `UVICORN_RELOAD` | Dev-only: enable auto-reload when running web_app.py directly (0/1). |
| `HEARTBEAT_INTERVAL` | n/a |
| `KDCUBE_STORAGE_PATH` | Storage backend root (file:///... or s3://...). |
| `CB_BUNDLE_STORAGE_URL` | n/a |
| `BUNDLE_STORAGE_ROOT` | Shared bundle local storage (used by ks: resolvers). Must match docker-compose mount. |
| `REACT_WORKSPACE_IMPLEMENTATION` | React workspace backend. `custom` keeps artifact/hosting-backed hydration. `git` enables git-backed `fi:<turn>.files/...` slice hydration. Default is `custom`. |
| `REACT_WORKSPACE_GIT_REPO` | Remote repo used by the git workspace backend. Required when `REACT_WORKSPACE_IMPLEMENTATION=git`. Auth reuses `GIT_HTTP_TOKEN`, `GIT_HTTP_USER`, `GIT_SSH_KEY_PATH`, `GIT_SSH_KNOWN_HOSTS`, and `GIT_SSH_STRICT_HOST_KEY_CHECKING`. |
| `CLAUDE_CODE_SESSION_STORE_IMPLEMENTATION` | Claude Code session-store backend. `local` keeps continuity on local disk only. `git` enables per-conversation bootstrap/publish of the bundle-selected Claude session root. |
| `CLAUDE_CODE_SESSION_GIT_REPO` | Remote repo used by the Claude Code git-backed session store. Required when `CLAUDE_CODE_SESSION_STORE_IMPLEMENTATION=git`. |
| `OPENAI_API_KEY` | Services credentials Ext services |
| `HUGGING_FACE_API_TOKEN` | n/a |
| `ANTHROPIC_API_KEY` | n/a |
| `BRAVE_API_KEY` | n/a |
| `GEMINI_CACHE_ENABLED` | n/a |
| `GEMINI_CACHE_TTL_SECONDS` | n/a |
| `DEFAULT_LLM_MODEL_ID` | n/a |
| `DEFAULT_EMBEDDING_MODEL_ID` | n/a |
| `AUTH_PROVIDER` | Auth Auth provider, simple|cognito |
| `ID_TOKEN_HEADER_NAME` | For non-simple auth, id token must be sent by client in addition to the access token in the auth header. |
| `STREAM_ID_HEADER_NAME` | Header carrying the connected peer/stream id for REST requests that need peer-targeted communicator delivery. |
| `AUTH_TOKEN_COOKIE_NAME` | n/a |
| `ID_TOKEN_COOKIE_NAME` | n/a |
| `COGNITO_REGION` | # Cognito specifics |
| `COGNITO_USER_POOL_ID` | n/a |
| `COGNITO_APP_CLIENT_ID` | n/a |
| `COGNITO_SERVICE_CLIENT_ID` | ideally, separate client for service users. can be the same as COGNITO_APP_CLIENT_ID |
| `JWKS_CACHE_TTL_SECONDS` | 24h JWKS cache. Not used |
| `OIDC_SERVICE_ADMIN_USERNAME` | # Service account settings |
| `OIDC_SERVICE_ADMIN_PASSWORD` | n/a |
| `ODIC_SERVICE_USER_EMAIL` | n/a |
| `EXEC_WORKSPACE_ROOT` | Exec |
| `EXEC_RUNTIME_MODE` | Exec runtime selector for proc-side code execution. Typical values: `docker`, `fargate`. |
| `PY_CODE_EXEC_IMAGE` | n/a |
| `PY_CODE_EXEC_TIMEOUT` | n/a |
| `PY_CODE_EXEC_NETWORK_MODE` | n/a |
| `FARGATE_EXEC_ENABLED` | Enable distributed Fargate exec path. |
| `FARGATE_CLUSTER` | ECS cluster ARN/name for distributed exec tasks. |
| `FARGATE_TASK_DEFINITION` | ECS task definition used for distributed exec tasks. |
| `FARGATE_CONTAINER_NAME` | Container name inside the exec task definition. |
| `FARGATE_SUBNETS` | Comma-separated subnets for `awsvpc` task launch. |
| `FARGATE_SECURITY_GROUPS` | Comma-separated security groups for `awsvpc` task launch. |
| `FARGATE_ASSIGN_PUBLIC_IP` | `ENABLED` or `DISABLED` for distributed exec tasks. |
| `FARGATE_LAUNCH_TYPE` | Launch type for exec tasks. Typical value: `FARGATE`. |
| `FARGATE_PLATFORM_VERSION` | Optional ECS platform version for exec tasks. |
| `TOOLS_WEB_SEARCH_FETCH_CONTENT` | Tools |
| `WEB_FETCH_RESOURCES_MEDIUM` | Medium credentials (uid and sid from your browser after logging in.) |
| `WEB_SEARCH_AGENTIC_THINKING_BUDGET` | n/a |
| `WEB_SEARCH_PRIMARY_BACKEND` | Is adaptive (best effort graceful service degradation) backends supported: duckduckgo|brave|hybrid |
| `WEB_SEARCH_BACKEND` | n/a |
| `WEB_SEARCH_HYBRID_MODE` | # Hybrid mode (optional, defaults to "sequential"). sequential|parallel |
| `WEB_SEARCH_SEGMENTER` | n/a |
| `MCP_CACHE_TTL_SECONDS` | n/a |
| `ACCOUNTING_SERVICES` | n/a |
| `AGENTIC_BUNDLES_JSON` | Bundles descriptor (JSON/YAML). Common value inside container: `/config/bundles.yaml`. This path is mounted from `HOST_BUNDLES_DESCRIPTOR_PATH` in `.env`. |
| `BUNDLES_INCLUDE_EXAMPLES` | Include built-in example bundles from sdk/examples/bundles (default: 1) |
| `BUNDLE_CLEANUP_ENABLED` | Bundle cleanup / ref tracking Enable periodic bundle cleanup loop (uses Redis locks). |
| `BUNDLE_CLEANUP_INTERVAL_SECONDS` | Cleanup interval (seconds). |
| `BUNDLE_CLEANUP_LOCK_TTL_SECONDS` | Cleanup lock TTL (seconds). |
| `BUNDLE_REF_TTL_SECONDS` | Active bundle ref TTL (seconds). |
| `BUNDLES_FORCE_ENV_ON_STARTUP` | Force bundles registry overwrite from env on startup (processor only). |
| `BUNDLES_FORCE_ENV_LOCK_TTL_SECONDS` | n/a |
| `BUNDLES_PRELOAD_ON_START` | Eagerly load all configured bundle modules and run on_bundle_load hooks at proc startup. Eliminates cold start on first request. Proc health returns 503 until preload completes (default: `0`). |
| `AGENTIC_BUNDLES_ROOT` | Agentic bundles root inside the container. All bundles (subfolders, wheels, zips) will be linked there. The paths in the AGENTIC_BUNDLES_JSON must start with this root. Container bundle root (from .env.proc): Docker/ECS: set AGENTIC_BUNDLES_ROOT=/bundles and mount your host/EFS path there. Host path for mounts lives in .env (HOST_BUNDLES_PATH). |
| `BUNDLE_GIT_RESOLUTION_ENABLED` | Git bundle resolution Disable git bundle resolution until git bundles are fully configured. |
| `BUNDLE_GIT_ATOMIC` | Atomic checkout (clone to temp dir then rename) |
| `BUNDLE_GIT_ALWAYS_PULL` | Always pull even if path exists (if using branch heads) |
| `BUNDLE_GIT_REDIS_LOCK` | Redis lock for git pulls (per instance; key includes INSTANCE_ID) |
| `BUNDLE_GIT_REDIS_LOCK_TTL_SECONDS` | n/a |
| `BUNDLE_GIT_REDIS_LOCK_WAIT_SECONDS` | n/a |
| `BUNDLE_GIT_PREFETCH_ENABLED` | Prefetch git bundles to gate readiness |
| `BUNDLE_GIT_PREFETCH_INTERVAL_SECONDS` | n/a |
| `BUNDLE_GIT_FAIL_BACKOFF_SECONDS` | Backoff after git failures |
| `BUNDLE_GIT_FAIL_MAX_BACKOFF_SECONDS` | n/a |
| `BUNDLE_GIT_KEEP` | Shallow clone settings (optional) BUNDLE_GIT_CLONE_DEPTH=50 BUNDLE_GIT_SHALLOW=1 Cleanup policy for old git bundles |
| `BUNDLE_GIT_TTL_HOURS` | n/a |
| `GIT_SSH_KEY_PATH` | Optional SSH auth (private repos) Container paths are fixed by docker-compose mounts: /run/secrets/git_ssh_key /run/secrets/git_known_hosts |
| `GIT_SSH_KNOWN_HOSTS` | n/a |
| `GIT_SSH_STRICT_HOST_KEY_CHECKING` | n/a |
| `LOG_LEVEL` | Log |
| `LOG_MAX_MB` | n/a |
| `LOG_BACKUP_COUNT` | n/a |
| `LOG_DIR` | n/a |
| `LOG_FILE_PREFIX` | n/a |
| `CORS_CONFIG` | to disable CORS - remove env var or set it empty all options are optional to enable CORS with all defaults CORS_CONFIG={} |
| `AWS_REGION` | AWS use AWS from the container AWS_PROFILE=... |
| `AWS_DEFAULT_REGION` | n/a |
| `AWS_SDK_LOAD_CONFIG` | optional: make boto3 read ~/.aws/config if present (harmless) |
| `NO_PROXY` | EC2 stuff. If you run dockercompose on EC2. Running with managed services don't proxy IMDS |
| `AWS_EC2_METADATA_DISABLED` | make sure SDKs don’t disable IMDS accidentally |

### Assembly -> React workspace env mapping

The reference assembly descriptor may declare:

```yaml
storage:
  workspace:
    type: git      # or custom
    repo: https://github.com/org/private-workspace.git
```

The CLI installer maps that to:

- `REACT_WORKSPACE_IMPLEMENTATION`
- `REACT_WORKSPACE_GIT_REPO`

`repo` is only meaningful when `type=git`.

### Assembly -> Claude Code session-store env mapping

The reference assembly descriptor may declare:

```yaml
storage:
  claude_code_session:
    type: git      # or local
    repo: https://github.com/org/private-claude-session-store.git
```

The CLI installer maps that to:

- `CLAUDE_CODE_SESSION_STORE_IMPLEMENTATION`
- `CLAUDE_CODE_SESSION_GIT_REPO`

`repo` is only meaningful when `type=git`.

### `.env.metrics`
| Key | Description |
|---|---|
| `METRICS_PORT` | n/a |
| `METRICS_MODE` | n/a |
| `GATEWAY_CONFIG_JSON` | Gateway config JSON (see Gateway Config section above). |
| `KDCUBE_GATEWAY_DESCRIPTOR_PATH` | Path to `gateway.yaml` used by the CLI to render `GATEWAY_CONFIG_JSON`. |
| `GATEWAY_CONFIG_FORCE_ENV_ON_STARTUP` | n/a |
| `REDIS_URL` | Redis endpoint (reachable from containers) |
| `METRICS_SCHEDULER_ENABLED` | Scheduler/export |
| `METRICS_EXPORT_INTERVAL_SEC` | n/a |
| `METRICS_EXPORT_ON_START` | n/a |
| `METRICS_PROM_SCRAPE_TTL_SEC` | Prometheus scrape cache TTL (seconds) |
| `METRICS_EXPORT_CLOUDWATCH` | CloudWatch export (optional) |
| `METRICS_CLOUDWATCH_NAMESPACE` | n/a |
| `METRICS_EXPORT_PROMETHEUS_PUSH` | Prometheus pushgateway export (optional) |
| `METRICS_PROM_JOB_NAME` | METRICS_PROM_PUSHGATEWAY_URL=http://pushgateway:9091 |

### `.env.postgres.setup`
| Key | Description |
|---|---|
| `POSTGRES_USER` | n/a |
| `POSTGRES_PASSWORD` | n/a |
| `POSTGRES_DATABASE` | n/a |
| `POSTGRES_PORT` | n/a |
| `POSTGRES_SSL` | n/a |
| `PGADMIN_DEFAULT_EMAIL` | PgAdmin defaults (used by pgadmin container) |
| `PGADMIN_DEFAULT_PASSWORD` | n/a |
| `TENANT_ID` | Project bootstrap (creates schema/tenant/project as needed) |
| `PROJECT_ID` | n/a |

### `.env.proxylogin`
| Key | Description |
|---|---|
| `STORAGE_TYPE` | Redis |
| `REDIS_KEYPREFIX` | n/a |
| `REDIS_URL` | In a docker-compose env_file, Docker does not expand ${REDIS_PASSWORD} or ${REDIS_HOST}. |
| `RATELIMITER_STORAGE` | n/a |
| `TOKEN_COOKIES_SAMESITE` | Cookies mode -- HTTP_CORS_ENABLED - not needed |
| `TOKEN_COOKIES_DOMAIN` | n/a |
| `TOKEN_MASQUERADE` | n/a |
| `COGNITO_CLIENTID` | n/a |
| `COGNITO_CLIENTSECRET` | n/a |
| `COGNITO_USERPOOLID` | n/a |
| `COGNITO_JWKSISSUER` | n/a |
| `COGNITO_JWKSSIGNINGKEYURL` | n/a |
| `PASSWORD_RESET_COMPANY` | n/a |
| `PASSWORD_RESET_SENDER` | n/a |
| `PASSWORD_RESET_TEMPLATENAME` | n/a |
| `PASSWORD_RESET_REDIRECTURL` | n/a |
| `HTTP_URLBASE` | n/a |
| `LOGGING_DEV` | n/a |
| `AWS_REGION` | AWS |
| `AWS_DEFAULT_REGION` | n/a |

**Short pitch (capacity + limits)**  
The chat service is **rate‑limited and capacity‑limited** by design:

- Requests are admitted only if the system has capacity (gateway + queue backpressure).
- Excess load is **rejected early** with `queue.enqueue_rejected`.
- Concurrency limits keep each processor stable under load.

For gateway‑level rate limits and backpressure configuration, see `docs/service/gateway-README.md`.
If you use a `gateway.yaml`, the CLI renders it into `GATEWAY_CONFIG_JSON`.
See: [gateway-config-README.md](../cicd/gateway-config-README.md).
## Gateway Config (Required)

Tenant/project **must** be provided via `GATEWAY_CONFIG_JSON` (per tenant/project).
There are no supported env fallbacks for tenant/project anymore.

### Guarded REST endpoints (current defaults)
The gateway uses **guarded REST patterns** to decide which REST endpoints are
treated as ingress (rate limit + backpressure) vs read‑only (session only).

**Ingress (chat REST)**
- `^/resources/link-preview$`
- `^/resources/by-rn$`
- `^/conversations/[^/]+/[^/]+/[^/]+/fetch$`
- `^/conversations/[^/]+/[^/]+/turns-with-feedbacks$`
- `^/conversations/[^/]+/[^/]+/feedback/conversations-in-period$`

**Processor (integrations)**
- `^/integrations/bundles/[^/]+/[^/]+/operations/[^/]+$`
- `^/integrations/bundles/[^/]+/[^/]+/[^/]+/operations/[^/]+$`
- `^/integrations/bundles/[^/]+/[^/]+/[^/]+/public/[^/]+$`
- `^/integrations/bundles/[^/]+/[^/]+/[^/]+/widgets/[^/]+$`

These defaults are used when no `guarded_rest_patterns` are provided in
`GATEWAY_CONFIG_JSON`.

### Where the patterns come from (and why you see the same list)
If Redis already contains a **flat** `guarded_rest_patterns` list (legacy), the UI
will show the same list for all components. To make it component‑specific,
store a **component‑scoped** object (see example below) and then:
1. Set `GATEWAY_CONFIG_JSON` with the component‑scoped lists.
2. Use **Reset to Env** in the admin UI (or clear cached config + restart).

### Precedence (critical)
On startup, gateway config is loaded in this order:
1. **Redis cache** for the tenant/project (if present)
2. **Env defaults** / `GATEWAY_CONFIG_JSON`

To enforce env config during deployment, **clear cached config** or call
`/admin/gateway/reset-config` after setting `GATEWAY_CONFIG_JSON`.

**CICD / forced env mode:** set `GATEWAY_CONFIG_FORCE_ENV_ON_STARTUP=1` to
overwrite the cached config on every service start. This forces the
effective config to match `GATEWAY_CONFIG_JSON` in CI/CD (and broadcasts
the update to other replicas).

### Admin update payloads (env shape vs component patch)
`/admin/gateway/update-config` and `/admin/gateway/validate-config` accept:

- **Full gateway config** (same shape as `GATEWAY_CONFIG_JSON`).
  Include `profile` and the component‑scoped sections (`service_capacity`,
  `backpressure`, `rate_limits`, `pools`, `limits`, and optional pattern lists).
- **Component patch** with `component` + partial sections (used by the admin UI).

If you send a full config, omit `component` (or wrap the config in
`raw_config`) to avoid patch semantics.

### Top-level keys (required unless noted)

| Key                     | Required | Purpose                                                           |
|-------------------------|----------|-------------------------------------------------------------------|
| `tenant`                | ✅        | Tenant scope for Redis keys, bundles, and control‑plane events    |
| `project`               | ✅        | Project scope for Redis keys, bundles, and control‑plane events   |
| `profile`               | ➖        | `development` / `production` (defaults to development)            |
| `guarded_rest_patterns` | ➖        | Regexes for protected REST routes (rate limit + backpressure)      |
| `bypass_throttling_patterns` | ➖   | Regexes for public endpoints that should **skip rate limiting** (e.g., Stripe webhooks) |

### Component-aware sections

Each section can be **flat** or **component‑scoped** (`ingress`, `proc`).  
When component‑scoped, each service reads its own subsection based on `GATEWAY_COMPONENT`.

| Section                       | Keys (examples)                                                                            | Purpose                                                     |
|-------------------------------|--------------------------------------------------------------------------------------------|-------------------------------------------------------------|
| `service_capacity`            | `processes_per_instance`, `concurrent_requests_per_process`, `avg_processing_time_seconds` | Capacity sizing. Used for backpressure math and validation. |
| `backpressure`                | `capacity_buffer`, `queue_depth_multiplier`, thresholds, `capacity_source_component`       | Queue/backpressure settings and capacity source selector.   |
| `rate_limits`                 | role limits (`hourly`, `burst`, `burst_window`)                                            | Per‑role rate limiting (per session).                       |
| `pools`                       | `pg_pool_min_size`, `pg_pool_max_size`, `redis_max_connections`, `pg_max_connections`      | Pool sizing per component; optional DB max for warnings.    |
| `limits`                      | `max_sse_connections_per_instance`, `max_integrations_ops_concurrency`, `max_queue_size`   | Soft limits for ingress/proc.                               |
| `guarded_rest_patterns`       | regex list                                                                           | REST endpoints gated by gateway (rate limit + backpressure). |
| `bypass_throttling_patterns`  | regex list                                                                    | REST endpoints that skip rate limiting (still routed + logged). |
| `redis`                       | `sse_stats_ttl_seconds`, `sse_stats_max_age_seconds`                                       | Redis‑based SSE stats retention.                            |

Pattern styles (strict vs prefix‑tolerant) are documented in
[docs/service/gateway-README.md](gateway-README.md).

### Example (readable, component‑scoped)

```json
{
  "tenant": "tenant-id",
  "project": "project-id",
  "profile": "development",
  "guarded_rest_patterns": {
    "ingress": [
      "^/resources/link-preview$",
      "^/resources/by-rn$",
      "^/conversations/[^/]+/[^/]+/[^/]+/fetch$",
      "^/conversations/[^/]+/[^/]+/turns-with-feedbacks$",
      "^/conversations/[^/]+/[^/]+/feedback/conversations-in-period$"
    ],
    "proc": [
      "^/integrations/bundles/[^/]+/[^/]+/operations/[^/]+$",
      "^/integrations/bundles/[^/]+/[^/]+/[^/]+/operations/[^/]+$",
      "^/integrations/bundles/[^/]+/[^/]+/[^/]+/public/[^/]+$",
      "^/integrations/bundles/[^/]+/[^/]+/[^/]+/widgets/[^/]+$"
    ]
  },
  "bypass_throttling_patterns": {
    "ingress": [
      "^/webhooks/stripe$"
    ],
    "proc": []
  },
  "service_capacity": {
    "ingress": {
      "processes_per_instance": 2
    },
    "proc": {
      "concurrent_requests_per_process": 8,
      "processes_per_instance": 4,
      "avg_processing_time_seconds": 25
    }
  },
  "backpressure": {
    "capacity_source_component": "proc",
    "ingress": {
      "capacity_buffer": 0.1,
      "queue_depth_multiplier": 3,
      "anonymous_pressure_threshold": 0.6,
      "registered_pressure_threshold": 0.85,
      "paid_pressure_threshold": 0.9,
      "hard_limit_threshold": 0.98
    },
    "proc": {
      "capacity_buffer": 0.1,
      "queue_depth_multiplier": 3,
      "anonymous_pressure_threshold": 0.6,
      "registered_pressure_threshold": 0.85,
      "paid_pressure_threshold": 0.9,
      "hard_limit_threshold": 0.98
    }
  },
  "rate_limits": {
    "ingress": {
      "anonymous": { "hourly": 120, "burst": 10, "burst_window": 60 },
      "registered": { "hourly": 2000, "burst": 100, "burst_window": 60 },
      "paid": { "hourly": 4000, "burst": 150, "burst_window": 60 },
      "privileged": { "hourly": -1, "burst": 300, "burst_window": 60 }
    },
    "proc": {
      "anonymous": { "hourly": 120, "burst": 10, "burst_window": 60 },
      "registered": { "hourly": 2000, "burst": 100, "burst_window": 60 },
      "paid": { "hourly": 4000, "burst": 150, "burst_window": 60 },
      "privileged": { "hourly": -1, "burst": 300, "burst_window": 60 }
    }
  },
  "pools": {
    "ingress": { "pg_pool_min_size": 0, "pg_pool_max_size": 4, "redis_max_connections": 20 },
    "proc": { "pg_pool_min_size": 0, "pg_pool_max_size": 8, "redis_max_connections": 40 },
    "pg_max_connections": 100
  },
  "limits": {
    "ingress": { "max_sse_connections_per_instance": 200 },
    "proc": { "max_integrations_ops_concurrency": 200, "max_queue_size": 100 }
  },
  "redis": {
    "sse_stats_ttl_seconds": 60,
    "sse_stats_max_age_seconds": 120
  }
}
```

## Core Connectivity (All Services)

### Storage (KDCUBE_STORAGE_PATH)
KDCube writes **artifacts, accounting, analytics, and optional execution snapshots**
under `KDCUBE_STORAGE_PATH`.

Supported schemes:
- Local FS: `file:///.../kdcube-storage`
- S3: `s3://<bucket>/<prefix>`

Storage layout and paths:
https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/sdk/storage/sdk-store-README.md

Conversation artifacts and turn workspace:
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/sdk/agents/react/conversation-artifacts-README.md
- https://github.com/kdcube/kdcube-ai-app/blob/main/app/ai-app/docs/sdk/agents/react/react-turn-workspace-README.md

### Postgres

| Setting              | Required | Purpose |
|----------------------|----------|---------|
| `POSTGRES_HOST`      | ✅        | Hostname of Postgres (managed or local) |
| `POSTGRES_PORT`      | ✅        | Port (default 5432) |
| `POSTGRES_DATABASE`  | ✅        | DB name |
| `POSTGRES_USER`      | ✅        | DB user |
| `POSTGRES_PASSWORD`  | ✅        | DB password |
| `POSTGRES_SSL`       | ➖        | `True` if managed DB requires SSL |

### Redis

| Setting            | Required | Purpose |
|--------------------|----------|---------|
| `REDIS_URL`        | ✅        | Full Redis connection URL |
| `REDIS_HOST`       | ➖        | Host, used to build `REDIS_URL` if not provided |
| `REDIS_PORT`       | ➖        | Port (default 6379) |
| `REDIS_PASSWORD`   | ➖        | Password (if required) |
| `REDIS_DB`         | ➖        | DB index (default 0) |

### Storage

| Setting                 | Required | Purpose                                                       |
|-------------------------|---------|---------------------------------------------------------------|
| `KDCUBE_STORAGE_PATH`   | ✅       | Storage root (local FS `file:///...` or `s3://bucket/path`)   |
| `CB_BUNDLE_STORAGE_URL` | ➖       | Bundle storage URL (proc only; defaults to storage path)      |
| `REACT_WORKSPACE_GIT_REPO` | ➖    | Remote git repo used as the authoritative backup/version store for React git-backed workspace lineages (proc/runtime only) |
| `CLAUDE_CODE_SESSION_GIT_REPO` | ➖ | Remote git repo used as the authoritative backup/version store for Claude Code per-conversation session roots (proc/runtime only) |

`REACT_WORKSPACE_GIT_REPO` and `CLAUDE_CODE_SESSION_GIT_REPO` use the same git authentication contract already supported for git bundle loading:
- `GIT_HTTP_TOKEN`
- `GIT_HTTP_USER`
- `GIT_SSH_KEY_PATH`
- `GIT_SSH_KNOWN_HOSTS`
- `GIT_SSH_STRICT_HOST_KEY_CHECKING`

### ClamAV (Ingress only)

| Setting             | Required | Purpose |
|---------------------|----------|---------|
| `APP_AV_SCAN`       | ➖        | Enable AV scanning (1/0) |
| `APP_AV_TIMEOUT_S`  | ➖        | Scan timeout per file |
| `CLAMAV_HOST`       | ➖        | ClamAV host |
| `CLAMAV_PORT`       | ➖        | ClamAV port |

### Auth

| Setting                     | Required | Purpose |
|-----------------------------|----------|---------|
| `AUTH_PROVIDER`             | ✅        | `simple` or `cognito` |
| `ID_TOKEN_HEADER_NAME`      | ➖        | Header for id token |
| `STREAM_ID_HEADER_NAME`     | ➖        | Header for connected peer/stream id |
| `AUTH_TOKEN_COOKIE_NAME`    | ➖        | Access token cookie name |
| `ID_TOKEN_COOKIE_NAME`      | ➖        | ID token cookie name |
| `COGNITO_REGION`            | ➖        | Cognito region |
| `COGNITO_USER_POOL_ID`      | ➖        | Cognito user pool |
| `COGNITO_APP_CLIENT_ID`     | ➖        | Cognito client id |
| `COGNITO_SERVICE_CLIENT_ID` | ➖        | Service client id |

### CORS

| Setting        | Required | Purpose |
|----------------|----------|---------|
| `CORS_CONFIG`  | ➖        | JSON config for allowed origins/headers/methods |

### Model Provider Keys (optional)

| Setting                 | Purpose              |
|-------------------------|----------------------|
| `OPENAI_API_KEY`        | OpenAI access        |
| `ANTHROPIC_API_KEY`     | Anthropic access     |
| `GEMINI_API_KEY`        | Google Gemini access |
| `HUGGING_FACE_API_TOKEN`| Hugging Face access  |
| `BRAVE_API_KEY`         | Brave search         |

### Exec (Processor only)

| Setting                     | Default    | Purpose                                                                                                                                                                                     |
|-----------------------------|------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `EXEC_RUNTIME_MODE`         | `docker`   | Default proc-side exec runtime selector. Bundle props can override this per bundle via `execution.runtime.mode`.                                                                           |
| `PY_CODE_EXEC_IMAGE`        | _(unset)_  | Exec runtime image                                                                                                                                                                          |
| `PY_CODE_EXEC_TIMEOUT`      | _(unset)_  | Exec timeout (seconds)                                                                                                                                                                      |
| `PY_CODE_EXEC_NETWORK_MODE` | _(unset)_  | Docker network mode                                                                                                                                                                         |
| `EXEC_WORKSPACE_ROOT`       | _(auto)_   | Local workspace root for per‑turn workdir/outdir. Defaults to `/exec-workspace` inside Docker or `/tmp` on host. Path is created if missing and **must be writable** or the request fails.  |
| `REACT_WORKSPACE_GIT_REPO`  | _(unset)_  | React git-backed workspace remote. The runtime carries it into `RuntimeCtx.workspace_git_repo` so React can reason about the authoritative workspace backup without trying to fetch from exec. |
| `CLAUDE_CODE_SESSION_STORE_IMPLEMENTATION` | `local` | Claude Code session-store mode. `git` enables runtime bootstrap/publish of the bundle-selected Claude session root on regular turns. |
| `CLAUDE_CODE_SESSION_GIT_REPO` | _(unset)_ | Claude Code git-backed session-store remote. Used by the Claude runtime bootstrap layer to rehydrate and publish per-conversation session roots. |
| `FARGATE_EXEC_ENABLED`      | `0`        | Enable distributed exec via ECS/Fargate. When disabled, `EXEC_RUNTIME_MODE=fargate` cannot launch tasks.                                                                                  |
| `FARGATE_CLUSTER`           | _(unset)_  | ECS cluster ARN/name for distributed exec tasks.                                                                                                                                           |
| `FARGATE_TASK_DEFINITION`   | _(unset)_  | ECS task definition for distributed exec tasks.                                                                                                                                            |
| `FARGATE_CONTAINER_NAME`    | `exec`     | Target container name inside the exec task definition.                                                                                                                                     |
| `FARGATE_SUBNETS`           | _(unset)_  | Comma-separated subnet list for `awsvpc` launch configuration.                                                                                                                             |
| `FARGATE_SECURITY_GROUPS`   | _(unset)_  | Comma-separated security-group list for `awsvpc` launch configuration.                                                                                                                     |
| `FARGATE_ASSIGN_PUBLIC_IP`  | `DISABLED` | Whether launched exec tasks receive a public IP.                                                                                                                                           |
| `FARGATE_LAUNCH_TYPE`       | `FARGATE`  | Launch type used for distributed exec tasks.                                                                                                                                               |
| `FARGATE_PLATFORM_VERSION`  | _(unset)_  | Optional ECS platform version for distributed exec tasks.                                                                                                                                  |

## Bundles

These values scope **bundle registries** and **control‑plane events**.

| Setting                  | Default   | Purpose                                                                                         | Used by                           |
|--------------------------|-----------|-------------------------------------------------------------------------------------------------|-----------------------------------|
| `AGENTIC_BUNDLES_JSON`   | _(unset)_ | Seed bundle registry from JSON                                                                  | `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/bundle_store.py`    |
| `BUNDLES_INCLUDE_EXAMPLES` | `1`     | Auto‑add example bundles from `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles`                                            | `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/bundle_store.py`    |
| `BUNDLES_FORCE_ENV_ON_STARTUP` | `0` | Force overwrite Redis registry from `AGENTIC_BUNDLES_JSON` (processor only)                     | `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/bundle_store.py`    |
| `BUNDLES_FORCE_ENV_LOCK_TTL_SECONDS` | `60` | Redis lock TTL for startup env reset                                                     | `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/bundle_store.py`    |
| `HOST_BUNDLES_PATH`      | _(unset)_ | Host path for bundle roots (git‑cloned or manually provisioned). Often mounted into containers. | `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/git_bundle.py`      |
| `AGENTIC_BUNDLES_ROOT`   | _(unset)_ | Container‑visible bundles root (path used by runtime inside container).                         | `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/git_bundle.py`      |
| `BUNDLE_STORAGE_ROOT` | _(unset)_ | Shared local filesystem root for bundle data (used by ks:), default: `<bundles_root>/_bundle_storage`. | `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/bundle_storage.py` |
| `BUNDLE_GIT_ALWAYS_PULL` | `0`       | Force refresh on resolve                                                                        | `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/bundle_registry.py` |
| `BUNDLE_GIT_ATOMIC`      | `1`       | Atomic clone/update                                                                             | `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/git_bundle.py`      |
| `BUNDLE_GIT_SHALLOW`     | `1`       | Shallow clone mode                                                                              | `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/git_bundle.py`      |
| `BUNDLE_GIT_CLONE_DEPTH` | `50`      | Shallow clone depth                                                                             | `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/git_bundle.py`      |
| `BUNDLE_GIT_KEEP`        | `3`       | Keep N old bundle dirs                                                                          | `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/git_bundle.py`      |
| `BUNDLE_GIT_TTL_HOURS`   | `0`       | TTL cleanup for old bundle dirs                                                                 | `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/git_bundle.py`      |
| `BUNDLE_GIT_REDIS_LOCK`  | `0`       | Use Redis lock to serialize git pulls **per instance** (key includes `INSTANCE_ID`)            | `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/git_bundle.py`      |
| `BUNDLE_GIT_REDIS_LOCK_TTL_SECONDS` | `300` | Redis lock TTL for git pulls                                                             | `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/git_bundle.py`      |
| `BUNDLE_GIT_REDIS_LOCK_WAIT_SECONDS` | `60` | Max wait to acquire git lock                                                            | `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/git_bundle.py`      |
| `BUNDLE_GIT_PREFETCH_ENABLED` | `1` | Prefetch git bundles once on startup to gate readiness                         | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/proc/web_app.py`       |
| `BUNDLE_GIT_FAIL_BACKOFF_SECONDS` | `60` | Initial backoff after git failure (cooldown)                                        | `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/git_bundle.py`      |
| `BUNDLE_GIT_FAIL_MAX_BACKOFF_SECONDS` | `300` | Max backoff after repeated failures                                         | `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/git_bundle.py`      |
| `GIT_SSH_COMMAND`        | _(unset)_ | Full SSH command override (optional)                                                            | `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/git_bundle.py`      |
| `GIT_SSH_KEY_PATH`       | _(unset)_ | Path to private SSH key (for private repos)                                                     | `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/git_bundle.py`      |
| `GIT_SSH_KNOWN_HOSTS`    | _(unset)_ | Path to `known_hosts` file (SSH)                                                                | `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/git_bundle.py`      |
| `GIT_SSH_STRICT_HOST_KEY_CHECKING` | _(unset)_ | `yes` / `no`                                                                              | `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/git_bundle.py`      |
| `GIT_HTTP_TOKEN`         | _(unset)_ | HTTPS token for private git repos (uses GIT_ASKPASS)                                             | `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/git_bundle.py`      |
| `GIT_HTTP_USER`          | _(unset)_ | HTTPS username (defaults to `x-access-token`)                                                   | `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/git_bundle.py`      |
| `BUNDLE_REF_TTL_SECONDS` | `3600`    | TTL for active bundle refs                                                                      | `src/kdcube-ai-app/kdcube_ai_app/infra/plugin/bundle_refs.py`     |

**Auth precedence:** if `GIT_HTTP_TOKEN` is set, HTTPS token auth is used and SSH settings are ignored (a warning is logged when both are set).

**Tenant/project scoped channels**

Control‑plane updates and cleanup are published to:

```
kdcube:config:bundles:update:{tenant}:{project}
kdcube:config:bundles:cleanup:{tenant}:{project}
```

Each processor instance only subscribes to its own tenant/project channel.

Bundle properties can also override selected runtime behavior per bundle. Reserved
platform property paths such as `role_models`, `embedding`,
`economics.reservation_amount_dollars`, and `execution.runtime` are documented in
[bundle-platform-properties-README.md](../../sdk/bundle/bundle-platform-properties-README.md).

**Host vs container bundles root**

- `HOST_BUNDLES_PATH` is typically defined in `.env` (docker‑compose) so the host path can be mounted (it does not need to be in `.env.proc`).
- `AGENTIC_BUNDLES_ROOT` is defined in `.env.proc` so the service inside the container knows the path.

**Admin bundle**

The built‑in admin bundle (`kdcube.admin`) lives inside the SDK and is used to serve admin UIs.
It is always present in the registry (auto‑injected if missing). Later it can also provide
product‑level chatbot capabilities.

## Instance Identity

| Setting                | Default           | Purpose | Used by                                                                                                             |
|------------------------|-------------------| --- |---------------------------------------------------------------------------------------------------------------------|
| `INSTANCE_ID`          | `home-instance-1` | Instance identity for heartbeats & monitoring | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/config.py`, `src/kdcube-ai-app/kdcube_ai_app/infra/availability/health_and_heartbeat.py`                                             |
| `HEARTBEAT_INTERVAL`   | `10`              | Heartbeat interval (seconds) | Orchestrator + KB services (`src/kdcube-ai-app/kdcube_ai_app/infra/orchestration/app/dramatiq/resolver.py`, `src/kdcube-ai-app/kdcube_ai_app/apps/knowledge_base/api/resolvers.py`) |

## Parallelism / Capacity

| Setting                                                                     | Default | Purpose                                | Used by                                           |
|-----------------------------------------------------------------------------|---------|----------------------------------------|---------------------------------------------------|
| `GATEWAY_CONFIG_JSON.service_capacity.proc.processes_per_instance`          | `1`     | Proc worker processes per instance     | `src/kdcube-ai-app/kdcube_ai_app/infra/gateway/config.py`, heartbeat expectations |
| `GATEWAY_CONFIG_JSON.service_capacity.proc.concurrent_requests_per_process` | `5`     | Max concurrent chat tasks per proc     | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/processor.py`                          |
| `GATEWAY_CONFIG_JSON.service_capacity.proc.avg_processing_time_seconds`     | `25`    | Capacity math / throughput estimate    | `src/kdcube-ai-app/kdcube_ai_app/infra/gateway/config.py`                         |
| `GATEWAY_CONFIG_JSON.service_capacity.ingress.processes_per_instance`       | `1`     | Ingress worker processes per instance  | `src/kdcube-ai-app/kdcube_ai_app/infra/gateway/config.py`                         |
| `GATEWAY_CONFIG_JSON.limits.proc.max_queue_size`                            | `0`     | Hard queue size limit (0 = disabled)   | `src/kdcube-ai-app/kdcube_ai_app/infra/gateway/backpressure.py`                   |
| `CHAT_TASK_TIMEOUT_SEC`                                                     | `600`   | Per‑task timeout (seconds)             | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/processor.py`                          |
| `PROC_CONTAINER_STOP_TIMEOUT_SEC`                                           | `120`   | Proc container/task stop window        | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/proc/web_app.py`                       |

Notes:
- `PROC_CONTAINER_STOP_TIMEOUT_SEC` should match the deployment/task-definition `stopTimeout`.
- Proc derives `timeout_graceful_shutdown` from this value with a small safety buffer, so raising the app-side drain window requires raising the deployment stop window too.

**Note:** The following are currently **not enforced** in the chat service (present only in examples):

- `ORCHESTRATOR_WORKER_CONCURRENCY`

If/when these are wired into runtime limits, this doc should be updated.

## Queue Rejection (Backpressure)

When queue capacity is exceeded, the enqueue step rejects the request and
returns a **system error** with:

- `error_type`: `queue.enqueue_rejected`
- `http_status`: `503`
- `reason`: one of
  - `queue_size_exceeded` (when `GATEWAY_CONFIG_JSON.limits.proc.max_queue_size` is set and exceeded)
  - `hard_limit_exceeded` / `registered_threshold_exceeded` / `anonymous_threshold_exceeded`

This is emitted from `src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/chat_core.py` and handled in SSE at
`src/kdcube-ai-app/kdcube_ai_app/apps/chat/ingress/sse/chat.py`.

## Metrics & Rolling Windows

The monitoring pipeline stores **rolling metrics** in Redis (tenant/project‑scoped):

- SSE connections (1m/15m/1h/max)
- Queue depth + pressure (1m/15m/1h/max)
- Pool utilization + max in‑use (1m/15m/1h/max)
- Task latency percentiles (queue wait + exec p50/p95/p99)
- Ingress REST latency percentiles (p50/p95/p99)

Retention is **1 hour**. Metrics are exposed via:
`GET /monitoring/system` and the Metrics server ([docs/service/scale/metric-server-README.md](scale/metric-server-README.md)).

## Scheduling (OPEX + Bundle Cleanup)

These settings are now **first‑class** in `Settings` (`src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/config.py`).

| Setting                                  | Default        | Purpose                                   |
|------------------------------------------|----------------|-------------------------------------------|
| `OPEX_AGG_CRON`                          | `0 3 * * *`    | Schedule for daily accounting aggregation |
| `BUNDLE_CLEANUP_ENABLED`                 | `true`         | Enable periodic git bundle cleanup        |
| `BUNDLE_CLEANUP_INTERVAL_SECONDS`        | `3600`         | Cleanup interval                          |
| `BUNDLE_CLEANUP_LOCK_TTL_SECONDS`        | `900`          | Redis lock TTL for cleanup loop           |
| `STRIPE_RECONCILE_ENABLED`               | `true`         | Enable/disable Stripe reconcile job       |
| `STRIPE_RECONCILE_CRON`                  | `45 * * * *`   | Stripe reconcile schedule                 |
| `STRIPE_RECONCILE_LOCK_TTL_SECONDS`      | `900`          | Redis lock TTL for reconcile job          |
| `SUBSCRIPTION_ROLLOVER_ENABLED`          | `true`         | Enable/disable subscription rollover job  |
| `SUBSCRIPTION_ROLLOVER_CRON`             | `15 * * * *`   | Subscription rollover schedule            |
| `SUBSCRIPTION_ROLLOVER_LOCK_TTL_SECONDS` | `900`          | Redis lock TTL for rollover job           |
| `SUBSCRIPTION_ROLLOVER_SWEEP_LIMIT`      | `500`          | Max subscriptions per rollover run        |

The cleanup loop uses Redis locks to avoid multi‑worker collisions.

## Economics

Economics requires PostgreSQL (control‑plane schema) and Redis (rate limiting + analytics).
Stripe and email are optional but recommended for production.

Control plane schema

Deploy the economics schema before enabling control plane endpoints:

- [deploy-kdcube-control-plane.sql](../../src/kdcube-ai-app/kdcube_ai_app/ops/deployment/sql/control_plane/deploy-kdcube-control-plane.sql)

Plan quota seeding

- A master bundle seeds `plan_quota_policies` from `app_quota_policies` on first run.
- After seeding, update limits in the admin UI (Quota Policies card).
- If code defaults change, update DB policies or clear the table to re‑seed.

Stripe configuration

Stripe secrets are managed via the **secrets sidecar** using dot‑path keys (see
[code-config-secrets-README.md](code-config-secrets-README.md)).
Set them in `secrets.yaml` and inject via the CLI — do **not** put them in `.env` files.

| Dot‑path key                    | Purpose                        |
|---------------------------------|--------------------------------|
| `services.stripe.secret_key`    | Stripe API key                 |
| `services.stripe.webhook_secret`| Webhook signature verification |

Legacy env vars (`STRIPE_SECRET_KEY`, `STRIPE_API_KEY`, `STRIPE_WEBHOOK_SECRET`) are
still supported as fallback aliases but are deprecated — use dot‑path keys in new code.

If `services.stripe.webhook_secret` is not set, webhook payloads are accepted without signature verification (not recommended).

Admin email notifications

| Setting          | Default              | Purpose                           |
|------------------|----------------------|-----------------------------------|
| `EMAIL_ENABLED`  | `true`               | Enable admin email notifications  |
| `EMAIL_HOST`     | _(unset)_            | SMTP host                         |
| `EMAIL_PORT`     | `587`                | SMTP port                         |
| `EMAIL_USER`     | _(unset)_            | SMTP username                     |
| `EMAIL_PASSWORD` | _(unset)_            | SMTP password                     |
| `EMAIL_FROM`     | _(EMAIL_USER)_       | From address                      |
| `EMAIL_TO`       | `ops@example.com`    | Default recipient                 |
| `EMAIL_USE_TLS`  | `true`               | Enable TLS                        |

Admin emails are sent for wallet refunds and subscription cancels/reconciles.
