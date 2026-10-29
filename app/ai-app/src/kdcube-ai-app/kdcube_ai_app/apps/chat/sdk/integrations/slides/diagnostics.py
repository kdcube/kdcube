"""Deterministic, bounded box relations and a derived grid with gutter tracks."""
from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.integrations.slides.geometry import EMU_PER_PT, GRID_ALIGNMENT_TOLERANCE, GRID_GAPS_PT


def intersects(a, b):
    return a["x"] < b["x"]+b["width"] and b["x"] < a["x"]+a["width"] and a["y"] < b["y"]+b["height"] and b["y"] < a["y"]+a["height"]


def _tracks(elements, axis, dimension, page_extent):
    tolerance = page_extent * GRID_ALIGNMENT_TOLERANCE
    values = sorted(v for e in elements for v in (e["geometry"][axis], e["geometry"][axis]+e["geometry"][dimension]))
    groups = []
    for value in values:
        if groups and value-groups[-1][0] <= tolerance:
            groups[-1].append(value)
        else:
            groups.append([value])
    edges = [sum(g)/len(g) for g in groups]
    intervals = [(min(range(len(edges)), key=lambda i: abs(edges[i]-e["geometry"][axis])), min(range(len(edges)), key=lambda i: abs(edges[i]-e["geometry"][axis]-e["geometry"][dimension]))) for e in elements]
    if any(end <= start for start, end in intervals):
        return None
    starts, ends = {a for a, _ in intervals}, {b for _, b in intervals}
    # A gutter separates an ending and a starting element, while spanning
    # elements may cross it. A narrow element with those edges is content.
    gaps = {i for i in range(len(edges)-1) if i in ends and i+1 in starts and i not in starts and i+1 not in ends and edges[i+1]-edges[i] <= GRID_GAPS_PT["large"]*EMU_PER_PT+tolerance}
    tracks = [(edges[i], edges[i+1]) for i in range(len(edges)-1) if i not in gaps]
    if not 1 <= len(tracks) <= 12:
        return None
    assignments = [[i for i, (left, right) in enumerate(tracks) if edges[start] <= (left+right)/2 <= edges[end]] for start, end in intervals]
    if any(not indices for indices in assignments):
        return None
    return tracks, assignments, any(len(group) > 1 for group in groups)


def layout_read(elements, width, height):
    top = [e for e in elements if not e.get("group_ref") and e["geometry"]["width"] > 0 and e["geometry"]["height"] > 0]
    off_slide = [e["ref"] for e in top if e["geometry"]["x"] < 0 or e["geometry"]["y"] < 0 or e["geometry"]["x"]+e["geometry"]["width"] > width or e["geometry"]["y"]+e["geometry"]["height"] > height]
    overlaps, relations = [], []
    for i, left in enumerate(top[:50]):
        a = left["geometry"]
        for right in top[i+1:50]:
            b = right["geometry"]
            if intersects(a, b):
                overlaps.append([left["ref"], right["ref"]])
                continue
            if a["x"]+a["width"] <= b["x"]:
                relations.append({"element": left["ref"], "left_of": right["ref"]})
            elif b["x"]+b["width"] <= a["x"]:
                relations.append({"element": right["ref"], "left_of": left["ref"]})
            if a["y"]+a["height"] <= b["y"]:
                relations.append({"element": left["ref"], "above": right["ref"]})
            elif b["y"]+b["height"] <= a["y"]:
                relations.append({"element": right["ref"], "above": left["ref"]})
    result = {"relations": relations[:100], "overlaps": overlaps[:100], "off_slide": off_slide[:100], "free_regions": [], "geometry_diagnostics_truncated": len(top)>50 or len(relations)>100 or len(overlaps)>100 or len(off_slide)>100}
    # Content grids exclude title/footer placeholders. Each top-level group
    # remains a single composed box, and its children do not become tracks.
    content = [e for e in top if e.get("role") not in ("title", "date_and_time", "footer", "header", "slide_number")]
    if width <= 0 or height <= 0 or not content or len(content) > 30 or any(e["rotation_deg"] for e in content) or any(intersects(a["geometry"], b["geometry"]) for i, a in enumerate(content) for b in content[i+1:]):
        return result
    xs, ys = _tracks(content, "x", "width", width), _tracks(content, "y", "height", height)
    if not xs or not ys or (len(content)>1 and not (xs[2] or ys[2])):
        return result
    cols, x_assign, _ = xs
    rows, y_assign, _ = ys
    cells, occupied = [], set()
    for element, xx, yy in zip(content, x_assign, y_assign):
        cells.append({"at": [yy[0], xx[0]], "row_span": len(yy), "col_span": len(xx), "element": element["ref"]})
        occupied.update((r, c) for r in yy for c in xx)
    widths, heights = [b-a for a, b in cols], [b-a for a, b in rows]
    result["grid"] = {"region": "content", "cols": [round(v/sum(widths), 4) for v in widths], "rows": [round(v/sum(heights), 4) for v in heights], "cells": cells}
    result["free_regions"] = [{"at": [r, c]} for r in range(len(rows)) for c in range(len(cols)) if (r, c) not in occupied]
    return result
