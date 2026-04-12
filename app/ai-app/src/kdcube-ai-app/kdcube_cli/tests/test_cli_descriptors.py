from pathlib import Path

from rich.console import Console

from kdcube_cli.cli import (
    _descriptor_fast_path_reasons,
    _load_bundle_ids_from_descriptor,
)
from kdcube_cli import export_live_bundles as export_mod
from kdcube_cli.installer import PathsContext, gather_configuration


def test_descriptor_fast_path_accepts_complete_release_descriptor():
    assembly = {
        "context": {"tenant": "example-product", "project": "chatbot"},
        "platform": {"repo": "kdcube/kdcube-ai-app", "ref": "2026.4.04.318"},
        "secrets": {"provider": "secrets-file"},
        "paths": {"host_bundles_path": "/Users/demo/bundles"},
        "auth": {"type": "simple"},
        "proxy": {"ssl": False},
        "storage": {
            "workspace": {"type": "git", "repo": "https://github.com/kdcube/agentic-workspace.git"},
            "claude_code_session": {"type": "git", "repo": "https://github.com/kdcube/agentic-workspace.git"},
        },
    }

    reasons = _descriptor_fast_path_reasons(
        assembly,
        have_secrets=True,
        have_gateway=True,
        latest=False,
        release=None,
    )

    assert reasons == []


def test_descriptor_fast_path_requires_platform_ref_without_latest():
    assembly = {
        "context": {"tenant": "example-product", "project": "chatbot"},
        "secrets": {"provider": "secrets-file"},
        "paths": {"host_bundles_path": "/Users/demo/bundles"},
        "auth": {"type": "simple"},
        "proxy": {"ssl": False},
    }

    reasons = _descriptor_fast_path_reasons(
        assembly,
        have_secrets=True,
        have_gateway=True,
        latest=False,
        release=None,
    )

    assert "assembly platform.ref is required unless --latest, --upstream, or --release is used" in reasons


def test_descriptor_fast_path_requires_cognito_fields():
    assembly = {
        "context": {"tenant": "example-product", "project": "chatbot"},
        "platform": {"ref": "2026.4.04.318"},
        "secrets": {"provider": "secrets-file"},
        "paths": {"host_bundles_path": "/Users/demo/bundles"},
        "auth": {
            "type": "cognito",
            "cognito": {
                "region": "eu-west-1",
                "user_pool_id": "pool",
            },
        },
        "proxy": {"ssl": False},
    }

    reasons = _descriptor_fast_path_reasons(
        assembly,
        have_secrets=True,
        have_gateway=True,
        latest=False,
        release=None,
    )

    assert "assembly auth.cognito.app_client_id is required" in reasons


def test_descriptor_fast_path_accepts_explicit_release_without_platform_ref():
    assembly = {
        "context": {"tenant": "example-product", "project": "chatbot"},
        "secrets": {"provider": "secrets-file"},
        "paths": {"host_bundles_path": "/Users/demo/bundles"},
        "auth": {"type": "simple"},
        "proxy": {"ssl": False},
    }

    reasons = _descriptor_fast_path_reasons(
        assembly,
        have_secrets=True,
        have_gateway=True,
        latest=False,
        release="2026.4.04.318",
    )

    assert reasons == []


def test_descriptor_fast_path_accepts_upstream_without_platform_ref():
    assembly = {
        "context": {"tenant": "example-product", "project": "chatbot"},
        "secrets": {"provider": "secrets-file"},
        "paths": {"host_bundles_path": "/Users/demo/bundles"},
        "auth": {"type": "simple"},
        "proxy": {"ssl": False},
    }

    reasons = _descriptor_fast_path_reasons(
        assembly,
        have_secrets=True,
        have_gateway=True,
        latest=False,
        upstream=True,
        release=None,
    )

    assert reasons == []


def test_descriptor_fast_path_requires_host_bundles_path_for_noninteractive_local_bundle_installs():
    assembly = {
        "context": {"tenant": "example-product", "project": "chatbot"},
        "platform": {"ref": "2026.4.04.318"},
        "secrets": {"provider": "secrets-file"},
        "auth": {"type": "simple"},
        "proxy": {"ssl": False},
    }

    reasons = _descriptor_fast_path_reasons(
        assembly,
        have_secrets=True,
        have_gateway=True,
        latest=False,
        release=None,
    )

    assert "assembly paths.host_bundles_path is required for non-interactive local bundle installs" in reasons


def test_load_bundle_ids_from_bundles_yaml(tmp_path: Path):
    path = tmp_path / "bundles.yaml"
    path.write_text(
        """
bundles:
  demo.bundle@1.0.0:
    path: /bundles/demo
    module: demo.entrypoint
  another.bundle@2.0.0:
    path: /bundles/another
    module: another.entrypoint
""".strip()
    )

    assert _load_bundle_ids_from_descriptor(path) == {
        "demo.bundle@1.0.0",
        "another.bundle@2.0.0",
    }


def test_load_bundle_ids_from_assembly_yaml(tmp_path: Path):
    path = tmp_path / "assembly.yaml"
    path.write_text(
        """
context:
  tenant: demo
  project: chatbot
bundles:
  demo.bundle@1.0.0:
    path: /bundles/demo
    module: demo.entrypoint
""".strip()
    )

    assert _load_bundle_ids_from_descriptor(path) == {"demo.bundle@1.0.0"}


def test_gather_configuration_accepts_descriptor_secret_paths(monkeypatch, tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    for name in (
        ".env",
        ".env.ingress",
        ".env.proc",
        ".env.metrics",
        ".env.postgres.setup",
        ".env.proxylogin",
    ):
        (config_dir / name).write_text("")

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    ai_app_root = tmp_path / "ai-app"
    ai_app_root.mkdir()
    docker_dir = ai_app_root / "deployment" / "docker" / "all_in_one_kdcube"
    docker_dir.mkdir(parents=True)

    assembly_path = tmp_path / "assembly.yaml"
    secrets_path = tmp_path / "secrets.yaml"
    bundles_path = tmp_path / "bundles.yaml"
    bundles_secrets_path = tmp_path / "bundles.secrets.yaml"
    gateway_path = tmp_path / "gateway.yaml"
    for p in (assembly_path, secrets_path, bundles_path, bundles_secrets_path, gateway_path):
        p.write_text("x: 1\n")

    monkeypatch.setattr(
        "kdcube_cli.installer.compute_paths",
        lambda *_args, **_kwargs: {
            "host_kb_storage": str(tmp_path / "kdcube-storage"),
            "host_bundles": str(tmp_path / "bundles"),
            "host_git_bundles": str(tmp_path / "git-bundles"),
            "host_bundle_storage": str(tmp_path / "bundle-storage"),
            "host_exec_workspace": str(tmp_path / "exec-workspace"),
            "ui_build_context": str(ai_app_root),
            "ui_dockerfile_path": "Dockerfile_UI",
            "ui_source_path": "ui/chat-web-app",
            "ui_env_build_relative": ".env.ui.build",
            "nginx_ui_config": "nginx_ui.conf",
            "nginx_proxy_config": "nginx_proxy.conf",
            "proxy_build_context": str(ai_app_root),
            "proxy_dockerfile_path": "Dockerfile_Proxy",
        },
    )
    monkeypatch.setattr("kdcube_cli.installer.ask", lambda _console, _label, default=None, secret=False: str(default or ""))
    monkeypatch.setattr("kdcube_cli.installer.ask_confirm", lambda _console, _label, default=False: default)
    monkeypatch.setattr(
        "kdcube_cli.installer.select_option",
        lambda _console, _title, options, default_index=0: options[default_index],
    )
    monkeypatch.setattr(
        "kdcube_cli.installer.ensure_absolute",
        lambda _console, _label, current, default, force_prompt=False: str(Path(current or default or tmp_path).resolve()),
    )
    monkeypatch.setattr("kdcube_cli.installer.prompt_secret_value", lambda *args, **kwargs: None)
    monkeypatch.setattr("kdcube_cli.installer.ensure_ui_env_build_file", lambda *args, **kwargs: None)
    monkeypatch.setattr("kdcube_cli.installer.ensure_ui_nginx_config_file", lambda *args, **kwargs: None)
    monkeypatch.setattr("kdcube_cli.installer.write_frontend_config", lambda *args, **kwargs: None)
    monkeypatch.setattr("kdcube_cli.installer.sync_nginx_proxy_config", lambda *args, **kwargs: None)
    monkeypatch.setattr("kdcube_cli.installer.update_nginx_routes_prefix", lambda *args, **kwargs: None)
    monkeypatch.setattr("kdcube_cli.installer.update_nginx_ssl_domain", lambda *args, **kwargs: None)
    monkeypatch.setattr("kdcube_cli.installer._load_json_file", lambda *_args, **_kwargs: {})

    ctx = PathsContext(
        lib_root=tmp_path / "lib",
        ai_app_root=ai_app_root,
        docker_dir=docker_dir,
        sample_env_dir=tmp_path / "sample_env",
        workdir=workdir,
        config_dir=config_dir,
        data_dir=tmp_path / "data",
    )

    gather_configuration(
        Console(file=None),
        ctx,
        release_descriptor_path=str(assembly_path),
        release_descriptor={
            "context": {"tenant": "demo-tenant", "project": "demo-project"},
            "platform": {"ref": "2026.4.11.012"},
            "secrets": {"provider": "secrets-file"},
            "paths": {"host_bundles_path": str(tmp_path / "bundles")},
            "auth": {"type": "simple"},
            "proxy": {"ssl": False},
        },
        secrets_descriptor_path=str(secrets_path),
        secrets_descriptor={"services": {}},
        bundles_descriptor_path=str(bundles_path),
        bundles_descriptor={"bundles": {}},
        bundles_secrets_path=str(bundles_secrets_path),
        bundles_secrets_descriptor={"bundles": {"items": []}},
        gateway_descriptor={},
        use_bundles_descriptor=True,
        use_bundles_secrets=True,
    )

    env_main = (config_dir / ".env").read_text()
    assert f"HOST_SECRETS_YAML_DESCRIPTOR_PATH={secrets_path}" in env_main
    assert f"HOST_BUNDLES_SECRETS_YAML_DESCRIPTOR_PATH={bundles_secrets_path}" in env_main


def test_gather_configuration_treats_null_redis_secret_as_unset(monkeypatch, tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    for name in (
        ".env",
        ".env.ingress",
        ".env.proc",
        ".env.metrics",
        ".env.postgres.setup",
        ".env.proxylogin",
    ):
        (config_dir / name).write_text("")

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    ai_app_root = tmp_path / "ai-app"
    ai_app_root.mkdir()
    docker_dir = ai_app_root / "deployment" / "docker" / "all_in_one_kdcube"
    docker_dir.mkdir(parents=True)

    assembly_path = tmp_path / "assembly.yaml"
    assembly_path.write_text("x: 1\n")
    secrets_path = tmp_path / "secrets.yaml"
    secrets_path.write_text("x: 1\n")

    monkeypatch.setattr(
        "kdcube_cli.installer.compute_paths",
        lambda *_args, **_kwargs: {
            "host_kb_storage": str(tmp_path / "kdcube-storage"),
            "host_bundles": str(tmp_path / "bundles"),
            "host_git_bundles": str(tmp_path / "git-bundles"),
            "host_bundle_storage": str(tmp_path / "bundle-storage"),
            "host_exec_workspace": str(tmp_path / "exec-workspace"),
            "ui_build_context": str(ai_app_root),
            "ui_dockerfile_path": "Dockerfile_UI",
            "ui_source_path": "ui/chat-web-app",
            "ui_env_build_relative": ".env.ui.build",
            "nginx_ui_config": "nginx_ui.conf",
            "nginx_proxy_config": "nginx_proxy.conf",
            "proxy_build_context": str(ai_app_root),
            "proxy_dockerfile_path": "Dockerfile_Proxy",
        },
    )
    monkeypatch.setattr("kdcube_cli.installer.ask", lambda _console, _label, default=None, secret=False: str(default or ""))
    monkeypatch.setattr("kdcube_cli.installer.ask_confirm", lambda _console, _label, default=False: default)
    monkeypatch.setattr(
        "kdcube_cli.installer.select_option",
        lambda _console, _title, options, default_index=0: options[default_index],
    )
    monkeypatch.setattr(
        "kdcube_cli.installer.ensure_absolute",
        lambda _console, _label, current, default, force_prompt=False: str(Path(current or default or tmp_path).resolve()),
    )
    monkeypatch.setattr("kdcube_cli.installer.prompt_secret_value", lambda *args, **kwargs: None)
    monkeypatch.setattr("kdcube_cli.installer.ensure_ui_env_build_file", lambda *args, **kwargs: None)
    monkeypatch.setattr("kdcube_cli.installer.ensure_ui_nginx_config_file", lambda *args, **kwargs: None)
    monkeypatch.setattr("kdcube_cli.installer.write_frontend_config", lambda *args, **kwargs: None)
    monkeypatch.setattr("kdcube_cli.installer.sync_nginx_proxy_config", lambda *args, **kwargs: None)
    monkeypatch.setattr("kdcube_cli.installer.update_nginx_routes_prefix", lambda *args, **kwargs: None)
    monkeypatch.setattr("kdcube_cli.installer.update_nginx_ssl_domain", lambda *args, **kwargs: None)
    monkeypatch.setattr("kdcube_cli.installer._load_json_file", lambda *_args, **_kwargs: {})

    ctx = PathsContext(
        lib_root=tmp_path / "lib",
        ai_app_root=ai_app_root,
        docker_dir=docker_dir,
        sample_env_dir=tmp_path / "sample_env",
        workdir=workdir,
        config_dir=config_dir,
        data_dir=tmp_path / "data",
    )

    gather_configuration(
        Console(file=None),
        ctx,
        release_descriptor_path=str(assembly_path),
        release_descriptor={
            "context": {"tenant": "demo-tenant", "project": "demo-project"},
            "platform": {"ref": "2026.4.12.500"},
            "secrets": {"provider": "secrets-file"},
            "paths": {"host_bundles_path": str(tmp_path / "bundles")},
            "auth": {"type": "simple"},
            "proxy": {"ssl": False},
            "infra": {
                "postgres": {
                    "user": "postgres",
                    "password": "postgres",
                    "database": "kdcube",
                    "host": "postgres-db",
                    "port": "5432",
                },
                "redis": {
                    "host": "redis.example.internal",
                    "port": "6379",
                },
            },
        },
        secrets_descriptor_path=str(secrets_path),
        secrets_descriptor={
            "infra": {
                "redis": {
                    "password": None,
                },
            },
        },
        gateway_descriptor={},
    )

    env_main = (config_dir / ".env").read_text()
    env_ingress = (config_dir / ".env.ingress").read_text()

    assert "REDIS_PASSWORD=\n" in env_main or env_main.endswith("REDIS_PASSWORD=")
    assert "REDIS_URL=redis://redis.example.internal:6379/0" in env_ingress
    assert "redis://:redispass@" not in env_ingress


def test_resolve_aws_sm_prefix_defaults_from_tenant_project():
    assert export_mod.resolve_aws_sm_prefix(tenant="demo", project="proj", explicit=None) == "kdcube/demo/proj"


def test_export_live_bundle_descriptors_reconstructs_effective_files(monkeypatch, tmp_path: Path):
    payloads = {
        "kdcube/demo/proj/bundles-meta": {
            "default_bundle_id": "demo.bundle",
            "bundle_ids": ["demo.bundle", "git.bundle"],
        },
        "kdcube/demo/proj/bundles/demo.bundle/descriptor": {
            "path": "/bundles/demo.bundle",
            "module": "entrypoint",
            "props": {"feature": {"enabled": True}},
        },
        "kdcube/demo/proj/bundles/demo.bundle/secrets": {
            "api": {"key": "secret-1"},
        },
        "kdcube/demo/proj/bundles/git.bundle/descriptor": {
            "repo": "https://github.com/example/git.bundle.git",
            "ref": "main",
            "subdir": "bundle",
            "module": "entrypoint",
        },
    }

    def _fake_get(*, secret_id, region, profile, required):
        assert region == "eu-west-1"
        assert profile == "demo"
        value = payloads.get(secret_id)
        if value is None and required:
            raise AssertionError(f"unexpected required secret lookup: {secret_id}")
        return value

    monkeypatch.setattr(export_mod, "aws_secret_json", _fake_get)

    export_mod.export_live_bundle_descriptors(
        Console(file=None),
        tenant="demo",
        project="proj",
        out_dir=tmp_path,
        aws_region="eu-west-1",
        aws_profile="demo",
        aws_sm_prefix=None,
    )

    bundles_yaml = export_mod.yaml.safe_load((tmp_path / "bundles.yaml").read_text())
    bundles_secrets_yaml = export_mod.yaml.safe_load((tmp_path / "bundles.secrets.yaml").read_text())

    assert bundles_yaml == {
        "bundles": {
            "version": "1",
            "items": [
                {
                    "id": "demo.bundle",
                    "path": "/bundles/demo.bundle",
                    "module": "entrypoint",
                    "props": {"feature": {"enabled": True}},
                },
                {
                    "id": "git.bundle",
                    "repo": "https://github.com/example/git.bundle.git",
                    "ref": "main",
                    "subdir": "bundle",
                    "module": "entrypoint",
                },
            ],
            "default_bundle_id": "demo.bundle",
        }
    }
    assert bundles_secrets_yaml == {
        "bundles": {
            "version": "1",
            "items": [
                {"id": "demo.bundle", "secrets": {"api": {"key": "secret-1"}}},
                {"id": "git.bundle", "secrets": {}},
            ],
        }
    }
