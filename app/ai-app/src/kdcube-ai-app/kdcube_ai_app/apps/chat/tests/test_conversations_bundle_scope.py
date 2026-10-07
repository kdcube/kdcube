
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.ingress.conversations import conversations


class _FakeConversationBrowser:
    def __init__(self):
        self.calls = []

    async def list_conversations(self, **kwargs):
        self.calls.append(("list_conversations", kwargs))
        return {"user_id": kwargs["user_id"], "items": []}

    async def get_conversation_details(self, **kwargs):
        self.calls.append(("get_conversation_details", kwargs))
        return {
            "user_id": kwargs["user_id"],
            "conversation_id": kwargs["conversation_id"],
            "conversation_title": "New Conversation",
            "bundle_id": "bundle.from.db",
            "bundle_ids": ["bundle.from.db"],
            "turns": [{"turn_id": "turn-1", "artifacts": [], "bundle_id": "bundle.from.db"}],
        }

    async def get_conversation_state(self, **kwargs):
        self.calls.append(("get_conversation_state", kwargs))
        return {
            "state": "idle",
            "updated_at": "2026-04-27T10:00:00Z",
            "meta": {},
        }

    async def fetch_conversation_artifacts(self, **kwargs):
        self.calls.append(("fetch_conversation_artifacts", kwargs))
        return {
            "user_id": kwargs["user_id"],
            "conversation_id": kwargs["conversation_id"],
            "conversation_title": "New Conversation",
            "bundle_id": "bundle.from.db",
            "bundle_ids": ["bundle.from.db"],
            "turns": [
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
            ],
        }

    async def fetch_turns_with_feedbacks(self, **kwargs):
        self.calls.append(("fetch_turns_with_feedbacks", kwargs))
        return {
            "user_id": kwargs["user_id"],
            "conversation_id": kwargs["conversation_id"],
            "bundle_id": "bundle.from.db",
            "bundle_ids": ["bundle.from.db"],
            "turns": [
                {
                    "turn_id": "turn-1",
                    "bundle_id": "bundle.from.db",
                    "feedbacks": [],
                    "reactions": [],
                }
            ],
        }

    async def delete_conversation(self, **kwargs):
        self.calls.append(("delete_conversation", kwargs))
        return {
            "deleted_messages": 3,
            "deleted_storage_messages": 2,
            "deleted_storage_attachments": 1,
            "deleted_storage_executions": 0,
        }

    async def remove_user_reaction(self, **kwargs):
        self.calls.append(("remove_user_reaction", kwargs))
        return True

    async def clear_user_feedback_in_turn_log(self, **kwargs):
        self.calls.append(("clear_user_feedback_in_turn_log", kwargs))
        return True

    async def append_reaction_to_turn_log(self, **kwargs):
        self.calls.append(("append_reaction_to_turn_log", kwargs))
        return None

    async def apply_feedback_to_turn_log(self, **kwargs):
        self.calls.append(("apply_feedback_to_turn_log", kwargs))
        return {"ok": True}


class _FakeChatComm:
    def __init__(self):
        self.calls = []

    async def emit_conversation_status(self, **kwargs):
        self.calls.append(kwargs)


@pytest.fixture
def _router_state(monkeypatch):
    browser = _FakeConversationBrowser()
    chat_comm = _FakeChatComm()
    monkeypatch.setattr(
        conversations.router,
        "state",
        SimpleNamespace(conversation_browser=browser, chat_comm=chat_comm),
        raising=False,
    )
    return SimpleNamespace(browser=browser, chat_comm=chat_comm)


@pytest.mark.asyncio
async def test_list_conversations_uses_default_bundle_from_redis(monkeypatch, _router_state):
    async def _resolve_default(runtime_ctx, tenant, project):
        assert runtime_ctx is conversations.router.state
        assert tenant == "tenant-a"
        assert project == "project-a"
        return "bundle.default"

    monkeypatch.setattr(
        conversations,
        "resolve_default_bundle_id_from_runtime_ctx",
        _resolve_default,
    )

    session = SimpleNamespace(user_id="user-1")
    resp = await conversations.list_conversations(
        tenant="tenant-a",
        project="project-a",
        last_n=None,
        started_after=None,
        days=365,
        include_titles=True,
        bundle_id=None,
        agent_id=None,
        session=session,
    )

    assert resp["user_id"] == "user-1"
    assert _router_state.browser.calls == [
        (
            "list_conversations",
            {
                "user_id": "user-1",
                "last_n": None,
                "started_after": None,
                "days": 365,
                "include_titles": True,
                "bundle_id": "bundle.default",
                "agent_id": None,
            },
        )
    ]


@pytest.mark.asyncio
async def test_list_conversations_respects_explicit_bundle_id(monkeypatch, _router_state):
    async def _unexpected(*args, **kwargs):
        raise AssertionError("default bundle resolver should not be called")

    monkeypatch.setattr(
        conversations,
        "resolve_default_bundle_id_from_runtime_ctx",
        _unexpected,
    )

    session = SimpleNamespace(user_id="user-1")
    await conversations.list_conversations(
        tenant="tenant-a",
        project="project-a",
        last_n=None,
        started_after=None,
        days=365,
        include_titles=True,
        bundle_id="bundle.explicit",
        session=session,
    )

    assert _router_state.browser.calls[0][1]["bundle_id"] == "bundle.explicit"


@pytest.mark.asyncio
async def test_conversation_details_does_not_resolve_default_bundle_and_preserves_inferred_bundle_id(
    monkeypatch, _router_state
):
    async def _load_registry(runtime_ctx, tenant, project):
        assert runtime_ctx is conversations.router.state
        assert tenant == "tenant-a"
        assert project == "project-a"
        return SimpleNamespace(
            bundles={
                "bundle.from.db": object(),
                "bundle.other": object(),
            }
        )

    monkeypatch.setattr(
        conversations,
        "load_persisted_registry_from_runtime_ctx",
        _load_registry,
    )

    session = SimpleNamespace(user_id="user-1")
    resp = await conversations.conversation_details(
        tenant="tenant-a",
        project="project-a",
        conversation_id="conv-1",
        bundle_id=None,
        session=session,
    )

    assert resp["bundle_id"] == "bundle.from.db"
    assert resp["bundle_ids"] == ["bundle.from.db"]
    assert resp["turns"][0]["bundle_id"] == "bundle.from.db"
    assert _router_state.browser.calls[0] == (
        "get_conversation_details",
        {
            "user_id": "user-1",
            "conversation_id": "conv-1",
            "bundle_id": None,
            "bundle_ids": ["bundle.from.db", "bundle.other"],
        },
    )


@pytest.mark.asyncio
async def test_fetch_conversation_does_not_resolve_default_bundle_and_preserves_inferred_bundle_id(
    monkeypatch, _router_state
):
    async def _load_registry(runtime_ctx, tenant, project):
        assert runtime_ctx is conversations.router.state
        assert tenant == "tenant-a"
        assert project == "project-a"
        return SimpleNamespace(
            bundles={
                "bundle.from.db": object(),
                "bundle.other": object(),
            }
        )

    monkeypatch.setattr(
        conversations,
        "load_persisted_registry_from_runtime_ctx",
        _load_registry,
    )

    session = SimpleNamespace(user_id="user-1")
    req = conversations.ConversationFetchRequest()
    resp = await conversations.fetch_conversation(
        tenant="tenant-a",
        project="project-a",
        conversation_id="conv-1",
        req=req,
        bundle_id=None,
        agent_id=None,
        session=session,
    )

    assert resp["bundle_id"] == "bundle.from.db"
    assert resp["bundle_ids"] == ["bundle.from.db"]
    assert resp["turns"][0]["artifacts"][0]["bundle_id"] == "bundle.from.db"
    assert _router_state.browser.calls[0] == (
        "get_conversation_details",
        {
            "user_id": "user-1",
            "conversation_id": "conv-1",
            "bundle_id": None,
            "bundle_ids": ["bundle.from.db", "bundle.other"],
        },
    )
    assert _router_state.browser.calls[1] == (
        "fetch_conversation_artifacts",
        {
            "user_id": "user-1",
            "conversation_id": "conv-1",
            "turn_ids": None,
            "materialize": False,
            "days": 365,
            "bundle_id": None,
            "bundle_ids": ["bundle.from.db", "bundle.other"],
            "agent_id": None,
        },
    )


@pytest.mark.asyncio
async def test_conversation_status_scopes_to_registry_bundle_set(monkeypatch, _router_state):
    async def _load_registry(runtime_ctx, tenant, project):
        assert runtime_ctx is conversations.router.state
        assert tenant == "tenant-a"
        assert project == "project-a"
        return SimpleNamespace(
            bundles={
                "bundle.from.db": object(),
                "bundle.other": object(),
            }
        )

    monkeypatch.setattr(
        conversations,
        "load_persisted_registry_from_runtime_ctx",
        _load_registry,
    )

    session = SimpleNamespace(user_id="user-1")
    resp = await conversations.conversation_status(
        tenant="tenant-a",
        project="project-a",
        conversation_id="conv-1",
        session=session,
    )

    assert resp.state == "idle"
    assert _router_state.browser.calls[0] == (
        "get_conversation_details",
        {
            "user_id": "user-1",
            "conversation_id": "conv-1",
            "bundle_id": None,
            "bundle_ids": ["bundle.from.db", "bundle.other"],
        },
    )
    assert _router_state.browser.calls[1] == (
        "get_conversation_state",
        {
            "user_id": "user-1",
            "conversation_id": "conv-1",
            "bundle_ids": ["bundle.from.db", "bundle.other"],
            "missing_as_idle": True,
        },
    )


@pytest.mark.asyncio
async def test_fetch_conversation_returns_404_when_not_in_scoped_registry(monkeypatch, _router_state):
    async def _load_registry(runtime_ctx, tenant, project):
        return SimpleNamespace(bundles={"bundle.scoped": object()})

    async def _empty_fetch(**kwargs):
        _router_state.browser.calls.append(("fetch_conversation_artifacts", kwargs))
        return {
            "user_id": kwargs["user_id"],
            "conversation_id": kwargs["conversation_id"],
            "conversation_title": None,
            "turns": [],
        }

    monkeypatch.setattr(conversations, "load_persisted_registry_from_runtime_ctx", _load_registry)
    _router_state.browser.fetch_conversation_artifacts = _empty_fetch

    session = SimpleNamespace(user_id="user-1")
    req = conversations.ConversationFetchRequest()
    with pytest.raises(conversations.HTTPException) as exc:
        await conversations.fetch_conversation(
            tenant="tenant-a",
            project="project-a",
            conversation_id="conv-foreign",
            req=req,
            bundle_id=None,
            session=session,
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "Conversation not found"


@pytest.mark.asyncio
async def test_delete_conversation_scopes_index_delete_to_registry_bundle_set(monkeypatch, _router_state):
    async def _load_registry(runtime_ctx, tenant, project):
        return SimpleNamespace(
            bundles={
                "bundle.from.db": object(),
                "bundle.other": object(),
            }
        )

    monkeypatch.setattr(conversations, "load_persisted_registry_from_runtime_ctx", _load_registry)

    session = SimpleNamespace(user_id="user-1", session_id="session-1", user_type="standard", fingerprint="fp-1")
    resp = await conversations.delete_conversation(
        tenant="tenant-a",
        project="project-a",
        conversation_id="conv-1",
        session=session,
    )

    assert resp.conversation_id == "conv-1"
    assert ("get_conversation_details", {
        "user_id": "user-1",
        "conversation_id": "conv-1",
        "bundle_id": None,
        "bundle_ids": ["bundle.from.db", "bundle.other"],
    }) in _router_state.browser.calls
    assert ("delete_conversation", {
        "tenant": "tenant-a",
        "project": "project-a",
        "user_id": "user-1",
        "conversation_id": "conv-1",
        "user_type": "standard",
        "bundle_id": None,
        "bundle_ids": ["bundle.from.db", "bundle.other"],
    }) in _router_state.browser.calls



@pytest.mark.parametrize("refusal", ["busy", "cold_unavailable"])
@pytest.mark.asyncio
async def test_delete_conversation_maps_a_retryable_refusal_to_503(monkeypatch, _router_state, refusal):
    from fastapi import HTTPException

    from kdcube_ai_app.apps.chat.sdk.context.vector.conv_retention import (
        ConversationDeleteBusy,
        ConversationDeleteUnavailable,
    )

    async def _load_registry(runtime_ctx, tenant, project):
        return SimpleNamespace(bundles={"bundle.from.db": object()})

    async def _refuse(**kwargs):
        _router_state.browser.calls.append(("delete_conversation", kwargs))
        if refusal == "busy":
            raise ConversationDeleteBusy("the retention lock stayed held")
        raise ConversationDeleteUnavailable("no cold tier", code="conversation_delete_cold_unavailable")

    monkeypatch.setattr(conversations, "load_persisted_registry_from_runtime_ctx", _load_registry)
    monkeypatch.setattr(_router_state.browser, "delete_conversation", _refuse)
    session = SimpleNamespace(user_id="user-1", session_id="session-1", user_type="standard", fingerprint="fp-1")
    with pytest.raises(HTTPException) as exc:
        await conversations.delete_conversation(
            tenant="tenant-a", project="project-a", conversation_id="conv-1", session=session,
        )

    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == f"conversation_delete_{refusal}"
    assert exc.value.headers == {"Retry-After": "5"}
    assert _router_state.chat_comm.calls == []  # no "deleted" status for a refused delete

@pytest.mark.asyncio
async def test_fetch_turns_with_feedbacks_does_not_resolve_default_bundle_and_preserves_inferred_bundle_id(
    monkeypatch, _router_state
):
    async def _load_registry(runtime_ctx, tenant, project):
        return SimpleNamespace(
            bundles={
                "bundle.from.db": object(),
                "bundle.other": object(),
            }
        )

    monkeypatch.setattr(
        conversations,
        "load_persisted_registry_from_runtime_ctx",
        _load_registry,
    )

    session = SimpleNamespace(user_id="user-1")
    req = conversations.ConversationFeedbackTurnsRequest()
    resp = await conversations.fetch_turns_with_feedbacks(
        tenant="tenant-a",
        project="project-a",
        conversation_id="conv-1",
        req=req,
        session=session,
    )

    assert resp["bundle_id"] == "bundle.from.db"
    assert resp["bundle_ids"] == ["bundle.from.db"]
    assert resp["turns"][0]["bundle_id"] == "bundle.from.db"
    assert _router_state.browser.calls[0] == (
        "get_conversation_details",
        {
            "user_id": "user-1",
            "conversation_id": "conv-1",
            "bundle_id": None,
            "bundle_ids": ["bundle.from.db", "bundle.other"],
        },
    )
    assert _router_state.browser.calls[1] == (
        "fetch_turns_with_feedbacks",
        {
            "user_id": "user-1",
            "conversation_id": "conv-1",
            "turn_ids": None,
            "days": 365,
        },
    )


@pytest.mark.asyncio
async def test_delete_conversation_does_not_resolve_default_bundle(monkeypatch, _router_state):
    async def _load_registry(runtime_ctx, tenant, project):
        assert runtime_ctx is conversations.router.state
        assert tenant == "tenant-a"
        assert project == "project-a"
        return SimpleNamespace(
            bundles={
                "bundle.from.db": object(),
                "bundle.other": object(),
            }
        )

    monkeypatch.setattr(
        conversations,
        "load_persisted_registry_from_runtime_ctx",
        _load_registry,
    )

    session = SimpleNamespace(user_id="user-1", session_id="session-1", user_type="standard", fingerprint="fp-1")
    resp = await conversations.delete_conversation(
        tenant="tenant-a",
        project="project-a",
        conversation_id="conv-1",
        session=session,
    )

    assert resp.deleted_messages == 3
    assert _router_state.browser.calls[0] == (
        "get_conversation_details",
        {
            "user_id": "user-1",
            "conversation_id": "conv-1",
            "bundle_id": None,
            "bundle_ids": ["bundle.from.db", "bundle.other"],
        },
    )
    assert _router_state.browser.calls[1] == (
        "get_conversation_details",
        {
            "user_id": "user-1",
            "conversation_id": "conv-1",
            "bundle_id": None,
            "bundle_ids": ["bundle.from.db", "bundle.other"],
        },
    )
    assert _router_state.browser.calls[2] == (
        "delete_conversation",
        {
            "tenant": "tenant-a",
            "project": "project-a",
            "user_id": "user-1",
            "conversation_id": "conv-1",
            "user_type": "standard",
            "bundle_id": None,
            "bundle_ids": ["bundle.from.db", "bundle.other"],
        },
    )
    assert _router_state.chat_comm.calls[0]["bundle_id"] == "bundle.from.db"


@pytest.mark.asyncio
async def test_submit_turn_feedback_add_does_not_resolve_default_bundle(monkeypatch, _router_state):
    async def _load_registry(runtime_ctx, tenant, project):
        return SimpleNamespace(
            bundles={
                "bundle.from.db": object(),
                "bundle.other": object(),
            }
        )

    monkeypatch.setattr(
        conversations,
        "load_persisted_registry_from_runtime_ctx",
        _load_registry,
    )

    session = SimpleNamespace(user_id="user-1", user_type=SimpleNamespace(value="standard"))
    req = conversations.TurnFeedbackRequest(reaction="ok", text="Great")
    resp = await conversations.submit_turn_feedback(
        tenant="tenant-a",
        project="project-a",
        conversation_id="conv-1",
        turn_id="turn-1",
        req=req,
        session=session,
    )

    assert resp.success is True
    assert _router_state.browser.calls[0] == (
        "get_conversation_details",
        {
            "user_id": "user-1",
            "conversation_id": "conv-1",
            "bundle_id": None,
            "bundle_ids": ["bundle.from.db", "bundle.other"],
        },
    )
    assert _router_state.browser.calls[1][0] == "append_reaction_to_turn_log"
    assert _router_state.browser.calls[1][1]["bundle_id"] is None
    assert _router_state.browser.calls[2][0] == "apply_feedback_to_turn_log"
    assert _router_state.browser.calls[2][1]["bundle_id"] is None


@pytest.mark.asyncio
async def test_submit_turn_feedback_clear_does_not_resolve_default_bundle(monkeypatch, _router_state):
    async def _load_registry(runtime_ctx, tenant, project):
        return SimpleNamespace(
            bundles={
                "bundle.from.db": object(),
                "bundle.other": object(),
            }
        )

    monkeypatch.setattr(
        conversations,
        "load_persisted_registry_from_runtime_ctx",
        _load_registry,
    )

    session = SimpleNamespace(user_id="user-1", user_type=SimpleNamespace(value="standard"))
    req = conversations.TurnFeedbackRequest(reaction=None)
    resp = await conversations.submit_turn_feedback(
        tenant="tenant-a",
        project="project-a",
        conversation_id="conv-1",
        turn_id="turn-1",
        req=req,
        session=session,
    )

    assert resp.success is True
    assert _router_state.browser.calls[0] == (
        "get_conversation_details",
        {
            "user_id": "user-1",
            "conversation_id": "conv-1",
            "bundle_id": None,
            "bundle_ids": ["bundle.from.db", "bundle.other"],
        },
    )
    assert _router_state.browser.calls[1][0] == "remove_user_reaction"
    assert _router_state.browser.calls[2][0] == "clear_user_feedback_in_turn_log"
    assert _router_state.browser.calls[2][1]["bundle_id"] is None
