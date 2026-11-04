"""Selectors, refusal boundaries and previews over neutral provider-shaped data."""
import copy
import json
from pathlib import Path

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.google.slides_structure import snapshot
from kdcube_ai_app.apps.chat.sdk.integrations.slides.comments import compile_anchor, comment_content
from kdcube_ai_app.apps.chat.sdk.integrations.slides.composition import compile_composition
from kdcube_ai_app.apps.chat.sdk.integrations.slides.prediction import predict
from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError
from kdcube_ai_app.apps.chat.sdk.integrations.slides.tables import compile_table, table_record
from kdcube_ai_app.apps.chat.sdk.integrations.slides.video import video_source

FIXTURE = Path(__file__).parents[2]/"google/tests/fixtures/slides/s3/comments.json"


def deck():
    return json.loads(FIXTURE.read_text())["presentation"]


def table_payload(**extra):
    return {"slide": {"position": 1}, "table": {"kind": "table", "index": 1}, **extra}


def test_cell_predicate_uses_named_header_and_preserves_the_terminal_newline():
    raw = deck()
    requests, plan = compile_table(raw, snapshot(raw), "set_cells", table_payload(header=1, cells=[{"row": {"where": {"column": "Stage", "equals": "Ready"}}, "column": "Owner", "text": "New editor"}]))
    assert requests[0]["deleteText"]["cellLocation"] == {"rowIndex": 1, "columnIndex": 1}
    assert requests[0]["deleteText"]["textRange"]["endIndex"] == 6
    assert requests[1]["insertText"]["text"] == "New editor"
    after = snapshot(predict(raw, requests))["slides"][0]["elements"][1]["table"]
    assert after["cells"][1]["cells"][1]["text"] == "New editor\n"
    assert plan["cells"][0]["row"] == 2
    assert deck() == raw


def test_ambiguous_rows_are_refused_with_bounded_candidates():
    raw = deck()
    raw["slides"][0]["pageElements"][1]["table"]["tableRows"][2]["tableCells"][0]["text"] = {"textElements": [{"textRun": {"content": "Ready\n"}}]}
    with pytest.raises(SlidesValidationError) as exc:
        compile_table(raw, snapshot(raw), "set_cells", table_payload(header=1, cells=[{"row": {"where": {"column": "Stage", "equals": "Ready"}}, "column": "Owner", "text": "X"}]))
    assert exc.value.code == "slides_table_row_ambiguous"
    assert len(exc.value.details["candidates"]) == 2


@pytest.mark.parametrize("row,column", [(1, 1), (1, 2)])
def test_merged_origin_and_covered_cell_are_both_refused(row, column):
    raw = deck()
    first = raw["slides"][0]["pageElements"][1]["table"]["tableRows"][0]
    first["tableCells"][0]["columnSpan"] = 2
    first["tableCells"].pop()
    with pytest.raises(SlidesValidationError) as exc:
        compile_table(raw, snapshot(raw), "set_cells", table_payload(cells=[{"row": row, "column": column, "text": "X"}]))
    assert exc.value.code == "slides_table_cell_merged"


@pytest.mark.parametrize("payload", [
    {"cells": [{"row": 2, "column": "Owner", "text": "X"}]},
    {"cells": [{"row": 2, "column": 1, "text": "X"}]*2},
    {"cells": [{"row": 2, "column": 1, "text": 123}]},
    {"cells": [{"row": 2, "column": 1, "text": "bad\rcontrol"}]},
    {"cells": []},
])
def test_invalid_cell_writes_compile_nothing(payload):
    raw = deck()
    with pytest.raises(SlidesValidationError):
        compile_table(raw, snapshot(raw), "set_cells", table_payload(**payload))
    assert raw == deck()


@pytest.mark.parametrize("relation,expected", [("before", 1), ("after", 2)])
def test_add_row_resolves_predicate_and_column_map(relation, expected):
    raw = deck()
    requests, plan = compile_table(raw, snapshot(raw), "add_row", table_payload(header=1, **{relation: {"where": {"column": "Stage", "contains": "Ready"}}}, values={"Stage": "Next", "Owner": "Editor"}))
    insertion = requests[0]["insertTableRows"]
    assert insertion["cellLocation"]["rowIndex"] == 1
    assert insertion["insertBelow"] == (relation == "after")
    assert requests[1]["insertText"]["cellLocation"]["rowIndex"] == expected
    after = snapshot(predict(raw, requests))["slides"][0]["elements"][1]["table"]
    assert after["rows"] == 4
    assert after["cells"][expected]["cells"][0]["text"] == "Next\n"
    assert plan["added_rows"][0]["row"] == expected+1


@pytest.mark.parametrize("placement", [{"region": "body"}, {"below": {"role": "title"}, "width": 1}])
@pytest.mark.parametrize("action,data", [("add_table", {"values": [["A", "B"], ["One", "Two"]]}), ("add_video", {"video": "https://youtu.be/AbCdEf012_-"})])
def test_new_table_and_video_use_computed_regions_and_relative_placement(action, data, placement):
    raw = deck()
    requests, plan = compile_composition(raw, snapshot(raw), action, {"slide": {"position": 1}, **placement, **data})
    create = requests[0]["createTable" if action == "add_table" else "createVideo"]
    assert create["elementProperties"]["pageObjectId"] == "page_fixture"
    assert create["elementProperties"]["transform"]["scaleX"] == 1
    after = snapshot(predict(raw, requests))["slides"][0]["elements"][-1]
    assert after["kind"] == ("table" if action == "add_table" else "video")
    assert plan["elements"][0]["ref"] == after["ref"]


def test_compose_creates_table_and_video_in_distinct_cells_and_predicts_cell_text():
    raw = deck()
    requests, _ = compile_composition(raw, snapshot(raw), "compose", {"slide": {"position": 1}, "cols": [1, 1], "cells": [{"at": [0, 0], "element": {"table": {"values": [["Header"], ["Value"]]}}}, {"at": [0, 1], "element": {"video": {"source": "drive", "id": "video_fixture"}}}]})
    assert any("createTable" in r for r in requests) and any("createVideo" in r for r in requests)
    view = snapshot(predict(raw, requests))
    table = view["slides"][0]["elements"][-2]["table"]
    assert table["cells"][1]["cells"][0]["text"] == "Value\n"


def test_table_snapshot_pages_rows_with_reusable_selectors():
    raw = deck()
    first = snapshot(raw, table_limit=1)["slides"][0]["elements"][1]["table"]
    second = snapshot(raw, table_start=first["next_row"], table_limit=2)["slides"][0]["elements"][1]["table"]
    assert first["next_row"] == 2 and second["next_row"] is None
    assert second["cells"][0]["row"] == 2
    assert first["selector"] == {"slide": {"ref": "page_fixture"}, "table": {"ref": "table_fixture"}}


@pytest.mark.parametrize("video,source,identity", [
    ("https://youtu.be/AbCdEf012_-", "YOUTUBE", "AbCdEf012_-"),
    ("https://www.youtube.com/watch?v=AbCdEf012_-", "YOUTUBE", "AbCdEf012_-"),
    ("https://drive.google.com/file/d/video_fixture/view", "DRIVE", "video_fixture"),
    ({"source": "drive", "id": "video_fixture"}, "DRIVE", "video_fixture"),
])
def test_video_sources(video, source, identity):
    assert video_source(video) == {"source": source, "id": identity}


@pytest.mark.parametrize("video", ["http://youtu.be/AbCdEf012_-", "https://evil.test/AbCdEf012_-", "https://youtube.com:bad/watch?v=AbCdEf012_-", "https://user:pass@youtube.com/watch?v=AbCdEf012_-", "https://youtu.be/short", {"source": "drive", "url": "https://youtu.be/AbCdEf012_-"}])
def test_invalid_video_sources_are_refused(video):
    with pytest.raises(SlidesValidationError) as exc:
        video_source(video)
    assert exc.value.code == "slides_invalid_video"


@pytest.mark.parametrize("target,kind", [
    ({}, None),
    ({"slide": {"position": 1}}, "objectId"),
    ({"slide": {"position": 1}, "element": {"role": "title"}}, "objectId"),
    ({"quoted_text": "Roadmap"}, "shapeTextAnchor"),
    (table_payload(row=2, column=1), "tableCellTextAnchor"),
    (table_payload(table_range={"from": {"row": 1, "column": 1}, "to": {"row": 2, "column": 2}}), "tableAnchor"),
])
def test_each_comment_anchor_compiles_directly_without_an_anchor_wrapper(target, kind):
    raw = deck()
    anchor, plan = compile_anchor(raw, snapshot(raw), target)
    assert (next(iter(anchor)) if anchor else None) == kind
    assert not anchor or "anchor" not in anchor
    if kind == "shapeTextAnchor":
        assert anchor[kind]["textRange"] == {"type": "FIXED_RANGE", "startIndex": 3, "endIndex": 10}
    assert plan["scope"] in {"file", "page", "element", "text", "cell", "table_range"}


@pytest.mark.parametrize("target", [table_payload(row=3, column=1), table_payload(table_range={"from": {"row": 3, "column": 1}, "to": {"row": 3, "column": 2}})])
def test_empty_cell_and_range_comments_refuse_before_provider_write(target):
    raw = deck()
    with pytest.raises(SlidesValidationError) as exc:
        compile_anchor(raw, snapshot(raw), target)
    assert exc.value.code == "slides_comment_target_empty"


@pytest.mark.parametrize("target", [{"element": {"ref": "shape_fixture"}}, {"row": 1, "column": 1}, table_payload(row=2), table_payload(row=2, column=1, table_range={})])
def test_partial_or_conflicting_targets_never_become_file_comments(target):
    raw = deck()
    with pytest.raises(SlidesValidationError) as exc:
        compile_anchor(raw, snapshot(raw), target)
    assert exc.value.code == "slides_comment_target_required"


def test_comment_limit_counts_utf8_bytes_and_preserves_content():
    assert comment_content("😀"*512) == "😀"*512
    with pytest.raises(SlidesValidationError):
        comment_content("😀"*513)


def test_table_cells_keep_sparse_locations_after_a_merge():
    raw = deck()["slides"][0]["pageElements"][1]
    raw["table"]["columns"] = 3
    first = raw["table"]["tableRows"][0]["tableCells"]
    first[0]["columnSpan"] = 2
    first[1]["location"]["columnIndex"] = 2
    record = table_record(raw)
    assert record["cells"][0][1]["merged"]
    assert record["cells"][0][2]["text"] == "Owner\n"
