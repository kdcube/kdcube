# Research Assistant — a standalone LangGraph prototype

A single-machine research assistant you could plausibly build on your laptop in an
afternoon. It answers questions from a small knowledge base, remembers facts and
preferences per user across turns, and delegates deep sub-questions to a subagent.
State is backed by local Postgres (pgvector for retrieval, a Postgres checkpointer
for conversation memory) and it runs from a CLI with token streaming.

This is a self-contained prototype — plain LangGraph, LangChain and Postgres, with
no dependency on any hosting platform. It is the kind of "before" you would later
wrap into a managed runtime.

## What it does

- **Knowledge base** (`knowledge.py`): a pgvector store of documents. Seeds a few
  sample docs on first run so a fresh install can answer immediately.
- **Memory** (`memory.py`): per-user semantic memory over pgvector. Facts and past
  exchanges are embedded and recalled by similarity, scoped by `--user`.
- **Subagent** (`subagent.py`): a nested `StateGraph` the main graph delegates a
  scoped sub-question to; it runs its own retrieve → synthesize and returns a
  compact finding.
- **Main graph** (`graph.py`): retrieve → plan → (optional delegate) → answer,
  compiled with a Postgres checkpointer keyed by `thread_id`, so a conversation
  resumes across process restarts.
- **CLI** (`cli.py`): a REPL that streams answer tokens and node steps live via
  `astream_events(version="v2")`.

## Architecture

```
                    ┌──────────────────────── CLI (REPL) ────────────────────────┐
                    │  input() ─▶ graph.astream_events(v2) ─▶ stream tokens+steps │
                    └───────────────────────────┬────────────────────────────────┘
                                                │  thread_id = cli-<user>
                                                ▼
   ┌───────────────────────────── main StateGraph ──────────────────────────────┐
   │                                                                             │
   │   START ─▶ retrieve ─▶ plan ─┬─(delegate?)─▶ delegate ─▶ answer ─▶ END      │
   │                              │                  │           │               │
   │                              └────── no ────────┼───────────┘               │
   │                                                 ▼                           │
   │                                        ┌── subagent sub-graph ──┐           │
   │                                        │ research ─▶ synthesize  │           │
   │                                        └────────────┬───────────┘           │
   └─────────────┬──────────────────┬───────────────────┼───────────────────────┘
                 │                  │                    │
                 ▼                  ▼                    ▼
        ┌──────────────┐   ┌────────────────┐   ┌────────────────────┐
        │ memories     │   │ kb_documents   │   │ checkpointer tables │
        │ (pgvector)   │   │ (pgvector)     │   │ (langgraph/pg)      │
        └──────────────┘   └────────────────┘   └────────────────────┘
                            Postgres (single instance)
```

## Run it locally

1. **Postgres with pgvector** (one-liner):

   ```bash
   docker run -d --name lg-pg -p 5432:5432 \
     -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=lg_solution \
     pgvector/pgvector:pg16
   ```

2. **Install deps** (a virtualenv is recommended):

   ```bash
   pip install -r requirements.txt
   ```

3. **Set env** (defaults match the docker one-liner above):

   ```bash
   export DATABASE_URL="postgresql://postgres:postgres@localhost:5432/lg_solution"
   export OPENAI_API_KEY="sk-..."
   ```

4. **Run the REPL:**

   ```bash
   python -m lg_solution.cli --user alice
   ```

   Ask something like *"How does LangGraph persist conversation state?"* and watch
   node steps and answer tokens stream. Re-run later with the same `--user` and the
   conversation (and your remembered facts) resume.

### Offline / no-key mode

Without `OPENAI_API_KEY`, the assistant runs in **offline stub mode**: deterministic
embeddings and a canned answer. The full graph shape still executes (including the
subagent branch), and if a database is reachable the vector stores work end to end.
This is enough to inspect and test the structure without spending on an LLM.

Without a reachable database, retrieval degrades to empty context with a clear
warning and the checkpointer falls back to in-memory (no cross-run persistence).

## What a hosting platform would still need to add

This prototype is deliberately single-machine. To run it as a real product a
platform layer would still need to provide:

- **Persistence beyond one process** — durable, backed-up storage and connection
  pooling rather than a local docker Postgres and a per-turn connection.
- **Streaming to a UI** — the CLI consumes `astream_events` in a terminal; a real
  product forwards those events to a web client over websockets/SSE.
- **Multi-user & isolation** — here `--user` is a bare string and everyone shares
  one database; a platform adds tenancy, per-user data isolation and quotas.
- **Auth** — there is no authentication; a platform authenticates users and
  authorizes what each may read/write.
- **Deploy & operations** — packaging, scaling, secrets management, observability
  and lifecycle, versus `python -m lg_solution.cli` on a laptop.
```
