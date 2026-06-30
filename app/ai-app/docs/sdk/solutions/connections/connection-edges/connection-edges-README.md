---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-edges/connection-edges-README.md
title: "Connection Edges"
summary: "Connection Hub role: one graph primitive for linked identities, delegation, authority projection, and identity-family resolution."
status: active
tags: ["sdk", "connections", "connection-hub", "connection-edges", "identity", "delegation", "authority"]
updated_at: 2026-06-29
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/connection-hub-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/identity-family-resolver/identity-family-resolver-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/authority-projection/authority-projection-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/delegated-connections/delegated-connections-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/connections/storage-model/storage-model-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/cross-runtime-context-README.md
---
# Connection Edges

Connection Hub stores one graph primitive:

```text
from authority identity
    -- relationship + grants + proof -->
to authority identity
```

A “linked identity” and a “delegation” are not separate stores. They are
different resolver views over the same edge graph.

```text
Identity family resolver:
  Which runtime user ids belong to this platform user?

Boundary authorization:
  Can this actor be represented in the authority required by this boundary,
  and which grants are delegated on that edge?
```

## Shape

```json
{
  "schema": "connection_hub.edge.v1",
  "edge_id": "edge_...",
  "relationship": "delegates_to",
  "from": {
    "authority_id": "telegram.kdcube_ref",
    "provider": "telegram",
    "subject": "434804821",
    "identity_ref": "telegram.kdcube_ref:434804821",
    "user_id": "telegram_434804821",
    "label": "elena_viter"
  },
  "to": {
    "authority_id": "platform",
    "provider": "platform",
    "subject": "02e53484-0081-70ce-11c1-e96706b1a182",
    "identity_ref": "platform:02e53484-0081-70ce-11c1-e96706b1a182",
    "user_id": "02e53484-0081-70ce-11c1-e96706b1a182",
    "label": "KDCube platform user"
  },
  "grants": [
    "identity:family",
    "economics:platform-user",
    "kdcube:role:chat-user"
  ],
  "constraints": {},
  "proof": {
    "challenge_id": "..."
  },
  "metadata": {
    "source": "telegram_miniapp"
  }
}
```

The edge does not store secrets. It also does not mint roles by itself. Its
`grants` field stores only what the target identity allowed this source identity
to derive. Roles, permissions, economics authority, and provider-account
capabilities still come from the authority that owns the target identity, then
Connection Hub intersects them with the edge grants.

For platform edges, common grants are:

| Grant | Meaning |
| --- | --- |
| `identity:family` | product reads may aggregate across the target platform user's connected runtime identities |
| `economics:platform-user` | economics may charge/check the target platform user while preserving the external actor as provenance |
| `kdcube:role:*` | the external actor may derive this platform role when a platform-authority boundary requires it |
| platform permission strings | the external actor may derive this platform permission when a platform-authority boundary requires it |

## Telegram Link Is A Delegation Edge

Telegram-first linking writes an edge:

```text
telegram.kdcube_ref:434804821
  -- selected grants -->
platform:02e53484-0081-70ce-11c1-e96706b1a182
```

That means:

- Telegram remains the actor identity for Telegram-originated work;
- platform remains the target authority for platform roles and economics;
- identity-family reads can include all runtime user ids connected by this edge
  only when the edge grants include `identity:family`;
- economics may use the platform user only when the edge grants include
  `economics:platform-user`;
- platform roles/permissions are projected only if they are both currently held
  by the platform identity and explicitly present in the edge grants;
- a boundary that requires platform authority must explicitly resolve this
  edge and carry the projection in the request context.

The Telegram Mini App does not silently become the browser platform session.
It proves Telegram identity, opens a provider-first claim, and the browser
claim proves the platform identity. Connection Hub writes the edge only when
both proofs meet.

The browser claim page must show an explicit consent step. Even if the browser
already has a valid KDCube platform session, the edge is not written until the
user selects delegated grants and confirms the link.

## Boundary Resolution

Each guarded boundary declares an authority and grants. The resolver starts
with the current actor identity and resolves only the edge needed for that
boundary.

```text
incoming actor:
  authority = telegram.kdcube_ref
  identity  = telegram.kdcube_ref:434804821

boundary:
  authority = platform
  grants    = economics:charge

Connection Hub:
  find edge telegram.kdcube_ref:434804821 -> platform:02e...
  resolve platform roles/permissions/economics for 02e...
  carry projection in cross-runtime context
```

Do not expand all possible identities preemptively. Resolve the next required
authority at the boundary that requires it, then carry that resolved edge and
projection forward.

## Current SDK Implementation

The SDK owns the edge store contract:

```text
kdcube_ai_app.apps.chat.sdk.solutions.connections.hub.edges.ConnectionEdgeStore
```

The example `connection-hub@1-0` bundle is a thin UI/API shell over that SDK
logic. Its current local development store is JSON-backed:

```text
<bundle_storage_root>/connections/connection-edges.json
<bundle_storage_root>/connections/connection-edge-challenges.json
```

Production storage can move to Postgres without changing the edge contract.

## Operations

The current public/operation API names are edge-oriented:

```text
connection_edges_list
connection_edge_upsert
connection_edge_remove
connection_edge_challenge_create
connection_edge_challenge_status
connection_edge_challenge_claim

telegram_connection_edge_start
telegram_connection_edge_status
telegram_connection_edge_remove
telegram_connection_edge_complete
```

The live update event is:

```text
connection_hub.edge.changed
```

## Delegated Client Edge Example

For an external client such as Claude, the edge is not a human identity link in
the same sense as Telegram, but it is still the same Connection Hub graph shape:

```text
from:
  authority_id = delegated_client
  identity     = integration:claude:<grantor-or-grant-id>

to:
  authority_id = platform
  identity     = 02e53484-...

resource:
  kdcube-services@1-0/public/mcp/named_services

selected grants:
  named_services:use
  memories:read
  tasks:read
```

The managed MCP guard first validates the delegated-client credential. The
generic `named_services` bridge then resolves the nested namespace boundary for
the requested namespace. For example, `named_services_search(namespace="mem")`
requires the credential edge to include both the selected generic tool and the
namespace grant `memories:read`.

The MCP server's icon, instructions, and `ToolAnnotations` are not part of the
edge. They are client-rendering hints. The edge remains the server-side
authority record used at enforcement time.
