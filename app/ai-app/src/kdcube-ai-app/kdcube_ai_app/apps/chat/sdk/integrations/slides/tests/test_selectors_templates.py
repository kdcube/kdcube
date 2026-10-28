import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError, resolve_slide, resolve_element, parse_slides_ref, presentation_id, slides_ref
from kdcube_ai_app.apps.chat.sdk.integrations.slides.templates import template_requests, replace_text_requests


def view():
    return {"slides": [{"position": 1, "ref": "slide_a", "title": "Roadmap", "kind": "title_and_body", "elements": [{"ref": "title_a", "role": "title", "kind": "shape", "text": "Hello {{NAME}}", "text_truncated": False}, {"ref": "image_a", "kind": "shape", "text": "{{LOGO}}"}]}, {"position": 2, "ref": "slide_b", "title": "Roadmap", "kind": "title_and_body", "elements": []}]}


@pytest.mark.parametrize("selector", [{"position": 1}, {"ref": "slide_a"}, {"tag": "{{NAME}}"}, {"ref": "slides:google:account:slide:slide_a:deck"}])
def test_slide_selectors_resolve_one_opaque_handle(selector):
    assert resolve_slide(view()["slides"], selector)["ref"] == "slide_a"


def test_ambiguity_has_bounded_candidates_and_no_write():
    with pytest.raises(SlidesValidationError, match="Choose one") as exc:
        resolve_slide(view()["slides"], {"title": "Roadmap"})
    assert exc.value.code == "slides_slide_ambiguous"
    assert len(exc.value.details["candidates"]) == 2


def test_element_role_and_kind_index():
    assert resolve_element(view()["slides"][0], {"role": "title"})["ref"] == "title_a"
    assert resolve_element(view()["slides"][0], {"kind": "shape", "index": 2})["ref"] == "image_a"


def test_template_discovers_tags_before_compiling_and_refuses_unknown_tag():
    with pytest.raises(SlidesValidationError) as exc:
        template_requests(view(), {"values": {"MISSING": "text"}})
    assert exc.value.code == "slides_template_tag_missing"
    requests, plan = template_requests(view(), {"values": {"NAME": "Ada"}, "images": {"LOGO": "https://images.example/logo.png"}})
    assert requests[0]["replaceAllText"]["containsText"]["text"] == "{{NAME}}"
    assert requests[1]["replaceAllShapesWithImage"]["imageUrl"].endswith("logo.png")
    assert set(plan["filled_tags"]) == {"{{NAME}}", "{{LOGO}}"}


def test_targeted_replace_uses_delete_insert_without_google_offsets():
    requests, plan = replace_text_requests(view(), {"slide": {"position": 1}, "element": {"role": "title"}, "replacements": {"{{NAME}}": "Ada"}})
    assert requests == [{"deleteText": {"objectId": "title_a", "textRange": {"type": "ALL"}}}, {"insertText": {"objectId": "title_a", "text": "Hello Ada", "insertionIndex": 0}}]
    assert plan[0]["before"] == "Hello {{NAME}}"


def test_unknown_selectors_and_bad_refs_refuse():
    for ref in ("slides:other:account:presentation:deck", "slides:google:account:export:exe:deck", "https://attacker.invalid/presentation/d/deck"):
        with pytest.raises(SlidesValidationError):
            presentation_id(ref)
    with pytest.raises(SlidesValidationError):
        resolve_slide(view()["slides"], {"position": 1, "unknown": True})
    ref = slides_ref("account", "deck")
    assert parse_slides_ref(ref)["account_id"] == "account"
