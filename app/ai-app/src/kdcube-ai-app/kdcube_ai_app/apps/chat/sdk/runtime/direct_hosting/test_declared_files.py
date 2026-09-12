"""The turn hosts the files its tools declared, with the declared visibility."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.tool_runtime import (
    DECLARED_FILES_FILENAME,
    DirectToolRuntime,
    read_declared_files,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.workspace import (
    DirectTurnWorkspace,
)


TURN_ID = "turn_01_abcdef"


def _workspace(tmp_path: Path) -> DirectTurnWorkspace:
    return DirectTurnWorkspace(run_root=tmp_path / "run_1", turn_id=TURN_ID)


def _runtime(workspace: DirectTurnWorkspace) -> DirectToolRuntime:
    runtime = DirectToolRuntime.__new__(DirectToolRuntime)
    runtime.workspace = workspace
    runtime._declared_files = {}
    runtime.logger = SimpleNamespace(log=lambda *a, **k: None)
    return runtime


def _write_artifact(workspace: DirectTurnWorkspace, relative: str) -> Path:
    path = workspace.artifact_path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"payload")
    return path


def _exec_result(*items: dict) -> dict:
    return {"ok": True, "items": list(items)}


def _file_item(
    *,
    artifact_id: str,
    relative: str,
    mime: str,
    visibility: str,
    error: str | None = None,
) -> dict:
    item = {
        "artifact_id": artifact_id,
        "output": {
            "type": "file",
            "path": relative,
            "filename": Path(relative).name,
            "mime": mime,
            "visibility": visibility,
        },
    }
    if error:
        item["error"] = error
    return item


def test_declared_visibility_is_not_replaced_by_an_assumption(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    runtime = _runtime(workspace)
    _write_artifact(workspace, f"{TURN_ID}/files/research/brief.html")
    _write_artifact(workspace, f"{TURN_ID}/files/research/data.xlsx")

    runtime._record_declared_files(
        _exec_result(
            _file_item(
                artifact_id="brief",
                relative=f"{TURN_ID}/files/research/brief.html",
                mime="text/html",
                visibility="external",
            ),
            _file_item(
                artifact_id="data",
                relative=f"{TURN_ID}/files/research/data.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                visibility="internal",
            ),
        ),
        tool_id="execute_python",
    )

    by_name = {row["output"]["filename"]: row for row in runtime.declared_files()}
    # The demo's fixed expectation had these two the other way round.
    assert by_name["brief.html"]["visibility"] == "external"
    assert by_name["data.xlsx"]["visibility"] == "internal"


def test_any_filename_the_agent_chose_is_hosted(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    runtime = _runtime(workspace)
    relative = f"{TURN_ID}/files/research/python_release_evidence.xlsx"
    _write_artifact(workspace, relative)

    runtime._record_declared_files(
        _exec_result(
            _file_item(
                artifact_id="evidence",
                relative=relative,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                visibility="external",
            )
        ),
        tool_id="execute_python",
    )

    rows = runtime.declared_files()
    assert [row["output"]["filename"] for row in rows] == [
        "python_release_evidence.xlsx"
    ]


def test_a_declared_file_that_was_never_written_is_not_hosted(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    runtime = _runtime(workspace)

    runtime._record_declared_files(
        _exec_result(
            _file_item(
                artifact_id="missing",
                relative=f"{TURN_ID}/files/research/missing.pdf",
                mime="application/pdf",
                visibility="external",
            )
        ),
        tool_id="execute_python",
    )

    assert runtime.declared_files() == []


def test_a_failed_tool_call_declares_nothing(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    runtime = _runtime(workspace)
    _write_artifact(workspace, f"{TURN_ID}/files/research/data.xlsx")

    runtime._record_declared_files(
        {
            "ok": False,
            "items": [
                _file_item(
                    artifact_id="data",
                    relative=f"{TURN_ID}/files/research/data.xlsx",
                    mime="application/octet-stream",
                    visibility="external",
                )
            ],
        },
        tool_id="execute_python",
    )

    assert runtime.declared_files() == []


def test_an_item_that_carries_an_error_is_skipped(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    runtime = _runtime(workspace)
    _write_artifact(workspace, f"{TURN_ID}/files/research/data.xlsx")

    runtime._record_declared_files(
        _exec_result(
            _file_item(
                artifact_id="data",
                relative=f"{TURN_ID}/files/research/data.xlsx",
                mime="application/octet-stream",
                visibility="external",
                error="xlsx_open_failed",
            )
        ),
        tool_id="execute_python",
    )

    assert runtime.declared_files() == []


def test_rewriting_the_same_path_keeps_one_declaration(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    runtime = _runtime(workspace)
    relative = f"{TURN_ID}/files/research/brief.pdf"
    _write_artifact(workspace, relative)

    for visibility in ("internal", "external"):
        runtime._record_declared_files(
            _exec_result(
                _file_item(
                    artifact_id="brief",
                    relative=relative,
                    mime="application/pdf",
                    visibility=visibility,
                )
            ),
            tool_id="execute_python",
        )

    rows = runtime.declared_files()
    assert len(rows) == 1
    assert rows[0]["visibility"] == "external"


def test_the_declaration_reaches_a_host_in_another_process(tmp_path: Path) -> None:
    # Claude Code reaches these tools through the stdio tool server, so the
    # process that hosts the turn never holds the runtime that ran them.
    workspace = _workspace(tmp_path)
    server_side = _runtime(workspace)
    relative = f"{TURN_ID}/files/research/brief.pdf"
    _write_artifact(workspace, relative)

    server_side._record_declared_files(
        _exec_result(
            _file_item(
                artifact_id="brief",
                relative=relative,
                mime="application/pdf",
                visibility="external",
            )
        ),
        tool_id="write_pdf",
    )

    assert (workspace.runtime_outdir / DECLARED_FILES_FILENAME).is_file()
    rows = DirectToolRuntime.declared_files_for_workspace(workspace)
    assert [row["output"]["filename"] for row in rows] == ["brief.pdf"]
    assert rows[0]["tool_id"] == "write_pdf"


def test_a_corrupt_record_is_read_as_empty(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    workspace.runtime_outdir.mkdir(parents=True, exist_ok=True)
    (workspace.runtime_outdir / DECLARED_FILES_FILENAME).write_text("not json")

    assert read_declared_files(workspace) == {}
    assert DirectToolRuntime.declared_files_for_workspace(workspace) == []


def test_rows_are_shaped_for_host_files(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    runtime = _runtime(workspace)
    relative = f"{TURN_ID}/files/research/brief.pdf"
    _write_artifact(workspace, relative)

    runtime._record_declared_files(
        _exec_result(
            _file_item(
                artifact_id="brief",
                relative=relative,
                mime="application/pdf",
                visibility="external",
            )
        ),
        tool_id="write_pdf",
    )

    row = runtime.declared_files()[0]
    assert row["type"] == "file"
    assert row["output"]["path"] == relative
    assert row["mime"] == "application/pdf"
    assert row["resource_id"] == "brief"
    stored = json.loads(
        (workspace.runtime_outdir / DECLARED_FILES_FILENAME).read_text()
    )
    assert relative in stored
