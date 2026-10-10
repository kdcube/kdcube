# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""W670: file-backed secrets are cached with their source file's fingerprint, never as a miss.

Operator, 2026-10-09: "i am also worried about cache and how its populated" / "yes" (validated cache and no
cached misses in PR #340). A write by any other process is visible on the next get, with no TTL wait.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
import yaml

from kdcube_ai_app.apps.chat.sdk import config as sdk_config
from kdcube_ai_app.apps.chat.sdk.config_cache import clear_secret_cache
from kdcube_ai_app.infra.secrets.manager import (
    SecretsFileSecretsManager, SecretsManagerConfig, SecretsManagerWriteError,
)
from kdcube_ai_app.infra.secrets.user_secret_files import UserSecretFileStore

HUB, USER = "connection-hub@1-0", "user-1"
APP_KEY = f"bundles.{HUB}.secrets.google.client_secret"
SETTINGS = SimpleNamespace(TENANT="t", PROJECT="p")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    (config / "secrets.yaml").write_text(yaml.safe_dump({"platform": {"services": {"key": "synthetic-platform"}}}))
    manager = SecretsFileSecretsManager(SecretsManagerConfig(
        provider="secrets-file", component="proc", global_secrets_yaml=(config / "secrets.yaml").as_uri()))
    reads = []
    original = manager.get_secret

    async def counted(key):
        reads.append(key)
        return await original(key)

    monkeypatch.setattr(manager, "get_secret", counted)
    monkeypatch.setattr(sdk_config, "get_secrets_manager", lambda settings=None: manager)
    clear_secret_cache()
    other = UserSecretFileStore(root=config / "secrets")  # another process: it shares only the files
    yield SimpleNamespace(config=config, manager=manager, reads=reads, other=other)
    clear_secret_cache()


async def _get(key):
    return await sdk_config._get_provider_secret_cached(SETTINGS, key)


@pytest.mark.asyncio
async def test_an_unchanged_file_is_one_stat_and_no_read(rig):
    rig.other.set_app(bundle_id=HUB, key="google.client_secret", value="synthetic-1")
    assert await _get(APP_KEY) == "synthetic-1"
    assert await _get(APP_KEY) == "synthetic-1"
    assert rig.reads == [APP_KEY]


@pytest.mark.asyncio
async def test_another_process_write_and_delete_are_visible_on_the_next_get(rig):
    rig.other.set_app(bundle_id=HUB, key="google.client_secret", value="synthetic-1")
    assert await _get(APP_KEY) == "synthetic-1"
    rig.other.set_app(bundle_id=HUB, key="google.client_secret", value="synthetic-2")
    assert await _get(APP_KEY) == "synthetic-2"
    rig.other.delete_app(bundle_id=HUB, key="google.client_secret")
    assert await _get(APP_KEY) is None


@pytest.mark.asyncio
async def test_a_miss_is_never_cached(rig):
    assert await _get(APP_KEY) is None
    rig.other.set_app(bundle_id=HUB, key="google.client_secret", value="synthetic-late")
    assert await _get(APP_KEY) == "synthetic-late"


@pytest.mark.asyncio
async def test_user_secrets_are_validated_the_same_way(rig):
    async def get_user():
        return await sdk_config._get_user_secret_cached(SETTINGS, user_id=USER, bundle_id=HUB, key="token")

    assert await get_user() is None
    rig.other.set(user_id=USER, bundle_id=HUB, key="token", value="synthetic-user-1")
    assert await get_user() == "synthetic-user-1"
    rig.other.set(user_id=USER, bundle_id=HUB, key="token", value="synthetic-user-2")
    assert await get_user() == "synthetic-user-2"
    rig.other.delete(user_id=USER, bundle_id=HUB, key="token")
    assert await get_user() is None


@pytest.mark.asyncio
async def test_platform_secrets_follow_the_yaml_file(rig):
    assert await _get("platform.services.key") == "synthetic-platform"
    assert await _get("platform.services.key") == "synthetic-platform"
    assert rig.reads == ["platform.services.key"]
    (rig.config / "secrets.yaml").write_text(yaml.safe_dump({"platform": {"services": {"key": "synthetic-new"}}}))
    assert await _get("platform.services.key") == "synthetic-new"


@pytest.mark.asyncio
async def test_a_record_made_unsafe_is_re_read_and_refused(rig):
    rig.other.set_app(bundle_id=HUB, key="google.client_secret", value="synthetic-1")
    assert await _get(APP_KEY) == "synthetic-1"
    path = rig.config / "secrets" / HUB / "google.client_secret.json"
    path.chmod(0o644)  # same content and mtime; ctime changes
    assert await _get(APP_KEY) is None  # the re-read refuses the broad file; the getter maps it to None
    path.chmod(0o600)
    moved = path.with_name("moved.json")
    path.rename(moved)
    os.symlink(moved, path)
    assert await _get(APP_KEY) is None
    assert len(rig.reads) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["secrets root", "bundle folder"])
async def test_a_warm_entry_is_not_served_once_a_private_folder_above_it_becomes_broad(rig, level):
    """W670 review R/U-CACHE-1 (Infra, exact cc45723d): a warm SDK cache still returned the value after a
    private secrets folder became 0755, while a direct manager read refused it."""
    rig.other.set_app(bundle_id=HUB, key="google.client_secret", value="synthetic-1")
    assert await _get(APP_KEY) == "synthetic-1"  # warm
    folder = rig.config / "secrets" if level == "secrets root" else rig.config / "secrets" / HUB
    folder.chmod(0o755)
    try:
        with pytest.raises(SecretsManagerWriteError, match="user_secret_storage_unavailable"):
            await rig.manager.get_secret(APP_KEY)  # the direct read refuses
        assert await _get(APP_KEY) is None  # and the warm cache no longer answers (the getter maps a refusal to None)
    finally:
        folder.chmod(0o700)
    assert await _get(APP_KEY) == "synthetic-1"  # private again: read afresh, not from the dropped entry


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["users folder", "user folder", "user bundle folder"])
async def test_a_warm_user_secret_is_not_served_once_a_private_folder_above_it_becomes_broad(rig, level):
    async def get_user():
        return await sdk_config._get_user_secret_cached(SETTINGS, user_id=USER, bundle_id=HUB, key="token")

    rig.other.set(user_id=USER, bundle_id=HUB, key="token", value="synthetic-user-1")
    assert await get_user() == "synthetic-user-1"
    record = next((rig.config / "secrets").rglob("token.json"))
    chain = [path for path in record.parents if (rig.config / "secrets") in path.parents]
    folder = {"users folder": chain[-2], "user folder": chain[-3] if len(chain) > 2 else chain[0],
              "user bundle folder": chain[0]}[level]
    folder.chmod(0o755)
    try:
        assert await get_user() is None
    finally:
        folder.chmod(0o700)
    assert await get_user() == "synthetic-user-1"


@pytest.mark.asyncio
async def test_a_warm_entry_is_not_served_once_its_folder_is_replaced(rig):
    rig.other.set_app(bundle_id=HUB, key="google.client_secret", value="synthetic-1")
    assert await _get(APP_KEY) == "synthetic-1"
    folder = rig.config / "secrets" / HUB
    moved = folder.with_name("moved")
    folder.rename(moved)
    os.symlink(moved, folder)  # same file, same inode, reached through a symlinked folder now
    assert await _get(APP_KEY) is None


def test_only_a_file_backed_miss_is_never_cached():
    from kdcube_ai_app.apps.chat.sdk.config_cache import get_secret_cache, set_secret_cache

    clear_secret_cache()
    file_key, other_key = ("provider", "t", "p", "platform.file"), ("provider", "t", "p", "platform.remote")
    assert set_secret_cache(file_key, None, fingerprint=(1, 2, 3, 4)) is None
    assert get_secret_cache(file_key, fingerprint=(1, 2, 3, 4)) == (False, None)
    # Non-file backends may cache a miss; the bundles.secrets.update invalidation clears it (TTL as backstop).
    assert set_secret_cache(other_key, None) is None
    assert get_secret_cache(other_key) == (True, None)
    clear_secret_cache()
