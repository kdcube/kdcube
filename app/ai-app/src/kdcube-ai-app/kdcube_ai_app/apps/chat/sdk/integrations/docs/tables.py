# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Natural, provider-neutral selectors for document tables, rows, and columns.

Operates on table records shaped like ``google.docs_structure.body_tables``
output (tab, position, nearest heading, header rows, cells with ``text``)
and never on provider indices. Rows and columns are 1-based and physical:
header rows are counted but never matched by a ``where`` predicate.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from kdcube_ai_app.apps.chat.sdk.integrations.docs.selectors import (
    SELECTOR_CANDIDATE_LIMIT,
    DocsSelectorError,
)

DEFAULT_TABLE_ROWS = 50
MAX_TABLE_READ_CELLS = 2_000
MAX_TABLE_READS = 5


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized(value: Any) -> str:
    return " ".join(_text(value).casefold().split())


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _cells(table: Mapping[str, Any]) -> list[list[Mapping[str, Any]]]:
    return [list(row) for row in table.get("cells") or []]


def effective_header_rows(table: Mapping[str, Any], header: Any = None) -> int:
    """Header rows from the document, or the caller's explicit count."""

    rows = int(table.get("rows") or 0)
    if header is None or header == "":
        return int(table.get("header_rows") or 0)
    if isinstance(header, bool):
        raise DocsSelectorError(
            "docs_table_selector_invalid",
            "header must be a non-negative number of header rows.",
            status=400,
            details={"header": header},
        )
    try:
        count = int(header)
    except (TypeError, ValueError):
        count = -1
    if count < 0 or count > rows:
        raise DocsSelectorError(
            "docs_table_selector_invalid",
            f"header must be a number of header rows between 0 and {rows}.",
            status=400,
            details={"header": header, "rows": rows},
        )
    return count


def header_names(table: Mapping[str, Any], header_rows: int) -> list[str] | None:
    cells = _cells(table)
    if header_rows < 1 or len(cells) < header_rows:
        return None
    return [_text(cell.get("text")) for cell in cells[header_rows - 1]]


def table_candidate(table: Mapping[str, Any]) -> dict[str, Any]:
    cells = _cells(table)
    header_rows = int(table.get("header_rows") or 0)
    names = header_names(table, header_rows)
    return {
        "position": table.get("position"),
        "after_heading": table.get("after_heading"),
        "rows": table.get("rows"),
        "columns": table.get("columns"),
        "header": names,
        "first_row": (
            None if names is not None or not cells
            else [_text(cell.get("text")) for cell in cells[0]]
        ),
    }


def _table_selector(selector: Any) -> dict[str, Any]:
    if isinstance(selector, bool):
        selector = None
    if isinstance(selector, int):
        return {"position": selector}
    if isinstance(selector, str) and selector.strip():
        return {"after_heading": selector}
    if isinstance(selector, Mapping):
        return dict(selector)
    raise DocsSelectorError(
        "docs_table_selector_invalid",
        "table must be a position number or an object with position, "
        "after_heading, or header_contains.",
        status=400,
        details={"table": selector},
    )


def resolve_table(
    tables: Sequence[Mapping[str, Any]],
    selector: Any,
) -> Mapping[str, Any]:
    """Resolve one table of a tab by position, nearest heading, or header text.

    ``position`` alone counts tables in the tab; combined with another
    predicate it counts the tables that predicate matched.
    """

    raw = _table_selector(selector)
    after_heading = _normalized(raw.get("after_heading"))
    header_contains = _normalized(raw.get("header_contains"))
    position = _positive_int(raw.get("position"))
    if raw.get("position") not in (None, "") and position is None:
        raise DocsSelectorError(
            "docs_table_selector_invalid",
            "table.position must be a positive 1-based integer.",
            status=400,
            details={"table": raw},
        )
    if not any((after_heading, header_contains, position)):
        raise DocsSelectorError(
            "docs_table_selector_invalid",
            "table must provide position, after_heading, or header_contains.",
            status=400,
            details={"table": raw},
        )

    matches: list[Mapping[str, Any]] = []
    for table in tables:
        if after_heading:
            heading = table.get("after_heading") or {}
            if _normalized(heading.get("text")) != after_heading:
                continue
        if header_contains:
            header_rows = int(table.get("header_rows") or 0)
            cells = _cells(table)
            row = cells[header_rows - 1] if header_rows else (cells[0] if cells else [])
            if not any(header_contains in _normalized(cell.get("text")) for cell in row):
                continue
        matches.append(table)
    if position is not None:
        matches = matches[position - 1 : position] if position <= len(matches) else []

    candidates = [table_candidate(table) for table in tables]
    if not matches:
        raise DocsSelectorError(
            "docs_table_not_found",
            "No table matches the supplied table selector.",
            status=404,
            details={
                "table": raw,
                "candidate_count": len(candidates),
                "candidates": candidates[:SELECTOR_CANDIDATE_LIMIT],
                "candidates_truncated": len(candidates) > SELECTOR_CANDIDATE_LIMIT,
                "next_action": (
                    "Choose one returned table by position, after_heading, or "
                    "header_contains."
                ),
            },
        )
    if len(matches) > 1:
        found = [table_candidate(table) for table in matches]
        raise DocsSelectorError(
            "docs_table_ambiguous",
            "The table selector matches more than one table.",
            status=409,
            details={
                "table": raw,
                "match_count": len(matches),
                "candidates": found[:SELECTOR_CANDIDATE_LIMIT],
                "candidates_truncated": len(found) > SELECTOR_CANDIDATE_LIMIT,
                "next_action": "Add position to choose one of the matching tables.",
            },
        )
    return matches[0]


def spread_table_selector(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Accept a ``tables[].selector`` as returned by a read.

    ``selector`` is spread into ``tab_selector`` and ``table``; a whole selector
    nested under ``table`` is unwrapped the same way. Explicit top-level fields win.
    """

    out = dict(payload or {})
    selector = out.pop("selector", None)
    nested = out.get("table")
    if isinstance(nested, Mapping) and "table" in nested:
        selector = {**dict(nested), **(dict(selector) if isinstance(selector, Mapping) else {})}
        out.pop("table")
    if isinstance(selector, Mapping):
        for key in ("tab_selector", "table"):
            if key in selector and out.get(key) in (None, "", {}):
                out[key] = selector[key]
    return out


def resolve_column(
    table: Mapping[str, Any],
    column: Any,
    *,
    header_rows: int,
) -> int:
    """Resolve a column by 1-based number, or by header name when a header exists.

    A string is a header name first; a digit string that names no header is a
    number. An integer is always a number.
    """

    columns = int(table.get("columns") or 0)
    names = header_names(table, header_rows)
    if isinstance(column, str) and column.strip().isdigit():
        wanted = _normalized(column)
        if not names or not any(_normalized(value) == wanted for value in names):
            column = int(column.strip())
    number = column if isinstance(column, int) and not isinstance(column, bool) else None
    if number is not None:
        if 1 <= number <= columns:
            return number
        raise DocsSelectorError(
            "docs_table_column_not_found",
            f"Column {number} is outside this table's 1-{columns} columns.",
            status=404,
            details={"column": column, "columns": columns, "header": names},
        )
    name = _normalized(column)
    if not name:
        raise DocsSelectorError(
            "docs_table_selector_invalid",
            "A column is a 1-based number or a header name.",
            status=400,
            details={"column": column},
        )
    if names is None:
        raise DocsSelectorError(
            "docs_table_no_header",
            "This table has no header row, so its columns have no names. Name the "
            "column by number, or pass header=1 when the first row is a header.",
            status=409,
            details={
                "column": column,
                "columns": columns,
                "first_row": [_text(cell.get("text")) for cell in (_cells(table) or [[]])[0]],
            },
        )
    found = [index for index, value in enumerate(names, start=1) if _normalized(value) == name]
    if len(found) == 1:
        return found[0]
    raise DocsSelectorError(
        "docs_table_column_ambiguous" if found else "docs_table_column_not_found",
        (
            f"More than one column is named '{_text(column)}'. Name it by number."
            if found
            else f"No column is named '{_text(column)}'."
        ),
        status=409 if found else 404,
        details={"column": column, "header": names, "matching_columns": found},
    )


def _row_candidate(
    table: Mapping[str, Any], row: int, column: int | None
) -> dict[str, Any]:
    cells = _cells(table)[row - 1]
    if column is not None:
        return {"row": row, "value": _text(cells[column - 1].get("text"))}
    return {"row": row, "cells": [_text(cell.get("text")) for cell in cells]}


def resolve_row(
    table: Mapping[str, Any],
    selector: Any,
    *,
    header_rows: int,
) -> int:
    """Resolve a physical 1-based row by number or by a ``where`` predicate."""

    rows = int(table.get("rows") or 0)
    if isinstance(selector, Mapping) and "where" not in selector:
        selector = selector.get("number", selector.get("position"))
    if isinstance(selector, int) and not isinstance(selector, bool):
        if 1 <= selector <= rows:
            return selector
        raise DocsSelectorError(
            "docs_table_row_not_found",
            f"Row {selector} is outside this table's 1-{rows} rows.",
            status=404,
            details={"row": selector, "rows": rows, "header_rows": header_rows},
        )
    where = selector.get("where") if isinstance(selector, Mapping) else None
    if not isinstance(where, Mapping):
        raise DocsSelectorError(
            "docs_table_selector_invalid",
            "row must be a 1-based number or {where: {column, equals | contains}}.",
            status=400,
            details={"row": selector},
        )
    equals_supplied = "equals" in where and where.get("equals") is not None
    contains = _normalized(where.get("contains"))
    if equals_supplied == bool(contains):
        raise DocsSelectorError(
            "docs_table_selector_invalid",
            "row.where needs exactly one of equals or contains.",
            status=400,
            details={"row": selector},
        )
    column = resolve_column(table, where.get("column"), header_rows=header_rows)
    equals = _normalized(where.get("equals"))
    matches: list[int] = []
    for row in range(header_rows + 1, rows + 1):
        value = _normalized(_cells(table)[row - 1][column - 1].get("text"))
        if (equals_supplied and value == equals) or (contains and contains in value):
            matches.append(row)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        candidates = [
            _row_candidate(table, row, column) for row in range(header_rows + 1, rows + 1)
        ]
        raise DocsSelectorError(
            "docs_table_row_not_found",
            "No row matches the supplied row.where predicate.",
            status=404,
            details={
                "row": selector,
                "column": column,
                "candidates": candidates[:SELECTOR_CANDIDATE_LIMIT],
                "candidates_truncated": len(candidates) > SELECTOR_CANDIDATE_LIMIT,
            },
        )
    found = [_row_candidate(table, row, None) for row in matches]
    raise DocsSelectorError(
        "docs_table_row_ambiguous",
        "The row.where predicate matches more than one row.",
        status=409,
        details={
            "row": selector,
            "match_count": len(matches),
            "candidates": found[:SELECTOR_CANDIDATE_LIMIT],
            "candidates_truncated": len(found) > SELECTOR_CANDIDATE_LIMIT,
            "next_action": "Name the row by number, or narrow the predicate.",
        },
    )


_ROWS_RE = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+)\s*)?$")


def parse_row_range(value: Any, *, rows: int) -> tuple[int, int]:
    """A 1-based inclusive ``"first-last"`` range, defaulting to the first rows."""

    if value in (None, ""):
        return (1, min(rows, DEFAULT_TABLE_ROWS)) if rows else (1, 0)
    match = _ROWS_RE.match(str(value))
    first = int(match.group(1)) if match else 0
    last = int(match.group(2) or match.group(1)) if match else 0
    if not match or first < 1 or last < first:
        raise DocsSelectorError(
            "docs_table_selector_invalid",
            "rows must look like \"1-50\" or \"7\".",
            status=400,
            details={"rows": value},
        )
    return first, min(last, rows)


__all__ = [
    "DEFAULT_TABLE_ROWS",
    "MAX_TABLE_READ_CELLS",
    "MAX_TABLE_READS",
    "effective_header_rows",
    "header_names",
    "parse_row_range",
    "resolve_column",
    "resolve_row",
    "resolve_table",
    "spread_table_selector",
    "table_candidate",
]
