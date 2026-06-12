---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/files-vs-outputs-README.md
title: "Files Vs Outputs"
summary: "Short ReAct namespace reference for files, outputs, snapshots, and attachments. The canonical workspace/artifact model is agent-workspace-collboration-README.md."
tags: ["sdk", "agents", "react", "workspace", "artifacts"]
keywords: ["files namespace", "outputs namespace", "snapshots namespace", "workspace membership", "artifact origin"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/agent-workspace-collboration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-turn-workspace-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/workspace/workspace-checkout-model-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/event-subsystem-README.md
status: confirmed
---
# Files Vs Outputs

This is the compact namespace reference. The full agent-facing workspace model,
including cross-conversation refs, custom namespace rehosters, and pull/checkout
behavior, lives in
[agent-workspace-collboration-README.md](./agent-workspace-collboration-README.md).

## Namespace Meanings

```text
files/       durable workspace/project state
outputs/     produced artifacts, reports, render sources, diagnostics
snapshots/   story/workflow state snapshots
attachments/ user-uploaded files for a turn
external/    externally authored/domain/followup attachments rehosted for ReAct
```

`files/` is the only namespace that represents current editable project state.
It is the namespace that `react.checkout` populates under
`turn_<current>/files/...`.

`outputs/` is not workspace history. It can hold HTML, Markdown, JSON, images,
PDFs, logs, reports, render sources, and test output, but those are produced
artifacts, not the project tree.

`snapshots/` is separate from `files/` even when the snapshot is text. A
snapshot records current story/workflow state, for example wizard state, canvas
state, or user-story state.

## Canonical Paths

```text
fi:turn_<id>.files/<rel>                              -> turn_<id>/files/<rel>
fi:turn_<id>.outputs/<rel>                            -> turn_<id>/outputs/<rel>
fi:turn_<id>.snapshots/<rel>                          -> turn_<id>/snapshots/<rel>
fi:turn_<id>.user.attachments/<rel>                   -> turn_<id>/attachments/<rel>
fi:turn_<id>.external.<event_kind>.attachments/<event_id>/<rel>  -> turn_<id>/external/<event_kind>/attachments/<event_id>/<rel>
fi:conv_<conversation_id>.turn_<id>.files/<rel>       -> conv_<conversation_id>/turn_<id>/files/<rel>
```

Custom namespace refs, such as `nmsp:draft/...`, are not `fi:` refs until
pulled. A registered namespace rehoster chooses the destination:

```text
nmsp:draft_1/issue-draft.yaml
  -> fi:turn_<current>.snapshots/nmsp/draft_1/issue-draft.yaml

nmsp:draft_1/evidence/screenshot.png
  -> fi:turn_<current>.external.nmsp.attachments/nmsp_<id>/nmsp/draft_1/evidence/screenshot.png
```

`nmsp` is only an example owner-domain namespace. It is valid only when a
bundle/module registers `@artifact_namespace_rehoster(namespace="nmsp")`.

## Visibility Is Separate

```text
files/...   + external  -> workspace member also emitted to the user
files/...   + internal  -> workspace member not emitted to the user
outputs/... + external  -> downloadable/visible artifact, not workspace state
outputs/... + internal  -> runtime/agent artifact, not workspace state
```

Use `turn_<current>/files/<scope>/...` for durable source trees, tests, assets,
configuration, and project docs that may be continued across turns.

Use `turn_<current>/outputs/<scope>/...` for reports, exports, render sources,
screenshots, diagnostics, and one-off deliverables.
