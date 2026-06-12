---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/auth-bundle-federated-README.md
title: "Bundle Federated Auth For Data Bus"
summary: "How a bundle-owned federated client, such as a Telegram WebApp, claims a short-lived scoped token and uses it to publish Data Bus messages without a platform browser login."
status: active
tags: ["sdk", "bundle", "auth", "federated", "telegram", "socketio", "data-bus"]
keywords:
  [
    "federated token claim",
    "telegram webapp data bus",
    "bundle public auth",
    "scoped data bus token",
    "socket.io federated token",
    "bundle owned identity",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/service/comm/data-bus-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-platform-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-client-communication-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-transports-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-runtime-README.md
---
# Bundle Federated Auth For Data Bus

Bundles can serve clients that have their own upstream identity but do not have
a platform browser session. A Telegram WebApp is the primary example: the
client has Telegram `initData`, while Data Bus requires a platform
`UserSession` on the Socket.IO connection.

The runtime supports a **federated token claim** flow for this case. The bundle
validates the upstream identity, then asks the platform to issue a short-lived
Data Bus token scoped to one tenant/project/bundle.

## Runtime Flow

```text
federated client
  -> POST bundle public federated_token_claim
bundle public API
  -> validates upstream identity and role
  -> calls issue_federated_data_bus_token(...)
platform temp federated IdP
  -> creates/refreshes UserSession
  -> registers a signed scoped token in Redis with TTL
client
  -> Socket.IO connect with auth.federated_token
Socket.IO ingress
  -> verifies token, scope, Redis record, and session
  -> saves UserSession into socket metadata
client
  -> socket.emit("data_bus.publish", package)
Data Bus ingress
  -> writes actor/reply metadata from the federated session
bundle Data Bus worker
  -> handles the message through @data_bus_handler(...)
```

The token is a Data Bus capability. It does not set browser cookies and does
not create a general platform login.

## Bundle Public Claim Endpoint

Expose the claim as a bundle-owned public API. Use `public_auth="bundle"` so
the bundle owns the upstream identity check.

```python
from kdcube_ai_app.auth.federated import issue_federated_data_bus_token
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.infra.plugin.bundle_loader import api


class MyEntrypoint:
    @api(
        method="POST",
        alias="federated_token_claim",
        route="public",
        public_auth="bundle",
    )
    async def federated_token_claim(
        self,
        request=None,
        init_data: str = "",
        **kwargs,
    ):
        del kwargs

        identity = await self.telegram_auth.resolve_identity(init_data=init_data)
        roles = ["kdcube:role:bundle-admin"] if identity.is_admin else []
        user_type = "privileged" if identity.is_admin else "registered"
        settings = get_settings()
        bundle_id = str(getattr(getattr(self.config, "ai_bundle_spec", None), "id", "") or "")

        grant = await issue_federated_data_bus_token(
            request=request,
            tenant=settings.TENANT,
            project=settings.PROJECT,
            bundle_id=bundle_id,
            provider="telegram",
            provider_subject=str(identity.telegram_user_id),
            user_id=identity.user_id,
            user_type=user_type,
            username=identity.username,
            roles=roles,
            allowed_subjects=[
                "example.task.create",
                "example.task.patch",
            ],
        )
        return {
            "schema": "kdcube.federated_token_claim.v1",
            "federated_token": grant.token,
            "session_id": grant.session.session_id,
            "expires_at": grant.expires_at,
            "user": {
                "user_id": grant.session.user_id,
                "user_type": grant.session.user_type.value,
                "username": grant.session.username,
                "roles": list(grant.session.roles or []),
            },
        }
```

The platform helper expects the bundle to validate upstream identity first. It
materializes the already-validated identity as a short-lived platform
`UserSession` and a Redis-registered scoped token.

## Client Socket.IO Connection

The client claims the token first, then uses it in the Socket.IO `auth`
payload. `tenant`, `project`, and `bundle_id` are required because they are
part of the token scope.

```ts
const claim = await fetch(federatedTokenClaimUrl, {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({
    init_data: window.Telegram.WebApp.initData,
  }),
}).then((res) => res.json());

const socket = io(platformBaseUrl, {
  path: "/socket.io",
  transports: ["websocket"],
  auth: {
    tenant,
    project,
    bundle_id: bundleId,
    federated_token: claim.federated_token,
  },
});

socket.emit(
  "data_bus.publish",
  {
    schema: "kdcube.data_bus.ingress.v1",
    bundle_id: bundleId,
    messages: [
      {
        subject: "example.document.patch",
        object_ref: "document-123",
        idempotency_key: "client-op-123",
        payload: { title: "Updated title" },
      },
    ],
  },
  (ack) => {
    console.log("data bus ack", ack);
  },
);
```

The Data Bus `actor` is built from the federated session and is available to
the bundle handler.

## Token Scope

The issued token carries and verifies:

| Claim | Meaning |
| --- | --- |
| `tenant` / `project` | Runtime scope. |
| `bundle_id` | Only this bundle can receive messages through the token. |
| `provider` | Upstream identity provider, for example `telegram`. |
| `provider_subject` | Stable upstream user subject. |
| `session_id` | Platform session materialized from the validated identity. |
| `user_id` / `username` / `email` | Federated user identity exposed to Data Bus actor metadata. |
| `user_type` | Runtime user type, usually `registered` or `privileged`. |
| `roles` / `permissions` | Scoped runtime roles and permissions selected by the bundle. |
| `allowed_transports` | Contains `data_bus`. |
| `allowed_subjects` | Bundle-selected subject allow-list enforced by Data Bus ingress when present. |
| `iat` / `exp` / `jti` | Issue time, expiry, and Redis registration id. |

Socket.IO verifies the signed token, the tenant/project/bundle scope, the
Redis registration record, and the backing session before it accepts the
connection.

## Runtime Secret

The token issuer and Socket.IO verifier use the same descriptor-backed service
secret:

```yaml
services:
  federated_token:
    secret: "<generated shared signing secret>"
```

The canonical lookup key is `services.federated_token.secret`. In the normal
local CLI runtime this value is generated when absent and persisted into
`secrets.yaml` through the same runtime secret path used by other service
secrets. In managed deployments the same key is materialized by the deployment
secret provisioning flow and then read by components through the configured
secrets provider.

Do not reuse service-local secrets-read tokens for this purpose. Those tokens
can intentionally differ between ingress and proc, while federated Data Bus
tokens are issued in one component and verified in another.

This secret is only for short-lived Data Bus capability tokens. Browser login
sessions for bundle-owned sign-in flows use `services.session_token.secret` and
the provider described in
[Bundle Session Auth](../../service/auth/bundle-session-auth-README.md).

## Handler Authorization

Ingress verifies that:

- the token is valid for the requested tenant/project/bundle;
- the subject is inside `allowed_subjects` when the token declares a subject
  allow-list;
- the target bundle is enabled and visible to the session roles;
- each published subject is registered by `@data_bus_handler(...)`;
- each subject is visible to the session user type and roles.

The handler still owns domain authorization. For example, a document handler
should check that the federated actor may mutate the selected document.

```python
@data_bus_handler(
    subject="example.document.patch",
    partition_by="object_ref",
    user_types=("registered",),
)
async def handle_document_patch(self, message, ctx):
    actor = message.actor
    if not await self.documents.can_edit(actor["user_id"], message.object_ref):
        return await ctx.reply.error("not_allowed")
    ...
```

## Files And Bytes

Data Bus messages are JSON packages. They should carry metadata and storage
refs, not raw file bytes.

Use this pattern for files:

1. Upload bytes through a bundle public operation that uses the same upstream
   identity validation or an already-issued federated token.
2. Store the bytes in bundle-controlled storage.
3. Return a stable ref.
4. Include that ref in the Data Bus message payload.

```json
{
  "subject": "example.attachment.added",
  "object_ref": "document-123",
  "payload": {
    "attachments": [
      {
        "name": "photo.png",
        "mime": "image/png",
        "size": 18433,
        "storage_ref": "uploaded-file-789"
      }
    ]
  }
}
```

This keeps Redis Streams bounded and lets handlers fetch bytes only when they
need them.
