"""Touched-slide diagnostics and bounded deck-wide counts for write results."""
from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.integrations.google.slides_fit import shape_text


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def touched_slide_ids(before, after, requests, *, replies=()):
    """Resolve compiled request targets, including notes, groups and new ids.

    Only native request identity fields are considered. Replacement operations
    further narrow their page scope to slides containing the requested text.
    Reading a source slide to duplicate it does not mark that source as edited.
    """
    owners = {}
    pages = {}
    for presentation in (before, after):
        for page in presentation.get("slides", []):
            page_id = page["objectId"]
            pages[page_id] = page
            for node in _walk(page):
                if isinstance(node.get("objectId"), str):
                    owners[node["objectId"]] = page_id
            notes_id = page.get("slideProperties", {}).get("notesPage", {}).get("notesProperties", {}).get("speakerNotesObjectId")
            if notes_id:
                owners[notes_id] = page_id
    touched = set()
    identity_fields = {"objectId", "imageObjectId", "pageObjectId", "pageObjectIds", "slideObjectIds", "childrenObjectIds", "pageElementObjectIds", "groupObjectId"}
    for index, request in enumerate(requests):
        reply = replies[index] if index < len(replies) else {}
        if "duplicateObject" in request:
            spec = request["duplicateObject"]
            target = reply.get("duplicateObject", {}).get("objectId") or spec.get("objectIds", {}).get(spec.get("objectId"))
            if target:
                touched.add(owners.get(target, target))
            continue
        if "createSlide" in request:
            target = reply.get("createSlide", {}).get("objectId") or request["createSlide"].get("objectId")
            if target:
                touched.add(target)
        replacement = request.get("replaceAllText") or request.get("replaceAllShapesWithImage")
        if replacement is not None:
            needle = replacement["containsText"]["text"]
            match_case = replacement["containsText"].get("matchCase", False)
            for page_id in replacement.get("pageObjectIds", pages):
                page = next((p for p in before.get("slides", []) if p["objectId"] == page_id), pages.get(page_id, {}))
                texts = [shape_text(node) for node in _walk(page.get("pageElements", [])) if isinstance(node.get("text"), dict) and "textElements" in node["text"]]
                matched = any(needle in text if match_case else needle.casefold() in text.casefold() for text in texts)
                if matched:
                    touched.add(page_id)
            continue
        for node in _walk(request):
            for key, value in node.items():
                if key in identity_fields:
                    for identity in value if isinstance(value, list) else [value]:
                        if isinstance(identity, str) and identity in owners:
                            touched.add(owners[identity])
    return touched


def mutation_diagnostics(view, *, touched=(), predicted=False, summary_only=False):
    """Count the whole projection; return detailed rows only for touched pages."""
    slides = view["slides"]
    all_fit = [{"slide": slide["ref"], "element": element["ref"], **element["fit"]} for slide in slides for element in slide["elements"] if element.get("fit")]
    counts = {"ok": 0, "tight": 0, "overflow_risk": 0}
    for row in all_fit:
        status = row.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    summary = {
        "slides": len(slides), "fit": counts,
        "overlaps": sum(len(slide.get("overlaps", [])) for slide in slides),
        "off_slide": sum(len(slide.get("off_slide", [])) for slide in slides),
        "geometry_diagnostics_truncated": any(slide.get("geometry_diagnostics_truncated", False) for slide in slides),
        "predicted": predicted,
    }
    result = {"diagnostics_summary": summary}
    if summary_only:
        return result
    selected = set(touched)
    flags = [row for row in all_fit if row["slide"] in selected]
    overlaps = [{"slide": slide["ref"], "elements": pair} for slide in slides if slide["ref"] in selected for pair in slide.get("overlaps", [])]
    off_slide = [{"slide": slide["ref"], "element": ref} for slide in slides if slide["ref"] in selected for ref in slide.get("off_slide", [])]
    return {
        **result, "fit": flags[:1000], "fit_truncated": len(flags) > 1000,
        "fit_predicted": predicted, "overlaps": overlaps[:1000], "off_slide": off_slide[:1000],
        "geometry_diagnostics_truncated": len(overlaps) > 1000 or len(off_slide) > 1000 or any(slide.get("geometry_diagnostics_truncated", False) for slide in slides if slide["ref"] in selected),
    }
