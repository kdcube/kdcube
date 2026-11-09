"""Typed advanced operations over the shared Slides transport."""
from kdcube_ai_app.apps.chat.sdk.integrations.slides.native import structure, validate_delete_targets
from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError
from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import resolve_slide
from kdcube_ai_app.apps.chat.sdk.integrations.slides.mutation_diagnostics import touched_slide_ids


async def run_native(api, operation, deck_id, presentation, *, requests=None, revision=None, preview=False, payload=None):
    base = {"presentation_id": deck_id, "title": presentation.get("title", ""), "revision_id": presentation.get("revisionId")}
    if operation == "get_structure":
        payload = payload or {}
        include = payload.get("include", ())
        if not isinstance(include, (list, tuple)) or any(not isinstance(name, str) or name not in {"layouts", "masters"} for name in include):
            raise SlidesValidationError("slides_invalid_include", "Pass include as an array containing layouts and/or masters.")
        pages = presentation.get("slides", [])
        if payload.get("slide_selector") is not None:
            selected = resolve_slide((await api.all_snapshot(presentation))["slides"], payload["slide_selector"])
            chosen = {selected["ref"]}
            page_range = {"start_slide": selected["position"], "returned": 1, "next_slide": None, "selection": "one_slide"}
        else:
            start, limit = payload.get("start_slide", 1), payload.get("slide_limit", 10)
            if type(start) is not int or start < 1 or type(limit) is not int or not 1 <= limit <= 50:
                raise SlidesValidationError("slides_invalid_range", "Use a 1-based start_slide and slide_limit from 1 to 50.")
            selected = pages[start-1:start-1+limit]
            chosen = {page["objectId"] for page in selected}
            next_slide = start + len(selected)
            page_range = {"start_slide": start, "returned": len(selected), "next_slide": next_slide if next_slide <= len(pages) else None}
        return {**base, "structure": structure(presentation, slide_ids=chosen, include=include), "range": page_range, "geometry_unit": "EMU"}
    if revision != presentation.get("revisionId"):
        raise SlidesValidationError("slides_revision_changed", "The presentation changed. Read get_structure again before retrying with its new revision.")
    validate_delete_targets(presentation, requests)
    if preview:
        return {**base, "preview": True, "requests": requests, "note": "Validated native request kinds, bounds and deletion targets; Google has not rendered these edits."}
    written = await api.write_batches(deck_id, requests, revision, operation=operation)
    observed = await api.get(deck_id)
    touched = touched_slide_ids(presentation, observed, requests, replies=written.get("replies", []))
    return {**base, **written, "observed_revision_id": observed.get("revisionId"), "touched_slide_ids": sorted(touched), "structure": structure(observed, slide_ids=touched), "geometry_unit": "EMU"}
