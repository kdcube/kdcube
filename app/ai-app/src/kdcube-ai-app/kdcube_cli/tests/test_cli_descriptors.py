import json
import subprocess
from pathlib import Path

import yaml
from rich.console import Console

from kdcube_cli import cli as cli_mod
from kdcube_cli.cli import (
    _export_platform_descriptors,
    _bootstrap_repo_for_defaults,
    _build_paths_for_repo,
    _canonical_descriptor_dir_from_initialized_workdir,
    _check_before_start,
    _collect_runtime_info,
    _collect_bundle_status,
    _bundle_reload_summary_lines,
    _cli_quiet_requested,
    _compose_running_services,
    _descriptor_fast_path_reasons,
    _compose_logs_dir_from_env,
    _load_bundle_ids_from_descriptor,
    _load_cli_defaults,
    _parse_init_secret_pairs,
    _copy_dirty_local_source,
    _print_json,
    _repo_path_from_install_meta,
    _bundle_apply_command,
    _resolve_cli_repo_path,
    _resolve_bundle_local_path_for_runtime,
    _resolve_subcommand_repo,
    _resolve_subcommand_workdir,
    _resolve_cli_workdir,
    _save_cli_defaults,
    apply_config_descriptors,
)
from kdcube_cli import export_live_bundles as export_mod
from kdcube_cli.installer import (
    PathsContext,
    apply_runtime_secrets_to_file_descriptors,
    build_ui_url,
    ensure_local_dirs,
    gather_configuration,
    render_nginx_frame_embedding_config,
    resolve_frontend_routes_prefix,
    stage_descriptor_directory,
    update_nginx_routes_prefix,
    ui_entry_path,
    write_frontend_config,
    workspace_namespace,
)


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)


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
    assert workspace_namespace("", None) == "default__default"


def test_ui_entry_path_uses_routes_prefix():
    assert ui_entry_path("/chatbot/demo") == "/chatbot/demo/chat"
    assert ui_entry_path(None) == "/chatbot/chat"


def test_build_ui_url_uses_routes_prefix():
    assert build_ui_url("5174", "/chatbot/demo") == "http://localhost:5174/chatbot/demo/chat"
    assert build_ui_url("80", None) == "http://localhost/chatbot/chat"


def test_parse_init_secret_pairs_accepts_dotted_keys_and_aliases():
    assert _parse_init_secret_pairs(
        [
            ["services.openai.api_key", "sk-openai"],
            ["ANTHROPIC_API_KEY", "sk-anthropic"],
        ]
    ) == {
        "services.openai.api_key": "sk-openai",
        "services.anthropic.api_key": "sk-anthropic",
    }


def test_ensure_local_dirs_creates_metrics_logs_dir(tmp_path: Path):
    data_dir = tmp_path / "data"
    logs_dir = tmp_path / "logs"

    ensure_local_dirs(data_dir, logs_dir)

    assert (logs_dir / "chat-ingress").is_dir()
    assert (logs_dir / "chat-proc").is_dir()
    assert (logs_dir / "metrics").is_dir()


def test_compose_logs_dir_from_env_uses_generated_compose_transport(tmp_path: Path):
    env_file = tmp_path / ".env"
    logs_dir = tmp_path / "runtime" / "logs"
    fallback = tmp_path / "fallback"
    env_file.write_text(f"KDCUBE_LOGS_DIR={logs_dir}\n", encoding="utf-8")

    assert _compose_logs_dir_from_env(env_file, fallback) == logs_dir.resolve()


def test_resolve_bundle_local_path_translates_host_path_under_runtime_root(tmp_path: Path):
    workdir = tmp_path / "runtime"
    host_root = tmp_path / "src"
    bundle_root = host_root / "apps" / "my-bundle"
    bundle_root.mkdir(parents=True)
    config_dir = workdir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "assembly.yaml").write_text(
        yaml.safe_dump(
            {
                "paths": {"host_bundles_path": str(host_root)},
                "platform": {
                    "services": {
                        "proc": {
                            "bundles": {
                                "bundles_root": "/bundles",
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    resolved, mode = _resolve_bundle_local_path_for_runtime(str(bundle_root), workdir)

    assert resolved == "/bundles/apps/my-bundle"
    assert mode == "translated"


def test_resolve_bundle_local_path_preserves_container_visible_path(tmp_path: Path):
    workdir = tmp_path / "runtime"
    config_dir = workdir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "assembly.yaml").write_text(
        yaml.safe_dump(
            {
                "paths": {"host_bundles_path": str(tmp_path / "src")},
                "platform": {"services": {"proc": {"bundles": {"bundles_root": "/bundles"}}}},
            }
        ),
        encoding="utf-8",
    )

    resolved, mode = _resolve_bundle_local_path_for_runtime("/bundles/apps/my-bundle", workdir)

    assert resolved == "/bundles/apps/my-bundle"
    assert mode == "container"


def test_resolve_bundle_local_path_preserves_managed_container_path(tmp_path: Path):
    workdir = tmp_path / "runtime"
    config_dir = workdir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "assembly.yaml").write_text(
        yaml.safe_dump(
            {
                "paths": {
                    "host_bundles_path": str(tmp_path / "src"),
                    "host_managed_bundles_path": str(tmp_path / "runtime" / "data" / "managed-bundles"),
                },
                "platform": {
                    "services": {
                        "proc": {
                            "bundles": {
                                "bundles_root": "/bundles",
                                "managed_bundles_root": "/managed-bundles",
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    resolved, mode = _resolve_bundle_local_path_for_runtime("/managed-bundles/demo.bundle__latest__abc", workdir)

    assert resolved == "/managed-bundles/demo.bundle__latest__abc"
    assert mode == "managed-container"


def test_resolve_bundle_local_path_translates_stale_managed_host_cache_path(tmp_path: Path):
    workdir = tmp_path / "runtime"
    managed_root = workdir / "data" / "managed-bundles"
    config_dir = workdir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "assembly.yaml").write_text(
        yaml.safe_dump(
            {
                "paths": {
                    "host_bundles_path": str(tmp_path / "src"),
                    "host_managed_bundles_path": str(managed_root),
                },
                "platform": {
                    "services": {
                        "proc": {
                            "bundles": {
                                "bundles_root": "/bundles",
                                "managed_bundles_root": "/managed-bundles",
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    stale_path = managed_root / "demo.bundle__old__missing"
    resolved, mode = _resolve_bundle_local_path_for_runtime(str(stale_path), workdir)

    assert resolved == "/managed-bundles/demo.bundle__old__missing"
    assert mode == "managed-translated"


def test_resolve_bundle_local_path_rejects_escaped_container_path(tmp_path: Path):
    workdir = tmp_path / "runtime"
    config_dir = workdir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "assembly.yaml").write_text(
        yaml.safe_dump(
            {
                "paths": {"host_bundles_path": str(tmp_path / "src")},
                "platform": {"services": {"proc": {"bundles": {"bundles_root": "/bundles"}}}},
            }
        ),
        encoding="utf-8",
    )

    try:
        _resolve_bundle_local_path_for_runtime("/bundles/../outside", workdir)
    except SystemExit as exc:
        assert "does not exist" in str(exc)
    else:
        raise AssertionError("escaped container path should not be accepted as runtime-visible")


def test_resolve_bundle_local_path_rejects_missing_host_path(tmp_path: Path):
    workdir = tmp_path / "runtime"
    config_dir = workdir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "assembly.yaml").write_text(
        yaml.safe_dump({"paths": {"host_bundles_path": str(tmp_path / "src")}}),
        encoding="utf-8",
    )

    try:
        _resolve_bundle_local_path_for_runtime(str(tmp_path / "src" / "missing"), workdir)
    except SystemExit as exc:
        assert "does not exist" in str(exc)
    else:
        raise AssertionError("missing host path should fail")


def test_resolve_bundle_local_path_rejects_host_path_outside_runtime_root(tmp_path: Path):
    workdir = tmp_path / "runtime"
    host_root = tmp_path / "src"
    outside = tmp_path / "other" / "my-bundle"
    outside.mkdir(parents=True)
    config_dir = workdir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "assembly.yaml").write_text(
        yaml.safe_dump({"paths": {"host_bundles_path": str(host_root)}}),
        encoding="utf-8",
    )

    try:
        _resolve_bundle_local_path_for_runtime(str(outside), workdir)
    except SystemExit as exc:
        message = str(exc)
        assert "not visible inside chat-proc" in message
        assert str(host_root.resolve()) in message
        assert "/bundles" in message
    else:
        raise AssertionError("host path outside runtime root should fail")


def test_bundle_apply_command_quotes_values_with_spaces(tmp_path: Path):
    assert _bundle_apply_command("bundle with space", tmp_path / "runtime dir") == (
        f"kdcube bundle reload 'bundle with space' --workdir '{tmp_path / 'runtime dir'}'"
    )


def test_cli_quiet_requested_for_json_quiet_env_and_non_tty(monkeypatch):
    assert _cli_quiet_requested(["bundle", "--help"], stdout_is_tty=False) is True
    assert _cli_quiet_requested(["--quiet", "info"], stdout_is_tty=True) is True
    assert _cli_quiet_requested(["info", "--json"], stdout_is_tty=True) is True

    monkeypatch.setenv("KDCUBE_CLI_QUIET", "1")
    assert _cli_quiet_requested(["info"], stdout_is_tty=True) is True
    monkeypatch.setenv("KDCUBE_CLI_QUIET", "0")
    assert _cli_quiet_requested(["info"], stdout_is_tty=True) is False


def test_print_json_emits_machine_readable_unwrapped_stdout(capsys):
    long_path = "/Users/elenaviter/.kdcube/kdcube-runtime/demo-tenant__demo-project/config/assembly.yaml"

    _print_json({"path": long_path, "status": "ok"})

    out = capsys.readouterr().out
    assert json.loads(out) == {"path": long_path, "status": "ok"}
    assert "\n" not in json.loads(out)["path"]


def test_bundle_reload_summary_hides_inner_compose_command(tmp_path: Path):
    lines = _bundle_reload_summary_lines(
        {
            "status": "ok",
            "authority": "file:/config/bundles.yaml",
            "broadcast_receivers": 1,
            "eviction": {"modules_removed": 3},
        },
        descriptor_path=tmp_path / "config" / "bundles.yaml",
        bundle_id="demo.bundle",
    )
    rendered = "\n".join(lines)

    assert "Bundle reload accepted." in rendered
    assert "demo.bundle" in rendered
    assert "docker compose" not in rendered
    assert "python -c" not in rendered
    assert "internal/bundles/reload-authority" not in rendered


def test_collect_bundle_status_reports_one_explicit_bundle_without_listing_others(tmp_path: Path):
    workdir = tmp_path / "runtime"
    config_dir = workdir / "config"
    host_root = tmp_path / "src"
    bundle_root = host_root / "apps" / "my-bundle"
    bundle_root.mkdir(parents=True)
    config_dir.mkdir(parents=True)
    (config_dir / ".env").write_text(
        f"HOST_BUNDLES_PATH={host_root}\n"
        "BUNDLES_ROOT=/bundles\n"
        "KDCUBE_COMPOSE_MODE=all-in-one\n",
        encoding="utf-8",
    )
    (config_dir / "assembly.yaml").write_text(
        yaml.safe_dump(
            {
                "paths": {"host_bundles_path": str(host_root)},
                "platform": {"services": {"proc": {"bundles": {"bundles_root": "/bundles"}}}},
            }
        ),
        encoding="utf-8",
    )
    (config_dir / "bundles.yaml").write_text(
        yaml.safe_dump(
            {
                "bundles": {
                    "default_bundle_id": "hidden.bundle",
                    "items": [
                        {
                            "id": "hidden.bundle",
                            "path": "/bundles/apps/hidden-bundle",
                            "module": "entrypoint",
                        },
                        {
                            "id": "my.bundle",
                            "path": "/bundles/apps/my-bundle",
                            "module": "entrypoint",
                            "singleton": False,
                        },
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    (config_dir / "bundles.secrets.yaml").write_text(
        yaml.safe_dump({"bundles": {"items": [{"id": "my.bundle", "secrets": {"token": "x"}}]}}),
        encoding="utf-8",
    )
    repo_root = tmp_path / "repo"
    ai_app_root = repo_root / "app" / "ai-app"
    (ai_app_root / "deployment" / "docker" / "all_in_one_kdcube").mkdir(parents=True)
    (ai_app_root / "deployment" / "docker" / "all_in_one_kdcube" / "docker-compose.yaml").write_text("")
    (ai_app_root / "src" / "kdcube-ai-app" / "kdcube_ai_app").mkdir(parents=True)

    status = _collect_bundle_status(
        repo_root=repo_root,
        workdir=workdir,
        bundle_id="my.bundle",
        include_live=False,
    )

    assert status["declared"] is True
    assert status["runtime_path"] == "/bundles/apps/my-bundle"
    assert status["host_path"] == str(bundle_root)
    assert status["host_path_exists"] is True
    assert status["secrets_declared"] is True
    assert "known_bundle_ids" not in status
    assert "hidden.bundle" not in json.dumps(status)


def test_resolve_frontend_routes_prefix_reads_generated_config(tmp_path: Path):
    config = tmp_path / "frontend.config.delegated.json"
    config.write_text('{"routesPrefix":"/chatbot/demo"}')

    assert resolve_frontend_routes_prefix(str(config)) == "/chatbot/demo"
    assert resolve_frontend_routes_prefix(str(tmp_path / "missing.json")) is None


def test_write_frontend_config_uses_cli_frontend_config_module(tmp_path: Path):
    template = tmp_path / "template.json"
    template.write_text(
        json.dumps(
            {
                "routesPrefix": "/chatbot",
                "auth": {"authType": "hardcoded", "token": "test-admin-token-123"},
                "debug": {"injectDebugCommands": True},
            }
        ),
        encoding="utf-8",
    )
    target = tmp_path / "frontend.config.json"

    write_frontend_config(
        target,
        "tenant-one",
        "project-one",
        template_path=template,
        routes_prefix="/platform",
        assembly={
            "auth": {"type": "simple"},
            "frontend": {"config": {"debug": {"animateStreaming": True}}},
        },
    )

    config = json.loads(target.read_text())
    assert config["tenant"] == "tenant-one"
    assert config["project"] == "project-one"
    assert config["routesPrefix"] == "/platform"
    assert config["auth"]["authType"] == "simple"
    assert config["debug"] == {"injectDebugCommands": True, "animateStreaming": True}


def test_update_nginx_routes_prefix_adds_prefix_root_redirect(tmp_path: Path):
    nginx = tmp_path / "nginx.conf"
    nginx.write_text(
        "server {\n"
        "    location = / {\n"
        "        return 301 /chatbot/chat;\n"
        "    }\n"
        "    location / {\n"
        "        proxy_pass http://web_ui;\n"
        "    }\n"
        "}\n"
    )

    update_nginx_routes_prefix(nginx, "/chatbot/demo")

    updated = nginx.read_text()
    assert "return 301 /chatbot/demo/chat;" in updated
    assert "location = /chatbot/demo {" in updated


def test_render_nginx_frame_embedding_defaults_to_standalone_shell_and_same_origin_frames():
    template = (
        "server {\n"
        "        # KDCUBE_FRAME_EMBEDDING:SHELL\n"
        "        more_set_headers \"X-Frame-Options: DENY\";\n"
        "        # /KDCUBE_FRAME_EMBEDDING:SHELL\n"
        "        location /api/integrations/bundles/ {\n"
        "            # KDCUBE_FRAME_EMBEDDING:FRAMEABLE\n"
        "            more_set_headers \"X-Frame-Options: SAMEORIGIN\";\n"
        "            # /KDCUBE_FRAME_EMBEDDING:FRAMEABLE\n"
        "        }\n"
        "}\n"
    )

    rendered = render_nginx_frame_embedding_config(
        template,
        {"proxy": {"frame_embedding": {"mode": "standalone"}}},
    )

    assert rendered.count('more_clear_headers "X-Frame-Options";') == 2
    assert "X-Frame-Options: DENY" in rendered
    assert "X-Frame-Options: SAMEORIGIN" in rendered
    assert "frame-ancestors" not in rendered


def test_render_nginx_frame_embedding_allowlist_uses_csp_for_shell_and_nested_frames():
    template = (
        "server {\n"
        "        # KDCUBE_FRAME_EMBEDDING:SHELL\n"
        "        more_set_headers \"X-Frame-Options: DENY\";\n"
        "        # /KDCUBE_FRAME_EMBEDDING:SHELL\n"
        "        location /api/integrations/static/ {\n"
        "            # KDCUBE_FRAME_EMBEDDING:FRAMEABLE\n"
        "            more_set_headers \"X-Frame-Options: SAMEORIGIN\";\n"
        "            # /KDCUBE_FRAME_EMBEDDING:FRAMEABLE\n"
        "        }\n"
        "}\n"
    )

    rendered = render_nginx_frame_embedding_config(
        template,
        {
            "proxy": {
                "frame_embedding": {
                    "mode": "allowlist",
                    "allowed_origins": ["https://host.example.com/path", "host2.example.com"],
                }
            }
        },
    )

    assert rendered.count('more_clear_headers "X-Frame-Options";') == 2
    assert "Content-Security-Policy: frame-ancestors 'self' https://host.example.com https://host2.example.com" in rendered
    assert "X-Frame-Options: DENY" not in rendered
    assert "X-Frame-Options: SAMEORIGIN" not in rendered


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
    _init_git_repo(repo_dir)
    (config_dir / "install-meta.json").write_text(
        json.dumps({"repo_root": str(repo_dir.resolve())})
    )

    resolved = _resolve_cli_repo_path(
        tmp_path / "ignored-default",
        workdir=tmp_path / "workspace",
        path_provided=False,
    )
    assert resolved == repo_dir.resolve()


def test_repo_path_from_install_meta_accepts_staged_platform_source_without_git(tmp_path: Path):
    runtime_dir = tmp_path / "workspace" / "demo_tenant__project_one"
    config_dir = runtime_dir / "config"
    config_dir.mkdir(parents=True)
    staged_repo = runtime_dir / "platform-source" / "kdcube-ai-app"
    (staged_repo / "app" / "ai-app" / "deployment").mkdir(parents=True)
    (config_dir / "install-meta.json").write_text(json.dumps({"repo_root": str(staged_repo.resolve())}))

    resolved = _repo_path_from_install_meta(runtime_dir)

    assert resolved == staged_repo.resolve()


def test_subcommand_repo_uses_install_meta_after_base_workdir_resolves_to_runtime(tmp_path: Path):
    base_workdir = tmp_path / "workspace"
    runtime_dir = base_workdir / "demo_tenant__project_one"
    config_dir = runtime_dir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / ".env").write_text("")
    staged_repo = runtime_dir / "platform-source" / "kdcube-ai-app"
    (staged_repo / "app" / "ai-app" / "deployment").mkdir(parents=True)
    (config_dir / "install-meta.json").write_text(json.dumps({"repo_root": str(staged_repo.resolve())}))

    resolved_workdir = _resolve_cli_workdir(base_workdir)
    resolved_repo = _resolve_subcommand_repo(tmp_path / "default-repo", workdir=resolved_workdir)

    assert resolved_workdir == runtime_dir.resolve()
    assert resolved_repo == staged_repo.resolve()


def test_subcommand_repo_explicit_path_overrides_install_meta(tmp_path: Path):
    runtime_dir = tmp_path / "workspace" / "demo__project"
    config_dir = runtime_dir / "config"
    config_dir.mkdir(parents=True)
    staged_repo = runtime_dir / "repo"
    explicit_repo = tmp_path / "source"
    (staged_repo / "app" / "ai-app" / "deployment").mkdir(parents=True)
    (explicit_repo / "app" / "ai-app" / "deployment").mkdir(parents=True)
    (config_dir / "install-meta.json").write_text(json.dumps({"repo_root": str(staged_repo.resolve())}))

    resolved_repo = _resolve_subcommand_repo(str(explicit_repo), workdir=runtime_dir, path_provided=True)

    assert resolved_repo == explicit_repo.resolve()


def test_copy_dirty_local_source_copies_tracked_and_untracked_nonignored_files(tmp_path: Path):
    source_repo = tmp_path / "source"
    _init_git_repo(source_repo)
    (source_repo / ".gitignore").write_text("ignored.txt\nignored-dir/\n", encoding="utf-8")
    tracked = source_repo / "app" / "ai-app" / "deployment" / "assembly.yaml"
    tracked.parent.mkdir(parents=True)
    tracked.write_text("context: {}\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore", str(tracked.relative_to(source_repo))], cwd=source_repo, check=True)
    untracked = source_repo / "app" / "ai-app" / "README.md"
    untracked.write_text("local change\n", encoding="utf-8")
    (source_repo / "ignored.txt").write_text("secret local data\n", encoding="utf-8")
    ignored_dir_file = source_repo / "ignored-dir" / "data.txt"
    ignored_dir_file.parent.mkdir()
    ignored_dir_file.write_text("secret local data\n", encoding="utf-8")

    workdir = tmp_path / "runtime"
    copied_repo = _copy_dirty_local_source(Console(file=None), source_repo=source_repo, workdir=workdir)

    assert (copied_repo / "app" / "ai-app" / "deployment" / "assembly.yaml").read_text(encoding="utf-8") == "context: {}\n"
    assert (copied_repo / "app" / "ai-app" / "README.md").read_text(encoding="utf-8") == "local change\n"
    assert not (copied_repo / ".git").exists()
    assert not (copied_repo / "ignored.txt").exists()
    assert not (copied_repo / "ignored-dir" / "data.txt").exists()


def test_copy_dirty_local_source_noops_when_source_is_runtime_repo(tmp_path: Path):
    workdir = tmp_path / "runtime"
    source_repo = workdir / "repo"
    _init_git_repo(source_repo)
    tracked = source_repo / "app" / "ai-app" / "deployment" / "assembly.yaml"
    tracked.parent.mkdir(parents=True)
    tracked.write_text("context: {}\n", encoding="utf-8")
    subprocess.run(["git", "add", str(tracked.relative_to(source_repo))], cwd=source_repo, check=True)

    copied_repo = _copy_dirty_local_source(Console(file=None), source_repo=source_repo, workdir=workdir)

    assert copied_repo == source_repo.resolve()
    assert tracked.exists()


def test_copy_dirty_local_source_restores_ui_nginx_build_config(tmp_path: Path):
    source_repo = tmp_path / "source"
    _init_git_repo(source_repo)
    nginx_source = source_repo / "app" / "ai-app" / "deployment" / "docker" / "custom-ui-managed-infra" / "nginx" / "conf" / "nginx_ui.conf"
    nginx_source.parent.mkdir(parents=True)
    nginx_source.write_text("events {}\n", encoding="utf-8")
    tracked = source_repo / "app" / "ai-app" / "deployment" / "assembly.yaml"
    tracked.write_text("context: {}\n", encoding="utf-8")
    subprocess.run(["git", "add", str(tracked.relative_to(source_repo)), str(nginx_source.relative_to(source_repo))], cwd=source_repo, check=True)

    workdir = tmp_path / "runtime"
    config_dir = workdir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / ".env").write_text(
        "\n".join(
            [
                "KDCUBE_COMPOSE_MODE=custom-ui-managed-infra",
                f"UI_BUILD_CONTEXT={workdir / 'repo' / 'app' / 'ai-app'}",
                "NGINX_UI_CONFIG_FILE_PATH=.kdcube/nginx_ui.conf",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    copied_repo = _copy_dirty_local_source(Console(file=None), source_repo=source_repo, workdir=workdir)

    assert (
        copied_repo / "app" / "ai-app" / ".kdcube" / "nginx_ui.conf"
    ).read_text(encoding="utf-8") == "events {}\n"


def test_copy_dirty_local_source_accepts_git_worktree_with_git_file(tmp_path: Path):
    source_repo = tmp_path / "source"
    _init_git_repo(source_repo)
    tracked = source_repo / "app" / "ai-app" / "deployment" / "assembly.yaml"
    tracked.parent.mkdir(parents=True)
    tracked.write_text("context: {}\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", str(tracked.relative_to(source_repo))],
        cwd=source_repo,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test User",
            "commit",
            "-m",
            "init",
        ],
        cwd=source_repo,
        check=True,
        capture_output=True,
        text=True,
    )

    linked_worktree = tmp_path / "linked-worktree"
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(linked_worktree), "HEAD"],
        cwd=source_repo,
        check=True,
        capture_output=True,
        text=True,
    )
    assert (linked_worktree / ".git").is_file()

    untracked = linked_worktree / "app" / "ai-app" / "README.md"
    untracked.write_text("local worktree change\n", encoding="utf-8")

    copied_repo = _copy_dirty_local_source(
        Console(file=None),
        source_repo=linked_worktree,
        workdir=tmp_path / "runtime",
    )

    assert (
        copied_repo / "app" / "ai-app" / "deployment" / "assembly.yaml"
    ).read_text(encoding="utf-8") == "context: {}\n"
    assert (
        copied_repo / "app" / "ai-app" / "README.md"
    ).read_text(encoding="utf-8") == "local worktree change\n"
    assert not (copied_repo / ".git").exists()


def test_canonical_descriptor_dir_from_initialized_workdir_uses_runtime_config(tmp_path: Path):
    runtime_dir = tmp_path / "workspace" / "demo_tenant__project_one"
    config_dir = runtime_dir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / ".env").write_text("")
    (config_dir / "install-meta.json").write_text(json.dumps({"repo_root": str((runtime_dir / "repo").resolve())}))
    for name in ("assembly.yaml", "secrets.yaml", "bundles.yaml", "bundles.secrets.yaml", "gateway.yaml"):
        (config_dir / name).write_text("x: 1\n")

    resolved = _canonical_descriptor_dir_from_initialized_workdir(tmp_path / "workspace")

    assert resolved == config_dir.resolve()


def test_canonical_descriptor_dir_from_initialized_workdir_requires_install_meta(tmp_path: Path):
    runtime_dir = tmp_path / "workspace" / "demo_tenant__project_one"
    config_dir = runtime_dir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / ".env").write_text("")
    for name in ("assembly.yaml", "secrets.yaml", "bundles.yaml", "bundles.secrets.yaml", "gateway.yaml"):
        (config_dir / name).write_text("x: 1\n")

    resolved = _canonical_descriptor_dir_from_initialized_workdir(tmp_path / "workspace")

    assert resolved is None


def test_canonical_descriptor_dir_from_initialized_workdir_requires_complete_descriptor_set(tmp_path: Path):
    runtime_dir = tmp_path / "workspace" / "demo_tenant__project_one"
    config_dir = runtime_dir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / ".env").write_text("")
    (config_dir / "install-meta.json").write_text(json.dumps({"repo_root": str((runtime_dir / "repo").resolve())}))
    for name in ("assembly.yaml", "secrets.yaml", "bundles.yaml"):
        (config_dir / name).write_text("x: 1\n")

    resolved = _canonical_descriptor_dir_from_initialized_workdir(tmp_path / "workspace")

    assert resolved is None


def test_compose_running_services_uses_runtime_docker_dir(monkeypatch, tmp_path: Path):
    docker_dir = tmp_path / "docker"
    docker_dir.mkdir()
    env_file = tmp_path / ".env"
    env_file.write_text("")
    seen: dict[str, object] = {}

    def _fake_output(cmd, env=None, *, cwd=None, timeout=None):
        seen["cmd"] = cmd
        seen["env"] = env
        seen["cwd"] = cwd
        seen["timeout"] = timeout
        return "chat-proc\nchat-ingress\n"

    monkeypatch.setattr("kdcube_cli.cli._docker_output", _fake_output)

    result = _compose_running_services(docker_dir, env_file)

    assert result == {"chat-proc", "chat-ingress"}
    assert seen["cwd"] == docker_dir
    assert seen["timeout"] == 20


def test_build_paths_for_repo_uses_runtime_compose_mode(tmp_path: Path):
    workdir = tmp_path / "runtime"
    config_dir = workdir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / ".env").write_text("KDCUBE_COMPOSE_MODE=custom-ui-managed-infra\n")

    repo_root = tmp_path / "repo"
    ai_app_root = repo_root / "app" / "ai-app"
    (ai_app_root / "deployment" / "docker" / "all_in_one_kdcube").mkdir(parents=True)
    (ai_app_root / "deployment" / "docker" / "custom-ui-managed-infra").mkdir(parents=True)
    (ai_app_root / "deployment" / "docker" / "all_in_one_kdcube" / "docker-compose.yaml").write_text("")
    (ai_app_root / "deployment" / "docker" / "custom-ui-managed-infra" / "docker-compose.yaml").write_text("")
    (ai_app_root / "src" / "kdcube-ai-app" / "kdcube_ai_app").mkdir(parents=True)

    ctx = _build_paths_for_repo(repo_root, workdir)

    assert ctx.docker_dir == ai_app_root / "deployment" / "docker" / "custom-ui-managed-infra"


def test_collect_runtime_info_reports_bundle_mount_mapping(tmp_path: Path):
    workdir = tmp_path / "runtime"
    config_dir = workdir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / ".env").write_text(
        "\n".join(
            [
                "HOST_BUNDLES_PATH=/host/bundles",
                "BUNDLES_ROOT=/bundles",
                "HOST_MANAGED_BUNDLES_PATH=/host/managed-bundles",
                "MANAGED_BUNDLES_ROOT=/managed-bundles",
                "HOST_BUNDLE_STORAGE_PATH=/host/bundle-storage",
                "BUNDLE_STORAGE_ROOT=/bundle-storage",
                "HOST_EXEC_WORKSPACE_PATH=/host/exec-workspace",
                "EXEC_WORKSPACE_ROOT=/exec-workspace",
                "HOST_REACT_DEBUG_PATH=/host/react-debug",
                "REACT_DEBUG_ROOT=/react-debug",
                "KDCUBE_LOGS_DIR=/host/logs",
                "KDCUBE_COMPOSE_MODE=all-in-one",
            ]
        )
        + "\n"
    )
    (config_dir / "assembly.yaml").write_text("context:\n  tenant: demo\n  project: project-one\n")
    (config_dir / "bundles.yaml").write_text("bundles:\n  default_bundle_id: demo.bundle\n  items: []\n")
    (config_dir / "install-meta.json").write_text(json.dumps({"install_mode": "upstream", "platform_ref": "abc123"}))

    repo_root = tmp_path / "repo"
    ai_app_root = repo_root / "app" / "ai-app"
    (ai_app_root / "deployment" / "docker" / "all_in_one_kdcube").mkdir(parents=True)
    (ai_app_root / "deployment" / "docker" / "all_in_one_kdcube" / "docker-compose.yaml").write_text("")
    (ai_app_root / "src" / "kdcube-ai-app" / "kdcube_ai_app").mkdir(parents=True)

    info = _collect_runtime_info(repo_root=repo_root, workdir=workdir)

    assert info["host_bundles_path"] == "/host/bundles"
    assert info["container_bundles_root"] == "/bundles"
    assert info["host_managed_bundles_path"] == "/host/managed-bundles"
    assert info["container_managed_bundles_root"] == "/managed-bundles"
    assert info["host_exec_workspace_path"] == "/host/exec-workspace"
    assert info["container_exec_workspace_root"] == "/exec-workspace"
    assert info["host_react_debug_path"] == "/host/react-debug"
    assert info["container_react_debug_root"] == "/react-debug"
    assert info["logs_dir"] == "/host/logs"
    assert info["default_bundle_id"] == "demo.bundle"
    assert info["bundle_count"] == 0
    assert info["tenant"] == "demo"
    assert info["project"] == "project-one"


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


def test_load_bundle_ids_from_bundles_items_yaml(tmp_path: Path):
    path = tmp_path / "bundles.yaml"
    path.write_text(
        """
bundles:
  version: "1"
  default_bundle_id: demo.bundle@1.0.0
  items:
    - id: demo.bundle@1.0.0
      path: /bundles/demo
      module: demo.entrypoint
    - id: another.bundle@2.0.0
      repo: git@example.com:repo.git
      ref: main
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

    assembly_path = config_dir / "assembly.yaml"
    secrets_path = config_dir / "secrets.yaml"
    bundles_path = config_dir / "bundles.yaml"
    bundles_secrets_path = config_dir / "bundles.secrets.yaml"
    gateway_path = tmp_path / "gateway.yaml"
    for p in (assembly_path, secrets_path, bundles_path, bundles_secrets_path, gateway_path):
        p.write_text("x: 1\n")

    monkeypatch.setattr(
        "kdcube_cli.installer.compute_paths",
        lambda *_args, **_kwargs: {
            "host_kb_storage": str(tmp_path / "kdcube-storage"),
            "host_bundles": str(tmp_path / "bundles"),
            "host_managed_bundles": str(tmp_path / "managed-bundles"),
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
    assert "HOST_SECRETS_YAML_DESCRIPTOR_PATH=" not in env_main
    assert "HOST_BUNDLES_SECRETS_YAML_DESCRIPTOR_PATH=" not in env_main
    assert (tmp_path / "kdcube-storage").is_dir()
    assert (tmp_path / "bundles").is_dir()
    assert (tmp_path / "managed-bundles").is_dir()
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

    assembly_path = config_dir / "assembly.yaml"
    assembly_path.write_text("x: 1\n")
    secrets_path = config_dir / "secrets.yaml"
    secrets_path.write_text("x: 1\n")
    bundles_path = config_dir / "bundles.yaml"
    bundles_path.write_text("bundles:\n  items: []\n")
    bundles_secrets_path = config_dir / "bundles.secrets.yaml"
    bundles_secrets_path.write_text("bundles:\n  items: []\n")

    monkeypatch.setattr(
        "kdcube_cli.installer.compute_paths",
        lambda *_args, **_kwargs: {
            "host_kb_storage": str(tmp_path / "kdcube-storage"),
            "host_bundles": str(tmp_path / "bundles"),
            "host_managed_bundles": str(tmp_path / "managed-bundles"),
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
    assert env_ingress.strip().splitlines() == [
        "GATEWAY_COMPONENT=ingress",
        "PLATFORM_DESCRIPTORS_DIR=/config",
    ]
    assert "redis://:redispass@" not in env_ingress


def test_gather_configuration_keeps_service_env_minimal_with_platform_descriptors(monkeypatch, tmp_path: Path):
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

    assembly_path = config_dir / "assembly.yaml"
    assembly_path.write_text("x: 1\n")
    secrets_path = config_dir / "secrets.yaml"
    secrets_path.write_text("x: 1\n")
    bundles_path = config_dir / "bundles.yaml"
    bundles_path.write_text("bundles:\n  items: []\n")
    bundles_secrets_path = config_dir / "bundles.secrets.yaml"
    bundles_secrets_path.write_text("bundles:\n  items: []\n")

    monkeypatch.setattr(
        "kdcube_cli.installer.compute_paths",
        lambda *_args, **_kwargs: {
            "host_kb_storage": str(tmp_path / "kdcube-storage"),
            "host_bundles": str(tmp_path / "bundles"),
            "host_managed_bundles": str(tmp_path / "managed-bundles"),
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
                            "gateway_config_force_env_on_startup": True,
                            "chat_scheduler_backend": "legacy_lists",
                            "chat_task_timeout_sec": 600,
                            "chat_task_idle_timeout_sec": 900,
                            "chat_task_max_wall_time_sec": 3600,
                            "chat_task_watchdog_poll_interval_sec": 0.5,
                        },
                        "tools": {
                            "web_search": {
                                "tools_web_search_fetch_content": True,
                                "web_search_primary_backend": "brave",
                                "web_search_backend": "hybrid",
                                "web_search_hybrid_mode": "sequential",
                                "web_search_segmenter": "fast",
                                "web_favicon_enrich_enabled": False,
                                "web_favicon_enrich_timeout_s": 2.5,
                            },
                            "web_fetch": {
                                "web_fetch_resources_medium": '{"cookies": {"sid": "abc"}}',
                            },
                            "mcp_cache_ttl_seconds": 120,
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
    assembly_data = yaml.safe_load(assembly_path.read_text())

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
    assert assembly_data["platform"]["services"]["ingress"]["service"]["cb_relay_identity"] == "relay.ingress"
    assert assembly_data["platform"]["services"]["proc"]["service"]["cb_relay_identity"] == "relay.proc"
    assert assembly_data["platform"]["services"]["proc"]["tools"]["web_search"]["web_favicon_enrich_enabled"] is False
    assert assembly_data["platform"]["services"]["proc"]["tools"]["web_search"]["web_favicon_enrich_timeout_s"] == 2.5


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
            "host_managed_bundles": str(tmp_path / "managed-bundles"),
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
            "auth": {
                "type": "delegated",
                "proxy_login": {
                    "redis_key_prefix": "proxylogin:<TENANT>:<PROJECT>:",
                    "token_masquerade": True,
                    "enforce_mfa": True,
                    "password_reset": {
                        "company": "KDCube",
                        "sender": "info@example.com",
                        "template_name": "KDCubeNewUserWelcomeTemplate",
                        "redirect_url": "http://YOUR_DOMAIN/platform/reset-password?user=%[1]s",
                    },
                    "http_urlbase": "http://YOUR_DOMAIN/auth",
                },
            },
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
    env_proxy = (config_dir / ".env.proxylogin").read_text()
    assert "REDIS_KEYPREFIX=proxylogin:demo-tenant:demo-project:" in env_proxy
    assert "COGNITO_ENFORCEMFA=true" in env_proxy
    assert "PASSWORD_RESET_REDIRECTURL=http://ai.example.com/platform/reset-password?user=%[1]s" in env_proxy
    assert "HTTP_URLBASE=http://ai.example.com/auth" in env_proxy
    assembly_data = yaml.safe_load(assembly_path.read_text())
    proxy_login = assembly_data["auth"]["proxy_login"]
    assert proxy_login["redis_key_prefix"] == "proxylogin:demo-tenant:demo-project:"
    assert proxy_login["enforce_mfa"] is True
    assert proxy_login["password_reset"]["redirect_url"] == "http://ai.example.com/platform/reset-password?user=%[1]s"
    assert proxy_login["http_urlbase"] == "http://ai.example.com/auth"


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
            "host_managed_bundles": str(tmp_path / "managed-bundles"),
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
                "host_managed_bundles_path": "/seed/managed-bundles",
                "host_bundle_storage_path": "/seed/bundle-storage",
                "host_exec_workspace_path": "/seed/exec-workspace",
            },
            "auth": {"type": "simple"},
            "proxy": {"ssl": False},
            "storage": {
                "kdcube": f"file://{tmp_path / 'seed-kdcube'}",
                "bundles": f"file://{tmp_path / 'seed-bundles'}",
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
                            "bundles_root": "/seed/bundles-root",
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
    assert "HOST_GATEWAY_YAML_DESCRIPTOR_PATH=" not in env_main
    assert f"HOST_KDCUBE_STORAGE_PATH={(tmp_path / 'seed-kdcube').resolve()}" in env_main
    assert f"HOST_BUNDLES_PATH={(tmp_path / 'bundles-root').resolve()}" in env_main
    assert f"HOST_MANAGED_BUNDLES_PATH={(tmp_path / 'managed-bundles').resolve()}" in env_main
    assert f"HOST_BUNDLE_STORAGE_PATH={(tmp_path / 'seed-bundles').resolve()}" in env_main
    assert f"HOST_EXEC_WORKSPACE_PATH={(tmp_path / 'exec-workspace').resolve()}" in env_main
    assert f"HOST_REACT_DEBUG_PATH={(workdir / 'data/react-debug').resolve()}" in env_main
    assert "REACT_DEBUG_ROOT=/react-debug" in env_main
    assert "REACT_DEBUG_KEEP_FILES=100" in env_main
    assert f"KDCUBE_CONFIG_DIR={config_dir}" in env_main
    assert "BUNDLES_ROOT=/bundles" in env_main
    assert "MANAGED_BUNDLES_ROOT=/managed-bundles" in env_main
    assert "BUNDLE_STORAGE_ROOT=/bundle-storage" in env_main

    assembly_data = yaml.safe_load(assembly_path.read_text())
    assert assembly_data["storage"]["kdcube"] == "file:///kdcube-storage"
    assert assembly_data["storage"]["bundles"] == "file:///bundle-storage"
    assert assembly_data["paths"]["host_kdcube_storage_path"] == str((tmp_path / "seed-kdcube").resolve())
    assert assembly_data["paths"]["host_bundles_path"] == str((tmp_path / "bundles-root").resolve())
    assert assembly_data["paths"]["host_managed_bundles_path"] == str((tmp_path / "managed-bundles").resolve())
    assert assembly_data["paths"]["host_bundle_storage_path"] == str((tmp_path / "seed-bundles").resolve())
    assert assembly_data["paths"]["host_exec_workspace_path"] == str((tmp_path / "exec-workspace").resolve())
    assert assembly_data["paths"]["host_react_debug_path"] == str((workdir / "data/react-debug").resolve())
    assert assembly_data["platform"]["services"]["ingress"]["log"]["log_dir"] == "/logs"
    assert assembly_data["platform"]["services"]["proc"]["log"]["log_dir"] == "/logs"
    assert assembly_data["platform"]["services"]["proc"]["exec"]["exec_workspace_root"] == "/exec-workspace"
    assert assembly_data["platform"]["services"]["proc"]["react_debug"]["debug_root"] == "/react-debug"
    assert assembly_data["platform"]["services"]["proc"]["react_debug"]["keep_files"] == 100
    assert assembly_data["platform"]["services"]["proc"]["bundles"]["bundles_root"] == "/bundles"
    assert assembly_data["platform"]["services"]["proc"]["bundles"]["bundle_storage_root"] == "/bundle-storage"


def test_gather_configuration_resolves_null_missing_and_s3_storage(monkeypatch, tmp_path: Path):
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

    def run_case(name: str, storage: dict) -> tuple[str, dict, Path]:
        root = tmp_path / name
        config_dir = root / "config"
        config_dir.mkdir(parents=True)
        for env_name in (
            ".env",
            ".env.ingress",
            ".env.proc",
            ".env.metrics",
            ".env.postgres.setup",
            ".env.proxylogin",
        ):
            (config_dir / env_name).write_text("")

        workdir = root / "workdir"
        workdir.mkdir()
        ai_app_root = root / "ai-app"
        ai_app_root.mkdir()
        docker_dir = ai_app_root / "deployment" / "docker" / "custom-ui-managed-infra"
        docker_dir.mkdir(parents=True)

        assembly_path = config_dir / "assembly.yaml"
        assembly_path.write_text("x: 1\n")
        secrets_path = config_dir / "secrets.yaml"
        secrets_path.write_text("services: {}\n")
        bundles_path = config_dir / "bundles.yaml"
        bundles_path.write_text("bundles:\n  items: []\n")
        bundles_secrets_path = config_dir / "bundles.secrets.yaml"
        bundles_secrets_path.write_text("bundles:\n  items: []\n")

        monkeypatch.setattr(
            "kdcube_cli.installer.compute_paths",
            lambda *_args, **_kwargs: {
                "host_kb_storage": str(workdir / "data/kdcube-storage"),
                "host_bundles": str(workdir / "data/bundles"),
                "host_managed_bundles": str(workdir / "data/managed-bundles"),
                "host_bundle_storage": str(workdir / "data/bundle-storage"),
                "host_exec_workspace": str(workdir / "data/exec-workspace"),
                "host_react_debug": str(workdir / "data/react-debug"),
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

        ctx = PathsContext(
            lib_root=root / "lib",
            ai_app_root=ai_app_root,
            docker_dir=docker_dir,
            sample_env_dir=root / "sample_env",
            workdir=workdir,
            config_dir=config_dir,
            data_dir=root / "data",
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
                    "host_kdcube_storage_path": None,
                    "host_bundle_storage_path": None,
                    "host_exec_workspace_path": None,
                    "host_react_debug_path": None,
                },
                "auth": {"type": "simple"},
                "proxy": {"ssl": False},
                "storage": {
                    **storage,
                    "workspace": {"type": "local", "repo": ""},
                    "claude_code_session": {"type": "local", "repo": ""},
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
        return (
            (config_dir / ".env").read_text(),
            yaml.safe_load(assembly_path.read_text()),
            workdir,
        )

    env_main, assembly_data, workdir = run_case(
        "local-null",
        {"kdcube": None, "bundles": None},
    )
    assert f"HOST_KDCUBE_STORAGE_PATH={workdir / 'data/kdcube-storage'}" in env_main
    assert f"HOST_BUNDLE_STORAGE_PATH={workdir / 'data/bundle-storage'}" in env_main
    assert assembly_data["storage"]["kdcube"] == "file:///kdcube-storage"
    assert assembly_data["storage"]["bundles"] == "file:///bundle-storage"

    env_main, assembly_data, workdir = run_case("local-missing", {})
    assert f"HOST_KDCUBE_STORAGE_PATH={workdir / 'data/kdcube-storage'}" in env_main
    assert f"HOST_BUNDLE_STORAGE_PATH={workdir / 'data/bundle-storage'}" in env_main
    assert assembly_data["storage"]["kdcube"] == "file:///kdcube-storage"
    assert assembly_data["storage"]["bundles"] == "file:///bundle-storage"

    env_main, assembly_data, workdir = run_case(
        "s3-overrides",
        {
            "kdcube": "s3://example-bucket/kdcube",
            "bundles": "s3://example-bucket/bundles",
        },
    )
    assert f"HOST_KDCUBE_STORAGE_PATH={workdir / 'data/kdcube-storage'}" in env_main
    assert f"HOST_BUNDLE_STORAGE_PATH={workdir / 'data/bundle-storage'}" in env_main
    assert assembly_data["storage"]["kdcube"] == "s3://example-bucket/kdcube"
    assert assembly_data["storage"]["bundles"] == "s3://example-bucket/bundles"


def test_gather_configuration_applies_current_aws_descriptor_shape(monkeypatch, tmp_path: Path):
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

    assembly_path = config_dir / "assembly.yaml"
    assembly_path.write_text("x: 1\n")
    monkeypatch.setattr(
        "kdcube_cli.installer.compute_paths",
        lambda *_args, **_kwargs: {
            "host_kb_storage": str(tmp_path / "kdcube-storage"),
            "host_bundles": str(tmp_path / "bundles-root"),
            "host_managed_bundles": str(tmp_path / "managed-bundles"),
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
            "platform": {"ref": "2026.4.21.001"},
            "secrets": {"provider": "secrets-file"},
            "paths": {
                "host_kdcube_storage_path": str(tmp_path / "seed-storage"),
                "host_bundles_path": str(tmp_path / "seed-bundles"),
                "host_managed_bundles_path": str(tmp_path / "seed-managed-bundles"),
                "host_bundle_storage_path": str(tmp_path / "seed-bundle-storage"),
                "host_exec_workspace_path": str(tmp_path / "seed-exec-workspace"),
            },
            "auth": {"type": "simple"},
            "proxy": {"ssl": False},
            "storage": {
                "workspace": {"type": "git", "repo": "https://github.com/example/workspace.git"},
                "claude_code_session": {"type": "git", "repo": "https://github.com/example/workspace.git"},
            },
            "aws": {
                "aws_region": "eu-west-1",
                "aws_profile": "demo",
                "aws_sdk_load_config": True,
                "aws_ec2_metadata_disabled": False,
                "no_proxy": "169.254.169.254,localhost,127.0.0.1",
            },
        },
    )

    env_proc = (config_dir / ".env.proc").read_text()
    env_ingress = (config_dir / ".env.ingress").read_text()
    env_metrics = (config_dir / ".env.metrics").read_text()

    for text in (env_proc, env_ingress, env_metrics):
        assert "AWS_REGION=eu-west-1" in text
        assert "AWS_DEFAULT_REGION=eu-west-1" in text
        assert "AWS_PROFILE=demo" in text
        assert "AWS_SDK_LOAD_CONFIG=1" in text
        assert "AWS_EC2_METADATA_DISABLED=false" in text
        assert "NO_PROXY=169.254.169.254,localhost,127.0.0.1" in text


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
            "host_managed_bundles": str(tmp_path / "managed-bundles"),
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
                "host_managed_bundles_path": str(tmp_path / "managed-bundles"),
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
            "host_managed_bundles": str(tmp_path / "managed-bundles"),
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
    assert "BUNDLES_ROOT=/bundles" in env_main

    assembly_data = yaml.safe_load(assembly_path.read_text())
    assert assembly_data["paths"]["host_bundles_path"] == str(host_bundle_root.resolve())

    bundles_data = yaml.safe_load(bundles_path.read_text())
    bundle_item = bundles_data["bundles"]["items"][0]
    bundle_item_2 = bundles_data["bundles"]["items"][1]
    assert bundle_item["path"] == "/bundles/marketing/demo.bundle"
    assert bundle_item_2["path"] == "/bundles/ops/admin.bundle"


def test_gather_configuration_reuses_existing_container_bundle_paths_from_runtime_descriptor(monkeypatch, tmp_path: Path):
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

    host_bundle_root = tmp_path / "src"
    (host_bundle_root / "marketing" / "demo.bundle").mkdir(parents=True)

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
            "host_managed_bundles": str(tmp_path / "managed-bundles"),
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
                "services": {
                    "proc": {
                        "bundles": {
                            "bundles_root": "/bundles",
                        }
                    }
                },
            },
            "secrets": {"provider": "secrets-file"},
            "paths": {
                "host_bundles_path": str(host_bundle_root),
            },
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
                        "path": "/bundles/marketing/demo.bundle",
                        "module": "entrypoint",
                    },
                    {
                        "id": "managed.bundle@1.0.0",
                        "path": "/managed-bundles/managed.bundle__latest__abc",
                        "module": "entrypoint",
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
    assert "BUNDLES_ROOT=/bundles" in env_main

    assembly_data = yaml.safe_load(assembly_path.read_text())
    assert assembly_data["paths"]["host_bundles_path"] == str(host_bundle_root.resolve())

    bundles_data = yaml.safe_load(bundles_path.read_text())
    bundle_items = bundles_data["bundles"]["items"]
    assert bundle_items[0]["path"] == "/bundles/marketing/demo.bundle"
    assert bundle_items[1]["path"] == "/managed-bundles/managed.bundle__latest__abc"


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

    assert yaml.safe_load((out_dir / "bundles.yaml").read_text()) == yaml.safe_load(bundles_path.read_text())
    assert yaml.safe_load((out_dir / "bundles.secrets.yaml").read_text()) == yaml.safe_load(
        bundles_secrets_path.read_text()
    )


def test_export_live_bundle_descriptors_keeps_managed_cache_paths_runtime_visible(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "assembly.yaml").write_text(
        yaml.safe_dump(
            {
                "paths": {
                    "host_bundles_path": str(tmp_path / "src"),
                    "host_managed_bundles_path": str(tmp_path / "runtime" / "data" / "managed-bundles"),
                },
                "platform": {
                    "services": {
                        "proc": {
                            "bundles": {
                                "bundles_root": "/bundles",
                                "managed_bundles_root": "/managed-bundles",
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    data = {
        "bundles": {
            "items": [
                {
                    "id": "local.bundle",
                    "path": "/bundles/apps/local.bundle",
                    "module": "entrypoint",
                },
                {
                    "id": "managed.bundle",
                    "path": "/managed-bundles/managed.bundle__main__abc",
                    "module": "entrypoint",
                },
            ]
        }
    }

    translations = export_mod._normalize_exported_bundle_paths(data, config_dir=config_dir)

    items = data["bundles"]["items"]
    assert items[0]["path"] == str(tmp_path / "src" / "apps" / "local.bundle")
    assert items[1]["path"] == "/managed-bundles/managed.bundle__main__abc"
    assert {item["action"] for item in translations} == {"translated_path", "kept_managed_path"}


def _write_initialized_runtime_config(workdir: Path, *, marker: str = "old") -> Path:
    config_dir = workdir / "config"
    config_dir.mkdir(parents=True)
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
                "auth": {"type": "simple"},
                "proxy": {"ssl": False},
                "paths": {"host_bundles_path": str(workdir / "bundles")},
            },
            sort_keys=False,
        )
    )
    (config_dir / "secrets.yaml").write_text(f"services:\n  marker: {marker}\n")
    (config_dir / "gateway.yaml").write_text(f"routes:\n  marker: {marker}\n")
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


def test_export_platform_descriptors_copies_platform_files(tmp_path: Path):
    config_dir = _write_initialized_runtime_config(tmp_path / "runtime", marker="source")
    out_dir = tmp_path / "out"

    files = _export_platform_descriptors(
        Console(file=None),
        config_dir=config_dir,
        out_dir=out_dir,
        quiet=True,
    )

    assert {item["name"] for item in files} == {"assembly.yaml", "secrets.yaml", "gateway.yaml"}
    assert (out_dir / "assembly.yaml").exists()
    for name in ("secrets.yaml", "gateway.yaml"):
        assert (out_dir / name).read_text() == (config_dir / name).read_text()


def test_export_platform_descriptors_denormalizes_local_infra_hosts(tmp_path: Path):
    config_dir = _write_initialized_runtime_config(tmp_path / "runtime", marker="source")
    assembly_path = config_dir / "assembly.yaml"
    assembly = yaml.safe_load(assembly_path.read_text())
    assembly["infra"] = {
        "postgres": {"host": "host.docker.internal", "port": "5432"},
        "redis": {"host": "host.docker.internal", "port": "6379"},
    }
    assembly_path.write_text(yaml.safe_dump(assembly, sort_keys=False), encoding="utf-8")
    out_dir = tmp_path / "out"

    _export_platform_descriptors(
        Console(file=None),
        config_dir=config_dir,
        out_dir=out_dir,
        quiet=True,
    )

    exported = yaml.safe_load((out_dir / "assembly.yaml").read_text())
    assert exported["infra"]["postgres"]["host"] == "localhost"
    assert exported["infra"]["redis"]["host"] == "localhost"


def test_export_platform_descriptors_drops_cli_managed_local_paths(tmp_path: Path):
    runtime = tmp_path / "runtime"
    config_dir = _write_initialized_runtime_config(runtime, marker="source")
    assembly_path = config_dir / "assembly.yaml"
    assembly = yaml.safe_load(assembly_path.read_text())
    assembly["storage"] = {
        "kdcube": "file:///kdcube-storage",
        "bundles": "file:///bundle-storage",
    }
    assembly["paths"] = {
        "host_kdcube_storage_path": str(runtime / "data" / "kdcube-storage"),
        "host_bundles_path": str(tmp_path / "src"),
        "host_managed_bundles_path": str(runtime / "data" / "managed-bundles"),
        "host_bundle_storage_path": str(runtime / "data" / "bundle-storage"),
        "host_exec_workspace_path": str(runtime / "data" / "exec-workspace"),
        "host_react_debug_path": str(runtime / "data" / "react-debug"),
    }
    assembly["platform"] = {
        "services": {
            "ingress": {"log": {"log_dir": "/logs", "log_level": "INFO"}},
            "proc": {"log": {"log_dir": "/logs", "log_level": "INFO"}},
        }
    }
    assembly_path.write_text(yaml.safe_dump(assembly, sort_keys=False), encoding="utf-8")
    out_dir = tmp_path / "out"

    _export_platform_descriptors(
        Console(file=None),
        config_dir=config_dir,
        out_dir=out_dir,
        quiet=True,
    )

    exported = yaml.safe_load((out_dir / "assembly.yaml").read_text())
    assert exported["storage"] == {"kdcube": None, "bundles": None}
    assert exported["paths"] == {
        "host_kdcube_storage_path": None,
        "host_bundles_path": str(tmp_path / "src"),
        "host_managed_bundles_path": None,
        "host_bundle_storage_path": None,
        "host_exec_workspace_path": None,
        "host_react_debug_path": None,
    }
    assert "log_dir" not in exported["platform"]["services"]["ingress"]["log"]
    assert "log_dir" not in exported["platform"]["services"]["proc"]["log"]
    assert exported["platform"]["services"]["ingress"]["log"]["log_level"] == "INFO"
    assert exported["platform"]["services"]["proc"]["log"]["log_level"] == "INFO"


def test_apply_config_descriptors_overwrites_platform_files_and_regenerates(monkeypatch, tmp_path: Path):
    target_config = _write_initialized_runtime_config(tmp_path / "runtime", marker="old")
    source_config = _write_initialized_runtime_config(tmp_path / "source", marker="new")
    regenerated: list[dict[str, object]] = []

    def _fake_regenerate(*args, **kwargs):
        regenerated.append({"args": args, **kwargs})

    monkeypatch.setattr(cli_mod, "_regenerate_runtime_config_from_descriptors", _fake_regenerate)

    result = apply_config_descriptors(
        Console(file=None),
        workdir=target_config.parent,
        descriptors_location=source_config,
        include_platform_descriptors=True,
        repo_root=tmp_path / "repo",
        quiet=True,
    )

    assert result["runtime_config_regenerated"] is True
    assert regenerated and regenerated[0]["workdir"] == target_config.parent
    for name in ("assembly.yaml", "secrets.yaml", "gateway.yaml", "bundles.yaml", "bundles.secrets.yaml"):
        assert (target_config / name).read_text() == (source_config / name).read_text()


def test_gather_configuration_default_bootstrap_prompts_only_minimal_inputs(monkeypatch, tmp_path: Path):
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

    assembly_path = config_dir / "assembly.yaml"
    assembly_path.write_text("x: 1\n")
    secrets_path = config_dir / "secrets.yaml"
    secrets_path.write_text("services: {}\n")
    bundles_path = config_dir / "bundles.yaml"
    bundles_path.write_text("bundles:\n  version: '1'\n  default_bundle_id: versatile@2026-03-31-13-36\n  items: []\n")
    bundles_secrets_path = config_dir / "bundles.secrets.yaml"
    bundles_secrets_path.write_text("bundles:\n  version: '1'\n  items: []\n")

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    ai_app_root = tmp_path / "ai-app"
    ai_app_root.mkdir()
    docker_dir = ai_app_root / "deployment" / "docker" / "all_in_one_kdcube"
    docker_dir.mkdir(parents=True)

    monkeypatch.setenv("KDCUBE_DEFAULT_DESCRIPTOR_BOOTSTRAP", "1")
    monkeypatch.setattr(
        "kdcube_cli.installer.compute_paths",
        lambda *_args, **_kwargs: {
            "host_kb_storage": str(tmp_path / "kdcube-storage"),
            "host_bundles": str(tmp_path / "src"),
            "host_managed_bundles": str(tmp_path / "managed-bundles"),
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

    ask_labels: list[str] = []
    select_titles: list[str] = []
    secret_labels: list[str] = []

    monkeypatch.setattr(
        "kdcube_cli.installer.ask",
        lambda _console, label, default=None, secret=False: ask_labels.append(label) or str(default or ""),
    )
    monkeypatch.setattr("kdcube_cli.installer.ask_confirm", lambda _console, _label, default=False: default)
    monkeypatch.setattr(
        "kdcube_cli.installer.select_option",
        lambda _console, title, options, default_index=0: select_titles.append(title) or options[default_index],
    )
    monkeypatch.setattr(
        "kdcube_cli.installer.ensure_absolute",
        lambda _console, _label, current, default, force_prompt=False: str(Path(current or default or tmp_path).resolve()),
    )
    monkeypatch.setattr(
        "kdcube_cli.installer.prompt_secret_value",
        lambda _console, label, required=False, current=None, force_prompt=False: secret_labels.append(label) or None,
    )
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
            "platform": {"ref": "2026.4.20.001"},
            "secrets": {"provider": "secrets-service"},
            "auth": {"type": "simple"},
            "proxy": {"ssl": False},
            "storage": {
                "kdcube": None,
                "bundles": None,
                "workspace": {"type": "local", "repo": ""},
                "claude_code_session": {"type": "local", "repo": ""},
            },
            "infra": {
                "postgres": {
                    "user": "postgres",
                    "password": "postgres",
                    "database": "kdcube",
                    "host": "localhost",
                    "port": "5432",
                },
                "redis": {
                    "password": "redispass",
                    "host": "localhost",
                    "port": "6379",
                },
            },
            "paths": {
                "host_bundles_path": str(tmp_path / "src"),
                "host_managed_bundles_path": str(tmp_path / "managed-bundles"),
                "host_kdcube_storage_path": str(tmp_path / "kdcube-storage"),
                "host_bundle_storage_path": str(tmp_path / "bundle-storage"),
                "host_exec_workspace_path": str(tmp_path / "exec-workspace"),
            },
            "ports": {"ui": "5174"},
        },
        secrets_descriptor_path=str(secrets_path),
        secrets_descriptor={"services": {}},
        bundles_descriptor_path=str(bundles_path),
        bundles_descriptor={"bundles": {"version": "1", "default_bundle_id": "versatile@2026-03-31-13-36", "items": []}},
        bundles_secrets_path=str(bundles_secrets_path),
        bundles_secrets_descriptor={"bundles": {"version": "1", "items": []}},
        gateway_descriptor={"gateway": {"tenant": "demo-tenant", "project": "demo-project"}},
        use_bundles_descriptor=True,
        use_bundles_secrets=True,
    )

    assert ask_labels == []
    assert select_titles == []
    assert secret_labels == [
        "OpenAI API key",
        "Anthropic API key",
        "Git HTTPS token",
    ]

    env_main = (config_dir / ".env").read_text()
    assembly = yaml.safe_load(assembly_path.read_text())
    assert f"HOST_BUNDLES_PATH={(tmp_path / 'src').resolve()}" in env_main
    assert assembly["storage"]["kdcube"] == "file:///kdcube-storage"
    assert assembly["storage"]["bundles"] == "file:///bundle-storage"
    assert assembly["infra"]["postgres"]["host"] == "host.docker.internal"
    assert assembly["infra"]["redis"]["host"] == "host.docker.internal"


# ---------------------------------------------------------------------------
# _bootstrap_repo_for_defaults
# ---------------------------------------------------------------------------

def _make_fake_repo_with_deployment(base: Path) -> Path:
    """Create a minimal git repo that contains app/ai-app/deployment/assembly.yaml."""
    repo = base / "repo"
    _init_git_repo(repo)
    deployment = repo / "app" / "ai-app" / "deployment"
    deployment.mkdir(parents=True)
    (deployment / "assembly.yaml").write_text("context:\n  tenant: demo-tenant\n  project: demo-project\n")
    return repo


def test_bootstrap_repo_for_defaults_uses_path_provided_repo(tmp_path: Path):
    repo = _make_fake_repo_with_deployment(tmp_path)
    console = Console(quiet=True)

    resolved_repo, descriptors_loc = _bootstrap_repo_for_defaults(
        console,
        repo="https://example.com/repo.git",
        repo_path=repo,
        path_provided=True,
    )

    assert resolved_repo == repo
    assert descriptors_loc == repo / "app" / "ai-app" / "deployment"
    assert (descriptors_loc / "assembly.yaml").exists()


def test_bootstrap_repo_for_defaults_path_provided_requires_git_repo(tmp_path: Path):
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    console = Console(quiet=True)

    try:
        _bootstrap_repo_for_defaults(
            console,
            repo="https://example.com/repo.git",
            repo_path=not_a_repo,
            path_provided=True,
        )
        raise AssertionError("Expected SystemExit for non-git path")
    except SystemExit as exc:
        assert "not a git repo" in str(exc).lower()


def test_bootstrap_repo_for_defaults_clones_when_path_not_provided(monkeypatch, tmp_path: Path):
    repo = _make_fake_repo_with_deployment(tmp_path)
    cloned: list[str] = []

    def fake_ensure_repo(console, repo_url, target):
        cloned.append(repo_url)

    monkeypatch.setattr("kdcube_cli.cli.ensure_repo", fake_ensure_repo)
    console = Console(quiet=True)

    resolved_repo, descriptors_loc = _bootstrap_repo_for_defaults(
        console,
        repo="https://example.com/repo.git",
        repo_path=repo,
        path_provided=False,
    )

    assert cloned == ["https://example.com/repo.git"]
    assert resolved_repo == repo
    assert descriptors_loc == repo / "app" / "ai-app" / "deployment"


def test_bootstrap_repo_for_defaults_raises_when_deployment_missing(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    # No app/ai-app/deployment directory created
    console = Console(quiet=True)

    try:
        _bootstrap_repo_for_defaults(
            console,
            repo="https://example.com/repo.git",
            repo_path=repo,
            path_provided=True,
        )
        raise AssertionError("Expected SystemExit for missing deployment dir")
    except SystemExit as exc:
        assert "deployment descriptors" in str(exc).lower()


# ---------------------------------------------------------------------------
# _check_before_start
# ---------------------------------------------------------------------------


def test_check_before_start_passes_without_lock(monkeypatch):
    monkeypatch.setattr("kdcube_cli.cli._read_cli_lock", lambda: None)

    _check_before_start(Console(quiet=True), tenant="tenant-a", project="project-a")


def test_check_before_start_passes_for_same_locked_deployment(monkeypatch):
    monkeypatch.setattr(
        "kdcube_cli.cli._read_cli_lock",
        lambda: {"tenant": "tenant-a", "project": "project-a"},
    )

    _check_before_start(Console(quiet=True), tenant="tenant-a", project="project-a")


def test_check_before_start_refuses_when_different_locked_deployment_is_running(monkeypatch):
    monkeypatch.setattr(
        "kdcube_cli.cli._read_cli_lock",
        lambda: {
            "tenant": "tenant-a",
            "project": "project-a",
            "workdir": "/tmp/workspace/tenant-a__project-a",
        },
    )
    monkeypatch.setattr("kdcube_cli.cli._lock_running_services", lambda _lock: {"chat-proc", "chat-ingress"})

    try:
        _check_before_start(Console(quiet=True), tenant="tenant-b", project="project-b")
        raise AssertionError("Expected SystemExit when another deployment is running")
    except SystemExit as exc:
        msg = str(exc)
        assert "tenant-a" in msg
        assert "project-a" in msg
        assert "chat-ingress" in msg or "chat-proc" in msg
        assert "kdcube stop --workdir" in msg


def test_check_before_start_clears_stale_different_lock(monkeypatch):
    cleared = []
    monkeypatch.setattr(
        "kdcube_cli.cli._read_cli_lock",
        lambda: {
            "tenant": "tenant-a",
            "project": "project-a",
            "workdir": "/tmp/workspace/tenant-a__project-a",
        },
    )
    monkeypatch.setattr("kdcube_cli.cli._lock_running_services", lambda _lock: set())
    monkeypatch.setattr("kdcube_cli.cli._clear_cli_lock", lambda: cleared.append(True))

    _check_before_start(Console(quiet=True), tenant="tenant-b", project="project-b")

    assert cleared == [True]


# ---------------------------------------------------------------------------
# Block 3: Defaults system — _load_cli_defaults / _save_cli_defaults
# ---------------------------------------------------------------------------


def test_load_cli_defaults_returns_empty_when_file_missing(monkeypatch, tmp_path: Path):
    import kdcube_cli.cli as cli_mod

    monkeypatch.setattr(cli_mod, "DEFAULT_DEFAULTS_FILE", tmp_path / "nonexistent.json")
    assert _load_cli_defaults() == {}


def test_load_cli_defaults_returns_data_when_file_exists(monkeypatch, tmp_path: Path):
    import kdcube_cli.cli as cli_mod

    defaults_file = tmp_path / "cli-defaults.json"
    defaults_file.write_text(json.dumps({"default_tenant": "acme", "default_project": "app"}))
    monkeypatch.setattr(cli_mod, "DEFAULT_DEFAULTS_FILE", defaults_file)

    result = _load_cli_defaults()

    assert result == {"default_tenant": "acme", "default_project": "app"}


def test_load_cli_defaults_returns_empty_on_corrupt_json(monkeypatch, tmp_path: Path):
    import kdcube_cli.cli as cli_mod

    defaults_file = tmp_path / "cli-defaults.json"
    defaults_file.write_text("{ not valid json }")
    monkeypatch.setattr(cli_mod, "DEFAULT_DEFAULTS_FILE", defaults_file)

    assert _load_cli_defaults() == {}


def test_save_cli_defaults_creates_file_and_parent(monkeypatch, tmp_path: Path):
    import kdcube_cli.cli as cli_mod

    defaults_file = tmp_path / "nested" / "dir" / "cli-defaults.json"
    monkeypatch.setattr(cli_mod, "DEFAULT_DEFAULTS_FILE", defaults_file)

    _save_cli_defaults({"default_tenant": "demo", "default_workdir": "/tmp/runtime"})

    assert defaults_file.exists()
    saved = json.loads(defaults_file.read_text())
    assert saved["default_tenant"] == "demo"
    assert saved["default_workdir"] == "/tmp/runtime"


def test_save_cli_defaults_overwrites_existing(monkeypatch, tmp_path: Path):
    import kdcube_cli.cli as cli_mod

    defaults_file = tmp_path / "cli-defaults.json"
    defaults_file.write_text(json.dumps({"default_tenant": "old"}))
    monkeypatch.setattr(cli_mod, "DEFAULT_DEFAULTS_FILE", defaults_file)

    _save_cli_defaults({"default_tenant": "new", "default_project": "app"})

    saved = json.loads(defaults_file.read_text())
    assert saved == {"default_tenant": "new", "default_project": "app"}


def test_save_and_load_cli_defaults_roundtrip(monkeypatch, tmp_path: Path):
    import kdcube_cli.cli as cli_mod

    defaults_file = tmp_path / "cli-defaults.json"
    monkeypatch.setattr(cli_mod, "DEFAULT_DEFAULTS_FILE", defaults_file)

    original = {"default_tenant": "corp", "default_project": "chat", "default_workdir": "/opt/rt"}
    _save_cli_defaults(original)
    loaded = _load_cli_defaults()

    assert loaded == original


# ---------------------------------------------------------------------------
# Block 4: Targeted command workdir resolution
# ---------------------------------------------------------------------------


def test_resolve_subcommand_workdir_raises_when_no_workdir_and_no_defaults():
    try:
        _resolve_subcommand_workdir(None, {})
        raise AssertionError("Expected SystemExit when no workdir can be resolved")
    except SystemExit as exc:
        msg = str(exc)
    assert "--workdir" in msg
    assert "--default-workdir" in msg


def test_resolve_subcommand_workdir_uses_explicit_workdir(tmp_path: Path):
    explicit = tmp_path / "runtime"

    resolved = _resolve_subcommand_workdir(str(explicit), {"default_workdir": str(tmp_path / "other")})

    assert resolved == explicit.resolve()


def test_resolve_subcommand_workdir_uses_default_workdir(tmp_path: Path):
    default = tmp_path / "default-runtime"

    resolved = _resolve_subcommand_workdir(None, {"default_workdir": str(default)})

    assert resolved == default.resolve()
