---
id: repo:kdcube-ai-app/app/ai-app/docs/recipes/kdcube_for_agents/port-your-solution-to-kdcube-README.md
title: "Port Your Solution To A KDCube App"
summary: "Executable procedure a coding agent (or engineer) follows to host an existing Python agent — in its own framework — as a KDCube app: vendor the solution unchanged, add a thin wrap (entry seam, state mapping, streaming, concurrent-user isolation, per-turn rebuild), then satisfy the canonical app-package contract. Worked instance: ported-langgraph-agents@2026-07-13 (poc/lg-solution + poc/lg-prebuilt-agent)."
status: draft
tags: ["recipes", "kdcube-for-agents", "port", "wrap", "langgraph", "streaming", "bundle", "app", "scaled-serving", "turn-workspace", "attachments"]
updated_at: 2026-07-16
see_also:
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-write-bundle-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/chat/chat-stream-events-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/chat/chat-component-communication-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/chat/chat-widget-solution-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/events/reactive-turn-delivery-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/recipes/dataflow/connect-agentic-loop-to-ordered-delivery-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/runtime/cross-runtime-context-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/conversation/hosted-agent-conversation-README.md
  - repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/build/how-to-test-bundle-README.md
---
# Port Your Solution To A KDCube App

You have a working Python agent — your own graph, your own framework, your own
memory and persistence — running on one machine. This recipe hosts it as a
KDCube app **at scale**, keeping your framework running as-is. The agent stays
in its own framework; KDCube adds hosting: streaming to the reusable chat
component, per-user isolation, and a platform-owned conversation record.

Power is preserved, not traded away. A sophisticated agent — a multi-node graph
with nested subagents, retrieval, and long-lived memory — runs unchanged; the
wrap adds hosting around it. The worked instance is exactly such an agent, so
this recipe shows a genuinely capable LangGraph agent functioning inside KDCube,
not a toy.

The whole port is a **thin wrap plus the standard app package**. Your solution's
code is vendored unchanged; the wrap is three small glue files; the package
contract is the same one every KDCube app satisfies.

> Agent-following-steps == human-following-steps. This page is the instruction
> set: give it to a coding agent (with the KDCube app-builder plugin) together
> with the user's solution, and it produces a runnable app. Every step names
> concrete files and symbols and ends in a verifiable checkpoint.

## The worked instance (read it alongside this recipe)

One before/after pair demonstrates every step. Read both:

| | Path | Role |
| --- | --- | --- |
| **Before** | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/poc/lg-solution` | A standalone LangGraph research assistant: KB retrieval + per-user pgvector memory + a nested subagent, streaming via `astream_events`, Postgres-checkpointed. Runs on one machine for one user. Zero KDCube references. |
| **After** | `src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles/ported-langgraph-agents@2026-07-13` | ONE KDCube app hosting BOTH agents (`poc/lg-solution` + `poc/lg-prebuilt-agent`, a `langchain.agents.create_agent` ReAct agent with tools + MCP + code-exec), vendored unchanged under `solution/`, dispatched by `agent_id` through a single `execute_core`. The recipe below teaches the single-agent port; the multi-agent dispatch is a small extension (`execute_core` selects the graph by `agent_id`). Its `README.md` / `AGENTS.md` / `docs/` are the maintained reference for every point below. |

The diff between them is exactly what this recipe produces: `solution/` copied
verbatim, plus `entrypoint.py` + `identity.py` + `stream_adapter.py`, plus the
package contract files.

---

## Phase 0 — Understand what you were handed

Before touching code, state three things in `README.md` and `docs/README.md`
(you will refine them, but write them first):

1. **The solution's job** — what one request/turn does (in: user input; out: an
   answer, maybe streamed).
2. **What KDCube adds** — hosting at scale, streaming to the chat component,
   concurrent-user isolation, a conversation record. The solution's own framework and
   persistence stay yours and internal.
3. **What is out of scope for the first pass** — everything not needed to serve a
   streamed chat turn (extra ingress surfaces, custom UI, model routing) is a
   later hardening step, not the port.

Reference: `how-to-write-bundle-README.md` §1D ("If You Are Wrapping Existing
Code") — keep business logic reusable, keep KDCube wiring near `entrypoint.py`,
do not entangle the two.

---

## Phase 1 — Locate the solution's seams (inspection)

Read the solution and find these five seams. They are the only things the wrap
needs to know.

| Seam | Question | In the worked instance |
| --- | --- | --- |
| **Entry callable** | Where is "handle one request"? A function, a compiled graph, a CLI `main`, an HTTP handler. | `solution/graph.py::build_graph()` → `graph.astream_events(...)` |
| **Framework** | Which framework? (You keep it.) | LangGraph |
| **Input / output** | Where does the user's text enter, where does the answer land? | input `question`; output state `answer` |
| **Streaming** | Does it already stream tokens/steps? (callbacks, generators, `astream_events`, even `print`.) | `astream_events(version="v2")` — answer-node token events + node-start events |
| **Per-user state** | How does it key memory / session per user? | memory keyed by `user_id`; checkpointer keyed by `thread_id` |

Checkpoint: you can name, for the solution, the callable to invoke, the input
field, the output field, its stream events, and its per-user keys. If it has no
streaming seam, note it — Phase 3 wraps it at coarse grain instead.

---

## Phase 2 — Vendor the solution unchanged

Copy the solution's package into the app under a subpackage (the worked instance
uses `solution/`). **Do not edit it.** A KDCube app is always loaded as a Python
package, so the solution's package-relative imports (`from .deps import ...`)
resolve as a subpackage with no change.

```
ported-langgraph-agents@2026-07-13/
  solution/          ← the "before", copied verbatim
```

Checkpoint: `python -m py_compile solution/*.py` is clean and no line differs
from the source project.

---

## Phase 3 — Write the wrap (the glue)

The wrap is the only hand-written code: three focused files (entry seam, streaming,
isolation) so each concern is visible and testable, plus one structural rule —
rebuild every turn (3d). Read the worked instance's versions as you write yours.

### 3a. Entry seam + state mapping — `entrypoint.py`

Subclass `BaseEntrypoint` and implement `execute_core`. This is KDCube's
framework-neutral turn seam: `@on_reactive_event → run() → execute_core(state,
thread_id, params)`. The framework binding lives inside `execute_core`; KDCube
never imports your framework.

- Pull the user's text out of the platform turn: `external_events_text(state.get("external_events") or [])`.
- Hand it to the solution's own input shape (its `AgentState`, its function args).
- Run the solution (Phase 3b streams it).
- Set `state["final_answer"]` to the solution's answer — the platform's canonical
  final, and what the conversation recorder persists.

Base-class choice: `how-to-write-bundle-README.md` "Base Entrypoint Class
Decision Table". A plain wrap uses `BaseEntrypoint`; derive a richer family class
only if you want KDCube's own economics/memory surfaces too.

**Delivery is ordered — you get it for free.** The `execute_core` seam is fed by
the **event bus**: turns arrive one at a time per conversation, in arrival order,
exactly once. A message sent while a turn is running waits and becomes the *next*
turn — a run-to-completion loop never runs two turns of one conversation at once,
and never loses the follow-on message. The lane bookkeeping (releasing the turn's
event-bus reservation at completion, promoting a queued followup) is done for you
at the door; you write no event-bus code. Declare, per agent,
`conversation.accepts_followup: false` / `accepts_steer: false` for a
run-to-completion loop — a mid-turn message is then queued for the next turn. For
the mechanism and how a loop that *can* consume mid-turn opts in, see
[Reactive Turn Delivery](../../sdk/events/reactive-turn-delivery-README.md) and
[Connect Your Agentic Loop To Ordered Message Delivery](../dataflow/connect-agentic-loop-to-ordered-delivery-README.md).

**Fold the turn's batch — the input is the batch, not the wakeup event.** A user
message with attachments arrives at ingress as ONE batch of external events — the
prompt event plus one `event.user.attachment.file` event per hosted file, all
sharing a `batch_id` — but the lane wakeup that starts your turn names only *one*
of those events, and the rehydrated `state["external_events"]` carries only that
one. Read as-is, your turn sees the prompt and is blind to the files that arrived
beside it (the exact bug the worked instance surfaced live: the agent answered a
"what's in this file?" turn as if no file existed). The wrap folds the batch back
in at the top of `execute_core` — read the conversation lane, take the wakeup
event's `batch_id` siblings in lane order, skip anything an earlier turn already
consumed. STRICTLY READ-ONLY: no consumption marks, no reservation changes — lane
bookkeeping stays with the door. The built-in ReAct agent is immune (it folds the
lane itself), which is why this bites only run-to-completion ports. Worked
instance: `platform/turn_batch.py::fold_turn_external_events` + `tests/test_turn_batch.py`
(including the exact surfaced case: prompt + hosted PNG in one batch).

### 3b. Streaming — `stream_adapter.py`

Redirect the solution's existing stream at KDCube's communicator so the reusable
chat component renders the turn live. The primitives (module functions over the
current communicator) are:

- `comm_ctx.step(step, status)` — a progress step (a graph node, a phase);
- `comm_ctx.delta(text, index, marker="answer")` — a streamed answer chunk;
- `comm_ctx.complete(data={"final_answer": ...})` — turn end.

Map your solution's stream events onto those. For the worked instance's
`astream_events` loop that is 1:1: node start → `step(node,"running")`,
answer-node token → `delta(token)`, final → `complete(...)`. If the solution has
no token stream, emit one `delta` with the whole answer and a `step` per phase —
the component renders both.

The envelope/marker contract you are emitting into is owned by
`chat-stream-events-README.md` (markers `answer`/`thinking`/`canvas`/
`timeline_text`/`subsystem`; `assistant.completion` is a catalogued type). Do
not restate it — read it if you need a marker other than `answer`.

### 3c. Per-user isolation — `identity.py` (the gate)

This is the least-obvious and most-important step. The solution ran for one user
on one machine. A KDCube deployment is bound to one tenant/project, but each
worker process can serve many users concurrently. Map the bound platform
identity onto the solution's per-user keys so no two users share state:

- `state["tenant"] / state["project"] / state["user"]` → the solution's per-user
  key (the worked instance folds them into `t:p:user`, so the same raw user id in
  two tenants never collides).
- `state["conversation_id"]` → the solution's session/thread/checkpointer key.

Forwarding a raw or constant user id here is the silent bug that leaks one user's
memory into another's turns. Keep this in its own module with a unit test that
asserts cross-tenant keys differ.

### 3d. Rebuild every turn — no in-process cache (scaled serving)

KDCube is distributed: a turn can land on any processor worker or machine. So
`execute_core` must **rebuild** the agent for each reactive event from rebuildable
state — never cache the compiled graph, the framework runner, or any per-turn object
on the long-lived entrypoint instance. Anything cached in one worker's memory is
invisible to the next turn on another worker and silently drifts.

- Build the graph **inside** `execute_core` (worked instance: `_build_graph(agent_id,
  disabled_tools)` per turn), not once at load. A first-port instinct is to cache the
  compiled graph "for speed" — don't; correctness beats the microseconds, and the
  per-turn build is also what lets the current conversation's saved tool
  selection (§5) narrow the graph.
- Reuse only true **connections** — a DB pool, a checkpointer connection opened once
  (worked instance: `_open_checkpointer`, idempotent) — because a connection is not
  rebuildable per-turn state.
- Keep nothing per-turn on `self`; every mutable byte lives in shared storage keyed
  by (user, conversation).

The graph instance exists only for the current turn. This is a lifecycle and
concurrency boundary, not the generated-code sandbox: when the graph invokes
open-ended code execution, that code runs through the separate ISO executor.

This is what lets any worker serve any turn. The runtime restores only its context
room across boundaries, never your cached Python objects — see
[Cross-Runtime Context](../../runtime/cross-runtime-context-README.md). Add a
regression test asserting the entrypoint holds no per-agent graph (the worked
instance asserts `not hasattr(inst, "_graphs")`).

Checkpoint (offline smoke, no DB, no API key): build the solution's entry with an
in-memory fallback, run one turn through `stream_adapter`, and assert it emits
steps, at least one answer `delta`, a `complete` carrying `final_answer`, and that
two different platform users produce different solution keys. The worked
instance's `tests/test_stream_adapter.py` + `tests/test_identity_isolation.py`
are copyable.

---

## Phase 4 — Satisfy the canonical app package

The wrap makes the turn work. The **package contract** makes it a maintained
KDCube app. This half is framework-agnostic — every port produces it. Follow
`how-to-write-bundle-README.md` §1B.1 ("Canonical App Package") and its Required
file contracts table. Do not infer the structure from an example; the guide
defines it.

Minimum the worked instance carries (all present in `ported-langgraph-agents@2026-07-13/`):

- `__init__.py`, `entrypoint.py`, `README.md`, `AGENTS.md`, `release.yaml`
- `config/bundles.template.yaml` + `config/bundles.secrets.template.yaml`
- `interface/README.md` + `interface/<app-slug>.openapi.yaml`
- `docs/README.md`, `docs/storage/README.md`, `docs/journal/…`
- `tests/`

Declare only the surfaces you actually have. A streamed-chat wrap has **one**
runtime surface — the reactive chat turn — declared in config as
`surfaces.as_provider.bundle.default_chat: true` (product intent lives in the
descriptor, never inferred from code). Its OpenAPI has `paths: {}` and declares
the turn under `x-kdcube-surfaces`. Document every surface that exists; add none
that does not.

Keep the contract synchronized (the shared bundle suite enforces this):

```
entrypoint decorators == interface surfaces == OpenAPI x-kdcube-surfaces
  == config template == README surface list == AGENTS.md == tests == journal
```

`AGENTS.md` is the **onboarding point for any agent that later maintains this
app** — distinct from this recipe, which is for the agent that *ports* it. Once
the port is done, this recipe steps aside; the app's own `AGENTS.md` is what the
next agent reads before changing it. It states read order, what the app owns
(and that `solution/` is vendored and never edited), the isolation and storage
boundaries, which files must stay synchronized, and the exact validation
commands. Use `connection-hub@1-0/AGENTS.md` as the structural model.

---

## Phase 5 — Resolve the decision points explicitly

These are the choices a port must make on purpose, not by default.

### Persistence split

Your solution keeps its **own** store (its DB, its checkpointer, its memory) —
internal and unchanged. KDCube separately owns the **conversation record**, so the
chat component's list / fetch / reload work with **no** record-writing code in your
app. What "reload works" means for a run-to-completion turn:

- The platform records a **minimal turn log** carrying the **user message, its
  attachments, and any hosted files** plus your `state["final_answer"]` — so the
  reloaded turn shows the user bubble + files, not just the answer.
- The dynamic objects your turn **emits through comm** — citations, progress steps,
  follow-ups — are captured full-payload and **replayed** on reload (the client
  renders them exactly as live). The rule: reload content comes from **comm + the
  turn log**, not from your runtime `state`. You get this by emitting through
  `comm_ctx` (§3b); you write no reload code.
- If your agent produces **downloadable files**, serve the `scene_object_action`
  operation so a file card's Download resolves — delegate a `conv:fi:` ref to
  `resolve_event_ref_action` (worked instance: `entrypoint.py::scene_object_action`).
  Without it the file is shown but Download has no endpoint. Contract:
  [chat-widget-solution-README.md](../../sdk/solutions/chat/chat-widget-solution-README.md).

**Where your agent's own store goes (hosted).** Route it onto KDCube's shared
Postgres (`self.pg_pool`) into the ONE per-tenant/project schema `schema_for_scope()`
returns, in **bundle-prefixed tables** scoped by `(tenant, project, bundle_id, user_id
[, agent_id])` columns. Do **not** create a per-agent / per-bundle / per-version
schema and do **not** `CREATE EXTENSION` — that pollutes Postgres and is a documented
anti-pattern (the platform provides `vector`/`pg_trgm`). Provision idempotently in
`on_bundle_load`. Guardrail + pattern: `how-to-write-bundle-README.md` ("Relational
(Postgres) storage rule").

Document the split in `docs/storage/README.md` as an ownership matrix (owned /
read-through / ephemeral / platform-owned). The full continuity model — the two
memories (your agent's own store vs. the platform record), durable-checkpointer
keying, the first-turn title, and restoring turn cost/time on reload (a
`post_run_hook` that calls `_save_events_artifact`) — is
[hosted-agent-conversation-README.md](../../sdk/solutions/conversation/hosted-agent-conversation-README.md).

### Configuration: descriptor properties overlay the vendored config

Your solution's own config almost certainly reads env vars — that is the normal
standalone idiom, and it keeps working offline. Hosted, the app's configuration
surface is the **descriptor**: a runtime knob an operator may need to change must
exist as a bundle property (read via `bundle_prop(...)`), because an env read has
no deployment surface — nothing declares it, nothing sets it, and the code runs
its hardcoded default forever. The wrap pattern is a per-knob overlay: read the
descriptor property, fall back to the vendored config's value when the property
is absent (worked instance: `_agent_max_tokens` overlaying
`surfaces.as_consumer.agents.<agent>.model.max_tokens` onto the vendored default).

The knob that earns this section its place: the **answer model's output-token
budget**. A tool-calling agent passes whole payloads as tool arguments (the exec
tool carries the full program text), so the budget must fit narration plus ONE
complete payload-bearing tool call. A budget below that cuts the response
mid-call; the truncated arguments fail tool validation with a misleading
"missing argument", and the model retries the identical call into the same
ceiling until the graph recursion limit (observed live: 12 attempts at exactly
the 1200-token adapter default). Make the budget a descriptor property, size it
as a generous safety cap (the model stops on its own), and note the platform's
LangChain adapter logs an INTERRUPTED warning — and tells the model in-band —
when a response spends its full budget on a tool-call turn.

### Dependencies

You own the platform, so extra deps are a choice, not a mandate. Most of your
framework is likely **already** in the processor env — KDCube ships `langgraph`,
`langchain(-core/-openai)`, and a Postgres driver in
`requirements-chat-processor.txt`. Diff your solution's requirements against it;
the real delta is usually small (for the worked instance: `langgraph-checkpoint-postgres`
and `psycopg[binary]` v3). Supply the delta either way:

1. add it to `requirements-chat-processor.txt` / `requirements-chat.txt` (simplest — the processor is yours), or
2. isolate it with the `@venv` contract using the app's `requirements.txt` as the spec.

If the solution needs a **newer framework version** than the processor ships,
bump it in the processor requirements — you own the platform, so raising
`langgraph` (or any shared dep) to the version your agent needs is a normal edit,
not a workaround. Keep the app's `requirements.txt` at the version the solution
actually targets so the intent is recorded.

Neither is required to run if the solution degrades gracefully when a dep is
absent (the worked instance falls back to an in-memory checkpointer).

### Capabilities: model pick, tools, code execution (optional)

The chat component's Capabilities widget can expose your agent's model and tools so
an admin sets a ceiling and a user picks per turn. All optional; add when the product
wants them. State which you chose in `docs/README.md`.

**Model pick.** If the solution selects its model internally (its own env/config),
the first port keeps that — it just works and the picker stays invisible. To route
through KDCube roles: declare the generic `simple_model_pick` provider per agent
(`surfaces.as_consumer.agents.<agent>`), naming an answer role + model list;
`execute_core` resolves the pick and overlays it onto `bundle_call_context.role_models`
around the graph run.

- **Title-binding gotcha:** if you generate a first-turn conversation title using an
  agent's answer role, ALSO bind that role in **base `config.role_models`**. The pick
  overlay is scoped to the active agent's turn, but the title runs outside it —
  without the base binding the role resolves to no model and the title comes back
  empty ("Untitled conversation").

**Tools (admin ceiling + user opt-out).** Declare your agent's tools as a
**connection list** under `surfaces.as_consumer.agents.<agent>.tools`
(`- {name, kind: python|mcp, alias, allowed}`) — the standard KDCube shape, so the
Capabilities catalog lists them natively and the platform stores per-user opt-outs as
a deny-map. Admin `allowed: false` is a hard ceiling; a user may opt OUT of an
admin-allowed tool but never opt IN to a denied one. Bind exactly (admin-declared ∩
user-enabled) each turn — the per-turn rebuild (§3d) is what makes this a clean
narrowing (worked instance: `platform/tool_pick.py`, `capabilities.py`).

**Platform paid tools (web search / web fetch).** The platform's web backends
(`sdk/tools/backends/web/search_backends.py`, `fetch_backends.py`) are directly
callable from your own `@tool` wrappers — no tool-subsystem adoption needed. Two
accountable providers meter per call, and both bill through seams your port already
stands on: the **web_search provider** (the search backend, e.g. Brave; key from
platform secrets) meters inside the backend against the ambient turn accounting
context — the same context your model calls bill under; the **llm provider** (snippet
reconciliation + content filtering/segmentation of retrieved results) runs on the
`_SERVICE` you pass in — hand it the entrypoint's accounted `models_service` and those
calls bill like any model call of your app. So the wrapper's whole job is service
pass-through plus shaping the rows for a chat model: drop accounting-only fields
(`provider`) and binary payloads (`base64`; keep mime + size), bound page content
under per-row/per-call budgets, and state truncation in-band so the model refetches
selectively instead of assuming it saw the full page. Declare the pair as one
connection (`- {name: web, kind: python, alias: web, allowed: [web_search, web_fetch]}`)
and bind through the same picker; the backends stream their own search/fetch progress
widgets over the ambient communicator, so the chat renders them with no client change
(worked instance: `platform/web_tools.py`).

**Code execution.** If a tool runs generated code, use the platform's isolated exec
runtime — do NOT invent a sandbox. Root the per-turn workspace at
`get_exec_workspace_root()` (a docker-mountable isolated workspace, the same concept
the React path uses via `resolve_exec_runtime_profile`) so files the code produces are
hosted into the conversation like attachments — reload + Download then work via the
Persistence-split machinery. It runs wherever the platform runs code; no separate
Docker requirement (worked instance: `platform/code_exec.py`). Two things make it feel
native:

- **Match the exec tool's shape, keep the contract advisory.** Give your tool the same
  inputs the platform exec tool takes — `code` + a `contract` of declared output files
  (+ `prog_name`) — so the model can plan its deliverables and the exec widget can render
  them. But the platform's strict contract runner (`run_exec_tool`) requires the code to
  write to the exact `OUTPUT_DIR/turn_<id>/files/…` path; a small model that saves to a
  plain path produces the file yet trips "missing contracted output" and **retries in a
  loop**. So run **side-effects** (wrap the code, host every produced file) and treat the
  contract as **advisory** (planning + widget only) — robust, and the model never loops
  on paths. (Wrapping the KDCube exec tool re-declares it for your framework — a
  `create_agent` tool takes code as an argument, not the React `<channel:code>` — so you
  copy the runtime-neutral guidance; a shared-builder factoring is future work.)
- **Live exec widget.** Drive the reusable `solutions/widgets/exec.py`
  streamer (`comm.delta(marker="subsystem", sub_type="code_exec.*")`, keyed by an
  `execution_id`) around the run — emit the program name, the **contract**, the code, and
  status `gen → exec → done|error`. The chat renders the same exec panel React shows; no
  client change. React feeds it from a decision `<channel:code>` stream; a
  create_agent tool call carries the code as an argument, so drive the widget directly.
- **Propagate errors, classified.** Return a structured error the model can act on:
  a **runtime/sandbox** failure (`sandbox_execution_failed`) is a *platform* problem it
  may RETRY; a **program** error is its own code to fix. Surface both in the tool's
  text result (Status + class + program-log tail) — a silent failure lets the agent
  assume success. Contract:
  [exec-logging-error-propagation-README.md](../../exec/exec-logging-error-propagation-README.md).

### The distributed turn workspace: files in (read / pull / exec over links)

Code execution (above) covers files going *out* — the code writes, the platform
hosts. Files coming **in** — the user attaches a spreadsheet, or the agent wants a
file its own code produced two turns ago — ride one platform concept the port must
adopt deliberately: the **distributed turn workspace**. It is the same per-turn
`work/`+`out/` surface the code tool already runs in, and it obeys ONE rule with no
exceptions:

> **The working directory starts EMPTY every turn.** Nothing carries over in the
> directory itself — not the user's files, not files the code produced or pulled in
> earlier turns. The durable record is the conversation: every file keeps a
> **conversation link** (`conv:fi:...`) that identifies it in any later turn.

**Nothing is read for the model automatically** — not text, not images. The turn
input carries the user's message plus each arriving file as **metadata + link**,
framed the way the built-in agent frames its timeline (an in-band turn boundary):

```text
[Turn start turn_<id>]
Your working directory is EMPTY — it starts fresh every turn. Files are given
to you as LINKS only; nothing is read for you automatically. ...

[User message]
whats in this file?

[Files arriving this turn]
- report.docx (application/vnd...document, 2.9 MB) — link: conv:fi:turn_<id>.user.attachments/report.docx
```

The frame is not cosmetic. A chat-shaped history spans many turns; without an
explicit `[Turn start ...]` stamp the model trusts stale history — "I pulled that
file before, it is still here" — and the fresh empty directory silently contradicts
everything it remembers. The frame rides the user message into the model input AND
the checkpointed history, so every turn's scope is legible forever. Worked
instance: `platform/turn_workspace.py::prepare_turn_workspace` (accounts for every
arriving file; with no workspace tools bound it states the honest reason contents
are out of reach — a file is never silently dropped) and `frame_turn_input` (the
frame); `execute_core` passes the framed text as the turn's question.

**Three tools operate over the links**, and they bind as one set with the code-exec
connection (a user opting out of code execution drops all three — a pulled file
with no way to run code would be inert):

- **`read_file(path)`** — view ONE file in visible context by its link, mirroring
  `react.read`: text files return bounded text (`max_text_symbols`); images and
  PDFs return as visual content (oversized images downscaled via
  `normalize_image_base64_for_model`, byte-capped); other binaries answer "pull +
  run_python".
- **`pull_files(paths)`** — materialize ANY links into the working directory: a
  file arriving now, a user upload from an earlier turn, a file the code produced
  before. After a pull the code reads it by the bare filename the pull reports.
- **`run_python(code, ...)`** — process. Pulled files sit under bare filenames in
  the working directory; every file the code writes is hosted back into the
  conversation, and its report lists each as `link=conv:fi:...` — which is exactly
  how the model's history accumulates pullable links for its own artifacts.

**One resolver serves every door.** `read_event_ref_bytes`
(`react/events/resolver.py`) resolves a `conv:fi:` link to bytes for read, for
pull (via the SDK core `runtime/workspace/pull.py::pull_refs_into_dir`), and for
the Download button (`scene_object_action`) — a link that downloads also reads and
pulls. Two ref shapes to know: uploads are
`conv:fi:turn_<id>.user.attachments/<filename>` (the turn-recorder / Files-tab
shape; the resolver bridges the timestamped stored name to the plain filename),
produced files are `conv:fi:conv_<conversation_id>.turn_<id>.files/<filename>`.

**Teach it with the shared block — do not write your own prose.** The SDK ships
the standalone instruction block
`shared_instructions.py::distributed_turn_workspace_guide(exec_tool=..., pull_tool=...,
read_tool=...)` — turn lifecycle (new user message = new turn = empty directory),
frame literacy, link vocabulary, and the three doors, parameterized by your tool
names (an empty `read_tool` drops the view bullet). Append it to the agent's
system prompt exactly when the triad is bound (worked instance:
`entrypoint.py::_prebuilt_system_prompt`; the vendored agent's own prompt leads,
the block follows). The paradigm is platform-shaped, not agent-shaped — any agent
you connect to the workspace gets the same literacy from the same block.

Worked instance files: `platform/turn_workspace.py` (frame + read + pull tools),
`platform/tool_pick.py` (companion binding), `platform/code_exec.py::exec_files_dir`
(the working directory, defined beside the exec wrapper so the two cannot drift),
`tests/test_turn_workspace.py` (frame moves no bytes; boundary stamped on fileless
turns; never-silent no-workspace case; read text/image/binary; pull through the
shared core; triad binding; guide-in-prompt).

### Ingress beyond chat (optional)

The reactive chat turn is one entry. The same `execute_core` can also be driven
by a **webhook** (`@api(route="public")`) — a synchronous response, or an
async submit that streams like the Telegram path. Add these only when the
product needs them; each is a declared surface with its own auth boundary.

---

## Phase 6 — Verify

In order:

1. **Offline smoke** (Phase 3 checkpoint) — the turn streams and isolates with no
   DB and no API key.
2. **Contract tests** — `python -m pytest <app>/tests -q`. Cover manifest/identity
   discovery, the single declared surface, the isolation invariant, and the
   stream adapter. The worked instance's `tests/` pass offline.
3. **Package validation** — the shared bundle suite + compile/import checks
   (`how-to-test-bundle-README.md`).
4. **Live run** — with a real model + the solution's DB reachable: confirm the
   answer streams token-by-token; the turn appears in the conversation list; and on
   **reload** the user message, its attachments, any hosted files, and the emitted
   citations/steps/followups all come back (the platform-owned record). If the agent
   hosts files, confirm **Download** works (`scene_object_action`); if it names the
   conversation, confirm the **title** appears (role bound in base `role_models`).
5. **Workspace loop (live)** — attach a non-image file (e.g. a `.docx`) and ask about
   it: the payload log shows the `[Turn start ...]` / `[Files arriving this turn]`
   frame, and the model chooses a door (`read_file`, or `pull_files` + `run_python`)
   instead of answering blind. Then, in a LATER turn, ask it to work with that same
   file again: the pass criterion is that it **pulls/reads by the link before
   assuming** — the empty-every-turn rule holding in model behavior, not just in
   text. Also confirm a `read_file` of an image returns visual content through your
   framework's tool-message path (the offline tests pin the tool's return shape;
   the wire passage through your framework's tool node is a live check).
6. **Paid tools accounting (live)** — if you connected the platform web tools, run a
   turn that searches and confirm BOTH meters ticked in the accounting store
   (`data/kdcube-storage/accounting/<tenant>/<project>/<date>/`): a `web_search`
   provider event for the search call and `llm` events for the result
   filtering/segmentation, all attributed to this conversation/turn.

---

## The shape, in one view

```
your Python agent (unchanged)          KDCube adds (the wrap + package)
──────────────────────────────         ────────────────────────────────
solution/  (vendored verbatim)   ──►    entrypoint.py   drive it via execute_core,
  its graph / framework                                 REBUILT every turn (no cache)
  its memory + persistence              stream_adapter  its stream → comm_ctx
  its streaming loop                    identity.py     platform identity → its keys
                                        + canonical package (docs/interface/config/tests)
                                        + (optional) capabilities / tools / code-exec
                                                     / turn workspace (read·pull·exec over links)
                                                     / file download / conversation title
```

Keep your framework. Add a wrap. Rebuild it per turn. Ship an app.
