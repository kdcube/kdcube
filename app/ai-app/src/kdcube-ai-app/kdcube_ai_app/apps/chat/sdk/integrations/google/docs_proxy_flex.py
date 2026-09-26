# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Graph-faithful, tab-aware Google Docs operations — the FLEXIBLE alternative.

This module sits side by side with ``docs_proxy`` for evaluation. Where
``docs_proxy`` exposes many narrow typed operations (one grant + one bounded
shape each), this one works with the document's native structure:

- ``get_structure`` returns the document's structural graph (tab hierarchy,
  per-tab elements with their start/end indices, heading style, tables) so an
  agent can reason over structure and target edits precisely — including inside
  a specific tab.
- ``batch_edit`` accepts a list of native Google Docs ``batchUpdate`` requests,
  but stays governed: an **allowlist** of request kinds (unknown/dangerous
  kinds are rejected), per-request bounds, a total-count cap, and optional
  ``tab_id`` targeting stamped into each request's location/range.
- ``list_tabs`` enumerates tabs without returning body content.

The trade-off to compare: fewer operations and richer edits (compound, tab-
aware, native schema) vs. one write grant covering all of them instead of the
per-operation grants the narrow proxy gives. Governance is preserved through
the allowlist + bounds, not through operation narrowness. Nothing here is an
"arbitrary batchUpdate passthrough": every request kind is validated.

Reuses the low-level plumbing (auth headers, id parsing, error normalization)
from ``docs_proxy`` so the two proxies never drift on transport or failures.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from kdcube_ai_app.apps.chat.sdk.integrations.provider_errors import (
    provider_failure_from_exception,
)
from kdcube_ai_app.apps.chat.sdk.integrations.docs.tables import DEFAULT_TABLE_ROWS
from kdcube_ai_app.apps.chat.sdk.integrations.google.docs_structure import body_tables
from kdcube_ai_app.apps.chat.sdk.integrations.google.docs_proxy import (
    DOCS_API,
    DocsValidationError,
    _DocsApiError,
    _body_end_index,
    _clean,
    _document_id,
    _document_tabs,
    _extract_paragraph_text,
    _fetch_document,
    _headers,
    _int,
    _raise_for_status,
    _select_single_tab,
    _tab_selection_details,
    _web_url,
    _PROVIDER_ID,
    _TIMEOUT,
)

_SERVICE = "google_docs_flex"

MAX_EDIT_REQUESTS = 50
MAX_TEXT_CHARS = 200_000

# Native Docs batchUpdate request kinds this flexible surface permits. This is
# the governance boundary in place of operation-narrowness: any request whose
# single key is not here is rejected before the call. Deliberately excludes
# document-structure-scope requests that have no clear bounded meaning yet.
_ALLOWED_REQUEST_KINDS = frozenset({
    "insertText",
    "deleteContentRange",
    "replaceAllText",
    "insertPageBreak",
    "insertInlineImage",
    "updateTextStyle",
    "updateParagraphStyle",
    "createParagraphBullets",
    "deleteParagraphBullets",
    "insertTable",
    "insertTableRow",
    "insertTableColumn",
    "deleteTableRow",
    "deleteTableColumn",
    "mergeTableCells",
    "unmergeTableCells",
    "updateTableCellStyle",
    "pinTableHeaderRows",
    "createNamedRange",
    "deleteNamedRange",
})

# Request kinds whose text payload we bound.
_TEXT_KINDS = {"insertText", "replaceAllText"}

# Keys inside a request that carry a Location/Range where a tabId belongs.
_LOCATION_KEYS = (
    "location",
    "range",
    "endOfSegmentLocation",
    "tableStartLocation",
)

MUTATING_OPERATIONS = frozenset({"batch_edit"})


def _mutating(op: str) -> bool:
    return op in MUTATING_OPERATIONS


# --------------------------------------------------------------------------- #
# get_structure / list_tabs — the graph-faithful read
# --------------------------------------------------------------------------- #

def _structural_element(block: Mapping[str, Any]) -> dict[str, Any] | None:
    start = _int(block.get("startIndex"))
    end = _int(block.get("endIndex"))
    paragraph = block.get("paragraph")
    if isinstance(paragraph, Mapping):
        style = _clean((paragraph.get("paragraphStyle") or {}).get("namedStyleType")) or "NORMAL_TEXT"
        text = _extract_paragraph_text(paragraph)
        return {
            "type": "heading" if style.startswith("HEADING") else "paragraph",
            "start_index": start,
            "end_index": end,
            "style": style,
            "bulleted": "bullet" in paragraph,
            "text": text,
        }
    if isinstance(block.get("sectionBreak"), Mapping):
        return {"type": "section_break", "start_index": start, "end_index": end}
    if isinstance(block.get("tableOfContents"), Mapping):
        return {"type": "table_of_contents", "start_index": start, "end_index": end}
    return None


def _table_element(table: Mapping[str, Any]) -> dict[str, Any]:
    """A table with per-cell indices; content_start/content_end bound the
    editable text (the cell's final newline is excluded)."""
    rows = table["cells"][:DEFAULT_TABLE_ROWS]
    return {
        "type": "table",
        "start_index": table["start_index"],
        "end_index": table["end_index"],
        "position": table["position"],
        "after_heading": table["after_heading"],
        "rows": table["rows"],
        "columns": table["columns"],
        "header_rows": table["header_rows"],
        "cells": [[dict(cell) for cell in row] for row in rows],
        "cells_truncated": len(table["cells"]) > len(rows),
    }


def _elements_from_body(
    body: Any, *, inline_objects: Mapping[str, Any] | None = None
) -> list[dict[str, Any]]:
    content = body.get("content") if isinstance(body, Mapping) else None
    tables = {
        table["start_index"]: table
        for table in body_tables(body, inline_objects=inline_objects)
    }
    elements: list[dict[str, Any]] = []
    for block in content or []:
        if not isinstance(block, Mapping):
            continue
        if isinstance(block.get("table"), Mapping):
            table = tables.get(_int(block.get("startIndex")))
            if table is not None:
                elements.append(_table_element(table))
            continue
        element = _structural_element(block)
        if element is not None:
            elements.append(element)
    return elements


def _tab_records(document: Mapping[str, Any], *, with_elements: bool) -> list[dict[str, Any]]:
    """Flatten the tab tree into records (parent_tab_id preserves hierarchy).

    A document with no tabs yields one synthetic record over its top-level body,
    so callers never special-case the tab-less shape."""
    tabs = document.get("tabs")
    records: list[dict[str, Any]] = []

    def _walk(tab: Mapping[str, Any], parent_id: str) -> None:
        props = tab.get("tabProperties") if isinstance(tab.get("tabProperties"), Mapping) else {}
        tab_id = _clean(props.get("tabId"))
        record: dict[str, Any] = {
            "tab_id": tab_id,
            "title": _clean(props.get("title")),
            "index": _int(props.get("index")),
            "parent_tab_id": parent_id,
        }
        if with_elements:
            doc_tab = tab.get("documentTab") if isinstance(tab.get("documentTab"), Mapping) else {}
            record["elements"] = _elements_from_body(
                doc_tab.get("body"), inline_objects=doc_tab.get("inlineObjects")
            )
        records.append(record)
        for child in tab.get("childTabs") or []:
            if isinstance(child, Mapping):
                _walk(child, tab_id)

    if isinstance(tabs, list) and tabs:
        for tab in tabs:
            if isinstance(tab, Mapping):
                _walk(tab, "")
    else:
        record = {
            "tab_id": "",
            "title": _clean(document.get("title")),
            "index": 0,
            "parent_tab_id": "",
        }
        if with_elements:
            record["elements"] = _elements_from_body(
                document.get("body"), inline_objects=document.get("inlineObjects")
            )
        records.append(record)
    return records


async def _get_structure(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    document_id = _document_id(payload.get("document_ref"))
    response = await client.get(
        f"{DOCS_API}/documents/{document_id}",
        headers=_headers(access_token),
        params={"includeTabsContent": "true"},
    )
    _raise_for_status(response, operation="get_structure", mutating=False)
    document = response.json()
    document = dict(document) if isinstance(document, Mapping) else {}
    tabs = _tab_records(document, with_elements=True)
    named_ranges = sorted(
        {
            _clean(row.get("name"))
            for row in (document.get("namedRanges") or {}).values()
            if isinstance(row, Mapping)
        }
        if isinstance(document.get("namedRanges"), Mapping)
        else set()
    )
    return {
        "document_id": document_id,
        "title": _clean(document.get("title")),
        "revision_id": _clean(document.get("revisionId")),
        "web_url": _web_url(document_id),
        "tab_count": len(tabs),
        "tabs": tabs,
        "named_ranges": [n for n in named_ranges if n],
        "end_index": _body_end_index(document),
    }


async def _list_tabs(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    document_id = _document_id(payload.get("document_ref"))
    response = await client.get(
        f"{DOCS_API}/documents/{document_id}",
        headers=_headers(access_token),
        params={"includeTabsContent": "true"},
    )
    _raise_for_status(response, operation="list_tabs", mutating=False)
    document = response.json()
    document = dict(document) if isinstance(document, Mapping) else {}
    tabs = _tab_records(document, with_elements=False)
    return {
        "document_id": document_id,
        "web_url": _web_url(document_id),
        "tab_count": len(tabs),
        "tabs": tabs,
    }


# --------------------------------------------------------------------------- #
# batch_edit — native requests, governed by allowlist + bounds + tab targeting
# --------------------------------------------------------------------------- #

def _stamp_tab_id(request: dict[str, Any], tab_id: str) -> None:
    """Inject tabId into a request's Location/Range so the edit targets a tab."""
    if not tab_id:
        return

    def _walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in _LOCATION_KEYS and isinstance(child, dict):
                    existing = _clean(child.get("tabId"))
                    if existing and existing != tab_id:
                        raise DocsValidationError(
                            "conflicting_tab_scope",
                            f"A request targets tab_id '{existing}', but the batch "
                            f"targets '{tab_id}'.",
                        )
                    child["tabId"] = tab_id
                _walk(child)
        elif isinstance(value, list):
            for child in value:
                _walk(child)

    _walk(request)


def _validate_request(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or len(raw) != 1:
        raise DocsValidationError(
            "invalid_request",
            "Each request must be a single-key native Docs batchUpdate request.",
        )
    kind = next(iter(raw))
    if kind not in _ALLOWED_REQUEST_KINDS:
        raise DocsValidationError(
            "request_kind_not_allowed",
            f"Request kind '{kind}' is not permitted. Allowed: "
            f"{', '.join(sorted(_ALLOWED_REQUEST_KINDS))}.",
        )
    spec = raw[kind]
    if not isinstance(spec, Mapping):
        raise DocsValidationError(
            "invalid_request", f"Request '{kind}' body must be an object."
        )
    if kind in _TEXT_KINDS:
        text = str((spec.get("text") if kind == "insertText" else spec.get("replaceText")) or "")
        if len(text) > MAX_TEXT_CHARS:
            raise DocsValidationError(
                "text_too_large",
                f"A '{kind}' request carries {len(text)} characters; "
                f"the limit is {MAX_TEXT_CHARS}.",
            )
    return {kind: copy.deepcopy(dict(spec))}


def _stamp_replace_tabs(request: dict[str, Any], tab_ids: list[str]) -> None:
    replacement = request.get("replaceAllText")
    if not isinstance(replacement, dict):
        return
    existing = replacement.get("tabsCriteria")
    if isinstance(existing, Mapping):
        existing_ids = [
            _clean(value) for value in existing.get("tabIds") or [] if _clean(value)
        ]
        if existing_ids and existing_ids != tab_ids:
            raise DocsValidationError(
                "conflicting_tab_scope",
                "A replaceAllText request carries tabsCriteria that conflicts "
                "with the batch tab scope.",
            )
    if tab_ids:
        replacement["tabsCriteria"] = {"tabIds": tab_ids}


async def _batch_edit(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    document_id = _document_id(payload.get("document_ref"))
    raw_requests = payload.get("requests")
    if not isinstance(raw_requests, Sequence) or isinstance(raw_requests, (str, bytes)):
        raise DocsValidationError(
            "requests_required",
            "requests must be a list of native Docs batchUpdate requests.",
        )
    if not raw_requests or len(raw_requests) > MAX_EDIT_REQUESTS:
        raise DocsValidationError(
            "request_too_large" if raw_requests else "requests_required",
            f"Provide 1-{MAX_EDIT_REQUESTS} requests.",
        )
    requests = [_validate_request(item) for item in raw_requests]
    kinds = sorted({next(iter(req)) for req in requests})
    document = await _fetch_document(
        client, access_token=access_token, document_id=document_id
    )
    tabs = _document_tabs(document)
    tab_id = _clean(payload.get("tab_id"))
    all_tabs = payload.get("all_tabs") is True
    if tab_id and all_tabs:
        raise DocsValidationError(
            "invalid_tab_scope",
            "Provide tab_id or all_tabs=true, not both.",
            details=_tab_selection_details(tabs),
        )
    if all_tabs:
        unsupported = [kind for kind in kinds if kind != "replaceAllText"]
        if unsupported:
            raise DocsValidationError(
                "all_tabs_not_supported",
                "all_tabs=true is supported only when every request is "
                "replaceAllText. Other edits need one tab_id.",
                details={
                    **_tab_selection_details(tabs),
                    "request_kinds": unsupported,
                },
            )
        selected_tab_ids = [
            _clean(tab.get("tab_id")) for tab in tabs if _clean(tab.get("tab_id"))
        ]
        tab_scope = "all"
    else:
        selected, _ = _select_single_tab(document, {"tab_id": tab_id})
        selected_tab_ids = [selected]
        tab_scope = "selected" if tab_id else "single"

    for request in requests:
        if "replaceAllText" in request:
            _stamp_replace_tabs(
                request, [value for value in selected_tab_ids if value]
            )
        elif selected_tab_ids:
            _stamp_tab_id(request, selected_tab_ids[0])
    response = await client.post(
        f"{DOCS_API}/documents/{document_id}:batchUpdate",
        headers=_headers(access_token),
        json={"requests": requests},
    )
    _raise_for_status(response, operation="batch_edit", mutating=True)
    body = response.json()
    body = dict(body) if isinstance(body, Mapping) else {}
    replies = body.get("replies") if isinstance(body.get("replies"), list) else []
    return {
        "document_id": document_id,
        "web_url": _web_url(document_id),
        "tab_id": selected_tab_ids[0] if len(selected_tab_ids) == 1 else "",
        "tab_ids": selected_tab_ids,
        "tab_scope": tab_scope,
        "tab_count": len(tabs),
        "applied_requests": len(requests),
        "request_kinds": kinds,
        "reply_count": len(replies),
    }


_OPERATIONS = {
    "get_structure": _get_structure,
    "list_tabs": _list_tabs,
    "batch_edit": _batch_edit,
}


async def execute_google_docs_flex_operation(
    *,
    operation: str,
    access_token: str,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one flexible Google Docs operation; same envelope as the narrow proxy."""
    op = _clean(operation)
    token = _clean(access_token)
    body = dict(payload or {})
    where = f"google_docs_flex.{op or 'unknown'}"
    if not token:
        return {
            "ok": False,
            "error": {
                "code": "credential_missing_access_token",
                "message": "The connected Google credential has no access token.",
                "where": where,
                "managed": True,
            },
            "ret": {"outcome_unknown": False},
        }
    handler = _OPERATIONS.get(op)
    if handler is None:
        return {
            "ok": False,
            "error": {
                "code": "unsupported_operation",
                "message": f"Unsupported flexible Google Docs operation: {op or '<empty>'}.",
                "where": where,
                "managed": True,
            },
            "ret": {"outcome_unknown": False},
        }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            ret = await handler(client, access_token=token, payload=body)
        return {"ok": True, "error": None, "ret": ret}
    except DocsValidationError as exc:
        ret = {"outcome_unknown": False, **exc.details}
        error = {
            "code": exc.code,
            "message": str(exc),
            "where": where,
            "managed": True,
        }
        if exc.details:
            error["details"] = exc.details
        return {
            "ok": False,
            "error": error,
            "ret": ret,
        }
    except _DocsApiError as exc:
        return exc.failure.error_result(where=where)
    except httpx.HTTPError as exc:
        failure = provider_failure_from_exception(
            exc,
            provider=_PROVIDER_ID,
            service=_SERVICE,
            operation=op,
            fallback="Google Docs did not return a response.",
            mutating=_mutating(op),
        )
        return failure.error_result(where=where)


__all__ = [
    "execute_google_docs_flex_operation",
    "MUTATING_OPERATIONS",
    "MAX_EDIT_REQUESTS",
    "MAX_TEXT_CHARS",
]
