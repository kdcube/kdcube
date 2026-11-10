"""Final-only delivery (`stream_activity: false`) must still deliver hosted files.

Reproduction: a bundle runs its workflow through
`run_with_queued_telegram_delivery`, a tool hosts a file with `emit=True`
(the hosting service fires `chat.files`), and the runner returns only
`{"answer": ...}` - as the reference workspace bundle does. With streaming on,
the activity streamer sends the file live. With streaming off, nothing
consumed `chat.files` and the final renderer saw no file in the result, so the
file was silently never delivered.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.telegram import user_admin
from kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot import (
    _file_delivery_key,
    render_telegram_messages_from_timeline,
)
from kdcube_ai_app.apps.chat.sdk.integrations.telegram.router import render_turn_messages
from kdcube_ai_app.apps.chat.sdk.integrations.telegram.stream import TelegramActivityStreamer

HOSTED_URI = "https://example.test/cb/turn_1/files/route.png"
SHA = "a" * 64


def _hosted_item(**overrides) -> dict:
    # Shape of one emit_solver_artifacts() item for a hosted file.
    item = {
        "filename": "route.png",
        "mime": "image/png",
        "description": "Route snapshot",
        "hosted_uri": HOSTED_URI,
        "key": "cb/turn_1/files/route.png",
        "rn": "ef:tenant:project:chatbot:artifact:route.png",
        "physical_path": "turn_1/files/route.png",
        "logical_path": "fi:turn_1.files/route.png",
        "content_sha256": SHA,
        "size": 1234,
        "visibility": "external",
    }
    item.update(overrides)
    return item


def _files_activity(*items: dict, turn_id: str = "turn_1") -> dict:
    return {
        "event": "chat_step",
        "data": {
            "type": "chat.files",
            "conversation": {"turn_id": turn_id},
            "event": {"step": "files", "status": "completed", "title": f"Files Ready ({len(items)})"},
            "data": {"count": len(items), "items": list(items)},
        },
    }


class _FakeComm:
    def __init__(self):
        self.listeners = []

    def add_activity_listener(self, cb):
        self.listeners.append(cb)

    def remove_activity_listener(self, cb):
        self.listeners.remove(cb)

    async def emit(self, activity):
        for cb in list(self.listeners):
            await cb(activity)


async def test_collecting_streamer_sends_nothing_and_keeps_the_files():
    sent: list = []
    edits: list = []

    async def _send(messages):
        sent.extend(messages)
        return {"ok": True}

    async def _edit(*args, **kwargs):
        edits.append((args, kwargs))
        return {"ok": True}

    comm = _FakeComm()
    async with TelegramActivityStreamer(
        comm=comm,
        bot_token="token",
        chat_id="chat",
        turn_id="turn_1",
        send_messages=_send,
        edit_text_message=_edit,
        collect_files=True,
        quiet_seconds=0.01,
        min_send_interval_seconds=0.01,
    ) as streamer:
        await comm.emit(
            {
                "event": "chat_step",
                "data": {"type": "chat.step", "event": {"step": "get_route", "status": "started", "title": "Route"}},
            }
        )
        await comm.emit(
            {"event": "chat_error", "data": {"type": "chat.error", "data": {"error": "tool failed"}}}
        )
        await comm.emit(_files_activity(_hosted_item()))
        await comm.emit(_files_activity(_hosted_item()))  # duplicate announcement
        await comm.emit(_files_activity(_hosted_item(filename="other.png"), turn_id="turn_other"))
        await comm.emit(_files_activity(_hosted_item(filename="secret.txt", visibility="internal", hosted_uri="https://example.test/secret")))

    assert sent == []
    assert edits == []
    assert streamer.progress_message_id() is None
    collected = streamer.collected_file_items()
    assert [item["filename"] for item in collected] == ["route.png"]
    assert streamer.delivered_file_keys() == set()


def test_extra_files_render_after_the_answer():
    messages = render_telegram_messages_from_timeline(
        timeline={"turn_id": "turn_1", "blocks": []},
        react_turn={"answer": "Here is the route."},
        prefer_react_turn_answer=True,
        extra_file_items=[TelegramActivityStreamer._file_item_from_event_item(_hosted_item())],
    )

    assert [message.kind for message in messages] == ["text", "photo"]
    assert messages[1].files[0]["hosted_uri"] == HOSTED_URI


def test_extra_file_already_in_the_timeline_is_delivered_once():
    timeline = {
        "turn_id": "turn_1",
        "blocks": [
            {
                "path": "conv:fi:turn_1.files/route.png",
                "turn_id": "turn_1",
                "meta": {
                    "hosted_uri": HOSTED_URI,
                    "filename": "route.png",
                    "mime": "image/png",
                    "content_sha256": SHA,
                    "visibility": "external",
                },
            }
        ],
    }
    extra = TelegramActivityStreamer._file_item_from_event_item(_hosted_item())

    messages = render_telegram_messages_from_timeline(
        timeline=timeline,
        react_turn={"answer": "Here is the route."},
        prefer_react_turn_answer=True,
        extra_file_items=[extra],
    )

    assert sum(1 for message in messages if message.files) == 1


def test_streamed_key_and_collected_key_are_the_same_key():
    # The live streamer and the final-only collector build the item from the
    # same chat.files payload, so a key recorded as delivered live also
    # suppresses the collected copy.
    extra = TelegramActivityStreamer._file_item_from_event_item(_hosted_item())
    messages = render_telegram_messages_from_timeline(
        timeline={"turn_id": "turn_1", "blocks": []},
        react_turn={"answer": "Here is the route."},
        prefer_react_turn_answer=True,
        exclude_file_keys={_file_delivery_key(extra)},
        extra_file_items=[extra],
    )

    assert [message.kind for message in messages] == ["text"]


def _queued_entrypoint(comm: _FakeComm, definition: dict) -> SimpleNamespace:
    integrations = {"telegram.default": {"provider": "telegram", "enabled": True, "definition": definition}}

    def bundle_prop(path, default=None):
        return integrations if path == "integrations" else default

    return SimpleNamespace(
        BUNDLE_ID="test.telegram-final-only",
        config=SimpleNamespace(ai_bundle_spec=SimpleNamespace(id="test.telegram-final-only")),
        comm=comm,
        bundle_prop=bundle_prop,
        comm_context=SimpleNamespace(
            request=SimpleNamespace(
                payload={"telegram": {"chat_id": "1001", "update_id": "upd-final-only", "turn_id": "turn_1"}}
            ),
            routing=SimpleNamespace(turn_id="turn_1"),
        ),
    )


@pytest.mark.parametrize(
    "definition",
    [
        {"stream_activity": False},
        {"webhook": {"stream_activity": False, "send_responses": True}},
    ],
)
async def test_final_only_queued_delivery_delivers_an_emitted_file(monkeypatch, definition):
    comm = _FakeComm()
    rendered: dict = {}

    async def _deliver(**kwargs):
        rendered["messages"] = render_turn_messages(
            turn_result=kwargs["turn_result"],
            delivered_file_keys=kwargs["delivered_file_keys"],
            extra_file_items=kwargs["extra_file_items"],
        )
        return {"messages": [], "telegram_delivery": {"ok": True}}

    async def _token(entrypoint=None, **_kwargs):
        return "telegram-token"

    monkeypatch.setattr(user_admin, "deliver_turn_to_telegram", _deliver)
    monkeypatch.setattr(user_admin, "_bot_token_value", _token)

    async def _runner():
        # A tool hosts and emits the file mid-turn; the runner result carries
        # only the answer.
        await comm.emit(_files_activity(_hosted_item()))
        return {"answer": "Here is the route.", "suggested_followups": []}

    await user_admin.run_with_queued_telegram_delivery(_queued_entrypoint(comm, definition), runner=_runner)

    kinds = [message.kind for message in rendered["messages"]]
    assert kinds == ["text", "photo"]
    assert rendered["messages"][1].files[0]["filename"] == "route.png"
    assert comm.listeners == []


async def test_send_responses_false_collects_nothing(monkeypatch):
    comm = _FakeComm()
    captured: dict = {}

    async def _deliver(**kwargs):
        captured.update(kwargs)
        return {"messages": [], "telegram_delivery": None}

    async def _token(entrypoint=None, **_kwargs):
        return "telegram-token"

    monkeypatch.setattr(user_admin, "deliver_turn_to_telegram", _deliver)
    monkeypatch.setattr(user_admin, "_bot_token_value", _token)

    async def _runner():
        await comm.emit(_files_activity(_hosted_item()))
        return {"answer": "ok"}

    await user_admin.run_with_queued_telegram_delivery(
        _queued_entrypoint(comm, {"stream_activity": False, "send_responses": False}),
        runner=_runner,
    )

    assert captured["extra_file_items"] == []
    assert captured["send_responses"] is False
