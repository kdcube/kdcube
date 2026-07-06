---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/context-caching-README.md
title: "Context Caching"
summary: "Dual checkpoint caching strategy for stable prefixes and growing tails."
tags: ["sdk", "agents", "react", "context", "caching"]
keywords: ["cache checkpoints", "prefix", "tail", "Anthropic cache", "cache TTL", "cold turn marker", "cache_cold_turn", "selection change", "cold-cache policy", "cache_last_touch_at"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/how/how-to-construct-react-agent-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/user-settings/user-settings-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/compaction-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/context-browser-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/context-layout.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/context-progression.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/micro-agents-and-subagents-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/system-instruction-README.md
---
# Context Caching (Dual Checkpoints, Round-Based)

The context browser uses **two cache checkpoints** to keep stable prefixes cached while allowing
the tail to grow. This reduces cache invalidations when the timeline grows or when older blocks
are compacted.

## Full Model Request Shape
React builds the same logical model input shape for every provider. Cache
points are placed on rendered timeline blocks, but the prefix that matters for
caching starts earlier, at the system/instruction envelope. In the current
ReAct decision path, tools are not exposed as provider-native tool definitions.
React renders the tool catalog and skill catalog as text near the bottom of the
system/instruction envelope. The rendered timeline is then sent as message
content.

```
MODEL REQUEST

+----------------------------------------------------------------+
| SYSTEM / INSTRUCTION ENVELOPE                                  |  prefix starts here
|  - base ReAct runtime instruction                              |
|  - selected agent instruction                                  |
|  - bundle/domain instruction suffixes                          |
|  - optional user/runtime custom instruction suffixes            |
|  - text-rendered tool catalog                                  |
|  - text-rendered skill catalog                                 |
+----------------------------------------------------------------+
+----------------------------------------------------------------+
| MESSAGES: RENDERED TIMELINE PREFIX                             |  stable prefix
|  - summaries and prior turns                                   |
|  - stable current-turn blocks                                  |
|  - stable round/tool blocks                                    |
|                                                                |
|  cache checkpoints are attached inside this block stream        |
+----------------------------------------------------------------+
+----------------------------------------------------------------+
| CURRENT MOVING TAIL                                            |  not cached
|  - sources pool, if rendered                                   |
|  - ANNOUNCE, if rendered                                       |
|  - newest volatile same-turn state                             |
+----------------------------------------------------------------+
```

The system/instruction envelope is not a timeline block, but it is still part
of the bytes that define the prompt-cache prefix for ReAct. Changing the
instruction body, custom suffixes, tool catalog, or skill catalog creates a
different downstream cache story even if the rendered timeline is otherwise
identical.

Provider note: today, React's explicit cache-control placement is designed for
Anthropic/Claude prompt caching. Other providers still receive the same prompt
layout, but React does not currently assume or control equivalent provider-side
prompt-cache behavior there.

Examples:

| Change | Cache effect |
| --- | --- |
| Same user, same agent instruction, same cached timeline prefix | Reused for later turns or rounds of that same user/conversation. |
| Different users, same common prefix before any user-specific segment | Shared across users only up to that identical common prefix. |
| Per-user instruction suffix in the system message | Prefix differs per user; cross-user cache sharing is lost. |
| Different micro-agent or subagent instruction | New prefix; the subagent has its own cache story. |
| User-selected tool catalog in the instruction envelope | Cache is shared only among requests with the same selected catalog. |
| User-selected skill catalog in the instruction envelope | Cache is shared only among requests with the same selected skill set. |
| Tool/skill choice changes between rounds | Even that user's same-turn cache after the changed segment is invalidated. |
| Per-turn board/state placed in ANNOUNCE | Not cached; avoids stale cache and avoids rewriting the stable prefix. |
| Per-turn board/state inserted into system/instruction | Prefix changes; expensive cache churn. |

This is why React uses ANNOUNCE for current derived state. ANNOUNCE is paid as
tail tokens on each call where it is present, but it does not rewrite the
stable cached prefix and does not make stale state reusable.

## Reuse Scopes
Prompt-cache reuse has three useful scopes. They are different and should not
be described with one vague "shared" label.

| Scope | When it works | Why it matters |
| --- | --- | --- |
| Same user, same agent | Later turns or later rounds use the same instruction/catalog prefix and stable timeline prefix. | This is the normal per-conversation cache benefit. |
| Same user, same subagent | The main agent repeatedly invokes the same configured subagent with the same subagent instruction/catalog prefix. | The subagent has a separate cache story, but it can still warm for this user. |
| Different users | All bytes before the cache point are identical across those users. | More users can keep Anthropic's short-lived cache hot, for example during a 5 minute cache window. |

Cross-user reuse is especially valuable for short-lived Anthropic caches because
traffic from multiple users can keep the shared prefix warm. It only works for
the prefix before the first user-specific segment. If per-user instructions,
user-selected tools, user-selected skills, or user-specific data are placed
before the timeline, all downstream cache points become user-specific.

## Prefix Boundaries
A prompt cache reuses an exact prefix. If bytes change in the instruction
envelope, the reusable prefix stops before that changed segment. Everything
after the changed segment must be treated as a new cache write, even when the
later timeline bytes are identical.

```
Shared across all users:

  [strict protocol]
  [stable default ReAct instruction]
  [stable common policy]
  [CACHE CAN BE SHARED UP TO HERE]

User A request:

  [user A custom instruction suffix]       first changed segment
  [user A selected tool catalog]
  [user A selected skill catalog]
  [timeline prefix]
  [ANNOUNCE tail]

User B request:

  [user B custom instruction suffix]       different bytes here
  [user B selected tool catalog]
  [user B selected skill catalog]
  [same timeline prefix]
  [ANNOUNCE tail]
```

In that example, User A and User B can share only the stable common prefix.
The timeline prefix is downstream of user-specific instruction/catalog bytes,
so the timeline cache is not shared across users.

The same rule applies within one user:

```
Round 1:
  [stable common instruction]
  [user instruction suffix]
  [tools: web, python]
  [timeline through round 1]               cacheable for this exact prefix

Round 2 after user/tool choice changes:
  [stable common instruction]
  [user instruction suffix]
  [tools: web, python, memory]             changed segment
  [same timeline through round 1]          downstream cache miss
```

This is why changing tool catalogs, skill catalogs, or user-selected
instruction fragments between rounds is more expensive than changing ANNOUNCE.
The model may still reuse bytes before the changed segment, but it cannot reuse
cache points placed after that segment.

## Anthropic Cache-Control Mapping
Anthropic/Claude prompt caching is hierarchical:

```
[tools] -> [system] -> [messages]
```

The current ReAct decision path uses the `system` and `messages` levels for
the decision prompt: ReAct's tool and skill catalogs are rendered as text inside
`system`, not as provider-native `tools`. The `tools` level matters for other
Claude integrations that use provider-native tools, but it is not how the
current ReAct decision agent exposes its tool catalog.

For Anthropic cache controls, changes invalidate that level and everything
after it:

| Changed segment | Tools cache | System cache | Messages cache |
| --- | --- | --- | --- |
| Provider-native tool definitions, when used by a different integration | miss | miss | miss |
| System instruction, text-rendered catalogs, web/citation/speed toggles | hit if tools unchanged | miss | miss |
| Rendered timeline/messages | hit if tools and system unchanged | hit if system unchanged | miss after changed message block |

For ReAct, user-selected tools or skills change the text-rendered catalog in
`system`. That keeps the Anthropic `tools` level irrelevant/empty for this
path, but it still invalidates the downstream `system` and `messages` cache
after the catalog segment.

Anthropic cache writes happen at cache breakpoints. Cache reads look for
earlier cache entries that were actually written, not merely for stable text.
For explicit block-level caching, Anthropic looks back only a bounded number of
blocks from a breakpoint. Therefore, React places breakpoints on content that
stays identical across the requests that should reuse it, and keeps another
breakpoint near the growing timeline tail when needed.

## Mixed 1h And 5m TTLs
This section describes Anthropic cache-control behavior. When 1-hour and
5-minute cache controls are mixed, longer TTL cache entries must appear before
shorter TTL entries:

```
[cache read A][1h cache write B-A][5m cache write C-B][rest of prompt]
```

Anthropic computes:

| Position | Meaning |
| --- | --- |
| `A` | Highest cache hit position, or zero if there is no hit. |
| `B` | Highest 1-hour cache-control position after `A`, or `A` if none exists. |
| `C` | Last cache-control position. |

Billing follows the same spatial split:

```
tokens <= A        cache read
A < tokens <= B    1h cache write
B < tokens <= C    5m cache write
tokens > C         regular prompt input
```

This is the meaning of the 1h/5m diagram: a 5-minute hit at `C` can make the
whole prefix before it a cache read; if the highest hit is earlier, then the
missed 1-hour and 5-minute sections are written again. If `A` is zero, the
request starts with cache writes rather than a cache read.

## Strategy
- **Previous-turn checkpoint**: points after the **last block before the current turn header**, if any.
- **Tail checkpoint**: points after the last stable **round**.
- **Additional checkpoint (pre-tail)**: points `offset_rounds` **before** the tail checkpoint,
  only when there are at least `min_rounds` rounds.

This yields **three** cache anchors in the stable prefix when a previous turn exists.
If the tail cache breaks, the additional checkpoint still provides a usable cached prefix.

## Schematic (cache points)
```
SYSTEM / INSTRUCTION ENVELOPE          => part of every cache prefix

... previous turns ...
[TURN turn_A header]
  ... blocks ...
  (last block of turn_A)                => [CP: prev-turn]
[TURN turn_B header]  <-- current turn
  ... round N-5 ...
  ... round N-4 (last block)           => [CP: pre-tail]  (offset_rounds=4)
  ... round N-3 ...
  ... round N-2 ...
  ... round N-1 ...
  ... round N (last block)             => [CP: tail]
```

With `cache_point_offset_rounds=4`, the **pre-tail checkpoint points after the
last block of round N-4** (counting from the tail), when enough rounds exist.

### Hide interaction
If `react.hide` is used and the **pre‑tail checkpoint is above the previous‑turn checkpoint**,
the previous‑turn checkpoint is **reset to the pre‑tail checkpoint** for the remainder of the turn.
This prevents hide operations from being constrained by a cache point that is older than the
current pre‑tail boundary.

## Rounds
A **round** is keyed by `tool_call_id`, plus a **final completion round** that contains:
`assistant.completion`, `stage.suggested_followups`, `react.turn.finalize`,
`react.exit`, `react.state`.

If one turn produces multiple visible `assistant.completion` blocks, they still belong to that
turn's final completion family. Caching remains round-based; the latest unsuffixed completion path
is preserved separately as the stable alias.

Rounds are counted across the **visible timeline slice**, which may include blocks
from previous turns (post‑compaction). Cache points are **not** restricted to the
current turn.

## Parameters
Configured on `RuntimeCtx.cache`:
- `cache_point_min_rounds`: minimum **total** rounds required before placing the additional checkpoint (default: `2`)
- `cache_point_offset_rounds`: distance (in rounds) from tail to the additional checkpoint once placed (default: `4`)

Context-size and TTL-pruning defaults are configured separately:

- `ai.react.context_max_tokens` / `AI_REACT_CONTEXT_MAX_TOKENS`: default hard
  render budget used before sending to the model when a bundle does not set
  `max_tokens` (default: `80000`)
- `ai.react.cache_keep_recent_turns` / `AI_REACT_CACHE_KEEP_RECENT_TURNS`:
  number of recent turns kept visible after TTL pruning (default: `6`)
- `ai.react.cache_keep_recent_intact_turns` /
  `AI_REACT_CACHE_KEEP_RECENT_INTACT_TURNS`: newest turns kept untrimmed
  during TTL pruning (default: `1`)
- `cache_truncation_replacement_max_tokens` on `RuntimeCtx.session`:
  maximum size for automatic TTL-generated replacement text (default: `240`
  tokens). This is intentionally separate from explicit `react.hide`, which
  preserves the requested replacement exactly.

## Application
- Cache points point at block boundaries in the **stable timeline**
  (post-compaction, pre-tail).
- Sources/announce are appended after rendering and remain uncached.
- The system/instruction envelope is outside the timeline, but it is still in
  the exact prompt prefix before every timeline cache point. Keep it stable
  when cache sharing matters.
- If `cache_last=True`, the last rendered block is additionally cached (cache points still apply).
- Live external events (`user.followup`, `user.steer`) are folded into the same timeline
  stream while a turn is active. They typically invalidate only the tail portion; the
  stable prefix remains reusable because the previous-turn and pre-tail checkpoints stay
  anchored earlier in the visible stream.

## Subagents And Cache Cost
A subagent or micro-agent is a separate model request with its own
system/instruction envelope. Even if it is invoked from an already-hot main
agent turn, its cache is separate:

```
Main agent call:
  [SYS main] [cached main timeline prefix] [ANNOUNCE main] [current user]

Subagent call:
  [SYS subagent] [subagent handoff/context] [subtask]
```

The subagent does not inherit the main agent prompt cache. It can only get a
hot cache if the subagent's own system/instruction envelope and its own cached
prefix are stable across calls.

Passing data to a subagent also has a cost:

| Handoff method | Cost profile |
| --- | --- |
| Copy a full timeline slice | Fast to implement, high token/cache-write cost. |
| Generate a precise summary first | Lower subagent tokens, adds latency/model cost. |
| Pass object refs and let the subagent pull/read | Lower prompt size, adds resolver/tool latency. |
| Put changing facts in the subagent system instruction | Worst for cache reuse; every change is a new prefix. |

Use stable subagent instructions for role and invariant behavior. Put changing
task data in the current request, ANNOUNCE-like tail, or explicit handoff
blocks.

## Selection Changes, The Cold-Turn Marker, And The User-Held Policy

Per-user selection changes are the main runtime source of deliberate cache
invalidation, and the platform makes their cost visible and governable:

- **Any tool/skill/MCP/namespace toggle colds the ENTIRE prompt for one
  turn.** The decision call sends ONE cached system block (protocol +
  instructions + text-rendered tool catalog + skill gallery + namespace
  intros) ahead of the timeline messages; because system precedes messages, a
  changed catalog segment invalidates every downstream cache point — the
  history cannot survive a catalog change. The next turn re-writes the cache
  and caching resumes.
- **A model switch colds everything too, differently:** provider prompt
  caches are per model, so the newly picked model starts with no cache at all.
- **The cold-turn marker.** Each conversation persists the last-APPLIED
  selection snapshot alongside the warmness signal (`cache_last_touch_at` +
  the stored TTL) in the timeline payload. When a changed selection applies on
  a warm conversation, the runtime sets `RuntimeCtx.cold_turn_marker`: the
  agent sees a one-line ANNOUNCE `[CACHE]` section, the decision call's
  accounting metadata carries `cache_cold_turn`, and a `kdcube.react.cache`
  log line correlates the change with the next cache attempt. Accounting
  already records `cache_creation_tokens` / `cache_read_tokens`; the marker
  joins that cache-write premium to the causing action — one identifiable
  component within the turn's spend sum (a turn's cost is always the sum of
  the spendings inside it).
- **The user decides when to pay.** The user pays for the cache, so the user
  holds the selection-change policy (`accept`, `confirm` — the platform
  default, `defer_cold`, `defer_conversation`; admin config bounds the set).
  Under `confirm` the composer menu turns a costly change into an inline
  choice (apply now / from the next conversation / when the cache is cold —
  where applying is free); deferred changes park as a pending delta the
  runtime promotes when the trigger fires. The selection layer itself is owned
  by [How To Construct A ReAct Agent](./how/how-to-construct-react-agent-README.md);
  the record and store semantics by the
  [User Settings Solution](../../solutions/user-settings/user-settings-solution-README.md).

## Cold Cache Mitigation
When a prompt cache expires on a provider where React uses explicit cache
controls today, currently Anthropic/Claude, rebuilding a very large prefix is
expensive. React mitigates that by combining:

- TTL pruning: historical turns with `conv.working.summary` blocks collapse to
  those compact semantic cards; multiple summaries from one turn are preserved.
  Historical turns without summaries fall back to retrieval-index rows with
  logical paths and tiny semantic hints
- TTL replacement bounding: automatic replacement text is capped before
  `Timeline.hide_paths(...)` is called, preventing a cold-cache pruning pass from
  expanding the prompt with verbose tool payloads. The guard applies both to
  absolute oversize replacements and to material growth over the original block.
- turn-status collapse: React finalization internals render as one compact
  `[TURN STATUS]` card
- round-scaffolding suppression: hidden `react.round.start`,
  `react.thinking`, `react.notes`, `react.notice`, and
  `stage.suggested_followups` blocks are not rendered as separate pruned refs
- hard compaction: the rendered model view is capped by `context_max_tokens`
- source/artifact retrieval: full content stays available via `react.read(path)`

The intended model-view shape after a cold cache is therefore a compact summary
plus recent working tail, not a replay of the entire conversation history.

The cold-cache mitigation depends on the model actually emitting useful
`summary` channel blocks at final/exit answer attempts. Without working
summaries, historical turns render as retrieval-index stubs; that is safe for
tokens, but weaker semantically.

## Implementation
See `kdcube_ai_app/apps/chat/sdk/solutions/react/caching.py`.

## Eviction Rule
Eviction is only allowed **after** the additional checkpoint. Use
`is_before_pre_tail_cache(...)` or `cache_points_for_blocks(...)` from
`src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/solutions/react/caching.py` to validate.
