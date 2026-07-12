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

mail / Slack provider storage
  source binaries fetched on demand; not copied into app state

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
mail, Slack, and staged-upload URLs. The signed token binds the exact object
reference, platform user, tenant/project, optional conversation, and expiry.
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

## Provider-Owned Integration Files

Mail and Slack objects remain provider-owned. `integration_file_download`
verifies the signed token, resolves the delegated provider credential through
Connection Hub, fetches the exact attachment/file from the provider, and
streams it with `Cache-Control: private, no-store`.

The app does not persist downloaded provider bytes in its domain state.
Connection Hub owns delegated account credentials; this app must never copy
OAuth tokens or app passwords into descriptors, Redis, MCP results, or file
metadata.

## Temporary Inbound Upload Staging

Named-service actions such as mail send or Slack upload can request an upload
slot. The client sends raw bytes to `integration_file_upload` and receives a
`staged:` ref:

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

Staged refs are single-use. A best-effort sweep removes directories older than
one hour. This is temporary user content, not durable app storage.

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
