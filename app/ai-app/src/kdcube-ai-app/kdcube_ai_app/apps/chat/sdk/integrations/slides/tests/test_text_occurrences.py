"""Repeated literals choose exact UTF-16 ranges and expose bounded context."""
import copy

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.google.slides_structure import snapshot
from kdcube_ai_app.apps.chat.sdk.integrations.google.tests.test_slides_proxy import deck
from kdcube_ai_app.apps.chat.sdk.integrations.slides.comments import compile_anchor
from kdcube_ai_app.apps.chat.sdk.integrations.slides.editing import compile_edit
from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError
from kdcube_ai_app.apps.chat.sdk.integrations.slides.templates import replace_text_requests


def repeated(text="😀 hit hit\n", *, cell=False):
    raw = deck()
    shape = raw["slides"][0]["pageElements"][0]
    shape["shape"]["text"] = {"textElements": [{"textRun": {"content": text}}]}
    if cell:
        table = {key: copy.deepcopy(value) for key, value in shape.items() if key != "shape"}
        table.update(objectId="table_a", table={"rows": 1, "columns": 1, "tableRows": [{"tableCells": [{"text": copy.deepcopy(shape["shape"]["text"])}]}]})
        raw["slides"][0]["pageElements"].append(table)
    return raw, snapshot(raw)


def style(raw, view, **extra):
    return compile_edit(raw, view, "style_text", {"slide": {"position": 1}, "element": {"ref": "title_a"}, "quoted_text": "hit", "style": {"bold": True}, **extra})


def test_second_style_occurrence_uses_utf16_after_a_surrogate_pair():
    raw, view = repeated()
    requests, plan = style(raw, view, occurrence=2)
    assert requests[0]["updateTextStyle"]["textRange"] == {"type": "FIXED_RANGE", "startIndex": 7, "endIndex": 10}
    assert plan["occurrence"] == 2


@pytest.mark.parametrize("kind", ["style", "comment"])
def test_ambiguity_reports_occurrence_location_and_80_characters_on_each_side(kind):
    raw, view = repeated("prefix " * 20 + "hit " * 25 + "suffix " * 20)
    with pytest.raises(SlidesValidationError) as exc:
        if kind == "style":
            style(raw, view)
        else:
            compile_anchor(raw, view, {"quoted_text": "hit"})
    assert exc.value.code == "slides_text_ambiguous"
    details = exc.value.details
    assert details["occurrences"] == 25 and details["candidates_truncated"]
    assert len(details["candidates"]) == 20
    for number, candidate in enumerate(details["candidates"], 1):
        assert set(candidate) == {"occurrence", "where", "context"}
        assert candidate["occurrence"] == number and candidate["where"] == {"slide": "slide_a", "element": "title_a"}
        assert "hit" in candidate["context"] and len(candidate["context"]) <= 80 + len("hit") + 80


@pytest.mark.parametrize("kind", ["style", "comment"])
def test_long_quote_candidates_keep_the_full_quote_and_distinguishing_distant_context(kind):
    quote = "A long quoted phrase: " + "q" * 90
    before_first = "p" * 20 + "FIRST" + "p" * 55
    before_second = "p" * 20 + "SECOND" + "p" * 54
    after_first = "s" * 55 + "FIRST_AFTER" + "s" * 30
    after_second = "s" * 55 + "SECOND_AFTER" + "s" * 30
    raw, view = repeated(before_first + quote + after_first + "\n" + before_second + quote + after_second)
    with pytest.raises(SlidesValidationError) as exc:
        if kind == "style":
            style(raw, view, quoted_text=quote)
        else:
            compile_anchor(raw, view, {"quoted_text": quote})
    assert exc.value.code == "slides_text_ambiguous"
    first, second = [candidate["context"] for candidate in exc.value.details["candidates"]]
    assert quote in first and quote in second
    assert len(first) == len(second) == 80 + len(quote) + 80
    assert first.startswith(before_first) and second.startswith(before_second)
    assert "FIRST_AFTER" in first and "SECOND_AFTER" in second


def test_comment_occurrence_counts_across_shapes_in_slide_order():
    raw, _ = repeated()
    second = copy.deepcopy(raw["slides"][0])
    second["objectId"] = "slide_b"
    second["pageElements"][0]["objectId"] = "title_b"
    raw["slides"].append(second)
    anchor, target = compile_anchor(raw, snapshot(raw), {"quoted_text": "hit", "occurrence": 3})
    assert anchor["shapeTextAnchor"]["objectId"] == "title_b"
    assert anchor["shapeTextAnchor"]["textRange"]["startIndex"] == 3
    assert target["slide"] == "slide_b" and target["occurrence"] == 3


def test_comment_cell_ambiguity_and_occurrence_keep_cell_coordinates():
    raw, view = repeated(cell=True)
    payload = {"slide": {"position": 1}, "table": {"ref": "table_a"}, "row": 1, "column": 1, "quoted_text": "hit"}
    with pytest.raises(SlidesValidationError) as exc:
        compile_anchor(raw, view, payload)
    assert exc.value.details["candidates"][1]["where"] == {"slide": "slide_a", "table": "table_a", "row": 1, "column": 1}
    anchor, _ = compile_anchor(raw, view, {**payload, "occurrence": 2})
    cell = anchor["tableCellTextAnchor"]
    assert cell["cellLocation"] == {"rowIndex": 0, "columnIndex": 0}
    assert cell["textRange"]["startIndex"] == 7


def test_replacement_occurrence_edits_one_match_and_preserves_bulk_default():
    _, view = repeated()
    requests, plan = replace_text_requests(view, {"replacements": {"hit": "chosen"}, "occurrence": 2})
    assert len(requests) == 2 and requests[0]["deleteText"]["textRange"]["startIndex"] == 7
    assert requests[1]["insertText"]["text"] == "chosen"
    assert plan[0]["after"] == "😀 hit chosen\n"
    bulk, _ = replace_text_requests(view, {"replacements": {"hit": "all"}})
    assert bulk[0]["replaceAllText"]["replaceText"] == "all"


def test_replacement_occurrence_counts_shape_and_table_cell_matches():
    raw, view = repeated(cell=True)
    requests, plan = replace_text_requests(view, {"replacements": {"hit": "chosen"}, "occurrence": 4}, presentation=raw)
    assert requests[0]["deleteText"]["objectId"] == "table_a"
    assert requests[0]["deleteText"]["cellLocation"] == {"rowIndex": 0, "columnIndex": 0}
    assert requests[0]["deleteText"]["textRange"]["startIndex"] == 7
    assert plan[0]["row"] == plan[0]["column"] == 1


@pytest.mark.parametrize("number", [0, -1, True, "2", 3])
def test_invalid_style_occurrences_compile_no_request(number):
    raw, view = repeated()
    with pytest.raises(SlidesValidationError) as exc:
        style(raw, view, occurrence=number)
    assert exc.value.code == "slides_invalid_occurrence"


def test_occurrence_alone_cannot_fall_back_to_a_file_comment():
    raw, view = repeated()
    with pytest.raises(SlidesValidationError) as exc:
        compile_anchor(raw, view, {"occurrence": 1})
    assert exc.value.code == "slides_comment_target_required"
