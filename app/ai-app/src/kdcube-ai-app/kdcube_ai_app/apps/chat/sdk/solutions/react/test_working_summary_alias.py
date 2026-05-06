# SPDX-License-Identifier: MIT

from kdcube_ai_app.apps.chat.sdk.solutions.react.timeline import resolve_artifact_from_timeline


def test_working_summary_canonical_path_resolves_latest_attempt_alias():
    timeline = {
        "blocks": [
            {
                "type": "conv.working.summary",
                "turn_id": "turn_1",
                "ts": "2026-04-26T10:01:00Z",
                "path": "ws:turn_1.conv.working.summary.attempt.1",
                "text": "Goal: first\nOutcome: interrupted",
                "mime": "text/markdown",
                "meta": {
                    "kind": "working_summary",
                    "summary_scope": "completion_attempt",
                    "assistant_completion_attempt_index": 1,
                },
            },
            {
                "type": "conv.working.summary",
                "turn_id": "turn_1",
                "ts": "2026-04-26T10:02:00Z",
                "path": "ws:turn_1.conv.working.summary.attempt.2",
                "text": "Goal: final\nOutcome: complete",
                "mime": "text/markdown",
                "meta": {
                    "kind": "working_summary",
                    "summary_scope": "completion_attempt",
                    "assistant_completion_attempt_index": 2,
                },
            },
        ],
    }

    artifact = resolve_artifact_from_timeline(timeline, "ws:turn_1.conv.working.summary")

    assert artifact
    assert artifact["text"] == "Goal: final\nOutcome: complete"
    assert artifact["path"] == "ws:turn_1.conv.working.summary"
    assert artifact["source_path"] == "ws:turn_1.conv.working.summary.attempt.2"
    assert artifact["alias"] is True
    assert artifact["assistant_completion_attempt_index"] == 2
