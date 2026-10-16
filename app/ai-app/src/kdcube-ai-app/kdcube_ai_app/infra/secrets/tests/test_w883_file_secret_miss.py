# SPDX-License-Identifier: MIT
"""W883: a not-set platform secret in the secrets files is remembered until either file's bytes change.

Operator, W883, verbatim: "yes, content hash" and "yes but this is only when the secrets are in file. and if
they are not in file? we have other secrets providerbackends". Other backends already cache a miss (120 s,
cleared by bundles.secrets.update); a per-secret record file still decides its miss by existence each lookup.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
import yaml

from kdcube_ai_app.apps.chat.sdk import config as sdk_config
from kdcube_ai_app.apps.chat.sdk.config_cache import clear_secret_cache
from kdcube_ai_app.infra.secrets.manager import SecretsFileSecretsManager, SecretsManagerConfig
from kdcube_ai_app.infra.secrets.user_secret_files import UserSecretFileStore

HUB = "connection-hub@1-0"
SETTINGS = SimpleNamespace(TENANT="t", PROJECT="p")
UNSET = "platform.services.openai.api_key"


def _write(path, services):
    path.write_text(yaml.safe_dump({"platform": {"services": services}}), encoding="utf-8")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    _write(config / "secrets.yaml", {"key": "synthetic-platform"})
    (config / "bundles.secrets.yaml").write_text(yaml.safe_dump({"bundles": {"version": "1", "items": []}}), encoding="utf-8")
    manager = SecretsFileSecretsManager(SecretsManagerConfig(
        provider="secrets-file", component="proc",
        global_secrets_yaml=(config / "secrets.yaml").as_uri(),
        bundle_secrets_yaml=(config / "bundles.secrets.yaml").as_uri()))
    reads = []
    original = manager.get_secret

    async def counted(key):
        reads.append(key)
        return await original(key)

    monkeypatch.setattr(manager, "get_secret", counted)
    monkeypatch.setattr(sdk_config, "get_secrets_manager", lambda settings=None: manager)
    clear_secret_cache()
    yield SimpleNamespace(config=config, manager=manager, reads=reads, other=UserSecretFileStore(root=config / "secrets"))
    clear_secret_cache()


async def _get(key):
    return await sdk_config._get_provider_secret_cached(SETTINGS, key)


@pytest.mark.asyncio
async def test_a_not_set_platform_secret_is_read_once_for_unchanged_files(rig):
    for _ in range(3):
        assert await _get(UNSET) is None
    assert rig.reads == [UNSET]


@pytest.mark.asyncio
async def test_the_remembered_miss_ends_when_the_global_file_changes_even_with_same_size_and_mtime(rig):
    assert await _get(UNSET) is None
    path = rig.config / "secrets.yaml"
    before = path.stat()
    _write(path, {"key": "synthetic-platform", "openai": {"api_key": "x"}})
    assert await _get(UNSET) == "x"
    # same size and mtime: only the content hash can tell
    _write(path, {"key": "synthetic-platform", "openai": {"api_key": "y"}})
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert await _get(UNSET) == "y"


@pytest.mark.asyncio
async def test_the_remembered_miss_ends_when_the_bundle_secrets_file_changes(rig):
    assert await _get(UNSET) is None
    calls = len(rig.reads)
    (rig.config / "bundles.secrets.yaml").write_text(
        yaml.safe_dump({"bundles": {"version": "1", "items": [{"id": HUB, "secrets": {"a": "b"}}]}}), encoding="utf-8")
    await _get(UNSET)
    assert len(rig.reads) == calls + 1  # re-read: the signal covers both files that feed platform keys


@pytest.mark.asyncio
async def test_a_found_platform_secret_also_follows_the_content(rig):
    assert await _get("platform.services.key") == "synthetic-platform"
    assert await _get("platform.services.key") == "synthetic-platform"
    assert rig.reads == ["platform.services.key"]
    _write(rig.config / "secrets.yaml", {"key": "synthetic-changed"})
    assert await _get("platform.services.key") == "synthetic-changed"


@pytest.mark.asyncio
async def test_a_per_secret_record_miss_is_still_decided_by_existence_each_lookup(rig):
    key = f"bundles.{HUB}.secrets.google.client_secret"
    assert await _get(key) is None
    assert await _get(key) is None
    assert rig.reads.count(key) == 2  # never remembered (claude-ops: EFS can serve a stale folder)
    rig.other.set_app(bundle_id=HUB, key="google.client_secret", value="synthetic-late")
    assert await _get(key) == "synthetic-late"


def test_only_a_local_platform_key_has_a_content_signal(rig):
    assert rig.manager.secret_miss_signal(UNSET)[0] == "sha256"
    assert rig.manager.secret_miss_signal(f"bundles.{HUB}.secrets.google.client_secret") is None
