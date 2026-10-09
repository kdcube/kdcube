"""W670: Connection Hub runtime records are normal secrets of their owner bundle, through the secrets manager.

Operator, 2026-10-09: "simply start to store the secrets with secrets manager" / "and read them with secreys
manager" / "so this is change in connection hub, not in secret manager". The adapter uses only get_secret /
set_secret / delete_secret of the configured manager; here a REAL secrets-file manager on a temporary
bundles.secrets.yaml, and no *_ephemeral_secret* manager method.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import pytest
import yaml

from kdcube_ai_app.infra.secrets import ephemeral as ephemeral_module
from kdcube_ai_app.infra.secrets.ephemeral import RECORD_LOCK_PREFIX, RUNTIME_RECORDS_KEY, ephemeral_secret_store
from kdcube_ai_app.infra.secrets.manager import SecretsFileSecretsManager, SecretsManagerConfig, SecretsManagerError

OWNER = "connection-hub@1-0"
NAMESPACE = "test-custody"
REF = "a" * 32
EPHEMERAL_METHODS = ("set_ephemeral_secret", "create_ephemeral_secret", "get_ephemeral_secret",
                     "delete_ephemeral_secret", "purge_expired_ephemeral_secrets", "qualify_runtime_custody")


def _manager(tmp_path):
    bundle = tmp_path / "bundles.secrets.yaml"
    if not bundle.exists():
        bundle.write_text(yaml.safe_dump({"bundles": {"version": "1", "items": [
            {"id": OWNER, "secrets": {"peer_proof_secret": "existing-descriptor-value"}}]}}))
    manager = SecretsFileSecretsManager(SecretsManagerConfig(
        provider="secrets-file", component="proc", bundle_secrets_yaml=bundle.as_uri()))
    for name in EPHEMERAL_METHODS:  # the adapter must never reach the manager's special runtime calls
        setattr(manager, name, _forbidden(name))
    return manager


class FakeRedis:
    """SET NX PX and the compare-and-delete release script, with Redis semantics (one shared server)."""

    def __init__(self):
        self.values, self.refused = {}, 0

    async def set(self, key, value, *, nx=False, px=None):
        if nx and key in self.values:
            self.refused += 1
            return None
        self.values[key] = value
        return True

    async def eval(self, script, numkeys, key, token):
        if self.values.get(key) == token:
            del self.values[key]
            return 1
        return 0


REDIS = FakeRedis()


def _forbidden(name):
    async def call(*args, **kwargs):
        raise AssertionError(f"{name} must not be called")
    return call


def _store(manager, redis=None, **kwargs):
    return ephemeral_secret_store(namespace=NAMESPACE, manager=manager, bundle_id=OWNER,
                                  redis=redis or REDIS, **kwargs)


def _stored(tmp_path):
    data = yaml.safe_load((tmp_path / "bundles.secrets.yaml").read_text())
    item = next(item for item in data["bundles"]["items"] if item["id"] == OWNER)
    return item["secrets"].get(RUNTIME_RECORDS_KEY, {})


@pytest.mark.asyncio
async def test_a_record_is_a_normal_owner_secret_and_survives_a_restart(tmp_path):
    expiry = int(time.time()) + 60
    assert await _store(_manager(tmp_path)).create(secret_ref=REF, value="synthetic-runtime", expires_at=expiry)
    stored = json.loads(_stored(tmp_path)[NAMESPACE][REF])
    assert stored == {"value": "synthetic-runtime", "expires_at": expiry}
    fresh = _manager(tmp_path)  # a new process reads the same file
    assert await _store(fresh).get(secret_ref=REF) == "synthetic-runtime"
    assert await fresh.get_secret(f"bundles.{OWNER}.secrets.peer_proof_secret") == "existing-descriptor-value"


@pytest.mark.asyncio
async def test_create_only_keeps_the_original_and_set_refuses_a_collision(tmp_path):
    store, expiry = _store(_manager(tmp_path)), int(time.time()) + 60
    assert await store.create(secret_ref=REF, value="original", expires_at=expiry)
    assert not await store.create(secret_ref=REF, value="other", expires_at=expiry + 5)
    with pytest.raises(SecretsManagerError, match="runtime_secret_create_conflict"):
        await store.set(secret_ref=REF, value="other", expires_at=expiry)
    assert await store.get(secret_ref=REF) == "original"


@pytest.mark.asyncio
async def test_expiry_is_enforced_and_delete_is_idempotent(tmp_path):
    store = _store(_manager(tmp_path))
    assert await store.create(secret_ref=REF, value="v", expires_at=int(time.time()) - 1)
    assert await store.get(secret_ref=REF) is None
    await store.delete(secret_ref=REF)
    await store.delete(secret_ref=REF)
    assert REF not in _stored(tmp_path).get(NAMESPACE, {})


@pytest.mark.asyncio
async def test_purge_uses_the_owner_inventory_and_is_bounded(tmp_path):
    store, now = _store(_manager(tmp_path)), int(time.time())
    for index in range(5):
        await store.create(secret_ref=f"old{index}", value="v", expires_at=now - 10)
    await store.create(secret_ref="live", value="v", expires_at=now + 60)
    other = ephemeral_secret_store(namespace="other-custody", manager=_manager(tmp_path), bundle_id=OWNER,
                                   redis=REDIS)
    await other.create(secret_ref="old-other", value="v", expires_at=now - 10)
    assert await store.purge_expired(now=now, limit=3) == 3
    assert await store.purge_expired(now=now, limit=3) == 2
    assert set(_stored(tmp_path)[NAMESPACE]) == {"live"}
    assert set(_stored(tmp_path)["other-custody"]) == {"old-other"}  # another namespace is untouched


@pytest.mark.asyncio
async def test_namespaces_and_owners_are_isolated(tmp_path):
    manager, expiry = _manager(tmp_path), int(time.time()) + 60
    await _store(manager).create(secret_ref=REF, value="one", expires_at=expiry)
    assert await ephemeral_secret_store(namespace="other-custody", manager=manager, bundle_id=OWNER,
                                        redis=REDIS).get(secret_ref=REF) is None
    assert await ephemeral_secret_store(namespace=NAMESPACE, manager=manager, bundle_id="other-app@1-0",
                                        redis=REDIS).get(secret_ref=REF) is None


@pytest.mark.asyncio
async def test_qualify_is_a_probe_round_trip_and_leaves_nothing(tmp_path):
    store = _store(_manager(tmp_path))
    assert await store.qualify_durable_backend() is True
    assert _stored(tmp_path).get(NAMESPACE, {}) == {}


@pytest.mark.asyncio
async def test_the_owner_defaults_to_the_current_bundle_and_a_bundle_less_caller_is_platform(tmp_path, monkeypatch):
    from kdcube_ai_app.apps.chat.sdk import config as sdk_config

    manager, expiry = _manager(tmp_path), int(time.time()) + 60
    monkeypatch.setattr(sdk_config, "_resolve_current_bundle_id", lambda: OWNER)
    store = ephemeral_secret_store(namespace=NAMESPACE, manager=manager, redis=REDIS)
    assert await store.create(secret_ref=REF, value="from-context", expires_at=expiry)
    assert await _store(manager).get(secret_ref=REF) == "from-context"
    monkeypatch.setattr(sdk_config, "_resolve_current_bundle_id", lambda: None)
    assert store._key(REF) == f"platform.{RUNTIME_RECORDS_KEY}.{NAMESPACE}.{REF}"


@pytest.mark.asyncio
async def test_values_are_never_logged(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    store = _store(_manager(tmp_path))
    await store.create(secret_ref=REF, value="never-logged-value", expires_at=int(time.time()) + 60)
    await store.get(secret_ref=REF)
    assert "never-logged-value" not in caplog.text


@pytest.mark.asyncio
async def test_two_instances_creating_the_same_ref_never_both_succeed(tmp_path):
    """Main/Infra guard, Infra's forced schedule on two REAL secrets-file manager instances sharing one Redis:
    A reads absent and pauses; B tries the same ref. With the Hub's per-record lock B cannot read until A has
    written and read back, so exactly one create succeeds and the original is preserved."""
    redis = FakeRedis()
    a_manager, b_manager = _manager(tmp_path), _manager(tmp_path)
    b_blocked = asyncio.Event()
    get = a_manager.get_secret
    first = {"a": True}

    async def a_get_secret(key):
        value = await get(key)
        if first["a"]:  # A's absence check: hold here until B has hit the lock
            first["a"] = False
            try:  # without the lock B is never blocked; then A resumes after B and the guard below fails
                await asyncio.wait_for(b_blocked.wait(), 1.0)
            except asyncio.TimeoutError:
                pass
        return value

    a_manager.get_secret = a_get_secret
    refused = redis.set

    async def watched_set(key, value, *, nx=False, px=None):
        result = await refused(key, value, nx=nx, px=px)
        if result is None:
            b_blocked.set()
        return result

    redis.set = watched_set
    expiry = int(time.time()) + 60
    a_task = asyncio.create_task(_store(a_manager, redis).create(secret_ref=REF, value="from-a", expires_at=expiry))
    await asyncio.sleep(0)
    b_created = await _store(b_manager, redis).create(secret_ref=REF, value="from-b", expires_at=expiry)
    a_created = await a_task
    assert (a_created, b_created) == (True, False)
    assert await _store(_manager(tmp_path), redis).get(secret_ref=REF) == "from-a"
    assert redis.values == {}  # both locks released


@pytest.mark.asyncio
async def test_a_lock_timeout_is_unavailable_and_never_reports_created(tmp_path, monkeypatch):
    redis, store = FakeRedis(), _store(_manager(tmp_path), FakeRedis())
    monkeypatch.setattr(ephemeral_module, "RECORD_LOCK_WAIT_SECONDS", 0.1)
    store = _store(_manager(tmp_path), redis)
    redis.values[RECORD_LOCK_PREFIX + store._key(REF)] = "another-holder"
    with pytest.raises(SecretsManagerError, match="runtime_record_lock_unavailable"):
        await store.create(secret_ref=REF, value="v", expires_at=int(time.time()) + 60)
    assert redis.values[RECORD_LOCK_PREFIX + store._key(REF)] == "another-holder"  # never released by us
    assert REF not in _stored(tmp_path).get(NAMESPACE, {})


@pytest.mark.asyncio
async def test_without_redis_a_create_is_unavailable_not_unlocked(tmp_path):
    store = ephemeral_secret_store(namespace=NAMESPACE, manager=_manager(tmp_path), bundle_id=OWNER)
    store._redis = None
    with pytest.raises(SecretsManagerError, match="runtime_record_lock_unavailable"):
        await store.create(secret_ref=REF, value="v", expires_at=int(time.time()) + 60)
