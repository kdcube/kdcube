import asyncio
from types import SimpleNamespace

import pytest

from kdcube_ai_app.infra.gateway import config as gateway_config


class _FakePubSub:
    def __init__(self, stop_event: asyncio.Event, messages):
        self._stop_event = stop_event
        self._messages = list(messages)
        self.subscribed = []
        self.unsubscribed = []
        self.closed = False

    async def subscribe(self, channel):
        self.subscribed.append(channel)

    async def unsubscribe(self, channel):
        self.unsubscribed.append(channel)

    async def close(self):
        self.closed = True

    async def get_message(self, ignore_subscribe_messages=True, timeout=1.0):
        if self._messages:
            message = self._messages.pop(0)
            if not self._messages:
                self._stop_event.set()
            return message
        self._stop_event.set()
        return None


class _FakeRedis:
    def __init__(self, pubsub):
        self._pubsub = pubsub

    def pubsub(self):
        return self._pubsub


@pytest.mark.asyncio
async def test_subscribe_gateway_config_updates_uses_shared_async_client_and_bytes_payload(monkeypatch):
    stop_event = asyncio.Event()
    pubsub = _FakePubSub(
        stop_event,
        messages=[
            {
                "type": "message",
                "data": b'{"config":{"profile":"production","tenant":"tenant-a","project":"project-a"}}',
            }
        ],
    )
    redis_client = _FakeRedis(pubsub)
    get_client_calls = []
    applied = {}

    def _fake_get_async_redis_client(redis_url, decode_responses=False, max_connections=None):
        get_client_calls.append(
            {
                "redis_url": redis_url,
                "decode_responses": decode_responses,
                "max_connections": max_connections,
            }
        )
        return redis_client

    def _fake_config_from_dict(data, component_override=None):
        return {
            "data": data,
            "component_override": component_override,
        }

    def _fake_apply_gateway_config_snapshot(gateway, new_config):
        applied["gateway"] = gateway
        applied["config"] = new_config
        gateway.gateway_config = new_config

    def _fake_set_gateway_config(new_config):
        applied["set_config"] = new_config

    monkeypatch.setattr(gateway_config, "get_async_redis_client", _fake_get_async_redis_client)
    monkeypatch.setattr(gateway_config, "_config_from_dict", _fake_config_from_dict)
    monkeypatch.setattr(gateway_config, "apply_gateway_config_snapshot", _fake_apply_gateway_config_snapshot)
    monkeypatch.setattr(gateway_config, "set_gateway_config", _fake_set_gateway_config)

    policy = SimpleNamespace(
        set_guarded_patterns=lambda *_args, **_kwargs: None,
        set_bypass_throttling_patterns=lambda *_args, **_kwargs: None,
    )
    gateway = SimpleNamespace(gateway_config=SimpleNamespace(guarded_rest_patterns=[], bypass_throttling_patterns=[]))
    gateway_adapter = SimpleNamespace(gateway=gateway, policy=policy)

    await gateway_config.subscribe_gateway_config_updates(
        gateway_adapter=gateway_adapter,
        tenant="tenant-a",
        project="project-a",
        redis_url="redis://example",
        stop_event=stop_event,
    )

    assert pubsub.subscribed
    assert pubsub.unsubscribed
    assert pubsub.closed is True
    assert applied["config"]["data"] == {
        "profile": "production",
        "tenant": "tenant-a",
        "project": "project-a",
    }
    assert applied["set_config"] == applied["config"]
    assert get_client_calls
    assert all(call["decode_responses"] is False for call in get_client_calls)
