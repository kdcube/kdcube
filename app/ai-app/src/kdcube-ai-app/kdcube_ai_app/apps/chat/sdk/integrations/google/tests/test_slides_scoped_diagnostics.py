"""Write replies retain the deck's counts while keeping element rows local."""
import copy

import httpx
import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.google.tests.test_slides_proxy import deck, run


def two_slides():
    value = deck()
    second = copy.deepcopy(value["slides"][0])
    second["objectId"] = "slide_b"
    second["pageElements"][0]["objectId"] = "title_b"
    second["pageElements"][0]["shape"]["text"]["textElements"][0]["textRun"]["content"] = "Untouched"
    value["slides"].append(second)
    return value


@pytest.mark.parametrize("preview", [False, True])
def test_one_slide_write_does_not_list_other_slide_elements(monkeypatch, preview):
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=two_slides())
        assert not preview
        return httpx.Response(200, json={"writeControl": {"requiredRevisionId": "r2"}, "replies": [{}]})

    result = run(monkeypatch, "replace_text", {"presentation_ref": "deck", "slide": {"position": 1}, "replacements": {"Hello": "Hi"}, "preview": preview}, handler)
    assert result["ok"]
    body = result["ret"]
    assert {row["slide"] for row in body["fit"]} == {"slide_a"}
    assert "title_b" not in str(body["fit"] + body["overlaps"] + body["off_slide"])
    assert body["diagnostics_summary"]["slides"] == 2
    assert sum(body["diagnostics_summary"]["fit"].values()) == 2


@pytest.mark.parametrize("operation", ["copy", "import", "fill_template"])
def test_whole_deck_creations_return_summary_only(monkeypatch, operation):
    def handler(request):
        if request.method == "GET":
            if request.url.host == "www.googleapis.com":
                return httpx.Response(200, json={"id": "source", "name": "Source", "mimeType": "application/vnd.google-apps.presentation"})
            return httpx.Response(200, json=two_slides())
        if request.url.host == "www.googleapis.com":
            return httpx.Response(200, json={"id": "deck", "name": "Copy", "mimeType": "application/vnd.google-apps.presentation"})
        return httpx.Response(200, json={"writeControl": {"requiredRevisionId": "r2"}, "replies": [{}]})

    payload = {"presentation_ref": "deck", "source_file": "source", "title": "Copy", "values": {"NAME": "Ada"}}
    result = run(monkeypatch, operation, payload, handler)
    assert result["ok"]
    assert result["ret"]["diagnostics_summary"]["slides"] == 2
    assert not {"fit", "overlaps", "off_slide"}.intersection(result["ret"])


def test_full_read_keeps_per_element_diagnostics(monkeypatch):
    result = run(monkeypatch, "get", {"presentation_ref": "deck"}, lambda req: httpx.Response(200, json=two_slides()))
    assert result["ok"]
    slides = result["ret"]["snapshot"]["slides"]
    assert len(slides) == 2
    assert all(slide["elements"][0]["fit"] for slide in slides)
