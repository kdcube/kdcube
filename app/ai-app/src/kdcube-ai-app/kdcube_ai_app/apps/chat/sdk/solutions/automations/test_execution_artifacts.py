# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import asyncio

from kdcube_ai_app.apps.chat.sdk.solutions.automations.execution_artifacts import (
    artifact_ref_for_execution_artifact,
    materialize_execution_artifact_for_current_turn,
)


def test_materialized_artifact_logical_path_carries_conversation_scope(tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"data")
    artifact = {
        "id": "a1",
        "kind": "file",
        "filename": "src.bin",
        "source_physical_path": str(src),
        "visibility": "external",
    }
    execution = {"id": "exec-1", "automation_id": "auto-1", "artifacts": [artifact]}
    sc = {
        "tenant": "t",
        "project": "p",
        "user_id": "u",
        "turn_id": "turn_9",
        "outdir": str(tmp_path / "out"),
        "storage_root": str(tmp_path),
        "conversation_id": "cur1",
    }
    ref = artifact_ref_for_execution_artifact(execution, artifact, index=0)

    result = asyncio.run(
        materialize_execution_artifact_for_current_turn(
            artifact_ref=ref, execution=execution, sc=sc
        )
    )

    current = result["current_turn"]
    assert current["logical_path"] == (
        "conv:fi:conv_cur1.turn_9.files/recovered-job-artifacts/exec-1/src.bin"
    )
    assert current["physical_path"] == "turn_9/files/recovered-job-artifacts/exec-1/src.bin"
    # The materialized copy exists where the physical path says it does.
    assert (tmp_path / "out" / current["physical_path"]).read_bytes() == b"data"
