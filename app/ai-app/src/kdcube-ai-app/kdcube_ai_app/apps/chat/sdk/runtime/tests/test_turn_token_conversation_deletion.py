"""No turn token while the conversation is being deleted."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime import agent_capability_control as control


class _DeletionIndex:
    def __init__(self, deleting=False, fails=False):
        self.deleting, self.fails, self.asked = deleting, fails, []

    async def open_conversation_deletion(self, *, user_id, conversation_id):
        self.asked.append((user_id, conversation_id))
        if self.fails:
            raise RuntimeError("pg down")
        return {"deletion_id": "del_1", "state": "failed"} if self.deleting else None


def _entrypoint(index):
    async def get_ctx_client():
        return SimpleNamespace(idx=index)

    return SimpleNamespace(get_ctx_client=get_ctx_client)


@pytest.fixture
def hub_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(control, "agent_capability_identity",
                        lambda entrypoint: {"tenant": "t", "project": "p", "bundle_id": "app@1-0", "user_id": "u1"})

    async def hub(entrypoint, kind, payload, **kwargs):
        calls.append(payload)
        return SimpleNamespace(ok=True, object={"turn_token": "tok"}, error=None)

    monkeypatch.setattr(control, "_call_connection_hub", hub)
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("deleting,fails,expected", [(True, False, "conversation_deleting"),
                                                     (False, True, "conversation_registry_unavailable")])
async def test_every_token_path_is_refused_for_a_conversation_being_deleted(hub_calls, deleting, fails, expected):
    """Turn start, per-call MCP tokens and renewal all come through here; the check fails closed."""
    index = _DeletionIndex(deleting=deleting, fails=fails)
    for mode, token in (("renew", ""), ("per_call", ""), ("renew", "previous-token")):
        answer = await control.request_agent_turn_token(_entrypoint(index), agent_id="agent", conversation_id="c1",
                                                        turn_id="turn-1", mode=mode, turn_token=token)
        assert answer["ok"] is False and answer["error"] == expected
    assert hub_calls == [] and index.asked == [("u1", "c1")] * 3


@pytest.mark.asyncio
async def test_a_conversation_without_an_open_deletion_gets_its_token(hub_calls):
    answer = await control.request_agent_turn_token(_entrypoint(_DeletionIndex()), agent_id="agent",
                                                    conversation_id="c1", turn_id="turn-1")
    assert answer == {"ok": True, "turn_token": "tok"} and len(hub_calls) == 1
