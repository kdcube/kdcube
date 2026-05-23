# SPDX-License-Identifier: MIT

from __future__ import annotations

import json

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.apps.chat.sdk.runtime.execution import _merge_comm_state_from_runtime_output


class _Relay:
    async def emit(self, **_kwargs) -> None:
        return None


def _make_comm() -> ChatCommunicator:
    return ChatCommunicator(
        emitter=_Relay(),
        tenant="t",
        project="p",
        user_id="u",
        user_type="registered",
        service={"request_id": "r1", "tenant": "t", "project": "p", "user": "u"},
        conversation={"session_id": "s1", "conversation_id": "c1", "turn_id": "turn1"},
    )


def test_merge_comm_state_reads_local_subprocess_delta_cache_from_artifact_workdir(tmp_path):
    outdir = tmp_path / "out"
    workdir = outdir / "workdir"
    workdir.mkdir(parents=True)
    (workdir / "delta_aggregates.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "conversation_id": "c1",
                        "turn_id": "turn1",
                        "agent": "Web search [abc]",
                        "marker": "subsystem",
                        "format": "json",
                        "artifact_name": "Web Search [abc].filtered_results",
                        "title": "Web search",
                        "extra": {"sub_type": "web_search.filtered_results"},
                        "chunks": [{"ts": 1, "idx": 0, "text": '{"results": []}'}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    comm = _make_comm()
    _merge_comm_state_from_runtime_output(comm, outdir)

    items = comm.get_delta_aggregates(conversation_id="c1", turn_id="turn1", merge_text=True)
    assert len(items) == 1
    assert items[0]["marker"] == "subsystem"
    assert items[0]["text"] == '{"results": []}'
    assert items[0]["extra"]["sub_type"] == "web_search.filtered_results"


def test_merge_comm_state_reads_local_subprocess_recorded_events_from_artifact_workdir(tmp_path):
    outdir = tmp_path / "out"
    workdir = outdir / "workdir"
    workdir.mkdir(parents=True)
    (workdir / "comm_recorded_events.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "record_id": "rec-1",
                        "socket_event": "chat_delta",
                        "data": {"type": "chat.delta"},
                    }
                ],
                "dropped": 0,
            }
        ),
        encoding="utf-8",
    )

    comm = _make_comm()
    _merge_comm_state_from_runtime_output(comm, outdir)

    items = comm.export_recorded_events()
    assert len(items) == 1
    assert items[0]["record_id"] == "rec-1"
