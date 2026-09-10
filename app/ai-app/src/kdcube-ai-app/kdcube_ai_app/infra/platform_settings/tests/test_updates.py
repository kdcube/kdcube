# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import asyncio
import json

import pytest

from kdcube_ai_app.infra.platform_settings.updates import (
    PlatformSettingsUpdate,
    PlatformSettingsUpdateListener,
    platform_settings_update_channel,
    publish_platform_settings_update,
)


class FakePubSub:
    def __init__(self, messages):
        self.messages = list(messages)
        self.subscribed = []
        self.unsubscribed = []
        self.closed = False

    async def subscribe(self, channel):
        self.subscribed.append(channel)

    async def unsubscribe(self, channel):
        self.unsubscribed.append(channel)

    async def get_message(self, **_kwargs):
        if self.messages:
            return self.messages.pop(0)
        await asyncio.sleep(0)
        return None

    async def aclose(self):
        self.closed = True


class FakeRedis:
    def __init__(self, messages=()):
        self.messages = list(messages)
        self.published = []
        self.pubsub_instance = None

    async def publish(self, channel, data):
        self.published.append((channel, data))
        return 1

    def pubsub(self):
        self.pubsub_instance = FakePubSub(self.messages)
        return self.pubsub_instance


@pytest.mark.asyncio
async def test_publish_contains_routing_metadata_and_no_setting_values():
    redis = FakeRedis()
    event = await publish_platform_settings_update(
        redis,
        tenant="tenant-a",
        project="project-a",
        section="auth",
        scope="providers",
        changed=["provider.trusted_providers", "provider.trusted_providers"],
        reason="admin edit",
        actor="user-1",
    )

    channel, raw = redis.published[0]
    payload = json.loads(raw)
    assert channel == "kdcube:config:platform-settings:update:tenant-a:project-a"
    assert payload["section"] == "auth" and payload["scope"] == "providers"
    assert payload["changed"] == ["provider.trusted_providers"]
    assert "value" not in payload and "settings" not in payload
    assert event.subscriber_count == 1
    assert PlatformSettingsUpdate.from_message(raw).to_dict() == event.to_dict()


@pytest.mark.asyncio
async def test_listener_catches_up_then_dispatches_only_its_runtime():
    stop = asyncio.Event()
    calls = []

    async def on_auth(event):
        calls.append((event.scope, event.reason))
        if event.scope == "providers":
            stop.set()

    event = PlatformSettingsUpdate.create(
        tenant="tenant-a",
        project="project-a",
        section="auth",
        scope="providers",
        changed=["provider"],
    )
    redis = FakeRedis([{"type": "message", "data": event.to_json()}])
    listener = PlatformSettingsUpdateListener(
        redis,
        tenant="tenant-a",
        project="project-a",
        handlers={"auth": on_auth},
        stop_event=stop,
        poll_seconds=0.01,
    )

    await asyncio.wait_for(listener.run(), timeout=1)

    assert calls == [
        ("catch_up", "platform-settings-listener.subscribe"),
        ("providers", ""),
    ]
    assert redis.pubsub_instance.subscribed == [
        platform_settings_update_channel(tenant="tenant-a", project="project-a")
    ]
    assert redis.pubsub_instance.closed is True


@pytest.mark.asyncio
async def test_listener_ignores_event_for_another_runtime():
    called = []
    listener = PlatformSettingsUpdateListener(
        FakeRedis(),
        tenant="tenant-a",
        project="project-a",
        handlers={"auth": called.append},
    )
    event = PlatformSettingsUpdate.create(
        tenant="tenant-b",
        project="project-a",
        section="auth",
        scope="providers",
    )

    assert await listener.dispatch(event) is False
    assert called == []
