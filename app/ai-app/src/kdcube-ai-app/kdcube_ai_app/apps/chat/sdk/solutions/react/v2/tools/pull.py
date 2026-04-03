# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from typing import Any, Dict, List

import json
import pathlib

from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.solution_workspace import (
    resolve_logical_artifact,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.workspace import (
    _infer_physical_from_fi,
    hydrate_workspace_paths,
    physical_to_logical_artifact_path,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.common import (
    tool_call_block,
    add_block,
    tc_result_path,
)


TOOL_SPEC = {
    "id": "react.pull",
    "purpose": (
        "Materialize selected fi: snapshot refs locally under OUT_DIR so later exec/code can use them by physical path. "
        "Use this for versioned files/folders you need locally. "
        "Folder pulls are supported only for fi:<turn_id>.files/<scope-or-subtree>. "
        "Non-workspace outputs and attachment/binary pulls must name exact refs."
    ),
    "args": {
        "paths": (
            "list[str] of fi: refs to materialize locally. "
            "Allowed: fi:<turn_id>.files/<path> (exact file or subtree), "
            "fi:<turn_id>.outputs/<file> (exact file only), "
            "fi:<turn_id>.user.attachments/<file> (exact file only), "
            "and legacy fi:<turn_id>.attachments/<file> (exact file only)."
        ),
    },
    "returns": (
        "JSON object {requested, pulled, missing, invalid, errors}. "
        "`pulled` contains logical_path, physical_path, and kind. "
        "Pulled files become available locally under OUT_DIR using those physical paths."
    ),
}


def _physical_to_logical(path: str) -> str:
    return physical_to_logical_artifact_path(path)


def _kind_for_physical(path: str) -> str:
    raw = str(path or "").strip()
    if "/attachments/" in raw:
        return "attachment"
    if "/outputs/" in raw:
        return "output"
    return "files"


async def handle_react_pull(*, ctx_browser: Any, state: Dict[str, Any], tool_call_id: str) -> Dict[str, Any]:
    last_decision = state.get("last_decision") or {}
    tool_call = last_decision.get("tool_call") or {}
    tool_id = "react.pull"
    params = tool_call.get("params") or {}
    raw_paths = params.get("paths")
    if raw_paths is None and isinstance(params, list):
        raw_paths = params
    requested_paths = [str(p).strip() for p in (raw_paths or []) if str(p).strip()]

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
    accepted_physical: List[str] = []
    seen_physical: set[str] = set()
    for raw in requested_paths:
        if not raw.startswith("fi:"):
            invalid.append({
                "path": raw,
                "reason": "react.pull accepts fi: refs only",
            })
            continue
        physical = _infer_physical_from_fi(raw)
        if not physical:
            invalid.append({
                "path": raw,
                "reason": "unresolvable_fi_ref",
            })
            continue
        if "/attachments/" in physical or "/outputs/" in physical:
            artifact = await resolve_logical_artifact(ctx_browser=ctx_browser, path=raw)
            if not isinstance(artifact, dict):
                invalid.append({
                    "path": raw,
                    "reason": "exact_artifact_pull_requires_exact_file_ref",
                })
                continue
        if physical in seen_physical:
            continue
        seen_physical.add(physical)
        accepted_physical.append(physical)

    rehost_result: Dict[str, Any]
    if not outdir:
        rehost_result = {
            "rehosted": [],
            "missing": [],
            "errors": ["missing_outdir"],
        }
    elif accepted_physical:
        rehost_result = await hydrate_workspace_paths(
            ctx_browser=ctx_browser,
            paths=accepted_physical,
            outdir=outdir,
        )
    else:
        rehost_result = {
            "rehosted": [],
            "missing": [],
            "errors": [],
        }

    pulled = []
    for physical in rehost_result.get("rehosted") or []:
        pulled.append({
            "logical_path": _physical_to_logical(physical),
            "physical_path": physical,
            "kind": _kind_for_physical(physical),
        })
    missing = []
    for physical in rehost_result.get("missing") or []:
        missing.append({
            "logical_path": _physical_to_logical(physical),
            "physical_path": physical,
            "kind": _kind_for_physical(physical),
        })

    payload = {
        "requested": requested_paths,
        "pulled": pulled,
        "missing": missing,
        "invalid": invalid,
        "errors": list(rehost_result.get("errors") or []),
    }
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
