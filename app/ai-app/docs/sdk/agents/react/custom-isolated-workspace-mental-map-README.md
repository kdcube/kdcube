---
id: ks:docs/sdk/agents/react/custom-isolated-workspace-mental-map-README.md
title: "Custom Isolated Workspace Mental Map"
summary: "How the React agent perceives its workspace in workspace_implementation=custom — what ANNOUNCE shows, what is reconstructed live vs persisted, how the agent reasons about continuation, and what is intentionally not in the picture (no git history, no implicit deletes)."
status: confirmed
tags: ["sdk", "agents", "react", "workspace", "custom", "mental-model"]
keywords:
  [
    "custom workspace",
    "mental map",
    "workspace scopes",
    "ANNOUNCE workspace block",
    "rolling map",
    "delete inference",
    "react.pull",
    "react.checkout",
  ]
see_also:
  - ks:docs/sdk/agents/react/react-turn-workspace-README.md
  - ks:docs/sdk/agents/react/workspace/workspace-checkout-model-README.md
  - ks:docs/sdk/agents/react/workspace/git-based-isolated-workspace-README.md
  - ks:docs/sdk/agents/react/files-vs-outputs-README.md
  - ks:docs/sdk/agents/react/agent-workspace-collboration-README.md
  - ks:docs/sdk/agents/react/react-announce-README.md
  - ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/react/layout.py
  - ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/react/workspace.py
  - ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/react/tools/checkout.py
---

# Custom Isolated Workspace Mental Map

This document explains **how the React agent perceives its workspace in `workspace_implementation=custom`** — what surfaces it sees, what is reconstructed live each round, and what it can and cannot infer about workspace state.

This is the mental-model companion to the mechanical workspace docs.

**Scope**: agent perception in custom mode only.

- The filesystem layout, lifecycle, and exec snapshot transport are in [react-turn-workspace-README.md](./react-turn-workspace-README.md).
- The `react.pull` / `react.checkout` tool contract is in [workspace/workspace-checkout-model-README.md](./workspace/workspace-checkout-model-README.md).
- The `files/...` vs `outputs/...` namespace contract is in [files-vs-outputs-README.md](./files-vs-outputs-README.md).
- Tool cooperation (read/rg/write/patch) is in [agent-workspace-collboration-README.md](./agent-workspace-collboration-README.md).
- Git mode is in [workspace/git-based-isolated-workspace-README.md](./workspace/git-based-isolated-workspace-README.md).

This doc focuses on the question: *given there is no git history to inspect, what does the agent know about its workspace, and how?*

---

## 1) The three surfaces the agent reasons across

In `custom` mode the agent does not see one mutable directory. It reasons across three distinct surfaces, each with its own visibility and lifetime rules.

```text
                       AGENT'S WORKSPACE PERCEPTION (custom mode)
                       ═══════════════════════════════════════════

  ┌─────────────────────────────────────────────────────────────────────────┐
  │ (1) CURRENT TURN ARTIFACT ROOT  ─  physical, current-turn writable      │
  │                                                                          │
  │   out/workdir/                                                           │
  │     turn_<current>/                                                      │
  │       files/<scope>/...        ← durable workspace/project state         │
  │       outputs/<scope>/...      ← produced artifacts, not workspace state │
  │       attachments/<name>       ← attachments for this turn               │
  │                                                                          │
  │   Written via: react.write, react.patch, exec, react.checkout            │
  │   Read via:    react.read (logical fi: paths), react.rg                  │
  └─────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
  ┌─────────────────────────────────────────────────────────────────────────┐
  │ (2) CONVERSATION ARTIFACT MEMORY  ─  logical, cross-turn, not a folder  │
  │                                                                          │
  │   fi:turn_<older>.files/<scope>/<path>      ← prior workspace versions   │
  │   fi:turn_<older>.outputs/<scope>/<path>    ← prior produced artifacts   │
  │   fi:turn_<older>.user.attachments/<name>   ← prior attachments          │
  │   ar:turn_<id>.* / tc:turn_<id>.* / so:... / su:...                      │
  │                                                                          │
  │   Materialized locally only via:  react.pull(paths=[fi:...])             │
  │   Activated into current turn via: react.checkout(mode=…, paths=[…])     │
  └─────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
  ┌─────────────────────────────────────────────────────────────────────────┐
  │ (3) BUNDLE KNOWLEDGE SPACE  ks:  ─  logical, read-only, virtual          │
  │                                                                          │
  │   ks:<bundle-defined-path>/...                                           │
  │   Not part of the artifact root. Loaded into context via react.read.    │
  └─────────────────────────────────────────────────────────────────────────┘
```

What is **not** in the agent's picture in custom mode:

- No local git history (no `log` / `diff` / `status` / `show` semantics).
- No automatic hydration of older versions into the current turn.
- No tombstone or "deleted" state for files (see §4).
- No `previous saved workspace paths` populated from a lineage branch — that list, when present, comes from a different mechanism (see §3.3).

---

## 2) How the rolling-map concept is actually realized today

The earlier draft of this doc proposed a *durable, conversation-level rolling workspace map* (one persisted artifact, updated incrementally from each turn, with explicit deletion tombstones). That is **not the current implementation**.

What is implemented today:

```text
                  ROLLING MAP IS RECONSTRUCTED, NOT PERSISTED
                  ════════════════════════════════════════════

  Each ANNOUNCE composition pass:

   ┌──────────────────────────────────────────────────────┐
   │ build_announce_workspace_lines()                     │  ← layout.py:663
   │   ├─ implementation: custom | git                    │
   │   ├─ current_turn_root: turn_<current>/              │
   │   ├─ local_turn_roots: last 6 from disk              │
   │   ├─ current editable workspace:                     │  ← live scope
   │   │     enumerated from disk by                      │     enumeration
   │   │     summarize_current_turn_scopes()              │
   │   │     (workspace.py:285)                           │
   │   └─ checked_out_from / checkout_mode                │
   └──────────────────────────────────────────────────────┘

  Inputs to this reconstruction (each round, no cache):
    - the current-turn directory tree on disk
    - timeline-metadata for prior turn fi: refs
    - the last checkout call's metadata, if any
```

So the *rolling map* exists conceptually — the agent does see "what scopes are around, what's currently editable, what was checked out from" — but as a **per-round reconstruction** rendered into ANNOUNCE, not as a separate persisted artifact.

There is **no separate `fi:` artifact path** holding "the latest known version per workspace file". The agent navigates by:

- inspecting `[WORKSPACE]` in ANNOUNCE every round
- following `fi:turn_<id>.files/...` refs that appear in tool result blocks earlier in the timeline
- using `react.pull` + `react.checkout` to bring a chosen slice into the active turn

---

## 3) `[WORKSPACE]` in ANNOUNCE — what the agent actually reads

The agent's primary source of workspace orientation is the `[WORKSPACE]` section of ANNOUNCE, composed by `build_announce_workspace_lines()` ([layout.py:663-781](../../../src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/react/layout.py)).

### 3.1 Shape with no prior workspace state

```text
[WORKSPACE]
  implementation: custom
  current_turn_root: turn_1779265234123_ab9d8e/
  local_turn_roots:
    - turn_1779265234123_ab9d8e/
  current editable workspace: none
  checked_out_from: none
```

Meaning to the agent: *"You are in custom mode; the current turn root exists but contains no editable workspace scope yet; nothing has been checked out."*

### 3.2 Shape with an active editable workspace and a prior history

```text
[WORKSPACE]
  implementation: custom
  current_turn_root: turn_1779265234123_ab9d8e/
  local_turn_roots:
    - turn_1779265234123_ab9d8e/
    - turn_1779261800000_aa11bb/    ← historical (read-only here)
    - turn_1779260000000_zz77yy/    ← historical (read-only here)
  current editable workspace:
    - files/projectA/  (12 files)
  checked_out_from:
    - fi:turn_1779261800000_aa11bb.files/projectA
  checkout_mode: replace
```

Meaning to the agent: *"You have an active workspace under `turn_<current>/files/projectA/`, built by checking out the `projectA` scope from an earlier turn. To inspect or edit that scope's history, refer to the listed source ref."*

### 3.3 What `previous saved workspace paths` would mean — and why it is git-only

In git mode `[WORKSPACE]` also exposes a `previous saved workspace paths` list (the top-level `files/...` paths that have been published to the lineage branch in prior successful turns). That list is meaningful because git mode has an authoritative lineage from which "previously published" can be answered cheaply.

**Custom mode does not currently surface that list.** The custom workspace has no lineage branch, no `versions/<turn>` immutable refs, and no separate publish step. "Previous workspace state" in custom mode is whatever is recoverable through visible `fi:turn_<older>.files/...` refs in the timeline and through `react.memsearch` recovery — but not through a curated rolling list.

That asymmetry is intentional today: it is the cost of not requiring git for custom-mode deployments. See §6 for what would close the gap.

---

## 4) The semantic gap: there is no first-class delete in custom mode

The agent must read this section before acting on workspace state.

In custom mode there is **no delete operation**:

- `react.write` creates or replaces.
- `react.patch` modifies an existing local current-turn text file.
- `react.checkout(mode="replace")` rebuilds `turn_<current>/files/` from the supplied refs, which effectively removes anything not requested in that checkout. This is the closest thing to a delete signal in custom mode today.
- Nothing else *records intent to delete*.

What the agent must therefore avoid:

```text
                    DO NOT INFER DELETION FROM ABSENCE
                    ══════════════════════════════════

   A file present in turn_<older>.files/<scope>/<path>
   that is not present in turn_<current>/files/<scope>/...
   ↳ DOES NOT mean: "the user/agent deleted that file"
   ↳ Means:        "this turn did not carry that file forward"

   Reason: hydration in custom mode is explicit (pull + checkout).
   Absence is "not pulled into this turn", not "removed from the project".
```

If a previous turn's `files/projectA/old.md` is not present locally, the only safe interpretation is "I have not materialized it; if I need it, I can `react.pull(fi:turn_<older>.files/projectA/old.md)`". Treating the absence as a delete would be incorrect.

This is the single biggest semantic difference from git mode, where deletion *is* a first-class history event recorded by the commit graph.

---

## 5) The agent's reasoning loop in custom mode

```text
                  AGENT'S WORKSPACE REASONING LOOP (custom mode)
                  ═══════════════════════════════════════════════

   ┌─────────────────────────────────────────────────────────┐
   │ (1) Read [WORKSPACE] in ANNOUNCE every round.           │
   │     This is the orientation surface — not raw disk.      │
   └─────────────────────────────────────────────────────────┘
                              │
                              ▼
   ┌─────────────────────────────────────────────────────────┐
   │ (2) Decide: am I continuing prior project state?         │
   │                                                          │
   │   ▸ If "current editable workspace" already shows the   │
   │     scope I need ─ work directly under turn_<current>/  │
   │     files/<scope>/...                                    │
   │                                                          │
   │   ▸ If I need an earlier version, find its fi: ref by:  │
   │     - looking at earlier tool result blocks in timeline │
   │     - or react.memsearch when ref is pruned             │
   └─────────────────────────────────────────────────────────┘
                              │
                              ▼
   ┌─────────────────────────────────────────────────────────┐
   │ (3) Materialize the slice I need.                        │
   │                                                          │
   │   ▸ react.pull(paths=[fi:turn_<older>.files/<scope>])   │
   │     to hydrate historical content as read-only side      │
   │     material under turn_<older>/files/...                │
   │                                                          │
   │   ▸ react.checkout(mode="replace"|"overlay",             │
   │                    paths=[fi:turn_<older>.files/<scope>])│
   │     to make it the active editable workspace under       │
   │     turn_<current>/files/<scope>/...                     │
   └─────────────────────────────────────────────────────────┘
                              │
                              ▼
   ┌─────────────────────────────────────────────────────────┐
   │ (4) Edit in place under turn_<current>/files/<scope>/.. │
   │     using react.write / react.patch / exec.              │
   │                                                          │
   │     Reports/exports/test outputs that should NOT become  │
   │     workspace state go to turn_<current>/outputs/...     │
   │     (see files-vs-outputs-README.md).                    │
   └─────────────────────────────────────────────────────────┘
```

Key invariants:

- The agent never assumes prior files are *already there*. It pulls or checks out explicitly.
- The agent never reasons about deletion as a runtime event. It either carries a file forward or does not.
- The "rolling map" of "what scope to continue under" is read from ANNOUNCE, not from a separate registry.

---

## 6) Comparison with git mode

```text
   ┌────────────────────────┬────────────────────────┬────────────────────────┐
   │ Concern                │ custom mode            │ git mode               │
   ├────────────────────────┼────────────────────────┼────────────────────────┤
   │ Workspace lineage      │ none                   │ refs/heads/kdcube/.../ │
   │                        │                        │ <user>/<conversation>  │
   │ Per-turn immutable ref │ none                   │ refs/kdcube/.../       │
   │                        │                        │ versions/<turn_id>     │
   │ Delete semantics       │ NOT modeled            │ first-class (git diff) │
   │ Diff/log/status        │ NOT available          │ available locally,     │
   │                        │                        │ lineage-only           │
   │ Previous saved paths   │ NOT in ANNOUNCE        │ shown in ANNOUNCE      │
   │ Workspace publish step │ none                   │ runs on turn success;  │
   │                        │                        │ publish failure fails  │
   │                        │                        │ the turn               │
   │ Rolling-map source     │ ANNOUNCE +             │ ANNOUNCE +             │
   │                        │ live-reconstructed     │ lineage branch         │
   │                        │ from disk + timeline   │ inspection             │
   │ fi:turn_<id>.files     │ same syntax            │ same syntax            │
   │ react.pull             │ same contract          │ same contract          │
   │ react.checkout         │ same contract          │ same contract          │
   │ files/ vs outputs/     │ same contract          │ same contract          │
   └────────────────────────┴────────────────────────┴────────────────────────┘
```

The **agent-facing tool surface is identical** in both modes — that is by design. The difference lives in what the agent can *infer* about workspace history, not in what tools it has.

---

## 7) What is intentionally still future work

Two items remain open for custom mode. Neither blocks normal use; both would tighten the mental model.

**7.1 First-class delete operation for custom mode.**
Without an explicit delete signal, "the user/agent removed this file from the project" cannot be distinguished from "this turn did not carry the file forward". The agent compensates by *never inferring deletion from absence* (§4), but a real delete operation would let the runtime record removal intent and surface it through `[WORKSPACE]` and the timeline.

**7.2 Durable conversation-level rolling-map artifact.**
Today `[WORKSPACE]` is reconstructed each round from disk and timeline metadata. A persisted per-conversation map (one stable `fi:`/`ar:` artifact path that the agent can `react.read` for the full latest-known view per scope) would:

- give the agent a single recoverable reference for workspace state across compaction;
- avoid repeating disk enumeration each round;
- be the natural home for delete-state once §7.1 lands.

Both items are tracked as design targets; the current per-round reconstruction is sufficient for the use cases custom mode supports today.

---

## 8) What this doc does **not** cover

- Filesystem mechanics, exec workspace transport, snapshot persistence — see [react-turn-workspace-README.md](./react-turn-workspace-README.md).
- `react.pull` vs `react.checkout` semantics, `replace` vs `overlay` modes — see [workspace/workspace-checkout-model-README.md](./workspace/workspace-checkout-model-README.md).
- `files/<scope>/...` vs `outputs/<scope>/...` namespace rules — see [files-vs-outputs-README.md](./files-vs-outputs-README.md).
- Per-tool cooperation (`react.read`, `react.rg`, `react.write`, `react.patch`) — see [agent-workspace-collboration-README.md](./agent-workspace-collboration-README.md).
- Git-mode lineage branches, immutable version refs, publish flow — see [workspace/git-based-isolated-workspace-README.md](./workspace/git-based-isolated-workspace-README.md).
- The shape and lifecycle of ANNOUNCE as a whole — see [react-announce-README.md](./react-announce-README.md).

This doc is purely about *how the agent perceives the workspace when no git lineage is available* — and what that perception is and is not built to support.