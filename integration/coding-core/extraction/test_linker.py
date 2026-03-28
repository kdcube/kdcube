"""
Links test files to the code entities they test via TESTS edges.
Uses import analysis and naming conventions.
"""

import ast
import logging
import re
from pathlib import Path

log = logging.getLogger("coding-core-mcp")


def _normalize_path(p: str) -> str:
    import os
    return p.replace(os.sep, "/").replace("\\", "/")


def extract_tests(project_root: str, source_roots: list[str],
                  test_patterns: list[str] = None) -> list[dict]:
    """Find all test files and extract Test nodes."""
    if test_patterns is None:
        test_patterns = ["test_*.py", "*_test.py"]

    root = Path(project_root)
    tests = []

    for src_root in source_roots:
        src_dir = root / src_root
        if not src_dir.exists():
            continue

        for pattern in test_patterns:
            for test_file in sorted(src_dir.rglob(pattern)):
                rel_path = _normalize_path(str(test_file.relative_to(root)))

                # Parse test file for class/function names
                try:
                    source = test_file.read_text(encoding="utf-8", errors="replace")
                    tree = ast.parse(source, filename=str(test_file))
                except Exception:
                    continue

                for node in ast.iter_child_nodes(tree):
                    if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                        tests.append({
                            "name": node.name,
                            "file_path": rel_path,
                            "test_class": node.name,
                            "qualified_name": f"{rel_path}::{node.name}",
                        })
                    elif isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                        tests.append({
                            "name": node.name,
                            "file_path": rel_path,
                            "test_class": "",
                            "qualified_name": f"{rel_path}::{node.name}",
                        })

    log.info("[TestLinker] Found %d test entries", len(tests))
    return tests


def link_tests_to_code(tests: list[dict], classes: list[dict],
                       project_root: str) -> list[dict]:
    """
    Create TESTS edges by analyzing imports in test files and name heuristics.
    Returns list of edge dicts for graph.writers.write_tests_edges().
    """
    root = Path(project_root)
    edges = []

    # Build lookup
    class_by_name = {}
    for cls in classes:
        name = cls["name"]
        if name not in class_by_name:
            class_by_name[name] = cls["qualified_name"]

    for test in tests:
        test_file = root / test["file_path"]
        if not test_file.exists():
            continue

        # Strategy 1: Parse imports to find tested classes
        try:
            source = test_file.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=str(test_file))
        except Exception:
            continue

        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    imported_names.add(alias.name)

        for imp_name in imported_names:
            if imp_name in class_by_name:
                edges.append({
                    "test_name": test["name"],
                    "test_file": test["file_path"],
                    "target_qname": class_by_name[imp_name],
                })

        # Strategy 2: Name heuristic — test_base_workflow.py tests BaseWorkflow
        test_stem = Path(test["file_path"]).stem
        # Remove test_ prefix and convert to PascalCase
        name_part = re.sub(r'^test_', '', test_stem)
        # Convert snake_case to PascalCase
        pascal = "".join(word.capitalize() for word in name_part.split("_"))
        if pascal in class_by_name and pascal not in imported_names:
            edges.append({
                "test_name": test["name"],
                "test_file": test["file_path"],
                "target_qname": class_by_name[pascal],
            })

    # Deduplicate
    seen = set()
    unique_edges = []
    for e in edges:
        key = (e["test_name"], e["test_file"], e["target_qname"])
        if key not in seen:
            seen.add(key)
            unique_edges.append(e)

    log.info("[TestLinker] Created %d TESTS edges", len(unique_edges))
    return unique_edges
