import asyncio
import contextlib
import fnmatch

import pytest

from kdcube_ai_app.apps.chat.emitters import ChatRelayCommunicator
from kdcube_ai_app.infra.orchestration.app import communicator as communicator_module
from kdcube_ai_app.infra.orchestration.app.communicator import ServiceCommunicator


_LISTENER_STOP = object()


class _FakePubSub:
    def __init__(self, owner: "_FakeRedis") -> None:
        self.owner = owner
        self.messages: asyncio.Queue[object] = asyncio.Queue()
        self.subscribed: set[str] = set()
        self.patterns: set[str] = set()
        self.closed = False

    async def subscribe(self, *channels: str) -> None:
        if self.owner.block_next_subscribe:
            self.owner.block_next_subscribe = False
            self.owner.subscribe_started.set()
            await self.owner.subscribe_release.wait()
        if self.owner.subscribe_failures:
            self.owner.subscribe_failures -= 1
            self.owner.subscribe_failed.set()
            raise ConnectionError("synthetic subscription failure")
        self.subscribed.update(channels)

    async def psubscribe(self, *channels: str) -> None:
        if self.owner.psubscribe_failures:
            self.owner.psubscribe_failures -= 1
            raise ConnectionError("synthetic pattern subscription failure")
        self.patterns.update(channels)

    async def unsubscribe(self, *channels: str) -> None:
        if self.owner.unsubscribe_failures:
            self.owner.unsubscribe_failures -= 1
            raise ConnectionError("synthetic unsubscribe failure")
        self.subscribed.difference_update(channels)

    async def punsubscribe(self, *channels: str) -> None:
        self.patterns.difference_update(channels)

    async def listen(self):
        while True:
            message = await self.messages.get()
            if message is _LISTENER_STOP:
                return
            if isinstance(message, Exception):
                raise message
            yield message

    async def emit(self, payload: dict) -> None:
        await self.messages.put({"type": "message", "data": payload})

    async def close(self) -> None:
        if self.closed:
            return
        if self.owner.block_next_close:
            self.owner.block_next_close = False
            self.owner.close_started.set()
            await self.owner.close_release.wait()
        self.closed = True
        self.owner.active_count -= 1
        await self.messages.put(_LISTENER_STOP)


class _FakeRedis:
    _kdcube_shared = True

    def __init__(self) -> None:
        self.pubsubs: list[_FakePubSub] = []
        self.active_count = 0
        self.max_active_count = 0
        self.subscribe_failures = 0
        self.psubscribe_failures = 0
        self.unsubscribe_failures = 0
        self.subscribe_failed = asyncio.Event()
        self.block_next_subscribe = False
        self.subscribe_started = asyncio.Event()
        self.subscribe_release = asyncio.Event()
        self.block_next_close = False
        self.close_started = asyncio.Event()
        self.close_release = asyncio.Event()

    def pubsub(self) -> _FakePubSub:
        pubsub = _FakePubSub(self)
        self.pubsubs.append(pubsub)
        self.active_count += 1
        self.max_active_count = max(self.max_active_count, self.active_count)
        return pubsub

    def numsub(self, channel: str) -> int:
        return sum(not pubsub.closed and channel in pubsub.subscribed for pubsub in self.pubsubs)

    async def publish(self, channel: str, payload: dict) -> int:
        """Model physical subscriptions, not the communicator's logical lists."""
        delivered = 0
        for pubsub in self.pubsubs:
            if pubsub.closed:
                continue
            if channel in pubsub.subscribed:
                await pubsub.emit(payload)
                delivered += 1
            for pattern in pubsub.patterns:
                if fnmatch.fnmatchcase(channel, pattern):
                    await pubsub.messages.put({"type": "pmessage", "data": payload})
                    delivered += 1
        return delivered


@pytest.fixture
async def relay_transport(monkeypatch):
    redis = _FakeRedis()
    monkeypatch.setattr(
        communicator_module,
        "get_async_redis_client",
        lambda _redis_url: redis,
    )
    comm = ServiceCommunicator(
        redis_url="redis://unused",
        orchestrator_identity="test.relay",
    )
    relay = ChatRelayCommunicator(comm=comm)
    try:
        yield relay, comm, redis
    finally:
        await relay.unsubscribe()


@pytest.mark.asyncio
async def test_final_session_release_stops_listener_and_closes_pubsub(relay_transport):
    relay, comm, redis = relay_transport

    await relay.acquire_session_channel("s1", tenant="t", project="p")
    pubsub = redis.pubsubs[-1]
    listener = comm._listen_task

    assert listener is not None
    assert comm.listener_alive()
    assert redis.active_count == 1

    await relay.release_session_channel("s1", tenant="t", project="p")

    assert listener.done()
    assert comm._listen_task is None
    assert comm._pubsub is None
    assert pubsub.closed
    assert redis.active_count == 0
    assert len(redis.pubsubs) == 1
    assert relay._listener_started is False


async def _cancel_listener_only(comm: ServiceCommunicator) -> None:
    """Simulate a dead listener without releasing the relay's connected refs."""
    task, comm._listen_task = comm._listen_task, None
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _wait_until(predicate) -> None:
    async with asyncio.timeout(3.0):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
@pytest.mark.parametrize("pattern", [False, True])
async def test_recreated_transport_restores_retained_subscription_on_duplicate_add(relay_transport, pattern):
    _relay, comm, redis = relay_transport
    channel = "retained.*" if pattern else "retained"
    await comm.subscribe_add(channel, pattern=pattern)
    old = comm._pubsub
    await old.close()
    comm._pubsub = None

    await comm.subscribe_add(channel, pattern=pattern)

    replacement = comm._pubsub
    assert replacement is not old
    expected = {comm._fmt_channel(channel)}
    assert (replacement.patterns if pattern else replacement.subscribed) == expected
    assert redis.active_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["subscribe", "psubscribe"])
async def test_failed_reconnect_discards_partial_pubsub_but_keeps_connected_refs(relay_transport, phase):
    relay, comm, redis = relay_transport
    await relay.acquire_session_channel("retained", tenant="t", project="p")
    await relay.acquire_session_channel("peer", tenant="t", project="p")
    await comm.subscribe_add("notifications.*", pattern=True)
    await _cancel_listener_only(comm)
    channels = set(comm._subscribed_channels)
    patterns = set(comm._subscribed_patterns)
    setattr(redis, f"{phase}_failures", 1)

    with pytest.raises(ConnectionError, match="synthetic"):
        await comm._reconnect_pubsub()

    assert redis.pubsubs[-1].closed
    assert comm._pubsub is None
    assert redis.active_count == 0
    assert set(comm._subscribed_channels) == channels
    assert set(comm._subscribed_patterns) == patterns

    await relay.acquire_session_channel("new", tenant="t", project="p")
    assert comm.listener_alive()
    assert comm._pubsub.subscribed == set(comm._subscribed_channels)
    assert comm._pubsub.patterns == patterns
    assert all(redis.numsub(channel) == 1 for channel in comm._subscribed_channels)
    assert redis.max_active_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failures", [1, 2])
async def test_listener_survives_failed_recovery_and_delivers_exact_retry_receipts(relay_transport, failures):
    relay, comm, redis = relay_transport
    received: list[dict] = []
    async def on_message(message: dict) -> None:
        received.append(message)

    await relay.acquire_session_channel("retained", tenant="t", project="p", callback=on_message)
    await relay.acquire_session_channel("peer", tenant="t", project="p")
    listener = comm._listen_task
    old = comm._pubsub
    redis.subscribe_failures = failures
    await old.messages.put(ConnectionError("synthetic listener failure"))
    await asyncio.wait_for(redis.subscribe_failed.wait(), timeout=1.0)
    await asyncio.sleep(0)
    assert comm.listener_alive()
    assert comm._listen_task is listener
    assert redis.pubsubs[-1].closed

    await _wait_until(lambda: comm._pubsub is not None and not comm._pubsub.closed)
    await relay.acquire_session_channel("new", tenant="t", project="p")
    expected = []
    for session, source in [("retained", "1-0"), ("peer", "2-0"), ("new", "3-0"), ("retained", "4-0")]:
        # The same caller message ID can name different source attempts. Match both.
        payload = {"event": "terminal", "data": {"message_id": "retry-id", "source_stream_id": source}}
        channel = comm._fmt_channel(relay._session_channel(session, tenant="t", project="p"))
        assert redis.numsub(channel) == 1
        assert await redis.publish(channel, payload) == 1
        expected.append(payload)
    await _wait_until(lambda: len(received) == len(expected))
    assert received == expected
    assert redis.max_active_count == 1


@pytest.mark.asyncio
async def test_failed_final_unsubscribe_still_releases_transport(relay_transport):
    relay, comm, redis = relay_transport
    await relay.acquire_session_channel("released", tenant="t", project="p")
    redis.unsubscribe_failures = 1

    await relay.release_session_channel("released", tenant="t", project="p")

    assert comm._subscribed_channels == []
    assert comm._pubsub is None
    assert not comm.listener_alive()
    assert redis.active_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("pattern", [False, True])
async def test_offline_release_is_not_resurrected_by_reconnect(relay_transport, pattern):
    _relay, comm, redis = relay_transport
    await comm.subscribe_add("released.*" if pattern else "released", pattern=pattern)
    await comm.subscribe_add("retained")
    await comm._pubsub.close()
    comm._pubsub = None

    await comm.unsubscribe_some("released.*" if pattern else "released")
    await comm._reconnect_pubsub()

    assert comm._subscribed_channels == [comm._fmt_channel("retained")]
    assert comm._subscribed_patterns == []
    assert comm._pubsub.subscribed == {comm._fmt_channel("retained")}
    assert comm._pubsub.patterns == set()
    assert redis.active_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["session", "project"])
async def test_reconnect_serializes_with_acquire_release_and_reconcile(relay_transport, scope):
    relay, comm, redis = relay_transport
    if scope == "session":
        await relay.acquire_session_channel("released", tenant="t", project="p")
        await relay.acquire_session_channel("retained", tenant="t", project="p")
    else:
        await relay.acquire_project_channel(tenant="t", project="released")
        await relay.acquire_project_channel(tenant="t", project="retained")
    await _cancel_listener_only(comm)
    relay._listener_started = False
    redis.block_next_subscribe = True
    reconnect = asyncio.create_task(comm._reconnect_pubsub())
    await asyncio.wait_for(redis.subscribe_started.wait(), timeout=1.0)

    if scope == "session":
        release = asyncio.create_task(relay.release_session_channel("released", tenant="t", project="p"))
        acquire = asyncio.create_task(relay.acquire_session_channel("new", tenant="t", project="p"))
        reconcile = asyncio.create_task(relay.reconcile_sessions({
            ("t", "p", "retained"): ("t", "p", 1),
            ("t", "p", "new"): ("t", "p", 1),
        }, reason="test"))
    else:
        release = asyncio.create_task(relay.release_project_channel(tenant="t", project="released"))
        acquire = asyncio.create_task(relay.acquire_project_channel(tenant="t", project="new"))
        reconcile = asyncio.create_task(relay.reconcile_project_channels({
            ("t", "retained"): 1, ("t", "new"): 1,
        }, reason="test"))
    try:
        await asyncio.sleep(0)
        assert not release.done()
        assert not acquire.done()
        assert not reconcile.done()
    finally:
        redis.subscribe_release.set()
        await asyncio.wait_for(asyncio.gather(reconnect, release, acquire, reconcile), timeout=1.0)

    assert comm._pubsub.subscribed == set(comm._subscribed_channels)
    assert len(comm._subscribed_channels) == 2
    assert all("released" not in channel for channel in comm._pubsub.subscribed)
    assert all(redis.numsub(channel) == 1 for channel in comm._subscribed_channels)
    assert comm.listener_alive()
    assert redis.max_active_count == 1


@pytest.mark.asyncio
async def test_stop_cancels_blocked_recovery_and_clears_channels_and_patterns(relay_transport):
    relay, comm, redis = relay_transport
    await relay.acquire_session_channel("retained", tenant="t", project="p")
    await comm.subscribe_add("notifications.*", pattern=True)
    redis.block_next_subscribe = True
    await comm._pubsub.messages.put(ConnectionError("synthetic listener failure"))
    await asyncio.wait_for(redis.subscribe_started.wait(), timeout=1.0)

    await asyncio.wait_for(comm.stop_listener(), timeout=1.0)

    assert not comm.listener_alive()
    assert comm._pubsub is None
    assert comm._subscribed_channels == []
    assert comm._subscribed_patterns == []
    assert redis.active_count == 0
    assert all(pubsub.closed for pubsub in redis.pubsubs)


@pytest.mark.asyncio
async def test_concurrent_acquire_waits_for_final_release_stop_then_reopens(relay_transport):
    relay, comm, redis = relay_transport
    await relay.acquire_session_channel("old", tenant="t", project="p")
    old = comm._pubsub
    redis.block_next_close = True
    release = asyncio.create_task(relay.release_session_channel("old", tenant="t", project="p"))
    await asyncio.wait_for(redis.close_started.wait(), timeout=1.0)
    acquire = asyncio.create_task(relay.acquire_session_channel("new", tenant="t", project="p"))
    try:
        await asyncio.sleep(0)
        assert not acquire.done()
    finally:
        redis.close_release.set()
        await asyncio.wait_for(asyncio.gather(release, acquire), timeout=1.0)

    new_channel = comm._fmt_channel(relay._session_channel("new", tenant="t", project="p"))
    assert old.closed
    assert comm._pubsub is not old
    assert comm.listener_alive()
    assert comm._subscribed_channels == [new_channel]
    assert redis.numsub(new_channel) == 1
    assert redis.max_active_count == 1


@pytest.mark.asyncio
async def test_repeated_acquire_release_cycles_do_not_accumulate_pubsubs(relay_transport):
    relay, _comm, redis = relay_transport

    for index in range(5):
        session_id = f"s{index}"
        await relay.acquire_session_channel(session_id, tenant="t", project="p")
        assert redis.active_count == 1

        await relay.release_session_channel(session_id, tenant="t", project="p")
        assert redis.active_count == 0

    assert len(redis.pubsubs) == 5
    assert all(pubsub.closed for pubsub in redis.pubsubs)
    assert redis.max_active_count == 1


@pytest.mark.asyncio
async def test_acquire_after_teardown_restarts_listener_and_delivers(relay_transport):
    relay, comm, redis = relay_transport

    await relay.acquire_session_channel("s1", tenant="t", project="p")
    first_pubsub = redis.pubsubs[-1]
    await relay.release_session_channel("s1", tenant="t", project="p")

    received: list[dict] = []
    delivered = asyncio.Event()

    async def on_message(message: dict) -> None:
        received.append(message)
        delivered.set()

    await relay.acquire_session_channel(
        "s2",
        tenant="t",
        project="p",
        callback=on_message,
    )
    second_pubsub = redis.pubsubs[-1]

    assert second_pubsub is not first_pubsub
    assert not second_pubsub.closed
    assert comm.listener_alive()

    payload = {"event": "chat_step", "data": {"text": "after restart"}}
    await second_pubsub.emit(payload)
    await asyncio.wait_for(delivered.wait(), timeout=1.0)

    assert received == [payload]

    await relay.release_session_channel("s2", tenant="t", project="p")
    assert second_pubsub.closed
    assert redis.active_count == 0


@pytest.mark.asyncio
async def test_transport_stays_open_until_session_and_project_refs_are_released(relay_transport):
    relay, comm, redis = relay_transport

    await relay.acquire_session_channel("s1", tenant="t", project="p")
    await relay.acquire_session_channel("s2", tenant="t", project="p")
    await relay.acquire_project_channel(tenant="t", project="p")
    pubsub = redis.pubsubs[-1]

    await relay.release_session_channel("s1", tenant="t", project="p")
    assert not pubsub.closed
    assert comm.listener_alive()

    await relay.release_session_channel("s2", tenant="t", project="p")
    assert not pubsub.closed
    assert comm.listener_alive()

    await relay.release_project_channel(tenant="t", project="p")
    assert pubsub.closed
    assert redis.active_count == 0


@pytest.mark.asyncio
async def test_legacy_subscription_keeps_transport_open(relay_transport):
    relay, comm, redis = relay_transport

    async def on_message(_message: dict) -> None:
        return None

    await relay.subscribe(on_message)
    await relay.acquire_session_channel("s1", tenant="t", project="p")
    pubsub = redis.pubsubs[-1]

    await relay.release_session_channel("s1", tenant="t", project="p")

    assert not pubsub.closed
    assert comm.listener_alive()
    assert comm._subscribed_channels == ["test.relay.chat.events"]

    await relay.unsubscribe()
    assert pubsub.closed
    assert redis.active_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["session", "project"])
async def test_reconcile_to_empty_releases_transport(relay_transport, scope):
    relay, comm, redis = relay_transport

    if scope == "session":
        await relay.acquire_session_channel("s1", tenant="t", project="p")
    else:
        await relay.acquire_project_channel(tenant="t", project="p")
    pubsub = redis.pubsubs[-1]

    if scope == "session":
        await relay.reconcile_sessions({}, reason="test")
    else:
        await relay.reconcile_project_channels({}, reason="test")

    assert pubsub.closed
    assert comm._pubsub is None
    assert not comm.listener_alive()
    assert redis.active_count == 0
    assert len(redis.pubsubs) == 1
