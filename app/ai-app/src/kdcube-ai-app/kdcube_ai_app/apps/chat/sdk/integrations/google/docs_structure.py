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
OBJECT_KINDS = {
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

# The route this deployment serves a staged file from for one provider fetch.
PROVIDER_FETCH_PATH = "/public/provider_fetch_download"


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


def magnitude_pt(value: Any) -> float | None:
    """A Docs dimension in points, when points is the unit it came in."""

    if not isinstance(value, Mapping):
        return None
    if str(value.get("unit") or "PT").upper() != "PT":
        return None
    try:
        return round(float(value.get("magnitude")), 1)
    except (TypeError, ValueError):
        return None


def _image_record(
    value: Mapping[str, Any], inline_objects: Mapping[str, Any] | None
) -> dict[str, Any]:
    """An inline image, by what a reader can act on: its id, alt text, and size.

    The properties live in the document-level ``inlineObjects`` map, which the
    element points into by id. ``contentUri`` is left out: measured
    2026-09-25, it serves the bytes to anyone who holds it, with no credential
    at all, until it expires - so passing it on hands out the image.
    """

    record: dict[str, Any] = {"kind": "image"}
    object_id = str(value.get("inlineObjectId") or "").strip()
    if object_id:
        record["object_id"] = object_id
    entry = (
        inline_objects.get(object_id)
        if object_id and isinstance(inline_objects, Mapping)
        else None
    )
    properties = (
        entry.get("inlineObjectProperties") if isinstance(entry, Mapping) else None
    )
    embedded = (
        properties.get("embeddedObject") if isinstance(properties, Mapping) else None
    )
    if not isinstance(embedded, Mapping):
        return record
    for field, key in (("alt", "description"), ("title", "title")):
        text = str(embedded.get(key) or "").strip()
        if text:
            record[field] = text
    size = embedded.get("size")
    if isinstance(size, Mapping):
        for field, key in (("width_pt", "width"), ("height_pt", "height")):
            magnitude = magnitude_pt(size.get(key))
            if magnitude is not None:
                record[field] = magnitude
    image = embedded.get("imageProperties")
    if isinstance(image, Mapping):
        # Empty for an image Docs holds no origin for; present when it was inserted by URL.
        source = str(image.get("sourceUri") or "").strip()
        if PROVIDER_FETCH_PATH in source:
            # Our own one-fetch URL, which the provider recorded when it took
            # the file. It is dead by the time anyone reads it - the staged
            # copy is deleted and the token expires - so handing it back would
            # offer a dead end and carry the token into whatever stores this
            # read. Saying where the picture came from is the useful part.
            record["source"] = "kdcube_file"
        elif source:
            record["source_uri"] = source
    return record


def inline_image_record(
    object_id: str, inline_objects: Mapping[str, Any] | None
) -> dict[str, Any]:
    """One image by id, described exactly as a cell read describes it."""

    return _image_record({"inlineObjectId": object_id}, inline_objects)


def object_record(
    kind: str,
    value: Any,
    *,
    inline_objects: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """What a non-text element is, and what it takes to write it back."""

    record: dict[str, Any] = {"kind": kind}
    if kind == "image" and isinstance(value, Mapping):
        return _image_record(value, inline_objects)
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
            # Docs answers with displayText - how the chip reads in the
            # document, by its locale - and with the instant behind it.
            text = str(properties.get("displayText") or "").strip()
            if text:
                record["text"] = text
            timestamp = str(properties.get("timestamp") or "").strip()
            if timestamp:
                record["timestamp"] = timestamp
    return record


def _cell_record(
    cell: Mapping[str, Any],
    *,
    inline_objects: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
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
                for key, kind in OBJECT_KINDS.items():
                    if key in element:
                        objects.append(
                            object_record(
                                kind, element[key], inline_objects=inline_objects
                            )
                        )
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


def _grid(
    table: Mapping[str, Any],
    *,
    inline_objects: Mapping[str, Any] | None = None,
) -> tuple[list[list[dict[str, Any]]], int, int]:
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
            record = _cell_record(cell, inline_objects=inline_objects)
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


def column_widths(table: Mapping[str, Any]) -> list[float | None]:
    """Each column's width in points, or None where the table distributes evenly."""

    style = table.get("tableStyle")
    properties = (
        style.get("tableColumnProperties") if isinstance(style, Mapping) else None
    )
    widths: list[float | None] = []
    for entry in properties or []:
        width = entry.get("width") if isinstance(entry, Mapping) else None
        widths.append(magnitude_pt(width))
    return widths


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
    inline_objects: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Top-level tables of one tab body, in document order.

    ``inline_objects`` is the map the body's tab carries; without it an image
    cell reports the kind alone.
    """

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
        cells, rows, columns = _grid(table, inline_objects=inline_objects)
        tables.append(
            {
                "tab_id": tab_id,
                "tab_title": tab_title,
                "position": len(tables) + 1,
                "after_heading": dict(heading) if heading else None,
                "rows": rows,
                "columns": columns,
                "header_rows": _header_rows(table),
                "column_widths": column_widths(table),
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


def table_grid(
    table: Mapping[str, Any],
    *,
    inline_objects: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One table's cells, size, and header rows, from the same walk as the rest."""

    cells, rows, columns = _grid(table, inline_objects=inline_objects)
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
    "column_widths",
    "magnitude_pt",
    "OBJECT_KINDS",
    "object_record",
    "inline_image_record",
    "paragraph_text",
    "public_cell",
    "body_segments",
    "table_grid",
    "public_table",
]
