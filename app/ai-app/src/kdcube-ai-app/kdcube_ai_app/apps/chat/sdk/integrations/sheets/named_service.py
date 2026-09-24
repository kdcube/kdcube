# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Provider-neutral spreadsheet named service.

The ``sheets`` namespace models spreadsheets and tabs. Google Sheets is the
first transport, but the named-service contract does not expose Google access
tokens or require consumers to use Google-specific MCP tools.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from typing import Any

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


LOGGER = logging.getLogger("kdcube.sdk.integrations.sheets.named_service")


SHEETS_NAMESPACE = "sheets"
PROVIDER_ID = "sdk.integrations.sheets"
GOOGLE_PROVIDER_KEY = "google"
SHEETS_READ_CLAIM = "sheets:read"
SHEETS_WRITE_CLAIM = "sheets:write"

SHEETS_SPREADSHEET_KIND = "sheets.spreadsheet"
SHEETS_TAB_KIND = "sheets.tab"
SHEETS_TRANSPORTS = (TRANSPORT_LOCAL, TRANSPORT_API)
SHEETS_SNAPSHOT_SCHEMA = "kdcube.sheets.snapshot.v1"
SHEETS_SNAPSHOT_MEDIA_TYPE = (
    "application/vnd.kdcube.sheets.snapshot+json;version=1"
)

ACTION_UPDATE_VALUES = "update_values"
ACTION_APPEND_ROWS = "append_rows"
ACTION_CLEAR_VALUES = "clear_values"
ACTION_ADD_TAB = "add_tab"
ACTION_UPDATE_TAB = "update_tab"
ACTION_DELETE_TAB = "delete_tab"
ACTION_FORMAT_RANGE = "format_range"
UI_ACTION_OPEN = "open"

SHEETS_ACTIONS = (
    ACTION_UPDATE_VALUES,
    ACTION_APPEND_ROWS,
    ACTION_CLEAR_VALUES,
    ACTION_ADD_TAB,
    ACTION_UPDATE_TAB,
    ACTION_DELETE_TAB,
    ACTION_FORMAT_RANGE,
)

ExecuteSheetsOperation = Callable[..., Awaitable[Mapping[str, Any]]]


SHEETS_SEARCH_FILTERS = {
    "account_id": {
        "type": "string",
        "description": (
            "Optional connected spreadsheet account id. Required when more "
            "than one connected account is eligible."
        ),
    },
    "cursor": {
        "type": "string",
        "description": "Optional next_cursor returned by an earlier search.",
    },
}

SHEETS_SEARCH_SCOPES = (
    NamedServiceSearchScope(
        namespace=SHEETS_NAMESPACE,
        label="spreadsheets",
        object_kind=SHEETS_SPREADSHEET_KIND,
        description=(
            "Find spreadsheets by title through an approved connected account. "
            "A blank query lists recently modified spreadsheets."
        ),
        filters_schema=SHEETS_SEARCH_FILTERS,
    ),
)

SHEETS_GRANT_HINTS = {
    "object.list": [SHEETS_READ_CLAIM],
    "object.search": [SHEETS_READ_CLAIM],
    "object.get": [SHEETS_READ_CLAIM],
    "object.upsert": [SHEETS_WRITE_CLAIM],
    "object.delete": [SHEETS_WRITE_CLAIM],
    "object.action": [SHEETS_WRITE_CLAIM],
    **{
        f"object.action.{action}": [SHEETS_WRITE_CLAIM]
        for action in SHEETS_ACTIONS
    },
}

SHEETS_CONNECTED_ACCOUNT_REQUIREMENTS = [
    {
        "provider_id": GOOGLE_PROVIDER_KEY,
        "provider_label": "Google",
        "claims": [SHEETS_READ_CLAIM, SHEETS_WRITE_CLAIM],
        "claim_labels": {
            SHEETS_READ_CLAIM: "read spreadsheets",
            SHEETS_WRITE_CLAIM: "edit spreadsheets",
        },
        "claims_by_operation": {
            "object.list": [SHEETS_READ_CLAIM],
            "object.search": [SHEETS_READ_CLAIM],
            "object.get": [SHEETS_READ_CLAIM],
            "object.upsert": [SHEETS_READ_CLAIM, SHEETS_WRITE_CLAIM],
            "object.delete": [SHEETS_READ_CLAIM, SHEETS_WRITE_CLAIM],
            "object.action": [SHEETS_READ_CLAIM, SHEETS_WRITE_CLAIM],
            **{
                f"object.action.{action}": [
                    SHEETS_READ_CLAIM,
                    SHEETS_WRITE_CLAIM,
                ]
                for action in SHEETS_ACTIONS
            },
        },
    }
]

SHEETS_PROVIDER_CATALOG = {
    GOOGLE_PROVIDER_KEY: {
        "provider_id": GOOGLE_PROVIDER_KEY,
        "label": "Google Sheets",
        "claims": {
            "read": SHEETS_READ_CLAIM,
            "write": SHEETS_WRITE_CLAIM,
        },
    },
}

SHEETS_SCHEMA = {
    "namespace": SHEETS_NAMESPACE,
    "refs": {
        "spreadsheet": (
            "sheets:<provider>:<account_id>:spreadsheet:<spreadsheet_id>"
        ),
        "tab": (
            "sheets:<provider>:<account_id>:spreadsheet:<spreadsheet_id>:"
            "tab:<sheet_id>"
        ),
    },
    "object_kinds": {
        SHEETS_SPREADSHEET_KIND: {
            "description": "One spreadsheet visible to a connected account.",
            "fields": [
                "ref",
                "provider",
                "account_id",
                "spreadsheet_id",
                "title",
                "web_url",
                "created_time",
                "modified_time",
                "tabs",
                "named_ranges",
            ],
        },
        SHEETS_TAB_KIND: {
            "description": "One stable tab inside a spreadsheet.",
            "fields": [
                "ref",
                "spreadsheet_ref",
                "provider",
                "account_id",
                "spreadsheet_id",
                "sheet_id",
                "title",
                "index",
                "row_count",
                "column_count",
            ],
        },
    },
    "search": {"filters": SHEETS_SEARCH_FILTERS},
    "get": {
        "description": (
            "Read a spreadsheet or tab by ref. A spreadsheet get returns "
            "metadata by default and, on a configured turnless transport, a "
            "short-lived URL for the complete JSON snapshot. Pass "
            "filters.ranges with explicit A1 ranges to read selected cell "
            "values inline."
        ),
        "filters": [
            "ranges",
            "major_dimension",
            "value_render_option",
            "date_time_render_option",
        ],
    },
    "materialization": {
        "description": (
            "A materializing client can resolve a spreadsheet or tab ref through "
            "object.get with response_mode=stream. The JSON snapshot begins with "
            "workbook and tab metadata and includes used values for the selected "
            "grid tabs."
        ),
        "schema": SHEETS_SNAPSHOT_SCHEMA,
        "media_type": SHEETS_SNAPSHOT_MEDIA_TYPE,
        "refs": ["spreadsheet", "tab"],
    },
    "upsert": {
        "create": {
            "description": "Omit object_ref to create a spreadsheet.",
            "object": [
                "title",
                "first_tab_title",
                "initial_values",
                "value_input_option",
            ],
        },
        "spreadsheet": {
            "description": (
                "Use a spreadsheet ref and object.updates=[{range, values}] "
                "to replace bounded values."
            ),
            "object": ["updates", "value_input_option"],
        },
        "tab": {
            "description": "Use a tab ref to rename, resize, or freeze that tab.",
            "object": [
                "title",
                "rows",
                "columns",
                "frozen_rows",
                "frozen_columns",
            ],
        },
    },
    "actions": {
        ACTION_UPDATE_VALUES: {
            "description": "Replace values in explicit ranges.",
            "object_ref": "spreadsheet ref",
            "payload": ["updates", "value_input_option"],
        },
        ACTION_APPEND_ROWS: {
            "description": "Append rows after a logical table range.",
            "object_ref": "spreadsheet ref",
            "payload": ["range", "rows", "value_input_option", "idempotency_key"],
        },
        ACTION_CLEAR_VALUES: {
            "description": "Clear values from explicit A1 ranges.",
            "object_ref": "spreadsheet ref",
            "payload": ["ranges"],
        },
        ACTION_ADD_TAB: {
            "description": "Add a bounded tab to a spreadsheet.",
            "object_ref": "spreadsheet ref",
            "payload": ["title", "rows", "columns", "index"],
        },
        ACTION_UPDATE_TAB: {
            "description": "Rename, resize, or freeze a tab.",
            "object_ref": "tab ref",
            "payload": [
                "title",
                "rows",
                "columns",
                "frozen_rows",
                "frozen_columns",
            ],
        },
        ACTION_DELETE_TAB: {
            "description": "Delete one tab. Spreadsheet deletion is not exposed.",
            "object_ref": "tab ref",
            "payload": [],
        },
        ACTION_FORMAT_RANGE: {
            "description": "Apply bounded common formatting to a tab range.",
            "object_ref": "tab ref",
            "payload": [
                "range",
                "bold",
                "italic",
                "font_size",
                "text_color",
                "background_color",
                "horizontal_alignment",
                "vertical_alignment",
                "wrap_strategy",
                "number_format_type",
                "number_format_pattern",
                "border_style",
                "border_color",
            ],
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
    "grant_hints": SHEETS_GRANT_HINTS,
    "connected_account_claims": {
        GOOGLE_PROVIDER_KEY: {
            "read": SHEETS_READ_CLAIM,
            "write": SHEETS_WRITE_CLAIM,
        }
    },
}

SHEETS_SCHEMA_PROJECTION = {
    "catalog": {
        "id": "sheets",
        "label": "Spreadsheets",
        "description": "Explore spreadsheet discovery, values, tabs, and formatting.",
        "children": [
            {
                "id": "discover",
                "label": "Find spreadsheets",
                "object_kind": SHEETS_SPREADSHEET_KIND,
                "operations": ["object.list", "object.search"],
            },
            {
                "id": "spreadsheets",
                "label": "Spreadsheet data",
                "object_kind": SHEETS_SPREADSHEET_KIND,
                "children": [
                    {
                        "id": "read",
                        "label": "Read metadata and ranges",
                        "operations": ["object.get"],
                    },
                    {
                        "id": "write",
                        "label": "Create and change values",
                        "keywords": ["cells", "rows", "clear", "append", "write"],
                        "operations": [
                            "object.upsert",
                            f"object.action:{ACTION_UPDATE_VALUES}",
                            f"object.action:{ACTION_APPEND_ROWS}",
                            f"object.action:{ACTION_CLEAR_VALUES}",
                        ],
                    },
                    {
                        "id": "tabs",
                        "label": "Add tabs",
                        "operations": [f"object.action:{ACTION_ADD_TAB}"],
                    },
                ],
            },
            {
                "id": "tabs",
                "label": "Worksheet tabs",
                "object_kind": SHEETS_TAB_KIND,
                "children": [
                    {
                        "id": "inspect",
                        "label": "Read a tab",
                        "operations": ["object.get"],
                    },
                    {
                        "id": "manage",
                        "label": "Rename, resize, and delete tabs",
                        "operations": [
                            "object.upsert",
                            "object.delete",
                            f"object.action:{ACTION_UPDATE_TAB}",
                            f"object.action:{ACTION_DELETE_TAB}",
                        ],
                    },
                    {
                        "id": "format",
                        "label": "Format ranges",
                        "keywords": ["bold", "header", "style", "color"],
                        "operations": [f"object.action:{ACTION_FORMAT_RANGE}"],
                    },
                ],
            },
        ],
    },
    "kinds": {
        SHEETS_SPREADSHEET_KIND: {
            "refs": ["spreadsheet"],
            "related_kinds": [SHEETS_TAB_KIND],
            "operations": {
                "object.list": {},
                "object.search": {"sections": ["search"]},
                "object.get": {"sections": ["get", "materialization"]},
                "object.upsert": {
                    "sections": ["upsert"],
                    "section_keys": {"upsert": ["create", "spreadsheet"]},
                },
            },
            "actions": [
                ACTION_UPDATE_VALUES,
                ACTION_APPEND_ROWS,
                ACTION_CLEAR_VALUES,
                ACTION_ADD_TAB,
            ],
        },
        SHEETS_TAB_KIND: {
            "refs": ["tab"],
            "related_kinds": [SHEETS_SPREADSHEET_KIND],
            "operations": {
                "object.get": {"sections": ["get", "materialization"]},
                "object.upsert": {
                    "sections": ["upsert"],
                    "section_keys": {"upsert": ["tab"]},
                },
                "object.delete": {
                    "description": "Delete one tab by its canonical ref."
                },
            },
            "actions": [
                ACTION_UPDATE_TAB,
                ACTION_DELETE_TAB,
                ACTION_FORMAT_RANGE,
            ],
        },
    },
}

SHEETS_INTRO = (
    "Use namespace `sheets` for user-connected spreadsheets. Search by title, "
    "get a spreadsheet ref to inspect metadata or selected ranges, then use "
    "object.upsert or a declared object.action for explicit changes."
)

SHEETS_PRESENTATION = {
    "about": "Find, read, create, and edit spreadsheets you connect.",
    "third_party": "Google Sheets is the first provider behind this namespace.",
    "operations": {
        "provider.about": {
            "label": "Service overview",
            "description": "What the spreadsheet service does and how to use it.",
        },
        "provider.capabilities": {
            "label": "Capabilities",
            "description": "The operations and bounded actions this service supports.",
        },
        "object.list": {
            "label": "Recent spreadsheets",
            "description": "List recently modified spreadsheets.",
        },
        "object.search": {
            "label": "Search spreadsheets",
            "description": "Find spreadsheets by title.",
        },
        "object.get": {
            "label": "Read spreadsheet",
            "description": "Inspect metadata or read explicit ranges.",
        },
        "object.schema": {
            "label": "Spreadsheet schema",
            "description": "Read refs, fields, limits, and action payloads.",
        },
        "object.upsert": {
            "label": "Create or update spreadsheet",
            "description": "Create a spreadsheet or update explicit values/tab properties.",
        },
        "object.delete": {
            "label": "Delete spreadsheet tab",
            "description": "Delete one tab; spreadsheet-file deletion is not exposed.",
        },
    },
    # Human titles and user-terms lines, beside the schema rather than from it:
    # the schema text is the agent's contract and reads as an instruction in a
    # consent card.
    "actions": {
        ACTION_UPDATE_VALUES: {
            "label": "Update cells",
            "description": "Replace the values in a range of cells.",
        },
        ACTION_APPEND_ROWS: {
            "label": "Append rows",
            "description": "Add rows to the end of a table.",
        },
        ACTION_CLEAR_VALUES: {
            "label": "Clear cells",
            "description": "Empty a range of cells, keeping the rows in place.",
        },
        ACTION_ADD_TAB: {
            "label": "Add a tab",
            "description": "Add a tab to a spreadsheet.",
        },
        ACTION_UPDATE_TAB: {
            "label": "Rename or resize a tab",
            "description": "Rename a tab, change its size, or freeze its header rows.",
        },
        ACTION_DELETE_TAB: {
            "label": "Delete a tab",
            "description": "Delete a tab and everything on it.",
        },
        ACTION_FORMAT_RANGE: {
            "label": "Format cells",
            "description": "Change how a range of cells looks.",
        },
    },
}


def _operations() -> dict[str, Any]:
    return {
        PROVIDER_ABOUT: {"transports": SHEETS_TRANSPORTS},
        PROVIDER_CAPABILITIES: {"transports": SHEETS_TRANSPORTS},
        OBJECT_LIST: {"transports": SHEETS_TRANSPORTS},
        OBJECT_SEARCH: {"transports": SHEETS_TRANSPORTS},
        OBJECT_GET: {"transports": SHEETS_TRANSPORTS},
        OBJECT_SCHEMA: {"transports": SHEETS_TRANSPORTS},
        OBJECT_UPSERT: {"transports": SHEETS_TRANSPORTS},
        OBJECT_DELETE: {"transports": SHEETS_TRANSPORTS},
        OBJECT_ACTION: {"transports": SHEETS_TRANSPORTS},
        OBJECT_RESOLVE: {"transports": SHEETS_TRANSPORTS},
        EVENT_RESOLVE: {"transports": SHEETS_TRANSPORTS},
        BLOCK_PRODUCE: {"transports": SHEETS_TRANSPORTS},
    }


def sheets_named_service_spec(*, bundle_id: str | None = None) -> NamedServiceProviderSpec:
    return NamedServiceProviderSpec(
        provider_id=PROVIDER_ID,
        bundle_id=bundle_id,
        namespace=SHEETS_NAMESPACE,
        refs=("sheets:*",),
        object_kinds=(SHEETS_SPREADSHEET_KIND, SHEETS_TAB_KIND),
        search_scopes=SHEETS_SEARCH_SCOPES,
        operations=_operations(),
        label="Spreadsheets",
        description=(
            "Provider-neutral spreadsheet namespace over user-connected accounts."
        ),
        intro=SHEETS_INTRO,
        metadata={
            "provider_catalog": SHEETS_PROVIDER_CATALOG,
            "grant_hints": SHEETS_GRANT_HINTS,
            "connected_accounts": SHEETS_CONNECTED_ACCOUNT_REQUIREMENTS,
            "canonical_refs": SHEETS_SCHEMA["refs"],
            "presentation": SHEETS_PRESENTATION,
            "actions": {
                name: str((meta or {}).get("description") or "")
                for name, meta in SHEETS_SCHEMA["actions"].items()
            },
            "object_kinds": {
                kind: str((meta or {}).get("description") or "")
                for kind, meta in SHEETS_SCHEMA["object_kinds"].items()
            },
        },
    )


def _text(value: Any) -> str:
    return str(value or "").strip()


def _is_materialization_request(request: NamedServiceRequest) -> bool:
    context = request.context if isinstance(request.context, Mapping) else {}
    payload = request.payload if isinstance(request.payload, Mapping) else {}
    if _text(request.response_mode).lower() == "stream":
        return True
    if context.get("materialize") or payload.get("materialize"):
        return True
    return _text(context.get("source") or payload.get("source")) == "react.pull"


def _whole_tab_range(title: Any) -> str:
    escaped = _text(title).replace("'", "''")
    return f"'{escaped}'"


def _snapshot_filename(parsed: Mapping[str, Any]) -> str:
    identity = (
        f"{_text(parsed.get('spreadsheet_id'))}-tab-{_int(parsed.get('sheet_id'))}"
        if parsed.get("kind") == "tab"
        else _text(parsed.get("spreadsheet_id"))
    )
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", identity).strip("._-")
    return f"{safe or 'spreadsheet'}.sheets.json"


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
    if _text(snapshot.get("schema")) != SHEETS_SNAPSHOT_SCHEMA:
        return {}
    return snapshot


def _range_inventory(values: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for value in values.get("ranges") or []:
        if not isinstance(value, Mapping):
            continue
        matrix = value.get("values") if isinstance(value.get("values"), list) else []
        row_count = len(matrix)
        cell_count = sum(len(row) for row in matrix if isinstance(row, list))
        rows.append(
            {
                "range": _text(value.get("range")) or "<unnamed>",
                "row_count": row_count,
                "cell_count": cell_count,
            }
        )
    return rows


def _snapshot_tabs(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    obj = snapshot.get("object") if isinstance(snapshot.get("object"), Mapping) else {}
    if _text(snapshot.get("object_kind")) == SHEETS_TAB_KIND:
        return [dict(obj)] if obj else []
    spreadsheet = (
        snapshot.get("spreadsheet")
        if isinstance(snapshot.get("spreadsheet"), Mapping)
        else {}
    )
    tabs = spreadsheet.get("tabs") or obj.get("tabs") or []
    return [dict(tab) for tab in tabs if isinstance(tab, Mapping)]


def _snapshot_inventory_text(
    snapshot: Mapping[str, Any],
    *,
    object_ref: str,
    target: Mapping[str, Any],
) -> str:
    obj = snapshot.get("object") if isinstance(snapshot.get("object"), Mapping) else {}
    spreadsheet = (
        snapshot.get("spreadsheet")
        if isinstance(snapshot.get("spreadsheet"), Mapping)
        else {}
    )
    values = snapshot.get("values") if isinstance(snapshot.get("values"), Mapping) else {}
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
    ranges = _range_inventory(values)
    range_count = (
        _int(values.get("range_count"))
        if values.get("range_count") is not None
        else (len(ranges) if ranges else None)
    )
    row_count = (
        _int(values.get("row_count"))
        if values.get("row_count") is not None
        else (
            sum(_int(row.get("row_count")) for row in ranges)
            if ranges
            else None
        )
    )
    cell_count = (
        _int(values.get("cell_count"))
        if values.get("cell_count") is not None
        else (
            sum(_int(row.get("cell_count")) for row in ranges)
            if ranges
            else None
        )
    )
    values_materialized = materialization.get("values_materialized")
    if values_materialized is None and values:
        values_materialized = True
    complete_values = materialization.get("complete_values")

    def _status(value: Any) -> str:
        if value is True:
            return "yes"
        if value is False:
            return "no"
        return "unknown"

    def _count(value: Any) -> str:
        return str(value) if value is not None else "unknown"

    lines = [
        "[SHEETS SNAPSHOT]",
        f"object_ref: {object_ref}",
        f"object_kind: {_text(snapshot.get('object_kind')) or _text(obj.get('object_kind'))}",
        f"title: {_text(obj.get('title')) or _text(spreadsheet.get('title')) or '<untitled>'}",
        "spreadsheet_id: "
        + (
            _text(spreadsheet.get("spreadsheet_id"))
            or _text(obj.get("spreadsheet_id"))
        ),
    ]
    web_url = _text(spreadsheet.get("web_url") or obj.get("web_url"))
    if web_url:
        lines.append(f"web_url: {web_url}")
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
            f"values_materialized: {_status(values_materialized)}",
            f"complete_values: {_status(complete_values)}",
            f"materialized_ranges: {_count(range_count)}",
            f"materialized_rows: {_count(row_count)}",
            f"materialized_cells: {_count(cell_count)}",
            "tabs:",
        ]
    )
    tabs = _snapshot_tabs(snapshot)
    if not tabs:
        lines.append("- none reported")
    for tab in tabs:
        dimensions = (
            f"{_int(tab.get('row_count'))} rows x "
            f"{_int(tab.get('column_count'))} columns"
        )
        sheet_type = _text(tab.get("sheet_type")) or "GRID"
        tab_line = (
            f"- {_text(tab.get('title')) or '<untitled>'} "
            f"(sheet_id={_int(tab.get('sheet_id'))}, type={sheet_type}, "
            f"dimensions={dimensions}"
        )
        tab_object_ref = _text(tab.get("ref"))
        if tab_object_ref:
            tab_line += f", object_ref={tab_object_ref}"
        lines.append(tab_line + ")")

    lines.append("ranges:")
    if not ranges:
        lines.append("- none materialized")
    for row in ranges:
        lines.append(
            f"- {row['range']} ({row['row_count']} rows, {row['cell_count']} cells)"
        )
    skipped_tabs = materialization.get("skipped_tabs") or []
    if skipped_tabs:
        lines.append("skipped_tabs:")
        for tab in skipped_tabs:
            if not isinstance(tab, Mapping):
                continue
            lines.append(
                f"- {_text(tab.get('title')) or '<untitled>'}: "
                f"{_text(tab.get('reason')) or 'not materialized'}"
            )
    lines.extend(
        [
            "snapshot_layout:",
            "- workbook and tab metadata: object, spreadsheet",
            "- cell matrices: values.ranges[].values",
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


def spreadsheet_ref(
    *, provider: str, account_id: str, spreadsheet_id: str
) -> str:
    return (
        f"{SHEETS_NAMESPACE}:{_text(provider) or GOOGLE_PROVIDER_KEY}:"
        f"{_text(account_id)}:spreadsheet:{_text(spreadsheet_id)}"
    )


def tab_ref(
    *,
    provider: str,
    account_id: str,
    spreadsheet_id: str,
    sheet_id: Any,
) -> str:
    parent_ref = spreadsheet_ref(
        provider=provider,
        account_id=account_id,
        spreadsheet_id=spreadsheet_id,
    )
    return f"{parent_ref}:tab:{_int(sheet_id)}"


def parse_sheets_ref(value: Any) -> dict[str, Any]:
    ref = _text(value)
    parts = ref.split(":")
    if len(parts) not in {5, 7} or parts[0].lower() != SHEETS_NAMESPACE:
        raise ValueError("Invalid sheets object ref.")
    if parts[3] != "spreadsheet" or not all(parts[index] for index in (1, 2, 4)):
        raise ValueError("Invalid sheets spreadsheet ref.")
    parsed: dict[str, Any] = {
        "ref": ref,
        "provider": parts[1].lower(),
        "account_id": parts[2],
        "spreadsheet_id": parts[4],
        "kind": "spreadsheet",
    }
    if len(parts) == 7:
        if parts[5] != "tab":
            raise ValueError("Invalid sheets tab ref.")
        try:
            parsed["sheet_id"] = int(parts[6])
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid sheets tab sheet_id.") from exc
        if parsed["sheet_id"] < 0:
            raise ValueError("Invalid sheets tab sheet_id.")
        parsed["kind"] = "tab"
    return parsed


def _spreadsheet_object(
    value: Mapping[str, Any],
    *,
    account_id: str,
    provider: str = GOOGLE_PROVIDER_KEY,
) -> dict[str, Any]:
    row = dict(value or {})
    spreadsheet_id = _text(row.get("spreadsheet_id"))
    ref = spreadsheet_ref(
        provider=provider,
        account_id=account_id,
        spreadsheet_id=spreadsheet_id,
    )
    tabs = [
        _tab_object(
            tab,
            account_id=account_id,
            spreadsheet_id=spreadsheet_id,
            provider=provider,
        )
        for tab in row.get("tabs") or []
        if isinstance(tab, Mapping)
    ]
    first_tab_raw = row.get("first_tab")
    first_tab = (
        _tab_object(
            first_tab_raw,
            account_id=account_id,
            spreadsheet_id=spreadsheet_id,
            provider=provider,
        )
        if isinstance(first_tab_raw, Mapping)
        else None
    )
    result = {
        **row,
        "ref": ref,
        "object_kind": SHEETS_SPREADSHEET_KIND,
        "provider": provider,
        "account_id": account_id,
        "spreadsheet_id": spreadsheet_id,
    }
    if "tabs" in row:
        result["tabs"] = tabs
    if first_tab is not None:
        result["first_tab"] = first_tab
    return result


def _tab_object(
    value: Mapping[str, Any],
    *,
    account_id: str,
    spreadsheet_id: str,
    provider: str = GOOGLE_PROVIDER_KEY,
) -> dict[str, Any]:
    row = dict(value or {})
    sheet_id = _int(row.get("sheet_id"))
    parent_ref = spreadsheet_ref(
        provider=provider,
        account_id=account_id,
        spreadsheet_id=spreadsheet_id,
    )
    return {
        **row,
        "ref": tab_ref(
            provider=provider,
            account_id=account_id,
            spreadsheet_id=spreadsheet_id,
            sheet_id=sheet_id,
        ),
        "object_kind": SHEETS_TAB_KIND,
        "spreadsheet_ref": parent_ref,
        "provider": provider,
        "account_id": account_id,
        "spreadsheet_id": spreadsheet_id,
        "sheet_id": sheet_id,
    }


@named_service_provider(
    provider_id=PROVIDER_ID,
    namespace=SHEETS_NAMESPACE,
    refs=("sheets:*",),
    object_kinds=(SHEETS_SPREADSHEET_KIND, SHEETS_TAB_KIND),
    search_scopes=SHEETS_SEARCH_SCOPES,
    operations=_operations(),
    label="Spreadsheets",
    description="Provider-neutral spreadsheet namespace over connected accounts.",
    intro=SHEETS_INTRO,
    metadata={
        "provider_catalog": SHEETS_PROVIDER_CATALOG,
        "grant_hints": SHEETS_GRANT_HINTS,
        "connected_accounts": SHEETS_CONNECTED_ACCOUNT_REQUIREMENTS,
        "canonical_refs": SHEETS_SCHEMA["refs"],
        "presentation": SHEETS_PRESENTATION,
        "actions": {
            name: str((meta or {}).get("description") or "")
            for name, meta in SHEETS_SCHEMA["actions"].items()
        },
        "object_kinds": {
            kind: str((meta or {}).get("description") or "")
            for kind, meta in SHEETS_SCHEMA["object_kinds"].items()
        },
    },
)
class SheetsNamedServiceProvider(NamedServiceProvider):
    schema_projection_index = SHEETS_SCHEMA_PROJECTION

    def __init__(
        self,
        *,
        execute_operation: ExecuteSheetsOperation,
        bundle_id: str | None = None,
        file_url_factory: Any = None,
    ) -> None:
        super().__init__(sheets_named_service_spec(bundle_id=bundle_id))
        self._execute_operation = execute_operation
        self._file_url_factory = file_url_factory

    def _provider_identity(self) -> dict[str, Any]:
        return {"provider_id": PROVIDER_ID, "bundle_id": self.spec.bundle_id}

    def schema_object_kind_from_ref(self, object_ref: str) -> str | None:
        try:
            parsed = parse_sheets_ref(object_ref)
        except ValueError:
            return None
        return (
            SHEETS_TAB_KIND
            if parsed.get("kind") == "tab"
            else SHEETS_SPREADSHEET_KIND
        )

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
            LOGGER.exception("sheets snapshot url factory failed for %s", ref)
            return None
        return dict(out) if isinstance(out, Mapping) and out.get("url") else None

    def _invalid_ref(
        self, request: NamedServiceRequest, exc: Exception
    ) -> NamedServiceResponse:
        return NamedServiceResponse.error_response(
            code="invalid_sheets_ref",
            message=str(exc),
            status=400,
            provider=self._provider_identity(),
            namespace=request.namespace or SHEETS_NAMESPACE,
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
        tool_name = f"named_services.{SHEETS_NAMESPACE}.{request.operation}"
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
                namespace=SHEETS_NAMESPACE,
                provider_identity=self._provider_identity(),
                default_code="sheets_operation_failed",
                fallback_message="The spreadsheet operation failed.",
            )
        ret = result.get("ret")
        return (dict(ret or {}) if isinstance(ret, Mapping) else {}), None

    def _provider_not_supported(
        self,
        request: NamedServiceRequest,
        parsed: Mapping[str, Any],
    ) -> NamedServiceResponse | None:
        provider = _text(parsed.get("provider"))
        if provider == GOOGLE_PROVIDER_KEY:
            return None
        return NamedServiceResponse.error_response(
            code="sheets_provider_not_implemented",
            message=f"Spreadsheet provider is not implemented: {provider}",
            status=501,
            provider=self._provider_identity(),
            namespace=request.namespace or SHEETS_NAMESPACE,
            object_ref=request.object_ref,
        )

    async def provider_about(
        self, ctx: NamedServiceContext, request: NamedServiceRequest
    ) -> NamedServiceResponse:
        del ctx
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or SHEETS_NAMESPACE,
            extra={
                "title": "KDCube Spreadsheets",
                "description": (
                    "Provider-neutral spreadsheet namespace. Google Sheets is "
                    "the first connected-account provider."
                ),
                "workflow": [
                    "Call object.search to find a spreadsheet by title.",
                    "Call object.get with its ref to inspect metadata.",
                    "Pass filters.ranges to object.get to read explicit A1 ranges.",
                    "Call object.upsert or a declared object.action for bounded changes.",
                ],
                "providers": SHEETS_PROVIDER_CATALOG,
                "schema": SHEETS_SCHEMA,
            },
        )

    async def provider_capabilities(
        self, ctx: NamedServiceContext, request: NamedServiceRequest
    ) -> NamedServiceResponse:
        del ctx
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or SHEETS_NAMESPACE,
            capabilities={
                "list": True,
                "search": True,
                "get": True,
                "upsert": True,
                "delete": "tabs_only",
                "actions": list(SHEETS_ACTIONS),
                "providers": SHEETS_PROVIDER_CATALOG,
                "grant_hints": SHEETS_GRANT_HINTS,
                "connected_account_claims": SHEETS_SCHEMA[
                    "connected_account_claims"
                ],
            },
        )

    async def object_schema(
        self, ctx: NamedServiceContext, request: NamedServiceRequest
    ) -> NamedServiceResponse:
        del ctx
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or SHEETS_NAMESPACE,
            extra={"schema": SHEETS_SCHEMA},
        )

    async def event_resolve(
        self, ctx: NamedServiceContext, request: NamedServiceRequest
    ) -> NamedServiceResponse:
        del ctx
        try:
            parsed = parse_sheets_ref(request.object_ref)
        except ValueError as exc:
            return self._invalid_ref(request, exc)
        unsupported = self._provider_not_supported(request, parsed)
        if unsupported is not None:
            return unsupported
        canonical_ref = (
            tab_ref(
                provider=parsed["provider"],
                account_id=parsed["account_id"],
                spreadsheet_id=parsed["spreadsheet_id"],
                sheet_id=parsed["sheet_id"],
            )
            if parsed["kind"] == "tab"
            else spreadsheet_ref(
                provider=parsed["provider"],
                account_id=parsed["account_id"],
                spreadsheet_id=parsed["spreadsheet_id"],
            )
        )
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or SHEETS_NAMESPACE,
            object_ref=canonical_ref,
            extra={
                "event_source_id": f"named_services.{SHEETS_NAMESPACE}",
                "object_ref": canonical_ref,
                "target_surface": "sdk.sheets.snapshot",
            },
        )

    async def block_produce(
        self, ctx: NamedServiceContext, request: NamedServiceRequest
    ) -> NamedServiceResponse:
        target_value = request.payload.get("target")
        target = dict(target_value) if isinstance(target_value, Mapping) else {}
        object_ref = _text(
            request.object_ref
            or target.get("object_ref")
            or target.get("ref")
        )
        try:
            parsed = parse_sheets_ref(object_ref)
        except ValueError as exc:
            return self._invalid_ref(request, exc)
        unsupported = self._provider_not_supported(request, parsed)
        if unsupported is not None:
            return unsupported

        snapshot = _snapshot_from_block_target(target)
        if not snapshot:
            metadata, error = await self._execute(
                request=request,
                operation="describe",
                claim=SHEETS_READ_CLAIM,
                payload={"spreadsheet_ref": parsed["spreadsheet_id"]},
                account_id=parsed["account_id"],
            )
            if error is not None:
                return NamedServiceResponse.ok_response(
                    provider=self._provider_identity(),
                    namespace=request.namespace or SHEETS_NAMESPACE,
                    object_ref=object_ref,
                    extra={"blocks": []},
                    warnings=[
                        {
                            "code": "sheets_block_produce_describe_failed",
                            "message": (
                                error.error.message
                                if error.error is not None
                                else "Spreadsheet metadata could not be loaded."
                            ),
                        }
                    ],
                )
            metadata = metadata or {}
            spreadsheet = _spreadsheet_object(
                metadata,
                account_id=_text(metadata.get("account_id"))
                or parsed["account_id"],
                provider=parsed["provider"],
            )
            if parsed["kind"] == "tab":
                obj = next(
                    (
                        tab
                        for tab in spreadsheet.get("tabs") or []
                        if isinstance(tab, Mapping)
                        and _int(tab.get("sheet_id")) == parsed["sheet_id"]
                    ),
                    {},
                )
                object_kind = SHEETS_TAB_KIND
            else:
                obj = spreadsheet
                object_kind = SHEETS_SPREADSHEET_KIND
            snapshot = {
                "schema": SHEETS_SNAPSHOT_SCHEMA,
                "object_ref": object_ref,
                "object_kind": object_kind,
                "object": obj,
                "spreadsheet": spreadsheet,
                "values": {},
                "materialization": {
                    "values_materialized": True,
                    "complete_values": None,
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
        snapshot_spreadsheet = (
            snapshot.get("spreadsheet")
            if isinstance(snapshot.get("spreadsheet"), Mapping)
            else {}
        )
        snapshot_values = (
            snapshot.get("values")
            if isinstance(snapshot.get("values"), Mapping)
            else {}
        )
        source_stats.update(
            {
                "object_ref": object_ref,
                "object_kind": _text(snapshot.get("object_kind")),
                "snapshot_schema": _text(snapshot.get("schema")),
                "title": _text(
                    snapshot_object.get("title")
                    or snapshot_spreadsheet.get("title")
                ),
                "spreadsheet_id": _text(
                    snapshot_spreadsheet.get("spreadsheet_id")
                    or snapshot_object.get("spreadsheet_id")
                ),
                "tab_count": len(_snapshot_tabs(snapshot)),
                "range_count": (
                    _int(snapshot_values.get("range_count"))
                    if snapshot_values.get("range_count") is not None
                    else None
                ),
                "row_count": (
                    _int(snapshot_values.get("row_count"))
                    if snapshot_values.get("row_count") is not None
                    else None
                ),
                "cell_count": (
                    _int(snapshot_values.get("cell_count"))
                    if snapshot_values.get("cell_count") is not None
                    else None
                ),
            }
        )
        source_stats = {
            key: value for key, value in source_stats.items() if value is not None
        }
        block = {
            "turn": target.get("turn_id") or ctx.turn_id or "",
            "type": "react.tool.result",
            "call_id": target.get("tool_call_id") or "",
            "tool_id": "named_services.sheets",
            "event_source_id": f"named_services.{SHEETS_NAMESPACE}",
            "mime": "text/markdown",
            "path": object_ref,
            "text": text,
            "original_object_stats": source_stats,
            "meta": {
                "tool_call_id": target.get("tool_call_id") or "",
                "tool_id": target.get("tool_id") or "react.read",
                "turn_id": target.get("turn_id") or ctx.turn_id or "",
                "object_ref": object_ref,
                "source_namespace": SHEETS_NAMESPACE,
                "materialized_path": target.get("logical_path")
                or target.get("path")
                or "",
                "physical_path": target.get("physical_path")
                or meta.get("physical_path")
                or "",
                "object_kind": _text(snapshot.get("object_kind")),
                "mime": SHEETS_SNAPSHOT_MEDIA_TYPE,
                "render_policy": "sheets.named_service.block_produce",
            },
        }
        LOGGER.info(
            "[sheets.named_service.block_produce] produced object_ref=%s "
            "materialized_path=%s text_symbols=%s",
            object_ref,
            target.get("logical_path") or target.get("path") or "",
            len(text),
        )
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or SHEETS_NAMESPACE,
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
            account_id=_text(filters.get("account_id") or request.payload.get("account_id")),
        )

    async def object_search(
        self, ctx: NamedServiceContext, request: NamedServiceRequest
    ) -> NamedServiceResponse:
        del ctx
        filters = dict(request.filters or {})
        return await self._search(
            request=request,
            query=_text(request.query),
            account_id=_text(filters.get("account_id") or request.payload.get("account_id")),
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
            claim=SHEETS_READ_CLAIM,
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
            _spreadsheet_object(row, account_id=resolved_account_id)
            for row in ret.get("items") or []
            if isinstance(row, Mapping)
        ]
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or SHEETS_NAMESPACE,
            items=items,
            next_cursor=_text(ret.get("next_cursor")) or None,
            extra={
                "count": len(items),
                "query": query,
                "account_id": resolved_account_id,
            },
        )

    async def object_get(
        self, ctx: NamedServiceContext, request: NamedServiceRequest
    ) -> NamedServiceResponse | NamedServiceStreamResult:
        try:
            parsed = parse_sheets_ref(request.object_ref)
        except ValueError as exc:
            return self._invalid_ref(request, exc)
        unsupported = self._provider_not_supported(request, parsed)
        if unsupported is not None:
            return unsupported
        if _is_materialization_request(request):
            return await self._materialize_snapshot(request=request, parsed=parsed)
        filters = dict(request.filters or {})
        ranges = filters.get("ranges") or request.payload.get("ranges")
        if parsed["kind"] == "tab" and ranges:
            return NamedServiceResponse.error_response(
                code="sheets_spreadsheet_ref_required_for_ranges",
                message=(
                    "Range reads require the parent sheets spreadsheet ref; "
                    "put the tab title in each A1 range."
                ),
                status=400,
                provider=self._provider_identity(),
                namespace=request.namespace or SHEETS_NAMESPACE,
                object_ref=request.object_ref,
            )
        operation = "read" if ranges else "describe"
        payload: dict[str, Any] = {"spreadsheet_ref": parsed["spreadsheet_id"]}
        if ranges:
            payload.update(
                {
                    "ranges": ranges,
                    "major_dimension": filters.get("major_dimension"),
                    "value_render_option": filters.get("value_render_option"),
                    "date_time_render_option": filters.get(
                        "date_time_render_option"
                    ),
                }
            )
        ret, error = await self._execute(
            request=request,
            operation=operation,
            claim=SHEETS_READ_CLAIM,
            payload=payload,
            account_id=parsed["account_id"],
        )
        if error is not None:
            return error
        ret = ret or {}
        if parsed["kind"] == "tab":
            tabs = ret.get("tabs") or []
            tab = next(
                (
                    row
                    for row in tabs
                    if isinstance(row, Mapping)
                    and _int(row.get("sheet_id")) == parsed["sheet_id"]
                ),
                None,
            )
            if tab is None:
                return NamedServiceResponse.error_response(
                    code="sheets_tab_not_found",
                    message="The spreadsheet tab was not found.",
                    status=404,
                    provider=self._provider_identity(),
                    namespace=request.namespace or SHEETS_NAMESPACE,
                    object_ref=request.object_ref,
                )
            obj = _tab_object(
                tab,
                account_id=parsed["account_id"],
                spreadsheet_id=parsed["spreadsheet_id"],
                provider=parsed["provider"],
            )
        else:
            obj = _spreadsheet_object(
                ret,
                account_id=parsed["account_id"],
                provider=parsed["provider"],
            )
        if not ctx.turn_id:
            url_info = await self._download_url(ctx, ref=obj["ref"])
            if url_info is not None:
                snapshot_download = {
                    "schema": SHEETS_SNAPSHOT_SCHEMA,
                    "media_type": SHEETS_SNAPSHOT_MEDIA_TYPE,
                    "filename": _snapshot_filename(parsed),
                    "download": {"encoding": "url", **url_info},
                }
                # Put the complete-artifact escape hatch before potentially
                # large inline range values in serialized MCP responses.
                obj = {"snapshot": snapshot_download, **obj}
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or SHEETS_NAMESPACE,
            object_ref=obj["ref"],
            object=obj,
        )

    async def _materialize_snapshot(
        self,
        *,
        request: NamedServiceRequest,
        parsed: Mapping[str, Any],
    ) -> NamedServiceResponse | NamedServiceStreamResult:
        metadata, error = await self._execute(
            request=request,
            operation="describe",
            claim=SHEETS_READ_CLAIM,
            payload={"spreadsheet_ref": parsed["spreadsheet_id"]},
            account_id=_text(parsed.get("account_id")),
        )
        if error is not None:
            return error
        metadata = metadata or {}
        account_id = _text(metadata.get("account_id") or parsed.get("account_id"))
        spreadsheet = _spreadsheet_object(
            metadata,
            account_id=account_id,
            provider=_text(parsed.get("provider")) or GOOGLE_PROVIDER_KEY,
        )
        raw_tabs = [
            dict(tab)
            for tab in metadata.get("tabs") or []
            if isinstance(tab, Mapping)
        ]

        if parsed["kind"] == "tab":
            selected_tabs = [
                tab
                for tab in raw_tabs
                if _int(tab.get("sheet_id")) == _int(parsed.get("sheet_id"))
            ]
            if not selected_tabs:
                return NamedServiceResponse.error_response(
                    code="sheets_tab_not_found",
                    message="The spreadsheet tab was not found.",
                    status=404,
                    provider=self._provider_identity(),
                    namespace=request.namespace or SHEETS_NAMESPACE,
                    object_ref=request.object_ref,
                )
            materialized_object = _tab_object(
                selected_tabs[0],
                account_id=account_id,
                spreadsheet_id=_text(parsed.get("spreadsheet_id")),
                provider=_text(parsed.get("provider")) or GOOGLE_PROVIDER_KEY,
            )
        else:
            selected_tabs = raw_tabs
            materialized_object = spreadsheet

        grid_tabs = [
            tab
            for tab in selected_tabs
            if _text(tab.get("sheet_type") or "GRID").upper() == "GRID"
            and _text(tab.get("title"))
        ]
        skipped_tabs = [
            {
                "sheet_id": _int(tab.get("sheet_id")),
                "title": _text(tab.get("title")),
                "sheet_type": _text(tab.get("sheet_type")),
                "reason": "non_grid_tab",
            }
            for tab in selected_tabs
            if tab not in grid_tabs
        ]
        values: dict[str, Any] = {
            "spreadsheet_id": _text(parsed.get("spreadsheet_id")),
            "ranges": [],
            "range_count": 0,
            "row_count": 0,
            "cell_count": 0,
        }
        if grid_tabs:
            values_result, values_error = await self._execute(
                request=request,
                operation="read",
                claim=SHEETS_READ_CLAIM,
                payload={
                    "spreadsheet_ref": parsed["spreadsheet_id"],
                    "ranges": [
                        _whole_tab_range(tab.get("title")) for tab in grid_tabs
                    ],
                    "major_dimension": "ROWS",
                    "value_render_option": "FORMATTED_VALUE",
                    "date_time_render_option": "FORMATTED_STRING",
                },
                account_id=account_id,
            )
            if values_error is not None:
                return values_error
            values = dict(values_result or {})

        object_ref = _text(materialized_object.get("ref") or request.object_ref)
        snapshot = {
            "schema": SHEETS_SNAPSHOT_SCHEMA,
            "object_ref": object_ref,
            "object_kind": _text(materialized_object.get("object_kind")),
            "object": materialized_object,
            "spreadsheet": spreadsheet,
            "materialization": {
                "values_materialized": True,
                "complete_values": not skipped_tabs,
                "selected_tab_count": len(selected_tabs),
                "grid_tab_count": len(grid_tabs),
                "range_count": _int(values.get("range_count")),
                "cell_count": _int(values.get("cell_count")),
                "skipped_tabs": skipped_tabs,
                "delivery": (
                    "The complete selected grid values are included. Explicit A1 "
                    "range reads remain available when a client wants a partial "
                    "response or the upstream provider requires smaller requests."
                ),
            },
            # Keep inventory before the potentially large cell matrices in the
            # serialized artifact so streaming clients see its shape first.
            "values": values,
        }
        response = NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or SHEETS_NAMESPACE,
            object_ref=object_ref,
            attrs={
                "materialization": {
                    "schema": SHEETS_SNAPSHOT_SCHEMA,
                    "media_type": SHEETS_SNAPSHOT_MEDIA_TYPE,
                    "range_count": _int(values.get("range_count")),
                    "cell_count": _int(values.get("cell_count")),
                    "complete_values": not skipped_tabs,
                }
            },
        )
        return NamedServiceStreamResult(
            response=response,
            chunks=_json_chunks(snapshot),
            filename=_snapshot_filename(parsed),
            media_type=SHEETS_SNAPSHOT_MEDIA_TYPE,
        )

    async def object_upsert(
        self, ctx: NamedServiceContext, request: NamedServiceRequest
    ) -> NamedServiceResponse:
        del ctx
        body = {**dict(request.payload or {}), **dict(request.object or {})}
        if not request.object_ref:
            operation = "create_spreadsheet"
            payload = {
                key: body.get(key)
                for key in (
                    "title",
                    "first_tab_title",
                    "initial_values",
                    "value_input_option",
                )
                if body.get(key) is not None
            }
            if request.idempotency_key:
                payload["idempotency_key"] = request.idempotency_key
            account_id = _text(body.get("account_id"))
            parsed = None
        else:
            try:
                parsed = parse_sheets_ref(request.object_ref)
            except ValueError as exc:
                return self._invalid_ref(request, exc)
            unsupported = self._provider_not_supported(request, parsed)
            if unsupported is not None:
                return unsupported
            account_id = parsed["account_id"]
            if parsed["kind"] == "tab":
                operation = "update_tab"
                payload = {
                    key: body.get(key)
                    for key in (
                        "title",
                        "rows",
                        "columns",
                        "frozen_rows",
                        "frozen_columns",
                    )
                    if body.get(key) is not None
                }
                payload.update(
                    {
                        "spreadsheet_ref": parsed["spreadsheet_id"],
                        "sheet_id": parsed["sheet_id"],
                    }
                )
            else:
                operation = "update_values"
                payload = {
                    "spreadsheet_ref": parsed["spreadsheet_id"],
                    "updates": body.get("updates"),
                    "value_input_option": body.get("value_input_option"),
                }
        ret, error = await self._execute(
            request=request,
            operation=operation,
            claim=(SHEETS_READ_CLAIM, SHEETS_WRITE_CLAIM),
            payload=payload,
            account_id=account_id,
        )
        if error is not None:
            return error
        return self._mutation_response(request=request, ret=ret or {}, parsed=parsed)

    async def object_resolve(
        self, ctx: NamedServiceContext, request: NamedServiceRequest
    ) -> NamedServiceResponse:
        del ctx
        action = _text(request.action or "capabilities").lower()
        if action not in {"capabilities", "describe"}:
            return NamedServiceResponse.error_response(
                code="sheets_resolve_action_not_supported",
                message=f"Unsupported spreadsheet resolve action: {action}.",
                status=400,
                provider=self._provider_identity(),
                namespace=request.namespace or SHEETS_NAMESPACE,
                object_ref=request.object_ref,
            )
        try:
            parsed = parse_sheets_ref(request.object_ref)
        except ValueError as exc:
            return self._invalid_ref(request, exc)
        unsupported = self._provider_not_supported(request, parsed)
        if unsupported is not None:
            return unsupported
        object_kind = (
            SHEETS_TAB_KIND
            if parsed["kind"] == "tab"
            else SHEETS_SPREADSHEET_KIND
        )
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or SHEETS_NAMESPACE,
            object_ref=request.object_ref,
            capabilities={
                "preview": False,
                "open": True,
                "download": False,
                "rehost": False,
            },
            extra={
                "object_kind": object_kind,
                "default_open_effect_action": UI_ACTION_OPEN,
            },
        )

    async def _open_spreadsheet(
        self, request: NamedServiceRequest
    ) -> NamedServiceResponse:
        try:
            parsed = parse_sheets_ref(request.object_ref)
        except ValueError as exc:
            return self._invalid_ref(request, exc)
        unsupported = self._provider_not_supported(request, parsed)
        if unsupported is not None:
            return unsupported
        ret, error = await self._execute(
            request=request,
            operation="describe",
            claim=SHEETS_READ_CLAIM,
            payload={"spreadsheet_ref": parsed["spreadsheet_id"]},
            account_id=parsed["account_id"],
        )
        if error is not None:
            return error
        spreadsheet = _spreadsheet_object(
            ret or {},
            account_id=parsed["account_id"],
            provider=parsed["provider"],
        )
        obj: dict[str, Any] = spreadsheet
        external_url = _text(spreadsheet.get("web_url"))
        if parsed["kind"] == "tab":
            tab = next(
                (
                    row
                    for row in spreadsheet.get("tabs") or []
                    if isinstance(row, Mapping)
                    and _int(row.get("sheet_id")) == parsed["sheet_id"]
                ),
                None,
            )
            if tab is None:
                return NamedServiceResponse.error_response(
                    code="sheets_tab_not_found",
                    message="The spreadsheet tab was not found.",
                    status=404,
                    provider=self._provider_identity(),
                    namespace=request.namespace or SHEETS_NAMESPACE,
                    object_ref=request.object_ref,
                )
            obj = dict(tab)
            if external_url:
                external_url = (
                    f"{external_url.split('#', 1)[0]}#gid={parsed['sheet_id']}"
                )
        if not external_url:
            return NamedServiceResponse.error_response(
                code="sheets_open_url_unavailable",
                message="The spreadsheet provider did not return a browser URL.",
                status=409,
                provider=self._provider_identity(),
                namespace=request.namespace or SHEETS_NAMESPACE,
                object_ref=request.object_ref,
            )
        object_kind = _text(obj.get("object_kind")) or (
            SHEETS_TAB_KIND
            if parsed["kind"] == "tab"
            else SHEETS_SPREADSHEET_KIND
        )
        title = _text(obj.get("title") or spreadsheet.get("title"))
        capabilities = {
            "preview": False,
            "open": True,
            "download": False,
            "rehost": False,
        }
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or SHEETS_NAMESPACE,
            object_ref=request.object_ref,
            object=obj,
            capabilities=capabilities,
            ui_event={
                "type": "kdcube.ui.object.open.requested",
                "action": UI_ACTION_OPEN,
                "object_ref": request.object_ref,
                "external_url": external_url,
                "title": title,
            },
            extra={
                "action": UI_ACTION_OPEN,
                "object_kind": object_kind,
                "default_open_effect_action": UI_ACTION_OPEN,
                "external_url": external_url,
                "title": title,
            },
        )

    async def object_action(
        self, ctx: NamedServiceContext, request: NamedServiceRequest
    ) -> NamedServiceResponse:
        del ctx
        action = _text(request.action)
        if action == UI_ACTION_OPEN:
            return await self._open_spreadsheet(request)
        if action not in SHEETS_ACTIONS:
            return NamedServiceResponse.error_response(
                code="sheets_action_not_supported",
                message=f"Unsupported spreadsheet action: {action or '<missing>'}.",
                status=400,
                provider=self._provider_identity(),
                namespace=request.namespace or SHEETS_NAMESPACE,
                object_ref=request.object_ref,
            )
        try:
            parsed = parse_sheets_ref(request.object_ref)
        except ValueError as exc:
            return self._invalid_ref(request, exc)
        unsupported = self._provider_not_supported(request, parsed)
        if unsupported is not None:
            return unsupported
        if action in {ACTION_UPDATE_TAB, ACTION_DELETE_TAB, ACTION_FORMAT_RANGE}:
            if parsed["kind"] != "tab":
                return NamedServiceResponse.error_response(
                    code="sheets_tab_ref_required",
                    message=f"Action {action} requires a sheets tab ref.",
                    status=400,
                    provider=self._provider_identity(),
                    namespace=request.namespace or SHEETS_NAMESPACE,
                    object_ref=request.object_ref,
                )
        elif parsed["kind"] != "spreadsheet":
            return NamedServiceResponse.error_response(
                code="sheets_spreadsheet_ref_required",
                message=f"Action {action} requires a sheets spreadsheet ref.",
                status=400,
                provider=self._provider_identity(),
                namespace=request.namespace or SHEETS_NAMESPACE,
                object_ref=request.object_ref,
            )
        payload = dict(request.payload or {})
        payload["spreadsheet_ref"] = parsed["spreadsheet_id"]
        if parsed["kind"] == "tab":
            payload["sheet_id"] = parsed["sheet_id"]
        if request.idempotency_key:
            payload["idempotency_key"] = request.idempotency_key
        ret, error = await self._execute(
            request=request,
            operation=action,
            claim=(SHEETS_READ_CLAIM, SHEETS_WRITE_CLAIM),
            payload=payload,
            account_id=parsed["account_id"],
        )
        if error is not None:
            return error
        return self._mutation_response(request=request, ret=ret or {}, parsed=parsed)

    async def object_delete(
        self, ctx: NamedServiceContext, request: NamedServiceRequest
    ) -> NamedServiceResponse:
        del ctx
        try:
            parsed = parse_sheets_ref(request.object_ref)
        except ValueError as exc:
            return self._invalid_ref(request, exc)
        unsupported = self._provider_not_supported(request, parsed)
        if unsupported is not None:
            return unsupported
        if parsed["kind"] != "tab":
            return NamedServiceResponse.error_response(
                code="sheets_spreadsheet_delete_not_supported",
                message=(
                    "Spreadsheet-file deletion is not exposed. object.delete "
                    "accepts only a sheets tab ref."
                ),
                status=400,
                provider=self._provider_identity(),
                namespace=request.namespace or SHEETS_NAMESPACE,
                object_ref=request.object_ref,
            )
        ret, error = await self._execute(
            request=request,
            operation="delete_tab",
            claim=(SHEETS_READ_CLAIM, SHEETS_WRITE_CLAIM),
            payload={
                "spreadsheet_ref": parsed["spreadsheet_id"],
                "sheet_id": parsed["sheet_id"],
            },
            account_id=parsed["account_id"],
        )
        if error is not None:
            return error
        return self._mutation_response(request=request, ret=ret or {}, parsed=parsed)

    def _mutation_response(
        self,
        *,
        request: NamedServiceRequest,
        ret: Mapping[str, Any],
        parsed: Mapping[str, Any] | None,
    ) -> NamedServiceResponse:
        result = dict(ret or {})
        account_id = _text(result.get("account_id")) or _text(
            (parsed or {}).get("account_id")
        )
        spreadsheet_id = _text(result.get("spreadsheet_id")) or _text(
            (parsed or {}).get("spreadsheet_id")
        )
        provider = _text((parsed or {}).get("provider")) or GOOGLE_PROVIDER_KEY
        tab = result.get("tab")
        if isinstance(tab, Mapping):
            obj = _tab_object(
                tab,
                account_id=account_id,
                spreadsheet_id=spreadsheet_id,
                provider=provider,
            )
        elif _text((parsed or {}).get("kind")) == "tab":
            obj = _tab_object(
                {**result, "sheet_id": (parsed or {}).get("sheet_id")},
                account_id=account_id,
                spreadsheet_id=spreadsheet_id,
                provider=provider,
            )
            if "deleted_sheet_id" in result:
                obj["deleted"] = True
        else:
            obj = _spreadsheet_object(
                result,
                account_id=account_id,
                provider=provider,
            )
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or SHEETS_NAMESPACE,
            object_ref=obj["ref"],
            object=obj,
            extra={"action": request.action or request.operation, "result": result},
        )


def make_sheets_named_service_provider(
    *,
    execute_operation: ExecuteSheetsOperation,
    bundle_id: str | None = None,
    file_url_factory: Any = None,
) -> SheetsNamedServiceProvider:
    return SheetsNamedServiceProvider(
        execute_operation=execute_operation,
        bundle_id=bundle_id,
        file_url_factory=file_url_factory,
    )


__all__ = [
    "ACTION_ADD_TAB",
    "ACTION_APPEND_ROWS",
    "ACTION_CLEAR_VALUES",
    "ACTION_DELETE_TAB",
    "ACTION_FORMAT_RANGE",
    "ACTION_UPDATE_TAB",
    "ACTION_UPDATE_VALUES",
    "GOOGLE_PROVIDER_KEY",
    "SHEETS_CONNECTED_ACCOUNT_REQUIREMENTS",
    "SHEETS_GRANT_HINTS",
    "SHEETS_NAMESPACE",
    "SHEETS_SCHEMA",
    "SHEETS_SNAPSHOT_MEDIA_TYPE",
    "SHEETS_SNAPSHOT_SCHEMA",
    "SHEETS_SPREADSHEET_KIND",
    "SHEETS_TAB_KIND",
    "SheetsNamedServiceProvider",
    "make_sheets_named_service_provider",
    "parse_sheets_ref",
    "sheets_named_service_spec",
    "spreadsheet_ref",
    "tab_ref",
]
