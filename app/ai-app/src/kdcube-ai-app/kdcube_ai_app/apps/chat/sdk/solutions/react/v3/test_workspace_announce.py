# SPDX-License-Identifier: MIT

import json
import pathlib
import subprocess

from kdcube_ai_app.apps.chat.sdk.solutions.react.layout import build_announce_text
from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.runtime.workspace import artifact_outdir_for


def test_build_announce_text_includes_git_workspace_summary(tmp_path):
    outdir = tmp_path / "out"
    artifact_outdir = artifact_outdir_for(outdir)
    turn_root = artifact_outdir / "turn_123"
    (turn_root / "files" / "projectA" / "src").mkdir(parents=True, exist_ok=True)
    (turn_root / "files" / "projectA" / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (artifact_outdir / "turn_122" / "snapshots").mkdir(parents=True, exist_ok=True)
    (artifact_outdir / "turn_122" / "snapshots" / "old.json").write_text("{}", encoding="utf-8")

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
    assert "current_turn_root: turn_123/" in announce_text
    # LOCAL material tree: current root is writable, pulled prior root is read-only
    assert "materialized on disk THIS turn." in announce_text
    assert "turn_123/" in announce_text and "writable" in announce_text
    assert "turn_122/" in announce_text and "read-only" in announce_text
    # checkout provenance is shown on the current project
    assert "checked out from fi:turn_122.files/projectA" in announce_text
    # REMOTE: one latest-committed-turn anchor builds every pull/checkout ref
    assert "REMOTE git branch" in announce_text
    assert "latest committed turn: turn_122" in announce_text
    assert "fi:turn_122.files/<project>" in announce_text
    assert "files/projectA" in announce_text
    assert "[writable in current turn]" in announce_text
    assert 'react.checkout(mode="replace", paths=["fi:turn_122.files/projectA"])' in announce_text


def test_build_announce_text_lists_actual_workdir_paths_except_files_tree(tmp_path):
    outdir = tmp_path / "out"
    artifact_outdir = artifact_outdir_for(outdir)
    turn_root = artifact_outdir / "turn_123"
    (turn_root / "files" / "projectA" / "src").mkdir(parents=True, exist_ok=True)
    (turn_root / "files" / "projectA" / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (turn_root / "snapshots" / "cnv").mkdir(parents=True, exist_ok=True)
    (turn_root / "snapshots" / "cnv" / "main.json").write_text("{}", encoding="utf-8")
    (turn_root / "external" / "external_event" / "attachments" / "evt_1").mkdir(parents=True, exist_ok=True)
    (turn_root / "external" / "external_event" / "attachments" / "evt_1" / "diagram.svg").write_text("<svg />", encoding="utf-8")
    (artifact_outdir / "diagnostics").mkdir(parents=True, exist_ok=True)
    (artifact_outdir / "diagnostics" / "note.txt").write_text("local", encoding="utf-8")

    runtime = RuntimeCtx(
        turn_id="turn_123",
        outdir=str(outdir),
        workspace_implementation="custom",
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

    assert "external/" in announce_text
    assert "external_event/attachments/evt_1/diagram.svg" in announce_text
    assert "snapshots/" in announce_text
    assert "cnv/main.json" in announce_text
    assert "diagnostics/" not in announce_text
    assert "note.txt" not in announce_text
    assert "projectA/" in announce_text
    assert "src/app.py" not in announce_text


def test_build_announce_text_renders_empty_current_turn_namespaces(tmp_path):
    outdir = tmp_path / "out"
    artifact_outdir = artifact_outdir_for(outdir)
    turn_root = artifact_outdir / "turn_123"
    (turn_root / "files").mkdir(parents=True, exist_ok=True)
    (turn_root / "outputs").mkdir(parents=True, exist_ok=True)
    (turn_root / "snapshots").mkdir(parents=True, exist_ok=True)

    runtime = RuntimeCtx(
        turn_id="turn_123",
        outdir=str(outdir),
        workspace_implementation="custom",
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

    assert "Timeline fi: refs that are not listed here are hosted/unhydrated" in announce_text
    assert "use react.pull to hydrate them before local-byte tools" in announce_text
    assert "react.read may inspect provider-rendered text" in announce_text
    assert "turn_123/   (current turn · writable)" in announce_text
    assert "    files/ (empty)" in announce_text
    assert "    outputs/ (empty)" in announce_text
    assert "    attachments/ (empty)" in announce_text
    assert "    external/ (empty)" in announce_text
    assert "    snapshots/ (empty)" in announce_text
    assert "      (empty)" not in announce_text
    assert "(no materialized files in the artifact workdir yet)" not in announce_text


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
    assert "tool_result_preview=12000" in announce_text
    assert "exec_file_preview=8000" in announce_text
    assert "regular text" in announce_text
    assert "skills are always uncapped" in announce_text
    assert "ranged react.read items" in announce_text
    assert "exec_stdout=capped" in announce_text


def test_build_announce_text_recomputes_runtime_limits_each_round(tmp_path):
    outdir = tmp_path / "out"
    workdir = tmp_path / "work"
    (outdir / "turn_123" / "outputs").mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)
    (outdir / "turn_123" / "outputs" / "report.txt").write_bytes(b"123456")
    (workdir / "scratch.bin").write_bytes(b"1234")
    runtime = RuntimeCtx(
        turn_id="turn_123",
        outdir=str(outdir),
        workdir=str(workdir),
        exec_runtime={
            "max_file_bytes": "8b",
            "max_exec_workspace_delta_bytes": "12b",
            "max_workspace_bytes": "20b",
        },
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

    assert "[RUNTIME LIMITS]" in announce_text
    assert "exec file max=8B; exec workspace delta max=12B; active workspace max=20B" in announce_text
    assert "active workspace used=10B across 2 files; remaining=10B" in announce_text
    assert "next exec new bytes max=10B; effective single new file max=8B" in announce_text
    assert "recomputed each round" in announce_text

    (workdir / "later.bin").write_bytes(b"12345")
    next_announce_text = build_announce_text(
        iteration=2,
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

    assert "active workspace used=15B across 3 files; remaining=5B" in next_announce_text
    assert "next exec new bytes max=5B; effective single new file max=5B" in next_announce_text


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

    # lineage projects still surface under REMOTE even when the local tree is sparse
    assert "REMOTE git branch" in announce_text
    assert "files/workspace_app" in announce_text


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
                "meta": {"sequence": 7, "explicit": True, "batch_id": "batch_1"},
            },
            {
                "type": "user.attachment.meta",
                "turn_id": "turn_123",
                "path": "fi:turn_123.external.external_event.attachments/evt_1/diagram.svg",
                "meta": {
                    "sequence": 7,
                    "batch_id": "batch_1",
                    "message_id": "evt_1",
                    "filename": "diagram.svg",
                    "mime": "image/svg+xml",
                },
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
    assert "attachments=1" in announce_text
    assert "attachment.1 diagram.svg | image/svg+xml | ref=fi:turn_123.external.external_event.attachments/evt_1/diagram.svg" in announce_text
    assert "• steer seq=8 explicit=True" in announce_text
    assert "text=(empty stop control)" in announce_text
    assert "old turn event should stay out of announce" not in announce_text


def test_build_announce_text_shows_live_turn_event_tail_count(tmp_path):
    runtime = RuntimeCtx(
        turn_id="turn_123",
        outdir=str(tmp_path / "out"),
        workspace_implementation="custom",
    )
    timeline_blocks = [
        {
            "type": "user.followup",
            "turn_id": "turn_123",
            "path": f"ar:turn_123.external.followup.evt_{idx}",
            "text": f"followup {idx}",
            "meta": {"sequence": idx, "explicit": True},
        }
        for idx in range(1, 7)
    ]

    announce_text = build_announce_text(
        iteration=2,
        max_iterations=8,
        started_at="2026-04-11T10:00:00Z",
        timezone="UTC",
        runtime_ctx=runtime,
        timeline_blocks=timeline_blocks,
        constraints=None,
        feedback_updates=None,
        feedback_incorporated=False,
        mode="full",
    )

    assert "events: showing last 4 of 6" in announce_text
    assert "followup 1" not in announce_text
    assert "followup 2" not in announce_text
    assert "followup 3" in announce_text
    assert "followup 6" in announce_text


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
