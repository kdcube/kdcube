---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/memory/conversational-memory-search-README.md
title: "Conversational Memory Search (react.memsearch)"
summary: "Cross-conversation searchable memory of agent activity: what is indexed, how `react.memsearch` scopes and ranks it, the hybrid semantic+lexical+recency retrieval, and the Retrieval-anchors contract that makes literal phrasings findable."
tags: ["sdk", "memory", "memsearch", "retrieval", "hybrid-search", "bm25", "rrf", "pgvector"]
keywords: ["react.memsearch", "conversational memory", "cross-conversation search", "hybrid retrieval", "BM25F", "Reciprocal Rank Fusion", "Retrieval-anchors", "search_tsv", "ts_rank_cd", "scope user", "working summary", "turn catalog"]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/memory/how-react-remembers-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/memory/user-memories-overview-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/memory/user-memories-react-integration-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/agents/react/react-tools-README.md
---
# Conversational Memory Search

Three distinct districts of memory exist in this system. This document is about
the middle one:

```text
current visible timeline
  what is in the agent's context right now

conversational memory index  ← this document
  every prior turn the user and agent produced, indexed and searchable
  managed by the agent as a byproduct of operation, not curated
  queried via react.memsearch

durable user memory
  user-visible curated facts, preferences, decisions
  separate Postgres tables, separate tools, separate widget
```

The conversational memory index is not edited by a user, not announced as a
hotset, and not promoted into product surfaces. It is the persisted activity of
the agent: every user prompt, every assistant completion, every working summary,
every internal note, every user attachment — written into `conv_messages` as the
agent operates, and made retrievable later when the agent itself decides it
needs to recover something it cannot already see.

`react.memsearch` is the only access path. There is no other read API and no
widget. Bundle code is not expected to query this index directly.

From the agent's point of view, this conversational memory is one of the user's
**memory realms** — alongside durable memories (`mem`) and context boards / the
pin board (`cnv`). The three are semantically and originally different sources —
what was actually said, versus curated facts, versus things pinned for later —
but all of them are districts of one thing: the user's memory. When it is worth
recalling what the user said, saved, or set aside, all three are candidates;
`react.memsearch` covers this one (and searches what the user said, what the
assistant said, and the user's uploaded attachments — not bot-produced files).

## Why This Exists

The agent's context window is finite. Conversations grow past it. Compaction
prunes the timeline tail, summaries replace pruned ranges, and the handles
that *were* visible (`fi:`, `ar:`, `tc:`, `so:` refs) disappear from view.
Without a way to recover them, the agent loses access to its own prior work.

There are three failure modes that motivated this tool:

1. **The agent needs an earlier turn's artifact and cannot see the path.**
   It knows the topic, but the ref is below the compaction line. Without
   `react.memsearch` it would have to ask the user, or invent a plausible
   path and fail.
2. **The user re-quotes a literal string from a prior turn.** A filename,
   an error message, an exact title — something the user might paste back
   verbatim. Semantic similarity is poor at matching literal tokens: an
   embedding of "Forecast-Q2-2026.xlsx" does not robustly cluster with an
   embedding of a paraphrase that mentions the file. Pure semantic recall
   fails this case routinely.
3. **The user references a different conversation entirely.** "What did we
   decide last week?" or "you helped me with X two days ago." Without
   cross-conversation scope, the agent has no recourse but to ask the user
   to re-explain.

The engineering response to each:

| Failure mode | Mechanism |
| --- | --- |
| Pruned-but-needed turn | Working summaries persisted per turn with `ws:` handles; semantic search over them |
| Literal-string recovery | `Retrieval-anchors:` block in summaries → BM25F lexical index on a `search_tsv` column → `ts_rank_cd` ranking |
| Cross-conversation | `scope="user"` parameter; `fi:conv_<id>.turn_<id>...` path resolution |

Hybrid retrieval (semantic + lexical + RRF) is not a refinement; it is what
makes both the paraphrase case and the literal-string case work in one tool.
Either alone leaves a class of queries silently broken.

For the cross-district picture of how this memory district relates to the
visible timeline and durable user memory, see
[How ReAct Remembers — the big picture](how-react-remembers-README.md). The
diagram below zooms in on what happens *inside* this district: how one turn's
output becomes a retrievable row, and how a future `react.memsearch` call
finds it.

```text
TURN N (the producing turn)                       TURN N+K (the recovering turn)
---------------------------                       -------------------------------

  Agent emits in <channel:summary>:                 Agent calls react.memsearch:
  +---------------------------+                     +--------------------------+
  | Goal: ...                 |                     | query="openpyxl error"   |
  | Outcome: ...              |                     | targets=["summary"]      |
  | Key facts: ...            |                     +-----------+--------------+
  | Refs: ...                 |                                 |
  | Retrieval-anchors:        |                                 v
  |   phrases: [...]          |               +----------------------------------+
  |   entities: [...]         |               |  search_context (rrf_hybrid)     |
  +-------------+-------------+               |                                  |
                |                              |  per target, run in parallel:   |
                | Runtime catches              |                                  |
                | the working-summary          |  +-------------+ +-----------+ |
                | block at turn end            |  | semantic    | | lexical   | |
                v                              |  | (pgvector,  | | (ts_rank_ | |
  +---------------------------+                |  |  cosine on  | |  cd on    | |
  | parse_retrieval_anchors() |                |  |  embedding) | |  search_  | |
  |   anchors.py              |                |  |             | |  tsv)     | |
  +-------------+-------------+                |  +------+------+ +-----+-----+ |
                |                              |         |              |       |
                | flattens block:              |         v              v       |
                | phrases get "quoted",        |  +-----------+   +-----------+ |
                | entities stay bare           |  | sem_rank  |   | lex_rank  | |
                v                              |  | per turn  |   | per turn  | |
  +---------------------------+                |  +-----+-----+   +-----+-----+ |
  | ConvIndex.add_message     |                |        |               |       |
  |   anchors_text=...        |                |        +-------+-------+       |
  |   text=full summary text  |                |                |               |
  |   embedding=embed(text)   |                |                v               |
  |   tags=[kind:working.     |                |  rrf_score = 1/(60+sem_rank)   |
  |        summary, ...]      |                |             + 1/(60+lex_rank)  |
  |   ts, ttl_days            |                |                |               |
  +-------------+-------------+                |                v               |
                |                              |  final = rrf * (1 + 1.0*rec)   |
                | INSERT INTO conv_messages    |                                  |
                v                              +----------------+-----------------+
  +---------------------------+                                 |
  |  conv_messages row        |                                 |
  |  (Postgres)               |   <-- read --------------+      |
  |                           |                          |      |
  |  generated automatically: |                          |      |
  |    search_tsv =           |                          |      |
  |      setweight(A,         |                          |      |
  |        to_tsvector(       |                          |      |
  |          'simple',        |                          |      |
  |          anchors_text)) ||                           |      |
  |      setweight(B,         |                          |      |
  |        to_tsvector(       |                          |      |
  |          'english',       |                          |      |
  |          text))           |                          |      |
  |                           |                          |      |
  |  indexed by GIN over      |                          |      |
  |    search_tsv             |                          |      |
  |  indexed by ivfflat over  |                          |      |
  |    embedding              |                          |      |
  +-------------+-------------+                          |      |
                |                                        |      |
                +----------------------------------------+      |
                                                                v
                                                  hits returned to agent:
                                                  +--------------------------+
                                                  | turn_id, ws: path,       |
                                                  | ar: turn index path,     |
                                                  | snippets, score,         |
                                                  | rrf_score, sem_rank,     |
                                                  | lex_rank, primary_source |
                                                  +-----------+--------------+
                                                              |
                                                              v
                                                  agent then react.read's
                                                  the returned refs to load
                                                  full content into context
```

The producing-turn column is the **write contract**: the agent must emit
anchors and the runtime must persist them correctly. The recovering-turn
column is the **read contract**: the runtime must run both retrievers and
fuse rank-wise. The middle row is the **storage contract**: Postgres derives
`search_tsv` from anchors_text + text on every write, so as long as those two
columns are correct the lexical side is always in sync.

## What Is Indexed

A row in `conv_messages` corresponds to one indexed unit. The agent does not
write to this table directly; the runtime persists rows as a side effect of
turn execution. The kinds of rows that exist:

| Tag pattern | What it holds | Where it comes from |
| --- | --- | --- |
| `chat:user` | The user's prompt / follow-up / steer text for a turn | Turn ingress |
| `chat:assistant` | The assistant's final completion text for a turn | Completion persistence |
| `chat:assistant` + `kind:working.summary` | The agent's `conv.working.summary` block for the turn | Working-summary persistence |
| `chat:internal_note` + `kind:react.note` | The text of a `react.note` block (an inline internal beacon) | `react.write(channel="internal", scratchpad=true)` |
| `artifact:user.attachment` | A user-uploaded attachment's text/summary | Attachment ingestion |
| `artifact:turn.log` | The persisted per-turn log artifact | Turn finalization |

Every row carries:

- `user_id`, `conversation_id`, `bundle_id`, `turn_id`
- `text` (the body)
- `embedding` (semantic vector, when applicable)
- `anchors_text` (verbatim phrases + high-IDF entities — see below)
- `search_tsv` (generated `tsvector`, BM25F-style field weighting)
- `tags` (the patterns above + topic tags + bundle/turn tags)
- `ts` (timestamp), `ttl_days` (default 365)

Rows past their TTL are filtered out of every search. No background job deletes
them; expiration is applied at query time.

## Required Index State

The hybrid retrieval has four moving parts, each with a column it depends on.
If a column is missing or wrong, the corresponding part silently degrades.
Knowing what must be populated by whom is the easiest way to debug "the agent
cannot find a turn it clearly produced":

| Part of retrieval | Column required | Who fills it | What happens if missing |
| --- | --- | --- | --- |
| Target filtering (`targets=[...]`) | `tags` | Runtime, at persistence | Row is invisible to `targets`; the agent will never see it |
| Semantic recall | `embedding` (`VECTOR(1536)`) | Runtime, calls embed at persistence | Row drops out of the semantic path; lexical can still match |
| Body lexical recall | `text` | Runtime, from the original block | No body recall; only anchor-side lexical (if any) |
| Anchor lexical recall (BM25F weight A) | `anchors_text` | Parsed from the model's `Retrieval-anchors:` block at working-summary persistence | Lexical degrades to body-only weighting; literal-phrase recovery becomes unreliable |
| Lexical scoring index | `search_tsv` | Postgres (generated column) | Never missing — Postgres recomputes on every write to `text` or `anchors_text` |
| TTL / recency | `ts`, `ttl_days` | Runtime, at persistence | Row may be filtered as expired, or its recency factor will be wrong |
| Cross-conversation scope | `user_id`, `conversation_id` | Runtime, at persistence | `scope="user"` cannot find it; `scope="conversation"` may fail too |

The two columns that are routinely **the cause of "memsearch did not find the
turn I expected"** are `anchors_text` and `tags`. The first because it is
parsed from a structured block the agent must emit correctly (see below); the
second because each target relies on a specific tag pattern and a missing tag
makes the row invisible to that target.

The `search_tsv` column is generated — it cannot be set wrong by application
code. Its weighting is fixed at `setweight('A', anchors_text)` ||
`setweight('B', text)`. Changing the weighting requires a schema migration.

## Scopes

The tool exposes two scopes:

```text
scope = "conversation"   (default)
  Only rows whose conversation_id equals the current conversation.
  This is the safe default. It cannot leak across conversations.

scope = "user"
  Rows for the same user_id, across all of that user's conversations,
  inside the same tenant + project + storage boundary.
```

The cross-conversation scope is what makes this a *district* rather than just a
conversation cache. The same user's prior conversations remain searchable as
long as their rows are within TTL. A returned `fi:` path that begins with
`fi:conv_<id>.turn_<id>...` indicates the artifact lives in another
conversation; passing it to `react.read` / `react.pull` resolves against that
conversation's storage automatically.

`scope="user"` does not cross tenants, projects, or storage backends. It is a
per-user search, not a global one.

## Targets

The agent picks what kinds of snippets it wants back via `targets`:

| Target | Picks rows tagged | Useful for |
| --- | --- | --- |
| `summary` | `kind:working.summary` | First choice for recovery — goal/outcome/refs of a prior turn |
| `user` | `chat:user`, `artifact:user.attachment` | Finding a turn by what the user said or attached |
| `assistant` | `chat:assistant` (non-summary) | Finding a turn by what the agent answered |
| `attachment` | `artifact:user.attachment` and inline followup attachments | Finding a turn by an attached file |
| `notes` | `kind:react.note` | Finding an internal beacon the agent left in a prior turn |

`targets` defaults to `["assistant", "user", "attachment", "summary"]` if the
agent omits it — notes are explicitly opt-in to avoid pulling internal scratch
into routine recovery queries.

## Retrieval Function

When `query` is set, `react.memsearch` runs **hybrid retrieval**: a semantic
search and a lexical search run in parallel for each target, then the two
ranked result lists are fused by Reciprocal Rank Fusion (RRF) and lifted by
recency.

```text
For each target — three parallel retrievers:

  +-- Semantic --------------------------------+
  |  embed(query) -> cosine on conv_messages.  |
  |  embedding via pgvector ivfflat.            |
  |  Returns turn-grouped rows ranked by sim.   |
  +--------------------------------------------+
  +-- Lexical (BM25F) -------------------------+
  |  (websearch_to_tsquery('simple',  q)        |
  |   || websearch_to_tsquery('english', q))    |
  |   against conv_messages.search_tsv          |
  |   (generated: setweight('A', anchors_text)  |
  |   || setweight('B', text)).                 |
  |  Union analyzers match anchors (simple)     |
  |  AND body (english) without one side        |
  |  silently mismatching the query side.       |
  |  Ranked by ts_rank_cd(...) with log-length  |
  |  norm.                                       |
  +--------------------------------------------+
  +-- Trigram (fuzzy) -------------------------+
  |  AVG(word_similarity(token, anchors_text))  |
  |  and AVG(word_similarity(token, text)) per  |
  |  row across the query tokens, weighted 1.0  |
  |  on anchors / 0.5 on body. Backed by gin    |
  |  (text gin_trgm_ops). Catches spelling      |
  |  variants (Vinnitsa <-> Vinnytsia), typos,  |
  |  and morphological drift. AVG (not MAX)     |
  |  rewards rows matching multiple query       |
  |  tokens — critical for multi-token queries  |
  |  where MAX would tie an unrelated turn      |
  |  matching one common token with a turn      |
  |  matching the discriminating one.           |
  |  Threshold: avg word_similarity >= 0.2.     |
  +--------------------------------------------+

Fusion (per turn_id):

  rrf_score = 1/(60 + sem_rank)
            + 1/(60 + lex_rank)
            + 1/(60 + trgm_rank)
              (each term contributes only if the turn appeared in
               that retriever's top-k)

  final_score = rrf_score x (1 + 1.0 x recency)
                where recency = exp(-ln(2) x age / half_life_days),
                half_life_days = 7
```

Four things to notice about this shape:

1. **RRF fuses by rank position, not raw score.** Cosine similarity,
   `ts_rank_cd`, and `word_similarity` live on entirely different scales
   — a weighted sum would over- or under-weight one depending on query
   and corpus. Position-based fusion is robust against that.
2. **Recency is multiplicative on the fused rank score.** It nudges fresh
   turns up while leaving the rank ordering substantively intact. At
   `L=1.0` today's turn is worth 2x a month-old turn at the same RRF
   rank; week-old gets 1.5x; month-old gets ~1.05x. Strong enough to
   break ties decisively toward recent turns without crowding out older
   high-quality matches.
3. **A turn does not have to appear in all three lists.** Semantic-only,
   lexical-only, or trigram-only matches still return; they just get
   one or two RRF terms instead of three.
4. **The three retrievers cover different failure modes.** Semantic
   handles paraphrase; lexical handles literal token recall (filenames,
   error strings, exact phrasings the user might re-quote); trigram
   handles spelling variants and typos that defeat token-equality. None
   of the three alone is sufficient.

The hybrid path applies only when `query` is set. The catalog modes (ordinal,
temporal date-window without query, timeline overview) use the persisted turn
catalog and a deterministic ordering — see [Catalog Routing](#catalog-routing)
below.

## The Working Summary Contract

The lexical side is only as good as what is in the index. The semantic side
embeds full text and handles paraphrases; the lexical side needs the *literal
strings the user might re-quote* — filenames, error messages, exact wording —
preserved with no stemming and no analyzer mangling.

That requirement is pushed onto the React decision agent through a contract
on what `<channel:summary>` must look like. The agent does not call into this
tool directly; it produces structured text, and the runtime extracts the
signals from that text.

### Required shape

Every complete/exit-round summary must follow this skeleton:

```text
<channel:summary>Goal: ...
Outcome: ...
Key facts: ...
Refs: ...
Retrieval-anchors:
  phrases: ["verbatim string the user might re-quote", ...]
  entities: ["HighIDFProperNoun", ...]
</channel:summary>
```

The five sections each carry a different signal. The first four are read by
future humans/agents as prose; only the last one is parsed structurally:

| Section | Read by | Purpose |
| --- | --- | --- |
| `Goal` | Future agent reading the summary | What the turn set out to do |
| `Outcome` | Future agent | What actually happened |
| `Key facts` | Future agent | Decisive context the next turn would need |
| `Refs` | Future agent | Logical handles produced (`fi:`, `ar:`, `tc:`, `so:` paths) |
| `Retrieval-anchors` | The runtime parser | High-precision lexical index tokens |

The `Retrieval-anchors` block is the one with a machine contract. The other
four are conventions that the next agent reads — useful, but they do not feed
the search index directly.

### Anchor discipline

Two keys inside the block, each with a strict definition:

- **`phrases`**: verbatim multi-word strings. Exact filenames, exact error
  messages, exact titles, the user's own wording. Never paraphrases. These
  get double-quoted when flattened so they survive as logically-grouped tokens.
- **`entities`**: single high-IDF proper nouns. Product / tool / project /
  person / bundle ids. The test is "would this token uniquely identify this
  turn among hundreds of other turns the same user has had?" If the answer is
  no, drop it. Generic nouns like "file" / "data" / "report" / "thing" do not
  pass that test.

Concrete example for a turn that built a Q2 forecast spreadsheet and hit an
`openpyxl` error while renaming a column:

```text
Retrieval-anchors:
  phrases: ["Forecast-Q2-2026.xlsx", "openpyxl IndexError", "rename ARR contribution column"]
  entities: ["Forecast-Q2-2026.xlsx", "openpyxl", "ARR contribution"]
```

Both keys are optional; the agent may emit either or omit the block entirely
for trivial exchanges (greetings, acknowledgments, tiny answers).

### How the runtime uses it

During working-summary persistence the runtime parses the block out of the
summary text, flattens it into a space-separated `anchors_text` string
(phrases double-quoted, entities bare), and stores it on the same
`conv_messages` row as the summary. The generated `search_tsv` then indexes:

```text
search_tsv = setweight(to_tsvector('simple',  anchors_text), 'A')
          || setweight(to_tsvector('english', text),         'B')
```

Two analyzers, two weights. The `'simple'` analyzer keeps anchor tokens intact:
filenames like `Forecast-Q2-2026.xlsx` survive without stemming or punctuation
splitting that would happen under `'english'`. The body text gets the
`'english'` analyzer's stemming and stop-word removal for normal prose recall.
The `'A'` weight on anchors gives them a strong rank multiplier in
`ts_rank_cd`, so a query whose tokens match the anchors outranks a query whose
tokens only appear in the body.

### Producer responsibility and failure modes

The agent's prompt — specifically the v3 multi/single-action and v2 decision
protocols in `chat/sdk/solutions/react/v*/agents/decision.py` — is the contract.
If the agent ignores it, retrieval degrades in predictable ways. There is no
runtime validation that *forces* the agent to produce useful anchors; only the
prompt discipline does. Operators reviewing recall regressions should check
which of the following actually happened:

| Producer behavior | Effect on retrieval |
| --- | --- |
| Summary omitted entirely | No `ws:` row persisted; the turn is invisible to `targets=["summary"]`, recoverable only via `targets=["assistant","user"]` |
| Summary present, no `Retrieval-anchors:` block | `anchors_text` empty; lexical recall is body-only with `'english'` stemming. Filenames and error strings often miss |
| `Retrieval-anchors:` present but empty lists | Same as above; the parser produces an empty string |
| Phrases are paraphrases, not verbatim | Lexical recall does not match the user's literal re-quote; the case the anchors exist to handle silently fails |
| Entities are generic nouns ("file", "report") | Anchors get weight A but the tokens have low IDF; many unrelated turns match equally well, drowning the signal |
| Anchors include things the user would never say | Wasted index budget; no harm to recall but adds noise |

The parser itself is defensive: malformed JSON, single-quoted lists, YAML
`-` lists, and missing keys all degrade gracefully to "empty anchors" rather
than failing the persistence write. So a malformed block does not break the
turn; it just degrades retrieval to body-only weighting for that one row.

If a turn has no anchors block (or only an empty one), the row's `search_tsv`
falls back to body-only weighting. Semantic recall is unaffected. Nothing
crashes; only the lexical-precision case quietly fails.

## Catalog Routing

The agent does not pass a `mode` field. Behavior is inferred from which fields
the agent sets:

| What the agent sets | What runs |
| --- | --- |
| `query` only | Hybrid (semantic + lexical + RRF + recency) |
| `query` + `from` / `to` | Hybrid narrowed to the time window |
| `ordinal` (no `query`) | Turn catalog by 1-based ordinal in scope |
| `from` / `to` (no `query`) | Turn catalog by date window |
| No `query`, no bounds, no ordinal | Turn catalog timeline overview |

The catalog path returns deterministic ordinals/timestamps without any ranking;
it is the right tool when the agent knows *where* a turn was (second turn /
turns from March / chronological overview) and does not need topical matching.

When `query` is passed to a catalog request it is reported back as ignored in
the warnings, with a hint that omitting the catalog signals (no `ordinal`,
no bounds without `query`) would have let hybrid search narrow inside the
window instead.

## Envelope Shape (What The Agent Sees)

The JSON envelope handed to the agent is deliberately minimal — every field
either gives the agent a way to judge the result, provides material content,
or supports a strategy correction. Telemetry and redundant metadata are
dropped:

```text
top-level:
  mode      "hybrid" | "ordinal" | "temporal" | "timeline" | "catalog"
  tokens    total tokens of snippet text returned
  warnings  list of strategy hints (only when present)
  hits      [...]

per hit:
  score             fused RRF + recency score (hybrid hits)
  turn_index_path   ar:[conv_<id>.]turn_<id>.react.turn.index — fallback handle
                    when snippets are not enough material
  ordinal           1-based turn position (catalog modes only)
  total_turns       size of the catalog window (catalog modes only)
  snippets          [{ path, role, text }, ...]

per snippet:
  path   <ns>:[conv_<id>.]turn_<id>...    self-describing, including cross-conv
  role   "user" | "assistant" | "summary" | "attachment" | "notes"
  text   trimmed preview (≤500 chars) of the underlying block
```

Conversation and turn are encoded in the snippet paths themselves; carrying
them as separate fields would be redundant. Timestamps, sub-scores
(`sim_score`, `recency_score`, `rrf_score`), per-side ranks (`sem_rank`,
`lex_rank`, `trgm_rank`), `primary_source`, `matched_via_role(s)`, and
`source_query` echoes are all omitted — they're telemetry for offline
analysis and don't drive agent decisions.

Telemetry that *is* recorded (outside the envelope, for offline tuning of
RRF `k` and the recency lift constant): per-hit `(sem_rank, lex_rank,
trgm_rank, final_score, recency_factor)` logged at memsearch's call site.
The agent never sees those.

## Avoiding the Self-Reference Feedback Loop

The Retrieval-anchors contract says anchors should be *verbatim strings the
user might re-quote — including the user's exact wording*. That works for
substantive turns ("user asked about X, agent gave info about X"). For
recovery turns ("user asked whether we previously discussed X, agent searched
and found / didn't find"), it self-poisons: the agent dutifully anchors X,
X gets indexed, the next search for X retrieves *this* turn instead of the
original X conversation. Repeated searches make the pollution worse.

The runtime breaks this loop structurally at the index layer:

1. **Detection at persistence time** (`base_workflow._is_recovery_turn`).
   When persisting the working summary, the runtime inspects the turn's
   tool-call manifest. If the turn called `react.memsearch` at least once
   and every tool it called was in the recovery/read-only set
   (`react.memsearch | react.read | react.rg | react.pull | react.checkout
   | react.plan | react.hide`), the turn produced no new artifact — it was a
   recovery session.

2. **Tag at persistence time.** The working summary's tags get
   `kind:react.recovery.session` added.

3. **Default exclusion in all three retrievers.** Each of the semantic,
   lexical, and trigram retrievers adds
   `NOT (tags @> ARRAY['kind:react.recovery.session'])` to the WHERE clause
   by default. Recovery summaries are invisible to future memsearches even
   though they're still in `conv_messages` (so the agent's own immediate
   turn-recall paths still find them).

4. **Opt-in surfacing.** `react.memsearch` exposes
   `include_recovery_sessions: bool` (default false). The agent sets this
   to true only when explicitly asked to introspect prior search activity
   (e.g., *"what did you previously try to find?"*).

Two side benefits: (a) per-conversation cap of 2 hits stops any single
polluted conversation from dominating top-k visibility on top of the
exclusion, and (b) the rich struct on `state["last_tool_result"]` keeps
everything for runtime callers even when the envelope drops noise.

## Cross-Conversation Recovery — The Full Chain

`scope="user"` is the entry point. Every path memsearch returns from a
cross-conversation hit is encoded as `<ns>:conv_<conv_id>.turn_<turn_id>...`
so the agent can hand it verbatim to any read-side tool without separately
tracking which conversation it belongs to.

The full recovery chain, hop by hop:

1. **`react.memsearch(query=..., scope="user")`** — runs hybrid retrieval
   across the same user's other conversations. Returns hits with snippet
   paths self-scoped as `ev:conv_X.turn_Y.events/...`,
   `ar:conv_X.turn_Y.assistant.completion`,
   `ws:conv_X.turn_Y.conv.working.summary...`, and friends. The envelope
   already carries an inline text preview (≤500 chars per snippet), so
   for most "I just need to recall what happened" cases the agent has the
   material in hand without another tool call.

2. **`react.read` on the snippet paths** (`ev:`/`ar:`/`ws:`/`tc:`) — works
   immediately because memsearch added each snippet to the timeline as a
   `react.tool.result` block keyed by the same self-scoped path. The agent
   reads the path; the in-context block is matched and returned. No
   cross-conversation fetch needed.

3. **`react.read("ar:conv_X.turn_Y.react.turn.index")`** — for deeper
   traversal: the agent reads the turn-index of a cross-conv turn to
   discover all the artifact refs that turn produced.
   - `parse_turn_index_path` strips the `conv_X.` segment and returns
     `turn_Y`.
   - `_conversation_id_for_path` peels the same segment and returns `X`.
   - The read pipeline calls `get_turn_log(turn_id=Y, conversation_id=X)`
     against the conversation index, fetches the blocks for that turn,
     and renders the turn-index from them.

4. **`react.pull(["fi:conv_X.turn_Y.files/foo.py"])`** — materializes a
   cross-conversation file into the current-turn workspace. Already
   supported via `split_logical_artifact_ref` honoring the `conv_<id>`
   prefix on `fi:` paths.

5. **`react.checkout(paths=["fi:conv_X.turn_Y.files/..."])`** — same
   resolution path as pull; copies cross-conv files into the editable
   current-turn workspace.

6. **`react.exec` referencing cross-conv files in code** — the exec
   validator's path-extraction regex (`_CODE_PATH_RE` in
   `solutions/react/workspace.py`) accepts the optional `conv_<id>/`
   prefix on physical paths. If a referenced cross-conv file is not yet
   materialized locally, the validator stops with `pre_exec_pull_required`
   and the `pull_hint` includes the cross-conv `fi:conv_<id>...` ref so
   the agent's next call has the right path.

All five read-side tool surfaces (`read`, `pull`, `checkout`, `rg`, `exec`)
accept the same self-describing `<ns>:conv_<id>...` shape. The agent does
not need to track conversation_id separately; the path itself is the
address.

**`tc:` cross-conv is intentionally not wired through memsearch.** `tc:`
tool-call results are not indexed in `conv_messages`, so memsearch never
returns `tc:conv_<id>...` directly. `tc:` refs surface only inside a
cross-conv turn-index (step 3 above); the agent traverses to them by
reading the cross-conv turn-index and pulling the underlying `fi:` or
`ar:` ref the `tc:` references.

**What's not crossable.** `scope="user"` cannot cross tenants, projects,
or storage backends — those are configuration-level boundaries enforced
by the runtime, not by path syntax. A `<ns>:conv_<id>...` path pointing at
a conversation outside the current tenant/project still resolves only if
that conversation is within the same scope.

## TTL and Boundaries

- All searches apply `ts + ttl_days >= now()`. The default TTL is 365 days. Rows
  past TTL are not returned and not deleted in the same query; cleanup is a
  separate purge job.
- `scope="user"` does not cross tenants, projects, or storage backends.
- `react.memsearch` returns **handles** (snippet `path` values, `turn_index_path`)
  alongside trimmed text previews. When the previews are not enough, the agent
  follows up with `react.read` / `react.pull` against the returned paths. The
  tool itself does not load file bytes for full artifacts.
- This district is independent of compaction. Compaction shapes the *visible
  timeline* in the current turn; the conversational memory index keeps every
  row until TTL regardless of whether the turn is currently visible.

## When To Use This vs. Other Districts

```text
"I cannot see the path in front of me, but I know what it was about."
  → react.memsearch with query, targets=[summary,...]

"I cannot see the path, but I know it was the second turn / from March."
  → react.memsearch with ordinal / from-to (no query)

"I see the path in front of me."
  → react.read (do NOT search; the handle is already in context)

"What does the user prefer / what is their canonical anchor for X?"
  → durable user memory (different system; see user-memories-overview)
```

## Implementation References

```text
kdcube_ai_app/apps/chat/sdk/solutions/react/tools/memsearch.py
  TOOL_SPEC, hybrid orchestration, per-hit RRF metadata surfacing

kdcube_ai_app/apps/chat/sdk/context/retrieval/ctx_rag.py
  search_context with scoring_mode="rrf_hybrid":
  parallel semantic+lexical+trigram, RRF (k=60), multiplicative recency lift (L=1.0)

kdcube_ai_app/apps/chat/sdk/context/vector/conv_index.py
  search_turn_logs_via_content          (semantic, pgvector cosine)
  search_turn_logs_via_content_lexical  (BM25F via ts_rank_cd over search_tsv;
                                         queries the union of simple+english
                                         tsquery so anchors and body each match
                                         their respective analyzer side)
  search_turn_logs_via_content_trigram  (fuzzy via word_similarity; catches
                                         spelling variants and typos the
                                         token-equality retrievers miss)
  add_message accepts anchors_text

kdcube_ai_app/apps/chat/sdk/context/vector/anchors.py
  parse_retrieval_anchors — extracts phrases/entities from a working-summary
  string and flattens into the anchors_text column value

kdcube_ai_app/apps/chat/sdk/solutions/chatbot/base_workflow.py
  Wires parse_retrieval_anchors into the kind:working.summary persistence path

kdcube_ai_app/ops/deployment/sql/chatbot/deploy-kdcube-proj-schema.sql
  conv_messages.anchors_text + generated search_tsv (BM25F setweight) + GIN

kdcube_ai_app/apps/chat/sdk/solutions/react/artifacts.py
  peel_conversation_prefix — generic <ns>:conv_<id>.<rest> peeler;
  foundation for cross-conv path resolution across all namespaces

kdcube_ai_app/apps/chat/sdk/solutions/react/timeline.py
  parse_turn_index_path, parse_turn_index_ref — accept the cross-conv
  ar:conv_<id>.turn_<X>.react.turn.index form;
  ws: alias resolver and tc: call_id parser also strip the conv_<id>. prefix

kdcube_ai_app/apps/chat/sdk/solutions/react/tools/read.py
  _conversation_id_for_path — falls back to peel_conversation_prefix for
  ar:/ws:/ev:/tc:/so: paths so cross-conv loading via get_turn_log fires
```

## Bottom Line

The conversational memory index exists so the agent can recover prior turns it
cannot see anymore. It is not a curated product surface and it is not the place
to look up user preferences. The retrieval is hybrid by default: semantic
recall for paraphrased intent, lexical recall for verbatim phrasings, RRF
fusion so both signals contribute, recency to break ties toward the recent
past. The `Retrieval-anchors:` block in working summaries is what makes the
lexical side worth running — without it, the index has only body text to
match against and the literal-phrase case stops working.
