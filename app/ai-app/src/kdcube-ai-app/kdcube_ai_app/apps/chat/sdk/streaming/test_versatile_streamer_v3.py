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


class _PhaseAwareService(_FakeService):
    def __init__(self, chunks):
        super().__init__(chunks)
        self.stream_finished = False

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
        self.stream_finished = True
        await on_complete({})
        return {"text": "".join(self._chunks), "service_error": None}


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
async def test_stream_with_channels_v3_streams_three_canvas_writes_from_repeated_decisions():
    html_report = "<!doctype html><html><body>" + ("<p>Report body.</p>" * 80) + "</body></html>"
    html_slides = "<!doctype html><html><body>" + ("<section>Slide body.</section>" * 70) + "</body></html>"
    markdown_doc = "# Report\n\n" + ("Markdown body.\n\n" * 120)

    payloads = [
        {
            "action": "call_tool",
            "notes": "Writing HTML source for PDF render",
            "tool_call": {
                "tool_id": "react.write",
                "params": {
                    "path": "outputs/ai_security_news/report.html",
                    "channel": "canvas",
                    "content": html_report,
                    "kind": "display",
                },
            },
        },
        {
            "action": "call_tool",
            "notes": "Writing HTML source for PPTX render",
            "tool_call": {
                "tool_id": "react.write",
                "params": {
                    "path": "outputs/ai_security_news/slides.html",
                    "channel": "canvas",
                    "content": html_slides,
                    "kind": "display",
                },
            },
        },
        {
            "action": "call_tool",
            "notes": "Writing Markdown source for DOCX render",
            "tool_call": {
                "tool_id": "react.write",
                "params": {
                    "path": "outputs/ai_security_news/report.md",
                    "channel": "canvas",
                    "content": markdown_doc,
                    "kind": "display",
                },
            },
        },
    ]
    full = "".join(_json_channel("ReactDecisionOutV2", payload) for payload in payloads)

    svc = _FakeService(_chunk_text(full, size=97))
    collector = _Collector()

    def _factory(channel: str, instance_idx: int):
        del channel
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

    subscribers = ChannelSubscribers().subscribe_factory("ReactDecisionOutV2", _factory)

    results, meta = await stream_with_channels(
        svc=svc,
        messages=["sys", "user"],
        role="answer.generator.regular",
        channels=[ChannelSpec(name="ReactDecisionOutV2", format="json", replace_citations=False, emit_marker="answer")],
        emit=collector.emit,
        agent="test.agent",
        artifact_name="react.decision",
        subscribers=subscribers,
        max_tokens=500,
        temperature=0.0,
        return_full_raw=True,
    )

    assert meta.get("service_error") is None
    assert len(results["ReactDecisionOutV2"].instances or []) == 3
    assert "Report body." in collector.text_for_artifact("ai_security_news/report.html")
    assert "Slide body." in collector.text_for_artifact("ai_security_news/slides.html")
    assert "Markdown body." in collector.text_for_artifact("ai_security_news/report.md")
    assert any(e.get("completed") is True for e in collector.events_for_artifact("ai_security_news/report.html"))
    assert any(e.get("completed") is True for e in collector.events_for_artifact("ai_security_news/slides.html"))
    assert any(e.get("completed") is True for e in collector.events_for_artifact("ai_security_news/report.md"))


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk_size", [97, 10**9])
async def test_stream_with_channels_v3_repeated_canvas_writes_emit_before_completion_recovery(chunk_size):
    payloads = []
    for path, label in [
        ("outputs/ai_security_news/report.html", "Report body."),
        ("outputs/ai_security_news/slides.html", "Slide body."),
        ("outputs/ai_security_news/report.md", "Markdown body."),
    ]:
        payloads.append(
            {
                "action": "call_tool",
                "notes": f"Writing {label}",
                "tool_call": {
                    "tool_id": "react.write",
                    "params": {
                        "path": path,
                        "channel": "canvas",
                        "content": label * 320,
                        "kind": "display",
                    },
                },
            }
        )

    full = "".join(_json_channel("ReactDecisionOutV2", payload) for payload in payloads)
    svc = _PhaseAwareService(_chunk_text(full, size=chunk_size))
    collector = _Collector()

    async def _emit_with_phase(**kwargs):
        event = dict(kwargs)
        event["after_model_stream"] = svc.stream_finished
        collector.events.append(event)

    def _factory(channel: str, instance_idx: int):
        del channel
        record = ReactWriteContentStreamer(
            emit_delta=_emit_with_phase,
            agent=f"test.record.{instance_idx}",
            artifact_name=f"react.record.{instance_idx}",
            turn_id="turn_1",
            sources_list=[],
        )
        return [_wrap_json_widget(record)]

    results, meta = await stream_with_channels(
        svc=svc,
        messages=["sys", "user"],
        role="answer.generator.regular",
        channels=[ChannelSpec(name="ReactDecisionOutV2", format="json", replace_citations=False, emit_marker="answer")],
        emit=_emit_with_phase,
        agent="test.agent",
        artifact_name="react.decision",
        subscribers=ChannelSubscribers().subscribe_factory("ReactDecisionOutV2", _factory),
        max_tokens=500,
        temperature=0.0,
        return_full_raw=True,
    )

    assert meta.get("service_error") is None
    assert len(results["ReactDecisionOutV2"].instances or []) == 3
    for artifact_name in [
        "ai_security_news/report.html",
        "ai_security_news/slides.html",
        "ai_security_news/report.md",
    ]:
        text_events = [
            e for e in collector.events_for_artifact(artifact_name)
            if e.get("text")
        ]
        assert text_events, artifact_name
        assert not text_events[0]["after_model_stream"], artifact_name


@pytest.mark.asyncio
async def test_stream_with_channels_v3_repeated_json_decisions_ignore_backticks_inside_strings():
    payloads = [
        {
            "action": "call_tool",
            "notes": "Writing source with fenced example",
            "tool_call": {
                "tool_id": "react.write",
                "params": {
                    "path": "outputs/report.md",
                    "channel": "canvas",
                    "content": "Before\n```python\nprint('not a protocol fence')\n```\nAfter",
                    "kind": "display",
                },
            },
        },
        {
            "action": "call_tool",
            "notes": "Writing slides after fenced source",
            "tool_call": {
                "tool_id": "react.write",
                "params": {
                    "path": "outputs/slides.html",
                    "channel": "canvas",
                    "content": "<html><body>slides after fenced content</body></html>",
                    "kind": "display",
                },
            },
        },
        {
            "action": "call_tool",
            "notes": "Writing doc after slides",
            "tool_call": {
                "tool_id": "react.write",
                "params": {
                    "path": "outputs/report.doc.md",
                    "channel": "canvas",
                    "content": "doc body after slides",
                    "kind": "display",
                },
            },
        },
    ]
    full = "".join(_json_channel("ReactDecisionOutV2", payload) for payload in payloads)
    svc = _PhaseAwareService(_chunk_text(full, size=113))
    collector = _Collector()

    async def _emit_with_phase(**kwargs):
        event = dict(kwargs)
        event["after_model_stream"] = svc.stream_finished
        collector.events.append(event)

    def _factory(channel: str, instance_idx: int):
        del channel
        record = ReactWriteContentStreamer(
            emit_delta=_emit_with_phase,
            agent=f"test.record.{instance_idx}",
            artifact_name=f"react.record.{instance_idx}",
            turn_id="turn_1",
            sources_list=[],
        )
        return [_wrap_json_widget(record)]

    results, meta = await stream_with_channels(
        svc=svc,
        messages=["sys", "user"],
        role="answer.generator.regular",
        channels=[ChannelSpec(name="ReactDecisionOutV2", format="json", replace_citations=False, emit_marker="answer")],
        emit=_emit_with_phase,
        agent="test.agent",
        artifact_name="react.decision",
        subscribers=ChannelSubscribers().subscribe_factory("ReactDecisionOutV2", _factory),
        max_tokens=500,
        temperature=0.0,
        return_full_raw=True,
    )

    assert meta.get("service_error") is None
    assert len(results["ReactDecisionOutV2"].instances or []) == 3
    for artifact_name in ["report.md", "slides.html", "report.doc.md"]:
        text_events = [e for e in collector.events_for_artifact(artifact_name) if e.get("text")]
        assert text_events, artifact_name
        assert not text_events[0]["after_model_stream"], artifact_name


@pytest.mark.asyncio
@pytest.mark.parametrize("chunk_size", [13, 97, 10**9])
async def test_stream_with_channels_v3_captures_mixed_repeated_channel_sequence(chunk_size):
    decisions = [
        {
            "action": "call_tool",
            "notes": "Run code first.",
            "tool_call": {
                "tool_id": "exec_tools.execute_code_python",
                "params": {
                    "prog_name": "mixed_sequence",
                    "contract": [
                        {
                            "filename": "outputs/alpha.txt",
                            "description": "First output",
                            "visibility": "external",
                        }
                    ],
                },
            },
        },
        {
            "action": "call_tool",
            "notes": "Write beta.",
            "tool_call": {
                "tool_id": "react.write",
                "params": {
                    "path": "outputs/beta.md",
                    "channel": "canvas",
                    "content": "beta body",
                    "kind": "display",
                },
            },
        },
        {
            "action": "call_tool",
            "notes": "Write gamma.",
            "tool_call": {
                "tool_id": "react.write",
                "params": {
                    "path": "outputs/gamma.md",
                    "channel": "canvas",
                    "content": "gamma body",
                    "kind": "display",
                },
            },
        },
    ]
    code = "print('alpha')\nprint('done')\n"
    full = (
        _text_channel("thinking", "first thought")
        + _json_channel("ReactDecisionOutV2", decisions[0])
        + _text_channel("code", code)
        + _text_channel("thinking", "second thought")
        + _json_channel("ReactDecisionOutV2", decisions[1])
        + _json_channel("ReactDecisionOutV2", decisions[2])
        + _text_channel("thinking", "final thought")
    )
    svc = _PhaseAwareService(_chunk_text(full, size=chunk_size))
    collector = _Collector()
    decision_factory_events = []
    decision_events = []
    code_events = []
    thinking_events = []

    async def _emit_with_phase(**kwargs):
        event = dict(kwargs)
        event["after_model_stream"] = svc.stream_finished
        collector.events.append(event)

    async def _decision_sub(text: str = "", completed: bool = False, channel_instance=None, **_kwargs):
        if text or completed:
            decision_events.append(
                {
                    "text": text,
                    "completed": completed,
                    "channel_instance": channel_instance,
                    "after_model_stream": svc.stream_finished,
                }
            )

    def _decision_factory(channel: str, channel_instance: int):
        del channel
        decision_factory_events.append(
            {
                "channel_instance": channel_instance,
                "after_model_stream": svc.stream_finished,
            }
        )
        return [_decision_sub]

    async def _code_sub(text: str = "", completed: bool = False, channel_instance=None, **_kwargs):
        if text or completed:
            code_events.append(
                {
                    "text": text,
                    "completed": completed,
                    "channel_instance": channel_instance,
                    "after_model_stream": svc.stream_finished,
                }
            )

    async def _thinking_sub(text: str = "", completed: bool = False, channel_instance=None, **_kwargs):
        if text or completed:
            thinking_events.append(
                {
                    "text": text,
                    "completed": completed,
                    "channel_instance": channel_instance,
                    "after_model_stream": svc.stream_finished,
                }
            )

    subscribers = (
        ChannelSubscribers()
        .subscribe_factory("ReactDecisionOutV2", _decision_factory)
        .subscribe("code", _code_sub)
        .subscribe("thinking", _thinking_sub)
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
        emit=_emit_with_phase,
        agent="test.agent",
        artifact_name="react.decision",
        subscribers=subscribers,
        max_tokens=1000,
        temperature=0.0,
        return_full_raw=True,
    )

    assert meta.get("service_error") is None
    assert results["thinking"].instances == ["first thought", "second thought", "final thought"]
    assert len(results["ReactDecisionOutV2"].instances or []) == 3
    assert results["code"].instances == [code]
    assert code in results["code"].raw

    assert [e["channel_instance"] for e in decision_factory_events] == [0, 1, 2]
    assert all(not e["after_model_stream"] for e in decision_factory_events), chunk_size

    completed_decisions = [e for e in decision_events if e["completed"]]
    assert [e["channel_instance"] for e in completed_decisions] == [0, 1, 2]
    assert all(not e["after_model_stream"] for e in completed_decisions), chunk_size

    completed_thinking = [e for e in thinking_events if e["completed"]]
    assert [e["channel_instance"] for e in completed_thinking] == [0, 1, 2]
    assert all(not e["after_model_stream"] for e in completed_thinking), chunk_size

    completed_code = [e for e in code_events if e["completed"]]
    assert [e["channel_instance"] for e in completed_code] == [0]
    assert all(not e["after_model_stream"] for e in completed_code), chunk_size


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
async def test_stream_with_channels_v3_code_channel_survives_trailing_repeated_thinking():
    decision_payload = {
        "action": "call_tool",
        "tool_call": {
            "tool_id": "exec_tools.execute_code_python",
            "params": {
                "prog_name": "html_writer",
                "contract": [{"filename": "outputs/page.html", "description": "HTML output"}],
            },
        },
    }
    code_text = (
        "from pathlib import Path\n"
        "html = r\"\"\"<script>\n"
        "const label = `template marker in generated HTML;\n"
        "</script>\"\"\"\n"
        "Path(OUTPUT_DIR, 'outputs/page.html').write_text(html)\n"
    )
    full = (
        _text_channel("thinking", "Writing HTML.")
        + _json_channel("ReactDecisionOutV2", decision_payload)
        + _text_channel("code", code_text)
        + _text_channel("thinking", "Extra diagnostic after code.")
    )

    for chunk_size in [7, 19, 64]:
        svc = _FakeService(_chunk_text(full, size=chunk_size))
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
            max_tokens=800,
            temperature=0.0,
            return_full_raw=True,
        )

        assert meta.get("service_error") is None
        assert results["ReactDecisionOutV2"].error is None
        assert results["code"].raw == code_text
        assert widget.get_code() == code_text
        assert "</channel:code>" not in results["code"].raw
        assert "Writing HTML." in results["thinking"].raw
        assert "Extra diagnostic after code." in results["thinking"].raw

        thinking_instances = [
            e.get("channel_instance")
            for e in collector.events_for_channel("thinking")
            if e.get("text")
        ]
        assert 0 in thinking_instances
        assert 1 in thinking_instances


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
async def test_stream_with_channels_v3_ignores_literal_channel_mentions_inside_legacy_thinking():
    payload = {
        "action": "call_tool",
        "tool_call": {
            "tool_id": "react.read",
            "params": {"paths": ["alpha"]},
        },
    }
    text = (
        "<thinking>The `<channel:code>` marker was mentioned as text, not opened.</thinking>\n"
        f"{_json_channel('ReactDecisionOutV2', payload)}\n"
        "<channel:code></channel:code>"
    )
    svc = _FakeService(_chunk_text(text, size=11))
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
    assert results["ReactDecisionOutV2"].raw == json.dumps(payload, ensure_ascii=True)
    assert results["code"].raw == ""


@pytest.mark.asyncio
async def test_stream_with_channels_v3_ignores_literal_react_decision_mentions_inside_thinking():
    payload = {
        "action": "call_tool",
        "tool_call": {
            "tool_id": "react.read",
            "params": {"paths": ["alpha"]},
        },
    }
    text = (
        "<channel:thinking>Do not open literal `<channel:ReactDecisionOutV2>` here.</channel:thinking>\n"
        f"{_json_channel('ReactDecisionOutV2', payload)}\n"
        "<channel:code></channel:code>"
    )
    svc = _FakeService(_chunk_text(text, size=17))
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
    assert "`<channel:ReactDecisionOutV2>`" in results["thinking"].raw
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
        assert any(
            e.get("artifact_name") == "react.exec.test.code_exec_contract"
            and "monthly_priorities.xlsx" in str(e.get("text") or "")
            for e in collector.events
        ), chunk_size
