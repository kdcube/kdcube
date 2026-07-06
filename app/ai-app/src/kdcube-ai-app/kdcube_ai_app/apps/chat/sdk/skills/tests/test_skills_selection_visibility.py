# SPDX-License-Identifier: MIT

"""Skills side of per-user agent selection.

Covers the picker catalog (flattened concrete skills with front-matter), the
"*" catch-all consumer deny layer, disabled applying on top of enabled
patterns, and the existing required-tool auto-hide interacting with a
narrowed tool set.
"""

from __future__ import annotations

import pathlib
import textwrap

import pytest

from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import (
    SkillsSubsystem,
    set_active_skills_subsystem,
    skills_for_consumer,
)


def _write_skill(root: pathlib.Path, category: str, sid: str, body: str) -> None:
    folder = root / category / sid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "skill.yaml").write_text(textwrap.dedent(body), encoding="utf-8")


@pytest.fixture()
def skills_root(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "skills"
    _write_skill(root, "research", "deep_dive", """
        id: deep_dive
        description: Dig into a topic with citations.
        namespace: custom
        when_to_use:
          - multi-source questions
    """)
    _write_skill(root, "research", "quick_scan", """
        id: quick_scan
        description: Fast single-pass overview.
        namespace: custom
    """)
    _write_skill(root, "research", "pipeline_glue", """
        id: pipeline_glue
        description: Internal plumbing.
        namespace: internal
    """)
    _write_skill(root, "research", "quiet_helper", """
        id: quiet_helper
        description: Hidden from galleries and pickers.
        namespace: custom
        agent_disclosure: hidden
    """)
    _write_skill(root, "research", "needs_web", """
        id: needs_web
        description: Requires the web search tool.
        namespace: custom
        tools:
          - id: web_tools.web_search
            required: true
    """)
    return root


def _subsystem(skills_root: pathlib.Path, agents_config: dict | None = None) -> SkillsSubsystem:
    return SkillsSubsystem(
        descriptor={
            "custom_skills_root": str(skills_root),
            "agents_config": dict(agents_config or {}),
        },
    )


def _ids(specs) -> set[str]:
    return {f"{s.namespace}.{s.id}" for s in specs}


# ── picker catalog ────────────────────────────────────────────────────────────


def test_picker_catalog_flattens_patterns_with_front_matter(skills_root):
    catalog = _subsystem(skills_root).picker_catalog(["custom.*"])
    by_id = {s["id"]: s for s in catalog}

    assert set(by_id) == {"custom.deep_dive", "custom.quick_scan", "custom.needs_web"}
    assert by_id["custom.deep_dive"]["description"] == "Dig into a topic with citations."
    assert by_id["custom.deep_dive"]["when_to_use"] == ["multi-source questions"]
    assert by_id["custom.deep_dive"]["namespace"] == "custom"


def test_picker_catalog_skips_internal_and_hidden_disclosure(skills_root):
    catalog = _subsystem(skills_root).picker_catalog(None)
    ids = {s["id"] for s in catalog}
    assert "internal.pipeline_glue" not in ids
    assert "custom.quiet_helper" not in ids
    # No patterns -> the whole (pickable) registry, so custom skills appear.
    assert "custom.deep_dive" in ids


def test_picker_catalog_concrete_id_pattern(skills_root):
    catalog = _subsystem(skills_root).picker_catalog(["custom.quick_scan"])
    assert [s["id"] for s in catalog] == ["custom.quick_scan"]


# ── consumer visibility with per-user denials ─────────────────────────────────


def test_star_consumer_denial_applies_to_unlisted_consumers(skills_root):
    set_active_skills_subsystem(
        _subsystem(skills_root, {"*": {"disabled": ["custom.quick_scan"]}})
    )
    visible = _ids(skills_for_consumer("some.random.consumer"))
    assert "custom.quick_scan" not in visible
    assert "custom.deep_dive" in visible


def test_disabled_applies_on_top_of_enabled_patterns(skills_root):
    set_active_skills_subsystem(
        _subsystem(
            skills_root,
            {
                "solver.react.v2.decision.v2.strong": {
                    "enabled": ["custom.*"],
                    "disabled": ["custom.quick_scan"],
                },
            },
        )
    )
    visible = _ids(skills_for_consumer("solver.react.v2.decision.v2.strong"))
    assert "custom.deep_dive" in visible
    assert "custom.quick_scan" not in visible


def test_star_denial_merges_with_consumer_config(skills_root):
    set_active_skills_subsystem(
        _subsystem(
            skills_root,
            {
                "picky": {"enabled": ["custom.*"]},
                "*": {"disabled": ["custom.deep_dive"]},
            },
        )
    )
    visible = _ids(skills_for_consumer("picky"))
    assert "custom.deep_dive" not in visible
    assert "custom.quick_scan" in visible


# ── required-tool auto-hide with a narrowed tool set ──────────────────────────


def test_skill_auto_hidden_when_required_tool_disabled(skills_root):
    set_active_skills_subsystem(_subsystem(skills_root))

    with_web = [{"id": "web_tools.web_search"}, {"id": "io_tools.tool_call"}]
    without_web = [{"id": "io_tools.tool_call"}]

    assert "custom.needs_web" in _ids(skills_for_consumer("any", tool_catalog=with_web))
    # The user turned web_tools off -> the tool vanished from the catalog ->
    # the skill that requires it auto-hides (existing enforcement, reused).
    assert "custom.needs_web" not in _ids(skills_for_consumer("any", tool_catalog=without_web))
