"""The announce hotset aggregates across the identity family per the user pref."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import kdcube_ai_app.apps.chat.sdk.infra.bundle_operations as bundle_operations
from kdcube_ai_app.apps.chat.sdk.context.memory.models import MemoryScope
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.base_workflow import BaseWorkflow


_ACTOR = "a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d"
_LINKED = "telegram_100200300"


def _family_v1(memory_user_ids, *, ok=True):
    return {
        "ok": ok,
        "schema": "connection_hub.identity_family.v1",
        "memory_user_ids": list(memory_user_ids),
    }


def _runtime_ctx(*, aggregation=True, bundle_id="connection-hub@1-0"):
    return SimpleNamespace(
        memory_identity_family_aggregation=aggregation,
        memory_identity_family_bundle_id=bundle_id,
    )


_SCOPE = MemoryScope(tenant="t", project="p", user_id=_ACTOR, bundle_id="b@1")


class _Stub:
    _announce_identity_family_user_ids = BaseWorkflow._announce_identity_family_user_ids


@pytest.fixture(autouse=True)
def _patch_call(monkeypatch):
    holder = {}

    async def _fake_call(**kwargs):
        holder["called_with"] = kwargs
        resolve = holder["resolve"]
        if isinstance(resolve, Exception):
            raise resolve
        return resolve

    monkeypatch.setattr(bundle_operations, "call_bundle_operation", _fake_call)
    return holder


@pytest.mark.asyncio
async def test_announce_family_pref_aggregates(_patch_call) -> None:
    _patch_call["resolve"] = _family_v1([_ACTOR, _LINKED])
    out = await _Stub()._announce_identity_family_user_ids(_runtime_ctx(), _SCOPE, "family")
    assert out == [_ACTOR, _LINKED]
    call = _patch_call["called_with"]
    assert call["operation"] == "identity_family_resolve"
    assert call["route"] == "operations"
    assert call["data"] == {"input_user_id": _ACTOR}


@pytest.mark.asyncio
async def test_announce_channel_pref_single_actor(_patch_call) -> None:
    _patch_call["resolve"] = _family_v1([_ACTOR, _LINKED])
    out = await _Stub()._announce_identity_family_user_ids(_runtime_ctx(), _SCOPE, "channel")
    assert out is None
    assert "called_with" not in _patch_call


@pytest.mark.asyncio
async def test_announce_kill_switch_off(_patch_call) -> None:
    _patch_call["resolve"] = _family_v1([_ACTOR, _LINKED])
    out = await _Stub()._announce_identity_family_user_ids(
        _runtime_ctx(aggregation=False), _SCOPE, "family"
    )
    assert out is None
    assert "called_with" not in _patch_call


@pytest.mark.asyncio
async def test_announce_ok_false_falls_back(_patch_call) -> None:
    _patch_call["resolve"] = _family_v1([_ACTOR, _LINKED], ok=False)
    out = await _Stub()._announce_identity_family_user_ids(_runtime_ctx(), _SCOPE, "family")
    assert out is None


@pytest.mark.asyncio
async def test_announce_resolver_failure_falls_back(_patch_call) -> None:
    _patch_call["resolve"] = RuntimeError("hub down")
    out = await _Stub()._announce_identity_family_user_ids(_runtime_ctx(), _SCOPE, "family")
    assert out is None


@pytest.mark.asyncio
async def test_announce_unlinked_single_actor(_patch_call) -> None:
    _patch_call["resolve"] = _family_v1([_ACTOR])
    out = await _Stub()._announce_identity_family_user_ids(_runtime_ctx(), _SCOPE, "family")
    assert out is None
