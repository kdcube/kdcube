---
id: repo:kdcube-ai-app/app/ai-app/docs/runtime/cross-runtime-context-README.md
title: "Cross-Runtime Context"
summary: "Reference for the portable context room that carries request identity, bundle call context, and platform descriptors across KDCube runtime boundaries."
tags: ["runtime", "context", "comm_ctx", "portable-spec", "subprocess", "iso-runtime", "namespace-services"]
keywords:
  [
    "cross runtime context",
    "portable context room",
    "comm_ctx",
    "REQUEST_CONTEXT",
    "BUNDLE_CALL_CONTEXT",
    "NAMED_SERVICE_DISCOVERY",
    "PORTABLE_SPEC_JSON",
    "ContextVar restore",
  ]
updated_at: 2026-06-26
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/subagents/subagents-runtime-bootstrap-and-reduce-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-runtime-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/exec/runtime-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/exec/README-iso-runtime.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/clients-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/comm-recording-event-sinks-README.md
---
# Cross-Runtime Context

The cross-runtime context is the platform-owned portable room that lets KDCube
preserve request identity and small runtime descriptors when execution crosses
async tasks, tools, subprocesses, Docker/Fargate supervisors, and peer bundle
calls.

It is not a generic object serializer. It is a compact set of JSON-safe fields
that the target runtime can use to rebuild trusted SDK surfaces.

## What Crosses

The current portable context is carried through `contextvars.comm_ctx` inside
`PORTABLE_SPEC_JSON`:

```text
comm_ctx
  REQUEST_CONTEXT
    ExternalEventPayload
      actor.tenant_id
      actor.project_id
      routing.bundle_id
      routing.session_id
      routing.conversation_id
      routing.turn_id
      user.user_type / user_id / roles / permissions
      request metadata
      bundle_call_context

  BUNDLE_ID
    current bundle id for bundle-scoped helpers

  BUNDLE_CALL_CONTEXT
    JSON-safe bundle-owned invocation metadata

  NAMED_SERVICE_DISCOVERY
    schema = kdcube.named_service.discovery.v1
    backend = redis
    tenant
    project

accounting
  context
    tenant_id / project_id / user_id / session_id
    conversation_id / turn_id / app_bundle_id / component
    agent_id
  enrichment
    metadata / seed_system_resources
```

The host builds the portable spec in:

```text
kdcube_ai_app/apps/chat/sdk/runtime/snapshot.py
```

Child runtimes restore it in:

```text
kdcube_ai_app/apps/chat/sdk/runtime/bootstrap.py
```

The context accessors live in:

```text
kdcube_ai_app/apps/chat/sdk/runtime/comm_ctx.py
```

## What Does Not Cross

These are not serialized:

- Redis client objects
- Postgres pools
- bundle entrypoint instances
- Python callbacks
- arbitrary provider/client objects
- non-JSON selector functions
- secrets
- large documents or binary payloads

The target runtime reconstructs trusted services from descriptor-backed runtime
configuration and the restored identity descriptor.

## ReAct Agent Identity

`agent_id` is part of the ReAct runtime context and the accounting context. The
host resolves it from the submitted event lane, stores it on `RuntimeCtx`, and
binds it into accounting before the ReAct run settles usage. When a runtime
boundary is crossed, `snapshot_ctxvars()` includes the accounting context and
`restore_ctxvars()` recreates it in the child runtime.

Consequences:

- stored accounting events can expose `agent_id` as a root context field;
- comm envelopes can expose the same id under `metadata.agent_id`;
- accounting role/model dimensions remain independent from `agent_id`.

For subagents, the host binds the subagent `agent_id` before it calls
`build_portable_spec(...)` and before it exports `COMM_SPEC`. The resulting
snapshot carries the subagent identity through comm metadata and accounting
context. A snapshot built before the subagent scope is bound represents the
host/coordinator identity.

## Actor Identity And Authority

Some executions start from a surface identity that is not the platform account
which owns roles or funding. For example, a Telegram-triggered scheduled
automation can execute as `telegram_434804821` for app storage and audit, while
the Telegram identity is linked to a platform user that owns the effective
roles and economics authority.

The conversion is done once at the execution boundary. After that, role checks
and economics checks must read the already-carried context; they should not ask
every surface how to reinterpret the actor.

The portable shape is:

```text
REQUEST_CONTEXT.user
  user_id       = telegram_434804821        # actor/storage identity
  user_type     = privileged                # resolved authority role
  roles         = ["kdcube:role:super-admin"]
  permissions   = [...]

BUNDLE_CALL_CONTEXT.identity_authority
  actor_user_id      = telegram_434804821
  storage_user_id    = telegram_434804821
  platform_user_id   = 02e53484-...
  economics_user_id  = 02e53484-...
  user_type          = privileged
  economics_user_type = privileged
  platform_roles     = ["kdcube:role:super-admin"]
  platform_permissions = [...]
  identity_provider  = telegram
  identity_provider_subject = 434804821

UserSession.identity_authority
  same authority envelope when the execution starts at the gateway selector
```

`REQUEST_CONTEXT.user` is what generic role validators see after context is
bound. `UserSession.identity_authority` preserves the same provenance at the
gateway/session boundary. `BUNDLE_CALL_CONTEXT.identity_authority` carries it
across subprocesses, isolated runtimes, and peer app calls.

Rules:

- actor identity remains the surface/app identity unless the product explicitly
  migrates storage scope;
- platform roles must come from the platform principal/authority resolver, not
  from a surface-local role such as a Telegram admin flag;
- economics may bill/check `economics_user_id` while app storage continues to
  use `actor_user_id`;
- the authority object must be JSON-safe because it travels in
  `bundle_call_context` through `PORTABLE_SPEC_JSON`;
- detached work must bind this context before invoking ReAct, tools, peer app
  calls, or isolated runtimes.

## Flow

```text
Host proc task
        |
        | bind_current_request_context(...)
        | bind_named_service_discovery(...)
        | update_current_bundle_call_context(...)
        v
+------------------------------------------+
| ContextVars in current task              |
| - REQUEST_CONTEXT_CV                     |
| - BUNDLE_ID_CV                           |
| - BUNDLE_CALL_CONTEXT_CV                 |
| - NAMED_SERVICE_DISCOVERY_CV             |
+------------------+-----------------------+
                   |
                   | build_portable_spec(...)
                   v
+------------------------------------------+
| PORTABLE_SPEC_JSON                       |
| contextvars.comm_ctx                     |
|   REQUEST_CONTEXT                        |
|   BUNDLE_ID                              |
|   BUNDLE_CALL_CONTEXT                    |
|   NAMED_SERVICE_DISCOVERY                |
+------------------+-----------------------+
                   |
                   | runtime bootstrap
                   v
+------------------------------------------+
| Target runtime                           |
| - restores comm_ctx ContextVars          |
| - rebuilds communicator from comm spec   |
| - rebuilds Redis clients from settings   |
| - rebuilds ModelService / ToolSubsystem  |
+------------------+-----------------------+
                   |
                   v
trusted bundle/tool code uses normal SDK accessors
```

## Accessors

| Context | Write/bind | Read |
| --- | --- | --- |
| request context | `bind_current_request_context(...)` | `get_current_request_context()` |
| bundle id | `bind_current_bundle_id(...)` or request bind | `get_current_bundle_id()` |
| bundle call context | `update_current_bundle_call_context(...)`, `bind_current_bundle_call_context_patch(...)` | `get_current_bundle_call_context()` |
| named-service discovery | `bind_named_service_discovery(...)` | `get_current_named_service_discovery()` |

Bundle code should normally write only `bundle_call_context`. Platform code
binds request context, bundle id, and named-service discovery.

## Named Service Discovery Context

Named Service Discovery is a provider lookup table scoped to tenant/project.
Provider bundles register records in Redis when they are loaded. Client bundles
configure the namespace and client policy, then resolve provider location at
call time.

The portable context carries only this descriptor:

```json
{
  "schema": "kdcube.named_service.discovery.v1",
  "backend": "redis",
  "tenant": "tenant-a",
  "project": "project-a"
}
```

In the target runtime, `get_current_named_service_discovery()` reconstructs a
`RedisNamedServiceDiscovery` using the runtime Redis configuration. It first
uses the explicit `NAMED_SERVICE_DISCOVERY` descriptor when present. If that
descriptor is absent but `REQUEST_CONTEXT` was restored, it uses
`REQUEST_CONTEXT.actor.tenant_id` and `REQUEST_CONTEXT.actor.project_id` as the
discovery scope. This is why named-service tools can discover providers after a
subprocess or ISO bootstrap without passing a live Redis object into the tool
registry.

Provider calls still run through a runtime bridge:

| Bridge | Use |
| --- | --- |
| `bundle_registry` | Same-KDCube direct registry call when the runtime has a platform-bound local named-service caller. |
| `bundle_operation` | Same-KDCube operation facade, useful when the provider exposes `@api(alias="named_service")` or when direct registry call is unavailable. |
| `module` | Same-runtime importable module provider. |
| MCP / Data Bus | Provider capability vocabulary; generic adapters are separate integration work. |

The call must preserve request identity. The provider authorizes through the
current `ExternalEventPayload` / `AuthContext`, not through a model-supplied
user id.

## Bundle Call Context

`bundle_call_context` is the bundle-owned part of the portable room. It is for
small request-scoped metadata that must follow nested tools and child runtimes.

Good values:

- ids
- execution modes
- selected agent strength
- correlation ids
- request-scoped policy snapshots
- `role_models` overlays for the current invocation

Bad values:

- secrets
- large documents
- binary payloads
- mutable process-local objects
- durable state that a later request must recover

If a later cron/job/request needs the value, store it in durable bundle state
and re-bind it when that later invocation starts.

## Runtime Guarantees By Boundary

| Boundary | Guaranteed |
| --- | --- |
| Same async task | Current ContextVars are visible during awaits. |
| In-process tool | Tool module sees the same restored request and bundle call context after SDK binding. |
| Local subprocess tool | `PORTABLE_SPEC_JSON` restores `run_ctx`, `comm_ctx`, and accounting; Redis/comm/model services are rebuilt from config. |
| Docker/Fargate supervisor | Same portable context restore, plus descriptor-backed settings/secrets for trusted supervisor tools. |
| Docker/Fargate executor | Minimal safe env and supervisor socket only; executor asks supervisor for approved tool calls. |
| Peer bundle local operation | Platform local caller builds a target request context for the target bundle and binds it around the call. |
| Data Bus handler | Handler receives `DataBusContext`; if it needs user context, the message actor/auth metadata must carry it. |
| Cron/job | Headless by default; use stored job auth/request metadata when a job must act on behalf of a user. |

## Design Rules

1. Keep the portable room JSON-safe and small.
2. Do not pass live infrastructure handles through tool registries.
3. Reconstruct runtime services from descriptor-backed settings.
4. Let platform code bind platform context; let bundle code write only
   bundle-owned call metadata.
5. Treat browser state, comm relay state, Data Bus state, and conversation lane
   state as separate surfaces.
6. Preserve user/session context for provider calls; do not let models provide
   identity as ordinary tool arguments.
