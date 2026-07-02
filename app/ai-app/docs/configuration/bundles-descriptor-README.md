---
id: repo:kdcube-ai-app/app/ai-app/docs/configuration/bundles-descriptor-README.md
title: "Bundles Descriptor"
summary: "Bundle registry and non-secret bundle deployment configuration in bundles.yaml: default bundle, git or local bundle sources, module paths, and bundle-scoped config."
tags: ["service", "configuration", "bundle", "bundle-registry", "deployment", "descriptor"]
keywords: ["bundle registry", "default bundle selection", "git bundle source", "local path bundle source", "bundle module mapping", "bundle configuration", "bundle inventory", "file-backed bundle authority", "bundle reload workflow", "deployment bundle catalog"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/cicd/descriptors-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/assembly-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundles-secrets-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/service-runtime-configuration-mapping-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundle-runtime-configuration-and-secrets-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-properties-and-secrets-lifecycle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-configure-and-run-bundle-README.md
---
# Bundles Descriptor

`bundles.yaml` defines:

- which bundles exist
- which bundle is the default
- non-secret bundle descriptor fields such as:
  - `repo`
  - `ref`
  - `subdir`
  - `module`
  - `path`
  - `config`

It does not carry bundle secrets.

This page is about the descriptor contract itself.
For the operational local workflow for reusing a runtime, changing bundle roots, and running `kdcube`, use:

- [how-to-configure-and-run-bundle-README.md](../sdk/bundle/build/how-to-configure-and-run-bundle-README.md)

## Direct runtime contract from this descriptor

### Supported access APIs

| Need | API | Notes |
|---|---|---|
| raw value from `bundles.yaml` | `read_plain("b:...")` / `get_plain("b:...")` | Reads the descriptor file directly |
| effective current bundle config | `self.bundle_prop("...")` | Reads the active runtime bundle authority, not the raw file |
| startup reseed from bundle descriptor authority | `BUNDLES_FORCE_ENV_ON_STARTUP=1` | Proc resets the bundle registry from the current local descriptor authority on startup |

### File-resolution and runtime env vars

| Env var | Descriptor path or role | Primary API / behavior | Modes |
|---|---|---|---|
| `BUNDLES_YAML_DESCRIPTOR_PATH` | explicit `bundles.yaml` path | `read_plain("b:...")`, local descriptor authority | direct local service run; explicit override in compose/Kubernetes |
| `HOST_BUNDLES_DESCRIPTOR_PATH` | host file staged into `/config/bundles.yaml` | CLI installer mount | CLI local compose |
| `PLATFORM_DESCRIPTORS_DIR` | fallback directory containing `bundles.yaml` | descriptor file discovery | direct local service run |
| `BUNDLES_FORCE_ENV_ON_STARTUP` | startup flag, not a YAML field | proc reseeds from current local descriptor authority | proc |
| `BUNDLE_SCHEDULER_RECONCILE_INTERVAL_SECONDS` | `assembly.yaml` proc bundle setting | periodic scheduler catch-up from active bundle descriptor authority; `0` disables | proc |
| `BUNDLE_GIT_RESOLUTION_ENABLED` | git bundle items in `bundles.yaml` | enables git bundle resolution | proc |

### Descriptor fields that matter to runtime

| `bundles.yaml` field | Used by | Meaning |
|---|---|---|
| `bundles.default_bundle_id` | `read_plain("b:default_bundle_id")`, runtime routing | default bundle |
| `bundles.items[].id` | runtime registry | bundle identifier |
| `bundles.items[].repo` / `ref` / `subdir` / `module` | proc git resolution | git-backed bundle definition |
| `bundles.items[].path` / `module` | proc local-path loading | local development bundle definition |
| `bundles.items[].config` | `self.bundle_prop("...")` | non-secret effective bundle config |

### Reserved runtime config under `config`

Most values under `bundles.items[].config` belong only to the bundle. A small
set is platform-reserved and interpreted by the runtime.

`config.enabled` is the platform-owned feature gate override section for bundle
surfaces. Bundle code, decorators, and bundle `configuration_defaults()` define
the default state. Descriptor config only overrides those defaults. If a key is
absent here, the runtime uses the code default; if a key is present, that value
overrides the default. Most descriptors should contain only intentional
overrides, usually `false` for a rare disable.

Canonical keys:

| Surface | Key |
|---|---|
| bundle | `enabled.bundle` |
| API | `enabled.api["<route>.<alias>.<METHOD>"]` |
| MCP | `enabled.mcp.<alias>` |
| widget | `enabled.widget.<alias>` |
| cron | `enabled.cron.<alias>` |

Do not mirror every enabled API/widget/cron as `true` in deployment descriptors.
That makes descriptors noisy and can leave stale explicit overrides after code
defaults change.

`config.surfaces.as_provider` is the platform-owned policy section for surfaces
this bundle exposes. It is separate from `enabled`: `enabled` decides whether a
surface exists; `surfaces.as_provider` decides who can see/call it and which
authority/grants are required.

`connection-hub@1-0.config.authority_registry` is also platform-owned. It is
the canonical registry for authority realms and provider instances, including
the Cognito platform authority provider. `assembly.yaml` selects a provider via
`auth.connection_hub`; the provider's concrete details live here.

Example platform Cognito provider:

```yaml
bundles:
  items:
    - id: connection-hub@1-0
      config:
        authority_registry:
          authorities:
            kdcube.platform:
              label: KDCube platform authority
              platform: true
              providers:
                cognito:
                  type: multi_cognito
                  enabled: true
                  authenticator:
                    type: cognito_id_token
                    id_token_header_name: X-ID-Token
                    region: eu-west-1
                    user_pool_id: eu-west-1_PRIMARY
                    app_client_id: primary-client
                    service_client_id: primary-client
                    cookie:
                      auth_token_cookie_name: __Secure-LATC
                      id_token_cookie_name: __Secure-LITC
                      masqueraded_token_cookie_name: __Secure-LMTC
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

Provider surface policy keys:

| Surface | Policy path |
|---|---|
| bundle | `surfaces.as_provider.bundle.visibility` |
| API | `surfaces.as_provider.api.<route>.<alias>.<METHOD>.visibility` / `.auth` |
| API alias fallback | `surfaces.as_provider.api.<route>.<alias>.visibility` / `.auth` |
| MCP | `surfaces.as_provider.mcp.<alias>.auth` |
| widget | `surfaces.as_provider.widget.<alias>.visibility` / `.auth` |

Example:

```yaml
bundles:
  items:
    - id: "my.bundle@1-0"
      config:
        surfaces:
          as_provider:
            bundle:
              visibility:
                allowed_roles: []
            api:
              operations:
                admin_data:
                  GET:
                    visibility:
                      roles: [kdcube:role:super-admin]
                    auth:
                      authority_id: platform
                      grants: [admin:read]
            widget:
              dashboard:
                visibility:
                  user_types: []
                  roles: []
            mcp:
              feedback:
                auth:
                  mode: managed
                  authority_id: delegated_client
                  tools:
                    conversations_export:
                      grants: [conversations:read]
                  selected_tool_grants: true
              knowledge:
                auth:
                  mode: bundle
                  header_name: X-Knowledge-MCP-Token
```

`mode: managed` means the proc MCP bridge verifies the delegated bearer
credential and each called MCP tool's required grants before the request enters
the bundle MCP app. If `mode` is absent or set to `bundle`, the auth block is
treated as bundle-owned metadata; bundle code reads it and enforces its own
header/JWT/API-key scheme.

```yaml
surfaces:
  as_provider:
    mcp:
      knowledge:
        auth:
          mode: bundle
          header_name: X-Knowledge-MCP-Token
```

Connection Hub delegated credential protocol adapters are configured on the
Connection Hub bundle itself:

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

The adapter is served by the `connection-hub@1-0` public `oauth` operation. It
issues delegated credentials that can be consumed by managed bundle surfaces,
for example a bundle MCP endpoint with `surfaces.as_provider.mcp.<alias>.auth`.

`config.execution.runtime` controls per-bundle execution runtime routing and
per-run ISO runtime limits. `config.exec_runtime` is the legacy alias.

Example:

```yaml
bundles:
  items:
    - id: "my.bundle@1-0"
      config:
        execution:
          runtime:
            mode: "docker"              # none | local | docker | fargate | external
            container_strategy: "split" # optional: combined | split; docker mode only
            max_file_bytes: "50m"       # overrides assembly default for this bundle exec call
            max_exec_workspace_delta_bytes: "100m" # overrides assembly default for this bundle exec call
            max_workspace_bytes: "150m" # optional cap for the active workspace before finalization/offload
            workspace_monitor_interval_s: 0.5
            descriptor_payload_scope: active_bundle # optional: filter bundle descriptors sent to supervisor
```

Platform defaults for these limits live in `assembly.yaml` under
`platform.services.proc.exec`. Bundle overrides are applied only to the
execution run that uses that bundle profile.

By default, Docker/Fargate supervisors receive full descriptor payloads because
the supervisor is platform trusted. Set
`execution.runtime.descriptor_payload_scope: active_bundle` when you want
`bundles.yaml` and `bundles.secrets.yaml` narrowed to the caller bundle before
transport. Platform descriptors (`assembly.yaml`, `gateway.yaml`, global
`secrets.yaml`) remain full.

`config.memory` is the reserved User Memory subsystem config for bundles that
derive from the memory entrypoint mixin. It is deployment-scoped config, not the
user memory records themselves.

Example:

```yaml
bundles:
  items:
    - id: "my.bundle@1-0"
      config:
        memory:
          enabled: true
          announce:
            enabled: true
            limit: 6
            scope_filter: current_bundle # current_bundle | all_user_memories
          tools:
            enabled: true
            allow_write: false # keep read-only until durable writes are policy-approved
            default_scope_filter: current_bundle
            embedding_enabled: true
          widget:
            enabled: true
            allow_write: true
            default_scope_filter: current_bundle
            allow_all_user_memories: true
            ensure_schema: true
          reconciliation:
            enabled: true
          snapshots:
            enabled: true
        ui:
          widgets:
            memories:
              enabled: true
            telegram_miniapp:
              shared_sources:
                memory_widget:
                  src_folder: sdk://context/memory/ui/widget/memories
                  target: _shared/memory-widget
```

`memory.enabled` gates the subsystem. `memory.announce` projects a read-only
hotset into ReAct announce context. `memory.tools` controls search/read/write
tools. `memory.widget` enables user-owned CRUD in the Memory widget.
`memory.reconciliation` and `memory.snapshots` control maintenance jobs and
restore points. `ui.widgets.memories.enabled` exposes the built widget
route; the memory mixin supplies the default source folder/build command.
`ui.widgets.<alias>.shared_sources` is optional and materializes reusable
SDK UI source into that widget build workspace; this is useful for external-git
bundles that want to mount platform widgets as direct React components.

`config.surfaces.as_consumer` is the reserved consumer wiring section. It
controls which platform or bundle-provided surfaces this bundle consumes:
model-callable tools, named-service event-source policies, pull/materialization
policy, and UI resolvers.

Example:

```yaml
bundles:
  items:
    - id: "my.bundle@1-0"
      config:
        surfaces:
          as_consumer:
            default_agent: main
            agents:
              main:
                tools:
                  - id: web
                    kind: python
                    module: kdcube_ai_app.apps.chat.sdk.tools.web_tools
                    alias: web_tools
                    allowed: [web_search, web_fetch]
                    tool_traits:
                      web_search:
                        strategy: [exploration]
                      web_fetch:
                        strategy: [exploration]
                  - id: knowledge
                    kind: mcp
                    server_id: knowledge
                    alias: knowledge
                    allowed: ["*"]
                    tool_traits:
                      "*":
                        strategy: [exploration]
                  - id: task_service
                    kind: named_service
                    alias: named_services
                    namespaces:
                      task:
                        allowed:
                          - provider.about
                          - object.list
                          - object.search
                          - object.schema
                          - object.host_file
                          - object.upsert
                          - object.delete
                    tool_traits:
                      provider_about:
                        strategy: [exploration]
                      list_objects:
                        strategy: [exploration]
                      search_objects:
                        strategy: [exploration]
                      object_schema:
                        strategy: [exploration]
                      host_file:
                        strategy: [exploitation]
                      upsert_object:
                        strategy: [exploitation]
                      delete_object:
                        strategy: [exploitation]
                event_sources:
                  - kind: named_service
                    namespace: task
                    enabled: true
                    discovery:
                      mode: service_discovery
                    policies:
                      block_production:
                        mode: provider
                        operation: block.produce
                      pull:
                        mode: provider
                        operation: object.get
            ui:
              canvas:
                resolvers:
                  - kind: named_service
                    namespace: task
                    enabled: true
                    discovery:
                      mode: service_discovery
                    allowed: [object.resolve, object.action]
```

Rules:

- `surfaces.as_consumer.agents.<agent_id>.tools` is a list; each entry is one
  connected tool source for that agent.
- This is the consumer-side surface: it says what that bundle/agent may call or
  resolve. Provider registration remains separate and is currently discovered
  from provider code.
- `kind: python` loads a platform module or bundle-local `ref`; `allowed`
  names are Python callable names.
- `kind: mcp` connects a server declared under `mcp.services`; `allowed`
  names are MCP tool names, or `["*"]`.
- `tool_traits` is keyed by the callable names for that connection. The
  `strategy` trait is used by ReAct multi-action policy and may be
  `exploration`, `exploitation`, `neutral`, or omitted/`unknown`.
- `kind: named_service` exposes namespace operations through the generic
  `named_services.*` tool family. ReAct catalog entries render only
  `namespaces applicable`, so an agent can see which configured namespaces may
  use each generic tool without seeing provider protocol ids.
- UI object actions belong under
  `surfaces.as_consumer.ui.<surface>.resolvers`; the remote provider enforces
  what each resolver action may do.
- For existing external refs, prefer pull/read over a normal model-callable
  `object.get`. The event-source `pull` policy calls provider `object.get` and
  stores an `fi:` artifact with provider-selected MIME, so large JSON or bytes
  can be read in ranges.
- For agent-owned runtime files that must become provider-owned refs, allow
  `object.host_file` and use `named_services.host_file`; the returned provider
  ref can then be cited in a provider-declared `object.upsert` payload.

`config.tools.agents` is still recognized as a legacy fallback for older
bundles. New descriptors should use `config.surfaces.as_consumer`.

`config.events` overrides the platform event recording defaults for this bundle.
Platform defaults come from `assembly.yaml -> events.record.*`. Bundle-level
fields are merged on top of assembly defaults field-by-field: a bundle can
override only `enabled`, only `selector`, or both. The `selector` list is
replaced as a whole when present — lists are not concatenated.

```yaml
bundles:
  items:
    - id: "my.bundle@1-0"
      config:
        events:
          record:
            persist:
              enabled: true
              selector:
                - "accounting.usage"
                - "chat.turn.summary"
                - "chat.conversation.accepted"
            telemetry:
              enabled: true
              selector:
                - "accounting.usage"
```

| Key | Effect |
|---|---|
| `events.record.persist.enabled` | enables/disables `conv.artifacts.events` artifact for this bundle |
| `events.record.persist.selector` | event types saved into the artifact; replaces the assembly default list |
| `events.record.telemetry.enabled` | enables/disables telemetry sink flush for this bundle |
| `events.record.telemetry.selector` | event types shipped to the telemetry sink; replaces the assembly default list |

## Two supported bundle styles

### 1. Git bundles

Use:

- `repo`
- `ref`
- `subdir`
- `module`

This is the normal shape for:

- AWS deployment
- any non-local deployment
- local compose when you want the runtime to resolve bundles from git

Bundle-root example:

```yaml
bundles:
  version: "1"
  default_bundle_id: "acme.marketing@2-0"
  items:
    - id: "acme.marketing@2-0"
      repo: "git@github.com:example-org/acme-platform.git"
      ref: "main"
      subdir: "src/marketing_bundle"
      module: "entrypoint"
      config:
        features:
          news: true
```

Parent-subdir example:

```yaml
bundles:
  version: "1"
  default_bundle_id: "marketing-bundle@2-0"
  items:
    - id: "marketing-bundle@2-0"
      repo: "git@github.com:example-org/acme-platform.git"
      ref: "main"
      subdir: "src"
      module: "marketing-bundle@2-0.entrypoint"
      config:
        features:
          news: true
```

`module` is a Python import path, so dots are package separators. If the bundle
directory name contains literal dots, use the bundle-root shape unless the
filesystem layout intentionally mirrors the dotted package path.

### 2. Local path bundles

Use:

- `path`
- `module`

This is for local development only.

The correct `path` value depends on which descriptor copy is being consumed.

#### Local path bundles: exact rule

Seed/source descriptors under the repository descriptor set are often consumed
by both:

- CLI init/staging
- host-side processor runs, such as IntelliJ launches

For those seed/source descriptors, use the host-visible concrete bundle root:

```yaml
bundles:
  version: "1"
  default_bundle_id: "my.bundle@1-0"
  items:
    - id: "my.bundle@1-0"
      path: "/Users/you/src/my-repo/src/my_bundle"
      module: "entrypoint"
```

When the CLI stages descriptors into a Docker-backed runtime, the staged copy
under `workdir/config/` may be rewritten to the runtime-visible mount path:

```yaml
bundles:
  items:
    - id: "my.bundle@1-0"
      path: "/bundles/my-repo/src/my_bundle"
      module: "entrypoint"
```

Do not edit the seed/source descriptor to `/bundles/...` only because the
Docker runtime copy uses that shape. The seed descriptor may still need to run
from the host.

#### `path` and `module`: only two valid forms

There are exactly two supported shapes for local path bundles.

##### Preferred form: `path` is the bundle root

If `path` already points to the actual bundle directory, use:

```yaml
path: /Users/you/src/my-repo/src/my_bundle
module: entrypoint
```

This is the preferred form because it is explicit and does not depend on loader fallback behavior.

##### Secondary form: `path` is the parent directory

If `path` points to the parent directory that contains the bundle directory, use:

```yaml
path: /Users/you/src/my-repo/src
module: my_bundle.entrypoint
```

This works because bundle root resolution supports:

- `<path>/<module_base>`
- `<path>/<module_base as package path>`

Do not mix the two forms without a reason.

Avoid this hybrid form:

```yaml
path: /Users/you/src/my-repo/src/my_bundle
module: my_bundle.entrypoint
```

It may still load because of fallback behavior, but it is not the clear contract to rely on.

#### Local path bundle must stay a pure path bundle

If you switch a bundle to local `path:` mode, do not leave git fields on the same entry.

Do not mix:

- `path`
- `repo`
- `ref`
- `subdir`

on one bundle entry.

For a local path bundle, keep only:

- `id`
- `name` if needed
- `path`
- `module`
- `config`

## `bundles.yaml` by run mode

### CLI local compose

Authority:

- staged into `workdir/config/bundles.yaml`

Typical use:

- local path bundles while editing source code on the host
- or git bundles resolved into local cache

Important local settings:

- `assembly.paths.host_bundles_path`
- `assembly.paths.host_managed_bundles_path`

Important runtime rule:

- an initialized runtime reuses its existing `workdir/config/*.yaml`
- running `kdcube refresh --tenant <t> --project <p> --upstream --build` does
  not reseed default descriptors
- if you want different bundle roots or bundle entries in that runtime, edit `workdir/config/assembly.yaml` and `workdir/config/bundles.yaml` directly

For the step-by-step workflow, use:

- [how-to-configure-and-run-bundle-README.md](../../sdk/bundle/build/how-to-configure-and-run-bundle-README.md)

Reload workflow:

- edit local bundle code
- keep `bundles.yaml` stable
- run:

```bash
kdcube bundle reload <bundle_id> --workdir <runtime-workdir> --path <repo-root>
```

That reapplies the mounted descriptor and clears proc bundle caches.

### Direct local service run

Authority:

- the file pointed to by `BUNDLES_YAML_DESCRIPTOR_PATH`

Important distinction:

- `BUNDLES_YAML_DESCRIPTOR_PATH` controls plain descriptor reads and file-backed
  bundle descriptor authority
- proc can load/reset from that bundle descriptor authority directly

For direct proc debug, set at least:

```bash
BUNDLES_YAML_DESCRIPTOR_PATH=/abs/path/to/bundles.yaml
```

If you want startup env reset to rebuild the registry from the file, set:

```bash
BUNDLES_YAML_DESCRIPTOR_PATH=/abs/path/to/bundles.yaml
BUNDLES_FORCE_ENV_ON_STARTUP=1
```

### AWS deployment

Authority:

- in `aws-sm`, live deployment-scoped bundle descriptor authority is grouped
  AWS SM docs
- `bundles.yaml` is the deployment import/export format and runtime-readable
  snapshot

Supported operational model:

- bundles from git only
- do not use local `path:` bundles
- do not use local host bundle roots

## Local development rule

For local development with code edits on the host:

- define the bundle in `bundles.yaml` with `path: /bundles/...`
- set `assembly.paths.host_bundles_path` to the matching host root
- run KDCube via the CLI compose path
- use `kdcube bundle reload <bundle_id>` after code changes

That is the correct local dev contract.

For AWS deployment:

- define the bundle from git
- do not use `path`
- do not use `assembly.paths.host_bundles_path`
