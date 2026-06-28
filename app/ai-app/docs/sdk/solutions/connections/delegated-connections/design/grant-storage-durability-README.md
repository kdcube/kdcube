---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-connections/design/grant-storage-durability-README.md
title: "Grant Storage Durability"
summary: "Design note for which delegated client connection grant records may remain volatile and which records need durable storage for production-grade connectors."
status: design
tags: ["sdk", "solutions", "connections", "delegated-connections", "oauth", "mcp", "storage", "redis", "postgres", "durability"]
updated_at: 2026-06-27
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-connections/delegated-connections-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/service/auth/oauth-mcp-integration-access-README.md
---
# Grant Storage Durability

The current OAuth/MCP implementation of delegated client connections uses Redis
for grant state. That is safe in the sense that Redis loss fails closed: missing
grant state prevents token use rather than granting more access. It is not
always product-friendly, because long-lived external MCP connections can
disappear after Redis loss.

## Current OAuth/MCP Storage Shape

`GrantStore` stores tenant/project scoped keys:

```text
{tenant}:{project}:kdcube:oauth:code:<auth_code>
{tenant}:{project}:kdcube:oauth:csrf:<csrf_token>
{tenant}:{project}:kdcube:oauth:refresh:<refresh_token>
{tenant}:{project}:kdcube:oauth:client:<client_id>
{tenant}:{project}:kdcube:oauth:agrant:<sha256(access_token)>
```

The access token itself is also a platform bundle-session token. The OAuth
`agrant` record is not the token; it binds that token to the selected MCP tool
allowlist.

## Volatile Is Fine

These records can remain in Redis without persistence:

| Record | Why Redis is fine |
| --- | --- |
| `code` | Authorization codes are short-lived, single-use, and exchanged immediately. |
| `csrf` | Consent CSRF tokens are short-lived and single-use. |
| `agrant` | Access grants last only as long as the access token. Loss fails closed and the client can refresh. |

## Volatile Is Product-Risky

These records should not rely on purely volatile Redis for production:

| Record | Current behavior if lost | Product impact |
| --- | --- | --- |
| `refresh` | Refresh token becomes invalid. | External MCP connector loses its long-lived delegated connection and user must re-consent. |
| `client` | Dynamically registered client id no longer resolves. | Connector may need to reconnect or re-register. |

For demos and early deployments this is acceptable if users can reconnect. For a
production integration, losing Redis should not silently revoke every connector.

## Target Split

```text
Redis / cache
  code      short TTL, single-use
  csrf      short TTL, single-use
  agrant    access-token TTL; fail-closed cache

Durable store
  client    dynamic client registration metadata
  refresh   rotating refresh tokens + selected tools
  grant     durable consent record, if we later model connection management
```

Durable store can be Postgres or a platform persistent KV abstraction. The
important requirement is that it follows the same tenant/project scope as the
current Redis keys.

## Why Not Put Everything In Postgres

Short-lived records are high-churn and naturally cache-shaped. Keeping `code`,
`csrf`, and `agrant` in Redis keeps the hot path cheap and simple.

Long-lived records are user-visible connection state. They need backup,
inspection, revocation, and continuity across Redis restarts.

## Migration Direction

1. Keep the current `GrantStore` interface as the service boundary.
2. Split implementation into volatile and durable backends.
3. Move `client` and `refresh` to the durable backend.
4. Preserve selected-tool allowlist on refresh rotation.
5. Add an operator view/API later for active MCP connections and revocation.

The security rule remains unchanged: if any required record is missing or
invalid, the OAuth/MCP path fails closed.
