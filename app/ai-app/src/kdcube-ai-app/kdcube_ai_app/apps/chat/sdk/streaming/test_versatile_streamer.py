# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
#
# Tests and usage examples for the versatile channeled streamer.

import json

import pytest

from kdcube_ai_app.apps.chat.sdk.streaming.versatile_streamer import ChannelSpec, stream_with_channels
from kdcube_ai_app.apps.chat.sdk.solutions.widgets.exec import DecisionExecCodeStreamer


HISTORICAL_EXEC_CODE_MISS_CHUNK_SIZES = [56, 62, 69, 70, 79, 80, 92, 93, 110, 111, 112]


class _FakeService:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def get_client(self, _role):
        return object()

    def describe_client(self, _client, role=None):
        return type("Cfg", (), {"provider": "fake", "model_name": role or "fake"})()

    async def stream_model_text_tracked(
        self,
        _client,
        messages,
        on_delta,
        on_complete,
        temperature,
        max_tokens,
        client_cfg,
        debug,
        role,
        debug_citations=False,
    ):
        for ch in self._chunks:
            await on_delta(ch)
        await on_complete({})
        return {"text": "".join(self._chunks), "service_error": None}


@pytest.mark.asyncio
async def test_stream_with_channels_citations_and_usage():
    """
    Demonstrates:
      - multi-channel protocol
      - in-stream citation replacement
      - raw storage preserving [[S:n]] tokens
    """
    chunks = [
        "<channel:answer>Hi [[S:",
        "1]]</channel:answer><channel:usage>[\"S1\"]</channel:usage>",
    ]
    svc = _FakeService(chunks)
    events = []

    async def _emit(**kwargs):
        events.append(kwargs)

    sources_list = [{"sid": 1, "title": "Example", "url": "https://example.com", "text": "X"}]
    channels = [
        ChannelSpec(name="answer", format="markdown", replace_citations=True, emit_marker="answer"),
        ChannelSpec(name="usage", format="json", replace_citations=False, emit_marker="answer"),
    ]

    results, meta = await stream_with_channels(
        svc=svc,
        messages=["sys", "user"],
        role="answer.generator.regular",
        channels=channels,
        emit=_emit,
        agent="test.agent",
        artifact_name="demo",
        sources_list=sources_list,
        max_tokens=200,
        temperature=0.0,
        return_full_raw=True,
    )
    assert meta.get("service_error") is None
    assert meta.get("raw") == "".join(chunks)

    rendered_answer = "".join(
        e.get("text", "") for e in events if e.get("channel") == "answer" and e.get("text")
    )
    assert "[[S:1]]" not in rendered_answer
    assert "https://example.com" in rendered_answer
    assert results["answer"].raw.strip() == "Hi [[S:1]]"
    assert results["answer"].used_sources == [1]


@pytest.mark.asyncio
async def test_stream_with_channels_json_fanout_to_canvas():
    """
    Demonstrates:
      - JSON channel used as source stream
      - per-attribute deltas emitted to a different marker (canvas)
      - only a subset of JSON attributes are streamed to canvas
      - top-level keys only (nested paths are not supported by CompositeJsonArtifactStreamer)
    """
    chunks = [
        "<channel:answer>{\"artifactA\":\"Hello [[S:1]]\",\"artifactB\":\"World [[S:1]]\",\"artifactC\":\"No stream\"}</channel:answer>",
        "<channel:usage>[\"S1\"]</channel:usage>",
    ]
    svc = _FakeService(chunks)
    events = []

    async def _emit(**kwargs):
        events.append(kwargs)

    sources_list = [{"sid": 1, "title": "Example", "url": "https://example.com", "text": "X"}]
    channels = [
        ChannelSpec(name="answer", format="json", replace_citations=False, emit_marker="answer"),
        ChannelSpec(name="usage", format="json", replace_citations=False, emit_marker="answer"),
    ]

    results, meta = await stream_with_channels(
        svc=svc,
        messages=["sys", "user"],
        role="answer.generator.regular",
        channels=channels,
        emit=_emit,
        agent="test.agent",
        artifact_name="demo.json",
        sources_list=sources_list,
        composite_cfg={"artifactA": "markdown", "artifactB": "markdown"},
        composite_channel="answer", # in source
        composite_marker="canvas", # comm recipient
        max_tokens=200,
        temperature=0.0,
        return_full_raw=True,
    )
    assert meta.get("service_error") is None
    assert meta.get("raw") == "".join(chunks)

    canvas_text = "".join(
        e.get("text", "") for e in events if e.get("marker") == "canvas" and e.get("text")
    )
    assert "https://example.com" in canvas_text
    assert any(
        e.get("marker") == "canvas" and e.get("artifact_name") == "artifactA" for e in events
    )
    assert any(
        e.get("marker") == "canvas" and e.get("artifact_name") == "artifactB" for e in events
    )
    assert not any(
        e.get("marker") == "canvas" and e.get("artifact_name") == "artifactC" for e in events
    )
    # Reconstruct per-attribute content from stream
    by_key = {"artifactA": [], "artifactB": [], "artifactC": []}
    for e in events:
        if e.get("marker") != "canvas":
            continue
        name = e.get("artifact_name")
        if name in by_key and e.get("text"):
            by_key[name].append(e.get("text"))
    assert "Hello" in "".join(by_key["artifactA"])
    assert "World" in "".join(by_key["artifactB"])
    assert "".join(by_key["artifactC"]) == ""


@pytest.mark.asyncio
async def test_stream_with_channels_ignores_literal_channel_mentions_inside_content():
    chunks = [
        "<channel:thinking>Explain the literal syntax `",
        "<channel:code></channel:code>` to the user.</channel:thinking>\n",
        "<channel:answer>Done.</channel:answer>\n",
        "<channel:code></channel:code>",
    ]
    svc = _FakeService(chunks)
    events = []

    async def _emit(**kwargs):
        events.append(kwargs)

    channels = [
        ChannelSpec(name="thinking", format="markdown", replace_citations=False, emit_marker="thinking"),
        ChannelSpec(name="answer", format="markdown", replace_citations=False, emit_marker="answer"),
        ChannelSpec(name="code", format="text", replace_citations=False, emit_marker="subsystem"),
    ]

    results, meta = await stream_with_channels(
        svc=svc,
        messages=["sys", "user"],
        role="answer.generator.regular",
        channels=channels,
        emit=_emit,
        agent="test.agent",
        artifact_name="demo",
        max_tokens=200,
        temperature=0.0,
        return_full_raw=True,
    )

    assert meta.get("service_error") is None
    assert "`<channel:code></channel:code>`" in results["thinking"].raw
    assert "Done." in results["answer"].raw
    assert results["code"].raw == ""


@pytest.mark.asyncio
async def test_stream_with_channels_captures_realistic_exec_code_payload():
    output_file = "turn_demo_exec_001/outputs/monthly_priorities.xlsx"
    payload = {
        "action": "call_tool",
        "notes": "Create the workbook.",
        "tool_call": {
            "tool_id": "exec_tools.execute_code_python",
            "params": {
                "prog_name": "monthly_priorities",
                "contract": [
                    {
                        "filename": output_file,
                        "description": "Excel workbook output.",
                        "visibility": "external",
                    }
                ],
            },
        },
    }
    code_text = (
        "import openpyxl\n"
        "from pathlib import Path\n\n"
        f"out_path = Path(OUTPUT_DIR) / \"{output_file}\"\n"
        "out_path.parent.mkdir(parents=True, exist_ok=True)\n"
        "print(f\"Saved: {out_path}\")\n"
    )
    text = (
        "<channel:thinking>Need a small Excel output.</channel:thinking>\n"
        f"<channel:ReactDecisionOutV2>```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```</channel:ReactDecisionOutV2>\n"
        f"<channel:code>\n{code_text}</channel:code>"
    )
    from kdcube_ai_app.apps.chat.sdk.streaming.versatile_streamer import ChannelSubscribers

    for chunk_size in HISTORICAL_EXEC_CODE_MISS_CHUNK_SIZES:
        svc = _FakeService([text[i:i + chunk_size] for i in range(0, len(text), chunk_size)])
        events = []

        async def _emit(**kwargs):
            events.append(kwargs)

        widget = DecisionExecCodeStreamer(
            emit_delta=_emit,
            agent="test.exec",
            artifact_name="react.exec.test",
            execution_id="exec_demo",
        )

        subscribers = (
            ChannelSubscribers()
            .subscribe("ReactDecisionOutV2", widget.feed_json)
            .subscribe("code", widget.feed_code)
        )

        results, meta = await stream_with_channels(
            svc=svc,
            messages=["sys", "user"],
            role="answer.generator.regular",
            channels=[
                ChannelSpec(name="thinking", format="markdown", replace_citations=False, emit_marker="thinking"),
                ChannelSpec(name="ReactDecisionOutV2", format="json", replace_citations=False, emit_marker="answer"),
                ChannelSpec(name="code", format="text", replace_citations=False, emit_marker="subsystem"),
            ],
            emit=_emit,
            agent="test.agent",
            artifact_name="react.decision",
            subscribers=subscribers,
            max_tokens=600,
            temperature=0.0,
            return_full_raw=True,
        )

        assert meta.get("service_error") is None
        assert results["ReactDecisionOutV2"].error is None
        assert code_text in results["code"].raw, chunk_size
        assert code_text in widget.get_code(), chunk_size
