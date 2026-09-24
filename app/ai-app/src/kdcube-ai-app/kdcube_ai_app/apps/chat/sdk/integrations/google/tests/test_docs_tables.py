# SPDX-License-Identifier: MIT
"""Google Docs tables: inventory, bounded cell reads, and set_cells writes.

Documents are built with consistent UTF-16 indices (docs_tables_fixture), so
the tests check the exact provider requests a write produces: which range is
deleted, where text is inserted, and that nothing is written on a refusal.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.google import docs_proxy, docs_proxy_flex
from kdcube_ai_app.apps.chat.sdk.integrations.google.docs_structure import body_tables
from kdcube_ai_app.apps.chat.sdk.integrations.google.tests.docs_tables_fixture import (
    Body,
    document,
    tasks_body,
)


class _Google:
    """Serves documents.get from a queue and records batchUpdate bodies."""

    def __init__(self, *documents: dict[str, Any], updates: list[httpx.Response] | None = None):
        self.documents = list(documents)
        self.updates = list(updates or [])
        self.writes: list[dict[str, Any]] = []
        self.reads = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            doc = self.documents[min(self.reads, len(self.documents) - 1)]
            self.reads += 1
            return httpx.Response(200, json=doc, request=request)
        assert request.url.path.endswith(":batchUpdate")
        self.writes.append(json.loads(request.content))
        if self.updates:
            response = self.updates.pop(0)
            response.request = request
            return response
        return httpx.Response(
            200,
            json={"writeControl": {"requiredRevisionId": "rev-2"}, "replies": []},
            request=request,
        )


def _run(module, operation: str, payload: dict[str, Any], google: _Google) -> dict[str, Any]:
    transport = httpx.MockTransport(google)
    real_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    execute = (
        docs_proxy_flex.execute_google_docs_flex_operation
        if module is docs_proxy_flex
        else docs_proxy.execute_google_docs_operation
    )
    original = docs_proxy.httpx.AsyncClient
    docs_proxy.httpx.AsyncClient = _factory  # type: ignore[assignment]
    try:
        return asyncio.run(
            execute(operation=operation, access_token="tok", payload=payload)
        )
    finally:
        docs_proxy.httpx.AsyncClient = original  # type: ignore[assignment]


def _set_cells(google: _Google, **payload: Any) -> dict[str, Any]:
    return _run(docs_proxy, "set_cells", {"document_ref": "DOC1", **payload}, google)


def _tasks_doc(**kwargs: Any) -> dict[str, Any]:
    return document(("t.0", "Main", tasks_body()), **kwargs)


def _cell(doc: dict[str, Any], table: int, row: int, column: int) -> dict[str, Any]:
    body = doc["tabs"][0]["documentTab"]["body"]
    return body_tables(body)[table - 1]["cells"][row - 1][column - 1]


# --------------------------------------------------------------------------- #
# Structure
# --------------------------------------------------------------------------- #


def test_body_tables_reads_headings_headers_and_cell_ranges() -> None:
    tables = body_tables(tasks_body().as_body(), tab_id="t.0", tab_title="Main")

    tasks, budget = tables
    assert tasks["position"] == 1
    assert tasks["after_heading"] == {"text": "Tasks", "level": 2}
    assert (tasks["rows"], tasks["columns"], tasks["header_rows"]) == (4, 3, 1)
    assert [cell["text"] for cell in tasks["cells"][1]] == ["Fix login", "", "owner-a"]
    assert budget["after_heading"] == {"text": "Budget", "level": 3}
    assert budget["header_rows"] == 0

    fix = tasks["cells"][1][0]
    # The editable range spans the text and excludes the cell's final newline.
    assert fix["content_end"] - fix["content_start"] == len("Fix login")
    empty = tasks["cells"][1][1]
    assert empty["content_start"] == empty["content_end"]


def test_body_tables_marks_merges_objects_and_nested_tables() -> None:
    body = Body().table(
        [
            ["A", "B", "C"],
            [{"text": "chip", "objects": ("person",)}, {"text": "", "nested": True}, "x"],
        ],
        spans={(0, 0): (1, 2)},
    )
    cells = body_tables(body.as_body())[0]["cells"]

    assert cells[0][0]["column_span"] == 2
    assert cells[0][1]["merged_into"] == [1, 1]
    assert cells[1][0]["objects"] == [{"kind": "person"}]
    assert cells[1][1]["nested_table"] is True


def test_body_tables_places_cells_when_covered_cells_are_omitted() -> None:
    body = Body().table(
        [["A", "B", "C"], ["D", "E", "F"]],
        spans={(0, 1): (2, 1)},
        omit_covered=True,
    )
    cells = body_tables(body.as_body())[0]["cells"]

    assert [cell["text"] for cell in cells[1]] == ["D", "", "F"]
    assert cells[1][1]["merged_into"] == [1, 2]


def test_nearest_heading_of_any_level_with_text_between() -> None:
    body = (
        Body()
        .heading("Report", 1)
        .heading("Expenses", 2)
        .heading("Office", 3)
        .paragraph("Receipts below.")
        .table([["a"]])
    )
    assert body_tables(body.as_body())[0]["after_heading"] == {"text": "Office", "level": 3}


# --------------------------------------------------------------------------- #
# Level 1 and level 2 reads
# --------------------------------------------------------------------------- #


def test_get_lists_tables_with_selectors_and_no_indices() -> None:
    out = _run(docs_proxy, "get", {"document_ref": "DOC1"}, _Google(_tasks_doc()))

    assert out["ok"] is True
    tasks, budget = out["ret"]["tables"]
    assert tasks["selector"] == {"table": {"position": 1}}
    assert tasks["header"] == ["Task", "Status", "Owner"]
    assert tasks["first_row"] is None
    assert budget["header"] is None
    assert budget["first_row"] == ["Office", "120"]
    assert "cells" not in tasks
    assert "start_index" not in json.dumps(out["ret"]["tables"])
    assert "table_reads" not in out["ret"]


def test_get_selector_names_the_tab_in_a_multi_tab_document() -> None:
    doc = document(
        ("t.0", "Main", Body().paragraph("intro")),
        ("t.1", "Budget", Body().table([["a", "b"]])),
    )
    out = _run(docs_proxy, "get", {"document_ref": "DOC1"}, _Google(doc))

    assert out["ret"]["tables"][0]["selector"] == {
        "tab_selector": {"title": "Budget"},
        "table": {"position": 1},
    }


def test_get_reads_one_table_with_row_window_and_next_rows() -> None:
    out = _run(
        docs_proxy,
        "get",
        {"document_ref": "DOC1", "tables": {"table": {"after_heading": "Tasks"}, "rows": "2-3"}},
        _Google(_tasks_doc()),
    )

    read = out["ret"]["table_reads"][0]
    assert [[cell["text"] for cell in row] for row in read["cells"]] == [
        ["Fix login", "", "owner-a"],
        ["Ship docs", "Open", ""],
    ]
    assert read["first_row_number"] == 2
    assert (read["rows_returned"], read["rows_total"], read["truncated"]) == (2, 4, True)
    assert read["next_rows"] == "4-5"
    assert "content_start" not in json.dumps(read)


def test_get_reads_several_tables_within_a_shared_cell_limit(monkeypatch) -> None:
    monkeypatch.setattr(docs_proxy, "MAX_TABLE_READ_CELLS", 7)
    out = _run(
        docs_proxy,
        "get",
        {
            "document_ref": "DOC1",
            "tables": [{"table": 1}, {"table": 2}],
        },
        _Google(_tasks_doc()),
    )

    tasks, budget = out["ret"]["table_reads"]
    # 7 cells fit two 3-column rows of the first table; one cell is too few for a
    # 2-column row of the second.
    assert tasks["rows_returned"] == 2
    assert tasks["next_rows"] == "3-6"
    assert budget["skipped"] == "cell_limit"


def test_get_reads_every_table_when_tables_is_all() -> None:
    out = _run(
        docs_proxy,
        "get",
        {"document_ref": "DOC1", "tables": "all"},
        _Google(_tasks_doc()),
    )

    tasks, budget = out["ret"]["table_reads"]
    assert [cell["text"] for cell in tasks["cells"][1]] == ["Fix login", "", "owner-a"]
    assert [cell["text"] for cell in budget["cells"][0]] == ["Office", "120"]
    assert "tables_truncated" not in out["ret"]


def test_reading_all_tables_stops_at_the_table_cap(monkeypatch) -> None:
    monkeypatch.setattr(docs_proxy, "MAX_TABLE_READS", 1)
    out = _run(
        docs_proxy,
        "get",
        {"document_ref": "DOC1", "tables": "all"},
        _Google(_tasks_doc()),
    )

    assert len(out["ret"]["table_reads"]) == 1
    assert out["ret"]["tables_truncated"] is True


def test_the_flat_text_tells_the_same_story_as_a_cell_read() -> None:
    body = Body().heading("Tasks").table(
        [
            ["Task", "Status", "Owner"],
            ["Fix login", "", {"text": "", "objects": ({"kind": "person"},)}],
            ["Rate | tier", "Open", {"text": "", "nested": True}],
        ],
        header_rows=1,
        spans={(2, 0): (1, 2)},
    )
    text = docs_proxy._extract_document_text(
        document(("t.0", "Main", body)), limit=4000
    )
    lines = [line for line in text.split("\n") if line]

    assert lines[2] == "[table · 3 rows × 3 columns · header row]"
    # One row of the table is one line of the text: reading it otherwise is what
    # made a row's end invisible.
    assert lines[3] == "Task | Status | Owner"
    # A chip cell is empty in the text; saying so is what stops a reader
    # concluding the document holds none.
    assert lines[4] == "Fix login |  | [person]"
    # A literal separator in a cell is escaped, a merged-away cell says so, and
    # a nested table is named rather than silently flattened.
    assert lines[5] == "Rate \\| tier | [merged] | [nested table]"


def test_a_table_without_a_header_says_so() -> None:
    body = Body().table([["Office", "120"], ["Travel", "300"]])
    text = docs_proxy._extract_document_text(
        document(("t.0", "Main", body)), limit=4000
    )

    assert "[table · 2 rows × 2 columns · no header row]" in text
    assert "Office | 120" in text


def test_a_multi_paragraph_cell_stays_on_its_row() -> None:
    body = Body().table([[{"text": "", "paragraphs": ["first", "second"]}, "x"]])
    text = docs_proxy._extract_document_text(
        document(("t.0", "Main", body)), limit=4000
    )

    assert "first second | x" in text


def test_every_read_cell_carries_its_row_and_column() -> None:
    out = _run(
        docs_proxy,
        "get",
        {"document_ref": "DOC1", "tables": {"table": 1}, "rows": "2-3"},
        _Google(_tasks_doc()),
    )

    read = out["ret"]["table_reads"][0]
    # A reader that counts rows itself writes into the wrong one: the header row
    # is a row, and a window does not start at 1.
    assert [cell["row"] for cell in read["cells"][0]] == [1, 1, 1]
    assert [cell["column"] for cell in read["cells"][0]] == [1, 2, 3]
    review = [
        cell
        for row in read["cells"]
        for cell in row
        if cell.get("text") == "Fix login"
    ]
    assert review[0]["row"] == 2


def test_a_write_names_the_row_it_landed_in() -> None:
    google = _Google(_tasks_doc())
    out = _set_cells(google, table=1, row=3, cells={"Status": "Open"})

    assert out["ok"] is True
    assert (out["ret"]["row"], out["ret"]["row_label"]) == (3, "Ship docs")


def test_the_row_label_is_what_the_write_leaves_in_the_first_column() -> None:
    renamed = _set_cells(
        _Google(_tasks_doc()), table=1, row=3, cells={"Task": "Ship docs v2"}
    )
    # Reading it from the document before the write would name the old row.
    assert renamed["ret"]["row_label"] == "Ship docs v2"

    chip = _set_cells(
        _Google(_tasks_doc()),
        table=1,
        row=3,
        cells=[{"column": 1, "person": "owner-b@example.com"}],
    )
    assert chip["ret"]["row_label"] == "owner-b@example.com"


def test_get_refuses_more_than_five_table_reads() -> None:
    out = _run(
        docs_proxy,
        "get",
        {"document_ref": "DOC1", "tables": [{"table": 1}] * 6},
        _Google(_tasks_doc()),
    )
    assert out["error"]["code"] == "request_too_large"


def test_get_snapshot_mode_includes_all_cells() -> None:
    out = _run(
        docs_proxy,
        "get",
        {"document_ref": "DOC1", "include_table_cells": True},
        _Google(_tasks_doc()),
    )
    assert len(out["ret"]["tables"][0]["cells"]) == 4


# --------------------------------------------------------------------------- #
# set_cells
# --------------------------------------------------------------------------- #


def test_set_cells_replaces_by_header_name_and_where_predicate() -> None:
    doc = _tasks_doc()
    google = _Google(doc)
    out = _set_cells(
        google,
        table={"after_heading": "Tasks"},
        row={"where": {"column": "Task", "equals": "ship DOCS"}},
        cells={"Status": "Done"},
    )

    assert out["ok"] is True, out
    status = _cell(doc, 1, 3, 2)
    assert google.writes == [
        {
            "requests": [
                {
                    "deleteContentRange": {
                        "range": {
                            "startIndex": status["content_start"],
                            "endIndex": status["content_end"],
                            "tabId": "t.0",
                        }
                    }
                },
                {
                    "insertText": {
                        "location": {"index": status["content_start"], "tabId": "t.0"},
                        "text": "Done",
                    }
                },
            ],
            "writeControl": {"requiredRevisionId": "rev-1"},
        }
    ]
    ret = out["ret"]
    assert (ret["row"], ret["revision_id"], ret["attempts"]) == (3, "rev-2", 1)
    assert ret["cells"] == [
        {"column": 2, "header": "Status", "before": "Open", "after": "Done"}
    ]


def test_set_cells_writes_one_row_last_cell_first() -> None:
    doc = _tasks_doc()
    google = _Google(doc)
    out = _set_cells(
        google,
        table=1,
        row=2,
        cells={"Status": "Done", "3": "owner-b"},
    )

    assert out["ok"] is True, out
    requests = google.writes[0]["requests"]
    status, owner = _cell(doc, 1, 2, 2), _cell(doc, 1, 2, 3)
    # Owner (later in the document) is rewritten before Status.
    assert requests[0]["deleteContentRange"]["range"]["startIndex"] == owner["content_start"]
    assert requests[1]["insertText"]["location"]["index"] == owner["content_start"]
    assert requests[2]["insertText"]["location"]["index"] == status["content_start"]
    assert len(requests) == 3  # the empty Status cell needs no delete


@pytest.mark.parametrize(
    ("mode", "field", "after"),
    [("append", "content_end", "owner-aX"), ("prepend", "content_start", "Xowner-a")],
)
def test_set_cells_append_and_prepend_keep_existing_text(mode, field, after) -> None:
    doc = _tasks_doc()
    google = _Google(doc)
    out = _set_cells(google, table=1, row=2, cells={"Owner": "X"}, mode=mode)

    owner = _cell(doc, 1, 2, 3)
    assert google.writes[0]["requests"] == [
        {"insertText": {"location": {"index": owner[field], "tabId": "t.0"}, "text": "X"}}
    ]
    assert out["ret"]["cells"][0]["after"] == after


def test_set_cells_empty_replace_clears_the_cell() -> None:
    doc = _tasks_doc()
    google = _Google(doc)
    _set_cells(google, table=1, row=2, cells={"Owner": ""})

    assert list(google.writes[0]["requests"][0]) == ["deleteContentRange"]
    assert len(google.writes[0]["requests"]) == 1


def test_where_never_matches_header_rows() -> None:
    google = _Google(_tasks_doc())
    out = _set_cells(
        google,
        table=1,
        row={"where": {"column": "Task", "equals": "Task"}},
        cells={"Status": "x"},
    )
    assert out["error"]["code"] == "docs_table_row_not_found"
    assert google.writes == []


def test_ambiguous_row_returns_candidates_and_writes_nothing() -> None:
    google = _Google(_tasks_doc())
    out = _set_cells(
        google,
        table=1,
        row={"where": {"column": "Status", "equals": "Open"}},
        cells={"Owner": "x"},
    )

    assert out["error"]["code"] == "docs_table_row_ambiguous"
    assert [row["row"] for row in out["error"]["details"]["candidates"]] == [3, 4]
    assert google.writes == []


def test_column_name_needs_a_header_row_or_explicit_header() -> None:
    google = _Google(_tasks_doc())
    refused = _set_cells(google, table=2, row=2, cells={"Travel": "310"})
    assert refused["error"]["code"] == "docs_table_no_header"

    google = _Google(_tasks_doc())
    out = _set_cells(google, table=2, row=2, cells={"Office": "310"}, header=1)
    assert out["ok"] is True, out
    assert out["ret"]["cells"][0] == {
        "column": 1,
        "header": "Office",
        "before": "Travel",
        "after": "310",
    }


def test_table_position_counts_among_heading_matches() -> None:
    body = (
        Body()
        .heading("Tasks")
        .table([["first"]])
        .paragraph("between")
        .table([["second"]])
    )
    google = _Google(document(("t.0", "Main", body)))
    out = _set_cells(
        google,
        table={"after_heading": "tasks", "position": 2},
        row=1,
        cells={"1": "x"},
    )
    assert out["ret"]["cells"][0]["before"] == "second"

    google = _Google(document(("t.0", "Main", body)))
    ambiguous = _set_cells(google, table={"after_heading": "Tasks"}, row=1, cells={"1": "x"})
    assert ambiguous["error"]["code"] == "docs_table_ambiguous"
    assert google.writes == []


def test_merged_nested_and_object_cells_are_refused_before_any_write() -> None:
    body = Body().table(
        [
            ["A", "B", "C"],
            [{"text": "chip", "objects": ("person",)}, {"text": "", "nested": True}, "x"],
        ],
        spans={(0, 0): (1, 2)},
    )
    doc = document(("t.0", "Main", body))

    google = _Google(doc)
    merged = _set_cells(google, table=1, row=1, cells={"2": "x"})
    assert merged["error"]["code"] == "docs_table_cell_merged"
    assert merged["error"]["details"]["merged_into"] == [1, 1]
    assert google.writes == []

    google = _Google(doc)
    nested = _set_cells(google, table=1, row=2, cells={"2": "x"})
    assert nested["error"]["code"] == "docs_table_nested"

    google = _Google(doc)
    objects = _set_cells(google, table=1, row=2, cells={"1": "x"})
    assert objects["error"]["code"] == "docs_table_cell_has_objects"
    assert objects["error"]["details"]["objects"] == [{"kind": "person"}]
    assert google.writes == []

    google = _Google(doc)
    assert _set_cells(google, table=1, row=2, cells={"1": "x"}, mode="append")["ok"] is True
    google = _Google(doc)
    assert _set_cells(google, table=1, row=2, cells={"1": "x"}, remove_objects=True)["ok"] is True


def test_revision_conflict_rereads_and_resolves_the_row_again() -> None:
    before = _tasks_doc(revision="rev-1")
    # A person inserted a row above "Ship docs" between our read and write.
    moved = document(
        (
            "t.0",
            "Main",
            Body()
            .paragraph("Report 🚀 July")
            .heading("Tasks", 2)
            .paragraph("Open items for the week.")
            .table(
                [
                    ["Task", "Status", "Owner"],
                    ["New item", "", ""],
                    ["Fix login", "", "owner-a"],
                    ["Ship docs", "Open", ""],
                    ["Review", "Open", "owner-b"],
                ],
                header_rows=1,
            ),
        ),
        revision="rev-5",
    )
    conflict = httpx.Response(
        400,
        json={
            "error": {
                "code": 400,
                "status": "INVALID_ARGUMENT",
                "message": "The required revision ID rev-1 does not match the latest revision.",
            }
        },
    )
    google = _Google(before, moved, updates=[conflict])
    out = _set_cells(
        google,
        table=1,
        row={"where": {"column": "Task", "equals": "Ship docs"}},
        cells={"Status": "Done"},
    )

    assert out["ok"] is True, out
    assert out["ret"]["attempts"] == 2
    assert out["ret"]["row"] == 4
    second = google.writes[1]
    assert second["writeControl"] == {"requiredRevisionId": "rev-5"}
    status = _cell(moved, 1, 4, 2)
    assert second["requests"][0]["deleteContentRange"]["range"]["startIndex"] == status["content_start"]


def test_caller_revision_mismatch_refuses_without_writing() -> None:
    google = _Google(_tasks_doc(revision="rev-9"))
    out = _set_cells(google, table=1, row=2, cells={"Owner": "x"}, revision_id="rev-1")

    assert out["error"]["code"] == "docs_revision_changed"
    assert out["error"]["details"]["revision_id"] == "rev-9"
    assert google.writes == []


def test_multi_tab_documents_need_a_tab() -> None:
    doc = document(
        ("t.0", "Main", Body().table([["a"]])),
        ("t.1", "Budget", Body().table([["Office", "120"]])),
    )
    google = _Google(doc)
    assert _set_cells(google, table=1, row=1, cells={"1": "x"})["error"]["code"] == (
        "docs_tab_selection_required"
    )

    google = _Google(doc)
    out = _set_cells(
        google, tab_selector={"title": "budget"}, table=1, row=1, cells={"2": "130"}
    )
    assert out["ok"] is True, out
    assert out["ret"]["tab_id"] == "t.1"
    assert google.writes[0]["requests"][0]["deleteContentRange"]["range"]["tabId"] == "t.1"


def test_set_cells_rejects_bad_mode_and_repeated_columns() -> None:
    google = _Google(_tasks_doc())
    assert _set_cells(google, table=1, row=2, cells={"1": "x"}, mode="upsert")["error"]["code"] == (
        "invalid_mode"
    )
    google = _Google(_tasks_doc())
    out = _set_cells(google, table=1, row=2, cells={"Task": "x", "1": "y"})
    assert out["error"]["code"] == "docs_table_column_repeated"
    assert google.writes == []


# --------------------------------------------------------------------------- #
# Flexible surface
# --------------------------------------------------------------------------- #


def _add_row(google: _Google, **payload: Any) -> dict[str, Any]:
    return _run(docs_proxy, "add_row", {"document_ref": "DOC1", **payload}, google)


def test_add_row_appends_at_the_end_and_fills_the_new_row() -> None:
    rows = [["Task", "Status"], ["Fix login", "Done"]]
    before = document(("t.0", "Main", Body().table(rows, header_rows=1)))
    # The fill re-reads: the new row exists only after the insert lands.
    after = document(
        ("t.0", "Main", Body().table(rows + [["", ""]], header_rows=1))
    )
    google = _Google(before, after)
    out = _add_row(google, table=1, cells={"Task": "Ship v2", "Status": "Open"})

    assert out["ok"] is True, out
    inserted = google.writes[0]["requests"][0]["insertTableRow"]
    assert inserted["tableCellLocation"]["rowIndex"] == 1
    assert inserted["insertBelow"] is True
    assert (out["ret"]["row"], out["ret"]["rows"]) == (3, 3)
    assert [cell["after"] for cell in out["ret"]["cells"]] == ["Ship v2", "Open"]
    assert out["ret"]["row_label"] == "Ship v2"


def test_add_row_puts_the_row_below_the_one_named_by_value() -> None:
    google = _Google(_tasks_doc())
    out = _add_row(
        google,
        table=1,
        after_row={"where": {"column": "Task", "equals": "Fix login"}},
    )

    assert out["ok"] is True, out
    assert google.writes[0]["requests"][0]["insertTableRow"]["tableCellLocation"][
        "rowIndex"
    ] == 1
    assert out["ret"]["row"] == 3
    # Without cells there is one write and nothing to report.
    assert len(google.writes) == 1
    assert out["ret"]["cells"] == []


def test_add_row_refuses_a_stale_revision_without_writing() -> None:
    google = _Google(_tasks_doc())
    out = _add_row(google, table=1, revision_id="rev-older")

    assert out["error"]["code"] == "docs_revision_changed"
    assert google.writes == []


def test_a_previewed_insert_says_what_sits_at_the_index_and_writes_nothing() -> None:
    google = _Google(_tasks_doc())
    cell = _cell(_tasks_doc(), 1, 1, 4 - 1)  # the header cell that was corrupted once
    out = _run(
        docs_proxy,
        "insert_text",
        {
            "document_ref": "DOC1",
            "text": "x",
            "index": cell["content_start"] + 2,
            "preview": True,
        },
        google,
    )

    assert out["ok"] is True, out
    assert google.writes == []
    preview = out["ret"]["preview"]
    assert out["ret"]["written"] is False
    assert preview["where"] == "table 1, row 1, column 3"
    # Two characters in: the reader sees the word it is about to land inside.
    assert preview["text_before"] == "Ow"
    assert preview["text_after"] == "ner"


def test_a_previewed_style_shows_the_text_the_range_covers() -> None:
    google = _Google(_tasks_doc())
    fix = _cell(_tasks_doc(), 1, 2, 1)
    out = _run(
        docs_proxy,
        "apply_text_style",
        {
            "document_ref": "DOC1",
            "start_index": fix["content_start"],
            "end_index": fix["content_end"],
            "bold": True,
            "preview": True,
        },
        google,
    )

    assert google.writes == []
    covered = out["ret"]["preview"]["covers"]
    assert [row["text"] for row in covered] == ["Fix login"]
    assert out["ret"]["would_style"] == ["bold"]


def test_a_previewed_replacement_counts_matches_before_changing_any() -> None:
    google = _Google(_tasks_doc())
    out = _run(
        docs_proxy,
        "replace_text",
        {
            "document_ref": "DOC1",
            "replacements": [{"find": "Open", "replace": "Done"}],
            "preview": True,
        },
        google,
    )

    assert google.writes == []
    entry = out["ret"]["preview"][0]
    # replaceAllText reports occurrences only after changing them. Three places
    # hold "Open" here, which is exactly what a caller aiming at one needs to
    # learn before the write, not after.
    assert entry["occurrences"] == 3
    assert sorted(match["where"] for match in entry["matches"]) == [
        "paragraph",
        "table 1, row 3, column 2",
        "table 1, row 4, column 2",
    ]


def test_adding_a_tab_names_the_tab_that_was_not_there_before() -> None:
    before = document(("t.0", "Main", Body().paragraph("a")))
    after = document(
        ("t.0", "Main", Body().paragraph("a")),
        ("t.7", "Notes", Body().paragraph("b")),
    )
    google = _Google(before, after)
    out = _run(
        docs_proxy,
        "add_tab",
        {"document_ref": "DOC1", "title": "Notes", "index": 1},
        google,
    )

    assert out["ok"] is True, out
    added = google.writes[0]["requests"][0]["addDocumentTab"]
    assert added["tabProperties"] == {"title": "Notes", "index": 1}
    # addDocumentTab returns no id, so the new tab is the one the document
    # gained between the two reads.
    assert out["ret"]["tab_id"] == "t.7"
    assert out["ret"]["tab_count"] == 2


def test_renaming_a_tab_sends_only_the_field_it_changes() -> None:
    doc = document(
        ("t.0", "Main", Body().paragraph("a")),
        ("t.1", "Notes", Body().paragraph("b")),
    )
    google = _Google(doc)
    out = _run(
        docs_proxy,
        "update_tab",
        {"document_ref": "DOC1", "tab_id": "t.1", "title": "Archive"},
        google,
    )

    assert out["ok"] is True, out
    request = google.writes[0]["requests"][0]["updateDocumentTabProperties"]
    assert request["tabProperties"] == {"tabId": "t.1", "title": "Archive"}
    assert request["fields"] == "title"
    assert out["ret"]["changed"] == ["title"]

    nothing = _run(
        docs_proxy, "update_tab", {"document_ref": "DOC1", "tab_id": "t.1"}, _Google(doc)
    )
    assert nothing["error"]["code"] == "tab_update_empty"


def test_deleting_a_tab_reports_the_children_google_takes_with_it() -> None:
    doc = document(
        ("t.0", "Main", Body().paragraph("a")),
        ("t.1", "Notes", Body().paragraph("b")),
    )
    doc["tabs"][1]["childTabs"] = [
        {
            "tabProperties": {"tabId": "t.2", "title": "Sub", "index": 0},
            "documentTab": {"body": Body().paragraph("c").as_body()},
        }
    ]
    remaining = document(("t.0", "Main", Body().paragraph("a")))
    google = _Google(doc, remaining)
    out = _run(
        docs_proxy, "delete_tab", {"document_ref": "DOC1", "tab_id": "t.1"}, google
    )

    assert out["ok"] is True, out
    assert google.writes[0]["requests"][0]["deleteTab"] == {"tabId": "t.1"}
    assert out["ret"]["deleted_child_tab_ids"] == ["t.2"]
    assert out["ret"]["tab_count"] == 1


def test_a_document_keeps_its_last_tab() -> None:
    google = _Google(document(("t.0", "Main", Body().paragraph("a"))))
    out = _run(
        docs_proxy, "delete_tab", {"document_ref": "DOC1", "tab_id": "t.0"}, google
    )

    assert out["error"]["code"] == "docs_last_tab"
    assert google.writes == []


def test_a_comment_and_a_reply_are_rewritten_where_drive_keeps_them() -> None:
    seen: list[tuple[str, str, dict[str, Any]]] = []

    def drive(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, json.loads(request.content or b"{}")))
        return httpx.Response(
            200,
            json={"id": "c1", "content": "revised", "modifiedTime": "2026-09-24T10:00:00Z"},
            request=request,
        )

    out = _run(
        docs_proxy,
        "update_comment",
        {"document_ref": "DOC1", "comment_id": "c1", "content": "revised"},
        drive,
    )
    assert out["ok"] is True, out
    method, path, body = seen[-1]
    assert method == "PATCH" and path.endswith("/comments/c1")
    assert body == {"content": "revised"}
    assert out["ret"]["content"] == "revised"
    assert "reply_id" not in out["ret"]

    out = _run(
        docs_proxy,
        "update_comment",
        {
            "document_ref": "DOC1",
            "comment_id": "c1",
            "reply_id": "r7",
            "content": "revised",
        },
        drive,
    )
    # A reply lives under its comment, so rewriting one is a different resource.
    assert seen[-1][1].endswith("/comments/c1/replies/r7")
    assert out["ret"]["reply_id"] == "r7"


def test_rewriting_a_comment_needs_the_comment_it_rewrites() -> None:
    out = _run(
        docs_proxy,
        "update_comment",
        {"document_ref": "DOC1", "content": "revised"},
        _Google(),
    )
    assert out["error"]["code"] == "comment_id_required"


def test_trash_and_restore_patch_the_drive_file() -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    def drive(trashed: bool):
        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, json.loads(request.content or b"{}")))
            return httpx.Response(
                200,
                json={"id": "DOC1", "name": "Sandbox", "trashed": trashed},
                request=request,
            )

        return handler

    out = _run(docs_proxy, "trash", {"document_ref": "DOC1"}, drive(True))
    assert out["ok"] is True, out
    # Trashing is a Drive file patch, not a document edit.
    assert seen[-1] == ("PATCH", {"trashed": True})
    assert out["ret"]["trashed"] is True
    assert out["ret"]["document_id"] == "DOC1"

    out = _run(docs_proxy, "restore", {"document_ref": "DOC1"}, drive(False))
    assert out["ok"] is True, out
    assert seen[-1] == ("PATCH", {"trashed": False})
    assert out["ret"]["trashed"] is False


def test_get_structure_exposes_cell_indices_for_batch_edit() -> None:
    doc = _tasks_doc()
    out = _run(docs_proxy_flex, "get_structure", {"document_ref": "DOC1"}, _Google(doc))

    elements = out["ret"]["tabs"][0]["elements"]
    tables = [element for element in elements if element["type"] == "table"]
    assert [table["position"] for table in tables] == [1, 2]
    owner = tables[0]["cells"][1][2]
    expected = _cell(doc, 1, 2, 3)
    assert (owner["content_start"], owner["content_end"]) == (
        expected["content_start"],
        expected["content_end"],
    )
    assert tables[0]["cells_truncated"] is False


def test_set_cells_accepts_the_selector_returned_by_get() -> None:
    doc = document(
        ("t.0", "Main", Body().paragraph("intro")),
        ("t.1", "Budget", Body().table([["Office", "120"], ["Travel", "300"]])),
    )
    read = _run(docs_proxy, "get", {"document_ref": "DOC1"}, _Google(doc))
    selector = read["ret"]["tables"][0]["selector"]

    google = _Google(doc)
    out = _set_cells(google, selector=selector, row=2, cells={"120": "310"}, header=1)

    assert out["ok"] is True, out
    assert out["ret"]["tab_id"] == "t.1"
    assert out["ret"]["cells"][0] == {
        "column": 2,
        "header": "120",
        "before": "300",
        "after": "310",
    }


def test_a_read_names_who_a_person_chip_points_at() -> None:
    body = Body().table(
        [
            ["Task", "Owner"],
            [
                "Review",
                {
                    "text": "",
                    "objects": ({"kind": "person", "email": "owner-a@example.com", "name": "Owner A"},),
                },
            ],
        ],
        header_rows=1,
    )
    out = _run(
        docs_proxy,
        "get",
        {"document_ref": "DOC1", "tables": "all"},
        _Google(document(("t.0", "Main", body))),
    )

    cell = out["ret"]["table_reads"][0]["cells"][1][1]
    # The flat text shows nothing here; the chip is the cell's whole content.
    assert cell["text"] == ""
    assert cell["objects"] == [
        {"kind": "person", "email": "owner-a@example.com", "name": "Owner A"}
    ]


def test_a_cell_can_be_written_as_a_person_chip() -> None:
    google = _Google(_tasks_doc())
    out = _set_cells(
        google,
        table=1,
        row={"where": {"column": "Task", "equals": "Fix login"}},
        cells=[{"column": "Owner", "person": "owner-b@example.com"}],
    )

    assert out["ok"] is True
    requests = google.writes[0]["requests"]
    assert requests[0]["deleteContentRange"]["range"]["startIndex"] > 0
    # Only the address: Google refuses insertPerson that carries a name.
    assert requests[1]["insertPerson"]["personProperties"] == {
        "email": "owner-b@example.com",
    }
    written = out["ret"]["cells"][0]
    assert written["before"] == "owner-a"
    assert written["wrote"] == {"kind": "person", "email": "owner-b@example.com"}


def test_a_write_reports_the_objects_it_replaced() -> None:
    body = Body().table(
        [["Task", "Owner"], ["Review", {"text": "", "objects": ({"kind": "person", "email": "a@b.com"},)}]],
        header_rows=1,
    )
    google = _Google(document(("t.0", "Main", body)))
    out = _set_cells(
        google,
        table=1,
        row=2,
        cells=[{"column": "Owner", "person": "c@d.com"}],
        remove_objects=True,
    )

    # Whoever the chip named is in the answer, so the write can be undone.
    assert out["ret"]["cells"][0]["before_objects"] == [
        {"kind": "person", "email": "a@b.com"}
    ]


def test_a_cell_takes_text_or_a_person_not_both() -> None:
    both = _set_cells(
        _Google(_tasks_doc()),
        table=1,
        row=2,
        cells=[{"column": "Owner", "text": "x", "person": "a@b.com"}],
    )
    assert both["error"]["code"] == "docs_cell_value_ambiguous"

    bad = _set_cells(
        _Google(_tasks_doc()),
        table=1,
        row=2,
        cells=[{"column": "Owner", "person": "owner-b"}],
    )
    assert bad["error"]["code"] == "docs_person_email_invalid"


def test_refusals_ask_the_user_instead_of_naming_a_way_around() -> None:
    body = Body().table(
        [["A", "B"], [{"text": "chip", "objects": ("person",)}, "x"]],
        spans={(0, 0): (1, 2)},
    )
    doc = document(("t.0", "Main", body))

    merged = _set_cells(_Google(doc), table=1, row=1, cells={"2": "x"})
    assert "Ask the user" in merged["error"]["message"]
    assert merged["error"]["details"]["merged_cell_text"] == "A"

    objects = _set_cells(_Google(doc), table=1, row=2, cells={"1": "x"})
    assert "Ask the user what to do with them" in objects["error"]["message"]
    # The way around a refusal belongs in the schema, not in the refusal.
    assert "remove_objects" not in objects["error"]["message"]
    assert objects["error"]["details"]["objects"] == [{"kind": "person"}]
