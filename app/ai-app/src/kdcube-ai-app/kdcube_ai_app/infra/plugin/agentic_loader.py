# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/plugin/agentic_loader.py
from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import sys
import inspect
import types
import asyncio
import functools
import hashlib
import json
import pickle
import shutil
import subprocess
import threading
import traceback
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple, Any, Dict, List

from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.infra.service_hub.inventory import AgentLogger
_log = logging.getLogger("kdcube.plugin.loader")

# --------------------------------------------------------------------------------------
# Public decorators — the ONLY way to mark workflow factory/class and optional init
# --------------------------------------------------------------------------------------

AGENTIC_ROLE_ATTR = "__agentic_role__"
AGENTIC_META_ATTR = "__agentic_meta__"
BUNDLE_ID_ATTR = "__bundle_id__"
API_METHOD_ATTR = "__bundle_api_method__"
UI_WIDGET_ATTR = "__bundle_ui_widget__"
ON_MESSAGE_ATTR = "__bundle_on_message__"
UI_MAIN_ATTR = "__bundle_ui_main__"
CRON_JOB_ATTR = "__bundle_cron_job__"
BUNDLE_VENV_ATTR = "__bundle_venv__"
_BUNDLE_VENV_EXEC_ENV = "KDCUBE_BUNDLE_VENV_EXEC"
_BUNDLE_VENV_STAMP_FILE = ".kdcube_venv_stamp.json"
_BUNDLE_VENV_RUNTIME_PTH = "kdcube_runtime_overlay.pth"
_bundle_venv_locks: Dict[str, threading.Lock] = {}
_bundle_venv_locks_guard = threading.Lock()
_bundle_declared_id_cache: Dict[str, str] = {}
_bundle_declared_id_lock = threading.Lock()
_BUNDLE_VENV_STRIP_ENV_PREFIXES = ("PYCHARM", "PYDEVD", "IDE_")
_BUNDLE_VENV_STRIP_ENV_KEYS = {
    "VIRTUAL_ENV",
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "PYTHONINSPECT",
    "PYTHONBREAKPOINT",
    "__PYVENV_LAUNCHER__",
}


@dataclass(frozen=True)
class APIEndpointSpec:
    method_name: str
    alias: str
    http_method: str = "POST"
    route: str = "operations"
    roles: tuple[str, ...] = ()
    public_auth: "PublicAPIAuthSpec | None" = None


@dataclass(frozen=True)
class PublicAPIAuthSpec:
    mode: str = "none"
    header: str | None = None
    secret_key: str | None = None


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
class CronJobSpec:
    method_name: str
    alias: str = ""
    cron_expression: str | None = None
    expr_config: str | None = None
    span: str = "system"


@dataclass(frozen=True)
class BundleInterfaceManifest:
    bundle_id: str
    allowed_roles: tuple[str, ...] = ()
    ui_widgets: tuple[UIWidgetSpec, ...] = ()
    api_endpoints: tuple[APIEndpointSpec, ...] = ()
    ui_main: UIMainSpec | None = None
    on_message: OnMessageSpec | None = None
    scheduled_jobs: tuple[CronJobSpec, ...] = ()


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


def _normalize_api_route(value: str | None) -> str:
    route = str(value or "operations").strip().lower() or "operations"
    if route not in {"operations", "public"}:
        raise ValueError(f"Unsupported bundle api route: {route}")
    return route


def _normalize_public_api_auth(route: str, value: Any) -> PublicAPIAuthSpec | None:
    resolved_route = _normalize_api_route(route)
    if resolved_route != "public":
        if value is None:
            return None
        raise ValueError("Bundle api public_auth is only supported for route='public'")

    if value is None:
        return None
    if isinstance(value, str):
        mode = str(value).strip().lower()
        if mode == "none":
            return PublicAPIAuthSpec(mode="none")
        raise ValueError(f"Unsupported public bundle api auth mode: {value}")
    if not isinstance(value, dict):
        raise ValueError("Bundle api public_auth must be a string or dict")

    mode = str(value.get("mode") or "header_secret").strip().lower()
    if mode == "none":
        return PublicAPIAuthSpec(mode="none")
    if mode != "header_secret":
        raise ValueError(f"Unsupported public bundle api auth mode: {mode}")

    header = str(value.get("header") or "X-KDCUBE-Public-Secret").strip()
    if not header:
        raise ValueError("Bundle api public_auth.header must be a non-empty HTTP header name")
    secret_key = str(value.get("secret_key") or "").strip()
    if not secret_key:
        raise ValueError("Bundle api public_auth.secret_key is required for mode='header_secret'")
    return PublicAPIAuthSpec(mode="header_secret", header=header, secret_key=secret_key)


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
        allowed_roles: List[str] | Tuple[str, ...] | None = None,
):
    """
    Mark a CLASS as the bundle's workflow CLASS.
    Recommended signature (flexible):
        class(config, *, communicator=None, step_emitter=None, delta_emitter=None)
    Only the kwargs present in the __init__ signature will be passed.

    allowed_roles: optional list of non-derived (external) role names such as
        "kdcube:role:<custom-role>" that restrict bundle visibility in the
        bundle listing. Only users whose raw roles (kdcube:role:* entries from
        the session) intersect with allowed_roles will see this bundle.
        Empty or None means visible to all authenticated users.
    """
    def _wrap(cls):
        setattr(cls, AGENTIC_ROLE_ATTR, "workflow_class")
        setattr(cls, AGENTIC_META_ATTR, {
            "name": name, "version": version, "priority": priority,
            "allowed_roles": _tuple_str(allowed_roles),
        })
        return cls
    return _wrap


def bundle_id(id: str):
    """
    Declare the bundle's ID from within the class definition.

    Usage::

        @agentic_workflow(name="My Bundle")
        @bundle_id("my-bundle@1.0.0")
        class MyBundle:
            ...

    The decorated value is stored as ``cls.__bundle_id__`` and is picked up
    by ``discover_bundle_interface_manifest`` when no external bundle_id is
    supplied by the caller.  An explicit ``bundle_id`` passed to the
    discovery function always takes precedence.
    """
    def _wrap(cls):
        setattr(cls, BUNDLE_ID_ATTR, str(id).strip())
        return cls
    return _wrap


def api(
        *,
        method: str = "POST",
        alias: str | None = None,
        route: str = "operations",
        roles: List[str] | Tuple[str, ...] | None = None,
        public_auth: str | Dict[str, Any] | None = None,
):
    http_method = _normalize_http_method(method)
    resolved_route = _normalize_api_route(route)
    resolved_public_auth = _normalize_public_api_auth(resolved_route, public_auth)

    def _wrap(fn):
        setattr(
            fn,
            API_METHOD_ATTR,
            APIEndpointSpec(
                method_name=getattr(fn, "__name__", "api_method"),
                alias=_clean_alias(alias, getattr(fn, "__name__", "api_method")),
                http_method=http_method,
                route=resolved_route,
                roles=_tuple_str(roles),
                public_auth=resolved_public_auth,
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


def venv(
        *,
        requirements: str = "requirements.txt",
        python: str | None = None,
        timeout_seconds: int | None = None,
):
    """Run the decorated callable inside a cached per-bundle venv subprocess."""

    meta = {
        "requirements": str(requirements or "requirements.txt").strip() or "requirements.txt",
        "python": str(python).strip() if python else None,
        "timeout_seconds": int(timeout_seconds) if timeout_seconds is not None else None,
    }

    def _wrap(fn):
        is_async = asyncio.iscoroutinefunction(fn)

        if is_async:
            @functools.wraps(fn)
            async def _async_wrapper(*args, **kwargs):
                if os.environ.get(_BUNDLE_VENV_EXEC_ENV) == "1":
                    return await fn(*args, **kwargs)
                return await asyncio.to_thread(_execute_in_bundle_venv, fn, meta, args, kwargs)

            setattr(_async_wrapper, BUNDLE_VENV_ATTR, meta)
            return _async_wrapper

        @functools.wraps(fn)
        def _sync_wrapper(*args, **kwargs):
            if os.environ.get(_BUNDLE_VENV_EXEC_ENV) == "1":
                return fn(*args, **kwargs)
            return _execute_in_bundle_venv(fn, meta, args, kwargs)

        setattr(_sync_wrapper, BUNDLE_VENV_ATTR, meta)
        return _sync_wrapper

    return _wrap


_VALID_CRON_SPANS = frozenset({"process", "instance", "system"})


def cron(
        *,
        alias: str | None = None,
        cron_expression: str | None = None,
        expr_config: str | None = None,
        span: str = "system",
):
    """
    Mark a bundle method as a scheduled job.

    Args:
        alias: human-readable job name used as a stable identifier. Defaults
            to the method name if not provided.
        cron_expression: inline cron expression, e.g. ``"*/15 * * * *"``.
        expr_config: dot-separated path into bundle props/config, e.g.
            ``"apps.app1.routines.cron"``. If provided, takes precedence over
            ``cron_expression`` at runtime. If the resolved value is missing,
            blank, or ``"disable"``, the job is not scheduled.
        span: exclusivity level — one of ``"process"``, ``"instance"``,
            ``"system"``. Defaults to ``"system"``.
    """
    span_norm = str(span).strip().lower() if span else "system"
    if span_norm not in _VALID_CRON_SPANS:
        raise ValueError(
            f"Invalid cron span: {span!r}. Must be one of: process, instance, system"
        )

    def _wrap(fn):
        method_name = getattr(fn, "__name__", "cron_job")
        setattr(
            fn,
            CRON_JOB_ATTR,
            CronJobSpec(
                method_name=method_name,
                alias=_clean_alias(alias, method_name),
                cron_expression=str(cron_expression).strip() if cron_expression is not None else None,
                expr_config=str(expr_config).strip() if expr_config is not None else None,
                span=span_norm,
            ),
        )
        return fn

    return _wrap


def _sanitize_bundle_segment(raw: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "-" for ch in (raw or ""))
    safe = safe.strip("-_")
    return safe or "default"


def _resolve_bundle_root_for_callable(fn: Callable[..., Any]) -> Path:
    file_path = Path(inspect.getsourcefile(fn) or inspect.getfile(fn)).resolve()
    search_dir = file_path.parent
    for candidate in (search_dir, *search_dir.parents):
        if (candidate / "entrypoint.py").exists():
            return candidate.resolve()
    return search_dir.resolve()


def _resolve_bundle_module_name(bundle_root: Path, fn: Callable[..., Any]) -> str:
    file_path = Path(inspect.getsourcefile(fn) or inspect.getfile(fn)).resolve()
    rel = file_path.relative_to(bundle_root.resolve())
    if rel.name == "__init__.py":
        parts = rel.parts[:-1]
    else:
        parts = rel.with_suffix("").parts
    if not parts:
        raise ValueError(f"Cannot derive bundle module name for {file_path}")
    return ".".join(parts)


def _resolve_bundle_id_for_root(bundle_root: Path) -> str:
    key = str(bundle_root.resolve())
    with _bundle_declared_id_lock:
        cached = _bundle_declared_id_cache.get(key)
        if cached:
            return cached
    declared = get_declared_bundle_id(bundle_root, "entrypoint") or bundle_root.name
    resolved = str(declared or bundle_root.name).strip() or bundle_root.name
    with _bundle_declared_id_lock:
        _bundle_declared_id_cache[key] = resolved
    return resolved


def _bundle_venv_dir(bundle_id: str) -> Path:
    from kdcube_ai_app.infra.plugin.bundle_storage import resolve_bundle_storage_root

    return (resolve_bundle_storage_root() / "_bundle_venvs" / _sanitize_bundle_segment(bundle_id)).resolve()


def _bundle_venv_python_path(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _bundle_venv_stamp_path(venv_dir: Path) -> Path:
    return venv_dir / _BUNDLE_VENV_STAMP_FILE


def _bundle_requirements_hash(requirements_path: Path) -> str:
    if not requirements_path.exists():
        return "missing"
    return hashlib.sha256(requirements_path.read_bytes()).hexdigest()


def _read_bundle_venv_stamp(venv_dir: Path) -> dict[str, Any]:
    stamp_path = _bundle_venv_stamp_path(venv_dir)
    if not stamp_path.exists():
        return {}
    try:
        return json.loads(stamp_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_bundle_venv_stamp(
    *,
    venv_dir: Path,
    bundle_id: str,
    requirements_path: Path,
    requirements_hash: str,
    base_python: str,
) -> None:
    stamp = {
        "bundle_id": bundle_id,
        "requirements_path": str(requirements_path),
        "requirements_hash": requirements_hash,
        "base_python": base_python,
        "built_at": int(time.time()),
        "build_id": time.time_ns(),
    }
    _bundle_venv_stamp_path(venv_dir).write_text(json.dumps(stamp, indent=2, sort_keys=True), encoding="utf-8")


def _current_runtime_site_paths() -> list[str]:
    try:
        import site as _site
    except Exception:
        return []
    paths: list[str] = []
    for raw in list(_site.getsitepackages()) + [_site.getusersitepackages()]:
        text = str(raw or "").strip()
        if not text:
            continue
        candidate = Path(text).expanduser()
        if candidate.exists():
            paths.append(str(candidate.resolve()))
    deduped: list[str] = []
    seen: set[str] = set()
    for item in paths:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _runtime_support_pythonpath() -> str:
    excluded = set(_current_runtime_site_paths())
    preferred_roots: list[str] = []
    package_root = str(Path(__file__).resolve().parents[3])
    preferred_roots.append(package_root)
    raw_env = os.environ.get("PYTHONPATH") or ""
    if raw_env:
        preferred_roots.extend([item for item in raw_env.split(os.pathsep) if item])

    deduped: list[str] = []
    seen: set[str] = set()
    for raw in preferred_roots:
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            resolved = str(Path(text).expanduser().resolve())
        except Exception:
            resolved = text
        if resolved in excluded or resolved in seen:
            continue
        if resolved and Path(resolved).exists():
            seen.add(resolved)
            deduped.append(resolved)
    return os.pathsep.join(deduped)


def _bundle_venv_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    pythonpath = _runtime_support_pythonpath()
    if pythonpath:
        env["PYTHONPATH"] = pythonpath
    env.pop(_BUNDLE_VENV_EXEC_ENV, None)
    return env


def _bundle_venv_build_env() -> dict[str, str]:
    """
    Environment for creating the bundle venv itself.

    Strip nested-venv and IDE/debugger variables so `python -m venv` and
    ensurepip do not inherit a contaminated launch environment from proc.
    """
    env = os.environ.copy()
    for key in list(env.keys()):
        if key in _BUNDLE_VENV_STRIP_ENV_KEYS or key.startswith(_BUNDLE_VENV_STRIP_ENV_PREFIXES):
            env.pop(key, None)
    env.pop(_BUNDLE_VENV_EXEC_ENV, None)
    return env


def _bundle_venv_base_python(meta: dict[str, Any]) -> str:
    requested = str(meta.get("python") or "").strip()
    if requested:
        return requested
    base = str(getattr(sys, "_base_executable", "") or "").strip()
    if base:
        return base
    return sys.executable


def _query_site_packages_for_python(python_executable: Path) -> list[Path]:
    proc = subprocess.run(
        [str(python_executable), "-c", "import json, site; print(json.dumps(site.getsitepackages()))"],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(proc.stdout.strip() or "[]")
    return [Path(item).expanduser().resolve() for item in data if item]


def _write_runtime_overlay_pth(venv_python: Path) -> None:
    site_packages = _query_site_packages_for_python(venv_python)
    if not site_packages:
        return
    runtime_paths = _current_runtime_site_paths()
    content = "\n".join(runtime_paths) + ("\n" if runtime_paths else "")
    for site_dir in site_packages:
        site_dir.mkdir(parents=True, exist_ok=True)
        (site_dir / _BUNDLE_VENV_RUNTIME_PTH).write_text(content, encoding="utf-8")


def _primary_site_packages_for_venv(venv_python: Path) -> Path:
    site_packages = _query_site_packages_for_python(venv_python)
    if not site_packages:
        raise RuntimeError(f"Cannot resolve site-packages for bundle venv python: {venv_python}")
    return site_packages[0]


def _bundle_venv_lock(lock_key: str) -> threading.Lock:
    with _bundle_venv_locks_guard:
        lock = _bundle_venv_locks.get(lock_key)
        if lock is None:
            lock = threading.Lock()
            _bundle_venv_locks[lock_key] = lock
        return lock


def _run_checked_subprocess(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
    label: str,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        details = stderr or stdout or f"exit code {proc.returncode}"
        _log.error(
            "[bundle.venv] subprocess failed: label=%s returncode=%s cmd=%r\nstdout:\n%s\nstderr:\n%s",
            label,
            proc.returncode,
            cmd,
            stdout or "<empty>",
            stderr or "<empty>",
        )
        raise RuntimeError(f"{label} failed: {details}")
    return proc


def _build_bundle_venv(
    *,
    venv_dir: Path,
    bundle_id: str,
    base_python: str,
    requirements_path: Path,
    requirements_hash: str,
) -> None:
    temp_dir = venv_dir.with_name(f"{venv_dir.name}.tmp-{os.getpid()}-{time.time_ns()}")
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)
    try:
        _run_checked_subprocess(
            [base_python, "-m", "venv", "--without-pip", str(temp_dir)],
            env=_bundle_venv_build_env(),
            label=f"create bundle venv for {bundle_id}",
        )
        venv_python = _bundle_venv_python_path(temp_dir)
        _write_runtime_overlay_pth(venv_python)
        requirements_text = requirements_path.read_text(encoding="utf-8").strip() if requirements_path.exists() else ""
        if requirements_text:
            target_site = _primary_site_packages_for_venv(venv_python)
            _run_checked_subprocess(
                [
                    base_python,
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "--upgrade",
                    "--target",
                    str(target_site),
                    "-r",
                    str(requirements_path),
                ],
                env=_bundle_venv_build_env(),
                label=f"install bundle requirements for {bundle_id}",
            )
        if venv_dir.exists():
            shutil.rmtree(venv_dir, ignore_errors=True)
        temp_dir.replace(venv_dir)
        _write_bundle_venv_stamp(
            venv_dir=venv_dir,
            bundle_id=bundle_id,
            requirements_path=requirements_path,
            requirements_hash=requirements_hash,
            base_python=base_python,
        )
    except Exception:
        _log.exception("[bundle.venv] failed to build cached venv for bundle=%s path=%s", bundle_id, temp_dir)
        raise
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


def _ensure_bundle_venv(
    *,
    bundle_id: str,
    requirements_path: Path,
    meta: dict[str, Any],
) -> Path:
    venv_dir = _bundle_venv_dir(bundle_id)
    requirements_hash = _bundle_requirements_hash(requirements_path)
    base_python = _bundle_venv_base_python(meta)
    lock = _bundle_venv_lock(str(venv_dir))
    with lock:
        stamp = _read_bundle_venv_stamp(venv_dir)
        venv_python = _bundle_venv_python_path(venv_dir)
        should_rebuild = (
            not venv_python.exists()
            or stamp.get("requirements_hash") != requirements_hash
        )
        if should_rebuild:
            venv_dir.parent.mkdir(parents=True, exist_ok=True)
            _build_bundle_venv(
                venv_dir=venv_dir,
                bundle_id=bundle_id,
                base_python=base_python,
                requirements_path=requirements_path,
                requirements_hash=requirements_hash,
            )
            venv_python = _bundle_venv_python_path(venv_dir)
        else:
            _write_runtime_overlay_pth(venv_python)
        return venv_python


def _resolve_bundle_callable_from_module(mod: types.ModuleType, qualname: str) -> Callable[..., Any]:
    if "<locals>" in qualname:
        raise ValueError(f"@venv does not support local nested callables: {qualname}")
    target: Any = mod
    for part in qualname.split("."):
        target = getattr(target, part)
    if not callable(target):
        raise TypeError(f"Resolved venv target is not callable: {qualname}")
    return target


def _resolve_bundle_callable(bundle_root: Path, module_name: str, qualname: str) -> Callable[..., Any]:
    mod = _load_module_from_dir(bundle_root, module_name)
    return _resolve_bundle_callable_from_module(mod, qualname)


def _bundle_venv_entry_main() -> int:
    if len(sys.argv) != 5:
        raise SystemExit("usage: python -c ... <request.json> <args.pkl> <kwargs.pkl> <response.pkl>")
    request_path = Path(sys.argv[1]).resolve()
    args_path = Path(sys.argv[2]).resolve()
    kwargs_path = Path(sys.argv[3]).resolve()
    response_path = Path(sys.argv[4]).resolve()
    os.environ[_BUNDLE_VENV_EXEC_ENV] = "1"
    try:
        payload = json.loads(request_path.read_text(encoding="utf-8"))
        bundle_root = Path(str(payload["bundle_root"])).resolve()
        module_name = str(payload["module_name"])
        module_root = str(payload.get("module_root") or "").strip() or None
        mod = (
            _load_module_from_dir_with_root(bundle_root, module_name, root_pkg=module_root)
            if module_root
            else _load_module_from_dir(bundle_root, module_name)
        )
        args = pickle.loads(args_path.read_bytes())
        kwargs = pickle.loads(kwargs_path.read_bytes())
        target = _resolve_bundle_callable_from_module(mod, str(payload["qualname"]))
        result = target(*args, **kwargs)
        if inspect.isawaitable(result):
            result = asyncio.run(result)
        envelope = {"ok": True, "result": result}
    except Exception as exc:
        envelope = {
            "ok": False,
            "error_type": exc.__class__.__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
    response_path.write_bytes(pickle.dumps(envelope, protocol=pickle.HIGHEST_PROTOCOL))
    return 0


def _execute_in_bundle_venv(
    fn: Callable[..., Any],
    meta: dict[str, Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    bundle_root = _resolve_bundle_root_for_callable(fn)
    module_name = _resolve_bundle_module_name(bundle_root, fn)
    bundle_id = _resolve_bundle_id_for_root(bundle_root)
    module_root = None
    fn_module = str(getattr(fn, "__module__", "") or "").strip()
    if fn_module.startswith("kdcube_bundle_"):
        module_root = fn_module.split(".", 1)[0]
    requirements_path = Path(str(meta.get("requirements") or "requirements.txt"))
    if not requirements_path.is_absolute():
        requirements_path = (bundle_root / requirements_path).resolve()
    timeout_seconds = meta.get("timeout_seconds")
    try:
        venv_python = _ensure_bundle_venv(
            bundle_id=bundle_id,
            requirements_path=requirements_path,
            meta=meta,
        )
    except Exception:
        _log.exception(
            "[bundle.venv] failed to prepare venv for bundle=%s callable=%s requirements=%s",
            bundle_id,
            fn.__qualname__,
            requirements_path,
        )
        raise
    try:
        args_payload = pickle.dumps(args, protocol=pickle.HIGHEST_PROTOCOL)
        kwargs_payload = pickle.dumps(kwargs, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as exc:
        raise TypeError(f"Arguments for @venv callable {fn.__qualname__} are not serializable: {exc}") from exc

    import tempfile

    with tempfile.TemporaryDirectory(prefix="bundle-venv-", dir=str(_bundle_venv_dir(bundle_id))) as temp_dir:
        temp_root = Path(temp_dir)
        request_path = temp_root / "request.json"
        args_path = temp_root / "args.pkl"
        kwargs_path = temp_root / "kwargs.pkl"
        response_path = temp_root / "response.pkl"
        request_path.write_text(
            json.dumps(
                {
                    "bundle_root": str(bundle_root),
                    "module_name": module_name,
                    "module_root": module_root,
                    "qualname": fn.__qualname__,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        args_path.write_bytes(args_payload)
        kwargs_path.write_bytes(kwargs_payload)
        env = _bundle_venv_subprocess_env()
        env[_BUNDLE_VENV_EXEC_ENV] = "1"
        cmd = [
            str(venv_python),
            "-c",
            "from kdcube_ai_app.infra.plugin.agentic_loader import _bundle_venv_entry_main as _m; raise SystemExit(_m())",
            str(request_path),
            str(args_path),
            str(kwargs_path),
            str(response_path),
        ]
        try:
            _run_checked_subprocess(
                cmd,
                env=env,
                timeout=int(timeout_seconds) if timeout_seconds is not None else None,
                label=f"run bundle venv callable {bundle_id}:{fn.__qualname__}",
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(
                f"Timed out running @venv callable {bundle_id}:{fn.__qualname__} after {timeout_seconds} seconds"
            ) from exc
        if not response_path.exists():
            raise RuntimeError(f"No response produced by @venv callable {bundle_id}:{fn.__qualname__}")
        response = pickle.loads(response_path.read_bytes())
    if response.get("ok"):
        return response.get("result")
    _log.error(
        "[bundle.venv] callable failed in child: bundle=%s qualname=%s error_type=%s message=%s\n%s",
        bundle_id,
        fn.__qualname__,
        response.get("error_type"),
        response.get("message"),
        (response.get("traceback") or "").rstrip() or "<no traceback>",
    )
    raise RuntimeError(
        f"@venv callable failed: {response.get('error_type')}: {response.get('message')}\n"
        f"{response.get('traceback') or ''}".rstrip()
    )


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
_manifest_cache: Dict[str, "BundleInterfaceManifest"] = {}
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

def _bundle_virtual_root_package(container_path: Path) -> str:
    digest = hashlib.sha256(str(container_path.resolve()).encode("utf-8")).hexdigest()[:16]
    return f"kdcube_bundle_{digest}"


def _load_module_from_dir_with_root(container_path: Path, module: str, *, root_pkg: str) -> types.ModuleType:
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
    existing = sys.modules.get(full_name)
    if isinstance(existing, types.ModuleType):
        return existing
    spec = importlib.util.spec_from_file_location(full_name, str(target))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create module spec from file: {target}")
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = f"{root_pkg}.{sub_pkg}" if sub_pkg else root_pkg
    sys.modules[full_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_module_from_dir(container_path: Path, module: str) -> types.ModuleType:
    return _load_module_from_dir_with_root(
        container_path,
        module,
        root_pkg=_bundle_virtual_root_package(container_path),
    )

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
        mod = importlib.import_module(module)
        # Guard against sys.modules cache returning a module from a *different* bundle.
        # Multiple bundles can share the same module name (e.g. "entrypoint"); the first
        # one wins in sys.modules and all subsequent bundles get the wrong module back.
        # Verify the loaded module actually lives inside container_path.
        mod_file = getattr(mod, "__file__", None) or getattr(mod, "__path__", [None])[0]
        if mod_file and not str(Path(mod_file).resolve()).startswith(root + "/"):
            _log.info(
                "[bundle.loader] sys.modules collision: module=%r cached from %r, expected inside %r — reloading via direct file",
                module, mod_file, root,
            )
            return _load_module_from_dir(container_path, module)
        return mod
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
    _bundle_attrs = (API_METHOD_ATTR, UI_WIDGET_ATTR, ON_MESSAGE_ATTR, UI_MAIN_ATTR, CRON_JOB_ATTR)
    for name, member in inspect.getmembers(cls, predicate=callable):
        if name.startswith("__"):
            continue
        # If the member itself carries a bundle decorator attribute, yield it directly.
        # This handles @api as the outermost decorator (attribute lives on the wrapper,
        # not on the unwrapped function that inspect.unwrap would reach).
        if any(hasattr(member, attr) for attr in _bundle_attrs):
            yield name, member
            continue
        # Otherwise unwrap to find @api applied as an inner decorator beneath functools.wraps.
        try:
            fn = inspect.unwrap(member)
        except Exception:
            fn = member
        yield name, fn


def discover_bundle_interface_manifest(target: Any, *, bundle_id: str | None = None) -> BundleInterfaceManifest:
    cls = target if isinstance(target, type) else target.__class__
    resolved_bundle_id = str(
        bundle_id
        or getattr(cls, BUNDLE_ID_ATTR, None)
        or getattr(target, BUNDLE_ID_ATTR, None)
        or getattr(cls, "BUNDLE_ID", None)
        or getattr(target, "BUNDLE_ID", None)
        or ""
    ).strip()

    api_endpoints: list[APIEndpointSpec] = []
    ui_widgets: list[UIWidgetSpec] = []
    ui_main_spec: UIMainSpec | None = None
    on_message_spec: OnMessageSpec | None = None
    scheduled_jobs: list[CronJobSpec] = []
    seen_api: set[tuple[str, str]] = set()
    seen_widgets: set[str] = set()

    for member_name, fn in _iter_bundle_callable_members(target):
        api_spec = getattr(fn, API_METHOD_ATTR, None)
        if isinstance(api_spec, APIEndpointSpec):
            resolved = APIEndpointSpec(
                method_name=member_name,
                alias=api_spec.alias,
                http_method=api_spec.http_method,
                route=api_spec.route,
                roles=tuple(api_spec.roles or ()),
                public_auth=api_spec.public_auth,
            )
            api_key = (resolved.alias, resolved.http_method, resolved.route)
            if api_key in seen_api:
                raise ValueError(
                    f"Duplicate bundle api alias detected: {resolved.alias} "
                    f"[{resolved.http_method}] route={resolved.route}"
                )
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

        cron_spec = getattr(fn, CRON_JOB_ATTR, None)
        if isinstance(cron_spec, CronJobSpec):
            scheduled_jobs.append(CronJobSpec(
                method_name=member_name,
                alias=cron_spec.alias or member_name,
                cron_expression=cron_spec.cron_expression,
                expr_config=cron_spec.expr_config,
                span=cron_spec.span,
            ))

    meta = getattr(cls, AGENTIC_META_ATTR, {}) or {}
    allowed_roles: tuple[str, ...] = _tuple_str(meta.get("allowed_roles"))

    api_endpoints.sort(key=lambda item: (item.alias, item.route, item.http_method, item.method_name))
    ui_widgets.sort(key=lambda item: (item.alias, item.method_name))
    scheduled_jobs.sort(key=lambda item: (item.alias, item.method_name))
    return BundleInterfaceManifest(
        bundle_id=resolved_bundle_id,
        allowed_roles=allowed_roles,
        ui_widgets=tuple(ui_widgets),
        api_endpoints=tuple(api_endpoints),
        ui_main=ui_main_spec,
        on_message=on_message_spec,
        scheduled_jobs=tuple(scheduled_jobs),
    )


def resolve_bundle_api_endpoint(
        target: Any,
        *,
        alias: str,
        http_method: str = "POST",
        route: str = "operations",
        bundle_id: str | None = None,
) -> tuple[APIEndpointSpec | None, tuple[str, ...]]:
    manifest = discover_bundle_interface_manifest(target, bundle_id=bundle_id)
    resolved_method = _normalize_http_method(http_method)
    resolved_route = _normalize_api_route(route)
    candidates = [spec for spec in manifest.api_endpoints if spec.alias == alias and spec.route == resolved_route]
    if candidates:
        for spec in candidates:
            if spec.http_method == resolved_method:
                return spec, tuple(sorted({item.http_method for item in candidates}))
        return None, tuple(sorted({item.http_method for item in candidates}))
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

    # Cache interface manifest (once per spec key — path::module)
    if key not in _manifest_cache:
        try:
            bundle_id_hint = getattr(getattr(config, "ai_bundle_spec", None), "id", None) or ""
            _manifest_cache[key] = discover_bundle_interface_manifest(
                instance, bundle_id=bundle_id_hint
            )
        except Exception:
            _log.warning("[manifest.cache] Failed to build manifest for key=%s", key, exc_info=True)

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
    _manifest_cache.clear()


def _module_matches_bundle_scope(mod: types.ModuleType, bundle_root: Path) -> bool:
    try:
        bundle_root = bundle_root.resolve()
    except Exception:
        bundle_root = Path(bundle_root)

    candidate_paths: list[Path] = []
    mod_file = getattr(mod, "__file__", None)
    if mod_file:
        candidate_paths.append(Path(mod_file))
    mod_path = getattr(mod, "__path__", None)
    if mod_path:
        try:
            iterator = iter(mod_path)
        except TypeError:
            iterator = ()
        for item in iterator:
            if item:
                candidate_paths.append(Path(item))

    for candidate in candidate_paths:
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        try:
            resolved.relative_to(bundle_root)
            return True
        except Exception:
            continue
    return False


def get_cached_manifest(spec: AgenticBundleSpec) -> "BundleInterfaceManifest | None":
    """Return the cached BundleInterfaceManifest for spec, or None if not yet loaded."""
    return _manifest_cache.get(_cache_key(spec))


def get_declared_bundle_id(path: "Path | str", module: str) -> "str | None":
    """
    Load the bundle module at ``path/module`` without instantiation and return
    the bundle ID declared via ``@bundle_id``, or ``None`` if the decorator is
    absent or the module cannot be loaded.
    """
    try:
        mod = _load_module_from_dir(Path(path), module)
        chosen = _discover_decorated(mod)
        if chosen:
            _, _, symbol = chosen
            declared = str(getattr(symbol, BUNDLE_ID_ATTR, None) or "").strip()
            return declared or None
    except Exception:
        return None
    return None


def load_bundle_manifest(
        spec: AgenticBundleSpec,
        *,
        bundle_id: str = "",
) -> "BundleInterfaceManifest":
    """
    Load (or reuse cached) module, discover the entrypoint class via
    @agentic_workflow, and return its BundleInterfaceManifest.

    Deliberately avoids instantiating the workflow so it never needs
    DB connections, LLM keys, or a comm_context.  Safe to call from
    listing endpoints on a cold cache.

    Result is stored in _manifest_cache under the same key used by
    get_workflow_instance, so a later real request won't re-discover.
    """
    key = _cache_key(spec)
    if key in _manifest_cache:
        return _manifest_cache[key]

    mod = _resolve_module(spec)
    chosen = _discover_decorated(mod)
    if not chosen:
        raise AttributeError(
            f"No @agentic_workflow class found in module '{mod.__name__}'"
        )
    _, _, symbol = chosen  # (kind, meta, class_or_factory)
    manifest = discover_bundle_interface_manifest(symbol, bundle_id=bundle_id)
    _manifest_cache[key] = manifest
    return manifest

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

    for key in list(_manifest_cache.keys()):
        if key not in active_keys:
            _manifest_cache.pop(key, None)

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


def evict_bundle_scope(spec: AgenticBundleSpec, *, drop_sys_modules: bool = True) -> Dict[str, int]:
    """
    Evict one bundle from loader caches and remove all sys.modules entries that
    belong to the same bundle directory or its virtual package namespace.

    This is intended for dev hot-reload of a single bundle without disturbing
    unrelated bundles.
    """
    key = cache_key_for_spec(spec)
    bundle_root = Path(spec.path).resolve()
    evicted_modules = 0
    evicted_singletons = 0
    evicted_manifests = 0
    sys_modules_deleted = 0

    mod = _module_cache.pop(key, None)
    if mod is not None:
        evicted_modules = 1

    if _singleton_cache.pop(key, None) is not None:
        evicted_singletons = 1

    if _manifest_cache.pop(key, None) is not None:
        evicted_manifests = 1

    if drop_sys_modules:
        virtual_roots: set[str] = set()
        if mod is not None:
            mod_name = getattr(mod, "__name__", None)
            if mod_name and mod_name.startswith("kdcube_bundle_"):
                virtual_roots.add(mod_name.split(".", 1)[0])

        to_delete: set[str] = set()
        for mod_name, candidate in list(sys.modules.items()):
            if candidate is None:
                continue
            if any(mod_name == root or mod_name.startswith(root + ".") for root in virtual_roots):
                to_delete.add(mod_name)
                continue
            if isinstance(candidate, types.ModuleType) and _module_matches_bundle_scope(candidate, bundle_root):
                to_delete.add(mod_name)

        for mod_name in to_delete:
            if sys.modules.pop(mod_name, None) is not None:
                sys_modules_deleted += 1

        importlib.invalidate_caches()

    return {
        "evicted_modules": evicted_modules,
        "evicted_singletons": evicted_singletons,
        "evicted_manifests": evicted_manifests,
        "sys_modules_deleted": sys_modules_deleted,
    }
