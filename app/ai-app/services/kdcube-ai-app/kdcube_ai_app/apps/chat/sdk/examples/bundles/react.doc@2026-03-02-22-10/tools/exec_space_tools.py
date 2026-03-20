# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# ── tools/exec_space_tools.py ──
# Bundle-local exec-only namespace resolver for react.doc.
#
# This tool intentionally sits outside the normal react.* knowledge-search surface.
# It resolves logical bundle refs such as ks:docs or ks:index.md to absolute
# exec-visible paths that generated Python can use inside isolated exec.

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any
import importlib.util
import sys

import semantic_kernel as sk

try:
    from semantic_kernel.functions import kernel_function
except Exception:
    from semantic_kernel.utils.function_decorator import kernel_function

from kdcube_ai_app.apps.chat.sdk.runtime.external.base import is_isolated_exec_process


def _ok_ret_result(ret: Any) -> dict[str, Any]:
    return {"ok": True, "error": None, "ret": ret}


def _error_result(
    *,
    code: str,
    message: str,
    where: str,
    managed: bool,
    ret: Any,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error = {
        "code": code,
        "message": message,
        "where": where,
        "managed": managed,
    }
    if details:
        error["details"] = details
    return {
        "ok": False,
        "error": error,
        "ret": ret,
    }


def _load_knowledge_resolver():
    module_name = "_kdcube_react_doc_knowledge_resolver"
    if module_name in sys.modules:
        return sys.modules[module_name]

    bundle_root = Path(__file__).resolve().parent.parent
    resolver_path = bundle_root / "knowledge" / "resolver.py"
    if not resolver_path.exists():
        raise ImportError(f"Knowledge resolver not found: {resolver_path}")

    spec = importlib.util.spec_from_file_location(module_name, str(resolver_path))
    if not spec or not spec.loader:
        raise ImportError(f"Cannot load knowledge resolver: {resolver_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


class ExecSpaceTools:
    @kernel_function(
        name="resolve_namespace",
        description=(
            "Resolve a bundle logical namespace or namespaced path to an absolute physical path that is valid "
            "ONLY inside isolated exec. EXEC-ONLY TOOL: use ONLY from generated Python running in execute_code_python. "
            "Do NOT call this as a normal planning-time knowledge tool. Outside isolated exec it returns an error. "
            "Returns an envelope: {ok, error, ret}. ret shape is "
            "{physical_path: str | null, access: 'r' | 'rw', browseable: bool}. "
            "physical_path is valid only inside the current isolated exec runtime. "
            "If ok=false, treat that as a blocker for the requested namespace-driven scenario unless a documented recovery path exists. "
            "If generated code discovers useful descendants under physical_path, it should derive follow-up logical "
            "refs from the logical_ref input it originally passed to this tool and emit those refs back through "
            "OUTPUT_DIR files or short user.log output."
        ),
    )
    async def resolve_namespace(
        self,
        logical_ref: Annotated[str, (
            "Logical namespace root or namespaced path to resolve, for example "
            "'ks:', 'ks:docs', 'ks:src', 'ks:deploy', or 'ks:index.md'."
        )],
    ) -> Annotated[dict[str, Any], (
        "Envelope: {ok, error, ret}. "
        "ret={physical_path: str | null, access: 'r' | 'rw', browseable: bool}. "
        "Use the logical_ref input as the logical base when generated code wants to emit follow-up refs for later react.read(...) calls. "
        "physical_path is usable only inside isolated exec."
    )]:
        where = "bundle_data.resolve_namespace"
        unavailable = {
            "physical_path": None,
            "access": "r",
            "browseable": False,
        }

        if not is_isolated_exec_process():
            return _error_result(
                code="exec_only_tool",
                message=(
                    "bundle_data.resolve_namespace is only valid inside isolated exec. "
                    "Use it from execute_code_python-generated code, not from normal agent tool planning."
                ),
                where=where,
                managed=True,
                ret=unavailable,
                details={"logical_ref": str(logical_ref or "").strip()},
            )

        try:
            resolver = _load_knowledge_resolver()
            resolved = resolver.resolve_exec_namespace(logical_ref=str(logical_ref or "").strip())
            return _ok_ret_result(resolved)
        except Exception as e:
            if getattr(e, "code", None):
                return _error_result(
                    code=str(getattr(e, "code", "namespace_resolution_failed")),
                    message=str(getattr(e, "message", "") or str(e) or "resolve_namespace failed").strip(),
                    where=where,
                    managed=True,
                    ret=unavailable,
                    details={"logical_ref": str(logical_ref or "").strip()},
                )
            return _error_result(
                code=type(e).__name__,
                message=str(e).strip() or "resolve_namespace failed",
                where=where,
                managed=False,
                ret=unavailable,
                details={"logical_ref": str(logical_ref or "").strip()},
            )


kernel = sk.Kernel()
tools = ExecSpaceTools()
kernel.add_plugin(tools, "exec_space_tools")
