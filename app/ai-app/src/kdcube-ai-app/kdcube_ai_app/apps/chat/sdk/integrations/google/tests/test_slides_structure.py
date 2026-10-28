import copy
import json
from pathlib import Path

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.google.slides_structure import snapshot, layout_word, geometry, fit
from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError

FIXTURES = Path(__file__).parent / "fixtures" / "slides"


@pytest.mark.parametrize("theme", ["simple_table", "streamline_rotated", "focus_group", "material_group_rotated", "pptx_converted"])
def test_probe_theme_snapshots_are_bounded_and_keep_layout_words(theme):
    raw = json.loads((FIXTURES / (theme + ".json")).read_text())
    result = snapshot(raw, limit=2)
    assert result["schema"] == "kdcube.slides.snapshot.v1"
    assert result["presentation"]["page"]["aspect"] == "16:9"
    assert len(result["slides"]) <= 2
    assert result["range"]["next_slide"] == (3 if len(raw["slides"]) > 2 else None)
    assert all(row["layout"] == layout_word(layout["layoutProperties"]["name"]) for row,layout in zip(result["layouts"], raw["layouts"]))
    assert "private-capability" not in json.dumps(result)


@pytest.mark.parametrize("name,expected", [("TITLE_AND_BODY", "title_and_body"), ("OBJECT", "title_and_body"), ("TWO_OBJECTS", "title_and_two_columns"), ("TWO_OBJECTS_WITH_TEXT", None), ("Title and body", None), ("PICTURE_WITH_CAPTION_TEXT", None), ("VERTICAL_TEXT", None)])
def test_d17_maps_names_without_guessing_placeholder_composition(name, expected):
    assert layout_word(name) == expected


def test_group_child_geometry_is_relative_to_group():
    child = {"size": {"width": {"magnitude": 3000000}, "height": {"magnitude": 3000000}}, "transform": {"scaleX": .3986, "scaleY": .323, "translateX": 2423700, "translateY": 1932325}}
    result = geometry(child, (1,0,0,1,2959175,-167000))
    assert result["x"] == 5382875
    assert result["y"] == 1765325
    assert result["width"] == pytest.approx(1195800)


def test_rotation_is_reported_from_shear():
    result = geometry({"size": {"width": {"magnitude": 3e6}, "height": {"magnitude": 3e6}}, "transform": {"scaleX": 2.537, "scaleY": .746, "shearX": .107, "shearY": -.365}})
    assert result["rotation_deg"] == pytest.approx(-8.187, abs=.02)


def test_text_budget_and_pagination_are_explicit():
    raw = json.loads((FIXTURES / "simple_table.json").read_text())
    element = raw["slides"][0]["pageElements"][0]
    element["shape"]["text"] = {"textElements": [{"textRun": {"content": "x" * 1000000}}]}
    result = snapshot(raw)
    assert result["slides"][0]["elements"][0]["text_truncated"]
    assert len(result["slides"][0]["elements"][0]["text"]) == 12000
    with pytest.raises(SlidesValidationError):
        snapshot(raw, start=0)


def test_fit_uses_inherited_font_and_script_specific_width():
    parent = {"text": {"textElements": [{"textRun": {"style": {"fontSize": {"magnitude": 24}}}}]}}
    shape = {"placeholder": {"parentObjectId": "parent"}, "text": {"textElements": [{"textRun": {"content": "A" * 300}}]}}
    box = {"width": 200*12700, "height": 120*12700}
    latin = fit(shape, box, {"parent": parent})
    shape["text"]["textElements"][0]["textRun"]["content"] = "Я" * 300
    cyrillic = fit(shape, box, {"parent": parent})
    assert latin["font_size_pt"] == 24
    assert cyrillic["lines_needed"] > latin["lines_needed"]
    assert cyrillic["status"] == "overflow_risk"
