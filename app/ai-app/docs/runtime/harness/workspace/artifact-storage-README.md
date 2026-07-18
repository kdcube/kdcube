---
id: repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/workspace/artifact-storage-README.md
title: "Harness Artifact Storage"
summary: "Persistence, hosting, metadata, visibility, and download rules for harness workspace artifacts."
tags: ["runtime", "harness", "artifacts", "storage", "files", "security"]
updated_at: 2026-07-18
keywords:
  [
    "artifact storage",
    "attachments",
    "conv:fi",
    "hosted file",
    "download_url",
    "artifact visibility",
    "WorkspaceArtifact",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/workspace/README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/workspace/references-and-paths-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/harness/timeline/conversation-artifacts-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/hosting/files-storage-system-README.md
---
# Harness Artifact Storage

This page describes byte-bearing artifacts in the shared agent harness. It is
not a list of every timeline block or tool result.

## Files Versus Timeline Records

Tool calls and results can exist only as timeline records such as
`conv:tc:...result`. They are not files unless the tool also produced bytes.

Byte-bearing objects include:

- generated-code outputs;
- framework write-tool file/display outputs;
- rendering outputs;
- user attachments;
- external-event or owner-domain attachments;
- historical files materialized into a turn workspace.

The common `WorkspaceArtifact` record describes a file independently of whether
it is later shown in Files, Artifacts, canvas, chat, or another client.

## Physical And Logical Identity

Physical paths are `OUTPUT_DIR`-relative:

```text
turn_<turn_id>/git/projects/<scope>/<path>
turn_<turn_id>/files/<scope>/<path>
turn_<turn_id>/git/snapshots/<scope>/<path>
turn_<turn_id>/attachments/<name>
turn_<turn_id>/external/<kind>/attachments/<event_id>/<name>
```

Durable logical refs include the owning conversation:

```text
conv:fi:conv_<conversation_id>.turn_<turn_id>.git/projects/<scope>/<path>
conv:fi:conv_<conversation_id>.turn_<turn_id>.files/<scope>/<path>
conv:fi:conv_<conversation_id>.turn_<turn_id>.git/snapshots/<scope>/<path>
conv:fi:conv_<conversation_id>.turn_<turn_id>.user.attachments/<name>
conv:fi:conv_<conversation_id>.turn_<turn_id>.external.<kind>.attachments/<event_id>/<name>
```

No emitted or persisted path uses `current_turn`. Use a concrete turn ID.

## Hosted Bytes

Assistant-produced files and uploads are hosted in conversation file storage.
The storage key preserves enough path identity to distinguish same-named files
from different scopes.

General shape:

```text
cb/tenants/<tenant>/projects/<project>/attachments/
  <owner>/<conversation_id>/<turn_id>/<artifact-root-relative-path>
```

The object store URI is implementation metadata. Clients should receive a
resource handle or download URL, not base64 bytes and not a logical workspace
path masquerading as a public URL.

## Resolution Preserves Bound Identity

`conv:fi:` contains object location, not user authority.

Resolution combines:

```text
bound tenant/project/user
  + ref conversation/turn/path
  + conversation storage policy
```

Only after an in-scope object is found are bytes returned or copied. Owner
namespaces apply their own authorization under the carried request identity.

## Artifact Metadata

Common fields:

| Field | Meaning |
| --- | --- |
| `logical_path` / `artifact_path` | Canonical `conv:fi:` identity. |
| `physical_path` | `OUTPUT_DIR`-relative workspace path. |
| `filename` | Basename for client presentation. |
| `mime` | Content type. |
| `kind` | Artifact behavior/presentation class. |
| `visibility` | Whether transport may emit it to the user/client. |
| `rn` | Platform resource name used by compatible clients. |
| `hosted_uri` | Backing storage location for trusted runtime use. |
| `key` | Storage key. |
| `download_url` | HTTP download route returned by object action resolution. |

These fields are not interchangeable. A logical path identifies an object; it
is not a URL. A hosted URI is not a browser contract. A client download action
should use `download_url` or the supported resource handle.

## Visibility And Kind

Visibility controls transport eligibility:

| Visibility | Meaning |
| --- | --- |
| `external` | May be emitted to the user/client if that transport supports the artifact class. |
| `internal` | Persists for runtime/agent use without user artifact emission. |

Kind controls artifact semantics:

| Kind | Meaning |
| --- | --- |
| `file` | Byte-bearing downloadable file. |
| `display` | Artifact intended for an artifact/canvas presentation. |
| `search_result` | Search-result artifact; not a file unless it also carries a file payload. |
| `timeline` | Timeline/progress content; never an Artifacts item merely because it is persisted. |

Clients must use explicit artifact/package metadata. Namespace prefixes,
filename extensions, and timeline channel names are not sufficient placement
rules.

## Example

```json
{
  "artifact_path": "conv:fi:conv_<conversation_id>.turn_<turn_id>.files/report/report.md",
  "physical_path": "turn_<turn_id>/files/report/report.md",
  "filename": "report.md",
  "mime": "text/markdown",
  "kind": "display",
  "visibility": "external",
  "rn": "ef:...:artifact:report.md",
  "hosted_uri": "s3://...",
  "key": "...",
  "download_url": "/api/cb/resources/.../download"
}
```

## Ownership

- `runtime.harness.workspace.artifacts` owns the framework-neutral artifact
  record.
- `runtime.harness.workspace.layout` owns artifact-root paths and change
  detection.
- `runtime.harness.events.resolver` owns generic `conv:fi:` byte/download
  resolution.
- conversation persistence owns hosted/index records.
- framework adapters may add model-facing tools or UI projection, but should
  not redefine the storage contract.
