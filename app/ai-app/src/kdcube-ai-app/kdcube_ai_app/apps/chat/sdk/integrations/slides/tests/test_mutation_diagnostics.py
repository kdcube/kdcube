import copy

from kdcube_ai_app.apps.chat.sdk.integrations.slides.mutation_diagnostics import mutation_diagnostics, touched_slide_ids


def test_summary_counts_all_slides_while_details_only_include_touched_slide():
    view = {"slides": [
        {"ref": "a", "elements": [{"ref": "a1", "fit": {"status": "ok"}}, {"ref": "a2", "fit": {"status": "tight"}}], "overlaps": [["a1", "a2"]], "off_slide": ["a2"]},
        {"ref": "b", "elements": [{"ref": "b1", "fit": {"status": "overflow_risk"}}], "overlaps": [], "off_slide": ["b1"], "geometry_diagnostics_truncated": True},
    ]}
    original = copy.deepcopy(view)
    result = mutation_diagnostics(view, touched={"a"})
    assert [r["element"] for r in result["fit"]] == ["a1", "a2"]
    assert result["overlaps"] == [{"slide": "a", "elements": ["a1", "a2"]}]
    assert result["off_slide"] == [{"slide": "a", "element": "a2"}]
    assert result["diagnostics_summary"] == {
        "slides": 2, "fit": {"ok": 1, "tight": 1, "overflow_risk": 1},
        "overlaps": 1, "off_slide": 2, "geometry_diagnostics_truncated": True, "predicted": False,
    }
    assert not result["geometry_diagnostics_truncated"]
    assert set(mutation_diagnostics(view, summary_only=True)) == {"diagnostics_summary"}
    assert view == original


def test_targets_resolve_nested_groups_notes_new_elements_and_duplicate_only():
    before = {"slides": [
        {"objectId": "a", "pageElements": [{"objectId": "group", "elementGroup": {"children": [{"objectId": "child"}]}}], "slideProperties": {"notesPage": {"notesProperties": {"speakerNotesObjectId": "notes"}}}},
        {"objectId": "b", "pageElements": [{"objectId": "untouched"}]},
    ]}
    after = {"slides": before["slides"] + [{"objectId": "new", "pageElements": [{"objectId": "new_child"}]}]}
    assert touched_slide_ids(before, after, [{"insertText": {"objectId": "child", "text": "untouched"}}]) == {"a"}
    assert touched_slide_ids(before, after, [{"insertText": {"objectId": "notes", "text": "notes"}}]) == {"a"}
    assert touched_slide_ids(before, after, [{"duplicateObject": {"objectId": "a", "objectIds": {"a": "new", "child": "new_child"}}}, {"insertText": {"objectId": "new_child", "text": "new"}}]) == {"new"}
    assert touched_slide_ids(before, after, [{"createImage": {"objectId": "image", "elementProperties": {"pageObjectId": "new"}}}]) == {"new"}
    assert touched_slide_ids(before, after, [{"updateSlidesPosition": {"slideObjectIds": ["b"], "insertionIndex": 0}}]) == {"b"}
    assert touched_slide_ids(before, after, [{"deleteObject": {"objectId": "a"}}]) == {"a"}


def test_replacements_include_only_matching_slides_in_the_selected_scope():
    def page(ref, text):
        return {"objectId": ref, "pageElements": [{"objectId": ref + "_text", "shape": {"text": {"textElements": [{"textRun": {"content": text}}]}}}]}
    deck = {"slides": [page("a", "Hello {{NAME}}"), page("b", "Other text"), page("c", "HELLO") ]}
    request = {"replaceAllText": {"containsText": {"text": "hello", "matchCase": False}, "replaceText": "Hi", "pageObjectIds": ["a", "b"]}}
    assert touched_slide_ids(deck, deck, [request]) == {"a"}
    request["replaceAllText"].pop("pageObjectIds")
    assert touched_slide_ids(deck, deck, [request]) == {"a", "c"}


def test_detail_limit_does_not_cap_deck_summary_counts():
    view = {"slides": [{"ref": "a", "elements": [{"ref": str(n), "fit": {"status": "ok"}} for n in range(1001)], "overlaps": [], "off_slide": []}]}
    result = mutation_diagnostics(view, touched={"a"})
    assert len(result["fit"]) == 1000 and result["fit_truncated"]
    assert result["diagnostics_summary"]["fit"]["ok"] == 1001
