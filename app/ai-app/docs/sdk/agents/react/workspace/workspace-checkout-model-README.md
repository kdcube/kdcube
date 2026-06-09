---
id: ks:docs/sdk/agents/react/workspace/workspace-checkout-model-README.md
title: "Workspace Checkout Model"
summary: "Final ReAct pull/checkout contract: react.pull materializes source refs, while react.checkout defines the current editable files/ workspace."
status: confirmed
tags: ["sdk", "agents", "react", "workspace", "checkout", "pull", "custom", "git"]
keywords:
  [
    "workspace checkout",
    "current turn files",
    "react.pull",
    "react.checkout",
    "workspace continuation",
    "git workspace",
    "custom workspace",
  ]
see_also:
  - ks:docs/sdk/events/namespaces-README.md
  - ks:docs/sdk/agents/react/agent-workspace-collboration-README.md
  - ks:docs/sdk/agents/react/react-turn-workspace-README.md
  - ks:docs/sdk/agents/react/custom-isolated-workspace-mental-map-README.md
  - ks:docs/sdk/agents/react/workspace/git-based-isolated-workspace-README.md
  - ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/react/v2/tools/pull.py
  - ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/react/v2/tools/checkout.py
---

# Workspace Checkout Model

This document is the short contract for `react.pull` and `react.checkout`.
The full workspace/artifact-origin model is in
[agent-workspace-collboration-README.md](../agent-workspace-collboration-README.md).

## Contract

`react.pull` materializes source refs locally.

`react.checkout` defines what should exist inside the current editable
workspace:

```text
turn_<current>/files/...
```

The two tools are deliberately separate. Pulling older or external material does
not activate it as current project state.

## react.pull

Use `react.pull` when bytes must exist locally under `OUTPUT_DIR` for
inspection, search, generated code, or copying.

Examples:

```text
fi:turn_111.files/app/src/main.py
  -> turn_111/files/app/src/main.py

fi:turn_111.outputs/report.html
  -> turn_111/outputs/report.html

fi:conv_conversation-42.turn_222.snapshots/wizard/current.yaml
  -> conv_conversation-42/turn_222/snapshots/wizard/current.yaml

nmsp:draft_1/issue-draft.yaml
  -> returns source_ref + materialized logical_path/physical_path chosen by the registered rehoster

ev:turn_111.events/task-tracker/snapshots/draft_1/latest
  -> readable timeline event identity; use the event's hosted_uri/payload.event_ref for bytes
```

Pull rules:

- accepts `fi:` refs;
- accepts registered custom namespace refs such as `nmsp:...`;
- `fi:turn_<id>.files/<scope-or-subtree>` may be pulled as a subtree;
- `fi:turn_<id>.outputs/<file>` is an exact-file artifact pull;
- attachments and external attachments are exact-file pulls;
- cross-conversation refs use `fi:conv_<conversation_id>.turn_<id>...` and
  materialize under `conv_<conversation_id>/turn_<id>/...`;
- custom namespace refs are opaque. The agent must use the returned
  `logical_path` / `physical_path` rows and must not derive `fi:` manually.
- `ev:` refs identify timeline event objects and are readable with
  `react.read` like `tc:`. They are not artifact refs. Do not pass `ev:` to
  `react.pull`; pull the event's `hosted_uri`,
  `payload.event_ref`, or artifact refs carried inside `payload.event`.

## react.checkout

Use `react.checkout` when historical `files/...` refs should become the current
editable project/workspace tree.

```json
{
  "paths": ["fi:turn_111.files/app"],
  "mode": "replace"
}
```

Result:

```text
turn_<current>/files/app/...
```

Checkout rules:

- accepts `fi:...files...` refs only;
- does not accept external owner refs such as `nmsp:`, `cnv:`, or `mem:` directly;
- does not accept `ev:` timeline event refs;
- `mode="replace"` clears `turn_<current>/files/` and applies the requested
  refs in order;
- `mode="overlay"` keeps the current `files/` tree and applies requested refs on
  top;
- after checkout, edit/search/run the current copy under
  `turn_<current>/files/...`, not the historical `turn_<older>/...` copy.

If a custom artifact must become editable workspace state, first pull it, then
write or copy the intended project file explicitly under
`turn_<current>/files/...`.

## Backend Notes

In `custom` mode, historical `files/...` refs hydrate from artifact/timeline
metadata and hosted blobs. There is no git lineage and no delete inference from
absence.

In `git` mode, textual `files/...` refs hydrate from the conversation lineage
snapshot. Hosted binaries and non-text exact refs still use the hosted artifact
path when timeline metadata says they are hosted artifacts.

In both modes, `outputs/...`, `snapshots/...`, and attachments are not current
editable project state unless explicitly copied/written into current-turn
`files/...`.
