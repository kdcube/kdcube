from __future__ import annotations

import ast
from pathlib import Path

from kdcube_ai_app.apps.chat.sdk.application_operations import (
    api_application_operation_ref,
    data_bus_application_operation_ref,
)


BUNDLE_ID = "workspace@2026-03-31-13-36"
ENTRYPOINT = Path(__file__).resolve().parents[1] / "entrypoint.py"


def _entrypoint_contract() -> tuple[ast.Module, dict[str, str]]:
    tree = ast.parse(ENTRYPOINT.read_text(encoding="utf-8"))
    constants = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }
    return tree, constants


def _decorator_keyword(
    tree: ast.Module,
    *,
    method_name: str,
    decorator_name: str,
    keyword: str,
) -> ast.expr:
    method = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == method_name
    )
    decorator = next(
        value
        for value in method.decorator_list
        if isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == decorator_name
    )
    return next(value.value for value in decorator.keywords if value.arg == keyword)


def test_canvas_patch_uses_one_card_operation_across_api_and_data_bus() -> None:
    tree, constants = _entrypoint_contract()
    operation_constant = "CANVAS_PATCH_OPERATION_ID"

    api_operation = _decorator_keyword(
        tree,
        method_name="canvas_patch",
        decorator_name="api",
        keyword="operation_id",
    )
    data_bus_operation = _decorator_keyword(
        tree,
        method_name="handle_canvas_patch_data_bus",
        decorator_name="data_bus_handler",
        keyword="operation_id",
    )

    assert isinstance(api_operation, ast.Name)
    assert isinstance(data_bus_operation, ast.Name)
    assert api_operation.id == data_bus_operation.id == operation_constant

    operation_id = constants[operation_constant]
    api_reference = api_application_operation_ref(
        application_id=BUNDLE_ID,
        alias="canvas_patch",
        method="POST",
        route="operations",
        operation_id=operation_id,
    )
    data_bus_reference = data_bus_application_operation_ref(
        application_id=BUNDLE_ID,
        subject=constants["CANVAS_DATA_BUS_SUBJECT"],
        operation_id=operation_id,
    )
    assert api_reference == data_bus_reference
    assert api_reference == (
        "urn:kdcube:application-operation:"
        "workspace%402026-03-31-13-36:canvas.patch"
    )
