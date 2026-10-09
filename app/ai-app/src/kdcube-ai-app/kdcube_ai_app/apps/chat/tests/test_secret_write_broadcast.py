"""Secret writes through the SDK helpers reach every process's secret cache (operator, 2026-10-09).

Operator: "this cache of secrets on cloud deployment which is not invalidated its a big problem" / "in proc we
have the config changes listener". The SDK write helpers publish the same bundles.secrets.update event the admin
REST paths publish (identifiers only), and the processor's existing config listener clears exactly that entry.
A broadcast failure never fails the write.
"""
from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk import config as sdk_config
from kdcube_ai_app.apps.chat.sdk.protocol import ExternalEventActor, ExternalEventPayload, ExternalEventRouting, ExternalEventUser
from kdcube_ai_app.apps.chat.sdk.runtime import comm_ctx
from kdcube_ai_app.apps.chat.tests import test_processor as harness  # imports the processor with real settings
from kdcube_ai_app.apps.chat.tests.test_config_secrets_provider import _FakeSecretsManager

CHANNEL = "kdcube:config:bundles:secrets:update:ctx-tenant:ctx-project"
REAL_GET_SETTINGS = sdk_config.get_settings
VALUE = "synthetic-secret-value-never-broadcast"


class _Redis:
    def __init__(self, fail: bool = False):
        self.published: list[tuple[str, dict]] = []
        self.deleted: list[str] = []
        self.fail = fail

    async def publish(self, channel, message):
        if self.fail:
            raise ConnectionError("redis down")
        self.published.append((channel, json.loads(message)))

    async def delete(self, key):
        if self.fail:
            raise ConnectionError("redis down")
        self.deleted.append(key)


@pytest.fixture
def wired(monkeypatch):
    import kdcube_ai_app.infra.redis.client as redis_client_module

    manager = _FakeSecretsManager()

    async def delete_user_secret(*, user_id, key, bundle_id=None):
        manager.set_calls.append(("delete", user_id, bundle_id, key))
    manager.delete_user_secret = delete_user_secret
    redis = _Redis()
    monkeypatch.setattr(sdk_config, "get_secrets_manager", lambda _settings: manager)
    monkeypatch.setattr(sdk_config, "get_settings",
                        lambda: SimpleNamespace(TENANT="demo-tenant", PROJECT="demo-project", REDIS_URL="redis://x"))
    monkeypatch.setattr(redis_client_module, "get_async_redis_client", lambda _url: redis)
    monkeypatch.setattr(comm_ctx, "get_current_request_context", lambda: ExternalEventPayload(
        routing=ExternalEventRouting(bundle_id="bundle.demo", session_id="s-1"),
        actor=ExternalEventActor(tenant_id="ctx-tenant", project_id="ctx-project"),
        user=ExternalEventUser(user_type="registered", user_id="user-1"),
    ))
    monkeypatch.setattr(sdk_config, "_PUBLISH_FAILURE_LOGGED", False)
    return manager, redis


@pytest.mark.asyncio
async def test_a_bundle_secret_write_publishes_identifiers_only(wired):
    manager, redis = wired
    await sdk_config.set_bundle_secret("api.token", VALUE)
    assert manager.set_calls == [("bundles.bundle.demo.secrets.api.token", VALUE)]
    assert len(redis.published) == 1
    channel, event = redis.published[0]
    assert channel == CHANNEL
    assert {key: event[key] for key in ("type", "tenant", "project", "bundle_id", "scope", "mode", "keys")} == {
        "type": "bundles.secrets.update", "tenant": "ctx-tenant", "project": "ctx-project",
        "bundle_id": "bundle.demo", "scope": "bundle", "mode": "set",
        "keys": ["bundles.bundle.demo.secrets.api.token"]}
    assert VALUE not in json.dumps(redis.published)
    assert redis.deleted  # the bundle's secret inventory projection is invalidated too


@pytest.mark.asyncio
async def test_user_secret_set_and_delete_publish_the_user_and_the_exact_key(wired):
    manager, redis = wired
    await sdk_config.set_user_secret("oauth.refresh", VALUE)
    await sdk_config.delete_user_secret("oauth.refresh")
    events = [event for _channel, event in redis.published]
    assert [(e["scope"], e["mode"], e["user_id"], e["keys"]) for e in events] == [
        ("user", "set", "user-1", ["users.user-1.bundles.bundle.demo.secrets.oauth.refresh"]),
        ("user", "clear", "user-1", ["users.user-1.bundles.bundle.demo.secrets.oauth.refresh"]),
    ]
    assert VALUE not in json.dumps(redis.published)


@pytest.mark.asyncio
async def test_a_broadcast_failure_never_fails_the_write_and_logs_no_values(wired, monkeypatch, caplog):
    manager, redis = wired
    redis.fail = True
    caplog.set_level(logging.WARNING)
    await sdk_config.set_bundle_secret("api.token", VALUE)
    await sdk_config.set_bundle_secret("api.other", VALUE)
    assert [call[0] for call in manager.set_calls] == ["bundles.bundle.demo.secrets.api.token",
                                                       "bundles.bundle.demo.secrets.api.other"]
    # The existing publisher (infra.secrets.projections) already catches and logs a Redis failure itself.
    assert any(r.levelno >= logging.WARNING for r in caplog.records)
    assert VALUE not in caplog.text


@pytest.mark.asyncio
async def test_an_unexpected_broadcast_error_is_contained_and_logged_once(wired, monkeypatch, caplog):
    import kdcube_ai_app.infra.secrets.projections as projections

    manager, _redis = wired

    async def explode(*_args, **_kwargs):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(projections, "publish_bundle_secret_update", explode)
    caplog.set_level(logging.WARNING, logger="kdcube.settings.secrets")
    await sdk_config.set_bundle_secret("api.token", VALUE)
    await sdk_config.set_bundle_secret("api.other", VALUE)
    assert len(manager.set_calls) == 2
    warnings = [r for r in caplog.records if "not broadcast" in r.getMessage()]
    assert len(warnings) == 1 and VALUE not in caplog.text


@pytest.mark.asyncio
async def test_the_published_event_clears_exactly_that_entry_through_the_proc_listener(wired, monkeypatch):
    """End to end on the real processor config listener: the event the SDK helper publishes drops that
    bundle's cached entry and leaves another bundle's."""
    from kdcube_ai_app.apps.chat.sdk import config_cache
    import kdcube_ai_app.infra.plugin.bundle_registry as bundle_registry_mod
    import kdcube_ai_app.infra.plugin.bundle_store as bundle_store_mod

    manager, redis = wired
    await sdk_config.set_bundle_secret("api.token", VALUE)
    channel, event = redis.published[0]
    config_cache.clear_secret_cache()
    mine = ("provider", "ctx-tenant", "ctx-project", "bundles.bundle.demo.secrets.api.token")
    other = ("provider", "ctx-tenant", "ctx-project", "bundles.other.demo.secrets.api.token")
    config_cache.set_secret_cache(mine, "stale")
    config_cache.set_secret_cache(other, "kept")

    async def _fake_load_registry(*_args, **_kwargs):
        return SimpleNamespace(bundles={}, default_bundle_id=None)

    async def _fake_set_registry_async(*_args, **_kwargs):
        return None
    monkeypatch.setattr(sdk_config, "get_settings", REAL_GET_SETTINGS)
    listener_redis = harness._RedisWithMessagePubSub(
        asyncio.Event(), [{"type": "message", "channel": channel, "data": json.dumps(event)}])
    processor = harness._build_processor(listener_redis)
    listener_redis.pubsub_instance.stop_event = processor._stop_event
    monkeypatch.setattr(sdk_config, "get_settings", lambda: SimpleNamespace(TENANT="ctx-tenant", PROJECT="ctx-project"))
    monkeypatch.setattr(bundle_store_mod, "load_registry", _fake_load_registry)
    monkeypatch.setattr(bundle_registry_mod, "set_registry_async", _fake_set_registry_async)
    await processor._config_listener_loop()
    assert config_cache.get_secret_cache(mine) == (False, None)
    assert config_cache.get_secret_cache(other) == (True, "kept")
    config_cache.clear_secret_cache()
