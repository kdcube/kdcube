# SPDX-License-Identifier: MIT
"""SDK code imports by absolute name (AGENTS.md, "Code architecture").

The tool subsystem loads SDK tool modules as dynamic copies inside a synthetic
package (``runtime/dynamic_module_loader.py``). A relative import in such a copy
executes the imported module a second time, with its own ContextVars, classes
and registries, so state written by the canonical module is invisible to it
(kdcube#270 D6, W39). App bundles are the opposite case: they must use
package-relative imports, and the bundle import contract checks them.
"""

from __future__ import annotations

import ast
import pathlib

import kdcube_ai_app

SDK_ROOT = pathlib.Path(kdcube_ai_app.__file__).resolve().parent
# Example apps and stand-alone example packages follow the app rule.
APP_CODE = ("apps/chat/sdk/examples/",)


def _relative_imports():
    for path in sorted(SDK_ROOT.rglob("*.py")):
        relative = path.relative_to(SDK_ROOT).as_posix()
        if relative.startswith(APP_CODE):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level:
                yield f"kdcube_ai_app/{relative}:{node.lineno}"


def test_sdk_code_uses_absolute_imports_only():
    offenders = list(_relative_imports())
    assert not offenders, "relative imports in SDK code:\n" + "\n".join(offenders)
