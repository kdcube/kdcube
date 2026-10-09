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

from kdcube_ai_app.infra.secrets.ephemeral import RUNTIME_RECORDS_KEY, ephemeral_secret_store
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


def _forbidden(name):
    async def call(*args, **kwargs):
        raise AssertionError(f"{name} must not be called")
    return call


def _store(manager, **kwargs):
    return ephemeral_secret_store(namespace=NAMESPACE, manager=manager, bundle_id=OWNER, **kwargs)


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
    other = ephemeral_secret_store(namespace="other-custody", manager=_manager(tmp_path), bundle_id=OWNER)
    await other.create(secret_ref="old-other", value="v", expires_at=now - 10)
    assert await store.purge_expired(now=now, limit=3) == 3
    assert await store.purge_expired(now=now, limit=3) == 2
    assert set(_stored(tmp_path)[NAMESPACE]) == {"live"}
    assert set(_stored(tmp_path)["other-custody"]) == {"old-other"}  # another namespace is untouched


@pytest.mark.asyncio
async def test_namespaces_and_owners_are_isolated(tmp_path):
    manager, expiry = _manager(tmp_path), int(time.time()) + 60
    await _store(manager).create(secret_ref=REF, value="one", expires_at=expiry)
    assert await ephemeral_secret_store(namespace="other-custody", manager=manager, bundle_id=OWNER).get(
        secret_ref=REF) is None
    assert await ephemeral_secret_store(namespace=NAMESPACE, manager=manager, bundle_id="other-app@1-0").get(
        secret_ref=REF) is None


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
    store = ephemeral_secret_store(namespace=NAMESPACE, manager=manager)
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
async def test_two_instances_writing_the_same_value_for_one_ref_leave_that_value(tmp_path):
    """(b) Main: no adapter lock. PostgreSQL serializes: the issuers reserve the ref and seal the value's digest
    before create, and every retry of a ref writes the identical value. Two independent managers creating the
    same ref with that same value concurrently leave exactly that value (either outcome is acceptable).
    (c) A foreign value under a reserved ref is refused at the issuer, by the sealed digest:
    test_original_refresh_issuer.py::test_existing_refresh_custody_value_must_match_sealed_original_digest and
    test_bound_session_crash_recovery.py::test_failed_readback_never_activates_candidate."""
    expiry = int(time.time()) + 60
    outcomes = await asyncio.gather(*[
        _store(_manager(tmp_path)).create(secret_ref=REF, value="same-sealed-value", expires_at=expiry)
        for _ in range(2)])
    assert True in outcomes
    assert await _store(_manager(tmp_path)).get(secret_ref=REF) == "same-sealed-value"
