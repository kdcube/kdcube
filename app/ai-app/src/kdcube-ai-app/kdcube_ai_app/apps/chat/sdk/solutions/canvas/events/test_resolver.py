from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.workspace import artifact_outdir_for
from kdcube_ai_app.apps.chat.sdk.solutions.canvas.events.policies import (
    canvas_read_block_policy,
    produce_canvas_announce_blocks,
    project_canvas_tool_result_blocks,
)
from kdcube_ai_app.apps.chat.sdk.solutions.canvas.events.resolver import CanvasArtifactResolver
from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx


class _FakeArtifacts:
    def read(self, key: str) -> bytes:
        assert key == "canvas/users/user-1/files/report.html"
        return b"<html>Hello</html>"


class _FakeStore:
    tenant = "tenant"
    project = "project"
    bundle_id = "bundle@1"
    artifact_resolver_name = "canvas.bundle_artifact_storage"
    artifacts = _FakeArtifacts()


def test_canvas_artifact_download_returns_url_not_json_bytes():
    resolver = CanvasArtifactResolver(_FakeStore())  # type: ignore[arg-type]

    result = resolver.download_ref("cnv:canvas/users/user-1/files/report.html", mime="text/html")

    assert result["ok"] is True
    assert result["filename"] == "report.html"
    assert result["mime"] == "text/html"
    assert result["size"] == len(b"<html>Hello</html>")
    assert "content_base64" not in result
    assert result["download_url"].startswith(
        "/api/integrations/bundles/tenant/project/bundle%401/operations/scene_object_download?"
    )
    assert "object_ref=cnv%3Acanvas%2Fusers%2Fuser-1%2Ffiles%2Freport.html" in result["download_url"]


@pytest.mark.asyncio
async def test_canvas_board_ref_open_returns_pinboard_ui_event_not_download():
    resolver = CanvasArtifactResolver(_FakeStore())  # type: ignore[arg-type]

    result = await resolver.object_action(
        {"object_ref": "cnv:main", "action": "open"},
        user_id="user-1",
        action="open",
    )

    assert result["ok"] is True
    assert result["default_open_effect_action"] == "open"
    assert result["capabilities"]["open"] is True
    assert result["capabilities"]["download"] is False
    assert result["canvas_name"] == "main"
    assert result["ui_event"]["target_surface"] == "sdk.canvas.pinboard"
    assert result["ui_event"]["action"] == "open"
    assert result["ui_event"]["canvas_name"] == "main"
    assert "download_url" not in result


def test_canvas_read_stats_policy_emits_original_object_stats_on_block(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_read", outdir=str(tmp_path), workdir=str(tmp_path))
    physical_path = "turn_read/snapshots/cnv/main.json"
    target_path = artifact_outdir_for(tmp_path) / physical_path
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        json.dumps({
            "ok": True,
            "canvas_name": "main",
            "canvas_id": "cnv:user-1:main",
            "revision": 7,
            "canvas_ref": "cnv:main@7",
            "latest_ref": "cnv:main",
            "projection": {
                "canvas_name": "main",
                "canvas_id": "cnv:user-1:main",
                "revision": 7,
                "cards_count": 2,
                "placed_count": 1,
                "floating_count": 1,
                "legend": [{"id": "card-1", "selected": True}],
            },
        }),
        encoding="utf-8",
    )
    target = {
        "stats_only": True,
        "turn_id": "turn_read",
        "tool_call_id": "read_1",
        "tool_id": "canvas.read",
        "event_source_id": "canvas.read",
        "object_ref": "cnv:main",
        "logical_path": "fi:turn_read.snapshots/cnv/main.json",
        "path": "fi:turn_read.snapshots/cnv/main.json",
        "physical_path": physical_path,
        "blocks": [],
    }

    canvas_read_block_policy(target, runtime_ctx=runtime)

    assert target["blocks"]
    stats = target["blocks"][0]["original_object_stats"]
    assert stats["kind"] == "canvas_snapshot"
    assert stats["object_ref"] == "cnv:main"
    assert stats["live_ref"] == "cnv:main"
    assert stats["revision_ref"] == "cnv:main@7"
    assert stats["cards_count"] == 2
    assert stats["selected_card_ids"] == ["card-1"]


def test_canvas_read_policy_projects_materialized_snapshot_for_announce(tmp_path):
    runtime = RuntimeCtx(turn_id="turn_read", outdir=str(tmp_path), workdir=str(tmp_path))
    physical_path = "turn_read/snapshots/cnv/main.json"
    target_path = artifact_outdir_for(tmp_path) / physical_path
    target_path.parent.mkdir(parents=True, exist_ok=True)
    projection = {
        "canvas_name": "main",
        "canvas_id": "cnv:user-1:main",
        "revision": 7,
        "cards_count": 1,
        "placed_count": 1,
        "floating_count": 0,
        "bounds": {"x": 0, "y": 0, "w": 100, "h": 100},
        "legend": [
            {
                "id": "card-1",
                "kind": "memory",
                "title": "Memory",
                "placement": "placed",
                "rect": {"x": 10, "y": 10, "w": 20, "h": 20},
            }
        ],
    }
    target_path.write_text(
        json.dumps({
            "ok": True,
            "canvas_name": "main",
            "canvas_id": "cnv:user-1:main",
            "revision": 7,
            "canvas_ref": "cnv:main@7",
            "latest_ref": "cnv:main",
            "projection": projection,
        }),
        encoding="utf-8",
    )
    target = {
        "turn_id": "turn_read",
        "tool_call_id": "read_1",
        "tool_id": "react.read",
        "event_source_id": "canvas.read",
        "object_ref": "cnv:main",
        "logical_path": "fi:turn_read.snapshots/cnv/main.json",
        "path": "fi:turn_read.snapshots/cnv/main.json",
        "physical_path": physical_path,
        "blocks": [],
        "meta": {
            "object_ref": "cnv:main",
            "physical_path": physical_path,
            "iteration": 2,
        },
    }

    canvas_read_block_policy(target, runtime_ctx=runtime)

    assert target["blocks"]
    block = target["blocks"][0]
    assert block["tool_id"] == "react.read"
    assert block["event_source_id"] == "canvas.read"
    assert block["meta"]["canvas"]["payload"]["projection"]["cards_count"] == 1
    visible = produce_canvas_announce_blocks(
        [],
        timeline_blocks=[block],
        source=SimpleNamespace(event_source_id="canvas.state"),
        current_turn_id="turn_read",
        iteration=2,
        announce_retention_rounds=3,
    )
    assert len(visible) == 1
    assert "[CANVAS BOARD]" in visible[0]["text"]
    assert "visibility: 3/3 render rounds remaining" in visible[0]["text"]
    assert "Memory" in visible[0]["text"]

    projected = project_canvas_tool_result_blocks(
        [dict(block)],
        source=SimpleNamespace(event_source_id="canvas.read"),
        announce_retention_rounds=3,
    )
    assert "[CANVAS TOOL RESULT]" in projected[0]["text"]
    assert "announce_effect: board projection refreshed in ANNOUNCE for 3 render rounds" in projected[0]["text"]
    assert "refresh_rule: use react.pull(paths=['cnv:main'])" in projected[0]["text"]


def test_canvas_announce_policy_shows_and_expires_retention_rounds():
    projection = {
        "canvas_name": "main",
        "canvas_id": "cnv:user-1:main",
        "revision": 7,
        "cards_count": 1,
        "placed_count": 1,
        "floating_count": 0,
        "bounds": {"x": 0, "y": 0, "w": 100, "h": 100},
        "legend": [
            {
                "id": "card-1",
                "kind": "memory",
                "title": "Memory",
                "placement": "placed",
                "rect": {"x": 10, "y": 10, "w": 20, "h": 20},
            }
        ],
    }
    block = {
        "turn": "turn_1",
        "type": "react.tool.result",
        "event_source_id": "canvas.read",
        "path": "cnv:main",
        "text": "{}",
        "meta": {
            "event_source_id": "canvas.read",
            "iteration": 2,
            "canvas": {
                "payload": {
                    "canvas_name": "main",
                    "canvas_id": "cnv:user-1:main",
                    "revision": 7,
                    "canvas_ref": "cnv:main@7",
                    "projection": projection,
                }
            },
        },
    }

    visible = produce_canvas_announce_blocks(
        [],
        timeline_blocks=[block],
        source=SimpleNamespace(event_source_id="canvas.state"),
        current_turn_id="turn_1",
        iteration=2,
        announce_retention_rounds=3,
    )
    assert len(visible) == 1
    assert "visibility: 3/3 render rounds remaining" in visible[0]["text"]
    assert "react.pull(paths=['cnv:main'])" in visible[0]["text"]

    stale = produce_canvas_announce_blocks(
        [],
        timeline_blocks=[block],
        source=SimpleNamespace(event_source_id="canvas.state"),
        current_turn_id="turn_1",
        iteration=5,
        announce_retention_rounds=3,
    )
    assert stale == []


def test_canvas_announce_consolidates_state_and_read_for_same_board():
    projection = {
        "canvas_name": "demo-board",
        "canvas_id": "cnv:user-1:demo-board",
        "revision": 52,
        "cards_count": 1,
        "placed_count": 1,
        "floating_count": 0,
        "bounds": {"x": 0, "y": 0, "w": 100, "h": 100},
        "legend": [
            {
                "id": "card-1",
                "kind": "memory",
                "title": "Memory",
                "placement": "placed",
                "rect": {"x": 10, "y": 10, "w": 20, "h": 20},
            }
        ],
    }
    state_block = {
        "turn": "turn_1",
        "type": "event.canvas",
        "event_source_id": "chat.canvas.state",
        "path": "ev:turn_1.events/demo-board",
        "text": json.dumps({
            "event_ref": "cnv:demo-board",
            "ret": {
                "canvas_name": "demo-board",
                "canvas_id": "cnv:user-1:demo-board",
                "revision": 52,
                "canvas_ref": "cnv:demo-board@52",
                "latest_ref": "cnv:demo-board",
                "projection": projection,
            },
        }),
        "meta": {
            "event_source_id": "chat.canvas.state",
        },
    }
    read_block = {
        "turn": "turn_1",
        "type": "react.tool.result",
        "event_source_id": "canvas.read",
        "path": "cnv:demo-board@52",
        "text": "{}",
        "meta": {
            "event_source_id": "canvas.read",
            "iteration": 2,
            "canvas": {
                "payload": {
                    "canvas_name": "demo-board",
                    "canvas_id": "cnv:user-1:demo-board",
                    "revision": 52,
                    "canvas_ref": "cnv:demo-board@52",
                    "latest_ref": "cnv:demo-board",
                    "projection": projection,
                }
            },
        },
    }
    timeline = [read_block, state_block]

    from_state = produce_canvas_announce_blocks(
        [],
        timeline_blocks=timeline,
        source=SimpleNamespace(event_source_id="chat.canvas.state"),
        current_turn_id="turn_1",
        iteration=3,
        announce_retention_rounds=3,
    )
    from_read = produce_canvas_announce_blocks(
        [],
        timeline_blocks=timeline,
        source=SimpleNamespace(event_source_id="canvas.read"),
        current_turn_id="turn_1",
        iteration=3,
        announce_retention_rounds=3,
    )

    assert len(from_state) == 1
    assert from_read == []
    assert "visibility: 2/3 render rounds remaining" in from_state[0]["text"]
    assert from_state[0]["text"].count("[CANVAS BOARD]") == 1
