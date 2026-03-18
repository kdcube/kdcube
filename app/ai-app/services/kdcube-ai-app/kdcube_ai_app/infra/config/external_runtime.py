# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .platform_env import build_external_runtime_base_env


def _compact_bundle_spec(
    bundle_spec: Mapping[str, Any],
    *,
    bundle_root: Optional[str] = None,
) -> Dict[str, Any]:
    compact: Dict[str, Any] = {}
    for key in (
        "id",
        "name",
        "path",
        "module",
        "singleton",
        "description",
        "version",
        "repo",
        "ref",
        "subdir",
        "git_commit",
    ):
        value = bundle_spec.get(key)
        if value is None:
            continue
        if isinstance(value, str) and value == "":
            continue
        compact[key] = value
    if bundle_root:
        compact["path"] = bundle_root
    return compact


def _compact_raw_tool_specs(raw_specs: Any) -> list[Dict[str, Any]]:
    compact: list[Dict[str, Any]] = []
    if not isinstance(raw_specs, list):
        return compact
    for item in raw_specs:
        if not isinstance(item, dict):
            continue
        spec = {
            key: value
            for key, value in item.items()
            if key != "raw" and value is not None and not (isinstance(value, str) and value == "")
        }
        if spec:
            compact.append(spec)
    return compact


def _rewrite_if_under_root(path: str, *, host_bundle_root: str, bundle_root: str) -> Optional[str]:
    if path.startswith(host_bundle_root):
        rel = os.path.relpath(path, host_bundle_root)
        return f"{bundle_root}/{rel}"
    return None


def _compact_mapping(value: Any) -> Any:
    if isinstance(value, dict):
        compact: Dict[str, Any] = {}
        for key, nested in value.items():
            compact_nested = _compact_mapping(nested)
            if compact_nested in (None, "", {}, []):
                continue
            compact[str(key)] = compact_nested
        return compact
    if isinstance(value, list):
        compact_list = [_compact_mapping(item) for item in value]
        return [item for item in compact_list if item not in (None, "", {}, [])]
    return value


def _compact_portable_spec_json(value: Any) -> Any:
    spec_json: Optional[str]
    if isinstance(value, str):
        spec_json = value
    elif isinstance(value, dict):
        try:
            spec_json = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        except Exception:
            return value
    else:
        return value

    try:
        parsed = json.loads(spec_json)
    except Exception:
        return value
    if not isinstance(parsed, dict):
        return value

    compact = _compact_mapping(parsed)
    try:
        return json.dumps(compact, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:
        return value


def prepare_external_runtime_globals(
    runtime_globals: Mapping[str, Any],
    *,
    host_bundle_root: Optional[str | Path] = None,
    bundle_root: Optional[str] = None,
    bundle_dir: Optional[str] = None,
    bundle_id: Optional[str] = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        str(key): value
        for key, value in (runtime_globals or {}).items()
        if value is not None
    }
    host_root_str = str(Path(host_bundle_root).resolve()) if host_bundle_root else None

    if bundle_dir:
        out["BUNDLE_DIR"] = bundle_dir
    if bundle_id or bundle_dir:
        out["BUNDLE_ID"] = bundle_id or bundle_dir

    if host_root_str and bundle_root:
        tool_files = out.get("TOOL_MODULE_FILES")
        if isinstance(tool_files, dict):
            rewritten_tool_files: Dict[str, str] = {}
            for alias, path in tool_files.items():
                if not path:
                    continue
                rewritten = _rewrite_if_under_root(str(path), host_bundle_root=host_root_str, bundle_root=bundle_root)
                if rewritten:
                    rewritten_tool_files[str(alias)] = rewritten
            if rewritten_tool_files:
                out["TOOL_MODULE_FILES"] = rewritten_tool_files
            else:
                out.pop("TOOL_MODULE_FILES", None)

        skills_desc = out.get("SKILLS_DESCRIPTOR")
        if isinstance(skills_desc, dict):
            csr = skills_desc.get("custom_skills_root")
            if isinstance(csr, str):
                rewritten = _rewrite_if_under_root(csr, host_bundle_root=host_root_str, bundle_root=bundle_root)
                skills_desc = dict(skills_desc)
                if rewritten:
                    skills_desc["custom_skills_root"] = rewritten
                elif csr.startswith(host_root_str):
                    skills_desc.pop("custom_skills_root", None)
                out["SKILLS_DESCRIPTOR"] = skills_desc

        raw_specs = out.get("RAW_TOOL_SPECS")
        if isinstance(raw_specs, list):
            rewritten_specs: list[Dict[str, Any]] = []
            for item in raw_specs:
                if not isinstance(item, dict):
                    continue
                spec = dict(item)
                ref = spec.get("ref")
                if isinstance(ref, str) and os.path.isabs(ref):
                    rewritten_ref = _rewrite_if_under_root(
                        ref,
                        host_bundle_root=host_root_str,
                        bundle_root=bundle_root,
                    )
                    if rewritten_ref:
                        spec["ref"] = rewritten_ref
                rewritten_specs.append(spec)
            out["RAW_TOOL_SPECS"] = rewritten_specs

    portable_spec_json = out.get("PORTABLE_SPEC_JSON")
    if portable_spec_json is not None:
        out["PORTABLE_SPEC_JSON"] = _compact_portable_spec_json(portable_spec_json)

    tool_files = out.get("TOOL_MODULE_FILES")
    if isinstance(tool_files, dict):
        compact_tool_files = {
            str(alias): str(path)
            for alias, path in tool_files.items()
            if path
        }
        if compact_tool_files:
            out["TOOL_MODULE_FILES"] = compact_tool_files
        else:
            out.pop("TOOL_MODULE_FILES", None)

    bundle_spec = out.get("BUNDLE_SPEC")
    if isinstance(bundle_spec, dict):
        compact_bundle = _compact_bundle_spec(bundle_spec, bundle_root=bundle_root)
        if compact_bundle:
            out["BUNDLE_SPEC"] = compact_bundle
        else:
            out.pop("BUNDLE_SPEC", None)

    raw_specs = _compact_raw_tool_specs(out.get("RAW_TOOL_SPECS"))
    if raw_specs:
        out["RAW_TOOL_SPECS"] = raw_specs
    else:
        out.pop("RAW_TOOL_SPECS", None)

    mcp_specs = out.get("MCP_TOOL_SPECS")
    if not mcp_specs:
        out.pop("MCP_TOOL_SPECS", None)

    skills_desc = out.get("SKILLS_DESCRIPTOR")
    if isinstance(skills_desc, dict):
        compact_skills = {
            key: value
            for key, value in skills_desc.items()
            if value not in (None, {}, [])
        }
        if compact_skills:
            out["SKILLS_DESCRIPTOR"] = compact_skills
        else:
            out.pop("SKILLS_DESCRIPTOR", None)

    snapshot = out.get("EXEC_SNAPSHOT")
    if isinstance(snapshot, dict):
        compact_snapshot: Dict[str, Any] = {}
        storage_uri = snapshot.get("storage_uri")
        base_prefix = snapshot.get("base_prefix")
        if storage_uri:
            compact_snapshot["storage_uri"] = storage_uri
        if base_prefix:
            compact_snapshot["base_prefix"] = base_prefix
        if not compact_snapshot:
            for key in (
                "input_work_uri",
                "input_out_uri",
                "output_work_uri",
                "output_out_uri",
                "base_prefix",
            ):
                value = snapshot.get(key)
                if value:
                    compact_snapshot[key] = value
        if compact_snapshot:
            out["EXEC_SNAPSHOT"] = compact_snapshot
        else:
            out.pop("EXEC_SNAPSHOT", None)

    out.pop("EXEC_CONTEXT", None)
    out.pop("EXEC_RUNTIME_CONFIG", None)
    out.pop("BUNDLE_ROOT_HOST", None)
    out.pop("BUNDLE_ROOT_CONTAINER", None)
    return out


__all__ = [
    "build_external_runtime_base_env",
    "prepare_external_runtime_globals",
]
