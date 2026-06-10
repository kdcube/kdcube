import pytest

from kdcube_ai_app.infra.redis import factory as redis_factory


class _FakeAsyncRedis:
    def __init__(self, url, **kwargs):
        self.url = url
        self.kwargs = kwargs
        self.closed = False

    async def close(self):
        self.closed = True


class _FakeSyncRedis:
    def __init__(self, url, **kwargs):
        self.url = url
        self.kwargs = kwargs
        self.closed = False

    def close(self):
        self.closed = True


def test_factory_creates_shared_standalone_async_client(monkeypatch):
    calls = []

    def _fake_from_url(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeAsyncRedis(url, **kwargs)

    monkeypatch.setattr(redis_factory.aioredis, "from_url", _fake_from_url)
    factory = redis_factory.RedisClientFactory(default_topology="standalone")

    request = factory.request(
        "redis://localhost:6379/0",
        runtime="async",
        decode_responses=True,
        max_connections=25,
        client_name_kind="gateway",
    )

    first = factory.client(request)
    second = factory.client(request)

    assert first is second
    assert len(calls) == 1
    assert first.url == "redis://localhost:6379/0"
    assert first.kwargs["decode_responses"] is True
    assert first.kwargs["max_connections"] == 25
    assert first.kwargs["socket_connect_timeout"] == redis_factory.DEFAULT_REDIS_CONNECT_TIMEOUT_SEC
    assert first.kwargs["health_check_interval"] == redis_factory.DEFAULT_REDIS_HEALTH_CHECK_INTERVAL_SEC
    assert first.kwargs["socket_keepalive"] is True
    assert first.kwargs["retry_on_timeout"] is True
    assert first.kwargs["client_name"].endswith(":gateway")
    assert getattr(first, "_kdcube_shared") is True


def test_factory_creates_dedicated_clients_without_cache(monkeypatch):
    calls = []

    def _fake_from_url(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeAsyncRedis(url, **kwargs)

    monkeypatch.setattr(redis_factory.aioredis, "from_url", _fake_from_url)
    factory = redis_factory.RedisClientFactory(default_topology="standalone")
    request = factory.request("redis://localhost:6379/0", runtime="async", shared=False)

    first = factory.client(request)
    second = factory.client(request)

    assert first is not second
    assert len(calls) == 2
    assert getattr(first, "_kdcube_shared") is False
    assert getattr(second, "_kdcube_shared") is False


def test_factory_creates_shared_sync_client(monkeypatch):
    calls = []

    def _fake_from_url(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeSyncRedis(url, **kwargs)

    monkeypatch.setattr(redis_factory.redis.Redis, "from_url", _fake_from_url)
    factory = redis_factory.RedisClientFactory(default_topology="standalone")
    request = factory.request("redis://localhost:6379/0", runtime="sync", decode_responses=True)

    first = factory.client(request)
    second = factory.client(request)

    assert first is second
    assert len(calls) == 1
    assert first.kwargs["decode_responses"] is True
    assert first.kwargs["client_name"].endswith(":sync_decode")
    assert getattr(first, "_kdcube_shared") is True


def test_factory_uses_max_connections_resolver(monkeypatch):
    calls = []

    def _fake_from_url(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeAsyncRedis(url, **kwargs)

    monkeypatch.setattr(redis_factory.aioredis, "from_url", _fake_from_url)
    factory = redis_factory.RedisClientFactory(
        default_topology="standalone",
        max_connections_resolver=lambda value: 17 if value is None else value,
    )

    request = factory.request("redis://localhost:6379/0", runtime="async")
    client = factory.client(request)

    assert client.kwargs["max_connections"] == 17


def test_factory_rejects_cluster_until_cluster_impl_is_integrated(monkeypatch):
    def _fake_from_url(_url, **_kwargs):
        raise AssertionError("standalone client constructor must not be called for cluster mode")

    monkeypatch.setattr(redis_factory.aioredis, "from_url", _fake_from_url)
    factory = redis_factory.RedisClientFactory(default_topology="cluster")
    request = factory.request("redis://localhost:6379/0", runtime="async")

    with pytest.raises(redis_factory.RedisTopologyNotImplementedError):
        factory.client(request)


def test_topology_normalization_accepts_only_canonical_names():
    assert redis_factory.normalize_redis_topology("standalone") == redis_factory.RedisTopology.STANDALONE
    assert redis_factory.normalize_redis_topology("cluster") == redis_factory.RedisTopology.CLUSTER

    with pytest.raises(redis_factory.RedisFactoryError):
        redis_factory.normalize_redis_topology("invalid")


@pytest.mark.asyncio
async def test_factory_close_closes_cached_clients(monkeypatch):
    async_clients = []
    sync_clients = []

    def _fake_async_from_url(url, **kwargs):
        client = _FakeAsyncRedis(url, **kwargs)
        async_clients.append(client)
        return client

    def _fake_sync_from_url(url, **kwargs):
        client = _FakeSyncRedis(url, **kwargs)
        sync_clients.append(client)
        return client

    monkeypatch.setattr(redis_factory.aioredis, "from_url", _fake_async_from_url)
    monkeypatch.setattr(redis_factory.redis.Redis, "from_url", _fake_sync_from_url)
    factory = redis_factory.RedisClientFactory(default_topology="standalone")

    factory.client(factory.request("redis://localhost:6379/0", runtime="async"))
    factory.client(factory.request("redis://localhost:6379/0", runtime="sync"))

    await factory.close()

    assert [client.closed for client in async_clients] == [True]
    assert [client.closed for client in sync_clients] == [True]
