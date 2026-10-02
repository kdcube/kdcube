# SPDX-License-Identifier: MIT
"""Comments attached to text: where the adapter places them and how they read back.

Documents come from docs_tables_fixture, so every expected range is computed
from the same UTF-16 rules Google uses.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.google import docs_proxy, docs_proxy_flex
from kdcube_ai_app.apps.chat.sdk.integrations.google.docs_structure import (
    body_tables,
    find_text,
    text_spans,
)
from kdcube_ai_app.apps.chat.sdk.integrations.google.tests.docs_tables_fixture import (
    Body,
    document,
    tasks_body,
    utf16_len,
)


def _thread(comment_id: str, anchor: str, quote: str) -> dict[str, Any]:
    return {
        "commentId": comment_id,
        "anchorId": anchor,
        "status": "OPEN",
        "plainTextQuote": quote,
        "headPost": {
            "postId": comment_id,
            "content": "note",
            "author": {"displayName": "Reviewer", "me": True},
            "createTime": "2026-10-02T10:00:00Z",
            "updateTime": "2026-10-02T10:00:00Z",
        },
    }


class _Google:
    """Docs get / batchUpdate and Drive comments, recorded."""

    def __init__(
        self,
        doc: dict[str, Any],
        *,
        update: dict[str, Any] | None = None,
        drive_comments: list[dict[str, Any]] | None = None,
        anchors: dict[str, dict[str, Any]] | None = None,
        anchors_status: int = 200,
    ) -> None:
        self.doc = doc
        self.update = update
        self.drive_comments = drive_comments or []
        self.anchors = anchors or {}
        self.anchors_status = anchors_status
        self.writes: list[dict[str, Any]] = []
        self.drive_posts: list[dict[str, Any]] = []
        self.reads: list[dict[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.googleapis.com":
            if request.method == "POST":
                body = json.loads(request.content)
                self.drive_posts.append(body)
                return httpx.Response(
                    200, json={"id": "drive-1", "content": body["content"]}, request=request
                )
            if request.url.path.endswith("/comments"):
                return httpx.Response(200, json={"comments": self.drive_comments}, request=request)
            return httpx.Response(200, json=self.drive_comments[0], request=request)
        if request.method == "GET":
            params = dict(request.url.params)
            self.reads.append(params)
            if params.get("commentsViewMode"):
                if self.anchors_status != 200:
                    return httpx.Response(
                        self.anchors_status,
                        json={"error": {"code": self.anchors_status, "message": "backend error"}},
                        request=request,
                    )
                doc = json.loads(json.dumps(self.doc))
                for tab in doc["tabs"]:
                    tab_id = tab["tabProperties"]["tabId"]
                    tab["documentTab"]["commentAnchors"] = {
                        anchor_id: {"anchorId": anchor_id, "ranges": entry["ranges"]}
                        for anchor_id, entry in self.anchors.items()
                        if entry["tab_id"] == tab_id
                    }
                return httpx.Response(200, json=doc, request=request)
            return httpx.Response(200, json=self.doc, request=request)
        body = json.loads(request.content)
        self.writes.append(body)
        if self.update is not None:
            return httpx.Response(200, json=self.update, request=request)
        comment = body["requests"][0]["insertComment"]
        return httpx.Response(
            200,
            json={
                "writeControl": {"requiredRevisionId": "rev-2"},
                "commentUpdateState": "ALL_SAVED",
                "replies": [
                    {"insertComment": {"commentThread": _thread("c-new", "kix.new", "quoted")}}
                ],
                "documentId": "DOC1",
                "_echo": comment,
            },
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
        return asyncio.run(execute(operation=operation, access_token="tok", payload=payload))
    finally:
        docs_proxy.httpx.AsyncClient = original  # type: ignore[assignment]


def _comment(google: _Google, **payload: Any) -> dict[str, Any]:
    return _run(docs_proxy, "create_comment", {"document_ref": "DOC1", **payload}, google)


def _tasks_doc() -> dict[str, Any]:
    return document(("t.0", "Main", tasks_body()))


def _body(doc: dict[str, Any], tab: int = 0) -> dict[str, Any]:
    return doc["tabs"][tab]["documentTab"]["body"]


def _cell(doc: dict[str, Any], table: int, row: int, column: int) -> dict[str, Any]:
    return body_tables(_body(doc))[table - 1]["cells"][row - 1][column - 1]


def _sent_comment(google: _Google) -> dict[str, Any]:
    assert len(google.writes) == 1
    return google.writes[0]["requests"][0]["insertComment"]


# --------------------------------------------------------------------------- #
# Text to index
# --------------------------------------------------------------------------- #


def test_a_phrase_after_an_emoji_lands_on_utf16_indices() -> None:
    matches = find_text(text_spans(_body(_tasks_doc())), "July")
    assert len(matches) == 1
    start = 1 + utf16_len("Report 🚀 ")
    assert (matches[0]["start_index"], matches[0]["end_index"]) == (start, start + 4)
    assert matches[0]["kind"] == "paragraph"


def test_a_chip_before_the_phrase_shifts_its_indices() -> None:
    body = Body().paragraph("Owner signs off", objects=("person",)).as_body()
    matches = find_text(text_spans(body), "signs off")
    # The person chip holds index 1, the text run starts at 2.
    assert matches[0]["start_index"] == 2 + utf16_len("Owner ")
    assert matches[0]["end_index"] == 2 + utf16_len("Owner signs off")


def test_a_phrase_does_not_match_across_a_chip() -> None:
    body = Body().paragraph("before", objects=()).as_body()
    body["content"][0]["paragraph"]["elements"].insert(
        0, {"startIndex": 1, "endIndex": 2, "person": {}}
    )
    spans = text_spans(body)
    assert spans[0]["text"].startswith("￼")
    assert find_text(spans, "￼before") != []  # the placeholder is literal...
    assert find_text(spans, "Xbefore") == []  # ...and no caller text equals it


def test_a_phrase_in_a_cell_names_its_table_row_and_column() -> None:
    doc = _tasks_doc()
    matches = find_text(text_spans(_body(doc)), "Ship docs")
    assert len(matches) == 1
    assert {key: matches[0][key] for key in ("kind", "table", "row", "column")} == {
        "kind": "cell", "table": 1, "row": 3, "column": 1,
    }
    cell = _cell(doc, 1, 3, 1)
    assert matches[0]["start_index"] == cell["content_start"]
    assert matches[0]["end_index"] == cell["content_end"]


def test_a_phrase_never_spans_two_paragraphs() -> None:
    assert find_text(text_spans(_body(_tasks_doc())), "July\nTasks") == []


def test_match_case_false_finds_other_casing() -> None:
    spans = text_spans(_body(_tasks_doc()))
    assert find_text(spans, "ship DOCS") == []
    assert len(find_text(spans, "ship DOCS", match_case=False)) == 1


# --------------------------------------------------------------------------- #
# create_comment
# --------------------------------------------------------------------------- #


def test_quoted_text_attaches_the_comment_to_exactly_that_text() -> None:
    google = _Google(_tasks_doc())
    out = _comment(google, content="Which week?", quoted_text="Open items for the week")
    assert out["ok"] is True, out
    sent = _sent_comment(google)
    match = find_text(text_spans(_body(google.doc)), "Open items for the week")[0]
    assert sent == {
        "content": "Which week?",
        "range": {"startIndex": match["start_index"], "endIndex": match["end_index"], "tabId": "t.0"},
    }
    assert google.writes[0]["writeControl"] == {"requiredRevisionId": "rev-1"}
    ret = out["ret"]
    assert ret["scope"] == "text" and ret["where"] == "paragraph"
    assert ret["quoted_text"] == "Open items for the week"
    assert ret["comment"]["comment_id"] == "c-new" and ret["comment"]["anchor"] == "kix.new"


def test_a_repeated_phrase_lists_candidates_and_writes_nothing() -> None:
    google = _Google(_tasks_doc())
    out = _comment(google, content="Status?", quoted_text="Open")
    assert out["ok"] is False and out["error"]["code"] == "quoted_text_ambiguous"
    assert out["ret"]["occurrences"] == 3
    candidates = out["ret"]["candidates"]
    assert [c["occurrence"] for c in candidates] == [1, 2, 3]
    assert candidates[0]["where"] == "paragraph"
    assert candidates[1]["where"] == {"table": 1, "row": 3, "column": 2}
    assert google.writes == []


def test_occurrence_picks_one_match() -> None:
    google = _Google(_tasks_doc())
    out = _comment(google, content="Still open?", quoted_text="Open", occurrence=3)
    assert out["ok"] is True, out
    cell = _cell(google.doc, 1, 4, 2)
    assert _sent_comment(google)["range"]["startIndex"] == cell["content_start"]
    assert out["ret"]["scope"] == "cell"
    assert out["ret"]["where"] == {"table": 1, "row": 4, "column": 2}


def test_an_occurrence_out_of_range_is_refused() -> None:
    google = _Google(_tasks_doc())
    out = _comment(google, content="x", quoted_text="Open", occurrence=4)
    assert out["ok"] is False and out["error"]["code"] == "invalid_occurrence"
    assert google.writes == []


def test_text_that_is_not_there_is_refused() -> None:
    google = _Google(_tasks_doc())
    out = _comment(google, content="x", quoted_text="Closed items")
    assert out["ok"] is False and out["error"]["code"] == "quoted_text_not_found"
    assert google.writes == []


def test_a_cell_alone_attaches_the_comment_to_its_text() -> None:
    google = _Google(_tasks_doc())
    out = _comment(google, content="Who?", table=1, row=3, column="Owner")
    assert out["ok"] is False and out["error"]["code"] == "docs_comment_cell_empty"
    out = _comment(google, content="Done?", table=1, row=3, column=1)
    assert out["ok"] is True, out
    cell = _cell(google.doc, 1, 3, 1)
    assert _sent_comment(google)["range"] == {
        "startIndex": cell["content_start"], "endIndex": cell["content_end"], "tabId": "t.0",
    }
    assert out["ret"]["scope"] == "cell" and out["ret"]["quoted_text"] == "Ship docs"


def test_a_cell_narrows_where_quoted_text_is_found() -> None:
    google = _Google(_tasks_doc())
    out = _comment(google, content="Open?", quoted_text="Open", table=1, row=4, column="Status")
    assert out["ok"] is True, out
    assert out["ret"]["where"] == {"table": 1, "row": 4, "column": 2}


def test_preview_shows_the_target_and_writes_nothing() -> None:
    google = _Google(_tasks_doc())
    out = _comment(google, content="x", quoted_text="Budget", preview=True)
    assert out["ok"] is True
    assert out["ret"]["written"] is False and out["ret"]["where"] == "heading"
    assert out["ret"]["quoted_text"] == "Budget"
    assert google.writes == [] and google.drive_posts == []


def test_without_a_target_the_comment_belongs_to_the_document() -> None:
    google = _Google(_tasks_doc())
    out = _comment(google, content="General note")
    assert out["ok"] is True
    assert out["ret"]["scope"] == "document"
    assert google.drive_posts == [{"content": "General note"}]
    assert google.writes == []


def test_a_tab_without_a_target_is_refused() -> None:
    google = _Google(_tasks_doc())
    out = _comment(google, content="x", tab_id="t.0")
    assert out["ok"] is False and out["error"]["code"] == "docs_comment_target_required"
    assert google.writes == [] and google.drive_posts == []


def test_the_named_tab_is_searched_and_stamped() -> None:
    doc = document(
        ("t.0", "Main", Body().paragraph("Shared phrase")),
        ("t.2", "Notes", Body().paragraph("Only here: Shared phrase")),
    )
    google = _Google(doc)
    out = _comment(google, content="x", quoted_text="Only here", tab_id="t.2")
    assert out["ok"] is True, out
    assert _sent_comment(google)["range"]["tabId"] == "t.2"
    assert out["ret"]["tab_title"] == "Notes"
    many = _comment(_Google(doc), content="x", quoted_text="Shared phrase")
    assert many["ok"] is False and many["error"]["code"] == "docs_tab_selection_required"
    assert "commented text" in many["error"]["message"]


def test_a_comment_google_did_not_save_is_reported() -> None:
    google = _Google(
        _tasks_doc(),
        update={"commentUpdateState": "ALL_FAILED_UNKNOWN_REASON", "replies": [{}]},
    )
    out = _comment(google, content="x", quoted_text="Budget")
    assert out["ok"] is False and out["error"]["code"] == "docs_comment_not_saved"
    assert out["ret"]["comment_update_state"] == "ALL_FAILED_UNKNOWN_REASON"


def test_an_anchored_comment_respects_googles_size_limit() -> None:
    google = _Google(_tasks_doc())
    out = _comment(google, content="é" * 1025, quoted_text="Budget")
    assert out["ok"] is False and out["error"]["code"] == "content_too_large"
    assert google.writes == []


# --------------------------------------------------------------------------- #
# Reading anchors back
# --------------------------------------------------------------------------- #


def _drive_row(comment_id: str, anchor: str = "", quote: str = "") -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": comment_id,
        "content": f"note {comment_id}",
        "resolved": False,
        "author": {"displayName": "Reviewer", "me": True},
    }
    if anchor:
        row["anchor"] = anchor
    if quote:
        row["quotedFileContent"] = {"value": quote}
    return row


def test_list_comments_says_what_each_comment_is_attached_to() -> None:
    doc = _tasks_doc()
    cell = _cell(doc, 1, 3, 1)
    july = find_text(text_spans(_body(doc)), "July")[0]
    google = _Google(
        doc,
        drive_comments=[
            _drive_row("c1", "kix.cell", "Ship docs"),
            _drive_row("c2", "kix.text", "June"),
            _drive_row("c3"),
            _drive_row("c4", "kix.gone", "deleted words"),
        ],
        anchors={
            "kix.cell": {"tab_id": "t.0", "ranges": [{"startIndex": cell["content_start"], "endIndex": cell["content_end"]}]},
            "kix.text": {"tab_id": "t.0", "ranges": [{"startIndex": july["start_index"], "endIndex": july["end_index"]}]},
            "kix.gone": {"tab_id": "t.0", "ranges": []},
        },
    )
    out = _run(docs_proxy, "list_comments", {"document_ref": "DOC1"}, google)
    assert out["ok"] is True, out
    rows = {row["comment_id"]: row for row in out["ret"]["comments"]}
    assert rows["c1"]["scope"] == "cell"
    assert rows["c1"]["where"] == {"table": 1, "row": 3, "column": 1}
    assert rows["c1"]["current_text"] == "Ship docs"
    assert rows["c2"]["scope"] == "text" and rows["c2"]["where"] == "paragraph"
    assert rows["c2"]["quoted_text"] == "June" and rows["c2"]["current_text"] == "July"
    assert rows["c3"]["scope"] == "document"
    assert rows["c4"]["anchor_state"] == "detached"
    assert "start_index" not in json.dumps(out["ret"])
    assert any(read.get("commentsViewMode") == "COMMENTS_VIEW_MODE_INCLUDED" for read in google.reads)


def test_list_comments_narrows_to_one_tab() -> None:
    doc = document(("t.0", "Main", Body().paragraph("alpha")), ("t.2", "Notes", Body().paragraph("beta")))
    google = _Google(
        doc,
        drive_comments=[_drive_row("c1", "kix.a"), _drive_row("c2", "kix.b"), _drive_row("c3")],
        anchors={
            "kix.a": {"tab_id": "t.0", "ranges": [{"startIndex": 1, "endIndex": 6}]},
            "kix.b": {"tab_id": "t.2", "ranges": [{"startIndex": 1, "endIndex": 5}]},
        },
    )
    out = _run(docs_proxy, "list_comments", {"document_ref": "DOC1", "tab_selector": {"title": "Notes"}}, google)
    assert out["ok"] is True, out
    assert [row["comment_id"] for row in out["ret"]["comments"]] == ["c2"]
    assert out["ret"]["comments"][0]["current_text"] == "beta"
    assert out["ret"]["tab_id"] == "t.2"


def test_get_comment_carries_its_anchor() -> None:
    doc = _tasks_doc()
    budget = find_text(text_spans(_body(doc)), "Budget")[0]
    google = _Google(
        doc,
        drive_comments=[_drive_row("c1", "kix.h", "Budget")],
        anchors={"kix.h": {"tab_id": "t.0", "ranges": [{"startIndex": budget["start_index"], "endIndex": budget["end_index"]}]}},
    )
    out = _run(docs_proxy, "get_comment", {"document_ref": "DOC1", "comment_id": "c1"}, google)
    assert out["ok"] is True, out
    assert out["ret"]["comment"]["where"] == "heading"
    assert out["ret"]["comment"]["tab_title"] == "Main"


def test_comments_still_list_when_anchors_cannot_be_read() -> None:
    google = _Google(_tasks_doc(), drive_comments=[_drive_row("c1", "kix.a")], anchors_status=500)
    out = _run(docs_proxy, "list_comments", {"document_ref": "DOC1"}, google)
    assert out["ok"] is True, out
    assert [row["comment_id"] for row in out["ret"]["comments"]] == ["c1"]
    assert "scope" not in out["ret"]["comments"][0]
    assert out["ret"]["anchors_unavailable"]


# --------------------------------------------------------------------------- #
# Flexible path
# --------------------------------------------------------------------------- #


def test_batch_edit_inserts_a_comment_on_an_explicit_range() -> None:
    google = _Google(_tasks_doc())
    out = _run(
        docs_proxy_flex,
        "batch_edit",
        {
            "document_ref": "DOC1",
            "requests": [{"insertComment": {"content": "Check", "range": {"startIndex": 1, "endIndex": 7}}}],
        },
        google,
    )
    assert out["ok"] is True, out
    assert google.writes[0]["requests"][0]["insertComment"]["range"]["tabId"] == "t.0"
    assert out["ret"]["comment_update_state"] == "ALL_SAVED"
    assert out["ret"]["comments"][0]["comment_id"] == "c-new"


@pytest.mark.parametrize(
    ("spec", "code"),
    [
        ({"content": "", "range": {"startIndex": 1, "endIndex": 2}}, "content_required"),
        ({"content": "x", "range": {"startIndex": 3, "endIndex": 3}}, "invalid_range"),
        ({"content": "x"}, "invalid_range"),
        ({"content": "x", "range": {"startIndex": 1, "endIndex": 2}, "assigneeEmailAddress": "a@example.test"}, "comment_assignee_not_allowed"),
        ({"content": "é" * 1025, "range": {"startIndex": 1, "endIndex": 2}}, "content_too_large"),
    ],
)
def test_batch_edit_bounds_insert_comment(spec: dict[str, Any], code: str) -> None:
    google = _Google(_tasks_doc())
    out = _run(docs_proxy_flex, "batch_edit", {"document_ref": "DOC1", "requests": [{"insertComment": spec}]}, google)
    assert out["ok"] is False and out["error"]["code"] == code
    assert google.writes == []
