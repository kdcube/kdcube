from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


_SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "tests",
    "ui",
    "venv",
}


@dataclass(frozen=True)
class BundleLocalImportViolation:
    path: Path
    line: int
    column: int
    import_name: str
    local_root: str

    def render(self, bundle_dir: Path) -> str:
        try:
            display = self.path.relative_to(bundle_dir)
        except ValueError:
            display = self.path
        return (
            f"{display}:{self.line}:{self.column + 1}: "
            f"bundle-local import {self.import_name!r} uses top-level root "
            f"{self.local_root!r}; use package-relative imports instead"
        )


def bundle_local_import_roots(bundle_dir: Path) -> set[str]:
    """Return top-level Python names owned by this bundle directory."""
    roots: set[str] = set()
    for child in bundle_dir.iterdir():
        name = child.name
        if name.startswith(".") or name in _SKIP_DIRS:
            continue
        if child.is_dir() and any(child.rglob("*.py")):
            roots.add(name)
        elif child.is_file() and child.suffix == ".py" and child.name != "__init__.py":
            roots.add(child.stem)
    return roots


def iter_bundle_python_files(bundle_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in bundle_dir.rglob("*.py"):
        rel_parts = path.relative_to(bundle_dir).parts
        if any(part.startswith(".") or part in _SKIP_DIRS for part in rel_parts[:-1]):
            continue
        files.append(path)
    return sorted(files)


def collect_bundle_local_import_violations(bundle_dir: Path) -> list[BundleLocalImportViolation]:
    bundle_dir = bundle_dir.resolve()
    local_roots = bundle_local_import_roots(bundle_dir)
    if not local_roots:
        return []

    violations: list[BundleLocalImportViolation] = []
    for path in iter_bundle_python_files(bundle_dir):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    import_name = str(alias.name or "")
                    root = import_name.split(".", 1)[0]
                    if root in local_roots:
                        violations.append(
                            BundleLocalImportViolation(
                                path=path,
                                line=node.lineno,
                                column=node.col_offset,
                                import_name=import_name,
                                local_root=root,
                            )
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                import_name = str(node.module or "")
                root = import_name.split(".", 1)[0]
                if root in local_roots:
                    violations.append(
                        BundleLocalImportViolation(
                            path=path,
                            line=node.lineno,
                            column=node.col_offset,
                            import_name=import_name,
                            local_root=root,
                        )
                    )
    return violations
