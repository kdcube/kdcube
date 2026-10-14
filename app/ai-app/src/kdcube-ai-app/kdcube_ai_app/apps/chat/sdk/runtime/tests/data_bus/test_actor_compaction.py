"""W827 S2c: storing the compact Card-bound actor changes nothing any consumer sees after the live re-check."""
from __future__ import annotations

import copy
import json
import time
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.application_operations import APPLICATION_OPERATION_POLICY_PROPERTY
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus import worker as worker_module
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.actor_compaction import compact_card_actor
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.stream import RedisDataBusStream
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.types import DataBusMessage

RESOURCE = "https://board.example/api/integrations/bundles/t/p/problem-board@1-0/public/mcp/problem_board"
OTHER = "https://other.example/mcp"


def _card(*, operations=("board.get", "worker.heartbeat"), policy=None):
    return SimpleNamespace(
        access_id="card-a", client_id="client-a", grantor_subject="user-a", delegate_subject="worker-a",
        expires_at=int(time.time()) + 3600,
        resource_grants={RESOURCE: ("work:relay",), OTHER: ("other:grant",) * 40},
        resource_operations={RESOURCE: tuple(operations), OTHER: tuple(f"op.{n}" for n in range(200))},
        properties={APPLICATION_OPERATION_POLICY_PROPERTY: policy} if policy is not None else {},
    )


def _stored_actor(card, **top):
    """The actor ingress builds today (publish._apply_live_delegated_card): every resource copied in."""
    return {
        "session_id": "s1", "user_type": "registered", "user_id": "card:card-a", "username": "worker",
        "email": "", "fingerprint": "fp", "roles": ["kdcube:role:registered"], "permissions": ["work:relay"],
        "timezone": "UTC", "rate_limit_subject": "card:card-a",
        "identity_authority": {
            "delegated_resource": RESOURCE, "authority_id": "kept-authority", "economics_user_id": "user-a",
            "grants": ["work:relay"], "scopes": ["work:relay"], "operations": list(card.resource_operations[RESOURCE]),
            "resource_grants": {k: list(v) for k, v in card.resource_grants.items()},
            "resource_operations": {k: list(v) for k, v in card.resource_operations.items()},
            "roles": ["kdcube:role:registered"], "permissions": ["work:relay"], "delegated_roles_selected": False,
            "delegated_card_binding": {
                "schema": "connection_hub.delegated_card_binding.v1", "access_id": "card-a",
                "client_id": "client-a", "grantor_user_id": "user-a", "delegate_identity": "worker-a",
                "expires_at": card.expires_at,
            },
        },
        **top,
    }


async def _resolve(monkeypatch, card, actor):
    async def resolve(*args, **kwargs):
        assert kwargs["access_id"] == "card-a"
        assert (kwargs["expected_client_id"], kwargs["expected_grantor_subject"],
                kwargs["expected_delegate_subject"]) == ("client-a", "user-a", "worker-a")
        return card

    monkeypatch.setattr(worker_module, "resolve_live_grant_card", resolve)
    monkeypatch.setattr(worker_module, "delegated_card_store", lambda **scope: object())
    return await worker_module._resolve_live_delegated_actor(object(), tenant="t", project="p",
                                                             actor=copy.deepcopy(actor))


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", [None, {"enabled": True, "operations": ["board.get"]}])
async def test_the_live_recheck_yields_the_same_actor_from_the_compact_and_the_full_copy(monkeypatch, policy):
    card = _card(policy=policy)
    full = _stored_actor(card)
    if policy is not None:
        full["identity_authority"][APPLICATION_OPERATION_POLICY_PROPERTY] = policy

    from_full = await _resolve(monkeypatch, card, full)
    from_compact = await _resolve(monkeypatch, card, compact_card_actor(full))

    assert from_compact == from_full


@pytest.mark.asyncio
async def test_a_card_changed_since_publish_is_what_the_handler_sees(monkeypatch):
    """The stored copy was never the authority: the live Card's narrower operations win either way."""
    published = _stored_actor(_card())
    narrowed = _card(operations=("board.get",))

    resolved = await _resolve(monkeypatch, narrowed, compact_card_actor(published))

    assert resolved["identity_authority"]["operations"] == ["board.get"]


def test_compaction_keeps_the_recheck_inputs_and_every_other_field():
    full = _stored_actor(_card())
    compact = compact_card_actor(full)
    authority = compact["identity_authority"]

    assert {k: v for k, v in compact.items() if k != "identity_authority"} == {
        k: v for k, v in full.items() if k != "identity_authority"}
    assert authority["delegated_resource"] == RESOURCE
    assert authority["authority_id"] == "kept-authority" and authority["economics_user_id"] == "user-a"
    assert authority["delegated_card_binding"] == {
        "access_id": "card-a", "client_id": "client-a", "grantor_user_id": "user-a", "delegate_identity": "worker-a"}
    for dropped in ("grants", "scopes", "operations", "resource_grants", "resource_operations", "roles",
                    "permissions", "delegated_roles_selected"):
        assert dropped not in authority
    assert len(json.dumps(compact)) < len(json.dumps(full)) / 3


def test_an_actor_without_a_card_binding_is_stored_unchanged():
    plain = {"user_id": "u", "roles": ["r"], "identity_authority": {"grants": ["g"], "operations": ["o"]}}
    unbound = {"user_id": "u", "identity_authority": {"grants": ["g"], "delegated_card_binding": {"access_id": ""}}}
    assert compact_card_actor(plain) == plain
    assert compact_card_actor(unbound) == unbound
    assert compact_card_actor(None) == {}


class _Recorder:
    def __init__(self):
        self.added = []

    async def xgroup_create(self, *args, **kwargs):
        return True

    async def xadd(self, key, fields, **kwargs):
        self.added.append(json.loads(fields["json"]))
        return f"1-{len(self.added)}"


@pytest.mark.asyncio
async def test_publish_and_requeue_store_the_compact_actor():
    redis = _Recorder()
    stream = RedisDataBusStream(redis, tenant="t", project="p", bundle_id="b@1")
    full = _stored_actor(_card())
    message = DataBusMessage(message_id="m", tenant="t", project="p", bundle_id="b@1", subject="s", actor=full)

    await stream.publish(message)

    assert redis.added[0]["actor"] == compact_card_actor(full)
