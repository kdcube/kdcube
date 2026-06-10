---
id: ks:docs/service/maintenance/gateway-control-README.md
title: "Gateway Control Maintenance"
summary: "Operational notes for clearing gateway rate-limit, Data Bus publish-limit, and backpressure state without flushing Redis."
tags: ["service", "maintenance", "gateway", "redis", "rate-limit", "data-bus", "backpressure"]
keywords: ["gateway control", "rate limit reset", "data bus publish limit reset", "anonymous burst limit", "redis browser", "throttling reset"]
see_also:
  - ks:docs/service/maintenance/requests-monitoring-README.md
  - ks:docs/configuration/gateway-descriptor-README.md
  - ks:docs/service/scale/redis-cluster-README.md
---
# Gateway Control Maintenance

Use this when a development or operations incident leaves gateway throttling
state behind after a client bug, bad auth state, IDP switch, or burst test.
Prefer targeted key deletion over flushing Redis.

## Rate-Limit Key Shape

Gateway rate-limit keys are tenant/project scoped:

```text
<tenant>:<project>:kdcube:system:ratelimit:<session_id>:burst
<tenant>:<project>:kdcube:system:ratelimit:<session_id>:hour:<epoch-hour>
```

The burst key is a short-window sorted set. The hourly key is a counter. Both
are keyed by `session_id`, not by user type alone.

## Data Bus Publish Limit Key Shape

Socket.IO `data_bus.publish` uses a separate streaming policy. The keys are
tenant/project scoped and keyed by connected `session_id` plus counter window:

```text
<tenant>:<project>:kdcube:system:ratelimit:data-bus-publish:<session_id>:<window>:packages
<tenant>:<project>:kdcube:system:ratelimit:data-bus-publish:<session_id>:<window>:messages
<tenant>:<project>:kdcube:system:ratelimit:data-bus-publish:<session_id>:<window>:bytes
```

These counters protect a package path, not a request path:

- `packages` counts `data_bus.publish` Socket.IO emits
- `messages` counts durable Data Bus `messages[]`
- `bytes` counts the JSON package size accepted by ingress

Deleting these keys clears only Data Bus publish counters. It does not clear
generic gateway request throttling, backpressure state, durable Data Bus
streams, or chat conversation queues.

For prototyping, the app-layer Data Bus publish limiter can be disabled in
`gateway.yaml`:

```yaml
gateway:
  data_bus:
    ingress:
      publish_limits:
        registered:
          enabled: false
```

When disabled for a role, ingress does not create the Data Bus publish counter
keys for sessions resolved to that role. The Socket.IO protocol checks,
auth/session checks, bundle visibility checks, and OpenResty/proxy limits still
apply.

## Anonymous Users

Anonymous users are not all grouped into one shared limiter. The gateway first
maps the request to an anonymous session:

```text
<tenant>:<project>:kdcube:session:anonymous:<fingerprint>
```

The fingerprint is derived from client IP and user agent. In local development,
the same browser, IP, and user agent normally reuse the same anonymous session
until the session TTL expires or the session key is deleted. Multiple tabs and
reloads can therefore share the same anonymous burst budget.

Effect:

- One noisy anonymous browser can rate-limit itself across tabs/reloads.
- Other browsers or different user agents usually get different anonymous sessions.
- Deleting rate-limit keys does not clear stale browser cookies or local session state.
- After changing IDP/auth mode, also clear the browser cookies/session storage or use a fresh browser profile.

## Reset Anonymous Rate Limits

When a request is already rate-limited, inspect the response first. Gateway
429/503 responses include the resolved server session:

```json
{
  "detail": "Hourly limit exceeded (493/120)",
  "user_type": "anonymous",
  "session_id": "<session_id>"
}
```

The same value is also exposed as:

```text
X-Session-ID: <session_id>
X-User-Type: anonymous
```

If you know the `session_id`, delete only that session's rate-limit keys:

```text
<tenant>:<project>:kdcube:system:ratelimit:<session_id>:burst
<tenant>:<project>:kdcube:system:ratelimit:<session_id>:hour:*
```

In Redis Browser:

1. Open the Redis Browser admin UI.
2. Search with prefix:

```text
<tenant>:<project>:kdcube:system:ratelimit:<session_id>
```

3. Delete the `:burst` key.
4. Delete any matching `:hour:<epoch-hour>` keys if the hourly counter is also exhausted.

If you do not know the session id, search by tenant/project prefix:

```text
<tenant>:<project>:kdcube:system:ratelimit:
```

For local development only, it is usually acceptable to delete all matching
rate-limit keys for the tenant/project. Do not do this in shared environments
unless you intentionally want to clear throttling state for all active sessions.

## Reset Via Admin Endpoint

The control-plane reset endpoint can clear the same keys:

```http
POST /admin/throttling/reset
```

Single session:

```json
{
  "tenant": "<tenant>",
  "project": "<project>",
  "reset_rate_limits": true,
  "reset_backpressure": false,
  "reset_throttling_stats": false,
  "all_sessions": false,
  "session_id": "<session_id>"
}
```

All sessions for one tenant/project:

```json
{
  "tenant": "<tenant>",
  "project": "<project>",
  "reset_rate_limits": true,
  "reset_backpressure": false,
  "reset_throttling_stats": false,
  "all_sessions": true
}
```

Use an authenticated admin user. Keep `reset_backpressure` false unless you are
also recovering from stale capacity counters or queue pressure.

## Backpressure Reset

Backpressure capacity counters are separate from rate-limit counters. Reset
them only when requests are blocked with backpressure/503 symptoms and the
queue/capacity state is known to be stale.

Relevant keys:

```text
<tenant>:<project>:kdcube:system:capacity:counter
<tenant>:<project>:kdcube:system:capacity:counter:total
<tenant>:<project>:kdcube:system:capacity:process-index:<service_type>:<service_name>
```

The `process-index` key is a bounded sorted set maintained by process
heartbeats. Gateway admission reads it to compute healthy process count and
capacity before running the Lua admission script. Do not delete it during normal
rate-limit cleanup. Delete it only when process heartbeat keys are known stale
and the service has been restarted or will immediately repopulate the index.

Do not purge chat queues during normal rate-limit recovery. Queue purge drops
pending work and should be reserved for explicit incident recovery.
