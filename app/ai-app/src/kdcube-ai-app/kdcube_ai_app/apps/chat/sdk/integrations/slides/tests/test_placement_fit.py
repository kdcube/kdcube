"""Relative callout sizing agrees with the measured provider text padding."""
import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.google.slides_structure import snapshot
from kdcube_ai_app.apps.chat.sdk.integrations.slides.composition import compile_composition
from kdcube_ai_app.apps.chat.sdk.integrations.slides.prediction import predict
from kdcube_ai_app.apps.chat.sdk.integrations.slides.tests.test_composition import deck


@pytest.mark.parametrize("anchor", ["left_of", "right_of", "over"])
@pytest.mark.parametrize("font,text,width", [(11, "Badge", 1), (12, "Badge", 1), (18, "Badge", 1), (11, "Readable label on several lines", .5)])
def test_relative_callout_fits_its_text_after_both_insets(anchor, font, text, width):
    raw = deck()
    body = raw["slides"][0]["pageElements"][1]
    body["transform"].update(translateX=350, translateY=180)
    body["size"]["width"]["magnitude"] = 180
    body["size"]["height"]["magnitude"] = 120
    requests, plan = compile_composition(raw, snapshot(raw), "add_shape", {"slide": {"position": 1}, anchor: {"role": "body"}, "width": width, "shape": "round_rectangle", "fill": "theme.accent1", "text": text, "font_size_pt": font})
    after = snapshot(predict(raw, requests))["slides"][0]
    created = next(element for element in after["elements"] if element["ref"] == plan["elements"][0]["ref"])
    assert created["kind"] == "callout"
    assert created["fit"]["lines_available"] >= created["fit"]["lines_needed"] >= 1
    assert created["fit"]["status"] in ("ok", "tight")
    assert not after["off_slide"]
