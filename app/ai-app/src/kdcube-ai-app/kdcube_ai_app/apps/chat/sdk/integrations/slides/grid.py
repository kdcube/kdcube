"""Bounded weighted grids with spans and nesting; all boxes are adapter-owned."""
from __future__ import annotations

import math
from collections.abc import Mapping

from kdcube_ai_app.apps.chat.sdk.integrations.slides.geometry import EMU_PER_PT, GRID_GAPS_PT
from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError


def invalid(message):
    raise SlidesValidationError("slides_invalid_composition", message)


def gap_size(value="medium"):
    if not isinstance(value, str) or value not in GRID_GAPS_PT:
        invalid("Choose gap small, medium or large.")
    return GRID_GAPS_PT[value] * EMU_PER_PT


def weights(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 12 or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in values):
        invalid("Use 1 to 12 positive finite row and column weights.")
    total = sum(values)
    if not math.isfinite(total):
        invalid("Keep the sum of weights finite.")
    return [v / total for v in values]


def grid_cells(spec, box, *, depth=0):
    if not isinstance(spec, Mapping) or depth > 3:
        invalid("Use a grid map with at most three nested grid levels.")
    cols, rows = weights(spec.get("cols", [1])), weights(spec.get("rows", [1]))
    cells = spec.get("cells")
    if not isinstance(cells, list) or not 1 <= len(cells) <= 100:
        invalid("Use 1 to 100 cells in each grid.")
    gap = gap_size(spec.get("gap", "medium"))
    available_w, available_h = box["width"]-gap*(len(cols)-1), box["height"]-gap*(len(rows)-1)
    if available_w <= 0 or available_h <= 0:
        invalid("The selected region is too small for these rows, columns and gaps.")
    occupied, result = set(), []
    for cell in cells:
        if not isinstance(cell, Mapping) or ("element" in cell) == ("grid" in cell):
            invalid("Each cell needs exactly one element or nested grid.")
        at = cell.get("at")
        if not isinstance(at, list) or len(at) != 2 or any(type(v) is not int or v < 0 for v in at):
            invalid("Address cells with zero-based [row, column] integers.")
        row, col = at
        rs, cs = cell.get("row_span", 1), cell.get("col_span", 1)
        if any(type(v) is not int or v < 1 for v in (rs, cs)) or row+rs > len(rows) or col+cs > len(cols):
            invalid("Keep every positive cell span inside its grid.")
        covered = {(r, c) for r in range(row, row+rs) for c in range(col, col+cs)}
        if occupied & covered:
            invalid("Grid cells overlap; use explicit overlay for layered content.")
        occupied.update(covered)
        bounds = {"x": box["x"]+sum(cols[:col])*available_w+col*gap, "y": box["y"]+sum(rows[:row])*available_h+row*gap, "width": sum(cols[col:col+cs])*available_w+(cs-1)*gap, "height": sum(rows[row:row+rs])*available_h+(rs-1)*gap}
        if "grid" in cell:
            result.extend(grid_cells(cell["grid"], bounds, depth=depth+1))
        else:
            if not isinstance(cell["element"], Mapping):
                invalid("Each element is a selector or a new element map.")
            result.append((dict(cell["element"]), bounds))
        if len(result) > 100:
            invalid("Keep the complete composition within 100 elements.")
    return result
