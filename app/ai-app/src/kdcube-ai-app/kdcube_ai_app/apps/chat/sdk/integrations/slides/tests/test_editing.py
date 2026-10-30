import copy

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.google.slides_structure import snapshot, shape_text
from kdcube_ai_app.apps.chat.sdk.integrations.slides.editing import compile_edit
from kdcube_ai_app.apps.chat.sdk.integrations.slides.prediction import predict
from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError


def presentation():
    def shape(ref, kind, parent=None, text="\n", x=10):
        placeholder = {"type": kind}
        if parent:
            placeholder["parentObjectId"] = parent
        return {"objectId": ref, "size": {"width": {"magnitude": 300, "unit": "PT"}, "height": {"magnitude": 100, "unit": "PT"}}, "transform": {"scaleX": 1, "scaleY": 1, "translateX": x, "translateY": 30, "unit": "PT"}, "shape": {"placeholder": placeholder, "text": {"textElements": [{"paragraphMarker": {"style": {"lineSpacing": 115}}}, {"textRun": {"content": text, "style": {}}}]}}}
    master_body = shape("master_body", "BODY")
    master_body["shape"]["text"]["textElements"][1]["textRun"]["style"] = {"fontSize": {"magnitude": 24, "unit": "PT"}}
    return {
        "presentationId": "deck", "title": "Fixture", "revisionId": "r1",
        "pageSize": {"width": {"magnitude": 720, "unit": "PT"}, "height": {"magnitude": 405, "unit": "PT"}},
        "masters": [{"objectId": "master", "pageElements": [master_body]}],
        "layouts": [{"objectId": "layout", "layoutProperties": {"name": "TITLE_AND_BODY", "displayName": "Title and body", "masterObjectId": "master"}, "pageElements": [shape("layout_title", "TITLE"), shape("layout_body", "BODY", "master_body")]}],
        "slides": [{"objectId": f"slide_{i}", "slideProperties": {"masterObjectId": "master", "layoutObjectId": "layout", "notesPage": {"notesProperties": {"speakerNotesObjectId": f"notes_{i}"}, "pageElements": [shape(f"notes_{i}", "BODY", text="Speaker\n")]}}, "pageElements": [shape(f"title_{i}", "TITLE", "layout_title", f"Slide {i}\n"), shape(f"body_{i}", "BODY", "layout_body", "😀 old phrase\n")]} for i in range(1, 4)],
    }


def compile(action, payload, raw=None):
    raw = raw or presentation()
    return compile_edit(raw, snapshot(raw), action, payload)


def test_creation_maps_every_content_placeholder_and_removes_unfilled_ones():
    raw = presentation()
    requests, plan = compile("add_slide", {"layout": "title_and_body", "title": "New title", "before": {"position": 2}}, raw)
    create = requests[0]["createSlide"]
    assert create["slideLayoutReference"] == {"layoutId": "layout"}
    assert create["insertionIndex"] == 1
    mappings = create["placeholderIdMappings"]
    assert {m["layoutPlaceholderObjectId"] for m in mappings} == {"layout_title", "layout_body"}
    ids = {m["layoutPlaceholderObjectId"]: m["objectId"] for m in mappings}
    assert {"deleteObject": {"objectId": ids["layout_body"]}} in requests
    assert plan["empty_placeholders"] == []
    result = snapshot(predict(raw, requests))
    assert result["slides"][1]["title"] == "New title"
    assert len(result["slides"][1]["elements"]) == 1
    assert len(raw["slides"]) == 3


def test_creation_can_keep_empty_placeholders_and_predict_inherited_fit():
    raw = presentation()
    requests, plan = compile("add_slide", {"layout": "title_and_body", "body": ["Line one", "Line two"], "keep_empty_placeholders": True}, raw)
    assert [e["role"] for e in plan["empty_placeholders"]] == ["title"]
    result = snapshot(predict(raw, requests))["slides"][-1]
    body = next(e for e in result["elements"] if e.get("role") == "body")
    assert body["text"] == "Line one\nLine two\n"
    assert body["fit"]["font_size_pt"] == 24
    assert any("createParagraphBullets" in r for r in requests)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_empty_content_is_removed_by_default(value):
    requests, plan = compile("add_slide", {"layout": "title_and_body", "title": value})
    assert len([r for r in requests if "deleteObject" in r]) == 2
    assert not plan["empty_placeholders"]


def test_layout_word_prefers_google_name_over_pptx_mapping():
    raw = presentation()
    pptx = copy.deepcopy(raw["layouts"][0])
    pptx["objectId"] = "pptx_layout"
    pptx["layoutProperties"]["name"] = "OBJECT"
    raw["layouts"].insert(0, pptx)
    requests, _ = compile("add_slide", {"layout": "title_and_body"}, raw)
    assert requests[0]["createSlide"]["slideLayoutReference"] == {"layoutId": "layout"}
    raw["layouts"] = [pptx]
    requests, _ = compile("add_slide", {"layout": "title_and_body"}, raw)
    assert requests[0]["createSlide"]["slideLayoutReference"] == {"layoutId": "pptx_layout"}


def test_free_layout_name_is_not_inferred_from_placeholder_composition():
    raw = presentation()
    raw["layouts"][0]["layoutProperties"]["name"] = "Custom"
    with pytest.raises(SlidesValidationError) as exc:
        compile("add_slide", {"layout": "title_and_body"}, raw)
    assert exc.value.code == "slides_layout_missing"
    requests, _ = compile("add_slide", {"layout_name": "Title and body"}, raw)
    assert requests[0]["createSlide"]["slideLayoutReference"]["layoutId"] == "layout"


def test_layout_resolution_uses_the_master_at_the_insertion_position():
    raw = presentation()
    alternate = copy.deepcopy(raw["layouts"][0])
    alternate["objectId"] = "alternate"
    alternate["layoutProperties"]["masterObjectId"] = "other_master"
    raw["layouts"].append(alternate)
    raw["slides"][1]["slideProperties"]["masterObjectId"] = "other_master"
    requests, _ = compile("add_slide", {"layout": "title_and_body", "after": {"position": 2}}, raw)
    assert requests[0]["createSlide"]["slideLayoutReference"]["layoutId"] == "alternate"


def test_ambiguous_layout_refuses_with_bounded_candidates():
    raw = presentation()
    raw["layouts"] *= 8
    with pytest.raises(SlidesValidationError) as exc:
        compile("add_slide", {"layout_name": "Title and body"}, raw)
    assert exc.value.code == "slides_layout_ambiguous"
    assert len(exc.value.details["candidates"]) == 5


def test_append_resolves_utf16_before_the_implicit_terminal_newline():
    raw = presentation()
    requests, plan = compile("set_text", {"slide": {"position": 1}, "element": {"role": "body"}, "text": " tail", "mode": "append"}, raw)
    assert requests[0]["insertText"]["insertionIndex"] == 13
    expected = "😀 old phrase tail\n"
    assert plan["after"] == expected
    after = snapshot(predict(raw, requests))["slides"][0]["elements"][1]
    assert after["text"] == expected


def test_replace_with_items_creates_bullets_and_clear_text_removes_them():
    raw = presentation()
    requests, plan = compile("set_text", {"slide": {"position": 1}, "element": {"role": "body"}, "items": ["One", "Two"]}, raw)
    assert plan["after"] == "One\nTwo\n"
    assert requests[-1]["createParagraphBullets"]["bulletPreset"] == "BULLET_DISC_CIRCLE_SQUARE"
    updated = predict(raw, requests)
    assert updated["slides"][0]["pageElements"][1]["shape"]["text"]["textElements"][0]["paragraphMarker"]["bullet"]
    requests, _ = compile("set_text", {"slide": {"position": 1}, "element": {"role": "body"}, "text": "Plain"}, updated)
    assert "deleteParagraphBullets" in requests[-1]
    plain = predict(updated, requests)["slides"][0]["pageElements"][1]["shape"]
    assert not any(e.get("paragraphMarker", {}).get("bullet") for e in plain["text"]["textElements"])


def test_style_resolves_unique_quote_after_non_bmp_and_predicts_size_change():
    raw = presentation()
    requests, plan = compile("style_text", {"slide": {"position": 1}, "element": {"role": "body"}, "quoted_text": "old phrase", "style": {"bold": True, "font_size_pt": 60, "color": "#112233"}}, raw)
    assert requests[0]["updateTextStyle"]["textRange"] == {"type": "FIXED_RANGE", "startIndex": 3, "endIndex": 13}
    before = copy.deepcopy(raw)
    result = snapshot(predict(raw, requests))["slides"][0]["elements"][1]
    assert result["fit"]["font_size_pt"] == 60
    assert result["text"] == "😀 old phrase\n"
    assert raw == before
    assert plan["before"] == plan["after"]


@pytest.mark.parametrize("quote,code", [("missing", "slides_text_not_found"), (" ", "slides_text_ambiguous")])
def test_style_quote_refuses_missing_or_duplicate_spans(quote, code):
    with pytest.raises(SlidesValidationError) as exc:
        compile("style_text", {"slide": {"position": 1}, "element": {"role": "body"}, "quoted_text": quote, "style": {"italic": True}})
    assert exc.value.code == code


def test_notes_resolve_the_fresh_speaker_placeholder_and_predict_text():
    raw = presentation()
    requests, plan = compile("set_notes", {"slide": {"position": 2}, "notes": "New notes"}, raw)
    assert requests[0]["deleteText"]["objectId"] == "notes_2"
    assert plan["before"] == "Speaker\n" and plan["after"] == "New notes\n"
    assert snapshot(predict(raw, requests))["slides"][1]["notes"] == "New notes\n"


def test_move_sorts_and_deduplicates_by_current_order_before_computing_position():
    raw = presentation()
    requests, plan = compile("move_slide", {"slides": [{"position": 3}, {"position": 1}, {"position": 3}], "after": {"position": 2}}, raw)
    assert requests == [{"updateSlidesPosition": {"slideObjectIds": ["slide_1", "slide_3"], "insertionIndex": 2}}]
    assert plan["after_order"] == ["slide_2", "slide_1", "slide_3"]
    assert [s["objectId"] for s in predict(raw, requests)["slides"]] == plan["after_order"]


def test_delete_keeps_one_slide_and_skip_can_be_reversed():
    raw = presentation()
    with pytest.raises(SlidesValidationError) as exc:
        compile("delete_slide", {"slides": [{"position": i} for i in (1, 2, 3)]}, raw)
    assert exc.value.code == "slides_last_slide"
    requests, plan = compile("delete_slide", {"slide": {"position": 2}}, raw)
    assert plan["after_order"] == ["slide_1", "slide_3"]
    assert len(predict(raw, requests)["slides"]) == 2
    requests, _ = compile("skip_slide", {"slide": {"position": 2}, "skipped": True}, raw)
    updated = predict(raw, requests)
    assert snapshot(updated)["slides"][1]["skipped"]
    requests, _ = compile("skip_slide", {"slide": {"position": 2}, "skipped": False}, updated)
    assert not snapshot(predict(updated, requests))["slides"][1]["skipped"]


@pytest.mark.parametrize("payload", [{"items": "wrong"}, {"items": ["has\ttab"]}, {"text": "bad\rcontrol"}, {"text": "\ue000"}, {"text": "\ud800"}])
def test_invalid_text_is_refused_during_compilation(payload):
    with pytest.raises(SlidesValidationError):
        compile("set_text", {"slide": {"position": 1}, "element": {"role": "body"}, **payload})


def test_notes_validation_happens_before_slide_creation():
    with pytest.raises(SlidesValidationError):
        compile("add_slide", {"layout": "title_and_body", "notes": "bad\rcontrol"})


@pytest.mark.parametrize("current", ["", "\n"])
@pytest.mark.parametrize("action", ["set_text", "set_notes"])
@pytest.mark.parametrize("replacement", ["Written", ""])
def test_replace_empty_shape_or_notes_never_deletes_the_implicit_newline(current, action, replacement):
    raw = presentation()
    slide = raw["slides"][0]
    if action == "set_notes":
        target = slide["slideProperties"]["notesPage"]["pageElements"][0]
        payload = {"notes": replacement}
    else:
        target = slide["pageElements"][0]
        payload = {"element": {"role": "title"}, "text": replacement}
    target["shape"]["text"]["textElements"] = [{"textRun": {"content": current}}] if current else []
    requests, plan = compile(action, {"slide": {"position": 1}, **payload}, raw)
    assert not any("deleteText" in request for request in requests)
    inserts = [request["insertText"] for request in requests if "insertText" in request]
    assert inserts == ([{"objectId": target["objectId"], "text": replacement, "insertionIndex": 0}] if replacement else [])
    assert plan["before"] == current
    assert plan["after"] == replacement + ("\n" if current else "")
