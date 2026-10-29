---
id: kdcube-services@1-0/docs/storage
title: "KDCube Services Storage Map"
summary: "Canonical storage and ownership map for kdcube-services@1-0: descriptor policy, signing secrets, conversation read-through storage, temporary integration staging, provider-owned files, generated UI output, and Redis coordination."
status: active
tags: ["kdcube-services", "storage", "secrets", "conversations", "staging", "mcp", "named-services"]
---

# KDCube Services Storage Map

`kdcube-services@1-0` is primarily a service facade. It reads several
platform/provider stores, but it does not become the owner of those records.
Keep storage ownership explicit:

```text
bundles.yaml
  app visibility, MCP managed-auth pointers, widget build config

bundles.secrets.yaml / configured bundle secret provider
  conversations.file_download_secret

tenant/project Postgres + conversation object storage
  read-through conversation summaries, turn records, artifacts and files

local integration staging
  short-lived, single-use inbound upload bytes

Google / mail / Slack provider storage
  source data fetched or changed on demand; not copied into app state

bundle storage
  generated widget build output and runtime build cache

Redis
  named-service discovery, Data Bus relay/idempotency and runtime coordination
```

## Ownership Matrix

| Object | Authority/owner | Storage used by this app | Read or write | Contains secrets? |
| --- | --- | --- | --- | ---: |
| App surface and visibility config | operator/admin | effective `bundles.yaml` app props | read | no |
| MCP delegated grant record | Connection Hub | Connection Hub delegated-credential storage | read through managed boundary | security-sensitive grants/tokens |
| File-link signing key | operator/admin | `bundles.secrets.yaml` or configured app secret provider | read | yes |
| Conversation index/search rows | conversation subsystem | tenant/project Postgres schema | read | no |
| Conversation turn artifacts and `conv:fi:` bytes | conversation subsystem | `ConversationStore` under configured `STORAGE_PATH` | read | may contain user content |
| Google Sheets data and metadata | Google account | Sheets and Drive APIs | read/write on demand | may contain user content |
| Mail and Slack attachment bytes | external provider/account | provider API | read on demand | may contain user content |
| Inbound integration upload | KDCube Services staging adapter | local `kdcube-integration-staging` directory | temporary write/delete | may contain user content |
| Widget build output | platform app loader | app-scoped bundle storage | generated write | no domain authority |
| Named-service discovery and relay state | platform runtime | Redis | ephemeral read/write | no provider credential values |

## Descriptor And Secret Boundary

Non-secret policy belongs in the `kdcube-services@1-0` app entry in
`bundles.yaml`:

```yaml
surfaces:
  as_provider:
    mcp:
      conversations:
        auth:
          mode: managed
          authority_id: delegated_client
      named_services:
        auth:
          mode: managed
          authority_id: delegated_client
```

The signing key belongs only in app secrets:

```yaml
bundles:
  items:
    - id: kdcube-services@1-0
      secrets:
        conversations:
          file_download_secret: "<random-secret>"
```

The app resolves this value through the bundle secret lifecycle at
`conversations.file_download_secret`. It signs all out-of-band conversation,
mail, Slack, Sheets-snapshot, and staged-upload URLs. The signed token binds the
exact object reference, platform user, tenant/project, optional conversation,
and expiry.
The public transfer routes trust the verified token, not caller-supplied
identity.

Tokens are stateless and short-lived. They are not persisted as app records.
Changing the key invalidates every outstanding link.

## Conversation Read-Through Storage

The `conversations` MCP resource and `conv` named-service provider use the
SDK-owned `ConversationReadService`. The app supplies its pooled Postgres
connection, model service, tenant/project scope, and `ConversationStore`:

```text
kdcube-services MCP / conv provider
  -> ConversationReadService
     -> ContextRAGClient / ConvIndex
        -> <tenant_project_schema>.conv_messages and conversation indexes
     -> ConversationStore(STORAGE_PATH)
        -> cb/tenants/{tenant}/projects/{project}/...
```

`ConversationStore` supports the configured storage backend, including local
`file://` and `s3://` roots. Typical object paths include:

```text
cb/tenants/{tenant}/projects/{project}/conversation/
  {user_id}/{conversation_id}/{turn_id}/{message_id}.json

cb/tenants/{tenant}/projects/{project}/attachments/
  {user_id}/{conversation_id}/{turn_id}/{filename}
```

This app is a scoped reader and materializer. It does not own conversation
retention, indexing, or write lifecycle. `conv_file_download` verifies its
signed token, then materializes the referenced `conv:fi:` bytes from the
conversation subsystem under the token's tenant/project/user/conversation
scope.

## Provider-Owned Integration Data

Google Sheets, Mail, and Slack objects remain provider-owned. Sheets operations
return values and metadata but do not copy spreadsheets into app state. When
an agent calls `react.pull` for a `sheets:` ref, this app streams a JSON
snapshot to the consumer's current turn workspace. That transient
materialization is owned by the harness/conversation lifecycle; it is not
retained as KDCube Services domain storage. A turnless MCP client can receive a
signed URL for the same complete snapshot. Mail message refs use the same
pattern for a complete normalized message snapshot; Mail attachment and Slack
file refs stream provider bytes. `integration_file_download` verifies the
signed token, resolves the current connected provider credential through
Connection Hub, fetches the exact message, attachment, file, or Sheets
snapshot, and streams it with `Cache-Control: private, no-store`.

The app does not persist downloaded provider bytes in its domain state.
Connection Hub owns delegated account credentials; this app must never copy
OAuth tokens or app passwords into descriptors, Redis, MCP results, or file
metadata.

## Temporary Inbound Upload Staging

An authorized `request_upload` action creates a slot for the current user,
tenant and project before returning a signed URL and `staged:` ref. The client
sends raw bytes to `integration_file_upload` and passes that ref to the later
consuming action:

```text
authorized MCP call requests upload slot
  -> signed URL + staged:<id>:<filename>
  -> raw HTTP upload (maximum 25 MiB)
  -> <staging-root>/<id>/<filename>
  -> later named-service action consumes the ref
  -> staged directory is deleted
```

The staging adapter uses:

```text
local STORAGE_PATH:
  <STORAGE_PATH>/kdcube-integration-staging

URI/non-local STORAGE_PATH:
  <system-temp>/kdcube-integration-staging
```

The SDK accepts the lowercase 32-character hexadecimal IDs it generates.
Every read, write, deletion and expiry sweep stays inside the resolved staging
root. Slots and their files use no-follow access; data writes replace a file
atomically inside the checked slot.

`request_upload` records `{user, tenant, project, created}` in the slot's
`.owner.json`, using authenticated context. The signed upload token contains
only `{fi_ref, exp, v}`. The public upload route verifies that capability and
writes only into an existing owned slot. Each authenticated consumer compares
the recorded user, tenant and project with its current context before loading
or deleting the file. A missing slot, missing owner record or different owner
gets the same not-found result. A slot created before owner records were
introduced must be replaced by a fresh `request_upload` call.

A provider-fetch URL remains a capability for the measured copy staged by the
authorized Docs action. Its token is the authorization for that server-side
read; Google supplies no platform identity. Staged refs are single-use after
successful consumption, unreadable after one hour, and collected by a
best-effort expiry sweep. This is temporary user content.

Current constraint: staging is filesystem-backed. The upload route and the
later consuming action must see the same staging directory. A multi-host
deployment with a non-local `STORAGE_PATH` needs a shared staging backend before
these calls can be distributed across hosts safely.

## Bundle Storage And Widget Browser

The platform app loader writes generated widget builds and build signatures to
the app's bundle-storage directory. Those files are disposable build output,
not service records.

The `bundle_storage` widget is also a browser over platform storage roots. It
does not mean this app owns every root it can display. Its backend APIs execute
in `chat-ingress`, so deployments must mount each permitted root into ingress,
commonly `/kdcube-storage`, `/bundle-storage`, and `/bundles`.

## Redis And Data Bus

Redis carries ephemeral coordination:

- named-service provider discovery;
- `kdcube.named_service.relay.v1` Data Bus request/reply and idempotency state;
- app registry/config caches and normal platform runtime coordination.

Redis is not the authority for conversation records, delegated credentials,
provider files, or the download-link signing key.

## Backup And Cleanup Consequences

| Surface | Backup expectation | Cleanup owner |
| --- | --- | --- |
| Descriptor config and app secrets | deployment configuration backup | operator/admin |
| Conversation Postgres/object storage | durable platform backup | conversation subsystem |
| Connection Hub delegated credentials | durable security-state backup | Connection Hub |
| Integration staging | do not back up | staging consumer + TTL sweep |
| Bundle-storage widget builds | rebuildable; backup not required | app loader cleanup |
| Redis relay/discovery state | ephemeral/reconstructable | platform runtime |
