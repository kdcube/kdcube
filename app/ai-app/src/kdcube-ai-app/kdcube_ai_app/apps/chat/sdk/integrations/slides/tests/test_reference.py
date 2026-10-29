import copy

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.google.slides_structure import snapshot
from kdcube_ai_app.apps.chat.sdk.integrations.slides.prediction import predict
from kdcube_ai_app.apps.chat.sdk.integrations.slides.reference import duplicate_slide_requests
from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError
from kdcube_ai_app.apps.chat.sdk.integrations.slides.tests.test_composition import deck


def test_reference_copy_uses_new_ids_and_preserves_unnamed_content():
    raw = deck()
    requests, plan = duplicate_slide_requests(raw, snapshot(raw), {"like": {"position": 1}, "replacements": {"title": "New title"}})
    ids = requests[0]["duplicateObject"]["objectIds"]
    assert set(ids) == {"slide1", "title1", "body1"}
    assert not set(ids) & set(ids.values())
    result = snapshot(predict(raw, requests))
    assert result["slides"][0]["title"] == "Neutral"
    assert result["slides"][1]["title"] == "New title"
    assert plan["kept_from_reference"] == [{"ref": ids["body1"], "kind": "shape", "role": "body"}]
    assert raw == deck()


def test_reference_position_resolves_original_anchor_before_duplication():
    raw = deck()
    raw["slides"].append({"objectId": "slide2", "pageElements": []})
    requests, plan = duplicate_slide_requests(raw, snapshot(raw), {"like": {"position": 1}, "after": {"position": 2}})
    result = snapshot(predict(raw, requests))
    assert [s["ref"] for s in result["slides"]] == ["slide1", "slide2", plan["slide"]]


def test_reference_kind_is_deterministic_and_ambiguity_is_refused():
    raw = deck()
    other = copy.deepcopy(raw["slides"][0])
    other["objectId"] = "slide2"
    raw["slides"].append(other)
    assert all(s["kind"] == "title_and_body" for s in snapshot(raw)["slides"])
    with pytest.raises(SlidesValidationError) as exc:
        duplicate_slide_requests(raw, snapshot(raw), {"like": {"kind": "title_and_body"}})
    assert exc.value.code == "slides_slide_ambiguous"
    assert len(exc.value.details["candidates"]) == 2


@pytest.mark.parametrize("value", ["\x00bad", "\ud800", ["line\nbreak"]])
def test_reference_text_is_validated_before_duplication(value):
    raw = deck()
    with pytest.raises(SlidesValidationError):
        duplicate_slide_requests(raw, snapshot(raw), {"like": {"position": 1}, "replacements": {"body": value}})


def test_reference_image_replacement_is_scoped_to_the_copy():
    raw = deck()
    raw["slides"][0]["pageElements"].append({"objectId": "image1", "size": {"width": {"magnitude": 100, "unit": "PT"}, "height": {"magnitude": 100, "unit": "PT"}}, "image": {}})
    requests, plan = duplicate_slide_requests(raw, snapshot(raw), {"like": {"position": 1}, "replacements": {"images": [{"element": {"kind": "image", "index": 1}, "image_uri": "https://example.test/image.png"}]}})
    request = next(r["replaceImage"] for r in requests if "replaceImage" in r)
    assert request["imageObjectId"] != "image1"
    assert request["imageObjectId"] == requests[0]["duplicateObject"]["objectIds"]["image1"]
    assert all(e["ref"] != request["imageObjectId"] for e in plan["kept_from_reference"])
