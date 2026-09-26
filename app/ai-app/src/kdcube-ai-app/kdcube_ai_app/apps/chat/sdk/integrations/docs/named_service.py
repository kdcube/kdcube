# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Provider-neutral document named service.

The ``docs`` namespace models word-processing documents. Google Docs is the
first transport, but the named-service contract does not expose Google access
tokens or require consumers to use Google-specific MCP tools. The document is a
flatter object than a spreadsheet: one ``docs.document`` object kind rather than
a spreadsheet-plus-tab pair.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import pathlib
import re
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Mapping,
    MutableMapping,
    Sequence,
)
from typing import Any

from kdcube_ai_app.apps.chat.sdk.integrations.docs.selectors import (
    DocsSelectorError,
    SELECTOR_CANDIDATE_LIMIT,
    comment_candidates,
    matching_comments,
    resolve_comment_selector,
    resolve_tab_selector,
    tab_candidates,
)
from kdcube_ai_app.apps.chat.sdk.integrations.docs.tables import spread_table_selector
from kdcube_ai_app.apps.chat.sdk.integrations.named_service_consent import (
    CONSENT_ERROR_CONTRACT,
    tool_error_response,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceContext,
    NamedServiceProvider,
    NamedServiceProviderSpec,
    NamedServiceRequest,
    NamedServiceResponse,
    NamedServiceSearchScope,
    NamedServiceStreamResult,
    TRANSPORT_API,
    TRANSPORT_LOCAL,
    named_service_provider,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.types import (
    BLOCK_PRODUCE,
    EVENT_RESOLVE,
    OBJECT_ACTION,
    OBJECT_DELETE,
    OBJECT_GET,
    OBJECT_LIST,
    OBJECT_RESOLVE,
    OBJECT_SCHEMA,
    OBJECT_SEARCH,
    OBJECT_UPSERT,
    PROVIDER_ABOUT,
    PROVIDER_CAPABILITIES,
)


LOGGER = logging.getLogger("kdcube.sdk.integrations.docs.named_service")


DOCS_NAMESPACE = "docs"
PROVIDER_ID = "sdk.integrations.docs"
GOOGLE_PROVIDER_KEY = "google"
DOCS_READ_CLAIM = "docs:read"
DOCS_WRITE_CLAIM = "docs:write"
DOCS_COMMENT_CLAIM = "docs:comment"
# The document as a file is a different subject from its contents: docs:write
# edits what is inside one, docs:delete decides whether it stays in Drive.
DOCS_DELETE_CLAIM = "docs:delete"
# Drive-as-files claims, mirrored from the productivity surface: raw upload
# and folder listing need honest Drive scopes because docs:write's drive.file
# cannot reach pre-existing folders. Folder placement on copy/import widens
# to drive:write only when a parent_id is actually requested.
DRIVE_READ_CLAIM = "drive:read"
DRIVE_WRITE_CLAIM = "drive:write"

DOCS_DOCUMENT_KIND = "docs.document"
DOCS_IMPORT_SOURCE_KIND = "docs.import_source"
DOCS_EXPORT_KIND = "docs.export"
DOCS_IMAGE_KIND = "docs.image"
DOCS_TRANSPORTS = (TRANSPORT_LOCAL, TRANSPORT_API)
DOCS_SNAPSHOT_SCHEMA = "kdcube.docs.snapshot.v1"
DOCS_SNAPSHOT_MEDIA_TYPE = "application/vnd.kdcube.docs.snapshot+json"

DOCS_EXPORT_FORMATS: dict[str, tuple[str, str]] = {
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

# The bounded verbs a caller may name through object.action. Each verb maps to
# the identically named underlying proxy operation, except the two drive-file
# verbs, whose proxy operations are drive_upload/drive_list.
ACTION_COPY = "copy"
ACTION_INSERT_TEXT = "insert_text"
ACTION_APPEND_TEXT = "append_text"
ACTION_REPLACE_TEXT = "replace_text"
ACTION_APPLY_TEXT_STYLE = "apply_text_style"
ACTION_INSERT_PAGE_BREAK = "insert_page_break"
ACTION_EMBED_IMAGE = "embed_image"
ACTION_SET_CELLS = "set_cells"
ACTION_ADD_ROW = "add_row"
ACTION_ADD_TAB = "add_tab"
ACTION_UPDATE_TAB = "update_tab"
ACTION_DELETE_TAB = "delete_tab"
ACTION_TRASH = "trash"
ACTION_RESTORE = "restore"
ACTION_EXPORT = "export"
ACTION_IMPORT = "import"
ACTION_LIST_COMMENTS = "list_comments"
ACTION_GET_COMMENT = "get_comment"
ACTION_CREATE_COMMENT = "create_comment"
ACTION_REPLY_COMMENT = "reply_comment"
ACTION_UPDATE_COMMENT = "update_comment"
ACTION_RESOLVE_COMMENT = "resolve_comment"
ACTION_DELETE_COMMENT = "delete_comment"
# Drive files as themselves: an upload keeps its format (a DOCX stays a DOCX,
# a PDF a PDF) where import always converts to a native document; the folder
# listing is the verification read behind uploads and placements.
ACTION_UPLOAD_FILE = "upload_file"
ACTION_LIST_FOLDER = "list_folder"

# Generic UI actions are resolved by the provider but are not advertised as
# model-callable document mutations.
UI_ACTION_OPEN = "open"
UI_ACTION_DOWNLOAD = "download"

# Read-only actions gate on docs:read alone.
DOCS_READ_ACTIONS = frozenset({ACTION_EXPORT, ACTION_LIST_COMMENTS, ACTION_GET_COMMENT})
# Body-mutating actions gate on docs:read + docs:write.
DOCS_WRITE_ACTIONS = frozenset(
    {
        ACTION_COPY,
        ACTION_INSERT_TEXT,
        ACTION_APPEND_TEXT,
        ACTION_REPLACE_TEXT,
        ACTION_APPLY_TEXT_STYLE,
        ACTION_INSERT_PAGE_BREAK,
        ACTION_EMBED_IMAGE,
        ACTION_SET_CELLS,
        ACTION_ADD_ROW,
        ACTION_ADD_TAB,
        ACTION_UPDATE_TAB,
        ACTION_DELETE_TAB,
        ACTION_IMPORT,
    }
)
# Trashing a document and restoring it act on the file, not on its contents.
DOCS_FILE_ACTIONS = frozenset({ACTION_TRASH, ACTION_RESTORE})
# Comment-thread actions gate on docs:read + docs:comment.
DOCS_COMMENT_ACTIONS = frozenset(
    {
        ACTION_CREATE_COMMENT,
        ACTION_REPLY_COMMENT,
        ACTION_UPDATE_COMMENT,
        ACTION_RESOLVE_COMMENT,
        ACTION_DELETE_COMMENT,
    }
)

# Drive-file actions gate on their own Drive claims, never the docs claims.
DRIVE_FILE_READ_ACTIONS = frozenset({ACTION_LIST_FOLDER})
DRIVE_FILE_WRITE_ACTIONS = frozenset({ACTION_UPLOAD_FILE})

DOCS_ACTIONS = (
    ACTION_COPY,
    ACTION_INSERT_TEXT,
    ACTION_APPEND_TEXT,
    ACTION_REPLACE_TEXT,
    ACTION_APPLY_TEXT_STYLE,
    ACTION_INSERT_PAGE_BREAK,
    ACTION_EMBED_IMAGE,
    ACTION_SET_CELLS,
    ACTION_ADD_ROW,
    ACTION_ADD_TAB,
    ACTION_UPDATE_TAB,
    ACTION_DELETE_TAB,
    ACTION_TRASH,
    ACTION_RESTORE,
    ACTION_EXPORT,
    ACTION_IMPORT,
    ACTION_LIST_COMMENTS,
    ACTION_GET_COMMENT,
    ACTION_CREATE_COMMENT,
    ACTION_REPLY_COMMENT,
    ACTION_UPDATE_COMMENT,
    ACTION_RESOLVE_COMMENT,
    ACTION_DELETE_COMMENT,
    ACTION_UPLOAD_FILE,
    ACTION_LIST_FOLDER,
)

DOCS_SINGLE_TAB_ACTIONS = frozenset(
    {
        ACTION_INSERT_TEXT,
        ACTION_APPEND_TEXT,
        ACTION_APPLY_TEXT_STYLE,
        ACTION_INSERT_PAGE_BREAK,
        ACTION_EMBED_IMAGE,
        ACTION_SET_CELLS,
        ACTION_ADD_ROW,
    }
)
DOCS_COMMENT_REFERENCE_ACTIONS = frozenset(
    {
        ACTION_GET_COMMENT,
        ACTION_REPLY_COMMENT,
        ACTION_UPDATE_COMMENT,
        ACTION_RESOLVE_COMMENT,
        ACTION_DELETE_COMMENT,
    }
)
DOCS_DOCUMENT_COMMENT_ACTIONS = DOCS_COMMENT_ACTIONS | frozenset(
    {ACTION_LIST_COMMENTS, ACTION_GET_COMMENT}
)
COMMENT_SELECTOR_PAGE_SIZE = 100
COMMENT_SELECTOR_MAX_PAGES = 5

# How many characters of body text block.produce previews inline.
BLOCK_PREVIEW_CHARS = 800

ExecuteDocsOperation = Callable[..., Awaitable[Mapping[str, Any]]]


def _action_claim(action: str) -> str | tuple[str, ...]:
    if action in DRIVE_FILE_READ_ACTIONS:
        return DRIVE_READ_CLAIM
    if action in DRIVE_FILE_WRITE_ACTIONS:
        return DRIVE_WRITE_CLAIM
    if action in DOCS_READ_ACTIONS:
        return DOCS_READ_CLAIM
    if action in DOCS_COMMENT_ACTIONS:
        return (DOCS_READ_CLAIM, DOCS_COMMENT_CLAIM)
    if action in DOCS_FILE_ACTIONS:
        return (DOCS_READ_CLAIM, DOCS_DELETE_CLAIM)
    return (DOCS_READ_CLAIM, DOCS_WRITE_CLAIM)


def _placement_claim(action: str, payload: Mapping[str, Any]) -> str | tuple[str, ...]:
    """The action's claim, widened to drive:write when the call asks for
    folder placement: docs:write's drive.file scope cannot write into a
    pre-existing folder, so a parent_id changes what credential the call
    honestly needs, and only then."""
    claim = _action_claim(action)
    if not _text(payload.get("parent_id")):
        return claim
    claims = (claim,) if isinstance(claim, str) else tuple(claim)
    if DRIVE_WRITE_CLAIM in claims:
        return claims
    return (*claims, DRIVE_WRITE_CLAIM)


DOCS_SEARCH_FILTERS = {
    "account_id": {
        "type": "string",
        "description": (
            "Optional connected document account id. Required when more than "
            "one connected account is eligible."
        ),
    },
    "cursor": {
        "type": "string",
        "description": "Optional next_cursor returned by an earlier search.",
    },
}

DOCS_SEARCH_SCOPES = (
    NamedServiceSearchScope(
        namespace=DOCS_NAMESPACE,
        label="documents",
        description=(
            "Find documents by title through an approved connected account. "
            "A non-blank query returns exact logical-title matches first, then "
            "title-prefix matches. Results can be native documents or supported "
            "import sources such as DOCX. A blank query lists recent results."
        ),
        filters_schema=DOCS_SEARCH_FILTERS,
    ),
)

DOCS_GRANT_HINTS = {
    "object.list": [DOCS_READ_CLAIM],
    "object.search": [DOCS_READ_CLAIM],
    "object.get": [DOCS_READ_CLAIM],
    "object.upsert": [DOCS_WRITE_CLAIM],
    "object.delete": [DOCS_COMMENT_CLAIM],
    "object.action": [DOCS_WRITE_CLAIM],
    **{
        f"object.action.{action}": (
            [DRIVE_READ_CLAIM]
            if action in DRIVE_FILE_READ_ACTIONS
            else (
                [DRIVE_WRITE_CLAIM]
                if action in DRIVE_FILE_WRITE_ACTIONS
                else (
                    [DOCS_READ_CLAIM]
                    if action in DOCS_READ_ACTIONS
                    else (
                        [DOCS_COMMENT_CLAIM]
                        if action in DOCS_COMMENT_ACTIONS
                        else (
                            [DOCS_DELETE_CLAIM]
                            if action in DOCS_FILE_ACTIONS
                            else [DOCS_WRITE_CLAIM]
                        )
                    )
                )
            )
        )
        for action in DOCS_ACTIONS
    },
}


def _operation_connected_claims() -> dict[str, list[str]]:
    claims: dict[str, list[str]] = {
        "object.list": [DOCS_READ_CLAIM],
        "object.search": [DOCS_READ_CLAIM],
        "object.get": [DOCS_READ_CLAIM],
        "object.upsert": [DOCS_READ_CLAIM, DOCS_WRITE_CLAIM],
        "object.delete": [DOCS_READ_CLAIM, DOCS_COMMENT_CLAIM],
        "object.action": [DOCS_READ_CLAIM, DOCS_WRITE_CLAIM],
    }
    for action in DOCS_ACTIONS:
        claim = _action_claim(action)
        claims[f"object.action.{action}"] = (
            [claim] if isinstance(claim, str) else list(claim)
        )
    return claims


DOCS_CONNECTED_ACCOUNT_REQUIREMENTS = [
    {
        "provider_id": GOOGLE_PROVIDER_KEY,
        "provider_label": "Google",
        "claims": [
            DOCS_READ_CLAIM,
            DOCS_WRITE_CLAIM,
            DOCS_COMMENT_CLAIM,
            DOCS_DELETE_CLAIM,
        ],
        "claim_labels": {
            DOCS_READ_CLAIM: "read documents",
            DOCS_WRITE_CLAIM: "edit documents",
            DOCS_COMMENT_CLAIM: "comment on documents",
            DOCS_DELETE_CLAIM: "move documents to the trash and back",
        },
        "claims_by_operation": _operation_connected_claims(),
    }
]

DOCS_PROVIDER_CATALOG = {
    GOOGLE_PROVIDER_KEY: {
        "provider_id": GOOGLE_PROVIDER_KEY,
        "label": "Google Docs",
        "claims": {
            "read": DOCS_READ_CLAIM,
            "write": DOCS_WRITE_CLAIM,
            "comment": DOCS_COMMENT_CLAIM,
            "delete": DOCS_DELETE_CLAIM,
        },
    },
}

DOCS_SCHEMA = {
    "namespace": DOCS_NAMESPACE,
    "refs": {
        "document": "docs:<provider>:<account_id>:document:<document_id>",
        "import_source": "docs:<provider>:<account_id>:source:<file_id>",
        "export": ("docs:<provider>:<account_id>:export:<format>:<document_id>"),
        "image": ("docs:<provider>:<account_id>:image:<object_id>:<document_id>"),
    },
    "object_kinds": {
        DOCS_DOCUMENT_KIND: {
            "description": "One document visible to a connected account.",
            "fields": [
                "ref",
                "provider",
                "account_id",
                "document_id",
                "title",
                "revision_id",
                "web_url",
                "text",
                "tabs",
                "end_index",
                "created_time",
                "modified_time",
            ],
        },
        DOCS_IMPORT_SOURCE_KIND: {
            "description": (
                "A compatible document file visible in Drive that must be "
                "copied and converted before native Google Docs edits."
            ),
            "fields": [
                "ref",
                "provider",
                "account_id",
                "document_id",
                "title",
                "logical_title",
                "mime_type",
                "source_format",
                "size_bytes",
                "web_url",
                "created_time",
                "modified_time",
                "copyable",
                "conversion_required",
                "next_action",
            ],
        },
        DOCS_IMAGE_KIND: {
            "description": (
                "One inline image of a document, by the object_id a table read "
                "reports. Resolve the ref to stream the image bytes; the "
                "reference itself carries what the document knows about it."
            ),
            "fields": [
                "ref",
                "provider",
                "account_id",
                "document_id",
                "object_id",
                "alt",
                "title",
                "width_pt",
                "height_pt",
                "source_uri",
                "mime_type",
                "filename",
                "download",
            ],
        },
        DOCS_EXPORT_KIND: {
            "description": (
                "One portable export of a document. Resolve the ref to stream "
                "the bytes or use its short-lived download capability."
            ),
            "fields": [
                "ref",
                "provider",
                "account_id",
                "document_id",
                "format",
                "filename",
                "mime_type",
                "download",
            ],
        },
    },
    "selectors": {
        "tab_selector": {
            "description": (
                "Identify one tab from the selected document's returned tab "
                "metadata. Matching is case-insensitive and lexical; duplicate "
                "matches return bounded candidates instead of being guessed."
            ),
            "fields": {
                "title": "Exact tab title.",
                "title_contains": "Literal fragment of the tab title.",
                "position": "1-based position in the document's flattened tab order.",
                "hierarchy": (
                    "Exact root-to-tab title path as a list, or as titles joined "
                    "with / or >."
                ),
            },
        },
        "comment_selector": {
            "description": (
                "Identify one document-level comment from bounded Drive comment "
                "pages. Matching is case-insensitive and lexical. Use author='me' "
                "for a comment written by the connected account."
            ),
            "fields": {
                "text_contains": "Literal fragment in the comment or quoted text.",
                "quoted_text_contains": "Literal fragment in quoted document text.",
                "author": "Exact display name, or 'me'.",
                "author_contains": "Literal fragment of the author display name.",
                "resolved": "Whether the comment thread is resolved.",
                "position": "1-based position in the bounded provider result.",
            },
            "scope": (
                "The stable Drive provider path manages document-level comments. "
                "A tab-scoped request returns tab_anchored_comments_unavailable."
            ),
        },
        "table": {
            "description": (
                "Identify one table in the selected tab from object.get's tables. "
                "A bare number is a position. Matching is case-insensitive and "
                "lexical; several matches return bounded candidates."
            ),
            "fields": {
                "position": (
                    "1-based table position in the tab; combined with another "
                    "field, the position among that field's matches."
                ),
                "after_heading": (
                    "Exact text of the nearest heading of any level above the "
                    "table; text between them does not matter."
                ),
                "header_contains": (
                    "Literal fragment of a header cell, or of a first-row cell "
                    "when the table has no header row."
                ),
            },
        },
        "row": {
            "description": (
                "Identify one row of the selected table. Rows are physical and "
                "1-based, header rows included."
            ),
            "fields": {
                "number": "1-based row number; a bare number means the same.",
                "where": (
                    "{column, equals | contains}: the one data row whose cell in "
                    "that column matches. Header rows never match."
                ),
            },
        },
    },
    "search": {
        "description": (
            "A non-blank query checks native titles and logical import-source "
            "titles first, then returns title-prefix matches. For example, "
            "26_006 is an exact logical-title match for 26_006.docx. Check "
            "exact_title_match and object_kind before choosing a result. Search "
            "uses Drive metadata, so read the selected native document to learn "
            "its tabs before editing."
        ),
        "filters": DOCS_SEARCH_FILTERS,
        "result_metadata": [
            "exact_match_count",
            "incomplete_search",
            "match_mode",
        ],
    },
    "get": {
        "description": (
            "Read an object by ref. A native document returns metadata and "
            "extracted body text - a table renders one row per line with a "
            "caption naming its size and header, cells separated by | , and "
            "[person], [image], [merged] or [nested table] where a cell holds "
            "something other than text - plus tab_count, each tab's id, title, hierarchy, "
            "and end index, and whether mutation requires tab selection. A "
            "native document also returns tables: each table's tab, position, "
            "nearest heading above it, size, header row or first row, and a "
            "ready selector. To read cell text, pass include: [\"tables\"] for "
            "every table, or filters.tables with a selector (or a list of "
            "up to 5), optionally with rows: \"1-50\" and header: 1 (a number "
            "of header rows); each table returns up to 50 "
            "rows by default and next_rows when more remain, within 2,000 "
            "cells per call, and tables_truncated when the document holds more "
            "than 5 tables. Each cell carries its own row and column and names "
            "what it holds besides text: a person chip names its email, a "
            "rich link its uri, an image its alt text, size in points, and the "
            "object_id that identifies it; rows count "
            "header rows. Write cells with object.action set_cells, passing "
            "the same selector as its selector field. An "
            "import source returns file metadata and "
            "the instruction to copy it into an editable native document. On "
            "a configured turnless transport, native documents can also offer "
            "a short-lived URL for the complete JSON snapshot."
        ),
        "filters": ["include_text", "tables"],
    },
    "materialization": {
        "description": (
            "A materializing client can resolve a document ref through "
            "object.get with response_mode=stream. The JSON snapshot carries "
            "document metadata, the extracted body text, every table with its "
            "cells, and open comments. "
            "An export ref streams the complete portable file bytes instead."
        ),
        "schema": DOCS_SNAPSHOT_SCHEMA,
        "media_type": DOCS_SNAPSHOT_MEDIA_TYPE,
        "refs": ["document", "import_source", "export"],
    },
    "upsert": {
        "create": {
            "description": "Omit object_ref to create a document.",
            "object": ["title", "initial_text"],
        },
        "document": {
            "description": (
                "Use a document ref with object.text/index to insert or append "
                "body text, or object.replacements to substitute text. Multi-tab "
                "edits accept tab selectors resolved from document metadata. "
                "Replacement can also use explicit all_tabs=true. Table cells "
                "are written with object.action set_cells, not here."
            ),
            "object": [
                "text",
                "index",
                "replacements",
                "tab_id",
                "tab_ids",
                "tab_selector",
                "tab_selectors",
                "all_tabs",
            ],
        },
    },
    "delete": {
        "description": (
            "Remove one document-level comment identified by comment_id or "
            "comment_selector. Document files remain under their provider's "
            "lifecycle controls."
        ),
        "object_ref": "document ref",
        "payload": ["comment_id", "comment_selector"],
        "claim": "docs:comment",
    },
    "actions": {
        ACTION_COPY: {
            "description": (
                "Copy a document under a new title. Native Google Docs use "
                "provider-native copy. A compatible import source such as DOCX "
                "is converted into a new native Google Doc while the source "
                "stays unchanged. Search for the target title before retrying "
                "an uncertain copy result."
            ),
            "object_ref": "native document or import-source ref",
            "payload": ["title", "parent_id"],
            "claim": "docs:write",
        },
        ACTION_INSERT_TEXT: {
            "description": (
                "Insert text at an index (defaults to the selected tab's end). "
                "Choose a multi-tab target with tab_id or tab_selector. With "
                "preview: true nothing is written: the answer says what already "
                "sits at that index, which paragraph or table cell it falls in, "
                "and the text on either side. A smart chip is its own element "
                "rather than styled text, so write a sentence holding one as "
                'pieces: [{"text": "Owner "}, {"person": "a@b.com"}, '
                '{"text": " reviews by "}, {"date": "2026-10-02"}, '
                '{"link": "https://docs.google.com/document/d/..."}] - named in order and '
                "written at one index, with no offsets for the caller to "
                "compute. Name text or pieces, never both. A heading or a list "
                "is written as a unit rather than as raw text: style takes "
                "title, subtitle, normal or heading_1..heading_6, list takes "
                "bullet or number, and items writes one paragraph per entry, "
                "each entry text or pieces. A styled paragraph starts its own "
                "paragraph, so an index inside a sentence is refused with the "
                "text it would have restyled."
            ),
            "object_ref": "document ref",
            "payload": [
                "text",
                "pieces",
                "items",
                "style",
                "list",
                "index",
                "preview",
                "tab_id",
                "tab_selector",
            ],
            "claim": "docs:write",
        },
        ACTION_APPEND_TEXT: {
            "description": (
                "Append text at the selected tab's body end, or pieces, items, "
                "style and list to append whole paragraphs, in the shapes "
                "insert_text takes. Choose a multi-tab target with tab_id or "
                "tab_selector."
            ),
            "object_ref": "document ref",
            "payload": [
                "text",
                "pieces",
                "items",
                "style",
                "list",
                "tab_id",
                "tab_selector",
            ],
            "claim": "docs:write",
        },
        ACTION_REPLACE_TEXT: {
            "description": (
                "Replace matches in selected tab_ids/tab_selectors, or in every "
                "tab only when all_tabs=true is explicit. Every match changes, "
                "table cells included; to change one table cell use set_cells. "
                "With preview: true nothing is written: the answer counts the "
                "matches of each phrase and shows where they sit."
            ),
            "object_ref": "document ref",
            "payload": [
                "replacements",
                "preview",
                "tab_ids",
                "tab_selector",
                "tab_selectors",
                "all_tabs",
            ],
            "claim": "docs:write",
        },
        ACTION_APPLY_TEXT_STYLE: {
            "description": (
                "Apply bounded character styling to a text range in one tab. "
                "Choose a multi-tab target with tab_id or tab_selector. With "
                "preview: true nothing is written: the answer shows the text "
                "the range covers."
            ),
            "object_ref": "document ref",
            "payload": [
                "preview",
                "start_index",
                "end_index",
                "bold",
                "italic",
                "underline",
                "strikethrough",
                "font_size",
                "link_url",
                "tab_id",
                "tab_selector",
            ],
            "claim": "docs:write",
        },
        ACTION_INSERT_PAGE_BREAK: {
            "description": (
                "Insert a page break (defaults to the selected tab's body end)."
            ),
            "object_ref": "document ref",
            "payload": ["index", "tab_id", "tab_selector"],
            "claim": "docs:write",
        },
        ACTION_EMBED_IMAGE: {
            "description": (
                "Embed an image from a public http(s) URL that Google fetches "
                "once at insert time (PNG/JPEG/GIF, <=25MB, <=2000px per side). "
                "For a file this deployment already holds, pass file_ref "
                "instead - a staged ref from request_upload, or a conv:fi: ref "
                "for an artifact of this conversation - and it is served to "
                "Google for that one fetch and removed straight after. Name "
                "one of image_uri or file_ref, never both. "
                "Name a table cell the way set_cells does - selector or "
                "tab_selector plus table, with row and column - and the image "
                "lands after what that cell already holds; name an index "
                "instead to place it in the body, or neither to append at the "
                "end. Naming both a cell and an index is refused. A merged-away "
                "or nested-table cell is refused before any write. The result "
                "names the object_id of the image, and the cell it went into. "
                "An image placed in a cell without width_pt is fitted to the "
                "column when it is wider, keeping its proportions, and the "
                "answer reports that as fit; one that already fits is left at "
                "its own size. The Docs API carries no field for an image's alt "
                "text on any request, so an image placed here reads back "
                "without one."
            ),
            "object_ref": "document ref",
            "payload": [
                "image_uri",
                "file_ref",
                "selector",
                "table",
                "row",
                "column",
                "header",
                "index",
                "width_pt",
                "height_pt",
                "tab_id",
                "tab_selector",
            ],
            "claim": "docs:write",
        },
        ACTION_ADD_ROW: {
            "description": (
                "Add one row to a table and, with cells, fill it in the same "
                "call. Name the table the way set_cells does, with selector or "
                "tab_selector plus table. The row goes to the end unless "
                "after_row names the row to put it below, by 1-based number or "
                "{where: {column, equals | contains}}. cells takes the shapes "
                "set_cells takes, so a dated record is one call instead of an "
                "index. The result names the new row's number, and its cells "
                "when it filled them."
            ),
            "object_ref": "document ref",
            "payload": [
                "selector",
                "table",
                "after_row",
                "cells",
                "header",
                "revision_id",
                "tab_id",
                "tab_selector",
            ],
        },
        ACTION_ADD_TAB: {
            "description": (
                "Add a tab to the document. Without title Google names it; "
                "index places it among its siblings, zero-based, and "
                "parent_tab_id nests it under an existing tab. The result "
                "carries the new tab and the document's tabs."
            ),
            "object_ref": "document ref",
            "payload": ["title", "index", "parent_tab_id"],
        },
        ACTION_UPDATE_TAB: {
            "description": (
                "Rename a tab or move it among its siblings. Name the tab with "
                "tab_id or tab_selector, and pass title, index, or both."
            ),
            "object_ref": "document ref",
            "payload": ["tab_id", "tab_selector", "title", "index"],
        },
        ACTION_DELETE_TAB: {
            "description": (
                "Delete one tab and everything in it. Google deletes its child "
                "tabs with it, and the result lists them. A document keeps at "
                "least one tab, so deleting the only tab is refused."
            ),
            "object_ref": "document ref",
            "payload": ["tab_id", "tab_selector"],
        },
        ACTION_TRASH: {
            "description": (
                "Move the document to the Drive trash, where it stays "
                "recoverable until Drive empties it. The document keeps its id, "
                "so restore brings it back. This acts on the document itself, "
                "not on its contents, and needs the docs:delete claim."
            ),
            "object_ref": "document ref",
            "payload": [],
        },
        ACTION_RESTORE: {
            "description": (
                "Bring a trashed document back out of the Drive trash. The "
                "counterpart of trash, under the same docs:delete claim."
            ),
            "object_ref": "document ref",
            "payload": [],
        },
        ACTION_SET_CELLS: {
            "description": (
                "Write text into cells of one table row. Name the table by "
                "passing a tables[].selector from object.get as selector, or by "
                "tab_selector plus table (position, after_heading, or "
                "header_contains). Name the row by 1-based number or "
                "{where: {column, equals | contains}} - a value the caller knows "
                "survives rows moving, and a number read from cells[].row does "
                "not have to be counted. Pass cells as "
                "{column: text}: a key naming a header is that column, otherwise "
                "a digit key is a 1-based number; for an explicit number pass a "
                "list of {column: <number>, text}. A list entry may carry a "
                "smart chip instead of text: "
                'person: "someone@example.com", date: "2026-10-02", or '
                'link: "https://docs.google.com/...". A link chip points at a Google '
                "resource - a Drive file, a Docs or Sheets document, a Calendar "
                "event, a YouTube video - and Google refuses any other address. "
                "Google fills a person's "
                "name, a link's title and a date's display text itself, so only "
                "the address, the uri and the instant are sent - a date chip "
                "may therefore read differently from what was passed, by the "
                "document's locale. Rows count header rows; a "
                "where predicate never matches them. Column names need a "
                "header row: the document's own, or header: 1. mode is replace "
                "(default), append, or prepend; an empty replace clears the "
                "cell. replace refuses a cell holding images or chips, and a "
                "merged-away cell is refused. remove_objects=true replaces "
                "them anyway; the result then lists them as before_objects, "
                "and an answer that removed someone's chip or image says so. "
                "Nested "
                "tables are refused. An optional revision_id from object.get "
                "refuses the write if the document changed since. Nothing is "
                "written when any cell is refused or a selector is ambiguous. "
                "The result names the row it wrote as row and row_label. This "
                "action writes text and smart chips; an image goes into a cell "
                "with embed_image, which takes these same selectors."
            ),
            "object_ref": "document ref",
            "payload": [
                "selector",
                "table",
                "row",
                "cells",
                "mode",
                "header",
                "remove_objects",
                "revision_id",
                "tab_id",
                "tab_selector",
            ],
            "modes": ["replace", "append", "prepend"],
            "claim": "docs:write",
        },
        ACTION_EXPORT: {
            "description": (
                "Produce a portable file ref and deliver it as a chat file "
                "when a chat lane is active. A materializing client can stream "
                "the returned ref without placing base64 in model context."
            ),
            "object_ref": "document ref",
            "payload": ["format"],
            "formats": sorted(DOCS_EXPORT_FORMATS),
            "claim": "docs:read",
        },
        ACTION_IMPORT: {
            "description": (
                "Create a document by importing source content. An optional "
                "parent_id places the new document into a Drive folder and "
                "additionally requires drive:write on the connected account."
            ),
            "object_ref": "none",
            "payload": [
                "title",
                "source_format",
                "content",
                "content_base64",
                "parent_id",
            ],
            "claim": "docs:write",
        },
        ACTION_UPLOAD_FILE: {
            "description": (
                "Upload one file to Drive WITHOUT conversion: a DOCX stays a "
                "DOCX, a PDF a PDF, a zip a zip. Optional parent_id places it "
                "into a folder. Use import instead when the goal is an "
                "editable native document."
            ),
            "object_ref": "none",
            "payload": ["name", "content_base64", "mime_type", "parent_id"],
            "claim": "drive:write",
        },
        ACTION_LIST_FOLDER: {
            "description": (
                "List one Drive folder's direct children with ids, names, "
                "MIME types, sizes, and links - the verification read behind "
                "uploads and placements."
            ),
            "object_ref": "none",
            "payload": ["folder_id", "limit", "cursor"],
            "claim": "drive:read",
        },
        ACTION_LIST_COMMENTS: {
            "description": "List comments on the document.",
            "object_ref": "document ref",
            "payload": ["include_resolved", "cursor", "limit"],
            "claim": "docs:read",
        },
        ACTION_GET_COMMENT: {
            "description": "Read one document-level comment by id or selector.",
            "object_ref": "document ref",
            "payload": ["comment_id", "comment_selector"],
            "claim": "docs:read",
        },
        ACTION_CREATE_COMMENT: {
            "description": "Create a comment on the document.",
            "object_ref": "document ref",
            "payload": ["content", "quoted_text", "anchor"],
            "claim": "docs:comment",
        },
        ACTION_REPLY_COMMENT: {
            "description": "Reply to one document-level comment by id or selector.",
            "object_ref": "document ref",
            "payload": ["comment_id", "comment_selector", "content"],
            "claim": "docs:comment",
        },
        ACTION_UPDATE_COMMENT: {
            "description": (
                "Rewrite the text of one document-level comment, named by id or "
                "selector. With reply_id, rewrite that reply instead of the "
                "comment. Only the author's own comment can be rewritten."
            ),
            "object_ref": "document ref",
            "payload": ["comment_id", "comment_selector", "reply_id", "content"],
            "claim": "docs:comment",
        },
        ACTION_RESOLVE_COMMENT: {
            "description": "Resolve one document-level comment by id or selector.",
            "object_ref": "document ref",
            "payload": ["comment_id", "comment_selector", "content"],
            "claim": "docs:comment",
        },
        ACTION_DELETE_COMMENT: {
            "description": "Delete one document-level comment by id or selector.",
            "object_ref": "document ref",
            "payload": ["comment_id", "comment_selector"],
            "claim": "docs:comment",
        },
    },
    "account_selection": {
        "search": (
            "Pass filters.account_id when several connected accounts are "
            "eligible. Otherwise the response returns reason=account_required "
            "with labeled candidates."
        ),
        "refs": "Every returned object ref embeds its provider and account id.",
    },
    "consent_errors": CONSENT_ERROR_CONTRACT,
    "grant_hints": DOCS_GRANT_HINTS,
    "connected_account_claims": {
        GOOGLE_PROVIDER_KEY: {
            "read": DOCS_READ_CLAIM,
            "write": DOCS_WRITE_CLAIM,
            "comment": DOCS_COMMENT_CLAIM,
            "delete": DOCS_DELETE_CLAIM,
        }
    },
}

DOCS_SCHEMA_PROJECTION = {
    "catalog": {
        "id": "docs",
        "label": "Documents",
        "description": "Explore document discovery, editing, transfer, and discussion capabilities.",
        "children": [
            {
                "id": "discover",
                "label": "Find documents",
                "description": "List and search native documents and importable files.",
                "operations": [
                    {"object_kind": DOCS_DOCUMENT_KIND, "schema_operation": "object.list"},
                    {"object_kind": DOCS_DOCUMENT_KIND, "schema_operation": "object.search"},
                    {"object_kind": DOCS_IMPORT_SOURCE_KIND, "schema_operation": "object.list"},
                    {"object_kind": DOCS_IMPORT_SOURCE_KIND, "schema_operation": "object.search"},
                ],
            },
            {
                "id": "work",
                "label": "Work with documents",
                "children": [
                    {
                        "id": "read",
                        "label": "Read and inspect",
                        "object_kind": DOCS_DOCUMENT_KIND,
                        "operations": ["object.get"],
                    },
                    {
                        "id": "edit",
                        "label": "Create and edit",
                        "object_kind": DOCS_DOCUMENT_KIND,
                        "keywords": ["write", "modify", "format", "image", "page", "table", "cell"],
                        "operations": [
                            "object.upsert",
                            "object.delete",
                            f"object.action:{ACTION_INSERT_TEXT}",
                            f"object.action:{ACTION_APPEND_TEXT}",
                            f"object.action:{ACTION_REPLACE_TEXT}",
                            f"object.action:{ACTION_APPLY_TEXT_STYLE}",
                            f"object.action:{ACTION_INSERT_PAGE_BREAK}",
                            f"object.action:{ACTION_EMBED_IMAGE}",
                            f"object.action:{ACTION_SET_CELLS}",
                            f"object.action:{ACTION_ADD_ROW}",
                        ],
                    },
                    {
                        "id": "tabs",
                        "label": "Tabs",
                        "description": (
                            "Add, rename, move, and delete the document's tabs."
                        ),
                        "object_kind": DOCS_DOCUMENT_KIND,
                        "keywords": ["tab", "section", "rename", "reorder"],
                        "operations": [
                            f"object.action:{ACTION_ADD_TAB}",
                            f"object.action:{ACTION_UPDATE_TAB}",
                            f"object.action:{ACTION_DELETE_TAB}",
                        ],
                    },
                    {
                        "id": "lifecycle",
                        "label": "Trash and restore",
                        "description": (
                            "Move a document to the Drive trash and bring it "
                            "back. These act on the document itself, under a "
                            "claim of their own."
                        ),
                        "object_kind": DOCS_DOCUMENT_KIND,
                        "keywords": ["delete", "remove", "trash", "restore", "cleanup"],
                        "operations": [
                            f"object.action:{ACTION_TRASH}",
                            f"object.action:{ACTION_RESTORE}",
                        ],
                    },
                    {
                        "id": "transfer",
                        "label": "Copy, import, and export",
                        "keywords": ["download", "docx", "pdf", "portable file"],
                        "operations": [
                            {"object_kind": DOCS_DOCUMENT_KIND, "schema_operation": f"object.action:{ACTION_COPY}"},
                            {"object_kind": DOCS_DOCUMENT_KIND, "schema_operation": f"object.action:{ACTION_EXPORT}"},
                            {"object_kind": DOCS_DOCUMENT_KIND, "schema_operation": f"object.action:{ACTION_IMPORT}"},
                            {"object_kind": DOCS_IMPORT_SOURCE_KIND, "schema_operation": "object.get"},
                            {"object_kind": DOCS_IMPORT_SOURCE_KIND, "schema_operation": f"object.action:{ACTION_COPY}"},
                            {"object_kind": DOCS_EXPORT_KIND, "schema_operation": "object.get"},
                            {"object_kind": DOCS_IMAGE_KIND, "schema_operation": "object.get"},
                        ],
                    },
                    {
                        "id": "drive-files",
                        "label": "Drive files as themselves",
                        "description": (
                            "Upload a file to Drive without conversion and "
                            "list a folder's children. Distinct from import, "
                            "which always creates a native document."
                        ),
                        "keywords": ["upload", "folder", "zip", "raw file", "placement"],
                        "operations": [
                            {"object_kind": DOCS_DOCUMENT_KIND, "schema_operation": f"object.action:{ACTION_UPLOAD_FILE}"},
                            {"object_kind": DOCS_DOCUMENT_KIND, "schema_operation": f"object.action:{ACTION_LIST_FOLDER}"},
                        ],
                    },
                ],
            },
            {
                "id": "discussion",
                "label": "Comments and replies",
                "description": "Inspect and manage document comment threads.",
                "object_kind": DOCS_DOCUMENT_KIND,
                "keywords": ["comment", "reply", "resolve", "discussion", "tab"],
                "operations": [
                    f"object.action:{ACTION_LIST_COMMENTS}",
                    f"object.action:{ACTION_GET_COMMENT}",
                    f"object.action:{ACTION_CREATE_COMMENT}",
                    f"object.action:{ACTION_REPLY_COMMENT}",
                    f"object.action:{ACTION_UPDATE_COMMENT}",
                    f"object.action:{ACTION_RESOLVE_COMMENT}",
                    f"object.action:{ACTION_DELETE_COMMENT}",
                ],
            },
        ],
    },
    "kinds": {
        DOCS_DOCUMENT_KIND: {
            "refs": ["document"],
            "selectors": ["tab_selector", "comment_selector", "table", "row"],
            "related_kinds": [DOCS_IMPORT_SOURCE_KIND, DOCS_EXPORT_KIND],
            "operations": {
                "object.list": {},
                "object.search": {"sections": ["search"]},
                "object.get": {"sections": ["get", "materialization"]},
                "object.upsert": {
                    "sections": ["upsert"],
                    "section_keys": {"upsert": ["create", "document"]},
                },
                "object.delete": {"sections": ["delete"]},
            },
            "actions": [
                ACTION_COPY,
                ACTION_INSERT_TEXT,
                ACTION_APPEND_TEXT,
                ACTION_REPLACE_TEXT,
                ACTION_APPLY_TEXT_STYLE,
                ACTION_INSERT_PAGE_BREAK,
                ACTION_EMBED_IMAGE,
                ACTION_SET_CELLS,
                ACTION_ADD_ROW,
                ACTION_ADD_TAB,
                ACTION_UPDATE_TAB,
                ACTION_DELETE_TAB,
                ACTION_TRASH,
                ACTION_RESTORE,
                ACTION_EXPORT,
                ACTION_IMPORT,
                ACTION_LIST_COMMENTS,
                ACTION_GET_COMMENT,
                ACTION_CREATE_COMMENT,
                ACTION_REPLY_COMMENT,
                ACTION_UPDATE_COMMENT,
                ACTION_RESOLVE_COMMENT,
                ACTION_DELETE_COMMENT,
                # The drive-file verbs take no document ref (like import);
                # they live on the document kind for the same reason import
                # does: the namespace has no separate drive-file object kind.
                ACTION_UPLOAD_FILE,
                ACTION_LIST_FOLDER,
            ],
        },
        DOCS_IMPORT_SOURCE_KIND: {
            "refs": ["import_source"],
            "related_kinds": [DOCS_DOCUMENT_KIND],
            "operations": {
                "object.list": {},
                "object.search": {"sections": ["search"]},
                "object.get": {"sections": ["get"]},
            },
            "actions": [ACTION_COPY],
        },
        DOCS_EXPORT_KIND: {
            "refs": ["export"],
            "related_kinds": [DOCS_DOCUMENT_KIND],
            "operations": {
                "object.get": {"sections": ["materialization"]},
            },
        },
        DOCS_IMAGE_KIND: {
            "refs": ["image"],
            "related_kinds": [DOCS_DOCUMENT_KIND],
            "operations": {
                "object.get": {"sections": ["materialization"]},
            },
        },
    },
}

DOCS_INTRO = (
    "Use namespace `docs` for user-connected documents. Search by title; native "
    "documents can be read and edited directly, while a DOCX, ODT, or RTF import "
    "source is copied into a native document before editing. Use object.upsert "
    "and declared object.actions for explicit changes and comments."
)

DOCS_PRESENTATION = {
    "about": "Find, read, create, edit, and comment on documents you connect.",
    "third_party": "Google Docs is the first provider behind this namespace.",
    "operations": {
        "provider.about": {
            "label": "Service overview",
            "description": "What the document service does and how to use it.",
        },
        "provider.capabilities": {
            "label": "Capabilities",
            "description": "The operations and bounded actions this service supports.",
        },
        "object.list": {
            "label": "Recent documents",
            "description": "List recently modified documents.",
        },
        "object.search": {
            "label": "Search documents",
            "description": "Find documents by title.",
        },
        "object.get": {
            "label": "Read document",
            "description": "Inspect metadata and read the body text.",
        },
        "object.schema": {
            "label": "Document schema",
            "description": "Read refs, fields, limits, and action payloads.",
        },
        "object.upsert": {
            "label": "Create or update document",
            "description": "Create a document or edit its body text.",
        },
        "object.delete": {
            "label": "Delete document comment",
            "description": "Delete one document comment.",
        },
    },
    # Human titles and user-terms lines, beside the schema rather than from it:
    # the schema text is written for the agent and reads as an instruction in a
    # consent card.
    "actions": {
        ACTION_COPY: {
            "label": "Copy document",
            "description": "Make a copy of a document under a new title.",
        },
        ACTION_INSERT_TEXT: {
            "label": "Insert text",
            "description": "Insert text at one place in a document.",
        },
        ACTION_APPEND_TEXT: {
            "label": "Append text",
            "description": "Add text to the end of a document.",
        },
        ACTION_REPLACE_TEXT: {
            "label": "Replace text",
            "description": "Replace every match of a phrase in a document.",
        },
        ACTION_APPLY_TEXT_STYLE: {
            "label": "Style text",
            "description": "Make a piece of text bold, italic, or a link.",
        },
        ACTION_INSERT_PAGE_BREAK: {
            "label": "Insert page break",
            "description": "Start a new page in a document.",
        },
        ACTION_EMBED_IMAGE: {
            "label": "Embed an image",
            "description": "Place an image from a public URL into a document or one of its table cells.",
        },
        ACTION_SET_CELLS: {
            "label": "Write table cells",
            "description": "Write text into cells of one table row.",
        },
        ACTION_ADD_ROW: {
            "label": "Add a table row",
            "description": "Add a row to a table and fill it in.",
        },
        ACTION_ADD_TAB: {
            "label": "Add a tab",
            "description": "Add a tab to a document.",
        },
        ACTION_UPDATE_TAB: {
            "label": "Rename or move a tab",
            "description": "Rename a tab or change its place in the document.",
        },
        ACTION_DELETE_TAB: {
            "label": "Delete a tab",
            "description": "Delete a tab and everything in it.",
        },
        ACTION_TRASH: {
            "label": "Trash document",
            "description": "Move a document to the Drive trash, where it stays recoverable.",
        },
        ACTION_RESTORE: {
            "label": "Restore document",
            "description": "Bring a document back out of the Drive trash.",
        },
        ACTION_EXPORT: {
            "label": "Export document",
            "description": "Deliver a document as a file you can download.",
        },
        ACTION_IMPORT: {
            "label": "Import a document",
            "description": "Turn a file such as DOCX into an editable document.",
        },
        ACTION_LIST_COMMENTS: {
            "label": "List comments",
            "description": "List the comment threads on a document.",
        },
        ACTION_GET_COMMENT: {
            "label": "Read a comment",
            "description": "Read one comment thread and its replies.",
        },
        ACTION_CREATE_COMMENT: {
            "label": "Comment on a document",
            "description": "Leave a comment on a document.",
        },
        ACTION_REPLY_COMMENT: {
            "label": "Reply to a comment",
            "description": "Reply in an existing comment thread.",
        },
        ACTION_UPDATE_COMMENT: {
            "label": "Edit a comment",
            "description": "Rewrite a comment you left on a document.",
        },
        ACTION_RESOLVE_COMMENT: {
            "label": "Resolve a comment",
            "description": "Mark a comment thread as resolved.",
        },
        ACTION_DELETE_COMMENT: {
            "label": "Delete a comment",
            "description": "Remove one comment from a document.",
        },
        ACTION_UPLOAD_FILE: {
            "label": "Upload a file to Drive",
            "description": "Put a file in Drive as it is, without converting it.",
        },
        ACTION_LIST_FOLDER: {
            "label": "List a Drive folder",
            "description": "See what a Drive folder holds.",
        },
    },
}


def _operations() -> dict[str, Any]:
    return {
        PROVIDER_ABOUT: {"transports": DOCS_TRANSPORTS},
        PROVIDER_CAPABILITIES: {"transports": DOCS_TRANSPORTS},
        OBJECT_LIST: {"transports": DOCS_TRANSPORTS},
        OBJECT_SEARCH: {"transports": DOCS_TRANSPORTS},
        OBJECT_GET: {"transports": DOCS_TRANSPORTS},
        OBJECT_SCHEMA: {"transports": DOCS_TRANSPORTS},
        OBJECT_UPSERT: {"transports": DOCS_TRANSPORTS},
        OBJECT_DELETE: {"transports": DOCS_TRANSPORTS},
        OBJECT_ACTION: {"transports": DOCS_TRANSPORTS},
        OBJECT_RESOLVE: {"transports": DOCS_TRANSPORTS},
        EVENT_RESOLVE: {"transports": DOCS_TRANSPORTS},
        BLOCK_PRODUCE: {"transports": DOCS_TRANSPORTS},
    }


def _spec_metadata() -> dict[str, Any]:
    return {
        "provider_catalog": DOCS_PROVIDER_CATALOG,
        "grant_hints": DOCS_GRANT_HINTS,
        "connected_accounts": DOCS_CONNECTED_ACCOUNT_REQUIREMENTS,
        "canonical_refs": DOCS_SCHEMA["refs"],
        "presentation": DOCS_PRESENTATION,
        "actions": {
            name: str((meta or {}).get("description") or "")
            for name, meta in DOCS_SCHEMA["actions"].items()
        },
        "object_kinds": {
            kind: str((meta or {}).get("description") or "")
            for kind, meta in DOCS_SCHEMA["object_kinds"].items()
        },
    }


def docs_named_service_spec(
    *, bundle_id: str | None = None
) -> NamedServiceProviderSpec:
    return NamedServiceProviderSpec(
        provider_id=PROVIDER_ID,
        bundle_id=bundle_id,
        namespace=DOCS_NAMESPACE,
        refs=("docs:*",),
        object_kinds=(
            DOCS_DOCUMENT_KIND,
            DOCS_IMPORT_SOURCE_KIND,
            DOCS_EXPORT_KIND,
        ),
        search_scopes=DOCS_SEARCH_SCOPES,
        operations=_operations(),
        label="Documents",
        description=(
            "Provider-neutral document namespace over user-connected accounts."
        ),
        intro=DOCS_INTRO,
        metadata=_spec_metadata(),
    )


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(
    value: Any,
    *,
    default: int = 0,
    minimum: int = 0,
    maximum: int = 2_147_483_647,
) -> int:
    try:
        parsed = int(value if value is not None else default)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _is_materialization_request(request: NamedServiceRequest) -> bool:
    context = request.context if isinstance(request.context, Mapping) else {}
    payload = request.payload if isinstance(request.payload, Mapping) else {}
    if _text(request.response_mode).lower() == "stream":
        return True
    if context.get("materialize") or payload.get("materialize"):
        return True
    return _text(context.get("source") or payload.get("source")) == "react.pull"


def document_ref(account_id: Any, document_id: Any) -> str:
    return (
        f"{DOCS_NAMESPACE}:{GOOGLE_PROVIDER_KEY}:"
        f"{_text(account_id)}:document:{_text(document_id)}"
    )


def document_source_ref(account_id: Any, file_id: Any) -> str:
    return (
        f"{DOCS_NAMESPACE}:{GOOGLE_PROVIDER_KEY}:"
        f"{_text(account_id)}:source:{_text(file_id)}"
    )


def _export_format(value: Any) -> tuple[str, str, str]:
    fmt = _text(value).lower() or "pdf"
    target = DOCS_EXPORT_FORMATS.get(fmt)
    if target is None:
        raise ValueError(
            "Invalid document export format. Allowed: "
            + ", ".join(sorted(DOCS_EXPORT_FORMATS))
            + "."
        )
    return fmt, target[0], target[1]


def document_export_ref(account_id: Any, document_id: Any, format: Any) -> str:
    fmt, _mime_type, _extension = _export_format(format)
    account = _text(account_id)
    document = _text(document_id)
    if not account or not document:
        raise ValueError("Document export refs require account_id and document_id.")
    return (
        f"{DOCS_NAMESPACE}:{GOOGLE_PROVIDER_KEY}:" f"{account}:export:{fmt}:{document}"
    )


def parse_docs_export_ref(value: Any) -> dict[str, Any]:
    ref = _text(value)
    parts = ref.split(":")
    if len(parts) != 6 or parts[0].lower() != DOCS_NAMESPACE:
        raise ValueError("Invalid docs export ref.")
    if parts[3] != "export" or not all(parts[index] for index in (1, 2, 4, 5)):
        raise ValueError("Invalid docs export ref.")
    fmt, mime_type, extension = _export_format(parts[4])
    return {
        "ref": ref,
        "provider": parts[1].lower(),
        "account_id": parts[2],
        "document_id": parts[5],
        "format": fmt,
        "mime_type": mime_type,
        "extension": extension,
        "object_kind": DOCS_EXPORT_KIND,
    }


def document_image_ref(account_id: Any, document_id: Any, object_id: Any) -> str:
    account = _text(account_id)
    document = _text(document_id)
    object_key = _text(object_id)
    if not account or not document or not object_key:
        raise ValueError(
            "Document image refs require account_id, document_id and object_id."
        )
    if ":" in object_key:
        raise ValueError("A document image object_id cannot contain :.")
    return (
        f"{DOCS_NAMESPACE}:{GOOGLE_PROVIDER_KEY}:"
        f"{account}:image:{object_key}:{document}"
    )


def parse_docs_image_ref(value: Any) -> dict[str, Any]:
    ref = _text(value)
    parts = ref.split(":")
    if len(parts) != 6 or parts[0].lower() != DOCS_NAMESPACE:
        raise ValueError("Invalid docs image ref.")
    if parts[3] != "image" or not all(parts[index] for index in (1, 2, 4, 5)):
        raise ValueError("Invalid docs image ref.")
    return {
        "ref": ref,
        "provider": parts[1].lower(),
        "account_id": parts[2],
        "object_id": parts[4],
        "document_id": parts[5],
        "object_kind": DOCS_IMAGE_KIND,
    }


def parse_docs_ref(value: Any) -> dict[str, Any]:
    ref = _text(value)
    parts = ref.split(":")
    if len(parts) != 5 or parts[0].lower() != DOCS_NAMESPACE:
        raise ValueError("Invalid docs object ref.")
    ref_kind = parts[3]
    if ref_kind not in {"document", "source"} or not all(
        parts[index] for index in (1, 2, 4)
    ):
        raise ValueError("Invalid docs document ref.")
    object_kind = (
        DOCS_IMPORT_SOURCE_KIND if ref_kind == "source" else DOCS_DOCUMENT_KIND
    )
    return {
        "ref": ref,
        "provider": parts[1].lower(),
        "account_id": parts[2],
        "document_id": parts[4],
        "object_kind": object_kind,
    }


def document_export_filename(*, title: Any, document_id: Any, extension: str) -> str:
    stem = _text(title) or _text(document_id) or "document"
    stem = re.sub(r"[\\/\x00-\x1f]+", "_", stem).strip(" .") or "document"
    return f"{stem}.{extension}"


def _export_object(
    parsed: Mapping[str, Any],
    *,
    title: Any = "",
    byte_size: Any = None,
) -> dict[str, Any]:
    size = _int(byte_size, default=-1, minimum=-1)
    obj = {
        "ref": _text(parsed.get("ref")),
        "object_ref": _text(parsed.get("ref")),
        "object_kind": DOCS_EXPORT_KIND,
        "provider": _text(parsed.get("provider")) or GOOGLE_PROVIDER_KEY,
        "account_id": _text(parsed.get("account_id")),
        "document_id": _text(parsed.get("document_id")),
        "format": _text(parsed.get("format")),
        "filename": document_export_filename(
            title=title,
            document_id=parsed.get("document_id"),
            extension=_text(parsed.get("extension")) or "bin",
        ),
        "mime_type": _text(parsed.get("mime_type")) or "application/octet-stream",
    }
    if size >= 0:
        obj["size_bytes"] = size
    return obj


_IMAGE_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/bmp": "bmp",
}


def document_image_filename(
    *, document_id: Any, object_id: Any, mime_type: Any
) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{_text(document_id)}-{_text(object_id)}")
    extension = _IMAGE_EXTENSIONS.get(_text(mime_type).lower(), "bin")
    return f"{stem.strip('._-') or 'image'}.{extension}"


def _image_object(
    parsed: Mapping[str, Any],
    *,
    described: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The image as an object: its ref plus what the document knows about it."""

    row = dict(described or {})
    row.pop("content_base64", None)
    obj: dict[str, Any] = {
        "ref": _text(parsed.get("ref")),
        "object_ref": _text(parsed.get("ref")),
        "object_kind": DOCS_IMAGE_KIND,
        "provider": _text(parsed.get("provider")) or GOOGLE_PROVIDER_KEY,
        "account_id": _text(parsed.get("account_id")),
        "document_id": _text(parsed.get("document_id")),
        "object_id": _text(parsed.get("object_id")),
    }
    for field in (
        "alt",
        "title",
        "width_pt",
        "height_pt",
        "source_uri",
        "tab_id",
        "tab_title",
        "cell",
        "mime_type",
        "byte_size",
        "web_url",
    ):
        if row.get(field) not in (None, "", {}):
            obj[field] = row[field]
    if obj.get("mime_type"):
        obj["filename"] = document_image_filename(
            document_id=obj["document_id"],
            object_id=obj["object_id"],
            mime_type=obj["mime_type"],
        )
    return obj


def _with_image_refs(
    row: MutableMapping[str, Any], *, account_id: str, document_id: str
) -> None:
    """Give every image a table read reports a ref that resolves to its bytes.

    A read hands back a ready ref for the same reason it hands back a ready
    table selector: nothing downstream should assemble one by hand.
    """

    if not account_id or not document_id:
        return
    for table in row.get("table_reads") or ():
        if not isinstance(table, Mapping):
            continue
        for cells in table.get("cells") or ():
            for cell in cells or ():
                if not isinstance(cell, MutableMapping):
                    continue
                for entry in cell.get("objects") or ():
                    if not isinstance(entry, MutableMapping):
                        continue
                    if _text(entry.get("kind")) != "image":
                        continue
                    object_id = _text(entry.get("object_id"))
                    if not object_id:
                        continue
                    try:
                        entry["ref"] = document_image_ref(
                            account_id, document_id, object_id
                        )
                    except ValueError:
                        continue


_INCLUDE_TABLE_CELLS = frozenset({"tables", "cells", "table_cells"})


def _wants_table_cells(include: Sequence[Any] | None) -> bool:
    """Whether include asks for cell content. An unknown name is refused."""

    wanted = False
    for entry in include or ():
        name = _text(entry).lower()
        if not name:
            continue
        if name in _INCLUDE_TABLE_CELLS:
            wanted = True
            continue
        raise ValueError(
            f"include does not support {name!r}. Read every table's cells with "
            'include: ["tables"], or name tables with filters.tables.'
        )
    return wanted


def _document_object(
    value: Mapping[str, Any],
    *,
    account_id: str,
    provider: str = GOOGLE_PROVIDER_KEY,
) -> dict[str, Any]:
    row = dict(value or {})
    document_id = _text(row.get("document_id"))
    import_source = bool(row.get("conversion_required")) or (
        row.get("native_document") is False
    )
    object_kind = DOCS_IMPORT_SOURCE_KIND if import_source else DOCS_DOCUMENT_KIND
    ref = (
        document_source_ref(account_id, document_id)
        if import_source
        else document_ref(account_id, document_id)
    )
    _with_image_refs(row, account_id=account_id, document_id=document_id)
    return {
        **row,
        "ref": ref,
        "object_kind": object_kind,
        "provider": provider,
        "account_id": account_id,
        "document_id": document_id,
    }


# Google fetches an inserted image itself and accepts only these.
DOCS_IMAGE_MEDIA_TYPES = {"image/png", "image/jpeg", "image/gif"}
MAX_DOCS_IMAGE_BYTES = 25 * 1024 * 1024
MAX_DOCS_IMAGE_PIXELS = 2000


_IMAGE_MEDIA_TYPE_BY_FORMAT = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "GIF": "image/gif",
    "WEBP": "image/webp",
    "BMP": "image/bmp",
}


def _mime_for_image(measured: Mapping[str, Any]) -> str:
    """The media type of bytes that arrived without one declared."""

    return _IMAGE_MEDIA_TYPE_BY_FORMAT.get(
        _text(measured.get("format")).upper(), ""
    )


def origin_unreachable_refusal(
    detail: str,
) -> Callable[[NamedServiceRequest], NamedServiceResponse]:
    """The public origin did not answer at all."""

    def _refuse(request: NamedServiceRequest) -> NamedServiceResponse:
        return _image_refusal(
            request,
            code="docs_image_origin_unreachable",
            message=(
                "This deployment's public origin did not answer a fetch of the "
                "staged file, so the document provider will not reach it "
                "either. Nothing was written."
            ),
            status=502,
            details={"error": detail},
        )

    return _refuse


def origin_answer_refusal(
    status: int, media_type: str
) -> Callable[[NamedServiceRequest], NamedServiceResponse]:
    """The public origin answered with something other than the file."""

    def _refuse(request: NamedServiceRequest) -> NamedServiceResponse:
        return _image_refusal(
            request,
            code="docs_image_origin_not_public",
            message=(
                "This deployment's public origin answered an anonymous "
                f"browser-like fetch with {media_type or 'no content type'} "
                f"(HTTP {status}) instead of the image. Something in front of "
                "the deployment - a tunnel's warning page, a proxy, a login "
                "screen - answers before the file is reached, so the document "
                "provider would receive that page rather than the picture. "
                "This is a deployment setting rather than a problem with the "
                "file: the origin must serve it to an anonymous caller. Pass "
                "image_uri with an already public URL to insert an image "
                "meanwhile."
            ),
            status=502,
            details={"origin_status": status, "origin_media_type": media_type},
        )

    return _refuse


def _image_refusal(
    request: NamedServiceRequest,
    *,
    code: str,
    message: str,
    status: int = 400,
    details: Mapping[str, Any] | None = None,
) -> NamedServiceResponse:
    return NamedServiceResponse.error_response(
        code=code,
        message=message,
        status=status,
        details=dict(details) if details else None,
        provider={"provider_id": PROVIDER_ID},
        namespace=request.namespace or DOCS_NAMESPACE,
        object_ref=request.object_ref,
    )



def _snapshot_filename(document_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", _text(document_id)).strip("._-")
    return f"{safe or 'document'}.docs.json"


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def _snapshot_from_block_target(target: Mapping[str, Any]) -> dict[str, Any]:
    raw = target.get("raw") if isinstance(target.get("raw"), Mapping) else {}
    text = raw.get("text") or target.get("text") or ""
    if not isinstance(text, str) or not text.strip():
        return {}
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return {}
    if not isinstance(value, Mapping):
        return {}
    snapshot = dict(value)
    if _text(snapshot.get("schema")) != DOCS_SNAPSHOT_SCHEMA:
        return {}
    return snapshot


def _snapshot_inventory_text(
    snapshot: Mapping[str, Any],
    *,
    object_ref: str,
    target: Mapping[str, Any],
) -> str:
    obj = snapshot.get("object") if isinstance(snapshot.get("object"), Mapping) else {}
    comments = (
        snapshot.get("comments") if isinstance(snapshot.get("comments"), list) else []
    )
    materialization = (
        snapshot.get("materialization")
        if isinstance(snapshot.get("materialization"), Mapping)
        else {}
    )
    meta = target.get("meta") if isinstance(target.get("meta"), Mapping) else {}
    raw = target.get("raw") if isinstance(target.get("raw"), Mapping) else {}
    logical_path = _text(target.get("logical_path") or target.get("path"))
    physical_path = _text(
        target.get("physical_path")
        or meta.get("physical_path")
        or raw.get("physical_path")
    )
    body_text = obj.get("text") if isinstance(obj.get("text"), str) else ""
    preview = body_text[:BLOCK_PREVIEW_CHARS]
    tabs = [dict(tab) for tab in obj.get("tabs") or [] if isinstance(tab, Mapping)]
    lines = [
        "[DOCS SNAPSHOT]",
        f"object_ref: {object_ref}",
        f"object_kind: {_text(snapshot.get('object_kind')) or _text(obj.get('object_kind'))}",
        f"title: {_text(obj.get('title')) or '<untitled>'}",
        f"document_id: {_text(obj.get('document_id'))}",
    ]
    web_url = _text(obj.get("web_url"))
    if web_url:
        lines.append(f"web_url: {web_url}")
    revision_id = _text(obj.get("revision_id"))
    if revision_id:
        lines.append(f"revision_id: {revision_id}")
    if obj.get("conversion_required"):
        lines.extend(
            [
                f"provider_filename: {_text(obj.get('title'))}",
                f"logical_title: {_text(obj.get('logical_title'))}",
                f"source_format: {_text(obj.get('source_format'))}",
                f"mime_type: {_text(obj.get('mime_type'))}",
                "conversion_required: yes",
                "next_action: copy this source to a new native document before editing",
            ]
        )
    for label, key in (
        ("source_bytes", "source_bytes"),
        ("source_text_symbols", "source_text_symbols"),
        ("source_line_count", "source_line_count"),
    ):
        if meta.get(key) is not None:
            lines.append(f"{label}: {meta.get(key)}")
    lines.extend(
        [
            f"materialized_path: {logical_path}",
            f"physical_path: {physical_path or '<not exposed>'}",
            f"snapshot_schema: {_text(snapshot.get('schema'))}",
            f"word_count: {_word_count(body_text)}",
            f"char_count: {len(body_text)}",
            f"end_index: {_int(obj.get('end_index'))}",
            f"tab_count: {_int(obj.get('tab_count'), default=len(tabs))}",
            f"comment_count: {len(comments)}",
            f"text_materialized: {'yes' if body_text else 'no'}",
        ]
    )
    complete_text = materialization.get("complete_text")
    if complete_text is not None:
        lines.append(f"complete_text: {'yes' if complete_text else 'no'}")
    lines.append("tabs:")
    if not tabs:
        lines.append("- none reported")
    for tab in tab_candidates(tabs):
        title = _text(tab.get("title")) or "<untitled>"
        tab_id = _text(tab.get("tab_id")) or "<single-tab-default>"
        parent = _text(tab.get("parent_tab_id"))
        suffix = f"; parent_tab_id={parent}" if parent else ""
        hierarchy = " / ".join(tab.get("hierarchy") or [])
        lines.append(
            f"- position={tab.get('position')}; title={title}; hierarchy={hierarchy}; "
            f"tab_id={tab_id}; end_index={_int(tab.get('end_index'))}{suffix}"
        )
    if len(tabs) > 1:
        lines.append(
            "mutation_scope: use tab_selector with title, title_contains, "
            "position, or hierarchy; exact tab ids and all_tabs=true remain "
            "available for explicit calls"
        )
    lines.append("text_preview:")
    if preview:
        lines.append(preview)
        if len(body_text) > len(preview):
            lines.append("[preview truncated]")
    else:
        lines.append("- no body text")
    lines.extend(
        [
            "snapshot_layout:",
            "- document metadata and body text: object",
            "- comment threads: comments[]",
        ]
    )
    return "\n".join(lines)


async def _json_chunks(
    value: Any,
    *,
    chunk_bytes: int = 64 * 1024,
) -> AsyncIterator[bytes]:
    """Encode a JSON artifact incrementally without monopolizing the proc loop."""
    encoder = json.JSONEncoder(ensure_ascii=False, indent=2)
    pending = bytearray()
    for piece in encoder.iterencode(value):
        encoded = piece.encode("utf-8")
        offset = 0
        if pending:
            take = min(chunk_bytes - len(pending), len(encoded))
            pending.extend(encoded[:take])
            offset = take
            if len(pending) == chunk_bytes:
                yield bytes(pending)
                pending.clear()
                await asyncio.sleep(0)
        while len(encoded) - offset >= chunk_bytes:
            yield encoded[offset : offset + chunk_bytes]
            offset += chunk_bytes
            await asyncio.sleep(0)
        pending.extend(encoded[offset:])
    if pending:
        yield bytes(pending)


async def _bytes_chunks(
    value: bytes,
    *,
    chunk_bytes: int = 64 * 1024,
) -> AsyncIterator[bytes]:
    for offset in range(0, len(value), chunk_bytes):
        yield value[offset : offset + chunk_bytes]
        await asyncio.sleep(0)


@named_service_provider(
    provider_id=PROVIDER_ID,
    namespace=DOCS_NAMESPACE,
    refs=("docs:*",),
    object_kinds=(DOCS_DOCUMENT_KIND, DOCS_EXPORT_KIND, DOCS_IMAGE_KIND),
    search_scopes=DOCS_SEARCH_SCOPES,
    operations=_operations(),
    label="Documents",
    description="Provider-neutral document namespace over connected accounts.",
    intro=DOCS_INTRO,
    metadata=_spec_metadata(),
)
class DocsNamedServiceProvider(NamedServiceProvider):
    schema_projection_index = DOCS_SCHEMA_PROJECTION

    def __init__(
        self,
        *,
        execute_operation: ExecuteDocsOperation,
        bundle_id: str | None = None,
        file_url_factory: Any = None,
        staging_root_factory: Any = None,
        provider_fetch_url_factory: Any = None,
    ) -> None:
        super().__init__(docs_named_service_spec(bundle_id=bundle_id))
        self._execute_operation = execute_operation
        self._file_url_factory = file_url_factory
        self._staging_root_factory = staging_root_factory
        self._provider_fetch_url_factory = provider_fetch_url_factory

    def _provider_identity(self) -> dict[str, Any]:
        return {"provider_id": PROVIDER_ID, "bundle_id": self.spec.bundle_id}

    def schema_object_kind_from_ref(self, object_ref: str) -> str | None:
        for parser in (parse_docs_ref, parse_docs_export_ref, parse_docs_image_ref):
            try:
                return _text(parser(object_ref).get("object_kind")) or None
            except ValueError:
                continue
        return None

    async def _download_url(
        self,
        ctx: NamedServiceContext,
        *,
        ref: str,
    ) -> dict[str, Any] | None:
        if self._file_url_factory is None:
            return None
        try:
            out = self._file_url_factory(ctx, {"ref": ref})
            if hasattr(out, "__await__"):
                out = await out
        except Exception:
            LOGGER.exception("docs file url factory failed for %s", ref)
            return None
        return dict(out) if isinstance(out, Mapping) and out.get("url") else None

    def _invalid_ref(
        self, request: NamedServiceRequest, exc: Exception
    ) -> NamedServiceResponse:
        return NamedServiceResponse.error_response(
            code="invalid_docs_ref",
            message=str(exc),
            status=400,
            provider=self._provider_identity(),
            namespace=request.namespace or DOCS_NAMESPACE,
            object_ref=request.object_ref,
        )

    def _unsupported_include(
        self, request: NamedServiceRequest, exc: Exception
    ) -> NamedServiceResponse:
        return NamedServiceResponse.error_response(
            code="docs_include_unsupported",
            message=str(exc),
            status=400,
            provider=self._provider_identity(),
            namespace=request.namespace or DOCS_NAMESPACE,
            object_ref=request.object_ref,
        )

    async def _execute(
        self,
        *,
        request: NamedServiceRequest,
        operation: str,
        claim: str | Sequence[str],
        payload: Mapping[str, Any],
        account_id: str,
    ) -> tuple[dict[str, Any] | None, NamedServiceResponse | None]:
        tool_name = f"named_services.{DOCS_NAMESPACE}.{request.operation}"
        if request.action:
            tool_name = f"{tool_name}.{request.action}"
        result = await self._execute_operation(
            operation=operation,
            claim=claim,
            tool_name=tool_name,
            payload=dict(payload or {}),
            account_id=_text(account_id),
        )
        if not isinstance(result, Mapping) or not result.get("ok"):
            envelope = dict(result or {}) if isinstance(result, Mapping) else {}
            return None, tool_error_response(
                envelope,
                request=request,
                namespace=DOCS_NAMESPACE,
                provider_identity=self._provider_identity(),
                default_code="docs_operation_failed",
                fallback_message="The document operation failed.",
            )
        ret = result.get("ret")
        return (dict(ret or {}) if isinstance(ret, Mapping) else {}), None

    def _selector_error_response(
        self,
        request: NamedServiceRequest,
        error: DocsSelectorError,
    ) -> NamedServiceResponse:
        return NamedServiceResponse.error_response(
            code=error.code,
            message=str(error),
            status=error.status,
            details=error.details,
            provider=self._provider_identity(),
            namespace=request.namespace or DOCS_NAMESPACE,
            object_ref=request.object_ref,
        )

    def _tab_comment_scope_error(
        self,
        request: NamedServiceRequest,
    ) -> NamedServiceResponse:
        return NamedServiceResponse.error_response(
            code="tab_anchored_comments_unavailable",
            message=(
                "This provider manages comments at document scope. Remove the tab "
                "selector and identify the document-level comment instead."
            ),
            status=422,
            details={
                "supported_scope": "document",
                "next_action": (
                    "List document comments, then use comment_selector with text, "
                    "author, resolved state, or position."
                ),
            },
            provider=self._provider_identity(),
            namespace=request.namespace or DOCS_NAMESPACE,
            object_ref=request.object_ref,
        )

    @staticmethod
    def _requests_tab_scoped_comment(payload: Mapping[str, Any]) -> bool:
        tab_keys = {
            "tab_id",
            "tab_ids",
            "tab_selector",
            "tab_selectors",
            "tab_title",
            "tab_position",
        }
        if any(
            key in payload and payload.get(key) not in (None, "", [])
            for key in tab_keys
        ):
            return True
        selector = payload.get("comment_selector")
        return isinstance(selector, Mapping) and any(
            _text(key).startswith("tab_") or _text(key) == "tab" for key in selector
        )

    async def _resolve_tab_payload(
        self,
        *,
        request: NamedServiceRequest,
        parsed: Mapping[str, Any],
        payload: Mapping[str, Any],
        plural: bool,
    ) -> tuple[
        dict[str, Any] | None,
        dict[str, Any] | None,
        NamedServiceResponse | None,
    ]:
        resolved_payload = dict(payload or {})
        single_selector = resolved_payload.get("tab_selector")
        raw_plural = resolved_payload.get("tab_selectors")
        plural_selectors = (
            list(raw_plural)
            if isinstance(raw_plural, Sequence)
            and not isinstance(raw_plural, (str, bytes))
            else []
        )
        selectors = plural_selectors or (
            [single_selector] if single_selector not in (None, "") else []
        )
        if raw_plural not in (None, []) and not plural_selectors:
            error = DocsSelectorError(
                "docs_tab_selector_invalid",
                "tab_selectors must be a non-empty list of tab selectors.",
                status=400,
            )
            return None, None, self._selector_error_response(request, error)
        if not selectors:
            return resolved_payload, None, None

        if plural:
            if (
                resolved_payload.get("tab_ids")
                or resolved_payload.get("all_tabs") is True
            ):
                error = DocsSelectorError(
                    "docs_tab_selector_conflict",
                    "Use tab selectors, tab_ids, or all_tabs=true as one tab scope.",
                    status=400,
                )
                return None, None, self._selector_error_response(request, error)
        elif len(selectors) != 1 or resolved_payload.get("tab_id"):
            error = DocsSelectorError(
                "docs_tab_selector_conflict",
                "Use one tab_selector or one tab_id for a single-tab action.",
                status=400,
            )
            return None, None, self._selector_error_response(request, error)

        metadata, provider_error = await self._execute(
            request=request,
            operation="get",
            claim=DOCS_READ_CLAIM,
            payload={
                "document_ref": _text(parsed.get("document_id")),
                "include_text": False,
            },
            account_id=_text(parsed.get("account_id")),
        )
        if provider_error is not None:
            return None, None, provider_error
        tabs = [
            dict(tab)
            for tab in (metadata or {}).get("tabs") or []
            if isinstance(tab, Mapping)
        ]
        try:
            matches = [resolve_tab_selector(tabs, selector) for selector in selectors]
        except DocsSelectorError as error:
            return None, None, self._selector_error_response(request, error)

        unique_matches: list[dict[str, Any]] = []
        seen: set[str] = set()
        for match in matches:
            tab_id = _text(match.get("tab_id"))
            if tab_id not in seen:
                seen.add(tab_id)
                unique_matches.append(match)
        resolved_payload.pop("tab_selector", None)
        resolved_payload.pop("tab_selectors", None)
        if plural:
            resolved_payload["tab_ids"] = [
                _text(match.get("tab_id")) for match in unique_matches
            ]
        else:
            resolved_payload["tab_id"] = _text(unique_matches[0].get("tab_id"))
        return (
            resolved_payload,
            {
                "kind": "tab",
                "selectors": selectors,
                "matches": unique_matches,
            },
            None,
        )

    async def _resolve_comment_payload(
        self,
        *,
        request: NamedServiceRequest,
        parsed: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> tuple[
        dict[str, Any] | None,
        dict[str, Any] | None,
        NamedServiceResponse | None,
    ]:
        resolved_payload = dict(payload or {})
        comment_id = _text(resolved_payload.get("comment_id"))
        selector = resolved_payload.get("comment_selector")
        if comment_id and selector not in (None, ""):
            error = DocsSelectorError(
                "docs_comment_selector_conflict",
                "Use comment_id or comment_selector, not both.",
                status=400,
            )
            return None, None, self._selector_error_response(request, error)
        if comment_id or selector in (None, ""):
            return resolved_payload, None, None

        comments: list[dict[str, Any]] = []
        cursor = ""
        scanned_pages = 0
        scanned_cursors: set[str] = set()
        try:
            for _page in range(COMMENT_SELECTOR_MAX_PAGES):
                list_payload: dict[str, Any] = {
                    "document_ref": _text(parsed.get("document_id")),
                    "include_resolved": True,
                    "limit": COMMENT_SELECTOR_PAGE_SIZE,
                }
                if cursor:
                    list_payload["cursor"] = cursor
                page, provider_error = await self._execute(
                    request=request,
                    operation=ACTION_LIST_COMMENTS,
                    claim=DOCS_READ_CLAIM,
                    payload=list_payload,
                    account_id=_text(parsed.get("account_id")),
                )
                if provider_error is not None:
                    return None, None, provider_error
                scanned_pages += 1
                comments.extend(
                    dict(row)
                    for row in (page or {}).get("comments") or []
                    if isinstance(row, Mapping)
                )
                matches = matching_comments(comments, selector)
                if len(matches) > 1:
                    resolve_comment_selector(comments, selector)
                cursor = _text((page or {}).get("next_cursor"))
                if not cursor:
                    match = resolve_comment_selector(comments, selector)
                    resolved_payload.pop("comment_selector", None)
                    resolved_payload["comment_id"] = _text(match.get("comment_id"))
                    return (
                        resolved_payload,
                        {
                            "kind": "comment",
                            "selector": selector,
                            "match": match,
                            "scanned_pages": scanned_pages,
                            "scanned_comments": len(comments),
                        },
                        None,
                    )
                if cursor in scanned_cursors:
                    break
                scanned_cursors.add(cursor)
        except DocsSelectorError as error:
            return None, None, self._selector_error_response(request, error)

        matches = matching_comments(comments, selector)
        candidates = matches or comment_candidates(comments)
        error = DocsSelectorError(
            "docs_comment_selector_incomplete",
            "The bounded comment scan ended while more provider results remained.",
            status=409,
            details={
                "selector": selector,
                "scanned_pages": scanned_pages,
                "scanned_comments": len(comments),
                "candidate_count": len(candidates),
                "candidates": candidates[:SELECTOR_CANDIDATE_LIMIT],
                "candidates_truncated": len(candidates) > SELECTOR_CANDIDATE_LIMIT,
                "next_cursor": cursor,
                "next_action": "Narrow the comment selector and retry.",
            },
        )
        return None, None, self._selector_error_response(request, error)

    async def _export_reference_response(
        self,
        ctx: NamedServiceContext,
        request: NamedServiceRequest,
        *,
        parsed: Mapping[str, Any],
    ) -> NamedServiceResponse:
        metadata, error = await self._execute(
            request=request,
            operation="get",
            claim=DOCS_READ_CLAIM,
            payload={
                "document_ref": _text(parsed.get("document_id")),
                "include_text": False,
            },
            account_id=_text(parsed.get("account_id")),
        )
        if error is not None:
            return error
        obj = _export_object(
            parsed,
            title=(metadata or {}).get("title"),
        )
        url_info = await self._download_url(ctx, ref=obj["ref"])
        if url_info is not None:
            obj["download"] = {"encoding": "url", **url_info}
        else:
            obj["delivery"] = {
                "response_mode": "stream",
                "note": (
                    "Resolve this export ref with a streaming object.get to "
                    "receive the file bytes."
                ),
            }
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or DOCS_NAMESPACE,
            object_ref=obj["ref"],
            object=obj,
            extra={"action": ACTION_EXPORT, "source_document_ref": request.object_ref},
        )

    async def _image_uri_for_file_ref(
        self,
        ctx: NamedServiceContext,
        request: NamedServiceRequest,
        payload: MutableMapping[str, Any],
    ) -> tuple[Callable[[], None] | None, NamedServiceResponse | None]:
        """Turn a file_ref into an image_uri Google can fetch once.

        Returns a cleanup to run after the write, and a refusal when the file
        cannot become one. The URL is never put in the answer: it is a
        capability, and it stops existing when the cleanup runs.
        """

        from kdcube_ai_app.apps.chat.sdk.integrations.file_staging import (
            STAGED_REF_PREFIX,
            delete_staged,
            load_staged,
            new_staged_ref,
            save_staged,
        )
        from kdcube_ai_app.infra.service_hub.multimodality import validate_image_bytes

        file_ref = _text(payload.pop("file_ref", ""))
        if not file_ref:
            return None, None
        if _text(payload.get("image_uri")):
            return None, _image_refusal(
                request,
                code="docs_image_source_ambiguous",
                message=(
                    "Name either file_ref, for a file this deployment holds, or "
                    "image_uri, for a public URL - not both."
                ),
            )
        if self._staging_root_factory is None or self._provider_fetch_url_factory is None:
            return None, _image_refusal(
                request,
                code="docs_image_hosting_unavailable",
                message=(
                    "This deployment cannot hand Google a file of its own: it "
                    "serves no public origin for one. Pass image_uri with a "
                    "public URL instead."
                ),
                status=409,
            )
        try:
            root = self._staging_root_factory()
        except Exception:
            root = None
        if root is None:
            return None, _image_refusal(
                request,
                code="docs_image_hosting_unavailable",
                message="This deployment has no staging area configured.",
                status=409,
            )

        owned = False
        if file_ref.startswith(STAGED_REF_PREFIX):
            staged_ref = file_ref
            try:
                filename, data = load_staged(root, staged_ref)
            except (FileNotFoundError, ValueError) as exc:
                return None, _image_refusal(
                    request,
                    code="docs_image_file_missing",
                    message=str(exc),
                    status=404,
                    details={"file_ref": file_ref},
                )
        else:
            resolved, error = await self._conversation_file_bytes(ctx, request, file_ref)
            if error is not None:
                return None, error
            filename, data = resolved
            staged_ref = new_staged_ref(filename)
            owned = True

        measured = validate_image_bytes(data)
        media_type = _text(measured.get("media_type")) or _mime_for_image(measured)
        if not measured.get("valid") or media_type not in DOCS_IMAGE_MEDIA_TYPES:
            return None, _image_refusal(
                request,
                code="docs_image_unsupported",
                message=(
                    "Google embeds PNG, JPEG and GIF. This file reads as "
                    f"{media_type or 'something else'}."
                ),
                details={"file_ref": file_ref, "media_type": media_type},
            )
        if len(data) > MAX_DOCS_IMAGE_BYTES:
            return None, _image_refusal(
                request,
                code="docs_image_too_large",
                message=(
                    f"The image is {len(data)} bytes; Google accepts at most "
                    f"{MAX_DOCS_IMAGE_BYTES}."
                ),
                details={"file_ref": file_ref, "byte_size": len(data)},
            )
        width, height = measured.get("width") or 0, measured.get("height") or 0
        if max(width, height) > MAX_DOCS_IMAGE_PIXELS:
            return None, _image_refusal(
                request,
                code="docs_image_too_large",
                message=(
                    f"The image is {width}x{height} pixels; Google accepts at "
                    f"most {MAX_DOCS_IMAGE_PIXELS} per side."
                ),
                details={"file_ref": file_ref, "width": width, "height": height},
            )

        if owned:
            try:
                save_staged(root, staged_ref, data)
            except ValueError as exc:
                return None, _image_refusal(
                    request,
                    code="docs_image_too_large",
                    message=str(exc),
                    details={"file_ref": file_ref},
                )

        def _cleanup() -> None:
            try:
                delete_staged(root, staged_ref)
            except Exception:
                LOGGER.warning("docs staged image not removed: %s", staged_ref)

        try:
            minted = self._provider_fetch_url_factory(
                ctx, {"staged_ref": staged_ref, "filename": filename}
            )
            if hasattr(minted, "__await__"):
                minted = await minted
        except Exception:
            LOGGER.exception("docs provider fetch url factory failed")
            minted = None
        url = _text((minted or {}).get("url")) if isinstance(minted, Mapping) else ""
        if not url.startswith("https://"):
            _cleanup()
            return None, _image_refusal(
                request,
                code="docs_image_hosting_unavailable",
                message=(
                    "This deployment has no public https origin Google could "
                    "fetch the file from. Pass image_uri with a public URL "
                    "instead."
                ),
                status=409,
            )
        reachable = await self._origin_serves_the_file(url)
        if reachable is not None:
            _cleanup()
            return None, reachable(request)
        payload["image_uri"] = url
        return _cleanup, None

    async def _origin_serves_the_file(
        self, url: str
    ) -> Callable[[NamedServiceRequest], NamedServiceResponse] | None:
        """Check that the minted URL answers an anonymous fetch with the file.

        A provider fetches with no credential and a browser-like agent, and a
        tunnel or proxy in front of this deployment may answer such a request
        with a page of its own - a warning interstitial, a login screen - which
        the provider then reports as a broken image. Asking the same way the
        provider will turns that into a diagnosis of the deployment, which is
        what it actually is.

        Returns None when the origin serves the file, or a refusal builder.
        """

        import httpx

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    url,
                    follow_redirects=True,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140 Safari/537.36"
                        )
                    },
                )
        except Exception as exc:
            return origin_unreachable_refusal(f"{type(exc).__name__}: {exc}")

        media_type = _text(response.headers.get("content-type")).split(";")[0].lower()
        if response.status_code == 200 and media_type.startswith("image/"):
            return None
        return origin_answer_refusal(response.status_code, media_type)

    async def _conversation_file_bytes(
        self,
        ctx: NamedServiceContext,
        request: NamedServiceRequest,
        file_ref: str,
    ) -> tuple[tuple[str, bytes], None] | tuple[None, NamedServiceResponse]:
        """Bytes of a conversation artifact, or a refusal that says why not."""

        from kdcube_ai_app.apps.chat.sdk.integrations.inline_files import (
            InlineFileError,
            materialized_conversation_file_ref,
        )

        try:
            async with materialized_conversation_file_ref(
                file_ref,
                tenant=_text(getattr(ctx, "tenant", "")),
                project=_text(getattr(ctx, "project", "")),
                user_id=_text(getattr(ctx, "user_id", "")),
                conversation_id=_text(getattr(ctx, "conversation_id", "")),
            ) as report:
                path = pathlib.Path(_text(report.get("path")))
                return (path.name, path.read_bytes()), None
        except InlineFileError as exc:
            return None, _image_refusal(
                request,
                code="docs_image_file_missing",
                message=(
                    f"{exc} A file made in this turn becomes a conversation "
                    "artifact only once it is delivered; stage it and pass the "
                    "staged ref instead."
                ),
                status=404,
                details={"file_ref": file_ref},
            )
        except Exception as exc:
            return None, _image_refusal(
                request,
                code="docs_image_file_missing",
                message=f"This file could not be read: {exc}",
                status=404,
                details={"file_ref": file_ref},
            )


    async def _image_response(
        self,
        ctx: NamedServiceContext,
        request: NamedServiceRequest,
        *,
        parsed: Mapping[str, Any],
        materialize: bool,
    ) -> NamedServiceResponse | NamedServiceStreamResult:
        """An image ref: its bytes when a client can take them, its record otherwise."""

        ret, error = await self._execute(
            request=request,
            operation="read_image",
            claim=DOCS_READ_CLAIM,
            payload={
                "document_ref": _text(parsed.get("document_id")),
                "object_id": _text(parsed.get("object_id")),
            },
            account_id=_text(parsed.get("account_id")),
        )
        if error is not None:
            return error
        ret = ret or {}
        obj = _image_object(parsed, described=ret)
        if not materialize:
            url_info = await self._download_url(ctx, ref=obj["ref"])
            if url_info is not None:
                obj["download"] = {"encoding": "url", **url_info}
            else:
                obj["delivery"] = {
                    "response_mode": "stream",
                    "note": (
                        "Resolve this image ref with a streaming object.get to "
                        "receive the image bytes."
                    ),
                }
            return NamedServiceResponse.ok_response(
                provider=self._provider_identity(),
                namespace=request.namespace or DOCS_NAMESPACE,
                object_ref=obj["ref"],
                object=obj,
                extra={"object_kind": DOCS_IMAGE_KIND},
            )
        encoded = _text(ret.get("content_base64"))
        try:
            data = base64.b64decode(encoded, validate=True) if encoded else b""
        except (ValueError, TypeError) as exc:
            return NamedServiceResponse.error_response(
                code="docs_image_payload_invalid",
                message="The document provider returned invalid image bytes.",
                status=502,
                details={"error": str(exc)},
                provider=self._provider_identity(),
                namespace=request.namespace or DOCS_NAMESPACE,
                object_ref=request.object_ref,
            )
        if not data:
            return NamedServiceResponse.error_response(
                code="docs_image_payload_missing",
                message="The document provider returned no image bytes.",
                status=502,
                provider=self._provider_identity(),
                namespace=request.namespace or DOCS_NAMESPACE,
                object_ref=request.object_ref,
            )
        response = NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or DOCS_NAMESPACE,
            object_ref=obj["ref"],
            object=obj,
            attrs={
                "materialization": {
                    "media_type": obj.get("mime_type"),
                    "filename": obj.get("filename"),
                    "size_bytes": len(data),
                    "complete": True,
                }
            },
        )
        return NamedServiceStreamResult(
            response=response,
            chunks=_bytes_chunks(data),
            filename=_text(obj.get("filename")),
            media_type=_text(obj.get("mime_type")),
        )

    async def _materialize_export(
        self,
        *,
        request: NamedServiceRequest,
        parsed: Mapping[str, Any],
    ) -> NamedServiceResponse | NamedServiceStreamResult:
        metadata, error = await self._execute(
            request=request,
            operation="get",
            claim=DOCS_READ_CLAIM,
            payload={
                "document_ref": _text(parsed.get("document_id")),
                "include_text": False,
            },
            account_id=_text(parsed.get("account_id")),
        )
        if error is not None:
            return error
        exported, error = await self._execute(
            request=request,
            operation=ACTION_EXPORT,
            claim=DOCS_READ_CLAIM,
            payload={
                "document_ref": _text(parsed.get("document_id")),
                "format": _text(parsed.get("format")),
            },
            account_id=_text(parsed.get("account_id")),
        )
        if error is not None:
            return error
        encoded = _text((exported or {}).get("content_base64"))
        if not encoded:
            return NamedServiceResponse.error_response(
                code="docs_export_payload_missing",
                message="The document provider returned no export bytes.",
                status=502,
                provider=self._provider_identity(),
                namespace=request.namespace or DOCS_NAMESPACE,
                object_ref=request.object_ref,
            )
        try:
            data = base64.b64decode(
                encoded,
                validate=True,
            )
        except (ValueError, TypeError) as exc:
            return NamedServiceResponse.error_response(
                code="docs_export_payload_invalid",
                message="The document provider returned invalid export bytes.",
                status=502,
                details={"error": str(exc)},
                provider=self._provider_identity(),
                namespace=request.namespace or DOCS_NAMESPACE,
                object_ref=request.object_ref,
            )
        obj = _export_object(
            parsed,
            title=(metadata or {}).get("title"),
            byte_size=len(data),
        )
        response = NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or DOCS_NAMESPACE,
            object_ref=obj["ref"],
            object=obj,
            attrs={
                "materialization": {
                    "media_type": obj["mime_type"],
                    "filename": obj["filename"],
                    "size_bytes": len(data),
                    "complete": True,
                }
            },
        )
        return NamedServiceStreamResult(
            response=response,
            chunks=_bytes_chunks(data),
            filename=obj["filename"],
            media_type=obj["mime_type"],
        )

    def _provider_not_supported(
        self,
        request: NamedServiceRequest,
        parsed: Mapping[str, Any],
    ) -> NamedServiceResponse | None:
        provider = _text(parsed.get("provider"))
        if provider == GOOGLE_PROVIDER_KEY:
            return None
        return NamedServiceResponse.error_response(
            code="docs_provider_not_implemented",
            message=f"Document provider is not implemented: {provider}",
            status=501,
            provider=self._provider_identity(),
            namespace=request.namespace or DOCS_NAMESPACE,
            object_ref=request.object_ref,
        )

    async def provider_about(
        self, ctx: NamedServiceContext, request: NamedServiceRequest
    ) -> NamedServiceResponse:
        del ctx
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or DOCS_NAMESPACE,
            extra={
                "title": "KDCube Documents",
                "description": (
                    "Provider-neutral document namespace. Google Docs is the "
                    "first connected-account provider."
                ),
                "workflow": [
                    (
                        "Call object.search to find a document by title; exact "
                        "title matches are returned first."
                    ),
                    "Call object.get with its ref to read metadata and body text.",
                    (
                        "For multi-tab documents, address a tab naturally by exact "
                        "title, title fragment, 1-based position, or hierarchy."
                    ),
                    (
                        "object.get lists the document's tables with ready "
                        "selectors; pass filters.tables to read cells, then "
                        "object.action set_cells to write one row's cells by "
                        "column name or number."
                    ),
                    (
                        "To clone a document, search for the target title first, "
                        "then call object.action copy on the source ref."
                    ),
                    "Call object.upsert or a declared object.action for bounded changes.",
                    (
                        "When the user needs a file, call object.action export; "
                        "the returned export ref is delivered or streamed out of band."
                    ),
                    (
                        "Use document-level comment actions with comment_id or a "
                        "selector over text, author, resolved state, or position."
                    ),
                ],
                "providers": DOCS_PROVIDER_CATALOG,
                "schema": DOCS_SCHEMA,
            },
        )

    async def provider_capabilities(
        self, ctx: NamedServiceContext, request: NamedServiceRequest
    ) -> NamedServiceResponse:
        del ctx
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or DOCS_NAMESPACE,
            capabilities={
                "list": True,
                "search": True,
                "get": True,
                "upsert": True,
                "delete": "comments_only",
                "actions": list(DOCS_ACTIONS),
                "providers": DOCS_PROVIDER_CATALOG,
                "grant_hints": DOCS_GRANT_HINTS,
                "connected_account_claims": DOCS_SCHEMA["connected_account_claims"],
            },
        )

    async def object_schema(
        self, ctx: NamedServiceContext, request: NamedServiceRequest
    ) -> NamedServiceResponse:
        del ctx
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or DOCS_NAMESPACE,
            extra={"schema": DOCS_SCHEMA},
        )

    async def event_resolve(
        self, ctx: NamedServiceContext, request: NamedServiceRequest
    ) -> NamedServiceResponse:
        del ctx
        try:
            parsed = parse_docs_ref(request.object_ref)
        except ValueError as exc:
            return self._invalid_ref(request, exc)
        unsupported = self._provider_not_supported(request, parsed)
        if unsupported is not None:
            return unsupported
        canonical_ref = (
            document_source_ref(parsed["account_id"], parsed["document_id"])
            if parsed["object_kind"] == DOCS_IMPORT_SOURCE_KIND
            else document_ref(parsed["account_id"], parsed["document_id"])
        )
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or DOCS_NAMESPACE,
            object_ref=canonical_ref,
            extra={
                "event_source_id": f"named_services.{DOCS_NAMESPACE}",
                "object_ref": canonical_ref,
                "target_surface": "sdk.docs.snapshot",
            },
        )

    async def block_produce(
        self, ctx: NamedServiceContext, request: NamedServiceRequest
    ) -> NamedServiceResponse:
        target_value = request.payload.get("target")
        target = dict(target_value) if isinstance(target_value, Mapping) else {}
        object_ref = _text(
            request.object_ref or target.get("object_ref") or target.get("ref")
        )
        try:
            parsed = parse_docs_ref(object_ref)
        except ValueError as exc:
            return self._invalid_ref(request, exc)
        unsupported = self._provider_not_supported(request, parsed)
        if unsupported is not None:
            return unsupported

        snapshot = _snapshot_from_block_target(target)
        if not snapshot:
            source_object = parsed["object_kind"] == DOCS_IMPORT_SOURCE_KIND
            metadata, error = await self._execute(
                request=request,
                operation="get_source" if source_object else "get",
                claim=DOCS_READ_CLAIM,
                payload={"document_ref": parsed["document_id"], "include_text": True},
                account_id=parsed["account_id"],
            )
            if error is not None:
                return NamedServiceResponse.ok_response(
                    provider=self._provider_identity(),
                    namespace=request.namespace or DOCS_NAMESPACE,
                    object_ref=object_ref,
                    extra={"blocks": []},
                    warnings=[
                        {
                            "code": "docs_block_produce_get_failed",
                            "message": (
                                error.error.message
                                if error.error is not None
                                else "Document metadata could not be loaded."
                            ),
                        }
                    ],
                )
            obj = _document_object(
                metadata or {},
                account_id=_text((metadata or {}).get("account_id"))
                or parsed["account_id"],
                provider=parsed["provider"],
            )
            snapshot = {
                "schema": DOCS_SNAPSHOT_SCHEMA,
                "object_ref": object_ref,
                "object_kind": obj["object_kind"],
                "object": obj,
                "comments": [],
                "materialization": {
                    "text_materialized": bool(obj.get("text")),
                    "complete_text": False if source_object else None,
                    "inventory_source": "provider_metadata_fallback",
                },
            }

        text = _snapshot_inventory_text(
            snapshot,
            object_ref=object_ref,
            target=target,
        )
        meta = target.get("meta") if isinstance(target.get("meta"), Mapping) else {}
        source_stats = {
            key: meta.get(key)
            for key in (
                "source_tokens",
                "source_text_symbols",
                "source_bytes",
                "source_line_count",
            )
            if meta.get(key) is not None
        }
        snapshot_object = (
            snapshot.get("object")
            if isinstance(snapshot.get("object"), Mapping)
            else {}
        )
        body_text = (
            snapshot_object.get("text")
            if isinstance(snapshot_object.get("text"), str)
            else ""
        )
        snapshot_comments = (
            snapshot.get("comments")
            if isinstance(snapshot.get("comments"), list)
            else []
        )
        source_stats.update(
            {
                "object_ref": object_ref,
                "object_kind": _text(snapshot.get("object_kind")),
                "snapshot_schema": _text(snapshot.get("schema")),
                "title": _text(snapshot_object.get("title")),
                "document_id": _text(snapshot_object.get("document_id")),
                "word_count": _word_count(body_text),
                "char_count": len(body_text),
                "comment_count": len(snapshot_comments),
            }
        )
        source_stats = {
            key: value for key, value in source_stats.items() if value is not None
        }
        block = {
            "turn": target.get("turn_id") or ctx.turn_id or "",
            "type": "react.tool.result",
            "call_id": target.get("tool_call_id") or "",
            "tool_id": "named_services.docs",
            "event_source_id": f"named_services.{DOCS_NAMESPACE}",
            "mime": "text/markdown",
            "path": object_ref,
            "text": text,
            "original_object_stats": source_stats,
            "meta": {
                "tool_call_id": target.get("tool_call_id") or "",
                "tool_id": target.get("tool_id") or "react.read",
                "turn_id": target.get("turn_id") or ctx.turn_id or "",
                "object_ref": object_ref,
                "source_namespace": DOCS_NAMESPACE,
                "materialized_path": target.get("logical_path")
                or target.get("path")
                or "",
                "physical_path": target.get("physical_path")
                or meta.get("physical_path")
                or "",
                "object_kind": _text(snapshot.get("object_kind")),
                "mime": DOCS_SNAPSHOT_MEDIA_TYPE,
                "render_policy": "docs.named_service.block_produce",
            },
        }
        LOGGER.info(
            "[docs.named_service.block_produce] produced object_ref=%s "
            "materialized_path=%s text_symbols=%s",
            object_ref,
            target.get("logical_path") or target.get("path") or "",
            len(text),
        )
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or DOCS_NAMESPACE,
            object_ref=object_ref,
            extra={"blocks": [block]},
        )

    async def object_list(
        self, ctx: NamedServiceContext, request: NamedServiceRequest
    ) -> NamedServiceResponse:
        del ctx
        filters = dict(request.filters or {})
        return await self._search(
            request=request,
            query="",
            account_id=_text(
                filters.get("account_id") or request.payload.get("account_id")
            ),
        )

    async def object_search(
        self, ctx: NamedServiceContext, request: NamedServiceRequest
    ) -> NamedServiceResponse:
        del ctx
        filters = dict(request.filters or {})
        return await self._search(
            request=request,
            query=_text(request.query),
            account_id=_text(
                filters.get("account_id") or request.payload.get("account_id")
            ),
        )

    async def _search(
        self,
        *,
        request: NamedServiceRequest,
        query: str,
        account_id: str,
    ) -> NamedServiceResponse:
        filters = dict(request.filters or {})
        ret, error = await self._execute(
            request=request,
            operation="search",
            claim=DOCS_READ_CLAIM,
            payload={
                "query": query,
                "limit": _int(request.limit, default=20, minimum=1, maximum=50),
                "cursor": _text(request.cursor or filters.get("cursor")),
            },
            account_id=account_id,
        )
        if error is not None:
            return error
        ret = ret or {}
        resolved_account_id = _text(ret.get("account_id") or account_id)
        items = [
            _document_object(row, account_id=resolved_account_id)
            for row in ret.get("items") or []
            if isinstance(row, Mapping)
        ]
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or DOCS_NAMESPACE,
            items=items,
            next_cursor=_text(ret.get("next_cursor")) or None,
            extra={
                "count": len(items),
                "query": query,
                "account_id": resolved_account_id,
                "exact_match_count": _int(ret.get("exact_match_count")),
                "incomplete_search": bool(ret.get("incomplete_search")),
                "match_mode": _text(ret.get("match_mode")),
            },
        )

    async def object_get(
        self, ctx: NamedServiceContext, request: NamedServiceRequest
    ) -> NamedServiceResponse | NamedServiceStreamResult:
        try:
            image_parsed = parse_docs_image_ref(request.object_ref)
        except ValueError:
            image_parsed = None
        if image_parsed is not None:
            unsupported = self._provider_not_supported(request, image_parsed)
            if unsupported is not None:
                return unsupported
            return await self._image_response(
                ctx,
                request,
                parsed=image_parsed,
                materialize=_is_materialization_request(request),
            )
        try:
            export_parsed = parse_docs_export_ref(request.object_ref)
        except ValueError:
            export_parsed = None
        if export_parsed is not None:
            unsupported = self._provider_not_supported(request, export_parsed)
            if unsupported is not None:
                return unsupported
            if _is_materialization_request(request):
                return await self._materialize_export(
                    request=request,
                    parsed=export_parsed,
                )
            return await self._export_reference_response(
                ctx,
                request,
                parsed=export_parsed,
            )
        try:
            parsed = parse_docs_ref(request.object_ref)
        except ValueError as exc:
            return self._invalid_ref(request, exc)
        unsupported = self._provider_not_supported(request, parsed)
        if unsupported is not None:
            return unsupported
        import_source = parsed["object_kind"] == DOCS_IMPORT_SOURCE_KIND
        if _is_materialization_request(request):
            return await self._materialize_snapshot(request=request, parsed=parsed)
        filters = dict(request.filters or {})
        include_text = filters.get("include_text")
        if include_text is None:
            include_text = request.payload.get("include_text")
        payload: dict[str, Any] = {"document_ref": parsed["document_id"]}
        if include_text is not None:
            payload["include_text"] = bool(include_text)
        table_filter = filters.get("tables")
        try:
            wants_cells = _wants_table_cells(request.include)
        except ValueError as exc:
            return self._unsupported_include(request, exc)
        if table_filter in (None, "", []) and wants_cells:
            table_filter = "all"
        if table_filter not in (None, "", []) and not import_source:
            payload["tables"] = table_filter
        ret, error = await self._execute(
            request=request,
            operation="get_source" if import_source else "get",
            claim=DOCS_READ_CLAIM,
            payload=payload,
            account_id=parsed["account_id"],
        )
        if error is not None:
            return error
        obj = _document_object(
            ret or {},
            account_id=parsed["account_id"],
            provider=parsed["provider"],
        )
        if not ctx.turn_id and not import_source:
            url_info = await self._download_url(ctx, ref=obj["ref"])
            if url_info is not None:
                snapshot_download = {
                    "schema": DOCS_SNAPSHOT_SCHEMA,
                    "media_type": DOCS_SNAPSHOT_MEDIA_TYPE,
                    "filename": _snapshot_filename(parsed["document_id"]),
                    "download": {"encoding": "url", **url_info},
                }
                # Put the complete-artifact escape hatch before potentially
                # large inline body text in serialized MCP responses.
                obj = {"snapshot": snapshot_download, **obj}
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or DOCS_NAMESPACE,
            object_ref=obj["ref"],
            object=obj,
        )

    async def object_resolve(
        self, ctx: NamedServiceContext, request: NamedServiceRequest
    ) -> NamedServiceResponse:
        del ctx
        action = _text(request.action or "capabilities").lower()
        if action not in {"capabilities", "describe"}:
            return NamedServiceResponse.error_response(
                code="docs_resolve_action_not_supported",
                message=f"Unsupported document resolve action: {action}.",
                status=400,
                provider=self._provider_identity(),
                namespace=request.namespace or DOCS_NAMESPACE,
                object_ref=request.object_ref,
            )
        try:
            parsed = parse_docs_ref(request.object_ref)
        except ValueError:
            try:
                export_parsed = parse_docs_export_ref(request.object_ref)
            except ValueError as exc:
                return self._invalid_ref(request, exc)
            unsupported = self._provider_not_supported(request, export_parsed)
            if unsupported is not None:
                return unsupported
            can_download = self._file_url_factory is not None
            return NamedServiceResponse.ok_response(
                provider=self._provider_identity(),
                namespace=request.namespace or DOCS_NAMESPACE,
                object_ref=request.object_ref,
                capabilities={
                    "preview": False,
                    "open": False,
                    "download": can_download,
                    "rehost": False,
                },
                extra={
                    "object_kind": DOCS_EXPORT_KIND,
                    **(
                        {"default_open_effect_action": UI_ACTION_DOWNLOAD}
                        if can_download
                        else {}
                    ),
                },
            )
        unsupported = self._provider_not_supported(request, parsed)
        if unsupported is not None:
            return unsupported
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or DOCS_NAMESPACE,
            object_ref=request.object_ref,
            capabilities={
                "preview": False,
                "open": True,
                "download": False,
                "rehost": False,
            },
            extra={
                "object_kind": parsed["object_kind"],
                "default_open_effect_action": UI_ACTION_OPEN,
            },
        )

    async def _open_document(
        self, request: NamedServiceRequest
    ) -> NamedServiceResponse:
        try:
            parsed = parse_docs_ref(request.object_ref)
        except ValueError as exc:
            return self._invalid_ref(request, exc)
        unsupported = self._provider_not_supported(request, parsed)
        if unsupported is not None:
            return unsupported
        import_source = parsed["object_kind"] == DOCS_IMPORT_SOURCE_KIND
        ret, error = await self._execute(
            request=request,
            operation="get_source" if import_source else "get",
            claim=DOCS_READ_CLAIM,
            payload={"document_ref": parsed["document_id"], "include_text": False},
            account_id=parsed["account_id"],
        )
        if error is not None:
            return error
        obj = _document_object(
            ret or {},
            account_id=parsed["account_id"],
            provider=parsed["provider"],
        )
        external_url = _text(obj.get("web_url"))
        if not external_url:
            return NamedServiceResponse.error_response(
                code="docs_open_url_unavailable",
                message="The document provider did not return a browser URL.",
                status=409,
                provider=self._provider_identity(),
                namespace=request.namespace or DOCS_NAMESPACE,
                object_ref=request.object_ref,
            )
        capabilities = {
            "preview": False,
            "open": True,
            "download": False,
            "rehost": False,
        }
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or DOCS_NAMESPACE,
            object_ref=obj["ref"],
            object=obj,
            capabilities=capabilities,
            ui_event={
                "type": "kdcube.ui.object.open.requested",
                "action": UI_ACTION_OPEN,
                "object_ref": obj["ref"],
                "external_url": external_url,
                "title": _text(obj.get("title")),
            },
            extra={
                "action": UI_ACTION_OPEN,
                "object_kind": obj["object_kind"],
                "default_open_effect_action": UI_ACTION_OPEN,
                "external_url": external_url,
                "title": _text(obj.get("title")),
            },
        )

    async def _download_export(
        self, ctx: NamedServiceContext, request: NamedServiceRequest
    ) -> NamedServiceResponse:
        try:
            parsed = parse_docs_export_ref(request.object_ref)
        except ValueError as exc:
            return self._invalid_ref(request, exc)
        response = await self._export_reference_response(ctx, request, parsed=parsed)
        if not response.ok:
            return response
        obj = response.object
        download = obj.get("download") if isinstance(obj.get("download"), Mapping) else {}
        download_url = _text(download.get("url"))
        if not download_url:
            return NamedServiceResponse.error_response(
                code="docs_download_url_unavailable",
                message="This client must stream the export ref to download it.",
                status=409,
                provider=self._provider_identity(),
                namespace=request.namespace or DOCS_NAMESPACE,
                object_ref=request.object_ref,
            )
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or DOCS_NAMESPACE,
            object_ref=request.object_ref,
            object=obj,
            capabilities={
                "preview": False,
                "open": False,
                "download": True,
                "rehost": False,
            },
            extra={
                "action": UI_ACTION_DOWNLOAD,
                "object_kind": DOCS_EXPORT_KIND,
                "default_open_effect_action": UI_ACTION_DOWNLOAD,
                "download_url": download_url,
                "filename": _text(obj.get("filename")),
                "mime": _text(obj.get("mime_type")),
            },
        )

    async def _materialize_snapshot(
        self,
        *,
        request: NamedServiceRequest,
        parsed: Mapping[str, Any],
    ) -> NamedServiceResponse | NamedServiceStreamResult:
        document_id = _text(parsed.get("document_id"))
        import_source = parsed.get("object_kind") == DOCS_IMPORT_SOURCE_KIND
        metadata, error = await self._execute(
            request=request,
            operation="get_source" if import_source else "get",
            claim=DOCS_READ_CLAIM,
            payload={
                "document_ref": document_id,
                "include_text": True,
                **({} if import_source else {"include_table_cells": True}),
            },
            account_id=_text(parsed.get("account_id")),
        )
        if error is not None:
            return error
        metadata = metadata or {}
        account_id = _text(metadata.get("account_id") or parsed.get("account_id"))
        obj = _document_object(
            metadata,
            account_id=account_id,
            provider=_text(parsed.get("provider")) or GOOGLE_PROVIDER_KEY,
        )

        comments: list[dict[str, Any]] = []
        comments_complete = not import_source
        if not import_source:
            comment_result, comment_error = await self._execute(
                request=request,
                operation="list_comments",
                claim=DOCS_READ_CLAIM,
                payload={"document_ref": document_id, "include_resolved": False},
                account_id=account_id,
            )
            if comment_error is not None:
                comments_complete = False
            else:
                comments = [
                    dict(row)
                    for row in (comment_result or {}).get("comments") or []
                    if isinstance(row, Mapping)
                ]

        object_ref = _text(obj.get("ref") or request.object_ref)
        body_text = obj.get("text") if isinstance(obj.get("text"), str) else ""
        snapshot = {
            "schema": DOCS_SNAPSHOT_SCHEMA,
            "object_ref": object_ref,
            "object_kind": obj["object_kind"],
            "object": obj,
            "comments": comments,
            "materialization": {
                "text_materialized": bool(body_text),
                "complete_text": not import_source,
                "comments_materialized": comments_complete,
                "comment_count": len(comments),
                "word_count": _word_count(body_text),
                "char_count": len(body_text),
                "delivery": (
                    "This import source carries file metadata. Copy it to a native "
                    "document before reading or editing body text."
                    if import_source
                    else "The complete document body text is included. The comment "
                    "actions remain available for full comment-thread reads."
                ),
            },
        }
        response = NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or DOCS_NAMESPACE,
            object_ref=object_ref,
            attrs={
                "materialization": {
                    "schema": DOCS_SNAPSHOT_SCHEMA,
                    "media_type": DOCS_SNAPSHOT_MEDIA_TYPE,
                    "word_count": _word_count(body_text),
                    "char_count": len(body_text),
                    "comment_count": len(comments),
                    "complete_text": not import_source,
                }
            },
        )
        return NamedServiceStreamResult(
            response=response,
            chunks=_json_chunks(snapshot),
            filename=_snapshot_filename(document_id),
            media_type=DOCS_SNAPSHOT_MEDIA_TYPE,
        )

    async def object_upsert(
        self, ctx: NamedServiceContext, request: NamedServiceRequest
    ) -> NamedServiceResponse:
        del ctx
        body = {**dict(request.payload or {}), **dict(request.object or {})}
        selector_resolution: dict[str, Any] | None = None
        if not request.object_ref:
            operation = "create"
            payload = {
                key: body.get(key)
                for key in ("title", "initial_text")
                if body.get(key) is not None
            }
            if request.idempotency_key:
                payload["idempotency_key"] = request.idempotency_key
            account_id = _text(body.get("account_id"))
            parsed = None
        else:
            try:
                parsed = parse_docs_ref(request.object_ref)
            except ValueError as exc:
                return self._invalid_ref(request, exc)
            unsupported = self._provider_not_supported(request, parsed)
            if unsupported is not None:
                return unsupported
            if parsed["object_kind"] == DOCS_IMPORT_SOURCE_KIND:
                return NamedServiceResponse.error_response(
                    code="docs_import_source_requires_copy",
                    message=(
                        "This object is an import source. Copy it to a native "
                        "document, then edit the returned document ref."
                    ),
                    status=409,
                    provider=self._provider_identity(),
                    namespace=request.namespace or DOCS_NAMESPACE,
                    object_ref=request.object_ref,
                )
            account_id = parsed["account_id"]
            if body.get("replacements") is not None:
                operation = "replace_text"
                payload = {
                    "document_ref": parsed["document_id"],
                    "replacements": body.get("replacements"),
                    "tab_ids": body.get("tab_ids"),
                    "tab_selector": body.get("tab_selector"),
                    "tab_selectors": body.get("tab_selectors"),
                    "all_tabs": body.get("all_tabs"),
                }
            elif body.get("index") is not None:
                operation = "insert_text"
                payload = {
                    "document_ref": parsed["document_id"],
                    "text": body.get("text"),
                    "index": body.get("index"),
                    "tab_id": body.get("tab_id"),
                    "tab_selector": body.get("tab_selector"),
                }
            else:
                operation = "append_text"
                payload = {
                    "document_ref": parsed["document_id"],
                    "text": body.get("text"),
                    "tab_id": body.get("tab_id"),
                    "tab_selector": body.get("tab_selector"),
                }
            if request.idempotency_key:
                payload["idempotency_key"] = request.idempotency_key
            payload, selector_resolution, selector_error = (
                await self._resolve_tab_payload(
                    request=request,
                    parsed=parsed,
                    payload=payload,
                    plural=operation == ACTION_REPLACE_TEXT,
                )
            )
            if selector_error is not None:
                return selector_error
            assert payload is not None
        ret, error = await self._execute(
            request=request,
            operation=operation,
            claim=(DOCS_READ_CLAIM, DOCS_WRITE_CLAIM),
            payload=payload,
            account_id=account_id,
        )
        if error is not None:
            return error
        return self._mutation_response(
            request=request,
            ret=ret or {},
            parsed=parsed,
            selector_resolution=selector_resolution,
        )

    async def object_action(
        self, ctx: NamedServiceContext, request: NamedServiceRequest
    ) -> NamedServiceResponse:
        action = _text(request.action)
        if action == UI_ACTION_OPEN:
            return await self._open_document(request)
        if action == UI_ACTION_DOWNLOAD:
            return await self._download_export(ctx, request)
        if action not in DOCS_ACTIONS:
            return NamedServiceResponse.error_response(
                code="docs_action_not_supported",
                message=f"Unsupported document action: {action or '<missing>'}.",
                status=400,
                provider=self._provider_identity(),
                namespace=request.namespace or DOCS_NAMESPACE,
                object_ref=request.object_ref,
            )
        # import creates a new document; it takes no existing document ref.
        if action == ACTION_IMPORT:
            body = {**dict(request.payload or {}), **dict(request.object or {})}
            payload = {
                key: body.get(key)
                for key in (
                    "title",
                    "source_format",
                    "content",
                    "content_base64",
                    "parent_id",
                )
                if body.get(key) is not None
            }
            if request.idempotency_key:
                payload["idempotency_key"] = request.idempotency_key
            ret, error = await self._execute(
                request=request,
                operation=ACTION_IMPORT,
                claim=_placement_claim(ACTION_IMPORT, payload),
                payload=payload,
                account_id=_text(body.get("account_id")),
            )
            if error is not None:
                return error
            return self._mutation_response(request=request, ret=ret or {}, parsed=None)
        # The drive-file verbs also take no document ref: upload creates a
        # Drive file as itself, list_folder reads a folder. Neither result is
        # a document object, so they answer with the raw operation result.
        if action in (ACTION_UPLOAD_FILE, ACTION_LIST_FOLDER):
            body = {**dict(request.payload or {}), **dict(request.object or {})}
            keys = (
                ("name", "content_base64", "mime_type", "parent_id")
                if action == ACTION_UPLOAD_FILE
                else ("folder_id", "limit", "cursor")
            )
            payload = {
                key: body.get(key) for key in keys if body.get(key) is not None
            }
            if action == ACTION_UPLOAD_FILE and request.idempotency_key:
                payload["idempotency_key"] = request.idempotency_key
            ret, error = await self._execute(
                request=request,
                operation=(
                    "drive_upload" if action == ACTION_UPLOAD_FILE else "drive_list"
                ),
                claim=_action_claim(action),
                payload=payload,
                account_id=_text(body.get("account_id")),
            )
            if error is not None:
                return error
            return NamedServiceResponse.ok_response(
                provider=self._provider_identity(),
                namespace=request.namespace or DOCS_NAMESPACE,
                object_ref=request.object_ref,
                extra={"action": action, "result": dict(ret or {})},
            )

        try:
            parsed = parse_docs_ref(request.object_ref)
        except ValueError as exc:
            return self._invalid_ref(request, exc)
        unsupported = self._provider_not_supported(request, parsed)
        if unsupported is not None:
            return unsupported
        if parsed["object_kind"] == DOCS_IMPORT_SOURCE_KIND and action != ACTION_COPY:
            return NamedServiceResponse.error_response(
                code="docs_import_source_requires_copy",
                message=(
                    "This object is an import source. Its only document action is "
                    "copy, which creates and returns an editable native document."
                ),
                status=409,
                provider=self._provider_identity(),
                namespace=request.namespace or DOCS_NAMESPACE,
                object_ref=request.object_ref,
            )
        if action == ACTION_EXPORT:
            try:
                ref = document_export_ref(
                    parsed["account_id"],
                    parsed["document_id"],
                    request.payload.get("format"),
                )
                export_parsed = parse_docs_export_ref(ref)
            except ValueError as exc:
                return NamedServiceResponse.error_response(
                    code="docs_export_format_invalid",
                    message=str(exc),
                    status=400,
                    provider=self._provider_identity(),
                    namespace=request.namespace or DOCS_NAMESPACE,
                    object_ref=request.object_ref,
                )
            return await self._export_reference_response(
                ctx,
                request,
                parsed=export_parsed,
            )
        payload = dict(request.payload or {})
        if action in (ACTION_SET_CELLS, ACTION_ADD_ROW):
            payload = spread_table_selector(payload)
        payload["document_ref"] = parsed["document_id"]
        if request.idempotency_key:
            payload["idempotency_key"] = request.idempotency_key
        selector_resolution: dict[str, Any] | None = None
        if (
            action in DOCS_DOCUMENT_COMMENT_ACTIONS
            and self._requests_tab_scoped_comment(payload)
        ):
            return self._tab_comment_scope_error(request)
        if action in DOCS_SINGLE_TAB_ACTIONS:
            payload, selector_resolution, selector_error = (
                await self._resolve_tab_payload(
                    request=request,
                    parsed=parsed,
                    payload=payload,
                    plural=False,
                )
            )
            if selector_error is not None:
                return selector_error
        elif action == ACTION_REPLACE_TEXT:
            payload, selector_resolution, selector_error = (
                await self._resolve_tab_payload(
                    request=request,
                    parsed=parsed,
                    payload=payload,
                    plural=True,
                )
            )
            if selector_error is not None:
                return selector_error
        elif action in DOCS_COMMENT_REFERENCE_ACTIONS:
            payload, selector_resolution, selector_error = (
                await self._resolve_comment_payload(
                    request=request,
                    parsed=parsed,
                    payload=payload,
                )
            )
            if selector_error is not None:
                return selector_error
        assert payload is not None
        staged_cleanup: Callable[[], None] | None = None
        if action == ACTION_EMBED_IMAGE:
            staged_cleanup, staging_error = await self._image_uri_for_file_ref(
                ctx, request, payload
            )
            if staging_error is not None:
                return staging_error
        try:
            ret, error = await self._execute(
                request=request,
                operation=action,
                claim=_placement_claim(action, payload),
                payload=payload,
                account_id=parsed["account_id"],
            )
        finally:
            # Google fetches the image inside the call it was given, so the
            # staged copy has no reason to outlive the call - on success or on
            # failure.
            if staged_cleanup is not None:
                staged_cleanup()
        if error is not None:
            return error
        return self._mutation_response(
            request=request,
            ret=ret or {},
            parsed=parsed,
            selector_resolution=selector_resolution,
        )

    async def object_delete(
        self, ctx: NamedServiceContext, request: NamedServiceRequest
    ) -> NamedServiceResponse:
        del ctx
        try:
            parsed = parse_docs_ref(request.object_ref)
        except ValueError as exc:
            return self._invalid_ref(request, exc)
        unsupported = self._provider_not_supported(request, parsed)
        if unsupported is not None:
            return unsupported
        if parsed["object_kind"] == DOCS_IMPORT_SOURCE_KIND:
            return NamedServiceResponse.error_response(
                code="docs_import_source_requires_copy",
                message=(
                    "This object is an import source. Copy it to a native document "
                    "before using document or comment mutations."
                ),
                status=409,
                provider=self._provider_identity(),
                namespace=request.namespace or DOCS_NAMESPACE,
                object_ref=request.object_ref,
            )
        payload = {**dict(request.payload or {}), **dict(request.object or {})}
        payload["document_ref"] = parsed["document_id"]
        if self._requests_tab_scoped_comment(payload):
            return self._tab_comment_scope_error(request)
        payload, selector_resolution, selector_error = (
            await self._resolve_comment_payload(
                request=request,
                parsed=parsed,
                payload=payload,
            )
        )
        if selector_error is not None:
            return selector_error
        assert payload is not None
        if not _text(payload.get("comment_id")):
            return NamedServiceResponse.error_response(
                code="docs_document_delete_not_supported",
                message=(
                    "object.delete removes one document comment. Pass "
                    "payload.comment_id or comment_selector. To remove the "
                    "document itself, use object.action trash, which moves it to "
                    "the Drive trash and needs the docs:delete claim."
                ),
                status=400,
                provider=self._provider_identity(),
                namespace=request.namespace or DOCS_NAMESPACE,
                object_ref=request.object_ref,
            )
        ret, error = await self._execute(
            request=request,
            operation=ACTION_DELETE_COMMENT,
            claim=(DOCS_READ_CLAIM, DOCS_COMMENT_CLAIM),
            payload=payload,
            account_id=parsed["account_id"],
        )
        if error is not None:
            return error
        return self._mutation_response(
            request=request,
            ret=ret or {},
            parsed=parsed,
            selector_resolution=selector_resolution,
        )

    def _mutation_response(
        self,
        *,
        request: NamedServiceRequest,
        ret: Mapping[str, Any],
        parsed: Mapping[str, Any] | None,
        selector_resolution: Mapping[str, Any] | None = None,
    ) -> NamedServiceResponse:
        result = dict(ret or {})
        account_id = _text(result.get("account_id")) or _text(
            (parsed or {}).get("account_id")
        )
        provider = _text((parsed or {}).get("provider")) or GOOGLE_PROVIDER_KEY
        obj = _document_object(result, account_id=account_id, provider=provider)
        extra = {"action": request.action or request.operation, "result": result}
        if selector_resolution:
            extra["selector_resolution"] = dict(selector_resolution)
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or DOCS_NAMESPACE,
            object_ref=obj["ref"],
            object=obj,
            extra=extra,
        )


def make_docs_named_service_provider(
    *,
    execute_operation: ExecuteDocsOperation,
    bundle_id: str | None = None,
    file_url_factory: Any = None,
    staging_root_factory: Any = None,
    provider_fetch_url_factory: Any = None,
) -> DocsNamedServiceProvider:
    return DocsNamedServiceProvider(
        execute_operation=execute_operation,
        bundle_id=bundle_id,
        file_url_factory=file_url_factory,
        staging_root_factory=staging_root_factory,
        provider_fetch_url_factory=provider_fetch_url_factory,
    )


__all__ = [
    "ACTION_COPY",
    "ACTION_APPEND_TEXT",
    "ACTION_APPLY_TEXT_STYLE",
    "ACTION_CREATE_COMMENT",
    "ACTION_DELETE_COMMENT",
    "ACTION_EMBED_IMAGE",
    "ACTION_EXPORT",
    "ACTION_GET_COMMENT",
    "ACTION_IMPORT",
    "ACTION_INSERT_PAGE_BREAK",
    "ACTION_INSERT_TEXT",
    "ACTION_LIST_COMMENTS",
    "ACTION_REPLACE_TEXT",
    "ACTION_REPLY_COMMENT",
    "ACTION_UPDATE_COMMENT",
    "ACTION_RESOLVE_COMMENT",
    "ACTION_SET_CELLS",
    "ACTION_ADD_ROW",
    "ACTION_ADD_TAB",
    "ACTION_UPDATE_TAB",
    "ACTION_DELETE_TAB",
    "ACTION_TRASH",
    "ACTION_RESTORE",
    "DOCS_ACTIONS",
    "DOCS_COMMENT_CLAIM",
    "DOCS_DELETE_CLAIM",
    "DOCS_CONNECTED_ACCOUNT_REQUIREMENTS",
    "DOCS_DOCUMENT_KIND",
    "DOCS_EXPORT_FORMATS",
    "DOCS_EXPORT_KIND",
    "DOCS_IMAGE_KIND",
    "DOCS_GRANT_HINTS",
    "DOCS_IMPORT_SOURCE_KIND",
    "DOCS_NAMESPACE",
    "DOCS_READ_CLAIM",
    "DOCS_SCHEMA",
    "DOCS_SNAPSHOT_MEDIA_TYPE",
    "DOCS_SNAPSHOT_SCHEMA",
    "DOCS_WRITE_CLAIM",
    "DocsNamedServiceProvider",
    "GOOGLE_PROVIDER_KEY",
    "document_export_filename",
    "document_export_ref",
    "document_ref",
    "document_source_ref",
    "docs_named_service_spec",
    "make_docs_named_service_provider",
    "document_image_filename",
    "document_image_ref",
    "parse_docs_export_ref",
    "parse_docs_image_ref",
    "parse_docs_ref",
]
