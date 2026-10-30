"""Leaf-frame markers survive content edits and fail closed on structural edits."""
import copy

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.google.slides_structure import snapshot
from kdcube_ai_app.apps.chat.sdk.integrations.slides.editing import compile_edit
from kdcube_ai_app.apps.chat.sdk.integrations.slides.frames import frame_content
from kdcube_ai_app.apps.chat.sdk.integrations.slides.geometry import EMU_PER_PT, TEXT_INSET_PT, geometry, matrix
from kdcube_ai_app.apps.chat.sdk.integrations.slides.prediction import predict
from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError
from kdcube_ai_app.apps.chat.sdk.integrations.slides.tests.test_composition import compile, deck


def framed(kind):
    if kind == "image":
        requests, _ = compile("add_image", {"image_uri": "https://example.test/image.png"}, image_sizes={"https://example.test/image.png": {"width": 100, "height": 100}})
    else:
        requests, _ = compile("add_shape", {"shape": "round_rectangle", "fill": "theme.accent1", "text": "Before"})
    raw = predict(deck(), requests)
    group = next(e for e in raw["slides"][0]["pageElements"] if "elementGroup" in e)
    return raw, group, requests


@pytest.mark.parametrize("kind,marker", [("image", "kdcube:image-frame"), ("callout", "kdcube:callout")])
def test_frame_title_is_on_leaf_and_content_edits_preserve_it(kind, marker):
    raw, group, requests = framed(kind)
    frame = next(e for e in group["elementGroup"]["children"] if "shape" in e and e["shape"].get("shapeType") != "TEXT_BOX")
    assert frame.get("title") == marker and "title" not in group
    assert all(r["updatePageElementAltText"]["objectId"] != group["objectId"] for r in requests if "updatePageElementAltText" in r)
    view = snapshot(raw)
    logical = next(e for e in view["slides"][0]["elements"] if e["ref"] == group["objectId"])
    assert logical["kind"] == kind and logical["alt_text"] == ""
    assert not any(e["ref"] in {c["objectId"] for c in group["elementGroup"]["children"]} for e in view["slides"][0]["elements"])
    edits, _ = compile("set_alt_text", {"element": {"ref": logical["ref"]}, "title": "Accessible title", "description": "Accessible description"}, raw)
    assert edits == [{"updatePageElementAltText": {"objectId": logical["_content_ref"], "title": "Accessible title", "description": "Accessible description"}}]
    if kind == "callout":
        text_edits, _ = compile_edit(raw, view, "set_text", {"slide": {"position": 1}, "element": {"ref": logical["ref"]}, "text": "After"})
        assert all(next(iter(r.values()))["objectId"] == logical["_content_ref"] for r in text_edits)
        edits += text_edits
    after = predict(raw, edits)
    updated = next(e for e in snapshot(after)["slides"][0]["elements"] if e["ref"] == logical["ref"])
    assert updated["kind"] == kind and updated["alt_text"] == "Accessible description"
    if kind == "callout":
        assert updated["text"] == "After\n"
    updated_group = next(e for e in after["slides"][0]["pageElements"] if e["objectId"] == logical["ref"])
    assert next(c for c in updated_group["elementGroup"]["children"] if c["objectId"] == frame["objectId"])["title"] == marker


@pytest.mark.parametrize("kind", ["image", "callout"])
@pytest.mark.parametrize("mutation", ["removed", "renamed", "wrong-content-type", "extra-child", "marker-on-group"])
def test_changed_frame_contract_reads_as_ordinary_group(kind, mutation):
    raw, group, _ = framed(kind)
    children = group["elementGroup"]["children"]
    frame = next(c for c in children if c.get("title", "").startswith("kdcube:"))
    content = next(c for c in children if c is not frame)
    if mutation == "removed":
        frame.pop("title")
    elif mutation == "renamed":
        frame["title"] = "My frame"
    elif mutation == "wrong-content-type":
        if kind == "image":
            content.pop("image")
            content["shape"] = {"shapeType": "TEXT_BOX"}
        else:
            content["shape"]["shapeType"] = "RECTANGLE"
    elif mutation == "extra-child":
        extra = copy.deepcopy(content)
        extra["objectId"] += "_extra"
        children.append(extra)
    else:
        group["title"] = frame.pop("title")
    assert frame_content(group) is None
    elements = snapshot(raw)["slides"][0]["elements"]
    ordinary = next(e for e in elements if e["ref"] == group["objectId"])
    assert ordinary["kind"] == "group" and "_content_ref" not in ordinary
    assert any(e["ref"] == content["objectId"] for e in elements)
    with pytest.raises(SlidesValidationError) as exc:
        compile("set_alt_text", {"element": {"ref": group["objectId"]}, "description": "Choose a child"}, raw)
    assert exc.value.code == "slides_group_alt_text_unsupported"


@pytest.mark.parametrize("kind", ["image", "callout"])
def test_ungrouped_frame_is_ordinary_content(kind):
    raw, group, _ = framed(kind)
    raw["slides"][0]["pageElements"].remove(group)
    raw["slides"][0]["pageElements"].extend(group["elementGroup"]["children"])
    elements = snapshot(raw)["slides"][0]["elements"]
    assert not any(e.get("_content_ref") for e in elements)
    assert all(any(e["ref"] == c["objectId"] for e in elements) for c in group["elementGroup"]["children"])


def test_resizing_callout_preserves_inset_and_marker():
    raw, group, _ = framed("callout")
    requests, _ = compile("compose", {"cols": [1, 1], "cells": [{"at": [0, 1], "element": {"ref": group["objectId"]}}]}, raw)
    after = predict(raw, requests)
    group = next(e for e in after["slides"][0]["pageElements"] if e["objectId"] == group["objectId"])
    assert frame_content(group)[0] == "callout"
    box = geometry(group)
    inner = geometry(frame_content(group)[1], matrix(group["transform"]))
    inset = TEXT_INSET_PT*EMU_PER_PT
    assert inner["x"] - box["x"] == pytest.approx(inset)
    assert inner["y"] - box["y"] == pytest.approx(inset)
    assert inner["width"] == pytest.approx(box["width"] - 2*inset)
    assert inner["height"] == pytest.approx(box["height"] - 2*inset)
