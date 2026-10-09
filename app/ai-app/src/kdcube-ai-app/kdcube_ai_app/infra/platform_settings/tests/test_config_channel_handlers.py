# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Operator, 2026-10-09: every process that caches secrets hears bundles.secrets.update. Ingress listens through
its existing PlatformSettingsUpdateListener (one connection, one loop), not a new subscriber."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from kdcube_ai_app.apps.chat.sdk import config_cache
from kdcube_ai_app.infra.platform_settings.updates import (
    PlatformSettingsUpdate, PlatformSettingsUpdateListener, platform_settings_update_channel,
)
from kdcube_ai_app.infra.secrets.projections import apply_bundle_secret_update, bundle_secret_update_channel

TENANT, PROJECT = "tenant-a", "project-a"
SECRETS = bundle_secret_update_channel(tenant=TENANT, project=PROJECT)
MINE = ("provider", TENANT, PROJECT, "bundles.bundle@1.secrets.api.token")
OTHER = ("provider", TENANT, PROJECT, "bundles.other@1.secrets.api.token")


def _event(**changes):
    event = {"type": "bundles.secrets.update", "tenant": TENANT, "project": PROJECT, "bundle_id": "bundle@1",
             "scope": "bundle", "mode": "set", "keys": ["bundles.bundle@1.secrets.api.token"], "updated_by": "sdk"}
    event.update(changes)
    return json.dumps(event)


class _PubSub:
    def __init__(self, messages):
        self.messages, self.subscribed, self.unsubscribed, self.closed = list(messages), [], [], False

    async def subscribe(self, *channels):
        self.subscribed.append(channels)

    async def unsubscribe(self, *channels):
        self.unsubscribed.append(channels)

    async def get_message(self, **_kwargs):
        if self.messages:
            return self.messages.pop(0)
        await asyncio.sleep(0)
        return None

    async def aclose(self):
        self.closed = True


class _Redis:
    def __init__(self, messages):
        self.messages, self.pubsub_instance = messages, None

    def pubsub(self):
        self.pubsub_instance = _PubSub(self.messages)
        return self.pubsub_instance


@pytest.fixture
def cache():
    config_cache.clear_secret_cache()
    config_cache.set_secret_cache(MINE, "stale")
    config_cache.set_secret_cache(OTHER, "kept")
    yield
    config_cache.clear_secret_cache()


def test_the_handler_clears_exactly_the_event_entry(cache):
    assert apply_bundle_secret_update(_event().encode(), tenant=TENANT, project=PROJECT) == 1
    assert config_cache.get_secret_cache(MINE) == (False, None)
    assert config_cache.get_secret_cache(OTHER) == (True, "kept")


@pytest.mark.parametrize("data", [_event(tenant="tenant-b"), _event(project="project-b"), "not json", "[]"])
def test_another_runtime_or_an_unreadable_event_clears_nothing(cache, data):
    assert apply_bundle_secret_update(data, tenant=TENANT, project=PROJECT) == 0
    assert config_cache.get_secret_cache(MINE) == (True, "stale")


@pytest.mark.asyncio
async def test_the_existing_listener_carries_the_secrets_channel_on_the_same_connection(cache):
    stop = asyncio.Event()
    sections = []

    async def on_auth(event):
        sections.append(event.scope)
        if event.scope == "providers":
            stop.set()

    platform = PlatformSettingsUpdate.create(tenant=TENANT, project=PROJECT, section="auth", scope="providers")
    redis = _Redis([
        {"type": "message", "channel": SECRETS.encode(), "data": _event().encode()},
        {"type": "message", "channel": platform_settings_update_channel(tenant=TENANT, project=PROJECT),
         "data": platform.to_json()},
    ])
    listener = PlatformSettingsUpdateListener(
        redis, tenant=TENANT, project=PROJECT, handlers={"auth": on_auth}, stop_event=stop, poll_seconds=0.01,
        channel_handlers={SECRETS: lambda data: apply_bundle_secret_update(data, tenant=TENANT, project=PROJECT)})
    await asyncio.wait_for(listener.run(), timeout=1)
    assert redis.pubsub_instance.subscribed == [
        (platform_settings_update_channel(tenant=TENANT, project=PROJECT), SECRETS)]
    assert config_cache.get_secret_cache(MINE) == (False, None)
    assert config_cache.get_secret_cache(OTHER) == (True, "kept")
    assert sections == ["catch_up", "providers"]  # platform settings still dispatch as before
    assert redis.pubsub_instance.unsubscribed[-1] == (
        platform_settings_update_channel(tenant=TENANT, project=PROJECT), SECRETS)


@pytest.mark.asyncio
async def test_a_failing_channel_handler_never_stops_the_listener(cache):
    stop = asyncio.Event()

    async def on_auth(event):
        if event.scope == "providers":
            stop.set()

    def boom(_data):
        raise RuntimeError("handler failed")
    platform = PlatformSettingsUpdate.create(tenant=TENANT, project=PROJECT, section="auth", scope="providers")
    redis = _Redis([
        {"type": "message", "channel": SECRETS, "data": _event()},
        {"type": "message", "channel": "other", "data": platform.to_json()},
    ])
    listener = PlatformSettingsUpdateListener(
        redis, tenant=TENANT, project=PROJECT, handlers={"auth": on_auth}, stop_event=stop, poll_seconds=0.01,
        channel_handlers={SECRETS: boom})
    await asyncio.wait_for(listener.run(), timeout=1)
    assert stop.is_set()


def test_ingress_wires_the_secrets_channel_into_its_existing_listener():
    source = (Path(__file__).resolve().parents[3] / "apps" / "chat" / "ingress" / "web_app.py").read_text()
    assert source.count("PlatformSettingsUpdateListener(") == 1
    assert ("bundle_secret_update_channel(tenant=settings.TENANT, project=settings.PROJECT): partial(\n"
            "                apply_bundle_secret_update, tenant=settings.TENANT, project=settings.PROJECT),") in source
