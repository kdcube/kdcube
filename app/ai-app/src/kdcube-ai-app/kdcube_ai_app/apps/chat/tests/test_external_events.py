# SPDX-License-Identifier: MIT

from __future__ import annotations

import time

import pytest

from kdcube_ai_app.apps.chat.external_events import build_conversation_external_event_source
from kdcube_ai_app.apps.chat.processor import EnhancedChatRequestProcessor
from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload


class _FakeRedis:
    def __init__(self):
        self._kv = {}
        self._streams = {}
        self._stream_seq = {}
        self._groups = {}

    async def incr(self, key):
        self._kv[key] = int(self._kv.get(key, 0)) + 1
        return self._kv[key]

    async def xadd(self, key, fields):
        seq = int(self._stream_seq.get(key, 0)) + 1
        self._stream_seq[key] = seq
        stream_id = f"{seq}-0"
        self._streams.setdefault(key, []).append((stream_id, dict(fields or {})))
        return stream_id

    async def xdel(self, key, *ids):
        ids_set = {str(item) for item in ids if item is not None}
        before = list(self._streams.get(key, []))
        self._streams[key] = [(sid, fields) for sid, fields in before if str(sid) not in ids_set]
        return len(before) - len(self._streams[key])

    async def lpush(self, key, value):
        self._kv.setdefault("__lists__", {})
        lists = self._kv["__lists__"]
        lists.setdefault(key, []).insert(0, value)
        return len(lists[key])

    async def xrange(self, key, min="-", max="+", count=None):
        items = list(self._streams.get(key, []))
        out = []
        for stream_id, fields in items:
            if min not in ("-", None, ""):
                exclusive = str(min).startswith("(")
                floor = str(min)[1:] if exclusive else str(min)
                if exclusive:
                    if stream_id <= floor:
                        continue
                elif stream_id < floor:
                    continue
            if max not in ("+", None, "") and stream_id > str(max):
                continue
            out.append((stream_id, dict(fields)))
            if count is not None and len(out) >= int(count):
                break
        return out

    async def xread(self, streams, count=None, block=None):
        del block
        out = []
        for key, start in (streams or {}).items():
            items = await self.xrange(key, min=f"({start}" if start not in ("$", None, "") else "-", max="+", count=count)
            if start == "$":
                items = []
            if items:
                out.append((key, items))
        return out

    async def xgroup_create(self, key, groupname, id="0-0", mkstream=False):
        if mkstream:
            self._streams.setdefault(key, [])
        group_key = (key, groupname)
        if group_key in self._groups:
            raise RuntimeError("BUSYGROUP Consumer Group name already exists")
        self._groups[group_key] = {"last_id": str(id or "0-0"), "pending": {}}
        return True

    async def xreadgroup(self, groupname, consumername, streams, count=None, block=None):
        del block
        out = []
        for key, start in (streams or {}).items():
            group = self._groups.setdefault((key, groupname), {"last_id": "0-0", "pending": {}})
            if start != ">":
                continue
            items = []
            for stream_id, fields in list(self._streams.get(key, [])):
                if self._compare_stream_ids(stream_id, group["last_id"]) <= 0:
                    continue
                group["last_id"] = stream_id
                group["pending"][stream_id] = {"consumer": consumername, "ts": time.time()}
                items.append((stream_id, dict(fields)))
                if count is not None and len(items) >= int(count):
                    break
            if items:
                out.append((key, items))
        return out

    async def xack(self, key, groupname, *ids):
        group = self._groups.setdefault((key, groupname), {"last_id": "0-0", "pending": {}})
        acked = 0
        for stream_id in ids:
            if stream_id in group["pending"]:
                group["pending"].pop(stream_id, None)
                acked += 1
        return acked

    async def xautoclaim(self, key, groupname, consumername, min_idle_time, start_id="0-0", count=None):
        group = self._groups.setdefault((key, groupname), {"last_id": "0-0", "pending": {}})
        now = time.time()
        claimed = []
        next_start = start_id
        for stream_id, info in sorted(group["pending"].items()):
            if self._compare_stream_ids(stream_id, str(start_id or "0-0")) < 0:
                continue
            idle_ms = int((now - float(info.get("ts") or 0.0)) * 1000)
            next_start = stream_id
            if idle_ms < int(min_idle_time or 0):
                continue
            info["consumer"] = consumername
            info["ts"] = now
            payload = self._lookup_stream_entry(key, stream_id)
            if payload is not None:
                claimed.append(payload)
            if count is not None and len(claimed) >= int(count):
                break
        return next_start, claimed, []

    async def setex(self, key, ttl, value):
        del ttl
        self._kv[key] = value

    async def set(self, key, value, ex=None, nx=False):
        del ex
        if nx and key in self._kv:
            return False
        self._kv[key] = value
        return True

    async def eval(self, script, numkeys, *args):
        del script
        if int(numkeys) == 3 and len(args) == 12:
            epoch_key, token_key, owner_key = args[:3]
            ttl, turn_id, bundle_id, instance_id, process_id, listener_id, lease_token, started_at, updated_at = args[3:]
            del ttl
            epoch = int(self._kv.get(epoch_key, 0)) + 1
            self._kv[epoch_key] = epoch
            self._kv[token_key] = lease_token
            self._kv[owner_key] = (
                '{'
                f'"turn_id":"{turn_id}",'
                f'"bundle_id":"{bundle_id}",'
                f'"instance_id":"{instance_id}",'
                f'"process_id":{int(process_id)},'
                f'"listener_id":"{listener_id}",'
                f'"lease_token":"{lease_token}",'
                f'"lease_epoch":{epoch},'
                f'"started_at":"{started_at}",'
                f'"updated_at":"{updated_at}"'
                '}'
            )
            return epoch
        if int(numkeys) == 2 and len(args) == 5:
            token_key, owner_key, expected, ttl, owner_json, lease_token = args
            current = self._kv.get(token_key)
            if current != expected:
                return 0
            self._kv[owner_key] = owner_json
            self._kv[token_key] = lease_token
            return 1
        if int(numkeys) == 2 and len(args) == 3:
            token_key, owner_key, expected = args
            current = self._kv.get(token_key)
            if current != expected:
                return 0
            self._kv.pop(owner_key, None)
            self._kv.pop(token_key, None)
            return 1
        raise NotImplementedError("eval shape not supported in fake redis")

    async def get(self, key):
        return self._kv.get(key)

    async def delete(self, key):
        self._kv.pop(key, None)

    def _lookup_stream_entry(self, key, stream_id):
        for sid, fields in self._streams.get(key, []):
            if sid == stream_id:
                return sid, dict(fields)
        return None

    def _compare_stream_ids(self, left: str, right: str) -> int:
        def _parts(value: str) -> tuple[int, int]:
            try:
                first, second = str(value or "").split("-", 1)
                return int(first), int(second)
            except Exception:
                return 0, 0
        l = _parts(left)
        r = _parts(right)
        if l < r:
            return -1
        if l > r:
            return 1
        return 0


@pytest.mark.asyncio
async def test_external_event_source_publish_read_and_owner():
    redis = _FakeRedis()
    source = build_conversation_external_event_source(
        redis=redis,
        tenant="t1",
        project="p1",
        conversation_id="conv1",
    )

    first = await source.publish(
        kind="followup",
        explicit=True,
        target_turn_id="turn_a",
        active_turn_id_at_ingress="turn_b",
        owner_turn_id="turn_b",
        source="ingress.sse",
        text="also include legal cases",
        payload={"message": "also include legal cases"},
    )
    second = await source.publish(
        kind="steer",
        explicit=True,
        active_turn_id_at_ingress="turn_b",
        owner_turn_id="turn_b",
        source="ingress.sse",
        text="change direction",
    )

    items = await source.read_since(0)
    assert [item.sequence for item in items] == [1, 2]
    assert items[0].message_id == first.message_id
    assert items[1].message_id == second.message_id
    assert items[0].text == "also include legal cases"
    assert items[0].stream_id == "1-0"
    assert items[1].stream_id == "2-0"

    lease = await source.acquire_owner(turn_id="turn_b", bundle_id="bundle@1", listener_id="listener_1")
    assert lease.turn_id == "turn_b"
    assert lease.lease_token
    assert lease.lease_epoch == 1
    owner = await source.get_owner()
    assert owner is not None
    assert owner.listener_id == "listener_1"

    rejected = await source.refresh_owner(
        listener_id="listener_1",
        turn_id="turn_b",
        bundle_id="bundle@1",
        lease_token="wrong-token",
    )
    assert rejected is None

    refreshed = await source.refresh_owner(
        listener_id="listener_1",
        turn_id="turn_b",
        bundle_id="bundle@1",
        lease_token=lease.lease_token,
    )
    assert refreshed.listener_id == "listener_1"
    assert refreshed.lease_epoch == lease.lease_epoch
    owner = await source.get_owner()
    assert owner is not None
    assert owner.turn_id == "turn_b"

    released = await source.release_owner(listener_id="listener_1", lease_token="wrong-token")
    assert released is False
    assert await source.get_owner() is not None

    released = await source.release_owner(listener_id="listener_1", lease_token=lease.lease_token)
    assert released is True
    assert await source.get_owner() is None

    lease_2 = await source.acquire_owner(turn_id="turn_c", bundle_id="bundle@1", listener_id="listener_2")
    assert lease_2.lease_epoch == 2
    owner = await source.get_owner()
    assert owner is not None
    assert owner.turn_id == "turn_c"
    assert owner.listener_id == "listener_2"


@pytest.mark.asyncio
async def test_external_event_source_claim_promote_and_consume():
    redis = _FakeRedis()
    source = build_conversation_external_event_source(
        redis=redis,
        tenant="t1",
        project="p1",
        conversation_id="conv1",
    )

    first = await source.publish(
        kind="followup",
        source="ingress.sse",
        text="continue with pricing",
        payload={"message": "continue with pricing"},
        task_payload={
            "meta": {"task_id": "task-1", "created_at": 1.0, "instance_id": "ingress-1"},
            "routing": {"bundle_id": "bundle.demo", "session_id": "sess-1", "conversation_id": "conv1", "turn_id": "turn-2"},
            "actor": {"tenant_id": "t1", "project_id": "p1"},
            "user": {"user_type": "registered", "user_id": "u1"},
            "request": {"message": "continue with pricing", "payload": {}},
            "config": {"values": {}},
            "accounting": {"envelope": {}},
            "continuation": {"kind": "followup", "explicit": False},
        },
    )
    await source.publish(
        kind="steer",
        source="ingress.sse",
        text="change direction",
        payload={"message": "change direction"},
    )

    claimed = await source.claim_next_promotable(claimant_id="proc-1")
    assert claimed is not None
    assert claimed.message_id == first.message_id

    await source.mark_promoted(message_id=claimed.message_id, claimant_id="proc-1", task_id="task-1")
    promoted = await source.get_event(claimed.message_id)
    assert promoted is not None
    assert promoted.promoted_task_id == "task-1"
    pending = redis._groups[(source.log_key, source.promotion_group)]["pending"]
    assert pending == {}

    updated = await source.mark_consumed_up_to(max_sequence=2, turn_id="turn-2")
    assert updated == 2
    first_after = await source.get_event(first.message_id)
    assert first_after is not None
    assert first_after.consumed_by_turn_id == "turn-2"


@pytest.mark.asyncio
async def test_external_event_source_wait_and_cursor_progression():
    redis = _FakeRedis()
    source = build_conversation_external_event_source(
        redis=redis,
        tenant="t1",
        project="p1",
        conversation_id="conv1",
    )

    first = await source.publish(
        kind="followup",
        source="ingress.sse",
        text="first",
        payload={"message": "first"},
        task_payload={
            "meta": {"task_id": "task-1", "created_at": 1.0, "instance_id": "ingress-1"},
            "routing": {"bundle_id": "bundle.demo", "session_id": "sess-1", "conversation_id": "conv1", "turn_id": "turn-2"},
            "actor": {"tenant_id": "t1", "project_id": "p1"},
            "user": {"user_type": "registered", "user_id": "u1"},
            "request": {"message": "first", "payload": {}},
            "config": {"values": {}},
            "accounting": {"envelope": {}},
            "continuation": {"kind": "followup", "explicit": False},
        },
    )
    waited = await source.wait_for_events_after("0-0", block_ms=1)
    assert waited
    assert waited[0].stream_id == first.stream_id

    claimed = await source.claim_next_promotable(claimant_id="proc-1")
    assert claimed is not None
    await source.mark_promoted(message_id=claimed.message_id, claimant_id="proc-1", task_id="task-1")

    second = await source.publish(
        kind="followup",
        source="ingress.sse",
        text="second",
        payload={"message": "second"},
        task_payload={
            "meta": {"task_id": "task-2", "created_at": 2.0, "instance_id": "ingress-1"},
            "routing": {"bundle_id": "bundle.demo", "session_id": "sess-1", "conversation_id": "conv1", "turn_id": "turn-3"},
            "actor": {"tenant_id": "t1", "project_id": "p1"},
            "user": {"user_type": "registered", "user_id": "u1"},
            "request": {"message": "second", "payload": {}},
            "config": {"values": {}},
            "accounting": {"envelope": {}},
            "continuation": {"kind": "followup", "explicit": False},
        },
    )
    claimed_next = await source.claim_next_promotable(claimant_id="proc-2")
    assert claimed_next is not None
    assert claimed_next.message_id == second.message_id


@pytest.mark.asyncio
async def test_external_event_source_retention_trims_terminal_entries_only():
    redis = _FakeRedis()
    source = build_conversation_external_event_source(
        redis=redis,
        tenant="t1",
        project="p1",
        conversation_id="conv1",
        stream_max_entries=2,
        stream_retention_seconds=0,
    )

    first = await source.publish(
        kind="followup",
        source="ingress.sse",
        text="first",
        payload={"message": "first"},
        task_payload={
            "meta": {"task_id": "task-1", "created_at": 1.0, "instance_id": "ingress-1"},
            "routing": {"bundle_id": "bundle.demo", "session_id": "sess-1", "conversation_id": "conv1", "turn_id": "turn-1"},
            "actor": {"tenant_id": "t1", "project_id": "p1"},
            "user": {"user_type": "registered", "user_id": "u1"},
            "request": {"message": "first", "payload": {}},
            "config": {"values": {}},
            "accounting": {"envelope": {}},
            "continuation": {"kind": "followup", "explicit": False},
        },
    )
    second = await source.publish(
        kind="followup",
        source="ingress.sse",
        text="second",
        payload={"message": "second"},
        task_payload={
            "meta": {"task_id": "task-2", "created_at": 2.0, "instance_id": "ingress-1"},
            "routing": {"bundle_id": "bundle.demo", "session_id": "sess-1", "conversation_id": "conv1", "turn_id": "turn-2"},
            "actor": {"tenant_id": "t1", "project_id": "p1"},
            "user": {"user_type": "registered", "user_id": "u1"},
            "request": {"message": "second", "payload": {}},
            "config": {"values": {}},
            "accounting": {"envelope": {}},
            "continuation": {"kind": "followup", "explicit": False},
        },
    )

    claimed_first = await source.claim_next_promotable(claimant_id="proc-1")
    assert claimed_first is not None
    await source.mark_promoted(message_id=claimed_first.message_id, claimant_id="proc-1", task_id="task-1")

    third = await source.publish(
        kind="steer",
        source="ingress.sse",
        text="third",
        payload={"message": "third"},
    )

    stream_ids = [sid for sid, _fields in redis._streams[source.log_key]]
    assert stream_ids == [second.stream_id, third.stream_id]
    assert await source.get_event(first.message_id) is None
    assert await source.get_event(second.message_id) is not None


@pytest.mark.asyncio
async def test_external_event_source_retention_trims_terminal_entries_by_age():
    redis = _FakeRedis()
    source = build_conversation_external_event_source(
        redis=redis,
        tenant="t1",
        project="p1",
        conversation_id="conv1",
        stream_max_entries=0,
        stream_retention_seconds=1,
    )

    first = await source.publish(
        kind="followup",
        source="ingress.sse",
        text="first",
        payload={"message": "first"},
        task_payload={
            "meta": {"task_id": "task-1", "created_at": 1.0, "instance_id": "ingress-1"},
            "routing": {"bundle_id": "bundle.demo", "session_id": "sess-1", "conversation_id": "conv1", "turn_id": "turn-1"},
            "actor": {"tenant_id": "t1", "project_id": "p1"},
            "user": {"user_type": "registered", "user_id": "u1"},
            "request": {"message": "first", "payload": {}},
            "config": {"values": {}},
            "accounting": {"envelope": {}},
            "continuation": {"kind": "followup", "explicit": False},
        },
    )
    claimed = await source.claim_next_promotable(claimant_id="proc-1")
    assert claimed is not None
    await source.mark_promoted(message_id=claimed.message_id, claimant_id="proc-1", task_id="task-1")

    stored = await source.get_event(first.message_id)
    assert stored is not None
    stored.promoted_at = time.time() - 10
    await source._write_event(stored)

    removed = await source._maybe_cleanup_retention()
    assert removed == 1
    assert redis._streams[source.log_key] == []
    assert await source.get_event(first.message_id) is None


@pytest.mark.asyncio
async def test_processor_promotes_next_external_event_from_shared_log():
    redis = _FakeRedis()
    source = build_conversation_external_event_source(
        redis=redis,
        tenant="t1",
        project="p1",
        conversation_id="conv1",
    )
    current_payload = ChatTaskPayload.model_validate(
        {
            "meta": {"task_id": "task-current", "created_at": 1.0, "instance_id": "proc-1"},
            "routing": {"bundle_id": "bundle.demo", "session_id": "sess-1", "conversation_id": "conv1", "turn_id": "turn-1"},
            "actor": {"tenant_id": "t1", "project_id": "p1"},
            "user": {"user_type": "registered", "user_id": "u1"},
            "request": {"message": "current", "payload": {}},
            "config": {"values": {}},
            "accounting": {"envelope": {}},
            "continuation": {"kind": "regular", "explicit": False},
        }
    )
    next_payload = {
        "meta": {"task_id": "task-next", "created_at": 2.0, "instance_id": "ingress-1"},
        "routing": {"bundle_id": "bundle.demo", "session_id": "sess-1", "conversation_id": "conv1", "turn_id": "turn-2"},
        "actor": {"tenant_id": "t1", "project_id": "p1"},
        "user": {"user_type": "registered", "user_id": "u1"},
        "request": {"message": "followup", "payload": {}},
        "config": {"values": {}},
        "accounting": {"envelope": {}},
        "continuation": {"kind": "followup", "explicit": False},
    }
    event = await source.publish(
        kind="followup",
        source="ingress.sse",
        text="followup",
        payload={"message": "followup"},
        task_payload=next_payload,
    )

    processor = EnhancedChatRequestProcessor.__new__(EnhancedChatRequestProcessor)
    processor.redis = redis
    processor.process_id = 551
    processor.middleware = type("MW", (), {"instance_id": "proc-1", "QUEUE_PREFIX": "chat-ready"})()

    promoted = await processor._promote_next_external_event(current_payload)
    assert promoted is not None
    assert promoted["payload"].routing.turn_id == "turn-2"
    assert redis._kv["__lists__"]["chat-ready:registered"]
    stored = await source.get_event(event.message_id)
    assert stored is not None
    assert stored.promoted_task_id == "task-next"


@pytest.mark.asyncio
async def test_processor_discards_stale_steers_before_promoting_followup():
    redis = _FakeRedis()
    source = build_conversation_external_event_source(
        redis=redis,
        tenant="t1",
        project="p1",
        conversation_id="conv1",
    )
    current_payload = ChatTaskPayload.model_validate(
        {
            "meta": {"task_id": "task-current", "created_at": 1.0, "instance_id": "proc-1"},
            "routing": {"bundle_id": "bundle.demo", "session_id": "sess-1", "conversation_id": "conv1", "turn_id": "turn-1"},
            "actor": {"tenant_id": "t1", "project_id": "p1"},
            "user": {"user_type": "registered", "user_id": "u1"},
            "request": {"message": "current", "payload": {}},
            "config": {"values": {}},
            "accounting": {"envelope": {}},
            "continuation": {"kind": "regular", "explicit": False},
        }
    )
    steer_one = await source.publish(
        kind="steer",
        explicit=True,
        target_turn_id="turn-1",
        active_turn_id_at_ingress="turn-1",
        owner_turn_id="turn-1",
        source="ingress.sse",
        text="stop",
        payload={"message": "stop"},
        task_payload={
            "meta": {"task_id": "task-steer-1", "created_at": 2.0, "instance_id": "ingress-1"},
            "routing": {"bundle_id": "bundle.demo", "session_id": "sess-1", "conversation_id": "conv1", "turn_id": "turn-steer-1"},
            "actor": {"tenant_id": "t1", "project_id": "p1"},
            "user": {"user_type": "registered", "user_id": "u1"},
            "request": {"message": "", "payload": {}},
            "config": {"values": {}},
            "accounting": {"envelope": {}},
            "continuation": {"kind": "steer", "explicit": True, "active_turn_id": "turn-1", "target_turn_id": "turn-1"},
        },
    )
    steer_two = await source.publish(
        kind="steer",
        explicit=True,
        target_turn_id="turn-1",
        active_turn_id_at_ingress="turn-1",
        owner_turn_id="turn-1",
        source="ingress.sse",
        text="stop again",
        payload={"message": "stop again"},
        task_payload={
            "meta": {"task_id": "task-steer-2", "created_at": 3.0, "instance_id": "ingress-1"},
            "routing": {"bundle_id": "bundle.demo", "session_id": "sess-1", "conversation_id": "conv1", "turn_id": "turn-steer-2"},
            "actor": {"tenant_id": "t1", "project_id": "p1"},
            "user": {"user_type": "registered", "user_id": "u1"},
            "request": {"message": "", "payload": {}},
            "config": {"values": {}},
            "accounting": {"envelope": {}},
            "continuation": {"kind": "steer", "explicit": True, "active_turn_id": "turn-1", "target_turn_id": "turn-1"},
        },
    )
    followup = await source.publish(
        kind="followup",
        source="ingress.sse",
        text="followup",
        payload={"message": "followup"},
        task_payload={
            "meta": {"task_id": "task-next", "created_at": 4.0, "instance_id": "ingress-1"},
            "routing": {"bundle_id": "bundle.demo", "session_id": "sess-1", "conversation_id": "conv1", "turn_id": "turn-2"},
            "actor": {"tenant_id": "t1", "project_id": "p1"},
            "user": {"user_type": "registered", "user_id": "u1"},
            "request": {"message": "followup", "payload": {}},
            "config": {"values": {}},
            "accounting": {"envelope": {}},
            "continuation": {"kind": "followup", "explicit": False, "active_turn_id": "turn-1"},
        },
    )

    processor = EnhancedChatRequestProcessor.__new__(EnhancedChatRequestProcessor)
    processor.redis = redis
    processor.process_id = 551
    processor.middleware = type("MW", (), {"instance_id": "proc-1", "QUEUE_PREFIX": "chat-ready"})()

    promoted = await processor._promote_next_external_event(current_payload)

    assert promoted is not None
    assert promoted["payload"].routing.turn_id == "turn-2"
    assert redis._kv["__lists__"]["chat-ready:registered"]

    steer_one_state = await source.get_event(steer_one.message_id)
    steer_two_state = await source.get_event(steer_two.message_id)
    followup_state = await source.get_event(followup.message_id)
    assert steer_one_state is not None
    assert steer_two_state is not None
    assert followup_state is not None
    assert steer_one_state.failed_reason == "steer_expired_not_promoted"
    assert steer_two_state.failed_reason == "steer_expired_not_promoted"
    assert followup_state.promoted_task_id == "task-next"
