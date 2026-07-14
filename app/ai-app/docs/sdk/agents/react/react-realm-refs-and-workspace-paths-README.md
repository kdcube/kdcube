---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-realm-refs-and-workspace-paths-README.md
title: "ReAct Realm Refs And Workspace Paths"
summary: "Authoritative grammar for conversation-owned ReAct refs, conv_<conversation_id> body segments, current per-turn workspace paths, and pull/read/checkout boundaries."
status: active
tags: ["sdk", "agents", "react", "refs", "workspace", "namespaces", "events"]
updated_at: 2026-07-14
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
    "react.read",
    "react.pull",
    "react.checkout",
    "ref authorization",
    "bound user scope",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/workspace/workspace-model-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/namespaces-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/event-ingress-to-react-turn-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/react-object-materialization-README.md
---
# ReAct Realm Refs And Workspace Paths

This is the source of truth for the model-facing ReAct ref grammar.

Conversation-owned ReAct refs use the outer owner namespace `conv:`:

```text
conv:<family>:<body>
```

`conv:` says the ref belongs to the ReAct conversation realm. The family after
it says which resolver owns the body:

| Ref family | Meaning | Common tool |
| --- | --- | --- |
| `conv:fi:` | ReAct file/artifact bytes and materialized workspace paths | `react.read`, `react.pull`, `react.checkout` for project refs |
| `conv:ar:` | Assistant/user/plan/conversation replica records | `react.read` |
| `conv:tc:` | Tool call/result/notice records | `react.read` |
| `conv:so:` | Source-pool rows and source metadata | `react.read` / citation support |
| `conv:ws:` | Working summaries | `react.read` |
| `conv:su:` | Summary or search-summary records | `react.read` |
| `conv:ev:` | Accepted event occurrence/object on a turn timeline | `react.read` |

External owner namespaces are separate. `mem:`, `task:`, and `cnv:` are not
conversation-owned ReAct refs. When ReAct needs exact content from them, it
uses `react.pull(paths=["mem:..."])`, `react.pull(paths=["task:..."])`, or
`react.pull(paths=["cnv:..."])`; the connected owner rehoster returns
`conv:fi:` rows for the current turn.

## Grammar

```text
conversation-owned ref:
  conv:<family>:<body>

same-conversation body:
  turn_<turn_id>.<record-or-path>

cross-conversation body:
  conv_<conversation_id>.turn_<turn_id>.<record-or-path>
```

Important distinction:

```text
conv:      outer owner namespace; always first for conversation-owned ReAct refs
conv_<id>  body segment that selects another conversation; keep it where it is
```

`conv_<id>` is not a namespace. It is a body segment inside the selected
`conv:<family>:` resolver. Do not remove it and do not replace it with `conv:`.

Examples:

```text
conv:ar:turn_2026-07-04-09-00-00-000.user.prompt
conv:tc:turn_2026-07-04-09-00-00-000.tc_abcd.result
conv:so:sources_pool[1-3]
conv:ev:turn_2026-07-04-09-00-00-000.events/chat/user-prompt/evt_1

conv:fi:turn_2026-07-04-09-00-00-000.git/projects/site/index.html
conv:fi:turn_2026-07-04-09-00-00-000.files/report.pdf
conv:fi:turn_2026-07-04-09-00-00-000.git/snapshots/story/main.json
conv:fi:turn_2026-07-04-09-00-00-000.user.attachments/input.xlsx
conv:fi:turn_2026-07-04-09-00-00-000.external/followup/attachments/evt_1/file.docx

conv:fi:conv_123.turn_2026-07-04-09-00-00-000.files/report.pdf
conv:ar:conv_123.turn_2026-07-04-09-00-00-000.assistant.completion
```

Conversation-owned family prefixes are not standalone model-facing
namespaces. Runtime code, instructions, docs, descriptors, UI styling, and
tests should use the `conv:<family>:` form.

## A Ref Is A Locator, Not Authority

ReAct can propose any ref string as a tool argument. Treat that string as
untrusted input. Tenant, project, actor, user, and authority come from the
runtime context established before the model runs.

The resolver owns access:

```text
ReAct proposes ref
  conv:fi:conv_<conversation>.turn_<turn>.files/<path>
       |
       v
trusted resolver combines locator with RuntimeCtx scope
  tenant + project + bound user + current authority
       |
       +-- resolves in scope -> copy bytes into current workspace
       +-- no in-scope object -> missing/denied result, no copied bytes
```

For conversation refs, the ref may select a conversation and turn. The user
used by historical context lookup remains `RuntimeCtx.user_id`; the query is
keyed by that user plus conversation and turn. Inventing another user's
conversation id therefore does not select that other user.

For git-backed project refs, runtime lineage is rooted by
`tenant/project/user_id/conversation_id`. Changing the conversation segment in
a ref preserves the bound tenant, project, and user. Absolute paths and parent
traversal fail safe-path validation.

External owner refs such as `mem:`, `task:`, and `cnv:` cross a registered
trusted rehoster. The owner service authorizes the ref under the carried
request identity before returning bytes. A custom rehoster is trusted app code
and must enforce its owner contract.

A compromised ReAct model can change which refs and tools it requests. It
cannot use a ref to rewrite runtime identity, grants, or storage roots.

## Physical Workspace

Each turn gets a sparse local workspace under `OUTPUT_DIR`.

```text
OUTPUT_DIR/
  turn_<current>/
    git/projects/<project_scope>/...     # editable durable project state
    files/<artifact_scope>/...           # produced files and deliverables
    git/snapshots/<snapshot_scope>/...    # story/canvas/wizard state snapshots
    attachments/...                      # user uploads for this turn
    external/<kind>/attachments/<event_id>/...
  conv_<other_conversation>/
    turn_<id>/...
  logs/
  timeline.json
```

Current-turn physical paths are `OUTPUT_DIR`-relative. Tools that execute code,
patch files, render files, or search local disk use these physical paths:

```text
turn_<current>/git/projects/site/index.html
turn_<current>/files/report.pdf
turn_<current>/git/snapshots/story/main.json
turn_<current>/attachments/input.xlsx
turn_<current>/external/followup/attachments/evt_1/file.docx
```

Logical refs point to the same content across turns and workers:

```text
conv:fi:turn_<id>.git/projects/site/index.html
conv:fi:turn_<id>.files/report.pdf
conv:fi:turn_<id>.git/snapshots/story/main.json
conv:fi:turn_<id>.user.attachments/input.xlsx
conv:fi:turn_<id>.external.followup.attachments/evt_1/file.docx
```

## What Each Physical Area Means

| Physical area | Meaning | Editable by checkout | Typical producer |
| --- | --- | --- | --- |
| `git/projects/` | Durable project/workspace state backed by the conversation git lineage. | Yes | `react.write`, `react.checkout`, code tools |
| `files/` | Produced artifacts and deliverables: reports, rendered outputs, spreadsheets, archives, diagnostics. | No by default | `react.write`, exec tools, rendering tools |
| `git/snapshots/` | Story, wizard, canvas, or workflow state snapshots. | No by default | owner rehosters, `react.write` |
| `attachments/` | Current-turn user uploads. | No | ingress/materialization |
| `external/` | Rehosted event/domain attachments or evidence. | No | `react.pull` / event policies |

## Tool Boundaries

```text
react.read
  reads visible/logical refs and already materialized files
  accepts conv:ar, conv:tc, conv:so, conv:ws, conv:su, conv:ev, conv:fi

react.pull
  materializes historical conv:fi refs or external owner refs into this turn
  returns logical_path=conv:fi:... and physical_path=turn_.../...

react.checkout
  makes historical project state editable
  accepts only conv:fi:<turn>.git/projects/... refs

react.rg
  searches local materialized physical paths only
  it does not search unpulled external owner refs

rendering tools / exec code / patch
  consume physical paths or inline content according to each tool contract
```

The ready rule:

```text
If code, rg, patch, or rendering needs bytes and the path is not visible in the
current `[WORKSPACE]` LOCAL tree, pull or checkout it first.
```

## Event Journey Boundary

Events arrive through the conversation event lane and are materialized into
timeline blocks under the effective ReAct turn id.

```text
ExternalEventPayload.routing.turn_id
  -> RuntimeCtx.turn_id
  -> timeline block paths such as conv:ar:turn_... or conv:ev:turn_...
  -> conv:fi:turn_... paths for files materialized in that turn
```

`active_turn_id_at_ingress`, `owner_turn_id`, and `target_turn_id` are
provenance. They do not replace `RuntimeCtx.turn_id` for refs produced by the
effective ReAct turn.

## Checklist

- Use `conv:<family>:` for ReAct-owned refs.
- Keep `conv_<conversation_id>` as a body segment after the family when a ref
  points into another conversation.
- Use `git/projects` for durable project state.
- Use `files` for produced artifacts and deliverables.
- Use `git/snapshots` for state snapshots.
- Use `react.pull` for exact external owner content and historical bytes.
- Use `react.checkout` only for editable `conv:fi:<turn>.git/projects/...`.
- Do not document or generate standalone conversation-family refs.
