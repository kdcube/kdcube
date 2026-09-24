# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Tables of a Google Docs body as provider-neutral records.

One walk over a tab body yields every top-level table with its tab, 1-based
position, nearest heading above it, header rows, and cells. Each cell carries
its text plus the Google indices a writer needs (``start_index``,
``end_index``, ``content_start``, ``content_end``); ``public_table`` and
``public_cell`` drop those indices for agent-facing output.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

_HEADING_LEVELS = {
    "TITLE": 0,
    "SUBTITLE": 0,
    **{f"HEADING_{level}": level for level in range(1, 7)},
}

# Paragraph element kinds other than text runs, named as a caller sees them.
_OBJECT_KINDS = {
    "inlineObjectElement": "image",
    "person": "person",
    "richLink": "rich_link",
    "dateElement": "date",
    "equation": "equation",
    "footnoteReference": "footnote",
    "horizontalRule": "horizontal_rule",
    "autoText": "auto_text",
}

_INDEX_KEYS = ("start_index", "end_index", "content_start", "content_end")


def _int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def paragraph_text(paragraph: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for element in paragraph.get("elements") or []:
        if not isinstance(element, Mapping):
            continue
        text_run = element.get("textRun")
        if isinstance(text_run, Mapping):
            parts.append(str(text_run.get("content") or ""))
    return "".join(parts)


def _heading(paragraph: Mapping[str, Any]) -> dict[str, Any] | None:
    style = paragraph.get("paragraphStyle")
    named = str((style or {}).get("namedStyleType") or "") if isinstance(style, Mapping) else ""
    if named not in _HEADING_LEVELS:
        return None
    text = paragraph_text(paragraph).strip()
    if not text:
        return None
    return {"text": text, "level": _HEADING_LEVELS[named]}


def _object_record(kind: str, value: Any) -> dict[str, Any]:
    """What a non-text element is, and what it takes to write it back."""

    record: dict[str, Any] = {"kind": kind}
    properties = value.get(f"{kind}Properties") if isinstance(value, Mapping) else None
    if kind == "person" and isinstance(value, Mapping):
        properties = value.get("personProperties")
        if isinstance(properties, Mapping):
            for field in ("email", "name"):
                text = str(properties.get(field) or "").strip()
                if text:
                    record[field] = text
    elif kind == "rich_link" and isinstance(value, Mapping):
        properties = value.get("richLinkProperties")
        if isinstance(properties, Mapping):
            for field, key in (("uri", "uri"), ("title", "title")):
                text = str(properties.get(key) or "").strip()
                if text:
                    record[field] = text
    elif kind == "date" and isinstance(value, Mapping):
        properties = value.get("dateElementProperties")
        if isinstance(properties, Mapping):
            text = str(properties.get("text") or "").strip()
            if text:
                record["text"] = text
    return record


def _cell_record(cell: Mapping[str, Any]) -> dict[str, Any]:
    content = [block for block in cell.get("content") or [] if isinstance(block, Mapping)]
    paragraphs: list[str] = []
    objects: list[dict[str, Any]] = []
    nested = False
    for block in content:
        paragraph = block.get("paragraph")
        if isinstance(paragraph, Mapping):
            paragraphs.append(paragraph_text(paragraph))
            for element in paragraph.get("elements") or []:
                if not isinstance(element, Mapping):
                    continue
                for key, kind in _OBJECT_KINDS.items():
                    if key in element:
                        objects.append(_object_record(kind, element[key]))
        elif isinstance(block.get("table"), Mapping):
            nested = True
    text = "".join(paragraphs)
    if text.endswith("\n"):
        text = text[:-1]
    start = _int(cell.get("startIndex"))
    end = _int(cell.get("endIndex"))
    content_start = _int(content[0].get("startIndex"), default=start + 1) if content else start + 1
    # The cell's final newline cannot be deleted; content ends just before it.
    content_end = _int(content[-1].get("endIndex"), default=end) - 1 if content else content_start
    style = cell.get("tableCellStyle") if isinstance(cell.get("tableCellStyle"), Mapping) else {}
    record: dict[str, Any] = {
        "text": text,
        "start_index": start,
        "end_index": end,
        "content_start": content_start,
        "content_end": max(content_start, content_end),
    }
    row_span = _int(style.get("rowSpan"), default=1)
    column_span = _int(style.get("columnSpan"), default=1)
    if row_span > 1:
        record["row_span"] = row_span
    if column_span > 1:
        record["column_span"] = column_span
    if nested:
        record["nested_table"] = True
    if objects:
        record["objects"] = objects
    return record


def _grid(table: Mapping[str, Any]) -> tuple[list[list[dict[str, Any]]], int, int]:
    rows_raw = [row for row in table.get("tableRows") or [] if isinstance(row, Mapping)]
    columns = _int(table.get("columns"))
    raw_cells = [
        [cell for cell in row.get("tableCells") or [] if isinstance(cell, Mapping)]
        for row in rows_raw
    ]
    columns = max([columns, *(len(cells) for cells in raw_cells)] or [0])
    grid: list[list[dict[str, Any] | None]] = [[None] * columns for _ in raw_cells]
    covered: dict[tuple[int, int], list[int]] = {}
    for r, cells in enumerate(raw_cells):
        full_row = len(cells) == columns
        c = 0
        for cell in cells:
            if not full_row:
                while c < columns and (r, c) in covered:
                    c += 1
            if c >= columns:
                break
            record = _cell_record(cell)
            if (r, c) in covered:
                record["merged_into"] = covered[(r, c)]
            grid[r][c] = record
            for dr in range(record.get("row_span", 1)):
                for dc in range(record.get("column_span", 1)):
                    if dr or dc:
                        covered[(r + dr, c + dc)] = [r + 1, c + 1]
            c += 1
    filled: list[list[dict[str, Any]]] = []
    for r, row in enumerate(grid):
        out_row: list[dict[str, Any]] = []
        for c, record in enumerate(row):
            if record is None:
                record = {"text": ""}
                if (r, c) in covered:
                    record["merged_into"] = covered[(r, c)]
            out_row.append(record)
        filled.append(out_row)
    return filled, len(filled), columns


def _header_rows(table: Mapping[str, Any]) -> int:
    count = 0
    for row in table.get("tableRows") or []:
        style = row.get("tableRowStyle") if isinstance(row, Mapping) else None
        if isinstance(style, Mapping) and style.get("tableHeader") is True:
            count += 1
            continue
        break
    return count


def body_tables(
    body: Mapping[str, Any] | None,
    *,
    tab_id: str = "",
    tab_title: str = "",
) -> list[dict[str, Any]]:
    """Top-level tables of one tab body, in document order."""

    tables: list[dict[str, Any]] = []
    heading: dict[str, Any] | None = None
    content = body.get("content") if isinstance(body, Mapping) else None
    for block in content or []:
        if not isinstance(block, Mapping):
            continue
        paragraph = block.get("paragraph")
        if isinstance(paragraph, Mapping):
            heading = _heading(paragraph) or heading
            continue
        table = block.get("table")
        if not isinstance(table, Mapping):
            continue
        cells, rows, columns = _grid(table)
        tables.append(
            {
                "tab_id": tab_id,
                "tab_title": tab_title,
                "position": len(tables) + 1,
                "after_heading": dict(heading) if heading else None,
                "rows": rows,
                "columns": columns,
                "header_rows": _header_rows(table),
                "has_merged_cells": any(
                    "merged_into" in cell or "row_span" in cell or "column_span" in cell
                    for row in cells
                    for cell in row
                ),
                "start_index": _int(block.get("startIndex")),
                "end_index": _int(block.get("endIndex")),
                "cells": cells,
            }
        )
    return tables


def body_segments(body: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Every addressable piece of one tab body, with the range it occupies.

    A caller about to write at an index needs to know what already lives there;
    the same walk answers that as answers the table reads.
    """

    segments: list[dict[str, Any]] = []
    content = body.get("content") if isinstance(body, Mapping) else None
    position = 0
    for block in content or []:
        if not isinstance(block, Mapping):
            continue
        paragraph = block.get("paragraph")
        if isinstance(paragraph, Mapping):
            heading = _heading(paragraph)
            segments.append(
                {
                    "kind": "heading" if heading else "paragraph",
                    "start": _int(block.get("startIndex")),
                    "end": _int(block.get("endIndex")),
                    "text": paragraph_text(paragraph).rstrip("\n"),
                }
            )
            continue
        table = block.get("table")
        if isinstance(table, Mapping):
            position += 1
            cells, _rows, _columns = _grid(table)
            for row_number, row in enumerate(cells, start=1):
                for column, cell in enumerate(row, start=1):
                    if cell.get("merged_into"):
                        continue
                    segments.append(
                        {
                            "kind": "cell",
                            "start": _int(cell.get("content_start")),
                            "end": _int(cell.get("content_end")),
                            "text": str(cell.get("text") or ""),
                            "table": position,
                            "row": row_number,
                            "column": column,
                        }
                    )
    return sorted(segments, key=lambda row: row["start"])


def table_grid(table: Mapping[str, Any]) -> dict[str, Any]:
    """One table's cells, size, and header rows, from the same walk as the rest."""

    cells, rows, columns = _grid(table)
    return {
        "cells": cells,
        "rows": rows,
        "columns": columns,
        "header_rows": _header_rows(table),
    }


def public_cell(
    cell: Mapping[str, Any], *, row: int = 0, column: int = 0
) -> dict[str, Any]:
    """Agent-facing cell. Its own row and column spare the reader the counting."""

    out: dict[str, Any] = {}
    if row and column:
        out = {"row": row, "column": column}
    out.update(
        {key: value for key, value in cell.items() if key not in _INDEX_KEYS}
    )
    return out


def public_table(
    table: Mapping[str, Any],
    *,
    tab_selector: Mapping[str, Any] | None = None,
    include_cells: bool = False,
) -> dict[str, Any]:
    """Agent-facing table summary; cells only when asked, never indices."""

    header_rows = _int(table.get("header_rows"))
    cells: Sequence[Sequence[Mapping[str, Any]]] = table.get("cells") or []
    selector: dict[str, Any] = {"table": {"position": table.get("position")}}
    if tab_selector:
        selector = {"tab_selector": dict(tab_selector), **selector}
    header_row = cells[header_rows - 1] if header_rows and len(cells) >= header_rows else None
    out: dict[str, Any] = {
        "selector": selector,
        "tab_id": table.get("tab_id"),
        "tab_title": table.get("tab_title"),
        "position": table.get("position"),
        "after_heading": table.get("after_heading"),
        "rows": table.get("rows"),
        "columns": table.get("columns"),
        "header_rows": header_rows,
        "header": [cell.get("text", "") for cell in header_row] if header_row else None,
        "first_row": (
            None
            if header_row or not cells
            else [cell.get("text", "") for cell in cells[0]]
        ),
        "has_merged_cells": bool(table.get("has_merged_cells")),
    }
    if include_cells:
        out["cells"] = [
            [
                public_cell(cell, row=number, column=index + 1)
                for index, cell in enumerate(row)
            ]
            for number, row in enumerate(cells, start=1)
        ]
    return out


__all__ = [
    "body_tables",
    "paragraph_text",
    "public_cell",
    "body_segments",
    "table_grid",
    "public_table",
]
