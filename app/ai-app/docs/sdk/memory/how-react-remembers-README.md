---
id: ks:docs/sdk/memory/how-react-remembers-README.md
title: "How ReAct Remembers"
summary: "Explains the different memory mechanisms available to ReAct: visible timeline, working summaries, range summaries, internal memory beacons, memsearch, turn indexes, and durable user memories."
tags: ["sdk", "memory", "react", "compaction", "memsearch", "internal-notes", "user-memory"]
keywords: ["react memory", "internal memory beacons", "react.note", "react.memsearch", "conv.working.summary", "conv.range.summary", "turn index", "user memories", "compaction"]
see_also:
  - ks:docs/sdk/memory/conversational-memory-search-README.md
  - ks:docs/sdk/memory/user-memories-overview-README.md
  - ks:docs/sdk/memory/user-memories-react-integration-README.md
  - ks:docs/sdk/memory/user-memories-operational-README.md
---
# How ReAct Remembers

ReAct has several mechanisms that look like memory, but they do different jobs.
Keeping the distinction clear is important; otherwise agents start mixing
conversation recovery, private scratch notes, and durable user preferences.

The short model:

```text
current timeline
  immediate context for the current turn and visible conversation tail

ReAct recovery memory  (a.k.a. conversational memory index)
  turn summaries, turn indexes, memsearch, internal beacons
  helps recover what happened in prior turns of the conversation

durable user memory
  user-visible, editable, cross-conversation facts, preferences,
  durable decisions, reusable anchors, specs, and milestones
  helps future conversations behave correctly
```

The big picture, drawn out:

```text
                  +---------------------------------------------+
                  |                  AGENT                      |
                  |       (current turn / decision loop)        |
                  +--+--------------+--------------+------------+
                     | writes       | react.read / | memory.*
                     | turn output  | react.       | tools,
                     |              | memsearch    | widget
                     v              v              v
       +----------------+  +-------------------+  +-------------------+
       |   VISIBLE      |  |  CONV MEMORY      |  |  DURABLE USER     |
       |   TIMELINE     |  |  INDEX            |  |  MEMORY           |
       |                |  |  (conv_messages)  |  |  (user_memory_*)  |
       |  In-context    |  |                   |  |                   |
       |  tail. Pruned  |  |  Every persistable|  |  Curated facts,   |
       |  by compaction |  |  turn block also  |  |  preferences,     |
       |  -> range      |  |  lives here until |  |  decisions,       |
       |  summary +     |  |  TTL (365d def.)  |  |  anchors, specs,  |
       |  preserved     |  |                   |  |  milestones       |
       |  notes         |  |  text, embedding, |  |                   |
       |                |  |  anchors_text,    |  |  user-visible,    |
       |  Not directly  |  |  search_tsv,      |  |  user-editable    |
       |  searchable;   |  |  tags, ts, ttl    |  |                   |
       |  shrinks every |  |                   |  |  reads:           |
       |  turn          |  |  retrieval:       |  |   announce hotset |
       |                |  |   hybrid (sem+lex |  |   (turn start),   |
       |                |  |   +RRF+recency),  |  |   memory.search,  |
       |                |  |   catalog modes,  |  |   widget          |
       |                |  |   scope conv|user |  |                   |
       |                |  |                   |  |  writes (explicit |
       |                |  |  Not user-visible,|  |  only):           |
       |                |  |  not curated.     |  |   memory.propose, |
       |                |  |                   |  |   reconciler,     |
       |                |  |                   |  |   widget          |
       +----------------+  +-------------------+  +-------------------+

Cross-district flows:

  agent writes a turn block       -> visible timeline      (becomes in-context)
  runtime persists each block     -> conv memory index     (one row per block)
  compaction prunes the tail      -> range summary +
                                     preserved notes       (replaces pruned slice
                                                            inside visible timeline)
  agent calls react.read          -> visible timeline      (reads visible refs)
  agent calls react.memsearch     -> conv memory index     (returns handles;
                                                            agent then react.read's them)
  agent calls memory.propose,
   reconciler or widget commits   -> durable user memory   (explicit only;
                                                            no auto-promotion)
  durable memory at turn start    -> visible timeline      (announce hotset injects)
```

The three districts share an author (the agent and runtime) but have separate
storage, separate retrieval, and separate visibility rules. The same turn output
can land in two of them simultaneously (visible timeline + conv memory index)
without landing in the third (durable user memory), because durable memory is
explicit-promotion-only.

A conversation is made of turns. ReAct sees the current turn plus whatever
conversation tail, summaries, and refs are loaded into context. Older turns in
the same conversation are not automatically fully visible after pruning; they
must be recovered through summaries, turn indexes, exact refs, or `react.memsearch`.
Content from other conversations is not automatically visible either, but
`react.memsearch(scope="user")` can search the indexed conversation history for
the same user when that wider recovery scope is allowed.

## Current Answer

Internal Memory Beacons do survive pruning only when they were written as
inline `react.note` blocks. That means:

```text
react.write(channel="internal", scratchpad=true)
```

In the actual ReAct protocol this is a full `react.write` tool call. The
parameter order matters: `path`, `channel`, `content`, `kind`, then optional
`scratchpad`. `scratchpad` defaults to `false`; the model must set it to `true`
when it wants the internal write to also become an inline `react.note`.

```text
<channel:ReactDecisionOutV2>
{
  "action": "call_tool",
  "notes": "",
  "tool_call": {
    "tool_id": "react.write",
    "params": {
      "path": "outputs/internal_notes/rendering_refs.md",
      "channel": "internal",
      "content": "[K] fi:turn_old.outputs/report.html - HTML source used for PDF rendering.\n[D] Rendering source refs must point at text source artifacts.\n[P] User prefers direct engineering explanations.",
      "kind": "display",
      "scratchpad": true
    }
  }
}
</channel:ReactDecisionOutV2>
```

Use an empty `notes` value, or neutral user-safe progress text, for internal
beacon writes. The outer `notes` field belongs to the decision/status envelope
and may be shown to the user; `channel="internal"` controls only the visibility
of the `react.write` artifact and inline `react.note`.

This creates two runtime objects:

```text
1. Internal file artifact
   path:       fi:<turn_id>.outputs/internal_notes/rendering_refs.md
   visibility: internal
   user sees:  no
   content:    the full multi-line beacon text

2. Inline timeline note
   type:       react.note
   path:       same fi:<turn_id>... path
   visibility: internal/user-invisible
   text:       the same multi-line beacon text
   meta:       channel=internal
```

Compaction preserves selected `react.note` blocks as authored, up to the
configured cap. It extracts all bracket tags from the note into metadata for
search/filtering, and additionally folds `[P]` lines from the selected notes
into an `[INTERNAL MEMORY DIGEST]` inside the range summary.
The preserved-note path uses the compaction summary turn id and an index:

```text
ar:<summary_turn_id>.react.note.preserved.1
ar:<summary_turn_id>.react.note.preserved.2
...
```

The original note path is retained in metadata as `source_path`.

By contrast:

```text
react.write(channel="internal", scratchpad=false)
```

creates only an internal file artifact. The artifact can persist and can be
read if its logical path is known, but its content is not promoted as an inline
memory beacon during compaction.

`react.memsearch` is not the same thing as durable memory. It searches the
conversation index and turn catalog to recover old turns, summaries, user
messages, assistant answers, attachments, and indexed internal notes. Internal
beacons are searchable with `targets=["notes"]` once they have been indexed as
`kind:react.note`; older turns written before note indexing may still only be
discoverable indirectly through summaries or turn indexes.

## Mechanism Table

| Mechanism | Written By | User Visible | Automatically Visible To ReAct | Retrieval | Compaction / Pruning Behavior | Cross Conversation | Semantic Purpose |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Current visible timeline tail | Runtime | Mixed | Yes, while still in context | Already visible, or `react.read` for visible refs | Old blocks may be compacted or replaced by summaries/path placeholders | No, except persisted conversation index | Immediate working context for the active turn/conversation |
| `conv.working.summary` | Assistant packet/runtime after a turn or attempt | Usually internal timeline/debug context | Often visible for old turns when loaded into context | `react.read("ws:<turn_id>.conv.working.summary")`; `react.memsearch(targets=["summary"])` | Stored/readable as a per-turn `ws:` recovery object; may help build a later `conv.range.summary`, but is not the main visible replacement for a compacted prefix | Conversation/user indexed, not durable user memory | Recover "what this turn accomplished / what matters next" without loading the whole turn |
| `conv.range.summary` | Compaction | Internal timeline context | Yes when compacted prefix is visible | `react.read("su:<turn_id>.conv.range.summary")` | Replaces a pruned prefix; receives internal preference digest and is kept as compact context | Conversation scoped | Summarize older pruned conversation history |
| Inline internal beacon: `react.note` | `react.write(channel="internal", scratchpad=true)` | No | Yes while visible; after compaction only if selected into preserved notes or summarized | Visible timeline; `react.read` by path if known; `react.memsearch(targets=["notes"])` after indexing | During compaction, deduped note blocks from the compacted slice are candidates for `react.note.preserved`; tags are extracted from all bracket lines; `[P]` lines from selected notes may also enter an internal digest | Conversation index; cross-conversation only through `react.memsearch(scope="user")` when retained/indexed, not durable user memory | Small high-signal "stones" left by the agent: preferences, decisions, specs, milestones, artifact anchors |
| Preserved beacon: `react.note.preserved` | Compaction | No | Yes after compaction | Visible timeline; `react.read("ar:<summary_turn_id>.react.note.preserved.<n>")` if path known | Up to `MAX_PROMOTED_INTERNAL_NOTES` deduped note blocks from the compacted slice are re-emitted as authored immediately after the new range summary; this is capped retention, not all notes forever | Conversation scoped | Keep selected important beacons visible after pruning |
| Internal file artifact | `react.write(channel="internal", scratchpad=false)` or internal tool output | No | Not inline by default | `react.read("fi:<...>")` if exact path is known; discover via turn index if indexed | File may persist, but content is not promoted as a note; old timeline references can be compacted | Conversation/turn artifact scoped | Larger private scratch material or internal source files that should not be shown to the user |
| `react.memsearch` | Tool over conversation index | Tool result is internal | Only when agent calls it | `react.memsearch(mode=..., scope="conversation"|"user", targets=["summary","user","assistant","attachment","notes"])`, then read returned `ws:`, `ar:`, `fi:`, `tc:` refs | Not memory itself; searches persisted indexes and turn catalog while retention/TTL allows | Explicitly supports conversation scope or user scope; user scope may recover indexed turns from other conversations | Find the turn or artifact when the path is not already known |
| Turn index: `ar:<turn_id>.react.turn.index` | `react.read` resolver from persisted turn log/artifact metadata | Internal | Only when read | `react.read(["ar:<turn_id>.react.turn.index"])` | Reconstructed from persisted turn log metadata | Conversation/turn scoped | Discover exact refs inside a known turn |
| Durable user memory hotset | SDK memory store and widget | Yes by default | Yes only when memory announce is enabled | Announce hotset, future `me:<id>` reads, widget/API search | Not part of timeline compaction; stored in Postgres | Yes | Stable user-visible facts, preferences, durable decisions, reusable anchors, specs, and milestones that should affect future conversations |
| Durable user memory search | SDK memory tools/widget | Yes for widget, internal for agent tools | Only when called or rendered in announce | Widget search, future memory search/read tools | Independent of ReAct pruning | Yes | Retrieve durable user memory by hybrid search, labels, keywords, recency, salience, confidence |

## Internal Memory Beacons

The producer instruction calls these "Internal Memory Beacons". The agent writes
them with `react.write` on the internal channel.

Recommended shape:

```text
[P] User prefers impact-first technical explanations.
[D] We chose ref normalization instead of telling the model to emit only fi: refs.
[S] Rendering refs accepted by write_* are text source artifacts, not final files.
[A] Memory widget CRUD was implemented and connected to bundle entrypoints.
[K] fi:turn_123.outputs/report.html - HTML source used for PDF rendering.
```

A single `react.write(content=...)` may contain several short beacon lines.
Each line that starts with `[P]`, `[D]`, `[S]`, `[A]`, or `[K]` contributes a
tag to the note metadata. The note is still preserved and retrieved as the full
authored block, because adjacent tags often provide needed context for each
other.

The tags mean:

```text
[P] personal/preferences
[D] decisions/rationale
[S] specs/structure/technical details
[A] achievements/milestones
[K] key artifacts/anchors with logical path and why they matter
```

Use `scratchpad=true` only for short lines that should stay visible to future
agents as inline notes. `scratchpad` defaults to `false`; without
`scratchpad=true`, the write is just a private file artifact.

## How Compaction Treats Beacons

The compaction path scans compacted blocks for:

```text
react.note
react.note.preserved
```

It does not scan arbitrary internal file artifacts.

For matching note blocks from the compacted slice, compaction:

1. Extracts all bracket tags from each note for metadata.
2. Deduplicates by source path, or by text when no path exists.
3. Keeps the most recent note blocks up to `MAX_PROMOTED_INTERNAL_NOTES`.
4. Rewrites selected note blocks as `react.note.preserved`, preserving the full authored text.
5. Places them immediately after the generated `conv.range.summary`.
6. Extracts `[P]` lines from the selected note blocks into an `[INTERNAL MEMORY DIGEST]`
   appended to the range summary, capped by `MAX_PREFERENCE_DIGEST_LINES`.

This does not retain every note ever made in the conversation. It carries
forward only the deduped, most recent note blocks selected from the compacted
slice. If the compacted slice contains more notes than the cap, older or
duplicate notes are not re-emitted as preserved note blocks. They may still be
partly reflected in the `conv.range.summary`, but that summary is lossy.

This is why beacons are "small stones" across pruning: selected stones are
carried beside the compacted summary instead of disappearing into a generic
summary.

After compaction, the operational timeline for the compacted prefix should look
roughly like this:

```text
[conv.range.summary]
  Summary of the pruned turns.

  [INTERNAL MEMORY DIGEST]
  Active conversation preferences:
  - User prefers direct engineering explanations.

[react.note.preserved]
  path: ar:<summary_turn_id>.react.note.preserved.1
  source_path: fi:<old_turn>.outputs/internal_notes/rendering_refs.md
  note_tags: ["K", "D", "P"]
  text:
    [K] fi:<old_turn>.outputs/report.html - HTML source used for PDF rendering.
    [D] Rendering source refs must point at text source artifacts.
    [P] User prefers direct engineering explanations.
```

The `[P]` line is not the only thing preserved. The whole selected note block is
preserved as authored. `[P]` lines are copied additionally into the compact
digest because preferences are useful to keep visible even inside the short
range summary. `[D]`, `[K]`, `[S]`, and `[A]` remain available through the
preserved note block and through note indexing/search.

## How Progressive Summaries Treat Beacons

Progressive summary prompts also recognize `react.note` blocks. They ask the
summarizer to preserve tags into the right sections:

```text
[P] -> Constraints & Preferences
[D] -> Key Decisions
[S] -> Critical Context
[A] -> Progress / Done / Next Steps / Critical Context
[K] -> Critical Context, preserving path and explanation
```

This gives notes two survival routes:

```text
inline note block -> preserved note block
inline note text  -> summary content, when relevant
```

The preserved block is stronger because it remains a distinct object. The
summary path is lossy and should not be treated as source of truth.

## What `react.memsearch` Actually Searches

For the full retrieval-function story — hybrid semantic + lexical (BM25F) +
RRF fusion + recency lift, the `Retrieval-anchors:` contract that powers the
lexical side, scope semantics, and per-hit telemetry — see
[Conversational Memory Search](conversational-memory-search-README.md). The
short version follows.

`react.memsearch` is a recovery tool for prior turns. It supports topic
(hybrid semantic + lexical), timeline, ordinal, and temporal lookup. Its
current targets are:

```text
summary
user
assistant
attachment
notes
```

For semantic search, it searches the conversation index. `targets=["notes"]`
searches indexed rows tagged as `kind:react.note`; note rows are stored as
whole authored note blocks, with metadata tags such as `note_tag:K` and
`note_tag:D` for filtering/debugging. For timeline/ordinal temporal lookup, it
searches the turn catalog. It then returns handles such as:

```text
turn_id
turn_index_path = ar:<turn_id>.react.turn.index
working_summary_path = ws:<turn_id>.conv.working.summary
snippets[].path
```

The intended recovery path is:

```text
react.memsearch(query="...", targets=["summary","user","assistant","attachment","notes"])
  -> inspect returned turn_id / ws: / ar: refs
  -> react.read(["ws:<turn_id>.conv.working.summary"])
  -> if exact refs are missing, react.read(["ar:<turn_id>.react.turn.index"])
  -> read/pull exact fi:/tc:/so:/ar: refs
```

For `notes` hits, the tool result includes the note text directly as a snippet.
The returned path is still useful as a recovery handle when the exact source
artifact or preserved note path needs to be reopened.

Example semantic note search:

```text
react.memsearch(query="renderer refs", targets=["notes"], top_k=3)
```

The structured tool result available to ReAct has the full note block in
`last_tool_result`:

```json
[
  {
    "turn_id": "turn_prev",
    "turn_index_path": "ar:turn_prev.react.turn.index",
    "snippets": [
      {
        "role": "notes",
        "path": "fi:turn_prev.outputs/internal_notes/rendering.md",
        "text": "[K] fi:turn_prev.outputs/report.html - source for rendered PDF\n[D] Renderer refs point at text source artifacts.",
        "ts": "2026-05-05T19:37:00Z",
        "meta": {
          "channel": "internal",
          "note_tags": ["K", "D"]
        }
      }
    ],
    "score": 0.9,
    "sim_score": 0.89,
    "recency_score": 0.92,
    "matched_via_role": "artifact",
    "source_query": "renderer refs",
    "best_turn_id": "turn_prev"
  }
]
```

The timeline also receives a compact JSON summary result that lists snippet
handles without duplicating the note text:

```json
{
  "mode": "hybrid",
  "hits": [
    {
      "turn_id": "turn_prev",
      "turn_index_path": "ar:turn_prev.react.turn.index",
      "snippets": [
        {
          "path": "fi:turn_prev.outputs/internal_notes/rendering.md",
          "role": "notes",
          "ts": "2026-05-05T19:37:00Z"
        }
      ]
    }
  ],
  "tokens": 14
}
```

And each note snippet is materialized as a separate internal tool-result block:

```text
type: react.tool.result
mime: text/markdown
path: fi:turn_prev.outputs/internal_notes/rendering.md
text:
  [K] fi:turn_prev.outputs/report.html - source for rendered PDF
  [D] Renderer refs point at text source artifacts.
```

So ReAct sees both: a compact hit summary with handles, and the full authored
note text as a recovered snippet. The note is not split into separate `[K]` and
`[D]` results.

## Durable User Memory Is Different

Durable user memory lives in the SDK memory tables:

```text
user_memory_entries
user_memory_events
user_memory_aliases
```

It is meant for stable user-visible information that should survive across
conversations and be inspectable/editable by the user. This includes
preferences, facts, durable decisions, reusable artifact anchors, project
structure/specs, and milestones. It is not limited to `[P]`-style preferences.

Examples:

```text
The user lives in Wuppertal, Germany.
When summarizing engineering work, start with the practical impact.
The user prefers neutral examples in product documentation.
For project X, the canonical board brief template is me:<id> / fi:<path>.
The user-approved integration decision for product Y is to keep auth external.
```

These should not be stored only as `react.note` beacons, because beacons are
conversation recovery aids. Conversely, not every beacon should become durable
user memory. A `[K]` artifact anchor or a `[D]` implementation decision may be
important inside one project thread but wrong as cross-conversation user memory
unless the user or policy explicitly makes it durable.

## Recommended Separation

Use the mechanisms this way:

| Need | Mechanism |
| --- | --- |
| Continue a long active task after pruning | `conv.range.summary`, `conv.working.summary`, `react.note.preserved` |
| Remember a key artifact path in this conversation | `[K]` beacon with `scratchpad=true` |
| Remember a project decision made in this conversation | `[D]` beacon with `scratchpad=true` |
| Recover an old turn when no path is visible | `react.memsearch`, then `react.read(ar:<turn_id>.react.turn.index)` |
| Store a user-visible fact, preference, durable decision, reusable anchor, spec, or milestone for future conversations | Durable user memory |
| Let the user inspect/edit what is remembered | Memory widget |
| Keep a long private scratch file | `react.write(channel="internal", scratchpad=false)` |

## Boundaries To Keep

### First-Class Note Search

Beacons are indexed into the conversation index as note rows, so the agent can
search them directly:

```text
react.memsearch(query="renderer ref decision", targets=["notes", "summary"])
```

The index stores the full authored note block and adds metadata tags such as
`kind:react.note`, `chat:internal_note`, `visibility:internal`, and
`note_tag:<TAG>`. Search should return the note block together, not split one
multi-line note into separate fragments.

### Durable Memory Promotion

Do not automatically promote every internal beacon to durable user memory,
regardless of whether it is `[P]`, `[D]`, `[S]`, `[A]`, or `[K]`. Automatic
promotion would couple private agent recovery to user-visible product memory
and would produce noisy, sometimes wrong durable entries.

Better staged approach:

```text
react.note beacon
  -> optional memory proposal
  -> policy/reconciler/user approval
  -> durable user_memory_entries row
```

The future write surface can be one of:

```text
memory.propose
memory.record_signal
explicit widget action
reconciler proposal queue
```

Avoid adding a new agent protocol channel unless the current tool-based path is
insufficient. A separate `memo` channel would be attractive semantically, but it
adds another thing the model must remember. The simpler design is:

```text
react.write channel=internal scratchpad=true  -> conversation beacon
memory tool/widget/reconciler                 -> durable user memory
```

### Agent Instruction Hygiene

The ReAct instructions should keep the distinction short and concrete:

```text
Use Internal Memory Beacons for this conversation's durable recovery anchors.
Use durable user memory only for stable user-visible cross-conversation facts,
preferences, durable decisions, reusable anchors, specs, and milestones.
Use react.memsearch to recover old turns, not to search user memory.
Do not assume an internal file artifact is an inline note; use scratchpad=true
for short beacons that must survive pruning as notes.
```

That is enough signal. More detailed policy belongs in this document and in tool
docs, not in every prompt.

## Implementation References

Relevant code paths:

```text
kdcube_ai_app/apps/chat/sdk/skills/instructions/shared_instructions.py
  INTERNAL_NOTES_PRODUCER / INTERNAL_NOTES_CONSUMER

kdcube_ai_app/apps/chat/sdk/solutions/react/tools/write.py
  channel=internal + scratchpad=true creates react.note

kdcube_ai_app/apps/chat/sdk/solutions/react/compaction_memory.py
  preserves react.note / react.note.preserved and builds preference digest

kdcube_ai_app/apps/chat/sdk/solutions/react/timeline.py
  inserts conv.range.summary and preserved note blocks during compaction

kdcube_ai_app/apps/chat/sdk/tools/backends/summary/conv_progressive_summary.py
  tells summarizer how to preserve [P]/[D]/[S]/[A]/[K] notes

kdcube_ai_app/apps/chat/sdk/solutions/react/tools/memsearch.py
  searches prior turns and returns recovery refs

kdcube_ai_app/apps/chat/sdk/solutions/react/tools/read.py
  resolves ar:<turn_id>.react.turn.index and other logical refs

kdcube_ai_app/apps/chat/sdk/context/memory/
  durable user memory models, store, scoring, tools, widget APIs
```

## Bottom Line

We still need durable user memory, even with ReAct notes and memsearch.

ReAct notes are local recovery beacons for the conversation/task. They are
excellent for "reopen this artifact", "remember this decision", and "this
project milestone matters". Durable user memory is a product feature: stable
user-visible facts, preferences, durable decisions, reusable anchors, specs,
and milestones that should be visible to the user and available across
conversations.

The immediate engineering improvement is not to merge these systems. It is to
make beacons first-class in conversation search, then add an explicit promotion
path from beacon or user statement into durable user memory when policy allows.
