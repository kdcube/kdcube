from __future__ import annotations

import pytest

from kdcube_ai_app.infra.jobs.stream import RedisBackgroundJobStream


class _FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.groups: set[tuple[str, str]] = set()
        self.acked: list[tuple[str, str, str]] = []

    async def set(self, key, value, nx=False, ex=None):
        del ex
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def delete(self, key):
        self.values.pop(key, None)
        return 1

    async def xadd(self, key, fields, maxlen=None, approximate=True):
        del maxlen, approximate
        stream = self.streams.setdefault(key, [])
        stream_id = f"{len(stream) + 1}-0"
        stream.append((stream_id, dict(fields)))
        return stream_id

    async def xgroup_create(self, key, groupname, id="0-0", mkstream=True):
        del id
        if mkstream:
            self.streams.setdefault(key, [])
        group_key = (key, groupname)
        if group_key in self.groups:
            raise RuntimeError("BUSYGROUP Consumer Group name already exists")
        self.groups.add(group_key)
        return True

    async def xreadgroup(self, groupname, consumername, streams, count=None, block=None):
        del groupname, consumername, count, block
        key = next(iter(streams.keys()))
        stream = self.streams.get(key, [])
        if not stream:
            return []
        stream_id, fields = stream.pop(0)
        return [(key, [(stream_id, fields)])]

    async def xautoclaim(self, key, groupname, consumername, min_idle_time, start_id="0-0", count=None):
        del key, groupname, consumername, min_idle_time, start_id, count
        return ["0-0", []]

    async def xack(self, key, groupname, stream_id):
        self.acked.append((key, groupname, stream_id))
        return 1


@pytest.mark.asyncio
async def test_background_job_stream_enqueue_dedupe_claim_ack():
    redis = _FakeRedis()
    stream = RedisBackgroundJobStream(redis, tenant="demo-tenant", project="demo-project")

    first = await stream.enqueue(
        work_kind="task.execution.due",
        bundle_id="task-and-memo-app@1-0",
        user_id="user-a",
        queue_label="registered",
        job_id="job-1",
        dedupe_key="bundle:user-a:task-1:slot-1",
        identity_authority={
            "actor_user_id": "telegram_42",
            "economics_user_id": "platform-user-1",
            "platform_roles": ["kdcube:role:super-admin"],
        },
        metadata={"conversation_id": "conv-1", "turn_id": "turn-1"},
        payload={"task_id": "task-1", "execution_id": "exec-1"},
    )
    duplicate = await stream.enqueue(
        work_kind="task.execution.due",
        bundle_id="task-and-memo-app@1-0",
        user_id="user-a",
        queue_label="registered",
        job_id="job-1",
        dedupe_key="bundle:user-a:task-1:slot-1",
        metadata={},
        payload={},
    )

    assert first.enqueued is True
    assert duplicate.enqueued is False
    assert duplicate.reason == "duplicate"
    stored_fields = next(iter(redis.streams[first.stream_key]))[1]
    assert stored_fields["queue_label"] == "registered"
    assert "user_type" not in stored_fields

    claim = await stream.claim_next(consumer_name="proc-1", queue_order=("registered",))
    assert claim is not None
    assert claim.job.job_id == "job-1"
    assert claim.job.work_kind == "task.execution.due"
    assert claim.job.identity_authority == {
        "actor_user_id": "telegram_42",
        "economics_user_id": "platform-user-1",
        "platform_roles": ["kdcube:role:super-admin"],
    }
    assert claim.job.metadata["conversation_id"] == "conv-1"
    assert claim.job.payload["execution_id"] == "exec-1"

    await stream.ack(claim)
    assert redis.acked == [(claim.stream_key, stream.group_name, claim.stream_id)]
