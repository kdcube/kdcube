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
        ["a%2Fb", "a%252%46b", USER, "%C3%A9", "100%25"])
    with pytest.raises(SecretsManagerError):  # a ".." in a provider key is refused before any storage
        await manager.set_secret(_key(user=".."), "synthetic")


def test_dot_users_are_encoded_folders(tmp_path):
    store = UserSecretFileStore(root=tmp_path / "secrets")
    for index, user in enumerate([".", "..", ".hidden"]):
        store.set(user_id=user, bundle_id=HUB, key="k", value=f"synthetic-{index}")
    for index, user in enumerate([".", "..", ".hidden"]):
        assert store.get(user_id=user, bundle_id=HUB, key="k") == f"synthetic-{index}"
    assert sorted(os.listdir(tmp_path / "secrets" / HUB / "users")) == ["%2E", "%2E.", "%2Ehidden"]


@pytest.mark.parametrize("bundle", ["platform", "../escape", "..", ".hidden", "a/b", ""])
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
        "found": 4, "user_found": 4, "app_found": 0, "written": 0, "already_present": 0, "removed_from_yaml": 0,
        "bundle_items_kept": 0, "would_write": 4}
    assert "users" in yaml.safe_load((tmp_path / "config" / "secrets.yaml").read_text())
    assert await manager.migrate_user_secrets() == {
        "found": 4, "user_found": 4, "app_found": 0, "written": 4, "already_present": 0, "removed_from_yaml": 4,
        "bundle_items_kept": 0}
    data = yaml.safe_load((tmp_path / "config" / "secrets.yaml").read_text())
    assert data == {"platform": {"existing": "platform-value"}}
    fresh = _manager(tmp_path)
    assert await fresh.get_secret(_key(key="google.refresh_token")) == "synthetic-a"
    assert await fresh.get_secret(_key(bundle=OTHER, key="k")) == "synthetic-c"
    assert await fresh.get_secret(_key(user="user-2", key="k")) == "synthetic-d"
    assert await fresh.migrate_user_secrets() == {"found": 0, "user_found": 0, "app_found": 0, "written": 0,
                                                  "already_present": 0, "removed_from_yaml": 0,
                                                  "bundle_items_kept": 0}


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
    assert await manager.migrate_user_secrets() == {"found": 4, "user_found": 4, "app_found": 0, "written": 0,
                                                    "already_present": 4, "removed_from_yaml": 4,
                                                    "bundle_items_kept": 0}
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


def test_the_host_command_migrates_a_config_folder_and_prints_counts_only(tmp_path, capsys):
    from kdcube_ai_app.infra.secrets.user_secret_files import main

    _yaml_with_users(tmp_path, SOURCE)
    config = str(tmp_path / "config")
    assert main(["migrate", "--config-dir", config, "--dry-run"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["would_write"] == 4 and dry["written"] == 0 and "root" not in dry  # counts only
    assert main(["migrate", "--config-dir", config]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["written"] == 4 and "synthetic-" not in output
    assert (_root(tmp_path) / HUB / "users" / "user-1" / "other.json").exists()
    assert main(["migrate", "--config-dir", str(tmp_path / "missing")]) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "global_secrets_yaml_not_found"


# W670 extension (operator: "the persons secrets and the stuff stored in bundles.secrets.yaml is now read from
# folders"): app secrets live in <root>/<bundle>/<key path>.json.

def _app_key(bundle=HUB, key="google.client_secret"):
    return f"bundles.{bundle}.secrets.{key}"


@pytest.mark.asyncio
async def test_app_secrets_are_one_private_file_each_and_never_in_yaml(tmp_path):
    manager = _manager(tmp_path, bundle_secrets_yaml=(tmp_path / "config" / "bundles.secrets.yaml").as_uri())
    await manager.set_secret(_app_key(), "synthetic-app")
    await manager.set_secret(_app_key(bundle="kdcube.copilot@2026-04-03", key="users"), "synthetic-dotted")
    await manager.set_secret(_app_key(key="card-credentials"), "synthetic-purpose-name")
    record = _root(tmp_path) / HUB / "google.client_secret.json"
    assert json.loads(record.read_text()) == {"value": "synthetic-app"}
    assert record.stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / "config" / "bundles.secrets.yaml").exists()
    assert await _manager(tmp_path).get_secret(_app_key()) == "synthetic-app"
    assert await manager.get_secret(_app_key(bundle="kdcube.copilot@2026-04-03", key="users")) == "synthetic-dotted"
    # Records end in .json; users/ and runtime purpose folders are directories, so names never collide.
    await manager.set_secret(_key(key="k"), "synthetic-user")
    assert json.loads(await manager.get_secret(f"bundles.{HUB}.secrets.__keys")) == sorted(
        [_app_key(), _app_key(key="card-credentials")])
    assert set(json.loads(await manager.get_secret("bundles.__keys"))) == {
        _app_key(), _app_key(key="card-credentials"), _app_key(bundle="kdcube.copilot@2026-04-03", key="users")}
    assert await manager.get_secret(_key(key="k")) == "synthetic-user"
    await manager.delete_secret(_app_key())
    assert await manager.get_secret(_app_key()) is None


@pytest.mark.parametrize("operation", ["set", "delete", "list", "set_app", "delete_app", "list_app"])
def test_an_unsafe_existing_record_is_refused_before_set_delete_or_list(tmp_path, operation):
    store = UserSecretFileStore(root=tmp_path / "secrets")
    app = operation.endswith("app")
    if app:
        store.set_app(bundle_id=HUB, key="k", value="synthetic")
        path = tmp_path / "secrets" / HUB / "k.json"
    else:
        store.set(user_id=USER, bundle_id=HUB, key="k", value="synthetic")
        path = tmp_path / "secrets" / HUB / "users" / USER / "k.json"
    path.chmod(0o644)
    calls = {
        "set": lambda: store.set(user_id=USER, bundle_id=HUB, key="k", value="other"),
        "delete": lambda: store.delete(user_id=USER, bundle_id=HUB, key="k"),
        "list": lambda: store.list_keys(user_id=USER, bundle_id=HUB),
        "set_app": lambda: store.set_app(bundle_id=HUB, key="k", value="other"),
        "delete_app": lambda: store.delete_app(bundle_id=HUB, key="k"),
        "list_app": lambda: store.list_app_keys(bundle_id=HUB),
    }
    with pytest.raises(Exception, match="user_secret_storage_unavailable"):
        calls[operation]()
    assert path.exists() and path.stat().st_mode & 0o777 == 0o644  # neither replaced, removed nor repaired
    path.chmod(0o600)
    os.symlink(path, path.with_name("linked.json"))
    with pytest.raises(Exception, match="user_secret_storage_unavailable"):
        (store.list_app_keys(bundle_id=HUB) if app else store.list_keys(user_id=USER, bundle_id=HUB))


BUNDLES_SOURCE = {"bundles": {"version": "1", "items": [
    {"id": HUB, "secrets": {"google": {"client_secret": "synthetic-app-1"}, "peer": "synthetic-app-2"},
     "note": "kept"},
    {"id": "kdcube.copilot@2026-04-03", "secrets": {"telegram": {"webhook_secret": "synthetic-app-3"}}},
    {"id": "no-secrets@1-0"},
]}}


@pytest.mark.asyncio
async def test_migration_moves_app_and_user_secrets_and_keeps_bundle_items(tmp_path):
    _yaml_with_users(tmp_path, SOURCE)
    bundle_yaml = tmp_path / "config" / "bundles.secrets.yaml"
    bundle_yaml.write_text(yaml.safe_dump(BUNDLES_SOURCE))
    manager = _manager(tmp_path, bundle_secrets_yaml=bundle_yaml.as_uri())
    counts = await manager.migrate_user_secrets()
    assert counts == {"found": 7, "user_found": 4, "app_found": 3, "written": 7, "already_present": 0,
                      "removed_from_yaml": 7, "bundle_items_kept": 2}
    kept = yaml.safe_load(bundle_yaml.read_text())["bundles"]["items"]
    assert kept == [{"id": HUB, "note": "kept"}, {"id": "kdcube.copilot@2026-04-03"}, {"id": "no-secrets@1-0"}]
    fresh = _manager(tmp_path, bundle_secrets_yaml=bundle_yaml.as_uri())
    assert await fresh.get_secret(_app_key()) == "synthetic-app-1"
    assert await fresh.get_secret("bundles.kdcube.copilot@2026-04-03.secrets.telegram.webhook_secret") == (
        "synthetic-app-3")
    assert (await fresh.migrate_user_secrets())["found"] == 0


@pytest.mark.asyncio
async def test_an_app_destination_conflict_stops_before_any_change(tmp_path):
    _yaml_with_users(tmp_path, SOURCE)
    bundle_yaml = tmp_path / "config" / "bundles.secrets.yaml"
    bundle_yaml.write_text(yaml.safe_dump(BUNDLES_SOURCE))
    before = (bundle_yaml.read_text(), (tmp_path / "config" / "secrets.yaml").read_text())
    UserSecretFileStore(root=_root(tmp_path)).set_app(bundle_id=HUB, key="peer", value="different")
    manager = _manager(tmp_path, bundle_secrets_yaml=bundle_yaml.as_uri())
    with pytest.raises(SecretsManagerError, match="destination_conflict"):
        await manager.migrate_user_secrets()
    assert (bundle_yaml.read_text(), (tmp_path / "config" / "secrets.yaml").read_text()) == before
    assert not (_root(tmp_path) / HUB / "users").exists()


@pytest.mark.asyncio
async def test_a_failed_parent_folder_fsync_leaves_both_yamls_intact(tmp_path, monkeypatch):
    _yaml_with_users(tmp_path, SOURCE)
    bundle_yaml = tmp_path / "config" / "bundles.secrets.yaml"
    bundle_yaml.write_text(yaml.safe_dump(BUNDLES_SOURCE))
    before = (bundle_yaml.read_text(), (tmp_path / "config" / "secrets.yaml").read_text())
    manager = _manager(tmp_path, bundle_secrets_yaml=bundle_yaml.as_uri())
    from kdcube_ai_app.infra.secrets import user_secret_files

    real_fsync, synced = os.fsync, []

    def failing_fsync(descriptor):
        synced.append(os.fstat(descriptor).st_ino)
        if os.fstat(descriptor).st_ino == (tmp_path / "config").stat().st_ino:
            raise OSError("synthetic parent fsync failure")
        return real_fsync(descriptor)

    monkeypatch.setattr(user_secret_files.os, "fsync", failing_fsync)
    with pytest.raises(SecretsManagerError, match="user_secret_storage_unavailable"):
        await manager.migrate_user_secrets()
    monkeypatch.undo()
    assert (tmp_path / "config").stat().st_ino in synced  # the new root's entry was fsynced in its parent
    assert (bundle_yaml.read_text(), (tmp_path / "config" / "secrets.yaml").read_text()) == before


def test_the_host_command_requires_paths_and_prints_only_fixed_reasons(tmp_path, capsys):
    from kdcube_ai_app.infra.secrets.user_secret_files import main

    assert main(["migrate"]) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "config_paths_required"
    config = tmp_path / "config"
    config.mkdir()
    (config / "secrets.yaml").write_text("users: [synthetic-canary-text\n")  # malformed yaml
    assert main(["migrate", "--config-dir", str(config)]) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["reason"] == "migration_unavailable" and "canary" not in output
    assert main(["migrate", "--global-secrets-yaml", str(config / "secrets.yaml"),
                 "--bundle-secrets-yaml", str(config / "missing.yaml")]) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "bundle_secrets_yaml_not_found"


def test_the_host_command_prints_only_enumerated_reasons(tmp_path, capsys, monkeypatch):
    from kdcube_ai_app.infra.secrets.manager import SecretsManagerWriteError
    from kdcube_ai_app.infra.secrets.user_secret_files import main

    _yaml_with_users(tmp_path, SOURCE)

    async def refuse(self, *, dry_run=False):
        raise SecretsManagerWriteError("synthetic_private_canary")

    monkeypatch.setattr(SecretsFileSecretsManager, "migrate_user_secrets", refuse)
    assert main(["migrate", "--config-dir", str(tmp_path / "config")]) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["reason"] == "migration_unavailable" and "canary" not in output

    async def conflict(self, *, dry_run=False):
        raise SecretsManagerWriteError("user_secret_migration_destination_conflict")

    monkeypatch.setattr(SecretsFileSecretsManager, "migrate_user_secrets", conflict)
    assert main(["migrate", "--config-dir", str(tmp_path / "config")]) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "user_secret_migration_destination_conflict"


# The live share is the host's case-insensitive APFS: names must stay unique under case folding (review F1).

@pytest.mark.parametrize("left,right", [("Token", "token"), ("Alice", "alice"), ("ABC", "abc"), ("aB", "Ab"),
                                        ("J", "%4a"), ("%4A", "j")])
def test_encoded_names_are_unique_under_case_folding(left, right):
    assert encode_segment(left).casefold() != encode_segment(right).casefold()
    for text in (left, right):
        assert decode_segment(encode_segment(text)) == text


@pytest.mark.asyncio
async def test_case_differing_keys_and_users_stay_separate_files(tmp_path):
    manager = _manager(tmp_path)
    await manager.set_secret(_app_key(key="Token"), "synthetic-upper")
    await manager.set_secret(_app_key(key="token"), "synthetic-lower")
    await manager.set_secret(_key(user="Alice", key="k"), "synthetic-alice-upper")
    await manager.set_secret(_key(user="alice", key="k"), "synthetic-alice-lower")
    assert await manager.get_secret(_app_key(key="Token")) == "synthetic-upper"
    assert await manager.get_secret(_app_key(key="token")) == "synthetic-lower"
    assert await manager.get_secret(_key(user="Alice", key="k")) == "synthetic-alice-upper"
    assert await manager.get_secret(_key(user="alice", key="k")) == "synthetic-alice-lower"
    for folder in (_root(tmp_path) / HUB, _root(tmp_path) / HUB / "users"):
        names = [name for name in os.listdir(folder)]
        assert len({name.casefold() for name in names}) == len(names)


@pytest.mark.parametrize("bundle", ["Hub@1-0", "CONNECTION-HUB@1-0"])
def test_uppercase_bundle_ids_are_refused(tmp_path, bundle):
    with pytest.raises(Exception, match="user_secret_bundle_invalid"):
        UserSecretFileStore(root=tmp_path / "secrets").set_app(bundle_id=bundle, key="k", value="v")
    with pytest.raises(RuntimeFileError, match="runtime_secret_owner_invalid"):
        RuntimeFileStore(root=tmp_path / "r", namespace="card-credentials",
                         authorized_namespaces=("card-credentials",), owner=bundle)
    assert not (tmp_path / "secrets").exists()


@pytest.mark.asyncio
async def test_migration_keeps_case_differing_keys_and_users_apart(tmp_path):
    _yaml_with_users(tmp_path, {
        "Alice": {"bundles": {HUB: {"secrets": {"Token": "synthetic-1", "token": "synthetic-2"}}}},
        "alice": {"bundles": {HUB: {"secrets": {"Token": "synthetic-3"}}}},
    })
    manager = _manager(tmp_path)
    counts = await manager.migrate_user_secrets()
    assert counts["written"] == 3
    fresh = _manager(tmp_path)
    assert await fresh.get_secret(_key(user="Alice", key="Token")) == "synthetic-1"
    assert await fresh.get_secret(_key(user="Alice", key="token")) == "synthetic-2"
    assert await fresh.get_secret(_key(user="alice", key="Token")) == "synthetic-3"


# Second review F2 / N1 / N2.

def test_bundle_ids_with_a_secrets_segment_are_refused(tmp_path):
    for bundle in ("acme.secrets.v1", "acme.secrets", "secrets.acme"):
        with pytest.raises(Exception, match="user_secret_bundle_invalid"):
            UserSecretFileStore(root=tmp_path / "secrets").set_app(bundle_id=bundle, key="k", value="v")


def test_unavailable_sandbox_records_log_one_value_free_warning(tmp_path, monkeypatch, caplog):
    import logging as _logging

    from kdcube_ai_app.infra.config import platform_env
    from kdcube_ai_app.infra.secrets import manager as manager_module

    host = _manager(tmp_path)
    store = UserSecretFileStore(root=_root(tmp_path))
    store.set_app(bundle_id=HUB, key="k", value="synthetic-never-logged")
    (_root(tmp_path) / HUB / "k.json").chmod(0o644)  # an unsafe record makes listing refuse
    monkeypatch.setattr(manager_module, "get_secrets_manager", lambda settings=None: host)
    caplog.set_level(_logging.WARNING)
    exported = {"KDCUBE_RUNTIME_BUNDLES_SECRETS_YAML_B64": "x"}
    assert platform_env._secret_records_payload(exported, bundle_id=None, descriptor_payload_scope=None) is None
    assert "[secrets] sandbox records unavailable reason=" in caplog.text
    assert "synthetic-never-logged" not in caplog.text


def test_the_sandbox_writes_records_independently_and_never_leaks_the_env_var(tmp_path, monkeypatch):
    import base64

    from kdcube_ai_app.apps.chat.sdk.runtime.isolated import py_code_exec_entry

    payload = base64.b64encode(json.dumps({"records": {
        _app_key(): "synthetic-good", "users.u.secrets.bundle-less": "synthetic-bad",
        _key(key="k"): "synthetic-user"}}).encode()).decode()
    runtime_dir = tmp_path / "sandbox"
    runtime_dir.mkdir(mode=0o700)
    logged = []
    log = type("L", (), {"log": lambda self, *a: logged.append(a)})()
    py_code_exec_entry._materialize_secret_records(runtime_dir, log, payload)
    store = UserSecretFileStore(root=runtime_dir / "secrets")
    assert store.get_app(bundle_id=HUB, key="google.client_secret") == "synthetic-good"
    assert store.get(user_id=USER, bundle_id=HUB, key="k") == "synthetic-user"
    assert any("skipped 1" in str(entry) for entry in logged)
    assert not any("synthetic-" in str(entry) for entry in logged)
    # N2: with no yaml copy at all, the records variable still never reaches user code.
    monkeypatch.setenv("KDCUBE_RUNTIME_SECRET_RECORDS_B64", payload)
    for name in ("KDCUBE_RUNTIME_ASSEMBLY_YAML_B64", "KDCUBE_RUNTIME_BUNDLES_YAML_B64", "KDCUBE_RUNTIME_GATEWAY_YAML_B64",
                 "KDCUBE_RUNTIME_SECRETS_YAML_B64", "KDCUBE_RUNTIME_BUNDLES_SECRETS_YAML_B64"):
        monkeypatch.delenv(name, raising=False)
    assert py_code_exec_entry._materialize_runtime_descriptor_payloads(log) is None
    assert "KDCUBE_RUNTIME_SECRET_RECORDS_B64" not in os.environ
