# Prebuilt ReAct agent — a standalone LangGraph prototype

A single-machine tool-using assistant built on **the agent everyone builds**:

```python
from langgraph.prebuilt import create_react_agent
graph = create_react_agent(model, tools, checkpointer=..., pre_model_hook=...)
```

It binds a few plain LangChain tools, streams the final answer token-by-token,
keeps short-term memory across turns with a Postgres checkpointer, and bounds the
model's context each turn. No dependency on any hosting platform — this is the
kind of "before" you would later wrap into a managed runtime.

## What it is

- **The agent** (`agent.py`): `create_react_agent(model, tools, checkpointer,
  pre_model_hook)`. The compiled graph is the standard ReAct loop with nodes
  `agent` (the looping model node) and `tools` (runs tool calls):

  ```text
  START ─▶ agent ─┬─(tool calls?)─▶ tools ─▶ agent ...   (loops)
                  └────── no tool calls ─────▶ END        (final message = answer)
  ```

- **Model** (`llm.py`): a LangChain `ChatOpenAI` or `ChatAnthropic`
  (`LG_PREBUILT_PROVIDER`), or a deterministic **offline stub** when no API key is
  set. The stub is tool-aware: it drives the real create_react loop (decides to
  call `calc` on arithmetic, then answers from the tool result), so the full graph
  shape runs without an API key.

- **Tools** (`tools.py`): three plain `@tool` functions — `calc` (safe arithmetic),
  `unit_convert` (length + temperature), and `kb_search` (keyword search over a
  small seeded local document list). Self-contained; no external service.

- **Short-term memory** (`cli.py`): an `AsyncPostgresSaver` checkpointer keyed by
  `thread_id`, so a conversation resumes across process restarts. Falls back to
  `MemorySaver` when no database is reachable.

- **Context management** (`context.py`): a `pre_model_hook` that runs before the
  model node each turn and **trims the messages the model sees** to a token budget
  (`LG_PREBUILT_CTX_TOKENS`, default 3000), always keeping the system message and
  the most recent turns.

- **CLI** (`cli.py`): a REPL that streams the final answer tokens and the ReAct
  loop (agent ↔ tools) live via `astream_events(version="v2")`.

## Where the conversation lives, and how context stays bounded

The conversation **is** the graph's `messages` list. It is persisted by the
**checkpointer in Postgres**, keyed by `thread_id` (one thread per user here). The
checkpointer holds the *full* history so a conversation resumes across restarts.

Left unbounded, that history would grow every turn and every model call would
re-send all of it — unbounded context and cost. The `pre_model_hook` fixes this:
before each model call it trims the message **view** to `LG_PREBUILT_CTX_TOKENS`
(keeping the system message + most recent turns) and returns it under
`llm_input_messages`. This bounds what the model *sees* **without deleting** the
stored history — the checkpointer keeps every turn; the model just doesn't see all
of it.

> A fuller alternative is a **summarization node** that compacts old turns into a
> running summary before trimming, keeping older context at a lower token cost.
> This prototype implements the trim, which is enough to keep context and spend
> controlled — and it is the clean default a host inherits unchanged.

## Streaming the ReAct loop (the one wrinkle)

The `agent` node **loops** — it fires once per tool-decision cycle. Only the
**last** agent turn (the one that makes no tool call) produces the answer. So the
CLI streams a model token as answer text only when it carries visible content and
**no tool-call chunk**; tool-deciding turns emit empty content plus a tool call,
and the `tools` runs surface as steps. This is exactly the detail a hosting
adapter must get right (see the port bundle's `platform/stream_adapter.py`).

## Run it locally

1. **Postgres** (optional — the agent runs without it, using in-memory state):

   ```bash
   docker run -d --name lg-prebuilt-pg -p 5432:5432 \
     -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=lg_prebuilt \
     postgres:16
   ```

2. **Install deps** (a virtualenv is recommended):

   ```bash
   pip install -r requirements.txt
   ```

3. **Set env** (defaults match the docker one-liner above):

   ```bash
   export DATABASE_URL="postgresql://postgres:postgres@localhost:5432/lg_prebuilt"
   export OPENAI_API_KEY="sk-..."          # or ANTHROPIC_API_KEY + LG_PREBUILT_PROVIDER=anthropic
   ```

4. **Run the REPL:**

   ```bash
   python -m lg_prebuilt_agent.cli --user alice
   ```

   Try *"what is 128 * 47?"* (watch the `calc` tool run), *"convert 10 km to miles"*,
   or *"how does create_react_agent decide it's done?"* (watch `kb_search`). Re-run
   later with the same `--user` and the conversation resumes.

### Offline / no-key mode

Without an API key, the agent runs in **offline stub mode**: a deterministic,
tool-aware fake model. The full create_react loop still executes — arithmetic
questions drive the `calc` tool, then the stub answers from the result — so you
can inspect and test the structure without spending on an LLM. Without a reachable
database the checkpointer falls back to in-memory (no cross-run persistence).

## What a hosting platform would still need to add

This prototype is deliberately single-machine. To run it as a real product a
platform layer would still need to provide: durable multi-tenant persistence and
isolation (here `--user` is a bare string and everyone shares one database),
streaming to a web UI (the CLI consumes `astream_events` in a terminal), auth,
accounting for the model calls, and deploy/operations. The companion port bundle
`bundles/lg-prebuilt-agent-port@2026-07-13/` wraps this agent, unchanged, to add
exactly those.
