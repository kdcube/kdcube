---
id: repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/workspace/references-and-paths-README.md
title: "Harness References And Workspace Paths"
summary: "Canonical grammar for conversation-owned refs, conversation owner segments, and distributed turn-workspace paths."
status: active
tags: ["runtime", "harness", "refs", "workspace", "namespaces", "security"]
updated_at: 2026-07-18
keywords:
  [
    "conv:fi",
    "conv:ar",
    "conv:tc",
    "conv:so",
    "conv:ev",
    "conv_<conversation_id>",
    "git/projects",
    "git/snapshots",
    "bound user scope",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/workspace/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/workspace/workspace-model-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/events/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/namespaces-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/react-object-materialization-README.md
---
# Harness References And Workspace Paths

This is the source of truth for conversation-owned logical refs and their
physical turn-workspace paths. The grammar is shared by agent adapters, chat,
canvas, conversation APIs, and integrations.

## Conversation Namespace

Conversation-owned refs use:

```text
conv:<family>:<body>
```

| Family | Meaning | Typical consumers |
| --- | --- | --- |
| `conv:fi:` | File/artifact bytes and workspace paths. | harness workspace, chat/canvas downloads, ReAct tools, ported agents |
| `conv:ar:` | Assistant, user, plan, and conversation replica records. | timeline readers and ReAct context |
| `conv:tc:` | Tool call, result, and notice records. | timeline readers and tool UI |
| `conv:so:` | Source-pool rows and source metadata. | citations and context readers |
| `conv:ws:` | Working summaries. | context adapters |
| `conv:su:` | Summary or search-summary records. | context adapters |
| `conv:ev:` | Accepted event occurrence/object on a timeline. | event/timeline readers |

External owner namespaces such as `mem:`, `task:`, and `cnv:` are not
conversation refs. Their providers own authorization and object semantics.

## Owner Segment

Every durable or cross-surface conversation ref includes the owning
conversation as the first body segment:

```text
conv:<family>:conv_<conversation_id>.<body>
```

`conv:` and `conv_<conversation_id>` have different jobs:

```text
conv:       outer namespace
conv_<id>   owner segment inside that namespace's ref body
```

Do not replace one with the other. The owner segment remains even when a ref is
copied into another conversation, pinned to canvas, sent through chat, or
consumed by a different agent adapter.

Canonical examples:

```text
conv:ar:conv_<conversation_id>.turn_<turn_id>.user.prompt
conv:tc:conv_<conversation_id>.turn_<turn_id>.tc_abcd.result
conv:ev:conv_<conversation_id>.turn_<turn_id>.events/chat/user-prompt/evt_1

conv:fi:conv_<conversation_id>.turn_<turn_id>.git/projects/site/index.html
conv:fi:conv_<conversation_id>.turn_<turn_id>.files/report.pdf
conv:fi:conv_<conversation_id>.turn_<turn_id>.git/snapshots/story/main.json
conv:fi:conv_<conversation_id>.turn_<turn_id>.user.attachments/input.xlsx
conv:fi:conv_<conversation_id>.turn_<turn_id>.external.followup.attachments/evt_1/file.docx
```

Conversation-local runtime code can temporarily operate on an unqualified ref
while the current conversation is carried separately. Before persistence or
cross-surface emission, qualify it with
`canonicalize_event_ref_for_context(...)` or
`qualify_conversation_ref(...)`. Documentation, descriptors, pins, and client
payloads should show only the canonical qualified form.

## Ref Is Locator, Not Authority

A model or client can propose a ref. It cannot use that string to choose its
tenant, project, user, role, grant, or storage root.

```text
requested ref
    +
runtime-bound tenant/project/user/authority
    +
namespace-owner policy
    |
    +-- permitted -> object/bytes
    +-- absent or denied -> no object/bytes
```

Conversation storage lookup combines the requested conversation/turn locator
with the bound runtime user. Git lineage remains rooted by
tenant/project/user/conversation. Owner namespaces authorize under the carried
request identity. Absolute paths and parent traversal fail path validation.

## Physical Turn Workspace

The workspace exposes `OUTPUT_DIR`-relative physical paths:

```text
OUTPUT_DIR/
  turn_<current>/
    git/projects/<project_scope>/...
    files/<artifact_scope>/...
    git/snapshots/<snapshot_scope>/...
    attachments/<name>
    external/<kind>/attachments/<event_id>/<name>
  conv_<other_conversation>/
    turn_<id>/...
```

`conv_<conversation_id>/turn_<id>/...` is the physical location for material
pulled from another conversation. It is not a logical namespace.

| Area | Meaning |
| --- | --- |
| `git/projects/` | Editable durable project/app state. |
| `files/` | Produced artifacts and deliverables. |
| `git/snapshots/` | Story, canvas, wizard, or workflow state snapshots. |
| `attachments/` | User uploads. |
| `external/` | Rehosted external-event or owner-domain evidence. |

## Logical And Physical Pairing

```text
logical
  conv:fi:conv_<conversation_id>.turn_<turn_id>.files/report.pdf

physical in its own active conversation
  turn_<turn_id>/files/report.pdf

physical after pull into another conversation
  conv_<conversation_id>/turn_<turn_id>/files/report.pdf
```

Logical refs are durable identities. Physical paths are worker-local
materializations.

## Shared APIs

`runtime.harness.workspace.references` owns:

- qualification/localization of conversation refs;
- logical and physical path builders;
- parsing of logical and physical artifact refs;
- conversation owner extraction;
- namespace inference;
- safe physical-path normalization;
- normalization of file payload filenames.

`runtime.harness.events.resolver` owns:

- canonicalizing `conv:fi:` refs before cross-surface use;
- resolving conversation-file bytes under bound identity;
- exposing generic object-action/download results.

Framework adapters should call these APIs instead of duplicating path parsing.

## Adapter Boundaries

The ReAct adapter exposes model tools over this grammar:

```text
react.pull      materialize historical or owner-domain refs
react.checkout  make historical git/projects state editable
react.read      load supported logical records into model context
react.rg        search already materialized local bytes
```

A ported agent can use the shared resolver and workspace APIs without exposing
those ReAct tool names.

## Checklist

- Emit `conv:<family>:conv_<conversation_id>...` for durable refs.
- Keep `conv_<conversation_id>` inside the ref body.
- Use `git/projects` for editable project state.
- Use `files` for produced artifacts.
- Use `git/snapshots` for state snapshots.
- Treat physical paths as `OUTPUT_DIR`-relative.
- Treat refs as locators and runtime identity as authority.
- Resolve owner namespaces through their provider/rehoster, never by guessing
  their storage layout.
