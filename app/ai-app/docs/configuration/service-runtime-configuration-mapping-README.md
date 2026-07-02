---
id: repo:kdcube-ai-app/app/ai-app/docs/configuration/service-runtime-configuration-mapping-README.md
title: "Service Runtime Configuration Mapping"
summary: "Cross-descriptor runtime mapping for the platform: which file or env owns which runtime values across CLI compose, direct local runs, and AWS deployment."
tags: ["service", "configuration", "env", "descriptors"]
keywords: ["descriptor to runtime mapping", "compose versus direct run versus aws", "descriptor file locations", "runtime env translation", "bundle descriptor provider mapping", "secrets provider mapping", "workspace backend mapping", "mode specific configuration contract", "local mount variables", "deployment runtime configuration overview"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/descriptors-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/runtime-read-write-contract-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/runtime-configuration-and-secrets-store-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/assembly-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundles-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/secrets-descriptor-README.md
---
# Service Runtime Configuration Mapping

This is the cross-descriptor runtime mapping document.

It explains:

- which descriptor owns which part of runtime config
- how the three runtime modes differ
- which values matter only in CLI compose
- which values matter only for direct local process runs
- which values should not appear in AWS descriptors

The canonical contract tables now live in the per-descriptor pages:

- [runtime-read-write-contract-README.md](runtime-read-write-contract-README.md)
- [runtime-configuration-and-secrets-store-README.md](runtime-configuration-and-secrets-store-README.md)
- [assembly-descriptor-README.md](assembly-descriptor-README.md)
- [bundles-descriptor-README.md](bundles-descriptor-README.md)
- [bundles-secrets-descriptor-README.md](bundles-secrets-descriptor-README.md)
- [secrets-descriptor-README.md](secrets-descriptor-README.md)
- [gateway-descriptor-README.md](gateway-descriptor-README.md)

## Three runtime modes

| Mode | Main authority | Runtime shape |
|---|---|---|
| CLI local compose (`kdcube`) | staged descriptors in `workdir/config` plus rendered `.env*` files | docker compose with `/config` mounts |
| Direct local service run | the files and env vars you pass to the process explicitly | host-run `web_app.py` / direct proc or ingress |
| AWS deployment | deployment stack plus provider-backed live state | ECS/EFS/S3/Secrets Manager |

## Descriptor-backed env vars

The detailed env/API mapping for each descriptor is documented in the
per-descriptor pages. This section keeps only the cross-descriptor overview.

### Descriptor file locations

| Env var | Descriptor file | Modes | Meaning |
|---|---|---|---|
| `ASSEMBLY_YAML_DESCRIPTOR_PATH` | `assembly.yaml` | direct local run | Explicit path for plain reads from `assembly.yaml`. |
| `BUNDLES_YAML_DESCRIPTOR_PATH` | `bundles.yaml` | direct local run | Explicit path for plain reads and file-backed bundle descriptor authority. |
| `HOST_ASSEMBLY_YAML_DESCRIPTOR_PATH` | `assembly.yaml` | CLI local compose | Host file mounted into `/config/assembly.yaml`. |
| `HOST_BUNDLES_DESCRIPTOR_PATH` | `bundles.yaml` | CLI local compose | Host file mounted into `/config/bundles.yaml`. |
| `HOST_SECRETS_YAML_DESCRIPTOR_PATH` | `secrets.yaml` | CLI local compose, `secrets-file` only | Host file mounted into `/config/secrets.yaml`. |
| `HOST_BUNDLES_SECRETS_YAML_DESCRIPTOR_PATH` | `bundles.secrets.yaml` | CLI local compose, `secrets-file` only | Host file mounted into `/config/bundles.secrets.yaml`. |

### Identity, auth, and ports

| Env var | Descriptor path | Descriptor file | Modes |
|---|---|---|---|
| `AUTH_PROVIDER` | selected provider type from `auth.connection_hub` -> `connection-hub@1-0.config.authority_registry` | `assembly.yaml` + `bundles.yaml` | effective runtime value |
| `COGNITO_REGION` | selected platform provider `authenticator.region` | `bundles.yaml` | CLI local compose, AWS deployment |
| `COGNITO_USER_POOL_ID` | selected platform provider `authenticator.user_pool_id` | `bundles.yaml` | CLI local compose, AWS deployment |
| `COGNITO_APP_CLIENT_ID` | selected platform provider `authenticator.app_client_id` | `bundles.yaml` | CLI local compose, AWS deployment |
| `COGNITO_SERVICE_CLIENT_ID` | selected platform provider `authenticator.service_client_id` | `bundles.yaml` | CLI local compose, AWS deployment |
| `AUTH_COGNITO_PROVIDERS_JSON` / `COGNITO_TRUSTED_PROVIDERS_JSON` | selected platform provider `authenticator.trusted_providers` | `bundles.yaml` | optional multi-Cognito trust list |
| `ID_TOKEN_HEADER_NAME` | selected platform provider `authenticator.id_token_header_name` | `bundles.yaml` | CLI local compose, AWS deployment |
| `AUTH_TOKEN_COOKIE_NAME` | selected platform provider `authenticator.cookie.auth_token_cookie_name` | `bundles.yaml` | CLI local compose, AWS deployment |
| `ID_TOKEN_COOKIE_NAME` | selected platform provider `authenticator.cookie.id_token_cookie_name` | `bundles.yaml` | CLI local compose, AWS deployment |
| `JWKS_CACHE_TTL_SECONDS` | selected platform provider `authenticator.jwks_cache_ttl_seconds` | `bundles.yaml` | CLI local compose, AWS deployment |
| `COGNITO_ENFORCEMFA` | `auth.proxy_login.enforce_mfa` | `assembly.yaml` | CLI local compose, AWS deployment |
| `CHAT_APP_PORT` | `ports.ingress` | `assembly.yaml` | CLI local compose |
| `CHAT_PROCESSOR_PORT` | `ports.proc` | `assembly.yaml` | CLI local compose |
| `METRICS_PORT` | `ports.metrics` | `assembly.yaml` | CLI local compose |
| `KDCUBE_UI_PORT` | `ports.ui` | `assembly.yaml` | CLI local compose |
| `KDCUBE_UI_SSL_PORT` | `ports.ui_ssl` | `assembly.yaml` | CLI local compose |
| `KDCUBE_PROXY_HTTP_PORT` | `ports.proxy_http` | `assembly.yaml` | CLI local compose |
| `KDCUBE_PROXY_HTTPS_PORT` | `ports.proxy_https` | `assembly.yaml` | CLI local compose |

Notes:

- `ports.proxy_http` and `ports.proxy_https` are compose-only host port
  overrides
- Kubernetes and AWS deployment do not use those fields as their public service
  exposure contract
- in delegated auth deployments, `AUTH_TOKEN_COOKIE_NAME` and
  `ID_TOKEN_COOKIE_NAME` are also passed to the web-proxy so it can distinguish
  already-present real cookies from the proxylogin masquerade/unmask flow
- `auth.connection_hub` selects the platform authority provider. For Cognito,
  the selected provider under `connection-hub@1-0.config.authority_registry`
  supplies both browser-facing OIDC config and server-side token verification.
- `auth.idp: session` is the bundle session auth provider. It requires the
  platform secret `services.session_token.secret`.

### Secrets provider and secrets-file inputs

| Env var | Descriptor path | Descriptor file | Modes |
|---|---|---|---|
| `SECRETS_PROVIDER` | `secrets.provider` | `assembly.yaml` | all modes |
| `GLOBAL_SECRETS_YAML` | n/a | `secrets.yaml` | direct local run, CLI local compose in `secrets-file` mode |
| `BUNDLE_SECRETS_YAML` | n/a | `bundles.secrets.yaml` | direct local run, CLI local compose in `secrets-file` mode |

Common platform service secrets:

| Secret path | Purpose |
|---|---|
| `services.federated_token.secret` | Short-lived bundle-federated Data Bus token signing. |
| `services.session_token.secret` | Bundle session auth cookie signing. |

### Gateway config source

| Env var | Descriptor path | Descriptor file | Modes |
|---|---|---|---|
| `GATEWAY_CONFIG_FORCE_ENV_ON_STARTUP` | `platform.services.<component>.service.gateway_config_force_env_on_startup` | `assembly.yaml` | ingress/proc/metrics startup; useful when `gateway.yaml` should override stale Redis gateway cache |

Gateway runtime policy itself is descriptor-owned by `gateway.yaml`.

| Descriptor path | Runtime owner | Purpose |
|---|---|---|
| `gateway.guarded_rest_patterns.<component>` | gateway policy middleware | classifies REST endpoints that create gateway-admitted work |
| `gateway.bypass_throttling_patterns.<component>` | gateway policy middleware | skips request throttling for selected READ/CONNECT endpoints |
| `gateway.rate_limits.<component>` | `GatewayConfiguration.rate_limits` | generic request throttling per role |
| `gateway.data_bus.ingress.publish_limits` | `GatewayConfiguration.data_bus.publish_limits` | Socket.IO `data_bus.publish` package/message/byte limits before durable stream writes |
| `gateway.data_bus.ingress.publish_limits.<role>.enabled` | `DataBusPublishLimit.enabled` | enables/disables Socket.IO `data_bus.publish` package/message/byte limits for one resolved user role |

There is no separate env-var surface for individual Data Bus publish limits.
Change them in `gateway.yaml` or through the gateway admin config path.

### Redis runtime

| Runtime field | Descriptor path | Descriptor file | Modes |
|---|---|---|---|
| `REDIS_TOPOLOGY` | `infra.redis.topology` | `assembly.yaml` | all modes |

`infra.redis.topology` uses the canonical value `standalone` in current
reference descriptors. The Redis client factory also recognizes `cluster` and
fails fast until Redis Cluster key-slot migration is complete.

### Bundle registry and bundle authority

| Env var | Descriptor path | Descriptor file | Modes |
|---|---|---|---|
| `BUNDLES_YAML_DESCRIPTOR_PATH` | `bundles.yaml` | local bundle descriptor authority | proc in direct local run; optional explicit path in compose/k8s |
| `BUNDLES_DESCRIPTOR_PROVIDER` | `platform.services.proc.bundles.descriptor_provider` | `assembly.yaml` | proc in all modes |
| `BUNDLES_FORCE_ENV_ON_STARTUP` | n/a | current bundle descriptor authority | proc in all modes |
| `BUNDLES_PRELOAD_ON_START` | `platform.services.proc.bundles.bundles_preload_on_start` | `assembly.yaml` | proc in all modes |
| `BUNDLES_PRELOAD_LOCK_TTL_SECONDS` | `platform.services.proc.bundles.bundles_preload_lock_ttl_seconds` | `assembly.yaml` | proc in all modes; coarse preload coordination TTL |
| `BUNDLES_PRELOAD_BUNDLE_LOCK_TTL_SECONDS` | `platform.services.proc.bundles.bundles_preload_bundle_lock_ttl_seconds` | `assembly.yaml` | proc in all modes; per-bundle preload claim TTL |
| `BUNDLE_SCHEDULER_RECONCILE_INTERVAL_SECONDS` | `platform.services.proc.bundles.bundle_scheduler_reconcile_interval_seconds` | `assembly.yaml` | proc in all modes; `0` disables the periodic loop |
| `BUNDLE_GIT_RESOLUTION_ENABLED` | bundle items use `repo` / `ref` | `bundles.yaml` | proc in all modes |

Important distinction:

- `BUNDLES_YAML_DESCRIPTOR_PATH` points at the file-backed bundle descriptor source
- `BUNDLES_DESCRIPTOR_PROVIDER` selects bundle descriptor authority independently from `SECRETS_PROVIDER`
- proc startup/reset can use bundle descriptor authority directly
- recommended ECS setup is:
  - `SECRETS_PROVIDER=aws-sm`
  - `BUNDLES_DESCRIPTOR_PROVIDER=file`
  - writable mounted `/config/bundles.yaml` on EFS
- if bundle admin or bundle code should persist deployment-scoped prop updates, proc must mount that descriptor path writable
- proc also periodically reconciles scheduled bundle jobs from the active descriptor authority when `BUNDLE_SCHEDULER_RECONCILE_INTERVAL_SECONDS` is greater than `0`; reference descriptors set it to `0`, so startup and Pub/Sub-driven reconciliation are the active paths unless an environment opts in

### Workspace and Claude session backends

| Env var | Descriptor path | Descriptor file | Modes |
|---|---|---|---|
| `REACT_WORKSPACE_IMPLEMENTATION` | `storage.workspace.type` | `assembly.yaml` | CLI local compose, direct local run |
| `REACT_WORKSPACE_GIT_REPO` | `storage.workspace.repo` | `assembly.yaml` | CLI local compose, direct local run |
| `CLAUDE_CODE_SESSION_STORE_IMPLEMENTATION` | `storage.claude_code_session.type` | `assembly.yaml` | CLI local compose, direct local run |
| `CLAUDE_CODE_SESSION_GIT_REPO` | `storage.claude_code_session.repo` | `assembly.yaml` | CLI local compose, direct local run |

Git repo transport for workspace/session stores:

- `storage.workspace.repo` and `storage.claude_code_session.repo` may use either HTTPS or SSH
  remote forms
- if HTTPS token auth is configured through `services.git.http_token`, the shared git helper
  prefers that path and may normalize SSH-style remotes to HTTPS before invoking git
- if SSH transport is intended, configure the matching SSH settings:
  - `services.git.git_ssh_key_path`
  - `services.git.git_ssh_known_hosts`
  - `services.git.git_ssh_strict_host_key_checking`

Operational guidance:

- HTTPS + PAT is usually the simpler deployment/runtime choice
- SSH is supported, but it additionally requires mounted key and host-verification material

### ReAct runtime limits

The proc service reads these non-secret ReAct limits from `assembly.yaml` through
`get_settings()` and passes them into `RuntimeCtx`.

| Env var | Descriptor path | Descriptor file | Modes | Meaning |
|---|---|---|---|---|
| `AI_REACT_MAX_ITERATIONS` | `ai.react.max_iterations` | `assembly.yaml` | all modes | base ReAct decision/tool-use round cap; bundle `config.react.default_agent.max_iterations` or named-agent `config.react.<agent_key>.max_iterations` overrides this default; runtime fallback `15` |
| `AI_REACT_CONTEXT_MAX_TOKENS` | `ai.react.context_max_tokens` | `assembly.yaml` | all modes | hard model-input budget before compaction; includes system/instruction text plus rendered timeline |
| `AI_REACT_READ_VISIBLE_MAX_TEXT_SYMBOLS` | `ai.react.read_visible_max_text_symbols` | `assembly.yaml` | all modes | max visible text characters per `react.read` text path |
| `AI_REACT_READ_VISIBLE_MAX_TOKENS` | `ai.react.read_visible_max_tokens` | `assembly.yaml` | all modes | token guard per `react.read` text path |
| `AI_REACT_READ_VISIBLE_MAX_BYTES` | `ai.react.read_visible_max_bytes` | `assembly.yaml` | all modes | raw byte guard for every `react.read` payload |
| `AI_REACT_READ_VISIBLE_CONTEXT_FRACTION` | `ai.react.read_visible_context_fraction` | `assembly.yaml` | all modes | additional clamp against the current ReAct context budget |
| `AI_REACT_EXEC_TEXT_PREVIEW_MAX_SYMBOLS` | `ai.react.exec_text_preview_max_symbols` | `assembly.yaml` | all modes | text preview cap for each exec-produced text artifact |
| `AI_REACT_TOOL_RESULT_PREVIEW_MAX_TEXT_SYMBOLS` | `ai.react.tool_result_preview_max_text_symbols` | `assembly.yaml` | all modes | model-visible text preview cap for large initial tool results |
| `AI_REACT_LINE_NUMBERS_MODE` | `ai.react.line_numbers_mode` | `assembly.yaml` | all modes | rendered text preview line numbering mode: `disabled`, `lines`, or `sparsed`; bundle `config.react.default_agent.line_numbers_mode` or named-agent override takes precedence |
| `AI_REACT_CACHE_KEEP_RECENT_TURNS` | `ai.react.cache_keep_recent_turns` | `assembly.yaml` | all modes | recent turns kept visible after TTL pruning |
| `AI_REACT_CACHE_KEEP_RECENT_INTACT_TURNS` | `ai.react.cache_keep_recent_intact_turns` | `assembly.yaml` | all modes | newest turns kept untrimmed during TTL pruning |
| `AI_REACT_WORKING_SUMMARY_ENABLED` | `ai.react.working_summary_enabled` | `assembly.yaml` | all modes | emits and indexes React working-summary cards |
| `AI_REACT_PRUNED_TURN_SUMMARY_MODE` | `ai.react.pruned_turn_summary_mode` | `assembly.yaml` | all modes | controls whether pruned historical turns prefer working-summary cards |
| `AI_REACT_RENDER_THINKING` | `ai.react.render_thinking` | `assembly.yaml` | all modes | renders live model thinking blocks in the active ReAct timeline; bundle `config.react.default_agent.render_thinking` or named-agent override takes precedence |
| `AI_REACT_EVENT_SOURCE_PIPELINE_ENABLED` | `ai.react.event_source_pipeline_enabled` | `assembly.yaml` | all modes | enables the alternate event-source policy pipeline; bundle `config.react.default_agent.event_source_pipeline.enabled` or named-agent override takes precedence |
| `AI_REACT_DEBUG_TIMELINE` | `ai.react.debug_timeline` | `assembly.yaml` | all modes | enables rendered prompt snapshot files; bundle `config.react.default_agent.debug_timeline` or named-agent override takes precedence |

Unit contract:

- `*_TEXT_SYMBOLS` means text characters and only applies to text materialized
  into model-visible context. Oversized text reads return configured bounded
  previews; oversized initial tool results render as a bounded preview plus
  shape/recovery metadata. Per-call `max_text_symbols` only asks for a smaller
  explicit `react.read` text preview. Read caps apply per requested path.
- Skills are not read-capped. Owner-defined document/source systems should
  expose their own tools, namespace service endpoints, or rehosters.
- `*_TOKENS` is a model-context budget guard.
- `*_BYTES` is a raw payload guard. PDF/image reads are either attached whole
  under the byte cap or represented by a recovery marker; they are not partially
  sliced by `max_text_symbols`.

Browser-tool sessions are not configured by assembly fields yet. They are scoped
by tenant/project/user/conversation/turn/request and cleaned through ReAct/proc
turn finalizers on normal completion, managed error, watchdog timeout, and task
cancellation. The current idle janitor TTL, janitor interval, and max session
count are backend constants.

### Proc task watchdog and turn finalizers

The proc service reads watchdog settings from `assembly.yaml` through
`get_settings().PLATFORM.SERVICE`.

| Env var | Descriptor path | Descriptor file | Modes | Meaning |
|---|---|---|---|---|
| `CHAT_TASK_TIMEOUT_SEC` | `platform.services.proc.service.chat_task_timeout_sec` | `assembly.yaml` | proc in all modes | legacy overall task timeout |
| `CHAT_TASK_IDLE_TIMEOUT_SEC` | `platform.services.proc.service.chat_task_idle_timeout_sec` | `assembly.yaml` | proc in all modes | idle timeout measured from last task activity |
| `CHAT_TASK_MAX_WALL_TIME_SEC` | `platform.services.proc.service.chat_task_max_wall_time_sec` | `assembly.yaml` | proc in all modes | hard wall-clock limit for one task |
| `CHAT_TASK_WATCHDOG_POLL_INTERVAL_SEC` | `platform.services.proc.service.chat_task_watchdog_poll_interval_sec` | `assembly.yaml` | proc in all modes | watchdog polling interval |

When a task is cancelled by the watchdog or by processor cancellation, proc
still runs the per-turn finalizer path before dropping local task state. This is
where lifecycle cleanup such as turn-scoped browser-session cleanup is invoked.

### Isolated execution defaults

The proc service reads ISO runtime defaults from `assembly.yaml` through
`get_settings().PLATFORM.EXEC`. These are platform defaults, not bundle config.

| Descriptor path | Settings API | Meaning |
|---|---|---|
| `platform.services.proc.exec.exec_workspace_root` | `get_settings().PLATFORM.EXEC.EXEC_WORKSPACE_ROOT` | container-visible exec workspace root |
| `platform.services.proc.exec.py_code_exec_image` | `get_settings().PLATFORM.EXEC.PY.PY_CODE_EXEC_IMAGE` | default ISO runtime image |
| `platform.services.proc.exec.py_code_exec_timeout` | `get_settings().PLATFORM.EXEC.PY.PY_CODE_EXEC_TIMEOUT` | default execution timeout |
| `platform.services.proc.exec.py_code_exec_network_mode` | `get_settings().PLATFORM.EXEC.PY.PY_CODE_EXEC_NETWORK_MODE` | Docker network mode for the supervisor container |
| `platform.services.proc.exec.py_code_exec_container_strategy` | `get_settings().PLATFORM.EXEC.PY.PY_CODE_EXEC_CONTAINER_STRATEGY` | Docker container strategy: `combined` or `split` |
| `platform.services.proc.exec.max_file_bytes` | `get_settings().PLATFORM.EXEC.PY.EXEC_MAX_FILE_BYTES` | max single generated file per exec call |
| `platform.services.proc.exec.max_exec_workspace_delta_bytes` | `get_settings().PLATFORM.EXEC.PY.EXEC_MAX_WORKSPACE_DELTA_BYTES` | max net-new monitored writable bytes per exec call |
| `platform.services.proc.exec.max_workspace_bytes` | `get_settings().PLATFORM.EXEC.PY.EXEC_MAX_WORKSPACE_BYTES` | optional max total bytes currently present in the active workspace writable roots before finalization/offload |
| `platform.services.proc.exec.workspace_monitor_interval_s` | `get_settings().PLATFORM.EXEC.PY.EXEC_WORKSPACE_MONITOR_INTERVAL_S` | workspace monitor polling interval |

The runtime forwards these values into the isolated process as internal
`EXEC_*` env values because the isolated boundary consumes env. Do not treat
those env names as the public configuration source.

Bundles may override execution limits and routing for a single run through
`bundles.yaml` non-secret props under `config.execution.runtime` or the legacy
`config.exec_runtime` alias.

## The `assembly.paths.*` keys you asked about

| Env var | Descriptor path | CLI local compose | Direct local run | AWS deployment |
|---|---|---|---|---|
| `HOST_KDCUBE_STORAGE_PATH` | `paths.host_kdcube_storage_path` | relevant | optional | not used |
| `HOST_BUNDLES_PATH` | `paths.host_bundles_path` | relevant for non-managed local path bundles | optional | not used |
| `HOST_MANAGED_BUNDLES_PATH` | `paths.host_managed_bundles_path` | relevant for platform-managed bundles (git/example) | optional | not used |
| `HOST_BUNDLE_STORAGE_PATH` | `paths.host_bundle_storage_path` | relevant | optional | not used |
| `HOST_EXEC_WORKSPACE_PATH` | `paths.host_exec_workspace_path` | relevant | optional | not used |
| `HOST_REACT_DEBUG_PATH` | `paths.host_react_debug_path` | relevant when timeline debug rendering is enabled | optional | EC2 host mount source for `/react-debug` |
| `REACT_DEBUG_ROOT` | `platform.services.proc.react_debug.debug_root` | `/react-debug` | optional host path for direct proc runs | `/react-debug` |
| `REACT_DEBUG_KEEP_FILES` | `platform.services.proc.react_debug.keep_files` | rolling render-debug retention | rolling render-debug retention | rolling render-debug retention |

Rule:

- in CLI compose these are mount-driving topology settings
- in direct local runs they are just host-path settings if the process needs
  them
- in AWS/ECS deployment `HOST_REACT_DEBUG_PATH` is the EC2 host-side EFS mount
  path, while `REACT_DEBUG_ROOT` is the container path used by proc

## What to set for direct local proc debugging

If you run proc or ingress directly on the host, start with:

```bash
ASSEMBLY_YAML_DESCRIPTOR_PATH=/abs/path/to/assembly.yaml
BUNDLES_YAML_DESCRIPTOR_PATH=/abs/path/to/bundles.yaml
```

If proc should seed or reset the bundle registry from that same descriptor:

```bash
BUNDLES_YAML_DESCRIPTOR_PATH=/abs/path/to/bundles.yaml
BUNDLES_DESCRIPTOR_PROVIDER=file
BUNDLES_FORCE_ENV_ON_STARTUP=1
```

If you use file-backed secrets:

```bash
SECRETS_PROVIDER=secrets-file
GLOBAL_SECRETS_YAML=file:///abs/path/to/secrets.yaml
BUNDLE_SECRETS_YAML=file:///abs/path/to/bundles.secrets.yaml
```

Set `HOST_*` path vars only if local runtime behavior needs those host
directories.

## What not to carry into AWS descriptors

Do not copy these local-only values into AWS/ECS deployment descriptors:

- `paths.host_kdcube_storage_path`
- `paths.host_bundles_path`
- `paths.host_bundle_storage_path`
- `paths.host_exec_workspace_path`

Cloud deployment owns storage and topology separately.

## Data access API summary

For the one-page read/write contract across helpers, see:

- [runtime-read-write-contract-README.md](runtime-read-write-contract-README.md)

| Data kind | API |
|---|---|
| effective typed platform setting | `get_settings()` |
| raw `assembly.yaml` value | `read_plain("...")` |
| raw `bundles.yaml` value | `read_plain("b:...")` |
| effective current bundle config | `self.bundle_prop("...")` |
| platform/global secret | `await get_secret("canonical.key")` |
| bundle-scoped secret | `await get_secret("b:group.key")` |
| user-scoped bundle secret | `await get_secret("u:group.key")`, `await set_user_secret(...)`, `await delete_user_secret(...)` |

For deployment-scoped bundle props in cloud:

- keep secrets in the configured secrets provider
- keep bundle descriptors and non-secret bundle props in `bundles.yaml`
- prefer file-backed authority over provider-backed bundle descriptor docs

## Related docs

- Descriptor ownership and mode differences:
  - [descriptors-README.md](../cicd/descriptors-README.md)
- One-page runtime helper contract:
  - [runtime-read-write-contract-README.md](runtime-read-write-contract-README.md)
- Per-descriptor docs:
  - [assembly-descriptor-README.md](assembly-descriptor-README.md)
  - [bundles-descriptor-README.md](bundles-descriptor-README.md)
  - [bundles-secrets-descriptor-README.md](bundles-secrets-descriptor-README.md)
  - [secrets-descriptor-README.md](secrets-descriptor-README.md)
  - [gateway-descriptor-README.md](gateway-descriptor-README.md)
