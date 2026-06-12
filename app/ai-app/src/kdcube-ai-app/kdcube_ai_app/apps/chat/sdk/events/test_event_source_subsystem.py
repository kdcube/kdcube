from __future__ import annotations

from types import ModuleType, SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.events import (
    EventSourceSubsystem,
    artifact_namespace_rehoster,
    event_source,
    event_source_declaration,
    event_source_resolver,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.live_events import resolve_reactive_iteration_credit
from kdcube_ai_app.apps.chat.sdk.solutions.react.events import (
    REACT_FOLLOWUP_EVENT_SOURCE_ID,
    REACT_MEMSEARCH_EVENT_SOURCE_ID,
    REACT_STEER_EVENT_SOURCE_ID,
    REACT_USER_ATTACHMENT_EVENT_SOURCE_ID,
    REACT_WRITE_EVENT_SOURCE_ID,
    TIMELINE_SEGMENT_META_KEY,
    announce_event_policy,
    apply_event_source_transformers,
    composite_artifact_source_policies,
    core as react_core_events,
    produce_event_source_announce_blocks,
    stamp_event_identity_many,
    timeline_projection_policy,
)


def _module(name: str, **attrs):
    mod = ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


def test_decorator_discovers_metadata_only_source():
    @timeline_projection_policy(event_policy_id="react.timeline_projection.keep_latest_three")
    def keep_latest_three(timeline, **_):
        """Keep only the latest three blocks in the mutable timeline."""
        del timeline[:-3]
        return timeline

    @event_source(
        event_source_id="bundle.demo.event",
        policies=[
            {
                "react_phase": "timeline_projection",
                "event_policy_id": "react.timeline_projection.keep_latest_three",
            },
        ],
    )
    class DemoEvent:
        pass

    subsystem = EventSourceSubsystem(modules=[{
        "mod": _module("demo_events", DemoEvent=DemoEvent, keep_latest_three=keep_latest_three),
        "alias": "demo",
    }])

    source = subsystem.by_event_source_id("bundle.demo.event")
    assert source is not None
    assert [binding.react_phase for binding in source.react.timeline_projection] == ["timeline_projection"]
    assert [binding.event_policy_id for binding in source.react.timeline_projection] == ["react.timeline_projection.keep_latest_three"]
    assert subsystem.by_block({
        "event_source_id": "bundle.demo.event",
        "event_id": "evt_1",
        "type": "event.external",
    }) == source
    assert subsystem.by_block({"event_id": "evt_1", "type": "event.external"}) is None
    blocks = [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}]
    subsystem.apply_react_phase_policies("timeline_projection", "bundle.demo.event", blocks)
    assert blocks == [{"id": 2}, {"id": 3}, {"id": 4}]


@pytest.mark.asyncio
async def test_event_source_resolver_decorator_routes_ref_without_reader():
    calls = []

    @event_source_resolver(namespace="task")
    async def resolve_task_ref(ref, namespace, key, **_):
        calls.append({"ref": ref, "namespace": namespace, "key": key})
        return {
            "event_source_id": "named_services.task",
            "object_ref": ref,
            "object_kind": "task.issue",
        }

    subsystem = EventSourceSubsystem(modules=[{
        "mod": _module("task_resolver_events", resolve_task_ref=resolve_task_ref),
    }])

    resolved = await subsystem.resolve_event_source_for_ref("task:issue:BUG-123")

    assert calls == [{"ref": "task:issue:BUG-123", "namespace": "task", "key": "issue:BUG-123"}]
    assert resolved["ok"] is True
    assert resolved["event_source_id"] == "named_services.task"
    assert resolved["object_kind"] == "task.issue"


@pytest.mark.asyncio
async def test_artifact_namespace_rehoster_is_discovered_and_invoked(tmp_path):
    @artifact_namespace_rehoster(namespace="cnv")
    async def canvas_rehoster(*, ref, key, outdir, **_):
        target = tmp_path / "out" / f"{key}.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ok", encoding="utf-8")
        return {
            "materialized": [{
                "source_ref": ref,
                "logical_path": "fi:turn_1.files/cnv/demo.txt",
                "physical_path": "turn_1/files/cnv/demo.txt",
            }]
        }

    subsystem = EventSourceSubsystem(modules=[{
        "mod": _module("namespace_rehosters", canvas_rehoster=canvas_rehoster),
    }])

    assert subsystem.namespace_rehoster("cnv") is not None
    result = await subsystem.rehost_namespace_ref(
        "cnv:demo",
        ctx_browser=SimpleNamespace(),
        outdir=tmp_path / "out",
    )

    assert result["errors"] == []
    assert result["materialized"] == [{
        "source_ref": "cnv:demo",
        "logical_path": "fi:turn_1.files/cnv/demo.txt",
        "physical_path": "turn_1/files/cnv/demo.txt",
        "file_count": 1,
    }]
    assert result["rehosted"] == ["turn_1/files/cnv/demo.txt"]


def test_module_can_declare_multiple_event_sources():
    @event_source(
        event_source_id="bundle.record.created",
        policies=[
            {
                "react_phase": "timeline_projection",
                "event_policy_id": "react.timeline_projection.identity",
                "params": {"limit": 2},
            },
        ],
    )
    class RecordCreated:
        pass

    @event_source(
        event_source_id="bundle.record.state_changed",
        policies=[
            {
                "react_phase": "timeline_projection",
                "event_policy_id": "react.timeline_projection.identity",
                "params": {"key": "data.record_id"},
            },
        ],
    )
    class RecordStateChanged:
        pass

    subsystem = EventSourceSubsystem(modules=[{
        "mod": _module(
            "record_events",
            RecordCreated=RecordCreated,
            RecordStateChanged=RecordStateChanged,
        ),
        "alias": "records",
    }])

    assert subsystem.by_event_source_id("bundle.record.created") is not None
    assert subsystem.by_event_source_id("bundle.record.state_changed") is not None
    assert {item["event_source_id"] for item in subsystem.list_sources()} == {
        "bundle.record.created",
        "bundle.record.state_changed",
    }


def test_list_event_sources_discovers_declarations():
    @timeline_projection_policy(event_policy_id="react.timeline_projection.keep_latest_per_story")
    def keep_latest_per_story(timeline, **_):
        """Keep the latest block per data.story_id in the mutable timeline."""
        seen = set()
        kept = []
        for block in reversed(timeline):
            story_id = str(((block.get("data") or {}) if isinstance(block, dict) else {}).get("story_id") or "")
            if story_id in seen:
                continue
            seen.add(story_id)
            kept.append(block)
        timeline[:] = list(reversed(kept))
        return timeline

    def list_event_sources():
        return [
            event_source_declaration(
                event_source_id="bundle.snapshot",
                policies=[
                    {
                        "react_phase": "timeline_projection",
                        "event_policy_id": "react.timeline_projection.keep_latest_per_story",
                    },
                ],
            )
        ]

    subsystem = EventSourceSubsystem(modules=[{
        "mod": _module(
            "demo_sources",
            list_event_sources=list_event_sources,
            keep_latest_per_story=keep_latest_per_story,
        ),
        "alias": "demo",
    }])

    source = subsystem.by_event_source_id("bundle.snapshot")
    assert source is not None
    assert [binding.event_policy_id for binding in source.react.timeline_projection] == ["react.timeline_projection.keep_latest_per_story"]
    blocks = [
        {"id": 1, "data": {"story_id": "a"}},
        {"id": 2, "data": {"story_id": "a"}},
        {"id": 3, "data": {"story_id": "b"}},
    ]
    subsystem.apply_react_phase_policies("timeline_projection", "bundle.snapshot", blocks)
    assert blocks == [
        {"id": 2, "data": {"story_id": "a"}},
        {"id": 3, "data": {"story_id": "b"}},
    ]


def test_event_source_reactivity_defaults_survive_discovery_and_listing():
    def list_event_sources():
        return [
            event_source_declaration(
                event_source_id="bundle.wizard.assistance.requested",
                policies=[],
                kind="react.external",
                reactive=True,
                iteration_credit=2,
                description="Wizard assistance request is authored as a reactive occurrence by default.",
            )
        ]

    subsystem = EventSourceSubsystem(modules=[{
        "mod": _module("wizard_sources", list_event_sources=list_event_sources),
        "alias": "wizard",
    }])

    source = subsystem.by_event_source_id("bundle.wizard.assistance.requested")
    assert source is not None
    assert source.reactive is True
    assert source.iteration_credit == 2
    assert source.to_dict()["reactive"] is True
    assert source.to_dict()["iteration_credit"] == 2


def test_web_tools_event_sources_use_alias_and_namespaced_policies():
    from kdcube_ai_app.apps.chat.sdk.tools import web_tools

    subsystem = EventSourceSubsystem(modules=[{"mod": web_tools, "alias": "web_tools"}])

    search = subsystem.by_event_source_id("web_tools.web_search")
    fetch = subsystem.by_event_source_id("web_tools.web_fetch")
    assert search is not None
    assert fetch is not None
    assert [binding.react_phase for binding in search.react.block_production] == [
        "block_production",
        "block_production",
        "block_production",
    ]
    assert [binding.event_policy_id for binding in search.react.block_production] == [
        "react.block_production.tool_default",
        "react.block_production.exploration_results",
        "react.block_production.generic_result_item",
    ]
    assert [binding.event_policy_id for binding in search.react.timeline_projection] == [
        "react.timeline_projection.identity",
    ]
    assert [binding.event_policy_id for binding in search.react.compaction_projection] == [
        "react.compaction_projection.identity",
    ]
    assert subsystem.should_merge_to_sources_pool("web_tools.web_search") is True
    assert subsystem.should_merge_to_sources_pool("web_tools.web_fetch") is True


def test_exploration_block_production_mutates_composite_target_accumulator():
    from kdcube_ai_app.apps.chat.sdk.tools import web_tools

    subsystem = EventSourceSubsystem(modules=[{"mod": web_tools, "alias": "web_tools"}])
    target = {
        "tool_id": "web_tools.web_search",
        "ok": True,
        "error": None,
        "ret": [
            {"title": "A", "url": "https://example.test/a", "content": "alpha"},
            {"title": "B", "url": "https://example.test/b", "content": "beta"},
        ],
        "blocks": [{"type": "react.tool.call"}],
        "source_rows": [],
        "artifact_rows": [{"path": "report.pdf"}],
        "snapshot_refs": ["fi:turn_1.snapshots/current.yaml"],
        "announce_candidates": [{"text": "candidate"}],
    }

    subsystem.apply_react_phase_policies("block_production", "web_tools.web_search", target)

    assert [row["url"] for row in target["source_rows"]] == [
        "https://example.test/a",
        "https://example.test/b",
    ]
    assert target["source_rows_merge"] is True
    assert target["result_items_produced"] is True
    assert target["result_items"] == [
        {
            "artifact_id": "web_tools.web_search",
            "output": [
                {"title": "A", "url": "https://example.test/a", "content": "alpha"},
                {"title": "B", "url": "https://example.test/b", "content": "beta"},
            ],
            "summary": "",
            "error": None,
            "artifact_kind": "file",
            "artifact_path_mode": "sources_pool",
        }
    ]
    assert target["blocks"] == [{"type": "react.tool.call"}]
    assert target["artifact_rows"] == [{"path": "report.pdf"}]
    assert target["snapshot_refs"] == ["fi:turn_1.snapshots/current.yaml"]
    assert target["announce_candidates"] == [{"text": "candidate"}]


def test_generic_result_item_policy_produces_existing_primary_item_shape():
    @event_source(
        event_source_id="bundle.browserish",
        policies=[
            {
                "react_phase": "block_production",
                "event_policy_id": "react.block_production.tool_default",
            },
            {
                "react_phase": "block_production",
                "event_policy_id": "react.block_production.generic_result_item",
            },
        ],
    )
    class BrowserishTool:
        pass

    subsystem = EventSourceSubsystem(modules=[{"mod": _module("browserish_events", BrowserishTool=BrowserishTool)}])
    target = {
        "tool_id": "bundle.browserish",
        "ret": {"ok": True, "page": {"title": "Demo"}},
        "summary": "Page loaded",
        "blocks": [],
        "result_items": [],
        "source_rows": [],
        "artifact_rows": [],
        "declared_file_items": [],
        "snapshot_refs": [],
        "announce_candidates": [],
    }

    subsystem.apply_react_phase_policies("block_production", "bundle.browserish", target)

    assert target["result_items_produced"] is True
    assert target["result_items"] == [
        {
            "artifact_id": "bundle.browserish",
            "output": {"ok": True, "page": {"title": "Demo"}},
            "summary": "Page loaded",
            "error": None,
        }
    ]


def test_generic_result_item_policy_produces_error_notice_rows():
    @event_source(
        event_source_id="bundle.browserish",
        policies=[
            {
                "react_phase": "block_production",
                "event_policy_id": "react.block_production.tool_default",
            },
            {
                "react_phase": "block_production",
                "event_policy_id": "react.block_production.generic_result_item",
            },
        ],
    )
    class BrowserishTool:
        pass

    subsystem = EventSourceSubsystem(modules=[{"mod": _module("browserish_error_events", BrowserishTool=BrowserishTool)}])
    target = {
        "tool_id": "bundle.browserish",
        "ret": None,
        "summary": "open failed",
        "call_error": {
            "code": "FileNotFoundError",
            "message": "missing.html",
            "where": "bundle.browserish",
        },
        "blocks": [],
        "result_items": [],
    }

    subsystem.apply_react_phase_policies("block_production", "bundle.browserish", target)

    assert target["result_items_produced"] is True
    assert target["notice_rows_produced"] is True
    assert [row["code"] for row in target["notice_rows"]] == [
        "tool_call_error",
        "tool_result_error",
    ]


def test_write_tool_result_policy_uses_requested_path_on_success():
    @event_source(
        event_source_id="rendering.write_pdf",
        policies=[
            {
                "react_phase": "block_production",
                "event_policy_id": "react.block_production.tool_default",
            },
            {
                "react_phase": "block_production",
                "event_policy_id": "react.block_production.write_tool_result",
            },
        ],
    )
    class WriteTool:
        pass

    subsystem = EventSourceSubsystem(modules=[{"mod": _module("write_events", WriteTool=WriteTool)}])
    target = {
        "tool_id": "rendering.write_pdf",
        "ret": None,
        "final_params": {"path": "turn_1/outputs/report.pdf"},
        "summary": "",
        "blocks": [],
        "result_items": [],
        "source_rows": [],
        "artifact_rows": [],
        "declared_file_items": [],
        "snapshot_refs": [],
        "announce_candidates": [],
    }

    subsystem.apply_react_phase_policies("block_production", "rendering.write_pdf", target)

    assert target["result_items_produced"] is True
    assert target["result_items"] == [
        {
            "artifact_id": "rendering.write_pdf",
            "output": "turn_1/outputs/report.pdf",
            "summary": "",
            "error": None,
            "artifact_kind": "file",
            "visibility": "external",
            "write_artifact": True,
            "analyze_write_output": True,
            "emit_hosted_file": True,
            "resolve_file_path": True,
            "default_mime": "application/octet-stream",
        }
    ]


def test_composite_block_production_policies_append_owned_surfaces():
    @event_source(
        event_source_id="bundle.composite",
        policies=composite_artifact_source_policies(),
    )
    class CompositeTool:
        pass

    subsystem = EventSourceSubsystem(modules=[{"mod": _module("composite_events", CompositeTool=CompositeTool)}])
    target = {
        "ok": True,
        "error": None,
        "ret": {
            "hosted_artifacts": [{"path": "files/report.pdf"}],
            "snapshot_ref": "fi:turn_1.snapshots/current.yaml",
            "announce_entry": {"text": "snapshot refreshed"},
        },
        "blocks": [],
        "source_rows": [],
        "artifact_rows": [],
        "snapshot_refs": [],
        "announce_candidates": [],
    }

    subsystem.apply_react_phase_policies("block_production", "bundle.composite", target)

    assert target["artifact_rows"] == [{"path": "files/report.pdf"}]
    assert target["snapshot_refs"] == ["fi:turn_1.snapshots/current.yaml"]
    assert target["announce_candidates"] == [{"text": "snapshot refreshed"}]


def test_exec_block_production_policy_maps_report_and_items_without_exploration():
    from kdcube_ai_app.apps.chat.sdk.tools import exec_tools

    subsystem = EventSourceSubsystem(modules=[{"mod": exec_tools, "alias": "exec_tools"}])
    source = subsystem.by_event_source_id("exec_tools.execute_code_python")
    assert source is not None
    assert [binding.event_policy_id for binding in source.react.tool_call_validation] == [
        "exec_tools.tool_call_validation.exec_preflight",
    ]
    assert [binding.event_policy_id for binding in source.react.block_production] == [
        "react.block_production.tool_default",
        "exec_tools.block_production.exec_result",
    ]
    assert subsystem.should_merge_to_sources_pool("exec_tools.execute_code_python") is False

    target = {
        "tool_id": "exec_tools.execute_code_python",
        "tool_call_id": "tc_1",
        "event_id": "tc_1",
        "turn_id": "turn_1",
        "tool_result_path": "tc:turn_1.tc_1.result",
        "raw": {
            "report_text": "Program finished.",
            "items": [
                {
                    "artifact_id": "summary",
                    "output": {"type": "file", "path": "turn_1/files/summary.txt"},
                    "visibility": "external",
                }
            ],
        },
        "ret": None,
        "blocks": [],
        "source_rows": [],
        "artifact_rows": [],
        "snapshot_refs": [],
        "announce_candidates": [],
    }

    subsystem.apply_react_phase_policies(
        "block_production",
        "exec_tools.execute_code_python",
        target,
    )

    assert target["source_rows"] == []
    assert target["blocks"] == [
        {
            "turn": "turn_1",
            "type": "react.tool.result",
            "call_id": "tc_1",
            "mime": "text/markdown",
            "path": "tc:turn_1.tc_1.result",
            "text": "Program finished.",
            "meta": {"tool_call_id": "tc_1"},
        }
    ]
    assert target["artifact_rows"] == [
        {
            "artifact_id": "summary",
            "output": {"type": "file", "path": "turn_1/files/summary.txt"},
            "visibility": "external",
            "emit_hosted_file": True,
            "resolve_file_path": True,
        }
    ]


def test_exec_tool_call_validation_policy_rejects_missing_code_before_execution():
    from kdcube_ai_app.apps.chat.sdk.tools import exec_tools

    subsystem = EventSourceSubsystem(modules=[{"mod": exec_tools, "alias": "exec_tools"}])
    target = {
        "tool_id": "exec_tools.execute_code_python",
        "event_source_id": "exec_tools.execute_code_python",
        "tool_call_id": "tc_1",
        "event_id": "tc_1",
        "turn_id": "turn_1",
        "final_params": {
            "contract": [
                {
                    "filename": "turn_1/files/out.txt",
                    "description": "test output",
                }
            ]
        },
        "state": {"last_decision": {"tool_call": {"tool_id": "exec_tools.execute_code_python"}}},
        "blocks": [],
        "notice_rows": [],
        "state_updates": {},
    }

    subsystem.apply_react_phase_policies(
        "tool_call_validation",
        "exec_tools.execute_code_python",
        target,
    )

    assert target["stop"] is True
    assert target["retry_decision"] is True
    assert target["decision_raw_reason"] == "missing_channel.code"
    assert [row["code"] for row in target["notice_rows"]] == ["protocol_violation.exec_missing_code"]
    assert target["state_updates"]["last_tool_result"][0]["error"]["code"] == "exec_missing_code"
    assert target["blocks"][-1]["type"] == "react.tool.result"
    assert "exec_missing_code" in (target["blocks"][-1].get("text") or "")


def test_builtin_external_tool_modules_declare_block_production_sources():
    from kdcube_ai_app.apps.chat.sdk.tools import browser_tools, exec_tools, rendering_tools

    subsystem = EventSourceSubsystem(modules=[
        {"mod": browser_tools, "alias": "browser_tools"},
        {"mod": exec_tools, "alias": "exec_tools"},
        {"mod": rendering_tools, "alias": "rendering_tools"},
    ])

    expected = {
        "browser_tools.open_page",
        "browser_tools.click",
        "browser_tools.fill",
        "browser_tools.scroll",
        "browser_tools.status",
        "browser_tools.close",
        "exec_tools.execute_code_python",
        "rendering_tools.write_pptx",
        "rendering_tools.write_png",
        "rendering_tools.write_pdf",
        "rendering_tools.write_docx",
    }
    declared = {item["event_source_id"] for item in subsystem.list_sources()}
    assert expected <= declared
    for event_source_id in expected:
        source = subsystem.by_event_source_id(event_source_id)
        assert source is not None
        expected_validation_policies = []
        expected_block_policies = ["react.block_production.tool_default"]
        if event_source_id == "exec_tools.execute_code_python":
            expected_validation_policies.append("exec_tools.tool_call_validation.exec_preflight")
            expected_block_policies.append("exec_tools.block_production.exec_result")
        elif event_source_id.startswith("browser_tools."):
            expected_block_policies.extend([
                "react.block_production.generic_result_item",
                "react.block_production.declared_file_items",
            ])
        elif event_source_id.startswith("rendering_tools."):
            expected_validation_policies.append("rendering_tools.tool_call_validation.prepare_inputs")
            expected_block_policies.extend([
                "react.block_production.write_tool_result",
                "react.block_production.declared_file_items",
            ])
        assert [binding.event_policy_id for binding in source.react.tool_call_validation] == expected_validation_policies
        assert [binding.event_policy_id for binding in source.react.block_production] == expected_block_policies
        assert [binding.event_policy_id for binding in source.react.timeline_projection] == [
            "react.timeline_projection.identity",
        ]
        assert [binding.event_policy_id for binding in source.react.compaction_projection] == [
            "react.compaction_projection.identity",
        ]


@pytest.mark.asyncio
async def test_rendering_tool_call_validation_policy_rewrites_attachment_output_path():
    from kdcube_ai_app.apps.chat.sdk.tools import rendering_tools

    subsystem = EventSourceSubsystem(modules=[{"mod": rendering_tools, "alias": "rendering_tools"}])
    target = {
        "tool_id": "rendering_tools.write_pdf",
        "event_source_id": "rendering_tools.write_pdf",
        "tool_call_id": "tc_1",
        "event_id": "tc_1",
        "turn_id": "turn_2",
        "final_params": {"path": "turn_1/attachments/report.pdf"},
        "notice_rows": [],
        "blocks": [],
        "state_updates": {},
    }

    await subsystem.apply_react_phase_policies_async(
        "tool_call_validation",
        "rendering_tools.write_pdf",
        target,
    )

    assert target["final_params"]["path"] == "turn_2/files/report.pdf"
    assert target["write_timeline_local"] is True
    assert target["notice_rows"] == [
        {
            "code": "protocol_violation.path_rewritten",
            "message": "Rendering tool path was rewritten to current turn files/.",
            "extra": {
                "original": "turn_1/attachments/report.pdf",
                "rewritten": "turn_2/files/report.pdf",
                "tool_id": "rendering_tools.write_pdf",
            },
        }
    ]


def test_default_hide_by_segment_policy_mutates_matching_source_blocks_inline():
    @event_source(
        event_source_id="bundle.snapshot.materialized",
        policies=[
            {
                "react_phase": "timeline_projection",
                "event_policy_id": "react.timeline_projection.hide_by_segment",
                "params": {"segments": ["old"], "replacement_text": "[snapshot omitted]"},
            },
        ],
    )
    class SnapshotEvent:
        pass

    subsystem = EventSourceSubsystem(modules=[{"mod": _module("snapshot_events", SnapshotEvent=SnapshotEvent)}])
    blocks = [
        {
            "event_source_id": "bundle.snapshot.materialized",
            "event_id": "evt_old",
            "meta": {TIMELINE_SEGMENT_META_KEY: "old"},
            "text": "old snapshot",
        },
        {
            "event_source_id": "bundle.snapshot.materialized",
            "event_id": "evt_recent",
            "meta": {TIMELINE_SEGMENT_META_KEY: "recent"},
            "text": "recent snapshot",
        },
        {
            "event_source_id": "bundle.other",
            "event_id": "evt_other",
            "meta": {TIMELINE_SEGMENT_META_KEY: "old"},
            "text": "other",
        },
    ]

    subsystem.apply_react_phase_policies("timeline_projection", "bundle.snapshot.materialized", blocks)

    assert blocks[0]["hidden"] is True
    assert blocks[0]["replacement_text"] == "[snapshot omitted]"
    assert blocks[0]["meta"]["hidden_prune_scope"] == "old"
    assert blocks[1].get("hidden") is None
    assert blocks[2].get("hidden") is None


def test_timeline_projection_derives_tool_event_source_from_call_meta():
    @event_source(
        event_source_id="web_tools.web_search",
        policies=[
            {
                "react_phase": "timeline_projection",
                "event_policy_id": "react.timeline_projection.hide_by_segment",
                "params": {"segments": ["old"], "replacement_text": "[search omitted]"},
            },
        ],
    )
    class WebSearchTool:
        pass

    subsystem = EventSourceSubsystem(modules=[{"mod": _module("web_events", WebSearchTool=WebSearchTool)}])
    blocks = [
        {
            "type": "react.tool.result",
            "call_id": "tc_search",
            "meta": {"tool_call_id": "tc_search", TIMELINE_SEGMENT_META_KEY: "old"},
            "text": "search rows",
        },
        {
            "type": "react.tool.result",
            "call_id": "tc_other",
            "meta": {"tool_call_id": "tc_other", TIMELINE_SEGMENT_META_KEY: "old"},
            "text": "other rows",
        },
    ]

    apply_event_source_transformers(
        event_sources=subsystem,
        react_phase="timeline_projection",
        timeline_blocks=blocks,
        call_meta={
            "tc_search": {"tool_id": "web_tools.web_search"},
            "tc_other": {"tool_id": "other.tool"},
        },
    )

    assert blocks[0]["hidden"] is True
    assert blocks[0]["replacement_text"] == "[search omitted]"
    assert blocks[1].get("hidden") is None


def test_builtin_react_external_event_sources_are_discoverable():
    subsystem = EventSourceSubsystem(modules=[{
        "mod": react_core_events,
        "alias": "react",
    }])

    followup = subsystem.by_event_source_id(REACT_FOLLOWUP_EVENT_SOURCE_ID)
    steer = subsystem.by_event_source_id(REACT_STEER_EVENT_SOURCE_ID)
    attachment = subsystem.by_event_source_id(REACT_USER_ATTACHMENT_EVENT_SOURCE_ID)
    write = subsystem.by_event_source_id(REACT_WRITE_EVENT_SOURCE_ID)
    memsearch = subsystem.by_event_source_id(REACT_MEMSEARCH_EVENT_SOURCE_ID)
    assert followup is not None
    assert steer is not None
    assert attachment is not None
    assert write is not None
    assert memsearch is not None
    assert [binding.event_policy_id for binding in followup.react.block_production] == [
        "react.block_production.user_followup_default",
    ]
    assert followup.react.timeline_projection == ()
    assert followup.react.compaction_projection == ()
    assert followup.react.announce_production == ()
    assert followup.reactive is True
    assert [binding.event_policy_id for binding in steer.react.block_production] == [
        "react.block_production.user_steer_default",
    ]
    assert steer.react.timeline_projection == ()
    assert steer.react.compaction_projection == ()
    assert steer.react.announce_production == ()
    assert steer.reactive is False
    assert steer.iteration_credit == 0
    assert [binding.event_policy_id for binding in attachment.react.block_production] == [
        "react.block_production.user_attachment_default",
    ]
    assert attachment.reactive is False
    assert write.kind == "react.native_tool.write"
    assert [binding.event_policy_id for binding in write.react.timeline_projection] == [
        "react.timeline_projection.identity",
    ]
    assert [binding.event_policy_id for binding in write.react.compaction_projection] == [
        "react.compaction_projection.identity",
    ]
    assert memsearch.kind == "react.native_tool.source"
    assert [binding.event_policy_id for binding in memsearch.react.timeline_projection] == [
        "react.timeline_projection.identity",
    ]


def test_reactive_credit_requires_occurrence_reactive_and_uses_source_credit_default():
    def list_event_sources():
        return [
            event_source_declaration(
                event_source_id="bundle.wizard.assistance.requested",
                policies=[],
                kind="react.external",
                reactive=True,
                iteration_credit=3,
            )
        ]

    subsystem = EventSourceSubsystem(modules=[{"mod": _module("wizard_credit_sources", list_event_sources=list_event_sources)}])
    runtime_ctx = SimpleNamespace(
        reactive_event_iteration_credit_enabled=True,
        reactive_event_iteration_credit_per_event=1,
        event_sources=subsystem,
    )
    no_reactive_occurrence = SimpleNamespace(
        kind="external_event",
        payload={
            "event": {
                "event_source_id": "bundle.wizard.assistance.requested",
                "payload": {},
            }
        },
    )

    assert resolve_reactive_iteration_credit(
        event_type="external_event",
        event=no_reactive_occurrence,
        runtime_ctx=runtime_ctx,
    ) == 0

    assert resolve_reactive_iteration_credit(
        event_type="external_event",
        event=SimpleNamespace(
            kind="external_event",
            payload={
                "event": {
                    "event_source_id": "bundle.wizard.assistance.requested",
                    "reactive": True,
                    "payload": {},
                }
            },
        ),
        runtime_ctx=runtime_ctx,
    ) == 3

    assert resolve_reactive_iteration_credit(
        event_type="external_event",
        event=SimpleNamespace(
            kind="external_event",
            payload={
                "event": {
                    "event_source_id": "bundle.wizard.assistance.requested",
                    "payload": {"iteration_credit": 2},
                }
            },
        ),
        runtime_ctx=runtime_ctx,
    ) == 0

    assert resolve_reactive_iteration_credit(
        event_type="external_event",
        event=SimpleNamespace(
            kind="external_event",
            payload={
                "event": {
                    "event_source_id": "bundle.wizard.assistance.requested",
                    "reactive": True,
                    "payload": {"iteration_credit": 2},
                }
            },
        ),
        runtime_ctx=runtime_ctx,
    ) == 2

    assert resolve_reactive_iteration_credit(
        event_type="external_event",
        event=SimpleNamespace(
            kind="external_event",
            payload={
                "event": {
                    "event_source_id": "bundle.wizard.assistance.requested",
                    "reactive": False,
                    "payload": {"iteration_credit": 5},
                }
            },
        ),
        runtime_ctx=runtime_ctx,
    ) == 0


def test_announce_production_policy_receives_full_timeline_context():
    seen = {}

    @announce_event_policy(event_policy_id="react.announce_production.snapshot_current_turn")
    def announce_policy(target, *, timeline_blocks, current_turn_id, source, **_ctx):
        """Append announce text only when the latest familiar event is in the current turn."""
        source_blocks = [
            block
            for block in timeline_blocks
            if block.get("event_source_id") == source.event_source_id
        ]
        seen["timeline_len"] = len(timeline_blocks)
        seen["source_blocks"] = len(source_blocks)
        latest = source_blocks[-1]
        latest_turn = latest.get("turn_id") or ""
        if latest_turn == current_turn_id:
            target.append({"text": f"snapshot ready: {latest.get('event_id')}"})
        return target

    @event_source(
        event_source_id="bundle.snapshot.materialized",
        policies=[
            {
                "react_phase": "announce_production",
                "event_policy_id": "react.announce_production.snapshot_current_turn",
            },
        ],
    )
    class SnapshotEvent:
        pass

    subsystem = EventSourceSubsystem(modules=[{
        "mod": _module(
            "snapshot_events",
            SnapshotEvent=SnapshotEvent,
            announce_policy=announce_policy,
        )
    }])
    timeline_blocks = [
        {"type": "turn.header", "turn_id": "turn_1", "text": "older"},
        {
            "type": "event.external",
            "turn_id": "turn_1",
            "event_source_id": "bundle.snapshot.materialized",
            "event_id": "evt_old",
            "text": "old snapshot",
        },
        {"type": "turn.header", "turn_id": "turn_2", "text": "current"},
        {
            "type": "event.external",
            "turn_id": "turn_2",
            "event_source_id": "bundle.snapshot.materialized",
            "event_id": "evt_new",
            "text": "new snapshot",
        },
    ]

    announce_blocks = produce_event_source_announce_blocks(
        event_sources=subsystem,
        timeline_blocks=timeline_blocks,
        current_turn_id="turn_2",
    )

    assert seen == {"timeline_len": 4, "source_blocks": 2}
    assert announce_blocks == [{"text": "snapshot ready: evt_new"}]


def test_event_identity_helper_stamps_occurrence_group():
    blocks = [{"type": "event.external"}, {"type": "user.attachment.meta"}]

    stamped = stamp_event_identity_many(
        blocks,
        event_source_id="react.followup",
        event_id="evt_1",
        story_id="record_wizard",
    )

    assert stamped == blocks
    assert {block["event_source_id"] for block in blocks} == {"react.followup"}
    assert {block["event_id"] for block in blocks} == {"evt_1"}
    assert {block["story_id"] for block in blocks} == {"record_wizard"}
