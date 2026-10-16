# SPDX-License-Identifier: MIT
"""W885: `kdcube config export --include-platform-descriptors` carries the runtime secrets folder.

Operator, 2026-10-16: the export is "something that can be used to bootstrap the configuration part. and no.
not the tokens that conn hub acuqires", with user data behind "include-user-data". The fixture is written
with the stores the services use, exported through the CLI, then read back through the same stores.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest
import yaml

from kdcube_cli import cli as cli_mod
from kdcube_cli.runtime_secrets_export import RuntimeSecretsExportError, export_runtime_secrets_folder
from kdcube_ai_app.infra.secrets.runtime_file import RuntimeFileStore
from kdcube_ai_app.infra.secrets.user_secret_files import UserSecretFileStore

from test_cli_descriptors import _write_initialized_runtime_config

APP = {("demo.bundle", "provider.token"): "app-canary-1", ("other.bundle", "api.key"): "app-canary-2"}
USERS = {("person-1", "demo.bundle", "github.token"): "user-canary-1",
         ("Person.Two@example.test", "other.bundle", "email.accounts.acct.tokens"): "user-canary-2"}
ACQUIRED = {("person-1", "demo.bundle", "connections.accounts.gmail.tokens"): "acquired-canary-1",
            ("person-1", "demo.bundle", "delegated_to_kdcube.grant"): "acquired-canary-2",
            ("Person.Two@example.test", "other.bundle", "remote_mcp.ref"): "acquired-canary-3",
            ("person-1", "other.bundle", "linkedin.accounts.acct.tokens"): "acquired-canary-4"}
RECORD = "record-canary"
PROPS = [{"user_id": "person-1", "bundle_id": "demo.bundle", "key": "theme", "subsystem": "bundle",
          "value_json": '{"dark": true, "size": 0, "tags": ["a"], "note": null}'},
         {"user_id": "person-1", "bundle_id": "demo.bundle", "key": "looks.like.json", "subsystem": "bundle",
          "value_json": '"{\\"not\\": \\"an object\\"}"'}]
NEVER = [*ACQUIRED.values(), RECORD]
SHOWN = [*APP.values(), *USERS.values()]


def _runtime(tmp_path: Path) -> tuple[Path, Path, Path]:
    workdir = tmp_path / "demo__project"
    config_dir = _write_initialized_runtime_config(workdir)
    root = config_dir / "secrets"
    root.mkdir(mode=0o700)
    files = UserSecretFileStore(root=root)
    for (bundle, key), value in APP.items():
        files.set_app(bundle_id=bundle, key=key, value=value)
    for (user, bundle, key), value in {**USERS, **ACQUIRED}.items():
        files.set(user_id=user, bundle_id=bundle, key=key, value=value)
    records = RuntimeFileStore(root=root, namespace="oauth-refresh-tokens",
                               authorized_namespaces=["oauth-refresh-tokens"], owner="demo-bundle")
    assert records.create(secret_ref="0" * 32, value=RECORD, expires_at=4_102_444_800)
    (root / ".DS_Store").write_bytes(b"desktop metadata")
    return workdir, config_dir, root


def _export(monkeypatch, workdir: Path, config_dir: Path, output: Path, *extra: str) -> None:
    monkeypatch.setattr(cli_mod, "_load_cli_defaults", lambda: {})
    monkeypatch.setattr(cli_mod, "_resolve_subcommand_workdir", lambda *_a, **_k: workdir)
    monkeypatch.setattr(cli_mod, "_resolve_cli_workdir", lambda path, **_k: Path(path))
    monkeypatch.setattr(cli_mod, "_canonical_descriptor_dir_from_initialized_workdir", lambda _w: config_dir)
    monkeypatch.setattr(cli_mod, "_parse_workdir_namespace", lambda _w: ("demo", "project"))
    monkeypatch.setattr(cli_mod, "_resolve_subcommand_repo", lambda *_a, **_k: workdir / "repo")
    monkeypatch.setattr(cli_mod, "_read_user_props", lambda **_k: [dict(row) for row in PROPS])
    monkeypatch.setattr(sys, "argv", ["kdcube", "config", "export", "--workdir", str(workdir), "--out-dir",
                                      str(output), "--include-platform-descriptors", *extra])
    cli_mod.main()


def _all_bytes(root: Path) -> bytes:
    return b"".join(path.read_bytes() for path in root.rglob("*") if path.is_file())


def _assert_nothing_named(text: str) -> None:
    for value in [*SHOWN, *NEVER]:
        assert value not in text
    for name in ("person-1", "Person.Two", "provider.token", "github.token"):
        assert name not in text


def test_app_and_user_secrets_arrive_byte_for_byte_and_acquired_tokens_never(monkeypatch, tmp_path, capsys):
    workdir, config_dir, root = _runtime(tmp_path)
    output = tmp_path / "export"
    _export(monkeypatch, workdir, config_dir, output, "--include-user-data")

    exported = output / "secrets"
    files = UserSecretFileStore(root=exported)
    for (bundle, key), value in APP.items():
        assert files.get_app(bundle_id=bundle, key=key) == value
        assert files.app_path(bundle_id=bundle, key=key).read_bytes() == (
            UserSecretFileStore(root=root).app_path(bundle_id=bundle, key=key).read_bytes())
    for (user, bundle, key), value in USERS.items():
        assert files.get(user_id=user, bundle_id=bundle, key=key) == value
    for (user, bundle, key), _value in ACQUIRED.items():
        assert files.get(user_id=user, bundle_id=bundle, key=key) is None
    assert not (exported / "demo-bundle").exists(), "runtime-record folders are not configuration"
    everything = _all_bytes(output)
    for value in NEVER:
        assert value.encode() not in everything
    _assert_nothing_named(capsys.readouterr().out)


def test_app_secrets_are_also_in_bundles_secrets_yaml_for_a_provider_backed_import(monkeypatch, tmp_path):
    workdir, config_dir, _root = _runtime(tmp_path)
    output = tmp_path / "export"
    _export(monkeypatch, workdir, config_dir, output)
    items = {item["id"]: item for item in yaml.safe_load((output / "bundles.secrets.yaml").read_text())["bundles"]["items"]}
    assert items["demo.bundle"]["secrets"]["provider"]["token"] == "app-canary-1"
    assert items["other.bundle"]["secrets"]["api"]["key"] == "app-canary-2"
    assert "users" not in json.dumps(items), "user secrets never go into the descriptor"


def test_without_the_flag_no_user_secret_and_no_props_are_written(monkeypatch, tmp_path, capsys):
    workdir, config_dir, _root = _runtime(tmp_path)
    output = tmp_path / "export"
    _export(monkeypatch, workdir, config_dir, output)
    assert not any(part == "users" for path in (output / "secrets").rglob("*") for part in path.parts)
    assert not (output / "user-data").exists()
    everything = _all_bytes(output)
    for value in [*USERS.values(), *NEVER]:
        assert value.encode() not in everything
    out = capsys.readouterr().out
    assert "per-user secrets left out: 2" in out
    _assert_nothing_named(out)


def test_props_keep_their_exact_json_and_subsystem_and_are_private(monkeypatch, tmp_path, capsys):
    workdir, config_dir, _root = _runtime(tmp_path)
    output = tmp_path / "export"
    _export(monkeypatch, workdir, config_dir, output, "--include-user-data", "--json")
    path = output / "user-data" / "user_bundle_props.json"
    document = json.loads(path.read_text())
    assert document == {"schema": "kdcube.export.user_bundle_props.v1", "rows": PROPS}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    text = capsys.readouterr().out
    entries = {item["name"]: item for item in json.loads(text)["files"]}
    assert entries["user-data/user_bundle_props.json"]["row_count"] == 2
    assert entries["secrets/"]["acquired_tokens_left_out"] == len(ACQUIRED)
    assert "theme" not in text and "dark" not in text
    _assert_nothing_named(text)


def test_folders_are_0700_files_0600_and_nothing_is_a_link(monkeypatch, tmp_path):
    workdir, config_dir, _root = _runtime(tmp_path)
    output = tmp_path / "export"
    _export(monkeypatch, workdir, config_dir, output, "--include-user-data")
    for current, folders, files in os.walk(output / "secrets"):
        assert stat.S_IMODE(os.lstat(current).st_mode) == 0o700
        for name in files:
            info = (Path(current) / name).lstat()
            assert stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
    assert not (output / "secrets" / ".DS_Store").exists()


def test_a_link_in_the_folder_refuses_the_export_without_naming_it(tmp_path):
    root = tmp_path / "secrets-root"
    (root / "demo.bundle").mkdir(parents=True, mode=0o700)
    (root / "demo.bundle" / "linked.json").symlink_to(tmp_path / "elsewhere.json")
    with pytest.raises(RuntimeSecretsExportError) as refused:
        export_runtime_secrets_folder(root, tmp_path / "out", include_user_data=True)
    assert "linked" not in str(refused.value) and str(root) not in str(refused.value)


def test_an_existing_secrets_folder_in_the_output_is_not_merged(tmp_path):
    root = tmp_path / "secrets-root"
    root.mkdir(mode=0o700)
    (tmp_path / "out" / "secrets").mkdir(parents=True)
    with pytest.raises(RuntimeSecretsExportError):
        export_runtime_secrets_folder(root, tmp_path / "out", include_user_data=True)


def test_the_flag_is_refused_outside_a_secrets_file_platform_export(monkeypatch, tmp_path):
    workdir, config_dir, _root = _runtime(tmp_path)
    monkeypatch.setattr(cli_mod, "_load_cli_defaults", lambda: {})
    monkeypatch.setattr(cli_mod, "_resolve_subcommand_workdir", lambda *_a, **_k: workdir)
    monkeypatch.setattr(cli_mod, "_resolve_cli_workdir", lambda path, **_k: Path(path))
    monkeypatch.setattr(cli_mod, "_canonical_descriptor_dir_from_initialized_workdir", lambda _w: config_dir)
    monkeypatch.setattr(cli_mod, "_parse_workdir_namespace", lambda _w: ("demo", "project"))
    monkeypatch.setattr(sys, "argv", ["kdcube", "config", "export", "--workdir", str(workdir), "--out-dir",
                                      str(tmp_path / "export"), "--include-user-data"])
    with pytest.raises(SystemExit, match="--include-user-data applies to"):
        cli_mod.main()
