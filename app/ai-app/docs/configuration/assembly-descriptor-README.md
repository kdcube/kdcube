---
id: repo:kdcube-ai-app/app/ai-app/docs/configuration/assembly-descriptor-README.md
title: "Platform Assembly Descriptor"
summary: "Platform-level non-secret deployment configuration in assembly.yaml: tenant/project identity, auth, ports, storage backends, local runtime paths, and frontend/runtime wiring."
tags: ["service", "configuration", "platform", "deployment", "assembly", "descriptor"]
keywords: ["platform deployment identity", "tenant and project scope", "auth and cognito settings", "service port layout", "storage and workspace backends", "runtime path wiring", "bundle descriptor provider", "frontend build metadata", "local compose topology", "aws deployment mapping"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/descriptors-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/service-runtime-configuration-mapping-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundles-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/secrets-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/gateway-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/oauth-mcp-integration-access-README.md
---
# Platform Assembly Descriptor

`assembly.yaml` is the platform-level non-secret descriptor.

It defines:

- deployment identity: tenant, project, domain, company
- auth mode and Cognito identifiers
- service ports
- storage and runtime backends
- local host-path topology for CLI compose and direct local debugging
- frontend build/image metadata for custom UI compose runs

It does not define:

- bundle inventory
- bundle secrets
- global secrets
- gateway throttling and route guards

Those belong to the other descriptor files.

## Direct runtime contract from this descriptor

### Supported access APIs

| Need | API | Notes |
|---|---|---|
| effective typed runtime setting | `get_settings()` | Uses `assembly.yaml > env var > code default` for fields that are promoted in `config_scopes.py` |
| raw value from `assembly.yaml` | `read_plain("...")` / `get_plain("...")` | Unprefixed keys read `assembly.yaml` by default |
| explicit raw value from `assembly.yaml` | `read_plain("a:...")` | Same as unprefixed read, but explicit |

### File-resolution env vars

| Env var | Meaning | Modes |
|---|---|---|
| `ASSEMBLY_YAML_DESCRIPTOR_PATH` | Explicit file path used by `read_plain(...)` and descriptor-backed runtime reads | direct local service run |
| `HOST_ASSEMBLY_YAML_DESCRIPTOR_PATH` | Host file staged/mounted into `/config/assembly.yaml` by the CLI installer | CLI local compose |
| `PLATFORM_DESCRIPTORS_DIR` | Fallback directory used when `ASSEMBLY_YAML_DESCRIPTOR_PATH` is not set | direct local service run |

### Promoted env vars resolved from `assembly.yaml`

These env vars are the direct runtime surface for assembly-backed settings.

| Env var | `assembly.yaml` path | Primary API | Modes |
|---|---|---|---|
| `SECRETS_PROVIDER` | `secrets.provider` | `get_settings()` | all modes |
| `AUTH_PROVIDER` | `auth.idp` | `get_settings()` | effective runtime value; descriptor remains the source of truth |
| `COGNITO_REGION` | `auth.cognito.region` | `get_settings()` | CLI local compose, AWS deployment |
| `COGNITO_USER_POOL_ID` | `auth.cognito.user_pool_id` | `get_settings()` | CLI local compose, AWS deployment |
| `COGNITO_APP_CLIENT_ID` | `auth.cognito.app_client_id` | `get_settings()` | CLI local compose, AWS deployment |
| `COGNITO_SERVICE_CLIENT_ID` | `auth.cognito.service_client_id` | `get_settings()` | CLI local compose, AWS deployment |
| `ID_TOKEN_HEADER_NAME` | `auth.id_token_header_name` | `get_settings()` | CLI local compose, AWS deployment |
| `AUTH_TOKEN_COOKIE_NAME` | `auth.auth_token_cookie_name` | `get_settings()` / web-proxy env | CLI local compose, AWS deployment |
| `ID_TOKEN_COOKIE_NAME` | `auth.id_token_cookie_name` | `get_settings()` / web-proxy env | CLI local compose, AWS deployment |
| `JWKS_CACHE_TTL_SECONDS` | `auth.jwks_cache_ttl_seconds` | `get_settings()` | CLI local compose, AWS deployment |
| `CHAT_APP_PORT` | `ports.ingress` | `get_settings()` | CLI local compose |
| `CHAT_PROCESSOR_PORT` | `ports.proc` | `get_settings()` | CLI local compose |
| `METRICS_PORT` | `ports.metrics` | `get_settings()` | CLI local compose |
| `KDCUBE_UI_PORT` | `ports.ui` | `get_settings()` | CLI local compose |
| `KDCUBE_UI_SSL_PORT` | `ports.ui_ssl` | `get_settings()` | CLI local compose |
| `KDCUBE_PROXY_HTTP_PORT` | `ports.proxy_http` | `get_settings()` | CLI local compose |
| `KDCUBE_PROXY_HTTPS_PORT` | `ports.proxy_https` | `get_settings()` | CLI local compose |
| `REDIS_TOPOLOGY` | `infra.redis.topology` | `get_settings()` / Redis client factory | all modes |
| `REACT_WORKSPACE_IMPLEMENTATION` | `storage.workspace.type` | `get_settings()` | CLI local compose, direct local service run |
| `REACT_WORKSPACE_GIT_REPO` | `storage.workspace.repo` | `get_settings()` | CLI local compose, direct local service run |
| `AI_REACT_AGENT_VERSION` | `ai.react.react_agent_version` | `get_settings()` | all modes |
| `AI_REACT_AGENT_MULTI_ACTION` | `ai.react.react_agent_multiaction` | `get_settings()` | all modes |
| `AI_REACT_MAX_ITERATIONS` | `ai.react.max_iterations` | `get_settings()` / `RuntimeCtx.max_iterations` | all modes |
| `AI_REACT_CONTEXT_MAX_TOKENS` | `ai.react.context_max_tokens` | `get_settings()` | all modes |
| `AI_REACT_READ_VISIBLE_MAX_TEXT_SYMBOLS` | `ai.react.read_visible_max_text_symbols` | `get_settings()` | all modes |
| `AI_REACT_READ_VISIBLE_MAX_TOKENS` | `ai.react.read_visible_max_tokens` | `get_settings()` | all modes |
| `AI_REACT_READ_VISIBLE_MAX_BYTES` | `ai.react.read_visible_max_bytes` | `get_settings()` | all modes |
| `AI_REACT_READ_VISIBLE_CONTEXT_FRACTION` | `ai.react.read_visible_context_fraction` | `get_settings()` | all modes |
| `AI_REACT_EXEC_TEXT_PREVIEW_MAX_SYMBOLS` | `ai.react.exec_text_preview_max_symbols` | `get_settings()` | all modes |
| `AI_REACT_TOOL_RESULT_PREVIEW_MAX_TEXT_SYMBOLS` | `ai.react.tool_result_preview_max_text_symbols` | `get_settings()` | all modes |
| `AI_REACT_LINE_NUMBERS_MODE` | `ai.react.line_numbers_mode` | `get_settings()` / `RuntimeCtx.line_numbers_mode` | all modes |
| `AI_REACT_CACHE_KEEP_RECENT_TURNS` | `ai.react.cache_keep_recent_turns` | `get_settings()` | all modes |
| `AI_REACT_CACHE_KEEP_RECENT_INTACT_TURNS` | `ai.react.cache_keep_recent_intact_turns` | `get_settings()` | all modes |
| `AI_REACT_WORKING_SUMMARY_ENABLED` | `ai.react.working_summary_enabled` | `get_settings()` | all modes |
| `AI_REACT_PRUNED_TURN_SUMMARY_MODE` | `ai.react.pruned_turn_summary_mode` | `get_settings()` | all modes |
| `AI_REACT_RENDER_THINKING` | `ai.react.render_thinking` | `get_settings()` / `RuntimeCtx.render_thinking` | all modes |
| `AI_REACT_EVENT_SOURCE_PIPELINE_ENABLED` | `ai.react.event_source_pipeline_enabled` | `get_settings()` / `RuntimeCtx.event_source_pipeline_enabled` | all modes |
| `CLAUDE_CODE_SESSION_STORE_IMPLEMENTATION` | `storage.claude_code_session.type` | `get_settings()` | CLI local compose, direct local service run |
| `CLAUDE_CODE_SESSION_GIT_REPO` | `storage.claude_code_session.repo` | `get_settings()` | CLI local compose, direct local service run |
| `BUNDLES_PRELOAD_BUNDLE_LOCK_TTL_SECONDS` | `platform.services.proc.bundles.bundles_preload_bundle_lock_ttl_seconds` | `get_settings().PLATFORM.APPLICATIONS` | proc in all modes |
| `BUNDLE_SCHEDULER_RECONCILE_INTERVAL_SECONDS` | `platform.services.proc.bundles.bundle_scheduler_reconcile_interval_seconds` | `get_settings().PLATFORM.APPLICATIONS` | proc in all modes |

## Fields that are always meaningful

These sections are normal platform configuration in every mode:

- `context.*`
- `auth.*`
- `proxy.*`
- `ports.*`
- `storage.*`
- `infra.*`
- `aws.region`

They are consumed either:

- by the installer/deployment layer
- by runtime env rendering
- or by direct `read_plain(...)` reads from `assembly.yaml`

### `auth.*` token transport

`auth.*` defines both the identity provider and the token transport names used
by runtime services and the delegated web-proxy.

Example:

```yaml
auth:
  type: "delegated"             # simple | cognito | delegated | bundle
  idp: "cognito"                # simple | cognito | session
  id_token_header_name: "X-ID-Token"
  auth_token_cookie_name: "__Secure-LATC"
  id_token_cookie_name: "__Secure-LITC"
  jwks_cache_ttl_seconds: 86400

  cognito:
    region: "eu-west-1"
    user_pool_id: "eu-west-1_XXXXXXXXX"
    app_client_id: ""
    service_client_id: ""

  proxy_login:
    redis_key_prefix: "proxylogin:<TENANT>:<PROJECT>:"
    token_masquerade: true
    enforce_mfa: false
    http_urlbase: "https://YOUR_DOMAIN/auth"
```

`auth_token_cookie_name` and `id_token_cookie_name` are not frontend-only
settings. They are rendered into ingress/proc runtime env and into the
delegated web-proxy. The proxy uses them to detect the non-masquerade path
where a top-level login flow has already set the real auth and identity
cookies. If either cookie is missing, the delegated proxy keeps using the
existing `/auth/unmask` flow.

`auth.proxy_login.token_masquerade` controls how proxylogin issues browser
cookies. It does not change the backend token validator; ingress/proc still
validate tokens using the configured auth provider.

`auth.proxy_login.enforce_mfa` maps to Proxy Login `COGNITO_ENFORCEMFA`. When
enabled, Proxy Login enforces MFA during the Cognito login flow.

`auth.idp: session` selects bundle session auth, where a bundle/front shell
validates an external identity and calls the async platform session authority to
issue platform-recognized `kst1.*` cookies. It requires
`services.session_token.secret` in `secrets.yaml`. See
[Bundle Session Auth](../service/auth/bundle-session-auth-README.md).

`auth.authenticators` configures optional request-auth selector bridge
candidates. The selector returns a complete `UserSession`; downstream gateway
policy does not need to know which bridge accepted the request.

```yaml
auth:
  authenticators:
    platform:
      id: "kdcube.multi-cognito"
      authority_id: "kdcube.platform"
      provider: "multi-cognito"

    connection_hub:
      enabled: true
      app_id: "connection-hub@1-0"
      operation: "request_authenticate"
      require_selector_hint: false
```

`auth.authenticators.platform` is the canonical descriptor registration for the
role-providing platform authenticator. Existing descriptors that only declare
`auth.idp` and `auth.providers` are treated as an implicit platform
authenticator so current deployments do not need a flag-day migration. New
descriptors should name the platform authenticator explicitly.

When Connection Hub is enabled, ingress/proc first accept a valid platform
token/cookie session when one is present. If no platform session is established,
the selector may call the Connection Hub request-auth bridge. Provider-specific
authenticators, such as Telegram Mini App `initData`, are Connection Hub modules
with access to Connection Hub config, secrets, and identity-link data. See
[Auth Selector](../service/auth/auth-selector-README.md) and
[Request Authenticators](../sdk/solutions/connections/request-authenticators/request-authenticators-README.md).

`require_selector_hint` defaults to `false`. Keep it false for open/public
bundle surfaces where Connection Hub must be allowed to inspect the request
envelope and decide whether any configured authority can authenticate it. Set it
true only for closed deployments where every caller is known to carry
`X-KDCube-Auth-*`, Telegram, provider signature, API-key, or equivalent query
hints.

`auth.idp: multi-cognito` selects the multi-provider Cognito verifier. The
browser-facing OIDC config still comes from `auth.cognito`; ingress/proc also
trust every Cognito provider listed in `auth.providers` or
`auth.cognito.providers`.

```yaml
auth:
  type: cognito
  idp: multi-cognito
  cognito:
    region: eu-west-1
    user_pool_id: eu-west-1_PRIMARY
    app_client_id: primary-client
  providers:
  - alias: primary
    kind: cognito
    region: eu-west-1
    user_pool_id: eu-west-1_PRIMARY
    app_client_id: primary-client
  - alias: peer
    kind: cognito
    region: eu-west-1
    user_pool_id: eu-west-1_PEER
    app_client_id: peer-client
```

The server selects a verifier from token claims (`iss` plus `client_id` or
`aud`) and then performs normal JWKS validation for that provider.

### `auth.oauth_mcp`

`auth.oauth_mcp` configures KDCube-hosted OAuth2 authorization for MCP
integration access. It is a platform auth capability served by chat-ingress.
Do not configure it under `platform.services.ingress.*`, and do not configure it
with operator-facing environment variables.

Reference shape:

```yaml
auth:
  oauth_mcp:
    enabled: false
    issuer: ""
    public_clients:
      - client_id: "claude"
        redirect_uris:
          - "https://claude.ai/api/mcp/auth_callback"
          - "http://localhost/callback"
          - "http://127.0.0.1/callback"
    dynamic_client_registration:
      allowed_redirect_uris:
        - "https://claude.ai/api/mcp/auth_callback"
        - "http://localhost/callback"
        - "http://127.0.0.1/callback"
```

Rules:

- `enabled` controls whether the OAuth/MCP integration-access routes are mounted.
- `issuer` is the public origin advertised in OAuth metadata. Leave it empty in
  local/dev when deriving the issuer from the request origin is acceptable.
- `public_clients[*].redirect_uris` configures pre-registered public clients.
- `dynamic_client_registration.allowed_redirect_uris` constrains pre-auth
  dynamic client registration.
- Tenant/project scope comes from `context.tenant` and `context.project`.
- The browser session cookie used by the flow is `auth.auth_token_cookie_name`.

See [OAuth MCP Integration Access](../service/auth/oauth-mcp-integration-access-README.md).

### `infra.redis.topology`

`infra.redis.topology` selects the Redis client topology used by the shared
Redis client factory. The canonical values are:

| Value | Meaning |
|---|---|
| `standalone` | single Redis endpoint |
| `cluster` | Redis Cluster topology |

The reference descriptor sets `standalone`. The current runtime recognizes
`cluster` but fails fast until the cluster key-slot migration is complete.

### `auth.turnstile_development_token`

`auth.turnstile_development_token` is an optional installer-facing setting for
local or development registration flows that use Cloudflare Turnstile.

When it is set to a non-placeholder value, the CLI installer writes it into the
generated frontend runtime config as:

```json
{
  "auth": {
    "turnstileDevelopmentToken": "XXXX.DUMMY.TOKEN.XXXX"
  }
}
```

A frontend that supports this field can submit that token instead of rendering
the Turnstile widget. Leave the field empty in shared, staging, and production
descriptors unless that environment is intentionally using Cloudflare's test
credentials.

### `frontend.config`

`frontend.config` is public browser config. The installer and
`GET /api/cp-frontend-config` use the same builder and merge this section into
the generated frontend config. Do not put secrets here.

Example:

```yaml
frontend:
  config:
    auth:
      authType: "delegated"      # none | bundle | simple | cognito | delegated
      totpAppName: "Example App"
      totpIssuer: "Example App"
      apiBase: "/auth/"
    routesPrefix: "/chatbot"
    debug:
      injectDebugCommands: false
      animateStreaming: true
```

Use this section for browser-only deployment differences, for example a local
development auth proxy path or a custom SPA route prefix. `auth.turnstile_development_token`
is still read from the `auth` section and is published as
`auth.turnstileDevelopmentToken` when it is non-placeholder.

If `frontend.config.auth.authType` is omitted, it is derived from top-level
auth: `auth.type: simple` emits browser `authType: simple`, `auth.type:
cognito` emits `authType: cognito`, and `auth.type: delegated` emits
`authType: delegated`. `auth.type: bundle` or `auth.idp: session` emits
`authType: bundle`: a bundle/front shell performs login and sets the
descriptor-configured platform cookies, while the platform still validates
requests through `auth.idp: session`. The older browser value `hardcoded` is a
legacy alias for `simple`; new descriptors should use `simple`. `oauth` is not
a deployment auth mode; use `cognito` for the OSS browser Cognito/OIDC flow.

### `proxy.frame_embedding`

`proxy.frame_embedding` controls whether the KDCube control-plane frontend may
be loaded inside another page. The setting is consumed by deployment/proxy
rendering, not by bundle code.

Example for the normal standalone deployment:

```yaml
proxy:
  ssl: false
  route_prefix: "/platform"
  frame_embedding:
    mode: "standalone"
    allowed_origins: []
```

Supported modes:

| Mode | Control-plane shell | Bundle/widget document routes |
|---|---|---|
| `standalone` | `X-Frame-Options: DENY` | `X-Frame-Options: SAMEORIGIN` so the control plane can load its own nested widgets |
| `same_origin` | `X-Frame-Options: SAMEORIGIN` | `X-Frame-Options: SAMEORIGIN` |
| `allowlist` | CSP `frame-ancestors 'self' ...` and no `X-Frame-Options` | same CSP policy, so nested widgets still work inside the embedded control plane |

For cross-origin embedding, list exact browser origins:

```yaml
proxy:
  frame_embedding:
    mode: "allowlist"
    allowed_origins:
      - "https://host-app.example.com"
```

Do not put paths in `allowed_origins`; use origins only. In `standalone`, the
platform can still use iframes internally because bundle/widget documents are
same-origin frameable. External embedding requires `allowlist`, otherwise nested
widget iframes may still be blocked by the browser's ancestor checks.

### `ai.react`

`ai.react` controls React-agent runtime behavior that is safe to keep in the
non-secret assembly descriptor.

Example:

```yaml
ai:
  react:
    react_agent_version: "v3"          # AI_REACT_AGENT_VERSION
    react_agent_multiaction: "off"     # AI_REACT_AGENT_MULTI_ACTION
    max_iterations: 15                 # AI_REACT_MAX_ITERATIONS
    context_max_tokens: 80000          # AI_REACT_CONTEXT_MAX_TOKENS
    read_visible_max_text_symbols: 48000 # AI_REACT_READ_VISIBLE_MAX_TEXT_SYMBOLS
    read_visible_max_tokens: 12000      # AI_REACT_READ_VISIBLE_MAX_TOKENS
    read_visible_max_bytes: 10485760    # AI_REACT_READ_VISIBLE_MAX_BYTES
    read_visible_context_fraction: 0.15 # AI_REACT_READ_VISIBLE_CONTEXT_FRACTION
    exec_text_preview_max_symbols: 8000 # AI_REACT_EXEC_TEXT_PREVIEW_MAX_SYMBOLS
    tool_result_preview_max_text_symbols: 12000 # AI_REACT_TOOL_RESULT_PREVIEW_MAX_TEXT_SYMBOLS
    line_numbers_mode: "lines"         # AI_REACT_LINE_NUMBERS_MODE: disabled | lines | sparsed
    cache_keep_recent_turns: 6         # AI_REACT_CACHE_KEEP_RECENT_TURNS
    cache_keep_recent_intact_turns: 1  # AI_REACT_CACHE_KEEP_RECENT_INTACT_TURNS
    working_summary_enabled: true      # AI_REACT_WORKING_SUMMARY_ENABLED
    pruned_turn_summary_mode: "working_summary"  # AI_REACT_PRUNED_TURN_SUMMARY_MODE
    render_thinking: true              # AI_REACT_RENDER_THINKING
    debug_timeline: false              # AI_REACT_DEBUG_TIMELINE
```

| Field | Env var | Meaning |
|---|---|---|
| `react_agent_version` | `AI_REACT_AGENT_VERSION` | React decision runtime version (`v2` or `v3`) |
| `react_agent_multiaction` | `AI_REACT_AGENT_MULTI_ACTION` | Experimental multi-action decision mode (`on` or `off`) |
| `max_iterations` | `AI_REACT_MAX_ITERATIONS` | Base ReAct decision/tool-use round cap; bundle `config.react.default_agent.max_iterations` or named-agent `config.react.<agent_key>.max_iterations` overrides this default; runtime fallback `15` |
| `context_max_tokens` | `AI_REACT_CONTEXT_MAX_TOKENS` | Default hard model-input budget before compaction when a bundle does not set `max_tokens`; includes system/instruction text plus rendered timeline; default `80000` |
| `read_visible_max_text_symbols` | `AI_REACT_READ_VISIBLE_MAX_TEXT_SYMBOLS` | Default max visible text characters per `react.read` text path; default `48000` |
| `read_visible_max_tokens` | `AI_REACT_READ_VISIBLE_MAX_TOKENS` | Default token guard per `react.read` text path; default `12000` |
| `read_visible_max_bytes` | `AI_REACT_READ_VISIBLE_MAX_BYTES` | Raw byte guard for every `react.read` payload; PDF/image content is attached whole only when under this cap; default `10485760` |
| `read_visible_context_fraction` | `AI_REACT_READ_VISIBLE_CONTEXT_FRACTION` | Additional clamp so one read does not consume more than this fraction of the React context budget; default `0.15` |
| `exec_text_preview_max_symbols` | `AI_REACT_EXEC_TEXT_PREVIEW_MAX_SYMBOLS` | Max text characters embedded as preview for each text file produced by exec tools; default `8000` |
| `tool_result_preview_max_text_symbols` | `AI_REACT_TOOL_RESULT_PREVIEW_MAX_TEXT_SYMBOLS` | Max text characters embedded from a large initial tool result before the prompt renderer replaces the rest with shape/recovery metadata; default `12000` |
| `line_numbers_mode` | `AI_REACT_LINE_NUMBERS_MODE` | How rendered text previews show line numbers: `lines` numbers every line, `sparsed` numbers first/middle/last lines only, and `disabled` omits line prefixes; bundle `config.react.default_agent.line_numbers_mode` or named-agent override takes precedence |
| `cache_keep_recent_turns` | `AI_REACT_CACHE_KEEP_RECENT_TURNS` | Recent turns kept visible after TTL pruning; default `6` |
| `cache_keep_recent_intact_turns` | `AI_REACT_CACHE_KEEP_RECENT_INTACT_TURNS` | Newest turns kept untrimmed during TTL pruning; default `1` |
| `working_summary_enabled` | `AI_REACT_WORKING_SUMMARY_ENABLED` | Capture React `channel:summary` on complete/exit, emit it as `conv.working.summary`, and embed it for memory search; default `true` |
| `pruned_turn_summary_mode` | `AI_REACT_PRUNED_TURN_SUMMARY_MODE` | Prefer working-summary cards when rendering pruned historical turns; multiple same-turn summaries are preserved; set to `working_summary` by default |
| `render_thinking` | `AI_REACT_RENDER_THINKING` | Render live model thinking blocks in the active ReAct timeline; bundle `config.react.default_agent.render_thinking` or named-agent override takes precedence; pruned thinking is never rendered |
| `event_source_pipeline_enabled` | `AI_REACT_EVENT_SOURCE_PIPELINE_ENABLED` | Enables the alternate event-source policy pipeline for ReAct blocks; bundle `config.react.default_agent.event_source_pipeline.enabled` or named-agent override takes precedence; keep `false` unless explicitly testing |
| `debug_timeline` | `AI_REACT_DEBUG_TIMELINE` | Enable rendered prompt snapshot files for ReAct timelines; bundle `config.react.default_agent.debug_timeline` or named-agent override takes precedence; keep `false` for normal deployments |

Visible read limits use separate units:

- `read_visible_max_text_symbols` and per-call `max_text_symbols` apply only to
  text payloads. Oversized text returns a bounded preview by default; per-call
  `max_text_symbols` requests a smaller explicit preview. Caps apply per
  requested path.
- Skills are not read-capped. Owner-defined document/source systems should
  expose their own tools, namespace service endpoints, or rehosters.
- `read_visible_max_tokens` guards the model-visible text budget.
- `read_visible_max_bytes` guards raw bytes for all payloads. PDF/image reads
  are not partially sliced: under the byte cap they are attached whole as
  multimodal content; over the cap React emits a recovery marker.
- `exec_text_preview_max_symbols` affects exec-produced text artifact previews,
  not `react.read`.
- `tool_result_preview_max_text_symbols` affects normal tool-result rendering
  before any `react.read` call. The full `tc:` result remains stored and
  recoverable; only the prompt-visible view is bounded.

These settings are part of the cold-cache cost control path. A long persisted
timeline should render as compact working-summary cards plus recent tail, not
as the full historical conversation. Retrieval-index rows remain the fallback
for historical turns without a working summary. Each retrieval row keeps the
logical path and a small hint; the path is enough to retrieve the full block with
`react.read([path])` when needed.

Browser-tool sessions are lifecycle-managed by the ReAct workflow and proc
processor finalizers. Normal completion, managed errors, watchdog timeout, and
task cancellation all attempt per-turn browser cleanup. The idle janitor TTL,
janitor interval, and max session count are backend constants today; they are
not assembly-backed operator settings yet.

### `platform.services.proc.service`

`platform.services.proc.service` owns proc service runtime controls, including
task watchdog settings used by long-running chat/job turns.

Example:

```yaml
platform:
  services:
    proc:
      service:
        gateway_config_force_env_on_startup: true
        chat_task_timeout_sec: 600
        chat_task_idle_timeout_sec: 600
        chat_task_max_wall_time_sec: 2400
        chat_task_watchdog_poll_interval_sec: 1.0
```

| Field | Env var | Meaning |
|---|---|---|
| `gateway_config_force_env_on_startup` | `GATEWAY_CONFIG_FORCE_ENV_ON_STARTUP` | when true, startup ignores cached Redis gateway config and reloads from `GATEWAY_CONFIG_JSON`, `GATEWAY_YAML_PATH`, or `PLATFORM_DESCRIPTORS_DIR/gateway.yaml` |
| `chat_task_timeout_sec` | `CHAT_TASK_TIMEOUT_SEC` | legacy overall processor event timeout in seconds |
| `chat_task_idle_timeout_sec` | `CHAT_TASK_IDLE_TIMEOUT_SEC` | watchdog idle timeout in seconds; elapsed time since last task activity |
| `chat_task_max_wall_time_sec` | `CHAT_TASK_MAX_WALL_TIME_SEC` | watchdog hard wall-clock limit for one task |
| `chat_task_watchdog_poll_interval_sec` | `CHAT_TASK_WATCHDOG_POLL_INTERVAL_SEC` | watchdog polling interval in seconds |

When the watchdog cancels a task, proc still runs the turn finalization path
and attempts lifecycle cleanup such as turn-scoped browser-session cleanup.

### `platform.services.<component>.exec`

`platform.services.proc.exec` owns platform defaults for isolated Python
execution. Access these defaults through `get_settings().PLATFORM.EXEC`.

Example:

```yaml
platform:
  services:
    proc:
      exec:
        exec_workspace_root: ""
        py_code_exec_image: "py-code-exec:latest"
        py_code_exec_timeout: 600
        py_code_exec_network_mode: "host"
        py_code_exec_container_strategy: "split"
        max_file_bytes: "100m"
        max_exec_workspace_delta_bytes: "250m"
        max_workspace_bytes: ""
        workspace_monitor_interval_s: 0.5
```

| Field | Settings API | Meaning |
|---|---|---|
| `exec_workspace_root` | `get_settings().PLATFORM.EXEC.EXEC_WORKSPACE_ROOT` | container-visible exec workspace root |
| `py_code_exec_image` | `get_settings().PLATFORM.EXEC.PY.PY_CODE_EXEC_IMAGE` | Docker image for the ISO runtime |
| `py_code_exec_timeout` | `get_settings().PLATFORM.EXEC.PY.PY_CODE_EXEC_TIMEOUT` | default Python execution timeout in seconds |
| `py_code_exec_network_mode` | `get_settings().PLATFORM.EXEC.PY.PY_CODE_EXEC_NETWORK_MODE` | Docker network mode for the ISO supervisor container |
| `py_code_exec_container_strategy` | `get_settings().PLATFORM.EXEC.PY.PY_CODE_EXEC_CONTAINER_STRATEGY` | `split` runs supervisor and generated code in separate containers and is the default; `combined` keeps the older single exec container |
| `max_file_bytes` | `get_settings().PLATFORM.EXEC.PY.EXEC_MAX_FILE_BYTES` | max single generated file size per isolated exec call |
| `max_exec_workspace_delta_bytes` | `get_settings().PLATFORM.EXEC.PY.EXEC_MAX_WORKSPACE_DELTA_BYTES` | max net-new monitored writable bytes per isolated exec call |
| `max_workspace_bytes` | `get_settings().PLATFORM.EXEC.PY.EXEC_MAX_WORKSPACE_BYTES` | optional max total bytes currently present in the active workspace writable roots before finalization/offload |
| `workspace_monitor_interval_s` | `get_settings().PLATFORM.EXEC.PY.EXEC_WORKSPACE_MONITOR_INTERVAL_S` | polling interval for workspace quota enforcement |

The ISO runtime passes the limit values into the isolated executor as internal
`EXEC_*` transport env vars. Those env vars are not the operator-facing source
of configuration; set the descriptor fields above instead.

Bundles may override these limits for their own execution profile through
bundle props (`config.execution.runtime` or legacy `config.exec_runtime`). The
override is applied only to that bundle run.

### `platform.services.proc.bundles`

`platform.services.proc.bundles` owns proc runtime bundle behavior that is not
part of the bundle inventory itself. Bundle inventory stays in `bundles.yaml`.

Example:

```yaml
platform:
  services:
    proc:
      bundles:
        bundles_preload_bundle_lock_ttl_seconds: 300
        bundle_scheduler_reconcile_interval_seconds: 0
```

| Field | Settings API | Meaning |
|---|---|---|
| `bundles_preload_bundle_lock_ttl_seconds` | `get_settings().PLATFORM.APPLICATIONS.BUNDLES_PRELOAD_BUNDLE_LOCK_TTL_SECONDS` | per-bundle startup preload claim TTL in seconds; stale claims may be retried by another proc |
| `bundle_scheduler_reconcile_interval_seconds` | `get_settings().PLATFORM.APPLICATIONS.BUNDLE_SCHEDULER_RECONCILE_INTERVAL_SECONDS` | periodic scheduler reconciliation interval in seconds; `0` disables the periodic loop |

The scheduler still reconciles on proc startup and on bundle update
notifications. The periodic loop is only the catch-up path for environments
that want scheduler convergence even if a notification is missed.

### `events`

`events.record` sets the platform-level defaults for comm event recording. Two
independent subsystems share this configuration:

- `telemetry` — which event types are buffered and shipped to the telemetry sink
  at end of turn
- `persist` — which event types are saved into the `conv.artifacts.events`
  artifact at end of turn

Per-bundle overrides go in `bundles.yaml -> items[].config.events`. Bundle-level
fields are merged on top of the assembly defaults field-by-field: a bundle can
override only `enabled`, only `selector`, or both. The `selector` list is
replaced as a whole when present — lists are not concatenated.

```yaml
events:
  record:
    telemetry:
      enabled: true
      selector:
        - "accounting.usage"
        - "chat.complete"
        - "chat.error"
        - "chat.conversation.accepted"
        - "chat.conversation.turn.completed"
        - "react.tool.call"
        - "react.skill.read"
        - "queue.continuation.accepted"
        - "timeline.external.accepted"
        - "bundle.workflow.turn.started"
        - "bundle.workflow.turn.completed"
        - "bundle.workflow.turn.failed"
        - "bundle.mcp.call"
        - "chat.turn.summary"
    persist:
      # Types must be emitted through the bundle entrypoint comm. Types emitted
      # by the processor comm (e.g. chat.complete) are not accessible because
      # the artifact is saved before the processor runs.
      enabled: true
      selector:
        - "accounting.usage"
        - "chat.turn.summary"
```

| Field | Meaning |
|---|---|
| `events.record.telemetry.enabled` | enables telemetry recording for the turn; `false` skips the sink flush entirely |
| `events.record.telemetry.selector` | event types recorded and shipped to the configured telemetry sink |
| `events.record.persist.enabled` | enables the `conv.artifacts.events` artifact; `false` produces no artifact |
| `events.record.persist.selector` | event types saved into the artifact; must be emitted through the bundle entrypoint comm |

Resolution order for each bundle: bundle props > `assembly.yaml` > code defaults.

## Fields that are local-run only

`paths.*` is local-run topology, not cloud deployment topology.

Supported keys:

- `paths.host_kdcube_storage_path`
- `paths.host_bundles_path`
- `paths.host_managed_bundles_path`
- `paths.host_bundle_storage_path`
- `paths.host_exec_workspace_path`

These keys exist so the local installer and local runtime know which host
directories should back the container-visible paths.

## `paths.*` by run mode

| Field | CLI local compose | Direct local service run | AWS deployment |
|---|---|---|---|
| `host_kdcube_storage_path` | relevant; mounted into container-backed local storage | optional; relevant only if the process should use that host storage path | ignore |
| `host_bundles_path` | relevant for non-managed local path bundles; mounted as `/bundles` | optional; relevant only if proc needs a host-visible local bundle root | ignore |
| `host_managed_bundles_path` | relevant for platform-managed bundles; mounted as `/managed-bundles` | optional; separate host root for git-resolved/example bundles | ignore |
| `host_bundle_storage_path` | relevant; mounted as `/bundle-storage` | optional; relevant only if local runtime should use host file-backed bundle storage | ignore |
| `host_exec_workspace_path` | relevant; mounted as `/exec-workspace` | optional; relevant only if local exec runtime should use a host workspace root | ignore |

The rule is simple:

- use `paths.*` for local development and local compose
- do not rely on `paths.*` for AWS/ECS descriptors

## Local compose contract

In CLI compose mode, the installer promotes `assembly.paths.*` into main compose
env keys:

- `HOST_KDCUBE_STORAGE_PATH`
- `HOST_BUNDLES_PATH`
- `HOST_MANAGED_BUNDLES_PATH`
- `HOST_BUNDLE_STORAGE_PATH`
- `HOST_EXEC_WORKSPACE_PATH`
- `HOST_REACT_DEBUG_PATH`

### `paths.*` -> runtime env mapping

| Env var | `assembly.yaml` path | Modes |
|---|---|---|
| `HOST_KDCUBE_STORAGE_PATH` | `paths.host_kdcube_storage_path` | CLI local compose |
| `HOST_BUNDLES_PATH` | `paths.host_bundles_path` | CLI local compose |
| `HOST_MANAGED_BUNDLES_PATH` | `paths.host_managed_bundles_path` | CLI local compose |
| `HOST_BUNDLE_STORAGE_PATH` | `paths.host_bundle_storage_path` | CLI local compose |
| `HOST_EXEC_WORKSPACE_PATH` | `paths.host_exec_workspace_path` | CLI local compose |
| `HOST_REACT_DEBUG_PATH` | `paths.host_react_debug_path` | CLI local compose and ECS EC2 host mount |
| `REACT_DEBUG_ROOT` | `platform.services.proc.react_debug.debug_root` | proc runtime path for timeline render debug |
| `REACT_DEBUG_KEEP_FILES` | `platform.services.proc.react_debug.keep_files` | rolling retention for timeline render debug |

Those host directories are then mounted into the containers at stable
container-visible paths such as:

- `/kdcube-storage`
- `/bundles`
- `/managed-bundles`
- `/bundle-storage`
- `/exec-workspace`
- `/react-debug`

So in `bundles.yaml`:

- non-managed local path bundles must use container-visible paths like `/bundles/...`
- platform-managed bundles are materialized under `/managed-bundles/...`
- not raw host paths from your laptop

## Direct local proc/ingress contract

When you run proc or ingress directly on the host, `assembly.yaml` is not
mounted automatically.

Use:

- `ASSEMBLY_YAML_DESCRIPTOR_PATH=/abs/path/to/assembly.yaml`

If code uses plain descriptor reads, that is enough for `assembly.yaml`.

`paths.*` is optional in this mode. It matters only if the service itself must
resolve host-facing runtime directories.

Example:

- direct proc debug that uses local exec workspace or local bundle roots

If you only need plain config reads, `ASSEMBLY_YAML_DESCRIPTOR_PATH` is the
important setting, not `paths.*`.

## AWS deployment contract

For AWS/ECS deployment:

- `assembly.yaml` is deployment input
- runtime may still read a mounted `/config/assembly.yaml`
- storage and mount topology comes from the deployment stack, not from
  `paths.*`

Do not put laptop or EC2 host paths into production descriptors.

For cloud deployments:

- keep `context`, `auth`, `proxy`, `ports`, `storage`, `infra`, and
  deployment-facing settings
- omit or ignore `paths.*`

## Frontend section

`frontend.*` is relevant to the CLI custom-UI compose path.

It is installer-facing metadata for:

- which frontend repo to clone
- which ref to use
- which Dockerfile to build
- which UI source path to build
- which frontend runtime config template to patch

It is not consumed directly by the runtime services.

## Minimal examples

### CLI local compose with local path bundles

```yaml
context:
  tenant: demo
  project: demo-local

secrets:
  provider: secrets-file

paths:
  host_bundles_path: "/Users/you/src"
  host_bundle_storage_path: "/Users/you/.kdcube/runtime/data/bundle-storage"
  host_exec_workspace_path: "/Users/you/.kdcube/runtime/data/exec-workspace"
  host_react_debug_path: "/Users/you/.kdcube/runtime/data/react-debug"

platform:
  services:
    proc:
      react_debug:
        debug_root: "/react-debug"
        keep_files: 100
```

### Direct local proc debug

```yaml
context:
  tenant: demo
  project: demo-direct

secrets:
  provider: secrets-file
```

Then point the process to the file with:

```bash
ASSEMBLY_YAML_DESCRIPTOR_PATH=/abs/path/to/assembly.yaml
```

Add `paths.*` only if the process really needs those host directories.

### AWS deployment

```yaml
context:
  tenant: acme
  project: prod

secrets:
  provider: aws-sm

storage:
  kdcube: "s3://..."
  bundles: "s3://..."
```

Do not carry over local `paths.*`.
