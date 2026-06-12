# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import importlib
import importlib.util
import inspect
import os
import pathlib
from collections.abc import Iterable as IterableABC
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, Callable, Iterable, Mapping, MutableMapping

from kdcube_ai_app.apps.chat.sdk.runtime.dynamic_module_loader import load_dynamic_module_for_path

from kdcube_ai_app.apps.chat.sdk.events.decorator import (
    ArtifactNamespaceRehosterDeclaration,
    EventSourceDeclaration,
    EventSourceReaderDeclaration,
    EventSourceResolverDeclaration,
    event_source_declaration,
    get_artifact_namespace_rehoster_declaration,
    get_event_source_declaration,
    get_event_source_reader_declaration,
    get_event_source_resolver_declaration,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.policies import (
    ReactEventPolicies,
    ReactEventPolicy,
    discover_react_event_policies,
    unknown_policy_paths,
)


@dataclass(frozen=True)
class ResolvedEventSource:
    event_source_id: str
    policies: tuple[dict[str, Any], ...]
    description: str = ""
    version: str = ""
    kind: str = ""
    reactive: bool | None = None
    iteration_credit: int | None = None
    module: str = ""
    alias: str = ""
    object_name: str = ""
    event_policies: dict[str, ReactEventPolicy] = field(default_factory=dict, repr=False, compare=False)

    @property
    def react(self) -> ReactEventPolicies:
        return ReactEventPolicies.from_specs(self.policies, registry=self.event_policies)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_source_id": self.event_source_id,
            "policies": self.policies,
            "description": self.description,
            "version": self.version,
            "kind": self.kind,
            "reactive": self.reactive,
            "iteration_credit": self.iteration_credit,
            "module": self.module,
            "alias": self.alias,
            "object_name": self.object_name,
        }


@dataclass(frozen=True)
class ResolvedArtifactNamespaceRehoster:
    namespace: str
    handler: Callable[..., Any] = field(compare=False)
    description: str = ""
    version: str = ""
    module: str = ""
    alias: str = ""
    object_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace,
            "description": self.description,
            "version": self.version,
            "module": self.module,
            "alias": self.alias,
            "object_name": self.object_name,
        }


@dataclass(frozen=True)
class ResolvedEventSourceReader:
    namespace: str
    event_source_id: str
    handler: Callable[..., Any] = field(compare=False)
    description: str = ""
    version: str = ""
    module: str = ""
    alias: str = ""
    object_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace,
            "event_source_id": self.event_source_id,
            "description": self.description,
            "version": self.version,
            "module": self.module,
            "alias": self.alias,
            "object_name": self.object_name,
        }


@dataclass(frozen=True)
class ResolvedEventSourceResolver:
    namespace: str
    handler: Callable[..., Any] = field(compare=False)
    description: str = ""
    version: str = ""
    module: str = ""
    alias: str = ""
    object_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace,
            "description": self.description,
            "version": self.version,
            "module": self.module,
            "alias": self.alias,
            "object_name": self.object_name,
        }


def _module_to_file(module_name: str) -> pathlib.Path:
    spec = importlib.util.find_spec(module_name)
    if not spec or not spec.origin:
        raise ValueError(f"Cannot locate module {module_name!r}")
    return pathlib.Path(spec.origin).resolve()


def resolve_event_source_specs(
    event_specs: Iterable[Mapping[str, Any]] | None,
    *,
    bundle_root: str | pathlib.Path | None = None,
) -> list[dict[str, Any]]:
    root = pathlib.Path(bundle_root).resolve() if bundle_root else None
    resolved: list[dict[str, Any]] = []
    for raw in event_specs or []:
        if not isinstance(raw, Mapping):
            continue
        alias = str(raw.get("alias") or "").strip() or None
        if raw.get("module"):
            resolved.append({
                "ref": str(_module_to_file(str(raw["module"]))),
                "alias": alias,
                "module": str(raw["module"]),
            })
            continue
        if raw.get("ref"):
            ref = pathlib.Path(str(raw["ref"]))
            if not ref.is_absolute():
                if root is None:
                    raise ValueError(f"Relative event source ref requires bundle_root: {ref}")
                ref = root / ref
            resolved.append({"ref": str(ref.resolve()), "alias": alias})
            continue
        raise ValueError(f"Event source spec must define 'module' or 'ref': {raw!r}")
    return resolved


class EventSourceSubsystem:
    """Discover and query declared event sources for tools and external events."""

    def __init__(
        self,
        *,
        modules: Iterable[Mapping[str, Any] | ModuleType] | None = None,
        event_specs: Iterable[Mapping[str, Any]] | None = None,
        bundle_root: str | pathlib.Path | None = None,
        logger: Any = None,
    ) -> None:
        self.log = logger
        self.bundle_root = pathlib.Path(bundle_root).resolve() if bundle_root else None
        self.warnings: list[str] = []
        self._by_event_source_id: dict[str, ResolvedEventSource] = {}
        self._namespace_rehosters: dict[str, ResolvedArtifactNamespaceRehoster] = {}
        self._event_source_readers: dict[str, ResolvedEventSourceReader] = {}
        self._event_source_resolvers: dict[str, ResolvedEventSourceResolver] = {}
        self._modules: list[dict[str, Any]] = []
        self._event_policies: dict[str, ReactEventPolicy] = {}

        for item in modules or []:
            self.add_module(item)

        for spec in resolve_event_source_specs(event_specs, bundle_root=self.bundle_root):
            mod_name, mod = self._load_module(spec["ref"])
            self.add_module({"name": mod_name, "mod": mod, "alias": spec.get("alias"), "file": spec.get("ref")})

    @classmethod
    def from_tool_subsystem(
        cls,
        tool_subsystem: Any,
        *,
        modules: Iterable[Mapping[str, Any] | ModuleType] | None = None,
        event_specs: Iterable[Mapping[str, Any]] | None = None,
        logger: Any = None,
    ) -> "EventSourceSubsystem":
        modules = list(modules or getattr(tool_subsystem, "_modules", None) or [])
        bundle_root = getattr(tool_subsystem, "bundle_root", None)
        return cls(modules=modules, event_specs=event_specs, bundle_root=bundle_root, logger=logger)

    def add_module(self, item: Mapping[str, Any] | ModuleType) -> None:
        if isinstance(item, ModuleType):
            mod = item
            module_info = {
                "name": mod.__name__,
                "mod": mod,
                "alias": None,
                "file": getattr(mod, "__file__", None),
            }
        else:
            mod = item.get("mod")
            if not isinstance(mod, ModuleType):
                ref = item.get("ref") or item.get("file") or item.get("module")
                if not ref:
                    raise ValueError(f"Event module entry must include mod or ref: {item!r}")
                mod_name, mod = self._load_module(str(ref))
            module_info = {
                "name": str(item.get("name") or getattr(mod, "__name__", "")),
                "mod": mod,
                "alias": item.get("alias"),
                "file": item.get("file") or getattr(mod, "__file__", None),
            }

        self._modules.append(module_info)
        self._event_policies.update(discover_react_event_policies(mod))
        for rehoster in self._discover_namespace_rehosters(module_info):
            self._register_namespace_rehoster(rehoster)
        for reader in self._discover_event_source_readers(module_info):
            self._register_event_source_reader(reader)
        for resolver in self._discover_event_source_resolvers(module_info):
            self._register_event_source_resolver(resolver)
        for source in self._discover_module(module_info):
            self._register(source)

    def by_event_source_id(self, event_source_id: str) -> ResolvedEventSource | None:
        return self._by_event_source_id.get(str(event_source_id or "").strip())

    def by_block(self, block: Mapping[str, Any] | None) -> ResolvedEventSource | None:
        if not isinstance(block, Mapping):
            return None
        event_source_id = str(block.get("event_source_id") or "").strip()
        if event_source_id:
            return self.by_event_source_id(event_source_id)
        return None

    def register_event_source(
        self,
        event_source_id: str,
        *,
        policies: Iterable[Mapping[str, Any]] | None = None,
        description: str = "",
        version: str = "",
        kind: str = "",
        reactive: bool | None = None,
        iteration_credit: int | None = None,
        module: str = "",
        alias: str = "",
        object_name: str = "",
        event_policies: Mapping[str, ReactEventPolicy] | None = None,
    ) -> None:
        source_id = str(event_source_id or "").strip()
        if not source_id:
            raise ValueError("event source id is required")
        self._register(
            ResolvedEventSource(
                event_source_id=source_id,
                policies=tuple(dict(policy) for policy in (policies or ()) if isinstance(policy, Mapping)),
                description=str(description or ""),
                version=str(version or ""),
                kind=str(kind or ""),
                reactive=reactive,
                iteration_credit=iteration_credit,
                module=str(module or ""),
                alias=str(alias or ""),
                object_name=str(object_name or ""),
                event_policies=dict(event_policies or self._event_policies),
            )
        )

    def should_merge_to_sources_pool(self, event_source_id: str) -> bool:
        source = self.by_event_source_id(event_source_id)
        return bool(
            source
            and any(
                binding.event_policy_id == "react.block_production.exploration_results"
                for binding in source.react.block_production
            )
        )

    def apply_react_phase_policies(
        self,
        react_phase: str,
        event_source_id: str,
        target: Any,
        **context: Any,
    ) -> Any:
        """Apply declared ReAct policies to a mutable block target.

        `react_phase` is deliberately limited to the extension seams that exist
        here. Callers choose the correct target from the existing ReAct path,
        such as raw source rows during production or a mutable timeline block
        list before rendering/pruning.
        """
        source = self.by_event_source_id(event_source_id)
        if not source:
            return target
        return source.react.apply_react_phase(react_phase, target, source=source, **context)

    async def apply_react_phase_policies_async(
        self,
        react_phase: str,
        event_source_id: str,
        target: Any,
        **context: Any,
    ) -> Any:
        """Apply declared ReAct policies and await async validation policies."""
        source = self.by_event_source_id(event_source_id)
        if not source:
            return target
        return await source.react.apply_react_phase_async(react_phase, target, source=source, **context)

    def list_sources(self) -> list[dict[str, Any]]:
        return [source.to_dict() for source in sorted(self._by_event_source_id.values(), key=lambda s: s.event_source_id)]

    def namespace_rehoster(self, namespace: str) -> ResolvedArtifactNamespaceRehoster | None:
        return self._namespace_rehosters.get(str(namespace or "").strip().rstrip(":"))

    def register_namespace_rehoster(
        self,
        namespace: str,
        handler: Callable[..., Any],
        *,
        description: str = "",
        version: str = "",
        module: str = "",
        alias: str = "",
        object_name: str = "",
    ) -> None:
        ns = str(namespace or "").strip().rstrip(":")
        if not ns:
            raise ValueError("namespace rehoster requires namespace")
        if not callable(handler):
            raise TypeError("namespace rehoster handler must be callable")
        self._register_namespace_rehoster(
            ResolvedArtifactNamespaceRehoster(
                namespace=ns,
                handler=handler,
                description=str(description or ""),
                version=str(version or ""),
                module=str(module or ""),
                alias=str(alias or ""),
                object_name=str(object_name or ""),
            )
        )

    def list_namespace_rehosters(self) -> list[dict[str, Any]]:
        return [
            rehoster.to_dict()
            for rehoster in sorted(self._namespace_rehosters.values(), key=lambda item: item.namespace)
        ]

    def event_source_reader(self, namespace: str) -> ResolvedEventSourceReader | None:
        return self._event_source_readers.get(str(namespace or "").strip().rstrip(":"))

    def event_source_reader_for_ref(self, ref: str) -> ResolvedEventSourceReader | None:
        namespace = str(ref or "").strip().partition(":")[0].strip()
        return self.event_source_reader(namespace)

    def register_event_source_resolver(
        self,
        namespace: str,
        handler: Callable[..., Any],
        *,
        description: str = "",
        version: str = "",
        module: str = "",
        alias: str = "",
        object_name: str = "",
    ) -> None:
        ns = str(namespace or "").strip().rstrip(":")
        if not ns:
            raise ValueError("event source resolver requires namespace")
        if not callable(handler):
            raise TypeError("event source resolver handler must be callable")
        self._register_event_source_resolver(
            ResolvedEventSourceResolver(
                namespace=ns,
                handler=handler,
                description=str(description or ""),
                version=str(version or ""),
                module=str(module or ""),
                alias=str(alias or ""),
                object_name=str(object_name or ""),
            )
        )

    def list_event_source_resolvers(self) -> list[dict[str, Any]]:
        return [
            resolver.to_dict()
            for resolver in sorted(self._event_source_resolvers.values(), key=lambda item: item.namespace)
        ]

    async def resolve_event_source_for_ref(
        self,
        ref: str,
        *,
        ctx_browser: Any = None,
        **context: Any,
    ) -> dict[str, Any]:
        """Resolve a canonical owner URI to the event source that should render it.

        This is a cheap routing lookup. It must not read or materialize the
        object. Namespace owners register a lightweight resolver function that
        maps ``uri -> {event_source_id, ...}``.
        """
        raw = str(ref or "").strip()
        if not raw:
            return {"ok": False, "ref": raw, "missing": True, "error": "empty_ref"}
        namespace, sep, key = raw.partition(":")
        namespace = namespace.strip().rstrip(":")
        if not sep or not namespace:
            return {"ok": False, "ref": raw, "missing": True, "error": "missing_namespace"}
        resolver = self._event_source_resolvers.get(namespace)
        if resolver is None:
            return {"ok": False, "ref": raw, "missing": True, "error": "no_event_source_resolver"}
        try:
            result = resolver.handler(
                ref=raw,
                namespace=namespace,
                key=key,
                ctx_browser=ctx_browser,
                **context,
            )
            if inspect.isawaitable(result):
                result = await result
            payload = dict(result) if isinstance(result, Mapping) else {"value": result}
        except Exception as exc:
            return {
                "ok": False,
                "ref": raw,
                "namespace": namespace,
                "error": "event_source_resolver_failed",
                "message": str(exc),
            }
        event_source_id = str(payload.get("event_source_id") or "").strip()
        if not event_source_id:
            return {
                "ok": False,
                "ref": raw,
                "namespace": namespace,
                "missing": True,
                "error": "event_source_resolver_declined",
            }
        payload.setdefault("ok", True)
        payload.setdefault("ref", raw)
        payload.setdefault("object_ref", raw)
        payload.setdefault("namespace", namespace)
        payload.setdefault("event_source_id", event_source_id)
        return payload

    async def resolve_event_source_id_for_ref(
        self,
        ref: str,
        *,
        ctx_browser: Any = None,
        **context: Any,
    ) -> str:
        resolved = await self.resolve_event_source_for_ref(ref, ctx_browser=ctx_browser, **context)
        if not isinstance(resolved, Mapping) or not resolved.get("ok"):
            return ""
        return str(resolved.get("event_source_id") or "").strip()

    def list_event_source_readers(self) -> list[dict[str, Any]]:
        return [
            reader.to_dict()
            for reader in sorted(self._event_source_readers.values(), key=lambda item: item.namespace)
        ]

    async def read_event_source_ref(
        self,
        ref: str,
        *,
        ctx_browser: Any = None,
        **context: Any,
    ) -> dict[str, Any]:
        raw = str(ref or "").strip()
        namespace, sep, key = raw.partition(":")
        namespace = namespace.strip()
        if not sep or not namespace:
            return {"ok": False, "ref": raw, "missing": True, "error": "missing_namespace"}
        reader = self.event_source_reader(namespace)
        if reader is None:
            return {"ok": False, "ref": raw, "missing": True, "error": "no_event_source_reader"}
        try:
            result = reader.handler(
                ref=raw,
                namespace=namespace,
                key=key,
                ctx_browser=ctx_browser,
                **context,
            )
            if inspect.isawaitable(result):
                result = await result
            payload = dict(result) if isinstance(result, Mapping) else {"value": result}
            payload.setdefault("ok", True)
            payload.setdefault("ref", raw)
            payload.setdefault("object_ref", raw)
            payload.setdefault("event_source_id", reader.event_source_id)
            return payload
        except Exception as exc:
            return {
                "ok": False,
                "ref": raw,
                "event_source_id": reader.event_source_id,
                "error": "event_source_reader_failed",
                "message": str(exc),
            }

    async def rehost_namespace_ref(
        self,
        ref: str,
        *,
        ctx_browser: Any,
        outdir: pathlib.Path,
        **context: Any,
    ) -> dict[str, Any]:
        raw = str(ref or "").strip()
        namespace, sep, key = raw.partition(":")
        namespace = namespace.strip()
        if not sep or not namespace:
            return {"rehosted": [], "missing": [], "errors": [], "invalid": [{"path": raw, "reason": "missing_namespace"}], "materialized": []}
        rehoster = self.namespace_rehoster(namespace)
        if rehoster is None:
            return {"rehosted": [], "missing": [], "errors": [], "invalid": [{"path": raw, "reason": "no_namespace_rehoster"}], "materialized": []}
        try:
            result = rehoster.handler(
                ref=raw,
                namespace=namespace,
                key=key,
                ctx_browser=ctx_browser,
                outdir=outdir,
                **context,
            )
            if inspect.isawaitable(result):
                result = await result
            return self._normalize_rehost_result(result, source_ref=raw)
        except Exception as exc:
            return {
                "rehosted": [],
                "missing": [],
                "errors": [{
                    "source_ref": raw,
                    "namespace": namespace,
                    "error": {
                        "code": "namespace_rehost_failed",
                        "message": str(exc),
                        "details": {"namespace": namespace},
                    },
                }],
                "invalid": [],
                "materialized": [],
            }

    def _load_module(self, ref: str) -> tuple[str, ModuleType]:
        if ref.endswith(".py") or os.path.sep in ref:
            path = pathlib.Path(ref)
            if not path.is_absolute():
                path = pathlib.Path.cwd() / path
            if not path.exists():
                raise RuntimeError(f"Event source module not found: {ref} -> {path}")
            return load_dynamic_module_for_path(path)
        mod = importlib.import_module(ref)
        return mod.__name__, mod

    def _discover_module(self, module_info: Mapping[str, Any]) -> list[ResolvedEventSource]:
        mod = module_info["mod"]
        alias = str(module_info.get("alias") or "").strip()
        out: list[ResolvedEventSource] = []

        list_fn = getattr(mod, "list_event_sources", None)
        listed = False
        if callable(list_fn):
            for idx, item in enumerate(list_fn() or []):
                resolved = self._resolve_declared_item(
                    item,
                    module_info=module_info,
                    alias=alias,
                    object_name=f"list_event_sources[{idx}]",
                )
                if resolved:
                    listed = True
                    out.append(resolved)
            if listed:
                return out

        for name, obj in vars(mod).items():
            if name.startswith("__") and name.endswith("__"):
                continue
            resolved = self._resolve_declared_item(
                obj,
                module_info=module_info,
                alias=alias,
                object_name=name,
            )
            if resolved:
                out.append(resolved)

        tools_owner = getattr(mod, "tools", None)
        if tools_owner is not None:
            for name in dir(tools_owner):
                if name.startswith("_"):
                    continue
                try:
                    obj = getattr(tools_owner, name)
                except Exception:
                    continue
                resolved = self._resolve_declared_item(
                    obj,
                    module_info=module_info,
                    alias=alias,
                    object_name=name,
                )
                if resolved:
                    out.append(resolved)

        return out

    def _discover_namespace_rehosters(self, module_info: Mapping[str, Any]) -> list[ResolvedArtifactNamespaceRehoster]:
        mod = module_info["mod"]
        alias = str(module_info.get("alias") or "").strip()
        out: list[ResolvedArtifactNamespaceRehoster] = []

        list_fn = getattr(mod, "list_artifact_namespace_rehosters", None)
        listed = False
        if callable(list_fn):
            for idx, item in enumerate(list_fn() or []):
                resolved = self._resolve_rehoster_item(
                    item,
                    module_info=module_info,
                    alias=alias,
                    object_name=f"list_artifact_namespace_rehosters[{idx}]",
                )
                if resolved:
                    listed = True
                    out.append(resolved)
            if listed:
                return out

        for name, obj in vars(mod).items():
            if name.startswith("__") and name.endswith("__"):
                continue
            resolved = self._resolve_rehoster_item(
                obj,
                module_info=module_info,
                alias=alias,
                object_name=name,
            )
            if resolved:
                out.append(resolved)

        return out

    def _discover_event_source_readers(self, module_info: Mapping[str, Any]) -> list[ResolvedEventSourceReader]:
        mod = module_info["mod"]
        alias = str(module_info.get("alias") or "").strip()
        out: list[ResolvedEventSourceReader] = []

        list_fn = getattr(mod, "list_event_source_readers", None)
        listed = False
        if callable(list_fn):
            for idx, item in enumerate(list_fn() or []):
                resolved = self._resolve_event_source_reader_item(
                    item,
                    module_info=module_info,
                    alias=alias,
                    object_name=f"list_event_source_readers[{idx}]",
                )
                if resolved:
                    listed = True
                    out.append(resolved)
            if listed:
                return out

        for name, obj in vars(mod).items():
            if name.startswith("__") and name.endswith("__"):
                continue
            resolved = self._resolve_event_source_reader_item(
                obj,
                module_info=module_info,
                alias=alias,
                object_name=name,
            )
            if resolved:
                out.append(resolved)

        return out

    def _discover_event_source_resolvers(self, module_info: Mapping[str, Any]) -> list[ResolvedEventSourceResolver]:
        mod = module_info["mod"]
        alias = str(module_info.get("alias") or "").strip()
        out: list[ResolvedEventSourceResolver] = []

        list_fn = getattr(mod, "list_event_source_resolvers", None)
        listed = False
        if callable(list_fn):
            for idx, item in enumerate(list_fn() or []):
                resolved = self._resolve_event_source_resolver_item(
                    item,
                    module_info=module_info,
                    alias=alias,
                    object_name=f"list_event_source_resolvers[{idx}]",
                )
                if resolved:
                    listed = True
                    out.append(resolved)
            if listed:
                return out

        for name, obj in vars(mod).items():
            if name.startswith("__") and name.endswith("__"):
                continue
            resolved = self._resolve_event_source_resolver_item(
                obj,
                module_info=module_info,
                alias=alias,
                object_name=name,
            )
            if resolved:
                out.append(resolved)

        return out

    def _resolve_event_source_reader_item(
        self,
        item: Any,
        *,
        module_info: Mapping[str, Any],
        alias: str,
        object_name: str,
    ) -> ResolvedEventSourceReader | None:
        handler: Any = item
        declaration: EventSourceReaderDeclaration | None = None
        if isinstance(item, Mapping):
            handler = item.get("handler")
            declaration = get_event_source_reader_declaration(handler)
        else:
            declaration = get_event_source_reader_declaration(item)
        if declaration is None:
            return None
        if not callable(handler):
            self._warn(f"invalid event source reader in {module_info.get('name')}.{object_name}: handler is not callable")
            return None
        namespace = str(declaration.namespace or "").strip().rstrip(":")
        if "{alias}" in namespace:
            if not alias:
                raise ValueError(f"event source reader namespace uses {{alias}} but module has no alias: {namespace}")
            namespace = namespace.replace("{alias}", alias)
        event_source_id = self._resolve_event_source_id(
            declaration.event_source_id,
            alias=alias,
            object_name=object_name,
        )
        return ResolvedEventSourceReader(
            namespace=namespace,
            event_source_id=event_source_id,
            handler=handler,
            description=declaration.description,
            version=declaration.version,
            module=str(module_info.get("name") or ""),
            alias=alias,
            object_name=object_name,
        )

    def _resolve_event_source_resolver_item(
        self,
        item: Any,
        *,
        module_info: Mapping[str, Any],
        alias: str,
        object_name: str,
    ) -> ResolvedEventSourceResolver | None:
        handler: Any = item
        declaration: EventSourceResolverDeclaration | None = None
        if isinstance(item, Mapping):
            handler = item.get("handler")
            declaration = get_event_source_resolver_declaration(handler)
        else:
            declaration = get_event_source_resolver_declaration(item)
        if declaration is None:
            return None
        if not callable(handler):
            self._warn(f"invalid event source resolver in {module_info.get('name')}.{object_name}: handler is not callable")
            return None
        namespace = str(declaration.namespace or "").strip().rstrip(":")
        if "{alias}" in namespace:
            if not alias:
                raise ValueError(f"event source resolver namespace uses {{alias}} but module has no alias: {namespace}")
            namespace = namespace.replace("{alias}", alias)
        return ResolvedEventSourceResolver(
            namespace=namespace,
            handler=handler,
            description=declaration.description,
            version=declaration.version,
            module=str(module_info.get("name") or ""),
            alias=alias,
            object_name=object_name,
        )

    def _resolve_rehoster_item(
        self,
        item: Any,
        *,
        module_info: Mapping[str, Any],
        alias: str,
        object_name: str,
    ) -> ResolvedArtifactNamespaceRehoster | None:
        handler: Any = item
        declaration: ArtifactNamespaceRehosterDeclaration | None = None
        if isinstance(item, Mapping):
            handler = item.get("handler")
            declaration = get_artifact_namespace_rehoster_declaration(handler)
        else:
            declaration = get_artifact_namespace_rehoster_declaration(item)
        if declaration is None:
            return None
        if not callable(handler):
            self._warn(f"invalid namespace rehoster in {module_info.get('name')}.{object_name}: handler is not callable")
            return None
        namespace = str(declaration.namespace or "").strip().rstrip(":")
        if "{alias}" in namespace:
            if not alias:
                raise ValueError(f"namespace uses {{alias}} but module has no alias: {namespace}")
            namespace = namespace.replace("{alias}", alias)
        return ResolvedArtifactNamespaceRehoster(
            namespace=namespace,
            handler=handler,
            description=declaration.description,
            version=declaration.version,
            module=str(module_info.get("name") or ""),
            alias=alias,
            object_name=object_name,
        )

    def _resolve_declared_item(
        self,
        item: Any,
        *,
        module_info: Mapping[str, Any],
        alias: str,
        object_name: str,
    ) -> ResolvedEventSource | None:
        declaration: EventSourceDeclaration | None
        if isinstance(item, EventSourceDeclaration):
            declaration = item
        elif isinstance(item, Mapping):
            try:
                declaration = event_source_declaration(**item)
            except Exception as exc:
                self._warn(f"invalid event source declaration in {module_info.get('name')}.{object_name}: {exc}")
                return None
        else:
            declaration = get_event_source_declaration(item)
        if declaration is None:
            return None

        event_source_id = self._resolve_event_source_id(
            declaration.event_source_id,
            alias=alias,
            object_name=object_name,
        )
        policies = tuple(dict(spec) for spec in (declaration.policies or ()))
        self._validate_policy_names(event_source_id, policies)
        return ResolvedEventSource(
            event_source_id=event_source_id,
            policies=policies,
            description=declaration.description,
            version=declaration.version,
            kind=declaration.kind,
            reactive=declaration.reactive,
            iteration_credit=declaration.iteration_credit,
            module=str(module_info.get("name") or ""),
            alias=alias,
            object_name=object_name,
            event_policies=dict(self._event_policies),
        )

    def _resolve_event_source_id(self, raw_id: str, *, alias: str, object_name: str) -> str:
        event_source_id = str(raw_id or "").strip()
        if not event_source_id:
            raise ValueError(f"event_source_id is empty for {object_name}")
        if "{alias}" in event_source_id:
            if not alias:
                raise ValueError(f"event_source_id uses {{alias}} but module has no alias: {event_source_id}")
            event_source_id = event_source_id.replace("{alias}", alias)
        elif "." not in event_source_id and alias:
            event_source_id = f"{alias}.{event_source_id}"
        return event_source_id

    def _validate_policy_names(self, event_source_id: str, policies: Iterable[Mapping[str, Any]]) -> None:
        for path in unknown_policy_paths(policies, registry=self._event_policies):
            self._warn(f"event source {event_source_id!r} declares unknown policy path {path!r}")

    def _register(self, source: ResolvedEventSource) -> None:
        existing = self._by_event_source_id.get(source.event_source_id)
        if existing:
            if existing.to_dict() == source.to_dict():
                return
            raise ValueError(f"Duplicate event_source_id: {source.event_source_id}")
        self._by_event_source_id[source.event_source_id] = source

    def _register_namespace_rehoster(self, rehoster: ResolvedArtifactNamespaceRehoster) -> None:
        existing = self._namespace_rehosters.get(rehoster.namespace)
        if existing:
            if existing.to_dict() == rehoster.to_dict():
                return
            raise ValueError(f"Duplicate artifact namespace rehoster: {rehoster.namespace}")
        self._namespace_rehosters[rehoster.namespace] = rehoster

    def _register_event_source_reader(self, reader: ResolvedEventSourceReader) -> None:
        existing = self._event_source_readers.get(reader.namespace)
        if existing:
            if existing.to_dict() == reader.to_dict():
                return
            raise ValueError(f"Duplicate event source reader namespace: {reader.namespace}")
        self._event_source_readers[reader.namespace] = reader

    def _register_event_source_resolver(self, resolver: ResolvedEventSourceResolver) -> None:
        existing = self._event_source_resolvers.get(resolver.namespace)
        if existing:
            if existing.to_dict() == resolver.to_dict():
                return
            raise ValueError(f"Duplicate event source resolver namespace: {resolver.namespace}")
        self._event_source_resolvers[resolver.namespace] = resolver

    @staticmethod
    def _normalize_rehost_result(result: Any, *, source_ref: str) -> dict[str, Any]:
        if result is None:
            return {"rehosted": [], "missing": [source_ref], "errors": [], "invalid": [], "materialized": []}
        if isinstance(result, str):
            result = {"physical_path": result}
        if isinstance(result, Mapping):
            rows_raw = result.get("materialized")
            if rows_raw is None and (result.get("physical_path") or result.get("logical_path")):
                rows_raw = [result]
            rows = [dict(row) for row in (rows_raw or []) if isinstance(row, Mapping)]
            rehosted = [
                str(row.get("physical_path") or "").strip()
                for row in rows
                if str(row.get("physical_path") or "").strip()
            ]
            rehosted.extend(str(item or "").strip() for item in (result.get("rehosted") or []) if str(item or "").strip())
            for row in rows:
                row.setdefault("source_ref", source_ref)
                if not row.get("file_count"):
                    row["file_count"] = 1
            return {
                "rehosted": list(dict.fromkeys(rehosted)),
                "missing": list(result.get("missing") or []),
                "errors": list(result.get("errors") or []),
                "invalid": list(result.get("invalid") or []),
                "materialized": rows,
            }
        if isinstance(result, IterableABC) and not isinstance(result, (bytes, str)):
            rows = [dict(row) for row in result if isinstance(row, Mapping)]
            rehosted = [
                str(row.get("physical_path") or "").strip()
                for row in rows
                if str(row.get("physical_path") or "").strip()
            ]
            for row in rows:
                row.setdefault("source_ref", source_ref)
                if not row.get("file_count"):
                    row["file_count"] = 1
            return {"rehosted": list(dict.fromkeys(rehosted)), "missing": [], "errors": [], "invalid": [], "materialized": rows}
        return {"rehosted": [], "missing": [], "errors": ["namespace_rehoster_returned_unsupported_shape"], "invalid": [], "materialized": []}

    def _warn(self, message: str) -> None:
        self.warnings.append(message)
        if self.log is not None:
            try:
                self.log.warning(message)
            except Exception:
                pass
