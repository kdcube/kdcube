"""Progress-card seams: adopt an existing message, start text, status labels.

Defaults must keep today's behaviour: the card is created lazily on the first
activity and a status line reads "<title or step>: <status>".
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.telegram import user_admin
from kdcube_ai_app.apps.chat.sdk.integrations.telegram.stream import TelegramActivityStreamer


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


def _step(step: str, title: str = "", status: str = "started") -> dict:
    return {
        "event": "chat_step",
        "data": {"type": "chat.step", "event": {"step": step, "status": status, "title": title}},
    }


class _Recorder:
    def __init__(self):
        self.sent: list = []
        self.edits: list = []

    async def send(self, messages):
        self.sent.extend(messages)
        return {"ok": True, "responses": [{"result": {"message_id": 900 + len(self.sent)}}]}

    async def edit(self, message_id, text, parse_mode):
        self.edits.append((message_id, text))
        return {"ok": True}


def _streamer(comm, recorder, **kwargs) -> TelegramActivityStreamer:
    return TelegramActivityStreamer(
        comm=comm,
        bot_token="token",
        chat_id="chat",
        send_messages=recorder.send,
        edit_text_message=recorder.edit,
        quiet_seconds=0.01,
        min_send_interval_seconds=0.01,
        **kwargs,
    )


async def test_default_card_is_created_lazily_with_the_default_status_text():
    comm, rec = _FakeComm(), _Recorder()
    async with _streamer(comm, rec) as streamer:
        await streamer.send_start_text("Seen")  # no adopted card: no-op
        await comm.emit(_step("get_route", "Get route"))

    assert len(rec.sent) == 1
    assert "Get route: started" in rec.sent[0].text
    assert "Seen" not in rec.sent[0].text
    assert streamer.progress_message_id() == 901


@pytest.mark.parametrize("initial", [555, "555"])
async def test_adopted_message_is_edited_instead_of_sending_a_new_card(initial):
    comm, rec = _FakeComm(), _Recorder()
    async with _streamer(comm, rec, initial_message_id=initial) as streamer:
        await streamer.send_start_text("Seen, thinking")
        await comm.emit(_step("get_route", "Get route"))

    assert rec.sent == []
    assert [message_id for message_id, _ in rec.edits] == [555, 555]
    assert "Seen, thinking" in rec.edits[0][1]
    assert "Get route: started" in rec.edits[-1][1]
    assert streamer.progress_message_id() == 555


@pytest.mark.parametrize("initial", [None, 0, "", "0"])
async def test_empty_initial_id_keeps_lazy_creation(initial):
    comm, rec = _FakeComm(), _Recorder()
    async with _streamer(comm, rec, initial_message_id=initial) as streamer:
        await comm.emit(_step("get_route", "Get route"))

    assert len(rec.sent) == 1
    assert streamer.progress_message_id() == 901


async def test_status_labels_and_format_shape_the_status_line():
    comm, rec = _FakeComm(), _Recorder()
    labels = {"get_route": "charting the route", "search": "looking it up", "ignored": None}
    async with _streamer(
        comm, rec, status_labels=labels, status_format="Working: {label}", initial_message_id=1
    ):
        await comm.emit(_step("get_route", "Get route"))  # exact step match
        await comm.emit(_step("search_web", "Search the web"))  # prefix match
        await comm.emit(_step("compose", "Compose answer"))  # no match: title

    body = rec.edits[-1][1]
    assert "Working: charting the route" in body
    assert "Working: looking it up" in body
    assert "Working: Compose answer" in body
    assert ": started" not in body


async def test_invalid_status_format_falls_back_to_default():
    comm, rec = _FakeComm(), _Recorder()
    async with _streamer(comm, rec, status_format="{nope}", initial_message_id=1):
        await comm.emit(_step("get_route", "Get route"))

    assert "Get route: started" in rec.edits[-1][1]


def _queued_entrypoint(comm, definition: dict) -> SimpleNamespace:
    integrations = {"telegram.default": {"provider": "telegram", "enabled": True, "definition": definition}}

    def bundle_prop(path, default=None):
        return integrations if path == "integrations" else default

    return SimpleNamespace(
        BUNDLE_ID="test.telegram-progress-seams",
        config=SimpleNamespace(ai_bundle_spec=SimpleNamespace(id="test.telegram-progress-seams")),
        comm=comm,
        bundle_prop=bundle_prop,
        comm_context=SimpleNamespace(
            request=SimpleNamespace(payload={"telegram": {"chat_id": "1001", "update_id": "upd-seams", "turn_id": "turn_1"}}),
            routing=SimpleNamespace(turn_id="turn_1"),
        ),
    )


@pytest.mark.parametrize(
    ("definition", "progress_message_id", "expected_initial", "expected_start"),
    [
        ({}, None, None, []),
        ({"progress_start_text": "Seen"}, 77, 77, ["Seen"]),
        # Final-only delivery never adopts or writes into a card during the turn.
        ({"stream_activity": False, "progress_start_text": "Seen"}, 77, None, []),
    ],
)
async def test_queued_delivery_passes_progress_seams(monkeypatch, definition, progress_message_id, expected_initial, expected_start):
    streamer_kwargs: list[dict] = []
    started: list[str] = []
    delivered: dict = {}

    class _FakeStreamer:
        def __init__(self, **kwargs):
            streamer_kwargs.append(dict(kwargs))
            self._initial = kwargs.get("initial_message_id")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def send_start_text(self, text):
            started.append(text)

        def delivered_file_keys(self):
            return set()

        def collected_file_items(self):
            return []

        def progress_message_id(self):
            return self._initial

        def progress_summary(self):
            return ""

    async def _deliver(**kwargs):
        delivered.update(kwargs)
        return {"messages": [], "telegram_delivery": None}

    async def _token(entrypoint=None, **_kwargs):
        return "telegram-token"

    monkeypatch.setattr(user_admin, "TelegramActivityStreamer", _FakeStreamer)
    monkeypatch.setattr(user_admin, "deliver_turn_to_telegram", _deliver)
    monkeypatch.setattr(user_admin, "_bot_token_value", _token)

    async def _runner():
        return {"answer": "ok"}

    await user_admin.run_with_queued_telegram_delivery(
        _queued_entrypoint(_FakeComm(), dict(definition, progress_status_labels={"get_route": "charting"})),
        runner=_runner,
        progress_message_id=progress_message_id,
    )

    assert streamer_kwargs[0]["initial_message_id"] == expected_initial
    assert streamer_kwargs[0]["status_labels"] == {"get_route": "charting"}
    assert started == expected_start
    assert delivered["progress_message_id"] == expected_initial
