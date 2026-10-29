"""Private geometry projections for composition and reference-slide previews."""
from __future__ import annotations

import copy


def remap_slide(slide, ids):
    def mapped(value):
        if isinstance(value, list):
            return [mapped(v) for v in value]
        if isinstance(value, dict):
            return {key: ids.get(val, val) if key in ("objectId", "parentObjectId") and isinstance(val, str) else mapped(val) for key, val in value.items()}
        return value
    return mapped(slide)


def _walk(elements):
    for element in elements:
        yield element
        yield from _walk(element.get("elementGroup", {}).get("children", []))


def apply_layout_request(result, request):
    slides = result.get("slides", [])
    duplicate = request.get("duplicateObject")
    if duplicate:
        source = next(s for s in slides if s["objectId"] == duplicate["objectId"])
        cloned = remap_slide(source, duplicate["objectIds"])
        slides.insert(slides.index(source)+1, cloned)
        return True
    for key in ("createShape", "createImage"):
        if key not in request:
            continue
        spec = request[key]
        props = spec["elementProperties"]
        target = next(s for s in slides if s["objectId"] == props["pageObjectId"])
        element = {"objectId": spec["objectId"], **{k: copy.deepcopy(props[k]) for k in ("size", "transform")}}
        if key == "createShape":
            element["shape"] = {"shapeType": spec["shapeType"], "text": {"textElements": [{"paragraphMarker": {"style": {}}}, {"textRun": {"content": "\n", "style": {}}}]}}
        else:
            element["image"] = {}
        target.setdefault("pageElements", []).append(element)
        return True
    group = request.get("groupObjects")
    if group:
        for slide in slides:
            elements = slide.get("pageElements", [])
            children = [e for e in elements if e["objectId"] in group["childrenObjectIds"]]
            if children:
                position = min(elements.index(child) for child in children)
                slide["pageElements"] = [e for e in elements if e not in children]
                slide["pageElements"].insert(position, {"objectId": group["groupObjectId"], "transform": {"scaleX": 1, "scaleY": 1, "unit": "EMU"}, "elementGroup": {"children": children}})
                break
        return True
    for key in ("updatePageElementTransform", "updateShapeProperties", "updateImageProperties", "updatePageElementAltText", "updateParagraphStyle", "replaceImage"):
        if key not in request:
            continue
        spec = request[key]
        object_id = spec.get("objectId", spec.get("imageObjectId"))
        for slide in slides:
            for element in _walk(slide.get("pageElements", [])):
                if element["objectId"] != object_id:
                    continue
                if key == "updatePageElementTransform":
                    element["transform"] = copy.deepcopy(spec["transform"])
                elif key == "updatePageElementAltText":
                    element.update({k: spec[k] for k in ("title", "description") if k in spec})
                elif key == "updateParagraphStyle":
                    runs = element.get("shape", {}).get("text", {}).get("textElements", [])
                    for run in runs:
                        if "paragraphMarker" in run:
                            run["paragraphMarker"].setdefault("style", {}).update(spec["style"])
                elif key != "replaceImage":
                    target = "shape" if key == "updateShapeProperties" else "image"
                    element[target].setdefault(target+"Properties", {}).update(copy.deepcopy(spec[target+"Properties"]))
        return True
    return "updatePageElementsZOrder" in request
