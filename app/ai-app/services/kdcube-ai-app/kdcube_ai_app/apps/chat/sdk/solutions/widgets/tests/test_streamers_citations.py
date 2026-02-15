# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

import json
import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.widgets.canvas import (
    ReactWriteContentStreamer,
    RenderingWriteContentStreamer,
    TimelineStreamer,
)


class _Collector:
    def __init__(self):
        self.events = []

    async def emit(self, **kwargs):
        self.events.append(kwargs)

    def text_for_marker(self, marker: str) -> str:
        return "".join(
            e.get("text", "")
            for e in self.events
            if e.get("marker") == marker and e.get("text")
        )


def _chunk_text(text: str, size: int = 13):
    return [text[i:i + size] for i in range(0, len(text), size)]


def _json_stream_payload(payload: dict) -> str:
    # Decision JSON channel is fenced in the model output.
    return "```json\n" + json.dumps(payload, ensure_ascii=True) + "\n```"


@pytest.mark.asyncio
async def test_react_write_streamer_replaces_citations_in_fenced_json():
    sources_list = [
        {"sid": 1, "title": "Example A", "url": "https://example.com/a", "text": "A"},
        {"sid": 2, "title": "Example B", "url": "https://example.com/b", "text": "B"},
    ]
    payload = {
        "action": "call_tool",
        "tool_call": {
            "tool_id": "react.write",
            "params": {
                "path": "turn_1/files/note.md",
                "channel": "canvas",
                "content": "Hello [[S:1]] and [[S:2]].",
            },
        },
    }

    collector = _Collector()
    streamer = ReactWriteContentStreamer(
        emit_delta=collector.emit,
        agent="test.agent",
        artifact_name="react.record.test",
        sources_list=sources_list,
        turn_id="turn_1",
    )

    for chunk in _chunk_text(_json_stream_payload(payload), size=11):
        await streamer.feed(chunk)
    await streamer.finish()

    rendered = collector.text_for_marker("canvas")
    assert "[[S:" not in rendered
    assert "https://example.com/a" in rendered
    assert "https://example.com/b" in rendered


@pytest.mark.asyncio
async def test_timeline_streamer_replaces_citations_in_final_answer_only():
    sources_list = [
        {"sid": 1, "title": "Source 1", "url": "https://example.com/1", "text": "One"},
        {"sid": 2, "title": "Source 2", "url": "https://example.com/2", "text": "Two"},
        {"sid": 3, "title": "Source 3", "url": "https://example.com/3", "text": "Three"},
    ]
    payload = {
        "action": "complete",
        "notes": "Short rationale [[S:1]].",
        "final_answer": "Done. See details [[S:2-3]].",
    }

    collector = _Collector()
    streamer = TimelineStreamer(
        emit_delta=collector.emit,
        agent="test.agent",
        sources_list=sources_list,
    )

    for chunk in _chunk_text(_json_stream_payload(payload), size=9):
        await streamer.feed(chunk)
    await streamer.finish()

    rendered = collector.text_for_marker("answer")
    assert "[[S:" not in rendered
    assert "https://example.com/2" in rendered
    assert "https://example.com/3" in rendered
    assert "https://example.com/1" not in rendered
    assert collector.text_for_marker("canvas") == ""


@pytest.mark.asyncio
async def test_timeline_streamer_streams_notes_on_tool_call():
    sources_list = [
        {"sid": 1, "title": "Source 1", "url": "https://example.com/1", "text": "One"},
    ]
    payload = {
        "action": "call_tool",
        "notes": "Plan [[S:1]].",
        "tool_call": {"tool_id": "web_tools.web_search", "params": {}},
    }

    collector = _Collector()
    streamer = TimelineStreamer(
        emit_delta=collector.emit,
        agent="test.agent",
        sources_list=sources_list,
    )

    for chunk in _chunk_text(_json_stream_payload(payload), size=9):
        await streamer.feed(chunk)
    await streamer.finish()

    rendered = collector.text_for_marker("timeline_text")
    assert "[[S:" not in rendered
    assert "https://example.com/1" in rendered


@pytest.mark.asyncio
async def test_react_write_streamer_defers_until_channel_known():
    sources_list = [
        {"sid": 1, "title": "Example", "url": "https://example.com", "text": "X"},
    ]
    payload = {
        "action": "call_tool",
        "tool_call": {
            "tool_id": "react.write",
            "params": {
                "content": "Hello [[S:1]].",
                "channel": "internal",
            },
        },
    }

    collector = _Collector()
    streamer = ReactWriteContentStreamer(
        emit_delta=collector.emit,
        agent="test.agent",
        artifact_name="react.record.test",
        sources_list=sources_list,
        turn_id="turn_1",
    )

    for chunk in _chunk_text(_json_stream_payload(payload), size=7):
        await streamer.feed(chunk)
    await streamer.finish()

    assert collector.text_for_marker("canvas") == ""


@pytest.mark.asyncio
async def test_react_write_streamer_defers_and_streams_canvas():
    sources_list = [
        {"sid": 1, "title": "Example", "url": "https://example.com", "text": "X"},
    ]
    payload = {
        "action": "call_tool",
        "tool_call": {
            "tool_id": "react.write",
            "params": {
                "content": "Hello [[S:1]].",
                "channel": "canvas",
            },
        },
    }

    collector = _Collector()
    streamer = ReactWriteContentStreamer(
        emit_delta=collector.emit,
        agent="test.agent",
        artifact_name="react.record.test",
        sources_list=sources_list,
        turn_id="turn_1",
    )

    for chunk in _chunk_text(_json_stream_payload(payload), size=7):
        await streamer.feed(chunk)
    await streamer.finish()

    rendered = collector.text_for_marker("canvas")
    assert "https://example.com" in rendered


@pytest.mark.asyncio
async def test_rendering_write_streamer_skips_ref_content():
    sources_list = [
        {"sid": 1, "title": "Example", "url": "https://example.com", "text": "X"},
    ]
    payload = {
        "action": "call_tool",
        "tool_call": {
            "tool_id": "rendering_tools.write_html",
            "params": {
                "path": "turn_1/files/page.html",
                "content": "ref:fi:turn_1.files/page.md",
            },
        },
    }

    collector = _Collector()
    streamer = RenderingWriteContentStreamer(
        emit_delta=collector.emit,
        agent="test.agent",
        artifact_name="react.record.test",
        sources_list=sources_list,
        turn_id="turn_1",
    )

    for chunk in _chunk_text(_json_stream_payload(payload), size=17):
        await streamer.feed(chunk)
    await streamer.finish()

    rendered = collector.text_for_marker("canvas")
    assert rendered == ""


@pytest.mark.asyncio
async def test_react_write_streamer_skips_ref_content():
    sources_list = [
        {"sid": 1, "title": "Example", "url": "https://example.com", "text": "X"},
    ]
    payload = {
        "action": "call_tool",
        "tool_call": {
            "tool_id": "react.write",
            "params": {
                "path": "turn_1/files/note.md",
                "channel": "canvas",
                "content": "ref:fi:turn_1.files/note.md",
            },
        },
    }

    collector = _Collector()
    streamer = ReactWriteContentStreamer(
        emit_delta=collector.emit,
        agent="test.agent",
        artifact_name="react.record.test",
        sources_list=sources_list,
        turn_id="turn_1",
    )

    for chunk in _chunk_text(_json_stream_payload(payload), size=11):
        await streamer.feed(chunk)
    await streamer.finish()

    rendered = collector.text_for_marker("canvas")
    assert rendered == ""


@pytest.mark.asyncio
async def test_react_write_streamer_routes_timeline_channel():
    sources_list = [
        {"sid": 1, "title": "Example", "url": "https://example.com", "text": "X"},
    ]
    payload = {
        "action": "call_tool",
        "tool_call": {
            "tool_id": "react.write",
            "params": {
                "path": "turn_1/files/note.md",
                "channel": "timeline_text",
                "content": "Hello [[S:1]].",
            },
        },
    }

    collector = _Collector()
    streamer = ReactWriteContentStreamer(
        emit_delta=collector.emit,
        agent="test.agent",
        artifact_name="react.record.test",
        sources_list=sources_list,
        turn_id="turn_1",
    )

    for chunk in _chunk_text(_json_stream_payload(payload), size=11):
        await streamer.feed(chunk)
    await streamer.finish()

    assert collector.text_for_marker("timeline_text") != ""
    assert collector.text_for_marker("canvas") == ""


@pytest.mark.asyncio
async def test_react_write_streamer_respects_action_complete():
    sources_list = [
        {"sid": 1, "title": "Example", "url": "https://example.com", "text": "X"},
    ]
    payload = {
        "action": "complete",
        "tool_call": {
            "tool_id": "react.write",
            "params": {
                "path": "turn_1/files/note.md",
                "channel": "canvas",
                "content": "Hello [[S:1]].",
            },
        },
        "final_answer": "Done.",
    }

    collector = _Collector()
    streamer = ReactWriteContentStreamer(
        emit_delta=collector.emit,
        agent="test.agent",
        artifact_name="react.record.test",
        sources_list=sources_list,
        turn_id="turn_1",
    )

    for chunk in _chunk_text(_json_stream_payload(payload), size=11):
        await streamer.feed(chunk)
    await streamer.finish()

    assert collector.text_for_marker("canvas") == ""


@pytest.mark.asyncio
async def test_timeline_streamer_replaces_split_citations_from_model_response():
    sources_list = [
        {"sid": 1, "title": "Wuppertal News", "url": "https://example.com/wuppertal", "text": "News"},
        {"sid": 2, "title": "Events", "url": "https://example.com/events", "text": "Events"},
    ]
    payload = {
        "action": "complete",
        "notes": "Presenting recent Wuppertal news findings",
        "tool_call": None,
        "final_answer": (
            "I found recent news from Wuppertal, Germany:\n\n"
            "## Traffic Disruptions [[S:1]]\n\n"
            "**Road closures and tunnel strikes** (February 9, 2026)\n"
            "- The A46 highway at Wuppertal-Nord junction will have closures Tuesday through Thursday\n"
            "- The Kiesbergtunnel and Velbert-Langenberg tunnel are closed due to public service strikes\n\n"
            "## Upcoming Events [[S:2]]\n\n"
            "**Cultural highlights** (February-April 2026)\n"
            "- This weekend: theater performance, Vincent van Gogh immersive exhibition\n"
            "- Music: The Music of Queen, AC/DC tribute band BAROCK\n\n"
            "The city continues to be an active cultural center with concerts, theater, and exhibitions.\n"
        ),
        "suggested_followups": [
            "Get more details about specific events",
            "Check current traffic conditions",
            "Find cultural venues in Wuppertal",
        ],
    }

    collector = _Collector()
    streamer = TimelineStreamer(
        emit_delta=collector.emit,
        agent="test.agent",
        sources_list=sources_list,
    )

    for chunk in _chunk_text(_json_stream_payload(payload), size=3):
        await streamer.feed(chunk)
    await streamer.finish()

    rendered = collector.text_for_marker("answer")
    assert "[[S:" not in rendered
    assert "https://example.com/wuppertal" in rendered
    assert "https://example.com/events" in rendered


@pytest.mark.asyncio
async def test_timeline_streamer_replaces_many_repeated_citations():
    sources_list = [
        {"sid": 1, "title": "Only Source", "url": "https://example.com/only", "text": "Only"},
    ]
    repeated = " ".join(f"Item {i}:[[S:1]]" for i in range(1, 120))
    payload = {
        "action": "complete",
        "notes": "Bulk citation test",
        "final_answer": repeated,
    }

    collector = _Collector()
    streamer = TimelineStreamer(
        emit_delta=collector.emit,
        agent="test.agent",
        sources_list=sources_list,
    )

    for chunk in _chunk_text(_json_stream_payload(payload), size=2):
        await streamer.feed(chunk)
    await streamer.finish()

    rendered = collector.text_for_marker("answer")
    assert "[[S:" not in rendered
    assert "https://example.com/only" in rendered
