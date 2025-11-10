# SPDX-License-Identifier: MIT
# Copyright (c) 2025

# chat/sdk/runtime/tool_subsystem.py

from __future__ import annotations
import os
import sys
import json
import time
import pathlib
import asyncio
import inspect
import importlib
import importlib.util
from typing import Any, Dict, List, Optional, Tuple, Callable

from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase, AgentLogger
from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import ContextRAGClient

# Contracts/doc types reused by CodegenToolManager
from .contracts import ToolModuleSpec

class ToolSubsystem:
    """
    Single place to:
      - resolve tool specs (`module` or `ref`) -> files
      - load tool modules once per session
      - introspect tools (SK/non-SK) -> entries (id, doc, call_template)
      - maintain alias <-> dyn module mapping
      - provide adapters/catalog for planners/codegen
      - prepare runtime bindings for in-memory execution
      - resolve callables for "<alias>.<fn>"
    """

    def __init__(
            self,
            *,
            service: ModelServiceBase,
            comm: ChatCommunicator,
            logger: Optional[AgentLogger],
            context_rag_client: Optional[ContextRAGClient],
            registry: Optional[Dict[str, Any]] = None,
            tools_specs: Optional[List[Dict[str, Any]]] = None,  # [{"module"| "ref", "alias", "use_sk": bool}]
    ):
        self.svc = service
        self.comm = comm
        self.log = logger or AgentLogger("tool_subsystem")
        self.context_rag_client = context_rag_client
        self.registry = registry or {}

        specs = self._resolve_tools(tools_specs or [])

        # Loaded modules + metadata
        self._modules: List[Dict[str, Any]] = []   # {name, mod, alias, use_sk, file}
        self.tools_info: List[Dict[str, Any]] = [] # flattened tool entries

        used_aliases: set[str] = set()

        for spec in specs:
            mod_name, mod = self._load_tools_module(spec["ref"])
            alias = spec.get("alias") or pathlib.Path(mod_name).name
            # ensure unique alias
            base_alias, i = alias, 1
            while alias in used_aliases:
                alias = f"{base_alias}{i}"; i += 1
            used_aliases.add(alias)

            # optional service bindings into modules
            try:
                if hasattr(mod, "bind_service"):
                    mod.bind_service(self.svc)
            except Exception:
                pass
            try:
                if hasattr(mod, "bind_registry"):
                    mod.bind_registry(self.registry)
            except Exception:
                pass
            try:
                if hasattr(mod, "bind_integrations"):
                    mod.bind_integrations({"ctx_client": self.context_rag_client})
            except Exception:
                pass

            self._modules.append({
                "name": mod_name,
                "mod": mod,
                "alias": alias,
                "use_sk": bool(spec.get("use_sk")),
                "file": getattr(mod, "__file__", None),
            })
            self.tools_info.extend(self._introspect_module(mod, mod_name, alias, bool(spec.get("use_sk"))))

        # fast maps
        self._by_id = {e["id"]: e for e in self.tools_info}
        self._mods_by_alias = {m["alias"]: m for m in self._modules}

    # ---------- public surface used by manager + react solver ----------

    def tool_catalog_for_prompt(self, *, allowed_plugins: Optional[List[str]] = None,
                                allowed_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        return [
            {"id": e["id"], "doc": {"purpose": e["doc"]["purpose"], "args": e["doc"]["args"], "returns": e["doc"]["returns"]}}
            for e in self._filter_entries(allowed_plugins, allowed_ids)
        ]

    def adapters_for_codegen(self, *, allowed_plugins: Optional[List[str]] = None,
                             allowed_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        # ensure io/ctx are always present
        ap = set(allowed_plugins or [])
        ap.update({"io_tools", "ctx_tools"})
        allowed_plugins = list(ap)
        return [{
            "id": e["id"],
            "import": e["import"],
            "call_template": e["call_template"],
            "is_async": bool(e.get("is_async")),
            "doc": e["doc"],
        } for e in self._filter_entries(allowed_plugins, allowed_ids)]

    def get_alias_maps(self) -> Tuple[Dict[str, str], Dict[str, Optional[str]]]:
        """Returns (alias->dyn_module_name, alias->file_path)."""
        alias_to_dyn = {m["alias"]: m["name"] for m in self._modules}
        alias_to_file = {m["alias"]: m.get("file") for m in self._modules}
        return alias_to_dyn, alias_to_file

    def tool_modules_tuple_list(self) -> List[Tuple[str, object]]:
        """[(dyn_module_name, module_obj)]"""
        return [(m["name"], m["mod"]) for m in self._modules]

    def get_owner_for_alias(self, alias: str):
        """Return module owner to call (prefer <mod>.tools if present)."""
        modrec = self._mods_by_alias.get(alias)
        if not modrec:
            return None
        owner = getattr(modrec["mod"], "tools", None) or modrec["mod"]
        return owner

    def resolve_callable(self, qualified_id: str):
        try:
            alias, fn = qualified_id.split(".", 1)
            owner = self.get_owner_for_alias(alias)
            if owner is None:
                return None
            return getattr(owner, fn, None)
        except Exception:
            return None

    async def prebind_for_in_memory(self, *, workdir: pathlib.Path, outdir: pathlib.Path, logger: AgentLogger):
        """
        Mirror sandbox bootstrap in-process so io_tools writes to the same outdir/workdir and
        tool modules have service bindings.
        """
        from importlib import util as _import_util
        from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import OUTDIR_CV, WORKDIR_CV
        from kdcube_ai_app.apps.chat.sdk.runtime.bootstrap import bootstrap_bind_all
        from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import set_comm
        from kdcube_ai_app.apps.chat.sdk.runtime.snapshot import build_portable_spec

        workdir.mkdir(parents=True, exist_ok=True)
        outdir.mkdir(parents=True, exist_ok=True)

        try:
            OUTDIR_CV.set(str(outdir))
            WORKDIR_CV.set(str(workdir))
        except Exception as e:
            logger.log(f"[tool-subsystem] Failed to set CVs: {e}", level="ERROR")

        # Preload dyn modules by file so `import dyn_*` works if needed
        alias_to_dyn, alias_to_file = self.get_alias_maps()
        for alias, dyn_mod in alias_to_dyn.items():
            path = (alias_to_file or {}).get(alias)
            if not path:
                continue
            try:
                spec_obj = _import_util.spec_from_file_location(dyn_mod, path)
                if not spec_obj or not spec_obj.loader:
                    continue
                mod_obj = _import_util.module_from_spec(spec_obj)
                spec_obj.loader.exec_module(mod_obj)  # type: ignore
                sys.modules[dyn_mod] = mod_obj
            except Exception as e:
                logger.log(f"[tool-subsystem] preload dyn module failed: {dyn_mod}: {e}", level="WARNING")

        # Bootstrap all loaded module names for service bindings in this process
        spec = build_portable_spec(svc=self.svc, chat_comm=self.comm)
        bind_names = [m["name"] for m in self._modules]
        try:
            bootstrap_bind_all(spec.to_json(), module_names=bind_names)
            set_comm(self.comm)
        except Exception as e:
            logger.log(f"[tool-subsystem] bootstrap_bind_all/set_comm failed: {e}", level="ERROR")

    # ---------- internals (moved from CodegenToolManager) ----------

    def _resolve_tools(self, specs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        def _module_to_file(module_name: str) -> pathlib.Path:
            spec = importlib.util.find_spec(module_name)
            if not spec or not spec.origin:
                raise ImportError(f"Cannot resolve module '{module_name}' to a file (no spec.origin).")
            return pathlib.Path(spec.origin).resolve()

        resolved = []
        for s in specs:
            alias = s["alias"]
            use_sk = bool(s.get("use_sk", True))
            if "module" in s:
                file_path = _module_to_file(s["module"])
            elif "ref" in s:
                file_path = pathlib.Path(s["ref"]).resolve()
            else:
                raise ValueError(f"Tool spec for alias={alias} must have 'module' or 'ref'.")
            resolved.append({"ref": str(file_path), "alias": alias, "use_sk": use_sk})
        return resolved

    def _load_tools_module(self, ref: str) -> Tuple[str, object]:
        if not ref:
            raise RuntimeError("tools_module ref is required")

        # file path
        if ref.endswith(".py") or os.path.sep in ref:
            p = pathlib.Path(ref)
            if not p.is_absolute():
                p = (pathlib.Path.cwd() / p).resolve()
            if not p.exists():
                raise RuntimeError(f"Tools module not found: {ref} -> {p}")

            import hashlib
            digest = hashlib.sha1(str(p).encode("utf-8")).hexdigest()[:8]
            mod_name = f"dyn_{p.stem}_{digest}"

            spec = importlib.util.spec_from_file_location(mod_name, str(p))
            if not spec or not spec.loader:
                raise RuntimeError(f"Cannot load tools module from path: {p}")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)  # type: ignore
            return mod_name, mod

        # dotted import
        mod = importlib.import_module(ref)
        return mod.__name__, mod

    def _filter_entries(self, allowed_plugins: Optional[List[str]] = None,
                        allowed_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        ents = list(self.tools_info)
        system_tool = lambda e: (e.get("plugin_alias") or "") in ["io_tools"]
        if allowed_plugins:
            allow = set(p.strip() for p in allowed_plugins if p and str(p).strip())
            ents = [e for e in ents if (e.get("plugin_alias") or "") in allow]
        if allowed_ids:
            allow_ids = set(allowed_ids)
            ents = [e for e in ents if system_tool(e) or e["id"] in allow_ids]
        return ents

    def _introspect_module(self, mod, mod_name: str, alias: str, use_sk: bool) -> List[Dict[str, Any]]:
        if use_sk and hasattr(mod, "kernel"):
            return self._introspect_via_semantic_kernel(mod, mod_name, alias)

        # Prefer list_tools() if present (non-SK)
        if hasattr(mod, "list_tools"):
            reg = mod.list_tools()  # {fn_name: {callable, description, ...}}
            entries: List[Dict[str, Any]] = []
            for fn_name, meta in reg.items():
                fn = meta.get("callable") or getattr(getattr(mod, "tools", mod), fn_name, None)
                desc = meta.get("description") or getattr(fn, "description", "") or (getattr(fn, "__doc__", "") or "")
                params = self._sig_to_params(fn)
                import_stmt = f"from {mod_name} import tools as {alias}"
                call_template = self._make_call_template(alias, fn_name, params)
                ret_annot = (str(meta.get("return_annotation"))
                             if isinstance(meta, dict) and meta.get("return_annotation") is not None
                             else self._annot_from_sig_return(fn))
                entries.append(self._mk_entry(
                    alias, fn_name, import_stmt, call_template, desc, params,
                    raw=meta, is_async=asyncio.iscoroutinefunction(fn), return_annotation=ret_annot
                ))
            return entries

        # Fallback: reflect on 'tools' or module
        owner = getattr(mod, "tools", mod)
        import_stmt = (f"from {mod_name} import tools as {alias}"
                       if hasattr(mod, "tools") else f"import {mod_name} as {alias}")
        entries: List[Dict[str, Any]] = []
        for name in dir(owner):
            if name.startswith("_"):
                continue
            fn = getattr(owner, name, None)
            if not callable(fn):
                continue
            params = self._sig_to_params(fn)
            desc = getattr(fn, "description", "") or (getattr(fn, "__doc__", "") or "")
            call_template = self._make_call_template(alias, name, params)
            is_async = asyncio.iscoroutinefunction(fn)
            ret_annot = self._annot_from_sig_return(fn)
            entries.append(self._mk_entry(
                alias, name, import_stmt, call_template, desc, params,
                raw=None, is_async=is_async, return_annotation=ret_annot
            ))
        return entries

    def _introspect_via_semantic_kernel(self, mod, mod_name: str, alias: str) -> List[Dict[str, Any]]:
        kernel = getattr(mod, "kernel")
        metas = getattr(kernel, "get_full_list_of_function_metadata")()
        dict_metas: List[Dict[str, Any]] = []
        for m in metas:
            if hasattr(m, "model_dump"):
                dict_metas.append(m.model_dump())
            elif hasattr(m, "to_dict"):
                dict_metas.append(m.to_dict())
            elif isinstance(m, dict):
                dict_metas.append(m)
            else:
                dict_metas.append(vars(m))

        entries: List[Dict[str, Any]] = []
        import_stmt = f"from {mod_name} import tools as {alias}"

        for fm in dict_metas:
            fn_name = fm.get("name")
            if not fn_name:
                continue
            desc = fm.get("description", "")
            plugin = fm.get("plugin_name") or ""
            params_meta = fm.get("parameters", []) or []
            params = []
            for p in params_meta:
                pname = p.get("name")
                if not pname or pname == "self":
                    continue
                default = p.get("default_value", None)
                annot = ""
                schema = p.get("schema_data") or {}
                if schema:
                    t = schema.get("type")
                    d = schema.get("description")
                    annot = ", ".join([s for s in [str(t) if t else "", str(d) if d else ""] if s]).strip(", ")
                params.append({"name": pname, "annotation": annot, "default": default, "kind": "POSITIONAL_OR_KEYWORD"})

            call_template = self._make_call_template(alias, fn_name, params)
            is_async = bool(fm.get("is_asynchronous"))
            ret_annot = self._annot_from_sk_return(fm)
            entry = self._mk_entry(
                alias, fn_name, import_stmt, call_template, desc, params,
                raw=fm, is_async=is_async, return_annotation=ret_annot
            )
            entry["plugin"] = plugin
            entry["plugin_alias"] = alias
            entries.append(entry)
        return entries

    def _sig_to_params(self, fn) -> List[Dict[str, Any]]:
        out = []
        try:
            sig = inspect.signature(fn)
        except Exception:
            sig = None
        if not sig:
            return out
        for p in sig.parameters.values():
            if p.name == "self":
                continue
            out.append({
                "name": p.name,
                "annotation": str(p.annotation) if p.annotation is not inspect._empty else "",
                "default": None if p.default is inspect._empty else p.default,
                "kind": str(p.kind),
            })
        return out

    def _annot_from_sig_return(self, fn) -> str:
        try:
            sig = inspect.signature(fn)
            ra = sig.return_annotation
            if ra is inspect._empty:
                return ""
            return str(ra)
        except Exception:
            return ""

    def _annot_from_sk_return(self, fm: Dict[str, Any]) -> str:
        rp = (fm or {}).get("return_parameter") or {}
        if not isinstance(rp, dict):
            return ""
        schema = rp.get("schema_data") or {}
        t = schema.get("type") or rp.get("type_") or ""
        d = rp.get("description") or schema.get("description") or ""
        parts = [str(t)] if t else []
        if d: parts.append(str(d))
        return " — ".join(parts) if parts else ""

    def _make_call_template(self, alias: str, fn_name: str, params: List[Dict[str, Any]]) -> str:
        if params:
            kw = ", ".join([f"{p['name']}={{${p['name']}$}}" for p in params])
            return f"{alias}.{fn_name}({kw})"
        return f"{alias}.{fn_name}()"

    def _mk_entry(
            self,
            alias: str,
            fn_name: str,
            import_stmt: str,
            call_template: str,
            desc: str,
            params: List[Dict[str, Any]],
            raw: Optional[Dict[str, Any]] = None,
            is_async: bool = False,
            return_annotation: Optional[str] = None,
    ) -> Dict[str, Any]:
        args_doc = {}
        for p in params:
            type_hint = (p.get("annotation") or "any")
            if p.get("default") not in (None, inspect._empty):
                type_hint += f" (default={p['default']})"
            args_doc[p["name"]] = type_hint
        returns_doc = (return_annotation or "").strip() or "str or JSON (tool-specific)"
        entry = {
            "id": f"{alias}.{fn_name}",
            "desc": (desc or "").strip(),
            "params": params,
            "import": import_stmt,
            "call_template": call_template.replace("${", "{").replace("}$", "}"),
            "is_async": bool(is_async),
            "doc": {
                "purpose": (desc or "").strip(),
                "args": args_doc,
                "returns": returns_doc,
                "constraints": [],
                "examples": [],
            },
            "raw": raw or {},
        }
        if "plugin" not in entry: entry["plugin"] = (raw or {}).get("plugin_name", "") or ""
        if "plugin_alias" not in entry: entry["plugin_alias"] = alias
        return entry
