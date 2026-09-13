from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from kdcube_cli import cli as cli_mod


def _runtime_paths(tmp_path: Path) -> SimpleNamespace:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / ".env").write_text("TENANT=demo\n", encoding="utf-8")
    (config_dir / ".env.proc").write_text("PROJECT=project\n", encoding="utf-8")
    docker_dir = tmp_path / "docker"
    docker_dir.mkdir()
    return SimpleNamespace(config_dir=config_dir, docker_dir=docker_dir)


def test_run_catalog_check_reads_live_request_serving_catalog(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _runtime_paths(tmp_path)
    calls: list[dict[str, object]] = []
    result = {
        "schema": "kdcube.delegated-catalog-check.v1",
        "status": "drift",
        "in_sync": False,
        "tenant": "demo",
        "project": "project",
        "contributors": ["problem-board@1-0"],
        "differences": [
            {
                "kind": "missing",
                "path": "connections.delegated_credentials.oauth.resources[problem-board]",
                "expected": {"resource": "problem-board"},
                "actual": None,
            }
        ],
    }

    monkeypatch.setattr(cli_mod, "_build_paths_for_repo", lambda *_args: paths)
    monkeypatch.setattr(
        cli_mod,
        "_compose_running_services",
        lambda *_args: {"chat-proc"},
    )

    def post(_console, **kwargs):
        calls.append(kwargs)
        return result

    monkeypatch.setattr(cli_mod, "_post_local_bundle_control", post)

    actual = cli_mod.run_catalog_check_command(
        cli_mod.Console(),
        repo_root=tmp_path,
        workdir=tmp_path,
        json_output=True,
    )

    assert actual == result
    assert calls == [
        {
            "ctx": paths,
            "env_main_path": paths.config_dir / ".env",
            "endpoint": "/internal/bundles/catalog/check",
            "payload": {},
            "label": "Delegated catalog check",
            "verbose": False,
            "require_ok_status": False,
        }
    ]
    assert json.loads(capsys.readouterr().out) == result


def test_catalog_check_human_output_reports_declaration_owner_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli_mod._print_catalog_check_result(
        cli_mod.Console(),
        {
            "status": "invalid",
            "in_sync": False,
            "tenant": "demo",
            "project": "project",
            "contributors": ["app-a", "app-b"],
            "differences": [
                {
                    "kind": "declaration_error",
                    "code": "duplicate_resource",
                    "path": "connections.delegated_credentials.oauth.resources[shared]",
                    "message": "resource is declared by two apps",
                    "owners": ["app-a", "app-b"],
                }
            ],
        },
    )

    output = capsys.readouterr().out
    assert "invalid" in output
    assert "resources[shared]" in output
    assert "resource is declared by two apps" in output
    assert "owners: app-a, app-b" in output


def test_cli_catalog_check_exits_nonzero_for_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(cli_mod, "_load_cli_defaults", lambda: {})
    monkeypatch.setattr(
        cli_mod,
        "_resolve_subcommand_workdir",
        lambda *_args, **_kwargs: tmp_path,
    )
    monkeypatch.setattr(cli_mod, "_resolve_cli_workdir", lambda value, **_kwargs: Path(value))
    monkeypatch.setattr(cli_mod, "_resolve_subcommand_repo", lambda *_args, **_kwargs: tmp_path)
    monkeypatch.setattr(
        cli_mod,
        "run_catalog_check_command",
        lambda *_args, **_kwargs: {"status": "drift", "in_sync": False},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["kdcube", "bundle", "catalog", "check", "--workdir", str(tmp_path)],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.main()

    assert exc_info.value.code == 1


def test_cli_catalog_rejects_removed_apply_workflow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(cli_mod, "_load_cli_defaults", lambda: {})
    monkeypatch.setattr(
        cli_mod,
        "_resolve_subcommand_workdir",
        lambda *_args, **_kwargs: tmp_path,
    )
    monkeypatch.setattr(cli_mod, "_resolve_cli_workdir", lambda value, **_kwargs: Path(value))
    monkeypatch.setattr(
        sys,
        "argv",
        ["kdcube", "bundle", "catalog", "apply", "--workdir", str(tmp_path)],
    )

    with pytest.raises(SystemExit, match="bundle catalog check"):
        cli_mod.main()
