# SPDX-License-Identifier: MIT

import json

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.widgets.canvas import (
    RenderingWriteContentStreamer,
    ReactWriteContentStreamer,
    TimelineStreamer,
)
from kdcube_ai_app.apps.chat.sdk.solutions.widgets.exec import DecisionExecCodeStreamer
from kdcube_ai_app.apps.chat.sdk.streaming.versatile_streamer_v3 import (
    ChannelSpec,
    ChannelSubscribers,
    stream_with_channels,
)


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
        del messages, temperature, max_tokens, client_cfg, debug, role, debug_citations
        for chunk in self._chunks:
            await on_delta(chunk)
        await on_complete({})
        return {"text": "".join(self._chunks), "service_error": None}


class _Collector:
    def __init__(self):
        self.events = []

    async def emit(self, **kwargs):
        self.events.append(dict(kwargs))

    def text_for_artifact(self, artifact_name: str) -> str:
        return "".join(
            e.get("text", "")
            for e in self.events
            if e.get("artifact_name") == artifact_name and e.get("text")
        )

    def events_for_channel(self, channel: str):
        return [e for e in self.events if e.get("channel") == channel]

    def events_for_artifact(self, artifact_name: str):
        return [e for e in self.events if e.get("artifact_name") == artifact_name]


def _chunk_text(text: str, size: int = 17):
    return [text[i : i + size] for i in range(0, len(text), size)]


def _json_channel(name: str, payload: dict) -> str:
    return f"<channel:{name}>```json\n{json.dumps(payload, ensure_ascii=True)}\n```</channel:{name}>"


def _text_channel(name: str, text: str) -> str:
    return f"<channel:{name}>{text}</channel:{name}>"


def _wrap_json_widget(widget):
    async def _emit(text: str = "", completed: bool = False, **_kwargs):
        if completed:
            await widget.finish()
            return
        await widget.feed(text)

    return _emit


@pytest.mark.asyncio
async def test_stream_with_channels_v3_fans_out_repeated_decisions_to_isolated_widgets():
    payload_a = {
        "action": "call_tool",
        "notes": "Write section alpha.",
        "tool_call": {
            "tool_id": "react.write",
            "params": {
                "path": "files/alpha.md",
                "channel": "canvas",
                "content": "Alpha body.",
            },
        },
    }
    payload_b = {
        "action": "call_tool",
        "notes": "Write section beta.",
        "tool_call": {
            "tool_id": "react.write",
            "params": {
                "path": "files/beta.md",
                "channel": "canvas",
                "content": "Beta body.",
            },
        },
    }
    full = (
        _json_channel("ReactDecisionOutV2", payload_a)
        + _json_channel("ReactDecisionOutV2", payload_b)
    )

    svc = _FakeService(_chunk_text(full, size=19))
    collector = _Collector()
    created_instances = []
    global_events = []

    async def _global_json_subscriber(**kwargs):
        global_events.append(
            {
                "channel": kwargs.get("channel"),
                "channel_instance": kwargs.get("channel_instance"),
                "completed": kwargs.get("completed"),
                "text": kwargs.get("text"),
            }
        )

    def _factory(channel: str, instance_idx: int):
        created_instances.append((channel, instance_idx))
        record = ReactWriteContentStreamer(
            emit_delta=collector.emit,
            agent=f"test.record.{instance_idx}",
            artifact_name=f"react.record.{instance_idx}",
            turn_id="turn_1",
            sources_list=[],
        )
        timeline = TimelineStreamer(
            emit_delta=collector.emit,
            agent=f"test.timeline.{instance_idx}",
            sources_list=[],
            notes_artifact_name=f"timeline_text.react.decision.{instance_idx}",
            final_answer_artifact_name=f"react.final_answer.{instance_idx}",
            plan_artifact_name=f"timeline_text.react.plan.{instance_idx}",
        )
        return [
            _wrap_json_widget(record),
            _wrap_json_widget(timeline),
        ]

    subscribers = (
        ChannelSubscribers()
        .subscribe("ReactDecisionOutV2", _global_json_subscriber)
        .subscribe_factory("ReactDecisionOutV2", _factory)
    )

    results, meta = await stream_with_channels(
        svc=svc,
        messages=["sys", "user"],
        role="answer.generator.regular",
        channels=[ChannelSpec(name="ReactDecisionOutV2", format="json", replace_citations=False, emit_marker="answer")],
        emit=collector.emit,
        agent="test.agent",
        artifact_name="react.decision",
        subscribers=subscribers,
        max_tokens=300,
        temperature=0.0,
        return_full_raw=True,
    )

    assert meta.get("service_error") is None
    assert results["ReactDecisionOutV2"].raw
    assert created_instances == [("ReactDecisionOutV2", 0), ("ReactDecisionOutV2", 1)]

    assert "Alpha body." in collector.text_for_artifact("alpha.md")
    assert "Beta body." in collector.text_for_artifact("beta.md")
    assert "Beta body." not in collector.text_for_artifact("alpha.md")
    assert "Alpha body." not in collector.text_for_artifact("beta.md")

    assert "Write section alpha." in collector.text_for_artifact("timeline_text.react.decision.0")
    assert "Write section beta." in collector.text_for_artifact("timeline_text.react.decision.1")

    completed_instances = sorted(
        {
            int(e.get("channel_instance"))
            for e in global_events
            if e.get("completed") is True and e.get("channel") == "ReactDecisionOutV2"
        }
    )
    assert completed_instances == [0, 1]


@pytest.mark.asyncio
async def test_stream_with_channels_v3_drives_exec_widget_from_single_decision_and_code_channel():
    decision_payload = {
        "action": "call_tool",
        "tool_call": {
            "tool_id": "exec_tools.execute_code_python",
            "params": {
                "prog_name": "demo.py",
            },
        },
    }
    code_text = "print('hello')\nprint('world')\n"
    full = _json_channel("ReactDecisionOutV2", decision_payload) + _text_channel("code", code_text)

    svc = _FakeService(_chunk_text(full, size=11))
    collector = _Collector()
    widget = DecisionExecCodeStreamer(
        emit_delta=collector.emit,
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
            ChannelSpec(name="ReactDecisionOutV2", format="json", replace_citations=False, emit_marker="answer"),
            ChannelSpec(name="code", format="text", replace_citations=False, emit_marker="answer"),
        ],
        emit=collector.emit,
        agent="test.agent",
        artifact_name="react.decision",
        subscribers=subscribers,
        max_tokens=300,
        temperature=0.0,
        return_full_raw=True,
    )

    assert meta.get("service_error") is None
    assert results["ReactDecisionOutV2"].raw
    assert code_text in results["code"].raw
    assert code_text in widget.get_code()

    assert "demo.py" in collector.text_for_artifact("react.exec.test.code_exec_program_name")
    assert "print('hello')" in collector.text_for_artifact("react.exec.test.code_exec_code")
    assert "print('world')" in collector.text_for_artifact("react.exec.test.code_exec_code")
    assert any(
        e.get("artifact_name") == "react.exec.test.code_exec_status"
        and "\"status\": \"gen\"" in str(e.get("text") or "")
        for e in collector.events
    )
    assert any(
        e.get("artifact_name") == "react.exec.test.code_exec_code" and e.get("completed") is True
        for e in collector.events
    )


@pytest.mark.asyncio
async def test_stream_with_channels_v3_uses_declared_rendering_format_for_pdf_stream():
    decision_payload = {
        "action": "call_tool",
        "tool_call": {
            "tool_id": "rendering_tools.write_pdf",
            "params": {
                "path": "outputs/science_news/top_news_april_2026.pdf",
                "format": "html",
                "title": "Top Science News",
                "content": "<!DOCTYPE html><html><body><h1>Top Science News</h1></body></html>",
            },
        },
    }
    full = _json_channel("ReactDecisionOutV2", decision_payload)

    svc = _FakeService(_chunk_text(full, size=13))
    collector = _Collector()
    widget = RenderingWriteContentStreamer(
        emit_delta=collector.emit,
        agent="test.render",
        artifact_name="react.render.test",
        turn_id="turn_1",
        sources_list=[],
        write_tool_prefix="rendering_tools.write_",
    )

    subscribers = ChannelSubscribers().subscribe("ReactDecisionOutV2", _wrap_json_widget(widget))

    results, meta = await stream_with_channels(
        svc=svc,
        messages=["sys", "user"],
        role="answer.generator.regular",
        channels=[ChannelSpec(name="ReactDecisionOutV2", format="json", replace_citations=False, emit_marker="answer")],
        emit=collector.emit,
        agent="test.agent",
        artifact_name="react.decision",
        subscribers=subscribers,
        max_tokens=300,
        temperature=0.0,
        return_full_raw=True,
    )

    assert meta.get("service_error") is None
    assert results["ReactDecisionOutV2"].raw

    artifact_events = collector.events_for_artifact("science_news/top_news_april_2026.pdf")
    assert artifact_events
    first_content = next(e for e in artifact_events if e.get("text"))
    assert first_content.get("format") == "html"
    assert first_content.get("marker") == "canvas"
    assert "<!DOCTYPE html>" in collector.text_for_artifact("science_news/top_news_april_2026.pdf")
    assert any(e.get("completed") is True and e.get("format") == "html" for e in artifact_events)


@pytest.mark.asyncio
async def test_stream_with_channels_v3_factory_failure_does_not_break_other_subscribers():
    payload = {
        "action": "call_tool",
        "tool_call": {
            "tool_id": "react.read",
            "params": {"paths": ["one"]},
        },
    }
    svc = _FakeService([_json_channel("ReactDecisionOutV2", payload)])
    collector = _Collector()
    created = []
    good_events = []

    async def _global_subscriber(**kwargs):
        good_events.append(("global", kwargs.get("channel_instance"), kwargs.get("completed")))

    async def _instance_subscriber(**kwargs):
        good_events.append(("instance", kwargs.get("channel_instance"), kwargs.get("completed")))

    def _bad_factory(channel: str, instance_idx: int):
        created.append(("bad", channel, instance_idx))
        raise RuntimeError("boom")

    def _good_factory(channel: str, instance_idx: int):
        created.append(("good", channel, instance_idx))
        return [_instance_subscriber]

    subscribers = (
        ChannelSubscribers()
        .subscribe("ReactDecisionOutV2", _global_subscriber)
        .subscribe_factory("ReactDecisionOutV2", _bad_factory)
        .subscribe_factory("ReactDecisionOutV2", _good_factory)
    )

    results, meta = await stream_with_channels(
        svc=svc,
        messages=["sys", "user"],
        role="answer.generator.regular",
        channels=[ChannelSpec(name="ReactDecisionOutV2", format="json", replace_citations=False, emit_marker="answer")],
        emit=collector.emit,
        agent="test.agent",
        artifact_name="react.decision",
        subscribers=subscribers,
        max_tokens=200,
        temperature=0.0,
        return_full_raw=True,
    )

    assert meta.get("service_error") is None
    assert results["ReactDecisionOutV2"].raw
    assert created == [
        ("bad", "ReactDecisionOutV2", 0),
        ("good", "ReactDecisionOutV2", 0),
    ]
    assert ("global", 0, True) in good_events
    assert ("instance", 0, True) in good_events


@pytest.mark.asyncio
async def test_stream_with_channels_v3_handles_split_tags_and_mismatched_close_tags():
    payload = {
        "action": "call_tool",
        "tool_call": {
            "tool_id": "react.read",
            "params": {"paths": ["alpha"]},
        },
    }
    text = (
        "</channel:ReactDecisionOutV2>"
        "<channel:ReactDecisionOutV2>"
        f"{json.dumps(payload, ensure_ascii=True)}"
        "</channel:wrong>"
        "</channel:ReactDecisionOutV2>"
    )
    svc = _FakeService(_chunk_text(text, size=7))
    collector = _Collector()
    seen = []

    async def _sub(**kwargs):
        seen.append(
            {
                "channel_instance": kwargs.get("channel_instance"),
                "completed": kwargs.get("completed"),
                "text": kwargs.get("text"),
            }
        )

    results, meta = await stream_with_channels(
        svc=svc,
        messages=["sys", "user"],
        role="answer.generator.regular",
        channels=[ChannelSpec(name="ReactDecisionOutV2", format="json", replace_citations=False, emit_marker="answer")],
        emit=collector.emit,
        agent="test.agent",
        artifact_name="react.decision",
        subscribers=ChannelSubscribers().subscribe("ReactDecisionOutV2", _sub),
        max_tokens=200,
        temperature=0.0,
        return_full_raw=True,
    )

    assert meta.get("service_error") is None
    assert results["ReactDecisionOutV2"].raw == json.dumps(payload, ensure_ascii=True)
    assert [e["channel_instance"] for e in seen if e["completed"] is True] == [0]
    assert not any("</channel:wrong>" in (e.get("text") or "") for e in seen)


@pytest.mark.asyncio
async def test_stream_with_channels_v3_ignores_literal_channel_mentions_inside_content():
    payload = {
        "action": "call_tool",
        "tool_call": {
            "tool_id": "react.read",
            "params": {"paths": ["alpha"]},
        },
    }
    text = (
        "<channel:thinking>Explain the literal syntax `<channel:code></channel:code>` to the user.</channel:thinking>\n"
        f"{_json_channel('ReactDecisionOutV2', payload)}\n"
        "<channel:code></channel:code>"
    )
    svc = _FakeService(_chunk_text(text, size=9))
    collector = _Collector()

    results, meta = await stream_with_channels(
        svc=svc,
        messages=["sys", "user"],
        role="answer.generator.regular",
        channels=[
            ChannelSpec(name="thinking", format="markdown", replace_citations=False, emit_marker="thinking"),
            ChannelSpec(name="ReactDecisionOutV2", format="json", replace_citations=False, emit_marker="answer"),
            ChannelSpec(name="code", format="text", replace_citations=False, emit_marker="subsystem"),
        ],
        emit=collector.emit,
        agent="test.agent",
        artifact_name="react.decision",
        max_tokens=200,
        temperature=0.0,
        return_full_raw=True,
    )

    assert meta.get("service_error") is None
    assert "`<channel:code></channel:code>`" in results["thinking"].raw
    assert results["ReactDecisionOutV2"].raw == json.dumps(payload, ensure_ascii=True)
    assert results["code"].raw == ""


@pytest.mark.asyncio
async def test_stream_with_channels_v3_captures_realistic_exec_code_payload():
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
        f"{_json_channel('ReactDecisionOutV2', payload)}\n"
        f"<channel:code>\n{code_text}</channel:code>"
    )
    for chunk_size in HISTORICAL_EXEC_CODE_MISS_CHUNK_SIZES:
        svc = _FakeService(_chunk_text(text, size=chunk_size))
        collector = _Collector()
        widget = DecisionExecCodeStreamer(
            emit_delta=collector.emit,
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
            emit=collector.emit,
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
