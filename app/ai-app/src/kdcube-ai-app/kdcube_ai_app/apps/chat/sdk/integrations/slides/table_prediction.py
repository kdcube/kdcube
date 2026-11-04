"""Private table/video preview projection; no provider mutation or persistence."""
from __future__ import annotations

import copy

from kdcube_ai_app.apps.chat.sdk.integrations.slides.layouts import walk


def empty_cell(r, c):
    return {"location": {"rowIndex": r, "columnIndex": c}, "rowSpan": 1, "columnSpan": 1, "text": {"textElements": [{"textRun": {"content": "\n"}}]}}


def apply_table_request(result, request):
    for key in ("createTable", "createVideo"):
        if key not in request:
            continue
        spec = request[key]
        props = spec["elementProperties"]
        slide = next(s for s in result["slides"] if s["objectId"] == props["pageObjectId"])
        element = {"objectId": spec["objectId"], **{k: copy.deepcopy(props[k]) for k in ("size", "transform")}}
        if key == "createVideo":
            element["video"] = {k: spec[k] for k in ("source", "id")}
        else:
            element["table"] = {"rows": spec["rows"], "columns": spec["columns"], "tableRows": [{"tableCells": [empty_cell(r, c) for c in range(spec["columns"])]} for r in range(spec["rows"])]}
        slide.setdefault("pageElements", []).append(element)
        return True
    insert = request.get("insertTableRows")
    cell_edit = request.get("insertText", request.get("deleteText", {}))
    if not insert and "cellLocation" not in cell_edit:
        return False
    identity = insert["tableObjectId"] if insert else cell_edit["objectId"]
    raw = next(e for s in result["slides"] for e in walk(s.get("pageElements", [])) if e["objectId"] == identity)
    table = raw["table"]
    if insert:
        index = insert["cellLocation"]["rowIndex"] + int(insert["insertBelow"])
        for row in table["tableRows"]:
            for cell in row["tableCells"]:
                if cell.get("location", {}).get("rowIndex", 0) >= index:
                    cell["location"]["rowIndex"] += insert["number"]
        for offset in range(insert["number"]):
            table["tableRows"].insert(index+offset, {"tableCells": [empty_cell(index+offset, c) for c in range(table["columns"])]})
        table["rows"] += insert["number"]
    else:
        from kdcube_ai_app.apps.chat.sdk.integrations.slides.prediction import _predict_text_edit
        location = cell_edit["cellLocation"]
        r, c = location["rowIndex"], location["columnIndex"]
        cell = next(cell for ci, cell in enumerate(table["tableRows"][r]["tableCells"]) if cell.get("location", {}).get("columnIndex", ci) == c)
        if "insertText" in request:
            start = cell_edit.get("insertionIndex", 0)
            _predict_text_edit(cell, start, start, cell_edit["text"])
        else:
            bounds = cell_edit["textRange"]
            _predict_text_edit(cell, bounds["startIndex"], bounds["endIndex"])
    return True
