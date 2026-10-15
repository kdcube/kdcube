# SPDX-License-Identifier: MIT
"""Synthetic Compose selection checks; no Docker or runtime dependencies."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from kdcube_cli import cli


@pytest.fixture(autouse=True)
def isolated_compose_environment(monkeypatch):
    for key in ("COMPOSE_FILE", "COMPOSE_PATH_SEPARATOR", "COMPOSE_ENV_FILES"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **kw: pytest.fail("unexpected subprocess"))


def _file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("services: {}\n", encoding="utf-8")
    return path


def _refresh(monkeypatch, repo: Path, cwd: Path, config: Path) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(sys, "argv", ["kdcube", "refresh", "--quiet", "--workdir", str(config.parent), "--build"])
    monkeypatch.setattr(cli, "_load_cli_defaults", lambda: {})
    monkeypatch.setattr(cli, "_resolve_namespaced_runtime_target", lambda **kw: (config.parent, "sample", "project"))
    monkeypatch.setattr(cli, "_canonical_descriptor_dir_from_initialized_workdir", lambda path: config)
    monkeypatch.setattr(cli, "_resolve_subcommand_repo", lambda *a, **kw: repo)
    monkeypatch.setattr(cli, "_build_paths_for_repo", lambda *a, **kw: SimpleNamespace(docker_dir=cwd, config_dir=config))
    monkeypatch.setattr(cli, "_refresh_runtime_proxy_config", lambda *a, **kw: calls.append("proxy"))
    monkeypatch.setattr(cli.installer_mod, "ensure_generated_runtime_secrets", lambda *a: calls.append("secrets"))
    monkeypatch.setattr(cli, "_parse_workdir_namespace", lambda path: ("sample", "project"))
    monkeypatch.setattr(cli, "stop_compose_stack", lambda *a, **kw: calls.append("stop"))
    monkeypatch.setattr(cli, "build_compose_images", lambda *a, **kw: calls.append("build"))
    monkeypatch.setattr(cli, "_check_before_start", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "start_compose_stack", lambda *a, **kw: calls.append("start"))
    return calls


@pytest.mark.parametrize("selection_source", ["process", "env-file"])
def test_refresh_refuses_foreign_compose_before_stop(monkeypatch, tmp_path, selection_source):
    repo = tmp_path / "selected"
    cwd = _file(repo / "deployment" / "compose.yaml").parent
    foreign = _file(tmp_path / "other-checkout" / "compose.yaml")
    config = tmp_path / "runtime" / "config"
    config.mkdir(parents=True)
    (config / ".env").write_text(f"COMPOSE_FILE={foreign}\n" if selection_source == "env-file" else "", encoding="utf-8")
    if selection_source == "process":
        monkeypatch.setenv("COMPOSE_FILE", str(foreign))
    calls = _refresh(monkeypatch, repo, cwd, config)

    with pytest.raises(SystemExit, match="error\\[compose_source_mismatch\\]") as raised:
        cli.main()

    assert str(foreign.resolve()) in str(raised.value)
    assert calls == []


@pytest.mark.parametrize("selection_source", ["default", "process", "env-file"])
def test_refresh_accepts_selected_checkout_before_normal_lifecycle(monkeypatch, tmp_path, selection_source):
    repo = tmp_path / "selected"
    base = _file(repo / "deployment" / "compose.yaml")
    override = _file(base.parent / "chosen.yaml")
    config = tmp_path / "runtime" / "config"
    config.mkdir(parents=True)
    (config / ".env").write_text('COMPOSE_FILE="chosen.yaml"\n' if selection_source == "env-file" else "", encoding="utf-8")
    if selection_source == "process":
        monkeypatch.setenv("COMPOSE_FILE", str(override))
    calls = _refresh(monkeypatch, repo, base.parent, config)

    cli.main()

    assert calls == ["proxy", "secrets", "stop", "build", "start"]


def _selection(repo, cwd, *, environment=None, options=()):
    from kdcube_cli.compose_source import require_compose_source

    return require_compose_source(repo, ["docker", "compose", *options], cwd=cwd, environment=environment or {})


@pytest.mark.parametrize("flag", ["-f", "--file", "--file=", "-fjoined"])
def test_command_file_wins_over_process_and_env_file(tmp_path, flag):
    repo = tmp_path / "selected"
    chosen = _file(repo / "deployment" / "chosen.yaml")
    foreign = _file(tmp_path / "foreign" / "compose.yaml")
    env_file = tmp_path / "config" / ".env"
    env_file.parent.mkdir()
    env_file.write_text(f"COMPOSE_FILE={foreign}\n", encoding="utf-8")
    file_args = [flag, chosen.name] if flag in {"-f", "--file"} else [f"--file={chosen.name}" if flag == "--file=" else f"-f{chosen.name}"]

    assert _selection(repo, chosen.parent, environment={"COMPOSE_FILE": str(foreign)}, options=["--env-file", str(env_file), *file_args]) == (chosen.resolve(),)


def test_every_command_file_is_checked(tmp_path):
    from kdcube_cli.compose_source import ComposeSourceError

    repo = tmp_path / "selected"
    base = _file(repo / "compose.yaml")
    foreign = _file(tmp_path / "selected-other" / "override.yaml")
    with pytest.raises(ComposeSourceError) as raised:
        _selection(repo, repo, options=["-f", str(base), "--file", str(foreign)])
    assert str(foreign.resolve()) in str(raised.value)


@pytest.mark.parametrize("separator", [":", ";", "|"])
def test_multiple_env_files_are_checked_with_selected_separator(tmp_path, separator):
    from kdcube_cli.compose_source import ComposeSourceError

    repo = tmp_path / "selected"
    base = _file(repo / "compose.yaml")
    override = _file(repo / "override.yaml")
    foreign = _file(tmp_path / "other" / "override.yaml")
    environment = {"COMPOSE_FILE": f"{base}{separator}{override}", "COMPOSE_PATH_SEPARATOR": separator}
    assert _selection(repo, repo, environment=environment) == (base.resolve(), override.resolve())
    environment["COMPOSE_FILE"] = f"{base}{separator}{foreign}"
    with pytest.raises(ComposeSourceError) as raised:
        _selection(repo, repo, environment=environment)
    assert str(foreign.resolve()) in str(raised.value)


def test_process_file_and_separator_override_selected_env_values(tmp_path):
    repo = tmp_path / "selected"
    base = _file(repo / "deployment" / "base.yaml")
    extra = _file(base.parent / "extra.yaml")
    env_file = tmp_path / "config" / ".env"
    env_file.parent.mkdir()
    env_file.write_text("COMPOSE_FILE=/outside/compose.yaml\nCOMPOSE_PATH_SEPARATOR=;\n", encoding="utf-8")
    assert _selection(repo, base.parent, environment={"COMPOSE_FILE": "base.yaml|extra.yaml", "COMPOSE_PATH_SEPARATOR": "|"}, options=["--env-file", str(env_file)]) == (base.resolve(), extra.resolve())


def test_selected_env_separator_applies_to_process_file(tmp_path):
    repo = tmp_path / "selected"
    base = _file(repo / "base.yaml")
    extra = _file(repo / "extra.yaml")
    env_file = tmp_path / ".env"
    env_file.write_text("COMPOSE_PATH_SEPARATOR=;\n", encoding="utf-8")
    assert _selection(repo, repo, environment={"COMPOSE_FILE": "base.yaml;extra.yaml"}, options=["--env-file", str(env_file)]) == (base.resolve(), extra.resolve())


def test_relative_paths_use_compose_cwd_not_env_directory(tmp_path):
    repo = tmp_path / "selected"
    chosen = _file(repo / "deployment" / "chosen.yaml")
    env_file = tmp_path / "config" / ".env"
    env_file.parent.mkdir()
    env_file.write_text(' export COMPOSE_FILE = "chosen.yaml" # comment\n', encoding="utf-8")
    assert _selection(repo, chosen.parent, options=["--env-file", str(env_file)]) == (chosen.resolve(),)


def test_explicit_env_file_ignores_cwd_dotenv_and_compose_env_files(tmp_path):
    repo = tmp_path / "selected"
    chosen = _file(repo / "compose.yaml")
    (repo / ".env").write_text("COMPOSE_FILE=/outside/compose.yaml\n", encoding="utf-8")
    selected = tmp_path / "selected.env"
    selected.write_text("", encoding="utf-8")
    assert _selection(repo, repo, environment={"COMPOSE_ENV_FILES": "/missing/unused.env"}, options=["--env-file", str(selected)]) == (chosen.resolve(),)


@pytest.mark.parametrize("expression", ["$SOURCE/compose.yaml", "${SOURCE}/compose.yaml", "${ABSENT:-${SOURCE}}/compose.yaml", "${SOURCE:+${SOURCE}/compose.yaml}"])
def test_env_selector_interpolation_uses_process_before_prior_env_values(tmp_path, expression):
    repo = tmp_path / "selected"
    chosen = _file(repo / "compose.yaml")
    env_file = tmp_path / "selected.env"
    env_file.write_text(f"SOURCE=/outside\nCOMPOSE_FILE={expression}\n", encoding="utf-8")
    assert _selection(repo, repo, environment={"SOURCE": str(repo)}, options=["--env-file", str(env_file)]) == (chosen.resolve(),)


def test_env_selector_interpolation_uses_prior_assignments_and_last_selector(tmp_path):
    repo = tmp_path / "selected"
    chosen = _file(repo / "compose.yaml")
    env_file = tmp_path / "selected.env"
    env_file.write_text(f"SOURCE={repo}\nCOMPOSE_FILE=/outside/compose.yaml\nCOMPOSE_FILE=${{SOURCE}}/compose.yaml\nSOURCE=/later\n", encoding="utf-8")
    assert _selection(repo, repo, options=["--env-file", str(env_file)]) == (chosen.resolve(),)


def test_single_quoted_selector_is_literal_and_unrelated_values_are_not_expanded(tmp_path):
    repo = tmp_path / "selected"
    chosen = _file(repo / "$LITERAL.yaml")
    env_file = tmp_path / "selected.env"
    env_file.write_text("UNRELATED=${UNSET:?not used}\nCOMPOSE_FILE='$LITERAL.yaml'\n", encoding="utf-8")
    assert _selection(repo, repo, options=["--env-file", str(env_file)]) == (chosen.resolve(),)


@pytest.mark.parametrize("discovery", ["base", "override", "parent"])
def test_default_discovery_checks_symlink_escape(tmp_path, discovery):
    from kdcube_cli.compose_source import ComposeSourceError

    repo = tmp_path / "selected"
    cwd = repo / "deployment"
    cwd.mkdir(parents=True)
    foreign = _file(tmp_path / "foreign" / "compose.yaml")
    if discovery == "base":
        (cwd / "compose.yaml").symlink_to(foreign)
    elif discovery == "override":
        _file(cwd / "compose.yaml")
        (cwd / "compose.override.yaml").symlink_to(foreign)
    else:
        (tmp_path / "compose.yaml").symlink_to(foreign)
    with pytest.raises(ComposeSourceError) as raised:
        _selection(repo, cwd)
    assert str(foreign.resolve()) in str(raised.value)


def test_default_discovery_prefers_compose_yaml_and_only_one_override(tmp_path):
    repo = tmp_path / "selected"
    base = _file(repo / "compose.yaml")
    override = _file(repo / "compose.override.yml")
    foreign = _file(tmp_path / "other" / "compose.yaml")
    (repo / "docker-compose.yaml").symlink_to(foreign)
    (repo / "compose.override.yaml").symlink_to(foreign)
    assert _selection(repo, repo) == (base.resolve(), override.resolve())


@pytest.mark.parametrize("selector", ["../foreign/compose.yaml", "escape/compose.yaml"])
def test_explicit_relative_or_directory_symlink_escape_is_refused(tmp_path, selector):
    from kdcube_cli.compose_source import ComposeSourceError

    repo = tmp_path / "selected"
    repo.mkdir()
    foreign = _file(tmp_path / "foreign" / "compose.yaml")
    (repo / "escape").symlink_to(foreign.parent, target_is_directory=True)
    with pytest.raises(ComposeSourceError) as raised:
        _selection(repo, repo, environment={"COMPOSE_FILE": selector})
    assert str(foreign.resolve()) in str(raised.value)


@pytest.mark.parametrize("selector", ["", "missing.yaml", "https://example.test/compose.yaml", "-"])
def test_unusable_selection_fails_closed(tmp_path, selector):
    from kdcube_cli.compose_source import ComposeSourceError

    repo = tmp_path / "selected"
    _file(repo / "compose.yaml")
    with pytest.raises(ComposeSourceError, match="error\\[compose_source_mismatch\\]"):
        _selection(repo, repo, environment={"COMPOSE_FILE": selector})


def test_remote_selector_diagnostic_does_not_echo_url(tmp_path):
    from kdcube_cli.compose_source import ComposeSourceError

    repo = tmp_path / "selected"
    repo.mkdir()
    remote = "https://example.test/compose.yaml?private-value=synthetic"
    with pytest.raises(ComposeSourceError) as raised:
        _selection(repo, repo, options=["-f", remote])
    assert remote not in str(raised.value)


def test_multiline_unrelated_value_cannot_override_real_selector(tmp_path):
    from kdcube_cli.compose_source import ComposeSourceError

    repo = tmp_path / "selected"
    chosen = _file(repo / "compose.yaml")
    foreign = _file(tmp_path / "other" / "compose.yaml")
    env_file = tmp_path / "selected.env"
    env_file.write_text(f'COMPOSE_FILE={foreign}\nUNRELATED="first line\nCOMPOSE_FILE={chosen}\nlast line"\n', encoding="utf-8")
    with pytest.raises(ComposeSourceError) as raised:
        _selection(repo, repo, options=["--env-file", str(env_file)])
    assert str(foreign.resolve()) in str(raised.value)


def test_colon_assignment_is_checked(tmp_path):
    from kdcube_cli.compose_source import ComposeSourceError

    repo = tmp_path / "selected"
    repo.mkdir()
    foreign = _file(tmp_path / "other" / "compose.yaml")
    env_file = tmp_path / "selected.env"
    env_file.write_text(f'COMPOSE_FILE: "{foreign}"\n', encoding="utf-8")
    with pytest.raises(ComposeSourceError) as raised:
        _selection(repo, repo, options=["--env-file", str(env_file)])
    assert str(foreign.resolve()) in str(raised.value)


def test_double_quoted_dollar_escape_is_literal(tmp_path):
    repo = tmp_path / "selected"
    chosen = _file(repo / "$LITERAL.yaml")
    env_file = tmp_path / "selected.env"
    env_file.write_text('COMPOSE_FILE="\\$LITERAL.yaml"\n', encoding="utf-8")
    assert _selection(repo, repo, options=["--env-file", str(env_file)]) == (chosen.resolve(),)


def test_repeated_env_files_use_the_last_selector(tmp_path):
    repo = tmp_path / "selected"
    chosen = _file(repo / "compose.yaml")
    first, last = tmp_path / "first.env", tmp_path / "last.env"
    first.write_text("COMPOSE_FILE=/outside/compose.yaml\n", encoding="utf-8")
    last.write_text(f"COMPOSE_FILE={chosen}\n", encoding="utf-8")
    assert _selection(repo, repo, options=["--env-file", str(first), "--env-file", str(last)]) == (chosen.resolve(),)


def test_unsupported_same_line_assignment_fails_closed_without_value_diagnostic(tmp_path):
    from kdcube_cli.compose_source import ComposeSourceError

    repo = tmp_path / "selected"
    _file(repo / "compose.yaml")
    env_file = tmp_path / "selected.env"
    raw = 'UNRELATED="synthetic"COMPOSE_FILE=/outside/compose.yaml'
    env_file.write_text(raw + "\n", encoding="utf-8")
    with pytest.raises(ComposeSourceError) as raised:
        _selection(repo, repo, options=["--env-file", str(env_file)])
    assert raw not in str(raised.value)


_NON_LF_BOUNDARIES = ("\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029")


@pytest.mark.parametrize("separator", _NON_LF_BOUNDARIES)
def test_non_lf_value_cannot_forge_a_later_selector(tmp_path, separator):
    from kdcube_cli.compose_source import ComposeSourceError

    repo = tmp_path / "selected"
    local = _file(repo / "compose.yaml")
    foreign = _file(tmp_path / "foreign" / "compose.yaml")
    env_file = tmp_path / "selected.env"
    env_file.write_bytes(
        f"COMPOSE_FILE={foreign}\nUNRELATED=synthetic{separator}COMPOSE_FILE={local}\n".encode("utf-8")
    )
    with pytest.raises(ComposeSourceError, match="outside selected checkout"):
        _selection(repo, repo, options=["--env-file", str(env_file)])


@pytest.mark.parametrize("separator", _NON_LF_BOUNDARIES)
def test_non_lf_forged_selector_refuses_before_refresh_lifecycle(monkeypatch, tmp_path, separator):
    repo = tmp_path / "selected"
    local = _file(repo / "compose.yaml")
    foreign = _file(tmp_path / "foreign" / "compose.yaml")
    config = tmp_path / "runtime" / "config"
    config.mkdir(parents=True)
    (config / ".env").write_bytes(
        f"COMPOSE_FILE={foreign}\nUNRELATED=synthetic{separator}COMPOSE_FILE={local}\n".encode("utf-8")
    )
    calls = _refresh(monkeypatch, repo, repo, config)
    with pytest.raises(SystemExit, match="compose_source_mismatch"):
        cli.main()
    assert calls == []


@pytest.mark.parametrize("line_ending", ["\n", "\r\n"])
def test_lf_terminated_assignment_can_select_a_later_local_file(tmp_path, line_ending):
    repo = tmp_path / "selected"
    local = _file(repo / "compose.yaml")
    env_file = tmp_path / "selected.env"
    env_file.write_bytes(line_ending.join(
        ["COMPOSE_FILE=/outside/compose.yaml", "UNRELATED=synthetic", f"COMPOSE_FILE={local}", ""]
    ).encode("utf-8"))
    assert _selection(repo, repo, options=["--env-file", str(env_file)]) == (local.resolve(),)
