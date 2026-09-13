import json
import sys
from pathlib import Path

import pytest
import yaml

from kdcube_cli import cli as cli_mod
from kdcube_cli.catalog_fragments import (
    CatalogFragmentError,
    process_catalog_fragment,
    validate_catalog_fragment,
)


def _write_yaml(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    return path


def _descriptor(catalog: dict | None = None) -> dict:
    config: dict = {}
    if catalog is not None:
        config = {
            "connections": {
                "delegated_credentials": {
                    "oauth": catalog,
                }
            }
        }
    return {
        "bundles": {
            "version": "1",
            "items": [
                {
                    "id": "connection-hub@1-0",
                    "name": "Connection Hub",
                    "config": config,
                },
                {"id": "sibling@1-0", "config": {"retained": True}},
            ],
        }
    }


def _fragment() -> dict:
    return {
        "capabilities": [
            {
                "grant": "work:observe",
                "label": "Observe work",
                "delegable_roles": ["registered", "paid"],
            }
        ],
        "resources": [
            {
                "resource": "*/problem-board/*",
                "label": "Problem Board",
                "tools": {
                    "project.get": {
                        "label": "Read project",
                        "grants": ["work:observe"],
                    },
                    "project.list": {
                        "label": "List projects",
                        "grants": ["work:observe"],
                    },
                },
            }
        ],
    }


def test_check_names_missing_and_conflicting_declarations(tmp_path: Path) -> None:
    descriptor_path = _write_yaml(
        tmp_path / "bundles.yaml",
        _descriptor(
            {
                "capabilities": [
                    {
                        "grant": "work:observe",
                        "label": "Old label",
                        "delegable_roles": ["registered"],
                    }
                ],
                "resources": [
                    {
                        "resource": "*/problem-board/*",
                        "label": "Problem Board",
                        "tools": {
                            "project.get": {
                                "label": "Read project",
                                "grants": ["work:observe"],
                            }
                        },
                    }
                ],
            }
        ),
    )
    fragment_path = _write_yaml(tmp_path / "fragment.yaml", _fragment())
    before = descriptor_path.read_bytes()

    result = process_catalog_fragment(
        bundles_path=descriptor_path,
        fragment_path=fragment_path,
        action="check",
    )

    assert result["in_sync"] is False
    assert result["changed"] is False
    assert result["declared"]["direct_tools"] == 2
    assert result["present"]["direct_tools"] == 1
    assert descriptor_path.read_bytes() == before
    differences = {(item["kind"], item["path"]) for item in result["differences"]}
    assert ("value_mismatch", "capabilities[work:observe].label") in differences
    assert ("missing", "capabilities[work:observe].delegable_roles[paid]") in differences
    assert (
        "missing",
        'resources[*/problem-board/*].tools["project.list"]',
    ) in differences


def test_apply_first_install_and_repeat_are_additive_and_idempotent(tmp_path: Path) -> None:
    descriptor_path = _write_yaml(tmp_path / "bundles.yaml", _descriptor())
    fragment_path = _write_yaml(tmp_path / "fragment.yaml", _fragment())

    first = process_catalog_fragment(
        bundles_path=descriptor_path,
        fragment_path=fragment_path,
        action="apply",
    )

    assert first["changed"] is True
    assert first["in_sync"] is True
    assert first["status"] == "applied"
    assert first["present"] == first["declared"]
    assert {item["path"] for item in first["changes"]}.issuperset(
        {
            "capabilities[work:observe]",
            "resources[*/problem-board/*]",
        }
    )
    applied_bytes = descriptor_path.read_bytes()
    applied = yaml.safe_load(applied_bytes)
    assert applied["bundles"]["items"][1] == {
        "id": "sibling@1-0",
        "config": {"retained": True},
    }

    second = process_catalog_fragment(
        bundles_path=descriptor_path,
        fragment_path=fragment_path,
        action="apply",
    )

    assert second["changed"] is False
    assert second["in_sync"] is True
    assert second["changes"] == []
    assert descriptor_path.read_bytes() == applied_bytes


def test_apply_adds_safe_siblings_and_preserves_conflicting_values(tmp_path: Path) -> None:
    descriptor_path = _write_yaml(
        tmp_path / "bundles.yaml",
        _descriptor(
            {
                "capabilities": [
                    {
                        "grant": "work:observe",
                        "label": "Operator-owned label",
                        "delegable_roles": ["registered"],
                    }
                ],
                "resources": [
                    {
                        "resource": "*/problem-board/*",
                        "label": "Problem Board",
                        "tools": {
                            "project.get": {
                                "label": "Read project",
                                "grants": ["work:observe"],
                            }
                        },
                    }
                ],
            }
        ),
    )
    fragment_path = _write_yaml(tmp_path / "fragment.yaml", _fragment())

    result = process_catalog_fragment(
        bundles_path=descriptor_path,
        fragment_path=fragment_path,
        action="apply",
    )

    assert result["changed"] is True
    assert result["in_sync"] is False
    assert result["status"] == "partial"
    assert any(item["path"].endswith('tools["project.list"]') for item in result["changes"])
    assert any(item["path"] == "capabilities[work:observe].label" for item in result["differences"])
    applied = yaml.safe_load(descriptor_path.read_text(encoding="utf-8"))
    oauth = applied["bundles"]["items"][0]["config"]["connections"][
        "delegated_credentials"
    ]["oauth"]
    assert oauth["capabilities"][0]["label"] == "Operator-owned label"
    assert "project.list" in oauth["resources"][0]["tools"]


def test_apply_overwrite_replaces_only_declared_conflicts_and_is_idempotent(
    tmp_path: Path,
) -> None:
    fragment = _fragment()
    descriptor_path = _write_yaml(
        tmp_path / "bundles.yaml",
        _descriptor(
            {
                "capabilities": [
                    {
                        "grant": "work:observe",
                        "label": "Old label",
                        "delegable_roles": ["registered", "legacy"],
                        "retained": "capability sibling",
                    }
                ],
                "resources": [
                    {
                        "resource": "*/problem-board/*",
                        "label": "Old resource label",
                        "retained": "resource sibling",
                        "tools": {
                            "project.get": {
                                "label": "Old tool label",
                                "grants": ["work:observe", "legacy:grant"],
                                "retained": "tool sibling",
                            },
                            "retained.tool": {
                                "label": "Unrelated tool",
                                "grants": ["other:grant"],
                            },
                        },
                    }
                ],
            }
        ),
    )
    fragment_path = _write_yaml(tmp_path / "fragment.yaml", fragment)

    first = process_catalog_fragment(
        bundles_path=descriptor_path,
        fragment_path=fragment_path,
        action="apply",
        overwrite_conflicts=True,
    )

    assert first["changed"] is True
    assert first["in_sync"] is True
    assert first["status"] == "applied"
    assert first["overwrite_conflicts"] is True
    updated_paths = {
        item["path"] for item in first["changes"] if item["kind"] == "updated"
    }
    assert updated_paths == {
        "capabilities[work:observe].label",
        "capabilities[work:observe].delegable_roles",
        "resources[*/problem-board/*].label",
        'resources[*/problem-board/*].tools["project.get"].label',
        'resources[*/problem-board/*].tools["project.get"].grants',
    }
    applied = yaml.safe_load(descriptor_path.read_text(encoding="utf-8"))
    oauth = applied["bundles"]["items"][0]["config"]["connections"][
        "delegated_credentials"
    ]["oauth"]
    assert oauth["capabilities"][0] == {
        **fragment["capabilities"][0],
        "retained": "capability sibling",
    }
    resource = oauth["resources"][0]
    assert resource["label"] == "Problem Board"
    assert resource["retained"] == "resource sibling"
    assert resource["tools"]["project.get"] == {
        **fragment["resources"][0]["tools"]["project.get"],
        "retained": "tool sibling",
    }
    assert resource["tools"]["retained.tool"]["label"] == "Unrelated tool"
    assert "project.list" in resource["tools"]
    first_bytes = descriptor_path.read_bytes()

    second = process_catalog_fragment(
        bundles_path=descriptor_path,
        fragment_path=fragment_path,
        action="apply",
        overwrite_conflicts=True,
    )

    assert second["changed"] is False
    assert second["in_sync"] is True
    assert second["changes"] == []
    assert descriptor_path.read_bytes() == first_bytes


def test_check_rejects_overwrite_mode(tmp_path: Path) -> None:
    descriptor_path = _write_yaml(tmp_path / "bundles.yaml", _descriptor())
    fragment_path = _write_yaml(tmp_path / "fragment.yaml", _fragment())

    with pytest.raises(CatalogFragmentError, match="only for catalog apply"):
        process_catalog_fragment(
            bundles_path=descriptor_path,
            fragment_path=fragment_path,
            action="check",
            overwrite_conflicts=True,
        )


def test_apply_merges_nested_named_service_operations_without_replacing_siblings(
    tmp_path: Path,
) -> None:
    resource_id = "*/api/integrations/bundles/*/*/services/public/mcp/named_services*"
    get_operation = {
        "label": "Get item",
        "grants": ["work:observe"],
    }
    list_operation = {
        "label": "List items",
        "grants": ["work:observe"],
    }
    retained_operation = {
        "label": "Retained sibling",
        "grants": ["other:grant"],
    }
    fragment = {
        "capabilities": [],
        "resources": [
            {
                "resource": resource_id,
                "named_services": {
                    "namespaces": {
                        "work": {
                            "tools": {
                                "action": {
                                    "operations": {
                                        "object.action.item.get": get_operation,
                                        "object.action.item.list": list_operation,
                                    }
                                }
                            }
                        }
                    }
                },
            }
        ],
    }
    deployed_resource = yaml.safe_load(yaml.safe_dump(fragment["resources"][0]))
    operations = deployed_resource["named_services"]["namespaces"]["work"]["tools"][
        "action"
    ]["operations"]
    operations.pop("object.action.item.list")
    operations["object.action.retained"] = retained_operation
    descriptor_path = _write_yaml(
        tmp_path / "bundles.yaml",
        _descriptor({"capabilities": [], "resources": [deployed_resource]}),
    )
    fragment_path = _write_yaml(tmp_path / "fragment.yaml", fragment)

    result = process_catalog_fragment(
        bundles_path=descriptor_path,
        fragment_path=fragment_path,
        action="apply",
    )

    assert result["in_sync"] is True
    assert result["declared"]["named_service_operations"] == 2
    assert result["present"]["named_service_operations"] == 2
    assert [item["path"] for item in result["changes"]] == [
        f'resources[{resource_id}].named_services.namespaces.work.tools.action.operations'
        '["object.action.item.list"]'
    ]
    applied = yaml.safe_load(descriptor_path.read_text(encoding="utf-8"))
    applied_operations = applied["bundles"]["items"][0]["config"]["connections"][
        "delegated_credentials"
    ]["oauth"]["resources"][0]["named_services"]["namespaces"]["work"]["tools"][
        "action"
    ]["operations"]
    assert applied_operations["object.action.item.list"] == list_operation
    assert applied_operations["object.action.retained"] == retained_operation


def test_fragment_removal_does_not_delete_catalog_entries(tmp_path: Path) -> None:
    fragment = _fragment()
    resource = fragment["resources"][0]
    deployed_resource = yaml.safe_load(yaml.safe_dump(resource))
    deployed_resource["tools"]["retained.extra"] = {
        "label": "Another app declaration",
        "grants": ["other:grant"],
    }
    descriptor_path = _write_yaml(
        tmp_path / "bundles.yaml",
        _descriptor(
            {
                "capabilities": yaml.safe_load(yaml.safe_dump(fragment["capabilities"])),
                "resources": [deployed_resource],
            }
        ),
    )
    fragment_path = _write_yaml(tmp_path / "fragment.yaml", fragment)
    before = descriptor_path.read_bytes()

    result = process_catalog_fragment(
        bundles_path=descriptor_path,
        fragment_path=fragment_path,
        action="apply",
    )

    assert result["in_sync"] is True
    assert result["changed"] is False
    assert descriptor_path.read_bytes() == before


def test_check_requires_exact_permission_sets(tmp_path: Path) -> None:
    fragment = _fragment()
    deployed = yaml.safe_load(yaml.safe_dump(fragment))
    deployed["resources"][0]["tools"]["project.get"]["grants"].append("work:coordinate")
    descriptor_path = _write_yaml(
        tmp_path / "bundles.yaml",
        _descriptor(
            {
                "capabilities": deployed["capabilities"],
                "resources": deployed["resources"],
            }
        ),
    )
    fragment_path = _write_yaml(tmp_path / "fragment.yaml", fragment)

    result = process_catalog_fragment(
        bundles_path=descriptor_path,
        fragment_path=fragment_path,
        action="check",
    )

    assert result["in_sync"] is False
    assert result["differences"] == [
        {
            "kind": "value_mismatch",
            "path": 'resources[*/problem-board/*].tools["project.get"].grants',
            "expected": ["work:observe"],
            "actual": ["work:observe", "work:coordinate"],
        }
    ]


def test_fragment_rejects_duplicate_capability_identity() -> None:
    with pytest.raises(CatalogFragmentError, match="more than once"):
        validate_catalog_fragment(
            {
                "capabilities": [
                    {"grant": "work:observe"},
                    {"grant": "work:observe"},
                ]
            }
        )


def test_cli_check_returns_json_and_nonzero_for_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workdir = tmp_path / "demo__project"
    config_dir = workdir / "config"
    _write_yaml(config_dir / "bundles.yaml", _descriptor({"capabilities": [], "resources": []}))
    fragment_path = _write_yaml(tmp_path / "fragment.yaml", _fragment())
    monkeypatch.setattr(cli_mod, "_load_cli_defaults", lambda: {})
    monkeypatch.setattr(
        cli_mod,
        "_resolve_subcommand_workdir",
        lambda *_args, **_kwargs: workdir,
    )
    monkeypatch.setattr(cli_mod, "_resolve_cli_workdir", lambda value, **_kwargs: Path(value))
    monkeypatch.setattr(
        cli_mod,
        "_canonical_descriptor_dir_from_initialized_workdir",
        lambda _workdir: config_dir,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "kdcube",
            "bundle",
            "catalog",
            "check",
            "--workdir",
            str(workdir),
            "--catalog-fragment",
            str(fragment_path),
            "--json",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.main()

    assert exc_info.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert result["action"] == "check"
    assert result["in_sync"] is False
    assert {item["path"] for item in result["differences"]} == {
        "capabilities[work:observe]",
        "resources[*/problem-board/*]",
    }


def test_cli_apply_accepts_explicit_overwrite_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workdir = tmp_path / "demo__project"
    config_dir = workdir / "config"
    deployed = _fragment()
    deployed["capabilities"][0]["label"] = "Old label"
    _write_yaml(
        config_dir / "bundles.yaml",
        _descriptor(
            {
                "capabilities": deployed["capabilities"],
                "resources": deployed["resources"],
            }
        ),
    )
    fragment_path = _write_yaml(tmp_path / "fragment.yaml", _fragment())
    monkeypatch.setattr(cli_mod, "_load_cli_defaults", lambda: {})
    monkeypatch.setattr(
        cli_mod,
        "_resolve_subcommand_workdir",
        lambda *_args, **_kwargs: workdir,
    )
    monkeypatch.setattr(cli_mod, "_resolve_cli_workdir", lambda value, **_kwargs: Path(value))
    monkeypatch.setattr(
        cli_mod,
        "_canonical_descriptor_dir_from_initialized_workdir",
        lambda _workdir: config_dir,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "kdcube",
            "bundle",
            "catalog",
            "apply",
            "--workdir",
            str(workdir),
            "--catalog-fragment",
            str(fragment_path),
            "--overwrite-conflicts",
            "--json",
        ],
    )

    cli_mod.main()

    result = json.loads(capsys.readouterr().out)
    assert result["in_sync"] is True
    assert result["overwrite_conflicts"] is True
    assert result["changes"] == [
        {
            "before": "Old label",
            "kind": "updated",
            "path": "capabilities[work:observe].label",
            "value": "Observe work",
        }
    ]


def test_cli_rejects_overwrite_mode_for_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workdir = tmp_path / "demo__project"
    config_dir = workdir / "config"
    _write_yaml(config_dir / "bundles.yaml", _descriptor())
    fragment_path = _write_yaml(tmp_path / "fragment.yaml", _fragment())
    monkeypatch.setattr(cli_mod, "_load_cli_defaults", lambda: {})
    monkeypatch.setattr(
        cli_mod,
        "_resolve_subcommand_workdir",
        lambda *_args, **_kwargs: workdir,
    )
    monkeypatch.setattr(cli_mod, "_resolve_cli_workdir", lambda value, **_kwargs: Path(value))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "kdcube",
            "bundle",
            "catalog",
            "check",
            "--workdir",
            str(workdir),
            "--catalog-fragment",
            str(fragment_path),
            "--overwrite-conflicts",
        ],
    )

    with pytest.raises(SystemExit, match="only with `kdcube bundle catalog apply`"):
        cli_mod.main()


def test_cli_human_output_preserves_bracketed_catalog_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workdir = tmp_path / "demo__project"
    config_dir = workdir / "config"
    _write_yaml(config_dir / "bundles.yaml", _descriptor({"capabilities": [], "resources": []}))
    fragment_path = _write_yaml(tmp_path / "fragment.yaml", _fragment())
    monkeypatch.setattr(cli_mod, "_load_cli_defaults", lambda: {})
    monkeypatch.setattr(
        cli_mod,
        "_resolve_subcommand_workdir",
        lambda *_args, **_kwargs: workdir,
    )
    monkeypatch.setattr(cli_mod, "_resolve_cli_workdir", lambda value, **_kwargs: Path(value))
    monkeypatch.setattr(
        cli_mod,
        "_canonical_descriptor_dir_from_initialized_workdir",
        lambda _workdir: config_dir,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "kdcube",
            "bundle",
            "catalog",
            "check",
            "--workdir",
            str(workdir),
            "--catalog-fragment",
            str(fragment_path),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.main()

    assert exc_info.value.code == 1
    output = capsys.readouterr().out
    assert "capabilities[work:observe]" in output
    assert "resources[*/problem-board/*]" in output
