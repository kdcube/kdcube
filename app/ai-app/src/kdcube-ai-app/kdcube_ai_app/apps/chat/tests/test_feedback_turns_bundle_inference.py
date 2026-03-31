from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import ContextRAGClient


@pytest.mark.asyncio
async def test_fetch_turns_with_feedbacks_ignores_ctx_bundle_filter_and_infers_bundle_id(monkeypatch):
    client = ContextRAGClient(
        conv_idx=SimpleNamespace(),
        store=SimpleNamespace(),
        model_service=None,
    )

    calls = []

    async def _search(**kwargs):
        calls.append(("search", kwargs["bundle_id"], dict(kwargs["ctx"] or {})))
        assert kwargs["bundle_id"] is None
        assert "bundle_id" not in (kwargs["ctx"] or {})
        assert "app_bundle_id" not in (kwargs["ctx"] or {})
        if kwargs["kinds"] == ("artifact:turn.log.reaction",):
            return {"items": [{"turn_id": "turn-1"}]}
        if kwargs["kinds"] == ("artifact:turn.log",):
            return {"items": []}
        raise AssertionError(f"unexpected search call: {kwargs}")

    async def _materialize_turn(**kwargs):
        calls.append(("materialize_turn", kwargs["bundle_id"], dict(kwargs["ctx"] or {})))
        assert kwargs["bundle_id"] is None
        assert "bundle_id" not in (kwargs["ctx"] or {})
        assert "app_bundle_id" not in (kwargs["ctx"] or {})
        return {
            "turn_log": {
                "bundle_id": "bundle.from.db",
                "payload": {
                    "blocks": [
                        {"type": "stage.feedback", "ts": "2026-04-01T09:00:00Z", "text": "Looks good"}
                    ]
                },
            },
            "assistant": {
                "bundle_id": "bundle.from.db",
                "payload": {"text": "assistant"},
            },
            "user": {
                "bundle_id": "bundle.from.db",
                "payload": {"text": "user"},
            },
        }

    async def _recent(**kwargs):
        calls.append(("recent", kwargs["bundle_id"], dict(kwargs["ctx"] or {})))
        assert kwargs["bundle_id"] is None
        assert "bundle_id" not in (kwargs["ctx"] or {})
        assert "app_bundle_id" not in (kwargs["ctx"] or {})
        return {"items": []}

    monkeypatch.setattr(client, "search", _search)
    monkeypatch.setattr(client, "materialize_turn", _materialize_turn)
    monkeypatch.setattr(client, "recent", _recent)

    result = await client.fetch_turns_with_feedbacks(
        user_id="user-1",
        conversation_id="conv-1",
        ctx={
            "user_id": "ctx-user",
            "conversation_id": "ctx-conv",
            "bundle_id": "bundle.ctx",
            "app_bundle_id": "bundle.ctx.app",
        },
    )

    assert result["user_id"] == "user-1"
    assert result["conversation_id"] == "conv-1"
    assert result["bundle_id"] == "bundle.from.db"
    assert result["bundle_ids"] == ["bundle.from.db"]
    assert result["turns"] == [
        {
            "turn_id": "turn-1",
            "bundle_id": "bundle.from.db",
            "turn_log": {
                "blocks": [
                    {"type": "stage.feedback", "ts": "2026-04-01T09:00:00Z", "text": "Looks good"}
                ]
            },
            "assistant": {
                "bundle_id": "bundle.from.db",
                "payload": {"text": "assistant"},
            },
            "user": {
                "bundle_id": "bundle.from.db",
                "payload": {"text": "user"},
            },
            "feedbacks": [{"ts": "2026-04-01T09:00:00Z", "text": "Looks good"}],
            "reactions": [],
        }
    ]
    assert calls


@pytest.mark.asyncio
async def test_fetch_conversation_artifacts_ignores_ctx_bundle_filter_and_infers_bundle_id(monkeypatch):
    idx = SimpleNamespace()
    client = ContextRAGClient(
        conv_idx=idx,
        store=SimpleNamespace(),
        model_service=None,
    )

    calls = []

    async def _get_conversation_turn_ids_from_tags(**kwargs):
        calls.append(("turn_ids", kwargs["bundle_id"], dict(kwargs["ctx"] or {})))
        assert kwargs["bundle_id"] is None
        assert "bundle_id" not in (kwargs["ctx"] or {})
        assert "app_bundle_id" not in (kwargs["ctx"] or {})
        return [
            {
                "turn_id": "turn-1",
                "ts": "2026-04-01T09:00:00Z",
                "tags": ["turn:turn-1", "artifact:conv.artifacts.stream"],
                "mid": "m-1",
                "hosted_uri": "index_only",
                "bundle_id": "bundle.from.db",
            }
        ]

    idx.get_conversation_turn_ids_from_tags = _get_conversation_turn_ids_from_tags

    async def _recent(**kwargs):
        calls.append(("recent", kwargs["bundle_id"], dict(kwargs["ctx"] or {})))
        assert kwargs["bundle_id"] is None
        assert "bundle_id" not in (kwargs["ctx"] or {})
        assert "app_bundle_id" not in (kwargs["ctx"] or {})
        return {"items": []}

    async def _materialize_turn(**kwargs):
        calls.append(("materialize_turn", kwargs["bundle_id"], dict(kwargs["ctx"] or {})))
        assert kwargs["bundle_id"] is None
        assert "bundle_id" not in (kwargs["ctx"] or {})
        assert "app_bundle_id" not in (kwargs["ctx"] or {})
        return {
            "turn_log": {},
            "assistant": None,
            "user": None,
        }

    monkeypatch.setattr(client, "recent", _recent)
    monkeypatch.setattr(client, "materialize_turn", _materialize_turn)

    result = await client.fetch_conversation_artifacts(
        user_id="user-1",
        conversation_id="conv-1",
        materialize=False,
        ctx={
            "user_id": "ctx-user",
            "conversation_id": "ctx-conv",
            "bundle_id": "bundle.ctx",
            "app_bundle_id": "bundle.ctx.app",
        },
    )

    assert result["user_id"] == "user-1"
    assert result["conversation_id"] == "conv-1"
    assert result["bundle_id"] == "bundle.from.db"
    assert result["bundle_ids"] == ["bundle.from.db"]
    assert result["turns"] == [
        {
            "turn_id": "turn-1",
            "artifacts": [
                {
                    "message_id": "m-1",
                    "type": "artifact:conv.artifacts.stream",
                    "ts": "2026-04-01T09:00:00Z",
                    "hosted_uri": "index_only",
                    "bundle_id": "bundle.from.db",
                }
            ],
        }
    ]
    assert calls
