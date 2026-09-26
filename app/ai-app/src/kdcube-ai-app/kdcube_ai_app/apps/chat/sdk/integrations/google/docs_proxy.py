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
    OBJECT_KINDS,
    body_tables,
    body_segments,
    inline_image_record,
    magnitude_pt,
    object_record,
    public_cell,
    public_table,
    table_grid,
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
MAX_IMAGE_BYTES = 10 * 1024 * 1024
# Bounded so a refusal can list what the document holds instead of everything.
MAX_IMAGE_CANDIDATES = 20
# Docs places an image at 96 dpi: 624 px wide arrives as 468 pt.
IMAGE_POINTS_PER_PIXEL = 0.75
# A table cell's default padding on each side.
DEFAULT_CELL_PADDING_PT = 5.0
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


def _points(value: Any, *, default: float = 0.0) -> float:
    """A size in points, kept to a tenth: Google reports widths that precise."""

    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return default


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


def _refuse_unsupported_rich_link(response: httpx.Response) -> None:
    """Say what a link chip is when Google refuses the address it was given.

    Docs defines a rich link as "a link to a Google resource (such as a file in
    Drive, a YouTube video, or a Calendar event)" and rejects anything else with
    a message that does not say so. Which addresses qualify is Google's rule and
    is not published as a list, so this explains rather than second-guesses it.
    """

    try:
        message = str((response.json().get("error") or {}).get("message") or "")
    except Exception:
        return
    if "insertRichLink" not in message:
        return
    raise DocsValidationError(
        "docs_link_unsupported",
        "Google refused this link chip: a link chip points at a Google "
        "resource - a Drive file, a Docs or Sheets document, a Calendar event, "
        "a YouTube video - and other addresses are rejected. Write an ordinary "
        "address as text instead.",
        details={"provider_message": message},
    )


def _raise_for_status(
    response: httpx.Response, *, operation: str, mutating: bool
) -> None:
    if response.status_code < 400:
        return
    _refuse_unsupported_rich_link(response)
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


MAX_OBJECT_LABEL_CHARS = 40

_CELL_MARKERS = {
    "person": "[person]",
    "image": "[image]",
    "date": "[date]",
    "rich_link": "[link]",
    "equation": "[equation]",
    "footnote": "[footnote]",
    "horizontal_rule": "[rule]",
    "auto_text": "[auto text]",
}


def _object_label(entry: Mapping[str, Any]) -> str:
    """One non-text element as the readable text shows it.

    Both renderings - a table cell on its line, a paragraph in the body - come
    through here, so the same object cannot read two ways. The detail inside
    the brackets is what a reader would otherwise have to go looking for: a
    chip's address, a link's title, an image's alt text.
    """

    kind = str(entry.get("kind") or "").strip()
    marker = _CELL_MARKERS.get(kind, f"[{kind}]" if kind else "")
    if not marker:
        return ""
    detail = ""
    if kind == "person":
        detail = str(entry.get("email") or entry.get("name") or "").strip()
    elif kind == "rich_link":
        detail = str(entry.get("title") or entry.get("uri") or "").strip()
    elif kind == "date":
        detail = str(entry.get("text") or "").strip()
    elif kind == "image":
        detail = str(entry.get("alt") or "").strip()
    if not detail:
        return marker
    detail = detail.replace("|", "\\|").replace("\n", " ")
    if len(detail) > MAX_OBJECT_LABEL_CHARS:
        detail = detail[: MAX_OBJECT_LABEL_CHARS - 1].rstrip() + "…"
    return f"{marker[:-1]}: {detail}]"


def _readable_paragraph_text(
    paragraph: Mapping[str, Any],
    *,
    inline_objects: Mapping[str, Any] | None = None,
) -> str:
    """A paragraph as a reader sees it, with its non-text elements in place.

    ``_extract_paragraph_text`` stays literal because its text is paired with
    document indices; this one is for reading, so a chip or a picture appears
    where it sits rather than vanishing.
    """

    parts: list[str] = []
    for element in paragraph.get("elements") or []:
        if not isinstance(element, Mapping):
            continue
        text_run = element.get("textRun")
        if isinstance(text_run, Mapping):
            parts.append(str(text_run.get("content") or ""))
            continue
        for key, kind in OBJECT_KINDS.items():
            if key in element:
                label = _object_label(
                    object_record(kind, element[key], inline_objects=inline_objects)
                )
                if label:
                    parts.append(label)
                break
    return "".join(parts)


def _cell_text(cell: Mapping[str, Any]) -> str:
    """One cell on one line: its text, then what it holds besides text.

    A cell that renders as nothing is the reason a reader concludes a document
    holds no chips, so anything non-textual is named here.
    """

    if cell.get("merged_into"):
        return "[merged]"
    text = str(cell.get("text") or "").replace("\n", " ").strip()
    text = text.replace("|", "\\|")
    parts = [text] if text else []
    if cell.get("nested_table"):
        parts.append("[nested table]")
    seen: list[str] = []
    for entry in cell.get("objects") or ():
        marker = _object_label(entry or {})
        if marker and marker not in seen:
            seen.append(marker)
    return " ".join([*parts, *seen])


def _iter_table_text(
    table: Mapping[str, Any],
    *,
    inline_objects: Mapping[str, Any] | None = None,
) -> Iterator[str]:
    grid = table_grid(table, inline_objects=inline_objects)
    rows, columns = int(grid["rows"]), int(grid["columns"])
    header_rows = int(grid["header_rows"])
    caption = f"[table · {rows} rows × {columns} columns"
    caption += " · header row]" if header_rows else " · no header row]"
    yield caption + "\n"
    for row in grid["cells"]:
        yield " | ".join(_cell_text(cell) for cell in row) + "\n"


def _iter_structural_text(
    content: Any,
    *,
    inline_objects: Mapping[str, Any] | None = None,
) -> Iterator[str]:
    """Yield readable text from paragraphs, tables, and table-of-contents blocks."""

    for block in content or []:
        if not isinstance(block, Mapping):
            continue
        paragraph = block.get("paragraph")
        if isinstance(paragraph, Mapping):
            yield _readable_paragraph_text(paragraph, inline_objects=inline_objects)
            continue
        table = block.get("table")
        if isinstance(table, Mapping):
            yield from _iter_table_text(table, inline_objects=inline_objects)
            continue
        table_of_contents = block.get("tableOfContents")
        if isinstance(table_of_contents, Mapping):
            yield from _iter_structural_text(
                table_of_contents.get("content"), inline_objects=inline_objects
            )


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
            yield from _iter_structural_text(
                body.get("content"),
                inline_objects=_objects_map(document_tab.get("inlineObjects")),
            )
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
        yield from _iter_structural_text(
            body.get("content"),
            inline_objects=_objects_map(document.get("inlineObjects")),
        )


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


def _objects_map(value: Any) -> Mapping[str, Any]:
    """A document's or tab's ``inlineObjects`` map, keyed by object id."""

    return value if isinstance(value, Mapping) else {}


def _default_document_content(
    document: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """The body of a document served without tabs, with its inline objects."""

    body = document.get("body")
    if isinstance(body, Mapping):
        return body, _objects_map(document.get("inlineObjects"))

    def _find(tabs: Any) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
        for tab in tabs or []:
            if not isinstance(tab, Mapping):
                continue
            document_tab = tab.get("documentTab")
            if isinstance(document_tab, Mapping) and isinstance(
                document_tab.get("body"), Mapping
            ):
                return (
                    document_tab["body"],
                    _objects_map(document_tab.get("inlineObjects")),
                )
            nested = _find(tab.get("childTabs"))
            if nested[0]:
                return nested
        return {}, {}

    return _find(document.get("tabs"))


def _default_document_body(document: Mapping[str, Any]) -> Mapping[str, Any]:
    return _default_document_content(document)[0]


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
) -> list[tuple[dict[str, Any], Mapping[str, Any], Mapping[str, Any]]]:
    """Each tab as (record, body, inline objects).

    The inline-objects map rides beside the body because an image element in
    that body carries only an id into it.
    """

    entries: list[tuple[dict[str, Any], Mapping[str, Any], Mapping[str, Any]]] = []

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
                    _objects_map(document_tab.get("inlineObjects")),
                )
            )
            _walk(
                tab.get("childTabs"),
                parent_tab_id=tab_id,
                nesting_level=nesting_level + 1,
            )

    _walk(document.get("tabs"))
    if not entries:
        body, inline_objects = _default_document_content(document)
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
                inline_objects,
            )
        )
    return entries


def _body_end_index(document: Mapping[str, Any], *, tab_id: str = "") -> int:
    entries = _document_tab_entries(document)
    if tab_id:
        for record, body, _objects in entries:
            if record["tab_id"] == tab_id:
                return _body_end_index_from_body(body)
    return _body_end_index_from_body(entries[0][1])


def _tab_body(document: Mapping[str, Any], *, tab_id: str = "") -> Mapping[str, Any]:
    entries = _document_tab_entries(document)
    if tab_id:
        for record, body, _objects in entries:
            if record["tab_id"] == tab_id:
                return body
    return entries[0][1] if entries else _default_document_body(document)


def _document_tabs(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [record for record, _body, _objects in _document_tab_entries(document)]


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
    for record, body, inline_objects in _document_tab_entries(document):
        tabs.append(record)
        tables.extend(
            body_tables(
                body,
                tab_id=record["tab_id"],
                tab_title=record["title"],
                inline_objects=inline_objects,
            )
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


def _inline_images(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every inline image in the document, with where it sits and how to fetch it.

    Positioned (floating) images live in a different map and are not covered.
    """

    images: list[dict[str, Any]] = []
    for record, body, inline_objects in _document_tab_entries(document):
        if not inline_objects:
            continue
        placement: dict[str, dict[str, Any]] = {}
        for table in body_tables(
            body,
            tab_id=record["tab_id"],
            tab_title=record["title"],
            inline_objects=inline_objects,
        ):
            for row_number, row in enumerate(table["cells"], start=1):
                for column, cell in enumerate(row, start=1):
                    for entry in cell.get("objects") or ():
                        object_id = str(entry.get("object_id") or "")
                        if entry.get("kind") == "image" and object_id:
                            placement[object_id] = {
                                "table": table.get("position"),
                                "row": row_number,
                                "column": column,
                            }
        for object_id, entry in inline_objects.items():
            described = inline_image_record(str(object_id), inline_objects)
            properties = (
                entry.get("inlineObjectProperties") if isinstance(entry, Mapping) else {}
            )
            embedded = (
                properties.get("embeddedObject")
                if isinstance(properties, Mapping)
                else {}
            )
            image = (
                embedded.get("imageProperties") if isinstance(embedded, Mapping) else {}
            )
            images.append(
                {
                    **described,
                    "tab_id": record["tab_id"],
                    "tab_title": record["title"],
                    **({"cell": placement[str(object_id)]} if str(object_id) in placement else {}),
                    "content_uri": _clean(
                        (image or {}).get("contentUri") if isinstance(image, Mapping) else ""
                    ),
                }
            )
    return images


def _image_candidates(images: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """What a refusal names: the images themselves, never their content URLs."""

    return [
        {key: value for key, value in image.items() if key != "content_uri"}
        for image in images[:MAX_IMAGE_CANDIDATES]
    ]


async def _read_image(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    document_id = _document_id(payload.get("document_ref"))
    wanted = _clean(payload.get("object_id"))
    document = await _fetch_document(
        client, access_token=access_token, document_id=document_id
    )
    images = _inline_images(document)
    if not images:
        raise DocsValidationError(
            "docs_image_not_found",
            "This document holds no inline images.",
            details={"document_id": document_id},
        )
    if wanted:
        matches = [image for image in images if image.get("object_id") == wanted]
        if not matches:
            raise DocsValidationError(
                "docs_image_not_found",
                f"This document holds no inline image {wanted!r}. A table read "
                "reports each image's object_id on the cell that holds it.",
                details={"images": _image_candidates(images)},
            )
        image = matches[0]
    elif len(images) > 1:
        raise DocsValidationError(
            "docs_image_selection_required",
            "This document holds several images. Name the object_id of the one "
            "to read.",
            details={"images": _image_candidates(images)},
        )
    else:
        image = images[0]

    content_uri = _clean(image.get("content_uri"))
    if not content_uri:
        raise DocsValidationError(
            "docs_image_bytes_unavailable",
            "Google reports no fetchable content for this object, which is what "
            "a drawing or a linked object looks like here.",
            details={"object_id": image.get("object_id")},
        )
    # The URL is its own capability: it needs no credential, so none is sent.
    response = await client.get(content_uri, follow_redirects=True)
    if response.status_code != 200:
        raise DocsValidationError(
            "docs_image_fetch_failed",
            f"Fetching the image returned HTTP {response.status_code}. The "
            "document's image URLs expire; read the document again and retry.",
            details={"status": response.status_code, "object_id": image.get("object_id")},
        )
    mime_type = _clean(response.headers.get("content-type")).split(";")[0].lower()
    if not mime_type.startswith("image/"):
        raise DocsValidationError(
            "docs_image_unexpected_type",
            f"The image URL returned {mime_type or 'no content type'} instead of "
            "an image.",
            details={"object_id": image.get("object_id"), "mime_type": mime_type},
        )
    content = response.content or b""
    if len(content) > MAX_IMAGE_BYTES:
        raise DocsValidationError(
            "docs_image_too_large",
            f"The image is {len(content)} bytes; the limit is {MAX_IMAGE_BYTES}.",
            details={"object_id": image.get("object_id"), "byte_size": len(content)},
        )
    described = {key: value for key, value in image.items() if key != "content_uri"}
    return {
        **described,
        "document_id": document_id,
        "web_url": _web_url(document_id),
        "mime_type": mime_type,
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


PREVIEW_WINDOW_CHARS = 80
MAX_PREVIEW_MATCHES = 5


def _segment_at(segments: Sequence[Mapping[str, Any]], index: int) -> dict[str, Any]:
    for segment in segments:
        if int(segment["start"]) <= index <= int(segment["end"]):
            return dict(segment)
    return {}


def _where_text(segment: Mapping[str, Any]) -> str:
    kind = str(segment.get("kind") or "")
    if kind == "cell":
        return (
            f"table {segment.get('table')}, row {segment.get('row')}, "
            f"column {segment.get('column')}"
        )
    return kind or "outside any paragraph or cell"


def _range_preview(
    document: Mapping[str, Any],
    *,
    tab_id: str,
    start: int,
    end: int | None = None,
) -> dict[str, Any]:
    """What already sits where a write is aimed, without writing anything."""

    body = _tab_body(document, tab_id=tab_id)
    segments = body_segments(body)
    segment = _segment_at(segments, start)
    text = str(segment.get("text") or "")
    offset = max(0, start - int(segment.get("start") or 0)) if segment else 0
    preview: dict[str, Any] = {
        "index": start,
        "tab_id": tab_id,
        "where": _where_text(segment) if segment else "outside any paragraph or cell",
        "text_before": text[max(0, offset - PREVIEW_WINDOW_CHARS) : offset],
        "text_after": text[offset : offset + PREVIEW_WINDOW_CHARS],
    }
    if segment:
        preview["segment"] = {
            key: segment[key]
            for key in ("kind", "start", "end", "table", "row", "column")
            if key in segment
        }
    if end is not None:
        preview["end_index"] = end
        covered = [
            {
                "where": _where_text(row),
                "text": str(row.get("text") or "")[:PREVIEW_WINDOW_CHARS],
            }
            for row in segments
            if int(row["start"]) < end and int(row["end"]) > start
        ]
        preview["covers"] = covered[:MAX_PREVIEW_MATCHES]
        preview["covers_total"] = len(covered)
    return preview


MAX_PIECES = 20


def _pieces_payload(payload: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    """The pieces one insert writes, or None when it is plain text.

    A chip is its own element rather than styled text, so a sentence that holds
    one is several requests. Naming them in order here keeps the index
    arithmetic out of the caller's hands, which is the whole point of this
    namespace.
    """

    raw = payload.get("pieces")
    if raw in (None, "", [], ()):
        return None
    if isinstance(raw, Mapping) or not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise DocsValidationError(
            "docs_pieces_invalid",
            'pieces is a list, e.g. [{"text": "Owner "}, {"person": "a@b.com"}].',
            details={"received_type": type(raw).__name__},
        )
    if _clean(payload.get("text")):
        raise DocsValidationError(
            "docs_value_ambiguous",
            "Name either text or pieces - pieces is the form that can hold a chip.",
        )
    if len(raw) > MAX_PIECES:
        raise DocsValidationError(
            "request_too_large", f"Write at most {MAX_PIECES} pieces per call."
        )
    values: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise DocsValidationError(
                "docs_pieces_invalid",
                'Each piece is an object: {"text": "..."}, {"person": "a@b.com"}, '
                '{"date": "2026-10-02"} or {"link": "https://..."}.',
            )
        value = _chip_value(item)
        if value["kind"] == "text":
            _bounded_text(value["text"], field="pieces[].text")
        values.append(value)
    if not values:
        raise DocsValidationError(
            "docs_pieces_invalid", "pieces holds at least one piece."
        )
    return values


def _pieces_requests(
    values: Sequence[Mapping[str, Any]], *, location: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Requests that leave the pieces in the order they were named.

    All of them go at one index, last one first: each insertion pushes what is
    already there to the right, so writing backwards lands them forwards.
    """

    index = int(location["index"])
    scope = {key: value for key, value in location.items() if key != "index"}
    requests: list[dict[str, Any]] = []
    for value in reversed(values):
        request = _chip_request(value, index=index, scope=scope)
        if request is not None:
            requests.append(request)
    return requests


def _pieces_summary(values: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "pieces": [value["kind"] for value in values],
        "chars": sum(len(value.get("text") or "") for value in values),
    }


MAX_BLOCK_ITEMS = 50

# What a caller names, and what Google calls it.
NAMED_STYLES = {
    "title": "TITLE",
    "subtitle": "SUBTITLE",
    "normal": "NORMAL_TEXT",
    **{f"heading_{level}": f"HEADING_{level}" for level in range(1, 7)},
}
BULLET_PRESETS = {
    "bullet": "BULLET_DISC_CIRCLE_SQUARE",
    "number": "NUMBERED_DECIMAL_ALPHA_ROMAN",
}


def _utf16_len(text: str) -> int:
    """Length the way Docs counts it.

    Indices are UTF-16 code units, so a rocket is two and a letter is one.
    A style range computed in Python characters would drift on the first
    emoji and take the neighbouring paragraph with it.
    """

    return len(str(text or "").encode("utf-16-le")) // 2


def _content_pieces(value: Any, *, field: str) -> list[dict[str, Any]]:
    """One paragraph's content: a plain string, or the pieces that make it."""

    if isinstance(value, str):
        return [{"kind": "text", "text": value}]
    if isinstance(value, Mapping):
        return [_chip_value(value)]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        pieces: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, Mapping):
                pieces.append(_chip_value(item))
            elif isinstance(item, str):
                pieces.append({"kind": "text", "text": item})
            else:
                raise DocsValidationError(
                    "docs_pieces_invalid",
                    f"{field} holds text or pieces like "
                    '{"text": "..."} and {"person": "a@b.com"}.',
                )
        return pieces
    raise DocsValidationError(
        "docs_pieces_invalid",
        f'{field} is text, or a list of pieces such as [{{"text": "Owner "}}, '
        '{"person": "a@b.com"}].',
    )


def _pieces_length(values: Sequence[Mapping[str, Any]]) -> int:
    """How many index positions these pieces will occupy: a chip takes one."""

    return sum(
        _utf16_len(value.get("text")) if value["kind"] == "text" else 1
        for value in values
    )


def _block_paragraphs(payload: Mapping[str, Any]) -> list[list[dict[str, Any]]] | None:
    """Paragraphs a call writes as units, or None when it writes one run."""

    items = payload.get("items")
    if items in (None, "", [], ()):
        return None
    if isinstance(items, (str, bytes, Mapping)) or not isinstance(items, Sequence):
        raise DocsValidationError(
            "docs_items_invalid",
            'items is a list, one entry per paragraph, e.g. ["Fix login", '
            '"Ship docs"].',
            details={"received_type": type(items).__name__},
        )
    if len(items) > MAX_BLOCK_ITEMS:
        raise DocsValidationError(
            "request_too_large", f"Write at most {MAX_BLOCK_ITEMS} items per call."
        )
    if _clean(payload.get("text")) or payload.get("pieces"):
        raise DocsValidationError(
            "docs_value_ambiguous",
            "Name items, or text/pieces - items writes one paragraph per entry.",
        )
    return [
        _content_pieces(item, field="items[]") for item in items
    ] or None


def _paragraph_style(payload: Mapping[str, Any]) -> tuple[str, str]:
    """The named style and the bullet preset this call asks for."""

    style = _clean(payload.get("style")).lower()
    if style and style not in NAMED_STYLES:
        raise DocsValidationError(
            "docs_style_unsupported",
            f"style must be one of: {', '.join(sorted(NAMED_STYLES))}.",
            details={"style": style},
        )
    listing = _clean(payload.get("list")).lower()
    if listing and listing not in BULLET_PRESETS:
        raise DocsValidationError(
            "docs_list_unsupported",
            f"list must be one of: {', '.join(sorted(BULLET_PRESETS))}.",
            details={"list": listing},
        )
    return NAMED_STYLES.get(style, ""), BULLET_PRESETS.get(listing, "")


def _structured_insert(
    document: Mapping[str, Any],
    *,
    tab_id: str,
    index: int,
    paragraphs: Sequence[Sequence[Mapping[str, Any]]],
    named_style: str,
    bullet_preset: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Requests that add whole paragraphs and give them their role.

    A paragraph style covers every paragraph its range touches, so a heading
    written into the middle of a sentence would restyle that sentence too.
    The write therefore starts on a paragraph of its own: at a paragraph's
    start it begins there, and at the body's end it opens one first.
    """

    body = _tab_body(document, tab_id=tab_id)
    segments = [row for row in body_segments(body) if row.get("kind") != "cell"]
    starts = {int(row["start"]) for row in segments}
    body_end = _body_end_index(document, tab_id=tab_id)
    if index in starts:
        prefix = ""
    elif index >= body_end:
        # The end of the body sits inside the last paragraph; open a new one.
        prefix = "\n"
    else:
        where = _segment_at(body_segments(body), index)
        quoted = str(where.get("text") or "").strip()
        if len(quoted) > PREVIEW_WINDOW_CHARS:
            quoted = quoted[: PREVIEW_WINDOW_CHARS - 1].rstrip() + "…"
        inside = f"{_where_text(where)} {quoted!r}" if quoted else _where_text(where)
        raise DocsValidationError(
            "docs_block_needs_its_own_paragraph",
            "A heading or a list item is a paragraph of its own, and a "
            f"paragraph style covers everything its range touches. Index {index} "
            f"falls inside {inside}, so writing here would restyle that text "
            "too. Use the index a paragraph starts at, or omit index to write "
            "at the end.",
            details={
                "index": index,
                "where": _where_text(where),
                "inside_text": quoted,
                "paragraph_starts": sorted(starts)[:MAX_PREVIEW_MATCHES],
            },
        )

    scope = {"tabId": tab_id} if tab_id else {}
    flat: list[dict[str, Any]] = []
    if prefix:
        flat.append({"kind": "text", "text": prefix})
    for paragraph in paragraphs:
        flat.extend(paragraph)
        flat.append({"kind": "text", "text": "\n"})

    requests = _pieces_requests(flat, location={"index": index, **scope})
    start = index + _utf16_len(prefix)
    end = index + _pieces_length(flat)
    span = {"startIndex": start, "endIndex": end, **scope}
    if named_style:
        requests.append(
            {
                "updateParagraphStyle": {
                    "range": span,
                    "paragraphStyle": {"namedStyleType": named_style},
                    "fields": "namedStyleType",
                }
            }
        )
    if bullet_preset:
        requests.append(
            {"createParagraphBullets": {"range": span, "bulletPreset": bullet_preset}}
        )
    summary = {
        "paragraphs": len(paragraphs),
        "start_index": start,
        "end_index": end,
        **({"style": named_style} if named_style else {}),
        **({"list": bullet_preset} if bullet_preset else {}),
    }
    return requests, summary


async def _insert_text(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    document_id = _document_id(payload.get("document_ref"))
    blocks = _block_paragraphs(payload)
    named_style, bullet_preset = _paragraph_style(payload)
    pieces = _pieces_payload(payload)
    text = (
        ""
        if pieces is not None or blocks is not None
        else _bounded_text(payload.get("text"))
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
    if payload.get("preview") is True:
        return {
            "document_id": document_id,
            "web_url": _web_url(document_id),
            "preview": _range_preview(document, tab_id=tab_id, start=location["index"]),
            **({"would_insert_chars": len(text)} if not pieces else {}),
            **({"would_insert": _pieces_summary(pieces)} if pieces else {}),
            "tab_id": tab_id,
            "tab_count": len(tabs),
            "written": False,
        }
    block_summary: dict[str, Any] = {}
    if blocks is not None or named_style or bullet_preset:
        paragraphs = blocks if blocks is not None else [pieces or [{"kind": "text", "text": text}]]
        requests, block_summary = _structured_insert(
            document,
            tab_id=tab_id,
            index=int(location["index"]),
            paragraphs=paragraphs,
            named_style=named_style,
            bullet_preset=bullet_preset,
        )
    else:
        requests = (
            _pieces_requests(pieces, location=location)
            if pieces is not None
            else [{"insertText": {"location": location, "text": text}}]
        )
    await _batch_update(
        client,
        access_token=access_token,
        document_id=document_id,
        requests=requests,
        operation="insert_text",
    )
    return {
        "document_id": document_id,
        "web_url": _web_url(document_id),
        **({"inserted_chars": len(text)} if not (pieces or block_summary) else {}),
        **({"inserted": _pieces_summary(pieces)} if pieces else {}),
        **({"wrote": block_summary} if block_summary else {}),
        "index": location["index"],
        "tab_id": tab_id,
        "tab_count": len(tabs),
    }


async def _append_text(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    document_id = _document_id(payload.get("document_ref"))
    blocks = _block_paragraphs(payload)
    named_style, bullet_preset = _paragraph_style(payload)
    pieces = _pieces_payload(payload)
    text = (
        ""
        if pieces is not None or blocks is not None
        else _bounded_text(payload.get("text"))
    )
    document = await _fetch_document(
        client, access_token=access_token, document_id=document_id
    )
    tab_id, tabs = _select_single_tab(document, payload)
    index = _body_end_index(document, tab_id=tab_id)
    location: dict[str, Any] = {"index": index}
    if tab_id:
        location["tabId"] = tab_id
    block_summary: dict[str, Any] = {}
    if blocks is not None or named_style or bullet_preset:
        paragraphs = blocks if blocks is not None else [pieces or [{"kind": "text", "text": text}]]
        requests, block_summary = _structured_insert(
            document,
            tab_id=tab_id,
            index=index,
            paragraphs=paragraphs,
            named_style=named_style,
            bullet_preset=bullet_preset,
        )
    else:
        requests = (
            _pieces_requests(pieces, location=location)
            if pieces is not None
            else [{"insertText": {"location": location, "text": text}}]
        )
    await _batch_update(
        client,
        access_token=access_token,
        document_id=document_id,
        requests=requests,
        operation="append_text",
    )
    return {
        "document_id": document_id,
        "web_url": _web_url(document_id),
        **({"appended_chars": len(text)} if not (pieces or block_summary) else {}),
        **({"appended": _pieces_summary(pieces)} if pieces else {}),
        **({"wrote": block_summary} if block_summary else {}),
        "index": index,
        "tab_id": tab_id,
        "tab_count": len(tabs),
    }


def _match_preview(
    document: Mapping[str, Any],
    *,
    tab_ids: Sequence[str],
    replacements: Sequence[Any],
) -> list[dict[str, Any]]:
    """Every place a replacement would land, counted before anything changes.

    replaceAllText reports what it changed only after it has changed it, which
    is exactly the wrong order for a caller who guessed a phrase.
    """

    entries = _document_tab_entries(document)
    wanted = {tab_id for tab_id in tab_ids if tab_id}
    out: list[dict[str, Any]] = []
    for item in replacements:
        if not isinstance(item, Mapping):
            continue
        find = str(item.get("find") or "")
        if not find:
            continue
        match_case = bool(item.get("match_case"))
        needle = find if match_case else find.lower()
        matches: list[dict[str, Any]] = []
        total = 0
        for record, body, _objects in entries:
            tab_id = str(record.get("tab_id") or "")
            if wanted and tab_id not in wanted:
                continue
            for segment in body_segments(body):
                text = str(segment.get("text") or "")
                hay = text if match_case else text.lower()
                start = hay.find(needle)
                while start >= 0:
                    total += 1
                    if len(matches) < MAX_PREVIEW_MATCHES:
                        matches.append(
                            {
                                "tab_id": tab_id,
                                "tab_title": str(record.get("title") or ""),
                                "where": _where_text(segment),
                                "context": text[
                                    max(0, start - PREVIEW_WINDOW_CHARS) : start
                                    + len(find)
                                    + PREVIEW_WINDOW_CHARS
                                ],
                            }
                        )
                    start = hay.find(needle, start + 1)
        out.append(
            {
                "find": find,
                "replace": str(item.get("replace") or ""),
                "match_case": match_case,
                "occurrences": total,
                "matches": matches,
                "matches_truncated": total > len(matches),
            }
        )
    return out


async def _replace_text(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    document_id = _document_id(payload.get("document_ref"))
    raw = payload.get("replacements")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise DocsValidationError(
            "replacements_required",
            'replacements is a list of objects: [{"find": "text to look for", '
            '"replace": "text to write"}].',
            details={"received_type": type(raw).__name__},
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
                "invalid_replacement",
                'Each replacement is an object {"find": "...", "replace": "..."}, '
                f"not {type(item).__name__}.",
            )
        find = str(item.get("find") or "")
        if not find:
            # The caller sent an object of some other shape; name the one that works.
            raise DocsValidationError(
                "invalid_replacement",
                'Each replacement needs a non-empty find: {"find": "text to look '
                'for", "replace": "text to write"}, with optional match_case.',
                details={"received_keys": sorted(str(key) for key in item)},
            )
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
    if payload.get("preview") is True:
        return {
            "document_id": document_id,
            "web_url": _web_url(document_id),
            "preview": _match_preview(document, tab_ids=tab_ids, replacements=raw),
            "replacements": len(requests),
            "tab_scope": tab_scope,
            "tab_ids": tab_ids,
            "tab_count": len(tabs),
            "written": False,
        }
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
    if payload.get("preview") is True:
        return {
            "document_id": document_id,
            "web_url": _web_url(document_id),
            "preview": _range_preview(
                document, tab_id=tab_id, start=start, end=end
            ),
            "would_style": fields,
            "tab_id": tab_id,
            "tab_count": len(tabs),
            "written": False,
        }
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


CHIP_KINDS = ("person", "date", "link")


def _chip_timestamp(value: Any) -> str:
    """A date a caller wrote as the instant Google stores.

    Docs keeps the point in time and renders it by the document's locale, so a
    chip can read differently from what was passed - that is the chip doing its
    job, not a mismatch.
    """

    from datetime import datetime, timezone

    raw = _clean(value.get("value") if isinstance(value, Mapping) else value)
    if not raw:
        raise DocsValidationError(
            "docs_date_invalid",
            'date must be a calendar date or timestamp, e.g. "2026-10-02".',
        )
    text = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise DocsValidationError(
            "docs_date_invalid",
            f'date must be ISO 8601, e.g. "2026-10-02" or '
            f'"2026-10-02T09:30:00Z"; {raw!r} is not.',
            details={"error": str(exc)},
        ) from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _chip_value(item: Mapping[str, Any]) -> dict[str, Any]:
    """One piece of content: plain text, or one of the chips Docs can insert."""

    named = [kind for kind in CHIP_KINDS if item.get(kind) not in (None, "")]
    if len(named) > 1:
        raise DocsValidationError(
            "docs_value_ambiguous",
            f"Name one of {', '.join(CHIP_KINDS)} - this names {', '.join(named)}.",
        )
    if not named:
        return {"kind": "text", "text": "" if item.get("text") is None else str(item["text"])}
    kind = named[0]
    if item.get("text"):
        raise DocsValidationError(
            "docs_value_ambiguous",
            f"A piece is text or a {kind} chip, not both. Write the chip, then "
            "the text beside it.",
        )
    value = item[kind]
    if kind == "person":
        email = _clean(value.get("email")) if isinstance(value, Mapping) else _clean(value)
        if "@" not in email:
            raise DocsValidationError(
                "docs_person_email_invalid",
                'person must be the email address the chip points at, or {"email": ...}.',
            )
        return {"kind": "person", "email": email}
    if kind == "date":
        chip: dict[str, Any] = {"kind": "date", "timestamp": _chip_timestamp(value)}
        zone = _clean(value.get("time_zone")) if isinstance(value, Mapping) else ""
        if zone:
            chip["time_zone"] = zone
        return chip
    uri = _clean(value.get("uri")) if isinstance(value, Mapping) else _clean(value)
    if not uri.startswith(("http://", "https://")):
        raise DocsValidationError(
            "docs_link_invalid",
            'link must be the http(s) address the chip points at, or {"uri": ...}. '
            "Google fills in the title and icon itself from the linked resource.",
        )
    return {"kind": "link", "uri": uri}


def _chip_request(
    value: Mapping[str, Any], *, index: int, scope: Mapping[str, Any]
) -> dict[str, Any] | None:
    """The one request that puts this piece at this index.

    Only the fields Google accepts on write are sent: it fills a person's name,
    a link's title and a date's display text itself, and refuses a caller that
    supplies them.
    """

    location = {"index": index, **dict(scope)}
    kind = value["kind"]
    if kind == "person":
        return {
            "insertPerson": {
                "personProperties": {"email": value["email"]},
                "location": location,
            }
        }
    if kind == "date":
        properties: dict[str, Any] = {"timestamp": value["timestamp"]}
        if value.get("time_zone"):
            properties["timeZoneId"] = value["time_zone"]
        return {"insertDate": {"dateElementProperties": properties, "location": location}}
    if kind == "link":
        return {
            "insertRichLink": {
                "richLinkProperties": {"uri": value["uri"]},
                "location": location,
            }
        }
    if value.get("text"):
        return {"insertText": {"location": location, "text": value["text"]}}
    return None


def _cell_value(item: Mapping[str, Any]) -> dict[str, Any]:
    """One cell's content: text, or a chip."""

    return _chip_value(item)


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
        request = _chip_request(value, index=index, scope=scope)
        if request is not None:
            requests.append(request)
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


async def _set_trashed(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    payload: Mapping[str, Any],
    trashed: bool,
) -> dict[str, Any]:
    document_id = _document_id(payload.get("document_ref"))
    response = await client.patch(
        f"{DRIVE_API}/files/{document_id}",
        headers=_headers(access_token),
        params={"fields": "id,name,trashed", "supportsAllDrives": "true"},
        json={"trashed": trashed},
    )
    _raise_for_status(
        response, operation="trash" if trashed else "restore", mutating=True
    )
    body = response.json()
    body = dict(body) if isinstance(body, Mapping) else {}
    return {
        "document_id": document_id,
        "title": _clean(body.get("name")),
        "web_url": _web_url(document_id),
        "trashed": bool(body.get("trashed")),
    }


async def _trash(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    return await _set_trashed(
        client, access_token=access_token, payload=payload, trashed=True
    )


async def _restore(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    return await _set_trashed(
        client, access_token=access_token, payload=payload, trashed=False
    )


async def _add_row(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    """Add one row to a named table, optionally filling it in the same call."""

    payload = spread_table_selector(payload)
    document_id = _document_id(payload.get("document_ref"))
    cells_in = _cells_payload(payload["cells"]) if payload.get("cells") else []
    for _column, value in cells_in:
        if value["kind"] == "text" and value["text"]:
            _bounded_text(value["text"])
    expected_revision = _clean(payload.get("revision_id"))

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
    except DocsSelectorError as exc:
        raise _selector_failure(exc) from exc
    after = payload.get("after_row")
    if after in (None, ""):
        after_row = int(table["rows"])
    else:
        try:
            after_row = resolve_row(table, after, header_rows=header_rows)
        except DocsSelectorError as exc:
            raise _selector_failure(exc) from exc
    scope = {"tabId": tab_id} if tab_id else {}
    await _batch_update(
        client,
        access_token=access_token,
        document_id=document_id,
        requests=[
            {
                "insertTableRow": {
                    "tableCellLocation": {
                        "tableStartLocation": {
                            "index": int(table["start_index"]),
                            **scope,
                        },
                        "rowIndex": after_row - 1,
                        "columnIndex": 0,
                    },
                    "insertBelow": True,
                }
            }
        ],
        operation="add_row",
        write_control={"requiredRevisionId": revision} if revision else None,
    )
    row = after_row + 1
    result: dict[str, Any] = {
        "document_id": document_id,
        "web_url": _web_url(document_id),
        "tab_id": tab_id,
        "tab_count": len(tabs),
        "row": row,
        "rows": int(table["rows"]) + 1,
        "columns": int(table["columns"]),
    }
    if not cells_in:
        result["cells"] = []
        return result
    # The new row's indices exist only after the insert, so the fill is a
    # second write on a fresh read.
    written = await _set_cells(
        client,
        access_token=access_token,
        payload={
            "document_ref": document_id,
            "tab_id": tab_id,
            "table": payload.get("table"),
            "tab_selector": payload.get("tab_selector"),
            "row": row,
            "cells": payload.get("cells"),
            "header": payload.get("header"),
        },
    )
    result["revision_id"] = written.get("revision_id")
    result["table"] = written.get("table")
    result["row_label"] = written.get("row_label")
    result["cells"] = written.get("cells") or []
    return result


def _tab_after(document: Mapping[str, Any], known: set[str]) -> dict[str, Any]:
    """The tab a lifecycle call just produced, found by what was not there."""

    for tab in _document_tabs(document):
        if _clean(tab.get("tab_id")) not in known:
            return tab
    return {}


async def _add_tab(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    document_id = _document_id(payload.get("document_ref"))
    title = _clean(payload.get("title"))
    if title and len(title) > MAX_TITLE_CHARS:
        raise DocsValidationError(
            "invalid_title", f"title must be at most {MAX_TITLE_CHARS} characters."
        )
    properties: dict[str, Any] = {}
    if title:
        properties["title"] = title
    index = payload.get("index")
    if index is not None:
        position = _int(index, default=-1)
        if position < 0:
            raise DocsValidationError(
                "invalid_tab_index", "index must be a zero-based position."
            )
        properties["index"] = position
    parent_tab_id = _clean(payload.get("parent_tab_id"))
    if parent_tab_id:
        properties["parentTabId"] = parent_tab_id
    before = await _fetch_document(
        client, access_token=access_token, document_id=document_id
    )
    known = {_clean(tab.get("tab_id")) for tab in _document_tabs(before)}
    await _batch_update(
        client,
        access_token=access_token,
        document_id=document_id,
        requests=[{"addDocumentTab": {"tabProperties": properties}}],
        operation="add_tab",
    )
    # addDocumentTab answers with no id of its own, so the new tab is the one
    # the document did not have a moment ago.
    after = await _fetch_document(
        client, access_token=access_token, document_id=document_id
    )
    tabs = _document_tabs(after)
    created = _tab_after(after, known)
    return {
        "document_id": document_id,
        "web_url": _web_url(document_id),
        "tab": created,
        "tab_id": _clean(created.get("tab_id")),
        "tabs": tabs,
        "tab_count": len(tabs),
        "idempotency_key": _clean(payload.get("idempotency_key")),
    }


async def _update_tab(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    document_id = _document_id(payload.get("document_ref"))
    document = await _fetch_document(
        client, access_token=access_token, document_id=document_id
    )
    tabs = _document_tabs(document)
    tab_id = _resolve_tab_id(tabs, payload)
    if not tab_id:
        raise DocsValidationError(
            "docs_tab_selection_required",
            "Name the tab with tab_id or tab_selector.",
            details=_tab_selection_details(tabs),
        )
    properties: dict[str, Any] = {"tabId": tab_id}
    fields: list[str] = []
    title = payload.get("title")
    if title is not None:
        renamed = _clean(title)
        if not renamed or len(renamed) > MAX_TITLE_CHARS:
            raise DocsValidationError(
                "invalid_title",
                f"title must be one to {MAX_TITLE_CHARS} characters.",
            )
        properties["title"] = renamed
        fields.append("title")
    index = payload.get("index")
    if index is not None:
        position = _int(index, default=-1)
        if position < 0:
            raise DocsValidationError(
                "invalid_tab_index", "index must be a zero-based position."
            )
        properties["index"] = position
        fields.append("index")
    if not fields:
        raise DocsValidationError(
            "tab_update_empty", "Pass title or index to change."
        )
    await _batch_update(
        client,
        access_token=access_token,
        document_id=document_id,
        requests=[
            {
                "updateDocumentTabProperties": {
                    "tabProperties": properties,
                    "fields": ",".join(fields),
                }
            }
        ],
        operation="update_tab",
    )
    after = await _fetch_document(
        client, access_token=access_token, document_id=document_id
    )
    updated = next(
        (tab for tab in _document_tabs(after) if _clean(tab.get("tab_id")) == tab_id),
        {},
    )
    return {
        "document_id": document_id,
        "web_url": _web_url(document_id),
        "tab": updated,
        "tab_id": tab_id,
        "changed": fields,
        "idempotency_key": _clean(payload.get("idempotency_key")),
    }


async def _delete_tab(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    document_id = _document_id(payload.get("document_ref"))
    document = await _fetch_document(
        client, access_token=access_token, document_id=document_id
    )
    tabs = _document_tabs(document)
    tab_id = _resolve_tab_id(tabs, payload)
    if not tab_id:
        raise DocsValidationError(
            "docs_tab_selection_required",
            "Name the tab with tab_id or tab_selector.",
            details=_tab_selection_details(tabs),
        )
    if len(tabs) <= 1:
        raise DocsValidationError(
            "docs_last_tab",
            "This is the document's only tab; a document keeps at least one.",
            details={"tab_id": tab_id},
        )
    children = [
        _clean(tab.get("tab_id"))
        for tab in tabs
        if _clean(tab.get("parent_tab_id")) == tab_id
    ]
    await _batch_update(
        client,
        access_token=access_token,
        document_id=document_id,
        requests=[{"deleteTab": {"tabId": tab_id}}],
        operation="delete_tab",
    )
    after = await _fetch_document(
        client, access_token=access_token, document_id=document_id
    )
    remaining = _document_tabs(after)
    result = {
        "document_id": document_id,
        "web_url": _web_url(document_id),
        "tab_id": tab_id,
        "tabs": remaining,
        "tab_count": len(remaining),
        "idempotency_key": _clean(payload.get("idempotency_key")),
    }
    if children:
        # Google deletes a tab's children with it; the caller hears which.
        result["deleted_child_tab_ids"] = children
    return result


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


def _tab_document_style(
    document: Mapping[str, Any], *, tab_id: str = ""
) -> Mapping[str, Any]:
    """The page setup that governs one tab, or the document's own."""

    def _walk(tabs: Any) -> Mapping[str, Any] | None:
        for tab in tabs or []:
            if not isinstance(tab, Mapping):
                continue
            properties = tab.get("tabProperties")
            current = _clean((properties or {}).get("tabId")) if isinstance(properties, Mapping) else ""
            document_tab = tab.get("documentTab")
            if current == tab_id and isinstance(document_tab, Mapping):
                style = document_tab.get("documentStyle")
                return style if isinstance(style, Mapping) else {}
            nested = _walk(tab.get("childTabs"))
            if nested is not None:
                return nested
        return None

    if tab_id:
        found = _walk(document.get("tabs"))
        if found is not None:
            return found
    style = document.get("documentStyle")
    return style if isinstance(style, Mapping) else {}


def _content_width_pt(document: Mapping[str, Any], *, tab_id: str = "") -> float | None:
    """Printable width of one tab's page, in points."""

    style = _tab_document_style(document, tab_id=tab_id)
    page = style.get("pageSize") if isinstance(style.get("pageSize"), Mapping) else {}
    width = magnitude_pt(page.get("width"))
    if width is None:
        return None
    for margin in ("marginLeft", "marginRight"):
        value = magnitude_pt(style.get(margin))
        if value is not None:
            width -= value
    return width if width > 0 else None


def _cell_content_width_pt(
    table: Mapping[str, Any],
    column: int,
    *,
    document: Mapping[str, Any],
    tab_id: str = "",
) -> float | None:
    """How wide a picture may be in one cell: the column, less its padding.

    A table that distributes its columns evenly reports no width, so the page's
    printable width divided by the column count is the best answer available.
    """

    widths = table.get("column_widths") or []
    width = widths[column - 1] if 0 < column <= len(widths) else None
    if width is None:
        columns = _int(table.get("columns"), default=0)
        page = _content_width_pt(document, tab_id=tab_id)
        if page is None or columns <= 0:
            return None
        width = page / columns
    width -= 2 * DEFAULT_CELL_PADDING_PT
    return width if width > 0 else None


async def _natural_size_pt(
    client: httpx.AsyncClient, image_uri: str
) -> tuple[float, float] | None:
    """The image's own size in points, or None when it cannot be measured.

    The URL comes from the caller, so this fetch passes the platform's SSRF
    guard first: the proxy must not be turned into a probe of the network it
    runs in. A measurement that fails is not an error - the image is inserted
    at its natural size, exactly as before.
    """

    try:
        from kdcube_ai_app.apps.chat.sdk.tools.backends.web import ssrf_guard
        from kdcube_ai_app.infra.service_hub.multimodality import validate_image_bytes
    except Exception:  # pragma: no cover - optional dependency
        return None
    _MEASURABLE_GUARD_MISSES = {
        ssrf_guard.ReasonCode.RESOLUTION_FAILED,
        ssrf_guard.ReasonCode.INVALID_URL,
    }
    try:
        verdict = await ssrf_guard.check_url(image_uri)
        if not verdict.allowed:
            # An address we must not reach is a refusal; a name we merely could
            # not resolve is not, because this fetch only measures. Google is
            # the authority on whether the URL works, and it answers in-band.
            if verdict.reason in _MEASURABLE_GUARD_MISSES:
                return None
            raise DocsValidationError(
                "docs_image_uri_blocked",
                ssrf_guard.deny_text(verdict),
                details={"image_uri": image_uri},
            )
        response = await client.get(image_uri, follow_redirects=True)
        if response.status_code != 200:
            return None
        content = response.content or b""
        if not content or len(content) > MAX_IMAGE_BYTES:
            return None
        measured = validate_image_bytes(
            content,
            media_type=_clean(response.headers.get("content-type")).split(";")[0].lower(),
        )
    except DocsValidationError:
        raise
    except Exception:
        return None
    if not measured.get("valid"):
        return None
    width, height = measured.get("width"), measured.get("height")
    if not width or not height:
        return None
    return (
        float(width) * IMAGE_POINTS_PER_PIXEL,
        float(height) * IMAGE_POINTS_PER_PIXEL,
    )


def _names_a_table_cell(payload: Mapping[str, Any]) -> bool:
    return any(
        payload.get(key) not in (None, "", {}) for key in ("table", "row", "column")
    )


def _image_cell_location(
    document: Mapping[str, Any], payload: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], float | None]:
    """Where in a named table cell an image goes, and what to report about it.

    The image lands after what the cell already holds, so a label written first
    stays first. The cell is named the way set_cells names it, which is what
    spares a caller the index arithmetic this namespace exists to avoid.
    """

    tables, tabs = _document_tables(document)
    tab_id = _resolve_tab_id(tabs, payload)
    table = _resolve_document_table(tables, tabs, payload, tab_id=tab_id)
    try:
        header_rows = effective_header_rows(table, payload.get("header"))
        row = resolve_row(table, payload.get("row"), header_rows=header_rows)
        column = resolve_column(table, payload.get("column"), header_rows=header_rows)
    except DocsSelectorError as exc:
        raise _selector_failure(exc) from exc
    cell = table["cells"][row - 1][column - 1]
    where = {"row": row, "column": column}
    if cell.get("merged_into"):
        head_row, head_column = cell["merged_into"]
        raise DocsValidationError(
            "docs_table_cell_merged",
            f"Row {row}, column {column} is merged into row {head_row}, column "
            f"{head_column}. Name the cell the image belongs in before retrying.",
            details={**where, "merged_into": cell["merged_into"]},
        )
    if cell.get("nested_table"):
        raise DocsValidationError(
            "docs_table_nested",
            f"Row {row}, column {column} holds a nested table, which this action "
            "does not write into.",
            details=where,
        )
    location: dict[str, Any] = {"index": _int(cell.get("content_end"))}
    if tab_id:
        location["tabId"] = tab_id
    return (
        location,
        {
            "tab_id": tab_id,
            "tab_count": len(tabs),
            "table": table.get("position"),
            "cell": where,
        },
        _cell_content_width_pt(table, column, document=document, tab_id=tab_id),
    )


async def _embed_image(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    payload = spread_table_selector(payload)
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
    if _names_a_table_cell(payload):
        if payload.get("index") is not None:
            raise DocsValidationError(
                "docs_image_target_ambiguous",
                "Name either a table cell (table with row and column) or an "
                "index, not both.",
                details={"index": payload.get("index")},
            )
        location, target, cell_width_pt = _image_cell_location(document, payload)
    else:
        cell_width_pt = None
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
        target = {"tab_id": tab_id, "tab_count": len(tabs)}
    insert: dict[str, Any] = {"location": location, "uri": image_uri}
    width = payload.get("width_pt")
    height = payload.get("height_pt")
    fit: dict[str, Any] | None = None
    if width is None and height is None and cell_width_pt:
        # A picture at its natural size stretches a table row across the page.
        # Measuring is what keeps a small image small: Docs scales to fit the
        # box it is given, which would also enlarge one that already fits.
        natural = await _natural_size_pt(client, image_uri)
        if natural is not None and natural[0] > cell_width_pt:
            scale = cell_width_pt / natural[0]
            width = round(cell_width_pt, 1)
            height = round(natural[1] * scale, 1)
            fit = {
                "reason": "cell_width",
                "cell_width_pt": round(cell_width_pt, 1),
                "natural_pt": [round(natural[0], 1), round(natural[1], 1)],
            }
    if width is not None or height is not None:
        object_size: dict[str, Any] = {}
        if width is not None:
            object_size["width"] = {"magnitude": _points(width), "unit": "PT"}
        if height is not None:
            object_size["height"] = {"magnitude": _points(height), "unit": "PT"}
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
        **({"fit": fit} if fit else {}),
        **target,
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


async def _update_comment(
    client: httpx.AsyncClient, *, access_token: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    """Rewrite the text of a comment, or of one reply inside it."""

    document_id = _document_id(payload.get("document_ref"))
    comment_id = _clean(payload.get("comment_id"))
    if not comment_id:
        raise DocsValidationError("comment_id_required", "comment_id is required.")
    reply_id = _clean(payload.get("reply_id"))
    content = _bounded_comment(payload.get("content"))
    url = f"{DRIVE_API}/files/{document_id}/comments/{comment_id}"
    fields = _COMMENT_FIELDS
    if reply_id:
        url = f"{url}/replies/{reply_id}"
        fields = "id,content,action,createdTime,modifiedTime,author(displayName)"
    response = await client.patch(
        url,
        headers=_headers(access_token),
        params={"fields": fields},
        json={"content": content},
    )
    _raise_for_status(response, operation="update_comment", mutating=True)
    updated = response.json()
    updated = dict(updated) if isinstance(updated, Mapping) else {}
    result: dict[str, Any] = {
        "document_id": document_id,
        "web_url": _web_url(document_id),
        "comment_id": comment_id,
        "content": _clean(updated.get("content")),
        "modified_time": _clean(updated.get("modifiedTime")),
        "idempotency_key": _clean(payload.get("idempotency_key")),
    }
    if reply_id:
        result["reply_id"] = reply_id
    return result


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
    "read_image": _read_image,
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
    "add_row": _add_row,
    "add_tab": _add_tab,
    "update_tab": _update_tab,
    "delete_tab": _delete_tab,
    "import": _import_document,
    # drive files as themselves (drive:read / drive:write)
    "drive_upload": _drive_upload_file,
    "drive_list": _drive_list_folder,
    # comment (docs:comment)
    "create_comment": _create_comment,
    "reply_comment": _reply_comment,
    "update_comment": _update_comment,
    # the document as a file (docs:delete)
    "trash": _trash,
    "restore": _restore,
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
        "add_row",
        "add_tab",
        "update_tab",
        "delete_tab",
        "import",
        "trash",
        "restore",
        "drive_upload",
        "create_comment",
        "reply_comment",
        "update_comment",
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
