
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.api.conversations import conversations


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
            "turns": [],
        }

    async def fetch_conversation_artifacts(self, **kwargs):
        self.calls.append(("fetch_conversation_artifacts", kwargs))
        return {
            "user_id": kwargs["user_id"],
            "conversation_id": kwargs["conversation_id"],
            "conversation_title": "New Conversation",
            "turns": [],
        }


@pytest.fixture
def _router_state(monkeypatch):
    browser = _FakeConversationBrowser()
    monkeypatch.setattr(
        conversations.router,
        "state",
        SimpleNamespace(conversation_browser=browser),
        raising=False,
    )
    return browser


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
        session=session,
    )

    assert resp["user_id"] == "user-1"
    assert _router_state.calls == [
        (
            "list_conversations",
            {
                "user_id": "user-1",
                "last_n": None,
                "started_after": None,
                "days": 365,
                "include_titles": True,
                "bundle_id": "bundle.default",
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

    assert _router_state.calls[0][1]["bundle_id"] == "bundle.explicit"


@pytest.mark.asyncio
async def test_conversation_details_returns_top_level_bundle_id(monkeypatch, _router_state):
    async def _resolve_default(runtime_ctx, tenant, project):
        del runtime_ctx, tenant, project
        return "bundle.default"

    monkeypatch.setattr(
        conversations,
        "resolve_default_bundle_id_from_runtime_ctx",
        _resolve_default,
    )

    session = SimpleNamespace(user_id="user-1")
    resp = await conversations.conversation_details(
        tenant="tenant-a",
        project="project-a",
        conversation_id="conv-1",
        bundle_id=None,
        session=session,
    )

    assert resp["bundle_id"] == "bundle.default"
    assert _router_state.calls[0] == (
        "get_conversation_details",
        {
            "user_id": "user-1",
            "conversation_id": "conv-1",
            "bundle_id": "bundle.default",
        },
    )


@pytest.mark.asyncio
async def test_fetch_conversation_returns_top_level_bundle_id(monkeypatch, _router_state):
    async def _resolve_default(runtime_ctx, tenant, project):
        del runtime_ctx, tenant, project
        return "bundle.default"

    monkeypatch.setattr(
        conversations,
        "resolve_default_bundle_id_from_runtime_ctx",
        _resolve_default,
    )

    session = SimpleNamespace(user_id="user-1")
    req = conversations.ConversationFetchRequest()
    resp = await conversations.fetch_conversation(
        tenant="tenant-a",
        project="project-a",
        conversation_id="conv-1",
        req=req,
        bundle_id=None,
        session=session,
    )

    assert resp["bundle_id"] == "bundle.default"
    assert _router_state.calls[0] == (
        "fetch_conversation_artifacts",
        {
            "user_id": "user-1",
            "conversation_id": "conv-1",
            "turn_ids": None,
            "materialize": False,
            "days": 365,
            "bundle_id": "bundle.default",
        },
    )
