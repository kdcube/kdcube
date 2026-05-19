# SPDX-License-Identifier: MIT

import json
import pathlib
import subprocess

from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.layout import build_announce_text
from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.runtime.workspace import artifact_outdir_for


def test_build_announce_text_includes_git_workspace_summary(tmp_path):
    outdir = tmp_path / "out"
    artifact_outdir = artifact_outdir_for(outdir)
    turn_root = artifact_outdir / "turn_123"
    (turn_root / "files" / "projectA" / "src").mkdir(parents=True, exist_ok=True)
    (turn_root / "files" / "projectA" / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (artifact_outdir / "turn_122" / "files").mkdir(parents=True, exist_ok=True)

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
    assert "local turn roots: turn_122 (read-only), turn_123 (current)" in announce_text
    assert "current editable workspace:" in announce_text
    assert "- files/projectA/ (1 file)" in announce_text
    assert "checkout_mode: replace" in announce_text
    assert "checked_out_from:" in announce_text
    assert "- fi:turn_122.files/projectA" in announce_text
    assert "repo_mode: sparse git repo" in announce_text
    assert "repo_status: clean" in announce_text
    assert "previous saved workspace paths (pull to bring local; checkout to edit):" in announce_text
    assert "- files/projectA/ (1 git-tracked file)" in announce_text
    assert "to focus on one path, use its fi: form, for example:" in announce_text
    assert "react.pull(paths=[\"fi:turn_122.files/projectA\"])" in announce_text
    assert "react.checkout(mode=\"replace\", paths=[\"fi:turn_122.files/projectA\"])" in announce_text
    assert "current_turn_publish: pending" in announce_text
    assert "last_published_turn: turn_122 (succeeded)" in announce_text


def test_build_announce_text_includes_context_caps(tmp_path):
    runtime = RuntimeCtx(
        turn_id="turn_123",
        outdir=str(tmp_path / "out"),
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

    assert "[CONTEXT CAPS]" in announce_text
    assert "read text=48000 tok=12000 bytes=10MB ctx_frac=0.15" in announce_text
    assert "ks_read text=none tok=none bytes=none" in announce_text
    assert "tool_result_preview=12000" in announce_text
    assert "exec_file_preview=8000" in announce_text
    assert "regular text" in announce_text
    assert "skills are always uncapped" in announce_text
    assert "ks: is uncapped unless knowledge_read_visible_* caps are configured" in announce_text
    assert "ranged react.read items" in announce_text
    assert "exec_stdout=capped" in announce_text


def test_build_announce_text_includes_lineage_scopes_even_when_current_turn_is_sparse(tmp_path):
    outdir = tmp_path / "out"
    artifact_outdir = artifact_outdir_for(outdir)
    turn_root = artifact_outdir / "turn_123"
    (turn_root / "files" / "workspace_app" / "src").mkdir(parents=True, exist_ok=True)
    (turn_root / "files" / "workspace_app" / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")

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

    assert "current editable workspace: none" in announce_text
    assert "previous saved workspace paths (pull to bring local; checkout to edit):" in announce_text
    assert "- files/workspace_app/ (1 git-tracked file)" in announce_text


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


def test_build_announce_text_includes_read_only_memory_hotset(tmp_path):
    runtime = RuntimeCtx(
        turn_id="turn_123",
        outdir=str(tmp_path / "out"),
        workspace_implementation="custom",
        memory_enabled=True,
        memory_announce_enabled=True,
        memory_scope_filter="current_bundle",
        memory_hotset=[
            {
                "id": "mem_1",
                "bundle_id": "demo@marketing",
                "memory": "The user prefers concise engineering explanations with concrete failure modes.",
                "context": "Observed across React protocol debugging turns.",
                "tier": 1,
                "confidence_score": 0.91,
                "salience_score": 0.84,
                "labels": ["preference", "engineering"],
                "updated_at": "2026-05-14T12:00:00+00:00",
            }
        ],
    )

    announce_text = build_announce_text(
        iteration=1,
        max_iterations=6,
        started_at="2026-04-11T10:00:00Z",
        timezone="UTC",
        runtime_ctx=runtime,
        timeline_blocks=[],
        constraints=None,
        feedback_updates=None,
        feedback_incorporated=False,
        mode="full",
    )

    assert "[USER MEMORY HOTSET]" in announce_text
    assert "policy: read-only durable user memory" in announce_text
    assert "format: memory text carries the trigger+rule; context is why/provenance/examples only." in announce_text
    assert "scope_filter: current_bundle" in announce_text
    assert "me:mem_1" in announce_text
    assert "bundle=demo@marketing" in announce_text
    assert "The user prefers concise engineering explanations" in announce_text
    assert "context=Observed across React protocol debugging turns." in announce_text


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
