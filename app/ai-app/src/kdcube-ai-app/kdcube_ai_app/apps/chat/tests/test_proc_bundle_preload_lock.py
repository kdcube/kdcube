# SPDX-License-Identifier: MIT

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
import dotenv

from kdcube_ai_app.apps.chat.sdk import config as sdk_config

os.environ["KDCUBE_STORAGE_PATH"] = "/tmp/kdcube-test-storage"
os.environ["ENABLE_DATABASE"] = "false"
dotenv.load_dotenv = lambda *args, **kwargs: False
dotenv.find_dotenv = lambda *args, **kwargs: ""
sdk_config.get_settings.cache_clear()

from kdcube_ai_app.apps.chat.proc import web_app
from kdcube_ai_app.infra.plugin import agentic_loader, bundle_registry


class _FakeRedis:
    def __init__(self, *, acquire: bool) -> None:
        self.acquire = acquire
        self.calls: list[tuple] = []
        self.values: dict[str, str] = {}

    async def set(self, key, value, ex=None, nx=None):
        self.calls.append(("set", key, value, ex, nx))
        if not self.acquire:
            return False
        self.values[key] = value
        return True

    async def get(self, key):
        self.calls.append(("get", key))
        return self.values.get(key)

    async def delete(self, key):
        self.calls.append(("delete", key))
        self.values.pop(key, None)
        return 1


def _app(redis: _FakeRedis) -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(
            bundle_git_task=None,
            redis_async=redis,
            pg_pool=object(),
            bundles_preload_ready=False,
            bundles_preload_errors=None,
        )
    )


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        TENANT="tenant-a",
        PROJECT="project-a",
        BUNDLES_PRELOAD_LOCK_TTL_SECONDS=45,
    )


@pytest.mark.asyncio
async def test_preload_bundles_loop_acquires_and_releases_leader_lock(monkeypatch):
    redis = _FakeRedis(acquire=True)
    app = _app(redis)
    preload_calls: list[tuple[str, str]] = []

    async def _fake_preload(spec, bundle_spec, **kwargs):
        preload_calls.append((spec.path, bundle_spec.id))
        assert kwargs["tenant"] == "tenant-a"
        assert kwargs["project"] == "project-a"

    monkeypatch.setattr(web_app, "get_settings", _settings)
    monkeypatch.setattr(
        web_app,
        "_get_bundle_registry",
        lambda: {"bundle.demo": {"path": "/tmp/demo", "module": "entrypoint", "singleton": False}},
    )
    monkeypatch.setattr(agentic_loader, "preload_bundle_async", _fake_preload)
    monkeypatch.setattr(bundle_registry, "resolve_bundle", lambda bid: SimpleNamespace(id=bid))
    monkeypatch.setattr(bundle_registry, "ADMIN_BUNDLE_ID", "kdcube.admin")

    await web_app._preload_bundles_loop(app)

    key = web_app.CONFIG.BUNDLES.PRELOAD_LOCK_FMT.format(tenant="tenant-a", project="project-a")
    assert preload_calls == [("/tmp/demo", "bundle.demo")]
    assert app.state.bundles_preload_ready is True
    assert app.state.bundles_preload_errors == {}
    assert redis.calls[0] == ("set", key, redis.calls[0][2], 45, True)
    assert ("delete", key) in redis.calls


@pytest.mark.asyncio
async def test_preload_bundles_loop_skips_when_another_instance_holds_lock(monkeypatch):
    redis = _FakeRedis(acquire=False)
    app = _app(redis)
    preload_calls: list[str] = []

    async def _fake_preload(spec, bundle_spec, **kwargs):
        del spec, bundle_spec, kwargs
        preload_calls.append("called")

    monkeypatch.setattr(web_app, "get_settings", _settings)
    monkeypatch.setattr(
        web_app,
        "_get_bundle_registry",
        lambda: {"bundle.demo": {"path": "/tmp/demo", "module": "entrypoint", "singleton": False}},
    )
    monkeypatch.setattr(agentic_loader, "preload_bundle_async", _fake_preload)
    monkeypatch.setattr(bundle_registry, "resolve_bundle", lambda bid: SimpleNamespace(id=bid))
    monkeypatch.setattr(bundle_registry, "ADMIN_BUNDLE_ID", "kdcube.admin")

    await web_app._preload_bundles_loop(app)

    key = web_app.CONFIG.BUNDLES.PRELOAD_LOCK_FMT.format(tenant="tenant-a", project="project-a")
    assert preload_calls == []
    assert app.state.bundles_preload_ready is True
    assert app.state.bundles_preload_errors == {}
    assert redis.calls == [("set", key, redis.calls[0][2], 45, True)]
