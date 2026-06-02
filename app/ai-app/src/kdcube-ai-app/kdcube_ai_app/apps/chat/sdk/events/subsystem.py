# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import importlib
import importlib.util
import os
import pathlib
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, Iterable, Mapping, MutableMapping

from kdcube_ai_app.apps.chat.sdk.runtime.dynamic_module_loader import load_dynamic_module_for_path

from kdcube_ai_app.apps.chat.sdk.events.decorator import (
    EventSourceDeclaration,
    event_source_declaration,
    get_event_source_declaration,
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

    def _warn(self, message: str) -> None:
        self.warnings.append(message)
        if self.log is not None:
            try:
                self.log.warning(message)
            except Exception:
                pass
