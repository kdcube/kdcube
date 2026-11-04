"""Physical Slides cells with the shared Docs row/column selector vocabulary."""
from __future__ import annotations

from collections.abc import Mapping

from kdcube_ai_app.apps.chat.sdk.integrations.docs.selectors import DocsSelectorError
from kdcube_ai_app.apps.chat.sdk.integrations.docs.tables import effective_header_rows, resolve_column, resolve_row
from kdcube_ai_app.apps.chat.sdk.integrations.google.slides_fit import shape_text
from kdcube_ai_app.apps.chat.sdk.integrations.slides.editing import content, utf16_len
from kdcube_ai_app.apps.chat.sdk.integrations.slides.geometry import element_properties
from kdcube_ai_app.apps.chat.sdk.integrations.slides.layouts import new_id, walk
from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError, resolve_element, resolve_slide

TABLE_ACTIONS = frozenset({"set_cells", "add_row"})
MAX_CELLS = 2000
MAX_WRITE_CELLS = 500
MAX_TEXT = 100000


def invalid(message, code="slides_table_selector_invalid"):
    raise SlidesValidationError(code, message)


def shared_selector(function, *args, **kwargs):
    """Preserve the common selector semantics while naming the Slides surface."""
    try:
        return function(*args, **kwargs)
    except DocsSelectorError as exc:
        raise SlidesValidationError(exc.code.replace("docs_", "slides_", 1), str(exc), details=exc.details) from exc


def table_record(raw):
    table = raw.get("table", {})
    rows, columns = table.get("rows"), table.get("columns")
    if any(type(v) is not int or v < 1 for v in (rows, columns)) or rows * columns > MAX_CELLS:
        invalid(f"Use a table with at most {MAX_CELLS} physical cells for selector edits.", "slides_table_too_large")
    # Merged cells may leave gaps in tableCells. Their location and spans, not
    # the list position, determine every physical slot.
    cells = [[{"text": "", "merged": True} for _ in range(columns)] for _ in range(rows)]
    for ri, row in enumerate(table.get("tableRows", [])):
        for ci, cell in enumerate(row.get("tableCells", [])):
            location = cell.get("location", {})
            r, c = location.get("rowIndex", ri), location.get("columnIndex", ci)
            rs, cs = cell.get("rowSpan", 1), cell.get("columnSpan", 1)
            if any(type(v) is not int for v in (r, c, rs, cs)) or min(r, c) < 0 or min(rs, cs) < 1 or r+rs > rows or c+cs > columns:
                invalid("Google returned an invalid table cell location. Read the presentation again.")
            value = {"text": shape_text(cell), "merged": rs > 1 or cs > 1, "row_span": rs, "column_span": cs}
            for rr in range(r, r+rs):
                for cc in range(c, c+cs):
                    cells[rr][cc] = {**value, "text": value["text"] if (rr, cc) == (r, c) else ""}
    return {"rows": rows, "columns": columns, "header_rows": 0, "cells": cells}


def table_snapshot(raw, *, start=1, limit=20, budget=100000):
    if type(start) is not int or start < 1 or type(limit) is not int or not 1 <= limit <= 50:
        invalid("Use table_start_row from 1 and table_row_limit from 1 to 50.")
    dimensions = {k: raw["table"].get(k) for k in ("rows", "columns")}
    try:
        table = table_record(raw)
    except SlidesValidationError as exc:
        return {**dimensions, "cells": [], "unavailable": exc.code, "selector_editable": False, "text_truncated": True}, 0
    rows, used, truncated = [], 0, False
    for r in range(start-1, min(table["rows"], start-1+limit)):
        cells = []
        for c, cell in enumerate(table["cells"][r]):
            text = cell["text"][:max(0, min(12000, budget-used))]
            used += len(text)
            clipped = len(text) < len(cell["text"])
            truncated |= clipped
            cells.append({"column": c+1, **{k: cell[k] for k in ("merged", "row_span", "column_span") if k in cell}, "text": text, "text_truncated": clipped})
        rows.append({"row": r+1, "cells": cells})
    next_row = start+len(rows)
    return {**dimensions, "header_rows": 0, "cells": rows, "start_row": start, "next_row": next_row if next_row <= table["rows"] else None, "text_truncated": truncated or next_row <= table["rows"], "selector_editable": True}, used


def resolve_table(presentation, view, payload):
    slide = resolve_slide(view["slides"], payload.get("slide"))
    selector = payload.get("table")
    if isinstance(selector, str):
        selector = {"ref": selector}
    if not isinstance(selector, Mapping):
        invalid("Pass table as an element selector from the slide snapshot.")
    if selector.get("kind", "table") != "table":
        invalid("Select a table element.")
    element = resolve_element(slide, {**selector, "kind": "table"})
    raw_slide = next(s for s in presentation["slides"] if s["objectId"] == slide["ref"])
    raw = next(e for e in walk(raw_slide.get("pageElements", [])) if e["objectId"] == element["ref"])
    return slide, element, table_record(raw)


def cell_location(table, row, column, *, header=0):
    headers = shared_selector(effective_header_rows, table, header)
    r = shared_selector(resolve_row, table, row, header_rows=headers) - 1
    c = shared_selector(resolve_column, table, column, header_rows=headers) - 1
    if table["cells"][r][c]["merged"]:
        invalid("This cell belongs to a merged range. Choose an unmerged cell or unmerge it in Slides.", "slides_table_cell_merged")
    return r, c


def _value(value):
    if not isinstance(value, str):
        invalid("Pass each table value as plain text.")
    content(value)  # The same control-character and Unicode validation as text edits.
    return value


def cell_requests(object_id, r, c, current, value, *, new=False):
    location = {"rowIndex": r, "columnIndex": c}
    requests = []
    # The implicit newline is not deletable, including a new/empty cell.
    length = utf16_len(current[:-1] if current.endswith("\n") else current)
    if length and not new:
        requests.append({"deleteText": {"objectId": object_id, "cellLocation": location, "textRange": {"type": "FIXED_RANGE", "startIndex": 0, "endIndex": length}}})
    if value:
        requests.append({"insertText": {"objectId": object_id, "cellLocation": location, "insertionIndex": 0, "text": value}})
    return requests


def new_table(raw_slide, spec, box):
    values = spec.get("values")
    rows = spec.get("rows", len(values) if isinstance(values, list) else None)
    columns = spec.get("columns", len(values[0]) if isinstance(values, list) and values and isinstance(values[0], list) else None)
    if any(type(v) is not int or not 1 <= v <= 50 for v in (rows, columns)) or rows * columns > MAX_WRITE_CELLS:
        invalid(f"Pass rows/columns from 1 to 50, with at most {MAX_WRITE_CELLS} cells; values may supply the dimensions.")
    if values is None:
        values = [[""] * columns for _ in range(rows)]
    if not isinstance(values, list) or len(values) != rows or any(not isinstance(row, list) or len(row) != columns for row in values):
        invalid("values must be a rectangular list matching rows and columns.")
    values = [[_value(value) for value in row] for row in values]
    if sum(len(value) for row in values for value in row) > MAX_TEXT:
        invalid("Keep one table write within 100000 characters.")
    ref = new_id()
    requests = [{"createTable": {"objectId": ref, "rows": rows, "columns": columns, "elementProperties": element_properties(raw_slide["objectId"], box)}}]
    for r, row in enumerate(values):
        for c, value in enumerate(row):
            requests.extend(cell_requests(ref, r, c, "", value, new=True))
    return requests, {"ref": ref, "kind": "table", "rows": rows, "columns": columns}, box


def compile_table(presentation, view, action, payload):
    slide, element, table = resolve_table(presentation, view, payload)
    header = payload.get("header", 0)
    headers = shared_selector(effective_header_rows, table, header)
    requests, changes = [], []
    if action == "set_cells":
        entries = payload.get("cells")
        if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_WRITE_CELLS:
            invalid(f"Pass cells as a list of 1 to {MAX_WRITE_CELLS} row/column/text maps.")
        seen, total = set(), 0
        for entry in entries:
            if not isinstance(entry, Mapping) or set(entry) != {"row", "column", "text"}:
                invalid("Each cell needs exactly row, column and text.")
            r, c = cell_location(table, entry["row"], entry["column"], header=headers)
            if (r, c) in seen:
                invalid("Select each physical cell only once per write.")
            seen.add((r, c))
            value = _value(entry["text"])
            total += len(value)
            if total > MAX_TEXT:
                invalid("Keep one table write within 100000 characters.")
            requests.extend(cell_requests(element["ref"], r, c, table["cells"][r][c]["text"], value))
            changes.append({"row": r+1, "column": c+1, "text": value})
    else:
        if "before" in payload and "after" in payload:
            invalid("Choose before or after one row, or omit both to append.")
        below = "before" not in payload
        selector = payload.get("before", payload.get("after", table["rows"]))
        row = shared_selector(resolve_row, table, selector, header_rows=headers)-1
        if any(cell["merged"] for cell in table["cells"][row]):
            invalid("Choose an insertion row without merged cells.", "slides_table_cell_merged")
        values = payload.get("values", [""]*table["columns"])
        if isinstance(values, Mapping):
            mapped, seen = [""]*table["columns"], set()
            for column, value in values.items():
                c = shared_selector(resolve_column, table, column, header_rows=headers)-1
                if c in seen:
                    invalid("Select each column only once.")
                seen.add(c)
                mapped[c] = value
            values = mapped
        if not isinstance(values, list) or len(values) != table["columns"]:
            invalid("Pass values for all columns as a list, or as a column-name map with header=1.")
        values = [_value(v) for v in values]
        if sum(map(len, values)) > MAX_TEXT or (table["rows"]+1)*table["columns"] > MAX_CELLS:
            invalid("The added row exceeds the table write/read bounds.")
        requests.append({"insertTableRows": {"tableObjectId": element["ref"], "cellLocation": {"rowIndex": row, "columnIndex": 0}, "insertBelow": below, "number": 1}})
        inserted = row + int(below)
        for c, value in enumerate(values):
            requests.extend(cell_requests(element["ref"], inserted, c, "", value, new=True))
        changes = [{"row": inserted+1, "values": values}]
    return requests, {"slide": slide["ref"], "table": element["ref"], "cells" if action == "set_cells" else "added_rows": changes}
