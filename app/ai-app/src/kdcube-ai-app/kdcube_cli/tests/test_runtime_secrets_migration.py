"""Old local runtimes are repaired through refresh and config apply, without Docker."""
from __future__ import annotations

import sys
import time
from types import SimpleNamespace

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from rich.console import Console

from kdcube_cli import cli, installer
from kdcube_cli.host_vault import DEFAULT_RUNTIME_NAMESPACES
from kdcube_ai_app.infra.secrets import runtime_bootstrap, runtime_contract, runtime_file
from kdcube_ai_app.infra.secrets.runtime_contract import POLICY_SCHEMA


def _runtime(tmp_path, *, runtime=None):
    config = tmp_path / "config"
    config.mkdir(parents=True)
    secrets = {"provider": "secrets-service"}
    if runtime is not None:
        secrets["runtime"] = runtime
    (config / "assembly.yaml").write_text(yaml.safe_dump({"secrets": secrets, "auth": {"type": "simple"}}))
    (config / ".env").write_text(f"KDCUBE_CONFIG_DIR={config}\n")
    for name, component in ((".env.proc", "proc"), (".env.ingress", "ingress")):
        (config / name).write_text(f"GATEWAY_COMPONENT={component}\nPLATFORM_DESCRIPTORS_DIR=/config\n")
    return config


def _launch(config, monkeypatch):
    tokens = {"SECRETS_TOKEN_PROC": "synthetic-proc", "SECRETS_ADMIN_TOKEN": "synthetic-admin",
              "SECRETS_TOKEN_INGRESS": "synthetic-ingress"}
    overlay = installer.write_env_overlay(config / ".env", tokens)
    try:
        env = {key: value for key, (_, value) in installer.load_env_file(overlay).entries.items()}
        env["ASSEMBLY_YAML_DESCRIPTOR_PATH"] = str(config / "assembly.yaml")
        # Translate only the synthetic container bind and filesystem classifier.
        # Real routes, policy, file store and records run; this is not mount proof.
        monkeypatch.setattr(runtime_contract, "persistent_filesystem", lambda root: True)
        monkeypatch.setattr(runtime_bootstrap, "RuntimeFileStore", lambda **kw: runtime_file.RuntimeFileStore(
            **{**kw, "root": config / "secrets"}))
        app = FastAPI()
        runtime_bootstrap.install_configured_runtime_routes(app, environ=env)
        client = TestClient(app)
        read = {"X-KDCUBE-SECRET-TOKEN": tokens["SECRETS_TOKEN_PROC"]}
        write = {"X-KDCUBE-ADMIN-TOKEN": tokens["SECRETS_ADMIN_TOKEN"]}
        for namespace in DEFAULT_RUNTIME_NAMESPACES:
            base = f"/runtime-secrets/{namespace}"
            assert client.get(base + "/qualification", headers={**read, **write}).status_code == 200
            payload = {"secret_ref": "a" * 32, "value": "synthetic-value", "expires_at": int(time.time()) + 60}
            assert client.post(base + "/create", headers=write, json=payload).status_code == 200
            assert client.get(base + "/secret/" + "a" * 32, headers=read).json() == {"value": "synthetic-value"}
            for headers in ({"X-KDCUBE-SECRET-TOKEN": tokens["SECRETS_TOKEN_INGRESS"]},
                            {"X-KDCUBE-ADMIN-TOKEN": tokens["SECRETS_TOKEN_INGRESS"]}):
                assert client.get(base + "/qualification", headers=headers).status_code == 403
    finally:
        overlay.unlink()


def _refresh(monkeypatch, config):
    repo = config.parent / "repo"
    repo.mkdir()
    calls = []
    monkeypatch.setattr(sys, "argv", ["kdcube", "refresh", "--quiet", "--workdir", str(config.parent)])
    monkeypatch.setattr(cli, "_load_cli_defaults", lambda: {})
    monkeypatch.setattr(cli, "_resolve_namespaced_runtime_target", lambda **kw: (config.parent, "sample", "project"))
    monkeypatch.setattr(cli, "_canonical_descriptor_dir_from_initialized_workdir", lambda path: config)
    monkeypatch.setattr(cli, "_resolve_subcommand_repo", lambda *a, **kw: repo)
    monkeypatch.setattr(cli, "_build_paths_for_repo", lambda *a, **kw: SimpleNamespace(docker_dir=repo, config_dir=config))
    monkeypatch.setattr(cli, "require_compose_source", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "_refresh_runtime_proxy_config", lambda *a, **kw: None)
    monkeypatch.setattr(installer, "ensure_generated_runtime_secrets", lambda *a: [])
    monkeypatch.setattr(cli, "_parse_workdir_namespace", lambda path: ("sample", "project"))
    monkeypatch.setattr(cli, "stop_compose_stack", lambda *a, **kw: calls.append("stop"))
    monkeypatch.setattr(cli, "_check_before_start", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "start_compose_stack", lambda *a, **kw: _launch(config, monkeypatch))
    return calls


def test_refresh_repairs_pre_w673_runtime_without_runtime_env(monkeypatch, tmp_path):
    config = _runtime(tmp_path)
    calls = _refresh(monkeypatch, config)
    cli.main()
    assert calls == ["stop"]
    assert yaml.safe_load((config / "assembly.yaml").read_text())["secrets"]["url"] == "http://kdcube-secrets:7777"
    assert (config / "secrets").is_dir()
    assert (config / "secrets").stat().st_mode & 0o777 == 0o700
    for name in (".env", ".env.proc", ".env.ingress"):
        text = (config / name).read_text()
        assert "SECRETS_URL=" not in text and "SECRETS_PROVIDER=" not in text
        assert "KDCUBE_SECRETS_RUNTIME_" not in text


def test_refresh_refuses_fixed_digest_policy_before_stop_or_writes(monkeypatch, tmp_path):
    config = _runtime(tmp_path, runtime={"scope_policy": {
        "schema": POLICY_SCHEMA, "read": {"a" * 64: ["login-attempts"]}, "write": {}}})
    before = (config / "assembly.yaml").read_bytes()
    calls = _refresh(monkeypatch, config)
    with pytest.raises(SystemExit, match=r"secrets.runtime.scope_policy.*fixed credential digests"):
        cli.main()
    assert calls == []
    assert (config / "assembly.yaml").read_bytes() == before
    assert not (config / "secrets").exists()


def test_auth_config_apply_backfills_service_url_with_two_line_env(monkeypatch, tmp_path):
    config = _runtime(tmp_path / "target")
    source = _runtime(tmp_path / "incoming")
    incoming = yaml.safe_load((source / "assembly.yaml").read_text())
    incoming["auth"]["type"] = "cognito"
    (source / "assembly.yaml").write_text(yaml.safe_dump(incoming))
    for name in (*cli.PLATFORM_DESCRIPTOR_FILENAMES[1:], "bundles.yaml"):
        (source / name).write_text("{}\n")
    monkeypatch.setattr(cli, "_canonical_descriptor_dir_from_initialized_workdir", lambda path: config)
    # Bundle materialization and general installer prompting have separate suites;
    # keep the actual platform import, migration and launch composition here.
    monkeypatch.setattr(cli, "apply_bundle_config_descriptors", lambda *a, **kw: {})
    monkeypatch.setattr(cli, "_regenerate_runtime_config_from_descriptors", lambda *a, **kw: None)
    result = cli.apply_config_descriptors(Console(quiet=True), workdir=config.parent,
        descriptors_location=source, include_platform_descriptors=True, repo_root=tmp_path, quiet=True)
    assert result["runtime_config_regenerated"]
    assembly = yaml.safe_load((config / "assembly.yaml").read_text())
    assert assembly["auth"]["type"] == "cognito"
    assert assembly["secrets"]["url"] == "http://kdcube-secrets:7777"
    for name in (".env.proc", ".env.ingress"):
        assert len((config / name).read_text().splitlines()) == 2
    _launch(config, monkeypatch)


def test_config_apply_refuses_fixed_policy_before_overwriting_descriptors(tmp_path):
    config = _runtime(tmp_path / "target")
    source = _runtime(tmp_path / "incoming", runtime={"scope_policy": {
        "schema": POLICY_SCHEMA, "read": {}, "write": {}}})
    for name in cli.PLATFORM_DESCRIPTOR_FILENAMES[1:]:
        (source / name).write_text("{}\n")
    before = (config / "assembly.yaml").read_bytes()
    with pytest.raises(SystemExit, match=r"secrets.runtime.scope_policy.*fixed credential digests"):
        cli._apply_platform_config_descriptors(Console(quiet=True), config_dir=config, descriptors_location=source)
    assert (config / "assembly.yaml").read_bytes() == before
