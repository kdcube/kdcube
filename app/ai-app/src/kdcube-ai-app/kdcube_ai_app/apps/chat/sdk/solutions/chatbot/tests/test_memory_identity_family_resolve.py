"""The memory mixin resolves the identity family server-side for READ scoping.

Driven by the per-user ``memory_scope`` preference (default ``family``) and a
bundle kill-switch, calling the real Connection Hub ``identity_family_resolve``
operation shape (``connection_hub.identity_family.v1``).
"""

from __future__ import annotations

import pytest

import kdcube_ai_app.apps.chat.sdk.infra.bundle_operations as bundle_operations
from kdcube_ai_app.apps.chat.sdk.context.memory.models import MemoryScope
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_memory import (
    MemoryEntrypointMixin,
)


_ACTOR = "a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d"
_LINKED = "telegram_100200300"


def _family_v1(memory_user_ids, *, ok=True, linked=True):
    """The connection_hub.identity_family.v1 response shape."""
    return {
        "ok": ok,
        "schema": "connection_hub.identity_family.v1",
        "linked": linked,
        "platform_user_id": _ACTOR,
        "user_ids": list(memory_user_ids),
        "memory_user_ids": list(memory_user_ids),
    }


class _Logger:
    def log(self, *args, **kwargs):
        return None


class _Stub:
    """Minimal carrier exposing only what _memory_read_user_ids touches."""

    def __init__(self, *, kill_switch: bool = True, scope_pref: str = "family"):
        self._kill_switch = kill_switch
        self._scope_pref = scope_pref
        self.logger = _Logger()

    _memory_read_user_ids = MemoryEntrypointMixin._memory_read_user_ids

    def _memory_scope(self):
        return MemoryScope(tenant="t", project="p", user_id=_ACTOR, bundle_id="b@1")

    def _memory_identity_family_kill_switch_enabled(self) -> bool:
        return self._kill_switch

    def _memory_identity_family_bundle_id(self) -> str:
        return "connection-hub@1-0"

    async def _memory_scope_pref_for(self, scope) -> str:
        return self._scope_pref


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
async def test_resolves_family_with_canonical_call(_patch_call) -> None:
    _patch_call["resolve"] = _family_v1([_ACTOR, _LINKED])
    stub = _Stub(kill_switch=True, scope_pref="family")
    family = await stub._memory_read_user_ids()
    assert family == [_ACTOR, _LINKED]
    # Canonical call: connection-hub@1-0 / identity_family_resolve /
    # route="operations" / data={"input_user_id": <actor>}.
    call = _patch_call["called_with"]
    assert call["bundle_id"] == "connection-hub@1-0"
    assert call["operation"] == "identity_family_resolve"
    assert call["route"] == "operations"
    assert call["data"] == {"input_user_id": _ACTOR}


@pytest.mark.asyncio
async def test_channel_preference_skips_resolution(_patch_call) -> None:
    _patch_call["resolve"] = _family_v1([_ACTOR, _LINKED])
    stub = _Stub(kill_switch=True, scope_pref="channel")
    assert await stub._memory_read_user_ids() is None
    # "Only this channel" never calls the resolver.
    assert "called_with" not in _patch_call


@pytest.mark.asyncio
async def test_kill_switch_off_skips_resolution(_patch_call) -> None:
    _patch_call["resolve"] = _family_v1([_ACTOR, _LINKED])
    stub = _Stub(kill_switch=False, scope_pref="family")
    assert await stub._memory_read_user_ids() is None
    assert "called_with" not in _patch_call


@pytest.mark.asyncio
async def test_unlinked_actor_returns_none(_patch_call) -> None:
    _patch_call["resolve"] = _family_v1([_ACTOR], linked=False)
    stub = _Stub()
    assert await stub._memory_read_user_ids() is None


@pytest.mark.asyncio
async def test_ok_false_falls_back_to_single_actor(_patch_call) -> None:
    # ok:false with a stale family must NOT aggregate.
    _patch_call["resolve"] = _family_v1([_ACTOR, _LINKED], ok=False)
    stub = _Stub()
    assert await stub._memory_read_user_ids() is None


@pytest.mark.asyncio
async def test_empty_family_falls_back_to_single_actor(_patch_call) -> None:
    _patch_call["resolve"] = _family_v1([])
    stub = _Stub()
    assert await stub._memory_read_user_ids() is None


@pytest.mark.asyncio
async def test_resolver_failure_falls_back_to_none(_patch_call) -> None:
    _patch_call["resolve"] = RuntimeError("connection hub down")
    stub = _Stub()
    assert await stub._memory_read_user_ids() is None


@pytest.mark.asyncio
async def test_family_always_includes_actor(_patch_call) -> None:
    # Even if the resolver omits the actor, it is included for the read scope.
    _patch_call["resolve"] = _family_v1([_LINKED])
    stub = _Stub()
    assert await stub._memory_read_user_ids() == [_ACTOR, _LINKED]
