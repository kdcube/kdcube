# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Serializable Google Docs operations for a trusted parent caller.

Like ``sheets_proxy`` this module owns no Connection Hub state and no ``@venv``
boundary. A trusted app resolves an access token, invokes one bounded operation,
and receives plain serializable data back. Unlike the Sheets proxy it needs no
heavy blocking dependency (no ``gspread``): it speaks raw REST to the Google
Docs API and Drive API over async ``httpx``, exactly as ``gmail_tools`` does, so
it runs directly on the proc event loop with no subprocess.

Operations span two Google APIs:
  - Docs API  (docs.googleapis.com/v1): read, create, and typed edits.
  - Drive API (www.googleapis.com/drive/v3): search, copy, export, import,
    comments.

Provider failures are normalized through the shared ``provider_errors`` helper,
so the service layer's ``credential_failure`` handling matches Gmail and Slack.
"""

from __future__ import annotations

import base64
import copy
import re
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

import httpx

from kdcube_ai_app.apps.chat.sdk.integrations.docs.selectors import (
    DocsSelectorError,
    resolve_tab_selector,
)
from kdcube_ai_app.apps.chat.sdk.integrations.docs.tables import (
    MAX_TABLE_READ_CELLS,
    MAX_TABLE_READS,
    effective_header_rows,
    header_names,
    parse_row_range,
    resolve_column,
    resolve_row,
    resolve_table,
    spread_table_selector,
)
from kdcube_ai_app.apps.chat.sdk.integrations.google.docs_structure import (
    body_tables,
    public_cell,
    public_table,
)
from kdcube_ai_app.apps.chat.sdk.integrations.provider_errors import (
    ProviderFailure,
    provider_failure_from_exception,
    provider_failure_from_payload,
)

DOCS_API = "https://docs.googleapis.com/v1"
DRIVE_API = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"

DOCS_MIME_TYPE = "application/vnd.google-apps.document"
DOCX_MIME_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
ODT_MIME_TYPE = "application/vnd.oasis.opendocument.text"
RTF_MIME_TYPE = "application/rtf"
_PROVIDER_ID = "google"
_SERVICE = "google_docs"
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

MAX_SEARCH_RESULTS = 50
MAX_TEXT_CHARS = 200_000
MAX_EXPORT_BYTES = 10 * 1024 * 1024  # Drive files.export ceiling
MAX_IMPORT_BYTES = 10 * 1024 * 1024
# Raw uploads ride the resumable lane (one initiate + one PUT), which has no
# multipart 5MB ceiling; the bound here is ours, sized for report/archive
# deliverables rather than media libraries.
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_LIST_RESULTS = 100
MAX_TITLE_CHARS = 300
MAX_REPLACEMENTS = 50
MAX_COMMENT_CHARS = 20_000
MAX_COMMENTS = 100
TABLE_WRITE_MODES = ("replace", "append", "prepend")

_DOC_URL_RE = re.compile(
    r"https?://docs\.google\.com/document/(?:u/\d+/)?d/([A-Za-z0-9_-]+)"
)
_DRIVE_FILE_URL_RE = re.compile(
    r"https?://drive\.google\.com/(?:file/d/|open\?id=)([A-Za-z0-9_-]+)"
)
_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Drive export mime targets a caller may name by short alias.
_EXPORT_FORMATS: dict[str, tuple[str, str]] = {
    # alias: (mime_type, file extension)
    "pdf": ("application/pdf", "pdf"),
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "docx",
    ),
    "odt": ("application/vnd.oasis.opendocument.text", "odt"),
    "rtf": ("application/rtf", "rtf"),
    "txt": ("text/plain", "txt"),
    "html": ("text/html", "html"),
    "epub": ("application/epub+zip", "epub"),
    "markdown": ("text/markdown", "md"),
    "md": ("text/markdown", "md"),
}

# Source mime a caller may hand to import for conversion into a Google Doc.
_IMPORT_FORMATS: dict[str, str] = {
    "txt": "text/plain",
    "text": "text/plain",
    "html": "text/html",
    "markdown": "text/markdown",
    "md": "text/markdown",
    "docx": DOCX_MIME_TYPE,
    "odt": ODT_MIME_TYPE,
    "rtf": RTF_MIME_TYPE,
}

# Provider files that can participate in Docs discovery and become editable
# native Google Docs through Drive upload conversion. Plain-text import remains
# available through the explicit import operation; listing every text file in a
# user's Drive would make the document namespace noisy.
_IMPORTABLE_DRIVE_DOCUMENTS: dict[str, tuple[str, tuple[str, ...]]] = {
    DOCX_MIME_TYPE: ("docx", (".docx",)),
    ODT_MIME_TYPE: ("odt", (".odt",)),
    RTF_MIME_TYPE: ("rtf", (".rtf",)),
}
_DRIVE_DOCUMENT_MIME_TYPES = (DOCS_MIME_TYPE, *_IMPORTABLE_DRIVE_DOCUMENTS)
_DRIVE_FILE_FIELDS = (
    "id,name,mimeType,parents,createdTime,modifiedTime,ownedByMe,webViewLink,"
    "size,capabilities(canCopy),owners(displayName,emailAddress)"
)

_TEXT_STYLE_BOOL_FIELDS = {"bold", "italic", "underline", "strikethrough"}


class DocsValidationError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code or "invalid_request")
        self.details = dict(details or {})


class _DocsApiError(Exception):
    """Carries a normalized provider failure up to the central handler."""

    def __init__(self, failure: ProviderFailure) -> None:
        super().__init__(failure.message)
        self.failure = failure


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _document_id(document_ref: Any) -> str:
    value = _clean(document_ref)
    if not value:
        raise DocsValidationError(
            "document_ref_required",
            "document_ref must be a Google Docs/Drive URL or document id.",
        )
    match = _DOC_URL_RE.search(value) or _DRIVE_FILE_URL_RE.search(value)
    if match:
        return match.group(1)
    if _ID_RE.fullmatch(value):
        return value
    raise DocsValidationError(
        "invalid_document_ref",
        "document_ref must be a Google Docs/Drive URL or document id.",
    )


def _web_url(document_id: str) -> str:
    return f"https://docs.google.com/document/d/{document_id}/edit"


def _headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _source_format(mime_type: Any) -> str:
    record = _IMPORTABLE_DRIVE_DOCUMENTS.get(_clean(mime_type))
    return record[0] if record else ""


def _logical_title(name: Any, mime_type: Any) -> str:
    title = _clean(name)
    record = _IMPORTABLE_DRIVE_DOCUMENTS.get(_clean(mime_type))
    if record is None:
        return title
    for suffix in record[1]:
        if title.casefold().endswith(suffix.casefold()):
            return title[: -len(suffix)]
    return title


def _exact_title_candidates(query: str) -> list[str]:
    candidates = [query]
    known_suffixes = {
        suffix.casefold()
        for _format, suffixes in _IMPORTABLE_DRIVE_DOCUMENTS.values()
        for suffix in suffixes
    }
    if not any(query.casefold().endswith(suffix) for suffix in known_suffixes):
        for _format, suffixes in _IMPORTABLE_DRIVE_DOCUMENTS.values():
            candidates.extend(f"{query}{suffix}" for suffix in suffixes)
    return list(dict.fromkeys(candidates))


def _drive_document_mime_clause() -> str:
    clauses = [f"mimeType = '{mime_type}'" for mime_type in _DRIVE_DOCUMENT_MIME_TYPES]
    return f"({' or '.join(clauses)})"


def _drive_document_row(
    row: Mapping[str, Any],
    *,
    query: str = "",
) -> dict[str, Any]:
    document_id = _clean(row.get("id"))
    name = _clean(row.get("name"))
    mime_type = _clean(row.get("mimeType")) or DOCS_MIME_TYPE
    logical_title = _logical_title(name, mime_type)
    native_document = mime_type == DOCS_MIME_TYPE
    owners = [
        {
            "display_name": _clean(owner.get("displayName")),
            "email": _clean(owner.get("emailAddress")),
        }
        for owner in (row.get("owners") or [])
        if isinstance(owner, Mapping)
    ]
    size = _int(row.get("size"), default=-1)
    result: dict[str, Any] = {
        "document_id": document_id,
        "title": name,
        "logical_title": logical_title,
        "created_time": _clean(row.get("createdTime")),
        "modified_time": _clean(row.get("modifiedTime")),
        "owned_by_me": bool(row.get("ownedByMe")),
        "owners": owners,
        "web_url": _clean(row.get("webViewLink")) or _web_url(document_id),
        "mime_type": mime_type,
        "source_format": _source_format(mime_type),
        "native_document": native_document,
        "conversion_required": not native_document,
        "parent_ids": [
            _clean(parent) for parent in (row.get("parents") or []) if _clean(parent)
        ],
        "copyable": bool((row.get("capabilities") or {}).get("canCopy")),
        "exact_title_match": bool(
            query and query.casefold() in {name.casefold(), logical_title.casefold()}
        ),
    }
    if size >= 0:
        result["size_bytes"] = size
    if not native_document:
        result["next_action"] = (
            "Copy this import source to create an editable native Google Doc."
        )
    return result


async def _drive_file_metadata(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    file_id: str,
    operation: str,
) -> dict[str, Any]:
    response = await client.get(
        f"{DRIVE_API}/files/{file_id}",
        headers=_headers(access_token),
        params={"supportsAllDrives": "true", "fields": _DRIVE_FILE_FIELDS},
    )
    _raise_for_status(response, operation=operation, mutating=False)
    value = response.json()
    return dict(value) if isinstance(value, Mapping) else {}


def _raise_for_status(
    response: httpx.Response, *, operation: str, mutating: bool
) -> None:
    if response.status_code < 400:
        return
    body: Mapping[str, Any] | None
    try:
        parsed = response.json()
        body = parsed if isinstance(parsed, Mapping) else None
    except Exception:
        body = None
    failure = provider_failure_from_payload(
        body,
        provider_status=response.status_code,
        provider=_PROVIDER_ID,
        service=_SERVICE,
        operation=operation,
        fallback="Google Docs operation failed.",
        mutating=mutating,
        retry_after=_clean(
            response.headers.get("Retry-After") or response.headers.get("retry-after")
        ),
    )
    raise _DocsApiError(failure)


# --------------------------------------------------------------------------- #
# Document text extraction (Docs API body -> plain text)
# --------------------------------------------------------------------------- #


def _extract_paragraph_text(paragraph: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for element in paragraph.get("elements") or []:
        if not isinstance(element, Mapping):
            continue
        text_run = element.get("textRun")
        if isinstance(text_run, Mapping):
            parts.append(str(text_run.get("content") or ""))
    return "".join(parts)


def _iter_structural_text(content: Any) -> Iterator[str]:
    """Yield readable text from paragraphs, tables, and table-of-contents blocks."""

    for block in content or []:
        if not isinstance(block, Mapping):
            continue
        paragraph = block.get("paragraph")
        if isinstance(paragraph, Mapping):
            yield _extract_paragraph_text(paragraph)
            continue
        table = block.get("table")
        if isinstance(table, Mapping):
            yield "[table]\n"
            for row in table.get("tableRows") or []:
                if not isinstance(row, Mapping):
                    continue
                for index, cell in enumerate(row.get("tableCells") or []):
                    if index:
                        yield " | "
                    if isinstance(cell, Mapping):
                        yield from _iter_structural_text(cell.get("content"))
                yield "\n"
            continue
        table_of_contents = block.get("tableOfContents")
        if isinstance(table_of_contents, Mapping):
            yield from _iter_structural_text(table_of_contents.get("content"))


def _iter_document_text(document: Mapping[str, Any]) -> Iterator[str]:
    tabs = document.get("tabs")

    def _walk_tab(tab: Mapping[str, Any]):
        properties = (
            tab.get("tabProperties")
            if isinstance(tab.get("tabProperties"), Mapping)
            else {}
        )
        title = _clean(properties.get("title"))
        if title:
            yield f"[tab: {title}]\n"
        document_tab = (
            tab.get("documentTab")
            if isinstance(tab.get("documentTab"), Mapping)
            else {}
        )
        body = document_tab.get("body")
        if isinstance(body, Mapping):
            yield from _iter_structural_text(body.get("content"))
        for child in tab.get("childTabs") or []:
            if isinstance(child, Mapping):
                yield from _walk_tab(child)

    if isinstance(tabs, list) and tabs:
        for tab in tabs:
            if isinstance(tab, Mapping):
                yield from _walk_tab(tab)
        return
    body = document.get("body")
    if isinstance(body, Mapping):
        yield from _iter_structural_text(body.get("content"))


def _extract_document_text(document: Mapping[str, Any], *, limit: int) -> str:
    chunks: list[str] = []
    remaining = max(0, limit)
    truncated = False
    for piece in _iter_document_text(document):
        if not piece:
            continue
        if len(piece) > remaining:
            chunks.append(piece[:remaining])
            truncated = True
            break
        chunks.append(piece)
        remaining -= len(piece)
        if remaining == 0:
            truncated = True
            break
    if truncated:
        chunks.append("\n[truncated]")
    return "".join(chunks)


def _default_document_body(document: Mapping[str, Any]) -> Mapping[str, Any]:
    body = document.get("body")
    if isinstance(body, Mapping):
        return body

    def _find(tabs: Any) -> Mapping[str, Any]:
        for tab in tabs or []:
            if not isinstance(tab, Mapping):
                continue
            document_tab = tab.get("documentTab")
            if isinstance(document_tab, Mapping) and isinstance(
                document_tab.get("body"), Mapping
            ):
                return document_tab["body"]
            nested = _find(tab.get("childTabs"))
            if nested:
                return nested
        return {}

    return _find(document.get("tabs"))


def _body_end_index_from_body(body: Mapping[str, Any]) -> int:
    """The insertion index just before the body's trailing newline."""
    content = body.get("content")
    end = 1
    for block in content or []:
        if isinstance(block, Mapping):
            end = max(end, _int(block.get("endIndex"), default=end))
    return max(1, end - 1)


def _document_tab_entries(
    document: Mapping[str, Any],
) -> list[tuple[dict[str, Any], Mapping[str, Any]]]:
    entries: list[tuple[dict[str, Any], Mapping[str, Any]]] = []

    def _walk(
        tabs: Any,
        *,
        parent_tab_id: str = "",
        nesting_level: int = 0,
    ) -> None:
        for tab in tabs or []:
            if not isinstance(tab, Mapping):
                continue
            properties = (
                tab.get("tabProperties")
                if isinstance(tab.get("tabProperties"), Mapping)
                else {}
            )
            tab_id = _clean(properties.get("tabId"))
            document_tab = (
                tab.get("documentTab")
                if isinstance(tab.get("documentTab"), Mapping)
                else {}
            )
            body = (
                document_tab.get("body")
                if isinstance(document_tab.get("body"), Mapping)
                else {}
            )
            entries.append(
                (
                    {
                        "tab_id": tab_id,
                        "title": _clean(properties.get("title")),
                        "index": _int(properties.get("index")),
                        "parent_tab_id": parent_tab_id,
                        "nesting_level": nesting_level,
                        "end_index": _body_end_index_from_body(body),
                    },
                    body,
                )
            )
            _walk(
                tab.get("childTabs"),
                parent_tab_id=tab_id,
                nesting_level=nesting_level + 1,
            )

    _walk(document.get("tabs"))
    if not entries:
        body = _default_document_body(document)
        entries.append(
            (
                {
                    "tab_id": "",
                    "title": _clean(document.get("title")),
                    "index": 0,
                    "parent_tab_id": "",
                    "nesting_level": 0,
                    "end_index": _body_end_index_from_body(body),
                },
                body,
            )
        )
    return entries


def _body_end_index(document: Mapping[str, Any], *, tab_id: str = "") -> int:
    entries = _document_tab_entries(document)
    if tab_id:
        for record, body in entries:
            if record["tab_id"] == tab_id:
                return _body_end_index_from_body(body)
    return _body_end_index_from_body(entries[0][1])


def _document_tabs(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [record for record, _body in _document_tab_entries(document)]


def _tab_selection_details(tabs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "tab_count": len(tabs),
        "tabs": [dict(tab) for tab in tabs],
        "next_action": (
            "Choose a tab_id from tabs and retry. If the user's request does not "
            "identify a tab, ask which tab to edit. For replace_text, pass tab_ids "
            "or explicitly set all_tabs=true."
        ),
    }


def _select_single_tab(
    document: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    tabs = _document_tabs(document)
    requested = _clean(payload.get("tab_id"))
    if requested:
        if not any(tab["tab_id"] == requested for tab in tabs):
            raise DocsValidationError(
                "docs_tab_not_found",
                f"tab_id '{requested}' does not identify a tab in this document.",
                details=_tab_selection_details(tabs),
            )
        return requested, tabs
    if len(tabs) > 1:
        raise DocsValidationError(
            "docs_tab_selection_required",
            "This document has multiple tabs. Choose the tab before editing it.",
            details=_tab_selection_details(tabs),
        )
    return _clean(tabs[0].get("tab_id")), tabs


def _replace_tab_selection(
    document: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> tuple[list[str], str, list[dict[str, Any]]]:
    tabs = _document_tabs(document)
    raw_tab_ids = payload.get("tab_ids")
    if raw_tab_ids is None:
        requested: list[str] = []
    elif isinstance(raw_tab_ids, Sequence) and not isinstance(
        raw_tab_ids, (str, bytes)
    ):
        requested = []
        for value in raw_tab_ids:
            tab_id = _clean(value)
            if tab_id and tab_id not in requested:
                requested.append(tab_id)
    else:
        raise DocsValidationError(
            "invalid_tab_ids",
            "tab_ids must be a list of tab ids returned by the document read.",
            details=_tab_selection_details(tabs),
        )
    all_tabs = payload.get("all_tabs") is True
    if requested and all_tabs:
        raise DocsValidationError(
            "invalid_tab_scope",
            "Provide tab_ids or all_tabs=true, not both.",
            details=_tab_selection_details(tabs),
        )
    ordered_known = [_clean(tab.get("tab_id")) for tab in tabs]
    known = set(ordered_known)
    missing = [tab_id for tab_id in requested if tab_id not in known]
    if missing:
        raise DocsValidationError(
            "docs_tab_not_found",
            "One or more tab_ids do not identify tabs in this document: "
            + ", ".join(missing),
            details=_tab_selection_details(tabs),
        )
    if all_tabs:
        return [tab_id for tab_id in ordered_known if tab_id], "all", tabs
    if requested:
        return requested, "selected", tabs
    if len(tabs) > 1:
        raise DocsValidationError(
            "docs_tab_selection_required",
            "This document has multiple tabs. Choose tab_ids or explicitly set "
            "all_tabs=true before replacing text.",
            details=_tab_selection_details(tabs),
        )
    return [_clean(tabs[0].get("tab_id"))], "single", tabs


# --------------------------------------------------------------------------- #
# Tables: inventory, bounded reads, and selector resolution
# --------------------------------------------------------------------------- #


def _selector_failure(exc: DocsSelectorError) -> DocsValidationError:
    return DocsValidationError(
        exc.code, str(exc), details={**exc.details, "status": exc.status}
    )


def _tab_selector_for(tabs: Sequence[Mapping[str, Any]], tab_id: str) -> dict[str, Any]:
    """The shortest tab selector that names one tab: title when unique, else position."""

    for position, tab in enumerate(tabs, start=1):
        if _clean(tab.get("tab_id")) != tab_id:
            continue
        title = _clean(tab.get("title"))
        same = [row for row in tabs if _clean(row.get("title")).casefold() == title.casefold()]
        return {"title": title} if title and len(same) == 1 else {"position": position}
    return {}


def _document_tables(
    document: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tables: list[dict[str, Any]] = []
    tabs: list[dict[str, Any]] = []
    for record, body in _document_tab_entries(document):
        tabs.append(record)
        tables.extend(
            body_tables(body, tab_id=record["tab_id"], tab_title=record["title"])
        )
    return tables, tabs


def _tables_inventory(
    tables: Sequence[Mapping[str, Any]],
    tabs: Sequence[Mapping[str, Any]],
    *,
    include_cells: bool = False,
) -> list[dict[str, Any]]:
    return [
        public_table(
            table,
            tab_selector=(
                _tab_selector_for(tabs, _clean(table.get("tab_id")))
                if len(tabs) > 1
                else None
            ),
            include_cells=include_cells,
        )
        for table in tables
    ]


def _resolve_tab_id(
    tabs: Sequence[Mapping[str, Any]], selector: Mapping[str, Any]
) -> str:
    tab_id = _clean(selector.get("tab_id"))
    if tab_id:
        if not any(_clean(tab.get("tab_id")) == tab_id for tab in tabs):
            raise DocsValidationError(
                "docs_tab_not_found",
                f"tab_id '{tab_id}' does not identify a tab in this document.",
                details=_tab_selection_details(tabs),
            )
        return tab_id
    tab_selector = selector.get("tab_selector")
    if tab_selector not in (None, "", {}):
        try:
            return _clean(resolve_tab_selector(tabs, tab_selector).get("tab_id"))
        except DocsSelectorError as exc:
            raise _selector_failure(exc) from exc
    if len(tabs) > 1:
        raise DocsValidationError(
            "docs_tab_selection_required",
            "This document has multiple tabs. Name the tab that holds the table.",
            details=_tab_selection_details(tabs),
        )
    return _clean(tabs[0].get("tab_id")) if tabs else ""


def _resolve_document_table(
    tables: Sequence[Mapping[str, Any]],
    tabs: Sequence[Mapping[str, Any]],
    selector: Mapping[str, Any],
    *,
    tab_id: str | None = None,
) -> Mapping[str, Any]:
    if tab_id is None:
        tab_id = _resolve_tab_id(tabs, selector)
    in_tab = [table for table in tables if _clean(table.get("tab_id")) == tab_id]
    try:
        return resolve_table(in_tab, selector.get("table"))
    except DocsSelectorError as exc:
        raise _selector_failure(exc) from exc


def _table_read_selectors(
    value: Any,
    *,
    inventory: Sequence[Mapping[str, Any]] = (),
) -> tuple[list[Mapping[str, Any]], bool]:
    """Selectors to read, and whether the document holds more than the cap."""

    if isinstance(value, str) and value.strip().lower() == "all":
        selectors = [dict(row["selector"]) for row in inventory if row.get("selector")]
        return selectors[:MAX_TABLE_READS], len(selectors) > MAX_TABLE_READS
    if isinstance(value, Mapping):
        selectors = [value]
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        selectors = list(value)
    else:
        selectors = []
    if not selectors or not all(isinstance(item, Mapping) for item in selectors):
        raise DocsValidationError(
            "docs_table_selector_invalid",
            'tables must be one table selector object, a list of them, or "all".',
        )
    if len(selectors) > MAX_TABLE_READS:
        raise DocsValidationError(
            "request_too_large",
            f"Read at most {MAX_TABLE_READS} tables per call.",
        )
    return selectors, False


def _read_tables(
    tables: Sequence[Mapping[str, Any]],
    tabs: Sequence[Mapping[str, Any]],
    selectors: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    budget = MAX_TABLE_READ_CELLS
    reads: list[dict[str, Any]] = []
    for selector in selectors:
        selector = spread_table_selector(selector)
        table = _resolve_document_table(tables, tabs, selector)
        try:
            header_rows = effective_header_rows(table, selector.get("header"))
            first, last = parse_row_range(selector.get("rows"), rows=int(table["rows"]))
        except DocsSelectorError as exc:
            raise _selector_failure(exc) from exc
        summary = public_table(
            {**table, "header_rows": header_rows},
            tab_selector=(
                _tab_selector_for(tabs, _clean(table.get("tab_id")))
                if len(tabs) > 1
                else None
            ),
        )
        columns = max(1, int(table.get("columns") or 1))
        wanted = max(0, last - first + 1)
        fit = min(wanted, budget // columns)
        if wanted and not fit:
            reads.append({**summary, "skipped": "cell_limit"})
            continue
        stop = first + fit - 1
        summary["cells"] = [
            [
                public_cell(cell, row=number, column=index + 1)
                for index, cell in enumerate(row)
            ]
            for number, row in enumerate(table["cells"][first - 1 : stop], start=first)
        ]
        summary["first_row_number"] = first
        summary["rows_returned"] = fit
        summary["rows_total"] = table["rows"]
        truncated = stop < int(table["rows"])
        summary["truncated"] = truncated
        if truncated:
            summary["next_rows"] = f"{stop + 1}-{stop + (last - first + 1)}"
        budget -= fit * columns
        reads.append(summary)
    return reads


# --------------------------------------------------------------------------- #
# Read operations (docs:read)
# --------------------------------------------------------------------------- #


async def _search(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    query = _clean(payload.get("query"))
    limit = max(1, min(_int(payload.get("limit"), default=20), MAX_SEARCH_RESULTS))
    escaped = query.replace("\\", "\\\\").replace("'", "\\'")
    cursor = _clean(payload.get("cursor"))

    async def _list(
        *,
        title_clause: str = "",
        page_token: str = "",
        page_size: int = limit,
    ) -> dict[str, Any]:
        clauses = [_drive_document_mime_clause(), "trashed = false"]
        if title_clause:
            clauses.append(title_clause)
        params = {
            "q": " and ".join(clauses),
            "pageSize": page_size,
            "orderBy": "modifiedTime desc",
            "spaces": "drive",
            "corpora": "user",
            "includeItemsFromAllDrives": "true",
            "supportsAllDrives": "true",
            "fields": (f"nextPageToken,incompleteSearch,files({_DRIVE_FILE_FIELDS})"),
        }
        if page_token:
            params["pageToken"] = page_token
        response = await client.get(
            f"{DRIVE_API}/files", headers=_headers(access_token), params=params
        )
        _raise_for_status(response, operation="search", mutating=False)
        value = response.json()
        return dict(value) if isinstance(value, Mapping) else {}

    # Drive defines `name contains` as prefix matching. On the first page,
    # issue the exact-title query separately, then add prefix matches and
    # de-duplicate. Cursor pages continue only the prefix query so exact hits
    # are not repeated.
    exact_body: dict[str, Any] = {}
    if escaped and not cursor:
        exact_clauses = [
            "name = '" + candidate.replace("\\", "\\\\").replace("'", "\\'") + "'"
            for candidate in _exact_title_candidates(query)
        ]
        exact_body = await _list(title_clause=f"({' or '.join(exact_clauses)})")

    rows: list[Mapping[str, Any]] = []
    seen_ids: set[str] = set()
    for body in (exact_body,):
        for row in body.get("files") or []:
            if not isinstance(row, Mapping):
                continue
            document_id = _clean(row.get("id"))
            if not document_id or document_id in seen_ids:
                continue
            seen_ids.add(document_id)
            rows.append(row)

    # Reserve the remainder of the requested page for title-prefix matches. This
    # keeps nextPageToken aligned with rows actually returned; fetching a full
    # prefix page and slicing after prepending exact hits would skip rows.
    prefix_body: dict[str, Any] = {}
    prefix_budget = limit if cursor else max(0, limit - len(rows))
    if prefix_budget:
        prefix_body = await _list(
            title_clause=f"name contains '{escaped}'" if escaped else "",
            page_token=cursor,
            page_size=prefix_budget,
        )
        for row in prefix_body.get("files") or []:
            if not isinstance(row, Mapping):
                continue
            document_id = _clean(row.get("id"))
            if not document_id or document_id in seen_ids:
                continue
            seen_ids.add(document_id)
            rows.append(row)

    items = [_drive_document_row(row, query=query) for row in rows[:limit]]
    exact_match_count = sum(1 for item in items if item["exact_title_match"])
    return {
        "items": items,
        "count": len(items),
        "next_cursor": _clean(prefix_body.get("nextPageToken")),
        "exact_match_count": exact_match_count,
        "incomplete_search": bool(
            exact_body.get("incompleteSearch") or prefix_body.get("incompleteSearch")
        ),
        "match_mode": (
            "exact_then_title_prefix"
            if query and not cursor
            else "title_prefix_page" if query else "recent_documents"
        ),
    }


async def _get_source(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    file_id = _document_id(payload.get("document_ref"))
    row = await _drive_file_metadata(
        client,
        access_token=access_token,
        file_id=file_id,
        operation="get_source",
    )
    result = _drive_document_row(row)
    if result["native_document"]:
        result["next_action"] = (
            "Read this native document with the document get operation."
        )
    return result


async def _get(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    document_id = _document_id(payload.get("document_ref"))
    response = await client.get(
        f"{DOCS_API}/documents/{document_id}",
        headers=_headers(access_token),
        params={"includeTabsContent": "true"},
    )
    _raise_for_status(response, operation="get", mutating=False)
    document = response.json()
    document = dict(document) if isinstance(document, Mapping) else {}
    include_text = payload.get("include_text")
    text = ""
    if include_text is None or bool(include_text):
        text = _extract_document_text(document, limit=MAX_TEXT_CHARS)
    tables, tabs = _document_tables(document)
    result: dict[str, Any] = {
        "document_id": document_id,
        "title": _clean(document.get("title")),
        "revision_id": _clean(document.get("revisionId")),
        "web_url": _web_url(document_id),
        "text": text,
        "tab_count": len(tabs),
        "tabs": tabs,
        "end_index": _body_end_index(document),
        "tab_selection": {
            "required_for_mutation": len(tabs) > 1,
            "single_tab_parameter": "tab_id",
            "replace_parameters": ["tab_ids", "all_tabs"],
        },
        "tables": _tables_inventory(
            tables, tabs, include_cells=payload.get("include_table_cells") is True
        ),
    }
    if payload.get("tables") not in (None, "", []):
        selectors, truncated = _table_read_selectors(
            payload.get("tables"), inventory=result["tables"]
        )
        result["table_reads"] = _read_tables(tables, tabs, selectors)
        if truncated:
            result["tables_truncated"] = True
    return result


async def _export(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    document_id = _document_id(payload.get("document_ref"))
    fmt = _clean(payload.get("format")).lower() or "pdf"
    if fmt not in _EXPORT_FORMATS:
        raise DocsValidationError(
            "invalid_format",
            f"format must be one of: {', '.join(sorted(_EXPORT_FORMATS))}.",
        )
    mime_type, extension = _EXPORT_FORMATS[fmt]
    response = await client.get(
        f"{DRIVE_API}/files/{document_id}/export",
        headers=_headers(access_token),
        params={"mimeType": mime_type},
    )
    _raise_for_status(response, operation="export", mutating=False)
    content = response.content or b""
    if len(content) > MAX_EXPORT_BYTES:
        raise DocsValidationError(
            "export_too_large",
            f"Exported document is {len(content)} bytes; the limit is "
            f"{MAX_EXPORT_BYTES}. Export a smaller document or a lighter format.",
        )
    return {
        "document_id": document_id,
        "web_url": _web_url(document_id),
        "format": fmt,
        "mime_type": mime_type,
        "extension": extension,
        "byte_size": len(content),
        "content_base64": base64.b64encode(content).decode("ascii"),
    }


# --------------------------------------------------------------------------- #
# Write operations (docs:write) — typed batchUpdate, never raw JSON
# --------------------------------------------------------------------------- #


async def _fetch_document(
    client: httpx.AsyncClient, *, access_token: str, document_id: str
) -> dict[str, Any]:
    response = await client.get(
        f"{DOCS_API}/documents/{document_id}",
        headers=_headers(access_token),
        params={"includeTabsContent": "true"},
    )
    _raise_for_status(response, operation="get", mutating=False)
    body = response.json()
    return dict(body) if isinstance(body, Mapping) else {}


async def _batch_update(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    document_id: str,
    requests: list[dict[str, Any]],
    operation: str,
    write_control: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"requests": requests}
    if write_control:
        body["writeControl"] = dict(write_control)
    response = await client.post(
        f"{DOCS_API}/documents/{document_id}:batchUpdate",
        headers=_headers(access_token),
        json=body,
    )
    _raise_for_status(response, operation=operation, mutating=True)
    body = response.json()
    return dict(body) if isinstance(body, Mapping) else {}


def _bounded_text(value: Any, *, field: str = "text") -> str:
    text = str(value if value is not None else "")
    if not text:
        raise DocsValidationError(f"{field}_required", f"{field} must not be empty.")
    if len(text) > MAX_TEXT_CHARS:
        raise DocsValidationError(
            "text_too_large",
            f"{field} is {len(text)} characters; the limit is {MAX_TEXT_CHARS}.",
        )
    return text


async def _create(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    title = _clean(payload.get("title"))
    if not title or len(title) > MAX_TITLE_CHARS:
        raise DocsValidationError(
            "invalid_title",
            f"title is required and must be at most {MAX_TITLE_CHARS} characters.",
        )
    response = await client.post(
        f"{DOCS_API}/documents",
        headers=_headers(access_token),
        json={"title": title},
    )
    _raise_for_status(response, operation="create", mutating=True)
    document = response.json()
    document = dict(document) if isinstance(document, Mapping) else {}
    document_id = _clean(document.get("documentId"))
    result: dict[str, Any] = {
        "document_id": document_id,
        "title": _clean(document.get("title")) or title,
        "web_url": _web_url(document_id),
        "revision_id": _clean(document.get("revisionId")),
        "idempotency_key": _clean(payload.get("idempotency_key")),
    }
    initial_text = str(payload.get("initial_text") or "")
    if initial_text:
        _bounded_text(initial_text, field="initial_text")
        update = await _batch_update(
            client,
            access_token=access_token,
            document_id=document_id,
            requests=[{"insertText": {"location": {"index": 1}, "text": initial_text}}],
            operation="create",
        )
        result["revision_id"] = (
            _clean((update.get("writeControl") or {}).get("requiredRevisionId"))
            or result["revision_id"]
        )
        result["completed_stages"] = ["create", "write_initial_text"]
    return result


async def _copy(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    source_document_id = _document_id(payload.get("document_ref"))
    title = _clean(payload.get("title"))
    if not title or len(title) > MAX_TITLE_CHARS:
        raise DocsValidationError(
            "invalid_title",
            f"title is required and must be at most {MAX_TITLE_CHARS} characters.",
        )
    source = await _drive_file_metadata(
        client,
        access_token=access_token,
        file_id=source_document_id,
        operation="copy_source_metadata",
    )
    source_mime_type = _clean(source.get("mimeType"))
    source_format = _source_format(source_mime_type)
    if source_mime_type not in _DRIVE_DOCUMENT_MIME_TYPES:
        raise DocsValidationError(
            "document_source_not_supported",
            "The source file is not a native Google Doc or a supported document "
            "import source (DOCX, ODT, or RTF).",
        )

    parent_id = _clean(payload.get("parent_id"))
    conversion_applied = source_mime_type != DOCS_MIME_TYPE
    if not conversion_applied:
        body: dict[str, Any] = {"name": title}
        if parent_id:
            body["parents"] = [parent_id]
        response = await client.post(
            f"{DRIVE_API}/files/{source_document_id}/copy",
            headers=_headers(access_token),
            params={
                "supportsAllDrives": "true",
                "fields": _DRIVE_FILE_FIELDS,
            },
            json=body,
        )
    else:
        source_size = _int(source.get("size"), default=-1)
        if source_size > MAX_IMPORT_BYTES:
            raise DocsValidationError(
                "import_too_large",
                f"Import source is {source_size} bytes; the limit is {MAX_IMPORT_BYTES}.",
            )
        download = await client.get(
            f"{DRIVE_API}/files/{source_document_id}",
            headers=_headers(access_token),
            params={"alt": "media", "supportsAllDrives": "true"},
        )
        _raise_for_status(
            download,
            operation="copy_source_download",
            mutating=False,
        )
        raw = download.content
        if len(raw) > MAX_IMPORT_BYTES:
            raise DocsValidationError(
                "import_too_large",
                f"Import source is {len(raw)} bytes; the limit is {MAX_IMPORT_BYTES}.",
            )
        source_parent_ids = [
            _clean(value) for value in (source.get("parents") or []) if _clean(value)
        ]
        target_parent_id = parent_id or next(iter(source_parent_ids), "")
        metadata: dict[str, Any] = {"name": title, "mimeType": DOCS_MIME_TYPE}
        if target_parent_id:
            metadata["parents"] = [target_parent_id]
        response = await client.post(
            f"{DRIVE_UPLOAD_API}/files",
            headers=_headers(access_token),
            params={
                "uploadType": "multipart",
                "supportsAllDrives": "true",
                "fields": _DRIVE_FILE_FIELDS,
            },
            files={
                "metadata": (
                    "metadata",
                    _json_dumps(metadata),
                    "application/json",
                ),
                "file": (
                    _clean(source.get("name")) or f"source.{source_format}",
                    raw,
                    source_mime_type,
                ),
            },
        )
    _raise_for_status(response, operation="copy", mutating=True)
    row = response.json()
    row = dict(row) if isinstance(row, Mapping) else {}
    document_id = _clean(row.get("id"))
    return {
        "source_document_id": source_document_id,
        "document_id": document_id,
        "title": _clean(row.get("name")) or title,
        "web_url": _clean(row.get("webViewLink")) or _web_url(document_id),
        "mime_type": _clean(row.get("mimeType")) or DOCS_MIME_TYPE,
        "native_document": True,
        "conversion_required": False,
        "conversion_applied": conversion_applied,
        "source_name": _clean(source.get("name")),
        "source_mime_type": source_mime_type,
        "source_format": source_format,
        "parent_ids": [
            _clean(parent) for parent in (row.get("parents") or []) if _clean(parent)
        ],
        "copied": True,
        "idempotency_key": _clean(payload.get("idempotency_key")),
    }


async def _insert_text(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    document_id = _document_id(payload.get("document_ref"))
    text = _bounded_text(payload.get("text"))
    document = await _fetch_document(
        client, access_token=access_token, document_id=document_id
    )
    tab_id, tabs = _select_single_tab(document, payload)
    index = payload.get("index")
    if index is None:
        location = {"index": _body_end_index(document, tab_id=tab_id)}
    else:
        idx = _int(index, default=1)
        if idx < 1:
            raise DocsValidationError("invalid_index", "index must be >= 1.")
        location = {"index": idx}
    if tab_id:
        location["tabId"] = tab_id
    await _batch_update(
        client,
        access_token=access_token,
        document_id=document_id,
        requests=[{"insertText": {"location": location, "text": text}}],
        operation="insert_text",
    )
    return {
        "document_id": document_id,
        "web_url": _web_url(document_id),
        "inserted_chars": len(text),
        "index": location["index"],
        "tab_id": tab_id,
        "tab_count": len(tabs),
    }


async def _append_text(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    document_id = _document_id(payload.get("document_ref"))
    text = _bounded_text(payload.get("text"))
    document = await _fetch_document(
        client, access_token=access_token, document_id=document_id
    )
    tab_id, tabs = _select_single_tab(document, payload)
    index = _body_end_index(document, tab_id=tab_id)
    location: dict[str, Any] = {"index": index}
    if tab_id:
        location["tabId"] = tab_id
    await _batch_update(
        client,
        access_token=access_token,
        document_id=document_id,
        requests=[{"insertText": {"location": location, "text": text}}],
        operation="append_text",
    )
    return {
        "document_id": document_id,
        "web_url": _web_url(document_id),
        "appended_chars": len(text),
        "index": index,
        "tab_id": tab_id,
        "tab_count": len(tabs),
    }


async def _replace_text(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    document_id = _document_id(payload.get("document_ref"))
    raw = payload.get("replacements")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise DocsValidationError(
            "replacements_required",
            "replacements must be a list of {find, replace} objects.",
        )
    if not raw or len(raw) > MAX_REPLACEMENTS:
        raise DocsValidationError(
            "request_too_large" if raw else "replacements_required",
            f"Provide 1-{MAX_REPLACEMENTS} replacements.",
        )
    document = await _fetch_document(
        client, access_token=access_token, document_id=document_id
    )
    tab_ids, tab_scope, tabs = _replace_tab_selection(document, payload)
    requests: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise DocsValidationError(
                "invalid_replacement", "Each replacement needs find and replace."
            )
        find = str(item.get("find") or "")
        if not find:
            raise DocsValidationError("invalid_replacement", "find must not be empty.")
        replacement: dict[str, Any] = {
            "containsText": {
                "text": find,
                "matchCase": bool(item.get("match_case")),
            },
            "replaceText": str(item.get("replace") or ""),
        }
        selected_ids = [tab_id for tab_id in tab_ids if tab_id]
        if selected_ids:
            replacement["tabsCriteria"] = {"tabIds": selected_ids}
        requests.append({"replaceAllText": replacement})
    result = await _batch_update(
        client,
        access_token=access_token,
        document_id=document_id,
        requests=requests,
        operation="replace_text",
    )
    occurrences = 0
    for reply in result.get("replies") or []:
        if isinstance(reply, Mapping):
            occurrences += _int(
                (reply.get("replaceAllText") or {}).get("occurrencesChanged")
            )
    return {
        "document_id": document_id,
        "web_url": _web_url(document_id),
        "replacements": len(requests),
        "occurrences_changed": occurrences,
        "tab_scope": tab_scope,
        "tab_ids": tab_ids,
        "tab_count": len(tabs),
    }


async def _apply_text_style(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    document_id = _document_id(payload.get("document_ref"))
    document = await _fetch_document(
        client, access_token=access_token, document_id=document_id
    )
    tab_id, tabs = _select_single_tab(document, payload)
    start = _int(payload.get("start_index"), default=-1)
    end = _int(payload.get("end_index"), default=-1)
    if start < 1 or end <= start:
        raise DocsValidationError(
            "invalid_range",
            "start_index must be >= 1 and end_index must be greater than start_index.",
        )
    style: dict[str, Any] = {}
    fields: list[str] = []
    for name in _TEXT_STYLE_BOOL_FIELDS:
        if payload.get(name) is not None:
            style[name] = bool(payload.get(name))
            fields.append(name)
    font_size = payload.get("font_size")
    if font_size is not None:
        size = _int(font_size)
        if size < 6 or size > 96:
            raise DocsValidationError("invalid_font_size", "font_size must be 6-96.")
        style["fontSize"] = {"magnitude": size, "unit": "PT"}
        fields.append("fontSize")
    link_url = _clean(payload.get("link_url"))
    if link_url:
        style["link"] = {"url": link_url}
        fields.append("link")
    if not fields:
        raise DocsValidationError(
            "style_required",
            "Provide at least one of: bold, italic, underline, strikethrough, "
            "font_size, link_url.",
        )
    text_range: dict[str, Any] = {"startIndex": start, "endIndex": end}
    if tab_id:
        text_range["tabId"] = tab_id
    await _batch_update(
        client,
        access_token=access_token,
        document_id=document_id,
        requests=[
            {
                "updateTextStyle": {
                    "range": text_range,
                    "textStyle": style,
                    "fields": ",".join(fields),
                }
            }
        ],
        operation="apply_text_style",
    )
    return {
        "document_id": document_id,
        "web_url": _web_url(document_id),
        "range": {"start_index": start, "end_index": end},
        "applied_fields": sorted(fields),
        "tab_id": tab_id,
        "tab_count": len(tabs),
    }


def _cell_value(item: Mapping[str, Any]) -> dict[str, Any]:
    """One cell's content: text, or a person chip named by email."""

    person = item.get("person")
    if person in (None, ""):
        return {"kind": "text", "text": "" if item.get("text") is None else str(item["text"])}
    email = _clean(person.get("email")) if isinstance(person, Mapping) else _clean(person)
    if "@" not in email:
        raise DocsValidationError(
            "docs_person_email_invalid",
            'person must be the email address the chip points at, or {"email": ...}.',
        )
    if item.get("text"):
        raise DocsValidationError(
            "docs_cell_value_ambiguous",
            "A cell takes text or person, not both. Write the chip, then "
            "append the text.",
        )
    return {"kind": "person", "email": email}


def _cells_payload(value: Any) -> list[tuple[Any, dict[str, Any]]]:
    if isinstance(value, Mapping):
        items = [
            (column, _cell_value(text if isinstance(text, Mapping) else {"text": text}))
            for column, text in value.items()
        ]
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        rows = [item for item in value if isinstance(item, Mapping)]
        items = (
            [(item.get("column"), _cell_value(item)) for item in rows]
            if len(rows) == len(value)
            else []
        )
    else:
        items = []
    if not items:
        raise DocsValidationError(
            "cells_required",
            "cells must map column names or numbers to text, e.g. "
            '{"Status": "Done"}, or be a list of {column, text} - or '
            '{column, person: "someone@example.com"} for a person chip.',
        )
    return items


def _is_revision_mismatch(exc: _DocsApiError) -> bool:
    failure = exc.failure
    return failure.provider_status == 400 and "revision" in (
        f"{failure.message} {failure.provider_reason}".lower()
    )


def _set_cells_requests(
    targets: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]],
    *,
    mode: str,
    tab_id: str,
) -> list[dict[str, Any]]:
    scope = {"tabId": tab_id} if tab_id else {}
    requests: list[dict[str, Any]] = []
    # Last cell first: earlier cells' indices stay valid while later ones change.
    for cell, value in sorted(targets, key=lambda item: -int(item[0]["content_start"])):
        start = int(cell["content_start"])
        end = int(cell["content_end"])
        if mode == "replace" and end > start:
            requests.append(
                {"deleteContentRange": {"range": {"startIndex": start, "endIndex": end, **scope}}}
            )
        index = end if mode == "append" else start
        if value["kind"] == "person":
            requests.append(
                {
                    "insertPerson": {
                        "personProperties": {"email": value["email"]},
                        "location": {"index": index, **scope},
                    }
                }
            )
        elif value["text"]:
            requests.append(
                {"insertText": {"location": {"index": index, **scope}, "text": value["text"]}}
            )
    return requests


def _row_label(
    table: Mapping[str, Any], row: int, report: Sequence[Mapping[str, Any]]
) -> str:
    written = next((entry for entry in report if entry["column"] == 1), None)
    if written is None:
        return str(table["cells"][row - 1][0].get("text") or "")
    wrote = written.get("wrote")
    if isinstance(wrote, Mapping):
        return str(wrote.get("email") or "")
    return str(written.get("after") or "")


async def _set_cells(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    payload = spread_table_selector(payload)
    document_id = _document_id(payload.get("document_ref"))
    mode = _clean(payload.get("mode")).lower() or "replace"
    if mode not in TABLE_WRITE_MODES:
        raise DocsValidationError(
            "invalid_mode", f"mode must be one of: {', '.join(TABLE_WRITE_MODES)}."
        )
    cells_in = _cells_payload(payload.get("cells"))
    for _column, value in cells_in:
        # An empty replace clears the cell; every other write needs text.
        if value["kind"] == "text" and (mode != "replace" or value["text"]):
            _bounded_text(value["text"])
    expected_revision = _clean(payload.get("revision_id"))
    remove_objects = payload.get("remove_objects") is True

    attempts = 0
    while True:
        attempts += 1
        document = await _fetch_document(
            client, access_token=access_token, document_id=document_id
        )
        revision = _clean(document.get("revisionId"))
        if expected_revision and expected_revision != revision:
            raise DocsValidationError(
                "docs_revision_changed",
                "The document changed after the revision_id you read. Read it "
                "again, check the table, and retry with the new revision_id.",
                details={"expected_revision_id": expected_revision, "revision_id": revision},
            )
        tables, tabs = _document_tables(document)
        tab_id = _resolve_tab_id(tabs, payload)
        table = _resolve_document_table(tables, tabs, payload, tab_id=tab_id)
        try:
            header_rows = effective_header_rows(table, payload.get("header"))
            row = resolve_row(table, payload.get("row"), header_rows=header_rows)
            columns = [
                resolve_column(table, column, header_rows=header_rows)
                for column, _value in cells_in
            ]
        except DocsSelectorError as exc:
            raise _selector_failure(exc) from exc
        if len(set(columns)) != len(columns):
            raise DocsValidationError(
                "docs_table_column_repeated",
                "cells names the same column more than once.",
                details={"columns": columns},
            )
        names = header_names(table, header_rows)
        targets: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
        report: list[dict[str, Any]] = []
        for column, (_key, value) in zip(columns, cells_in):
            cell = table["cells"][row - 1][column - 1]
            where = {"row": row, "column": column}
            if cell.get("merged_into"):
                head_row, head_column = cell["merged_into"]
                head_text = str(table["cells"][head_row - 1][head_column - 1].get("text") or "")
                raise DocsValidationError(
                    "docs_table_cell_merged",
                    f"Row {row}, column {column} is merged into row {head_row}, "
                    f"column {head_column}, which holds {head_text!r}. Writing there "
                    "changes that cell. Ask the user which cell to write before "
                    "retrying.",
                    details={
                        **where,
                        "merged_into": cell["merged_into"],
                        "merged_cell_text": head_text,
                    },
                )
            if cell.get("nested_table"):
                raise DocsValidationError(
                    "docs_table_nested",
                    f"Row {row}, column {column} holds a nested table, which "
                    "this action does not edit.",
                    details=where,
                )
            if mode == "replace" and cell.get("objects") and not remove_objects:
                raise DocsValidationError(
                    "docs_table_cell_has_objects",
                    f"Row {row}, column {column} holds "
                    f"{', '.join(sorted({row['kind'] for row in cell['objects']}))} "
                    "besides text; replacing it "
                    "would delete them. Ask the user what to do with them "
                    "before writing this cell.",
                    details={**where, "objects": list(cell["objects"])},
                )
            before = str(cell.get("text") or "")
            text = value["text"] if value["kind"] == "text" else ""
            after = (
                text if mode == "replace"
                else before + text if mode == "append"
                else text + before
            )
            targets.append((cell, value))
            entry = {
                "column": column,
                "header": names[column - 1] if names else None,
                "before": before,
                "after": after,
            }
            if cell.get("objects"):
                entry["before_objects"] = copy.deepcopy(list(cell["objects"]))
            if value["kind"] == "person":
                entry["wrote"] = dict(value)
            report.append(entry)
        requests = _set_cells_requests(targets, mode=mode, tab_id=tab_id)
        response: dict[str, Any] = {}
        if requests:
            try:
                response = await _batch_update(
                    client,
                    access_token=access_token,
                    document_id=document_id,
                    requests=requests,
                    operation="set_cells",
                    write_control={"requiredRevisionId": revision} if revision else None,
                )
            except _DocsApiError as exc:
                if attempts == 1 and not expected_revision and _is_revision_mismatch(exc):
                    continue
                raise
        new_revision = _clean(
            (response.get("writeControl") or {}).get("requiredRevisionId")
            if isinstance(response.get("writeControl"), Mapping)
            else ""
        ) or revision
        return {
            "document_id": document_id,
            "web_url": _web_url(document_id),
            "revision_id": new_revision,
            "tab_id": tab_id,
            "tab_count": len(tabs),
            "table": {
                key: value
                for key, value in public_table({**table, "header_rows": header_rows}).items()
                if key in ("position", "after_heading", "rows", "columns", "header_rows", "header")
            },
            "row": row,
            # The row's own first cell as this write leaves it, so a
            # confirmation shows which row was written.
            "row_label": _row_label(table, row, report),
            "mode": mode,
            "cells": report,
            "attempts": attempts,
            "idempotency_key": _clean(payload.get("idempotency_key")),
        }


async def _insert_page_break(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    document_id = _document_id(payload.get("document_ref"))
    document = await _fetch_document(
        client, access_token=access_token, document_id=document_id
    )
    tab_id, tabs = _select_single_tab(document, payload)
    index = payload.get("index")
    if index is None:
        location = {"index": _body_end_index(document, tab_id=tab_id)}
    else:
        idx = _int(index, default=1)
        if idx < 1:
            raise DocsValidationError("invalid_index", "index must be >= 1.")
        location = {"index": idx}
    if tab_id:
        location["tabId"] = tab_id
    await _batch_update(
        client,
        access_token=access_token,
        document_id=document_id,
        requests=[{"insertPageBreak": {"location": location}}],
        operation="insert_page_break",
    )
    return {
        "document_id": document_id,
        "web_url": _web_url(document_id),
        "index": location["index"],
        "tab_id": tab_id,
        "tab_count": len(tabs),
    }


async def _embed_image(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    document_id = _document_id(payload.get("document_ref"))
    image_uri = _clean(payload.get("image_uri"))
    if not image_uri.startswith(("http://", "https://")):
        raise DocsValidationError(
            "invalid_image_uri",
            "image_uri must be a public http(s) URL that Google can fetch once "
            "at insert time (PNG/JPEG/GIF, <=25MB, <=2000px per side).",
        )
    document = await _fetch_document(
        client, access_token=access_token, document_id=document_id
    )
    tab_id, tabs = _select_single_tab(document, payload)
    index = payload.get("index")
    if index is None:
        location = {"index": _body_end_index(document, tab_id=tab_id)}
    else:
        idx = _int(index, default=1)
        if idx < 1:
            raise DocsValidationError("invalid_index", "index must be >= 1.")
        location = {"index": idx}
    if tab_id:
        location["tabId"] = tab_id
    insert: dict[str, Any] = {"location": location, "uri": image_uri}
    width = payload.get("width_pt")
    height = payload.get("height_pt")
    if width is not None or height is not None:
        object_size: dict[str, Any] = {}
        if width is not None:
            object_size["width"] = {"magnitude": _int(width), "unit": "PT"}
        if height is not None:
            object_size["height"] = {"magnitude": _int(height), "unit": "PT"}
        insert["objectSize"] = object_size
    result = await _batch_update(
        client,
        access_token=access_token,
        document_id=document_id,
        requests=[{"insertInlineImage": insert}],
        operation="embed_image",
    )
    object_id = ""
    for reply in result.get("replies") or []:
        if isinstance(reply, Mapping):
            object_id = (
                _clean((reply.get("insertInlineImage") or {}).get("objectId"))
                or object_id
            )
    return {
        "document_id": document_id,
        "web_url": _web_url(document_id),
        "index": location["index"],
        "object_id": object_id,
        "tab_id": tab_id,
        "tab_count": len(tabs),
    }


async def _import_document(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    title = _clean(payload.get("title"))
    if not title or len(title) > MAX_TITLE_CHARS:
        raise DocsValidationError(
            "invalid_title",
            f"title is required and must be at most {MAX_TITLE_CHARS} characters.",
        )
    fmt = _clean(payload.get("source_format")).lower() or "markdown"
    if fmt not in _IMPORT_FORMATS:
        raise DocsValidationError(
            "invalid_source_format",
            f"source_format must be one of: {', '.join(sorted(_IMPORT_FORMATS))}.",
        )
    source_mime = _IMPORT_FORMATS[fmt]
    content_b64 = _clean(payload.get("content_base64"))
    if content_b64:
        try:
            raw = base64.b64decode(content_b64, validate=True)
        except Exception as exc:  # noqa: BLE001
            raise DocsValidationError(
                "invalid_content_base64", "content_base64 is not valid base64."
            ) from exc
    else:
        text = str(payload.get("content") or "")
        if not text:
            raise DocsValidationError(
                "content_required",
                "Provide content (text) or content_base64 (bytes) to import.",
            )
        raw = text.encode("utf-8")
    if len(raw) > MAX_IMPORT_BYTES:
        raise DocsValidationError(
            "import_too_large",
            f"Import payload is {len(raw)} bytes; the limit is {MAX_IMPORT_BYTES}.",
        )
    metadata: dict[str, Any] = {"name": title, "mimeType": DOCS_MIME_TYPE}
    parent_id = _clean(payload.get("parent_id"))
    if parent_id:
        # Placement into a pre-existing folder needs a credential whose scope
        # actually reaches that folder (drive, not drive.file); the caller's
        # claim selection owns that, this layer just states the intent.
        metadata["parents"] = [parent_id]
    files = {
        "metadata": ("metadata", _json_dumps(metadata), "application/json"),
        "file": ("source", raw, source_mime),
    }
    response = await client.post(
        f"{DRIVE_UPLOAD_API}/files",
        headers=_headers(access_token),
        params={
            "uploadType": "multipart",
            "fields": "id,name,webViewLink,parents",
            "supportsAllDrives": "true",
        },
        files=files,
    )
    _raise_for_status(response, operation="import", mutating=True)
    body = response.json()
    body = dict(body) if isinstance(body, Mapping) else {}
    document_id = _clean(body.get("id"))
    return {
        "document_id": document_id,
        "title": _clean(body.get("name")) or title,
        "web_url": _clean(body.get("webViewLink")) or _web_url(document_id),
        "source_format": fmt,
        "byte_size": len(raw),
        "parent_ids": [
            _clean(parent) for parent in (body.get("parents") or []) if _clean(parent)
        ],
        "idempotency_key": _clean(payload.get("idempotency_key")),
    }


# --------------------------------------------------------------------------- #
# Drive file operations (drive:read / drive:write) — files as themselves
# --------------------------------------------------------------------------- #


async def _drive_upload_file(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    """Upload one file to Drive WITHOUT conversion: a .docx stays a .docx, a
    PDF a PDF, a zip a zip. The import operation above always converts to a
    Google Doc, which is right for documents and wrong for deliverable files;
    this is the other half. Resumable lane: initiate, then one PUT."""
    name = _clean(payload.get("name") or payload.get("title"))
    if not name or len(name) > MAX_TITLE_CHARS:
        raise DocsValidationError(
            "invalid_name",
            f"name is required and must be at most {MAX_TITLE_CHARS} characters.",
        )
    content_b64 = _clean(payload.get("content_base64"))
    if not content_b64:
        raise DocsValidationError(
            "content_required", "Provide content_base64 (the file bytes)."
        )
    try:
        raw = base64.b64decode(content_b64, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise DocsValidationError(
            "invalid_content_base64", "content_base64 is not valid base64."
        ) from exc
    if len(raw) > MAX_UPLOAD_BYTES:
        raise DocsValidationError(
            "upload_too_large",
            f"Upload payload is {len(raw)} bytes; the limit is {MAX_UPLOAD_BYTES}.",
        )
    mime_type = _clean(payload.get("mime_type")) or "application/octet-stream"
    metadata: dict[str, Any] = {"name": name, "mimeType": mime_type}
    parent_id = _clean(payload.get("parent_id"))
    if parent_id:
        metadata["parents"] = [parent_id]
    initiate = await client.post(
        f"{DRIVE_UPLOAD_API}/files",
        headers={
            **_headers(access_token),
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": mime_type,
            "X-Upload-Content-Length": str(len(raw)),
        },
        params={
            "uploadType": "resumable",
            "fields": "id,name,mimeType,size,webViewLink,parents",
            "supportsAllDrives": "true",
        },
        content=_json_dumps(metadata),
    )
    _raise_for_status(initiate, operation="drive_upload", mutating=True)
    upload_url = _clean(initiate.headers.get("Location"))
    if not upload_url:
        raise DocsValidationError(
            "upload_session_missing",
            "Drive did not return a resumable upload session URL.",
        )
    response = await client.put(
        upload_url,
        headers={"Content-Type": mime_type, "Content-Length": str(len(raw))},
        content=raw,
    )
    _raise_for_status(response, operation="drive_upload", mutating=True)
    body = response.json()
    body = dict(body) if isinstance(body, Mapping) else {}
    file_id = _clean(body.get("id"))
    return {
        "file_id": file_id,
        "name": _clean(body.get("name")) or name,
        "mime_type": _clean(body.get("mimeType")) or mime_type,
        "byte_size": _int(body.get("size"), default=len(raw)),
        "web_url": _clean(body.get("webViewLink")),
        "parent_ids": [
            _clean(parent) for parent in (body.get("parents") or []) if _clean(parent)
        ],
        "idempotency_key": _clean(payload.get("idempotency_key")),
    }


async def _drive_list_folder(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    """List one Drive folder's direct children: the verification read behind
    every upload ("did the invoice land where intended"), and the browse a
    person's folder link implies."""
    folder_id = _clean(payload.get("folder_id") or payload.get("parent_id"))
    if not folder_id:
        raise DocsValidationError("folder_required", "Provide folder_id.")
    limit = max(1, min(_int(payload.get("limit"), default=50), MAX_LIST_RESULTS))
    escaped = folder_id.replace("\\", "\\\\").replace("'", "\\'")
    params: dict[str, Any] = {
        "q": f"'{escaped}' in parents and trashed = false",
        "pageSize": limit,
        "orderBy": "folder,modifiedTime desc",
        "spaces": "drive",
        "includeItemsFromAllDrives": "true",
        "supportsAllDrives": "true",
        "fields": f"nextPageToken,files({_DRIVE_FILE_FIELDS})",
    }
    cursor = _clean(payload.get("cursor"))
    if cursor:
        params["pageToken"] = cursor
    response = await client.get(
        f"{DRIVE_API}/files", headers=_headers(access_token), params=params
    )
    _raise_for_status(response, operation="drive_list", mutating=False)
    body = response.json()
    body = dict(body) if isinstance(body, Mapping) else {}
    files = [
        {
            "file_id": _clean(row.get("id")),
            "name": _clean(row.get("name")),
            "mime_type": _clean(row.get("mimeType")),
            "byte_size": _int(row.get("size"), default=0),
            "modified_time": _clean(row.get("modifiedTime")),
            "web_url": _clean(row.get("webViewLink")),
        }
        for row in (body.get("files") or [])
        if isinstance(row, Mapping)
    ]
    return {
        "folder_id": folder_id,
        "files": files,
        "next_cursor": _clean(body.get("nextPageToken")),
    }


# --------------------------------------------------------------------------- #
# Comment operations (docs:comment) — Drive comments/replies API
# --------------------------------------------------------------------------- #

_COMMENT_FIELDS = (
    "id,content,anchor,resolved,createdTime,modifiedTime,"
    "author(displayName,emailAddress,me),quotedFileContent(value),"
    "replies(id,content,action,createdTime,author(displayName,emailAddress,me))"
)


def _comment_row(row: Mapping[str, Any]) -> dict[str, Any]:
    author = row.get("author") if isinstance(row.get("author"), Mapping) else {}
    quoted = (
        row.get("quotedFileContent")
        if isinstance(row.get("quotedFileContent"), Mapping)
        else {}
    )
    replies = [
        {
            "reply_id": _clean(reply.get("id")),
            "content": _clean(reply.get("content")),
            "action": _clean(reply.get("action")),
            "created_time": _clean(reply.get("createdTime")),
            "author": _clean((reply.get("author") or {}).get("displayName")),
            "author_is_me": bool((reply.get("author") or {}).get("me")),
        }
        for reply in (row.get("replies") or [])
        if isinstance(reply, Mapping)
    ]
    return {
        "comment_id": _clean(row.get("id")),
        "content": _clean(row.get("content")),
        "anchor": _clean(row.get("anchor")),
        "resolved": bool(row.get("resolved")),
        "quoted_text": _clean(quoted.get("value")),
        "created_time": _clean(row.get("createdTime")),
        "modified_time": _clean(row.get("modifiedTime")),
        "author": _clean(author.get("displayName")),
        "author_email": _clean(author.get("emailAddress")),
        "author_is_me": bool(author.get("me")),
        "replies": replies,
    }


async def _list_comments(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    document_id = _document_id(payload.get("document_ref"))
    limit = max(1, min(_int(payload.get("limit"), default=50), MAX_COMMENTS))
    params = {
        "pageSize": limit,
        "fields": f"nextPageToken,comments({_COMMENT_FIELDS})",
    }
    if bool(payload.get("include_resolved")):
        params["includeDeleted"] = "false"
    cursor = _clean(payload.get("cursor"))
    if cursor:
        params["pageToken"] = cursor
    response = await client.get(
        f"{DRIVE_API}/files/{document_id}/comments",
        headers=_headers(access_token),
        params=params,
    )
    _raise_for_status(response, operation="list_comments", mutating=False)
    body = response.json()
    items = [
        _comment_row(row)
        for row in (body.get("comments") or [])
        if isinstance(row, Mapping)
    ]
    if not bool(payload.get("include_resolved")):
        items = [item for item in items if not item["resolved"]]
    return {
        "document_id": document_id,
        "web_url": _web_url(document_id),
        "comments": items,
        "count": len(items),
        "next_cursor": _clean(body.get("nextPageToken")),
    }


async def _get_comment(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    document_id = _document_id(payload.get("document_ref"))
    comment_id = _clean(payload.get("comment_id"))
    if not comment_id:
        raise DocsValidationError("comment_id_required", "comment_id is required.")
    response = await client.get(
        f"{DRIVE_API}/files/{document_id}/comments/{comment_id}",
        headers=_headers(access_token),
        params={"fields": _COMMENT_FIELDS},
    )
    _raise_for_status(response, operation="get_comment", mutating=False)
    body = response.json()
    return {
        "document_id": document_id,
        "web_url": _web_url(document_id),
        "comment": _comment_row(body if isinstance(body, Mapping) else {}),
    }


def _bounded_comment(value: Any) -> str:
    content = _clean(value)
    if not content:
        raise DocsValidationError("content_required", "content must not be empty.")
    if len(content) > MAX_COMMENT_CHARS:
        raise DocsValidationError(
            "content_too_large",
            f"content is {len(content)} characters; the limit is {MAX_COMMENT_CHARS}.",
        )
    return content


async def _create_comment(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    document_id = _document_id(payload.get("document_ref"))
    content = _bounded_comment(payload.get("content"))
    body: dict[str, Any] = {"content": content}
    quoted = _clean(payload.get("quoted_text"))
    if quoted:
        body["quotedFileContent"] = {"value": quoted[:MAX_COMMENT_CHARS]}
    anchor = _clean(payload.get("anchor"))
    if anchor:
        body["anchor"] = anchor
    response = await client.post(
        f"{DRIVE_API}/files/{document_id}/comments",
        headers=_headers(access_token),
        params={"fields": _COMMENT_FIELDS},
        json=body,
    )
    _raise_for_status(response, operation="create_comment", mutating=True)
    result = response.json()
    return {
        "document_id": document_id,
        "web_url": _web_url(document_id),
        "comment": _comment_row(result if isinstance(result, Mapping) else {}),
        "idempotency_key": _clean(payload.get("idempotency_key")),
    }


async def _reply_comment(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    document_id = _document_id(payload.get("document_ref"))
    comment_id = _clean(payload.get("comment_id"))
    if not comment_id:
        raise DocsValidationError("comment_id_required", "comment_id is required.")
    content = _bounded_comment(payload.get("content"))
    response = await client.post(
        f"{DRIVE_API}/files/{document_id}/comments/{comment_id}/replies",
        headers=_headers(access_token),
        params={"fields": "id,content,action,createdTime,author(displayName)"},
        json={"content": content},
    )
    _raise_for_status(response, operation="reply_comment", mutating=True)
    reply = response.json()
    reply = dict(reply) if isinstance(reply, Mapping) else {}
    return {
        "document_id": document_id,
        "web_url": _web_url(document_id),
        "comment_id": comment_id,
        "reply_id": _clean(reply.get("id")),
        "content": _clean(reply.get("content")),
        "idempotency_key": _clean(payload.get("idempotency_key")),
    }


async def _resolve_comment(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    document_id = _document_id(payload.get("document_ref"))
    comment_id = _clean(payload.get("comment_id"))
    if not comment_id:
        raise DocsValidationError("comment_id_required", "comment_id is required.")
    # A comment is resolved by posting a reply carrying action=resolve.
    content = _clean(payload.get("content")) or "Resolved."
    response = await client.post(
        f"{DRIVE_API}/files/{document_id}/comments/{comment_id}/replies",
        headers=_headers(access_token),
        params={"fields": "id,action,content"},
        json={"content": content, "action": "resolve"},
    )
    _raise_for_status(response, operation="resolve_comment", mutating=True)
    reply = response.json()
    reply = dict(reply) if isinstance(reply, Mapping) else {}
    return {
        "document_id": document_id,
        "web_url": _web_url(document_id),
        "comment_id": comment_id,
        "reply_id": _clean(reply.get("id")),
        "action": _clean(reply.get("action")) or "resolve",
    }


async def _delete_comment(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    document_id = _document_id(payload.get("document_ref"))
    comment_id = _clean(payload.get("comment_id"))
    if not comment_id:
        raise DocsValidationError("comment_id_required", "comment_id is required.")
    response = await client.delete(
        f"{DRIVE_API}/files/{document_id}/comments/{comment_id}",
        headers=_headers(access_token),
    )
    _raise_for_status(response, operation="delete_comment", mutating=True)
    return {
        "document_id": document_id,
        "web_url": _web_url(document_id),
        "deleted_comment_id": comment_id,
    }


def _json_dumps(value: Mapping[str, Any]) -> bytes:
    import json

    return json.dumps(value).encode("utf-8")


_OPERATIONS = {
    # read (docs:read)
    "search": _search,
    "get_source": _get_source,
    "get": _get,
    "export": _export,
    "list_comments": _list_comments,
    "get_comment": _get_comment,
    # write (docs:write)
    "create": _create,
    "copy": _copy,
    "insert_text": _insert_text,
    "append_text": _append_text,
    "replace_text": _replace_text,
    "apply_text_style": _apply_text_style,
    "insert_page_break": _insert_page_break,
    "embed_image": _embed_image,
    "set_cells": _set_cells,
    "import": _import_document,
    # drive files as themselves (drive:read / drive:write)
    "drive_upload": _drive_upload_file,
    "drive_list": _drive_list_folder,
    # comment (docs:comment)
    "create_comment": _create_comment,
    "reply_comment": _reply_comment,
    "resolve_comment": _resolve_comment,
    "delete_comment": _delete_comment,
}

MUTATING_OPERATIONS = frozenset(
    {
        "create",
        "copy",
        "insert_text",
        "append_text",
        "replace_text",
        "apply_text_style",
        "insert_page_break",
        "embed_image",
        "set_cells",
        "import",
        "drive_upload",
        "create_comment",
        "reply_comment",
        "resolve_comment",
        "delete_comment",
    }
)


async def execute_google_docs_operation(
    *,
    operation: str,
    access_token: str,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one bounded Google Docs operation and return a serializable envelope.

    Returns ``{"ok": True, "error": None, "ret": {...}}`` on success or
    ``{"ok": False, "error": {...}, "ret": {...}}`` on a validation or provider
    failure. The provider failure ``ret`` carries the normalized category so the
    service layer can decide whether to refresh/reconnect.
    """
    op = _clean(operation)
    token = _clean(access_token)
    body = dict(payload or {})
    where = f"google_docs.{op or 'unknown'}"
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
                "message": f"Unsupported Google Docs operation: {op or '<empty>'}.",
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
        fallback = "Google Docs did not return a response."
        if op == "set_cells" and _clean(body.get("mode")).lower() in ("append", "prepend"):
            # A lost answer can still have reached Google; repeating an append
            # would write the text twice.
            fallback = (
                "Google Docs did not return a response, so this write may or "
                "may not have landed. Read the cell again before retrying."
            )
        failure = provider_failure_from_exception(
            exc,
            provider=_PROVIDER_ID,
            service=_SERVICE,
            operation=op,
            fallback=fallback,
            mutating=op in MUTATING_OPERATIONS,
        )
        return failure.error_result(where=where)


__all__ = [
    "execute_google_docs_operation",
    "MUTATING_OPERATIONS",
    "MAX_SEARCH_RESULTS",
    "MAX_TEXT_CHARS",
    "MAX_EXPORT_BYTES",
    "MAX_UPLOAD_BYTES",
    "MAX_LIST_RESULTS",
    "DOCS_MIME_TYPE",
    "DOCX_MIME_TYPE",
    "DocsValidationError",
]
