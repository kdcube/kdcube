from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk import config as sdk_config
from kdcube_ai_app.infra.redis import client as redis_client
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


@pytest.mark.asyncio
async def test_existing_async_helper_uses_factory_shared_cache(monkeypatch):
    await redis_client.close_all_redis_clients()
    calls = []

    def _fake_from_url(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeAsyncRedis(url, **kwargs)

    monkeypatch.setattr(redis_factory.aioredis, "from_url", _fake_from_url)

    first = redis_client.get_async_redis_client(
        "redis://localhost:6379/0",
        decode_responses=True,
        max_connections=11,
    )
    second = redis_client.get_async_redis_client(
        "redis://localhost:6379/0",
        decode_responses=True,
        max_connections=11,
    )

    assert first is second
    assert len(calls) == 1
    assert first.kwargs["decode_responses"] is True
    assert first.kwargs["max_connections"] == 11
    assert getattr(first, "_kdcube_shared") is True

    await redis_client.close_all_redis_clients()


@pytest.mark.asyncio
async def test_existing_helper_reads_topology_from_settings(monkeypatch):
    await redis_client.close_all_redis_clients()
    monkeypatch.setattr(sdk_config, "get_settings", lambda: SimpleNamespace(REDIS_TOPOLOGY="cluster"))

    with pytest.raises(redis_factory.RedisTopologyNotImplementedError):
        redis_client.get_async_redis_client("redis://localhost:6379/0")

    await redis_client.close_all_redis_clients()


@pytest.mark.asyncio
async def test_existing_create_async_helper_uses_factory_dedicated_clients(monkeypatch):
    await redis_client.close_all_redis_clients()
    calls = []

    def _fake_from_url(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeAsyncRedis(url, **kwargs)

    monkeypatch.setattr(redis_factory.aioredis, "from_url", _fake_from_url)

    first = redis_client.create_async_redis_client("redis://localhost:6379/0", client_name_kind="worker")
    second = redis_client.create_async_redis_client("redis://localhost:6379/0", client_name_kind="worker")

    assert first is not second
    assert len(calls) == 2
    assert first.kwargs["client_name"].endswith(":worker")
    assert getattr(first, "_kdcube_shared") is False
    assert getattr(second, "_kdcube_shared") is False

    await redis_client.close_all_redis_clients()


@pytest.mark.asyncio
async def test_existing_close_all_closes_factory_cached_clients(monkeypatch):
    await redis_client.close_all_redis_clients()
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

    redis_client.get_async_redis_client("redis://localhost:6379/0")
    redis_client.get_sync_redis_client("redis://localhost:6379/0")

    await redis_client.close_all_redis_clients()

    assert [client.closed for client in async_clients] == [True]
    assert [client.closed for client in sync_clients] == [True]
