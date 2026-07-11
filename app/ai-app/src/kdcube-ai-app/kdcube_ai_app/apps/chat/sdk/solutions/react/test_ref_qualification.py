# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Qualification-at-birth for conversation-scoped refs.

Every ref the runtime emits carries its `conv_<conversation_id>.` scope
segment; the engineering layer resolves both dialects (legacy
conversation-local refs keep resolving, forever). These tests pin:
  - the canonical helpers (qualify / localize / text pass),
  - the render funnel (model-visible text is qualified),
  - dual-dialect resolution in the timeline,
  - the zero-signal rule over instruction sources (no model-visible
    example shows an unqualified conversation-scoped ref body).
"""

from __future__ import annotations

import pathlib
import re

from kdcube_ai_app.apps.chat.sdk.solutions.react.artifacts import (
    localize_conversation_ref,
    qualify_conversation_ref,
    qualify_conversation_refs_in_text,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.timeline import (
    resolve_artifact_from_timeline,
)

CID = "662927de-770b-4190-a39d-557acf99d664"


# ---------------------------------------------------------------- helpers


def test_qualify_inserts_scope_segment():
    assert (
        qualify_conversation_ref("conv:fi:turn_1.files/x.md", CID)
        == f"conv:fi:conv_{CID}.turn_1.files/x.md"
    )
    assert (
        qualify_conversation_ref("conv:ar:turn_2.react.turn.index", CID)
        == f"conv:ar:conv_{CID}.turn_2.react.turn.index"
    )
    assert (
        qualify_conversation_ref("conv:so:sources_pool[1-4]", CID)
        == f"conv:so:conv_{CID}.sources_pool[1-4]"
    )


def test_qualify_is_idempotent_and_preserves_origin():
    own = f"conv:fi:conv_{CID}.turn_1.files/x.md"
    assert qualify_conversation_ref(own, CID) == own
    foreign = "conv:fi:conv_other-conv.turn_1.files/x.md"
    assert qualify_conversation_ref(foreign, CID) == foreign


def test_qualify_passes_through_non_conversation_refs():
    for ref in ("sk:skill/name", "ks:doc/1", "turn_1/files/x.md", "", None):
        assert qualify_conversation_ref(ref, CID) == ref
    assert qualify_conversation_ref("conv:fi:turn_1.files/x.md", "") == "conv:fi:turn_1.files/x.md"


def test_localize_strips_only_own_segment():
    own = f"conv:fi:conv_{CID}.turn_1.files/x.md"
    assert localize_conversation_ref(own, CID) == "conv:fi:turn_1.files/x.md"
    foreign = "conv:fi:conv_other-conv.turn_1.files/x.md"
    assert localize_conversation_ref(foreign, CID) == foreign
    bare = "conv:fi:turn_1.files/x.md"
    assert localize_conversation_ref(bare, CID) == bare


def test_localize_qualify_roundtrip():
    bare = "conv:ws:turn_3.conv.working.summary"
    assert localize_conversation_ref(qualify_conversation_ref(bare, CID), CID) == bare


# ---------------------------------------------------------------- text pass


def test_text_pass_qualifies_every_ref_kind():
    text = (
        "wrote conv:fi:turn_1.files/report.md; index at conv:ar:turn_1.react.turn.index; "
        "sources conv:so:sources_pool[1,3-5]; plan conv:ar:plan.latest:p1; "
        "summary conv:ws:turn_2.conv.working.summary; call conv:tc:turn_1.tc_1.result"
    )
    out = qualify_conversation_refs_in_text(text, CID)
    assert f"conv:fi:conv_{CID}.turn_1.files/report.md" in out
    assert f"conv:ar:conv_{CID}.turn_1.react.turn.index" in out
    assert f"conv:so:conv_{CID}.sources_pool[1,3-5]" in out
    assert f"conv:ar:conv_{CID}.plan.latest:p1" in out
    assert f"conv:ws:conv_{CID}.turn_2.conv.working.summary" in out
    assert f"conv:tc:conv_{CID}.turn_1.tc_1.result" in out


def test_text_pass_is_idempotent_and_keeps_foreign_and_vocabulary():
    text = (
        f"own conv:fi:conv_{CID}.turn_1.files/x.md, foreign "
        "conv:fi:conv_other.turn_9.files/y.md, the conv:fi: namespace, "
        "timestamp turn conv:tc:2026-07-10-17-33-01-001.c1.call"
    )
    once = qualify_conversation_refs_in_text(text, CID)
    assert qualify_conversation_refs_in_text(once, CID) == once
    assert f"conv:fi:conv_{CID}.turn_1.files/x.md" in once
    assert "conv:fi:conv_other.turn_9.files/y.md" in once
    assert "the conv:fi: namespace" in once
    assert f"conv:tc:conv_{CID}.2026-07-10-17-33-01-001.c1.call" in once


# ---------------------------------------------------------------- resolution


def _timeline_with_file_block(path: str, *, meta_path: str | None = None) -> dict:
    return {
        "blocks": [
            {
                "type": "react.tool.result",
                "turn_id": "turn_1",
                "path": path,
                "mime": "text/markdown",
                "text": "body",
                "meta": {"artifact_path": meta_path or path, "physical_path": "turn_1/files/x.md"},
            }
        ],
        "sources_pool": [],
    }


def test_resolve_accepts_legacy_ref_against_legacy_block():
    tl = _timeline_with_file_block("conv:fi:turn_1.files/x.md")
    art = resolve_artifact_from_timeline(tl, "conv:fi:turn_1.files/x.md", current_conversation_id=CID)
    assert art and art.get("text") == "body"


def test_resolve_accepts_qualified_ref_against_legacy_block():
    tl = _timeline_with_file_block("conv:fi:turn_1.files/x.md")
    art = resolve_artifact_from_timeline(
        tl, f"conv:fi:conv_{CID}.turn_1.files/x.md", current_conversation_id=CID
    )
    assert art and art.get("text") == "body"


def test_resolve_accepts_legacy_ref_against_qualified_block():
    tl = _timeline_with_file_block(f"conv:fi:conv_{CID}.turn_1.files/x.md")
    art = resolve_artifact_from_timeline(
        tl, "conv:fi:turn_1.files/x.md", current_conversation_id=CID
    )
    assert art and art.get("text") == "body"


def test_resolve_foreign_qualified_ref_does_not_match_local_same_body_block():
    tl = _timeline_with_file_block("conv:fi:turn_1.files/x.md")
    art = resolve_artifact_from_timeline(
        tl, "conv:fi:conv_other.turn_1.files/x.md", current_conversation_id=CID
    )
    assert art is None


def test_resolve_foreign_qualified_ref_matches_its_exact_stored_form():
    # Fork-copied blocks keep their origin-qualified paths and stay resolvable.
    tl = _timeline_with_file_block("conv:fi:conv_other.turn_9.files/y.md")
    art = resolve_artifact_from_timeline(
        tl, "conv:fi:conv_other.turn_9.files/y.md", current_conversation_id=CID
    )
    assert art and art.get("text") == "body"


def test_resolve_own_qualified_sources_pool_selector_resolves_locally():
    tl = {"blocks": [], "sources_pool": [{"sid": 1, "title": "s1"}, {"sid": 2, "title": "s2"}]}
    art = resolve_artifact_from_timeline(
        tl, f"conv:so:conv_{CID}.sources_pool[1]", current_conversation_id=CID
    )
    assert art and art.get("kind") == "sources_pool"
    assert [r.get("sid") for r in art.get("items") or []] == [1]
    # foreign pool selectors are not resolved against this timeline
    assert (
        resolve_artifact_from_timeline(
            tl, "conv:so:conv_other.sources_pool[1]", current_conversation_id=CID
        )
        is None
    )


def test_resolve_qualified_working_summary_alias():
    tl = {
        "blocks": [
            {
                "type": "conv.working.summary",
                "turn_id": "turn_1",
                "path": "conv:ws:turn_1.conv.working.summary.attempt.1",
                "text": "Goal: final",
                "mime": "text/markdown",
                "meta": {"kind": "working_summary"},
            }
        ],
        "sources_pool": [],
    }
    art = resolve_artifact_from_timeline(
        tl, f"conv:ws:conv_{CID}.turn_1.conv.working.summary", current_conversation_id=CID
    )
    assert art and art.get("alias") is True and art.get("text") == "Goal: final"


# ---------------------------------------------------------------- render funnel


def test_render_funnel_qualifies_block_paths_and_text():
    from kdcube_ai_app.apps.chat.sdk.solutions.react.timeline import Timeline
    from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx

    ctx = RuntimeCtx(turn_id="turn_1", started_at="2026-07-10T00:00:00Z", conversation_id=CID)
    tl = Timeline(runtime=ctx)
    tl.blocks.append(
        {
            "type": "react.tool.result",
            "turn_id": "turn_1",
            "path": "conv:fi:turn_1.files/report.md",
            "mime": "text/markdown",
            "text": "see conv:fi:turn_1.files/report.md and conv:ar:turn_1.react.turn.index",
        }
    )
    msg_blocks = tl._blocks_to_message_blocks(tl.blocks)
    joined = "\n".join(b.get("text") or "" for b in msg_blocks if isinstance(b, dict))
    assert f"conv:fi:conv_{CID}.turn_1.files/report.md" in joined
    assert f"conv:ar:conv_{CID}.turn_1.react.turn.index" in joined
    assert "conv:fi:turn_1.files/report.md and" not in joined


def test_render_funnel_is_stable_across_repeat_renders():
    from kdcube_ai_app.apps.chat.sdk.solutions.react.timeline import Timeline
    from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx

    ctx = RuntimeCtx(turn_id="turn_1", started_at="2026-07-10T00:00:00Z", conversation_id=CID)
    tl = Timeline(runtime=ctx)
    tl.blocks.append(
        {
            "type": "react.tool.result",
            "turn_id": "turn_1",
            "path": "conv:fi:turn_1.files/report.md",
            "mime": "text/markdown",
            "text": "see conv:fi:turn_1.files/report.md",
        }
    )
    first = [dict(b) for b in tl._blocks_to_message_blocks(tl.blocks)]
    second = [dict(b) for b in tl._blocks_to_message_blocks(tl.blocks)]
    assert [b.get("text") for b in first] == [b.get("text") for b in second]


def test_visible_paths_carries_both_dialects():
    from kdcube_ai_app.apps.chat.sdk.solutions.react.timeline import Timeline
    from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx

    ctx = RuntimeCtx(turn_id="turn_1", started_at="2026-07-10T00:00:00Z", conversation_id=CID)
    tl = Timeline(runtime=ctx)
    tl.blocks.append(
        {
            "type": "react.tool.result",
            "turn_id": "turn_1",
            "path": "conv:fi:turn_1.files/legacy.md",
            "mime": "text/markdown",
            "text": "legacy",
        }
    )
    tl.blocks.append(
        {
            "type": "react.tool.result",
            "turn_id": "turn_1",
            "path": f"conv:fi:conv_{CID}.turn_1.files/minted.md",
            "mime": "text/markdown",
            "text": "minted",
        }
    )
    visible = tl.visible_paths()
    assert "conv:fi:turn_1.files/legacy.md" in visible
    assert f"conv:fi:conv_{CID}.turn_1.files/legacy.md" in visible
    assert f"conv:fi:conv_{CID}.turn_1.files/minted.md" in visible
    assert "conv:fi:turn_1.files/minted.md" in visible


# ---------------------------------------------------------------- zero signal

# Model-visible instruction sources: every conversation-scoped ref example
# must carry a scope segment (a `conv_...` placeholder or literal). A ref
# body starting right after the namespace is the signal we forbid.
_INSTRUCTION_SOURCES = (
    "skills/instructions/shared_instructions.py",
    "skills/instructions/shared_instructions_lite.py",
    "solutions/react/v3/agents/decision.py",
    "solutions/react/v2/agents/decision.py",
    "tools/backends/summary/conv_progressive_summary.py",
)

_UNQUALIFIED_EXAMPLE_RE = re.compile(
    r"conv:(?:fi|ar|ws|tc|so|su|ev):(?:(?:telegram_)?turn_|sources_pool\[|plan\.latest:)"
)


def test_instruction_sources_show_only_qualified_ref_examples():
    sdk_root = pathlib.Path(__file__).resolve().parents[2]
    offenders: list[str] = []
    for rel in _INSTRUCTION_SOURCES:
        path = sdk_root / rel
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _UNQUALIFIED_EXAMPLE_RE.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, "\n".join(offenders)
