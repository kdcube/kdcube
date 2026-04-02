# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/plugin/agentic_loader.py
from __future__ import annotations

import importlib
import importlib.util
import sys
import inspect
import types
import asyncio
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple, Any, Dict, List

from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.infra.service_hub.inventory import AgentLogger

# --------------------------------------------------------------------------------------
# Public decorators — the ONLY way to mark workflow factory/class and optional init
# --------------------------------------------------------------------------------------

AGENTIC_ROLE_ATTR = "__agentic_role__"
AGENTIC_META_ATTR = "__agentic_meta__"
API_METHOD_ATTR = "__bundle_api_method__"
UI_WIDGET_ATTR = "__bundle_ui_widget__"
ON_MESSAGE_ATTR = "__bundle_on_message__"
UI_MAIN_ATTR = "__bundle_ui_main__"


@dataclass(frozen=True)
class APIEndpointSpec:
    method_name: str
    alias: str
    http_method: str = "POST"
    roles: tuple[str, ...] = ()


@dataclass(frozen=True)
class UIWidgetSpec:
    method_name: str
    alias: str
    icon: dict[str, str]
    roles: tuple[str, ...] = ()


@dataclass(frozen=True)
class OnMessageSpec:
    method_name: str


@dataclass(frozen=True)
class UIMainSpec:
    method_name: str


@dataclass(frozen=True)
class BundleInterfaceManifest:
    bundle_id: str
    ui_widgets: tuple[UIWidgetSpec, ...] = ()
    api_endpoints: tuple[APIEndpointSpec, ...] = ()
    ui_main: UIMainSpec | None = None
    on_message: OnMessageSpec | None = None


def _clean_alias(value: str | None, default: str) -> str:
    alias = str(value or "").strip()
    return alias or default


def _tuple_str(values: Any) -> tuple[str, ...]:
    if not values:
        return ()
    if isinstance(values, (str, bytes)):
        raw = [values]
    else:
        raw = list(values)
    out: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return tuple(out)


def _normalize_http_method(value: str | None) -> str:
    method = str(value or "POST").strip().upper() or "POST"
    if method not in {"GET", "POST"}:
        raise ValueError(f"Unsupported bundle api method: {method}")
    return method


def _normalize_icon_spec(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if isinstance(value, str):
        text = value.strip()
        return {"tailwind": text} if text else {}
    if not isinstance(value, dict):
        raise ValueError("Bundle widget icon must be a string or a dict of provider -> icon name")
    out: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key or "").strip().lower()
        if key == "lucid":
            key = "lucide"
        if key not in {"tailwind", "lucide"}:
            raise ValueError(f"Unsupported widget icon provider: {raw_key}")
        text = str(raw_value or "").strip()
        if text:
            out[key] = text
    return out

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


def api(
        *,
        method: str = "POST",
        alias: str | None = None,
        roles: List[str] | Tuple[str, ...] | None = None,
):
    http_method = _normalize_http_method(method)

    def _wrap(fn):
        setattr(
            fn,
            API_METHOD_ATTR,
            APIEndpointSpec(
                method_name=getattr(fn, "__name__", "api_method"),
                alias=_clean_alias(alias, getattr(fn, "__name__", "api_method")),
                http_method=http_method,
                roles=_tuple_str(roles),
            ),
        )
        return fn

    return _wrap


def ui_widget(
        *,
        icon: str | Dict[str, str],
        alias: str | None = None,
        roles: List[str] | Tuple[str, ...] | None = None,
):
    def _wrap(fn):
        setattr(
            fn,
            UI_WIDGET_ATTR,
            UIWidgetSpec(
                method_name=getattr(fn, "__name__", "widget"),
                alias=_clean_alias(alias, getattr(fn, "__name__", "widget")),
                icon=_normalize_icon_spec(icon),
                roles=_tuple_str(roles),
            ),
        )
        return fn

    return _wrap


def on_message(fn):
    setattr(
        fn,
        ON_MESSAGE_ATTR,
        OnMessageSpec(method_name=getattr(fn, "__name__", "run")),
    )
    return fn


def ui_main(fn):
    setattr(
        fn,
        UI_MAIN_ATTR,
        UIMainSpec(method_name=getattr(fn, "__name__", "main_ui")),
    )
    return fn


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
_singleton_cache: Dict[str, Tuple[Any, types.ModuleType]] = {}
_bundle_load_done: set[str] = set()
_bundle_load_inflight: set[str] = set()
_bundle_load_lock = threading.Lock()

def _cache_key(spec: AgenticBundleSpec) -> str:
    return f"{Path(spec.path).resolve()}::{spec.module or ''}"

def cache_key_for_spec(spec: AgenticBundleSpec) -> str:
    return _cache_key(spec)

def _tp_from_ctx(ctx: ChatTaskPayload) -> tuple[Optional[str], Optional[str]]:
    t = getattr(getattr(ctx, "actor", None), "tenant_id", None)
    p = getattr(getattr(ctx, "actor", None), "project_id", None)
    if not t or not p:
        t = t or getattr(getattr(ctx, "meta", None), "tenant", None)
        p = p or getattr(getattr(ctx, "meta", None), "project", None)
    return t, p

def _bundle_load_key(spec: AgenticBundleSpec, comm_context: ChatTaskPayload) -> str:
    t, p = _tp_from_ctx(comm_context)
    return f"{_cache_key(spec)}::{t or 'default'}::{p or 'default'}"

def _maybe_run_bundle_on_load(
    *,
    instance: Any,
    mod: types.ModuleType,
    spec: AgenticBundleSpec,
    config: Any,
    comm_context: ChatTaskPayload,
    pg_pool: Optional[Any],
    redis: Optional[Any],
) -> None:
    hook = None
    if hasattr(instance, "on_bundle_load") and callable(getattr(instance, "on_bundle_load")):
        hook = getattr(instance, "on_bundle_load")
    elif hasattr(mod, "on_bundle_load") and callable(getattr(mod, "on_bundle_load")):
        hook = getattr(mod, "on_bundle_load")
    if hook is None:
        return

    key = _bundle_load_key(spec, comm_context)
    with _bundle_load_lock:
        if key in _bundle_load_done or key in _bundle_load_inflight:
            return
        _bundle_load_inflight.add(key)

    logger = AgentLogger("bundle.on_load", getattr(config, "log_level", "INFO"))
    try:
        from kdcube_ai_app.infra.plugin.bundle_storage import storage_for_spec
    except Exception:
        storage_for_spec = None

    bundle_spec = getattr(config, "ai_bundle_spec", None)
    tenant, project = _tp_from_ctx(comm_context)
    bundle_id = getattr(bundle_spec, "id", None) or getattr(bundle_spec, "name", None) or spec.path
    storage_root = None
    if storage_for_spec is not None:
        try:
            storage_root = storage_for_spec(
                spec=bundle_spec,
                tenant=tenant,
                project=project,
                ensure=True,
            )
        except Exception:
            storage_root = None

    kwargs = {
        "bundle_spec": bundle_spec,
        "agentic_spec": spec,
        "storage_root": storage_root,
        "config": config,
        "comm_context": comm_context,
        "pg_pool": pg_pool,
        "redis": redis,
        "logger": logger,
    }
    call_kwargs = _select_supported_kwargs(hook, kwargs)
    try:
        logger.log(
            f"[bundle.on_load] start: bundle={bundle_id} tenant={tenant} project={project} storage={storage_root}",
            level="INFO",
        )
        res = hook(**call_kwargs)
        if asyncio.iscoroutine(res):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(res)
            else:
                # avoid blocking the request; warn that the hook should be sync
                loop.create_task(res)
                logger.log("[bundle.on_load] async hook scheduled in background (prefer sync)", level="WARNING")
        with _bundle_load_lock:
            _bundle_load_done.add(key)
        logger.log(
            f"[bundle.on_load] done: bundle={bundle_id} tenant={tenant} project={project}",
            level="INFO",
        )
    except Exception:
        logger.log("[bundle.on_load] hook failed:\n" + traceback.format_exc(), level="ERROR")
        raise
    finally:
        with _bundle_load_lock:
            _bundle_load_inflight.discard(key)

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

def _ensure_virtual_package(root_name: str, path: Path) -> None:
    if root_name in sys.modules:
        return
    pkg = types.ModuleType(root_name)
    pkg.__path__ = [str(path.resolve())]
    pkg.__package__ = root_name
    sys.modules[root_name] = pkg

def _ensure_virtual_subpackages(root_name: str, path: Path, sub_pkg: str) -> None:
    if not sub_pkg:
        return
    curr = root_name
    base = path.resolve()
    for part in sub_pkg.split("."):
        curr = f"{curr}.{part}"
        base = base / part
        if curr in sys.modules:
            continue
        pkg = types.ModuleType(curr)
        pkg.__path__ = [str(base)]
        pkg.__package__ = curr
        sys.modules[curr] = pkg

def _load_module_from_dir(container_path: Path, module: str) -> types.ModuleType:
    root_pkg = f"kdcube_bundle_{abs(hash(str(container_path.resolve())))}"
    _ensure_virtual_package(root_pkg, container_path)
    sub_pkg = module.rsplit(".", 1)[0] if "." in module else ""
    _ensure_virtual_subpackages(root_pkg, container_path, sub_pkg)

    target = container_path / f"{module.replace('.', '/')}.py"
    if not target.exists() and "." in module:
        # Allow "folder.name.entrypoint" where folder contains dots or other separators.
        # Fallback: treat only the last segment as the module filename.
        alt_module = module.rsplit(".", 1)[-1]
        alt_target = container_path / f"{alt_module.replace('.', '/')}.py"
        if alt_target.exists():
            module = alt_module
            sub_pkg = ""
            target = alt_target
    if not target.exists():
        raise ImportError(f"Module file not found in bundle dir: {target}")

    full_name = f"{root_pkg}.{module}"
    spec = importlib.util.spec_from_file_location(full_name, str(target))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create module spec from file: {target}")
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = f"{root_pkg}.{sub_pkg}" if sub_pkg else root_pkg
    sys.modules[full_name] = mod
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
    try:
        return importlib.import_module(module)
    except ModuleNotFoundError as e:
        # Only fallback if the *requested* module is missing.
        missing = (e.name or "").split(".")[0]
        if missing != (module.split(".")[0] if module else ""):
            raise
        return _load_module_from_dir(container_path, module)
    except ImportError as e:
        # If module was imported as top-level, relative imports may fail.
        if "attempted relative import with no known parent package" in str(e):
            return _load_module_from_dir(container_path, module)
        raise

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

    for obj in vars(mod).values():
        role = getattr(obj, AGENTIC_ROLE_ATTR, None)
        meta = getattr(obj, AGENTIC_META_ATTR, {}) or {}
        prio = int(meta.get("priority", 100))

        if role == "workflow_factory" and callable(obj):
            factories.append((prio, meta, obj))  # fn(config, step_emitter, delta_emitter)
        elif role == "workflow_class" and isinstance(obj, type):
            classes.append((prio, meta, obj))    # class(config, step_emitter, delta_emitter)

    # sort by priority desc, then by name to stabilize
    factories.sort(key=lambda t: (-t[0], getattr(t[2], "__name__", "")))
    classes.sort(key=lambda t: (-t[0], getattr(t[2], "__name__", "")))
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

    return chosen

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
# Declarative integration discovery
# --------------------------------------------------------------------------------------

def _iter_bundle_callable_members(target: Any):
    cls = target if isinstance(target, type) else target.__class__
    for name, member in inspect.getmembers(cls, predicate=callable):
        if name.startswith("__"):
            continue
        try:
            fn = inspect.unwrap(member)
        except Exception:
            fn = member
        yield name, fn


def discover_bundle_interface_manifest(target: Any, *, bundle_id: str | None = None) -> BundleInterfaceManifest:
    cls = target if isinstance(target, type) else target.__class__
    resolved_bundle_id = str(bundle_id or getattr(cls, "BUNDLE_ID", None) or getattr(target, "BUNDLE_ID", None) or "").strip()

    api_endpoints: list[APIEndpointSpec] = []
    ui_widgets: list[UIWidgetSpec] = []
    ui_main_spec: UIMainSpec | None = None
    on_message_spec: OnMessageSpec | None = None
    seen_api: set[tuple[str, str]] = set()
    seen_widgets: set[str] = set()

    for member_name, fn in _iter_bundle_callable_members(target):
        api_spec = getattr(fn, API_METHOD_ATTR, None)
        if isinstance(api_spec, APIEndpointSpec):
            resolved = APIEndpointSpec(
                method_name=member_name,
                alias=api_spec.alias,
                http_method=api_spec.http_method,
                roles=tuple(api_spec.roles or ()),
            )
            api_key = (resolved.alias, resolved.http_method)
            if api_key in seen_api:
                raise ValueError(f"Duplicate bundle api alias detected: {resolved.alias} [{resolved.http_method}]")
            seen_api.add(api_key)
            api_endpoints.append(resolved)

        widget_spec = getattr(fn, UI_WIDGET_ATTR, None)
        if isinstance(widget_spec, UIWidgetSpec):
            resolved = UIWidgetSpec(
                method_name=member_name,
                alias=widget_spec.alias,
                icon=dict(widget_spec.icon or {}),
                roles=tuple(widget_spec.roles or ()),
            )
            if resolved.alias in seen_widgets:
                raise ValueError(f"Duplicate bundle widget alias detected: {resolved.alias}")
            seen_widgets.add(resolved.alias)
            ui_widgets.append(resolved)

        current_ui_main = getattr(fn, UI_MAIN_ATTR, None)
        if isinstance(current_ui_main, UIMainSpec):
            resolved = UIMainSpec(method_name=member_name)
            if ui_main_spec and ui_main_spec.method_name != resolved.method_name:
                raise ValueError("Multiple @ui_main methods detected on bundle entrypoint")
            ui_main_spec = resolved

        current_on_message = getattr(fn, ON_MESSAGE_ATTR, None)
        if isinstance(current_on_message, OnMessageSpec):
            resolved = OnMessageSpec(method_name=member_name)
            if on_message_spec and on_message_spec.method_name != resolved.method_name:
                raise ValueError("Multiple @on_message methods detected on bundle entrypoint")
            on_message_spec = resolved

    api_endpoints.sort(key=lambda item: (item.alias, item.http_method, item.method_name))
    ui_widgets.sort(key=lambda item: (item.alias, item.method_name))
    return BundleInterfaceManifest(
        bundle_id=resolved_bundle_id,
        ui_widgets=tuple(ui_widgets),
        api_endpoints=tuple(api_endpoints),
        ui_main=ui_main_spec,
        on_message=on_message_spec,
    )


def resolve_bundle_api_endpoint(
        target: Any,
        *,
        alias: str,
        http_method: str = "POST",
        bundle_id: str | None = None,
) -> tuple[APIEndpointSpec | None, tuple[str, ...]]:
    manifest = discover_bundle_interface_manifest(target, bundle_id=bundle_id)
    resolved_method = _normalize_http_method(http_method)
    candidates = [spec for spec in manifest.api_endpoints if spec.alias == alias]
    if candidates:
        for spec in candidates:
            if spec.http_method == resolved_method:
                return spec, tuple(sorted({item.http_method for item in candidates}))
        return None, tuple(sorted({item.http_method for item in candidates}))

    if hasattr(target, alias) and callable(getattr(target, alias)):
        return APIEndpointSpec(
            method_name=alias,
            alias=alias,
            http_method=resolved_method,
            roles=(),
        ), ()
    return None, ()


def resolve_bundle_widget(
        target: Any,
        *,
        alias: str,
        bundle_id: str | None = None,
) -> UIWidgetSpec | None:
    manifest = discover_bundle_interface_manifest(target, bundle_id=bundle_id)
    for spec in manifest.ui_widgets:
        if spec.alias == alias:
            return spec
    return None


def resolve_bundle_message_method(
        target: Any,
        *,
        bundle_id: str | None = None,
) -> OnMessageSpec | None:
    return discover_bundle_interface_manifest(target, bundle_id=bundle_id).on_message


# --------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------

def get_workflow_instance(
        spec: AgenticBundleSpec,
        config: Any,
        *,
        comm_context: ChatTaskPayload,        # ← optional unified communicator
        pg_pool: Optional[Any] = None,             # ← optional DB pools
        redis: Optional[Any] = None,               # ← optional DB pools
) -> Tuple[Any, types.ModuleType]:
    """
    Load the bundle at 'spec', discover decorated symbols, instantiate a workflow,
    and return (workflow_instance, module).

    Notes:
    - ONLY decorated @agentic_workflow_factory / @agentic_workflow are recognized.
    - If both exist, the higher 'priority' wins (tie → factory wins).
    - Singleton is honored if:
        * spec.singleton is True, OR
        * the chosen factory has decorator meta singleton=True
    """
    key = _cache_key(spec)

    # Track active bundle references (best-effort, non-blocking)
    try:
        from kdcube_ai_app.infra.plugin.bundle_refs import touch_bundle_ref_best_effort

        if spec.path:
            t, p = _tp_from_ctx(comm_context)
            touch_bundle_ref_best_effort(redis, path=spec.path, tenant=t, project=p)
    except Exception:
        pass
    # singleton cache hit?
    if spec.singleton and key in _singleton_cache:
        inst, mod = _singleton_cache[key]
        try:
            rebind = getattr(inst, "rebind_request_context", None)
            if callable(rebind):
                rebind(comm_context=comm_context, pg_pool=pg_pool, redis=redis)
        except Exception:
            pass
        return inst, mod

    mod = _resolve_module(spec)
    chosen = _discover_decorated(mod)

    if not chosen:
        raise AttributeError(
            f"No decorated workflow found in module '{mod.__name__}'. "
            f"Use @agentic_workflow_factory or @agentic_workflow."
        )

    chosen_kind, meta, symbol = chosen

    # instantiate
    extra_kwargs = {
        "comm_context": comm_context,
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

    # Optional bundle on-load hook (runs once per spec/tenant/project)
    _maybe_run_bundle_on_load(
        instance=instance,
        mod=mod,
        spec=spec,
        config=config,
        comm_context=comm_context,
        pg_pool=pg_pool,
        redis=redis,
    )

    if final_singleton:
        _singleton_cache[key] = (instance, mod)

    return instance, mod

class _StartupCommContext:
    """
    Minimal comm_context stub for startup bundle preloading.
    """
    class _Actor:
        def __init__(self, tenant: str, project: str) -> None:
            self.tenant_id = tenant
            self.project_id = project

    def __init__(self, tenant: str, project: str) -> None:
        self.actor = self._Actor(tenant, project)


async def preload_bundle_async(
    spec: AgenticBundleSpec,
    bundle_spec: Any,
    *,
    tenant: str,
    project: str,
    pg_pool: Optional[Any] = None,
    redis: Optional[Any] = None,
) -> None:
    """
    Eagerly load a bundle module and run its on_bundle_load hook (if any).
    Called at proc startup when BUNDLES_PRELOAD_ON_START=1.

    Builds a minimal startup comm_context. Read from settings.TENANT /
    settings.PROJECT. This ensures _bundle_load_key() produces the same
    key that real requests will use, so the hook is not re-run on first
    actual request.
    """
    from kdcube_ai_app.infra.service_hub.inventory import ConfigRequest, create_workflow_config

    comm_ctx = _StartupCommContext(tenant, project)
    wf_config = create_workflow_config(ConfigRequest())
    wf_config.ai_bundle_spec = bundle_spec

    await asyncio.to_thread(
        get_workflow_instance,
        spec,
        wf_config,
        comm_context=comm_ctx,
        pg_pool=pg_pool,
        redis=redis,
    )


def clear_agentic_caches() -> None:
    """Utility for tests/dev hot-reload."""
    _module_cache.clear()
    _singleton_cache.clear()

def evict_inactive_specs(
        *,
        active_specs: List[AgenticBundleSpec],
        drop_sys_modules: bool = True,
) -> Dict[str, int]:
    """
    Evict cached modules/singletons not present in active_specs.
    Optionally remove module entries from sys.modules (best-effort).
    """
    active_keys = {cache_key_for_spec(s) for s in (active_specs or [])}
    evicted_modules = 0
    evicted_singletons = 0
    sys_modules_deleted = 0

    for key in list(_singleton_cache.keys()):
        if key not in active_keys:
            _singleton_cache.pop(key, None)
            evicted_singletons += 1

    for key, mod in list(_module_cache.items()):
        if key in active_keys:
            continue
        _module_cache.pop(key, None)
        evicted_modules += 1
        if drop_sys_modules and mod is not None:
            mod_name = getattr(mod, "__name__", None)
            mod_file = getattr(mod, "__file__", None) or ""
            mod_path_key = key.split("::")[0]
            if mod_name and (
                mod_name.startswith("agentic_bundle_")
                or (mod_file and mod_path_key and str(mod_file).find(str(Path(mod_path_key))) >= 0)
            ):
                if sys.modules.pop(mod_name, None) is not None:
                    sys_modules_deleted += 1

    if drop_sys_modules:
        importlib.invalidate_caches()

    return {
        "evicted_modules": evicted_modules,
        "evicted_singletons": evicted_singletons,
        "sys_modules_deleted": sys_modules_deleted,
    }

def evict_spec(spec: AgenticBundleSpec, *, drop_sys_modules: bool = True) -> bool:
    """
    Evict a single spec from module/singleton caches.
    Returns True if a cached module was removed.
    """
    key = cache_key_for_spec(spec)
    removed = False
    mod = _module_cache.pop(key, None)
    if mod is not None:
        removed = True
        if drop_sys_modules:
            mod_name = getattr(mod, "__name__", None)
            mod_file = getattr(mod, "__file__", None) or ""
            mod_path_key = key.split("::")[0]
            if mod_name and (
                mod_name.startswith("agentic_bundle_")
                or (mod_file and mod_path_key and str(mod_file).find(str(Path(mod_path_key))) >= 0)
            ):
                sys.modules.pop(mod_name, None)
    _singleton_cache.pop(key, None)
    if drop_sys_modules:
        importlib.invalidate_caches()
    return removed
