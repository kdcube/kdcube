# SPDX-License-Identifier: MIT

from __future__ import annotations

import importlib.util
import inspect
import os
import pathlib
import sys

from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint


def _bundle_root() -> pathlib.Path:
    raw = (os.environ.get("BUNDLE_UNDER_TEST") or "").strip()
    assert raw, "BUNDLE_UNDER_TEST env var is required"
    root = pathlib.Path(raw).expanduser().resolve()
    assert root.is_dir(), f"BUNDLE_UNDER_TEST does not point to a directory: {root}"
    return root


def _load_package_root(bundle_root: pathlib.Path):
    package_name = "_bundle_under_test_pkg"
    init_path = bundle_root / "__init__.py"
    assert init_path.exists(), f"Missing __init__.py: {init_path}"

    spec = importlib.util.spec_from_file_location(
        package_name,
        init_path,
        submodule_search_locations=[str(bundle_root)],
    )
    assert spec and spec.loader, f"Cannot create package spec for {init_path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return package_name, module


def _load_submodule(package_name: str, path: pathlib.Path, submodule: str):
    full_name = f"{package_name}.{submodule}"
    spec = importlib.util.spec_from_file_location(full_name, path)
    assert spec and spec.loader, f"Cannot create module spec for {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def test_bundle_has_required_files():
    bundle_root = _bundle_root()
    required = [
        "__init__.py",
        "entrypoint.py",
        "tools_descriptor.py",
        "skills_descriptor.py",
    ]
    missing = [name for name in required if not (bundle_root / name).exists()]
    assert not missing, f"Bundle is missing required files: {missing}"


def test_bundle_entrypoint_and_descriptors_import():
    bundle_root = _bundle_root()
    package_name, _ = _load_package_root(bundle_root)

    entrypoint_mod = _load_submodule(package_name, bundle_root / "entrypoint.py", "entrypoint")
    _load_submodule(package_name, bundle_root / "tools_descriptor.py", "tools_descriptor")
    _load_submodule(package_name, bundle_root / "skills_descriptor.py", "skills_descriptor")

    bundle_id = getattr(entrypoint_mod, "BUNDLE_ID", "")
    assert isinstance(bundle_id, str) and bundle_id.strip(), "entrypoint.py must define non-empty BUNDLE_ID"

    workflow_classes = [
        obj
        for obj in vars(entrypoint_mod).values()
        if inspect.isclass(obj)
        and obj is not BaseEntrypoint
        and issubclass(obj, BaseEntrypoint)
        and obj.__module__ == entrypoint_mod.__name__
    ]
    assert workflow_classes, "entrypoint.py must expose a bundle workflow class derived from BaseEntrypoint"
