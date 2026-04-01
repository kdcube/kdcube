from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import pathlib
import re
import sys
from types import ModuleType


def sanitize_module_part(value: str) -> str:
    raw = (value or "").strip()
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_")
    if not safe:
        safe = "pkg"
    if safe[0].isdigit():
        safe = f"p_{safe}"
    return safe


def _discover_package_dirs(path: pathlib.Path) -> list[pathlib.Path]:
    package_dirs: list[pathlib.Path] = []
    cur = path.parent
    while (cur / "__init__.py").exists():
        package_dirs.append(cur)
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    package_dirs.reverse()
    return package_dirs


def build_dynamic_module_name(path: str | pathlib.Path) -> str:
    resolved = pathlib.Path(path).resolve()
    package_dirs = _discover_package_dirs(resolved)
    if not package_dirs:
        digest = hashlib.sha1(str(resolved).encode("utf-8")).hexdigest()[:8]
        return f"dyn_{sanitize_module_part(resolved.stem)}_{digest}"

    root_dir = package_dirs[0]
    root_name = f"dynpkg_{hashlib.sha1(str(root_dir).encode('utf-8')).hexdigest()[:10]}"
    current_name = root_name
    for pkg_dir in package_dirs[1:]:
        current_name = f"{current_name}.{sanitize_module_part(pkg_dir.name)}"
    return f"{current_name}.{sanitize_module_part(resolved.stem)}"


def ensure_dynamic_package_chain(module_name: str, file_path: str | pathlib.Path) -> None:
    parts = [p for p in str(module_name or "").split(".") if p]
    if len(parts) < 2:
        return

    path = pathlib.Path(file_path).resolve()
    package_parts = parts[:-1]
    root_dir = path.parent
    for _ in package_parts[1:]:
        root_dir = root_dir.parent

    current_dir = root_dir
    parent_module: ModuleType | None = None
    for idx, part in enumerate(package_parts):
        pkg_name = ".".join(package_parts[: idx + 1])
        pkg_mod = sys.modules.get(pkg_name)
        if pkg_mod is None:
            pkg_mod = importlib.util.module_from_spec(
                importlib.machinery.ModuleSpec(pkg_name, loader=None, is_package=True)
            )
            pkg_mod.__path__ = [str(current_dir)]  # type: ignore[attr-defined]
            pkg_mod.__package__ = pkg_name
            sys.modules[pkg_name] = pkg_mod
        if parent_module is not None and not hasattr(parent_module, part):
            setattr(parent_module, part, pkg_mod)
        parent_module = pkg_mod
        if idx + 1 < len(package_parts):
            current_dir = current_dir / package_parts[idx + 1]


def load_dynamic_module_from_file(module_name: str, file_path: str | pathlib.Path) -> ModuleType:
    path = pathlib.Path(file_path).resolve()
    ensure_dynamic_package_chain(module_name, path)
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load dynamic module {module_name} from {path}")

    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = module_name.rsplit(".", 1)[0] if "." in module_name else ""
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    if "." in module_name:
        parent_name, attr_name = module_name.rsplit(".", 1)
        parent = sys.modules.get(parent_name)
        if parent is not None:
            setattr(parent, attr_name, mod)
    return mod


def load_dynamic_module_for_path(file_path: str | pathlib.Path) -> tuple[str, ModuleType]:
    path = pathlib.Path(file_path).resolve()
    module_name = build_dynamic_module_name(path)
    return module_name, load_dynamic_module_from_file(module_name, path)
