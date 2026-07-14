---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/artifact-storage-README.md
title: "Artifact Storage"
summary: "Where ReAct files, attachments, timeline artifacts, hosted file metadata, and conv:fi paths are stored and indexed."
tags: ["sdk", "agents", "react", "artifacts", "storage"]
updated_at: 2026-07-14
keywords: ["artifact storage", "attachments", "turn artifacts", "timeline files", "conv:fi", "storage rules", "bound user scope", "artifact materialization"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-realm-refs-and-workspace-paths-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/artifact-discovery-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/conversation-artifacts-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-tools-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/workspace/workspace-model-README.md
---
# Artifact Storage Rules

This page describes persistence and hosting. The workspace/ref grammar is in
[ReAct Realm Refs And Workspace Paths](./react-realm-refs-and-workspace-paths-README.md).

## Files vs Tool Results

Tool call/result JSON is stored in timeline/tool-call records such as
`conv:tc:<turn>.<call>.result`, not as ordinary disk files unless a tool also
produces files.

Only these byte-bearing objects are written under the artifact output root:

- exec tool outputs;
- `react.write` file/display artifacts;
- `rendering_tools.write_*` outputs;
- user attachments;
- owner refs materialized by `react.pull`.

## Physical Path Contract

Agent-visible physical paths are always `OUTPUT_DIR`-relative:

```text
turn_<id>/git/projects/<project_scope>/...     # editable durable project state
turn_<id>/files/<artifact_scope>/...           # produced files and deliverables
turn_<id>/git/snapshots/<snapshot_scope>/...    # state snapshots
turn_<id>/attachments/<filename>               # user uploads
turn_<id>/external/<kind>/attachments/<event_id>/<filename>
conv_<conversation_id>/turn_<id>/<area>/<path> # cross-conversation material
```

The agent should not know or mention host/runtime absolute prefixes. In local
runtime storage these paths live under the artifact root, for example
`out/workdir/`.

Logical refs use the `conv:fi:` family. Every ref the runtime emits is
conversation-qualified at birth: the `conv_<conversation_id>.` scope segment
right after the namespace names the conversation the ref lives in, so refs are
absolute identities — fork-safe, pin-safe, and location-independent:

```text
conv:fi:conv_<conversation_id>.turn_<id>.git/projects/<project_scope>/<path>
conv:fi:conv_<conversation_id>.turn_<id>.files/<artifact_scope>/<path>
conv:fi:conv_<conversation_id>.turn_<id>.git/snapshots/<snapshot_scope>/<path>
conv:fi:conv_<conversation_id>.turn_<id>.user.attachments/<filename>
conv:fi:conv_<conversation_id>.turn_<id>.external.<kind>.attachments/<event_id>/<filename>
```

The same scope segment applies to every conversation-scoped ref kind
(`conv:ar:`, `conv:so:`, `conv:tc:`, `conv:ws:`, `conv:su:`, `conv:ev:`).
Resolution notes:

- Resolvers accept conversation-local legacy refs (persisted before
  qualification-at-birth) forever; they resolve relative to the conversation
  that owns the timeline.
- A qualified ref whose segment names the current conversation resolves to the
  local physical layout (`turn_<id>/...`); refs from other conversations
  materialize under `conv_<conversation_id>/turn_<id>/...`.
- `conv:ar:`/`conv:tc:`/`conv:ws:` refs are timeline-resident: a qualified ref
  resolves within its home timeline (including fork-copied blocks carried into
  a child conversation). The segment records identity and provenance;
  cross-conversation resolution for these kinds is a possible follow-up, not a
  current capability.

Artifacts never use `current_turn` in stored paths. Always use the concrete
`turn_id`.

## Hosted Storage Location

Assistant-produced file bytes are hosted in conversation file storage under the
current turn. The stored key preserves the full artifact-root-relative path, so
files with the same basename in different directories remain distinct.

General shape:

```text
s3://<bucket>/<prefix>/cb/tenants/<tenant>/projects/<project>/attachments/<user_id>/<conversation_id>/<turn_id>/<artifact-root-relative-path>
```

Example:

```text
physical_path:
  turn_2026-07-04-09-00-00-000/files/analysis/zip_contents.json

logical_path:
  conv:fi:turn_2026-07-04-09-00-00-000.files/analysis/zip_contents.json

storage key:
  cb/tenants/demo/projects/demo/attachments/<user>/<conversation>/
    turn_2026-07-04-09-00-00-000/
    turn_2026-07-04-09-00-00-000/files/analysis/zip_contents.json
```

Visibility controls transport/UI emission, not byte persistence.

## Ref Resolution Preserves The Bound User

The model-facing `conv:fi:` ref contains conversation, turn, namespace, and
relative path identity. It does not carry tenant or user authority.

Historical artifact lookup receives `RuntimeCtx.user_id` from the authenticated
runtime and queries the conversation index by:

```text
bound user_id + requested conversation_id + requested turn_id
```

The resolved turn-log artifact supplies trusted hosted metadata. Only then does
the workspace service fetch the hosted bytes and copy them into the current
workspace. A guessed ref for another user's conversation yields no artifact in
the bound user's index scope, so no hosted bytes are materialized.

External owner refs follow their registered owner resolver instead of this
conversation-artifact path. The owner resolver applies its own authorization
under the carried request identity.

## Conversation State Artifacts

Conversation state is stored as conversation artifacts:

- `artifact:conv.timeline.v1` — timeline blocks, conversation metadata, and
  current full `sources_pool`;
- `artifact:conv:sources_pool` — sources pool only;
- `artifact:turn.log` — per-turn block log.

These artifacts are indexed in `conv_messages` with `role = "artifact"` and
tags such as `artifact:conv.timeline.v1`, `artifact:conv:sources_pool`, and
`kind:turn.log`.

## Hosted File Fields

When a file is hosted, metadata blocks include:

| Field | Meaning |
| --- | --- |
| `rn` | Resource name; primary download/open handle for clients. |
| `hosted_uri` | Backing storage URI, usually S3. |
| `key` | Storage key under the configured bucket/prefix. |
| `logical_path` or `artifact_path` | `conv:fi:` ref. |
| `physical_path` | `OUTPUT_DIR`-relative path. |

These fields are not interchangeable. UI download flows expect `rn`.
`react.pull` uses hosted metadata or the current workspace backend to
materialize bytes. It must not treat a text preview as the complete file.

## Visibility

| Visibility | Meaning |
| --- | --- |
| `external` | Emitted to the user/client when the transport supports this artifact class. |
| `internal` | Persisted and available to agent/runtime, not emitted as a user artifact. |

Visibility does not decide whether something is project state. Namespace does:

```text
git/projects/... + external/internal  -> project state
files/...        + external/internal  -> produced artifact
git/snapshots/...+ external/internal  -> state snapshot
```

## Kind

Common artifact kinds:

| Kind | Meaning |
| --- | --- |
| `file` | Byte-bearing file artifact. |
| `display` | Displayable artifact block, often rendered in Artifacts UI. |
| `search_result` | Search-result artifact entry; not a downloadable file unless it also has a file payload. |
| `timeline` | Timeline/progress text; not an Artifacts-tab item. |

Clients should use explicit package/artifact metadata, not namespace guesses,
when deciding whether something belongs in Files, Artifacts, Links, Steps, or
Timeline.

## Example Metadata Block

```json
{
  "artifact_path": "conv:fi:turn_2026-07-04-09-00-00-000.files/report/report.md",
  "physical_path": "turn_2026-07-04-09-00-00-000/files/report/report.md",
  "mime": "text/markdown",
  "kind": "display",
  "visibility": "external",
  "rn": "ef:...:artifact:report.md",
  "hosted_uri": "s3://...",
  "key": "..."
}
```
