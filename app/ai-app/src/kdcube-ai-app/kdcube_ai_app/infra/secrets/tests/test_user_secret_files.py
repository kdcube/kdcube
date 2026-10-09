# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""W670 (U): per-user secrets of the secrets-file provider live in <root>/<bundle>/users/<user>/<key>.json.

Operator, 2026-10-09: "step 2 should not stay in secrets.yaml. it should be also in folder" / "this makes
sense. not in platform."
"""
from __future__ import annotations

import json
import logging
import os

import pytest
import yaml

from kdcube_ai_app.infra.secrets import manager as manager_module
from kdcube_ai_app.infra.secrets.manager import (
    SecretsFileSecretsManager, SecretsManagerConfig, SecretsManagerError, resolve_runtime_secrets_root,
)
from kdcube_ai_app.infra.secrets.runtime_file import RuntimeFileError, RuntimeFileStore
from kdcube_ai_app.infra.secrets.user_secret_files import UserSecretFileStore, decode_segment, encode_segment

HUB, OTHER = "connection-hub@1-0", "task-and-memo-app@1-0"
USER = "user-1"


def _key(user=USER, bundle=HUB, key="google.refresh_token"):
    return f"users.{user}.bundles.{bundle}.secrets.{key}"


def _config(tmp_path, **overrides):
    config = tmp_path / "config"
    config.mkdir(exist_ok=True)
    descriptor = config / "secrets.yaml"
    if not descriptor.exists():
        descriptor.write_text(yaml.safe_dump({"platform": {"existing": "platform-value"}}))
    values = {"provider": "secrets-file", "component": "proc", "global_secrets_yaml": descriptor.as_uri()}
    values.update(overrides)
    return SecretsManagerConfig(**values)


def _manager(tmp_path, **overrides):
    return SecretsFileSecretsManager(_config(tmp_path, **overrides))


def _root(tmp_path):
    return tmp_path / "config" / "secrets"


def test_root_defaults_beside_the_secrets_yaml_and_an_explicit_root_wins(tmp_path):
    descriptor = (tmp_path / "config" / "secrets.yaml")
    assert resolve_runtime_secrets_root(None, global_secrets_yaml=descriptor.as_uri()) == str(
        (tmp_path / "config").resolve() / "secrets")
    assert resolve_runtime_secrets_root(None, bundle_secrets_yaml=str(descriptor)) == str(
        (tmp_path / "config").resolve() / "secrets")
    assert resolve_runtime_secrets_root("/explicit/root", global_secrets_yaml=descriptor.as_uri()) == "/explicit/root"
    assert resolve_runtime_secrets_root(None, global_secrets_yaml="s3://bucket/config/secrets.yaml") is None
    assert resolve_runtime_secrets_root(None) is None


@pytest.mark.asyncio
async def test_a_user_secret_is_one_private_file_under_its_bundle_and_never_in_yaml(tmp_path):
    manager = _manager(tmp_path)  # no secrets.runtime.root: the default root is used
    before = (tmp_path / "config" / "secrets.yaml").read_text()
    await manager.set_user_secret(user_id=USER, bundle_id=HUB, key="google.refresh_token", value="synthetic-token")
    path = _root(tmp_path) / HUB / "users" / USER / "google.refresh_token.json"
    assert json.loads(path.read_text()) == {"value": "synthetic-token"}
    assert path.stat().st_mode & 0o777 == 0o600
    for folder in (_root(tmp_path), _root(tmp_path) / HUB, _root(tmp_path) / HUB / "users", path.parent):
        assert folder.stat().st_mode & 0o777 == 0o700
    assert (tmp_path / "config" / "secrets.yaml").read_text() == before
    assert await _manager(tmp_path).get_secret(_key()) == "synthetic-token"  # a fresh process
    assert await manager.get_secret("platform.existing") == "platform-value"


@pytest.mark.asyncio
async def test_an_explicit_root_is_used(tmp_path):
    explicit = tmp_path / "elsewhere"
    manager = _manager(tmp_path, runtime_secrets_root=str(explicit))
    await manager.set_secret(_key(), "synthetic")
    assert (explicit / HUB / "users" / USER / "google.refresh_token.json").exists()
    assert not _root(tmp_path).exists()


@pytest.mark.asyncio
async def test_owners_are_isolated_and_set_overwrites(tmp_path):
    manager = _manager(tmp_path)
    await manager.set_secret(_key(bundle=HUB), "synthetic-hub")
    await manager.set_secret(_key(bundle=OTHER), "synthetic-other")
    await manager.set_secret(_key(bundle=HUB), "synthetic-hub-2")
    assert await manager.get_secret(_key(bundle=HUB)) == "synthetic-hub-2"
    assert await manager.get_secret(_key(bundle=OTHER)) == "synthetic-other"
    await manager.delete_secret(_key(bundle=HUB))
    assert await manager.get_secret(_key(bundle=HUB)) is None
    assert await manager.get_secret(_key(bundle=OTHER)) == "synthetic-other"
    await manager.delete_secret(_key(bundle=HUB))  # idempotent


@pytest.mark.asyncio
async def test_a_bundle_less_user_secret_is_refused_on_read_and_write(tmp_path):
    manager = _manager(tmp_path)
    with pytest.raises(SecretsManagerError, match="^user_secret_bundle_required$"):
        await manager.set_user_secret(user_id=USER, key="token", value="synthetic")
    with pytest.raises(SecretsManagerError, match="^user_secret_bundle_required$"):
        await manager.get_user_secret(user_id=USER, key="token")
    assert not _root(tmp_path).exists()
    assert await manager.list_user_secret_keys(user_id=USER) == []


@pytest.mark.asyncio
async def test_inventories_come_from_the_folder_listing(tmp_path):
    manager = _manager(tmp_path)
    await manager.set_secret(_key(key="a"), "1")
    await manager.set_secret(_key(key="b.c"), "2")
    await manager.set_secret(_key(user="user-2", bundle=OTHER, key="a"), "3")
    (_root(tmp_path) / HUB / "users" / USER / ".write-leftover").write_text("x")
    assert await manager.list_user_secret_keys(user_id=USER, bundle_id=HUB) == [_key(key="a"), _key(key="b.c")]
    assert await manager.list_user_secret_keys(user_id=USER, bundle_id=OTHER) == []
    everything = await manager.list_secret_keys("users.__keys")
    assert everything == sorted([_key(key="a"), _key(key="b.c"), _key(user="user-2", bundle=OTHER, key="a")])
    assert set(everything) <= set(await manager.list_all_secret_keys())


@pytest.mark.parametrize("left,right", [
    ("a/b", "a%2Fb"), (".", "%2E"), ("..", "%2E."), ("x.json", "x"), ("é", "%C3%A9"), ("100%", "100%25"),
    (".hidden", "%2Ehidden"),
])
def test_segment_encoding_is_injective_and_never_a_path(tmp_path, left, right):
    assert encode_segment(left) != encode_segment(right)
    for text in (left, right):
        name = encode_segment(text)
        assert "/" not in name and name not in {".", ".."} and not name.startswith(".")
        assert decode_segment(name) == text


NAMES = [("a/b", "k"), ("a%2Fb", "k"), (USER, "x.json"), (USER, "x"), ("é", "k"), ("100%", "k")]


@pytest.mark.asyncio
async def test_colliding_looking_names_stay_separate_records(tmp_path):
    manager = _manager(tmp_path)
    for index, (user, key) in enumerate(NAMES):
        await manager.set_secret(_key(user=user, key=key), f"synthetic-{index}")
    for index, (user, key) in enumerate(NAMES):
        assert await manager.get_secret(_key(user=user, key=key)) == f"synthetic-{index}"
    assert sorted(os.listdir(_root(tmp_path) / HUB / "users")) == sorted(
        ["a%2Fb", "a%252Fb", USER, "%C3%A9", "100%25"])
    with pytest.raises(SecretsManagerError):  # a ".." in a provider key is refused before any storage
        await manager.set_secret(_key(user=".."), "synthetic")


def test_dot_users_are_encoded_folders(tmp_path):
    store = UserSecretFileStore(root=tmp_path / "secrets")
    for index, user in enumerate([".", "..", ".hidden"]):
        store.set(user_id=user, bundle_id=HUB, key="k", value=f"synthetic-{index}")
    for index, user in enumerate([".", "..", ".hidden"]):
        assert store.get(user_id=user, bundle_id=HUB, key="k") == f"synthetic-{index}"
    assert sorted(os.listdir(tmp_path / "secrets" / HUB / "users")) == ["%2E", "%2E.", "%2Ehidden"]


@pytest.mark.parametrize("bundle", ["platform", "../escape", "a.b"])
def test_an_invalid_owner_bundle_is_refused(tmp_path, bundle):
    store = UserSecretFileStore(root=tmp_path / "secrets")
    with pytest.raises(Exception, match="user_secret_bundle_invalid"):
        store.set(user_id=USER, bundle_id=bundle, key="k", value="v")
    assert not (tmp_path / "secrets").exists()


@pytest.mark.parametrize("level", ["bundle", "users", "user"])
def test_a_symlink_or_broad_folder_at_any_level_is_refused(tmp_path, level):
    store = UserSecretFileStore(root=tmp_path / "secrets")
    store.set(user_id=USER, bundle_id=HUB, key="k", value="synthetic")
    folder = {"bundle": tmp_path / "secrets" / HUB, "users": tmp_path / "secrets" / HUB / "users",
              "user": tmp_path / "secrets" / HUB / "users" / USER}[level]
    folder.chmod(0o755)
    with pytest.raises(Exception, match="user_secret_storage_unavailable"):
        store.get(user_id=USER, bundle_id=HUB, key="k")
    assert folder.stat().st_mode & 0o777 == 0o755  # refused, never chmod-ed
    folder.chmod(0o700)
    moved = tmp_path / "moved"
    folder.rename(moved)
    folder.symlink_to(moved)
    for call in (lambda: store.get(user_id=USER, bundle_id=HUB, key="k"),
                 lambda: store.set(user_id=USER, bundle_id=HUB, key="k", value="other"),
                 lambda: store.list_keys(user_id=USER, bundle_id=HUB)):
        with pytest.raises(Exception, match="user_secret_storage_unavailable"):
            call()


def test_a_broad_or_linked_record_is_refused(tmp_path):
    store = UserSecretFileStore(root=tmp_path / "secrets")
    store.set(user_id=USER, bundle_id=HUB, key="k", value="synthetic")
    path = tmp_path / "secrets" / HUB / "users" / USER / "k.json"
    path.chmod(0o644)
    with pytest.raises(Exception, match="user_secret_storage_unavailable"):
        store.get(user_id=USER, bundle_id=HUB, key="k")
    path.chmod(0o600)
    os.link(path, tmp_path / "second-name")
    with pytest.raises(Exception, match="user_secret_storage_unavailable"):
        store.get(user_id=USER, bundle_id=HUB, key="k")


def test_users_is_not_a_runtime_namespace(tmp_path):
    with pytest.raises(RuntimeFileError, match="^runtime_secret_scope_invalid$"):
        RuntimeFileStore(root=tmp_path, namespace="users", authorized_namespaces=("users",), owner=HUB)


def _yaml_with_users(tmp_path, users):
    config = tmp_path / "config"
    config.mkdir(exist_ok=True)
    (config / "secrets.yaml").write_text(yaml.safe_dump({"platform": {"existing": "platform-value"}, "users": users}))


SOURCE = {
    "user-1": {"bundles": {HUB: {"secrets": {"google": {"refresh_token": "synthetic-a"}, "other": "synthetic-b"}},
                           OTHER: {"secrets": {"k": "synthetic-c"}}}},
    "user-2": {"bundles": {HUB: {"secrets": {"k": "synthetic-d"}}}},
}


@pytest.mark.asyncio
async def test_migration_moves_every_user_leaf_then_removes_them_from_yaml(tmp_path):
    _yaml_with_users(tmp_path, SOURCE)
    manager = _manager(tmp_path)
    assert await manager.migrate_user_secrets(dry_run=True) == {
        "found": 4, "written": 0, "already_present": 0, "removed_from_yaml": 0, "would_write": 4}
    assert "users" in yaml.safe_load((tmp_path / "config" / "secrets.yaml").read_text())
    assert await manager.migrate_user_secrets() == {
        "found": 4, "written": 4, "already_present": 0, "removed_from_yaml": 4}
    data = yaml.safe_load((tmp_path / "config" / "secrets.yaml").read_text())
    assert data == {"platform": {"existing": "platform-value"}}
    fresh = _manager(tmp_path)
    assert await fresh.get_secret(_key(key="google.refresh_token")) == "synthetic-a"
    assert await fresh.get_secret(_key(bundle=OTHER, key="k")) == "synthetic-c"
    assert await fresh.get_secret(_key(user="user-2", key="k")) == "synthetic-d"
    assert await fresh.migrate_user_secrets() == {"found": 0, "written": 0, "already_present": 0,
                                                  "removed_from_yaml": 0}


@pytest.mark.asyncio
async def test_a_crash_before_the_yaml_write_loses_nothing_and_a_rerun_completes(tmp_path, monkeypatch):
    _yaml_with_users(tmp_path, SOURCE)
    manager = _manager(tmp_path)

    def crash(*_args):
        raise SecretsManagerError("synthetic crash")

    monkeypatch.setattr(manager_module, "_write_yaml_mapping_to_storage", crash)
    with pytest.raises(SecretsManagerError):
        await manager.migrate_user_secrets()
    monkeypatch.undo()
    assert "users" in yaml.safe_load((tmp_path / "config" / "secrets.yaml").read_text())
    assert await manager.migrate_user_secrets() == {"found": 4, "written": 0, "already_present": 4,
                                                    "removed_from_yaml": 4}
    assert await manager.get_secret(_key(key="other")) == "synthetic-b"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["conflict", "bundle_less"])
async def test_migration_stops_before_any_change(tmp_path, case):
    users = json.loads(json.dumps(SOURCE))
    if case == "bundle_less":
        users["user-3"] = {"secrets": {"k": "synthetic-e"}}
    _yaml_with_users(tmp_path, users)
    before = (tmp_path / "config" / "secrets.yaml").read_text()
    manager = _manager(tmp_path)
    if case == "conflict":
        UserSecretFileStore(root=_root(tmp_path)).set(user_id=USER, bundle_id=OTHER, key="k", value="different")
    reason = "destination_conflict" if case == "conflict" else "bundle_less_source"
    with pytest.raises(SecretsManagerError, match=reason):
        await manager.migrate_user_secrets()
    assert (tmp_path / "config" / "secrets.yaml").read_text() == before
    assert not (_root(tmp_path) / HUB).exists()


@pytest.mark.asyncio
async def test_values_never_reach_logs(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    _yaml_with_users(tmp_path, {"user-1": {"bundles": {HUB: {"secrets": {"k": "synthetic-never-logged"}}}}})
    manager = _manager(tmp_path)
    await manager.migrate_user_secrets()
    await manager.get_secret(_key(key="k"))
    assert "synthetic-never-logged" not in caplog.text
