# Service Compose Env (Docker Compose)

| Variable | Purpose | Default | File | Service/Scope |
|---|---|---|---|---|
| `HOST_KDCUBE_STORAGE_PATH` | Host path mounted to `/kdcube-storage` in containers | — | `deployment/docker/all_in_one/sample_env/.env` | docker-compose volumes |
| `HOST_BUNDLES_PATH` | Host path with bundle directories mounted to `/bundles` | — | `deployment/docker/all_in_one/sample_env/.env` | docker-compose volumes |
| `HOST_EXEC_WORKSPACE_PATH` | Host path mounted to `/exec-workspace` for code execution | — | `deployment/docker/all_in_one/sample_env/.env` | docker-compose volumes + executor |
| `AGENTIC_BUNDLES_ROOT` | Bundle root inside containers | — | `deployment/docker/all_in_one/sample_env/.env` | chat/kb/worker |
| `UI_BUILD_CONTEXT` | UI repo root for Docker build | — | `deployment/docker/all_in_one/sample_env/.env` | UI build |
| `UI_DOCKERFILE_PATH` | Path to `Dockerfile_UI` relative to `UI_BUILD_CONTEXT` | — | `deployment/docker/all_in_one/sample_env/.env` | UI build |
| `UI_ENV_FILE_PATH` | Absolute path to UI env file used in build | — | `deployment/docker/all_in_one/sample_env/.env` | UI build |
| `UI_SOURCE_PATH` | UI source directory relative to `UI_BUILD_CONTEXT` | — | `deployment/docker/all_in_one/sample_env/.env` | UI build |
| `UI_ENV_BUILD_RELATIVE` | UI env file path (relative) copied into build | — | `deployment/docker/all_in_one/sample_env/.env` | UI build |
| `NGINX_UI_CONFIG_FILE_PATH` | Nginx config path for UI container | — | `deployment/docker/all_in_one/sample_env/.env` | UI build |
| `PATH_TO_FRONTEND_CONFIG_JSON` | Path to UI config JSON (host path for bind mount) | — | `deployment/docker/all_in_one/sample_env/.env` | UI run |
| `PROXY_BUILD_CONTEXT` | Common parent path for proxy build | — | `deployment/docker/all_in_one/sample_env/.env` | proxy build |
| `PROXY_DOCKERFILE_PATH` | Path to `Dockerfile_Proxy` relative to proxy build context | — | `deployment/docker/all_in_one/sample_env/.env` | proxy build |
| `NGINX_PROXY_CONFIG_FILE_PATH` | Nginx proxy config path relative to build context | — | `deployment/docker/all_in_one/sample_env/.env` | proxy run |
| `INSTANCE_ID` | Service instance identifier | — | `deployment/docker/all_in_one/sample_env/.env` | chat/kb/worker |
| `DEFAULT_PROJECT_NAME` | Default project name | — | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb/worker |
| `DEFAULT_TENANT` | Default tenant id | — | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb/worker |
| `TENANT_ID` | Tenant id (service identity) | — | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb/worker |
| `POSTGRES_USER` | Postgres user | — | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb/worker/postgres |
| `POSTGRES_PASSWORD` | Postgres password | — | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb/worker/postgres |
| `POSTGRES_DATABASE` | Postgres database name | `kdcube` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb/worker/postgres |
| `POSTGRES_PORT` | Postgres port | `5432` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb/worker/postgres |
| `POSTGRES_SSL` | Enable SSL for Postgres | `False` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb/worker |
| `POSTGRES_DB` | Postgres container DB name | `${POSTGRES_DATABASE}` | `deployment/docker/all_in_one/sample_env/.env.backend` | postgres container |
| `PGPORT` | Postgres port for client tools | `${POSTGRES_PORT}` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb/worker |
| `SELF_HOSTED_SERVING_ENDPOINT` | Custom model serving endpoint | `http://localhost:5005` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb/worker |
| `REDIS_PASSWORD` | Redis password | — | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb/worker/redis |
| `REDIS_URL` | Redis connection URL | — | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb/worker |
| `KDCUBE_STORAGE_PATH` | Storage backend path or S3 URI | `file:///kdcube-storage` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb/worker |
| `ORCHESTRATOR_WORKER_CONCURRENCY` | Worker concurrency for orchestrator | `10` | `deployment/docker/all_in_one/sample_env/.env.backend` | worker/orchestrator |
| `CB_ORCHESTRATOR_TYPE` | Orchestrator name | `chatbot` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/worker |
| `CB_RELAY_IDENTITY` | Redis pubsub identity | `kdcube.relay.chatbot` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/worker |
| `DRAMATIQ_PROCESSES` | Dramatiq worker process count | `4` | `deployment/docker/all_in_one/sample_env/.env.backend` | worker |
| `MAX_QUEUE_SIZE` | Hard cap for enqueue | `100` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `MAX_CONCURRENT_CHAT` | Max concurrent tasks per processor | `5` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `CHAT_TASK_TIMEOUT_SEC` | Per-task timeout (seconds) | `600` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `KB_PARALLELISM` | KB service parallelism | `4` | `deployment/docker/all_in_one/sample_env/.env.backend` | kb |
| `CHAT_APP_PARALLELISM` | Chat app process count. When `web_app.py` runs directly, it spawns this many Uvicorn workers. Total concurrency ≈ `MAX_CONCURRENT_CHAT` × `CHAT_APP_PARALLELISM`. | `4` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `UVICORN_RELOAD` | Enable Uvicorn auto-reload for `web_app.py` (dev only). Avoid with multi-worker in production. | `0` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `HEARTBEAT_INTERVAL` | Heartbeat interval (seconds) | `5` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb |
| `OPENAI_API_KEY` | OpenAI API key | — | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb |
| `HUGGING_FACE_API_TOKEN` | Hugging Face token | — | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb |
| `ANTHROPIC_API_KEY` | Anthropic API key | — | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb |
| `BRAVE_API_KEY` | Brave Search API key | — | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb |
| `GEMINI_CACHE_ENABLED` | Enable Gemini cache | `0` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb |
| `GEMINI_CACHE_TTL_SECONDS` | Gemini cache TTL | `3600` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb |
| `APP_DOMAIN` | CORS allow domain | — | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `TORCH_DEVICE` | Marker torch device (CPU/GPU) | `cpu` | `deployment/docker/all_in_one/sample_env/.env.backend` | kb |
| `AUTH_PROVIDER` | Auth provider (simple or cognito) | `simple` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `EXTRA_ID_TOKEN_HEADER` | Extra ID token header name | `X-ID-Token` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `COGNITO_REGION` | Cognito region | `eu-west-1` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `COGNITO_USER_POOL_ID` | Cognito user pool id | — | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `COGNITO_APP_CLIENT_ID` | Cognito app client id | — | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `COGNITO_SERVICE_CLIENT_ID` | Cognito service client id | — | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `JWKS_CACHE_TTL_SECONDS` | JWKS cache TTL | `86400` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `OIDC_SERVICE_ADMIN_USERNAME` | OIDC service admin username | `service.user` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `OIDC_SERVICE_ADMIN_PASSWORD` | OIDC service admin password | — | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `ODIC_SERVICE_USER_EMAIL` | Service user email | `service@org.com` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `AGENTIC_BUNDLES_ROOT` | Bundles root inside container | `/bundles` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb/worker |
| `HOST_BUNDLES_PATH` | Host bundles root (for mounts) | — | `deployment/docker/all_in_one/sample_env/.env.backend` | docker-compose volumes |
| `DEFAULT_LLM_MODEL_ID` | Default LLM model | `sonnet-4.5` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb |
| `DEFAULT_EMBEDDING_MODEL_ID` | Default embedding model | `openai-text-embedding-3-small` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb |
| `APP_AV_SCAN` | Enable AV scan | `1` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `APP_AV_TIMEOUT_S` | AV scan timeout | `3.0` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `CLAMAV_HOST` | ClamAV host | `clamav` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `CLAMAV_PORT` | ClamAV port | `3310` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `AWS_REGION` | AWS region | `eu-west-1` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb |
| `AWS_DEFAULT_REGION` | AWS default region | `eu-west-1` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb |
| `NO_PROXY` | No-proxy hosts | `169.254.169.254,localhost,127.0.0.1` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb |
| `AWS_EC2_METADATA_DISABLED` | Allow EC2 IMDS | `false` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb |
| `AWS_SDK_LOAD_CONFIG` | Load AWS config file | `1` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb |
| `TOOLS_WEB_SEARCH_FETCH_CONTENT` | Enable web fetch | `True` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `WEB_FETCH_RESOURCES_MEDIUM` | Medium cookies JSON | — | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `WEB_SEARCH_AGENTIC_THINKING_BUDGET` | Web search thinking budget | `200` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `WEB_SEARCH_PRIMARY_BACKEND` | Primary web search backend | `brave` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `WEB_SEARCH_BACKEND` | Web search backend | `hybrid` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `WEB_SEARCH_HYBRID_MODE` | Hybrid mode (sequential or parallel) | `sequential` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `WEB_SEARCH_SEGMENTER` | Search segmenter | `fast` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `OPEX_AGG_CRON` | Accounting aggregation schedule | `0 23 * * *` | `deployment/docker/all_in_one/sample_env/.env.backend` | worker |
| `BUNDLE_CLEANUP_ENABLED` | Enable bundle cleanup loop | `1` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/worker |
| `BUNDLE_CLEANUP_INTERVAL_SECONDS` | Cleanup interval | `3600` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/worker |
| `BUNDLE_CLEANUP_LOCK_TTL_SECONDS` | Cleanup lock TTL | `900` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/worker |
| `BUNDLE_REF_TTL_SECONDS` | Active bundle ref TTL | `3600` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/worker |
| `LOG_LEVEL` | Log level | `INFO` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb/worker |
| `LOG_MAX_MB` | Log rotation size | `20` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb/worker |
| `LOG_BACKUP_COUNT` | Log rotation count | `10` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb/worker |
| `LOG_DIR` | Log directory | `/logs` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb/worker |
| `LOG_FILE_PREFIX` | Log file prefix | `chat` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb/worker |
| `PY_CODE_EXEC_IMAGE` | Executor image name | `py-code-exec:latest` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `PY_CODE_EXEC_TIMEOUT` | Executor timeout (seconds) | `600` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `PY_CODE_EXEC_NETWORK_MODE` | Executor network mode | `host` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `ACCOUNTING_SERVICES` | Accounting services JSON | — | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/worker |
| `AUTH_TOKEN_COOKIE_NAME` | Auth token cookie name | `__Secure-LATC` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `ID_TOKEN_COOKIE_NAME` | ID token cookie name | `__Secure-LITC` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `MCP_CACHE_TTL_SECONDS` | MCP cache TTL | `36000` | `deployment/docker/all_in_one/sample_env/.env.backend` | chat |
| `INSTANCE_ID` | Instance id for services | — | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb/worker |
| `CHAT_WEB_APP_KB_BASE` | KB base URL in UI | — | `deployment/docker/all_in_one/sample_env/.env.ui.build` | UI build |
| `CHAT_WEB_APP_KB_SOCKET` | KB socket URL in UI | — | `deployment/docker/all_in_one/sample_env/.env.ui.build` | UI build |
| `CHAT_WEB_APP_KB_SOCKETIO_PATH` | KB Socket.IO path | — | `deployment/docker/all_in_one/sample_env/.env.ui.build` | UI build |
| `CHAT_WEB_APP_CHAT_SOCKETIO_PATH` | Chat Socket.IO path | — | `deployment/docker/all_in_one/sample_env/.env.ui.build` | UI build |
| `CHAT_WEB_APP_MONITORING_BASE` | Monitoring base URL | — | `deployment/docker/all_in_one/sample_env/.env.ui.build` | UI build |
| `CHAT_WEB_APP_DEFAULT_TENANT` | Default tenant in UI | — | `deployment/docker/all_in_one/sample_env/.env.ui.build` | UI build |
| `CHAT_WEB_APP_DEFAULT_PROJECT` | Default project in UI | — | `deployment/docker/all_in_one/sample_env/.env.ui.build` | UI build |
| `CHAT_WEB_APP_PROJECT` | Project for UI | — | `deployment/docker/all_in_one/sample_env/.env.ui.build` | UI build |
| `CHAT_WEB_APP_AUTH_TYPE` | Auth mode for UI | — | `deployment/docker/all_in_one/sample_env/.env.ui.build` | UI build |
| `CHAT_WEB_APP_EXTRA_ID_TOKEN_HEADER` | Extra ID token header | — | `deployment/docker/all_in_one/sample_env/.env.ui.build` | UI build |
| `CHAT_WEB_APP_TOTP_APP_NAME` | TOTP app name | — | `deployment/docker/all_in_one/sample_env/.env.ui.build` | UI build |
| `CHAT_WEB_APP_TOTP_ISSUER` | TOTP issuer | — | `deployment/docker/all_in_one/sample_env/.env.ui.build` | UI build |
| `CHAT_WEB_APP_PROXY_LOGIN_BASE` | Proxy login base path | — | `deployment/docker/all_in_one/sample_env/.env.ui.build` | UI build |
| `CHAT_WEB_APP_OIDC_SCOPE` | OIDC scope | — | `deployment/docker/all_in_one/sample_env/.env.ui.build` | UI build |
| `CHAT_WEB_APP_OIDC_CLIENT_ID` | OIDC client id | — | `deployment/docker/all_in_one/sample_env/.env.ui.build` | UI build |
| `CHAT_WEB_APP_OIDC_AUTHORITY` | OIDC authority URL | — | `deployment/docker/all_in_one/sample_env/.env.ui.build` | UI build |
| `CHAT_WEB_APP_HARDCODED_AUTH_TOKEN` | Hardcoded auth token | — | `deployment/docker/all_in_one/sample_env/.env.ui.build` | UI build |
| `CHAT_WEB_APP_DEFAULT_ROUTE_PREFIX` | Default route prefix | — | `deployment/docker/all_in_one/sample_env/.env.ui.build` | UI build |
| `AWS_REGION` | AWS region for proxy-login | — | `deployment/docker/all_in_one/sample_env/.env.proxylogin` | proxy-login |
| `AWS_DEFAULT_REGION` | AWS default region for proxy-login | — | `deployment/docker/all_in_one/sample_env/.env.proxylogin` | proxy-login |
| `COGNITO_CLIENTID` | Cognito client id for proxy-login | — | `deployment/docker/all_in_one/sample_env/.env.proxylogin` | proxy-login |
| `COGNITO_CLIENTSECRET` | Cognito client secret | — | `deployment/docker/all_in_one/sample_env/.env.proxylogin` | proxy-login |
| `COGNITO_USERPOOLID` | Cognito user pool id | — | `deployment/docker/all_in_one/sample_env/.env.proxylogin` | proxy-login |
| `COGNITO_JWKSISSUER` | JWKS issuer URL | — | `deployment/docker/all_in_one/sample_env/.env.proxylogin` | proxy-login |
| `COGNITO_JWKSSIGNINGKEYURL` | JWKS signing key URL | — | `deployment/docker/all_in_one/sample_env/.env.proxylogin` | proxy-login |
| `HTTP_CORS_ENABLED` | Enable CORS mode | — | `deployment/docker/all_in_one/sample_env/.env.proxylogin` | proxy-login |
| `TOKEN_COOKIES_SAMESITE` | Cookie SameSite policy | — | `deployment/docker/all_in_one/sample_env/.env.proxylogin` | proxy-login |
| `TOKEN_COOKIES_DOMAIN` | Cookie domain | — | `deployment/docker/all_in_one/sample_env/.env.proxylogin` | proxy-login |
| `TOKEN_MASQUERADE` | Token masquerade flag | — | `deployment/docker/all_in_one/sample_env/.env.proxylogin` | proxy-login |
| `PASSWORD_RESET_COMPANY` | Password reset company name | — | `deployment/docker/all_in_one/sample_env/.env.proxylogin` | proxy-login |
| `PASSWORD_RESET_SENDER` | Password reset sender | — | `deployment/docker/all_in_one/sample_env/.env.proxylogin` | proxy-login |
| `PASSWORD_RESET_TEMPLATENAME` | Password reset template name | — | `deployment/docker/all_in_one/sample_env/.env.proxylogin` | proxy-login |
| `PASSWORD_RESET_REDIRECTURL` | Password reset redirect URL | — | `deployment/docker/all_in_one/sample_env/.env.proxylogin` | proxy-login |
| `HTTP_URLBASE` | Base URL for proxy-login | — | `deployment/docker/all_in_one/sample_env/.env.proxylogin` | proxy-login |
| `LOGGING_DEV` | Enable dev logging | — | `deployment/docker/all_in_one/sample_env/.env.proxylogin` | proxy-login |
| `REDIS_URL` | Redis URL for proxy-login | — | `deployment/docker/all_in_one/sample_env/.env.proxylogin` | proxy-login |
| `RATELIMITER_STORAGE` | Rate limiter storage backend | — | `deployment/docker/all_in_one/sample_env/.env.proxylogin` | proxy-login |
| `STORAGE_TYPE` | Storage backend type | — | `deployment/docker/all_in_one/sample_env/.env.proxylogin` | proxy-login |

**Optional or commented variables in sample env**

| Variable | Purpose | Default | File | Service/Scope |
|---|---|---|---|---|
| `CHAT_WEB_APP_CHAT_BASE` | Chat base URL in UI | — | `deployment/docker/all_in_one/sample_env/.env.ui.build` | UI build |
| `CHAT_WEB_APP_CHAT_SOCKET` | Chat socket URL in UI | — | `deployment/docker/all_in_one/sample_env/.env.ui.build` | UI build |
| `N4J_USER` | Neo4j user | — | `deployment/docker/all_in_one/sample_env/.env.backend` | neo4j |
| `N4J_PASSWORD` | Neo4j password | — | `deployment/docker/all_in_one/sample_env/.env.backend` | neo4j |
| `N4J_PAGECACHE` | Neo4j page cache | — | `deployment/docker/all_in_one/sample_env/.env.backend` | neo4j |
| `N4J_HEAP_INITIAL` | Neo4j heap initial | — | `deployment/docker/all_in_one/sample_env/.env.backend` | neo4j |
| `N4J_HEAP_MAX` | Neo4j heap max | — | `deployment/docker/all_in_one/sample_env/.env.backend` | neo4j |
| `APP_NEO4J_URI` | Neo4j URI (app-side) | — | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb |
| `APP_NEO4J_USERNAME` | Neo4j username (app-side) | — | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb |
| `APP_NEO4J_PASSWORD` | Neo4j password (app-side) | — | `deployment/docker/all_in_one/sample_env/.env.backend` | chat/kb |
| `NEO4J_AUTH` | Neo4j auth string | — | `deployment/docker/all_in_one/sample_env/.env.backend` | neo4j |
| `TOKEN_COOKIES_ENABLED` | Enable token cookies | — | `deployment/docker/all_in_one/sample_env/.env.proxylogin` | proxy-login |
| `CORS_ALLOWED_ORIGIN` | CORS allow origin | — | `deployment/docker/all_in_one/sample_env/.env.proxylogin` | proxy-login |
| `CORS_ALLOWED_METHODS` | CORS allowed methods | — | `deployment/docker/all_in_one/sample_env/.env.proxylogin` | proxy-login |
| `CORS_ALLOWED_HEADERS` | CORS allowed headers | — | `deployment/docker/all_in_one/sample_env/.env.proxylogin` | proxy-login |

**Notes**
Use `deployment/docker/all_in_one/sample_env` as the source of truth for compose env variable names and intended scopes.
`CHAT_APP_PARALLELISM` controls Uvicorn worker count when the container runs `web_app.py` directly (as in current Dockerfiles).
