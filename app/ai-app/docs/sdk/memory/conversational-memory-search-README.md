---
id: ks:docs/sdk/memory/conversational-memory-search-README.md
title: "Conversational Memory Search (react.memsearch)"
summary: "Cross-conversation searchable memory of agent activity: what is indexed, how `react.memsearch` scopes and ranks it, the hybrid semantic+lexical+recency retrieval, and the Retrieval-anchors contract that makes literal phrasings findable."
tags: ["sdk", "memory", "memsearch", "retrieval", "hybrid-search", "bm25", "rrf", "pgvector"]
keywords: ["react.memsearch", "conversational memory", "cross-conversation search", "hybrid retrieval", "BM25F", "Reciprocal Rank Fusion", "Retrieval-anchors", "search_tsv", "ts_rank_cd", "scope user", "working summary", "turn catalog"]
see_also:
  - ks:docs/sdk/memory/how-react-remembers-README.md
  - ks:docs/sdk/memory/user-memories-overview-README.md
  - ks:docs/sdk/memory/user-memories-react-integration-README.md
  - ks:docs/sdk/agents/react/react-tools-README.md
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
                |                              |  final = rrf * (1 + 0.25*rec)  |
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
For each target:

  ┌─ Semantic ────────────────────────────────┐
  │  embed(query) → cosine on conv_messages.  │
  │  embedding via pgvector ivfflat.           │
  │  Returns turn-grouped rows ranked by sim.  │
  └───────────────────────────────────────────┘
  ┌─ Lexical ─────────────────────────────────┐
  │  websearch_to_tsquery('english', query)   │
  │   against conv_messages.search_tsv         │
  │   (generated: setweight('A', anchors_text)│
  │   || setweight('B', text)). Ranked by      │
  │   ts_rank_cd(...) with log-length norm.    │
  └───────────────────────────────────────────┘

Fusion (per turn_id):

  rrf_score = 1/(60 + sem_rank) + 1/(60 + lex_rank)
              (each term contributes only if the turn appeared in that list)

  final_score = rrf_score × (1 + 0.25 × recency)
                where recency = exp(-ln(2) × age / half_life_days),
                half_life_days = 7
```

Three things to notice about this shape:

1. **RRF fuses by rank position, not raw score.** Cosine similarity and
   `ts_rank_cd` live on different scales; a weighted sum would over- or
   under-weight one of them depending on query and corpus. Position-based
   fusion is robust against that.
2. **Recency is multiplicative on the fused rank score.** It nudges fresh
   turns up without overwhelming the lexical/semantic ordering. The 0.25
   ceiling is intentionally modest.
3. **A turn does not have to appear in both lists.** Semantic-only or
   lexical-only matches are still returned; they just get one RRF term
   instead of two. This is the entire reason BM25 was added — semantic search
   misses literal strings (filenames, error messages, exact user phrasings)
   that the lexical side catches.

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
`lex_rank`), `primary_source`, `matched_via_role(s)`, and `source_query`
echoes are all omitted — they're telemetry for offline analysis and don't
drive agent decisions.

Telemetry that *is* recorded (outside the envelope, for offline tuning of
RRF `k` and the recency lift constant): per-hit `(sem_rank, lex_rank,
final_score, recency_factor)` logged at memsearch's call site. The agent
never sees those.

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
  parallel semantic+lexical, RRF (k=60), multiplicative recency lift (0.25)

kdcube_ai_app/apps/chat/sdk/context/vector/conv_index.py
  search_turn_logs_via_content          (semantic)
  search_turn_logs_via_content_lexical  (lexical, ts_rank_cd over search_tsv)
  add_message accepts anchors_text

kdcube_ai_app/apps/chat/sdk/context/vector/anchors.py
  parse_retrieval_anchors — extracts phrases/entities from a working-summary
  string and flattens into the anchors_text column value

kdcube_ai_app/apps/chat/sdk/solutions/chatbot/base_workflow.py
  Wires parse_retrieval_anchors into the kind:working.summary persistence path

kdcube_ai_app/ops/deployment/sql/chatbot/deploy-kdcube-proj-schema.sql
  conv_messages.anchors_text + generated search_tsv (BM25F setweight) + GIN
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
