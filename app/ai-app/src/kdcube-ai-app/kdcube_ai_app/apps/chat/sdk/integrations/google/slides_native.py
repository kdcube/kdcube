"""Typed advanced operations over the shared Slides transport."""
from kdcube_ai_app.apps.chat.sdk.integrations.slides.native import structure, validate_delete_targets
from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError


async def run_native(api, operation, deck_id, presentation, *, requests=None, revision=None, preview=False):
    base = {"presentation_id": deck_id, "title": presentation.get("title", ""), "revision_id": presentation.get("revisionId")}
    if operation == "get_structure":
        return {**base, "structure": structure(presentation), "geometry_unit": "EMU"}
    if revision != presentation.get("revisionId"):
        raise SlidesValidationError("slides_revision_changed", "The presentation changed. Read get_structure again before retrying with its new revision.")
    validate_delete_targets(presentation, requests)
    if preview:
        return {**base, "preview": True, "requests": requests, "note": "Validated native request kinds, bounds and deletion targets; Google has not rendered these edits."}
    written = await api.write_batches(deck_id, requests, revision, operation=operation)
    observed = await api.get(deck_id)
    return {**base, **written, "observed_revision_id": observed.get("revisionId"), "structure": structure(observed), "geometry_unit": "EMU"}
