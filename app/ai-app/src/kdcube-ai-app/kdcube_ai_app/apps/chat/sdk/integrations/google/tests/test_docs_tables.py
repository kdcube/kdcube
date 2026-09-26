# SPDX-License-Identifier: MIT
"""Google Docs tables: inventory, bounded cell reads, and set_cells writes.

Documents are built with consistent UTF-16 indices (docs_tables_fixture), so
the tests check the exact provider requests a write produces: which range is
deleted, where text is inserted, and that nothing is written on a refusal.
"""
from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import httpx
import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.google import docs_proxy, docs_proxy_flex
from kdcube_ai_app.apps.chat.sdk.integrations.google.docs_structure import body_tables
from kdcube_ai_app.apps.chat.sdk.integrations.google.tests.docs_tables_fixture import (
    Body,
    document,
    inline_image,
    tasks_body,
)


class _Google:
    """Serves documents.get from a queue and records batchUpdate bodies."""

    def __init__(
        self,
        *documents: dict[str, Any],
        updates: list[httpx.Response] | None = None,
        image: httpx.Response | None = None,
    ):
        self.documents = list(documents)
        self.updates = list(updates or [])
        self.image = image
        self.writes: list[dict[str, Any]] = []
        self.image_fetches: list[httpx.Request] = []
        self.reads = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.host != "docs.googleapis.com":
            # An image's contentUri points at Google's content host, not the API.
            self.image_fetches.append(request)
            response = self.image or httpx.Response(
                200, content=b"\x89PNG bytes", headers={"content-type": "image/png"}
            )
            response.request = request
            return response
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


def test_get_structure_describes_an_image_the_way_a_cell_read_does() -> None:
    body = Body().table([["Chart"], [_image_cell()]])
    doc = document(
        ("t.0", "Main", body),
        inline_objects={"img.1": inline_image(alt="Q3 revenue by region")},
    )

    out = _run(docs_proxy_flex, "get_structure", {"document_ref": "DOC1"}, _Google(doc))

    # Both doors read the same walk, so the index-oriented structure cannot
    # describe an image the cell reads describe differently.
    table = [
        element
        for element in out["ret"]["tabs"][0]["elements"]
        if element["type"] == "table"
    ][0]
    assert table["cells"][1][0]["objects"] == [
        {"kind": "image", "object_id": "img.1", "alt": "Q3 revenue by region"}
    ]


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


def _image_cell(object_id: str = "img.1") -> dict[str, Any]:
    return {"text": "", "objects": ({"kind": "inlineObjectElement", "id": object_id},)}


def test_a_read_names_what_an_image_is_without_its_short_lived_uri() -> None:
    body = Body().table(
        [["Chart", "Note"], [_image_cell(), "Q3"]],
        header_rows=1,
    )
    out = _run(
        docs_proxy,
        "get",
        {"document_ref": "DOC1", "tables": "all"},
        _Google(
            document(
                ("t.0", "Main", body),
                inline_objects={
                    "img.1": inline_image(
                        alt="Q3 revenue by region",
                        title="Revenue",
                        width_pt=468.00000000000006,
                        height_pt=263.20000000000005,
                        source_uri="https://example.invalid/chart.png",
                        content_uri="https://lh3.example.invalid/expires-in-30-minutes",
                    )
                },
            )
        ),
    )

    cell = out["ret"]["table_reads"][0]["cells"][1][0]
    assert cell["objects"] == [
        {
            "kind": "image",
            "object_id": "img.1",
            "alt": "Q3 revenue by region",
            "title": "Revenue",
            "width_pt": 468.0,
            "height_pt": 263.2,
            "source_uri": "https://example.invalid/chart.png",
        }
    ]
    # contentUri is scoped to the account that read the document and expires;
    # it stays out of what a caller is handed.
    assert "expires-in-30-minutes" not in json.dumps(out)


def test_an_image_read_without_the_objects_map_still_names_the_object() -> None:
    body = Body().table([["Chart"], [_image_cell("img.9")]])

    tables = body_tables(body.as_body())

    assert tables[0]["cells"][1][0]["objects"] == [
        {"kind": "image", "object_id": "img.9"}
    ]


def test_the_flat_text_names_an_image_by_its_alt_text() -> None:
    body = Body().table([["Chart", "Note"], [_image_cell(), "Q3"]], header_rows=1)

    text = docs_proxy._extract_document_text(
        document(
            ("t.0", "Main", body),
            inline_objects={"img.1": inline_image(alt="Revenue | by region")},
        ),
        limit=4000,
    )

    # The overview names what the cell read names, and escapes the separator
    # inside the label the same way cell text is escaped.
    assert "[image: Revenue \\| by region] | Q3" in text


def test_a_long_alt_text_does_not_swamp_the_row() -> None:
    alt = "A very long description of the quarterly revenue chart by region and product"
    body = Body().table([[_image_cell()]])

    text = docs_proxy._extract_document_text(
        document(
            ("t.0", "Main", body),
            inline_objects={"img.1": inline_image(alt=alt)},
        ),
        limit=4000,
    )

    assert "[image: A very long description of the q" in text
    assert "…]" in text
    assert alt not in text


def test_an_image_with_no_properties_reads_as_an_image() -> None:
    body = Body().table([[_image_cell()]])
    doc = document(("t.0", "Main", body), inline_objects={"img.1": inline_image()})

    out = _run(docs_proxy, "get", {"document_ref": "DOC1", "tables": "all"}, _Google(doc))

    assert out["ret"]["table_reads"][0]["cells"][0][0]["objects"] == [
        {"kind": "image", "object_id": "img.1"}
    ]


def _image_document(**kwargs: Any) -> dict[str, Any]:
    body = Body().table([["Chart", "Note"], [_image_cell(), "Q3"]], header_rows=1)
    return document(
        ("t.0", "Main", body),
        inline_objects={
            "img.1": inline_image(
                alt="Q3 revenue by region",
                width_pt=240,
                height_pt=160,
                source_uri="https://example.invalid/chart.png",
                content_uri="https://lh7-rt.googleusercontent.com/expiring-capability",
                **kwargs,
            )
        },
    )


def _read_image(google: _Google, **payload: Any) -> dict[str, Any]:
    return _run(
        docs_proxy, "read_image", {"document_ref": "DOC1", **payload}, google
    )


def test_reading_an_image_returns_its_bytes_and_where_it_sits() -> None:
    google = _Google(_image_document())

    out = _read_image(google, object_id="img.1")

    ret = out["ret"]
    assert ret["mime_type"] == "image/png"
    assert ret["byte_size"] == len(b"\x89PNG bytes")
    assert base64.b64decode(ret["content_base64"]) == b"\x89PNG bytes"
    # The answer describes the image the way a cell read does, and says where it is.
    assert ret["alt"] == "Q3 revenue by region"
    assert ret["source_uri"] == "https://example.invalid/chart.png"
    assert ret["cell"] == {"table": 1, "row": 2, "column": 1}
    assert ret["tab_id"] == "t.0"
    # The URL is its own capability, so no credential rides with the fetch.
    assert "authorization" not in google.image_fetches[0].headers
    # And it is never handed back.
    assert "expiring-capability" not in json.dumps(out)


def test_reading_an_unknown_image_names_the_ones_the_document_holds() -> None:
    google = _Google(_image_document())

    out = _read_image(google, object_id="img.9")

    assert out["error"]["code"] == "docs_image_not_found"
    candidates = out["error"]["details"]["images"]
    assert [image["object_id"] for image in candidates] == ["img.1"]
    # A refusal that listed content URLs would hand out the images it refused.
    assert all("content_uri" not in image for image in candidates)


def test_reading_without_an_object_id_takes_the_only_image() -> None:
    google = _Google(_image_document())

    assert _read_image(google)["ret"]["object_id"] == "img.1"


def test_several_images_need_the_one_to_read_named() -> None:
    body = Body().table([["A"], [_image_cell("img.1")], [_image_cell("img.2")]])
    google = _Google(
        document(
            ("t.0", "Main", body),
            inline_objects={
                "img.1": inline_image(content_uri="https://lh7-rt.googleusercontent.com/a"),
                "img.2": inline_image(content_uri="https://lh7-rt.googleusercontent.com/b"),
            },
        )
    )

    out = _read_image(google)

    assert out["error"]["code"] == "docs_image_selection_required"
    assert len(out["error"]["details"]["images"]) == 2
    assert google.image_fetches == []


def test_an_expired_image_url_is_reported_as_such() -> None:
    google = _Google(
        _image_document(),
        image=httpx.Response(403, content=b"expired"),
    )

    out = _read_image(google, object_id="img.1")

    assert out["error"]["code"] == "docs_image_fetch_failed"
    assert out["error"]["details"]["status"] == 403


def test_an_object_with_no_content_is_refused_before_any_fetch() -> None:
    body = Body().table([["A"], [_image_cell()]])
    google = _Google(
        document(("t.0", "Main", body), inline_objects={"img.1": inline_image(alt="a drawing")})
    )

    out = _read_image(google, object_id="img.1")

    assert out["error"]["code"] == "docs_image_bytes_unavailable"
    assert google.image_fetches == []


def test_a_non_image_response_is_not_passed_off_as_an_image() -> None:
    google = _Google(
        _image_document(),
        image=httpx.Response(
            200, content=b"<html>sign in</html>", headers={"content-type": "text/html"}
        ),
    )

    out = _read_image(google, object_id="img.1")

    assert out["error"]["code"] == "docs_image_unexpected_type"
    assert out["error"]["details"]["mime_type"] == "text/html"


def _embed(google: _Google, **payload: Any) -> dict[str, Any]:
    return _run(
        docs_proxy,
        "embed_image",
        {
            "document_ref": "DOC1",
            "image_uri": "https://example.invalid/chart.png",
            **payload,
        },
        google,
    )


def test_an_image_goes_into_the_cell_a_selector_names() -> None:
    doc = _tasks_doc()
    google = _Google(doc)

    out = _embed(google, table=1, row=3, column="Owner")

    # The write lands at the end of that cell's content, so a label already in
    # the cell keeps its place - and the caller never computed an index.
    request = google.writes[0]["requests"][0]["insertInlineImage"]
    assert request["location"] == {
        "index": _cell(doc, 1, 3, 3)["content_end"],
        "tabId": "t.0",
    }
    assert request["uri"] == "https://example.invalid/chart.png"
    assert out["ret"]["cell"] == {"row": 3, "column": 3}
    assert out["ret"]["table"] == 1


def test_an_image_takes_the_selector_a_read_handed_back() -> None:
    doc = _tasks_doc()
    google = _Google(doc)

    out = _embed(google, selector={"table": {"position": 2}}, row=1, column=1)

    assert out["ok"] is True
    assert google.writes[0]["requests"][0]["insertInlineImage"]["location"]["index"] == (
        _cell(doc, 2, 1, 1)["content_end"]
    )


def test_naming_both_a_cell_and_an_index_is_refused() -> None:
    google = _Google(_tasks_doc())

    out = _embed(google, table=1, row=2, column=1, index=5)

    assert out["error"]["code"] == "docs_image_target_ambiguous"
    assert google.writes == []


def test_an_image_is_refused_for_a_merged_away_cell() -> None:
    body = Body().table([["A", "B"], ["wide", ""]], spans={(1, 0): (1, 2)})
    google = _Google(document(("t.0", "Main", body)))

    out = _embed(google, table=1, row=2, column=2)

    assert out["error"]["code"] == "docs_table_cell_merged"
    assert out["error"]["details"]["merged_into"] == [2, 1]
    assert google.writes == []


def test_an_image_is_refused_for_a_cell_holding_a_nested_table() -> None:
    body = Body().table([["A"], [{"text": "", "nested": True}]])
    google = _Google(document(("t.0", "Main", body)))

    out = _embed(google, table=1, row=2, column=1)

    assert out["error"]["code"] == "docs_table_nested"
    assert google.writes == []


def test_an_image_with_no_cell_named_still_lands_in_the_body() -> None:
    google = _Google(_tasks_doc())

    out = _embed(google, index=3, width_pt=200)

    request = google.writes[0]["requests"][0]["insertInlineImage"]
    assert request["location"]["index"] == 3
    assert request["objectSize"]["width"] == {"magnitude": 200, "unit": "PT"}
    assert "cell" not in out["ret"]


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
    assert both["error"]["code"] == "docs_value_ambiguous"

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


def test_a_replacement_of_the_wrong_shape_is_told_the_right_one() -> None:
    google = _Google(_tasks_doc())

    out = _run(
        docs_proxy,
        "replace_text",
        {
            "document_ref": "DOC1",
            "all_tabs": True,
            "replacements": [{"text": "Open", "replace": "Done"}],
        },
        google,
    )

    # A refusal that only says a field is empty leaves the caller guessing;
    # this one names the shape that works and what actually arrived.
    assert out["error"]["code"] == "invalid_replacement"
    assert '{"find"' in out["error"]["message"]
    assert out["error"]["details"]["received_keys"] == ["replace", "text"]
    assert google.writes == []


def test_replacements_given_as_something_other_than_a_list_say_so() -> None:
    google = _Google(_tasks_doc())

    out = _run(
        docs_proxy,
        "replace_text",
        {"document_ref": "DOC1", "all_tabs": True, "replacements": {"Open": "Done"}},
        google,
    )

    assert out["error"]["code"] == "replacements_required"
    assert out["error"]["details"]["received_type"] == "dict"
    assert google.writes == []


# --------------------------------------------------------------------------- #
# An image in a cell is fitted to the column
# --------------------------------------------------------------------------- #


def _png(width: int, height: int) -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (width, height), (10, 120, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


def _image_response(width: int, height: int) -> httpx.Response:
    return httpx.Response(
        200, content=_png(width, height), headers={"content-type": "image/png"}
    )


@pytest.fixture
def allow_measurement(monkeypatch):
    """The measuring fetch passes the SSRF guard; the test URL never resolves."""

    from kdcube_ai_app.apps.chat.sdk.tools.backends.web import ssrf_guard

    async def _allowed(url: str):
        return ssrf_guard.Verdict(True, ssrf_guard.ReasonCode.ALLOWED, {"url": url})

    monkeypatch.setattr(ssrf_guard, "check_url", _allowed)
    return ssrf_guard


def _sized_doc(**kwargs: Any) -> dict[str, Any]:
    body = Body().table(
        [["Task", "Comment"], ["Ship docs", ""]],
        header_rows=1,
        **kwargs,
    )
    return document(("t.0", "Main", body))


def test_an_image_too_wide_for_its_cell_is_fitted_to_the_column(allow_measurement) -> None:
    # 800 px is 600 pt; the column is 200 pt wide, less 5 pt padding each side.
    google = _Google(_sized_doc(column_widths=[120.0, 200.0]), image=_image_response(800, 400))

    out = _embed(google, table=1, row=2, column="Comment")

    size = google.writes[0]["requests"][0]["insertInlineImage"]["objectSize"]
    assert size["width"] == {"magnitude": 190.0, "unit": "PT"}
    # The height follows the image's own proportions, not the cell's.
    assert size["height"] == {"magnitude": 95.0, "unit": "PT"}
    fit = out["ret"]["fit"]
    assert fit["reason"] == "cell_width"
    assert fit["cell_width_pt"] == 190.0
    assert fit["natural_pt"] == [600.0, 300.0]


def test_an_image_that_already_fits_is_left_alone(allow_measurement) -> None:
    # 200 px is 150 pt, well inside the 190 pt the cell offers.
    google = _Google(_sized_doc(column_widths=[120.0, 200.0]), image=_image_response(200, 100))

    out = _embed(google, table=1, row=2, column="Comment")

    # Docs scales to fit the box it is given, so a box here would enlarge it.
    assert "objectSize" not in google.writes[0]["requests"][0]["insertInlineImage"]
    assert "fit" not in out["ret"]


def test_a_size_the_caller_gave_is_never_second_guessed(allow_measurement) -> None:
    google = _Google(_sized_doc(column_widths=[120.0, 200.0]), image=_image_response(800, 400))

    out = _embed(google, table=1, row=2, column="Comment", width_pt=300)

    size = google.writes[0]["requests"][0]["insertInlineImage"]["objectSize"]
    assert size["width"] == {"magnitude": 300.0, "unit": "PT"}
    assert "fit" not in out["ret"]
    # Nothing was measured: the answer was already decided.
    assert google.image_fetches == []


def test_an_evenly_distributed_table_falls_back_to_the_page(allow_measurement) -> None:
    # 612 pt page less two 72 pt margins is 468 pt for two columns: 234 each.
    google = _Google(_sized_doc(), image=_image_response(800, 400))

    out = _embed(google, table=1, row=2, column="Comment")

    assert out["ret"]["fit"]["cell_width_pt"] == 224.0


def test_an_image_in_the_body_is_not_measured(allow_measurement) -> None:
    google = _Google(_sized_doc(column_widths=[120.0, 200.0]), image=_image_response(800, 400))

    out = _embed(google, index=3)

    assert "objectSize" not in google.writes[0]["requests"][0]["insertInlineImage"]
    assert google.image_fetches == []
    assert "fit" not in out["ret"]


def test_a_measurement_that_cannot_run_still_inserts_the_image(monkeypatch) -> None:
    from kdcube_ai_app.apps.chat.sdk.tools.backends.web import ssrf_guard

    async def _unresolvable(url: str):
        return ssrf_guard.Verdict(
            False, ssrf_guard.ReasonCode.RESOLUTION_FAILED, {"hostname": "example.invalid"}
        )

    monkeypatch.setattr(ssrf_guard, "check_url", _unresolvable)
    google = _Google(_sized_doc(column_widths=[120.0, 200.0]), image=_image_response(800, 400))

    out = _embed(google, table=1, row=2, column="Comment")

    # A name that does not resolve is Google's verdict to give, not ours.
    assert out["ok"] is True
    assert "objectSize" not in google.writes[0]["requests"][0]["insertInlineImage"]


def test_an_address_the_guard_refuses_is_not_fetched(monkeypatch) -> None:
    from kdcube_ai_app.apps.chat.sdk.tools.backends.web import ssrf_guard

    async def _blocked(url: str):
        return ssrf_guard.Verdict(
            False, ssrf_guard.ReasonCode.BLOCKED_PRIVATE_IP, {"blocked_ip": "10.0.0.5"}
        )

    monkeypatch.setattr(ssrf_guard, "check_url", _blocked)
    google = _Google(_sized_doc(column_widths=[120.0, 200.0]), image=_image_response(800, 400))

    out = _embed(google, table=1, row=2, column="Comment")

    # Measuring must not turn this proxy into a probe of its own network.
    assert out["error"]["code"] == "docs_image_uri_blocked"
    assert google.writes == []


def test_our_own_one_fetch_url_is_not_handed_back_as_a_source() -> None:
    body = Body().table([["Chart"], [_image_cell()]])
    minted = (
        "https://tunnel.invalid/api/integrations/bundles/t/p/kdcube-services%401-0"
        "/public/provider_fetch_download?object_ref=staged%3Aabc%3Achart.png"
        "&download_token=eyJhbGciOiJub25lIn0.sig"
    )
    doc = document(
        ("t.0", "Main", body),
        inline_objects={"img.1": inline_image(source_uri=minted)},
    )

    out = _run(docs_proxy, "get", {"document_ref": "DOC1", "tables": "all"}, _Google(doc))

    cell = out["ret"]["table_reads"][0]["cells"][1][0]
    # The URL is dead by now and carries a token; where it came from is the
    # part worth reporting.
    assert cell["objects"][0]["source"] == "kdcube_file"
    assert "source_uri" not in cell["objects"][0]
    assert "download_token" not in json.dumps(out)


def test_a_public_source_url_is_still_reported() -> None:
    body = Body().table([["Chart"], [_image_cell()]])
    doc = document(
        ("t.0", "Main", body),
        inline_objects={
            "img.1": inline_image(source_uri="https://example.invalid/chart.png")
        },
    )

    out = _run(docs_proxy, "get", {"document_ref": "DOC1", "tables": "all"}, _Google(doc))

    cell = out["ret"]["table_reads"][0]["cells"][1][0]
    assert cell["objects"][0]["source_uri"] == "https://example.invalid/chart.png"


# --------------------------------------------------------------------------- #
# Non-text elements in ordinary paragraphs
# --------------------------------------------------------------------------- #


def test_a_paragraph_shows_its_chips_where_they_sit() -> None:
    body = Body().paragraph(
        " reviews this by ",
        objects=(
            {"kind": "person", "email": "owner-a@example.com", "name": "Owner A"},
            {"kind": "dateElement", "text": "Oct 2, 2026"},
        ),
    )

    text = docs_proxy._extract_document_text(
        document(("t.0", "Main", body)), limit=4000
    )

    # Elements keep their place in the sentence, and each says what it is -
    # a paragraph has no second-level read to look the detail up in.
    assert "[person: owner-a@example.com][date: Oct 2, 2026] reviews this by" in text


def test_a_paragraph_holding_only_a_picture_is_no_longer_blank() -> None:
    body = Body().paragraph("", objects=({"kind": "inlineObjectElement", "id": "img.1"},))

    text = docs_proxy._extract_document_text(
        document(
            ("t.0", "Main", body),
            inline_objects={"img.1": inline_image(alt="Q3 revenue by region")},
        ),
        limit=4000,
    )

    assert "[image: Q3 revenue by region]" in text


def test_a_cell_and_a_paragraph_name_the_same_chip_the_same_way() -> None:
    chip = {"kind": "person", "email": "owner-b@example.com"}
    body = (
        Body()
        .paragraph("", objects=(chip,))
        .table([["Owner"], [{"text": "", "objects": (chip,)}]])
    )

    text = docs_proxy._extract_document_text(
        document(("t.0", "Main", body)), limit=4000
    )

    # One vocabulary for both renderings: the rule that keeps the two levels
    # from drifting applies to the readable text as much as to the records.
    assert text.count("[person: owner-b@example.com]") == 2


def test_the_text_that_carries_indices_stays_literal() -> None:
    body = Body().paragraph(
        "Owner ", objects=({"kind": "person", "email": "owner-a@example.com"},)
    )
    doc = document(("t.0", "Main", body))

    out = _run(docs_proxy_flex, "get_structure", {"document_ref": "DOC1"}, _Google(doc))

    element = out["ret"]["tabs"][0]["elements"][0]
    # This text is paired with start_index/end_index for batch_edit, and a
    # marker occupies no character in the document: putting one here would
    # move every offset computed from it.
    assert element["text"] == "Owner \n"
    assert "[person" not in element["text"]


def test_a_heading_with_a_chip_still_matches_its_selector() -> None:
    body = (
        Body()
        .heading("Tasks", 2)
        .table([["Task"], ["Fix login"]])
    )
    doc = document(("t.0", "Main", body))

    out = _run(
        docs_proxy,
        "get",
        {"document_ref": "DOC1", "tables": {"table": {"after_heading": "Tasks"}}},
        _Google(doc),
    )

    # Heading text is what after_heading matches on, so it stays literal too.
    assert out["ok"] is True
    assert out["ret"]["table_reads"][0]["after_heading"]["text"] == "Tasks"


# --------------------------------------------------------------------------- #
# Writing smart chips
# --------------------------------------------------------------------------- #


def _insert(google: _Google, **payload: Any) -> dict[str, Any]:
    return _run(docs_proxy, "insert_text", {"document_ref": "DOC1", **payload}, google)


def test_a_sentence_with_chips_is_written_as_ordered_pieces() -> None:
    google = _Google(_tasks_doc())

    out = _insert(
        google,
        index=10,
        tab_id="t.0",
        pieces=[
            {"text": "Owner "},
            {"person": "owner-a@example.com"},
            {"text": " reviews by "},
            {"date": "2026-10-02"},
            {"text": " per "},
            {"link": "https://example.invalid/plan"},
        ],
    )

    requests = google.writes[0]["requests"]
    # All at one index, written backwards so they land forwards - no offset
    # arithmetic anywhere, and none asked of the caller.
    assert {r["insertText"]["location"]["index"] for r in requests if "insertText" in r} == {10}
    kinds = [next(iter(request)) for request in requests]
    # Written last piece first, which is what lands them in the named order.
    assert kinds == [
        "insertRichLink",
        "insertText",
        "insertDate",
        "insertText",
        "insertPerson",
        "insertText",
    ]
    assert requests[4]["insertPerson"]["personProperties"] == {
        "email": "owner-a@example.com"
    }
    assert requests[2]["insertDate"]["dateElementProperties"] == {
        "timestamp": "2026-10-02T00:00:00Z"
    }
    # Google fills a link's title and icon itself, so only the uri is sent.
    assert requests[0]["insertRichLink"]["richLinkProperties"] == {
        "uri": "https://example.invalid/plan"
    }
    assert out["ret"]["inserted"]["pieces"] == [
        "text", "person", "text", "date", "text", "link"
    ]


def test_a_date_chip_keeps_the_zone_it_was_given() -> None:
    google = _Google(_tasks_doc())

    _insert(
        google,
        index=5,
        tab_id="t.0",
        pieces=[{"date": {"value": "2026-10-02T09:30:00", "time_zone": "Europe/Kyiv"}}],
    )

    properties = google.writes[0]["requests"][0]["insertDate"]["dateElementProperties"]
    assert properties == {
        "timestamp": "2026-10-02T09:30:00Z",
        "timeZoneId": "Europe/Kyiv",
    }


def test_text_and_pieces_together_are_refused() -> None:
    google = _Google(_tasks_doc())

    out = _insert(google, index=5, tab_id="t.0", text="hello", pieces=[{"text": "hi"}])

    assert out["error"]["code"] == "docs_value_ambiguous"
    assert google.writes == []


def test_a_piece_naming_two_kinds_is_refused() -> None:
    google = _Google(_tasks_doc())

    out = _insert(
        google,
        index=5,
        tab_id="t.0",
        pieces=[{"person": "a@b.com", "date": "2026-10-02"}],
    )

    assert out["error"]["code"] == "docs_value_ambiguous"
    assert google.writes == []


def test_a_date_that_is_not_a_date_says_what_one_looks_like() -> None:
    google = _Google(_tasks_doc())

    out = _insert(google, index=5, tab_id="t.0", pieces=[{"date": "next tuesday"}])

    assert out["error"]["code"] == "docs_date_invalid"
    assert "2026-10-02" in out["error"]["message"]
    assert google.writes == []


def test_a_link_that_is_not_a_url_is_refused() -> None:
    google = _Google(_tasks_doc())

    out = _insert(google, index=5, tab_id="t.0", pieces=[{"link": "the quarterly plan"}])

    assert out["error"]["code"] == "docs_link_invalid"
    assert google.writes == []


def test_a_cell_takes_a_date_chip_the_same_way_it_takes_a_person() -> None:
    google = _Google(_tasks_doc())

    out = _set_cells(
        google,
        table=1,
        row=2,
        cells=[{"column": "Status", "date": "2026-10-02"}],
    )

    assert out["ok"] is True
    request = [r for r in google.writes[0]["requests"] if "insertDate" in r][0]
    assert request["insertDate"]["dateElementProperties"]["timestamp"] == (
        "2026-10-02T00:00:00Z"
    )


def test_a_cell_takes_a_link_chip() -> None:
    google = _Google(_tasks_doc())

    out = _set_cells(
        google,
        table=1,
        row=2,
        cells=[{"column": "Status", "link": "https://example.invalid/plan"}],
    )

    assert out["ok"] is True
    request = [r for r in google.writes[0]["requests"] if "insertRichLink" in r][0]
    assert request["insertRichLink"]["richLinkProperties"] == {
        "uri": "https://example.invalid/plan"
    }


# --------------------------------------------------------------------------- #
# Headings and lists as units (kdcube#243 item 6)
# --------------------------------------------------------------------------- #


def _paragraph_start(doc: dict[str, Any], number: int = 0) -> int:
    """Where a paragraph begins - the only place a block may be written."""

    from kdcube_ai_app.apps.chat.sdk.integrations.google.docs_structure import (
        body_segments,
    )

    body = doc["tabs"][0]["documentTab"]["body"]
    paragraphs = [row for row in body_segments(body) if row["kind"] != "cell"]
    return int(paragraphs[number]["start"])


def test_a_heading_is_written_as_one_unit() -> None:
    doc = _tasks_doc()
    google = _Google(doc)
    start = _paragraph_start(doc, 1)

    out = _insert(
        google, index=start, tab_id="t.0", text="Release notes", style="heading_2"
    )

    requests = google.writes[0]["requests"]
    # Written backwards, so the paragraph's own terminating newline goes first
    # and the text after it. Two inserts and no more: at a paragraph start
    # nothing has to be opened, unlike an append at the body's end.
    assert [r["insertText"]["text"] for r in requests if "insertText" in r] == [
        "\n",
        "Release notes",
    ]
    style = requests[2]["updateParagraphStyle"]
    assert style["paragraphStyle"] == {"namedStyleType": "HEADING_2"}
    # The mask is required, and it must name only what the call changes.
    assert style["fields"] == "namedStyleType"
    assert style["range"] == {
        "startIndex": start,
        "endIndex": start + len("Release notes\n"),
        "tabId": "t.0",
    }
    assert out["ret"]["wrote"]["paragraphs"] == 1


def test_a_style_range_counts_the_way_docs_counts() -> None:
    doc = _tasks_doc()
    google = _Google(doc)
    start = _paragraph_start(doc, 1)

    _insert(google, index=start, tab_id="t.0", text="Ship 🚀 now", style="heading_1")

    style = [r for r in google.writes[0]["requests"] if "updateParagraphStyle" in r][0]
    # A rocket is one Python character and two index positions; counting it as
    # one would pull the neighbouring paragraph into the heading.
    assert style["updateParagraphStyle"]["range"]["endIndex"] == start + 12


def test_a_list_is_written_as_one_unit() -> None:
    doc = _tasks_doc()
    google = _Google(doc)
    start = _paragraph_start(doc, 1)

    out = _insert(
        google,
        index=start,
        tab_id="t.0",
        items=["Fix login", "Ship docs", "Review"],
        list="bullet",
    )

    requests = google.writes[0]["requests"]
    bullets = [r for r in requests if "createParagraphBullets" in r][0]
    assert bullets["createParagraphBullets"]["bulletPreset"] == "BULLET_DISC_CIRCLE_SQUARE"
    assert bullets["createParagraphBullets"]["range"]["endIndex"] == start + len(
        "Fix login\nShip docs\nReview\n"
    )
    assert out["ret"]["wrote"]["paragraphs"] == 3


def test_a_numbered_list_item_can_hold_a_chip() -> None:
    doc = _tasks_doc()
    google = _Google(doc)
    start = _paragraph_start(doc, 1)

    _insert(
        google,
        index=start,
        tab_id="t.0",
        items=[
            "Fix login",
            [{"text": "Review by "}, {"person": "owner-a@example.com"}],
        ],
        list="number",
    )

    requests = google.writes[0]["requests"]
    assert [r for r in requests if "insertPerson" in r]
    bullets = [r for r in requests if "createParagraphBullets" in r][0]
    assert bullets["createParagraphBullets"]["bulletPreset"] == (
        "NUMBERED_DECIMAL_ALPHA_ROMAN"
    )
    # The chip occupies one index position, the same as any other element.
    assert bullets["createParagraphBullets"]["range"]["endIndex"] == start + len(
        "Fix login\nReview by \n"
    ) + 1


def test_a_heading_in_the_middle_of_a_sentence_is_refused() -> None:
    google = _Google(_tasks_doc())

    out = _insert(google, index=5, tab_id="t.0", text="Notes", style="heading_2")

    # A paragraph style covers every paragraph its range touches, so this one
    # would restyle the sentence it landed in.
    assert out["error"]["code"] == "docs_block_needs_its_own_paragraph"
    assert "Report" in out["error"]["details"]["inside_text"]
    assert google.writes == []


def test_an_unknown_style_lists_the_ones_that_work() -> None:
    google = _Google(_tasks_doc())

    out = _insert(google, index=1, tab_id="t.0", text="Notes", style="header2")

    assert out["error"]["code"] == "docs_style_unsupported"
    assert "heading_2" in out["error"]["message"]
    assert google.writes == []


def test_items_and_text_together_are_refused() -> None:
    google = _Google(_tasks_doc())

    out = _insert(google, index=1, tab_id="t.0", text="a", items=["b"], list="bullet")

    assert out["error"]["code"] == "docs_value_ambiguous"
    assert google.writes == []


def test_a_heading_appended_at_the_end_opens_its_own_paragraph() -> None:
    doc = _tasks_doc()
    google = _Google(doc)

    out = _run(
        docs_proxy,
        "append_text",
        {"document_ref": "DOC1", "tab_id": "t.0", "text": "Summary", "style": "heading_3"},
        google,
    )

    requests = google.writes[0]["requests"]
    # The body end sits inside the last paragraph, so a break comes first and
    # the styled range starts after it.
    assert requests[-1]["updateParagraphStyle"]["range"]["startIndex"] == (
        out["ret"]["wrote"]["start_index"]
    )
    assert out["ret"]["wrote"]["style"] == "HEADING_3"


def test_a_date_chip_reads_the_way_the_document_shows_it() -> None:
    body = Body().paragraph(
        "Due ",
        objects=({"kind": "dateElement", "text": "Oct 2, 2026"},),
    )

    text = docs_proxy._extract_document_text(
        document(("t.0", "Main", body)), limit=4000
    )

    # Docs answers with displayText; reading 'text' left every date chip blank,
    # and the fixture agreed with the mistake until a live document disagreed.
    assert "[date: Oct 2, 2026]" in text


def test_a_link_google_will_not_take_is_explained() -> None:
    google = _Google(
        _tasks_doc(),
        updates=[
            httpx.Response(
                400,
                json={
                    "error": {
                        "code": 400,
                        "status": "INVALID_ARGUMENT",
                        "message": (
                            "Invalid requests[0].insertRichLink: The URL is invalid."
                        ),
                    }
                },
            )
        ],
    )

    out = _insert(
        google,
        index=1,
        tab_id="t.0",
        pieces=[{"link": "https://github.com/kdcube/kdcube/issues/243"}],
    )

    # A link chip points at a Google resource; the provider's own message does
    # not say that, so this one does.
    assert out["error"]["code"] == "docs_link_unsupported"
    assert "Drive file" in out["error"]["message"]
    assert "insertRichLink" in out["error"]["details"]["provider_message"]


def test_a_pieces_write_does_not_report_a_plain_text_count() -> None:
    google = _Google(_tasks_doc())

    out = _insert(
        google,
        index=1,
        tab_id="t.0",
        pieces=[{"text": "Owner "}, {"person": "owner-a@example.com"}],
    )

    # The plain-text counter belongs to the plain-text path; beside pieces it
    # would always read zero and contradict the count that is true.
    assert "inserted_chars" not in out["ret"]
    assert out["ret"]["inserted"]["chars"] == len("Owner ")


def test_a_pieces_preview_counts_only_once() -> None:
    google = _Google(_tasks_doc())

    out = _insert(
        google,
        index=1,
        tab_id="t.0",
        preview=True,
        pieces=[{"text": "Owner "}, {"person": "owner-a@example.com"}],
    )

    assert "would_insert_chars" not in out["ret"]
    assert out["ret"]["would_insert"]["chars"] == len("Owner ")
    assert out["ret"]["written"] is False
    assert google.writes == []


def test_every_object_kind_has_its_own_marker() -> None:
    # The recipe publishes this vocabulary as closed, and _object_label falls
    # back to the kind's own name, so a family added to the walk without a
    # marker would read as [rich_link] instead of [link] and the doc would
    # quietly stop being true.
    from kdcube_ai_app.apps.chat.sdk.integrations.google.docs_structure import (
        OBJECT_KINDS,
    )

    assert set(OBJECT_KINDS.values()) == set(docs_proxy._CELL_MARKERS)
