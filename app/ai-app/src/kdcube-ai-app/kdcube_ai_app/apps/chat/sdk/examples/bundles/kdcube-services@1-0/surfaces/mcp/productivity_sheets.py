# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Google Sheets tool declarations for the productivity MCP surface."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from pydantic import Field

from kdcube_ai_app.apps.chat.sdk.solutions.connections.mcp_metadata import (
    destructive_annotations,
    read_only_annotations,
    write_annotations,
)

from ...services.productivity.google_sheets import (
    GoogleSheetsService,
    SHEETS_READ_CLAIM,
    SHEETS_WRITE_CLAIM,
)


EnforceTool = Callable[[str, str, str], Awaitable[dict[str, Any] | None]]


SHEETS_PRODUCTIVITY_TOOLS: dict[str, dict[str, Any]] = {
    "productivity_sheets_search": {
        "label": "Find Google Sheets",
        "description": "Find spreadsheets visible to the user's connected Google account.",
        "connections": {
            "delegated_to_kdcube": {
                "connected_accounts": [
                    {"provider_id": "google", "claims": [SHEETS_READ_CLAIM]},
                ],
            },
        },
    },
    "productivity_sheets_describe": {
        "label": "Describe Google Sheet",
        "description": "Inspect spreadsheet tabs, dimensions, locale, time zone, and named ranges.",
        "connections": {
            "delegated_to_kdcube": {
                "connected_accounts": [
                    {"provider_id": "google", "claims": [SHEETS_READ_CLAIM]},
                ],
            },
        },
    },
    "productivity_sheets_read": {
        "label": "Read Google Sheet",
        "description": "Read selected A1 ranges from a spreadsheet.",
        "connections": {
            "delegated_to_kdcube": {
                "connected_accounts": [
                    {"provider_id": "google", "claims": [SHEETS_READ_CLAIM]},
                ],
            },
        },
    },
    "productivity_sheets_update_values": {
        "label": "Update Google Sheet values",
        "description": "Replace values in explicit spreadsheet ranges.",
        "connections": {
            "delegated_to_kdcube": {
                "connected_accounts": [
                    {
                        "provider_id": "google",
                        "claims": [SHEETS_READ_CLAIM, SHEETS_WRITE_CLAIM],
                    },
                ],
            },
        },
    },
    "productivity_sheets_append_rows": {
        "label": "Append Google Sheet rows",
        "description": "Append rows after a logical table in a spreadsheet.",
        "connections": {
            "delegated_to_kdcube": {
                "connected_accounts": [
                    {
                        "provider_id": "google",
                        "claims": [SHEETS_READ_CLAIM, SHEETS_WRITE_CLAIM],
                    },
                ],
            },
        },
    },
    "productivity_sheets_clear_values": {
        "label": "Clear Google Sheet values",
        "description": "Clear values from explicit spreadsheet ranges.",
        "connections": {
            "delegated_to_kdcube": {
                "connected_accounts": [
                    {
                        "provider_id": "google",
                        "claims": [SHEETS_READ_CLAIM, SHEETS_WRITE_CLAIM],
                    },
                ],
            },
        },
    },
    "productivity_sheets_create_spreadsheet": {
        "label": "Create Google spreadsheet",
        "description": "Create a spreadsheet, optionally with initial values.",
        "connections": {
            "delegated_to_kdcube": {
                "connected_accounts": [
                    {
                        "provider_id": "google",
                        "claims": [SHEETS_READ_CLAIM, SHEETS_WRITE_CLAIM],
                    },
                ],
            },
        },
    },
    "productivity_sheets_add_tab": {
        "label": "Add Google Sheet tab",
        "description": "Add a bounded tab to an existing spreadsheet.",
        "connections": {
            "delegated_to_kdcube": {
                "connected_accounts": [
                    {
                        "provider_id": "google",
                        "claims": [SHEETS_READ_CLAIM, SHEETS_WRITE_CLAIM],
                    },
                ],
            },
        },
    },
    "productivity_sheets_update_tab": {
        "label": "Update Google Sheet tab",
        "description": "Rename, resize, or freeze rows and columns on a tab.",
        "connections": {
            "delegated_to_kdcube": {
                "connected_accounts": [
                    {
                        "provider_id": "google",
                        "claims": [SHEETS_READ_CLAIM, SHEETS_WRITE_CLAIM],
                    },
                ],
            },
        },
    },
    "productivity_sheets_delete_tab": {
        "label": "Delete Google Sheet tab",
        "description": "Delete one tab from a spreadsheet by stable sheet id.",
        "connections": {
            "delegated_to_kdcube": {
                "connected_accounts": [
                    {
                        "provider_id": "google",
                        "claims": [SHEETS_READ_CLAIM, SHEETS_WRITE_CLAIM],
                    },
                ],
            },
        },
    },
    "productivity_sheets_format_range": {
        "label": "Format Google Sheet range",
        "description": "Apply bounded common formatting to a range on one tab.",
        "connections": {
            "delegated_to_kdcube": {
                "connected_accounts": [
                    {
                        "provider_id": "google",
                        "claims": [SHEETS_READ_CLAIM, SHEETS_WRITE_CLAIM],
                    },
                ],
            },
        },
    },
}


def register_google_sheets_tools(
    *,
    mcp: Any,
    tool_annotations_type: Any,
    enforce: EnforceTool,
) -> None:
    """Register Google Sheets read tools and bounded mutations."""

    ToolAnnotations = tool_annotations_type
    _enforce = enforce
    sheets = GoogleSheetsService()

    @mcp.tool(
        name="productivity_sheets_search",
        title="Find Google Sheets",
        description=(
            "Find spreadsheets visible to the approving user's connected Google "
            "account. Use this when the spreadsheet id or URL is unknown. A blank "
            "query lists recently modified spreadsheets. Returns stable spreadsheet "
            "ids, titles, URLs, ownership metadata, and next_cursor."
        ),
        annotations=read_only_annotations(ToolAnnotations, title="Find Google Sheets"),
        structured_output=False,
    )
    async def _productivity_sheets_search(
        query: Annotated[
            str,
            Field(description="Optional case-insensitive spreadsheet title fragment."),
        ] = "",
        limit: Annotated[
            int,
            Field(ge=1, le=50, description="Maximum spreadsheet results, 1-50."),
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
        denial = await _enforce("productivity_sheets_search", "search", account_id)
        if denial is not None:
            return denial
        return await sheets.execute(
            operation="search",
            claim=SHEETS_READ_CLAIM,
            tool_name="productivity_sheets_search",
            payload={"query": query, "limit": limit, "cursor": cursor},
            account_id=account_id,
        )

    @mcp.tool(
        name="productivity_sheets_describe",
        title="Describe Google Sheet",
        description=(
            "Inspect a spreadsheet before reading or changing it. Accepts a "
            "spreadsheet id or full Google Sheets URL and returns stable tab ids, "
            "titles, dimensions, frozen rows/columns, locale, time zone, and named ranges."
        ),
        annotations=read_only_annotations(
            ToolAnnotations, title="Describe Google Sheet"
        ),
        structured_output=False,
    )
    async def _productivity_sheets_describe(
        spreadsheet_ref: Annotated[
            str,
            Field(description="Spreadsheet id or full Google Sheets URL."),
        ],
        account_id: Annotated[
            str,
            Field(
                description="Optional connected Google account id when several are available."
            ),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_sheets_describe", "describe", account_id)
        if denial is not None:
            return denial
        return await sheets.execute(
            operation="describe",
            claim=SHEETS_READ_CLAIM,
            tool_name="productivity_sheets_describe",
            payload={"spreadsheet_ref": spreadsheet_ref},
            account_id=account_id,
        )

    @mcp.tool(
        name="productivity_sheets_read",
        title="Read Google Sheet",
        description=(
            "Read one or more A1 ranges from a spreadsheet id or URL. "
            "Use describe first when tab titles are unknown. Returns each normalized "
            "range and its values without silently truncating cells."
        ),
        annotations=read_only_annotations(ToolAnnotations, title="Read Google Sheet"),
        structured_output=False,
    )
    async def _productivity_sheets_read(
        spreadsheet_ref: Annotated[
            str,
            Field(description="Spreadsheet id or full Google Sheets URL."),
        ],
        ranges: Annotated[
            list[str],
            Field(
                min_length=1,
                description='A1 ranges, for example ["Summary!A1:F40"].',
            ),
        ],
        major_dimension: Annotated[
            str,
            Field(description="Return values by ROWS or COLUMNS."),
        ] = "ROWS",
        value_render_option: Annotated[
            str,
            Field(
                description=(
                    "FORMATTED_VALUE, UNFORMATTED_VALUE, or FORMULA. FORMULA returns formulas instead of calculated values."
                )
            ),
        ] = "FORMATTED_VALUE",
        date_time_render_option: Annotated[
            str,
            Field(
                description="SERIAL_NUMBER or FORMATTED_STRING for unformatted dates."
            ),
        ] = "FORMATTED_STRING",
        account_id: Annotated[
            str,
            Field(
                description="Optional connected Google account id when several are available."
            ),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_sheets_read", "read", account_id)
        if denial is not None:
            return denial
        return await sheets.execute(
            operation="read",
            claim=SHEETS_READ_CLAIM,
            tool_name="productivity_sheets_read",
            payload={
                "spreadsheet_ref": spreadsheet_ref,
                "ranges": ranges,
                "major_dimension": major_dimension,
                "value_render_option": value_render_option,
                "date_time_render_option": date_time_render_option,
            },
            account_id=account_id,
        )

    @mcp.tool(
        name="productivity_sheets_update_values",
        title="Update Google Sheet values",
        description=(
            "Replace values in one or more explicit A1 ranges. Each updates item is "
            "{range: string, values: row arrays}. Repeating the same call writes the "
            "same values rather than appending duplicates."
        ),
        annotations=write_annotations(
            ToolAnnotations, title="Update Google Sheet values"
        ),
        structured_output=False,
    )
    async def _productivity_sheets_update_values(
        spreadsheet_ref: Annotated[
            str,
            Field(description="Spreadsheet id or full Google Sheets URL."),
        ],
        updates: Annotated[
            list[dict[str, Any]],
            Field(
                min_length=1,
                max_length=20,
                description=(
                    'One to twenty objects shaped {range: "Tab!A1:C3", values: [[...], ...]}.'
                ),
            ),
        ],
        value_input_option: Annotated[
            str,
            Field(
                description="USER_ENTERED parses formulas/dates; RAW stores literal values."
            ),
        ] = "USER_ENTERED",
        account_id: Annotated[
            str,
            Field(
                description="Optional connected Google account id when several are available."
            ),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_sheets_update_values", "update", account_id)
        if denial is not None:
            return denial
        return await sheets.execute(
            operation="update_values",
            claim=SHEETS_WRITE_CLAIM,
            tool_name="productivity_sheets_update_values",
            payload={
                "spreadsheet_ref": spreadsheet_ref,
                "updates": updates,
                "value_input_option": value_input_option,
            },
            account_id=account_id,
        )

    @mcp.tool(
        name="productivity_sheets_append_rows",
        title="Append Google Sheet rows",
        description=(
            "Append rows after the logical table in an explicit A1 range. This "
            "changes the spreadsheet each time it succeeds. After a transport result "
            "with outcome_unknown=true, inspect the sheet before retrying."
        ),
        annotations=write_annotations(
            ToolAnnotations, title="Append Google Sheet rows"
        ),
        structured_output=False,
    )
    async def _productivity_sheets_append_rows(
        spreadsheet_ref: Annotated[
            str,
            Field(description="Spreadsheet id or full Google Sheets URL."),
        ],
        range: Annotated[
            str,
            Field(description="A1 table range, for example Data!A1:F1."),
        ],
        rows: Annotated[
            list[list[Any]],
            Field(
                min_length=1,
                max_length=1000,
                description="Rows to append; cells may be strings, numbers, booleans, or null.",
            ),
        ],
        value_input_option: Annotated[
            str,
            Field(
                description="USER_ENTERED parses formulas/dates; RAW stores literal values."
            ),
        ] = "USER_ENTERED",
        idempotency_key: Annotated[
            str,
            Field(
                description=(
                    "Optional caller correlation key returned with the result. It does not make Google's append operation exactly-once."
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
        denial = await _enforce("productivity_sheets_append_rows", "append", account_id)
        if denial is not None:
            return denial
        return await sheets.execute(
            operation="append_rows",
            claim=SHEETS_WRITE_CLAIM,
            tool_name="productivity_sheets_append_rows",
            payload={
                "spreadsheet_ref": spreadsheet_ref,
                "range": range,
                "rows": rows,
                "value_input_option": value_input_option,
                "idempotency_key": idempotency_key,
            },
            account_id=account_id,
        )

    @mcp.tool(
        name="productivity_sheets_clear_values",
        title="Clear Google Sheet values",
        description=(
            "Clear values from one or more explicit A1 ranges while preserving "
            "formatting. This is destructive; use describe/read first when the target is uncertain."
        ),
        annotations=destructive_annotations(
            ToolAnnotations, title="Clear Google Sheet values"
        ),
        structured_output=False,
    )
    async def _productivity_sheets_clear_values(
        spreadsheet_ref: Annotated[
            str,
            Field(description="Spreadsheet id or full Google Sheets URL."),
        ],
        ranges: Annotated[
            list[str],
            Field(
                min_length=1, max_length=20, description="Explicit A1 ranges to clear."
            ),
        ],
        account_id: Annotated[
            str,
            Field(
                description="Optional connected Google account id when several are available."
            ),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_sheets_clear_values", "clear", account_id)
        if denial is not None:
            return denial
        return await sheets.execute(
            operation="clear_values",
            claim=SHEETS_WRITE_CLAIM,
            tool_name="productivity_sheets_clear_values",
            payload={"spreadsheet_ref": spreadsheet_ref, "ranges": ranges},
            account_id=account_id,
        )

    @mcp.tool(
        name="productivity_sheets_create_spreadsheet",
        title="Create Google spreadsheet",
        description=(
            "Create a spreadsheet in the connected Google account, optionally "
            "rename its first tab and populate values from A1. Returns the stable spreadsheet and tab ids."
        ),
        annotations=write_annotations(
            ToolAnnotations, title="Create Google spreadsheet"
        ),
        structured_output=False,
    )
    async def _productivity_sheets_create_spreadsheet(
        title: Annotated[
            str,
            Field(min_length=1, max_length=200, description="New spreadsheet title."),
        ],
        first_tab_title: Annotated[
            str,
            Field(max_length=100, description="Optional first tab title."),
        ] = "Sheet1",
        initial_values: Annotated[
            list[list[Any]] | None,
            Field(description="Optional initial row matrix written from A1."),
        ] = None,
        value_input_option: Annotated[
            str,
            Field(
                description="USER_ENTERED parses formulas/dates; RAW stores literal values."
            ),
        ] = "USER_ENTERED",
        idempotency_key: Annotated[
            str,
            Field(
                description=(
                    "Optional caller correlation key returned with the result. It does not make Google's create operation exactly-once."
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
        denial = await _enforce("productivity_sheets_create_spreadsheet", "create", account_id)
        if denial is not None:
            return denial
        return await sheets.execute(
            operation="create_spreadsheet",
            claim=SHEETS_WRITE_CLAIM,
            tool_name="productivity_sheets_create_spreadsheet",
            payload={
                "title": title,
                "first_tab_title": first_tab_title,
                "initial_values": initial_values,
                "value_input_option": value_input_option,
                "idempotency_key": idempotency_key,
            },
            account_id=account_id,
        )

    @mcp.tool(
        name="productivity_sheets_add_tab",
        title="Add Google Sheet tab",
        description=(
            "Add a tab to an existing spreadsheet with bounded row and column dimensions. Returns its stable sheet_id."
        ),
        annotations=write_annotations(ToolAnnotations, title="Add Google Sheet tab"),
        structured_output=False,
    )
    async def _productivity_sheets_add_tab(
        spreadsheet_ref: Annotated[
            str,
            Field(description="Spreadsheet id or full Google Sheets URL."),
        ],
        title: Annotated[
            str,
            Field(min_length=1, max_length=100, description="New tab title."),
        ],
        rows: Annotated[
            int,
            Field(ge=1, le=1000000, description="Initial tab row count."),
        ] = 1000,
        columns: Annotated[
            int,
            Field(ge=1, le=18278, description="Initial tab column count."),
        ] = 26,
        index: Annotated[
            int | None,
            Field(ge=0, description="Optional zero-based tab position."),
        ] = None,
        account_id: Annotated[
            str,
            Field(
                description="Optional connected Google account id when several are available."
            ),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_sheets_add_tab", "create", account_id)
        if denial is not None:
            return denial
        return await sheets.execute(
            operation="add_tab",
            claim=SHEETS_WRITE_CLAIM,
            tool_name="productivity_sheets_add_tab",
            payload={
                "spreadsheet_ref": spreadsheet_ref,
                "title": title,
                "rows": rows,
                "columns": columns,
                "index": index,
            },
            account_id=account_id,
        )

    @mcp.tool(
        name="productivity_sheets_update_tab",
        title="Update Google Sheet tab",
        description=(
            "Rename, resize, or change frozen rows/columns on one tab. Use describe "
            "first and pass its stable sheet_id. Omitted properties remain unchanged."
        ),
        annotations=write_annotations(ToolAnnotations, title="Update Google Sheet tab"),
        structured_output=False,
    )
    async def _productivity_sheets_update_tab(
        spreadsheet_ref: Annotated[
            str,
            Field(description="Spreadsheet id or full Google Sheets URL."),
        ],
        sheet_id: Annotated[
            int,
            Field(ge=0, description="Stable sheet_id returned by describe."),
        ],
        title: Annotated[
            str,
            Field(max_length=100, description="Optional replacement tab title."),
        ] = "",
        rows: Annotated[
            int | None,
            Field(ge=1, le=1000000, description="Optional replacement row count."),
        ] = None,
        columns: Annotated[
            int | None,
            Field(ge=1, le=18278, description="Optional replacement column count."),
        ] = None,
        frozen_rows: Annotated[
            int | None,
            Field(ge=0, description="Optional number of frozen rows; zero unfreezes."),
        ] = None,
        frozen_columns: Annotated[
            int | None,
            Field(
                ge=0, description="Optional number of frozen columns; zero unfreezes."
            ),
        ] = None,
        account_id: Annotated[
            str,
            Field(
                description="Optional connected Google account id when several are available."
            ),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_sheets_update_tab", "update", account_id)
        if denial is not None:
            return denial
        return await sheets.execute(
            operation="update_tab",
            claim=SHEETS_WRITE_CLAIM,
            tool_name="productivity_sheets_update_tab",
            payload={
                "spreadsheet_ref": spreadsheet_ref,
                "sheet_id": sheet_id,
                "title": title,
                "rows": rows,
                "columns": columns,
                "frozen_rows": frozen_rows,
                "frozen_columns": frozen_columns,
            },
            account_id=account_id,
        )

    @mcp.tool(
        name="productivity_sheets_delete_tab",
        title="Delete Google Sheet tab",
        description=(
            "Delete one tab by stable sheet_id. This is destructive. Use describe "
            "first and confirm the id and title before calling."
        ),
        annotations=destructive_annotations(
            ToolAnnotations, title="Delete Google Sheet tab"
        ),
        structured_output=False,
    )
    async def _productivity_sheets_delete_tab(
        spreadsheet_ref: Annotated[
            str,
            Field(description="Spreadsheet id or full Google Sheets URL."),
        ],
        sheet_id: Annotated[
            int,
            Field(ge=0, description="Stable sheet_id returned by describe."),
        ],
        account_id: Annotated[
            str,
            Field(
                description="Optional connected Google account id when several are available."
            ),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_sheets_delete_tab", "delete", account_id)
        if denial is not None:
            return denial
        return await sheets.execute(
            operation="delete_tab",
            claim=SHEETS_WRITE_CLAIM,
            tool_name="productivity_sheets_delete_tab",
            payload={"spreadsheet_ref": spreadsheet_ref, "sheet_id": sheet_id},
            account_id=account_id,
        )

    @mcp.tool(
        name="productivity_sheets_format_range",
        title="Format Google Sheet range",
        description=(
            "Apply common formatting to a bounded range on one tab. Use describe "
            "first for sheet_id. Provide at least one formatting property; colors use #RRGGBB."
        ),
        annotations=write_annotations(
            ToolAnnotations, title="Format Google Sheet range"
        ),
        structured_output=False,
    )
    async def _productivity_sheets_format_range(
        spreadsheet_ref: Annotated[
            str,
            Field(description="Spreadsheet id or full Google Sheets URL."),
        ],
        sheet_id: Annotated[
            int,
            Field(ge=0, description="Stable sheet_id returned by describe."),
        ],
        range: Annotated[
            str,
            Field(description="A1 range relative to this tab, for example A1:F3."),
        ],
        bold: Annotated[
            bool | None, Field(description="Optional bold setting.")
        ] = None,
        italic: Annotated[
            bool | None, Field(description="Optional italic setting.")
        ] = None,
        font_size: Annotated[
            int | None,
            Field(ge=6, le=72, description="Optional font size, 6-72."),
        ] = None,
        text_color: Annotated[
            str,
            Field(description="Optional text color as #RRGGBB."),
        ] = "",
        background_color: Annotated[
            str,
            Field(description="Optional background color as #RRGGBB."),
        ] = "",
        horizontal_alignment: Annotated[
            str,
            Field(description="Optional LEFT, CENTER, or RIGHT."),
        ] = "",
        vertical_alignment: Annotated[
            str,
            Field(description="Optional TOP, MIDDLE, or BOTTOM."),
        ] = "",
        wrap_strategy: Annotated[
            str,
            Field(description="Optional OVERFLOW_CELL, LEGACY_WRAP, CLIP, or WRAP."),
        ] = "",
        number_format_type: Annotated[
            str,
            Field(
                description=(
                    "Optional TEXT, NUMBER, PERCENT, CURRENCY, DATE, TIME, DATE_TIME, or SCIENTIFIC."
                )
            ),
        ] = "",
        number_format_pattern: Annotated[
            str,
            Field(description="Optional Google Sheets number-format pattern."),
        ] = "",
        border_style: Annotated[
            str,
            Field(
                description=(
                    "Optional DOTTED, DASHED, SOLID, SOLID_MEDIUM, SOLID_THICK, or DOUBLE for all four edges."
                )
            ),
        ] = "",
        border_color: Annotated[
            str,
            Field(description="Optional border color as #RRGGBB."),
        ] = "",
        account_id: Annotated[
            str,
            Field(
                description="Optional connected Google account id when several are available."
            ),
        ] = "",
    ) -> dict[str, Any]:
        denial = await _enforce("productivity_sheets_format_range", "format", account_id)
        if denial is not None:
            return denial
        return await sheets.execute(
            operation="format_range",
            claim=SHEETS_WRITE_CLAIM,
            tool_name="productivity_sheets_format_range",
            payload={
                "spreadsheet_ref": spreadsheet_ref,
                "sheet_id": sheet_id,
                "range": range,
                "bold": bold,
                "italic": italic,
                "font_size": font_size,
                "text_color": text_color,
                "background_color": background_color,
                "horizontal_alignment": horizontal_alignment,
                "vertical_alignment": vertical_alignment,
                "wrap_strategy": wrap_strategy,
                "number_format_type": number_format_type,
                "number_format_pattern": number_format_pattern,
                "border_style": border_style,
                "border_color": border_color,
            },
            account_id=account_id,
        )
