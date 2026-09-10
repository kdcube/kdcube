---
id: repo:kdcube-ai-app/app/ai-app/docs/configuration/assembly-descriptor-README.md
title: "Platform Assembly Descriptor"
summary: "Platform-level non-secret deployment configuration in assembly.yaml: tenant/project identity, auth, models, services, ports, storage backends, local runtime paths, and frontend/runtime wiring."
tags: ["service", "configuration", "platform", "deployment", "assembly", "descriptor"]
keywords: ["platform deployment identity", "tenant and project scope", "auth and cognito settings", "service port layout", "storage and workspace backends", "runtime path wiring", "application preparation concurrency", "application preparation retry", "bundle descriptor provider", "frontend build metadata", "local compose topology", "aws deployment mapping"]
updated_at: 2026-09-07
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/descriptors-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/service-runtime-configuration-mapping-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundles-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/secrets-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/gateway-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/arch/proc/application-startup-health-and-readiness-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md
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
| `AUTH_PROVIDER` | `auth.connection_hub` selects Connection Hub provider | `get_settings()` | effective runtime value |
| `COGNITO_REGION` | selected Connection Hub platform provider `authenticator.region` | `get_settings()` | CLI local compose, AWS deployment |
| `COGNITO_USER_POOL_ID` | selected Connection Hub platform provider `authenticator.user_pool_id` | `get_settings()` | CLI local compose, AWS deployment |
| `COGNITO_APP_CLIENT_ID` | selected Connection Hub platform provider `authenticator.app_client_id` | `get_settings()` | CLI local compose, AWS deployment |
| `COGNITO_SERVICE_CLIENT_ID` | selected Connection Hub platform provider `authenticator.service_client_id` | `get_settings()` | CLI local compose, AWS deployment |
| `ID_TOKEN_HEADER_NAME` | selected Connection Hub platform provider `authenticator.id_token_header_name` | `get_settings()` | CLI local compose, AWS deployment |
| `AUTH_TOKEN_COOKIE_NAME` | selected Connection Hub platform provider `authenticator.cookie.auth_token_cookie_name` | `get_settings()` / web-proxy env | CLI local compose, AWS deployment |
| `ID_TOKEN_COOKIE_NAME` | selected Connection Hub platform provider `authenticator.cookie.id_token_cookie_name` | `get_settings()` / web-proxy env | CLI local compose, AWS deployment |
| `JWKS_CACHE_TTL_SECONDS` | selected Connection Hub platform provider `authenticator.jwks_cache_ttl_seconds` | `get_settings()` | CLI local compose, AWS deployment |
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
| `APPLICATION_PREPARATION_CONCURRENCY` | `platform.services.proc.bundles.application_preparation_concurrency` | `get_settings().PLATFORM.APPLICATIONS` | proc in all modes |
| `APPLICATION_PREPARATION_RETRY_INITIAL_SECONDS` | `platform.services.proc.bundles.application_preparation_retry_initial_seconds` | `get_settings().PLATFORM.APPLICATIONS` | proc in all modes |
| `APPLICATION_PREPARATION_RETRY_MAX_SECONDS` | `platform.services.proc.bundles.application_preparation_retry_max_seconds` | `get_settings().PLATFORM.APPLICATIONS` | proc in all modes |
| `BUNDLE_SCHEDULER_RECONCILE_INTERVAL_SECONDS` | `platform.services.proc.bundles.bundle_scheduler_reconcile_interval_seconds` | `get_settings().PLATFORM.APPLICATIONS` | proc in all modes |

## Fields that are always meaningful

These sections are normal platform configuration in every mode:

- `context.*`
- `auth.*`
- `proxy.*`
- `ports.*`
- `storage.*`
- `infra.*`
- `models.*`
- `services.*`
- `aws.region`

They are consumed either:

- by the installer/deployment layer
- by runtime env rendering
- or by direct `read_plain(...)` reads from `assembly.yaml`

### Direct-agent model selection

A direct SDK agent host selects one default model with an explicit provider
and model ID:

```yaml
models:
  default_llm_provider: custom
  default_llm_model_id: <model-tag-served-by-the-endpoint>

services:
  llm:
    custom:
      endpoint: http://127.0.0.1:11500/generate
      num_ctx: 32768
```

`configured_model_selection()` resolves the pair for any direct adapter.
`build_model_service()` additionally projects it into a role-bound
`ModelServiceBase`. An unknown model ID without an explicit provider fails
instead of falling back to a different registered model. `provider: custom`
requires an absolute HTTP(S) endpoint and accepts one positive shared
`num_ctx`; the model ID is sent unchanged on every request. A protected custom
gateway reads `platform.services.llm.custom.api_key` from `secrets.yaml`.

Native ReAct and LangGraph use this model-service route in the runnable agent
examples. A provider-native subprocess can consume the same model selection
while retaining its own protocol constraints; the Claude Code example accepts
the Anthropic provider only. Hosted application model picks remain bundle
configuration under `role_models` or the application's supported-model list.

### Platform Auth Selection

`assembly.yaml` selects the platform authority provider. Provider details and
token transport names are registered in Connection Hub.

Example:

```yaml
auth:
  type: "cognito"               # simple | cognito | delegated | bundle
  connection_hub:
    bundle_id: connection-hub@1-0
    authority_id: kdcube.platform
    provider_id: cognito

  proxy_login:
    redis_key_prefix: "proxylogin:<TENANT>:<PROJECT>:"
    token_masquerade: true
    enforce_mfa: false
    http_urlbase: "https://YOUR_DOMAIN/auth"
```

The auth and identity cookie names are not frontend-only settings. They are
registered on the selected Connection Hub platform provider and rendered into
ingress/proc runtime env and into the delegated web-proxy. The proxy uses them
to detect the non-masquerade path where a top-level login flow has already set
the real auth and identity cookies. If either cookie is missing, the delegated
proxy keeps using the existing `/auth/unmask` flow.

`auth.proxy_login.token_masquerade` controls how proxylogin issues browser
cookies. It does not change the backend token validator; ingress/proc still
validate tokens using the configured auth provider.

`auth.proxy_login.enforce_mfa` maps to Proxy Login `COGNITO_ENFORCEMFA`. When
enabled, Proxy Login enforces MFA during the Cognito login flow.

The session lane (application-hosted platform login, or the platform-hosted
sign-in) is selected by the Connection Hub provider that `auth.connection_hub`
names: a `bundle_session_login` provider puts the platform on it. `auth.idp:
session` is the fallback selector for descriptors without a Connection Hub
provider. On this lane an application/front shell, or the platform itself,
validates an external identity and the platform session authority issues the
platform-recognized `kst1.*` cookie. It requires
`platform.services.session_token.secret` in `secrets.yaml`. See
[Application-Hosted Platform Login And Session](../service/auth/app-hosted-platform-login-and-session-README.md).
When the selected session provider's `input.authenticator_ref` names a Cognito
or OIDC provider, the platform hosts the sign-in itself on
`/api/platform/session/login`: [Platform-Hosted Sign-In](../service/auth/app-hosted-platform-login-and-session-README.md#platform-hosted-sign-in-the-server-held-browser-session).

`auth.authenticators` configures request-auth surfaces. The platform
authenticator itself is derived from the selected Connection Hub platform
authority provider; assembly does not carry Cognito pool/client/cookie
configuration.

```yaml
auth:
  type: cognito
  connection_hub:
    bundle_id: "connection-hub@1-0"
    authority_id: "kdcube.platform"
    provider_id: "cognito"

  authenticators:
    connection_hub:
      enabled: true
      app_id: "connection-hub@1-0"
      operation: "request_authenticate"
```

`auth.connection_hub` selects which Connection Hub authority/provider instance
supplies the concrete platform auth configuration. `auth.authenticators` is for
additional request-auth surfaces, such as the Connection Hub external-channel
surface.

When Connection Hub is enabled, ingress/proc first accept a valid platform
token/cookie session when one is present. If no platform session is established,
the resolver asks the Connection Hub authentication surface. That surface calls
the Connection Hub operation only when the request carries external auth
material or selector hints. Provider-specific authenticators, such as Telegram
Mini App `initData`, are Connection Hub modules with access to Connection Hub
config, secrets, and connection-edge data.
See
[Auth Selector](../service/auth/auth-selector-README.md) and
[Request Authenticators](../sdk/solutions/connections/request-authenticators/request-authenticators-README.md).

For Cognito and Multi-Cognito, `auth.connection_hub` points at a provider under
`connection-hub@1-0.config.authority_registry`. That provider supplies the
browser-facing OIDC config and the server-side trust list.

```yaml
items:
  - id: connection-hub@1-0
    config:
      authority_registry:
        authorities:
          kdcube.platform:
            platform: true
            providers:
              cognito:
                type: multi_cognito
                authenticator:
                  region: eu-west-1
                  user_pool_id: eu-west-1_PRIMARY
                  app_client_id: primary-client
                  hosted_ui_domain: https://auth.example.com
                  trusted_providers:
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

### Connection Hub Delegated Credential Adapters

Delegated credential adapters, including the current OAuth delegated credential adapter, are not
configured under `assembly.yaml -> auth`. They belong to the `connection-hub@1-0`
bundle config in `bundles.yaml`:

```yaml
bundles:
  items:
    - id: "connection-hub@1-0"
      config:
        connections:
          delegated_credentials:
            oauth:
              enabled: true
              brand: "KDCube"
```

Assembly still owns deployment context such as `context.tenant` and
`context.project`. Platform auth/session settings used by the adapter are
owned by the selected Connection Hub authority provider.

See [OAuth Delegated Credential Protocol Adapter](../sdk/solutions/connections/delegated-credentials/oauth-delegated-credential-protocol-adapter-README.md).

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

For Cognito, declare `hosted_ui_domain` on the selected platform authority
provider's `authenticator` (or under `auth.cognito` for the legacy top-level
shape). The generated `auth.oidcConfig` then contains the matching `authority`,
`client_id`, and `end_session_endpoint`. A browser takes all three from the
same `/api/cp-frontend-config` response; combining runtime login metadata with
a site-local logout domain can address two different Cognito pools.

Example:

```yaml
frontend:
  config:
    auth:
      authType: "delegated"      # none | bundle | simple | cognito | delegated
      totpAppName: "Example App"
      totpIssuer: "Example App"
      apiBase: "/auth/"
    routesPrefix: "/platform"
    debug:
      injectDebugCommands: false
      animateStreaming: true
```

Use this section for browser-only deployment differences, for example a local
development auth proxy path or a custom SPA route prefix. `auth.turnstile_development_token`
is still read from the `auth` section and is published as
`auth.turnstileDevelopmentToken` when it is non-placeholder.

`auth.type: simple` is the one mode with a browser credential in this section.
`kdcube init` and `kdcube config apply --auth-type simple` write
`frontend.config.auth.authType` and `frontend.config.auth.token`, defaulting the
token to the development administrator seeded into the SimpleIDP store. The
other auth types clear the whole block, so a token set for `simple` does not
survive a switch away and back.

If `frontend.config.auth.authType` is omitted, it is derived from top-level
auth: `auth.type: simple` emits browser `authType: simple`, `auth.type:
cognito` emits `authType: cognito`, and `auth.type: delegated` emits
`authType: delegated`. `auth.type: bundle` or `auth.idp: session` emits
`authType: bundle`: the server side owns login (an application-hosted page or
the platform's own sign-in route) and the browser only probes `/profile`;
the platform validates requests through the session provider selected by
`auth.connection_hub` (fallback `auth.idp: session`). The older browser value `hardcoded` is a
legacy alias for `simple`; new descriptors should use `simple`. `oauth` is not
a deployment auth mode; use `cognito` for the OSS browser Cognito/OIDC flow.

### `proxy.route_prefix`

`proxy.route_prefix` is the non-root URL mount owned by the KDCube control
plane. The reference value is `/platform`; multi-segment mounts such as
`/control/ui` are supported.

The same descriptor value configures the frontend build/runtime and renders
the generated OpenResty route matrix:

```text
<route_prefix>       -> redirect to <route_prefix>/chat
<route_prefix>/*     -> control-plane frontend, with the prefix stripped
/sites/*, /, /<path> -> application-site origin through proc
```

`proxy.route_prefix: /` is valid only when no application-hosted site is
enabled. An enabled site requires a non-root mount because root clean paths
belong to that site.

### `proxy.forwarded_proto`

`proxy.forwarded_proto.source` tells a non-TLS local OpenResty deployment where
to obtain the browser-visible request scheme that it forwards to applications.
The setting is rendered into the generated proxy configuration by the CLI.

Accepted values are:

| Value | Meaning |
| --- | --- |
| `request` | Default. Use the scheme received directly by OpenResty. This is the safe choice for direct local access. |
| `trusted_x_forwarded_proto` | Use `X-Forwarded-Proto` supplied by a trusted TLS terminator that is the effective ingress. Untrusted callers must not be able to bypass that terminator. |

An omitted or unknown value fails safe to `request`.

The default is the scheme received directly by OpenResty:

```yaml
proxy:
  forwarded_proto:
    source: "request"
```

Use the forwarded header only when a trusted TLS terminator is the effective
ingress and it overwrites or appends its own `X-Forwarded-Proto` observation:

```yaml
proxy:
  forwarded_proto:
    source: "trusted_x_forwarded_proto"
```

In that mode, OpenResty takes the rightmost header value, accepts only `http`
or `https`, and falls back to its immediate request scheme for any other value.
The setting declares which input to use; it does not restrict network access to
the proxy. The deployment remains responsible for preventing an untrusted
caller from bypassing the declared terminator. TLS configurations that terminate
HTTPS in OpenResty use the immediate request scheme.

Every proxy before OpenResty must preserve the same provenance. For example,
when a local ngrok agent terminates TLS and forwards through Caddy, Caddy must
trust the loopback ngrok peer; otherwise Caddy replaces ngrok's `https` value
with its own inward `http` scheme before OpenResty can validate it. The complete
Caddy/ngrok configuration is in
[Serving Local KDCube With Ngrok](../service/cicd/ngrok-README.md#trust-the-local-ngrok-hop-in-caddy).

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
  before any `react.read` call. The full `conv:tc:` result remains stored and
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
        py_code_exec_network_mode: "auto"
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
| `py_code_exec_network_mode` | `get_settings().PLATFORM.EXEC.PY.PY_CODE_EXEC_NETWORK_MODE` | Network selection for the trusted ISO supervisor. `auto` uses `host` for a host-run processor and shares the current processor container's existing network namespace under Docker-in-Docker. Explicit Docker modes remain supported. The split generated-code executor always uses `none`. |
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
        static_widget_delivery_mode: deployed
        bundles_preload_bundle_lock_ttl_seconds: 300
        application_preparation_concurrency: 4
        application_preparation_retry_initial_seconds: 2
        application_preparation_retry_max_seconds: 60
        bundle_scheduler_reconcile_interval_seconds: 0
```

| Field | Settings API | Meaning |
|---|---|---|
| `static_widget_delivery_mode` | `get_settings().PLATFORM.APPLICATIONS.STATIC_WIDGET_DELIVERY_MODE` | `legacy` serves prepared files through the app-resolving route; `shadow` also publishes deployment manifests but still serves through legacy; `deployed` prefers the role-guarded manifest path and falls back to prepared legacy serving when its manifest is missing or stale. No mode builds from an HTTP request. |
| `bundles_preload_bundle_lock_ttl_seconds` | `get_settings().PLATFORM.APPLICATIONS.BUNDLES_PRELOAD_BUNDLE_LOCK_TTL_SECONDS` | compatibility-named lower bound used by the shared app-resource generation lock; a crashed owner's heartbeat stops and another worker may retry after expiry |
| `application_preparation_concurrency` | `get_settings().PLATFORM.APPLICATIONS.APPLICATION_PREPARATION_CONCURRENCY` | maximum process-local application preparation tasks running concurrently; minimum `1` |
| `application_preparation_retry_initial_seconds` | `get_settings().PLATFORM.APPLICATIONS.APPLICATION_PREPARATION_RETRY_INITIAL_SECONDS` | initial retry delay after an app preparation attempt fails |
| `application_preparation_retry_max_seconds` | `get_settings().PLATFORM.APPLICATIONS.APPLICATION_PREPARATION_RETRY_MAX_SECONDS` | maximum delay for per-app exponential retry backoff |
| `bundle_scheduler_reconcile_interval_seconds` | `get_settings().PLATFORM.APPLICATIONS.BUNDLE_SCHEDULER_RECONCILE_INTERVAL_SECONDS` | periodic scheduler reconciliation interval in seconds; `0` disables the periodic loop |

The scheduler still reconciles on proc startup and on bundle update
notifications. The periodic loop is only the catch-up path for environments
that want scheduler convergence even if a notification is missed.

The static-widget deployment mode is assembly-only. See
[App Deployment And Static Widget Delivery](../sdk/bundle/app-deployment-and-static-widget-delivery-README.md)
for the lifecycle, policy manifest, local/EFS behavior, and rollback switch.

Application preparation runs for every configured app. Aggregate readiness
policy is selected per app through `bundles.items[].service.readiness`, not by
an assembly-wide preload switch. See
[Application Startup, Health, And Readiness](../arch/proc/application-startup-health-and-readiness-README.md).

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
