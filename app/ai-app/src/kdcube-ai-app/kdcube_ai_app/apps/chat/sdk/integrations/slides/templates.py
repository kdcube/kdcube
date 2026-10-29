"""Compile bounded, selector-resolved template edits before any write."""
from __future__ import annotations

import re
from collections.abc import Mapping

from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import (
    SlidesValidationError, resolve_slide, resolve_element,
)

MAX_REPLACEMENTS = 100
MAX_REPLACEMENT_TEXT = 20_000
MAX_ELEMENT_EDIT_REQUESTS = 1_000
TAG_RE = re.compile(r"\{\{[^{}\n]+\}\}")


def tags_in(snapshot):
    return sorted({tag for slide in snapshot["slides"] for element in slide["elements"] for tag in TAG_RE.findall(element.get("text", ""))})


def tag_name(value):
    value = str(value)
    return value if value.startswith("{{") and value.endswith("}}") else "{{" + value + "}}"


def _replacements(payload):
    values = payload.get("replacements")
    if values is None and "text" in payload and "replacement" in payload:
        values = {payload["text"]: payload["replacement"]}
    if not isinstance(values, Mapping) or not values or len(values) > MAX_REPLACEMENTS:
        raise SlidesValidationError("slides_invalid_replacements", "Pass a replacements map with 1 to 100 literal text keys.")
    for key, value in values.items():
        if not isinstance(key, str) or not key or len(key) > MAX_REPLACEMENT_TEXT or not isinstance(value, str) or len(value) > MAX_REPLACEMENT_TEXT:
            raise SlidesValidationError("slides_invalid_replacements", "Replacement keys and values must be text, at most 20000 characters each; keys must be nonempty.")
    if sum(len(k)+len(v) for k,v in values.items()) > 100_000:
        raise SlidesValidationError("slides_invalid_replacements", "Keep the complete replacement map within 100000 characters.")
    return dict(values)


def selected_slides(snapshot, payload):
    selectors = payload.get("slides")
    if selectors is None and payload.get("slide") is not None:
        selectors = [payload["slide"]]
    if selectors is None:
        return snapshot["slides"]
    if not isinstance(selectors, list) or not selectors or len(selectors) > 100:
        raise SlidesValidationError("slides_invalid_selector", "Pass a nonempty slides list of at most 100 selectors.")
    found = [resolve_slide(snapshot["slides"], selector) for selector in selectors]
    return list({s["ref"]: s for s in found}.values())


def replace_text_requests(snapshot, payload):
    values = _replacements(payload)
    slides = selected_slides(snapshot, payload)
    page_ids = [s["ref"] for s in slides]
    if not page_ids:
        raise SlidesValidationError("slides_slide_not_found", "This presentation has no slides to edit.")
    if payload.get("element") is not None:
        if len(slides) != 1:
            raise SlidesValidationError("slides_element_ambiguous", "Name one slide when replacing text within an element.")
        element = resolve_element(slides[0], payload["element"])
        if element.get("kind") not in ("shape", "callout") or element.get("text_truncated"):
            raise SlidesValidationError("slides_element_not_editable", "Read one untruncated text shape before replacing its text.")
        current = element.get("text", "")
        target_id = element.get("_content_ref", element["ref"])
        if len(current) > 100_000:
            raise SlidesValidationError("slides_text_too_large", "This shape exceeds the bounded targeted-edit size. Use literal replacement on its slide instead of replacing the whole element text.")
        updated, requests = current, []
        for old, new in values.items():
            pattern = re.compile(re.escape(old), 0 if payload.get("match_case", True) else re.IGNORECASE)
            matches = [m for m in pattern.finditer(updated) if m.group() != new]
            if not matches:
                continue
            offsets = [0]
            for char in updated:
                offsets.append(offsets[-1] + (2 if ord(char) > 0xFFFF else 1))
            # Later occurrences are edited first so earlier UTF-16 indices
            # stay valid. Unmatched runs retain their provider-owned styling.
            for match in reversed(matches):
                start, end = offsets[match.start()], offsets[match.end()]
                requests.append({"deleteText": {"objectId": target_id, "textRange": {"type": "FIXED_RANGE", "startIndex": start, "endIndex": end}}})
                if new:
                    requests.append({"insertText": {"objectId": target_id, "text": new, "insertionIndex": start}})
                if len(requests) > MAX_ELEMENT_EDIT_REQUESTS:
                    raise SlidesValidationError("slides_too_many_replacements", "Keep targeted edits within 1000 requests; narrow the replacement text or use a slide-wide literal replacement.")
            updated = pattern.sub(lambda _: new, updated)
            if len(updated) > 100_000:
                raise SlidesValidationError("slides_text_too_large", "Keep targeted element text within 100000 characters.")
        preview={"ref": element["ref"], "before": current[:12000], "after": updated[:12000], "before_truncated": len(current)>12000, "after_truncated": len(updated)>12000}
        return requests, [preview]
    requests = [{"replaceAllText": {"containsText": {"text": old, "matchCase": bool(payload.get("match_case", True))}, "replaceText": new, "pageObjectIds": page_ids}} for old,new in values.items()]
    return requests, [{"slides": page_ids, "replacements": values}]


def image_requests(snapshot, payload):
    text = payload.get("tag", payload.get("text"))
    if not isinstance(text, str) or not text or len(text) > MAX_REPLACEMENT_TEXT:
        raise SlidesValidationError("slides_template_tag_missing", "Name the tag or literal text in the image placeholder.")
    slides = selected_slides(snapshot, payload)
    if not any(text in e.get("text", "") for s in slides for e in s["elements"] if e.get("kind") == "shape"):
        raise SlidesValidationError("slides_template_tag_missing", "No shape contains this image tag. Read the template tags and choose one of them.")
    uri = payload.get("image_uri")
    if not uri:
        raise SlidesValidationError("slides_image_hosting_unavailable", "Pass a public image_uri or a staged file_ref.")
    method = str(payload.get("fit", "CENTER_INSIDE")).upper()
    if method not in ("CENTER_INSIDE", "CENTER_CROP"):
        raise SlidesValidationError("slides_invalid_image_fit", "Use CENTER_INSIDE or CENTER_CROP.")
    return [{"replaceAllShapesWithImage": {"containsText": {"text": text, "matchCase": True}, "imageUrl": uri, "imageReplaceMethod": method, "pageObjectIds": [s["ref"] for s in slides]}}]


def template_requests(snapshot, payload):
    values = payload.get("values", {})
    images = payload.get("images", {})
    if not isinstance(values, Mapping) or not isinstance(images, Mapping) or len(values) + len(images) > MAX_REPLACEMENTS:
        raise SlidesValidationError("slides_invalid_template", "Pass values and images maps with at most 100 tags in total.")
    present = set(tags_in(snapshot))
    supplied = {tag_name(k) for k in [*values, *images]}
    missing = sorted(supplied - present)
    if missing:
        raise SlidesValidationError("slides_template_tag_missing", "These tags do not occur in the template. Choose a discovered tag.", details={"missing_tags": missing, "tags": sorted(present)[:100]})
    requests = []
    if values:
        requests, _ = replace_text_requests(snapshot, {"replacements": {tag_name(k):v for k,v in values.items()}})
    for key,image in images.items():
        if not isinstance(image,(str,Mapping)):
            raise SlidesValidationError("slides_invalid_template", "Each template image must be a public URL or a map with image_uri.")
        image = {"image_uri": image} if isinstance(image,str) else dict(image)
        requests += image_requests(snapshot, {**image, "tag": tag_name(key)})
    return requests, {"tags": sorted(present)[:100], "filled_tags": sorted(supplied), "unfilled_tags": sorted(present-supplied)[:100]}
