# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from typing import Any

def default_tool_event_policies() -> list[dict[str, Any]]:
    """Return the default policy bindings for ordinary ReAct tools.

    Ordinary tools use the existing ReAct block builders. These bindings make
    them explicit event sources in every already-supported phase while leaving
    projection unchanged unless a source adds a more specific policy.
    """
    return [
        {
            "react_phase": "block_production",
            "event_policy_id": "react.block_production.tool_default",
        },
        {
            "react_phase": "timeline_projection",
            "event_policy_id": "react.timeline_projection.identity",
        },
        {
            "react_phase": "compaction_projection",
            "event_policy_id": "react.compaction_projection.identity",
        },
    ]


def exploration_source_policies() -> list[dict[str, Any]]:
    """Return policy bindings for search/fetch result tools.

    Search/fetch tools are ordinary ReAct tools plus an additional
    `block_production` policy that extracts exploration rows from the raw
    `{ok,error,ret}` result target and asks the caller to merge them into the
    shared sources pool. They also produce the ordinary result item consumed by
    the shared ReAct artifact/result builder, matching the existing
    `external.py` visible result path.
    """
    return [
        *default_tool_event_policies(),
        {
            "react_phase": "block_production",
            "event_policy_id": "react.block_production.exploration_results",
        },
        {
            "react_phase": "block_production",
            "event_policy_id": "react.block_production.generic_result_item",
        },
    ]


def structured_result_source_policies() -> list[dict[str, Any]]:
    """Return policy bindings for non-write structured-result tools.

    Browser-style tools return an ordinary JSON/text result and may also return
    explicit `{artifact_type:"files"}` rows. These policies produce the same
    primary result item and declared-file items that the old `external.py` path
    derived before handing them to the shared artifact/result builders.
    """
    return [
        *default_tool_event_policies(),
        {
            "react_phase": "block_production",
            "event_policy_id": "react.block_production.generic_result_item",
        },
        {
            "react_phase": "block_production",
            "event_policy_id": "react.block_production.declared_file_items",
        },
    ]


def write_tool_source_policies() -> list[dict[str, Any]]:
    """Return policy bindings for rendering/write tools.

    Rendering tools usually return success with no payload; the produced file is
    identified by the requested `params.path`. The write-tool policy mirrors the
    existing write branch by turning that path into the primary result item for
    the shared artifact/result builder. Rendering-specific input preparation is
    owned by the tool-call validation policy declared by `rendering_tools.py`.
    """
    return [
        *default_tool_event_policies(),
        {
            "react_phase": "tool_call_validation",
            "event_policy_id": "rendering_tools.tool_call_validation.prepare_inputs",
        },
        {
            "react_phase": "block_production",
            "event_policy_id": "react.block_production.write_tool_result",
        },
        {
            "react_phase": "block_production",
            "event_policy_id": "react.block_production.declared_file_items",
        },
    ]


def composite_artifact_source_policies() -> list[dict[str, Any]]:
    """Return policy bindings for tools that may return several result surfaces.

    A composite result can contain ordinary text/json, hosted files, snapshot
    refs, ANNOUNCE candidates, and exploration rows in one `{ok,error,ret}`
    envelope. Each block-production policy inspects the same mutable target and
    appends only the surface it owns.
    """
    return [
        *default_tool_event_policies(),
        {
            "react_phase": "block_production",
            "event_policy_id": "react.block_production.hosted_artifacts",
        },
        {
            "react_phase": "block_production",
            "event_policy_id": "react.block_production.snapshot_refs",
        },
        {
            "react_phase": "block_production",
            "event_policy_id": "react.block_production.announce_candidates",
        },
    ]
