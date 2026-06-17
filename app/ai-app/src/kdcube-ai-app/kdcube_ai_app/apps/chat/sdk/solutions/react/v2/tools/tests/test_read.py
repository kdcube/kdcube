# SPDX-License-Identifier: MIT

import pytest
import json
import random
from types import ModuleType, SimpleNamespace

from kdcube_ai_app.apps.chat.sdk.events import EventSourceSubsystem, event_source_declaration
from kdcube_ai_app.apps.chat.sdk.solutions.react.events import block_production_policy
from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.read import handle_react_read
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.tools.tests.helpers import FakeBrowser


def _module(name: str, **attrs):
    mod = ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


class _FakeComm:
    def __init__(self):
        self.events = []

    async def service_event(self, **kwargs):
        self.events.append(kwargs)


@pytest.mark.asyncio
async def test_read_missing_paths_notice(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_read", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    state = {"last_decision": {"tool_call": {"params": {"paths": ["fi:turn_read.files/missing.md"]}}}}
    await handle_react_read(ctx_browser=ctx, state=state, tool_call_id="r1")
    assert any(b.get("type") == "react.notice" for b in ctx.timeline.blocks)


@pytest.mark.asyncio
async def test_read_returns_latest_version(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_read", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    path = "fi:turn_read.files/report.md"
    # older version
    ctx.timeline.blocks.append({
        "type": "react.tool.result",
        "mime": "application/json",
        "text": '{"artifact_path":"fi:turn_read.files/report.md","physical_path":"turn_read/files/report.md"}',
        "turn_id": "turn_read",
    })
    ctx.timeline.blocks.append({
        "type": "react.tool.result",
        "mime": "text/markdown",
        "path": path,
        "text": "old",
        "turn_id": "turn_read",
    })
    # newer version
    ctx.timeline.blocks.append({
        "type": "react.tool.result",
        "mime": "application/json",
        "text": '{"artifact_path":"fi:turn_read.files/report.md","physical_path":"turn_read/files/report.md"}',
        "turn_id": "turn_read",
    })
    ctx.timeline.blocks.append({
        "type": "react.tool.result",
        "mime": "text/markdown",
        "path": path,
        "text": "new",
        "turn_id": "turn_read",
    })
    state = {"last_decision": {"tool_call": {"params": {"paths": [path]}}}}
    await handle_react_read(ctx_browser=ctx, state=state, tool_call_id="r2")
    assert any(b.get("text") == "new" for b in ctx.timeline.blocks if b.get("type") == "react.tool.result")


@pytest.mark.asyncio
async def test_read_supports_outdir_relative_fi_paths(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_read", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "docker.err.log").write_text("boom", encoding="utf-8")

    state = {"last_decision": {"tool_call": {"params": {"paths": ["fi:logs/docker.err.log"]}}}}
    await handle_react_read(ctx_browser=ctx, state=state, tool_call_id="r3")

    assert any(
        b.get("path") == "fi:logs/docker.err.log" and b.get("text") == "boom"
        for b in ctx.timeline.blocks
        if b.get("type") == "react.tool.result"
    )


@pytest.mark.asyncio
async def test_read_preserves_pulled_namespace_object_ref_metadata(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_read", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    physical = tmp_path / "turn_read" / "files" / "memory.json"
    physical.parent.mkdir(parents=True, exist_ok=True)
    physical.write_text('{"ok": true, "memory": {"memory": "Prefer concise docs"}}', encoding="utf-8")
    path = "fi:turn_read.files/memory.json"
    state = {
        "last_decision": {"tool_call": {"params": {"paths": [path]}}},
        "pulled_logical_refs": {
            path: {
                "object_ref": "mem:record:mem_123",
                "mime": "application/vnd.kdcube.memory.record+json;version=1",
            }
        },
    }

    await handle_react_read(ctx_browser=ctx, state=state, tool_call_id="r3_source")

    blocks = [
        b for b in ctx.timeline.blocks
        if b.get("type") == "react.tool.result" and b.get("path") == path
    ]
    assert blocks
    meta = blocks[-1]["meta"]
    assert meta["object_ref"] == "mem:record:mem_123"
    assert "source_ref" not in meta
    assert "original_ref" not in meta
    assert meta["source_namespace"] == "mem"
    assert meta["source_mime"] == "application/vnd.kdcube.memory.record+json;version=1"


@pytest.mark.asyncio
async def test_read_projects_pulled_namespace_ref_through_owner_event_source(tmp_path):
    @block_production_policy(event_policy_id="demo.block_production.owner_read")
    async def owner_read_policy(target, **_):
        blocks = target.setdefault("blocks", [])
        blocks.append({
            "turn": target.get("turn_id") or "",
            "type": "react.tool.result",
            "call_id": target.get("tool_call_id") or "",
            "event_source_id": target.get("event_source_id") or "",
            "mime": "text/markdown",
            "path": target.get("object_ref") or "",
            "text": f"OWNER BLOCK\nobject_ref: {target.get('object_ref')}",
            "meta": {"from_owner": True},
        })
        target["blocks_produced"] = True
        return target

    def list_event_sources():
        return [
            event_source_declaration(
                event_source_id="named_services.mem",
                policies=[
                    {"react_phase": "block_production", "event_policy_id": "demo.block_production.owner_read"},
                ],
                kind="named_service",
            )
        ]

    event_sources = EventSourceSubsystem(modules=[{
        "mod": _module(
            "demo_owner_read_events",
            list_event_sources=list_event_sources,
            owner_read_policy=owner_read_policy,
        ),
        "alias": "demo",
    }])
    runtime = RuntimeCtx(turn_id="turn_read", outdir=str(tmp_path), workdir=str(tmp_path), event_sources=event_sources)
    ctx = FakeBrowser(runtime)
    physical = tmp_path / "turn_read" / "files" / "memory.json"
    physical.parent.mkdir(parents=True, exist_ok=True)
    physical.write_text('{"ok": true, "memory": {"memory": "Prefer concise docs"}}', encoding="utf-8")
    path = "fi:turn_read.files/memory.json"
    state = {
        "last_decision": {"tool_call": {"params": {"paths": [path]}}},
        "pulled_logical_refs": {
            path: {
                "object_ref": "mem:record:mem_123",
                "mime": "application/vnd.kdcube.memory.record+json;version=1",
            }
        },
    }

    await handle_react_read(ctx_browser=ctx, state=state, tool_call_id="r_owner_projected")

    assert any(
        b.get("path") == "mem:record:mem_123"
        and "OWNER BLOCK" in (b.get("text") or "")
        and b.get("meta", {}).get("owner_projected") is True
        and b.get("meta", {}).get("object_ref") == "mem:record:mem_123"
        for b in ctx.timeline.blocks
        if b.get("type") == "react.tool.result"
    )
    assert not any(
        b.get("path") == path and "Prefer concise docs" in (b.get("text") or "")
        for b in ctx.timeline.blocks
        if b.get("type") == "react.tool.result"
    )


@pytest.mark.asyncio
async def test_read_duplicate_visible_content_returns_visible_ref(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_read", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "note.md").write_text("already visible", encoding="utf-8")

    state = {"last_decision": {"tool_call": {"params": {"paths": ["fi:logs/note.md"]}}}}
    await handle_react_read(ctx_browser=ctx, state=state, tool_call_id="r_visible_1")
    await handle_react_read(ctx_browser=ctx, state=state, tool_call_id="r_visible_2")

    summaries = []
    for b in ctx.timeline.blocks:
        if (
            b.get("type") != "react.tool.result"
            or b.get("path") != "tc:turn_read.r_visible_2.result"
            or b.get("mime") != "application/json"
        ):
            continue
        payload = json.loads(b.get("text") or "{}")
        if "paths" in payload:
            summaries.append(payload)

    assert summaries
    assert summaries[-1]["exists_in_visible_context"] == ["fi:logs/note.md"]
    ref = summaries[-1]["visible_context_refs"]["fi:logs/note.md"]
    assert ref["path"] == "fi:logs/note.md"
    assert ref["tool_result_path"] == "tc:turn_read.r_visible_1.result"


@pytest.mark.asyncio
async def test_read_tc_result_prefers_inline_payload_over_meta(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_read", outdir=str(tmp_path), workdir=str(tmp_path))
    ctx = FakeBrowser(runtime)
    source_path = "tc:turn_src.pref1.result"

    ctx.timeline.blocks.extend([
        {
            "type": "react.tool.result",
            "mime": "application/json",
            "path": source_path,
            "text": (
                '{"artifact_path":"tc:turn_src.pref1.result","mime":"application/json",'
                '"kind":"file","visibility":"internal","tool_call_id":"pref1"}'
            ),
            "turn_id": "turn_src",
            "call_id": "pref1",
            "meta": {"tool_call_id": "pref1"},
        },
        {
            "type": "react.tool.result",
            "mime": "application/json",
            "path": source_path,
            "text": (
                '{"ok": true, "current": {"location": {"value": "Wuppertal"}}, '
                '"summary": "Current preferences:\\n- location: Wuppertal"}'
            ),
            "turn_id": "turn_src",
            "call_id": "pref1",
            "meta": {"tool_call_id": "pref1"},
        },
    ])

    state = {"last_decision": {"tool_call": {"params": {"paths": [source_path]}}}}
    await handle_react_read(ctx_browser=ctx, state=state, tool_call_id="r4")

    assert any(
        b.get("call_id") == "r4"
        and b.get("path") == source_path
        and '"location": {"value": "Wuppertal"}' in (b.get("text") or "")
        for b in ctx.timeline.blocks
        if b.get("type") == "react.tool.result"
    )


@pytest.mark.asyncio
async def test_read_tc_items_materializes_line_range(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_read", outdir=str(tmp_path), workdir=str(tmp_path), max_tokens=80_000)
    ctx = FakeBrowser(runtime)
    source_path = "tc:turn_src.tc_big.call"
    ctx.timeline.blocks.append({
        "type": "react.tool.call",
        "mime": "text/plain",
        "path": source_path,
        "text": "\n".join([
            "line 1",
            "line 2",
            "line 3",
            "line 4",
            "line 5",
        ]),
        "turn_id": "turn_src",
        "call_id": "tc_big",
        "meta": {"tool_call_id": "tc_big"},
    })

    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "items": [
                        {"path": source_path, "line_start": 2, "line_count": 2}
                    ]
                }
            }
        }
    }

    await handle_react_read(ctx_browser=ctx, state=state, tool_call_id="r_tc_range")

    range_block = next(
        b for b in ctx.timeline.blocks
        if b.get("type") == "react.tool.result"
        and b.get("call_id") == "r_tc_range"
        and b.get("path") == source_path
        and "[READ RANGE]" in (b.get("text") or "")
    )
    assert "lines: [2-3]/5" in range_block["text"]
    assert "     2\tline 2" in range_block["text"]
    assert "     3\tline 3" in range_block["text"]
    assert "line 4" not in range_block["text"]

    status = next(
        json.loads(b["text"])
        for b in ctx.timeline.blocks
        if b.get("path") == "tc:turn_read.r_tc_range.result" and b.get("mime") == "application/json"
    )
    assert status["paths"][0]["read_range"]["line_start"] == 2
    assert status["paths"][0]["read_range"]["visible_lines"] == 2


@pytest.mark.asyncio
async def test_read_ev_event_path(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_read", outdir=str(tmp_path), workdir=str(tmp_path), max_tokens=80_000)
    ctx = FakeBrowser(runtime)
    event_path = "ev:turn_src.events/task-tracker/canvas/review/evt_1"
    ctx.timeline.blocks.append({
        "type": "event.external",
        "mime": "application/json",
        "path": event_path,
        "text": json.dumps({
            "event_id": "evt_1",
            "event_source_id": "task_tracker.canvas.review.requested",
            "ok": True,
            "ret": {"prompt": "Review selected text"},
        }),
        "turn_id": "turn_src",
        "meta": {
            "event_id": "evt_1",
            "event_source_id": "task_tracker.canvas.review.requested",
            "event_type": "event.external",
        },
    })

    state = {"last_decision": {"tool_call": {"params": {"paths": [event_path]}}}}
    await handle_react_read(ctx_browser=ctx, state=state, tool_call_id="r_ev")

    assert any(
        b.get("type") == "react.tool.result"
        and b.get("call_id") == "r_ev"
        and b.get("path") == event_path
        and "Review selected text" in (b.get("text") or "")
        for b in ctx.timeline.blocks
    )


@pytest.mark.asyncio
async def test_read_items_materializes_multiple_line_ranges(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_read", outdir=str(tmp_path), workdir=str(tmp_path), max_tokens=80_000)
    ctx = FakeBrowser(runtime)
    out_file = tmp_path / "turn_read" / "outputs" / "page.html"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text("\n".join([
        "<html>",
        "<body>",
        "<section id=\"hero\">Hero</section>",
        "<section id=\"pricing\">Pricing</section>",
        "<section id=\"checkout\">Checkout</section>",
        "</body>",
        "</html>",
    ]), encoding="utf-8")

    source_path = "fi:turn_read.outputs/page.html"
    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "items": [
                        {"path": source_path, "line_start": 3, "line_count": 2},
                        {"path": source_path, "line_start": 5, "line_count": 1},
                    ]
                }
            }
        }
    }

    await handle_react_read(ctx_browser=ctx, state=state, tool_call_id="r_ranges")

    range_blocks = [
        b for b in ctx.timeline.blocks
        if b.get("type") == "react.tool.result"
        and b.get("call_id") == "r_ranges"
        and b.get("path") == source_path
        and "[READ RANGE]" in (b.get("text") or "")
    ]
    assert len(range_blocks) == 2
    assert "lines: [3-4]/7" in range_blocks[0]["text"]
    assert "     3\t<section id=\"hero\">Hero</section>" in range_blocks[0]["text"]
    assert "     4\t<section id=\"pricing\">Pricing</section>" in range_blocks[0]["text"]
    assert "lines: [5-5]/7" in range_blocks[1]["text"]

    status = next(
        json.loads(b["text"])
        for b in ctx.timeline.blocks
        if b.get("path") == "tc:turn_read.r_ranges.result" and b.get("mime") == "application/json"
    )
    assert len(status["paths"]) == 2
    assert status["paths"][0]["read_range"]["line_start"] == 3
    assert status["paths"][1]["read_range"]["line_start"] == 5


@pytest.mark.asyncio
async def test_read_ks_items_materializes_line_range(tmp_path):
    runtime = RuntimeCtx(
        turn_id="turn_read",
        outdir=str(tmp_path),
        workdir=str(tmp_path),
        bundle_storage=str(tmp_path / "bundle-storage"),
        max_tokens=80_000,
    )
    text = "\n".join([
        "# Knowledge Article",
        "",
        "alpha",
        "beta",
        "gamma",
        "delta",
    ])

    def read_knowledge(*, path: str):
        assert path == "ks:docs/article.md"
        return {
            "text": text,
            "mime": "text/markdown",
            "physical_path": str(tmp_path / "bundle-storage" / "docs" / "article.md"),
        }

    runtime.knowledge_read_fn = read_knowledge
    ctx = FakeBrowser(runtime)
    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "items": [
                        {"path": "ks:docs/article.md", "line_start": 3, "line_count": 2}
                    ]
                }
            }
        }
    }

    await handle_react_read(ctx_browser=ctx, state=state, tool_call_id="r_ks_range")

    range_block = next(
        b for b in ctx.timeline.blocks
        if b.get("type") == "react.tool.result"
        and b.get("call_id") == "r_ks_range"
        and b.get("path") == "ks:docs/article.md"
        and "[READ RANGE]" in (b.get("text") or "")
    )
    assert "lines: [3-4]/6" in range_block["text"]
    assert "     3\talpha" in range_block["text"]
    assert "     4\tbeta" in range_block["text"]
    assert "gamma" not in range_block["text"]

    status = json.loads(next(
        b["text"]
        for b in ctx.timeline.blocks
        if b.get("path") == "tc:turn_read.r_ks_range.result" and b.get("mime") == "application/json"
    ))
    assert status["paths"][0]["read_range"]["line_start"] == 3
    assert status["paths"][0]["read_range"]["line_end"] == 4


@pytest.mark.asyncio
async def test_read_ks_stats_includes_line_count(tmp_path):
    runtime = RuntimeCtx(
        turn_id="turn_read",
        outdir=str(tmp_path),
        workdir=str(tmp_path),
        bundle_storage=str(tmp_path / "bundle-storage"),
    )

    def read_knowledge(*, path: str):
        return {
            "text": "one\ntwo\nthree\n",
            "mime": "text/markdown",
            "physical_path": str(tmp_path / "bundle-storage" / "docs" / "article.md"),
        }

    runtime.knowledge_read_fn = read_knowledge
    ctx = FakeBrowser(runtime)
    state = {
        "last_decision": {
            "tool_call": {
                "params": {
                    "paths": ["ks:docs/article.md"],
                    "stats_only": True,
                }
            }
        }
    }

    await handle_react_read(ctx_browser=ctx, state=state, tool_call_id="r_ks_stats")

    status = json.loads(next(
        b["text"]
        for b in ctx.timeline.blocks
        if b.get("path") == "tc:turn_read.r_ks_stats.result" and b.get("mime") == "application/json"
    ))
    assert status["paths"][0]["status"] == "stats_only"
    assert status["paths"][0]["kind"] == "text"
    assert status["paths"][0]["line_count"] == 3


@pytest.mark.asyncio
async def test_read_ks_text_is_uncapped_by_default(tmp_path):
    runtime = RuntimeCtx(
        turn_id="turn_read",
        outdir=str(tmp_path),
        workdir=str(tmp_path),
        bundle_storage=str(tmp_path / "bundle-storage"),
        read_visible_max_text_symbols=30,
        read_visible_max_tokens=4,
        read_visible_max_bytes=64,
        max_tokens=100,
    )
    text = "alpha\n" + ("knowledge body line\n" * 20) + "omega"

    def read_knowledge(*, path: str):
        return {
            "text": text,
            "mime": "text/markdown",
            "physical_path": str(tmp_path / "bundle-storage" / "docs" / "long.md"),
        }

    runtime.knowledge_read_fn = read_knowledge
    ctx = FakeBrowser(runtime)
    state = {"last_decision": {"tool_call": {"params": {"paths": ["ks:docs/long.md"]}}}}

    await handle_react_read(ctx_browser=ctx, state=state, tool_call_id="r_ks_full")

    ks_block = next(
        b for b in ctx.timeline.blocks
        if b.get("type") == "react.tool.result"
        and b.get("call_id") == "r_ks_full"
        and b.get("path") == "ks:docs/long.md"
    )
    assert ks_block["text"].endswith("omega")
    assert "[READ PREVIEW TRUNCATED]" not in ks_block["text"]


@pytest.mark.asyncio
async def test_read_ks_text_uses_explicit_knowledge_cap(tmp_path):
    runtime = RuntimeCtx(
        turn_id="turn_read",
        outdir=str(tmp_path),
        workdir=str(tmp_path),
        bundle_storage=str(tmp_path / "bundle-storage"),
        knowledge_read_visible_max_text_symbols=40,
        max_tokens=100,
    )
    text = "alpha\n" + ("knowledge body line\n" * 20) + "omega"

    def read_knowledge(*, path: str):
        return {
            "text": text,
            "mime": "text/markdown",
            "physical_path": str(tmp_path / "bundle-storage" / "docs" / "long.md"),
        }

    runtime.knowledge_read_fn = read_knowledge
    ctx = FakeBrowser(runtime)
    state = {"last_decision": {"tool_call": {"params": {"paths": ["ks:docs/long.md"]}}}}

    await handle_react_read(ctx_browser=ctx, state=state, tool_call_id="r_ks_capped")

    ks_block = next(
        b for b in ctx.timeline.blocks
        if b.get("type") == "react.tool.result"
        and b.get("call_id") == "r_ks_capped"
        and b.get("path") == "ks:docs/long.md"
    )
    assert "[READ PREVIEW TRUNCATED]" in ks_block["text"]
    assert "visible_text_symbols: 40" in ks_block["text"]
    assert "omega" not in ks_block["text"]


@pytest.mark.asyncio
async def test_read_skill_is_not_read_capped(monkeypatch, tmp_path):
    import kdcube_ai_app.apps.chat.sdk.skills.skills_registry as registry

    runtime = RuntimeCtx(
        turn_id="turn_read",
        outdir=str(tmp_path),
        workdir=str(tmp_path),
        read_visible_max_text_symbols=20,
        read_visible_max_tokens=4,
        read_visible_max_bytes=64,
        max_tokens=100,
    )
    long_instruction = "skill-start\n" + ("full skill instruction\n" * 30) + "skill-end"
    spec = SimpleNamespace(
        name="Big Skill",
        namespace="public",
        id="big",
        instruction_text=long_instruction,
        instruction_compact_text="",
        instruction_paths=None,
        sources=[],
    )
    monkeypatch.setattr(registry, "build_skill_short_id_map", lambda consumer, **kwargs: {})
    monkeypatch.setattr(registry, "import_skillset", lambda items, short_id_map=None, **kwargs: ["public.big"])
    monkeypatch.setattr(registry, "get_skill", lambda sid: spec if sid == "public.big" else None)

    ctx = FakeBrowser(runtime)
    state = {"last_decision": {"tool_call": {"params": {"paths": ["sk:public.big"]}}}}

    await handle_react_read(ctx_browser=ctx, state=state, tool_call_id="r_skill")

    skill_block = next(
        b for b in ctx.timeline.blocks
        if b.get("type") == "react.tool.result"
        and b.get("call_id") == "r_skill"
        and b.get("path") == "sk:public.big"
    )
    assert "ACTIVE 💡" in skill_block["text"]
    assert "skill-end" in skill_block["text"]
    assert "[READ PREVIEW TRUNCATED]" not in skill_block["text"]


@pytest.mark.asyncio
async def test_read_skill_emits_comm_event(monkeypatch, tmp_path):
    import kdcube_ai_app.apps.chat.sdk.skills.skills_registry as registry

    runtime = RuntimeCtx(
        turn_id="turn_read",
        outdir=str(tmp_path),
        workdir=str(tmp_path),
        max_tokens=100,
    )
    spec = SimpleNamespace(
        name="PDF Press",
        namespace="public",
        id="pdf-press",
        instruction_text="PDF generation guidance.",
        instruction_compact_text="",
        instruction_paths=None,
        sources=[],
    )
    monkeypatch.setattr(registry, "build_skill_short_id_map", lambda consumer, **kwargs: {})
    monkeypatch.setattr(registry, "import_skillset", lambda items, short_id_map=None, **kwargs: ["public.pdf-press"])
    monkeypatch.setattr(registry, "get_skill", lambda sid: spec if sid == "public.pdf-press" else None)

    ctx = FakeBrowser(runtime)
    comm = _FakeComm()
    react = SimpleNamespace(comm=comm)
    state = {"last_decision": {"tool_call": {"params": {"paths": ["sk:public.pdf-press"]}}}}

    await handle_react_read(ctx_browser=ctx, state=state, tool_call_id="r_skill_event", react=react)

    assert len(comm.events) == 1
    event = comm.events[0]
    assert event["type"] == "react.skill.read"
    assert event["step"] == "react.read"
    assert event["data"]["tool_call_id"] == "r_skill_event"
    assert event["data"]["requested_count"] == 1
    assert event["data"]["resolved_count"] == 1
    assert event["data"]["skills"] == [{
        "path": "sk:public.pdf-press",
        "status": "materialized",
        "materialized": True,
        "disclosure_hidden": False,
        "id": "public.pdf-press",
        "name": "PDF Press",
        "namespace": "public",
        "local_id": "pdf-press",
    }]


@pytest.mark.asyncio
async def test_read_hidden_skill_event_redacts_identity(monkeypatch, tmp_path):
    import kdcube_ai_app.apps.chat.sdk.skills.skills_registry as registry

    runtime = RuntimeCtx(
        turn_id="turn_read",
        outdir=str(tmp_path),
        workdir=str(tmp_path),
        max_tokens=100,
    )
    spec = SimpleNamespace(
        name="Secret Skill",
        namespace="private",
        id="secret",
        instruction_text="Hidden guidance.",
        instruction_compact_text="",
        instruction_paths=None,
        sources=[],
        is_disclosure_hidden=lambda: True,
    )
    monkeypatch.setattr(registry, "build_skill_short_id_map", lambda consumer, **kwargs: {})
    monkeypatch.setattr(registry, "import_skillset", lambda items, short_id_map=None, **kwargs: ["private.secret"])
    monkeypatch.setattr(registry, "get_skill", lambda sid: spec if sid == "private.secret" else None)

    ctx = FakeBrowser(runtime)
    comm = _FakeComm()
    react = SimpleNamespace(comm=comm)
    state = {"last_decision": {"tool_call": {"params": {"paths": ["sk:private.secret"]}}}}

    await handle_react_read(ctx_browser=ctx, state=state, tool_call_id="r_hidden_skill_event", react=react)

    skill = comm.events[0]["data"]["skills"][0]
    assert skill["disclosure_hidden"] is True
    assert skill["path"].startswith("sk:hidden-guidance-")
    assert "id" not in skill
    assert "name" not in skill
    assert "namespace" not in skill
    assert "local_id" not in skill


@pytest.mark.asyncio
async def test_read_skill_is_materialized_once_by_logical_path(monkeypatch, tmp_path):
    import kdcube_ai_app.apps.chat.sdk.skills.skills_registry as registry

    runtime = RuntimeCtx(
        turn_id="turn_read",
        outdir=str(tmp_path),
        workdir=str(tmp_path),
        max_tokens=100,
    )
    spec = SimpleNamespace(
        name="PDF Press",
        namespace="public",
        id="pdf-press",
        instruction_text="PDF generation guidance.",
        instruction_compact_text="",
        instruction_paths=None,
        sources=[],
    )
    monkeypatch.setattr(registry, "build_skill_short_id_map", lambda consumer, **kwargs: {})
    monkeypatch.setattr(registry, "import_skillset", lambda items, short_id_map=None, **kwargs: ["public.pdf-press"])
    monkeypatch.setattr(registry, "get_skill", lambda sid: spec if sid == "public.pdf-press" else None)

    ctx = FakeBrowser(runtime)
    state = {"last_decision": {"tool_call": {"params": {"paths": ["sk:public.pdf-press"]}}}}

    await handle_react_read(ctx_browser=ctx, state=state, tool_call_id="r_skill_1")
    await handle_react_read(ctx_browser=ctx, state=state, tool_call_id="r_skill_2")

    skill_blocks = [
        b for b in ctx.timeline.blocks
        if b.get("type") == "react.tool.result"
        and b.get("path") == "sk:public.pdf-press"
        and "ACTIVE" in (b.get("text") or "")
    ]
    assert len(skill_blocks) == 1

    summaries = []
    for block in ctx.timeline.blocks:
        if (
            block.get("type") == "react.tool.result"
            and block.get("path") == "tc:turn_read.r_skill_2.result"
            and block.get("mime") == "application/json"
        ):
            summaries.append(json.loads(block.get("text") or "{}"))
    assert summaries
    assert summaries[-1]["exists_in_visible_context"] == ["sk:public.pdf-press"]


@pytest.mark.asyncio
async def test_read_skill_hidden_by_pruning_is_not_treated_as_visible(monkeypatch, tmp_path):
    import kdcube_ai_app.apps.chat.sdk.skills.skills_registry as registry

    runtime = RuntimeCtx(
        turn_id="turn_read",
        outdir=str(tmp_path),
        workdir=str(tmp_path),
        max_tokens=100,
    )
    spec = SimpleNamespace(
        name="PDF Press",
        namespace="public",
        id="pdf-press",
        instruction_text="PDF generation guidance after restore.",
        instruction_compact_text="",
        instruction_paths=None,
        sources=[],
    )
    monkeypatch.setattr(registry, "build_skill_short_id_map", lambda consumer, **kwargs: {})
    monkeypatch.setattr(registry, "import_skillset", lambda items, short_id_map=None, **kwargs: ["public.pdf-press"])
    monkeypatch.setattr(registry, "get_skill", lambda sid: spec if sid == "public.pdf-press" else None)

    ctx = FakeBrowser(runtime)
    ctx.timeline.blocks.append({
        "type": "react.tool.result",
        "call_id": "r_old",
        "path": "sk:public.pdf-press",
        "mime": "text/markdown",
        "text": "ACTIVE old hidden skill text",
        "hidden": True,
        "meta": {
            "tool_call_id": "r_old",
            "hidden": True,
            "hidden_prune_scope": "cold_recent",
        },
    })
    state = {
        "loaded_skills": {"public.pdf-press"},
        "last_decision": {"tool_call": {"params": {"paths": ["sk:public.pdf-press"]}}},
    }

    await handle_react_read(ctx_browser=ctx, state=state, tool_call_id="r_skill_restore")

    visible_skill_blocks = [
        b for b in ctx.timeline.blocks
        if b.get("type") == "react.tool.result"
        and b.get("path") == "sk:public.pdf-press"
        and not b.get("hidden")
        and "ACTIVE" in (b.get("text") or "")
    ]
    assert visible_skill_blocks
    assert "PDF generation guidance after restore." in visible_skill_blocks[-1]["text"]


@pytest.mark.asyncio
async def test_read_range_materializes_even_when_full_path_visible(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_read", outdir=str(tmp_path), workdir=str(tmp_path), max_tokens=80_000)
    ctx = FakeBrowser(runtime)
    out_file = tmp_path / "turn_read" / "outputs" / "visible.md"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text("line 1\nline 2\nline 3\nline 4\n", encoding="utf-8")
    source_path = "fi:turn_read.outputs/visible.md"

    await handle_react_read(
        ctx_browser=ctx,
        state={"last_decision": {"tool_call": {"params": {"paths": [source_path]}}}},
        tool_call_id="r_full",
    )
    await handle_react_read(
        ctx_browser=ctx,
        state={
            "last_decision": {
                "tool_call": {
                    "params": {
                        "items": [{"path": source_path, "line_start": 2, "line_count": 2}]
                    }
                }
            }
        },
        tool_call_id="r_range_after_full",
    )

    range_blocks = [
        b for b in ctx.timeline.blocks
        if b.get("type") == "react.tool.result"
        and b.get("call_id") == "r_range_after_full"
        and b.get("path") == source_path
        and "[READ RANGE]" in (b.get("text") or "")
    ]
    assert range_blocks
    assert "lines: [2-3]/4" in range_blocks[-1]["text"]


@pytest.mark.asyncio
async def test_read_large_image_file_returns_downscaled_multimodal_preview(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    runtime = RuntimeCtx(
        turn_id="turn_read",
        outdir=str(tmp_path),
        workdir=str(tmp_path),
        read_visible_max_bytes=90_000,
    )
    ctx = FakeBrowser(runtime)
    out_file = tmp_path / "turn_read" / "outputs" / "large.png"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(123)
    width = height = 900
    payload = rng.randbytes(width * height * 3)
    Image.frombytes("RGB", (width, height), payload).save(out_file, "PNG")
    assert out_file.stat().st_size > runtime.read_visible_max_bytes

    source_path = "fi:turn_read.outputs/large.png"
    state = {"last_decision": {"tool_call": {"params": {"paths": [source_path]}}}}
    await handle_react_read(ctx_browser=ctx, state=state, tool_call_id="r_img")

    read_blocks = [
        b for b in ctx.timeline.blocks
        if b.get("type") == "react.tool.result" and b.get("call_id") == "r_img"
    ]
    image_block = next(b for b in read_blocks if b.get("path") == source_path and b.get("base64"))
    assert image_block["mime"] == "image/png"
    assert image_block["meta"]["image_view"]["view_kind"] == "image_downscaled"
    assert image_block["meta"]["image_view"]["original_size_bytes"] == out_file.stat().st_size
    assert image_block["meta"]["image_view"]["visible_size_bytes"] <= runtime.read_visible_max_bytes

    status = json.loads(next(
        b["text"]
        for b in read_blocks
        if b.get("path") == "tc:turn_read.r_img.result" and b.get("mime") == "application/json"
    ))
    assert status["paths"][0]["status"] == "image_downscaled_for_visible_context"
    assert status["paths"][0]["image_view"]["visible_size_bytes"] <= runtime.read_visible_max_bytes
