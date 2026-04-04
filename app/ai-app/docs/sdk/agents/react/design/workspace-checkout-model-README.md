---
id: ks:docs/sdk/agents/react/design/workspace-checkout-model-README.md
title: "Draft: Workspace Checkout Model"
summary: "Draft design for making current-turn workspace population explicit in React by keeping react.pull as historical materialization and redefining react.checkout as the normal current-workspace checkout operation."
draft: true
status: draft
tags: ["sdk", "agents", "react", "design", "workspace", "checkout", "git", "custom"]
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
  - ks:docs/sdk/agents/react/design/git-based-isolated-workspace-README.md
  - ks:docs/sdk/agents/react/design/custom-isolated-workspace-mental-map-README.md
  - ks:docs/sdk/agents/react/react-turn-workspace-README.md
  - ks:docs/sdk/agents/react/flow-README.md
  - ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/react/v2/tools/pull.py
  - ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/react/v2/tools/checkout.py
  - ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/skills/instructions/shared_instructions.py
  - ks:src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/react/v2/agents/decision.py
---

# Workspace Checkout Model

This draft addresses a semantic gap in the current React workspace model:

- `react.pull(...)` materializes historical snapshot views under their original turn roots
- `react.checkout(...)` is currently described as a rare git-only whole-workspace reset
- there is still no clear, ordinary operation that answers:
  - "what should exist inside `turn_<current>/files/...` right now?"

That missing answer is the reason project continuation still feels indirect.

Current status:

- `react.pull(...)` is implemented as historical side materialization
- `react.checkout(...)` is implemented as current-workspace materialization
- `react.checkout(mode="replace")` replaces `turn_<current>/files/...`
- `react.checkout(mode="overlay")` imports/overwrites selected historical files into the existing current workspace without deleting unspecified files

---

## 1) The Problem

Today React can ask for older content like this:

```json
{"tool_id":"react.pull","params":{"paths":["fi:<turn_id>.files/<scope>/<path-or-prefix>"]}}
```

This correctly materializes history under:

```text
turn_<older_turn>/files/...
```

That is useful for:

- comparison
- copying from older turns
- allowing code to inspect older local files
- materializing the needed history as "working material"

But it does **not** explain what belongs in:

```text
turn_<current_turn>/files/...
```

which is the actual current editable workspace.

So React is left with an awkward split:

- `pull` gives it historical material
- current turn `files/` remains largely empty
- there is no normal operation that says:
  - "take this known workspace state and make it the current workspace"

That makes continuation less intuitive than it should be.

---

## 2) Two Candidate Designs

### Option A: special path syntax inside `pull`

Example idea:

```json
{"tool_id":"react.pull","params":{"paths":["w:fi:<turn_id>.files/projectA"]}}
```

Meaning:

- `fi:...` still means historical materialization
- `w:fi:...` additionally means:
  - apply this data into `turn_<current_turn>/files/...`

### Option B: use `checkout` for current workspace population

Meaning:

- `react.pull(...)` always materializes history only
- `react.checkout(...)` defines what gets materialized into the current turn workspace

This draft recommends **Option B**.

---

## 3) Why Option B Is More Natural

### 3.1 `pull` should keep one meaning

`react.pull(...)` is already useful and clear if it means exactly this:

- "make historical versioned material available locally"

If we also make it mean:

- "and sometimes also copy into the active workspace depending on a path prefix"

then the tool becomes overloaded.

That creates three problems:

- destination becomes path-prefix magic instead of a normal tool distinction
- current workspace composition becomes harder to explain in ANNOUNCE
- the agent has to reason about two different outcomes of the same tool

### 3.2 `checkout` already points at the right mental model

Even outside strict git semantics, "checkout" naturally suggests:

- make something the current working tree
- define what is now present in the editable workspace

That is much closer to the missing concept.

### 3.3 It works across both workspace backends

The user-facing meaning can be backend-neutral:

- in `custom`, checkout copies from artifact/timeline-backed history
- in `git`, checkout restores from the git-backed snapshot

React does not need to think about backend internals. It only needs a stable
tool contract for:

- historical materialization
- current workspace materialization

---

## 4) Proposed Semantic Split

### `react.pull(...)`

Keep it narrow:

- materialize historical data under its historical turn root
- never mutate the active current workspace

Example:

```json
{"tool_id":"react.pull","params":{"paths":["fi:turn_100.files/projectA/src/"]}}
```

Result:

```text
turn_100/files/projectA/src/...
```

### `react.checkout(...)`

Redefine it as:

- the normal tool that defines the contents of `turn_<current>/files/...`

Proposed contract:

```json
{
  "tool_id": "react.checkout",
  "params": {
    "mode": "replace | overlay",
    "paths": [
      "fi:<turn_id>.files/<scope-or-subtree>",
      "fi:<turn_id>.files/<scope-or-file>"
    ]
  }
}
```

Semantics:

- accepts `fi:...files...` refs only
- in `replace` mode, clears and rebuilds `turn_<current_turn>/files/...`
- in `overlay` mode, applies them on top of the existing `turn_<current_turn>/files/...`
- applies them in the order given
- later entries override earlier entries if they overlap

This gives a deterministic answer to:

- what is inside the current workspace right now?

Answer:

- the result of the latest `replace` checkout
- plus any later `overlay` checkouts
- plus later current-turn writes/patches/exec outputs under `files/`

---

## 5) Replace-Then-Apply Semantics

To stay clear, `checkout` should be defined as:

1. clear the current workspace tree under:

```text
turn_<current_turn>/files/
```

2. apply the requested `fi:...files...` refs in order

This is the crucial design point.

If checkout only overlaid onto whatever already happened to be in
`turn_<current_turn>/files/`, then the workspace would still be ambiguous.

The whole benefit of checkout is:

- it defines the current workspace deterministically

So the normal deterministic rule should be:

- `checkout(mode="replace")` replaces the current workspace view
- then applies the requested refs in order

Overlay is now a separate explicit mode:

- `checkout(mode="overlay")` keeps the current workspace view
- then applies the requested refs in order on top
- unspecified current-turn files remain present

---

## 6) Why Ordered Paths Matter

Ordered application lets React compose a workspace view deliberately.

Example:

```json
{
  "tool_id": "react.checkout",
  "params": {
    "paths": [
      "fi:turn_10.files/projectA",
      "fi:turn_12.files/projectA/config/settings.yaml"
    ]
  }
}
```

Meaning:

- start with `projectA` from turn 10
- then overlay a newer settings file from turn 12

This is expressive enough for:

- continuation from a previous project version
- mixing a base scope with a few more recent files
- preparing a current workspace that should be runnable now

And it still keeps the meaning simple:

- current workspace = ordered checkout result

---

## 7) What This Means for `turn_<current>/files`

After this change, the active workspace becomes easy to explain:

```text
turn_<current_turn>/files/... = the current checked-out workspace
```

Historical material remains separate:

```text
turn_<older_turn>/files/... = pulled historical material
```

That is the missing distinction the current model does not express well enough.

---

## 8) ANNOUNCE / Attention-Area Presentation

With this model, ANNOUNCE can be much more explicit.

Example:

```text
[WORKSPACE]
  implementation: git
  current_turn_root: turn_1775.../
  checked_out_from:
    - fi:turn_1774.files/projectA
    - fi:turn_1775.files/projectA/config/settings.yaml
  current_turn_scopes:
    - projectA/ (12 files)
  ls workspace:
    - projectA/ (latest turn_1775..., 12 files)
    - old_experiment/ (latest turn_1768..., 3 files)
```

If nothing has been checked out yet:

```text
[WORKSPACE]
  implementation: custom
  current_turn_root: turn_1776.../
  checked_out_from: none
  current_turn_scopes: none
  ls workspace:
    - projectA/ (latest turn_1775..., 12 files)
    - old_experiment/ (latest turn_1768..., 3 files)
```

This makes the agent's next step obvious:

- use `checkout` if it wants to continue a project
- use `pull` if it only wants historical material

---

## 9) Why This Helps Scope Reuse

Right now, React can see historical scopes but still has to improvise how to
make them current.

That contributes to scope drift such as:

- `minimal_bundle/`
- `minimal_bundle_telegram/`
- `minimal_bundle_admin/`

With checkout, the normal continuation sequence becomes:

1. inspect `ls workspace`
2. choose the project scope to continue
3. checkout that scope into the current workspace
4. keep editing `files/<scope>/...`

This does not solve naming discipline by itself, but it removes a major source
of ambiguity about how continuation should work.

---

## 10) Backend Semantics

### `custom`

Checkout should:

- resolve the requested `fi:...files...` refs from artifact/timeline-backed history
- clear `turn_<current>/files/`
- materialize the requested paths there in order

### `git`

Checkout should:

- resolve the requested `fi:...files...` refs from the git-backed lineage snapshots
- clear `turn_<current>/files/`
- restore/materialize the requested paths there in order

Important point:

- same visible contract
- different backend implementation

---

## 11) What Happens to the Existing `version` Param

The current `react.checkout(version="<turn_id>")` contract is too coarse.

It should be replaced by:

- `paths: [...]`

because the new question is not:

- "which whole repo version do I want?"

but:

- "which workspace content do I want in the current turn?"

If full-version checkout is still needed later, it can be expressed as:

```json
{"tool_id":"react.checkout","params":{"paths":["fi:<turn_id>.files/"]}}
```

or remain as an advanced compatibility form.

But the primary contract should be the ordered `paths` list.

---

## 12) Why This Is Better Than Introducing `w:fi:...`

`w:fi:...` would work technically, but it has weaker ergonomics:

- it hides a major workspace state change inside path syntax
- it makes `pull` do two jobs
- it is harder to explain and harder to surface in ANNOUNCE
- it is less readable in tool results and logs

`checkout(paths=[...])` is more explicit:

- it names the operation
- it names the destination semantics
- it gives us a stable place to show ordered workspace construction

---

## 13) Proposed Future Prompt Guidance

Once implemented, the agent guidance should become:

1. read `[WORKSPACE]`
2. if continuing a project and `checked_out_from` is empty:
   - call `react.checkout(paths=[...])`
3. work inside:
   - `files/<scope>/...`
4. use `react.pull(...)` only for historical side materialization

That sequence is much clearer than the current split between historical pull and
an almost-never-correct whole-workspace checkout.

---

## 14) Recommended Next Implementation Slices

### Slice 1: Tool contract

- redefine `react.checkout`
- accept ordered `paths`
- accept only `fi:...files...` in first iteration

### Slice 2: Runtime semantics

- clear `turn_<current>/files/`
- apply selected refs in order
- store checkout origin metadata

### Slice 3: ANNOUNCE

- expose:
  - `checked_out_from`
  - `current_turn_scopes`
  - `ls workspace`

### Slice 4: Prompt/docs

- revise:
  - `shared_instructions.py`
  - `decision.py`
  - `flow-README.md`
  - `react-turn-workspace-README.md`
  - git/custom workspace design docs

---

## 15) Bottom Line

Between the two candidate designs, the more natural one is:

- keep `react.pull(...)` strictly historical
- make `react.checkout(...)` the explicit current-workspace materialization tool

with this key rule:

- `checkout` replaces `turn_<current>/files/` and then applies the requested
  `fi:...files...` refs in order

That is the clearest answer to:

- what is in the current workspace?

and it gives React a much more intuitive continuation model.
