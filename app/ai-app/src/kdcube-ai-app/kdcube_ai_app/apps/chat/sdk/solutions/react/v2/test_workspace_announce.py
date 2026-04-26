# SPDX-License-Identifier: MIT

import json
import pathlib
import subprocess

from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.layout import build_announce_text
from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx


def test_build_announce_text_includes_git_workspace_summary(tmp_path):
    outdir = tmp_path / "out"
    turn_root = outdir / "turn_123"
    (turn_root / "files" / "projectA" / "src").mkdir(parents=True, exist_ok=True)
    (turn_root / "files" / "projectA" / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (outdir / "turn_122" / "files").mkdir(parents=True, exist_ok=True)

    subprocess.run(["git", "init", str(turn_root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(turn_root), "config", "user.name", "Test User"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(turn_root), "config", "user.email", "test@example.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(turn_root), "checkout", "-b", "workspace"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(turn_root), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(turn_root), "commit", "-m", "init"], check=True, capture_output=True)

    runtime = RuntimeCtx(
        turn_id="turn_123",
        outdir=str(outdir),
        workspace_implementation="git",
    )

    publish_block = {
        "type": "react.workspace.publish",
        "turn_id": "turn_122",
        "path": "ar:turn_122.react.workspace.publish",
        "mime": "application/json",
        "text": json.dumps({
            "turn_id": "turn_122",
            "status": "succeeded",
        }),
    }
    checkout_block = {
        "type": "react.workspace.checkout",
        "turn_id": "turn_123",
        "path": "ar:turn_123.react.workspace.checkout",
        "mime": "application/json",
        "text": json.dumps({
            "turn_id": "turn_123",
            "mode": "replace",
            "checked_out_from": ["fi:turn_122.files/projectA"],
        }),
    }

    announce_text = build_announce_text(
        iteration=1,
        max_iterations=6,
        started_at="2026-04-02T10:00:00Z",
        timezone="UTC",
        runtime_ctx=runtime,
        timeline_blocks=[publish_block, checkout_block],
        constraints=None,
        feedback_updates=None,
        feedback_incorporated=False,
        mode="full",
    )

    assert "[WORKSPACE]" in announce_text
    assert "implementation: git" in announce_text
    assert "current_turn_root: turn_123/" in announce_text
    assert "materialized_turn_roots: turn_122, turn_123 (current)" in announce_text
    assert "current_turn_scopes:" in announce_text
    assert "- projectA/ (1 file)" in announce_text
    assert "checkout_mode: replace" in announce_text
    assert "checked_out_from:" in announce_text
    assert "- fi:turn_122.files/projectA" in announce_text
    assert "repo_mode: sparse git repo" in announce_text
    assert "repo_status: clean" in announce_text
    assert "ls workspace:" in announce_text
    assert "- projectA/ (1 file)" in announce_text
    assert "continue_one_by_checkout: react.checkout(mode=\"replace\", paths=[\"fi:<turn>.files/<that_scope>\"])" in announce_text
    assert "current_turn_publish: pending" in announce_text
    assert "last_published_turn: turn_122 (succeeded)" in announce_text


def test_build_announce_text_includes_lineage_scopes_even_when_current_turn_is_sparse(tmp_path):
    outdir = tmp_path / "out"
    turn_root = outdir / "turn_123"
    (turn_root / "files" / "customer_portal" / "src").mkdir(parents=True, exist_ok=True)
    (turn_root / "files" / "customer_portal" / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")

    subprocess.run(["git", "init", str(turn_root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(turn_root), "config", "user.name", "Test User"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(turn_root), "config", "user.email", "test@example.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(turn_root), "checkout", "-b", "workspace"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(turn_root), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(turn_root), "commit", "-m", "init"], check=True, capture_output=True)
    for child in (turn_root / "files").iterdir():
        if child.is_dir():
            import shutil
            shutil.rmtree(child)
        else:
            child.unlink()

    runtime = RuntimeCtx(
        turn_id="turn_123",
        outdir=str(outdir),
        workspace_implementation="git",
    )

    announce_text = build_announce_text(
        iteration=1,
        max_iterations=6,
        started_at="2026-04-02T10:00:00Z",
        timezone="UTC",
        runtime_ctx=runtime,
        timeline_blocks=[],
        constraints=None,
        feedback_updates=None,
        feedback_incorporated=False,
        mode="full",
    )

    assert "current_turn_scopes: none" in announce_text
    assert "ls workspace:" in announce_text
    assert "- customer_portal/ (1 file)" in announce_text


def test_build_announce_text_includes_current_turn_live_events(tmp_path):
    runtime = RuntimeCtx(
        turn_id="turn_123",
        outdir=str(tmp_path / "out"),
        workspace_implementation="custom",
    )

    announce_text = build_announce_text(
        iteration=1,
        max_iterations=6,
        started_at="2026-04-11T10:00:00Z",
        timezone="UTC",
        runtime_ctx=runtime,
        timeline_blocks=[
            {
                "type": "user.followup",
                "turn_id": "turn_123",
                "path": "ar:turn_123.external.followup.evt_1",
                "text": "especially interesting in quantum",
                "meta": {"sequence": 7, "explicit": True},
            },
            {
                "type": "user.steer",
                "turn_id": "turn_123",
                "path": "ar:turn_123.external.steer.evt_2",
                "text": "",
                "meta": {"sequence": 8, "explicit": True},
            },
            {
                "type": "user.followup",
                "turn_id": "turn_old",
                "path": "ar:turn_old.external.followup.evt_0",
                "text": "old turn event should stay out of announce",
                "meta": {"sequence": 6, "explicit": True},
            },
        ],
        constraints=None,
        feedback_updates=None,
        feedback_incorporated=False,
        mode="full",
    )

    assert "[LIVE TURN EVENTS]" in announce_text
    assert "• followup seq=7 explicit=True" in announce_text
    assert "text=especially interesting in quantum" in announce_text
    assert "• steer seq=8 explicit=True" in announce_text
    assert "text=(empty stop control)" in announce_text
    assert "old turn event should stay out of announce" not in announce_text


def test_build_announce_text_explains_reactive_iteration_bonus(tmp_path):
    runtime = RuntimeCtx(
        turn_id="turn_123",
        outdir=str(tmp_path / "out"),
        workspace_implementation="custom",
    )

    announce_text = build_announce_text(
        iteration=3,
        max_iterations=16,
        base_max_iterations=15,
        reactive_iteration_credit=1,
        started_at="2026-04-11T10:00:00Z",
        timezone="UTC",
        runtime_ctx=runtime,
        timeline_blocks=[],
        constraints=None,
        feedback_updates=None,
        feedback_incorporated=False,
        mode="full",
    )

    assert "ANNOUNCE — Iteration 4/16 (15 + 1 reactive bonus)" in announce_text
    assert "iterations" in announce_text
    assert "(base 15 + 1 bonus from live reactive events)" in announce_text
