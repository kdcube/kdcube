"""The streaming seam for the create_agent (looping-model) ReAct shape.

Drives ``stream_react_turn`` with stub graphs that yield LangGraph-shaped
astream_events for the ReAct loop, plus a fake communicator bound through
comm_ctx. Asserts the ReAct-specific rule:

  - an INTERMEDIATE model turn (empty content + a tool call) is NOT streamed as
    the answer, and its tool run surfaces as step(running)/step(completed);
  - only the FINAL model turn (text, no tool call) streams as answer deltas;
  - the offline path (final turn emits no token stream) streams the node's
    returned message content as a single delta.

A final smoke runs the REAL ``create_agent`` graph (offline stub model) through the
adapter, proving tokens flow from the ``"model"`` node end to end.

Fully offline — no DB, no API key.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from kdcube_ai_app.apps.chat.sdk.runtime import comm_ctx
from kdcube_ai_app.apps.chat.sdk.runtime.dynamic_module_loader import load_dynamic_module_for_path

BUNDLE_ROOT = Path(__file__).resolve().parents[1]


def _stream_module():
    _name, module = load_dynamic_module_for_path(BUNDLE_ROOT / "platform" / "stream_prebuilt.py")
    return module


class _FakeComm:
    def __init__(self) -> None:
        self.steps: list[tuple[str, str]] = []
        self.step_payloads: list[dict] = []
        self.deltas: list[str] = []
        self.complete_data: dict | None = None
        self.completed = False

    async def step(self, *, step: str, status: str, **payload) -> None:
        self.steps.append((step, status))
        self.step_payloads.append({"step": step, "status": status, **payload})

    async def delta(self, *, text: str, index: int, marker: str = "answer", **kwargs) -> None:
        self.deltas.append(text)

    async def complete(self, *, data=None) -> None:
        self.completed = True
        self.complete_data = data


def _chunk(content: str = "", tool_call_chunks=None):
    return SimpleNamespace(content=content, tool_call_chunks=tool_call_chunks)


def _ai(content: str, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls or [])


class _ToolLoopGraph:
    """A create_agent-ReAct-shaped stream: the model node decides a tool (empty
    content + tool call chunk), the tools node runs, then a FINAL model turn streams
    the answer."""

    async def astream_events(self, inputs, config, *, version=None):
        assert version == "v2"
        # -- intermediate model turn: a tool call, no visible answer text --
        yield {"event": "on_chain_start", "name": "model", "metadata": {"langgraph_node": "model"}}
        yield {
            "event": "on_chat_model_stream", "name": "model",
            "metadata": {"langgraph_node": "model"},
            "data": {"chunk": _chunk(content="", tool_call_chunks=[{"name": "calc", "args": "{}", "id": "1", "index": 0}])},
        }
        yield {
            "event": "on_chain_end", "name": "model",
            "metadata": {"langgraph_node": "model"},
            "data": {"output": {"messages": [_ai("", tool_calls=[{"name": "calc", "args": {}, "id": "1"}])]}},
        }
        # -- tools node runs the tool --
        yield {"event": "on_tool_start", "name": "calc", "metadata": {"langgraph_node": "tools"}}
        yield {"event": "on_tool_end", "name": "calc", "metadata": {"langgraph_node": "tools"},
               "data": {"output": _ai("42")}}
        # -- FINAL model turn: streams the answer, no tool call --
        yield {"event": "on_chain_start", "name": "model", "metadata": {"langgraph_node": "model"}}
        for tok in ("The ", "answer ", "is ", "42."):
            yield {
                "event": "on_chat_model_stream", "name": "model",
                "metadata": {"langgraph_node": "model"},
                "data": {"chunk": _chunk(content=tok)},
            }
        yield {
            "event": "on_chain_end", "name": "model",
            "metadata": {"langgraph_node": "model"},
            "data": {"output": {"messages": [_ai("The answer is 42.")]}},
        }


class _OfflineFinalGraph:
    """A single final model turn that emits NO token stream (offline/non-streaming
    model): the node's returned message content is streamed as a single delta."""

    async def astream_events(self, inputs, config, *, version=None):
        yield {"event": "on_chain_start", "name": "model", "metadata": {"langgraph_node": "model"}}
        yield {
            "event": "on_chain_end", "name": "model",
            "metadata": {"langgraph_node": "model"},
            "data": {"output": {"messages": [_ai("canned offline answer")]}},
        }


def _run(graph):
    async def _go():
        comm = _FakeComm()
        comm_ctx.set_comm(comm)
        answer = await _stream_module().stream_react_turn(
            graph, {"messages": [("user", "hi")]}, {"configurable": {"thread_id": "t"}},
            agent_node="model",
        )
        return comm, answer

    return asyncio.run(_go())


def test_final_turn_only_streams_answer_and_tool_surfaces_as_step() -> None:
    comm, answer = _run(_ToolLoopGraph())

    # Only the FINAL agent turn's tokens are the answer — the intermediate
    # tool-deciding turn contributed nothing to the answer.
    assert answer == "The answer is 42."
    assert "".join(comm.deltas) == "The answer is 42."

    # The tool run surfaced as a step lifecycle.
    assert ("calc", "running") in comm.steps
    assert ("calc", "completed") in comm.steps

    # Turn completes with the final answer.
    assert comm.completed is True
    assert comm.complete_data == {"final_answer": "The answer is 42."}


def test_offline_final_turn_streams_content_as_single_delta() -> None:
    comm, answer = _run(_OfflineFinalGraph())

    assert answer == "canned offline answer"
    assert comm.deltas == ["canned offline answer"]
    assert comm.completed is True
    assert comm.complete_data == {"final_answer": "canned offline answer"}


# ── tool-call visibility in the Steps view ───────────────────────────────────
# The step row must show HOW the tool was called (title = signature, body =
# arguments), each invocation as its own row — surfaced live when a truncated
# turn produced N run_python calls that all rendered as ONE bare "run_python /
# running" row, hiding that their arguments were empty.

class _TwoCallsGraph:
    """Two run_python invocations in one turn, args attached the way
    astream_events v2 delivers them (data.input + run_id)."""

    async def astream_events(self, inputs, config, *, version=None):
        yield {"event": "on_chain_start", "name": "model", "metadata": {"langgraph_node": "model"}}
        yield {
            "event": "on_tool_start", "name": "run_python", "run_id": "r1",
            "metadata": {"langgraph_node": "tools"},
            "data": {"input": {"code": "print('hello')\n" * 40, "prog_name": "news"}},
        }
        yield {"event": "on_tool_end", "name": "run_python", "run_id": "r1",
               "metadata": {"langgraph_node": "tools"}, "data": {"output": _ai("ok")}}
        yield {
            "event": "on_tool_start", "name": "run_python", "run_id": "r2",
            "metadata": {"langgraph_node": "tools"},
            "data": {"input": {}},
        }
        yield {"event": "on_tool_end", "name": "run_python", "run_id": "r2",
               "metadata": {"langgraph_node": "tools"}, "data": {"output": _ai("err")}}
        yield {
            "event": "on_chain_end", "name": "model",
            "metadata": {"langgraph_node": "model"},
            "data": {"output": {"messages": [_ai("done")]}},
        }


def test_tool_call_views_signature_and_args_body() -> None:
    m = _stream_module()
    code = "print('x')\n" * 200
    title, md = m._tool_call_views("run_python", {"code": code, "prog_name": "news"})
    # Signature: long values as size, short ones inline.
    assert title.startswith("run_python(code=<")
    assert "prog_name='news'" in title
    # Body: the code as a fenced python block, truncated with the total stated.
    assert "```python" in md
    assert "chars total" in md

    # Empty/missing args are STATED, not rendered like a healthy call.
    t_empty, md_empty = m._tool_call_views("run_python", {})
    assert t_empty == "run_python()"
    assert "No arguments received" in md_empty


def test_each_tool_invocation_is_its_own_step_with_signature() -> None:
    comm, _ = _run(_TwoCallsGraph())

    keys = [p["step"] for p in comm.step_payloads if p["status"] == "running"]
    assert keys == ["run_python", "run_python (2)"]

    first = next(p for p in comm.step_payloads if p["step"] == "run_python")
    assert first["title"].startswith("run_python(code=<")
    assert "```python" in first["markdown"]

    second = next(p for p in comm.step_payloads if p["step"] == "run_python (2)")
    assert second["title"] == "run_python()"
    assert "No arguments received" in second["markdown"]

    # Completion reuses the invocation's own key + signature (run_id-matched).
    done = [p for p in comm.step_payloads if p["status"] == "completed" and p["step"].startswith("run_python")]
    assert {p["step"] for p in done} == {"run_python", "run_python (2)"}
    assert all(p["title"] for p in done)


def _agent_module():
    _name, module = load_dynamic_module_for_path(BUNDLE_ROOT / "solution" / "lg_prebuilt" / "agent.py")
    return module


def _llm_module():
    _name, module = load_dynamic_module_for_path(BUNDLE_ROOT / "solution" / "lg_prebuilt" / "llm.py")
    return module


def test_real_create_agent_streams_tokens_from_model_node() -> None:
    # End-to-end offline proof: a REAL create_agent graph (offline stub model) driven
    # through the adapter with agent_node="model" streams answer deltas from the
    # "model" node. If the model node were misnamed, no delta would ever fire.
    agent = _agent_module()
    stub = _llm_module().StubChatModel()
    graph = agent.build_agent(model=stub, tools=[], summary_model=None)

    async def _go():
        comm = _FakeComm()
        comm_ctx.set_comm(comm)
        answer = await _stream_module().stream_react_turn(
            graph, {"messages": [("user", "just say hello")]},
            {"configurable": {"thread_id": "t"}},
            agent_node=agent.AGENT_NODE,
        )
        return comm, answer

    comm, answer = asyncio.run(_go())
    assert agent.AGENT_NODE == "model"
    assert answer  # tokens flowed from the model node
    assert "".join(comm.deltas) == answer
    assert comm.completed is True
