import copy
import math

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.google.slides_structure import snapshot
from kdcube_ai_app.apps.chat.sdk.integrations.slides.composition import compile_composition
from kdcube_ai_app.apps.chat.sdk.integrations.slides.geometry import EMU_PER_PT, geometry
from kdcube_ai_app.apps.chat.sdk.integrations.slides.grid import grid_cells
from kdcube_ai_app.apps.chat.sdk.integrations.slides.prediction import predict
from kdcube_ai_app.apps.chat.sdk.integrations.slides.regions import resolve_region
from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError
from kdcube_ai_app.apps.chat.sdk.integrations.slides.editing import compile_edit


def deck():
    def shape(ref, role, x, y, width, height):
        return {"objectId": ref, "size": {"width": {"magnitude": width, "unit": "PT"}, "height": {"magnitude": height, "unit": "PT"}}, "transform": {"scaleX": 1, "scaleY": 1, "translateX": x, "translateY": y, "unit": "PT"}, "shape": {"placeholder": {"type": role, "parentObjectId": "unread_parent"}, "text": {"textElements": [{"textRun": {"content": "Neutral\n", "style": {}}}]}}}
    return {"presentationId": "deck1", "revisionId": "r1", "pageSize": {"width": {"magnitude": 960, "unit": "PT"}, "height": {"magnitude": 540, "unit": "PT"}}, "slides": [{"objectId": "slide1", "pageElements": [shape("title1", "TITLE", 36, 36, 888, 50), shape("body1", "BODY", 36, 110, 888, 394)]}]}


def compile(action, payload, raw=None, **kwargs):
    raw = raw or deck()
    return compile_composition(raw, snapshot(raw), action, {"slide": {"position": 1}, **payload}, **kwargs)


def test_regions_use_own_placeholder_geometry_and_real_two_body_boxes():
    raw = deck()
    first = raw["slides"][0]["pageElements"][1]
    first["size"]["width"]["magnitude"] = 400
    second = copy.deepcopy(first)
    second["objectId"] = "body2"
    second["transform"]["translateX"] = 500
    second["size"]["width"]["magnitude"] = 424
    raw["slides"][0]["pageElements"].append(second)
    slide = raw["slides"][0]
    assert resolve_region(raw, slide, "left_half")["width"] == 400*EMU_PER_PT
    assert resolve_region(raw, slide, "right_half")["x"] == 500*EMU_PER_PT
    assert resolve_region(raw, slide, "body")["width"] == 888*EMU_PER_PT


@pytest.mark.parametrize("parent", [False, True])
def test_regions_refuse_rotation_or_shear_after_group_composition(parent):
    raw = deck()
    body = raw["slides"][0]["pageElements"][1]
    if parent:
        body = {"objectId": "group1", "transform": {"scaleX": 1, "scaleY": 1, "shearX": .2, "unit": "PT"}, "elementGroup": {"children": [body]}}
        raw["slides"][0]["pageElements"][1] = body
    else:
        body["transform"]["shearY"] = .2
    with pytest.raises(SlidesValidationError) as exc:
        resolve_region(raw, raw["slides"][0], "body")
    assert exc.value.code == "slides_region_unsupported_geometry"
    assert "body" not in snapshot(raw)["slides"][0]["regions"]
    assert snapshot(raw)["slides"][0]["unsupported_regions"]


def test_unrotated_group_placeholders_compose_translation():
    raw = deck()
    body = raw["slides"][0]["pageElements"].pop()
    raw["slides"][0]["pageElements"].append({"objectId": "group1", "transform": {"scaleX": 1, "scaleY": 1, "translateX": 10, "translateY": 20, "unit": "PT"}, "elementGroup": {"children": [body]}})
    box = resolve_region(raw, raw["slides"][0], "body")
    assert box["x"] == 46*EMU_PER_PT and box["y"] == 130*EMU_PER_PT


def test_fallback_body_reserves_title_and_page_margins():
    raw = deck()
    raw["slides"][0]["pageElements"].pop()
    box = resolve_region(raw, raw["slides"][0], "body")
    assert box == {"x": 36*EMU_PER_PT, "y": 122*EMU_PER_PT, "width": 888*EMU_PER_PT, "height": 382*EMU_PER_PT}


@pytest.mark.parametrize("weights", [[0], [-1], [math.nan], [math.inf], [True], [], [1]*13])
def test_grid_rejects_invalid_weights(weights):
    with pytest.raises(SlidesValidationError):
        grid_cells({"cols": weights, "cells": [{"at": [0, 0], "element": {"text": "One"}}]}, {"x": 0, "y": 0, "width": 5000000, "height": 5000000})


def test_grid_rejects_overlapping_and_out_of_range_spans():
    for cells in ([{"at": [0, 0], "col_span": 2, "element": {"text": "One"}}], [{"at": [0, 0], "element": {"text": "One"}}, {"at": [0, 0], "element": {"text": "Two"}}]):
        with pytest.raises(SlidesValidationError):
            grid_cells({"cells": cells}, {"x": 0, "y": 0, "width": 5000000, "height": 5000000})


def test_nested_grid_allocates_inside_its_parent_cell():
    outer = {"x": 0, "y": 0, "width": 600*EMU_PER_PT, "height": 300*EMU_PER_PT}
    cells = grid_cells({"cols": [2, 1], "cells": [{"at": [0, 1], "grid": {"rows": [1, 1], "cells": [{"at": [1, 0], "element": {"text": "Nested"}}]}}]}, outer)
    assert len(cells) == 1
    assert cells[0][1]["x"] > outer["width"]/2
    assert cells[0][1]["y"] > outer["height"]/2
    assert cells[0][1]["x"]+cells[0][1]["width"] == pytest.approx(outer["width"])


def test_compose_spanned_text_callout_and_caption_reads_back_its_tracks():
    raw = deck()
    payload = {"cols": [1.6, 1], "rows": [1, 2.2, .3], "cells": [
        {"at": [0, 0], "row_span": 3, "element": {"role": "body"}},
        {"at": [0, 1], "element": {"shape": "round_rectangle", "fill": "theme.accent1", "text": "Callout"}},
        {"at": [1, 1], "element": {"text": "Image placeholder"}},
        {"at": [2, 1], "element": {"text": "Caption", "align": "center"}},
    ]}
    requests, plan = compile("compose", payload, raw)
    assert len(plan["elements"]) == 4
    assert any("groupObjects" in request for request in requests)
    result = snapshot(predict(raw, requests))["slides"][0]
    assert not result["overlaps"] and not result["off_slide"]
    assert result["grid"]["cols"] == pytest.approx([1.6/2.6, 1/2.6], abs=.0001)
    assert result["grid"]["rows"] == pytest.approx([1/3.5, 2.2/3.5, .3/3.5], abs=.0001)
    assert next(c for c in result["grid"]["cells"] if c["element"] == "body1")["row_span"] == 3
    assert raw == deck()


def test_relative_placement_uses_one_anchor_and_derived_height():
    raw = deck()
    requests, _ = compile("add_shape", {"below": {"role": "title"}, "width": .5, "align": "end", "text": "One line"}, raw)
    props = requests[0]["createShape"]["elementProperties"]
    assert props["size"]["width"]["magnitude"] == 444*EMU_PER_PT
    assert props["transform"]["translateX"] == 480*EMU_PER_PT
    assert props["transform"]["translateY"] > 86*EMU_PER_PT
    assert props["size"]["height"]["magnitude"] > 18*EMU_PER_PT
    with pytest.raises(SlidesValidationError):
        compile("add_shape", {"below": {"role": "title"}, "above": {"role": "body"}, "text": "Ambiguous"})


def test_overlay_reports_overlap_and_small_text_warning():
    raw = deck()
    requests, plan = compile("add_shape", {"over": {"role": "body"}, "at": "top_right", "width": .2, "text": "Badge", "font_size_pt": 12}, raw)
    assert plan["elements"][0]["warnings"] == ["text_below_14pt"]
    result = snapshot(predict(raw, requests))["slides"][0]
    assert result["overlaps"]
    assert "updatePageElementsZOrder" in requests[-1]


def test_new_colors_are_theme_colors_and_s3_creation_is_explicit():
    with pytest.raises(SlidesValidationError):
        compile("add_shape", {"shape": "rectangle", "fill": "#ff0000"})
    with pytest.raises(SlidesValidationError) as exc:
        compile("compose", {"cells": [{"at": [0, 0], "element": {"table": {"rows": 2}}}]})
    assert exc.value.code == "slides_element_kind_not_yet_supported"


def test_alt_text_is_content_without_service_metadata():
    raw = deck()
    requests, _ = compile("set_alt_text", {"element": {"role": "body"}, "title": "Neutral content", "description": "An accessible description"}, raw)
    after = snapshot(predict(raw, requests))["slides"][0]["elements"][1]
    assert after["alt_text"] == "An accessible description"


def image_composition():
    return {"cols": [1.6, 1], "rows": [1, 2.2, .3], "cells": [
        {"at": [0, 0], "row_span": 3, "element": {"role": "body"}},
        {"at": [0, 1], "element": {"shape": "round_rectangle", "fill": "theme.accent1", "text": "Callout"}},
        {"at": [1, 1], "element": {"image": {"image_uri": "https://example.test/image.png"}, "border": True}},
        {"at": [2, 1], "element": {"text": "Caption", "align": "center"}},
    ]}


def test_letterboxed_image_and_inset_callout_are_one_element_each_and_read_same_grid():
    raw = deck()
    requests, _ = compile("compose", image_composition(), raw, image_sizes={"https://example.test/image.png": {"width": 100, "height": 100}})
    after = predict(raw, requests)
    result = snapshot(after)["slides"][0]
    assert len(result["elements"]) == 5  # title plus four semantic cells
    assert len([e for e in result["elements"] if e["kind"] == "image"]) == 1
    assert len([e for e in result["elements"] if e["kind"] == "callout"]) == 1
    assert result["grid"]["cols"] == pytest.approx([1.6/2.6, 1/2.6], abs=.0001)
    assert result["grid"]["rows"] == pytest.approx([1/3.5, 2.2/3.5, .3/3.5], abs=.0001)
    assert not result["overlaps"] and not result["off_slide"]
    assert all(e.get("alt_text") != "kdcube:frame" for e in result["elements"])
    image = next(e for e in result["elements"] if e["kind"] == "image")
    edits, _ = compile("set_alt_text", {"element": {"ref": image["ref"]}, "description": "A square chart"}, after)
    assert edits[0]["updatePageElementAltText"]["objectId"] == image["_content_ref"]
    assert next(e for e in snapshot(predict(after, edits))["slides"][0]["elements"] if e["kind"] == "image")["alt_text"] == "A square chart"


def test_image_filling_its_cell_and_unfilled_shape_remain_single_elements():
    raw = deck()
    requests, _ = compile("add_image", {"image_uri": "https://example.test/image.png"}, raw, image_sizes={"https://example.test/image.png": {"width": 888, "height": 394}})
    assert not any("groupObjects" in r for r in requests)
    requests, _ = compile("add_shape", {"shape": "rectangle", "text": "Plain outline"}, raw)
    assert not any("groupObjects" in r for r in requests)


def test_image_replacement_creates_new_content_before_deleting_the_selected_placeholder():
    raw = deck()
    requests, plan = compile("add_image", {"element": {"role": "body"}, "image_uri": "https://example.test/image.png"}, raw, image_sizes={"https://example.test/image.png": {"width": 100, "height": 100}})
    assert requests[-1] == {"deleteObject": {"objectId": "body1"}}
    assert plan["replaced_element"] == "body1"
    result = snapshot(predict(raw, requests))["slides"][0]
    assert not any(e["ref"] == "body1" for e in result["elements"])
    assert len([e for e in result["elements"] if e["kind"] == "image"]) == 1


def test_callout_text_edits_target_the_inset_and_whole_group_deletion_removes_it():
    raw = deck()
    requests, _ = compile("add_shape", {"shape": "round_rectangle", "fill": "theme.accent1", "text": "Before"}, raw)
    raw = predict(raw, requests)
    view = snapshot(raw)
    callout = next(e for e in view["slides"][0]["elements"] if e["kind"] == "callout")
    edits, _ = compile_edit(raw, view, "set_text", {"slide": {"position": 1}, "element": {"ref": callout["ref"]}, "text": "After"})
    assert edits[0]["deleteText"]["objectId"] == callout["_content_ref"]
    raw = predict(raw, edits)
    assert next(e for e in snapshot(raw)["slides"][0]["elements"] if e["kind"] == "callout")["text"] == "After\n"
    raw = predict(raw, [{"deleteObject": {"objectId": callout["ref"]}}])
    assert not any(e["kind"] == "callout" for e in snapshot(raw)["slides"][0]["elements"])


def test_resizing_image_frame_keeps_child_aspect_and_full_cell_box():
    raw = deck()
    requests, _ = compile("add_image", {"image_uri": "https://example.test/image.png"}, raw, image_sizes={"https://example.test/image.png": {"width": 100, "height": 100}})
    raw = predict(raw, requests)
    image = next(e for e in snapshot(raw)["slides"][0]["elements"] if e["kind"] == "image")
    requests, _ = compile("compose", {"cols": [1, 1], "cells": [{"at": [0, 1], "element": {"ref": image["ref"]}}]}, raw)
    assert requests[0]["updatePageElementTransform"]["objectId"] == image["ref"]
    after = predict(raw, requests)
    group = next(e for e in after["slides"][0]["pageElements"] if e["objectId"] == image["ref"])
    child = next(e for e in group["elementGroup"]["children"] if "image" in e)
    from kdcube_ai_app.apps.chat.sdk.integrations.slides.geometry import matrix
    box = geometry(child, matrix(group["transform"]))
    assert box["width"] == pytest.approx(box["height"])
    assert geometry(group)["width"] > box["width"] or geometry(group)["height"] > box["height"]


@pytest.mark.parametrize("action", ["compose", "add_shape"])
@pytest.mark.parametrize("content", [{}, {"text": ""}, {"text": "\n"}])
def test_textless_shape_emits_no_text_or_paragraph_style(action, content):
    spec = {"shape": "rectangle", "fill": "theme.accent1", **content}
    payload = {"cells": [{"at": [0, 0], "element": spec}]} if action == "compose" else spec
    requests, _ = compile(action, payload)
    assert any("createShape" in request for request in requests)
    assert not any(key in request for request in requests for key in ("insertText", "updateTextStyle", "updateParagraphStyle", "deleteParagraphBullets"))


def test_created_text_keeps_theme_default_unless_color_is_explicit():
    raw = deck()
    raw["slides"][0]["pageProperties"] = {"pageBackgroundFill": {"solidFill": {"color": {"themeColor": "DARK1"}}}}
    for spec, expected in (({"text": "Theme default"}, None), ({"text": "Explicit light", "color": "theme.light1"}, "LIGHT1")):
        requests, _ = compile("add_shape", spec, raw)
        styles = [request["updateTextStyle"] for request in requests if "updateTextStyle" in request]
        assert len(styles) == 1
        if expected is None:
            assert "foregroundColor" not in styles[0]["style"]
            assert "foregroundColor" not in styles[0]["fields"]
        else:
            assert styles[0]["style"]["foregroundColor"] == {"opaqueColor": {"themeColor": expected}}


def test_outline_only_shape_explicitly_has_no_default_fill():
    requests, _ = compile("add_shape", {"shape": "rectangle", "outline": "theme.accent1"})
    props = next(request["updateShapeProperties"] for request in requests if "updateShapeProperties" in request)
    assert props["shapeProperties"]["shapeBackgroundFill"] == {"propertyState": "NOT_RENDERED"}
    assert "shapeBackgroundFill" in props["fields"].split(",")
    assert props["shapeProperties"]["outline"]["outlineFill"]["solidFill"]["color"] == {"themeColor": "ACCENT1"}
