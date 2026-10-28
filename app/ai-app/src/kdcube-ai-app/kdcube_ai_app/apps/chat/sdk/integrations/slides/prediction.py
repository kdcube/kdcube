"""Private text and layout projections used for Slides write previews."""
from __future__ import annotations

import copy
import re

from kdcube_ai_app.apps.chat.sdk.integrations.slides.layouts import walk


def _style_text(shape, start, end, patch):
    output, offset = [], 0
    for item in shape.get("text", {}).get("textElements", []):
        if "textRun" not in item:
            output.append(item)
            continue
        run = item["textRun"]
        encoded = run.get("content", "").encode("utf-16-le")
        length = len(encoded) // 2
        left, right = min(length, max(0, start - offset)), min(length, max(0, end - offset))
        for lo, hi, selected in ((0, left, False), (left, right, True), (right, length, False)):
            if hi > lo:
                style = {**run.get("style", {}), **(patch if selected else {})}
                output.append({"textRun": {**run, "content": encoded[2 * lo:2 * hi].decode("utf-16-le"), "style": style}})
        offset += length
    shape.setdefault("text", {})["textElements"] = output


def _create_slide(result, spec):
    layout = next(l for l in result.get("layouts", []) if l["objectId"] == spec["slideLayoutReference"]["layoutId"])
    source = {e["objectId"]: e for e in walk(layout.get("pageElements", []))}
    elements = []
    for mapping in spec["placeholderIdMappings"]:
        parent = source[mapping["layoutPlaceholderObjectId"]]
        shape = parent["shape"]
        elements.append({
            "objectId": mapping["objectId"],
            **{k: copy.deepcopy(parent[k]) for k in ("size", "transform") if k in parent},
            "shape": {"shapeType": shape.get("shapeType", "TEXT_BOX"),
                      "placeholder": {**shape["placeholder"], "parentObjectId": parent["objectId"]},
                      "text": {"textElements": [{"paragraphMarker": {"style": {}}}, {"textRun": {"content": "\n", "style": {}}}]}},
        })
    props = {"layoutObjectId": layout["objectId"]}
    if layout.get("layoutProperties", {}).get("masterObjectId"):
        props["masterObjectId"] = layout["layoutProperties"]["masterObjectId"]
    result.setdefault("slides", []).insert(spec.get("insertionIndex", len(result.get("slides", []))), {"objectId": spec["objectId"], "slideProperties": props, "pageElements": elements})


def _delete_elements(elements, object_id):
    kept = []
    for element in elements:
        if element.get("objectId") == object_id:
            continue
        if "elementGroup" in element:
            element["elementGroup"]["children"] = _delete_elements(element["elementGroup"].get("children", []), object_id)
        kept.append(element)
    return kept
def _predict_text_edit(shape, start, end, inserted=""):
    """Splice UTF-16 offsets while retaining unaffected run styles for fit."""
    elements, offset, did_insert = [], 0, False
    for item in shape.get("text", {}).get("textElements", []):
        if "textRun" not in item:
            elements.append(item)
            continue
        run = item["textRun"]
        encoded = run.get("content", "").encode("utf-16-le")
        length = len(encoded) // 2
        left = min(length, max(0, start - offset))
        right = min(length, max(0, end - offset))
        content = encoded[:2 * left].decode("utf-16-le")
        if not did_insert and offset <= start <= offset + length:
            content += inserted
            did_insert = True
        content += encoded[2 * right:].decode("utf-16-le")
        if content:
            elements.append({**item, "textRun": {**run, "content": content}})
        offset += length
    if not did_insert and inserted:
        elements.append({"textRun": {"content": inserted}})
    # This is a fit-only projection. Provider text-element indices are no
    # longer current and are not needed by the snapshot or fit estimator.
    for item in elements:
        item.pop("startIndex", None)
        item.pop("endIndex", None)
    shape.setdefault("text", {})["textElements"] = elements


def predict(presentation, requests):
    """Estimate text fit over a private copy; never mutate the provider input."""
    from kdcube_ai_app.apps.chat.sdk.integrations.google.slides_structure import shape_text
    result=copy.deepcopy(presentation)
    def shapes(elements):
        for e in elements:
            if "shape" in e:yield e
            yield from shapes(e.get("elementGroup", {}).get("children", []))
    for request in requests:
        if "createSlide" in request:
            _create_slide(result, request["createSlide"])
            continue
        if "deleteObject" in request:
            object_id = request["deleteObject"]["objectId"]
            result["slides"] = [s for s in result.get("slides", []) if s["objectId"] != object_id]
            for slide in result["slides"]:
                slide["pageElements"] = _delete_elements(slide.get("pageElements", []), object_id)
            continue
        if "updateSlidesPosition" in request:
            spec = request["updateSlidesPosition"]
            before = result.get("slides", [])
            ids = spec["slideObjectIds"]
            moved = [s for s in before if s["objectId"] in ids]
            index = spec["insertionIndex"] - sum(s["objectId"] in ids for s in before[:spec["insertionIndex"]])
            rest = [s for s in before if s["objectId"] not in ids]
            result["slides"] = rest[:index] + moved + rest[index:]
            continue
        if "updateSlideProperties" in request:
            spec = request["updateSlideProperties"]
            for slide in result.get("slides", []):
                if slide["objectId"] == spec["objectId"]:
                    slide.setdefault("slideProperties", {}).update(spec["slideProperties"])
            continue
        replacement=request.get("replaceAllText")
        image=request.get("replaceAllShapesWithImage")
        for slide in result.get("slides", []):
            target_pages=(replacement or image or {}).get("pageObjectIds", [])
            if target_pages and slide["objectId"] not in target_pages:continue
            targets = list(shapes(slide.get("pageElements", [])))
            if not (replacement or image):
                targets.extend(shapes(slide.get("slideProperties", {}).get("notesPage", {}).get("pageElements", [])))
            for element in targets:
                text=shape_text(element["shape"])
                changed=None
                if replacement:
                    old=replacement["containsText"]["text"]
                    changed=text.replace(old,replacement["replaceText"]) if replacement["containsText"].get("matchCase") else re.sub(re.escape(old),lambda m:replacement["replaceText"],text,flags=re.I)
                elif image and image["containsText"]["text"] in text:
                    changed=""
                elif request.get("deleteText", {}).get("objectId")==element["objectId"]:
                    bounds = request["deleteText"]["textRange"]
                    end = len(text.encode("utf-16-le")) // 2
                    if bounds["type"] == "ALL" and text.endswith("\n"):
                        end -= 1
                    _predict_text_edit(element["shape"], bounds.get("startIndex", 0), bounds.get("endIndex", end))
                elif request.get("insertText", {}).get("objectId")==element["objectId"]:
                    insertion = request["insertText"]
                    index = insertion.get("insertionIndex", 0)
                    _predict_text_edit(element["shape"], index, index, insertion["text"])
                elif request.get("updateTextStyle", {}).get("objectId") == element["objectId"]:
                    update = request["updateTextStyle"]
                    bounds = update["textRange"]
                    _style_text(element["shape"], bounds["startIndex"], bounds["endIndex"], update["style"])
                elif (request.get("createParagraphBullets") or request.get("deleteParagraphBullets") or {}).get("objectId") == element["objectId"]:
                    runs = element["shape"].setdefault("text", {}).setdefault("textElements", [])
                    markers = [r["paragraphMarker"] for r in runs if "paragraphMarker" in r]
                    if "createParagraphBullets" in request:
                        if not markers:
                            runs.insert(0, {"paragraphMarker": {"style": {}}})
                            markers = [runs[0]["paragraphMarker"]]
                        for marker in markers:
                            marker["bullet"] = {"glyph": "●"}
                    else:
                        for marker in markers:
                            marker.pop("bullet", None)
                if changed is not None:
                    runs=element["shape"].get("text", {}).get("textElements", [])
                    style=next((r["textRun"].get("style", {}) for r in runs if "textRun" in r), {})
                    element["shape"]["text"]={"textElements":[{"textRun":{"content":changed,"style":style}}]}
    return result


