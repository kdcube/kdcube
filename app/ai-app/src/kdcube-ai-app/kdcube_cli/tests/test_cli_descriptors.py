import json
from pathlib import Path

import yaml
from rich.console import Console

from kdcube_cli.cli import (
    _descriptor_fast_path_reasons,
    _load_bundle_ids_from_descriptor,
    _resolve_cli_repo_path,
    _resolve_cli_workdir,
)
from kdcube_cli import export_live_bundles as export_mod
from kdcube_cli.installer import (
    PathsContext,
    apply_runtime_secrets_to_file_descriptors,
    gather_configuration,
    stage_descriptor_directory,
    workspace_namespace,
)


def test_descriptor_fast_path_accepts_complete_release_descriptor():
    assembly = {
        "context": {"tenant": "acme", "project": "platform"},
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


def test_workspace_namespace_uses_safe_names():
    assert workspace_namespace("Demo Tenant", "Project/One") == "demo_tenant__project_one"
    assert workspace_namespace("", None) == "default_tenant__default_project"


def test_resolve_cli_workdir_uses_descriptor_context_namespace(tmp_path: Path):
    descriptors_dir = tmp_path / "descriptors"
    descriptors_dir.mkdir()
    (descriptors_dir / "assembly.yaml").write_text(
        "context:\n  tenant: Demo Tenant\n  project: Project/One\n"
    )

    resolved = _resolve_cli_workdir(tmp_path / "workspace", descriptors_location=descriptors_dir)
    assert resolved == (tmp_path / "workspace" / "demo_tenant__project_one").resolve()


def test_resolve_cli_workdir_auto_selects_single_runtime(tmp_path: Path):
    runtime_dir = tmp_path / "workspace" / "demo_tenant__project_one"
    (runtime_dir / "config").mkdir(parents=True)
    (runtime_dir / "config" / ".env").write_text("")

    resolved = _resolve_cli_workdir(tmp_path / "workspace")
    assert resolved == runtime_dir.resolve()


def test_resolve_cli_repo_path_defaults_under_namespaced_runtime(tmp_path: Path):
    descriptors_dir = tmp_path / "descriptors"
    descriptors_dir.mkdir()
    (descriptors_dir / "assembly.yaml").write_text(
        "context:\n  tenant: Demo Tenant\n  project: Project/One\n"
    )

    resolved = _resolve_cli_repo_path(
        tmp_path / "ignored-default",
        workdir=tmp_path / "workspace",
        path_provided=False,
        descriptors_location=descriptors_dir,
    )
    assert resolved == (
        tmp_path / "workspace" / "demo_tenant__project_one" / "repo"
    ).resolve()


def test_resolve_cli_repo_path_prefers_install_meta_repo_root(tmp_path: Path):
    runtime_dir = tmp_path / "workspace" / "demo_tenant__project_one"
    config_dir = runtime_dir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / ".env").write_text("")
    repo_dir = runtime_dir / "checked-out-repo"
    (repo_dir / ".git").mkdir(parents=True)
    (config_dir / "install-meta.json").write_text(
        json.dumps({"repo_root": str(repo_dir.resolve())})
    )

    resolved = _resolve_cli_repo_path(
        tmp_path / "ignored-default",
        workdir=tmp_path / "workspace",
        path_provided=False,
    )
    assert resolved == repo_dir.resolve()


def test_stage_descriptor_directory_requires_canonical_descriptor_set(tmp_path: Path):
    ai_app_root = tmp_path / "ai-app"
    deployment_dir = ai_app_root / "deployment"
    deployment_dir.mkdir(parents=True)
    for name in ("assembly.yaml", "secrets.yaml", "bundles.yaml", "bundles.secrets.yaml", "gateway.yaml"):
        (deployment_dir / name).write_text("x: 1\n")

    source_dir = tmp_path / "descriptors"
    source_dir.mkdir()
    (source_dir / "assembly.yaml").write_text("context: {}\n")

    target_dir = tmp_path / "workdir" / "config"
    try:
        stage_descriptor_directory(
            target_dir,
            source_dir=source_dir,
            ai_app_root=ai_app_root,
            require_complete=True,
        )
    except SystemExit as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected SystemExit for incomplete canonical descriptor set")

    assert "canonical descriptor set" in message
    assert "secrets.yaml" in message
    assert "gateway.yaml" in message


def test_stage_descriptor_directory_stages_complete_canonical_set(tmp_path: Path):
    ai_app_root = tmp_path / "ai-app"
    deployment_dir = ai_app_root / "deployment"
    deployment_dir.mkdir(parents=True)
    for name in ("assembly.yaml", "secrets.yaml", "bundles.yaml", "bundles.secrets.yaml", "gateway.yaml"):
        (deployment_dir / name).write_text("x: 1\n")

    source_dir = tmp_path / "descriptors"
    source_dir.mkdir()
    (source_dir / "assembly.yaml").write_text("context:\n  tenant: demo\n  project: demo\n")
    (source_dir / "secrets.yaml").write_text("services: {}\n")
    (source_dir / "bundles.yaml").write_text("bundles: {}\n")
    (source_dir / "bundles.secrets.yaml").write_text("bundles:\n  items: []\n")
    (source_dir / "gateway.yaml").write_text("gateway:\n  tenant: demo\n  project: demo\n")

    target_dir = tmp_path / "workdir" / "config"
    staged = stage_descriptor_directory(
        target_dir,
        source_dir=source_dir,
        ai_app_root=ai_app_root,
        require_complete=True,
    )

    assert staged["assembly_path"] == target_dir / "assembly.yaml"
    assert staged["secrets_path"] == target_dir / "secrets.yaml"
    assert staged["bundles_path"] == target_dir / "bundles.yaml"
    assert staged["bundles_secrets_path"] == target_dir / "bundles.secrets.yaml"
    assert staged["gateway_path"] == target_dir / "gateway.yaml"
    assert staged["assembly"]["context"]["tenant"] == "demo"


def test_descriptor_fast_path_requires_platform_ref_without_latest():
    assembly = {
        "context": {"tenant": "acme", "project": "platform"},
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
        "context": {"tenant": "acme", "project": "platform"},
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
        "context": {"tenant": "acme", "project": "platform"},
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
        "context": {"tenant": "acme", "project": "platform"},
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
        "context": {"tenant": "acme", "project": "platform"},
        "platform": {"ref": "2026.4.04.318"},
        "secrets": {"provider": "secrets-file"},
        "auth": {"type": "simple"},
        "proxy": {"ssl": False},
    }
    bundles_descriptor = {
        "bundles": {
            "items": [
                {
                    "id": "demo.bundle@1.0.0",
                    "path": "/bundles/demo",
                    "module": "demo.entrypoint",
                }
            ]
        }
    }

    reasons = _descriptor_fast_path_reasons(
        assembly,
        have_secrets=True,
        have_gateway=True,
        bundles_descriptor=bundles_descriptor,
        latest=False,
        release=None,
    )

    assert "assembly paths.host_bundles_path is required for non-interactive local bundle installs" in reasons


def test_descriptor_fast_path_accepts_absolute_host_bundle_paths_without_host_bundles_path():
    assembly = {
        "context": {"tenant": "acme", "project": "platform"},
        "platform": {"ref": "2026.4.04.318"},
        "secrets": {"provider": "secrets-file"},
        "auth": {"type": "simple"},
        "proxy": {"ssl": False},
    }
    bundles_descriptor = {
        "bundles": {
            "items": [
                {
                    "id": "demo.bundle@1.0.0",
                    "path": "/Users/demo/src/platform/bundles/demo.bundle",
                    "module": "demo.entrypoint",
                }
            ]
        }
    }

    reasons = _descriptor_fast_path_reasons(
        assembly,
        have_secrets=True,
        have_gateway=True,
        bundles_descriptor=bundles_descriptor,
        latest=False,
        release=None,
    )

    assert "assembly paths.host_bundles_path is required for non-interactive local bundle installs" not in reasons


def test_descriptor_fast_path_accepts_git_only_bundles_without_host_bundles_path():
    assembly = {
        "context": {"tenant": "acme", "project": "platform"},
        "secrets": {"provider": "secrets-file"},
        "auth": {"type": "simple"},
        "proxy": {"ssl": False},
        "storage": {
            "workspace": {"type": "git", "repo": "https://github.com/kdcube/agentic-workspace.git"},
            "claude_code_session": {"type": "git", "repo": "https://github.com/kdcube/agentic-workspace.git"},
        },
    }
    bundles_descriptor = {
        "bundles": {
            "items": [
                {
                    "id": "acme.marketing@2-0",
                    "repo": "git@github.com:example-org/acme-platform.git",
                    "ref": "main",
                    "subdir": "src/acme/bundles/marketing",
                    "module": "acme.marketing@2-0.entrypoint",
                }
            ]
        }
    }

    reasons = _descriptor_fast_path_reasons(
        assembly,
        have_secrets=True,
        have_gateway=True,
        bundles_descriptor=bundles_descriptor,
        latest=False,
        upstream=True,
        release=None,
    )

    assert reasons == []


def test_descriptor_fast_path_accepts_frontend_build_without_frontend_config_override():
    assembly = {
        "context": {"tenant": "acme", "project": "platform"},
        "secrets": {"provider": "secrets-file"},
        "paths": {"host_bundles_path": "/Users/demo/bundles"},
        "auth": {"type": "delegated", "cognito": {"region": "eu-west-1", "user_pool_id": "pool", "app_client_id": "client"}},
        "proxy": {"ssl": False},
        "storage": {
            "workspace": {"type": "git", "repo": "https://github.com/kdcube/agentic-workspace.git"},
            "claude_code_session": {"type": "git", "repo": "https://github.com/kdcube/agentic-workspace.git"},
        },
        "frontend": {
            "build": {
                "repo": "git@github.com:example-org/acme-platform.git",
                "ref": "main",
                "dockerfile": "ops/acme/dockercompose/Dockerfile_UI",
                "src": "src/acme/ui/chat-web-app",
            }
        },
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
    monkeypatch.setattr("kdcube_cli.installer.git_clone_or_update", lambda *_args, **_kwargs: ai_app_root)
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
    assert (tmp_path / "kdcube-storage").is_dir()
    assert (tmp_path / "bundles").is_dir()
    assert (tmp_path / "git-bundles").is_dir()
    assert (tmp_path / "bundle-storage").is_dir()
    assert (tmp_path / "exec-workspace").is_dir()


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
    monkeypatch.setattr("kdcube_cli.installer.git_clone_or_update", lambda *_args, **_kwargs: ai_app_root)
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


def test_gather_configuration_applies_platform_service_env_from_assembly(monkeypatch, tmp_path: Path):
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
    monkeypatch.setattr("kdcube_cli.installer.git_clone_or_update", lambda *_args, **_kwargs: ai_app_root)
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
            "platform": {
                "ref": "2026.4.12.500",
                "services": {
                    "ingress": {
                        "service": {
                            "uvicorn_reload": True,
                            "cb_relay_identity": "relay.ingress",
                        }
                    },
                    "proc": {
                        "service": {
                            "cb_relay_identity": "relay.proc",
                            "chat_scheduler_backend": "legacy_lists",
                            "chat_task_timeout_sec": 600,
                            "chat_task_idle_timeout_sec": 900,
                            "chat_task_max_wall_time_sec": 3600,
                            "chat_task_watchdog_poll_interval_sec": 0.5,
                        }
                    },
                    "metrics": {
                        "service": {
                            "uvicorn_reload": False,
                        }
                    },
                },
            },
            "secrets": {"provider": "secrets-file"},
            "paths": {"host_bundles_path": str(tmp_path / "bundles")},
            "auth": {"type": "simple"},
            "proxy": {"ssl": False},
        },
        secrets_descriptor_path=str(secrets_path),
        secrets_descriptor={"services": {}},
        gateway_descriptor={},
    )

    env_ingress = (config_dir / ".env.ingress").read_text()
    env_proc = (config_dir / ".env.proc").read_text()
    env_metrics = (config_dir / ".env.metrics").read_text()

    assert "UVICORN_RELOAD=1" in env_ingress
    assert "CB_RELAY_IDENTITY=relay.ingress" in env_ingress
    assert "CB_RELAY_IDENTITY=relay.proc" in env_proc
    assert "CHAT_SCHEDULER_BACKEND=legacy_lists" in env_proc
    assert "CHAT_TASK_TIMEOUT_SEC=600" in env_proc
    assert "CHAT_TASK_IDLE_TIMEOUT_SEC=900" in env_proc
    assert "CHAT_TASK_MAX_WALL_TIME_SEC=3600" in env_proc
    assert "CHAT_TASK_WATCHDOG_POLL_INTERVAL_SEC=0.5" in env_proc
    assert "UVICORN_RELOAD=0" in env_metrics


def test_gather_configuration_supports_explicit_proxy_host_ports(monkeypatch, tmp_path: Path):
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
    docker_dir = ai_app_root / "deployment" / "docker" / "custom-ui-managed-infra"
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
            "nginx_proxy_config": "nginx_proxy_ssl_delegated_auth.conf",
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
    monkeypatch.setattr("kdcube_cli.installer.git_clone_or_update", lambda *_args, **_kwargs: ai_app_root)
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
            "domain": "ai.example.com",
            "context": {"tenant": "demo-tenant", "project": "demo-project"},
            "platform": {"ref": "2026.4.19.100"},
            "secrets": {"provider": "secrets-file"},
            "paths": {"host_bundles_path": str(tmp_path / "bundles")},
            "auth": {"type": "delegated"},
            "proxy": {"ssl": True, "route_prefix": "/chatbot"},
            "ports": {
                "ui": "5174",
                "ui_ssl": "443",
                "proxy_http": "80",
                "proxy_https": "443",
            },
            "frontend": {"build": {"repo": "git@example/repo.git", "ref": "main", "dockerfile": "Dockerfile_UI", "src": "ui"}},
        },
        secrets_descriptor_path=str(secrets_path),
        secrets_descriptor={"services": {}},
        gateway_descriptor={},
    )

    env_main = (config_dir / ".env").read_text()
    assert "KDCUBE_UI_PORT=5174" in env_main
    assert "KDCUBE_UI_SSL_PORT=443" in env_main
    assert "KDCUBE_PROXY_HTTP_PORT=80" in env_main
    assert "KDCUBE_PROXY_HTTPS_PORT=443" in env_main


def test_gather_configuration_keeps_proc_and_ingress_env_minimal_for_user_descriptors(monkeypatch, tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / ".env").write_text("")
    (config_dir / ".env.ingress").write_text(
        "AUTH_PROVIDER=cognito\n"
        "GATEWAY_CONFIG_JSON='{\n"
        "  \"tenant\": \"demo-tenant\",\n"
        "  \"project\": \"demo-project\"\n"
        "}'\n"
    )
    (config_dir / ".env.proc").write_text(
        "POSTGRES_HOST=postgres-db\n"
        "GIT_SSH_KEY_PATH=/run/secrets/git_ssh_key\n"
        "GIT_SSH_KNOWN_HOSTS=/run/secrets/git_known_hosts\n"
    )
    (config_dir / ".env.metrics").write_text(
        "METRICS_PORT=8090\n"
        "GATEWAY_CONFIG_JSON='{\"tenant\":\"demo-tenant\",\"project\":\"demo-project\"}'\n"
    )
    for name in (".env.postgres.setup", ".env.proxylogin"):
        (config_dir / name).write_text("")

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    ai_app_root = tmp_path / "ai-app"
    ai_app_root.mkdir()
    docker_dir = ai_app_root / "deployment" / "docker" / "custom-ui-managed-infra"
    docker_dir.mkdir(parents=True)

    assembly_path = config_dir / "assembly.yaml"
    assembly_path.write_text("x: 1\n")
    secrets_path = config_dir / "secrets.yaml"
    secrets_path.write_text("x: 1\n")
    bundles_path = config_dir / "bundles.yaml"
    bundles_path.write_text("bundles:\n  items: []\n")
    bundles_secrets_path = config_dir / "bundles.secrets.yaml"
    bundles_secrets_path.write_text("bundles:\n  items: []\n")
    gateway_path = config_dir / "gateway.yaml"
    gateway_path.write_text("gateway:\n  tenant: demo-tenant\n  project: demo-project\n")
    monkeypatch.setattr(
        "kdcube_cli.installer.compute_paths",
        lambda *_args, **_kwargs: {
            "host_kb_storage": str(tmp_path / "kdcube-storage"),
            "host_bundles": str(tmp_path / "bundles-root"),
            "host_git_bundles": str(tmp_path / "git-bundles"),
            "host_bundle_storage": str(tmp_path / "bundle-storage"),
            "host_exec_workspace": str(tmp_path / "exec-workspace"),
            "ui_build_context": str(ai_app_root),
            "ui_dockerfile_path": "Dockerfile_UI",
            "ui_source_path": "ui/chat-web-app",
            "ui_env_build_relative": ".env.ui.build",
            "nginx_ui_config": "nginx_ui.conf",
            "nginx_proxy_config": "nginx_proxy_ssl_cognito.conf",
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
    monkeypatch.setattr("kdcube_cli.installer.git_clone_or_update", lambda *_args, **_kwargs: ai_app_root)
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
            "platform": {"ref": "2026.4.19.999"},
            "secrets": {"provider": "secrets-file"},
            "paths": {
                "host_kdcube_storage_path": "/seed/storage",
                "host_bundles_path": "/seed/bundles",
                "host_git_bundles_path": "/seed/git-bundles",
                "host_bundle_storage_path": "/seed/bundle-storage",
                "host_exec_workspace_path": "/seed/exec-workspace",
            },
            "auth": {"type": "simple"},
            "proxy": {"ssl": False},
            "storage": {
                "workspace": {"type": "git", "repo": "https://github.com/example/workspace.git"},
                "claude_code_session": {"type": "git", "repo": "https://github.com/example/workspace.git"},
            },
            "platform": {
                "ref": "2026.4.19.999",
                "services": {
                    "ingress": {"log": {"log_dir": "/seed/ingress-logs"}},
                    "proc": {
                        "log": {"log_dir": "/seed/proc-logs"},
                        "exec": {"exec_workspace_root": "/seed/exec"},
                        "bundles": {
                            "agentic_bundles_root": "/seed/bundles-root",
                            "bundle_storage_root": "/seed/bundle-storage-root",
                        },
                    },
                },
            },
        },
        secrets_descriptor_path=str(secrets_path),
        secrets_descriptor={"services": {}},
        bundles_descriptor_path=str(bundles_path),
        bundles_descriptor={"bundles": {"items": []}},
        bundles_secrets_path=str(bundles_secrets_path),
        bundles_secrets_descriptor={"bundles": {"items": []}},
        gateway_descriptor={"gateway": {"tenant": "demo-tenant", "project": "demo-project"}},
        use_bundles_descriptor=True,
        use_bundles_secrets=True,
    )

    env_ingress = (config_dir / ".env.ingress").read_text()
    env_proc = (config_dir / ".env.proc").read_text()
    env_metrics = (config_dir / ".env.metrics").read_text()
    env_main = (config_dir / ".env").read_text()

    assert env_ingress.strip().splitlines() == [
        "GATEWAY_COMPONENT=ingress",
        "PLATFORM_DESCRIPTORS_DIR=/config",
    ]
    assert env_proc.strip().splitlines() == [
        "GATEWAY_COMPONENT=proc",
        "PLATFORM_DESCRIPTORS_DIR=/config",
    ]
    assert env_metrics.strip().splitlines() == [
        "GATEWAY_COMPONENT=proc",
        "PLATFORM_DESCRIPTORS_DIR=/config",
    ]
    assert f"HOST_GATEWAY_YAML_DESCRIPTOR_PATH={config_dir / 'gateway.yaml'}" in env_main
    assert f"HOST_KDCUBE_STORAGE_PATH={(tmp_path / 'kdcube-storage').resolve()}" in env_main
    assert f"HOST_BUNDLES_PATH={(tmp_path / 'bundles-root').resolve()}" in env_main
    assert f"HOST_GIT_BUNDLES_PATH={(tmp_path / 'git-bundles').resolve()}" in env_main
    assert f"HOST_BUNDLE_STORAGE_PATH={(tmp_path / 'bundle-storage').resolve()}" in env_main
    assert f"HOST_EXEC_WORKSPACE_PATH={(tmp_path / 'exec-workspace').resolve()}" in env_main
    assert "AGENTIC_BUNDLES_ROOT=/bundles" in env_main
    assert "AGENTIC_GIT_BUNDLES_ROOT=/git-bundles" in env_main
    assert "BUNDLE_STORAGE_ROOT=/bundle-storage" in env_main

    assembly_data = yaml.safe_load(assembly_path.read_text())
    assert assembly_data["paths"]["host_kdcube_storage_path"] == str((tmp_path / "kdcube-storage").resolve())
    assert assembly_data["paths"]["host_bundles_path"] == str((tmp_path / "bundles-root").resolve())
    assert assembly_data["paths"]["host_git_bundles_path"] == str((tmp_path / "git-bundles").resolve())
    assert assembly_data["paths"]["host_bundle_storage_path"] == str((tmp_path / "bundle-storage").resolve())
    assert assembly_data["paths"]["host_exec_workspace_path"] == str((tmp_path / "exec-workspace").resolve())
    assert assembly_data["platform"]["services"]["ingress"]["log"]["log_dir"] == "/logs"
    assert assembly_data["platform"]["services"]["proc"]["log"]["log_dir"] == "/logs"
    assert assembly_data["platform"]["services"]["proc"]["exec"]["exec_workspace_root"] == "/exec-workspace"
    assert assembly_data["platform"]["services"]["proc"]["bundles"]["agentic_bundles_root"] == "/bundles"
    assert assembly_data["platform"]["services"]["proc"]["bundles"]["bundle_storage_root"] == "/bundle-storage"


def test_gather_configuration_uses_descriptor_git_ssh_mounts(monkeypatch, tmp_path: Path):
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
    docker_dir = ai_app_root / "deployment" / "docker" / "custom-ui-managed-infra"
    docker_dir.mkdir(parents=True)

    ssh_key = tmp_path / "id_test"
    ssh_key.write_text("key")
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("github.com ssh-ed25519 AAAA")

    assembly_path = config_dir / "assembly.yaml"
    assembly_path.write_text("x: 1\n")
    secrets_path = config_dir / "secrets.yaml"
    secrets_path.write_text("services: {}\n")
    bundles_path = config_dir / "bundles.yaml"
    bundles_path.write_text("bundles:\n  items: []\n")
    bundles_secrets_path = config_dir / "bundles.secrets.yaml"
    bundles_secrets_path.write_text("bundles:\n  items: []\n")
    gateway_path = config_dir / "gateway.yaml"
    gateway_path.write_text("gateway:\n  tenant: demo-tenant\n  project: demo-project\n")

    monkeypatch.setattr(
        "kdcube_cli.installer.compute_paths",
        lambda *_args, **_kwargs: {
            "host_kb_storage": str(tmp_path / "kdcube-storage"),
            "host_bundles": str(tmp_path / "bundles-root"),
            "host_git_bundles": str(tmp_path / "git-bundles"),
            "host_bundle_storage": str(tmp_path / "bundle-storage"),
            "host_exec_workspace": str(tmp_path / "exec-workspace"),
            "ui_build_context": str(ai_app_root),
            "ui_dockerfile_path": "Dockerfile_UI",
            "ui_source_path": "ui/chat-web-app",
            "ui_env_build_relative": ".env.ui.build",
            "nginx_ui_config": "nginx_ui.conf",
            "nginx_proxy_config": "nginx_proxy_ssl_cognito.conf",
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
    monkeypatch.setattr("kdcube_cli.installer.git_clone_or_update", lambda *_args, **_kwargs: ai_app_root)
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
            "platform": {
                "ref": "2026.4.19.999",
                "applications": {
                    "bundles": {
                        "git": {
                            "git_ssh_key_path": "/run/secrets/git_ssh_key",
                            "git_ssh_known_hosts": "/run/secrets/git_known_hosts",
                        }
                    }
                },
            },
            "secrets": {"provider": "secrets-file"},
            "paths": {
                "host_bundles_path": str(tmp_path / "bundles-root"),
                "host_git_bundles_path": str(tmp_path / "git-bundles"),
                "host_bundle_storage_path": str(tmp_path / "bundle-storage"),
                "host_exec_workspace_path": str(tmp_path / "exec-workspace"),
                "host_git_ssh_key_path": str(ssh_key),
                "host_git_ssh_known_hosts_path": str(known_hosts),
            },
            "auth": {"type": "simple"},
            "proxy": {"ssl": False},
        },
        secrets_descriptor_path=str(secrets_path),
        secrets_descriptor={"services": {}},
        bundles_descriptor_path=str(bundles_path),
        bundles_descriptor={"bundles": {"items": []}},
        bundles_secrets_path=str(bundles_secrets_path),
        bundles_secrets_descriptor={"bundles": {"items": []}},
        gateway_descriptor={"gateway": {"tenant": "demo-tenant", "project": "demo-project"}},
        use_bundles_descriptor=True,
        use_bundles_secrets=True,
    )

    env_main = (config_dir / ".env").read_text()
    assert f"HOST_GIT_SSH_KEY_PATH={ssh_key}" in env_main
    assert f"HOST_GIT_KNOWN_HOSTS_PATH={known_hosts}" in env_main


def test_gather_configuration_rewrites_local_bundle_paths_into_staged_descriptor(monkeypatch, tmp_path: Path):
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
    docker_dir = ai_app_root / "deployment" / "docker" / "custom-ui-managed-infra"
    docker_dir.mkdir(parents=True)

    host_bundle_root = tmp_path / "source-bundles"
    bundle_root = host_bundle_root / "marketing" / "demo.bundle"
    bundle_root.mkdir(parents=True)
    bundle_root_2 = host_bundle_root / "ops" / "admin.bundle"
    bundle_root_2.mkdir(parents=True)

    assembly_path = config_dir / "assembly.yaml"
    assembly_path.write_text("x: 1\n")
    secrets_path = config_dir / "secrets.yaml"
    secrets_path.write_text("services: {}\n")
    bundles_path = config_dir / "bundles.yaml"
    bundles_path.write_text("bundles:\n  items: []\n")
    bundles_secrets_path = config_dir / "bundles.secrets.yaml"
    bundles_secrets_path.write_text("bundles:\n  items: []\n")
    gateway_path = config_dir / "gateway.yaml"
    gateway_path.write_text("gateway:\n  tenant: demo-tenant\n  project: demo-project\n")

    monkeypatch.setattr(
        "kdcube_cli.installer.compute_paths",
        lambda *_args, **_kwargs: {
            "host_kb_storage": str(tmp_path / "kdcube-storage"),
            "host_bundles": str(tmp_path / "bundles-root"),
            "host_git_bundles": str(tmp_path / "git-bundles"),
            "host_bundle_storage": str(tmp_path / "bundle-storage"),
            "host_exec_workspace": str(tmp_path / "exec-workspace"),
            "ui_build_context": str(ai_app_root),
            "ui_dockerfile_path": "Dockerfile_UI",
            "ui_source_path": "ui/chat-web-app",
            "ui_env_build_relative": ".env.ui.build",
            "nginx_ui_config": "nginx_ui.conf",
            "nginx_proxy_config": "nginx_proxy_ssl_cognito.conf",
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
    monkeypatch.setattr("kdcube_cli.installer.git_clone_or_update", lambda *_args, **_kwargs: ai_app_root)
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
            "platform": {"ref": "2026.4.19.999"},
            "secrets": {"provider": "secrets-file"},
            "auth": {"type": "simple"},
            "proxy": {"ssl": False},
        },
        secrets_descriptor_path=str(secrets_path),
        secrets_descriptor={"services": {}},
        bundles_descriptor_path=str(bundles_path),
        bundles_descriptor={
            "bundles": {
                "items": [
                    {
                        "id": "demo.bundle@1.0.0",
                        "path": str(bundle_root),
                        "module": "demo.entrypoint",
                    },
                    {
                        "id": "admin.bundle@1.0.0",
                        "path": str(bundle_root_2),
                        "module": "admin.entrypoint",
                    }
                ]
            }
        },
        bundles_secrets_path=str(bundles_secrets_path),
        bundles_secrets_descriptor={"bundles": {"items": []}},
        gateway_descriptor={"gateway": {"tenant": "demo-tenant", "project": "demo-project"}},
        use_bundles_descriptor=True,
        use_bundles_secrets=True,
    )

    env_main = (config_dir / ".env").read_text()
    assert f"HOST_BUNDLES_PATH={host_bundle_root.resolve()}" in env_main
    assert "AGENTIC_BUNDLES_ROOT=/bundles" in env_main

    assembly_data = yaml.safe_load(assembly_path.read_text())
    assert assembly_data["paths"]["host_bundles_path"] == str(host_bundle_root.resolve())

    bundles_data = yaml.safe_load(bundles_path.read_text())
    bundle_item = bundles_data["bundles"]["items"][0]
    bundle_item_2 = bundles_data["bundles"]["items"][1]
    assert bundle_item["path"] == "/bundles/marketing/demo.bundle"
    assert bundle_item_2["path"] == "/bundles/ops/admin.bundle"


def test_apply_runtime_secrets_to_file_descriptors_updates_secrets_files(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "secrets.yaml").write_text(
        """
git:
  http_token: legacy-token
""".strip()
    )
    (config_dir / "bundles.secrets.yaml").write_text(
        """
bundles:
  items:
    - id: demo-bundle
      secrets:
        existing:
          key: value
""".strip()
    )

    apply_runtime_secrets_to_file_descriptors(
        config_dir=config_dir,
        runtime_secrets={
            "services.git.http_token": "new-token",
            "services.git.http_user": "x-access-token",
            "bundles.demo-bundle.secrets.api.token": "abc",
            "bundles.demo-bundle.secrets.__keys": "[\"ignored\"]",
        },
    )

    secrets_data = yaml.safe_load((config_dir / "secrets.yaml").read_text())
    bundles_secrets_data = yaml.safe_load((config_dir / "bundles.secrets.yaml").read_text())

    assert secrets_data["services"]["git"]["http_token"] == "new-token"
    assert secrets_data["services"]["git"]["http_user"] == "x-access-token"
    items = bundles_secrets_data["bundles"]["items"]
    bundle_item = next(item for item in items if item["id"] == "demo-bundle")
    assert bundle_item["secrets"]["existing"]["key"] == "value"
    assert bundle_item["secrets"]["api"]["token"] == "abc"


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
                    "config": {"feature": {"enabled": True}},
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


def test_export_live_bundle_descriptors_prefers_local_descriptor_files(tmp_path: Path):
    bundles_path = tmp_path / "mounted-bundles.yaml"
    bundles_secrets_path = tmp_path / "mounted-bundles.secrets.yaml"
    bundles_path.write_text(
        """
bundles:
  version: "1"
  items:
    - id: demo.bundle
      path: /bundles/demo.bundle
      module: entrypoint
      config:
        feature:
          enabled: true
  default_bundle_id: demo.bundle
""".strip()
    )
    bundles_secrets_path.write_text(
        """
bundles:
  version: "1"
  items:
    - id: demo.bundle
      secrets:
        api:
          key: secret-1
""".strip()
    )

    out_dir = tmp_path / "out"
    export_mod.export_live_bundle_descriptors(
        Console(file=None),
        tenant="",
        project="",
        out_dir=out_dir,
        aws_region=None,
        aws_profile=None,
        aws_sm_prefix=None,
        bundles_path=bundles_path,
        bundles_secrets_path=bundles_secrets_path,
    )

    assert (out_dir / "bundles.yaml").read_text() == bundles_path.read_text()
    assert (out_dir / "bundles.secrets.yaml").read_text() == bundles_secrets_path.read_text()
