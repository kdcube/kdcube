"""DataBusLiveSessionRegistry.sessions_many equals sessions() per principal, with fewer round trips.

Against a disposable Redis (W812_LEASE_TEST_REDIS_PORT, db 15): the same
results and the same index cleanup as the serial path for multiple and
duplicate principals, several sockets, missing, rebound and expired records,
and live authority True, False and None; a wrong-type socket key raises as GET
does; work stays bounded; failures surface as one unwrapped exception; a
cancelled call leaves no task behind.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from redis.exceptions import ResponseError

import kdcube_ai_app.apps.chat.sdk.runtime.data_bus.live_sessions as live_sessions
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.live_sessions import (
    DataBusLiveSessionPublisher,
    DataBusLiveSessionRegistry,
)

PROJECT = "project"
BUNDLE = "bundle@1-0"


@pytest_asyncio.fixture
async def redis_db():
    port = os.getenv("W812_LEASE_TEST_REDIS_PORT")
    if not port:
        pytest.skip("Owned disposable Redis port W812_LEASE_TEST_REDIS_PORT is required")
    redis = Redis(host="127.0.0.1", port=int(port), db=15, decode_responses=False)
    tags: list[str] = []
    try:
        await redis.ping()
        yield redis, tags
    finally:
        for tag in tags:
            async for key in redis.scan_iter(match=f"*{tag}*"):
                await redis.delete(key)
        await redis.aclose()


def _tenant(tags: list[str]) -> str:
    tag = uuid.uuid4().hex
    tags.append(tag)
    return f"t-{tag}"


async def _layout(registry: DataBusLiveSessionRegistry, tenant: str, tag: str) -> dict[str, str]:
    """Principals a..e with: two live sockets, one live, a missing record, a rebound record, an expired lease."""

    future = int(time.time()) + 20000
    sockets: dict[str, str] = {}

    async def add(principal: str, name: str) -> str:
        socket_id = f"{tag}-{name}"
        await registry.register(tenant=tenant, project=PROJECT, bundle_id=BUNDLE, principal=principal,
                                session_id=f"s-{name}", socket_id=socket_id, expires_at=future)
        sockets[name] = socket_id
        return socket_id

    await add("a", "a1")
    await add("a", "a2")
    await add("b", "b1")
    missing = await add("c", "c1")
    await registry.redis.delete(registry._socket_key(missing))
    rebound = await add("d", "d1")
    record = json.loads(await registry.redis.get(registry._socket_key(rebound)))
    record["principal"] = "someone-else"
    await registry.redis.set(registry._socket_key(rebound), json.dumps(record))
    expired = await add("e", "e1")
    record = json.loads(await registry.redis.get(registry._socket_key(expired)))
    record["lease_expires_at"] = int(time.time()) - 5
    await registry.redis.set(registry._socket_key(expired), json.dumps(record))
    legacy = await add("f", "f1")
    record = json.loads(await registry.redis.get(registry._socket_key(legacy)))
    record.pop("schema", None)
    await registry.redis.set(registry._socket_key(legacy), json.dumps(record))
    beyond = await add("g", "g1")
    record = json.loads(await registry.redis.get(registry._socket_key(beyond)))
    record["lease_expires_at"] = record["expires_at"] + 10
    await registry.redis.set(registry._socket_key(beyond), json.dumps(record))
    malformed = await add("h", "h1")
    await registry.redis.set(registry._socket_key(malformed), "{not json")
    return sockets


async def _index_state(registry, tenant):
    state = {}
    for principal in "abcdefgh":
        key = registry._index_key(tenant=tenant, project=PROJECT, bundle_id=BUNDLE, principal=principal)
        state[principal] = sorted(json.loads(m)["session_id"] for m in await registry.redis.zrange(key, 0, -1))
    return state


@pytest.mark.asyncio
async def test_batch_equals_serial_results_and_cleanup(redis_db, monkeypatch):
    redis, tags = redis_db
    registry = DataBusLiveSessionRegistry(redis)
    serial_tenant, batch_tenant = _tenant(tags), _tenant(tags)
    serial_sockets = await _layout(registry, serial_tenant, tags[0])
    batch_sockets = await _layout(registry, batch_tenant, tags[1])
    verdict = {serial_sockets["a2"]: False, serial_sockets["b1"]: None,
               batch_sockets["a2"]: False, batch_sockets["b1"]: None}

    async def live(self, *, socket_id, raw_record=None):
        return verdict.get(socket_id, True)

    monkeypatch.setattr(DataBusLiveSessionRegistry, "_live_authority", live)
    now = int(time.time())
    principals = ["a", "b", "c", "d", "e", "f", "g", "h", "a", "nobody"]
    serial = {p: await registry.sessions(tenant=serial_tenant, project=PROJECT, bundle_id=BUNDLE,
                                         principal=p, now=now) for p in dict.fromkeys(principals)}
    batch = await registry.sessions_many(tenant=batch_tenant, project=PROJECT, bundle_id=BUNDLE,
                                         principals=principals, now=now, concurrency=2, chunk=2)
    assert batch == serial == {"a": ("s-a1",), "b": (), "c": (), "d": (), "e": (), "f": (), "g": (),
                               "h": (), "nobody": ()}
    # The same cleanup: revoked a2, missing c1 and expired e1 are pruned; the
    # unavailable b1 keeps its route; the rebound d1 member is pruned from d's index.
    assert await _index_state(registry, batch_tenant) == await _index_state(registry, serial_tenant)
    assert (await _index_state(registry, batch_tenant))["b"] == ["s-b1"]


@pytest.mark.asyncio
async def test_a_wrong_type_socket_key_raises_like_get_and_prunes_nothing(redis_db, monkeypatch):
    redis, tags = redis_db
    registry = DataBusLiveSessionRegistry(redis)
    tenant = _tenant(tags)
    socket_id = f"{tags[0]}-w1"
    await registry.register(tenant=tenant, project=PROJECT, bundle_id=BUNDLE, principal="w",
                            session_id="s-w1", socket_id=socket_id, expires_at=int(time.time()) + 20000)
    await redis.delete(registry._socket_key(socket_id))
    await redis.rpush(registry._socket_key(socket_id), "not-a-string")
    with pytest.raises(ResponseError) as serial_error:
        await registry.sessions(tenant=tenant, project=PROJECT, bundle_id=BUNDLE, principal="w")
    with pytest.raises(ResponseError) as batch_error:
        await registry.sessions_many(tenant=tenant, project=PROJECT, bundle_id=BUNDLE, principals=["w"])
    assert type(batch_error.value) is type(serial_error.value)
    index = registry._index_key(tenant=tenant, project=PROJECT, bundle_id=BUNDLE, principal="w")
    assert await redis.zcard(index) == 1, "a backend error is not a missing record"


@pytest.mark.asyncio
async def test_authority_work_is_bounded_and_complete(redis_db, monkeypatch):
    redis, tags = redis_db
    registry = DataBusLiveSessionRegistry(redis)
    tenant = _tenant(tags)
    future = int(time.time()) + 20000
    principals = [f"p{n}" for n in range(9)]
    for n, principal in enumerate(principals):
        await registry.register(tenant=tenant, project=PROJECT, bundle_id=BUNDLE, principal=principal,
                                session_id=f"s{n}", socket_id=f"{tags[0]}-{n}", expires_at=future)
    running, peak = 0, 0

    async def live(self, *, socket_id, raw_record=None):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return True

    monkeypatch.setattr(DataBusLiveSessionRegistry, "_live_authority", live)
    result = await registry.sessions_many(tenant=tenant, project=PROJECT, bundle_id=BUNDLE,
                                          principals=principals, concurrency=3, chunk=4)
    assert result == {principal: (f"s{n}",) for n, principal in enumerate(principals)}
    assert peak <= 3


@pytest.mark.asyncio
async def test_the_earliest_authority_failure_is_raised_unwrapped(redis_db, monkeypatch):
    redis, tags = redis_db
    registry = DataBusLiveSessionRegistry(redis)
    tenant = _tenant(tags)
    future = int(time.time()) + 20000
    for n in range(4):
        await registry.register(tenant=tenant, project=PROJECT, bundle_id=BUNDLE, principal=f"p{n}",
                                session_id=f"s{n}", socket_id=f"{tags[0]}-{n}", expires_at=future)

    async def live(self, *, socket_id, raw_record=None):
        number = int(socket_id.rsplit("-", 1)[1])
        await asyncio.sleep(0.02 * (4 - number))  # the later position fails first
        if number in (1, 3):
            raise RuntimeError(f"cleanup failed at {number}")
        return True

    monkeypatch.setattr(DataBusLiveSessionRegistry, "_live_authority", live)
    with pytest.raises(RuntimeError, match="at 1$") as error:
        await registry.sessions_many(tenant=tenant, project=PROJECT, bundle_id=BUNDLE,
                                     principals=[f"p{n}" for n in range(4)], concurrency=4)
    assert not isinstance(error.value, BaseExceptionGroup)


@pytest.mark.asyncio
async def test_a_cancelled_call_leaves_no_task_behind(redis_db, monkeypatch):
    redis, tags = redis_db
    registry = DataBusLiveSessionRegistry(redis)
    tenant = _tenant(tags)
    await registry.register(tenant=tenant, project=PROJECT, bundle_id=BUNDLE, principal="p",
                            session_id="s", socket_id=f"{tags[0]}-c", expires_at=int(time.time()) + 20000)
    started = asyncio.Event()

    async def live(self, *, socket_id, raw_record=None):
        started.set()
        await asyncio.sleep(30)
        return True

    monkeypatch.setattr(DataBusLiveSessionRegistry, "_live_authority", live)
    before = asyncio.all_tasks()
    call = asyncio.create_task(registry.sessions_many(tenant=tenant, project=PROJECT, bundle_id=BUNDLE,
                                                      principals=["p"]))
    await started.wait()
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    await asyncio.sleep(0)
    assert asyncio.all_tasks() - before - {asyncio.current_task()} == set()


class _CountingRedis:
    def __init__(self):
        self.calls = 0

    def pipeline(self, **_kwargs):
        self.calls += 1
        raise AssertionError("no Redis command for an empty input")


@pytest.mark.asyncio
async def test_empty_input_sends_nothing_and_bounds_are_validated():
    redis = _CountingRedis()
    registry = DataBusLiveSessionRegistry(redis)
    assert await registry.sessions_many(tenant="t", project=PROJECT, bundle_id=BUNDLE, principals=[]) == {}
    assert redis.calls == 0
    for bad in ({"concurrency": 0}, {"chunk": 0}, {"concurrency": 1.5}):
        with pytest.raises(ValueError):
            await registry.sessions_many(tenant="t", project=PROJECT, bundle_id=BUNDLE, principals=["p"], **bad)


@pytest.mark.asyncio
async def test_a_client_without_pipelines_takes_the_serial_path(monkeypatch):
    class _NoPipeline:
        pass

    registry = DataBusLiveSessionRegistry(_NoPipeline())
    calls = []

    async def sessions(self, **scope):
        calls.append(scope["principal"])
        return ("s",) if scope["principal"] == "a" else ()

    monkeypatch.setattr(DataBusLiveSessionRegistry, "sessions", sessions)
    assert await registry.sessions_many(tenant="t", project=PROJECT, bundle_id=BUNDLE,
                                        principals=["a", "b", "a"]) == {"a": ("s",), "b": ()}
    assert calls == ["a", "b"]


async def _three_routes(registry, tenant, tag):
    future = int(time.time()) + 20000
    for n in range(3):
        await registry.register(tenant=tenant, project=PROJECT, bundle_id=BUNDLE, principal=f"p{n}",
                                session_id=f"s{n}", socket_id=f"{tag}-{n}", expires_at=future)
    return [f"p{n}" for n in range(3)]


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["authority", "cleanup"])
async def test_an_independently_cancelled_worker_is_never_a_partial_result(redis_db, monkeypatch, where):
    redis, tags = redis_db
    registry = DataBusLiveSessionRegistry(redis)
    tenant = _tenant(tags)
    principals = await _three_routes(registry, tenant, tags[0])
    seen = []

    async def live(self, *, socket_id, raw_record=None):
        seen.append(socket_id)
        if socket_id.endswith("-0"):
            if where == "authority":
                raise asyncio.CancelledError()  # the worker's own await, not the caller's
            return False
        return True

    async def release(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(DataBusLiveSessionRegistry, "_live_authority", live)
    if where == "cleanup":
        monkeypatch.setattr(live_sessions, "release_lease", release)
    for call in ("sessions_many", "connected_many"):
        if call == "sessions_many":
            run = registry.sessions_many(tenant=tenant, project=PROJECT, bundle_id=BUNDLE,
                                         principals=principals, concurrency=1)
        else:
            publisher = DataBusLiveSessionPublisher(redis=redis, tenant=tenant, project=PROJECT,
                                                    bundle_id=BUNDLE, relay=object())
            run = publisher.connected_many(principals)
        task = asyncio.create_task(run)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not task.cancelling(), "the caller was never cancelled"


@pytest.mark.asyncio
async def test_a_real_cleanup_helper_failure_is_raised_unwrapped(redis_db, monkeypatch):
    redis, tags = redis_db
    registry = DataBusLiveSessionRegistry(redis)
    tenant = _tenant(tags)
    principals = await _three_routes(registry, tenant, tags[0])

    async def live(self, *, socket_id, raw_record=None):
        return not socket_id.endswith("-1")

    real_release = live_sessions.release_lease

    async def release(redis_client, **kwargs):
        if kwargs["socket_key"].endswith("-1"):
            raise ConnectionError("cleanup write failed")
        return await real_release(redis_client, **kwargs)

    monkeypatch.setattr(DataBusLiveSessionRegistry, "_live_authority", live)
    monkeypatch.setattr(live_sessions, "release_lease", release)
    with pytest.raises(ConnectionError, match="cleanup write failed") as error:
        await registry.sessions_many(tenant=tenant, project=PROJECT, bundle_id=BUNDLE, principals=principals)
    assert not isinstance(error.value, BaseExceptionGroup)


@pytest.mark.asyncio
async def test_a_renewal_during_cleanup_survives_as_in_the_serial_path(redis_db, monkeypatch):
    redis, tags = redis_db
    registry = DataBusLiveSessionRegistry(redis)
    states = {}
    for path in ("serial", "batch"):
        tenant = _tenant(tags)
        socket_id = f"{tags[-1]}-r"
        future = int(time.time()) + 20000
        await registry.register(tenant=tenant, project=PROJECT, bundle_id=BUNDLE, principal="r",
                                session_id="s-r", socket_id=socket_id, expires_at=future)

        async def live(self, *, socket_id, raw_record=None, _tenant=tenant, _future=future):
            # A reconnect re-registers the same socket between the read and the cleanup.
            await registry.register(tenant=_tenant, project=PROJECT, bundle_id=BUNDLE, principal="r",
                                    session_id="s-r", socket_id=socket_id, expires_at=_future)
            return False

        monkeypatch.setattr(DataBusLiveSessionRegistry, "_live_authority", live)
        if path == "serial":
            await registry.sessions(tenant=tenant, project=PROJECT, bundle_id=BUNDLE, principal="r")
        else:
            await registry.sessions_many(tenant=tenant, project=PROJECT, bundle_id=BUNDLE, principals=["r"])
        index = registry._index_key(tenant=tenant, project=PROJECT, bundle_id=BUNDLE, principal="r")
        states[path] = (await redis.zcard(index), await redis.exists(registry._socket_key(socket_id)))
    assert states["batch"] == states["serial"] == (1, 1), "exact-value cleanup keeps the renewed route"


@pytest.mark.asyncio
async def test_record_chunks_are_processed_one_at_a_time(redis_db, monkeypatch):
    redis, tags = redis_db
    registry = DataBusLiveSessionRegistry(redis)
    tenant = _tenant(tags)
    future = int(time.time()) + 20000
    principals = [f"p{n}" for n in range(5)]
    for n, principal in enumerate(principals):
        await registry.register(tenant=tenant, project=PROJECT, bundle_id=BUNDLE, principal=principal,
                                session_id=f"s{n}", socket_id=f"{tags[0]}-{n}", expires_at=future)
    events = []
    original = DataBusLiveSessionRegistry._process_record_chunk

    async def chunk(self, part, found, **kwargs):
        events.append(("enter", len(part)))
        await original(self, part, found, **kwargs)
        events.append(("exit", len(part)))

    async def live(self, *, socket_id, raw_record=None):
        return True

    monkeypatch.setattr(DataBusLiveSessionRegistry, "_process_record_chunk", chunk)
    monkeypatch.setattr(DataBusLiveSessionRegistry, "_live_authority", live)
    result = await registry.sessions_many(tenant=tenant, project=PROJECT, bundle_id=BUNDLE,
                                          principals=principals, chunk=2)
    assert result == {principal: (f"s{n}",) for n, principal in enumerate(principals)}
    assert events == [("enter", 2), ("exit", 2), ("enter", 2), ("exit", 2), ("enter", 1), ("exit", 1)]


@pytest.mark.asyncio
async def test_a_repeated_caller_cancel_still_drains_every_worker(redis_db, monkeypatch):
    redis, tags = redis_db
    registry = DataBusLiveSessionRegistry(redis)
    tenant = _tenant(tags)
    principals = await _three_routes(registry, tenant, tags[0])
    started = asyncio.Event()
    finished = []

    async def live(self, *, socket_id, raw_record=None):
        started.set()
        try:
            await asyncio.sleep(30)
        finally:
            await asyncio.sleep(0.05)  # cleanup still running when the second cancel arrives
            finished.append(socket_id)
        return True

    monkeypatch.setattr(DataBusLiveSessionRegistry, "_live_authority", live)
    before = asyncio.all_tasks()
    task = asyncio.create_task(registry.sessions_many(tenant=tenant, project=PROJECT, bundle_id=BUNDLE,
                                                      principals=principals, concurrency=3))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sorted(finished) == sorted(f"{tags[0]}-{n}" for n in range(3)), "every worker's cleanup finished"
    await asyncio.sleep(0)
    assert asyncio.all_tasks() - before - {asyncio.current_task()} == set()


@pytest.mark.asyncio
async def test_the_serial_fallback_uses_one_now(monkeypatch):
    class _NoPipeline:
        pass

    registry = DataBusLiveSessionRegistry(_NoPipeline())
    clock = iter(range(1000, 2000, 7))
    monkeypatch.setattr(live_sessions.time, "time", lambda: next(clock))
    seen = []

    async def sessions(self, **scope):
        seen.append(scope["now"])
        return ()

    monkeypatch.setattr(DataBusLiveSessionRegistry, "sessions", sessions)
    await registry.sessions_many(tenant="t", project=PROJECT, bundle_id=BUNDLE, principals=["a", "b", "c"])
    assert len(set(seen)) == 1 and seen[0] is not None
