from __future__ import annotations

import ast
from pathlib import Path

import yaml

from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.relay import (
    NAMED_SERVICE_RELAY_SUBJECT,
)


BUNDLE_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = BUNDLE_ROOT / "entrypoint.py"
OPENAPI = BUNDLE_ROOT / "interface" / "kdcube-services.openapi.yaml"
DECLARED_CONSTANTS = {
    "NAMED_SERVICE_RELAY_SUBJECT": NAMED_SERVICE_RELAY_SUBJECT,
}


def _decorator_name(node: ast.expr) -> str:
    target = node.func if isinstance(node, ast.Call) else node
    return target.id if isinstance(target, ast.Name) else ""


def _literal_keyword(call: ast.Call, name: str, default):
    for keyword in call.keywords:
        if keyword.arg == name:
            if isinstance(keyword.value, ast.Name):
                return DECLARED_CONSTANTS[keyword.value.id]
            return ast.literal_eval(keyword.value)
    return default


def _declared_surfaces() -> dict[str, set]:
    tree = ast.parse(ENTRYPOINT.read_text(encoding="utf-8"))
    declared: dict[str, set] = {
        "api": set(),
        "widgets": set(),
        "mcp": set(),
        "data_bus": set(),
    }
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            name = _decorator_name(decorator)
            if name == "api":
                declared["api"].add(
                    (
                        str(_literal_keyword(decorator, "route", "operations")),
                        str(_literal_keyword(decorator, "alias", node.name)),
                        str(_literal_keyword(decorator, "method", "POST")).lower(),
                    )
                )
            elif name == "ui_widget":
                declared["widgets"].add(str(_literal_keyword(decorator, "alias", node.name)))
            elif name == "mcp":
                declared["mcp"].add(
                    (
                        str(_literal_keyword(decorator, "route", "operations")),
                        str(_literal_keyword(decorator, "alias", node.name)),
                    )
                )
            elif name == "data_bus_handler":
                declared["data_bus"].add(str(_literal_keyword(decorator, "subject", "")))
    return declared


def test_openapi_matches_entrypoint_decorators() -> None:
    contract = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    paths = contract["paths"]
    declared = _declared_surfaces()
    prefix = "/bundles/{tenant}/{project}/{bundle_id}"

    for route, alias, method in declared["api"]:
        path = f"{prefix}/{route}/{alias}"
        assert path in paths, f"missing OpenAPI path for @{route} API {alias}"
        assert method in paths[path], f"missing OpenAPI method {method.upper()} for {alias}"

    for alias in declared["widgets"]:
        assert f"{prefix}/widgets/{alias}" in paths, f"missing widget path for {alias}"

    for route, alias in declared["mcp"]:
        assert f"{prefix}/{route}/mcp/{alias}" in paths, f"missing MCP path for {alias}"

    extension = contract["x-kdcube-surfaces"]
    assert declared["data_bus"] == set(extension["data_bus_handlers"])


def test_required_package_contract_is_present() -> None:
    required = [
        "README.md",
        "AGENTS.md",
        "release.yaml",
        "config/bundles.template.yaml",
        "config/bundles.secrets.template.yaml",
        "interface/README.md",
        "interface/kdcube-services.openapi.yaml",
        "docs/README.md",
        "docs/storage/README.md",
        "docs/journal/README.md",
        "docs/journal/journal.md",
    ]
    missing = [path for path in required if not (BUNDLE_ROOT / path).is_file()]
    assert not missing, f"missing app package declarations: {missing}"

    secrets = yaml.safe_load(
        (BUNDLE_ROOT / "config" / "bundles.secrets.template.yaml").read_text(encoding="utf-8")
    )
    item = next(entry for entry in secrets["bundles"]["items"] if entry["id"] == "kdcube-services@1-0")
    assert item["secrets"]["conversations"]["file_download_secret"]
