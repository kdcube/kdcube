---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/discovery-README.md
title: "Namespace Services: Discovery Registry"
summary: "The per-tenant/project, Redis-backed discovery table where named-service providers announce themselves so consumers across processes can find them: the discovery record, Redis key schema, the registration write path, and the rule that reads always go through the discovery module."
status: design
tags: ["sdk", "namespace-services", "discovery", "registry", "redis", "named-service-provider", "providers", "namespaces", "cross-process"]
updated_at: 2026-06-26
keywords:
  [
    "named service discovery",
    "RedisNamedServiceDiscovery",
    "discovery registry",
    "discovery entry",
    "provider record",
    "redis key schema",
    "register_provider",
    "register_registry",
    "service discovery read path",
    "namespace roster intros",
    "cross-process provider table",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/providers-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/clients-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/cross-runtime-context-README.md
---
# Namespace Services: Discovery Registry

The discovery registry is a per-tenant/project, Redis-backed table where
named-service providers announce themselves. Consumers — agents, named-service
clients, canvas/chat resolvers, and the ReAct namespace roster — read this table
to find which provider owns a namespace, **across processes**.

The in-process `NamedServiceRegistry` (`registry.py`) is process-local: it holds
the live provider objects loaded in one runtime and is the fastest local
dispatch path. The discovery registry is the shared source of truth a consumer
uses when the provider may live in another bundle, another process, or behind a
transport. A provider bundle publishes its registry into discovery on load; any
consumer with the same tenant/project then resolves the provider from Redis.

The implementation is `RedisNamedServiceDiscovery` in
`kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.discovery`. One
instance is scoped to one `(tenant, project)` pair.

```python
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    RedisNamedServiceDiscovery,
)

discovery = RedisNamedServiceDiscovery(redis, tenant=tenant, project=project)
```

## The Discovery Record Per Provider

Each provider stored in discovery is a `NamedServiceDiscoveryEntry`: a provider
`spec` plus a transport/retention envelope. The spec is a
`NamedServiceProviderSpec`; its fields (from `to_dict()` / `from_dict()`) are:

| Field | Meaning |
| --- | --- |
| `provider_id` | Provider registry identifier, for example `task.issue`. |
| `bundle_id` | Owning bundle id, for example `task-tracker@1-0`. |
| `namespace` | Primary base namespace (first of `namespaces`). |
| `namespaces` | Sorted base namespaces the provider owns/operates on. |
| `refs` | Owned ref patterns, for example `task:issue:*`. |
| `object_kinds` | Concrete object kinds the provider exposes. |
| `search_scopes` | Bounded searchable scopes (`namespace`, `label`, `object_kind`, `description`, `filters_schema`). |
| `operations` | Operation → `{transports}` capability map. |
| `label` | Short human label. |
| `description` | Human description. |
| `intro` | Roster intro string for the namespace (see the read path below). |
| `metadata` | Provider metadata; `metadata.priority` participates in ranking. |

Compact reference for one entry:

```text
provider_id · bundle_id · namespace(s) · label · description ·
search_scopes:[…] · operations:[…] · refs:[…] · object_kinds:[…] · intro:"…"
```

The `NamedServiceDiscoveryEntry` envelope wraps the spec:

| Entry field | Meaning |
| --- | --- |
| `spec` | The `NamedServiceProviderSpec` above. |
| `endpoint` | Transport/routing payload: `transport`, `bundle_id`, `provider`, `registry_method`. |
| `registered_at` | `time.time()` at registration. |
| `expires_at` | Absolute expiry (`registered_at + ttl`), or `0.0` when persistent. |
| `schema` | `NAMED_SERVICE_DISCOVERY_SCHEMA` = `"kdcube.named_service.discovery.v1"`. |

The `endpoint` written by `register_provider` defaults to
`transport="bundle_registry"` and `registry_method="named_services"`, so a
consumer that resolves the entry knows to call the owner bundle's
`named_services()` registry method (callers may pass extra endpoint fields).

## Redis Key Schema

Every key is namespaced by tenant and project. The base prefix is:

```text
kdcube:named_services:{tenant}:{project}
```

(`tenant`/`project` are sanitized by `_key_part`, which keeps alphanumerics and
`- _ . @ :`.) From that base:

| Key | Type | Contents |
| --- | --- | --- |
| `kdcube:named_services:{tenant}:{project}:provider:{bundle_id}::{provider_id}` | string | JSON of one `NamedServiceDiscoveryEntry`. |
| `kdcube:named_services:{tenant}:{project}:providers` | set | All provider keys for this tenant/project. |
| `kdcube:named_services:{tenant}:{project}:namespace:{ns}` | set | Provider keys that own namespace `{ns}`. |

The per-provider uid is `{bundle_id}::{provider_id}` (`_provider_uid`), so two
bundles registering the same `provider_id` get distinct keys. The `:providers`
set is the all-providers index; one `:namespace:{ns}` set is written per base
namespace in `spec.namespaces`.

### TTL And Expiry

TTL is `ttl_seconds`, defaulting to `DEFAULT_DISCOVERY_TTL_SECONDS = 0`
(persistent). When `ttl > 0`:

- the per-provider key is written with `ex=ttl` (`expires_at = now + ttl`);
- the `:providers` and `:namespace:{ns}` index sets are given `expire(ttl * 2)`,
  so the index outlives the records it points at.

When `ttl == 0`, records and index sets are persistent: `register_provider`
calls `redis.persist(...)` on the index sets (when the client supports it) so a
previously-expiring set does not silently drop. Expired or missing per-provider
records are tolerated on read: `providers()` skips index keys whose record no
longer resolves and logs a `missing_records` count.

## Registration (Write Path)

A provider bundle publishes its providers into discovery when the bundle is
loaded and its local prerequisites (storage, indexes) are ready — typically in
`on_bundle_load`. Two methods write:

- `register_registry(registry, *, bundle_id, transport="bundle_registry",
  registry_method="named_services", ttl_seconds=None)` — iterates
  `registry.providers()` and registers each provider's spec.
- `register_provider(spec, *, bundle_id, transport="bundle_registry",
  registry_method="named_services", endpoint=None, ttl_seconds=None)` —
  registers one spec. Requires a resolvable `bundle_id` (from the argument or
  `spec.bundle_id`), else it raises `ValueError`.

`register_provider` stores the **full** spec — including `intro`, `search_scopes`,
`operations`, `refs`, and `object_kinds` — as the JSON entry under the
per-provider key, then adds that key to the `:providers` set and to each
`:namespace:{ns}` set. It returns the stored `NamedServiceDiscoveryEntry` and
logs a structured registration line (scope, provider, bundle, namespaces,
endpoint, retention, refs, object_kinds, search_scopes, operations).

```python
discovery = RedisNamedServiceDiscovery(redis, tenant=tenant, project=project)
await discovery.register_registry(
    self.named_services(),
    bundle_id="task-tracker@1-0",
    transport="bundle_registry",
    registry_method="named_services",
)
```

The discovery scope itself is portable runtime context. `bind_named_service_discovery(...)`
binds an instance to a `ContextVar` and publishes a JSON-safe
`{schema, backend, tenant, project}` descriptor through `comm_ctx`;
`get_current_named_service_discovery()` restores a `RedisNamedServiceDiscovery`
from that descriptor (or the current request actor) without passing a live Redis
client through tool registries. See
[Cross-Runtime Context](../../runtime/cross-runtime-context-README.md).

## Reading: Go Through The Discovery Module

**The discovery module is the single entry point for reads.** Consumers resolve
providers through its read methods; they do not SCAN raw Redis keys and do not
treat the process-local `NamedServiceRegistry` as the authoritative provider
table. The process-local registry only knows providers loaded in the current
runtime; the discovery table is the cross-process source of truth. Reading
through the module keeps record decoding, missing-record tolerance, namespace
scoping, and ranking in one place.

`RedisNamedServiceDiscovery` exposes these reads:

| Method | Returns | Use |
| --- | --- | --- |
| `providers(*, namespace="")` | `list[NamedServiceDiscoveryEntry]` | Read all entries, or only those in one base namespace. |
| `entries_for_namespace(namespace)` | `list[NamedServiceDiscoveryEntry]` | Live provider entries that own one base namespace (normalized). |
| `list_entries()` | `list[NamedServiceDiscoveryEntry]` | Every entry in the tenant/project table. |
| `namespace_intros(namespaces=None)` | `dict[str, dict[str, str]]` | `{base_namespace: {"intro", "label"}}` for the ReAct namespace roster. |
| `resolve(request, *, namespace="", provider_id="")` | `NamedServiceDiscoveryEntry \| None` | Select the best provider for a `NamedServiceRequest` by namespace, `provider_id`, operation support, ref match, and object kind, ranked. |

`providers()` reads keys from the `:namespace:{ns}` set (when a namespace is
given) or the `:providers` set, loads each record, and drops unreadable/missing
ones. `resolve()` filters candidates by `provider_id`, `supports_operation`,
`matches_ref`, and `object_kinds`, then ranks by ref match score, object-kind
match, namespace match plus `metadata.priority`, and `provider_id`.

`namespace_intros(...)` is the canonical roster read. It pulls entries via
`entries_for_namespace` (or `list_entries` when no namespaces are passed) and
maps each provider's `spec.intro` (with `spec.label` fallback) to **every** base
namespace it owns through the shared `intros_from_entries(...)` helper — reading
only the existing `:providers` / `:namespace:{ns}` sets, no raw key scan. The
module-level convenience `fetch_namespace_intros(discovery, namespaces=None)`
delegates to whatever discovery object's `namespace_intros` reader is given
(and returns `{}` when `discovery is None`), so it works against both
`RedisNamedServiceDiscovery` and the in-memory `ConfiguredNamedServiceDiscovery`.
Reading the process-local registry instead of discovery here is the failure mode
the rule prevents: the roster would render only providers loaded in the current
process, missing cross-process providers.

`ConfiguredNamedServiceDiscovery` is the in-memory discovery view for explicit
provider config when Redis discovery is not available. It exposes the same
`resolve(...)` read contract, so consumers depend on the discovery surface, not
on the backend.

## Cross-Links

- [Providers](providers-README.md) — how a provider and its
  `NamedServiceProviderSpec` are defined.
- [Clients](clients-README.md) — how consumers call the resolved provider.
