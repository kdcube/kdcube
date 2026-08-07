# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Google Docs tool declarations for the productivity MCP surface.

Mirrors ``productivity_sheets``: each tool declares the connected-account
claims it needs (``DOCS_PRODUCTIVITY_TOOLS``) and, at execution time, enforces
them via the shared ``enforce_tool`` callback before running the governed
``GoogleDocsService``. The Annotated parameter descriptions teach the natural
chains: search -> get -> edit; list_comments -> reply/resolve.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from pydantic import Field

from kdcube_ai_app.apps.chat.sdk.solutions.connections.mcp_metadata import (
    destructive_annotations,
    read_only_annotations,
    write_annotations,
)

from ...services.productivity.google_docs import (
    DOCS_COMMENT_CLAIM,
    DOCS_READ_CLAIM,
    DOCS_WRITE_CLAIM,
)


EnforceTool = Callable[[str, str, str], Awaitable[dict[str, Any] | None]]

# The three claim bundles a Docs tool can require, in the SAME shape
# application tool configs use (ToolClaimPolicy.from_tool_config). ``claims``
# speak the PROVIDER's claim vocabulary - what a connected Google account row
# can hold. Write and comment tools require read as well: an edit that cannot
# first read the document is not a useful grant.
_READ_CLAIMS = [DOCS_READ_CLAIM]
_WRITE_CLAIMS = [DOCS_READ_CLAIM, DOCS_WRITE_CLAIM]
_COMMENT_CLAIMS = [DOCS_READ_CLAIM, DOCS_COMMENT_CLAIM]


def _requirement(claims: list[str]) -> dict[str, Any]:
    return {
        "connections": {
            "delegated_to_kdcube": {
                "connected_accounts": [
                    {"provider_id": "google", "claims": list(claims)},
                ],
            },
        },
    }


DOCS_PRODUCTIVITY_TOOLS: dict[str, dict[str, Any]] = {
    # ── flexible / tab-aware (evaluation surface, side by side with the
    #    narrow typed tools; graph-faithful read + bounded native batch edit) ──
    "productivity_docs_get_structure": {
        "label": "Read Google Doc structure",
        "description": "Read the document's structural graph and tabs with element indices.",
        **_requirement(_READ_CLAIMS),
    },
    "productivity_docs_list_tabs": {
        "label": "List Google Doc tabs",
        "description": "List a document's tabs without returning body content.",
        **_requirement(_READ_CLAIMS),
    },
    "productivity_docs_batch_edit": {
        "label": "Batch-edit Google Doc",
        "description": "Apply bounded native Docs edits with explicit tab scope.",
        **_requirement(_WRITE_CLAIMS),
    },
    # ── read (docs:read) ──────────────────────────────────────────────────
    "productivity_docs_search": {
        "label": "Find Google Docs",
        "description": (
            "Find native Google Docs and compatible document import sources "
            "visible to the user's connected Google account."
        ),
        **_requirement(_READ_CLAIMS),
    },
    "productivity_docs_get": {
        "label": "Read Google Doc",
        "description": "Read a document's title, text, full tab inventory, and revision.",
        **_requirement(_READ_CLAIMS),
    },
    "productivity_docs_export": {
        "label": "Export Google Doc",
        "description": "Export a document to PDF, DOCX, or another format.",
        **_requirement(_READ_CLAIMS),
    },
    "productivity_docs_list_comments": {
        "label": "List Google Doc comments",
        "description": "List comments on a document with their replies.",
        **_requirement(_READ_CLAIMS),
    },
    "productivity_docs_get_comment": {
        "label": "Read Google Doc comment",
        "description": "Read one comment thread on a document.",
        **_requirement(_READ_CLAIMS),
    },
    # ── write (docs:write) ────────────────────────────────────────────────
    "productivity_docs_create": {
        "label": "Create Google Doc",
        "description": "Create a document, optionally with initial text.",
        **_requirement(_WRITE_CLAIMS),
    },
    "productivity_docs_copy": {
        "label": "Copy Google Doc",
        "description": (
            "Copy a native document, or convert a DOCX, ODT, or RTF source "
            "into a new native Google Doc."
        ),
        **_requirement(_WRITE_CLAIMS),
    },
    "productivity_docs_insert_text": {
        "label": "Insert Google Doc text",
        "description": "Insert text at an index in a selected document tab.",
        **_requirement(_WRITE_CLAIMS),
    },
    "productivity_docs_append_text": {
        "label": "Append Google Doc text",
        "description": "Append text to the end of a selected document tab.",
        **_requirement(_WRITE_CLAIMS),
    },
    "productivity_docs_replace_text": {
        "label": "Replace Google Doc text",
        "description": "Replace text in selected tabs or explicitly in every tab.",
        **_requirement(_WRITE_CLAIMS),
    },
    "productivity_docs_apply_text_style": {
        "label": "Style Google Doc text",
        "description": "Apply bold, italic, size, or link styling to a text range.",
        **_requirement(_WRITE_CLAIMS),
    },
    "productivity_docs_insert_page_break": {
        "label": "Insert Google Doc page break",
        "description": "Insert a page break at an index in a document.",
        **_requirement(_WRITE_CLAIMS),
    },
    "productivity_docs_embed_image": {
        "label": "Embed Google Doc image",
        "description": "Embed an inline image from a public URL into a document.",
        **_requirement(_WRITE_CLAIMS),
    },
    "productivity_docs_import": {
        "label": "Import Google Doc",
        "description": "Import Markdown, HTML, or another source into a new document.",
        **_requirement(_WRITE_CLAIMS),
    },
    # ── comment (docs:comment) ────────────────────────────────────────────
    "productivity_docs_create_comment": {
        "label": "Comment on Google Doc",
        "description": "Create a comment on a document.",
        **_requirement(_COMMENT_CLAIMS),
    },
    "productivity_docs_reply_comment": {
        "label": "Reply to Google Doc comment",
        "description": "Reply to an existing comment thread on a document.",
        **_requirement(_COMMENT_CLAIMS),
    },
    "productivity_docs_resolve_comment": {
        "label": "Resolve Google Doc comment",
        "description": "Resolve a comment thread on a document.",
        **_requirement(_COMMENT_CLAIMS),
    },
    "productivity_docs_delete_comment": {
        "label": "Delete Google Doc comment",
        "description": "Delete a comment thread from a document.",
        **_requirement(_COMMENT_CLAIMS),
    },
}


def register_google_docs_tools(
    mcp: Any,
    *,
    service: Any,
    enforce_tool: EnforceTool,
) -> None:
    """Register Google Docs read tools, bounded edits, and comment tools.

    ``service`` is a governed ``GoogleDocsService`` (the SAME implementation the
    named-services door uses, so credential resolution and Google execution have
    one code path for both doors). ``enforce_tool(tool_name, operation)`` runs
    ``_prepare`` + ``enforce_tool_requirements`` and returns a consent-denial
    envelope, or ``None`` when the call may proceed.
    """
    from mcp.types import ToolAnnotations

    docs = service
    _enforce = enforce_tool

    # ── read (docs:read) ──────────────────────────────────────────────────

    @mcp.tool(
        name="productivity_docs_search",
        title="Find Google Docs",
        description=(
            "Find documents visible to the approving user's connected Google "
            "account. Results include native Google Docs and compatible DOCX, ODT, "
            "or RTF import sources. A non-blank query checks the requested title and "
            "logical filename first, so '26_006' can exactly match '26_006.docx', "
            "then returns title-prefix matches. Check exact_title_match, "
            "native_document, and conversion_required before acting. Read native "
            "documents with productivity_docs_get; copy an import source to create "
            "an editable native document. Search uses Drive metadata and does not "
            "inspect tabs; get the chosen document before editing it. A blank query "
            "lists recent results."
        ),
        annotations=read_only_annotations(ToolAnnotations, title="Find Google Docs"),
        structured_output=False,
    )
    async def _productivity_docs_search(
        query: Annotated[
            str,
            Field(description="Optional document title or title prefix."),
        ] = "",
        limit: Annotated[
            int,
            Field(ge=1, le=50, description="Maximum document results, 1-50."),
        ] = 20,
        cursor: Annotated[
            str,
            Field(description="Optional next_cursor returned by an earlier search."),
        ] = "",
        account_id: Annotated[
            str,
            Field(
                description="Optional connected Google account id when several are available."
            ),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_docs_search", "search", account_id)
        if denial is not None:
            return denial
        return await docs.execute(
            operation="search",
            claim=_READ_CLAIMS,
            tool_name="productivity_docs_search",
            payload={"query": query, "limit": limit, "cursor": cursor},
            account_id=account_id,
        )

    @mcp.tool(
        name="productivity_docs_get",
        title="Read Google Doc",
        description=(
            "Read a document by id or full Google Docs URL. Use search first when "
            "the id is unknown. Returns extracted text with tab markers, tab_count, "
            "and each tab's tab_id, title, hierarchy, and end_index. Read all tabs "
            "when useful. Before editing a multi-tab document, choose the intended "
            "tab; ambiguous writes are rejected with the available tabs."
        ),
        annotations=read_only_annotations(ToolAnnotations, title="Read Google Doc"),
        structured_output=False,
    )
    async def _productivity_docs_get(
        document_ref: Annotated[
            str,
            Field(description="Document id or full Google Docs URL."),
        ],
        include_text: Annotated[
            bool,
            Field(description="Include the extracted plain-text body in the result."),
        ] = True,
        account_id: Annotated[
            str,
            Field(
                description="Optional connected Google account id when several are available."
            ),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_docs_get", "get", account_id)
        if denial is not None:
            return denial
        return await docs.execute(
            operation="get",
            claim=_READ_CLAIMS,
            tool_name="productivity_docs_get",
            payload={"document_ref": document_ref, "include_text": include_text},
            account_id=account_id,
        )

    @mcp.tool(
        name="productivity_docs_export",
        title="Export Google Doc",
        description=(
            "Export a document to a portable format. Returns base64-encoded bytes "
            "with the resolved mime type and extension. Use productivity_docs_get "
            "first when you only need the text; export is for producing a file."
        ),
        annotations=read_only_annotations(ToolAnnotations, title="Export Google Doc"),
        structured_output=False,
    )
    async def _productivity_docs_export(
        document_ref: Annotated[
            str,
            Field(description="Document id or full Google Docs URL."),
        ],
        format: Annotated[
            str,
            Field(
                description=(
                    "Export format: pdf, docx, odt, rtf, txt, html, epub, or "
                    "markdown."
                )
            ),
        ] = "pdf",
        account_id: Annotated[
            str,
            Field(
                description="Optional connected Google account id when several are available."
            ),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_docs_export", "export", account_id)
        if denial is not None:
            return denial
        return await docs.execute(
            operation="export",
            claim=_READ_CLAIMS,
            tool_name="productivity_docs_export",
            payload={"document_ref": document_ref, "format": format},
            account_id=account_id,
        )

    @mcp.tool(
        name="productivity_docs_list_comments",
        title="List Google Doc comments",
        description=(
            "List comments on a document, newest first, each with its replies. "
            "Resolved threads are hidden unless include_resolved is set. Pass a "
            "returned comment_id to productivity_docs_reply_comment or "
            "productivity_docs_resolve_comment."
        ),
        annotations=read_only_annotations(
            ToolAnnotations, title="List Google Doc comments"
        ),
        structured_output=False,
    )
    async def _productivity_docs_list_comments(
        document_ref: Annotated[
            str,
            Field(description="Document id or full Google Docs URL."),
        ],
        limit: Annotated[
            int,
            Field(ge=1, le=100, description="Maximum comments to return, 1-100."),
        ] = 50,
        include_resolved: Annotated[
            bool,
            Field(description="Include resolved comment threads."),
        ] = False,
        cursor: Annotated[
            str,
            Field(description="Optional next_cursor returned by an earlier call."),
        ] = "",
        account_id: Annotated[
            str,
            Field(
                description="Optional connected Google account id when several are available."
            ),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_docs_list_comments", "list_comments", account_id)
        if denial is not None:
            return denial
        return await docs.execute(
            operation="list_comments",
            claim=_READ_CLAIMS,
            tool_name="productivity_docs_list_comments",
            payload={
                "document_ref": document_ref,
                "limit": limit,
                "include_resolved": include_resolved,
                "cursor": cursor,
            },
            account_id=account_id,
        )

    @mcp.tool(
        name="productivity_docs_get_comment",
        title="Read Google Doc comment",
        description=(
            "Read one comment thread by id, including its quoted text and replies. "
            "Use productivity_docs_list_comments first to discover comment ids."
        ),
        annotations=read_only_annotations(
            ToolAnnotations, title="Read Google Doc comment"
        ),
        structured_output=False,
    )
    async def _productivity_docs_get_comment(
        document_ref: Annotated[
            str,
            Field(description="Document id or full Google Docs URL."),
        ],
        comment_id: Annotated[
            str,
            Field(
                description="Comment id returned by productivity_docs_list_comments."
            ),
        ],
        account_id: Annotated[
            str,
            Field(
                description="Optional connected Google account id when several are available."
            ),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_docs_get_comment", "get_comment", account_id)
        if denial is not None:
            return denial
        return await docs.execute(
            operation="get_comment",
            claim=_READ_CLAIMS,
            tool_name="productivity_docs_get_comment",
            payload={"document_ref": document_ref, "comment_id": comment_id},
            account_id=account_id,
        )

    # ── write (docs:write) ────────────────────────────────────────────────

    @mcp.tool(
        name="productivity_docs_create",
        title="Create Google Doc",
        description=(
            "Create a document in the connected Google account, optionally seeding "
            "it with initial text. Returns the stable document_id and web_url; pass "
            "the id to the edit tools to keep building the document."
        ),
        annotations=write_annotations(ToolAnnotations, title="Create Google Doc"),
        structured_output=False,
    )
    async def _productivity_docs_create(
        title: Annotated[
            str,
            Field(min_length=1, max_length=300, description="New document title."),
        ],
        initial_text: Annotated[
            str,
            Field(description="Optional initial body text inserted at the top."),
        ] = "",
        idempotency_key: Annotated[
            str,
            Field(
                description=(
                    "Optional caller correlation key returned with the result. It "
                    "does not make Google's create operation exactly-once."
                )
            ),
        ] = "",
        account_id: Annotated[
            str,
            Field(
                description="Optional connected Google account id when several are available."
            ),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_docs_create", "create", account_id)
        if denial is not None:
            return denial
        return await docs.execute(
            operation="create",
            claim=_WRITE_CLAIMS,
            tool_name="productivity_docs_create",
            payload={
                "title": title,
                "initial_text": initial_text,
                "idempotency_key": idempotency_key,
            },
            account_id=account_id,
        )

    @mcp.tool(
        name="productivity_docs_copy",
        title="Copy Google Doc",
        description=(
            "Copy a native Google Doc under a new title, or convert a compatible "
            "DOCX, ODT, or RTF search result into a new native Google Doc. The "
            "source stays unchanged. Search for both the source and intended target "
            "title first. A provider timeout may leave the outcome unknown, so "
            "search for the target before retrying. Returns the new native "
            "document_id and web_url."
        ),
        annotations=write_annotations(ToolAnnotations, title="Copy Google Doc"),
        structured_output=False,
    )
    async def _productivity_docs_copy(
        document_ref: Annotated[
            str,
            Field(
                description=(
                    "Source id or URL returned by document search. It may identify "
                    "a native Google Doc or a compatible import source."
                )
            ),
        ],
        title: Annotated[
            str,
            Field(min_length=1, max_length=300, description="New document title."),
        ],
        parent_id: Annotated[
            str,
            Field(
                description=(
                    "Optional Drive folder id for the copy. Omit it to let Google "
                    "use the source/default placement."
                )
            ),
        ] = "",
        idempotency_key: Annotated[
            str,
            Field(
                description=(
                    "Optional caller correlation key returned with the result. It "
                    "does not make Google's copy operation exactly-once."
                )
            ),
        ] = "",
        account_id: Annotated[
            str,
            Field(
                description="Optional connected Google account id when several are available."
            ),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_docs_copy", "copy", account_id)
        if denial is not None:
            return denial
        return await docs.execute(
            operation="copy",
            claim=_WRITE_CLAIMS,
            tool_name="productivity_docs_copy",
            payload={
                "document_ref": document_ref,
                "title": title,
                "parent_id": parent_id,
                "idempotency_key": idempotency_key,
            },
            account_id=account_id,
        )

    @mcp.tool(
        name="productivity_docs_insert_text",
        title="Insert Google Doc text",
        description=(
            "Insert text at a body index. Omit index to insert at the end of the "
            "selected tab. Use productivity_docs_get first for tab_id, end_index, or the "
            "coordinates around the text you want to edit. This changes the "
            "document each time it succeeds. tab_id is required for a multi-tab "
            "document."
        ),
        annotations=write_annotations(
            ToolAnnotations, title="Insert Google Doc text"
        ),
        structured_output=False,
    )
    async def _productivity_docs_insert_text(
        document_ref: Annotated[
            str,
            Field(description="Document id or full Google Docs URL."),
        ],
        text: Annotated[
            str,
            Field(min_length=1, description="Text to insert."),
        ],
        index: Annotated[
            int | None,
            Field(ge=1, description="Optional 1-based body index; omit to append."),
        ] = None,
        tab_id: Annotated[
            str,
            Field(
                description=(
                    "Target tab_id returned by productivity_docs_get. Required "
                    "when the document has multiple tabs."
                )
            ),
        ] = "",
        account_id: Annotated[
            str,
            Field(
                description="Optional connected Google account id when several are available."
            ),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_docs_insert_text", "insert", account_id)
        if denial is not None:
            return denial
        return await docs.execute(
            operation="insert_text",
            claim=_WRITE_CLAIMS,
            tool_name="productivity_docs_insert_text",
            payload={
                "document_ref": document_ref,
                "text": text,
                "index": index,
                "tab_id": tab_id,
            },
            account_id=account_id,
        )

    @mcp.tool(
        name="productivity_docs_append_text",
        title="Append Google Doc text",
        description=(
            "Append text to the end of the selected tab. This changes the "
            "document each time it succeeds; repeating the call appends again "
            "rather than replacing. tab_id is required for a multi-tab document."
        ),
        annotations=write_annotations(
            ToolAnnotations, title="Append Google Doc text"
        ),
        structured_output=False,
    )
    async def _productivity_docs_append_text(
        document_ref: Annotated[
            str,
            Field(description="Document id or full Google Docs URL."),
        ],
        text: Annotated[
            str,
            Field(min_length=1, description="Text to append at the end of the body."),
        ],
        tab_id: Annotated[
            str,
            Field(
                description=(
                    "Target tab_id returned by productivity_docs_get. Required "
                    "when the document has multiple tabs."
                )
            ),
        ] = "",
        account_id: Annotated[
            str,
            Field(
                description="Optional connected Google account id when several are available."
            ),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_docs_append_text", "append", account_id)
        if denial is not None:
            return denial
        return await docs.execute(
            operation="append_text",
            claim=_WRITE_CLAIMS,
            tool_name="productivity_docs_append_text",
            payload={
                "document_ref": document_ref,
                "text": text,
                "tab_id": tab_id,
            },
            account_id=account_id,
        )

    @mcp.tool(
        name="productivity_docs_replace_text",
        title="Replace Google Doc text",
        description=(
            "Replace every occurrence of each find string in selected tabs. "
            "Each replacement is {find, replace, match_case?}. Use "
            "productivity_docs_get first to confirm the text and tab ids. For a "
            "multi-tab document, pass tab_ids or explicitly set all_tabs=true. "
            "Returns occurrences_changed so you can verify what was matched."
        ),
        annotations=write_annotations(
            ToolAnnotations, title="Replace Google Doc text"
        ),
        structured_output=False,
    )
    async def _productivity_docs_replace_text(
        document_ref: Annotated[
            str,
            Field(description="Document id or full Google Docs URL."),
        ],
        replacements: Annotated[
            list[dict[str, Any]],
            Field(
                min_length=1,
                max_length=50,
                description=(
                    'One to fifty objects shaped {find: "old", replace: "new", '
                    "match_case?: bool}."
                ),
            ),
        ],
        tab_ids: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Tabs in which to replace text. Use ids returned by "
                    "productivity_docs_get."
                )
            ),
        ] = None,
        all_tabs: Annotated[
            bool,
            Field(
                description=(
                    "Explicitly replace across every tab. Do not combine with tab_ids."
                )
            ),
        ] = False,
        account_id: Annotated[
            str,
            Field(
                description="Optional connected Google account id when several are available."
            ),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_docs_replace_text", "replace", account_id)
        if denial is not None:
            return denial
        return await docs.execute(
            operation="replace_text",
            claim=_WRITE_CLAIMS,
            tool_name="productivity_docs_replace_text",
            payload={
                "document_ref": document_ref,
                "replacements": replacements,
                "tab_ids": tab_ids,
                "all_tabs": all_tabs,
            },
            account_id=account_id,
        )

    @mcp.tool(
        name="productivity_docs_apply_text_style",
        title="Style Google Doc text",
        description=(
            "Apply character styling to a text range. Provide start_index and "
            "end_index and tab_id from productivity_docs_get plus at least one of bold, "
            "italic, underline, strikethrough, font_size, or link_url."
        ),
        annotations=write_annotations(
            ToolAnnotations, title="Style Google Doc text"
        ),
        structured_output=False,
    )
    async def _productivity_docs_apply_text_style(
        document_ref: Annotated[
            str,
            Field(description="Document id or full Google Docs URL."),
        ],
        start_index: Annotated[
            int,
            Field(ge=1, description="1-based start of the range to style."),
        ],
        end_index: Annotated[
            int,
            Field(ge=2, description="End of the range, greater than start_index."),
        ],
        bold: Annotated[bool | None, Field(description="Optional bold setting.")] = None,
        italic: Annotated[
            bool | None, Field(description="Optional italic setting.")
        ] = None,
        underline: Annotated[
            bool | None, Field(description="Optional underline setting.")
        ] = None,
        strikethrough: Annotated[
            bool | None, Field(description="Optional strikethrough setting.")
        ] = None,
        font_size: Annotated[
            int | None,
            Field(ge=6, le=96, description="Optional font size in points, 6-96."),
        ] = None,
        link_url: Annotated[
            str,
            Field(description="Optional http(s) URL to link the range to."),
        ] = "",
        tab_id: Annotated[
            str,
            Field(
                description=(
                    "Target tab_id returned by productivity_docs_get. Required "
                    "when the document has multiple tabs."
                )
            ),
        ] = "",
        account_id: Annotated[
            str,
            Field(
                description="Optional connected Google account id when several are available."
            ),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_docs_apply_text_style", "format", account_id)
        if denial is not None:
            return denial
        return await docs.execute(
            operation="apply_text_style",
            claim=_WRITE_CLAIMS,
            tool_name="productivity_docs_apply_text_style",
            payload={
                "document_ref": document_ref,
                "start_index": start_index,
                "end_index": end_index,
                "bold": bold,
                "italic": italic,
                "underline": underline,
                "strikethrough": strikethrough,
                "font_size": font_size,
                "link_url": link_url,
                "tab_id": tab_id,
            },
            account_id=account_id,
        )

    @mcp.tool(
        name="productivity_docs_insert_page_break",
        title="Insert Google Doc page break",
        description=(
            "Insert a page break at a body index. Omit index to break at the end "
            "of the selected tab. Use productivity_docs_get for tab_id and the "
            "coordinates."
        ),
        annotations=write_annotations(
            ToolAnnotations, title="Insert Google Doc page break"
        ),
        structured_output=False,
    )
    async def _productivity_docs_insert_page_break(
        document_ref: Annotated[
            str,
            Field(description="Document id or full Google Docs URL."),
        ],
        index: Annotated[
            int | None,
            Field(ge=1, description="Optional 1-based body index; omit to append."),
        ] = None,
        tab_id: Annotated[
            str,
            Field(
                description=(
                    "Target tab_id returned by productivity_docs_get. Required "
                    "when the document has multiple tabs."
                )
            ),
        ] = "",
        account_id: Annotated[
            str,
            Field(
                description="Optional connected Google account id when several are available."
            ),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce(
            "productivity_docs_insert_page_break", "insert", account_id
        )
        if denial is not None:
            return denial
        return await docs.execute(
            operation="insert_page_break",
            claim=_WRITE_CLAIMS,
            tool_name="productivity_docs_insert_page_break",
            payload={
                "document_ref": document_ref,
                "index": index,
                "tab_id": tab_id,
            },
            account_id=account_id,
        )

    @mcp.tool(
        name="productivity_docs_embed_image",
        title="Embed Google Doc image",
        description=(
            "Embed an inline image at a body index. image_uri must be a public "
            "http(s) URL that Google fetches once at insert time (PNG/JPEG/GIF, "
            "<=25MB, <=2000px per side). Omit index to append at the selected "
            "tab's end."
        ),
        annotations=write_annotations(
            ToolAnnotations, title="Embed Google Doc image"
        ),
        structured_output=False,
    )
    async def _productivity_docs_embed_image(
        document_ref: Annotated[
            str,
            Field(description="Document id or full Google Docs URL."),
        ],
        image_uri: Annotated[
            str,
            Field(description="Public http(s) URL of the image to embed."),
        ],
        index: Annotated[
            int | None,
            Field(ge=1, description="Optional 1-based body index; omit to append."),
        ] = None,
        width_pt: Annotated[
            int | None,
            Field(ge=1, description="Optional display width in points."),
        ] = None,
        height_pt: Annotated[
            int | None,
            Field(ge=1, description="Optional display height in points."),
        ] = None,
        tab_id: Annotated[
            str,
            Field(
                description=(
                    "Target tab_id returned by productivity_docs_get. Required "
                    "when the document has multiple tabs."
                )
            ),
        ] = "",
        account_id: Annotated[
            str,
            Field(
                description="Optional connected Google account id when several are available."
            ),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_docs_embed_image", "insert", account_id)
        if denial is not None:
            return denial
        return await docs.execute(
            operation="embed_image",
            claim=_WRITE_CLAIMS,
            tool_name="productivity_docs_embed_image",
            payload={
                "document_ref": document_ref,
                "image_uri": image_uri,
                "index": index,
                "width_pt": width_pt,
                "height_pt": height_pt,
                "tab_id": tab_id,
            },
            account_id=account_id,
        )

    @mcp.tool(
        name="productivity_docs_import",
        title="Import Google Doc",
        description=(
            "Create a new document by converting a source into Google Docs format. "
            "Provide content (text) or content_base64 (bytes) plus source_format "
            "(markdown, html, txt, docx, odt, or rtf). Returns the new document_id."
        ),
        annotations=write_annotations(ToolAnnotations, title="Import Google Doc"),
        structured_output=False,
    )
    async def _productivity_docs_import(
        title: Annotated[
            str,
            Field(min_length=1, max_length=300, description="New document title."),
        ],
        content: Annotated[
            str,
            Field(description="Source text to convert; use for markdown/html/txt."),
        ] = "",
        content_base64: Annotated[
            str,
            Field(description="Base64 source bytes; use for docx/odt/rtf."),
        ] = "",
        source_format: Annotated[
            str,
            Field(
                description=(
                    "Source format: markdown, html, txt, docx, odt, or rtf."
                )
            ),
        ] = "markdown",
        idempotency_key: Annotated[
            str,
            Field(
                description=(
                    "Optional caller correlation key returned with the result. It "
                    "does not make Google's import operation exactly-once."
                )
            ),
        ] = "",
        account_id: Annotated[
            str,
            Field(
                description="Optional connected Google account id when several are available."
            ),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_docs_import", "import", account_id)
        if denial is not None:
            return denial
        return await docs.execute(
            operation="import",
            claim=_WRITE_CLAIMS,
            tool_name="productivity_docs_import",
            payload={
                "title": title,
                "content": content,
                "content_base64": content_base64,
                "source_format": source_format,
                "idempotency_key": idempotency_key,
            },
            account_id=account_id,
        )

    # ── comment (docs:comment) ────────────────────────────────────────────

    @mcp.tool(
        name="productivity_docs_create_comment",
        title="Comment on Google Doc",
        description=(
            "Create a comment on a document, optionally quoting a passage. Returns "
            "the new comment_id; use productivity_docs_list_comments to read the "
            "thread later. This changes the document each time it succeeds."
        ),
        annotations=write_annotations(
            ToolAnnotations, title="Comment on Google Doc"
        ),
        structured_output=False,
    )
    async def _productivity_docs_create_comment(
        document_ref: Annotated[
            str,
            Field(description="Document id or full Google Docs URL."),
        ],
        content: Annotated[
            str,
            Field(min_length=1, description="Comment text."),
        ],
        quoted_text: Annotated[
            str,
            Field(description="Optional passage the comment quotes."),
        ] = "",
        anchor: Annotated[
            str,
            Field(description="Optional Drive anchor JSON binding the comment to a region."),
        ] = "",
        idempotency_key: Annotated[
            str,
            Field(
                description=(
                    "Optional caller correlation key returned with the result. It "
                    "does not make Google's comment operation exactly-once."
                )
            ),
        ] = "",
        account_id: Annotated[
            str,
            Field(
                description="Optional connected Google account id when several are available."
            ),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_docs_create_comment", "comment", account_id)
        if denial is not None:
            return denial
        return await docs.execute(
            operation="create_comment",
            claim=_COMMENT_CLAIMS,
            tool_name="productivity_docs_create_comment",
            payload={
                "document_ref": document_ref,
                "content": content,
                "quoted_text": quoted_text,
                "anchor": anchor,
                "idempotency_key": idempotency_key,
            },
            account_id=account_id,
        )

    @mcp.tool(
        name="productivity_docs_reply_comment",
        title="Reply to Google Doc comment",
        description=(
            "Reply to an existing comment thread. Use "
            "productivity_docs_list_comments first to get the comment_id. This "
            "changes the document each time it succeeds."
        ),
        annotations=write_annotations(
            ToolAnnotations, title="Reply to Google Doc comment"
        ),
        structured_output=False,
    )
    async def _productivity_docs_reply_comment(
        document_ref: Annotated[
            str,
            Field(description="Document id or full Google Docs URL."),
        ],
        comment_id: Annotated[
            str,
            Field(
                description="Comment id returned by productivity_docs_list_comments."
            ),
        ],
        content: Annotated[
            str,
            Field(min_length=1, description="Reply text."),
        ],
        idempotency_key: Annotated[
            str,
            Field(
                description=(
                    "Optional caller correlation key returned with the result. It "
                    "does not make Google's reply operation exactly-once."
                )
            ),
        ] = "",
        account_id: Annotated[
            str,
            Field(
                description="Optional connected Google account id when several are available."
            ),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_docs_reply_comment", "comment", account_id)
        if denial is not None:
            return denial
        return await docs.execute(
            operation="reply_comment",
            claim=_COMMENT_CLAIMS,
            tool_name="productivity_docs_reply_comment",
            payload={
                "document_ref": document_ref,
                "comment_id": comment_id,
                "content": content,
                "idempotency_key": idempotency_key,
            },
            account_id=account_id,
        )

    @mcp.tool(
        name="productivity_docs_resolve_comment",
        title="Resolve Google Doc comment",
        description=(
            "Resolve a comment thread by posting a resolving reply. Use "
            "productivity_docs_list_comments first to get the comment_id."
        ),
        annotations=write_annotations(
            ToolAnnotations, title="Resolve Google Doc comment"
        ),
        structured_output=False,
    )
    async def _productivity_docs_resolve_comment(
        document_ref: Annotated[
            str,
            Field(description="Document id or full Google Docs URL."),
        ],
        comment_id: Annotated[
            str,
            Field(
                description="Comment id returned by productivity_docs_list_comments."
            ),
        ],
        content: Annotated[
            str,
            Field(description="Optional resolving reply text."),
        ] = "",
        account_id: Annotated[
            str,
            Field(
                description="Optional connected Google account id when several are available."
            ),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_docs_resolve_comment", "comment", account_id)
        if denial is not None:
            return denial
        return await docs.execute(
            operation="resolve_comment",
            claim=_COMMENT_CLAIMS,
            tool_name="productivity_docs_resolve_comment",
            payload={
                "document_ref": document_ref,
                "comment_id": comment_id,
                "content": content,
            },
            account_id=account_id,
        )

    @mcp.tool(
        name="productivity_docs_delete_comment",
        title="Delete Google Doc comment",
        description=(
            "Delete a comment thread from a document. This is destructive. Use "
            "productivity_docs_list_comments first and confirm the comment_id "
            "before calling."
        ),
        annotations=destructive_annotations(
            ToolAnnotations, title="Delete Google Doc comment"
        ),
        structured_output=False,
    )
    async def _productivity_docs_delete_comment(
        document_ref: Annotated[
            str,
            Field(description="Document id or full Google Docs URL."),
        ],
        comment_id: Annotated[
            str,
            Field(
                description="Comment id returned by productivity_docs_list_comments."
            ),
        ],
        account_id: Annotated[
            str,
            Field(
                description="Optional connected Google account id when several are available."
            ),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_docs_delete_comment", "delete", account_id)
        if denial is not None:
            return denial
        return await docs.execute(
            operation="delete_comment",
            claim=_COMMENT_CLAIMS,
            tool_name="productivity_docs_delete_comment",
            payload={"document_ref": document_ref, "comment_id": comment_id},
            account_id=account_id,
        )

    # ── flexible / tab-aware (evaluation surface) ─────────────────────────
    # These run the graph-faithful proxy (docs_proxy_flex) instead of the
    # narrow typed one. Governance is preserved by the batch_edit allowlist +
    # bounds rather than per-operation narrowness; compare against the typed
    # tools above.

    @mcp.tool(
        name="productivity_docs_get_structure",
        title="Read Google Doc structure",
        description=(
            "Read a document's structural graph: every tab (with its tab_id and "
            "hierarchy) and, per tab, the ordered elements (headings, paragraphs, "
            "tables) with their start_index/end_index and style. Use this to "
            "reason about structure or to find exact indices/tab_id to target "
            "with productivity_docs_batch_edit. Prefer productivity_docs_get for "
            "plain reading; use this when structure or tabs matter."
        ),
        annotations=read_only_annotations(ToolAnnotations, title="Read Google Doc structure"),
        structured_output=False,
    )
    async def _productivity_docs_get_structure(
        document_ref: Annotated[str, Field(description="Document id or full Google Docs URL.")],
        account_id: Annotated[
            str,
            Field(description="Optional connected Google account id when several are available."),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_docs_get_structure", "get_structure", account_id)
        if denial is not None:
            return denial
        return await docs.execute(
            operation="get_structure",
            claim=_READ_CLAIMS,
            tool_name="productivity_docs_get_structure",
            payload={"document_ref": document_ref},
            account_id=account_id,
        )

    @mcp.tool(
        name="productivity_docs_list_tabs",
        title="List Google Doc tabs",
        description=(
            "Enumerate a document's tabs (tab_id, title, index, parent tab) "
            "without returning body content. Use before "
            "productivity_docs_get_structure or productivity_docs_batch_edit to "
            "pick a tab_id."
        ),
        annotations=read_only_annotations(ToolAnnotations, title="List Google Doc tabs"),
        structured_output=False,
    )
    async def _productivity_docs_list_tabs(
        document_ref: Annotated[str, Field(description="Document id or full Google Docs URL.")],
        account_id: Annotated[
            str,
            Field(description="Optional connected Google account id when several are available."),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_docs_list_tabs", "list_tabs", account_id)
        if denial is not None:
            return denial
        return await docs.execute(
            operation="list_tabs",
            claim=_READ_CLAIMS,
            tool_name="productivity_docs_list_tabs",
            payload={"document_ref": document_ref},
            account_id=account_id,
        )

    @mcp.tool(
        name="productivity_docs_batch_edit",
        title="Batch-edit Google Doc",
        description=(
            "Apply a list (1-50) of native Google Docs batchUpdate requests in "
            "one call. A multi-tab document requires one tab_id. The only "
            "all-tabs batch is replaceAllText-only with explicit all_tabs=true. "
            "Each request is a "
            "single-key object of an allowlisted kind (insertText, "
            "deleteContentRange, replaceAllText, updateTextStyle, "
            "updateParagraphStyle, createParagraphBullets, insertTable, "
            "insertTableRow/Column, insertInlineImage, insertPageBreak, "
            "createNamedRange, ...). Get indices/tab_id from "
            "productivity_docs_get_structure first. This is the flexible "
            "alternative to the single-purpose edit tools; unknown request kinds "
            "are rejected."
        ),
        annotations=write_annotations(ToolAnnotations, title="Batch-edit Google Doc"),
        structured_output=False,
    )
    async def _productivity_docs_batch_edit(
        document_ref: Annotated[str, Field(description="Document id or full Google Docs URL.")],
        requests: Annotated[
            list[dict[str, Any]],
            Field(description="1-50 native Docs batchUpdate requests, each a single allowlisted request kind."),
        ],
        tab_id: Annotated[
            str,
            Field(
                description=(
                    "Target tab_id from list_tabs/get_structure. Required when "
                    "the document has multiple tabs unless all_tabs=true is used "
                    "for a replaceAllText-only batch."
                )
            ),
        ] = "",
        all_tabs: Annotated[
            bool,
            Field(
                description=(
                    "Explicitly target every tab. Supported only when every "
                    "request is replaceAllText."
                )
            ),
        ] = False,
        account_id: Annotated[
            str,
            Field(description="Optional connected Google account id when several are available."),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_docs_batch_edit", "batch_edit", account_id)
        if denial is not None:
            return denial
        return await docs.execute(
            operation="batch_edit",
            claim=_WRITE_CLAIMS,
            tool_name="productivity_docs_batch_edit",
            payload={
                "document_ref": document_ref,
                "requests": requests,
                "tab_id": tab_id,
                "all_tabs": all_tabs,
            },
            account_id=account_id,
        )
