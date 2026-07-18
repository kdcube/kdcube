---
id: repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/events/README.md
title: "Agent Harness Events"
summary: "Framework-neutral resolution of canonical event and object references."
tags: ["runtime", "harness", "events", "refs", "resolver", "artifacts"]
updated_at: 2026-07-18
keywords:
  [
    "event ref resolver",
    "object action",
    "conv:fi",
    "download",
    "byte resolution",
    "namespace owner",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/events/artifact-resolution-and-materialization-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/workspace/references-and-paths-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/event-subsystem-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/providers-README.md
---
# Agent Harness Events

The harness events scope resolves canonical event and object references without
depending on a particular agent loop.

```text
event/object ref
      |
      v
namespace owner + trusted runtime identity
      |
      +-- bytes for workspace materialization
      +-- action response for download/open/preview capabilities
      +-- explicit unsupported or denied result
```

This is not the Event Bus. The Event Bus orders and wakes work. The harness
events scope resolves the objects referenced by accepted events, timeline
blocks, canvas pins, chat files, or agent workspace operations.

## Built-In Resolution

The built-in byte-backed namespace is `conv:fi:`. Its resolver:

1. parses the embedded conversation, turn, artifact area, and relative path;
2. combines that locator with the runtime-bound tenant, project, and user;
3. resolves bytes in conversation storage;
4. returns metadata or a download URL, never a base64 file transport.

`resolve_event_ref_action(...)` currently advertises `download` for
`conv:fi:`. Unknown namespaces return an explicit unsupported result.

Other namespaces such as `mem:`, `task:`, and `cnv:` remain owner-controlled.
They must expose their own named-service operation or register a namespace
rehoster. Do not add namespace names or provider storage rules to the generic
resolver.

## Canonicalization

A durable cross-surface conversation file ref includes its conversation owner:

```text
conv:fi:conv_<conversation_id>.turn_<turn_id>.files/report.pdf
```

Conversation-local code may construct a ref before the owner segment is known.
`canonicalize_event_ref_for_context(...)` qualifies it when the current
conversation is available. Persisted, pinned, emitted, or cross-client refs
must use the qualified form.

## Authority Boundary

The ref is a locator, not a credential:

```text
untrusted ref
  + trusted tenant/project/user/authority
  + namespace-owner policy
  = permitted resolution or no bytes
```

The model cannot switch users, tenants, projects, or grants by changing the ref
string. A namespace owner must authorize its own objects under the carried
request identity.

## Shared Consumers

- chat and canvas object actions use the generic resolver for conversation
  files;
- ReAct `pull` uses the byte resolver underneath its model-facing tool;
- the ported LangGraph example uses the same resolver to populate a turn
  workspace;
- download and materialization therefore resolve the same canonical file ref.

See [Artifact Resolution And Materialization](artifact-resolution-and-materialization-README.md)
for owner namespaces and rehosters.
