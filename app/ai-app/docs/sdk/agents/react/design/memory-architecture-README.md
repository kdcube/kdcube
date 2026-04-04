---
id: ks:docs/sdk/agents/react/design/memory-architecture-README.md
title: "Memory Architecture"
summary: "How memory is represented, stored, indexed, surfaced, compressed, and reopened in the React system."
tags: ["sdk", "agents", "react", "memory", "timeline", "workspace", "retrieval"]
keywords:
  [
    "memory architecture",
    "timeline memory",
    "announce",
    "sources pool",
    "workspace memory",
    "compaction",
    "pruning",
    "logical paths",
    "memsearch",
  ]
see_also:
  - ks:docs/sdk/agents/react/context-layout.md
  - ks:docs/sdk/agents/react/context-progression.md
  - ks:docs/sdk/agents/react/context-caching-README.md
  - ks:docs/sdk/agents/react/conversation-artifacts-README.md
  - ks:docs/sdk/agents/react/react-announce-README.md
  - ks:docs/sdk/agents/react/source-pool-README.md
  - ks:docs/sdk/agents/react/react-turn-workspace-README.md
  - ks:docs/sdk/agents/react/design/custom-isolated-workspace-mental-map-README.md
---

# Memory Architecture

React does not have one single memory bucket.

It works with a **memory architecture** composed of several cooperating surfaces:

- the **timeline** as durable temporal event memory
- the **attention area** (`SOURCES POOL + ANNOUNCE`) as always-visible rolling state
- the **turn log** as structured per-turn reconstruction memory
- the **workspace** as project/produced-file memory
- the **conversation artifact store + index** as durable persisted memory
- derived memory forms such as **summaries**, **replacement text**, **feedback**, and **plans**
- adjacent readable memory realms exposed through **logical namespaces** such as `ks:` and `sk:`

The key design choice is this:

- **visible context is only one slice of memory**
- **logical paths are the routing layer that let React reopen what is no longer visible**

## 1. Design Goal

The system is designed so that React can:

- keep the most useful state directly visible
- let older or bulky state fall out of the visible window
- still recover it cheaply later by path, search, or explicit workspace activation
- combine many memory realms without flattening them into one format

That is why React memory is organized around:

- **surfaces**: where the information lives
- **visibility rules**: whether it is always visible, tail-only, searchable, or hidden
- **retrieval handles**: the logical paths and tools that reopen it

## 2. Memory Surfaces

| Surface | Role | Visibility | Persistence |
|---|---|---|---|
| Timeline | Durable temporal event memory | Visible slice only | Persisted as `conv.timeline.v1` |
| SOURCES POOL | Rolling source registry and citation memory | Tail, always easy to find | Persisted as `conv:sources_pool` |
| ANNOUNCE | Operational attention board / signal memory | Tail, always easy to find | Rebuilt each round; final state persisted on exit |
| Turn log | Per-turn reconstruction memory | Not directly the model view | Persisted as `artifact:turn.log` |
| Workspace | Project/file continuity memory | Local filesystem + `fi:` refs | Files/outputs in OUT_DIR, optionally git-backed |
| Conversation artifact index | Searchable/indexed memory | Not directly visible | Postgres index rows + storage blobs |
| Summaries / hidden replacements / feedback / plans | Derived memory layers | Sometimes visible, sometimes retrievable | Persisted as blocks and/or artifacts |

## 3. The Timeline Is Memory, Not Just Chat History

The timeline is the core temporal memory surface.

It stores ordered blocks such as:

- user prompts
- user attachments
- assistant completions
- tool calls and tool results
- plan snapshots and plan history
- feedback blocks
- system messages such as TTL pruning notices
- compaction summaries (`conv.range.summary`)

The timeline is persisted as:

- `artifact:conv.timeline.v1`

The persisted payload carries:

- ordered `blocks`
- conversation metadata
- cache TTL state such as `cache_last_touch_at`
- a compact `sources_pool` snapshot for indexing/local access

Important semantic rule:

- the timeline is the **durable event memory**
- the model does **not** always see the full durable timeline
- it sees a rendered, pruned, compacted slice of it

So “in memory” and “currently visible” are not the same thing.

### Session view is a derived memory view

The model-facing **session view** is derived from the timeline at render time.

It is the visible slice after:

- TTL pruning
- optional compaction
- hidden-block replacement
- cache-point recomputation
- appending `SOURCES POOL` and `ANNOUNCE`

So the session view is not a separate authoritative store.

It is:

- the current **visible memory window**
- built from the deeper durable memory surfaces

## 4. Logical Paths Are the Memory Routing Layer

React memory is unified by **logical paths**.

Logical paths do two jobs:

- they identify memory objects across storage surfaces
- they remind the agent how to reopen something after it became hidden or fell out of the visible window

### Main namespaces

| Namespace | Meaning | Typical role in memory |
|---|---|---|
| `ar:` | Timeline / conversation artifact memory | prompts, completions, feedback blocks, plan aliases, internal events |
| `fi:` | File/workspace/output memory | workspace files, outputs, attachments, logs |
| `so:` | Sources pool memory | source rows and citations |
| `su:` | Summary memory | `conv.range.summary` blocks and compacted ranges |
| `tc:` | Tool call/result memory | exact call/result artifacts from tool execution |
| `ks:` | Knowledge-space memory | bundle-owned read-only reference space |
| `sk:` | Skill memory | loaded skill instructions and sources |

Some of these are strictly conversation memory (`ar:`, `so:`, `su:`, `tc:`), while others connect adjacent reusable memory realms (`ks:`, `sk:`). React treats them as one readable system because the retrieval contract is unified at the path level.

Examples:

- `ar:<turn_id>.user.prompt`
- `ar:<turn_id>.assistant.completion`
- `ar:plan.latest:<plan_id>`
- `fi:<turn_id>.files/<path>`
- `fi:<turn_id>.outputs/<path>`
- `fi:<turn_id>.user.attachments/<name>`
- `so:sources_pool[1-5]`
- `su:<turn_id>.conv.range.summary`
- `tc:<turn_id>.<tool_call_id>.result`

This is one of the main reasons the memory system stays coherent:

- the agent does not have to remember every storage backend
- it remembers a path family and the tool that can reopen it

## 5. Attention Area: Always-On-Top Memory

React also has a dedicated **attention area** at the tail of the rendered context:

- `SOURCES POOL`
- `ANNOUNCE`

This area is intentionally different from the main historical timeline.

### Why it exists

The attention area is where the system keeps things that should be:

- easy to find every round
- visually stable in location
- fresh and allowed to evolve quickly
- outside the stable cached prefix

### SOURCES POOL

The sources pool is rolling memory for:

- web sources
- eligible image/file sources
- skill-provided sources
- citable source identifiers (`sid`)

It is:

- conversation-level
- authoritative as `conv:sources_pool`
- rendered as one compact tail block
- the place where React can reliably find current source ids and short previews

It is both:

- **citation memory**
- **retrieval memory**

### ANNOUNCE

ANNOUNCE is operational memory, not long-form history.

It is the fixed place where React can expect to find things that must stay in front of its eyes, for example:

- budget / iteration state
- temporal ground truth
- open plans
- workspace state
- publish/prune notices
- new feedback notices
- bundle/runtime signals
- other conversation-wide rolling state that the solution wants always visible, such as preferences, recalls, or similar bundle-defined memory cues

This is why ANNOUNCE matters even beyond caching:

- it is not just an uncached tail block
- it is a **known attention board**

The model learns:

- “if something is important right now, it will be there”

## 6. Turn Log: Per-Turn Reconstruction Memory

The turn log is the minimal structured record of what happened in one turn.

It is persisted as:

- `artifact:turn.log`

Its payload contains:

- `turn_id`
- `ts`
- `blocks`
- `end_ts`
- `sources_used`
- `blocks_count`
- `tokens`

The crucial distinction:

- the **timeline** is the conversation-level temporal memory
- the **turn log** is the per-turn reconstruction substrate

Turn logs are used for:

- reconstructing fetched turn views
- rebuilding historical user/assistant/file artifacts
- resolving snippets for `react.memsearch`
- carrying feedback summaries in compact index text

The turn log is also one of the places where memory becomes searchable through the conversation index.

## 7. Workspace Is Memory Too

Workspace is not just scratch disk.

It is the memory surface for:

- evolving project trees
- earlier produced files
- active current-turn editable state
- artifacts the agent may need to inspect or continue later

### Namespace split

Workspace-related file memory is intentionally split:

- `files/...`
  - durable workspace/project state
- `outputs/...`
  - produced artifacts that should not become workspace history

That split matters because “user-visible artifact” and “workspace member” are different questions.

### Two workspace implementations

React supports two workspace backends:

- `custom`
- `git`

#### `custom`

- historical `.files/...` snapshots are hydrated from artifact/timeline/hosting-backed state
- the agent is not instructed to treat the workspace as git
- future design work adds a rolling mental map of latest known workspace state

#### `git`

- the current turn root is a sparse local repo
- historical `.files/...` pulls resolve against lineage snapshots
- the worktree starts sparse; files are not assumed to be present locally
- React must explicitly activate slices with `react.pull(...)`

### Attachments and binaries

Attachments and non-text hosted artifacts are also part of memory, but they behave differently:

- they are often reopened by exact `fi:` path
- they may be hosted rather than stored as readable text in context
- folder pulls do not imply binary descendants

Workspace memory therefore spans:

- local filesystem state
- historical version refs
- hosted artifact references
- logical paths that reconnect all of the above

## 8. Fetch and Stream Artifacts Are Delivery Views

The system also materializes client-facing views over memory.

Examples:

- conversation fetch payloads
- `conv.timeline_text.stream`
- `conv.artifacts.stream`
- synthesized `conv.thinking.stream`

These are useful, but they are not the primary reasoning memory stores.

They are derived from:

- timeline blocks
- turn logs
- sources pool
- hosted artifact metadata

So the rule is:

- timeline / turn log / sources pool / workspace / hosted artifacts = memory substrates
- fetch payloads and stream artifacts = delivery views over those substrates

## 9. Derived Memory Forms

Not all memory is raw original content.

React deliberately creates **derived memory** that preserves continuity while shrinking visible load.

### Compaction summaries

Compaction inserts:

- `conv.range.summary`
- logical path family `su:...`

These summaries are durable memory for ranges of earlier turns.

They are:

- stored as index-only artifacts
- embedded for retrieval
- used as the visible replacement for older dense history

### Hidden replacement text

Two mechanisms can replace visible content while preserving recoverability:

- TTL pruning
- `react.hide`

In both cases the original blocks remain in the timeline/artifact memory, while the visible stream shows short replacement text.

That replacement text is itself a memory layer:

- it tells the agent what used to be there
- it preserves a reopening handle
- it reduces token cost without destroying continuity

`react.hide` is especially important because it lets the agent intentionally forget irrelevant recent material while keeping a path-based route back to it.

### Plans

Plans are memory, not just control flow.

React stores plan snapshots and history as timeline blocks such as:

- `react.plan`
- `react.plan.history`

and exposes stable recovery handles such as:

- `ar:plan.latest:<plan_id>`

Plans therefore exist in three memory forms at once:

- persisted timeline blocks
- stable retrieval aliases
- current plan presentation in ANNOUNCE

### Feedback

Feedback is another derived memory layer.

It is persisted as:

- `artifact:turn.log.reaction`

and mirrored into:

- turn log payloads
- timeline `turn.feedback` blocks when cache is cold
- ANNOUNCE notices when cache is hot

So feedback can be:

- searchable/indexable memory
- timeline history
- live operational signal

## 10. Indexed Conversation Memory: Postgres + Storage

The React memory system is backed by two cooperating persistence layers:

- **blob/object storage** for full payload bodies
- **conversation index rows** in PostgreSQL (`conv_messages`) for compact searchable records

In practice:

- large or structured bodies live in storage via the conversation store
- compact summaries live in `conv_messages.text`
- some artifacts also carry embeddings for semantic retrieval

Important examples:

| Artifact kind | Blob | Index text | Embedding |
|---|---|---|---|
| `conv.timeline.v1` | yes | compact summary | yes |
| `conv:sources_pool` | yes | compact summary | yes |
| `turn.log` | yes | compact JSON summary | no |
| `turn.log.reaction` | yes | reaction JSON | no |
| `conv.range.summary` | no (index-only) | summary text | yes |

This means “memory in the system” includes both:

- what is stored as full content
- what is searchable/indexed as compact representation

## 11. Retrieval Paths: How React Gets Memory Back

React can reopen memory in several ways.

### `react.read`

Use when the agent already knows the logical path.

Examples:

- reopen a hidden prompt or completion via `ar:...`
- reopen a summary via `su:...`
- reopen source rows via `so:...`
- reopen workspace files or outputs via `fi:...`
- load a skill via `sk:...`
- read knowledge-space references via `ks:...`

This is the most direct path-based memory retrieval tool.

### `react.memsearch`

Use when the agent does **not** know the exact turn/path but remembers the topic.

`react.memsearch` searches the semantic conversation index and then resolves hits back into concrete turn snippets from turn-log/timeline blocks.

So memsearch is:

- indexed semantic memory lookup first
- exact turn-snippet reconstruction second

### `react.search_files`

Use to search the current local filesystem surface:

- OUT_DIR
- optionally workdir

It returns `logical_path` for OUT_DIR hits so the agent can immediately reopen content with `react.read`.

### `react.pull`

Use to materialize historical workspace/file memory locally.

This is the historical materialization tool for workspace/file memory:

- subtree pulls for `.files/...`
- exact file pulls for `.outputs/...` and attachments

### `react.checkout`

Use to materialize the active current-turn workspace itself under
`turn_<current_turn>/files/...` when React needs a runnable/searchable/testable
project snapshot.

### Generated code / exec

When needed, React can also explore local materialized memory with code:

- local workspace files
- logs
- pulled files
- bundle-readable `ks:` material if a resolver is available

This is still part of the memory system, because the code is operating on previously persisted or explicitly activated memory surfaces.

## 12. Visibility vs Memory Availability

One of the most important principles in this system is:

- **if something is not visible, that does not mean it is gone**

The memory system keeps information available across several states:

| State | Meaning |
|---|---|
| Visible in current rendered context | Immediately in front of the model |
| Present in attention area | Always-on-top operational memory |
| Hidden with replacement text | Still present; temporarily compressed |
| Summarized by compaction | Older detail collapsed into durable summary memory |
| Persisted in timeline / turn log / storage | Durable but not currently visible |
| Indexed/searchable | Recoverable by semantic or tag-based retrieval |
| Materializable into workspace | Recoverable by `react.pull` / `react.read` / code |

This is why the React memory architecture can stay both:

- information-dense
- operationally manageable

## 13. A Working Mental Model

A useful way to think about React memory is:

```mermaid
flowchart TD
    A[Conversation index and storage] --> B[Timeline memory]
    A --> C[Turn-log memory]
    A --> D[Sources-pool memory]
    A --> E[Workspace and hosted artifacts]
    B --> F[Rendered visible context]
    D --> G[Attention area]
    H[ANNOUNCE] --> G
    C --> I[react.memsearch snippet recovery]
    B --> J[react.read by logical path]
    E --> K[react.pull and local code exploration]
    L[Compaction / prune / hide / plans / feedback] --> B
    L --> G
```

In short:

- the **timeline** is the main temporal memory
- the **attention area** is the always-visible memory
- the **turn log** is the reconstruction memory
- the **workspace** is the project/file memory
- the **index + storage** provide durable searchable memory
- **logical paths** connect them all

## 14. Practical Design Rules

### Keep memory types distinct

Do not collapse these into one concept:

- durable historical memory
- operational attention memory
- local workspace memory
- indexed semantic memory

They cooperate, but they are not interchangeable.

### Prefer explicit recovery handles

Whenever React sees a logical path, it should treat that as a durable retrieval handle.

That is why paths are surfaced in:

- timeline blocks
- tool results
- replacement text
- workspace artifacts

### Use compression without losing reopenability

The system is designed so that:

- pruning does not destroy history
- compaction does not destroy continuity
- hiding does not destroy recoverability

Memory gets smaller in the visible window, not smaller in the system as a whole.

## 15. Memory Flow Across a Turn

At turn start, React memory is reassembled from several stores:

1. workspace bootstrap prepares the local execution surface
2. latest timeline artifact is loaded
3. latest sources-pool artifact is loaded
4. the in-memory timeline is initialized
5. user prompt and attachments are contributed into the new turn
6. feedback refresh updates either ANNOUNCE or the timeline, depending on cache state

During the turn:

- React contributes new blocks into the timeline
- the turn log grows as the per-turn reconstruction memory
- SOURCES POOL and ANNOUNCE are refreshed as the attention area
- hidden replacements, TTL pruning, and compaction may change the visible window without destroying deeper memory
- workspace memory may be expanded explicitly through `react.pull(...)` or local code execution

At turn finish:

- assistant completion is added
- turn log is persisted
- timeline artifact is persisted
- sources-pool artifact is persisted
- stream/fetch-facing delivery artifacts may be persisted or synthesized
- in `git` workspace mode, the current workspace snapshot may also be published to the lineage backend

So memory management in React is continuous:

- load durable memory
- expose a useful visible slice
- let the agent contribute and compress
- persist the updated memory surfaces again

## 16. Summary

React memory is an architecture, not a single store.

It combines:

- temporal event memory in the timeline
- always-visible operational memory in the attention area
- per-turn reconstruction memory in turn logs
- file/project memory in workspace and hosted artifacts
- indexed/searchable memory in the conversation index
- derived memory forms such as summaries, replacements, feedback, and plans

The glue across all of this is:

- **logical paths**

That is what allows React to work with memory that is:

- partially visible
- partially compressed
- partially local
- partially hosted
- still recoverable as one coherent system
