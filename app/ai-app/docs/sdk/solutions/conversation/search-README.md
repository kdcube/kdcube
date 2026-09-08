---
id: repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/conversation/search-README.md
title: "Conversation Search"
summary: "Recover decisions, sources, and prior work from the same user's current or earlier conversations through one identity-safe search used by agents, apps, MCP clients, REST, and the chat UI."
tags: ["sdk", "solutions", "conversation", "search", "conv", "memory-realm", "agent-tools", "named-service-provider", "rank-weights", "rrf"]
updated_at: 2026-09-08
keywords:
  [
    "conversation search",
    "conversation_tools.search",
    "conv namespace",
    "run_conversation_search",
    "ConversationSearchContext",
    "ConversationSearchParams",
    "rank weights",
    "rrf_hybrid",
    "normalize_rank_weights",
    "working summary target",
    "temporal browse",
    "turn catalog",
    "bring me here",
    "POST /api/cb/conversations search",
    "scope conversation user agent",
    "snippet fallback",
  ]
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/memory/conversational-memory-search-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/memory/as-named-services-provider-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/tools/named-services-tools-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/namespace-services/discovery-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/kdcube-services/named-services-from-isolated-runtime-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/search_service/search-model-service-README.md
---
# Conversation Search

Conversation search exists so a person or agent can recover useful work without
remembering which conversation contains it. An agent can continue from an
earlier decision or source; a person can find a prior exchange; and an app can
offer the same recall without building another index.

For example, a user can open a new conversation and ask an agent to continue
last week's supplier comparison. Search can recover the shortlist, reasoning,
and cited sources from the user's earlier conversation. The user does not have
to remember its title, copy old messages, or give every agent framework a
different memory system.

The shared search makes four product behaviors possible through one boundary:

- an agent can resume work from another conversation;
- a person can find and reopen an earlier exchange;
- an app or external agent can add recall through a named service or MCP;
- teams can change agent adapters without fragmenting the user's searchable
  history.

Conversations are one of the user's **memory realms**: what was actually said
in this conversation or earlier ones. Every route reaches one engine,
`run_conversation_search(...)` in `sdk/solutions/conversation/api.py`. The
**KDCube Native ReAct agent** is called the **Native agent** below:

```text
Native agent -- react.memsearch ------------------------+
LangGraph, Claude, or another agent --                  |
  conversation_tools.search ----------------------------+--> one search engine
App or external agent -- conv named service / MCP ------+    one user boundary
Person -- REST endpoint / chat search UI ---------------+
```

Every route gets the same candidates, ranking, and identity boundary. Search
is read-only: it recovers conversations; it never writes them.

## The search model

A search's candidate set is an intersection of four constraints:

```text
candidates = user boundary  ∩  scope  ∩  time window  ∩  targets
```

- **User boundary** — always on, never optional. `context.user_id` is a hard
  isolation filter; a search can only ever see the searched user's own
  conversations. A `bundle_id` on the context additionally confines results to
  one app's conversations when set.
- **Scope** — which conversations:
  - `conversation` — only the current conversation (`context.conversation_id`).
  - `user` — all of the same user's conversations; the current conversation id
    then only anchors cross-conversation ref labeling.
  - `agent` — user-wide, narrowed to turns owned by the current agent
    (`context.agent_id` becomes a filter; the backend still sees `user` scope).
- **Time window** — `from_ts` (inclusive) / `to_ts` (exclusive), plus a `days`
  horizon (default 365 for topic search, 3650 for catalog/temporal requests).
- **Targets** — which content kinds inside a turn:
  `user` (prompts and follow-ups), `assistant` (replies), `summary` (working
  summaries), `attachment` (the user's **uploaded** attachments, by their
  indexed summaries), `notes` (the assistant's internal notes). The realm
  covers the user's own uploads; bot-produced files live elsewhere.

### Topic search and catalog browse

The engine routes each request one of two ways:

- **Hybrid topic search** (`effective_mode="hybrid"`) — runs whenever `query`
  is set and no catalog signal is present. Ranked retrieval, described below.
- **Catalog modes** (`ordinal`, `temporal`, `timeline`) — deterministic
  turn-catalog lookups with no ranking. A **blank query with a time window is a
  temporal browse**: "show me what happened that week" without naming a topic.
  An `ordinal` fetches turn N; no query and no window is a timeline overview.
  A query sent alongside catalog signals is ignored with an explicit warning in
  the response. A blank query with no catalog signal is a contract error
  (`missing_query`); the REST endpoint turns it into a 400.

**Query shape** is part of the contract, not a hint. The lexical arm ANDs
every unquoted word of `query` (`websearch_to_tsquery`), treats a
`"quoted phrase"` as verbatim, honours `OR` and `-word`, and the fuzzy arm
averages similarity over the query tokens, so conversational framing ("last
time I worked with excel on ...") empties the lexical result and dilutes the
rest. Every agent-facing route renders the same guidance: content words as they
would appear in the stored text, one topic per call, exact strings quoted,
synonyms joined with `OR`, time words moved to `from`/`to`. The text is
`CONVERSATION_QUERY_GUIDE` in `sdk/solutions/conversation/instructions.py`,
carried by the `react.memsearch` spec, this namespace's search description,
and the hosted-agent recovery guide; its writing-side twin
(`turn_summary_writing_guide`) shapes the summaries the `summary` target
searches. When the AND pass returns nothing, the lexical arm reruns once with
the terms OR'd as a safety net.

Results are turn-level hits carrying recovery paths (`conv:ar:…`,
`conv:ws:…`), snippets, and ranking telemetry — never the raw transcript. In
the hybrid path at most **two hits per source conversation** survive
(`MAX_HITS_PER_CONVERSATION`), so one repetitive conversation cannot
monopolize the top-k; the engine over-fetches from the retriever to refill the
requested limit after that cap.

## Ranking: three arms, one fusion, user-held weights

Topic search is `scoring_mode="rrf_hybrid"` in
`sdk/solutions/conversation/ctx_rag.py::search_context`: three parallel retrievers —
**semantic** (embedding cosine), **lexical** (BM25F over `search_tsv`), and
**trigram** (fuzzy word similarity) — fused by Reciprocal Rank Fusion and
lifted by recency. The full retrieval mechanics (analyzers, anchor discipline,
fusion math, why three arms) live in
[Conversational Memory Search](../../memory/conversational-memory-search-README.md);
this doc covers what is layered on top.

### Rank weights

Callers may hold the fusion knobs. `rank_weights` is an optional
`{semantic, lexical, recency}` mapping of multipliers, normalized by
`ctx_rag.normalize_rank_weights` — unknown keys and non-numeric values are
dropped, survivors are clamped to **[0, 2]**:

```text
rrf_score   = w_sem · 1/(k + sem_rank)
            + w_lex · 1/(k + lex_rank)
            + w_lex · 1/(k + trgm_rank)          k = 60
final_score = rrf_score × (1 + w_rec · L · recency)   L = 1.0, half-life 7 days
```

- `semantic` scales the semantic arm's RRF contribution.
- `lexical` scales **both** the lexical and trigram arms — they are two faces
  of the same text-match signal.
- `recency` scales the recency lift.
- Every weight defaults to **1.0**, and all-1.0 (or omitting the knob entirely)
  reproduces the unweighted fusion **byte-identically** — the knob is only
  forwarded to the backend when set, so backends without it keep working.

### Which hosted turns contribute content rows

Every framework-neutral minimal TurnLog projects one index-only row per folded
user submission and one for the final assistant completion. These rows share
the saved turn identity and carry `projection:minimal.turn.log`; embedding is
best effort, while their stored text always remains eligible for lexical and
trigram retrieval. The Native agent writes the same prompt/completion roles through
its rich finalization and additionally contributes supported attachment text,
working summaries, retrieval anchors, and selected notes. Agent-native
checkpoints and session stores remain separate continuation mechanisms and are
not searched as platform conversation content.

### Honest summary and notes labeling

`summary` and `notes` are **tag-scoped arms**, not role aliases. Working
summaries are indexed as assistant-role rows tagged `kind:working.summary`;
the summary arm scopes to those tags, so a summary-targeted search matches
summary content only — never plain assistant completions. Notes ride artifact
rows tagged `kind:react.note`. Hits produced by such an arm are labeled with
the **target vocabulary** (`matched_via_role: "summary"` / `"notes"`), and
their snippets carry the same role — so UIs and agents see what was actually
matched, not the storage role it happens to ride on.

## Snippets and the retrieval-row fallback

For each hybrid hit the engine fetches the turn log and materializes snippets
per requested target: user prompt blocks, assistant completions, the
`conv.working.summary` block, note blocks, attachment text/summary/meta. Each
snippet carries `role`, `path`, `text`, `ts`.

Turn-log materialization is best-effort. When the turn log is unavailable or
yields no text, the hit is **not dropped**: the retrieval row's own matched
text (fetched `with_payload`) ships as a single snippet labeled with the
matched target vocabulary and `meta.source: "retrieval_row"`. The condition is
loud, twice — a response-level warning ("turn log snippets unavailable for N
hit(s)…") and a `[conversation.search]` log warning naming the turn ids, so a
mis-wired conversation store is visible, never silent.

## Identity: the explicit calling context

`run_conversation_search` reads **no ambient contextvars**. It takes an
explicit `ConversationSearchContext` and a search backend:

| Field | Role |
| --- | --- |
| `user_id` | The searched user. Always a hard isolation filter. |
| `conversation_id` | Confines a `conversation`-scope search; anchors cross-conversation ref labeling in `user` scope. |
| `turn_id` | Current-turn metadata for labeling produced blocks — not a search filter. |
| `bundle_id` | The app scope; the index filters by it when present. |
| `agent_id` | A filter only under `scope="agent"`. |
| `tenant` / `project` / `schema` | Provenance only — see below. |

Tenant and project are **not** `WHERE` filters. Isolation is the Postgres
**schema name**, derived from tenant + project when the search backend is
constructed; the backend handed to the engine is already bound to one. Each
surface therefore supplies the same two inputs: a **context** built from its own
authority (runtime state, named-service request auth, or the HTTP session) and
a **backend** bound to the right schema
(`search_backend.make_conversation_search_backend`).

## Ways to use conversation search

### Let an agent recover earlier work

The Native agent tool `react.memsearch` is a thin caller: it builds the context
with `ConversationSearchContext.from_runtime_ctx(...)` — the only place
identity is read off runtime state — and passes the live context browser as the
backend.
Agents can also reach the realm through the `conv` named service with the
standard named-service tools (see the
[named-services tools doc](../../tools/named-services-tools-README.md)):

```text
named_services.search_objects(
  namespace="conv",
  query="<topic>",
  filters={"targets": ["user", "assistant", "summary"], "scope": "user"},
)
```

Empty `query` with `ordinal` or a `from`/`to` window does the deterministic
catalog lookup instead of topic search.

The canonical descriptor-selectable SDK tool is
`sdk/tools/conversation_tools.py::ConversationTools.search`. It accepts only
search choices such as query, scope, targets, age, and result limit. Tenant,
project, user, current conversation, turn, bundle, and agent identity come from
the trusted communicator and tool subsystem bound to the active turn; the
model cannot name another caller in tool arguments.

The direct LangGraph example presents this callable as `conversation_search`.
The direct Claude example presents it through the local harness MCP server as
`mcp__kdcube_harness__conversation_search`. Both are schema adapters over
`conversation_tools.search`; the Native agent keeps its established
`react.memsearch` agent interface. Their runnable descriptor and verification
flow are in
[Run the Agent Harness from Python](../../../recipes/quickstart/run-agent-harness-from-python-README.md).

### Give an external agent the same recall through managed MCP

The provider (`sdk/solutions/conversation/named_service.py`, provider id
`sdk.conversation`) mirrors the memory provider's shape: decorated class,
spec factory, search scopes, `intro`. The **kdcube-services** app registers it,
supplying the two seams — `conversation_search_context_from_ns` as the context
factory and a pooled `make_conversation_search_backend` bound per request to
the caller's tenant/project — and publishes `conv` on its `named_services` MCP
surface. From there the realm is reachable by connected external agents and by
generated code in isolated runtimes over the relay (see the
[isolated-runtime doc](../kdcube-services/named-services-from-isolated-runtime-README.md)).
The provider advertises read operations only and carries advisory grant hints
(`conversations:read`, `conversations:read:any_user` for selected-user access);
consent and enforcement live at the managed boundary, and registration follows
the [discovery registry](../../namespace-services/discovery-README.md) like
every other provider.

### Let a person search their own conversations

`POST /api/cb/conversations/{tenant}/{project}/search`
(`apps/chat/ingress/conversations/search.py`) is the human-facing route. Its contract:

- **The searched user is always the authenticated session user.** The route
  builds the context from the session and a pooled backend from the request's
  tenant/project; there is no way to name another user.
- **Scope** is `user` or `conversation`; conversation scope requires a
  `conversation_id` the session user owns (ownership is checked, 404
  otherwise).
- **Targets** are a non-empty subset of
  `{user, assistant, summary, attachment}`; unknown targets are a 400.
- **Time window** `from_ts`/`to_ts` (ISO 8601). Blank query **with** a window
  is the temporal browse; blank query **without** one is a 400.
- **`limit`** is 1..50; **`weights`** are the rank weights above, clamped
  server-side; **`bundle_id`** selects an app scope (validated against the
  registry; the tenant/project default when omitted).
- **Responses are enriched** with each hit conversation's title and last
  activity, so the widget needs no join round-trip. Enrichment is best-effort
  and never fails the search.
- **The semantic arm degrades, it never 500s.** Embedding failures are
  contained per arm inside the retriever, so lexical and trigram matching still
  answer; a missing model service behaves the same way.

The chat widget (`components-core/src/chat/conversationSearch.ts` +
`engine.searchConversations`; `components-react` feature
`chat/ui/features/conversations/`) fronts this endpoint in the conversations
sidebar. Nothing paid runs on typing: the **Titles** scope is a free local
filter over the chat list, and the deep scopes (**This chat** / **All chats**)
run only on an explicit Search. The settings panel states the query
prerequisites — WHERE (scope) ∩ WHEN (presets or a custom date range) ∩ HOW
(kind opt-ins) — plus RANK sliders (0..2, step 0.1) with an ⓘ overlay that
teaches the three arms and RRF fusion on a worked example. Results group by
conversation, one card per snippet with its role chip, best-match marker,
"turn N of M" position, timestamp, and relative relevance; a blank-query date
range renders as a chronological browse. **Bring me here** loads the
conversation in the same chat view, scrolls to the turn's `data-turn-anchor`,
and flashes it — the search state is shared between the expanded sidebar and
the compact view.

## Observability

Every run logs one greppable `[conversation.search] start … done …` pair with
routing, mode, scope, counts, and clipped query — plus the fallback warning
above when snippet materialization degrades.
