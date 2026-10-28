import copy
import json
from pathlib import Path

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.google.slides_structure import snapshot
from kdcube_ai_app.apps.chat.sdk.integrations.slides.editing import compile_edit
from kdcube_ai_app.apps.chat.sdk.integrations.slides.prediction import predict

ROOT = Path(__file__).parent / "fixtures" / "slides" / "s2a"
PPTX = json.loads((ROOT / "pptx-object-calibration.json").read_text())


@pytest.mark.parametrize("theme", ["simple_table", "streamline_rotated", "focus_group", "material_group_rotated", "pptx_converted"])
@pytest.mark.parametrize("layout", ["title_and_body", "title_and_two_columns"])
def test_add_slide_uses_captured_stock_and_powerpoint_layouts(theme, layout):
    raw = json.loads((ROOT / (theme + "-layouts.json")).read_text())
    payload = {"layout": layout, "title": "Neutral title", "body": ["First item", "Second item"]}
    if layout == "title_and_two_columns":
        payload["body_2"] = "Second column"
    requests, plan = compile_edit(raw, snapshot(raw), "add_slide", payload)
    create = requests[0]["createSlide"]
    selected = next(l for l in raw["layouts"] if l["objectId"] == create["slideLayoutReference"]["layoutId"])
    expected = {e["objectId"] for e in selected["pageElements"] if e.get("shape", {}).get("placeholder", {}).get("type") not in (None, "DATE_AND_TIME", "SLIDE_NUMBER", "HEADER", "FOOTER")}
    assert {m["layoutPlaceholderObjectId"] for m in create["placeholderIdMappings"]} == expected
    assert not plan["empty_placeholders"]
    after = snapshot(predict(raw, requests))["slides"][-1]
    assert after["title"] == "Neutral title"
    body = next(e for e in after["elements"] if e.get("role") == "body")
    expected_font = 13 if theme in ("streamline_rotated", "focus_group") else 18
    if layout == "title_and_two_columns" and theme in ("simple_table", "material_group_rotated"):
        expected_font = 14
    assert body["fit"]["font_size_pt"] == expected_font
    assert {e["ref"] for e in after["elements"]} == {m["objectId"] for m in create["placeholderIdMappings"]}


@pytest.mark.parametrize("case", PPTX["cases"], ids=lambda c: c["case"])
def test_pptx_created_body_fit_matches_the_six_measured_renders(case):
    raw = copy.deepcopy(PPTX["context"])
    raw["slides"] = [case["page_after_write"]]
    body = next(e for e in snapshot(raw)["slides"][0]["elements"] if e.get("role") == "body")
    assert body["fit"]["font_size_pt"] == 18
    assert body["fit"]["status"] in ("ok", "tight")
    assert not case["observed"]["overflow"]
    assert abs(body["fit"]["lines_needed"] - case["observed"]["visible_lines"]) <= 1


def test_pptx_create_preview_copies_the_measured_placeholder_geometry():
    raw = copy.deepcopy(PPTX["context"])
    raw["slides"] = []
    requests, _ = compile_edit(raw, snapshot(raw), "add_slide", {"layout": "title_and_body", "title": "Neutral", "body": "Neutral body"})
    predicted = next(e for e in snapshot(predict(raw, requests))["slides"][0]["elements"] if e.get("role") == "body")
    captured = copy.deepcopy(raw)
    captured["slides"] = [PPTX["created_page"]]
    measured = next(e for e in snapshot(captured)["slides"][0]["elements"] if e.get("role") == "body")
    # The create response rounds scale components to four decimal places.
    # 250 EMU is below 0.02 pt and covers that captured provider rounding.
    assert predicted["geometry"] == pytest.approx(measured["geometry"], abs=250)
    assert predicted["rotation_deg"] == measured["rotation_deg"]


def test_live_unmapped_body_capture_requires_complete_content_id_mapping():
    fixture = json.loads((ROOT / "unmapped-body.json").read_text())
    body = next(e for e in fixture["created_page"]["pageElements"] if e.get("shape", {}).get("placeholder", {}).get("type") == "BODY")
    assert body["objectId"] == fixture["unmapped_body_id"]
    assert body["objectId"].startswith("SLIDES_API")
    raw = {"slides": [], "layouts": [fixture["layout"]]}
    requests, _ = compile_edit(raw, snapshot(raw), "add_slide", {"layout": "title_and_body", "title": "Neutral"})
    mapped = requests[0]["createSlide"]["placeholderIdMappings"]
    assert len(mapped) == 2
    assert {"deleteObject": {"objectId": next(m["objectId"] for m in mapped if m["layoutPlaceholderObjectId"] == body["shape"]["placeholder"]["parentObjectId"])}} in requests
