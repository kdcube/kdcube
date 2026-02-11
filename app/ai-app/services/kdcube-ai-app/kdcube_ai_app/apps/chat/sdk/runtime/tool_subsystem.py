# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/runtime/tool_subsystem.py

from __future__ import annotations
import os
from dataclasses import dataclass, asdict

import sys
import pathlib
import asyncio
import inspect
import importlib
import importlib.util
from typing import Any, Dict, List, Optional, Tuple

from kdcube_ai_app.apps.chat.sdk.runtime.isolated.secure_client import ToolStub
from kdcube_ai_app.infra.plugin.bundle_registry import BundleSpec
from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase, AgentLogger
from kdcube_ai_app.infra.service_hub.cache import create_kv_cache
from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import ContextRAGClient


def parse_tool_id(tool_id: str) -> Tuple[str, str, str]:
    """
    Parse tool_id into (origin, provider, tool_name).
    - Back-compat: alias.tool_id -> ("mod", alias, tool_id)
    - Namespaced: origin.provider.tool_id... -> (origin, provider, tool_id...)
    """
    if not tool_id or not isinstance(tool_id, str):
        return "mod", "", ""
    parts = [p for p in tool_id.split(".") if p]
    if len(parts) == 2:
        return "mod", parts[0], parts[1]
    if len(parts) >= 3:
        origin = parts[0]
        provider = parts[1]
        tool_name = ".".join(parts[2:])
        return origin, provider, tool_name
    return "mod", parts[0], ""

@dataclass
class ToolModuleSpec:
    ref: str                 # dotted path or file path (abs/rel)
    use_sk: bool = False     # introspect via Semantic Kernel metadata
    alias: Optional[str] = None  # import alias for 'tools' (unique per module)


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
            bundle_spec: BundleSpec,
            context_rag_client: Optional[ContextRAGClient],
            registry: Optional[Dict[str, Any]] = None,
            tools_specs: Optional[List[Dict[str, Any]]] = None,  # [{"module"| "ref", "alias", "use_sk": bool}]
            raw_tool_specs: Optional[List[Dict[str, Any]]] = None,
            tool_runtime: Optional[Dict[str, str]] = None,
            mcp_subsystem: Optional[Any] = None,
    ):
        self.svc = service
        self.comm = comm
        self.bundle_spec = bundle_spec
        self.log = logger or AgentLogger("tool_subsystem")
        self.context_rag_client = context_rag_client
        self.registry = registry or {}
        try:
            self.kv_cache = create_kv_cache()
        except Exception:
            self.kv_cache = None
        self.raw_tool_specs = raw_tool_specs or []
        self._tool_runtime = tool_runtime or {}
        self.mcp_subsystem = mcp_subsystem
        self._mcp_entries: List[Dict[str, Any]] = []

        # --- compute bundle_root once ---
        self.bundle_root: pathlib.Path | None = None
        if bundle_spec and bundle_spec.path and bundle_spec.module:
            # Extract first segment of module (e.g., 'codegen' from 'codegen.entrypoint')
            module_first_segment = bundle_spec.module.split('.')[0]
            self.bundle_root = pathlib.Path(bundle_spec.path).joinpath(
                module_first_segment
            ).resolve()

        # If resolved tool_specs are not provided, resolve from raw_tool_specs + bundle_root
        if tools_specs is None and self.raw_tool_specs:
            if not self.bundle_root:
                raise RuntimeError("bundle_root is required to resolve raw_tool_specs")
            tools_specs = resolve_codegen_tools_specs(
                tool_specs=self.raw_tool_specs,
                bundle_root=self.bundle_root,
            )
        specs = self._resolve_tools(tools_specs or [])

        s_: List[ToolModuleSpec] = []

        if specs:
            for m in specs:
                s_.append(ToolModuleSpec(
                    ref=m.get("ref"),
                    use_sk=bool(m.get("use_sk", False)),
                    alias=m.get("alias")
                ))

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
                    mod.bind_integrations({
                        "ctx_client": self.context_rag_client,
                        "kv_cache": self.kv_cache,
                        "tool_subsystem": self,
                    })
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

        self._secure_stub = ToolStub()

    # ---------- public surface used by manager + react solver ----------
    def get_tool_runtime(self, tool_id: str) -> Optional[str]:
        if not tool_id:
            return None
        val = self._tool_runtime.get(tool_id)
        if not isinstance(val, str):
            return None
        v = val.strip().lower()
        if v in ("none", "local", "docker"):
            return v
        return None

    async def validate_tool_params(
            self,
            *,
            tool_id: str,
            params: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Validate tool params against the known signature.
        Returns:
          {
            "status": "green" | "yellow" | "red",
            "params": <filtered params>,
            "issues": [ {param, code, message, expected?, got?} ... ],
          }
        """
        issues: List[Dict[str, Any]] = []
        raw_params = params if isinstance(params, dict) else {}
        tid = (tool_id or "").strip()
        entry = self._by_id.get(tid)
        if not entry and self.mcp_subsystem:
            try:
                await self.ensure_mcp_entries()
            except Exception:
                pass
            entry = self._by_id.get(tid)
        if not entry:
            return {
                "status": "red",
                "params": {},
                "issues": [{
                    "param": "",
                    "code": "unknown_tool",
                    "message": f"tool_id '{tool_id}' not found in tool catalog",
                }],
            }

        sig_params = entry.get("params") or []
        if not isinstance(sig_params, list) or not sig_params:
            # No signature info; pass-through as green.
            return {
                "status": "green",
                "params": raw_params,
                "issues": [],
            }

        # Build signature map
        sig_by_name: Dict[str, Dict[str, Any]] = {}
        required_names: set[str] = set()
        for p in sig_params:
            if not isinstance(p, dict):
                continue
            name = (p.get("name") or "").strip()
            if not name:
                continue
            sig_by_name[name] = p
            if p.get("required") is True:
                required_names.add(name)

        def _expected_types_from_annot(annot: str) -> List[str]:
            a = (annot or "").lower()
            if not a:
                return []
            types = []
            for t in ("bool", "int", "float", "str", "string", "dict", "list", "object"):
                if t in a:
                    types.append("str" if t == "string" else t)
            return types

        def _type_ok(val: Any, annot: str) -> bool:
            if val is None:
                return True
            exp = _expected_types_from_annot(annot)
            if not exp:
                return True
            ok = False
            for t in exp:
                if t == "bool":
                    ok = ok or isinstance(val, bool)
                elif t == "int":
                    ok = ok or (isinstance(val, int) and not isinstance(val, bool))
                elif t == "float":
                    ok = ok or isinstance(val, (int, float)) and not isinstance(val, bool)
                elif t == "str":
                    ok = ok or isinstance(val, str)
                elif t == "list":
                    ok = ok or isinstance(val, list)
                elif t == "dict":
                    ok = ok or isinstance(val, dict)
                elif t == "object":
                    ok = True
            return ok

        filtered: Dict[str, Any] = {}
        # unknown params
        for k in raw_params.keys():
            if k not in sig_by_name:
                issues.append({
                    "param": k,
                    "code": "unknown_param",
                    "message": f"param '{k}' not in tool signature",
                })
        # check provided params
        for name, val in raw_params.items():
            meta = sig_by_name.get(name)
            if not meta:
                continue
            annot = meta.get("annotation") or ""
            if val is None:
                if name in required_names:
                    issues.append({
                        "param": name,
                        "code": "missing_required",
                        "message": f"required param '{name}' is null/empty",
                    })
                else:
                    filtered[name] = val
                continue
            if not _type_ok(val, annot):
                issues.append({
                    "param": name,
                    "code": "type_mismatch",
                    "message": f"param '{name}' has incompatible type",
                    "expected": annot,
                    "got": type(val).__name__,
                })
                # drop mismatched param
                continue
            filtered[name] = val

        # missing required params
        for req in sorted(required_names):
            if req not in raw_params or raw_params.get(req) is None:
                issues.append({
                    "param": req,
                    "code": "missing_required",
                    "message": f"required param '{req}' is missing",
                })

        # classify status
        status = "green"
        has_red = any(i.get("code") == "missing_required" for i in issues)
        if has_red:
            status = "red"
        elif issues:
            status = "yellow"

        return {
            "status": status,
            "params": filtered,
            "issues": issues,
        }

    def tool_catalog_for_prompt(self, *, allowed_plugins: Optional[List[str]] = None,
                                allowed_ids: Optional[List[str]] = None,
                                include_mcp: bool = True) -> List[Dict[str, Any]]:
        return [
            {"id": e["id"], "doc": {"purpose": e["doc"]["purpose"], "args": e["doc"]["args"], "returns": e["doc"]["returns"]}}
            for e in self._filter_entries(allowed_plugins, allowed_ids, include_mcp=include_mcp)
        ]

    async def tool_catalog_for_prompt_async(self, *, allowed_plugins: Optional[List[str]] = None,
                                            allowed_ids: Optional[List[str]] = None,
                                            include_mcp: bool = True) -> List[Dict[str, Any]]:
        if include_mcp:
            try:
                await self.ensure_mcp_entries()
            except Exception:
                pass
        return self.tool_catalog_for_prompt(allowed_plugins=allowed_plugins, allowed_ids=allowed_ids, include_mcp=include_mcp)

    def adapters_for_codegen(self, *,
                             allowed_plugins: Optional[List[str]] = None,
                             allowed_ids: Optional[List[str]] = None,
                             denied_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        # Deprecated: use react_tools()/react_tools_cached()
        return self.react_tools_cached(
            allowed_plugins=allowed_plugins,
            allowed_ids=allowed_ids,
            denied_ids=denied_ids,
        )

    async def react_tools(self, *,
                          allowed_plugins: Optional[List[str]] = None,
                          allowed_ids: Optional[List[str]] = None,
                          denied_ids: Optional[List[str]] = None,
                          include_mcp: bool = True) -> List[Dict[str, Any]]:
        if include_mcp:
            try:
                await self.ensure_mcp_entries()
            except Exception:
                pass
        return self.react_tools_cached(
            allowed_plugins=allowed_plugins,
            allowed_ids=allowed_ids,
            denied_ids=denied_ids,
            include_mcp=include_mcp,
        )

    def react_tools_cached(self, *,
                           allowed_plugins: Optional[List[str]] = None,
                           allowed_ids: Optional[List[str]] = None,
                           denied_ids: Optional[List[str]] = None,
                           include_mcp: bool = True) -> List[Dict[str, Any]]:
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
        } for e in self._filter_entries(allowed_plugins, allowed_ids, denied_ids, include_mcp=include_mcp)]

    def get_alias_maps(self) -> Tuple[Dict[str, str], Dict[str, Optional[str]]]:
        """Returns (alias->dyn_module_name, alias->file_path)."""
        alias_to_dyn = {m["alias"]: m["name"] for m in self._modules}
        alias_to_file = {m["alias"]: m.get("file") for m in self._modules}
        return alias_to_dyn, alias_to_file

    def get_mcp_subsystem(self):
        return self.mcp_subsystem

    async def ensure_mcp_entries(self) -> List[Dict[str, Any]]:
        if not self.mcp_subsystem:
            return []
        if self._mcp_entries:
            return self._mcp_entries
        try:
            entries = await self.mcp_subsystem.build_tool_entries()
        except Exception:
            entries = []
        if entries:
            self._mcp_entries = entries
            # merge into by_id so validation works
            for e in entries:
                tid = e.get("id")
                if tid:
                    self._by_id[tid] = e
        return self._mcp_entries

    def tool_modules_tuple_list(self) -> List[Tuple[str, object]]:
        """[(dyn_module_name, module_obj)]"""
        return [(m["name"], m["mod"]) for m in self._modules]

    def get_owner_for_alias(self, alias: str):
        modrec = self._mods_by_alias.get(alias)
        if not modrec:
            return None
        owner = getattr(modrec["mod"], "tools", None) or modrec["mod"]
        return owner

    def resolve_callable(self, qualified_id: str):
        try:
            origin, provider, fn = parse_tool_id(qualified_id)
            if origin != "mod" or not provider or not fn:
                return None
            owner = self.get_owner_for_alias(provider)
            if owner is None:
                return None
            return getattr(owner, fn, None)
        except Exception:
            return None

    async def prebind_for_in_memory(self, *,
                                    workdir: pathlib.Path,
                                    outdir: pathlib.Path,
                                    logger: AgentLogger,
                                    bootstrap_env: bool = True):
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
            bootstrap_bind_all(spec.to_json(), module_names=bind_names, bootstrap_env=bootstrap_env)
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
                        allowed_ids: Optional[List[str]] = None,
                        denied_ids: Optional[List[str]] = None,
                        include_mcp: bool = True) -> List[Dict[str, Any]]:
        ents = list(self.tools_info)
        if include_mcp and self._mcp_entries:
            ents = ents + list(self._mcp_entries)
        system_tool = lambda e: (e.get("plugin_alias") or "") in ["io_tools"]
        if allowed_plugins:
            allow = set(p.strip() for p in allowed_plugins if p and str(p).strip())
            ents = [e for e in ents if (e.get("plugin_alias") or "") in allow]
        if allowed_ids:
            allow_ids = set(allowed_ids)
            ents = [e for e in ents if system_tool(e) or e["id"] in allow_ids]
        if denied_ids:
            deny_ids = set(denied_ids)
            ents = [e for e in ents if e["id"] not in deny_ids]
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
            required = p.default is inspect._empty
            out.append({
                "name": p.name,
                "annotation": str(p.annotation) if p.annotation is not inspect._empty else "",
                "default": None if p.default is inspect._empty else p.default,
                "kind": str(p.kind),
                "required": bool(required),
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

    def export_runtime_globals(self) -> Dict[str, Any]:
        """
        Minimal shape to pass into iso_runtime / docker for tool execution.
        Host-only / heavy things stay out.
        """
        alias_to_dyn, alias_to_file = self.get_alias_maps()

        bundle_dict = None
        if self.bundle_spec:
            try:
                bundle_dict = asdict(self.bundle_spec)
            except TypeError:
                bundle_dict = {
                    "id": self.bundle_spec.id,
                    "name": self.bundle_spec.name,
                    "path": self.bundle_spec.path,
                    "module": self.bundle_spec.module,
                    "singleton": self.bundle_spec.singleton,
                    "description": self.bundle_spec.description,
                    "version": getattr(self.bundle_spec, "version", None),
                }

        return {
            "TOOL_ALIAS_MAP": alias_to_dyn,
            "TOOL_MODULE_FILES": alias_to_file,
            "BUNDLE_SPEC": bundle_dict,
            "BUNDLE_ROOT_HOST": str(self.bundle_root) if self.bundle_root else None,
            "RAW_TOOL_SPECS": self.raw_tool_specs or [],
            "MCP_TOOL_SPECS": [
                {"server_id": s.server_id, "alias": s.alias, "tools": s.tools}
                for s in (getattr(self.mcp_subsystem, "mcp_specs", []) or [])
            ] if self.mcp_subsystem else [],
        }

def resolve_codegen_tools_specs(tool_specs: List[Dict[str, Any]],
                                bundle_root: pathlib.Path | None = None) -> List[Dict[str, Any]]:
    """
    Turn the portable descriptor into concrete specs with absolute paths
    for "ref" entries, *relative to bundle_root*.

    This function can be called:
      - on the host (orchestrator) with its bundle_root
      - inside docker with the container's bundle_root (/bundles/<id>, etc.)
    """
    root = bundle_root
    specs: List[Dict[str, Any]] = []

    for spec in tool_specs:
        s = dict(spec)  # shallow copy
        ref = s.get("ref")
        if ref and not os.path.isabs(ref):
            if root is None:
                raise ValueError("bundle_root is required when ref is relative")
            s["ref"] = str((root / ref).resolve())
        specs.append(s)

    return specs


def create_tool_subsystem_with_mcp(
        *,
        service: ModelServiceBase,
        comm: ChatCommunicator,
        logger: Optional[AgentLogger],
        bundle_spec: BundleSpec,
        context_rag_client: Optional[ContextRAGClient],
        registry: Optional[Dict[str, Any]] = None,
        tools_specs: Optional[List[Dict[str, Any]]] = None,
        raw_tool_specs: Optional[List[Dict[str, Any]]] = None,
        tool_runtime: Optional[Dict[str, str]] = None,
        mcp_tool_specs: Optional[List[Dict[str, Any]]] = None,
        mcp_env_json: Optional[str] = None,
):
    """
    Factory to create MCPToolsSubsystem (optional) + ToolSubsystem.
    Returns (tool_subsystem, mcp_subsystem).
    """
    mcp_subsystem = None
    try:
        if mcp_tool_specs:
            from kdcube_ai_app.apps.chat.sdk.runtime.mcp.mcp_tools_subsystem import MCPToolsSubsystem
            mcp_subsystem = MCPToolsSubsystem(
                bundle_id=bundle_spec.id,
                mcp_tool_specs=mcp_tool_specs,
                env_json=mcp_env_json or "",
            )
    except Exception:
        mcp_subsystem = None

    tool_subsystem = ToolSubsystem(
        service=service,
        comm=comm,
        bundle_spec=bundle_spec,
        logger=logger,
        context_rag_client=context_rag_client,
        registry=registry,
        tools_specs=tools_specs,
        raw_tool_specs=raw_tool_specs,
        tool_runtime=tool_runtime,
        mcp_subsystem=mcp_subsystem,
    )
    return tool_subsystem, mcp_subsystem
