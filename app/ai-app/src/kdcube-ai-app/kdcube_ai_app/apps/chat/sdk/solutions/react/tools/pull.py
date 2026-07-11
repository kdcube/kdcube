# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Dict, List

import logging
import pathlib

from kdcube_ai_app.apps.chat.sdk.solutions.react.solution_workspace import (
    resolve_logical_artifact,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.artifacts import (
    ARTIFACT_NAMESPACE_SNAPSHOTS,
    REACT_FILE_REF_PREFIX,
    localize_conversation_ref,
    qualify_conversation_ref,
    split_logical_artifact_ref,
)
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
    notice_block,
    tc_result_path,
    deliver_file_artifact,
    enrich_artifact_file_metadata,
    _safe_json,
)
from kdcube_ai_app.tools.serialization import json_safe as _json_safe_value

LOGGER = logging.getLogger("kdcube.react.pull")

TOOL_SPEC = {
    "id": "react.pull",
    "purpose": (
        "Materialize artifact refs locally under OUT_DIR and return the paths that other tools should use next. "
        "conv:fi: refs already belong to the ReAct artifact model and have the normal logical/physical path rules. "
        "Externally owned refs such as cnv:, mem:, or task: may appear in timeline events, snapshots, or tool results. "
        "They are opaque owner handles resolved only when a registered namespace rehoster is available. "
        "conv:ev: refs identify event objects on the timeline; they are not artifact refs and are not accepted by react.pull. "
        "When an event/object shows object_ref, pass that object_ref. "
        "When an event points to bytes or a snapshot body through another field, pass that referenced artifact ref. "
        "Unsupported namespaces are reported by the pull result. "
        "For an externally owned ref, react.pull calls the registered namespace rehoster, copies the artifact into a ReAct artifact surface, "
        "and returns the materialized conv:fi: logical_path plus physical_path. "
        "The rehoster chooses whether the artifact lands into files, git/snapshots, external attachments, or another supported ReAct artifact surface. "
        "Use those returned paths with react.read, react.rg, exec/code, or later artifact operations. "
        "Use this for versioned files/folders you need locally as historical reference material. "
        "Pulled content stays under its historical turn root as reference material; checkout copies versioned files into the editable current-turn workspace. "
        "Folder/slice pulls are supported for conv:fi:conv_<conversation_id>.turn_<id>.git/projects/<scope-or-subtree>. "
        "Snapshot subtree pulls are available when the backing implementation reports snapshot subtree support. "
        "Produced files, user attachments, external-event attachments, and hosted binaries require exact refs. "
        "The conv_<conversation_id> segment names the conversation the ref lives in; use refs exactly as supplied. "
        "Choose share by where the pulled file goes next. "
        "You read/analyze/edit the content yourself (react.read, react.rg, exec/code, rendering) -> share=false (default); the pull is local reference material and the user sees no file from it. "
        "The USER should receive the file itself as a download (they asked for the file, or the deliverable IS this binary) -> share=true; the file is hosted and delivered to the user (Files tab). "
        "The file goes to ANOTHER SERVICE (mail/slack attachment, host_file into a namespace) -> share=false; pass the returned logical_path/physical_path in that action's file field (e.g. attachment_paths, file_ref) — the service reads the bytes itself. "
        "share delivers exactly ONE file: pull a single exact file ref with share=true. A folder/subtree pull, or a pull of several "
        "refs at once, is NOT shared (the call reports that share was not applied)."
    ),
    "args": {
        "share": (
            "bool. Optional, default false. share=false materializes for your own use; the user receives no file. "
            "share=true delivers the ONE pulled file to the user as a download (hosted, visibility=external, kind=file) — "
            "use it when the user should get the file itself. It applies to a single exact file ref only — "
            "a folder/subtree pull or a multi-ref pull is never delivered. "
            "Sending a pulled file to another service needs no share: pass the returned path to that action's file field."
        ),
        "paths": (
            "list[str] of artifact refs to materialize locally. Each item is either a normal conv:fi: ref or an externally owned ref shown by the runtime. "
            "Allowed conv:fi: refs include conv:fi:conv_<conversation_id>.turn_<id>.git/projects/<path> (exact file or subtree), "
            "conv:fi:conv_<conversation_id>.turn_<id>.git/snapshots/<path> (exact text snapshot or subtree when git-backed), "
            "conv:fi:conv_<conversation_id>.turn_<id>.files/<file> (exact produced file), "
            "conv:fi:conv_<conversation_id>.turn_<id>.user.attachments/<file> (exact file only), "
            "and conv:fi:conv_<conversation_id>.turn_<id>.external.<event_kind>.attachments/<event_id>/<file> (exact file only). "
            "The conv_<conversation_id> segment names the conversation the ref lives in; pass refs exactly as supplied. "
            "External namespaces such as cnv:, mem:, or task: are accepted only when a namespace rehoster is registered. "
            "conv:ev: timeline event refs are not artifact refs."
        ),
    },
    "returns": (
        "JSON object with requested refs and compact pulled summaries. "
        "Folder pulls are grouped by logical_root/physical_root with file_count, bounded tree, and path_rule. "
        "Exact file pulls return one logical_path/physical_path item. "
        "Externally owned refs return object_ref plus the resolved/rehosted conv:fi: logical_path, physical_path, materialization scope (surface), mime, size_bytes, and file_count when available. "
        "When share=true, each delivered file is also listed under shared with its logical_path. "
        "user_delivery reports what the user received from this call. "
        "Diagnostics such as missing, invalid, and errors are included only when non-empty."
    ),
}


def _shareable_file_rows(pulled: List[Any]) -> List[Dict[str, Any]]:
    """Single-file pulled rows eligible for user delivery (exclude folder/subtree roots)."""
    rows: List[Dict[str, Any]] = []
    for row in pulled or []:
        if not isinstance(row, Mapping):
            continue
        logical_path = str(row.get("logical_path") or "").strip()
        physical_path = str(row.get("physical_path") or "").strip()
        if logical_path and physical_path:
            rows.append(dict(row))
    return rows


def _file_artifact_from_row(row: Mapping) -> Dict[str, Any]:
    physical_path = str(row.get("physical_path") or "").strip()
    logical_path = str(row.get("logical_path") or "").strip()
    filename = physical_path.split("/")[-1] if physical_path else (logical_path.split("/")[-1] if logical_path else "")
    mime = str(row.get("mime") or "").strip()
    value: Dict[str, Any] = {
        "type": "file",
        "path": physical_path,
        "filename": filename,
    }
    if mime:
        value["mime"] = mime
    if row.get("size_bytes") is not None:
        value["size_bytes"] = row.get("size_bytes")
    return {
        "artifact_id": logical_path or filename,
        "tool_id": "react.pull",
        "visibility": "external",
        "artifact_kind": "file",
        "channel": "file",
        "value": value,
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


async def handle_react_pull(*, react: Any = None, ctx_browser: Any, state: Dict[str, Any], tool_call_id: str) -> Dict[str, Any]:
    last_decision = state.get("last_decision") or {}
    tool_call = last_decision.get("tool_call") or {}
    tool_id = "react.pull"
    raw_params = tool_call.get("params") or {}
    params = raw_params if isinstance(raw_params, dict) else {}
    share_requested = bool(params.get("share")) if isinstance(params, dict) else False
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
    namespace_errors: List[Any] = []
    accepted_by_conversation: Dict[str, List[str]] = {}
    seen_physical: set[tuple[str, str]] = set()
    workspace_impl = get_workspace_implementation(getattr(ctx_browser, "runtime_ctx", None))
    event_sources = getattr(getattr(ctx_browser, "runtime_ctx", None), "event_sources", None)
    current_conversation_id = str(getattr(getattr(ctx_browser, "runtime_ctx", None), "conversation_id", "") or "").strip()
    for req in requested:
        # A ref qualified with the current conversation's scope segment maps to
        # the local physical layout (turn_<id>/...); refs scoped to another
        # conversation keep their conv_<id>/ physical layout.
        raw = localize_conversation_ref(req["path"], current_conversation_id)
        embedded_conversation_id, _, _, _ = split_logical_artifact_ref(raw)
        source_conversation_id = str(embedded_conversation_id or "").strip()
        if not raw.startswith(REACT_FILE_REF_PREFIX):
            namespace = raw.partition(":")[0].strip() if ":" in raw else ""
            rehoster = getattr(event_sources, "namespace_rehoster", lambda _namespace: None)(namespace) if namespace else None
            if rehoster is None:
                registered_namespaces: List[str] = []
                try:
                    registered_namespaces = [
                        str(item.get("namespace") or "").strip()
                        for item in (event_sources.list_namespace_rehosters() if event_sources is not None else [])
                        if str(item.get("namespace") or "").strip()
                    ]
                except Exception:
                    registered_namespaces = []
                LOGGER.warning(
                    "react.pull namespace rehoster missing: path=%s namespace=%s event_sources_bound=%s registered_namespaces=%s",
                    raw,
                    namespace or "<missing>",
                    event_sources is not None,
                    registered_namespaces,
                )
                invalid.append({
                    "path": raw,
                    **({"conversation_id": source_conversation_id} if source_conversation_id else {}),
                    "reason": "react.pull accepts conv:fi: refs or registered artifact namespaces",
                    "namespace": namespace,
                    "event_sources_bound": event_sources is not None,
                    "registered_namespaces": registered_namespaces,
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
            for item in result.get("errors") or []:
                if isinstance(item, Mapping):
                    namespace_errors.append(dict(item))
                elif item:
                    namespace_errors.append(str(item))
            for item in result.get("missing") or []:
                if isinstance(item, Mapping):
                    namespace_missing.append(dict(item))
                else:
                    namespace_missing.append({"object_ref": raw, "missing": str(item or raw)})
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
        snapshot_marker = f"/{ARTIFACT_NAMESPACE_SNAPSHOTS}/"
        snapshot_can_use_workspace = snapshot_marker in physical and workspace_impl == WORKSPACE_IMPLEMENTATION_GIT
        if "/attachments/" in physical or "/files/" in physical or (snapshot_marker in physical and not snapshot_can_use_workspace):
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
                    if snapshot_marker in physical
                    else "file_pulls_require_exact_file_ref"
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

    def _qualify_row_refs(rows: List[Any]) -> None:
        """Conversation-qualify the logical refs a row reports back to the model."""
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            for key in ("logical_path", "logical_root"):
                val = str(row.get(key) or "").strip()
                if val:
                    row[key] = qualify_conversation_ref(val, current_conversation_id)
            rule = row.get("path_rule")
            if isinstance(rule, dict):
                rule_logical = str(rule.get("logical") or "").strip()
                if rule_logical:
                    rule["logical"] = qualify_conversation_ref(rule_logical, current_conversation_id)

    _qualify_row_refs(pulled)
    pulled_object_refs = state.setdefault("pulled_object_refs", {})
    if not isinstance(pulled_object_refs, dict):
        pulled_object_refs = {}
        state["pulled_object_refs"] = pulled_object_refs
    pulled_logical_refs = state.setdefault("pulled_logical_refs", {})
    if not isinstance(pulled_logical_refs, dict):
        pulled_logical_refs = {}
        state["pulled_logical_refs"] = pulled_logical_refs
    for row in pulled:
        if not isinstance(row, Mapping):
            continue
        object_ref = str(row.get("object_ref") or row.get("source_ref") or "").strip()
        logical_path = str(row.get("logical_path") or "").strip()
        if object_ref and isinstance(row, dict):
            row["object_ref"] = object_ref
            row.pop("source_ref", None)
        if object_ref and logical_path:
            source_info = {
                "object_ref": object_ref,
                "logical_path": logical_path,
                **({"physical_path": str(row.get("physical_path") or "").strip()} if row.get("physical_path") else {}),
                **({"mime": str(row.get("mime") or "").strip()} if row.get("mime") else {}),
            }
            pulled_object_refs[object_ref] = {
                key: value for key, value in source_info.items() if key != "object_ref"
            }
            pulled_logical_refs[logical_path] = {
                key: value for key, value in source_info.items() if key != "logical_path"
            }
    hydrated_missing = _compact_path_rows(
        [str(p or "") for p in (rehost_result.get("missing") or [])],
        requested_roots=accepted_physical,
    )
    missing = namespace_missing + hydrated_missing
    _qualify_row_refs(missing)

    shared: List[str] = []
    if share_requested:
        shareable = _shareable_file_rows(pulled)
        folder_pulled = any(isinstance(r, Mapping) and r.get("logical_root") for r in pulled)
        if react is None or outdir is None:
            pass  # no hosting surface in this context; deliver is a no-op
        elif len(shareable) != 1 or folder_pulled:
            # share delivers exactly one file. A folder/subtree pull or a multi-file pull is not shared.
            notice_block(
                ctx_browser=ctx_browser,
                tool_call_id=tool_call_id,
                code="react.pull.share_single_file_only",
                message=(
                    "share delivers a single file and was not applied: this pull resolved to "
                    f"{'a folder/subtree' if folder_pulled else f'{len(shareable)} files'}. "
                    "To share, pull the one exact file ref with share=true."
                ),
                rel="result",
            )
        else:
            row = shareable[0]
            physical_path = str(row.get("physical_path") or "").strip()
            logical_path = str(row.get("logical_path") or "").strip()
            artifact = _file_artifact_from_row(row)
            try:
                enrich_artifact_file_metadata(
                    artifact=artifact,
                    outdir=outdir,
                    physical_path=physical_path,
                )
                await deliver_file_artifact(
                    react=react,
                    ctx_browser=ctx_browser,
                    artifact=artifact,
                    outdir=outdir,
                    turn_id=turn_id,
                    tool_call_id=tool_call_id,
                    artifact_path=logical_path,
                    physical_path=physical_path,
                    host=True,
                    visibility="external",
                    channel="file",
                )
                if logical_path:
                    shared.append(logical_path)
            except Exception:
                LOGGER.exception("react.pull share failed for %s", logical_path or physical_path)

    payload = {
        "requested": requested_paths,
        "pulled": pulled,
    }
    if shared:
        payload["shared"] = shared
        payload["user_delivery"] = "delivered to the user as downloadable file(s): " + ", ".join(shared)
    elif pulled:
        payload["user_delivery"] = (
            "none — these files are local reference material for this turn; the user received no file from this call. "
            "To hand ONE pulled file to the user as a download, pull its exact ref with share=true. "
            "To send a pulled file to another service, pass its logical_path/physical_path in that action's "
            "file field (e.g. attachment_paths, file_ref)."
        )
    if missing:
        payload["missing"] = missing
    if invalid:
        payload["invalid"] = invalid
    errors = list(namespace_errors) + list(rehost_result.get("errors") or [])
    if errors:
        payload["errors"] = errors
    payload = _json_safe_value(payload)
    add_block(ctx_browser, {
        "turn": turn_id,
        "type": "react.tool.result",
        "call_id": tool_call_id,
        "mime": "application/json",
        "path": tc_result_path(turn_id=turn_id, call_id=tool_call_id),
        "text": _safe_json(payload),
        "meta": {
            "tool_call_id": tool_call_id,
        },
    })
    state["last_tool_result"] = payload.get("pulled", []) if isinstance(payload, dict) else []
    return state
