# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Dict, List

import json
import pathlib

from kdcube_ai_app.apps.chat.sdk.solutions.react.solution_workspace import (
    resolve_logical_artifact,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.artifacts import split_logical_artifact_ref
from kdcube_ai_app.apps.chat.sdk.solutions.react.workspace import (
    WORKSPACE_IMPLEMENTATION_GIT,
    _infer_physical_from_fi,
    get_workspace_implementation,
    _tree_summary_for_relpaths,
    hydrate_workspace_paths,
    physical_to_logical_artifact_path,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.tools.common import (
    tool_call_block,
    add_block,
    tc_result_path,
)


TOOL_SPEC = {
    "id": "react.pull",
    "purpose": (
        "Materialize artifact refs locally under OUT_DIR and return the paths that other tools should use next. "
        "fi: refs already belong to the ReAct artifact model and have the normal logical/physical path rules. "
        "Externally owned refs such as cnv:, mem:, or task: may appear in timeline events, snapshots, or tool results. "
        "They are opaque owner handles resolved only when a registered namespace rehoster is available. "
        "ev: refs identify event objects on the timeline; they are not artifact refs and are not accepted by react.pull. "
        "When an event/object shows object_ref, pass that object_ref. "
        "When an event points to bytes or a snapshot body through another field, pass that referenced artifact ref. "
        "Unsupported namespaces are reported by the pull result. "
        "For an externally owned ref, react.pull calls the registered namespace rehoster, copies the artifact into a ReAct artifact surface, "
        "and returns the materialized fi: logical_path plus physical_path. "
        "The rehoster chooses whether the artifact lands as files, snapshots, external attachments, or another supported ReAct artifact surface. "
        "Use those returned paths with react.read, react.rg, exec/code, or later artifact operations. "
        "Use this for versioned files/folders you need locally as historical reference material. "
        "Pulled content stays under its historical turn root as reference material; checkout copies versioned files into the editable current-turn workspace. "
        "Folder/slice pulls are supported for fi:turn_<id>.files/<scope-or-subtree>. "
        "Snapshot subtree pulls are available when the backing implementation reports snapshot subtree support. "
        "Outputs, user attachments, external-event attachments, and hosted binaries require exact refs. "
        "An fi:conv_<conversation_id>.turn_<id>... path belongs to another conversation and is resolved in that conversation. "
        "Current-conversation fi: paths use fi:turn_<id>... without a conv_ scope segment."
    ),
    "args": {
        "paths": (
            "list[str] of artifact refs to materialize locally. Each item is either a normal fi: ref or an externally owned ref shown by the runtime. "
            "Allowed fi: refs include fi:turn_<id>.files/<path> (exact file or subtree), "
            "fi:turn_<id>.snapshots/<path> (exact text snapshot or subtree when git-backed), "
            "fi:turn_<id>.outputs/<file> (exact file only), "
            "fi:turn_<id>.user.attachments/<file> (exact file only), "
            "fi:turn_<id>.external.<event_kind>.attachments/<event_id>/<file> (exact file only), "
            "and cross-conversation fi:conv_<conversation_id>.turn_<id>... refs. "
            "External namespaces such as cnv:, mem:, or task: are accepted only when a namespace rehoster is registered. "
            "ev: timeline event refs are not artifact refs."
        ),
    },
    "returns": (
        "JSON object with requested refs and compact pulled summaries. "
        "Folder pulls are grouped by logical_root/physical_root with file_count, bounded tree, and path_rule. "
        "Exact file pulls return one logical_path/physical_path item. "
        "Externally owned refs return source_ref plus the resolved/rehosted fi: logical_path, physical_path, mime, size_bytes, and file_count when available. "
        "Diagnostics such as missing, invalid, and errors are included only when non-empty."
    ),
}


def _physical_to_logical(path: str) -> str:
    return physical_to_logical_artifact_path(path)


def _is_under_or_equal(path: str, root: str) -> bool:
    p = str(path or "").strip().strip("/")
    r = str(root or "").strip().strip("/")
    return bool(p and r and (p == r or p.startswith(f"{r}/")))


def _rel_under_root(path: str, root: str) -> str:
    p = str(path or "").strip().strip("/")
    r = str(root or "").strip().strip("/")
    if p == r:
        return ""
    if r and p.startswith(f"{r}/"):
        return p[len(r) + 1:]
    return p


def _compact_path_rows(paths: List[str], *, requested_roots: List[str]) -> List[Dict[str, Any]]:
    remaining = [str(p or "").strip().strip("/") for p in paths if str(p or "").strip()]
    summaries: List[Dict[str, Any]] = []
    seen_roots: set[str] = set()

    for root in requested_roots:
        root_norm = str(root or "").strip().strip("/")
        if not root_norm or root_norm in seen_roots:
            continue
        seen_roots.add(root_norm)
        matched = [p for p in remaining if _is_under_or_equal(p, root_norm)]
        if not matched:
            continue
        exact_only = len(matched) == 1 and matched[0] == root_norm
        if exact_only:
            summaries.append({
                "logical_path": _physical_to_logical(root_norm),
                "physical_path": root_norm,
                "file_count": 1,
            })
            continue

        rels = [_rel_under_root(p, root_norm) for p in matched]
        rels = [rel for rel in rels if rel]
        tree_summary = _tree_summary_for_relpaths(rels)
        logical_root = _physical_to_logical(root_norm)
        entry = {
            "logical_root": logical_root,
            "physical_root": root_norm,
            **tree_summary,
            "path_rule": {
                "logical": f"{logical_root}/<path shown in tree>",
                "physical": f"{root_norm}/<path shown in tree>",
            },
        }
        summaries.append(entry)

    grouped = {
        p
        for root in seen_roots
        for p in remaining
        if _is_under_or_equal(p, root)
    }
    for path in remaining:
        if path in grouped:
            continue
        summaries.append({
            "logical_path": _physical_to_logical(path),
            "physical_path": path,
            "file_count": 1,
        })

    return summaries


async def handle_react_pull(*, ctx_browser: Any, state: Dict[str, Any], tool_call_id: str) -> Dict[str, Any]:
    last_decision = state.get("last_decision") or {}
    tool_call = last_decision.get("tool_call") or {}
    tool_id = "react.pull"
    raw_params = tool_call.get("params") or {}
    params = raw_params if isinstance(raw_params, dict) else {}
    raw_paths = params.get("paths")
    if raw_paths is None and isinstance(raw_params, list):
        raw_paths = raw_params
    requested: List[Dict[str, str]] = []
    for raw_path in (raw_paths or []):
        if isinstance(raw_path, dict):
            path = str(raw_path.get("path") or "").strip()
            if not path:
                continue
            requested.append({"path": path})
            continue
        path = str(raw_path).strip()
        if path:
            requested.append({"path": path})
    requested_paths = [req["path"] for req in requested]

    turn_id = (ctx_browser.runtime_ctx.turn_id or "")
    tool_call_block(
        ctx_browser=ctx_browser,
        tool_call_id=tool_call_id,
        tool_id=tool_id,
        payload={
            "tool_id": tool_id,
            "tool_call_id": tool_call_id,
            "params": tool_call.get("params") or {},
        },
    )

    outdir_raw = str(state.get("outdir") or getattr(getattr(ctx_browser, "runtime_ctx", None), "outdir", "") or "").strip()
    outdir = pathlib.Path(outdir_raw) if outdir_raw else None

    invalid: List[Dict[str, Any]] = []
    namespace_materialized: List[Dict[str, Any]] = []
    namespace_rehosted: List[str] = []
    namespace_missing: List[Dict[str, Any]] = []
    namespace_errors: List[str] = []
    accepted_by_conversation: Dict[str, List[str]] = {}
    seen_physical: set[tuple[str, str]] = set()
    workspace_impl = get_workspace_implementation(getattr(ctx_browser, "runtime_ctx", None))
    event_sources = getattr(getattr(ctx_browser, "runtime_ctx", None), "event_sources", None)
    for req in requested:
        raw = req["path"]
        embedded_conversation_id, _, _, _ = split_logical_artifact_ref(raw)
        source_conversation_id = str(embedded_conversation_id or "").strip()
        if not raw.startswith("fi:"):
            namespace = raw.partition(":")[0].strip() if ":" in raw else ""
            rehoster = getattr(event_sources, "namespace_rehoster", lambda _namespace: None)(namespace) if namespace else None
            if rehoster is None:
                invalid.append({
                    "path": raw,
                    **({"conversation_id": source_conversation_id} if source_conversation_id else {}),
                    "reason": "react.pull accepts fi: refs or registered artifact namespaces",
                })
                continue
            if not outdir:
                namespace_errors.append("missing_outdir")
                continue
            result = await event_sources.rehost_namespace_ref(
                raw,
                ctx_browser=ctx_browser,
                outdir=outdir,
                tool_call_id=tool_call_id,
                state=state,
                tool_id=tool_id,
            )
            namespace_materialized.extend(
                dict(row)
                for row in (result.get("materialized") or [])
                if isinstance(row, Mapping)
            )
            namespace_rehosted.extend(
                str(path or "").strip()
                for path in (result.get("rehosted") or [])
                if str(path or "").strip()
            )
            namespace_errors.extend(str(item) for item in (result.get("errors") or []) if str(item))
            for item in result.get("missing") or []:
                if isinstance(item, Mapping):
                    namespace_missing.append(dict(item))
                else:
                    namespace_missing.append({"source_ref": raw, "missing": str(item or raw)})
            for item in result.get("invalid") or []:
                if isinstance(item, Mapping):
                    invalid.append(dict(item))
            continue
        physical = _infer_physical_from_fi(raw)
        if not physical:
            invalid.append({
                "path": raw,
                **({"conversation_id": source_conversation_id} if source_conversation_id else {}),
                "reason": "unresolvable_fi_ref",
            })
            continue
        snapshot_can_use_workspace = "/snapshots/" in physical and workspace_impl == WORKSPACE_IMPLEMENTATION_GIT
        if "/attachments/" in physical or "/outputs/" in physical or ("/snapshots/" in physical and not snapshot_can_use_workspace):
            artifact = await resolve_logical_artifact(
                ctx_browser=ctx_browser,
                path=raw,
                conversation_id=source_conversation_id or None,
            )
            if not isinstance(artifact, dict):
                reason = (
                    "attachment_pulls_require_exact_file_ref"
                    if "/attachments/" in physical
                    else "snapshot_pulls_require_exact_file_ref"
                    if "/snapshots/" in physical
                    else "output_pulls_require_exact_file_ref"
                )
                invalid.append({
                    "path": raw,
                    **({"conversation_id": source_conversation_id} if source_conversation_id else {}),
                    "reason": reason,
                })
                continue
        seen_key = (source_conversation_id, physical)
        if seen_key in seen_physical:
            continue
        seen_physical.add(seen_key)
        accepted_by_conversation.setdefault(source_conversation_id, []).append(physical)

    accepted_physical = [
        physical
        for paths_for_conversation in accepted_by_conversation.values()
        for physical in paths_for_conversation
    ]
    rehost_result: Dict[str, Any]
    if not outdir:
        rehost_result = {
            "rehosted": [],
            "missing": [],
            "errors": ["missing_outdir"],
        }
    elif accepted_physical:
        rehost_result = {"rehosted": [], "missing": [], "errors": []}
        for source_conversation_id, physical_paths in accepted_by_conversation.items():
            group_result = await hydrate_workspace_paths(
                ctx_browser=ctx_browser,
                paths=physical_paths,
                outdir=outdir,
                conversation_id=source_conversation_id or None,
            )
            for key in ("rehosted", "missing", "errors"):
                rehost_result[key].extend(list(group_result.get(key) or []))
    else:
        rehost_result = {
            "rehosted": [],
            "missing": [],
            "errors": [],
        }

    hydrated_pulled = _compact_path_rows(
        [str(p or "") for p in (rehost_result.get("rehosted") or [])],
        requested_roots=accepted_physical,
    )
    namespace_fallback_pulled = []
    if namespace_rehosted and not namespace_materialized:
        namespace_fallback_pulled = _compact_path_rows(
            [str(p or "") for p in namespace_rehosted],
            requested_roots=namespace_rehosted,
        )
    pulled = namespace_materialized + namespace_fallback_pulled + hydrated_pulled
    pulled_source_refs = state.setdefault("pulled_source_refs", {})
    if not isinstance(pulled_source_refs, dict):
        pulled_source_refs = {}
        state["pulled_source_refs"] = pulled_source_refs
    for row in pulled:
        if not isinstance(row, Mapping):
            continue
        source_ref = str(row.get("source_ref") or "").strip()
        logical_path = str(row.get("logical_path") or "").strip()
        if source_ref and logical_path:
            pulled_source_refs[source_ref] = {
                "logical_path": logical_path,
                **({"physical_path": str(row.get("physical_path") or "").strip()} if row.get("physical_path") else {}),
                **({"mime": str(row.get("mime") or "").strip()} if row.get("mime") else {}),
            }
    hydrated_missing = _compact_path_rows(
        [str(p or "") for p in (rehost_result.get("missing") or [])],
        requested_roots=accepted_physical,
    )
    missing = namespace_missing + hydrated_missing

    payload = {
        "requested": requested_paths,
        "pulled": pulled,
    }
    if missing:
        payload["missing"] = missing
    if invalid:
        payload["invalid"] = invalid
    errors = list(namespace_errors) + list(rehost_result.get("errors") or [])
    if errors:
        payload["errors"] = errors
    add_block(ctx_browser, {
        "turn": turn_id,
        "type": "react.tool.result",
        "call_id": tool_call_id,
        "mime": "application/json",
        "path": tc_result_path(turn_id=turn_id, call_id=tool_call_id),
        "text": json.dumps(payload, ensure_ascii=False, indent=2),
        "meta": {
            "tool_call_id": tool_call_id,
        },
    })
    state["last_tool_result"] = pulled
    return state
