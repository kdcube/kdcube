"""Interactive REPL for the research assistant.

    python -m lg_solution.cli --user alice

Reads a line, runs the graph with a stable thread_id (per user), and streams the
answer token-by-token via `astream_events(version="v2")`, printing node steps as
they happen. This astream_events loop is the exact surface a hosting platform
would adapt to its own streaming/comm events.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys

from .deps import build_deps

NODE_STEPS = {"retrieve", "plan", "delegate", "answer"}


async def _run_turn(graph, user_id: str, thread_id: str, question: str) -> None:
    """One turn: stream node steps + answer tokens for a single question."""
    run_config = {"configurable": {"thread_id": thread_id}}
    inputs = {"question": question, "user_id": user_id, "messages": [("user", question)]}

    printed_answer_header = False
    async for event in graph.astream_events(inputs, run_config, version="v2"):
        kind = event["event"]
        name = event.get("name")
        node = event.get("metadata", {}).get("langgraph_node")

        if kind == "on_chain_start" and name in NODE_STEPS:
            print(f"\n  · {name} …", file=sys.stderr, flush=True)

        elif kind == "on_chat_model_stream" and node == "answer":
            if not printed_answer_header:
                print("\nassistant> ", end="", flush=True)
                printed_answer_header = True
            chunk = event["data"]["chunk"]
            token = getattr(chunk, "content", "") or ""
            if token:
                print(token, end="", flush=True)

        elif kind == "on_chain_end" and name == "answer":
            # Offline/stub mode does not emit token stream events; print once.
            if not printed_answer_header:
                answer = (event["data"].get("output") or {}).get("answer", "")
                print(f"\nassistant> {answer}", flush=True)
                printed_answer_header = True

    print("\n", flush=True)


@contextlib.asynccontextmanager
async def _open_graph(deps):
    """Compile the graph with a Postgres checkpointer keyed by thread_id.
    Falls back to an in-memory saver (no cross-run persistence) if the DB is
    unreachable, so the CLI still runs for inspection."""
    from .graph import build_graph

    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        cm = AsyncPostgresSaver.from_conn_string(deps.config.database_url)
        checkpointer = await cm.__aenter__()
        await checkpointer.setup()
        try:
            with contextlib.suppress(Exception):
                deps.knowledge.seed()
            yield build_graph(deps, checkpointer=checkpointer)
        finally:
            await cm.__aexit__(None, None, None)
        return
    except Exception as e:  # noqa: BLE001
        print(f"[warn] Postgres checkpointer unavailable ({e}); "
              "using in-memory state (no persistence across runs).", file=sys.stderr)

    from langgraph.checkpoint.memory import MemorySaver

    with contextlib.suppress(Exception):
        deps.knowledge.seed()
    yield build_graph(deps, checkpointer=MemorySaver())


async def _repl(user_id: str) -> None:
    deps = build_deps()
    mode = "OFFLINE (stub LLM)" if deps.config.offline else f"model={deps.config.chat_model}"
    print(f"research-assistant · user={user_id} · {mode}")
    print("Type a question. Ctrl-D or 'exit' to quit.\n")

    thread_id = f"cli-{user_id}"
    async with _open_graph(deps) as graph:
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
                await _run_turn(graph, user_id, thread_id, question)
            except Exception as e:  # noqa: BLE001
                print(f"\n[error] turn failed: {e}\n", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone LangGraph research assistant (prototype).")
    parser.add_argument("--user", default="local", help="user id; scopes per-user memory + thread_id")
    args = parser.parse_args()
    asyncio.run(_repl(args.user))


if __name__ == "__main__":
    main()
