# SPDX-License-Identifier: MIT

import json
import pathlib
import subprocess

from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.layout import build_announce_text
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.proto import RuntimeCtx


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
