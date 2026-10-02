# SPDX-License-Identifier: MIT
"""Flexible Google Docs proxy: tab-aware structural read + governed batch_edit.

The FLEXIBLE alternative to the narrow typed proxy. These tests pin the two
things it adds over the narrow one — a structure/tab-faithful read and a
native-request batch_edit that stays governed by an allowlist + bounds + tab
targeting — over a mocked httpx transport."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

import httpx

from kdcube_ai_app.apps.chat.sdk.integrations.google import docs_proxy_flex as flex


def _run(operation: str, payload: dict[str, Any], handler: Callable[[httpx.Request], httpx.Response],
         *, token: str = "tok-xyz"):
    transport = httpx.MockTransport(handler)
    real = flex.httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real(*args, **kwargs)

    flex.httpx.AsyncClient = _factory  # type: ignore[assignment]
    try:
        return asyncio.run(
            flex.execute_google_docs_flex_operation(
                operation=operation, access_token=token, payload=payload
            )
        )
    finally:
        flex.httpx.AsyncClient = real  # type: ignore[assignment]


def _json(request: httpx.Request, body: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body, request=request)


_TABBED_DOC = {
    "documentId": "D1",
    "title": "Design",
    "revisionId": "r1",
    "tabs": [
        {
            "tabProperties": {"tabId": "t.0", "title": "Overview", "index": 0},
            "documentTab": {"body": {"content": [
                {"startIndex": 1, "endIndex": 10, "paragraph": {
                    "paragraphStyle": {"namedStyleType": "HEADING_1"},
                    "elements": [{"textRun": {"content": "Intro\n"}}]}},
                {"startIndex": 10, "endIndex": 30, "paragraph": {
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                    "elements": [{"textRun": {"content": "Body text\n"}}]}},
            ]}},
            "childTabs": [
                {"tabProperties": {"tabId": "t.0.1", "title": "Sub", "index": 0},
                 "documentTab": {"body": {"content": [
                     {"startIndex": 1, "endIndex": 5, "table": {"rows": 2, "columns": 3}},
                 ]}}},
            ],
        },
        {
            "tabProperties": {"tabId": "t.1", "title": "Appendix", "index": 1},
            "documentTab": {"body": {"content": [
                {"startIndex": 1, "endIndex": 8, "paragraph": {
                    "elements": [{"textRun": {"content": "Notes\n"}}]}},
            ]}},
        },
    ],
}


def test_get_structure_returns_tab_hierarchy_with_indexed_elements():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["includeTabsContent"] == "true"
        return _json(request, _TABBED_DOC)

    out = _run("get_structure", {"document_ref": "D1"}, handler)
    assert out["ok"] is True
    ret = out["ret"]
    assert ret["tab_count"] == 3  # Overview, its child Sub, Appendix (flattened)
    tabs = {t["tab_id"]: t for t in ret["tabs"]}
    # child tab preserves its parent
    assert tabs["t.0.1"]["parent_tab_id"] == "t.0"
    # structural graph: heading vs paragraph, with indices, per tab
    overview = tabs["t.0"]["elements"]
    assert overview[0]["type"] == "heading" and overview[0]["style"] == "HEADING_1"
    assert overview[0]["start_index"] == 1 and overview[1]["type"] == "paragraph"
    assert tabs["t.0.1"]["elements"][0]["type"] == "table"


def test_get_structure_synthesizes_single_tab_for_tabless_doc():
    doc = {"documentId": "D2", "title": "Flat", "body": {"content": [
        {"startIndex": 1, "endIndex": 6, "paragraph": {"elements": [{"textRun": {"content": "hi\n"}}]}}]}}
    out = _run("get_structure", {"document_ref": "D2"}, lambda r: _json(r, doc))
    assert out["ok"] is True and out["ret"]["tab_count"] == 1
    assert out["ret"]["tabs"][0]["tab_id"] == "" and out["ret"]["tabs"][0]["elements"][0]["text"] == "hi\n"


def test_list_tabs_omits_content():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["includeTabsContent"] == "true"
        return _json(request, _TABBED_DOC)

    out = _run("list_tabs", {"document_ref": "D1"}, handler)
    assert out["ok"] is True and out["ret"]["tab_count"] == 3
    assert "elements" not in out["ret"]["tabs"][0]


def test_batch_edit_passes_allowlisted_requests_and_stamps_tab_id():
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return _json(request, _TABBED_DOC)
        captured["body"] = json.loads(request.content)
        return _json(request, {"replies": [{}, {}]})

    out = _run("batch_edit", {"document_ref": "D1", "tab_id": "t.1", "requests": [
        {"insertText": {"location": {"index": 1}, "text": "Hello"}},
        {"replaceAllText": {"containsText": {"text": "foo"}, "replaceText": "bar"}},
    ]}, handler)
    assert out["ok"] is True
    assert out["ret"]["applied_requests"] == 2
    assert out["ret"]["request_kinds"] == ["insertText", "replaceAllText"]
    sent = captured["body"]["requests"]
    assert sent[0]["insertText"]["location"]["tabId"] == "t.1"
    assert sent[1]["replaceAllText"]["tabsCriteria"] == {"tabIds": ["t.1"]}
    assert out["ret"]["tab_scope"] == "selected"
    assert out["ret"]["tab_count"] == 3


def test_batch_edit_requires_tab_for_multi_tab_document():
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return _json(request, _TABBED_DOC)

    out = _run(
        "batch_edit",
        {
            "document_ref": "D1",
            "requests": [
                {"insertText": {"location": {"index": 1}, "text": "Hello"}}
            ],
        },
        handler,
    )

    assert out["ok"] is False
    assert out["error"]["code"] == "docs_tab_selection_required"
    assert out["ret"]["tab_count"] == 3
    assert methods == ["GET"]


def test_batch_edit_all_tabs_is_explicit_and_replace_only():
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return _json(request, _TABBED_DOC)
        captured["body"] = json.loads(request.content)
        return _json(request, {"replies": [{}]})

    out = _run(
        "batch_edit",
        {
            "document_ref": "D1",
            "all_tabs": True,
            "requests": [
                {
                    "replaceAllText": {
                        "containsText": {"text": "old"},
                        "replaceText": "new",
                    }
                }
            ],
        },
        handler,
    )

    assert out["ok"] is True
    assert out["ret"]["tab_scope"] == "all"
    assert out["ret"]["tab_ids"] == ["t.0", "t.0.1", "t.1"]
    replacement = captured["body"]["requests"][0]["replaceAllText"]
    assert replacement["tabsCriteria"] == {
        "tabIds": ["t.0", "t.0.1", "t.1"]
    }


def test_batch_edit_rejects_all_tabs_for_non_replace_request():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json(request, _TABBED_DOC)

    out = _run(
        "batch_edit",
        {
            "document_ref": "D1",
            "all_tabs": True,
            "requests": [
                {"insertText": {"location": {"index": 1}, "text": "Hello"}}
            ],
        },
        handler,
    )

    assert out["ok"] is False
    assert out["error"]["code"] == "all_tabs_not_supported"


def test_batch_edit_rejects_request_kind_not_in_allowlist():
    out = _run("batch_edit", {"document_ref": "D1", "requests": [
        {"deleteDocumentTab": {"tabId": "t.0"}},  # not allowed (and not a real API op)
    ]}, lambda r: _json(r, {}))
    assert out["ok"] is False and out["error"]["code"] == "request_kind_not_allowed"


def test_batch_edit_pins_a_table_header_row():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen.append(json.loads(request.content))
            return _json(request, {"replies": [{}]})
        return _json(request, _TABBED_DOC)

    out = _run(
        "batch_edit",
        {
            "document_ref": "D1",
            "tab_id": "t.0",
            "requests": [
                {
                    "pinTableHeaderRows": {
                        "tableStartLocation": {"index": 5},
                        "pinnedHeaderRowsCount": 1,
                    }
                }
            ],
        },
        handler,
    )

    # Without this a caller can build a table through the door but never mark
    # the header row that names its columns on every later read.
    assert out["ok"] is True, out
    pinned = seen[0]["requests"][0]["pinTableHeaderRows"]
    assert pinned["pinnedHeaderRowsCount"] == 1
    assert pinned["tableStartLocation"]["tabId"] == "t.0"


def test_batch_edit_rejects_multi_key_request():
    out = _run("batch_edit", {"document_ref": "D1", "requests": [
        {"insertText": {"text": "x"}, "replaceAllText": {}},
    ]}, lambda r: _json(r, {}))
    assert out["ok"] is False and out["error"]["code"] == "invalid_request"


def test_batch_edit_bounds_request_count():
    many = [{"insertText": {"location": {"index": 1}, "text": "x"}}] * (flex.MAX_EDIT_REQUESTS + 1)
    out = _run("batch_edit", {"document_ref": "D1", "requests": many}, lambda r: _json(r, {}))
    assert out["ok"] is False and out["error"]["code"] == "request_too_large"


def test_batch_edit_5xx_marks_outcome_unknown_and_redacts():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return _json(
                request,
                {"documentId": "D1", "body": {"content": [{"endIndex": 2}]}},
            )
        return _json(request, {"error": {"message": "server error with Bearer tok-xyz"}}, status=503)

    out = _run("batch_edit", {"document_ref": "D1", "requests": [
        {"insertText": {"location": {"index": 1}, "text": "x"}}]}, handler)
    assert out["ok"] is False
    assert out["ret"]["outcome_unknown"] is True
    assert "tok-xyz" not in json.dumps(out)
