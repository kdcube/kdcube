"""
Python codebase extractor using AST parsing.
Extracts classes, methods, functions, properties, imports, decorators.
Produces dicts ready for graph.writers to write into Neo4j.

LSP augmentation (call hierarchy, type hierarchy, references) is optional
and layered on top of the AST base.
"""

import ast
import logging
import os
from pathlib import Path

log = logging.getLogger("coding-core-mcp")


def _normalize_path(p: str) -> str:
    return p.replace(os.sep, "/").replace("\\", "/")


def _visibility(name: str) -> str:
    if name.startswith("__") and not name.endswith("__"):
        return "private"
    if name.startswith("_"):
        return "protected"
    return "public"


def _get_docstring(node) -> str:
    """Extract docstring from an AST node."""
    ds = ast.get_docstring(node)
    return ds if ds else ""


def _get_signature(node: ast.FunctionDef) -> str:
    """Build a signature string from a function/method AST node."""
    args = []
    all_args = node.args

    # Positional args
    defaults_offset = len(all_args.args) - len(all_args.defaults)
    for i, arg in enumerate(all_args.args):
        name = arg.arg
        annotation = ""
        if arg.annotation:
            try:
                annotation = f": {ast.unparse(arg.annotation)}"
            except Exception:
                pass
        default = ""
        default_idx = i - defaults_offset
        if default_idx >= 0 and default_idx < len(all_args.defaults):
            try:
                default = f" = {ast.unparse(all_args.defaults[default_idx])}"
            except Exception:
                default = " = ..."
        args.append(f"{name}{annotation}{default}")

    # *args
    if all_args.vararg:
        a = all_args.vararg
        ann = f": {ast.unparse(a.annotation)}" if a.annotation else ""
        args.append(f"*{a.arg}{ann}")

    # keyword-only args
    kw_defaults = all_args.kw_defaults
    for i, arg in enumerate(all_args.kwonlyargs):
        name = arg.arg
        annotation = f": {ast.unparse(arg.annotation)}" if arg.annotation else ""
        default = ""
        if i < len(kw_defaults) and kw_defaults[i] is not None:
            try:
                default = f" = {ast.unparse(kw_defaults[i])}"
            except Exception:
                default = " = ..."
        args.append(f"{name}{annotation}{default}")

    # **kwargs
    if all_args.kwarg:
        a = all_args.kwarg
        ann = f": {ast.unparse(a.annotation)}" if a.annotation else ""
        args.append(f"**{a.arg}{ann}")

    ret = ""
    if node.returns:
        try:
            ret = f" -> {ast.unparse(node.returns)}"
        except Exception:
            pass

    return f"({', '.join(args)}){ret}"


def _get_return_type(node: ast.FunctionDef) -> str:
    if node.returns:
        try:
            return ast.unparse(node.returns)
        except Exception:
            pass
    return ""


def _get_decorator_names(node) -> list[str]:
    """Extract decorator names from a class or function."""
    names = []
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name):
            names.append(dec.id)
        elif isinstance(dec, ast.Attribute):
            try:
                names.append(ast.unparse(dec))
            except Exception:
                pass
        elif isinstance(dec, ast.Call):
            if isinstance(dec.func, ast.Name):
                names.append(dec.func.id)
            elif isinstance(dec.func, ast.Attribute):
                try:
                    names.append(ast.unparse(dec.func))
                except Exception:
                    pass
    return names


def _is_abstract_method(node: ast.FunctionDef) -> bool:
    return any(
        (isinstance(d, ast.Name) and d.id == "abstractmethod")
        or (isinstance(d, ast.Attribute) and d.attr == "abstractmethod")
        for d in node.decorator_list
    )


def _is_static_method(node: ast.FunctionDef) -> bool:
    return any(
        (isinstance(d, ast.Name) and d.id == "staticmethod")
        for d in node.decorator_list
    )


def _is_classmethod(node: ast.FunctionDef) -> bool:
    return any(
        (isinstance(d, ast.Name) and d.id == "classmethod")
        for d in node.decorator_list
    )


def _is_property(node: ast.FunctionDef) -> bool:
    return any(
        (isinstance(d, ast.Name) and d.id == "property")
        for d in node.decorator_list
    )


def _has_base(cls_node: ast.ClassDef, name: str) -> bool:
    """Check if a class inherits from a given name."""
    for base in cls_node.bases:
        if isinstance(base, ast.Name) and base.id == name:
            return True
        if isinstance(base, ast.Attribute) and base.attr == name:
            return True
        try:
            if ast.unparse(base) == name:
                return True
        except Exception:
            pass
    return False


def _get_base_names(cls_node: ast.ClassDef) -> list[str]:
    """Get all base class names."""
    bases = []
    for base in cls_node.bases:
        try:
            bases.append(ast.unparse(base))
        except Exception:
            pass
    return bases


def _extract_class_properties(cls_node: ast.ClassDef) -> list[dict]:
    """Extract class-level property annotations and assignments."""
    props = []
    for item in cls_node.body:
        # Type annotations: name: Type = value
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            prop = {
                "name": item.target.id,
                "type_annotation": "",
                "default_value": "",
                "visibility": _visibility(item.target.id),
            }
            try:
                prop["type_annotation"] = ast.unparse(item.annotation)
            except Exception:
                pass
            if item.value:
                try:
                    prop["default_value"] = ast.unparse(item.value)
                except Exception:
                    pass
            props.append(prop)
    return props


def _extract_imports(tree: ast.Module) -> list[dict]:
    """Extract import statements from a module."""
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append({"module": alias.name, "symbol": alias.name})
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append({"module": module, "symbol": alias.name})
    return imports


# ---------------------------------------------------------------------------
# Main extraction function
# ---------------------------------------------------------------------------

def extract_python_file(file_path: str, module_qualified_name: str) -> dict:
    """
    Extract all code entities from a single Python file.

    Returns a dict with keys:
      classes, methods, functions, properties, decorators,
      imports, inherits, containment
    """
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            source = f.read()
        tree = ast.parse(source, filename=file_path)
    except SyntaxError as e:
        log.warning("Syntax error in %s: %s", file_path, e)
        return _empty_result()

    rel_path = _normalize_path(file_path)
    line_count = source.count("\n") + 1

    classes = []
    methods = []
    functions = []
    properties = []
    decorators_set = set()
    decorated_by = []
    inherits = []
    containment = []
    imports = _extract_imports(tree)

    # Module node
    module = {
        "name": module_qualified_name.split(".")[-1],
        "file_path": rel_path,
        "qualified_name": module_qualified_name,
        "language": "python",
        "line_count": line_count,
    }

    # Walk top-level nodes
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            cls_qname = f"{module_qualified_name}.{node.name}"

            # Detect abstract/protocol/dataclass
            is_abstract = _has_base(node, "ABC") or _has_base(node, "ABCMeta")
            is_protocol = _has_base(node, "Protocol")
            dec_names = _get_decorator_names(node)
            is_dataclass = "dataclass" in dec_names

            cls = {
                "name": node.name,
                "qualified_name": cls_qname,
                "module": module_qualified_name,
                "file_path": rel_path,
                "docstring": _get_docstring(node)[:500],
                "line_start": node.lineno,
                "line_end": node.end_lineno or node.lineno,
                "visibility": _visibility(node.name),
                "is_abstract": is_abstract or any(_is_abstract_method(m) for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))),
                "is_dataclass": is_dataclass,
                "is_protocol": is_protocol,
                "language": "python",
            }
            classes.append(cls)

            # Containment: module -> class
            containment.append({
                "parent_qname": module_qualified_name,
                "child_qname": cls_qname,
                "rel_type": "CONTAINS_CLASS",
            })

            # Decorators
            for dec_name in dec_names:
                decorators_set.add(dec_name)
                decorated_by.append({"entity_qname": cls_qname, "decorator_name": dec_name})

            # Base classes -> INHERITS edges
            for base_name in _get_base_names(node):
                if base_name not in ("object", "ABC", "Protocol"):
                    inherits.append({
                        "child_qname": cls_qname,
                        "parent_name": base_name,  # unresolved — needs LSP or import resolution
                    })

            # Class properties
            for prop in _extract_class_properties(node):
                prop_qname = f"{cls_qname}.{prop['name']}"
                prop["qualified_name"] = prop_qname
                prop["class_name"] = node.name
                properties.append(prop)
                containment.append({
                    "parent_qname": cls_qname,
                    "child_qname": prop_qname,
                    "rel_type": "CONTAINS_PROPERTY",
                })

            # Methods
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    meth_qname = f"{cls_qname}.{item.name}"
                    meth = {
                        "name": item.name,
                        "qualified_name": meth_qname,
                        "class_name": node.name,
                        "signature": _get_signature(item),
                        "return_type": _get_return_type(item),
                        "visibility": _visibility(item.name),
                        "is_abstract": _is_abstract_method(item),
                        "is_static": _is_static_method(item),
                        "is_async": isinstance(item, ast.AsyncFunctionDef),
                        "is_property": _is_property(item),
                        "line_start": item.lineno,
                        "line_end": item.end_lineno or item.lineno,
                        "docstring": _get_docstring(item)[:300],
                    }
                    methods.append(meth)
                    containment.append({
                        "parent_qname": cls_qname,
                        "child_qname": meth_qname,
                        "rel_type": "CONTAINS_METHOD",
                    })

                    # Method decorators
                    for dec_name in _get_decorator_names(item):
                        decorators_set.add(dec_name)
                        decorated_by.append({"entity_qname": meth_qname, "decorator_name": dec_name})

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_qname = f"{module_qualified_name}.{node.name}"
            func = {
                "name": node.name,
                "qualified_name": func_qname,
                "module": module_qualified_name,
                "file_path": rel_path,
                "signature": _get_signature(node),
                "return_type": _get_return_type(node),
                "is_async": isinstance(node, ast.AsyncFunctionDef),
                "line_start": node.lineno,
                "line_end": node.end_lineno or node.lineno,
                "docstring": _get_docstring(node)[:300],
            }
            functions.append(func)
            containment.append({
                "parent_qname": module_qualified_name,
                "child_qname": func_qname,
                "rel_type": "CONTAINS_FUNCTION",
            })

            # Function decorators
            for dec_name in _get_decorator_names(node):
                decorators_set.add(dec_name)
                decorated_by.append({"entity_qname": func_qname, "decorator_name": dec_name})

    # Build decorator dicts
    decorator_nodes = [{"name": d, "qualified_name": d} for d in decorators_set]

    return {
        "module": module,
        "classes": classes,
        "methods": methods,
        "functions": functions,
        "properties": properties,
        "decorators": decorator_nodes,
        "decorated_by": decorated_by,
        "imports": imports,
        "inherits": inherits,
        "containment": containment,
    }


def _empty_result() -> dict:
    return {
        "module": None,
        "classes": [],
        "methods": [],
        "functions": [],
        "properties": [],
        "decorators": [],
        "decorated_by": [],
        "imports": [],
        "inherits": [],
        "containment": [],
    }


# ---------------------------------------------------------------------------
# Project-level extraction
# ---------------------------------------------------------------------------

def extract_python_project(project_root: str, source_roots: list[str]) -> dict:
    """
    Extract all Python files under source_roots.

    Returns aggregated result with all entities and relationships.
    """
    root = Path(project_root)
    all_modules = []
    all_classes = []
    all_methods = []
    all_functions = []
    all_properties = []
    all_decorators = {}  # name -> dict
    all_decorated_by = []
    all_imports = []  # raw import info per module
    all_inherits = []  # unresolved base names
    all_containment = []
    all_packages = {}  # qualified_name -> dict

    for src_root in source_roots:
        src_dir = root / src_root
        if not src_dir.exists():
            log.warning("Source root not found: %s", src_dir)
            continue

        py_files = sorted(src_dir.rglob("*.py"))
        log.info("[Extractor] Found %d Python files in %s", len(py_files), src_root)

        for py_file in py_files:
            rel = py_file.relative_to(root)
            # Convert file path to module qualified name
            # e.g., app/ai-app/services/kdcube-ai-app/kdcube_ai_app/apps/chat/api.py
            #     -> kdcube_ai_app.apps.chat.api
            parts = list(rel.with_suffix("").parts)
            # Find the first part that looks like a Python package (has __init__.py sibling)
            module_qname = _path_to_module_qname(py_file, src_dir)

            # Register packages
            _register_packages(module_qname, all_packages, src_dir, root)

            result = extract_python_file(str(py_file), module_qname)
            if result["module"] is None:
                continue

            # Normalize file paths to be relative to project root
            result["module"]["file_path"] = _normalize_path(str(rel))
            for cls in result["classes"]:
                cls["file_path"] = _normalize_path(str(rel))
            for func in result["functions"]:
                func["file_path"] = _normalize_path(str(rel))

            all_modules.append(result["module"])
            all_classes.extend(result["classes"])
            all_methods.extend(result["methods"])
            all_functions.extend(result["functions"])
            all_properties.extend(result["properties"])
            for dec in result["decorators"]:
                all_decorators[dec["name"]] = dec
            all_decorated_by.extend(result["decorated_by"])
            all_imports.append({
                "module_qname": module_qname,
                "imports": result["imports"],
            })
            all_inherits.extend(result["inherits"])
            all_containment.extend(result["containment"])

    # Resolve INHERITS edges: match unresolved base names to known class qnames
    class_by_name = {}
    for cls in all_classes:
        name = cls["name"]
        if name not in class_by_name:
            class_by_name[name] = []
        class_by_name[name].append(cls["qualified_name"])

    resolved_inherits = []
    for edge in all_inherits:
        base_name = edge["parent_name"]
        # Try exact match by simple name
        simple_name = base_name.split(".")[-1]
        candidates = class_by_name.get(simple_name, [])
        if len(candidates) == 1:
            resolved_inherits.append({
                "child_qname": edge["child_qname"],
                "parent_qname": candidates[0],
            })
        elif len(candidates) > 1:
            # Ambiguous — try to resolve by import context
            # For now, take the first match
            resolved_inherits.append({
                "child_qname": edge["child_qname"],
                "parent_qname": candidates[0],
            })

    # Resolve IMPORTS edges: match module names to known module qnames
    module_by_name = {m["qualified_name"]: m for m in all_modules}
    resolved_imports = []
    for mod_info in all_imports:
        src_qname = mod_info["module_qname"]
        for imp in mod_info["imports"]:
            target_module = imp["module"]
            if target_module in module_by_name:
                resolved_imports.append({
                    "source_module_qname": src_qname,
                    "target_module_qname": target_module,
                    "symbol": imp["symbol"],
                })

    # Add package containment
    for pkg_qname, pkg in all_packages.items():
        parts = pkg_qname.split(".")
        if len(parts) > 1:
            parent_qname = ".".join(parts[:-1])
            if parent_qname in all_packages:
                all_containment.append({
                    "parent_qname": parent_qname,
                    "child_qname": pkg_qname,
                    "rel_type": "CONTAINS_PACKAGE",
                })

    # Module -> Package containment
    for mod in all_modules:
        parts = mod["qualified_name"].split(".")
        if len(parts) > 1:
            pkg_qname = ".".join(parts[:-1])
            if pkg_qname in all_packages:
                all_containment.append({
                    "parent_qname": pkg_qname,
                    "child_qname": mod["qualified_name"],
                    "rel_type": "CONTAINS_MODULE",
                })

    stats = {
        "modules": len(all_modules),
        "classes": len(all_classes),
        "methods": len(all_methods),
        "functions": len(all_functions),
        "properties": len(all_properties),
        "decorators": len(all_decorators),
        "inherits": len(resolved_inherits),
        "imports": len(resolved_imports),
        "containment": len(all_containment),
    }
    log.info("[Extractor] Extraction complete: %s", stats)

    return {
        "packages": list(all_packages.values()),
        "modules": all_modules,
        "classes": all_classes,
        "methods": all_methods,
        "functions": all_functions,
        "properties": all_properties,
        "decorators": list(all_decorators.values()),
        "decorated_by": all_decorated_by,
        "inherits": resolved_inherits,
        "imports": resolved_imports,
        "containment": all_containment,
        "stats": stats,
    }


def _path_to_module_qname(file_path: Path, src_root: Path) -> str:
    """Convert a file path to a Python module qualified name."""
    rel = file_path.relative_to(src_root)
    parts = list(rel.with_suffix("").parts)
    # Remove __init__ from the end — it's the package itself
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    # Replace hyphens with underscores in package names
    parts = [p.replace("-", "_") for p in parts]
    return ".".join(parts) if parts else file_path.stem


def _register_packages(module_qname: str, packages: dict, src_dir: Path, root: Path):
    """Register all parent packages for a module."""
    parts = module_qname.split(".")
    for i in range(1, len(parts)):
        pkg_qname = ".".join(parts[:i])
        if pkg_qname not in packages:
            pkg_path = src_dir
            for p in parts[:i]:
                pkg_path = pkg_path / p
            rel_path = _normalize_path(str(pkg_path.relative_to(root))) if pkg_path.exists() else ""
            packages[pkg_qname] = {
                "name": parts[i - 1],
                "path": rel_path,
                "qualified_name": pkg_qname,
            }
