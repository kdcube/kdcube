---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/operations/move-authority-to-postgresql-README.md
title: "Move Runtime Authority To PostgreSQL"
summary: "Dry-run, review, quiesce, activate, and verify a PostgreSQL authority generation while preserving complete live Card credential chains and keeping Redis as a fenced session projection."
status: active
tags: ["operations", "authentication", "connection-hub", "postgresql", "redis", "migration"]
keywords: ["authority cutover", "authority generation", "kdcube authority preview", "kdcube authority apply", "PostgreSQL sessions", "Redis session projection", "Card credential chain"]
updated_at: 2026-09-23
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/operations/operate-runtime-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/assembly-descriptor-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/configuration/bundles-descriptor-README.md
  - https://github.com/elenaviter/app-ecosystem/blob/main/docs/connection-hub/package/durable-authority-generations.md
---
# Move Runtime Authority To PostgreSQL

Use this procedure once for a runtime whose session and Connection Hub
authority currently comes from Redis. It creates a reviewed PostgreSQL
generation and then makes that generation permanent in descriptors.

The cutover preserves every complete, active, unexpired Card credential chain,
so hosted workers and external OAuth/MCP clients keep valid credentials. It
intentionally starts other reconstructable state clean:

- users sign in once after restart;
- current user identity, roles, permissions, and authority versions rebuild on
  login;
- OAuth records not owned by a preserved Card chain authorize or register again;
- descriptor-declared public OAuth clients are recreated from `bundles.yaml`;
- replay claims and consumed one-time records begin in the new generation;
- conversations, app files, identity-provider accounts, and durable Card
  documents are unchanged.

PostgreSQL is authoritative after activation. Redis keeps generation-scoped
session projections with native TTL. A missing or stale projection rebuilds
from PostgreSQL, and every positive projection hit is checked against a compact
PostgreSQL revision/state fence. Restoring an old Redis snapshot therefore
cannot revive a logged-out, invalidated, deleted, or expired session.

The operation never deletes Redis. Keep the old Redis data until verification
is complete, then remove it as a separate maintenance decision.

## 1. Keep the source backend selected

Before preview and apply, both authority descriptors select the Redis source
and carry no `generation_id`:

```yaml
# assembly.yaml
auth:
  sessions:
    authority:
      backend: redis-migration-source
```

```yaml
# bundles.yaml, connection-hub@1-0 config
connections:
  delegated_credentials:
    authority:
      backend: redis-migration-source
```

Run this procedure with the runtime version that contains the `kdcube
authority` command and the corresponding Connection Hub package. A source
refresh stages code; it does not change the descriptors above.

Shipped and generated descriptors use this source mode by default. A fresh
runtime therefore starts without claiming a PostgreSQL generation that has no
activation receipt. Rebuilding an existing runtime before cutover continues to
serve the Redis source; only step 4 changes the authority backend.

## 2. Create and inspect the preview

Choose a stable generation identifier. It remains in both descriptors after
cutover and changes only for a future authority epoch.

```shell
WORKDIR="$HOME/.kdcube/kdcube-runtime/${TENANT}__${PROJECT}"
GENERATION="authority-2026-09-23"
PREVIEW="$HOME/.kdcube/authority-${TENANT}-${PROJECT}.json"

kdcube authority preview \
  --workdir "$WORKDIR" \
  --generation-id "$GENERATION" \
  --preview-file "$PREVIEW"
```

The artifact is safe to review: it contains aggregate counts and hashes, not
tokens, token-bearing keys, record payloads, or user identities. Check:

- `blockers` is empty;
- `source_summary.preserved.card_handles` is the expected count of all live
  Cards that must remain connected;
- the preserved OAuth access, refresh, and client counts account for each
  hosted worker and external OAuth/MCP client that needs continuity;
- every `source_summary.reset` count is understood;
- `source_counts` names every authority family, including families whose count
  is zero;
- `preview_sha256` is present.

## 3. Stop writers and apply exactly that preview

```shell
PREVIEW_SHA256="$(jq -r '.preview_sha256' "$PREVIEW")"

kdcube authority apply \
  --workdir "$WORKDIR" \
  --preview-file "$PREVIEW" \
  --confirm-preview-sha256 "$PREVIEW_SHA256" \
  --stop-writers
```

`--stop-writers` explicitly stops `chat-ingress` and `chat-proc`, verifies they
are stopped, and leaves them stopped. PostgreSQL, Redis, and the secret service
remain available to the one-off operation.

Apply reads the source again after quiescence. If any count or preserved record
changed since preview, it refuses without activating the generation. Leave the
writers stopped, create a new preview at a new path, inspect its new hash, and
apply that artifact.

A blocker named `live_card_access_binding_missing`,
`live_card_oauth_chain_missing`, or `live_card_oauth_client_missing` means a
Card appears live but its credential chain is incomplete. Repair or explicitly
expire that Card before creating a new preview; apply never silently disconnects
it.

An interrupted apply is rerunnable. Imports refuse conflicting target content,
the activation receipt is written last, and an exact rerun after activation
returns the receipt without reading Redis.

## 4. Select the activated generation

Only after apply returns its activation receipt, update the staged descriptors:

```yaml
# assembly.yaml
auth:
  sessions:
    authority:
      backend: postgresql
      generation_id: authority-2026-09-23
```

```yaml
# bundles.yaml, connection-hub@1-0 config
connections:
  delegated_credentials:
    authority:
      backend: postgresql
      generation_id: authority-2026-09-23
```

The two values must equal the receipt's `generation_id`. Restart from the
staged descriptors:

```shell
kdcube refresh --workdir "$WORKDIR"
```

Startup fails closed if the receipt is absent, the generation differs, or the
receipt omits an authority family.

## 5. Verify the result

Verify behavior through the real transports:

1. a hosted worker or relay performs one governed operation without receiving
   a new credential;
2. an active external OAuth/MCP client performs one operation and refreshes
   without reauthorization;
3. a browser user signs in again and receives current roles and permissions;
4. restoring an old Redis snapshot does not change a PostgreSQL-backed
   authority decision;
5. restarting the runtime retains the same descriptor generation and receipt.

The preview's operator-facing survival matrix is:

| Caller state before cutover | Result after activation |
| --- | --- |
| Active hosted worker or relay with a complete Card chain | Keeps its current credential. |
| Active external OAuth/MCP client with a complete Card chain | Keeps its access/refresh/client chain. |
| Pending agent with no issued credential | Remains pending and authorizes when activated. |
| Expired Card credential | Stays expired. The durable Card and grants remain; renewal or a new grant issues a new credential. |
| Browser/platform session | User signs in again; current identity, roles, permissions, and authority version rebuild through login. |

After activation, Redis is not a rollback authority. Do not point the runtime
back at the retired Redis source after new PostgreSQL authority has been
created.
