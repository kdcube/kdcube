---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/micro-agents-and-subagents-README.md
title: "Micro-Agents And Subagents"
summary: "How separate agent calls affect rendered context, prompt caching, and data handoff cost."
tags: ["sdk", "agents", "react", "context", "caching", "subagents"]
keywords: ["subagent", "micro-agent", "prompt cache", "system instruction", "handoff", "announce"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/subagents/subagents-runtime-bootstrap-and-reduce-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/context-caching-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/context-layout.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/context-progression.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/system-instruction-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-announce-README.md
---
# Micro-Agents And Subagents

A micro-agent or subagent is a separate model call. It may be launched from a
main ReAct turn, but it does not inherit the main agent's prompt cache for free.
It has its own system/instruction envelope, its own rendered context, and its
own cache story.

## Model Input Shape

Every agent call has the same outer shape:

```
MODEL INPUT

+----------------------------------------------------------------+
| SYSTEM / INSTRUCTION ENVELOPE                                  |
|  - runtime system instruction                                  |
|  - selected agent or subagent instruction                      |
|  - integration/domain instruction                              |
|  - optional custom suffixes                                    |
|  - text-rendered tool catalog                                  |
|  - text-rendered skill catalog                                 |
+----------------------------------------------------------------+
+----------------------------------------------------------------+
| RENDERED TIMELINE / MESSAGE BLOCKS                             |
|  - summaries                                                   |
|  - prior turns                                                 |
|  - stable current-turn blocks                                  |
|  - stable round/tool blocks                                    |
+----------------------------------------------------------------+
+----------------------------------------------------------------+
| CURRENT NON-CACHED TAIL                                        |
|  - sources pool, if rendered                                   |
|  - ANNOUNCE, if rendered                                       |
|  - other volatile current state                                |
+----------------------------------------------------------------+

Cache checkpoint pointers:
  CP-prev-turn -> boundary after a stable previous-turn block
  CP-pre-tail  -> boundary after an older stable round, when present
  CP-tail      -> boundary after the last stable rendered round
```

The system/instruction envelope is not a timeline block, but it is still part
of the exact bytes that define the prompt-cache prefix for ReAct. In the
current ReAct decision path, tool and skill catalogs are text rendered inside
this system/instruction envelope. React maps its cache checkpoints to explicit
Anthropic/Claude cache controls today; for other providers, this document still
describes prompt shape and token cost, but not an explicit provider cache
control contract.

Cache checkpoints are not blocks and do not "live inside" the timeline. They
are request/cache-control metadata that point to specific block boundaries in
the rendered timeline/message stream.

## Layered Prefix Reuse

Cache reuse stops at the first changed segment. In practice, the instruction
envelope often contains more than role text: tool catalogs, skill catalogs, and
admin or user-selected instruction fragments are also commonly rendered there.

```
Instruction envelope for one agent

  [strict protocol]                         stable
  [default React instruction]               stable
  [shared integration instruction]          stable
  ------------------------------------------------ first variable boundary
  [agent-specific instruction]              differs per agent/subagent
  [user-specific instruction suffix]        differs per user, if present
  [selected tool catalog]                   differs when tools differ
  [selected skill catalog]                  differs when skills differ
```

The rendered timeline is not instruction. It is the message stream that comes
after the instruction envelope:

```
Message/timeline stream

  [range summaries / prior turns]
  [current turn user blocks]
  [round/tool/result blocks]
  [assistant completion blocks]

Non-cached current tail

  [sources pool, if rendered]
  [ANNOUNCE, if rendered]
```

If `[selected tool catalog]` changes, then the timeline cache points after it
do not help for that changed request on providers where React controls prompt
caching, currently Anthropic/Claude. The reusable exact prefix stops above the
first changed segment. If a user can change tools or skills between turns, that
user's downstream cache is partitioned by each exact choice. If that choice can
change between rounds, the same turn can lose reuse after the changed segment.

This is the usual trap with subagents: each subagent has its own instruction
and often its own tool/skill catalog. That makes the subagent useful as a
separate specialist, but it also means the subagent has a separate cache story.

## Cache Points Point At Blocks

React chooses cache checkpoints by looking at the rendered timeline/message
blocks. The checkpoints point to positions after selected blocks:

```
[SYSTEM / INSTRUCTION ENVELOPE]

[TURN A header]
[TURN A blocks]
  ^
  CP-prev-turn points after the last stable block before the current turn

[TURN B header - current turn]
[round N-4 last block]
  ^
  CP-pre-tail can point here when there are enough rounds

[round N last block]
  ^
  CP-tail points at the last stable rendered round

[SOURCES POOL]     not cached
[ANNOUNCE]         not cached
```

The checkpoint positions are recomputed for each render. They point to the
currently rendered blocks; they are not durable objects inside those blocks.

The rendered timeline value can change in two different ways:

- Normal progression: new blocks are appended. Earlier stable blocks can keep
  the same value and remain useful cache targets.
- Cold/TTL/compaction progression: old history may be rendered as summaries or
  retrieval stubs. That changes the value of the rendered timeline prefix
  itself, even if the semantic conversation is the same.

## Snapshot A: Normal Progression

Same instruction value, same catalog value, timeline grows by appending blocks.
Earlier checkpoint values are still the same exact bytes.

```
LEFT: render at end of turn T1              RIGHT: render during turn T2
--------------------------------            --------------------------------
INSTRUCTION ENVELOPE                        INSTRUCTION ENVELOPE
  [strict protocol]                           [strict protocol]
  [default React instruction]                 [default React instruction]
  [agent instruction: main]                   [agent instruction: main]
  [user suffix: none]                         [user suffix: none]
  [tool catalog: web, python]                 [tool catalog: web, python]
  [skill catalog: base]                       [skill catalog: base]
  value: SYS_A                                value: SYS_A
  status: SAME                               status: SAME

MESSAGES / RENDERED TIMELINE                MESSAGES / RENDERED TIMELINE
  [TURN T0 summary]                          [TURN T0 summary]
  [TURN T1 user]                             [TURN T1 user]
  [TURN T1 tool result]                      [TURN T1 tool result]
  [TURN T1 assistant]                        [TURN T1 assistant]
        ^ CP-tail(T1)                              ^ CP-prev-turn(T2)
                                                same prefix value as
                                                left through CP-tail(T1)

                                              [TURN T2 user]
                                              [TURN T2 round 1]
                                              [TURN T2 assistant]
                                                    ^ CP-tail(T2)

NON-CACHED TAIL                             NON-CACHED TAIL
  [ANNOUNCE T1]                              [ANNOUNCE T2]
  [sources pool T1]                          [sources pool T2]
  status: not cached                         status: not cached
```

In the right render, `CP-prev-turn(T2)` points to the same exact prefix value
as `CP-tail(T1)` did in the left render. If the Anthropic cache entry is still
valid, that part can be read from cache. The new T2 blocks extend the prefix
and can create a newer tail checkpoint.

## Snapshot B: Different Instruction Content

Here the rendered timeline is unchanged, but the instruction content is
different because a suffix/catalog changed. That is enough to create a
different cache entry for every checkpoint downstream of the changed
instruction content.

```
LEFT: before suffix change                  RIGHT: after suffix change
--------------------------------            --------------------------------
INSTRUCTION ENVELOPE                        INSTRUCTION ENVELOPE
  [strict protocol]                           [strict protocol]
  [default React instruction]                 [default React instruction]
  [agent instruction: main]                   [agent instruction: main]
  [user suffix: A]                            [user suffix: B]     <-- DIFF
  [tool catalog: web, python]                 [tool catalog: web, python]
  [skill catalog: base]                       [skill catalog: base]
  value: SYS_A                                value: SYS_B
  status: DIFFERENT VALUE                     status: DIFFERENT VALUE

MESSAGES / RENDERED TIMELINE                MESSAGES / RENDERED TIMELINE
  [TURN T0 summary]                          [TURN T0 summary]
  [TURN T1 user]                             [TURN T1 user]
  [TURN T1 tool result]                      [TURN T1 tool result]
  [TURN T1 assistant]                        [TURN T1 assistant]
        ^ CP-tail(SYS_A + T1)                      ^ CP-tail(SYS_B + T1)

NON-CACHED TAIL                             NON-CACHED TAIL
  [ANNOUNCE]                                 [ANNOUNCE]
  status: not cached                         status: not cached
```

The two cache points point to the same relative message block boundary and can
cover the same number of tokens. They are still different cache entries:

```
left cache key/value:  SYS_A + messages through T1
right cache key/value: SYS_B + messages through T1
```

Cache identity is by exact prefix content, not by length, visual shape, or
checkpoint name.

Same length is only a corner case of the same rule: even if two instruction
prefixes have the same token count, they do not share cache when their content
is different.

## Main Agent Versus Subagent

```
Main agent turn:

  [SYS main agent]
  [cached main timeline prefix]
  [ANNOUNCE for current main-agent state]
  [current user request]


Subagent call launched from that turn:

  [SYS subagent]
  [handoff data prepared by main agent/runtime]
  [subtask request]
```

The subagent starts a different prefix because `[SYS subagent]` is different
from `[SYS main agent]`. The main agent cache does not make the subagent call
free. The subagent can still become cache-efficient if its own instruction and
stable context are reused across calls.

There are two common reuse cases for subagents:

- Same user: a main agent invokes the same configured subagent multiple times
  for that user. The subagent cache can warm for that user's repeated calls if
  the subagent instruction, tool catalog, skill catalog, and handoff prefix are
  stable.
- Different users: a ready-made/static subagent is used by multiple users with
  identical instruction/catalog bytes. Those users can share the common
  subagent prefix until the first user-specific segment. This is valuable for
  short Anthropic cache TTLs because multiple users can keep the same prefix
  hot.

## Cache Sharing Matrix

| Situation | Cache result | Reason |
| --- | --- | --- |
| Same user, same agent, same system/instruction envelope, stable timeline prefix | Same-user cache hit is possible | Prefix bytes match for that user's later turns/rounds. |
| Different users, same static instruction/catalog prefix | Cross-user prefix reuse is possible | Prefix bytes match until the first user-specific segment. |
| Same timeline, different agent instruction | Separate cache story | Prefix starts with different instruction bytes. |
| Same agent, per-user suffix in system/instruction | No cross-user sharing | Each user's prefix bytes differ. |
| Same agent, user selects a different tool catalog | Cache splits by exact catalog | Timeline is downstream of the catalog text. |
| Same user changes tools/skills between rounds | Same-turn downstream cache miss | The prefix changes before the timeline checkpoint. |
| Same user reuses the same subagent with stable instruction and stable handoff prefix | Subagent cache can warm for that user | The subagent has its own reusable prefix. |
| Different users use the same static subagent prefix | Cross-user subagent prefix reuse is possible | The shared subagent prefix can stay hot across users. |
| Current board/state placed in ANNOUNCE | Stable prefix can remain hot | ANNOUNCE is a non-cached tail. |
| Current board/state appended to system instruction | Cache churn | Every state change changes prefix bytes. |
| Full source data copied into every subagent prompt | High token/cache-write cost | The subagent prefix grows and changes. |
| Source refs passed and pulled only when needed | Lower prompt cost, extra tool latency | Data enters the workspace on demand. |

## Why ANNOUNCE Exists

ANNOUNCE is for current derived state that should be visible to the model now
but should not become part of the stable cached prefix.

```
Good shape:

  [SYS stable role/instructions]
  [cached timeline prefix]
  [ANNOUNCE current board/task/live state]      not cached
  [current user message]


Bad shape:

  [SYS stable role/instructions + current board/task/live state]
  [timeline prefix]
  [current user message]
```

The bad shape may work semantically, but it rewrites the cache prefix whenever
the current state changes. If that state is user-specific, it also prevents
cross-user cache sharing.

## Data Handoff Options

When a subagent needs data from the main agent or from an external object, the
system must choose how to hand it over. None of these options are free.

| Handoff | What the subagent receives | Cost |
| --- | --- | --- |
| Direct task text | Small instruction or task payload | Fast, but only works when enough context fits directly. |
| Precise summary | Generated summary of the needed state | Adds summary latency/model cost; saves subagent tokens. |
| Object refs | Paths such as `fi:...`, `mem:...`, `cnv:...` | Small prompt; subagent must pull/read through runtime tools when needed. |
| Timeline slice | Selected blocks copied from main timeline | Accurate but can be expensive if the slice is large. |
| Full transcript copy | Large block of prior context | Easiest to wire, usually worst for cache and tokens. |

Precise handoff is a real mechanism, not magic. Something must decide which
facts, refs, or blocks the subagent needs, and that selection adds either
latency, tool work, or model work.

## Visual Progression

```
Step 1: main agent receives user request

  [SYS main]
  [cached main history]
  [ANNOUNCE current state]
  [USER: "Analyze this issue"]


Step 2: main agent decides to call subagent

  It must prepare one of:
    - a small direct task
    - a precise summary
    - object refs
    - a selected timeline slice


Step 3: subagent runs

  [SYS subagent]
  [prepared handoff]
  [SUBTASK: "Check the issue for missing acceptance criteria"]


Step 4: subagent result returns to main turn

  [TOOL/SUBAGENT RESULT]
  [main agent continues with existing main timeline]
```

There are two cache stories in this picture:

```
main cache story:
  [SYS main] + [main rendered prefix] + checkpoints

subagent cache story:
  [SYS subagent] + [subagent rendered prefix] + checkpoints
```

## Rules

1. Keep role, policy, and invariant behavior in stable system/instruction
   blocks.
2. Do not put per-turn state into system/instruction unless cache churn is
   acceptable.
3. Put volatile current state in ANNOUNCE or an equivalent non-cached tail.
4. Avoid per-user system suffixes when cross-user cache sharing matters.
5. Treat each subagent as another model request with its own cached prefix.
6. Pass refs or precise summaries to subagents instead of blindly copying large
   histories.
7. If a subagent needs exact bytes from an object, give it refs and make sure
   the runtime exposes the needed pull/read tools.

## Practical Consequence

Adding a subagent can improve reasoning structure, but it can also add:

- a new system/instruction prefix to cache
- a new cache write for that subagent's stable context
- extra handoff latency
- extra tool or model calls to prepare the handoff
- poorer cache sharing if custom instructions are user-specific

Use subagents when the separation has a real benefit. Keep their instruction
stable, keep their handoff small and precise, and avoid embedding fast-changing
state into their system messages.
