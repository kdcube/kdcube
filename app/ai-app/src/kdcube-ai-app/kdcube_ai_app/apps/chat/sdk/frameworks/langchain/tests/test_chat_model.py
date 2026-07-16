# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Unit tests for KDCubeChatModel over a fake model service.

Fully offline: a stand-in ``models_service`` whose ``stream_model_text_tracked``
drives ``on_delta`` a couple of times proves the queue bridge re-yields each
token as a chunk (so ``astream_events`` would see ``on_chat_model_stream``), and
that ``ainvoke`` collapses the same stream to one message.
"""
from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessageChunk, HumanMessage
from langchain_core.tools import tool

from kdcube_ai_app.apps.chat.sdk.frameworks.langchain import KDCubeChatModel


class _FakeModelService:
    """Records the call and streams two tokens through on_delta."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def get_client(self, role, temperature):
        return ("client", role, temperature)

    def describe_client(self, client, role=None):
        return {"provider": "fake", "model": "fake-1", "role": role}

    async def stream_model_text_tracked(self, client, messages, *, on_delta, **kwargs):
        self.calls.append({"client": client, "messages": messages, "kwargs": kwargs})
        for tok in ("Hel", "lo"):
            await on_delta(tok)
        return {"text": "Hello", "usage": {}, "model_name": "fake-1"}


def test_astream_yields_token_chunks() -> None:
    ms = _FakeModelService()
    model = KDCubeChatModel(models_service=ms, role="unit.role", temperature=0.1, max_tokens=64)

    async def _go():
        chunks = []
        async for chunk in model.astream([HumanMessage(content="hi")]):
            # langchain_core 1.x appends a trailing empty metadata chunk; the
            # real consumer (stream_adapter) skips empty tokens, so do the same.
            if chunk.content:
                chunks.append(chunk.content)
        return chunks

    chunks = asyncio.run(_go())
    assert chunks == ["Hel", "lo"]

    # The bridge forwarded role/params/client_cfg to the accounted call.
    assert ms.calls and ms.calls[0]["kwargs"]["role"] == "unit.role"
    assert ms.calls[0]["kwargs"]["temperature"] == 0.1
    assert ms.calls[0]["kwargs"]["max_tokens"] == 64
    assert ms.calls[0]["kwargs"]["client_cfg"]["provider"] == "fake"


def test_ainvoke_collapses_stream_to_message() -> None:
    ms = _FakeModelService()
    model = KDCubeChatModel(models_service=ms, role="unit.role")

    async def _go():
        resp = await model.ainvoke([HumanMessage(content="hi")])
        return resp.content

    assert asyncio.run(_go()) == "Hello"


def test_astream_events_surfaces_on_chat_model_stream() -> None:
    """Proves the model participates in astream_events v2 as a chat model."""
    ms = _FakeModelService()
    model = KDCubeChatModel(models_service=ms, role="unit.role")

    async def _go():
        tokens = []
        async for ev in model.astream_events([HumanMessage(content="hi")], version="v2"):
            if ev.get("event") == "on_chat_model_stream":
                chunk = (ev.get("data") or {}).get("chunk")
                content = getattr(chunk, "content", "")
                if content:
                    tokens.append(content)
        return tokens

    assert asyncio.run(_go()) == ["Hel", "lo"]


# ---------------------------------------------------------------------------
# Tool support (bind_tools + tool-call streaming)
# ---------------------------------------------------------------------------


@tool
def get_weather(city: str) -> str:
    """Look up the weather for a city."""
    return f"sunny in {city}"


class _ToolEmittingModelService:
    """FakeMS that emits a tool call (start + arguments_delta) on the first turn.

    First call: streams ``tool.start`` + two ``tool.arguments_delta`` slices through
    ``on_tool_result_event`` (no text), mirroring a provider deciding to call a tool.
    Every later call: streams plain text through ``on_delta`` (the final answer turn
    after the tool result is fed back).
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def get_client(self, role, temperature):
        return ("client", role, temperature)

    def describe_client(self, client, role=None):
        return {"provider": "fake", "model": "fake-1", "role": role}

    async def stream_model_text_tracked(
        self, client, messages, *, on_delta, on_tool_result_event=None, tools=None, tool_choice=None, **kwargs
    ):
        self.calls.append({"tools": tools, "tool_choice": tool_choice, "messages": messages})
        if len(self.calls) == 1:
            # A tool-calling turn.
            assert on_tool_result_event is not None
            await on_tool_result_event({"type": "tool.start", "index": 0, "id": "call_1", "name": "get_weather"})
            await on_tool_result_event({"type": "tool.arguments_delta", "index": 0, "delta": '{"city":'})
            await on_tool_result_event({"type": "tool.arguments_delta", "index": 0, "delta": ' "Paris"}'})
            # A real provider also emits a completing tool.use; the bridge dedupes it
            # against the streamed deltas for this index, so args are not doubled.
            await on_tool_result_event({"type": "tool.use", "index": 0, "id": "call_1", "name": "get_weather", "input": {"city": "Paris"}})
            return {"text": "", "usage": {}, "model_name": "fake-1"}
        # A plain-text answer turn.
        for tok in ("It is ", "sunny."):
            await on_delta(tok)
        return {"text": "It is sunny.", "usage": {}, "model_name": "fake-1"}


def test_bind_tools_builds_react_agent() -> None:
    """create_react_agent constructs without hitting BaseChatModel.NotImplementedError."""
    from langgraph.prebuilt import create_react_agent

    ms = _ToolEmittingModelService()
    model = KDCubeChatModel(models_service=ms, role="unit.role")
    agent = create_react_agent(model, tools=[get_weather])
    assert agent is not None


def test_bind_tools_normalizes_and_forwards_tools() -> None:
    ms = _ToolEmittingModelService()
    model = KDCubeChatModel(models_service=ms, role="unit.role")
    bound = model.bind_tools([get_weather], tool_choice="auto")

    async def _go():
        async for _ in bound.astream([HumanMessage(content="weather in Paris?")]):
            pass

    asyncio.run(_go())
    # The normalized platform tool dict + tool_choice reached the accounted call.
    fwd = ms.calls[0]["tools"]
    assert fwd and fwd[0]["name"] == "get_weather"
    assert "parameters" in fwd[0] and fwd[0]["parameters"].get("type") == "object"
    assert ms.calls[0]["tool_choice"] == "auto"


def test_astream_emits_tool_call_chunks_and_aggregates_tool_calls() -> None:
    ms = _ToolEmittingModelService()
    model = KDCubeChatModel(models_service=ms, role="unit.role").bind_tools([get_weather])

    async def _go():
        merged = None
        saw_tool_chunk = False
        async for chunk in model.astream([HumanMessage(content="weather in Paris?")]):
            if chunk.tool_call_chunks:
                saw_tool_chunk = True
            merged = chunk if merged is None else merged + chunk
        return merged, saw_tool_chunk

    merged, saw_tool_chunk = asyncio.run(_go())
    assert saw_tool_chunk, "streamed chunks should carry tool_call_chunks"
    assert isinstance(merged, AIMessageChunk)
    assert merged.tool_calls, "aggregated message must expose .tool_calls"
    tc = merged.tool_calls[0]
    assert tc["name"] == "get_weather"
    assert tc["args"] == {"city": "Paris"}
    assert tc["id"] == "call_1"


class _ToolUseOnlyModelService(_ToolEmittingModelService):
    """A provider that delivers a tool call as ONE complete ``tool.use`` — NO
    streamed ``tool.start`` / ``tool.arguments_delta``. Some models (e.g. Haiku via
    the accounted path) do exactly this. The bridge must synthesize the tool_call
    chunk from ``tool.use`` alone, or the turn streams zero chunks and LangChain
    raises "No generations found in stream" — crashing the turn before the tool runs.
    """

    async def stream_model_text_tracked(
        self, client, messages, *, on_delta, on_tool_result_event=None, tools=None, tool_choice=None, **kwargs
    ):
        self.calls.append({"tools": tools, "tool_choice": tool_choice, "messages": messages})
        if len(self.calls) == 1:
            assert on_tool_result_event is not None
            await on_tool_result_event(
                {"type": "tool.use", "index": 0, "id": "call_1", "name": "get_weather", "input": {"city": "Paris"}}
            )
            return {"text": "", "usage": {}, "model_name": "fake-1"}
        for tok in ("It is ", "sunny."):
            await on_delta(tok)
        return {"text": "It is sunny.", "usage": {}, "model_name": "fake-1"}


def test_astream_synthesizes_chunk_from_complete_tool_use() -> None:
    # Regression: a complete (non-streamed) tool.use must still yield a tool_call
    # chunk so the stream is non-empty and the call reaches the agent loop.
    ms = _ToolUseOnlyModelService()
    model = KDCubeChatModel(models_service=ms, role="unit.role").bind_tools([get_weather])

    async def _go():
        merged = None
        chunk_count = 0
        async for chunk in model.astream([HumanMessage(content="weather in Paris?")]):
            chunk_count += 1
            merged = chunk if merged is None else merged + chunk
        return merged, chunk_count

    merged, chunk_count = asyncio.run(_go())
    assert chunk_count >= 1, "a complete tool.use must still produce at least one chunk"
    assert isinstance(merged, AIMessageChunk)
    assert merged.tool_calls, "the tool call must be surfaced from tool.use alone"
    tc = merged.tool_calls[0]
    assert tc["name"] == "get_weather"
    assert tc["args"] == {"city": "Paris"}, "args must not be doubled or empty"
    assert tc["id"] == "call_1"


def test_tool_use_only_drives_create_agent_loop() -> None:
    # End-to-end: create_agent must route the synthesized tool call to the tools
    # node and answer (the exact "No generations found in stream" crash, fixed).
    from langchain.agents import create_agent

    ms = _ToolUseOnlyModelService()
    model = KDCubeChatModel(models_service=ms, role="unit.role")
    agent = create_agent(model, [get_weather], system_prompt="x")

    async def _go():
        return await agent.ainvoke({"messages": [HumanMessage(content="weather in Paris?")]})

    out = asyncio.run(_go())
    kinds = [type(m).__name__ for m in out["messages"]]
    assert "ToolMessage" in kinds, f"tool must have run; got {kinds}"


class _EmptyModelService:
    """A model call that produces NOTHING — no text delta, no tool event. The
    bridge must still yield a (empty) chunk so the stream is never empty; an empty
    stream makes LangChain raise "No generations found in stream" and crash the turn.
    """

    def get_client(self, role, temperature):
        return ("client", role, temperature)

    def describe_client(self, client, role=None):
        return {"provider": "fake", "model": "fake-1", "role": role}

    async def stream_model_text_tracked(self, client, messages, *, on_delta, **kwargs):
        return {"text": "", "usage": {}, "model_name": "fake-1"}


def test_empty_model_response_does_not_crash_the_stream() -> None:
    # Regression: an empty model response yields one empty chunk (not zero), so
    # ainvoke returns an empty message instead of raising "No generations found".
    ms = _EmptyModelService()
    model = KDCubeChatModel(models_service=ms, role="unit.role")

    async def _go():
        chunks = [c async for c in model.astream([HumanMessage(content="hi")])]
        return chunks

    chunks = asyncio.run(_go())
    assert len(chunks) >= 1, "an empty response must still yield at least one chunk"

    async def _inv():
        return await model.ainvoke([HumanMessage(content="hi")])

    msg = asyncio.run(_inv())
    assert type(msg).__name__ == "AIMessage"
    assert msg.content == ""


def test_ainvoke_preserves_tool_calls_then_plain_content() -> None:
    ms = _ToolEmittingModelService()
    model = KDCubeChatModel(models_service=ms, role="unit.role").bind_tools([get_weather])

    async def _go():
        first = await model.ainvoke([HumanMessage(content="weather in Paris?")])
        second = await model.ainvoke([HumanMessage(content="thanks")])
        return first, second

    first, second = asyncio.run(_go())
    # First turn: tool call, no content.
    assert first.tool_calls and first.tool_calls[0]["name"] == "get_weather"
    assert first.tool_calls[0]["args"] == {"city": "Paris"}
    # Second turn (no tool events): plain content, no tool calls.
    assert not second.tool_calls
    assert second.content == "It is sunny."


class _CeilingToolCallService(_FakeModelService):
    """A tool-call turn that spends its ENTIRE output budget — the shape of a
    response truncated mid-tool-call (surfaced live: run_python(code=<HTML page>)
    cut at the ceiling on every retry until the graph recursion limit)."""

    async def stream_model_text_tracked(self, client, messages, *, on_delta, **kwargs):
        on_tool_event = kwargs.get("on_tool_result_event")
        if on_tool_event is not None:
            # A truncated-args call, the way the provider delivers it: the JSON
            # was cut mid-payload, so the parsed input arrives unusable/partial.
            await on_tool_event({
                "type": "tool.use", "id": "t1", "name": "run_python",
                "input": {"code": "html = '<html><body>...cut here"}, "index": 0,
            })
        return {"text": "", "usage": {"output_tokens": 64}, "model_name": "fake-1"}


def test_interrupted_tool_call_is_explained_to_the_model_and_the_log(caplog) -> None:
    """Neither audience may be left guessing: the MODEL gets an in-band notice in
    its own (interrupted) message — so the next round it acts on 'I was cut off'
    instead of confabulating around the tool's 'missing argument' error — and the
    LOG records the evidence (budget, spend, each reconstructed call's name, args
    size, and the tail where the cut hit)."""
    import logging

    ms = _CeilingToolCallService()
    model = KDCubeChatModel(models_service=ms, role="unit.role", max_tokens=64).bind_tools([get_weather])

    async def _go():
        return await model.ainvoke([HumanMessage(content="make the page")])

    with caplog.at_level(logging.WARNING, logger="kdcube.langchain.chat_model"):
        msg = asyncio.run(_go())

    # To the MODEL: the notice rides the assistant message beside the truncated call.
    assert msg.tool_calls and msg.tool_calls[0]["name"] == "run_python"
    assert "interrupted" in msg.content
    assert "maximum output length" in msg.content
    assert "Do not repeat the identical call" in msg.content

    # To the LOG: the evidence — what was in flight when the budget ran out.
    warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    hit = next(m for m in warnings if "INTERRUPTED" in m)
    assert "max_tokens=64" in hit and "output_tokens=64" in hit
    assert "run_python" in hit and "args=" in hit and "cut here" in hit


def test_under_budget_turn_does_not_warn(caplog) -> None:
    import logging

    ms = _FakeModelService()  # usage empty -> 0 output tokens
    model = KDCubeChatModel(models_service=ms, role="unit.role", max_tokens=64)

    with caplog.at_level(logging.WARNING, logger="kdcube.langchain.chat_model"):
        asyncio.run(model.ainvoke([HumanMessage(content="hi")]))

    assert not [r for r in caplog.records if "max_tokens" in r.message and r.levelno >= logging.WARNING]
