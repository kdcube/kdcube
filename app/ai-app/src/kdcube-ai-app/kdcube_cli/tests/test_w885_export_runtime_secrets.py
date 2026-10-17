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

# Self-contained (README: a sibling test module imported by bare name does not collect under importlib).


def _write_initialized_runtime_config(
    workdir: Path,
    *,
    marker: str = "old",
    secrets_provider: str = "secrets-file",
) -> Path:
    config_dir = workdir / "config"
    config_dir.mkdir(parents=True)
    (workdir / "bundles" / "demo.bundle").mkdir(parents=True)
    (config_dir / "install-meta.json").write_text(
        json.dumps(
            {
                "tenant": "demo",
                "project": "project",
                "repo_root": str(workdir / "repo"),
            }
        )
    )
    (config_dir / "assembly.yaml").write_text(
        yaml.safe_dump(
            {
                "context": {"tenant": "demo", "project": "project"},
                "platform": {"repo": "https://github.com/example/platform.git", "ref": marker},
                "secrets": {"provider": secrets_provider},
                "auth": {"type": "simple"},
                "proxy": {"ssl": False},
                "paths": {"host_bundles_path": str(workdir / "bundles")},
            },
            sort_keys=False,
        )
    )
    (config_dir / "secrets.yaml").write_text(
        f"platform:\n  services:\n    marker: {marker}\n"
    )
    (config_dir / "gateway.yaml").write_text(f"routes:\n  marker: {marker}\n")
    (config_dir / "economics.yaml").write_text(f"economics:\n  marker: {marker}\n")
    (config_dir / "bundles.yaml").write_text(
        yaml.safe_dump(
            {
                "bundles": {
                    "version": "1",
                    "items": [{"id": "demo.bundle", "path": "/bundles/demo.bundle", "module": "entrypoint"}],
                }
            },
            sort_keys=False,
        )
    )
    (config_dir / "bundles.secrets.yaml").write_text(
        yaml.safe_dump(
            {"bundles": {"version": "1", "items": [{"id": "demo.bundle", "secrets": {"api": {"key": marker}}}]}},
            sort_keys=False,
        )
    )
    return config_dir

APP = {("demo.bundle", "provider.token"): "app-canary-1", ("other.bundle", "api.key"): "app-canary-2"}
USERS = {("person-1", "demo.bundle", "github.token"): "user-canary-1",
         ("person-two", "other.bundle", "email.accounts.acct.tokens"): "user-canary-2"}
ACQUIRED = {("person-1", "demo.bundle", "connections.accounts.gmail.tokens"): "acquired-canary-1",
            ("person-1", "demo.bundle", "delegated_to_kdcube.grant"): "acquired-canary-2",
            ("person-two", "other.bundle", "remote_mcp.ref"): "acquired-canary-3",
            ("person-1", "other.bundle", "linkedin.accounts.acct.tokens"): "acquired-canary-4"}
RECORD = "record-canary"
LEGACY_YAML_USERS = {"person-1": {"secrets": {"connections": {"accounts": {"gmail": {"tokens": "legacy-acquired-canary"}}}},
                                  "bundles": {"demo.bundle": {"secrets": {"api": {"key": "legacy-user-canary"}}}}}}
PROPS = [{"user_id": "person-1", "bundle_id": "demo.bundle", "key": "theme", "subsystem": "bundle",
          "value_json": '{"dark": true, "size": 0, "tags": ["a"], "note": null}'},
         {"user_id": "person-1", "bundle_id": "demo.bundle", "key": "looks.like.json", "subsystem": "bundle",
          "value_json": '"{\\"not\\": \\"an object\\"}"'}]
NEVER = [*ACQUIRED.values(), RECORD, "legacy-acquired-canary", "legacy-user-canary"]
ACQUIRED_ALWAYS = [*ACQUIRED.values(), RECORD, "legacy-acquired-canary"]
SHOWN = [*APP.values(), *USERS.values()]


def _runtime(tmp_path: Path) -> tuple[Path, Path, Path]:
    workdir = tmp_path / "demo__project"
    config_dir = _write_initialized_runtime_config(workdir)
    # A legacy secrets.yaml still holding users (a supported descriptor shape): never part of the bootstrap.
    (config_dir / "secrets.yaml").write_text(yaml.safe_dump(
        {"platform": {"services": {"marker": "kept"}}, "users": LEGACY_YAML_USERS}), encoding="utf-8")
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
    for name in ("person-1", "person-two", "provider.token", "github.token"):
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
    for value in ACQUIRED_ALWAYS:
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
    platform = yaml.safe_load((output / "secrets.yaml").read_text())
    assert platform == {"platform": {"services": {"marker": "kept"}}}, "the legacy users root is left out"


def test_without_the_flag_the_legacy_yaml_users_are_in_no_exported_file(monkeypatch, tmp_path, capsys):
    workdir, config_dir, _root = _runtime(tmp_path)
    output = tmp_path / "export"
    _export(monkeypatch, workdir, config_dir, output)
    everything = _all_bytes(output)
    for value in NEVER:
        assert value.encode() not in everything
    assert "user entries of the legacy secrets.yaml left out: 1" in capsys.readouterr().out


def test_with_the_flag_the_legacy_yaml_users_go_out_without_acquired_tokens(monkeypatch, tmp_path):
    workdir, config_dir, _root = _runtime(tmp_path)
    output = tmp_path / "export"
    _export(monkeypatch, workdir, config_dir, output, "--include-user-data")
    platform = yaml.safe_load((output / "secrets.yaml").read_text())
    # W890: the legacy entry is kept and the folder's per-user values join it (the same values as secrets/).
    assert platform["users"]["person-1"]["bundles"]["demo.bundle"]["secrets"] == {
        "api": {"key": "legacy-user-canary"}, "github": {"token": "user-canary-1"}}
    assert platform["users"]["person-1"]["secrets"] == {}
    assert b"legacy-acquired-canary" not in _all_bytes(output)


def test_a_key_with_a_key_below_it_refuses_rather_than_drop_one():
    """W890: the descriptors are nested, as aws-sm reads them; token and token.child cannot both be nested,
    so the export refuses (never drops one). dev-main had 0 such pairs among 54 keys on 2026-10-16."""
    from kdcube_cli.runtime_secrets_export import RuntimeSecretsExportError, merge_app_secrets

    with pytest.raises(RuntimeSecretsExportError, match="one is below the other"):
        merge_app_secrets({}, {"demo.bundle": {"token": "canary-parent", "token.child": "canary-child"}})


def test_an_older_yaml_value_is_flattened_and_the_folder_value_wins():
    from kdcube_cli.runtime_secrets_export import merge_app_secrets

    merged = merge_app_secrets({"bundles": {"version": "1", "items": [
        {"id": "demo.bundle", "secrets": {"provider": {"token": "old", "region": "eu"}}}]}},
        {"demo.bundle": {"provider.token": "new"}})
    assert merged["bundles"]["items"][0]["secrets"] == {"provider": {"region": "eu", "token": "new"}}


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
    assert entries["secrets/"]["acquired_tokens_left_out"] == len(ACQUIRED) + 1, "the folder plus the legacy YAML"
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


def test_a_refused_folder_export_leaves_no_unflagged_legacy_user_secret_behind(monkeypatch, tmp_path):
    """Review on #380: secrets.yaml is filtered while it is written, before the folder export can refuse."""
    workdir, config_dir, _root = _runtime(tmp_path)
    output = tmp_path / "export"
    (output / "secrets").mkdir(parents=True)
    with pytest.raises(SystemExit):
        _export(monkeypatch, workdir, config_dir, output)
    left = _all_bytes(output)
    assert b"legacy-user-canary" not in left and b"legacy-acquired-canary" not in left


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


@pytest.mark.parametrize("flag", [(), ("--include-user-data",)])
def test_a_provider_source_gives_the_same_output_as_the_secrets_file_source(monkeypatch, tmp_path, flag):
    """Operator, 2026-10-16: "this must be simply equal and the same code for any export"."""
    from types import SimpleNamespace

    from kdcube_cli.management.models import ManagementSecretTarget
    from kdcube_cli.management.secret_export import ExportedSecret

    workdir, config_dir, _root = _runtime(tmp_path)
    # Since the move to folders the app secrets live in the folder, as they do in a provider.
    (config_dir / "bundles.secrets.yaml").write_text(yaml.safe_dump({"bundles": {"version": "1", "items": []}}))
    file_out = tmp_path / "from-file"
    _export(monkeypatch, workdir, config_dir, file_out, *flag)

    values = [ExportedSecret(target=ManagementSecretTarget.create(scope="bundle", bundle_id=b, key=k), value=v)
              for (b, k), v in APP.items()]
    values += [ExportedSecret(target=ManagementSecretTarget.create(scope="user", user_id=u, bundle_id=b, key=k),
                              value=v) for (u, b, k), v in USERS.items()] if flag else []

    async def bootstrap(**kwargs):
        assert kwargs["user_data"] is bool(flag)
        return SimpleNamespace(request_digest="a" * 64, assurance="session_confirmation",
                               approval_method="browser_session", approval_verified_at=1,
                               values=tuple(values), user_props=tuple(dict(row) for row in PROPS) if flag else ())

    provider_config = _write_initialized_runtime_config(tmp_path / "vault__project", secrets_provider="secrets-service")
    provider_out = tmp_path / "from-provider"
    monkeypatch.setattr(cli_mod, "local_management_target", lambda _w: object())
    monkeypatch.setattr(cli_mod, "export_bootstrap_values", bootstrap)
    _export(monkeypatch, tmp_path / "vault__project", provider_config, provider_out, *flag)

    assert _tree_bytes(provider_out / "secrets") == _tree_bytes(file_out / "secrets")
    def app_blocks(out):
        items = yaml.safe_load((out / "bundles.secrets.yaml").read_text())["bundles"]["items"]
        return {item["id"]: item.get("secrets") for item in items if item.get("secrets")}
    assert app_blocks(provider_out) == app_blocks(file_out)
    if flag:
        assert (provider_out / "user-data" / "user_bundle_props.json").read_bytes() == (
            file_out / "user-data" / "user_bundle_props.json").read_bytes()
    else:
        assert not (provider_out / "user-data").exists()


def test_the_cli_folder_encoding_is_the_file_store_encoding():
    from kdcube_ai_app.infra.secrets.user_secret_files import encode_segment as store_encoding
    from kdcube_cli.runtime_secrets_export import encode_segment

    for name in ["github.token", "Person.Two@example.test", ".hidden", "ünïcode key", "a/b\\c", "UPPER_case-1"]:
        assert encode_segment(name) == store_encoding(name)


def _provider_export(monkeypatch, tmp_path, *extra, prepare=None):
    """A provider-backed export whose exchange records whether it was called."""
    from types import SimpleNamespace

    calls = []

    async def bootstrap(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(request_digest="a" * 64, assurance="session_confirmation",
                               approval_method="browser_session", approval_verified_at=1, values=(), user_props=())

    workdir = tmp_path / "vault__project"
    config_dir = _write_initialized_runtime_config(workdir, secrets_provider="secrets-service")
    output = tmp_path / "out"
    output.mkdir()
    if prepare:
        prepare(output)
    monkeypatch.setattr(cli_mod, "local_management_target", lambda _w: object())
    monkeypatch.setattr(cli_mod, "export_bootstrap_values", bootstrap)
    return calls, lambda: _export(monkeypatch, workdir, config_dir, output, *extra), output


def test_an_existing_descriptor_pair_refuses_before_anything_is_disclosed(monkeypatch, tmp_path):
    """codex-infra on #381: no overwrite without --replace-descriptor-files, checked before the exchange."""
    calls, run, output = _provider_export(
        monkeypatch, tmp_path, prepare=lambda out: (out / "secrets.yaml").write_text("old: pair\n"))
    with pytest.raises(SystemExit):
        run()
    assert calls == [] and (output / "secrets.yaml").read_text() == "old: pair\n"


def test_replace_descriptor_files_allows_replacing_the_pair(monkeypatch, tmp_path):
    calls, run, output = _provider_export(
        monkeypatch, tmp_path, "--replace-descriptor-files",
        prepare=lambda out: (out / "secrets.yaml").write_text("old: pair\n"))
    run()
    assert len(calls) == 1 and (output / "secrets.yaml").read_text() != "old: pair\n"


@pytest.mark.parametrize("conflict", ["link", "secrets", "props"])
def test_a_linked_pair_or_existing_output_refuses_before_the_exchange(monkeypatch, tmp_path, conflict):
    def prepare(out):
        if conflict == "link":
            (out / "bundles.secrets.yaml").symlink_to(tmp_path / "elsewhere.yaml")
        elif conflict == "secrets":
            (out / "secrets").mkdir()
        else:
            (out / "user-data").mkdir()
            (out / "user-data" / "user_bundle_props.json").write_text("{}")
    calls, run, _output = _provider_export(monkeypatch, tmp_path, "--include-user-data", "--replace-descriptor-files",
                                           prepare=prepare)
    with pytest.raises(SystemExit):
        run()
    assert calls == [], "nothing is disclosed when the destination is wrong"


@pytest.mark.parametrize("flag", [(), ("--include-user-data",)])
def test_a_bundle_less_legacy_user_secret_follows_the_flag_and_an_acquired_one_never_goes(monkeypatch, tmp_path, flag):
    """EMain, W885: a user secret with no app in a legacy secrets.yaml goes out only with --include-user-data,
    and a Connection Hub acquired token never does, bundle-less or not."""
    workdir, config_dir, _root = _runtime(tmp_path)
    (config_dir / "secrets.yaml").write_text(yaml.safe_dump({"platform": {"services": {"marker": "kept"}}, "users": {
        "person-1": {"secrets": {"provider": {"token": "bundle-less-ordinary-canary"},
                                 "delegated_to_kdcube": {"grant": "bundle-less-acquired-canary"}}}}}), encoding="utf-8")
    output = tmp_path / "export"
    _export(monkeypatch, workdir, config_dir, output, *flag)
    everything = _all_bytes(output)
    assert b"bundle-less-acquired-canary" not in everything
    platform = yaml.safe_load((output / "secrets.yaml").read_text())
    if flag:
        assert platform["users"]["person-1"]["secrets"] == {"provider": {"token": "bundle-less-ordinary-canary"}}
    else:
        assert "users" not in platform and b"bundle-less-ordinary-canary" not in everything


# --- W890: the exported pair is the cloud secrets bootstrap (SECRETS_YAML and BUNDLES_SECRETS_YAML) ---------

import re as _re

_INTAKE_ID = _re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.@-]{0,511}")


def _intake_identifier(value):
    text = str(value or "").strip()
    assert (_INTAKE_ID.fullmatch(text) and ".." not in text and ".bundles." not in text
            and ".secrets." not in text), f"identifier the intake refuses: {text!r}"
    return text


def _assert_cloud_intake_accepts(secrets_doc: dict, bundles_doc: dict) -> None:
    """The ECS secrets intake's validation rules (it writes the two descriptors into AWS Secrets Manager):
    secrets.yaml has only a platform mapping and an optional users mapping; a user entry has only secrets
    and/or bundles; a user application entry has exactly one secrets mapping; bundles.secrets.yaml has a
    bundles mapping whose items are mappings with a unique id and a secrets mapping."""
    assert set(secrets_doc) <= {"platform", "users"} and isinstance(secrets_doc.get("platform"), dict), set(secrets_doc)
    for user_id, entry in (secrets_doc.get("users") or {}).items():
        _intake_identifier(user_id)
        assert isinstance(entry, dict) and entry and not set(entry) - {"secrets", "bundles"}, entry.keys()
        if "secrets" in entry:
            assert isinstance(entry["secrets"], dict)
        for bundle_id, bundle_entry in (entry.get("bundles") or {}).items():
            _intake_identifier(bundle_id)
            assert set(bundle_entry) == {"secrets"} and isinstance(bundle_entry["secrets"], dict)
    root = bundles_doc.get("bundles")
    assert isinstance(root, dict) and isinstance(root.get("items", []), list)
    ids = []
    for item in root.get("items", []):
        assert isinstance(item, dict) and isinstance(item.get("secrets", {}), dict) and item.get("id")
        ids.append(_intake_identifier(item["id"]))
    assert len(ids) == len(set(ids)), "bundle secret ids must be unique"


def _resolves(document: dict, key: str):
    """aws-sm's own nested lookup (manager._resolve_nested_value) over one stored JSON document."""
    from kdcube_ai_app.infra.secrets.manager import _resolve_nested_value

    return _resolve_nested_value(document, key)


def test_with_the_flag_the_users_root_holds_exactly_what_the_secrets_folder_holds(monkeypatch, tmp_path):
    workdir, config_dir, _root = _runtime(tmp_path)
    (config_dir / "secrets.yaml").write_text(yaml.safe_dump({"platform": {"services": {"marker": "kept"}}}))
    output = tmp_path / "export"
    _export(monkeypatch, workdir, config_dir, output, "--include-user-data")
    users = yaml.safe_load((output / "secrets.yaml").read_text())["users"]
    folder = UserSecretFileStore(root=output / "secrets")
    for (user, bundle, key), value in USERS.items():
        assert folder.get(user_id=user, bundle_id=bundle, key=key) == value
        assert _resolves(users[user]["bundles"][bundle]["secrets"], key) == value, "YAML and folder agree"
    yaml_pairs = {(user, bundle) for user, entry in users.items() for bundle in (entry.get("bundles") or {})}
    assert yaml_pairs == {(user, bundle) for (user, bundle, _key) in USERS}, "nothing extra in the YAML"


@pytest.mark.parametrize("flag", [(), ("--include-user-data",)])
def test_the_file_export_pair_passes_the_cloud_intake_rules_and_every_value_resolves(monkeypatch, tmp_path, flag):
    workdir, config_dir, _root = _runtime(tmp_path)
    (config_dir / "secrets.yaml").write_text(yaml.safe_dump({"platform": {"services": {"marker": "kept"}}}))
    output = tmp_path / "export"
    _export(monkeypatch, workdir, config_dir, output, *flag)
    secrets_doc = yaml.safe_load((output / "secrets.yaml").read_text())
    bundles_doc = yaml.safe_load((output / "bundles.secrets.yaml").read_text())
    _assert_cloud_intake_accepts(secrets_doc, bundles_doc)
    items = {item["id"]: item.get("secrets") or {} for item in bundles_doc["bundles"]["items"]}
    for (bundle, key), value in APP.items():
        assert _resolves(items[bundle], key) == value
    assert ("users" in secrets_doc) is bool(flag)
    both = (output / "secrets.yaml").read_bytes() + (output / "bundles.secrets.yaml").read_bytes()
    for value in [*ACQUIRED.values(), RECORD]:
        assert value.encode() not in both


@pytest.mark.parametrize("flag", [(), ("--include-user-data",)])
def test_the_provider_export_pair_passes_the_intake_and_keeps_bundle_less_user_secrets(monkeypatch, tmp_path, flag):
    """W890: a provider user secret with no app has no folder home; it goes to users.<user>.secrets."""
    from types import SimpleNamespace

    from kdcube_cli.management.models import ManagementSecretTarget
    from kdcube_cli.management.secret_export import ExportedSecret

    def secret(scope, key, value, *, bundle_id="", user_id=""):
        return ExportedSecret(target=ManagementSecretTarget.create(scope=scope, key=key, bundle_id=bundle_id,
                                                                   user_id=user_id), value=value)

    values = (secret("platform", "platform.services.fixture.token", "platform-canary"),
              secret("bundle", "provider.token", "app-canary", bundle_id="demo.bundle"),
              secret("user", "github.token", "user-app-canary", bundle_id="demo.bundle", user_id="person-1"),
              secret("user", "provider.token", "user-bundle-less-canary", user_id="person-1"),
              secret("user", "connections.accounts.gmail.tokens", "acquired-canary", user_id="person-1"))

    async def bootstrap(**kwargs):
        return SimpleNamespace(request_digest="a" * 64, assurance="session_confirmation",
                               approval_method="browser_session", approval_verified_at=1,
                               values=values, user_props=(*PROPS, HUB_PROP))

    workdir = tmp_path / "vault__project"
    config_dir = _write_initialized_runtime_config(workdir, secrets_provider="secrets-service")
    monkeypatch.setattr(cli_mod, "local_management_target", lambda _w: object())
    monkeypatch.setattr(cli_mod, "export_bootstrap_values", bootstrap)
    output = tmp_path / "out"
    _export(monkeypatch, workdir, config_dir, output, *flag)
    secrets_doc = yaml.safe_load((output / "secrets.yaml").read_text())
    bundles_doc = yaml.safe_load((output / "bundles.secrets.yaml").read_text())
    _assert_cloud_intake_accepts(secrets_doc, bundles_doc)
    assert _resolves(secrets_doc["platform"], "services.fixture.token") == "platform-canary"
    assert _resolves(bundles_doc["bundles"]["items"][0]["secrets"], "provider.token") == "app-canary"
    everything = _all_bytes(output)
    assert b"acquired-canary" not in everything
    assert b"hub-prop-canary" not in everything, "Connection Hub props never leave, whatever the runtime returns"
    if flag:
        assert json.loads((output / "user-data" / "user_bundle_props.json").read_text())["rows"] == PROPS
        person = secrets_doc["users"]["person-1"]
        assert _resolves(person["secrets"], "provider.token") == "user-bundle-less-canary"
        assert _resolves(person["bundles"]["demo.bundle"]["secrets"], "github.token") == "user-app-canary"
        assert UserSecretFileStore(root=output / "secrets").get(
            user_id="person-1", bundle_id="demo.bundle", key="github.token") == "user-app-canary"
    else:
        assert "users" not in secrets_doc
        assert b"user-bundle-less-canary" not in everything and b"user-app-canary" not in everything


def test_a_refused_nesting_names_both_keys_and_leaves_no_secrets_folder(monkeypatch, tmp_path):
    """claude-ops on #385: the refusal names the keys (never a value), and the export folder it created is
    removed, so a refused export leaves no partial secrets/."""
    workdir, config_dir, root = _runtime(tmp_path)
    (config_dir / "secrets.yaml").write_text(yaml.safe_dump({"platform": {"services": {"marker": "kept"}}}))
    (root / "demo.bundle" / "provider.token.child.json").write_text('{"value": "child-canary"}')
    output = tmp_path / "export"
    with pytest.raises(SystemExit) as refused:
        _export(monkeypatch, workdir, config_dir, output)
    message = str(refused.value)
    assert "'provider.token'" in message and "provider.token.child" in message
    assert "canary" not in message
    assert not (output / "secrets").exists()


# --- Connection Hub's own user props are never exported (operator, 2026-10-17: "3. exclude") --------------

HUB_PROP = {"user_id": "person-1", "bundle_id": "connection-hub@1-0", "key": "delegated_to_kdcube.account_index",
            "subsystem": "bundle", "value_json": '"hub-prop-canary"'}


def test_the_file_export_leaves_connection_hub_props_out(monkeypatch, tmp_path):
    workdir, config_dir, _root = _runtime(tmp_path)
    (config_dir / "secrets.yaml").write_text(yaml.safe_dump({"platform": {"services": {"marker": "kept"}}}))
    output = tmp_path / "export"
    plain = [dict(row) for row in PROPS]
    monkeypatch.setitem(globals(), "PROPS", [*plain, dict(HUB_PROP)])  # what _export's props reader returns
    _export(monkeypatch, workdir, config_dir, output, "--include-user-data")
    document = json.loads((output / "user-data" / "user_bundle_props.json").read_text())
    assert document["rows"] == plain
    assert b"hub-prop-canary" not in _all_bytes(output)


def test_the_helper_counts_what_it_leaves_out():
    from kdcube_cli.runtime_secrets_export import exportable_user_props

    kept, left_out = exportable_user_props([*PROPS, HUB_PROP])
    assert kept == PROPS and left_out == 1



def test_the_props_script_filters_in_the_database_and_gets_the_prefixes(monkeypatch, tmp_path):
    """claude-ops on #388: the Hub rows never reach the host; the processor's SELECT leaves them out."""
    from types import SimpleNamespace

    seen = {}

    def run(command, **_kwargs):
        seen["command"] = command
        return SimpleNamespace(returncode=0, stdout="[]\n", stderr="")

    monkeypatch.setattr(cli_mod.subprocess, "run", run)
    monkeypatch.setattr(cli_mod, "_build_paths_for_repo", lambda *_a: SimpleNamespace(docker_dir=tmp_path))
    monkeypatch.setattr(cli_mod.installer_mod, "compose_env", lambda _e: {})
    assert cli_mod._read_user_props(repo_root=tmp_path, workdir=tmp_path, config_dir=tmp_path) == []
    script, argument = seen["command"][-2], seen["command"][-1]
    assert script == cli_mod._USER_PROPS_EXPORT_SCRIPT
    assert "starts_with(key, prefix)" in script and "excluded," in script
    assert json.loads(argument) == ["delegated_to_kdcube."]


# --- W890: config import --include-user-data inserts the exported props into the running runtime ---------
# Operator, option A ("5. A agree"): insert, never overwrite, report equal and different rows.

def _props_source(tmp_path: Path, rows) -> Path:
    source = tmp_path / "incoming"
    _write_initialized_runtime_config(source.parent / "incoming-runtime", marker="incoming").rename(source)
    (source / "user-data").mkdir()
    (source / "user-data" / "user_bundle_props.json").write_text(
        json.dumps({"schema": "kdcube.export.user_bundle_props.v1", "rows": rows}))
    if os.name == "posix":
        source.chmod(0o700)
        for name in ("secrets.yaml", "bundles.secrets.yaml"):
            (source / name).chmod(0o600)
    return source


def _import(monkeypatch, tmp_path, *flags, provider="secrets-file", rows=None):
    workdir = tmp_path / "demo__project"
    target_config = _write_initialized_runtime_config(workdir, marker="retained", secrets_provider=provider)
    source = _props_source(tmp_path, [*PROPS, HUB_PROP] if rows is None else rows)
    calls = {"bearer": 0, "props": [], "secrets": 0}

    def bearer(_args):
        calls["bearer"] += 1
        return "delegated-test-bearer"

    async def props_import(**kwargs):
        assert kwargs["bearer"] == "delegated-test-bearer"
        calls["props"].append(kwargs["rows"])
        from kdcube_cli.secrets_commands import UserPropsImportResult

        return UserPropsImportResult(inserted=1, equal=1, different=0, batches=1)

    async def secret_import(**kwargs):
        assert kwargs["bearer"] == "delegated-test-bearer"
        calls["secrets"] += 1
        from types import SimpleNamespace

        return SimpleNamespace(ok=True, applied=1)

    monkeypatch.setattr(cli_mod, "_load_cli_defaults", lambda: {})
    monkeypatch.setattr(cli_mod, "_resolve_subcommand_workdir", lambda *_a, **_k: workdir)
    monkeypatch.setattr(cli_mod, "_resolve_cli_workdir", lambda path, **_k: Path(path))
    monkeypatch.setattr(cli_mod, "_canonical_descriptor_dir_from_initialized_workdir", lambda _w: target_config)
    monkeypatch.setattr(cli_mod, "_resolve_subcommand_repo", lambda *_a, **_k: tmp_path / "repo")
    monkeypatch.setattr(cli_mod, "_regenerate_runtime_config_from_descriptors", lambda *_a, **_k: None)
    monkeypatch.setattr(cli_mod, "delegated_credential_from_input", bearer)
    monkeypatch.setattr(cli_mod, "local_management_target", lambda _w: object())
    monkeypatch.setattr(cli_mod, "apply_user_props_import", props_import)
    monkeypatch.setattr(cli_mod, "apply_secret_descriptor_import", secret_import)
    monkeypatch.setattr(sys, "argv", ["kdcube", "config", "import", "--workdir", str(workdir), "--descriptors-location",
                                      str(source), "--include-platform-descriptors", "--include-user-data", "--json",
                                      *flags])
    cli_mod.main()
    return calls


def test_import_inserts_the_exported_props_without_connection_hub_props(monkeypatch, tmp_path, capsys):
    calls = _import(monkeypatch, tmp_path, "--yes", "--credential-stdin")
    assert calls["props"] == [PROPS], "Connection Hub's prop never reaches the runtime"
    assert calls["bearer"] == 1 and calls["secrets"] == 0
    out = capsys.readouterr().out
    view = json.loads(out[out.index("{"):])["user_props_import"]
    assert view == {"rows": 2, "connection_hub_props_left_out": 1, "dry_run": False, "inserted": 1, "equal": 1,
                    "different": 0, "batches": 1, "ok": True}
    assert "hub-prop-canary" not in out and "dark" not in out, "counts only"


def test_provider_import_reads_the_bearer_once_for_secrets_and_props(monkeypatch, tmp_path):
    calls = _import(monkeypatch, tmp_path, "--yes", "--credential-stdin", provider="secrets-service")
    assert calls["secrets"] == 1 and calls["props"] == [PROPS] and calls["bearer"] == 1


def test_a_dry_run_counts_the_props_and_imports_nothing(monkeypatch, tmp_path, capsys):
    calls = _import(monkeypatch, tmp_path, "--dry-run")
    assert calls["props"] == [] and calls["bearer"] == 0
    out = capsys.readouterr().out
    assert json.loads(out[out.index("{"):])["user_props_import"] == {
        "rows": 2, "connection_hub_props_left_out": 1, "dry_run": True}


def test_importing_props_needs_yes(monkeypatch, tmp_path):
    with pytest.raises(SystemExit, match="requires --yes"):
        _import(monkeypatch, tmp_path)


def test_import_with_the_flag_needs_a_real_props_export(tmp_path):
    with pytest.raises(SystemExit, match="needs .*user_bundle_props.json"):
        cli_mod._load_exported_user_props(tmp_path)
    (tmp_path / "user-data").mkdir()
    for document in ("[]", '{"schema": "other", "rows": []}', '{"schema": "kdcube.export.user_bundle_props.v1"}'):
        (tmp_path / "user-data" / "user_bundle_props.json").write_text(document)
        with pytest.raises(SystemExit, match="not a KDCube user props export"):
            cli_mod._load_exported_user_props(tmp_path)


def test_props_go_in_bounded_batches_with_stable_invocation_ids():
    from kdcube_cli.management.models import ManagementTarget
    from kdcube_cli.secrets_commands import USER_PROPS_BATCH_ROWS, _batch_invocation_id, user_props_batches

    rows = [{"user_id": "u", "bundle_id": "b", "key": f"k{i:04d}", "subsystem": "bundle", "value_json": "1"}
            for i in range(USER_PROPS_BATCH_ROWS + 3)]
    batches = user_props_batches(list(reversed(rows)))
    assert [len(batch) for batch in batches] == [USER_PROPS_BATCH_ROWS, 3]
    big = [{"user_id": "u", "bundle_id": "b", "key": f"big{i}", "subsystem": "bundle",
            "value_json": json.dumps("x" * 60000)} for i in range(60)]
    assert len(user_props_batches(big)) == 2, "the byte bound splits too"
    target = ManagementTarget.__new__(ManagementTarget)
    object.__setattr__(target, "tenant", "t")
    object.__setattr__(target, "project", "p")
    assert _batch_invocation_id(target, batches[1]) == _batch_invocation_id(target, list(batches[1]))


def test_the_request_is_exact_keyed_and_its_receipt_is_checked():
    from kdcube_cli.management.errors import ManagementCliError
    from kdcube_cli.management.models import ManagementRequest, ManagementTarget, _user_props_import_result

    target = ManagementTarget.__new__(ManagementTarget)
    for name, value in (("public_base_url", "https://runtime.test"), ("tenant", "t a"), ("project", "p"),
                        ("session_target_key", "k")):
        object.__setattr__(target, name, value)
    request = ManagementRequest.user_props_import(target, rows=[
        {"user_id": "u", "bundle_id": "b", "key": "z", "subsystem": "bundle", "value_json": '{"b": 1, "a": 2}'},
        {"user_id": "u", "bundle_id": "b", "key": "a", "subsystem": "bundle", "value_json": '"x"'}])
    assert request.path == "/api/integrations/management/v1/user-props/import"
    assert request.resource == "urn:kdcube:management:user-props:t%20a:p"
    assert [row["key"] for row in request.body["rows"]] == ["a", "z"]
    assert request.body["rows"][1]["value_json"] == '{"a":2,"b":1}'
    assert request.request_digest == "", "keyed by KDCube; the CLI never makes an offline verifier"
    good = {"inserted": 1, "equal": 0, "different": 1, "received": 2, "state": "imported"}
    assert _user_props_import_result(good, request=request) == good
    for bad in ({**good, "received": 3}, {**good, "inserted": 2}, {**good, "state": "stored"}, {**good, "equal": -1}):
        with pytest.raises(ManagementCliError):
            _user_props_import_result(bad, request=request)
