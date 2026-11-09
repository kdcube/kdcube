"""Compact snapshots and native reads preserve selection while bounding output."""
import copy

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.google.tests.test_slides_s3_proxy import Provider, call
from kdcube_ai_app.apps.chat.sdk.integrations.google.slides_structure import snapshot


def many_pages():
    provider = Provider()
    first = copy.deepcopy(provider.state["slides"][0])
    provider.state["slides"] = []
    for n in range(13):
        page = copy.deepcopy(first)
        page["objectId"] = f"page_{n+1}"
        provider.state["slides"].append(page)
    provider.state["layouts"] = [{"objectId": "layout_fixture", "pageElements": []}]
    provider.state["masters"] = [{"objectId": "master_fixture", "pageElements": []}]
    return provider


def test_default_snapshot_omits_expensive_optional_projections():
    raw = many_pages().state
    view = snapshot(raw)
    assert len(view["slides"]) == 10
    assert view["range"]["next_slide"] == 11
    assert "layouts" not in view
    assert all("relations" not in page and page.get("free_regions") != [] for page in view["slides"])
    expanded = snapshot(raw, include=["layouts", "relations"])
    assert expanded["layouts"] and "relations" in expanded["slides"][0]


def test_snapshot_omits_a_trivial_grid_and_empty_free_regions():
    raw = many_pages().state
    raw["slides"] = raw["slides"][:1]
    raw["slides"][0]["pageElements"] = raw["slides"][0]["pageElements"][:1]
    raw["slides"][0]["pageElements"][0]["shape"].pop("placeholder", None)
    page = snapshot(raw)["slides"][0]
    assert "grid" not in page and "free_regions" not in page


def test_native_read_is_paged_and_themes_are_opt_in(monkeypatch):
    result, _ = call(monkeypatch, "get_structure", {}, many_pages())
    assert result["ok"], result
    assert len(result["ret"]["structure"]["slides"]) == 10
    assert result["ret"]["range"]["next_slide"] == 11
    assert "layouts" not in result["ret"]["structure"] and "masters" not in result["ret"]["structure"]


@pytest.mark.parametrize("payload", [{"slide_selector": {"position": 13}}, {"start_slide": 13, "slide_limit": 1}])
def test_native_read_selects_a_late_slide_with_requested_theme(monkeypatch, payload):
    result, _ = call(monkeypatch, "get_structure", {**payload, "include": ["layouts", "masters"]}, many_pages())
    assert result["ok"], result
    raw = result["ret"]["structure"]
    assert [page["objectId"] for page in raw["slides"]] == ["page_13"]
    assert raw["layouts"] and raw["masters"]


def test_raw_batch_returns_only_the_page_it_changed(monkeypatch):
    provider = many_pages()
    for n, page in enumerate(provider.state["slides"]):
        for element in page["pageElements"]:
            element["objectId"] += f"_{n}"
    target = provider.state["slides"][9]["pageElements"][0]["objectId"]
    result, _ = call(monkeypatch, "batch_update", {"requiredRevisionId": "r1", "requests": [{"deleteObject": {"objectId": target}}]}, provider)
    assert result["ok"], result
    assert result["ret"]["touched_slide_ids"] == ["page_10"]
    assert [page["objectId"] for page in result["ret"]["structure"]["slides"]] == ["page_10"]
    assert "layouts" not in result["ret"]["structure"] and "masters" not in result["ret"]["structure"]
