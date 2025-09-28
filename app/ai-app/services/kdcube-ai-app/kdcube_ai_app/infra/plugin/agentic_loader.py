# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/plugin/agentic_loader.py
from __future__ import annotations

import importlib
import importlib.util
import sys
import inspect
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple, Any, Dict, List

# --------------------------------------------------------------------------------------
# Public decorators — the ONLY way to mark workflow factory/class and optional init
# --------------------------------------------------------------------------------------

AGENTIC_ROLE_ATTR = "__agentic_role__"
AGENTIC_META_ATTR = "__agentic_meta__"

def agentic_workflow_factory(
        *,
        name: str | None = None,
        version: str | None = None,
        priority: int = 100,
        singleton: bool | None = None,
):
    """
    Mark a function as the bundle's workflow FACTORY.
    Recommended signature (flexible):
        fn(config, *, communicator=None, step_emitter=None, delta_emitter=None) -> workflow_instance
    Only the kwargs present in the function signature will be passed.
    """
    def _wrap(fn):
        setattr(fn, AGENTIC_ROLE_ATTR, "workflow_factory")
        setattr(fn, AGENTIC_META_ATTR, {
            "name": name, "version": version, "priority": priority, "singleton": singleton
        })
        return fn
    return _wrap


def agentic_workflow(
        *,
        name: str | None = None,
        version: str | None = None,
        priority: int = 100,
):
    """
    Mark a CLASS as the bundle's workflow CLASS.
    Recommended signature (flexible):
        class(config, *, communicator=None, step_emitter=None, delta_emitter=None)
    Only the kwargs present in the __init__ signature will be passed.
    """
    def _wrap(cls):
        setattr(cls, AGENTIC_ROLE_ATTR, "workflow_class")
        setattr(cls, AGENTIC_META_ATTR, {
            "name": name, "version": version, "priority": priority
        })
        return cls
    return _wrap


def agentic_initial_state(
        *,
        name: str | None = None,
        priority: int = 100,
):
    """
    Mark a function that builds the initial state.
    Signature: fn(user_message: str) -> state(dict|TypedDict)
    """
    def _wrap(fn):
        setattr(fn, AGENTIC_ROLE_ATTR, "initial_state")
        setattr(fn, AGENTIC_META_ATTR, {"name": name, "priority": priority})
        return fn
    return _wrap


# --------------------------------------------------------------------------------------
# Spec & caches
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class AgenticBundleSpec:
    """
    Where/how to load a bundle module:
      - path: file.py | package_dir/ | archive.zip/.whl
      - module: dotted module **inside** path (required for zip/whl; optional otherwise)
      - singleton: if True, cache & reuse the workflow instance
    """
    path: str
    module: Optional[str] = None
    singleton: bool = False

_module_cache: Dict[str, types.ModuleType] = {}
_singleton_cache: Dict[str, Tuple[Any, Optional[Callable[[str], Any]], types.ModuleType]] = {}

def _cache_key(spec: AgenticBundleSpec) -> str:
    return f"{Path(spec.path).resolve()}::{spec.module or ''}"

# --------------------------------------------------------------------------------------
# Module loading
# --------------------------------------------------------------------------------------

def _load_module_from_file(path: Path, name_hint: str) -> types.ModuleType:
    mname = f"{name_hint}_{abs(hash(str(path)))}"
    spec = importlib.util.spec_from_file_location(mname, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create module spec from file: {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mname] = mod
    spec.loader.exec_module(mod)
    return mod

def _load_package_root(pkg_dir: Path) -> types.ModuleType:
    if not (pkg_dir / "__init__.py").exists():
        raise ImportError(f"Directory is not a package (missing __init__.py): {pkg_dir}")
    parent = str(pkg_dir.parent.resolve())
    if parent not in sys.path:
        sys.path.insert(0, parent)
    pkg_name = pkg_dir.name.replace("-", "_")
    return importlib.import_module(pkg_name)

def _load_from_sys_with_path_on_syspath(container_path: Path, module: str) -> types.ModuleType:
    root = str(container_path.resolve())
    if root not in sys.path:
        sys.path.insert(0, root)
    return importlib.import_module(module)

def _resolve_module(spec: AgenticBundleSpec) -> types.ModuleType:
    key = _cache_key(spec)
    if key in _module_cache:
        return _module_cache[key]

    p = Path(spec.path)
    if not p.exists():
        raise FileNotFoundError(f"Agentic bundle path does not exist: {p}")

    if p.is_file():
        if p.suffix in {".zip", ".whl"}:
            if not spec.module:
                raise ImportError("For .zip/.whl bundles you must provide 'module' (e.g., 'customer_bundle').")
            mod = _load_from_sys_with_path_on_syspath(p, spec.module)
        elif p.suffix == ".py":
            mod = _load_module_from_file(p, "agentic_bundle")
        else:
            raise ImportError(f"Unsupported file type for agentic bundle: {p}")
    else:
        # directory
        if spec.module:
            mod = _load_from_sys_with_path_on_syspath(p, spec.module)
        else:
            mod = _load_package_root(p)

    _module_cache[key] = mod
    return mod

# --------------------------------------------------------------------------------------
# Discovery (decorators ONLY)
# --------------------------------------------------------------------------------------

def _discover_decorated(mod: types.ModuleType):
    factories: List[Tuple[int, Dict[str, Any], Callable[..., Any]]] = []
    classes:   List[Tuple[int, Dict[str, Any], type]] = []
    inits:     List[Tuple[int, Dict[str, Any], Callable[[str], Any]]] = []

    for obj in vars(mod).values():
        role = getattr(obj, AGENTIC_ROLE_ATTR, None)
        meta = getattr(obj, AGENTIC_META_ATTR, {}) or {}
        prio = int(meta.get("priority", 100))

        if role == "workflow_factory" and callable(obj):
            factories.append((prio, meta, obj))  # fn(config, step_emitter, delta_emitter)
        elif role == "workflow_class" and isinstance(obj, type):
            classes.append((prio, meta, obj))    # class(config, step_emitter, delta_emitter)
        elif role == "initial_state" and callable(obj):
            inits.append((prio, meta, obj))      # fn(user_message)

    # sort by priority desc, then by name to stabilize
    factories.sort(key=lambda t: (-t[0], getattr(t[2], "__name__", "")))
    classes.sort(key=lambda t: (-t[0], getattr(t[2], "__name__", "")))
    inits.sort(key=lambda t: (-t[0], getattr(t[2], "__name__", "")))

    # choose winner across factory/class by highest priority; tie → prefer factory
    winner_factory = factories[0] if factories else None
    winner_class   = classes[0] if classes else None

    if winner_factory and winner_class:
        if winner_factory[0] > winner_class[0]:
            chosen = ("factory", winner_factory[1], winner_factory[2])
        elif winner_class[0] > winner_factory[0]:
            chosen = ("class", winner_class[1], winner_class[2])
        else:
            chosen = ("factory", winner_factory[1], winner_factory[2])  # tie → factory
    elif winner_factory:
        chosen = ("factory", winner_factory[1], winner_factory[2])
    elif winner_class:
        chosen = ("class", winner_class[1], winner_class[2])
    else:
        chosen = None

    init_fn = inits[0][2] if inits else None
    return chosen, init_fn

def _select_supported_kwargs(symbol: Any, provided: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return only those kwargs that the target symbol actually accepts.
    Works for functions and classes (uses __init__ for classes).
    """
    try:
        sig = inspect.signature(symbol if not isinstance(symbol, type) else symbol.__init__)
    except Exception:
        # if we can't introspect, be conservative
        return {}
    supported = {}
    for name in provided.keys():
        if name in sig.parameters:
            supported[name] = provided[name]
    return supported

def _instantiate_symbol(kind: str, symbol: Any, config: Any, extra_kwargs: Dict[str, Any]):
    """
    Instantiate a factory/class while passing only supported kwargs.
    """
    call_kwargs = _select_supported_kwargs(symbol, extra_kwargs)
    if kind == "factory":
        # factories are callables returning an instance
        return symbol(config, **call_kwargs)
    else:
        # classes to be constructed
        return symbol(config, **call_kwargs)

# --------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------

def get_workflow_instance(
        spec: AgenticBundleSpec,
        config: Any,
        *,
        communicator: Optional[Any] = None,        # ← optional unified communicator
        pg_pool: Optional[Any] = None,             # ← optional DB pools
        redis: Optional[Any] = None,               # ← optional DB pools
) -> Tuple[Any, Optional[Callable[[str], Any]], types.ModuleType]:
    """
    Load the bundle at 'spec', discover decorated symbols, instantiate a workflow,
    and return (workflow_instance, initial_state_fn_or_None, module).

    Notes:
    - ONLY decorated @agentic_workflow_factory / @agentic_workflow are recognized.
    - If both exist, the higher 'priority' wins (tie → factory wins).
    - Singleton is honored if:
        * spec.singleton is True, OR
        * the chosen factory has decorator meta singleton=True
    - initial_state is optional; if not provided, returns None.
    """
    key = _cache_key(spec)
    # singleton cache hit?
    if spec.singleton and key in _singleton_cache:
        inst, init_fn, mod = _singleton_cache[key]
        return inst, init_fn, mod

    mod = _resolve_module(spec)
    chosen, init_fn = _discover_decorated(mod)

    if not chosen:
        raise AttributeError(
            f"No decorated workflow found in module '{mod.__name__}'. "
            f"Use @agentic_workflow_factory or @agentic_workflow."
        )

    chosen_kind, meta, symbol = chosen

    # instantiate
    extra_kwargs = {
        "communicator": communicator,
        "comm": communicator,
        "pg_pool": pg_pool,
        "redis": redis,
    }

    if chosen_kind == "factory":
        instance = _instantiate_symbol("factory", symbol, config, extra_kwargs)
        dec_singleton = bool(meta.get("singleton"))
        final_singleton = bool(spec.singleton or dec_singleton)
    else:
        instance = _instantiate_symbol("class", symbol, config, extra_kwargs)
        final_singleton = bool(spec.singleton)

    if final_singleton:
        _singleton_cache[key] = (instance, init_fn, mod)

    return instance, init_fn, mod

def clear_agentic_caches() -> None:
    """Utility for tests/dev hot-reload."""
    _module_cache.clear()
    _singleton_cache.clear()
