"""Interactive REPL for the prebuilt ReAct agent.

    python -m lg_prebuilt_agent.cli --user alice

Reads a line, runs the agent with a stable ``thread_id`` (per user), and streams
the FINAL answer token-by-token via ``astream_events(version="v2")``, printing the
create_react loop (agent <-> tools) as it happens.

Streaming the create_react loop has one wrinkle worth seeing here, because it is
the exact thing a hosting adapter must get right: the ``agent`` node LOOPS — it
fires once per tool-decision cycle. Only the LAST agent turn (the one that makes
no tool call) produces the answer. So we stream a model token as answer text only
when it carries visible content and no tool-call chunk; tool-deciding turns emit
empty content + a tool call, and the ``tools`` node runs surface as steps.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys

from .agent import AGENT_NODE, build_agent
from .config import get_config


async def _run_turn(graph, thread_id: str, question: str) -> None:
    """One turn: stream the create_react loop + the final answer for a question."""
    run_config = {"configurable": {"thread_id": thread_id}}
    inputs = {"messages": [("user", question)]}

    printed_answer_header = False
    turn_had_tool_call = False

    async for event in graph.astream_events(inputs, run_config, version="v2"):
        kind = event["event"]
        name = event.get("name")
        node = (event.get("metadata") or {}).get("langgraph_node")

        if kind == "on_chat_model_stream" and node == AGENT_NODE:
            chunk = event["data"]["chunk"]
            # A tool-call chunk marks this agent turn as a tool-deciding turn, not
            # the final answer — never stream it as answer text.
            if getattr(chunk, "tool_call_chunks", None):
                turn_had_tool_call = True
                continue
            token = getattr(chunk, "content", "") or ""
            if token and not turn_had_tool_call:
                if not printed_answer_header:
                    print("\nassistant> ", end="", flush=True)
                    printed_answer_header = True
                print(token, end="", flush=True)

        elif kind == "on_chain_start" and name == AGENT_NODE:
            turn_had_tool_call = False  # new agent turn

        elif kind == "on_tool_start":
            print(f"\n  · tool {name} …", file=sys.stderr, flush=True)

        elif kind == "on_tool_end":
            out = (event.get("data") or {}).get("output")
            content = getattr(out, "content", out)
            print(f"  · tool {name} -> {str(content)[:80]}", file=sys.stderr, flush=True)

        elif kind == "on_chain_end" and name == AGENT_NODE:
            # Offline/stub or non-streaming models may not emit token events —
            # take the final agent turn's message content (only if it made no
            # tool call) and print it once.
            out = (event.get("data") or {}).get("output") or {}
            msgs = out.get("messages") if isinstance(out, dict) else None
            last = msgs[-1] if msgs else None
            if last is not None and not getattr(last, "tool_calls", None) and not printed_answer_header:
                content = getattr(last, "content", "") or ""
                if content:
                    print(f"\nassistant> {content}", flush=True)
                    printed_answer_header = True

    print("\n", flush=True)


@contextlib.asynccontextmanager
async def _open_graph():
    """Compile the agent with a Postgres checkpointer keyed by thread_id, falling
    back to an in-memory saver (no cross-run persistence) if the DB is
    unreachable, so the CLI still runs for inspection."""
    config = get_config()
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        cm = AsyncPostgresSaver.from_conn_string(config.database_url)
        checkpointer = await cm.__aenter__()
        await checkpointer.setup()
        try:
            yield build_agent(config, checkpointer=checkpointer)
        finally:
            await cm.__aexit__(None, None, None)
        return
    except Exception as e:  # noqa: BLE001
        print(f"[warn] Postgres checkpointer unavailable ({e}); using in-memory "
              "state (no persistence across runs).", file=sys.stderr)

    from langgraph.checkpoint.memory import MemorySaver

    yield build_agent(config, checkpointer=MemorySaver())


async def _repl(user_id: str) -> None:
    config = get_config()
    mode = "OFFLINE (stub model)" if config.offline else f"provider={config.provider} model={config.model_name}"
    print(f"lg-prebuilt-agent · user={user_id} · {mode}")
    print("Type a question. Ctrl-D or 'exit' to quit.\n")

    thread_id = f"cli-{user_id}"
    async with _open_graph() as graph:
        loop = asyncio.get_event_loop()
        while True:
            try:
                line = await loop.run_in_executor(None, lambda: input(f"[{user_id}]> "))
            except (EOFError, KeyboardInterrupt):
                print()
                break
            question = line.strip()
            if not question:
                continue
            if question.lower() in {"exit", "quit"}:
                break
            try:
                await _run_turn(graph, thread_id, question)
            except Exception as e:  # noqa: BLE001
                print(f"\n[error] turn failed: {e}\n", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone prebuilt LangGraph ReAct agent (prototype).")
    parser.add_argument("--user", default="local", help="user id; scopes the thread_id / checkpointer")
    args = parser.parse_args()
    asyncio.run(_repl(args.user))


if __name__ == "__main__":
    main()
