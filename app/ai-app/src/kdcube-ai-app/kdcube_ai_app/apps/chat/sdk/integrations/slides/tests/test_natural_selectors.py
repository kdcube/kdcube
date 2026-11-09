"""Natural identities resolve inside Slides, without title or account guesses."""
import asyncio

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.slides.tests.test_named_service import provider, request, snapshot
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import NamedServiceContext


@pytest.mark.parametrize("operation", ["object.search", "object.get"])
@pytest.mark.parametrize("selector", [
    {"url": "https://docs.google.com/presentation/d/deck/edit"},
    {"url": "https://docs.google.com/presentation/d/deck/edit?slide=id.s42#slide=id.s42"},
    {"url": "https://docs.google.com/presentation/u/0/d/deck/present?usp=sharing"},
    {"url": "https://docs.google.com/presentation/d/deck/preview"},
    {"url": "https://drive.google.com/file/d/deck/view"},
    {"url": "https://drive.google.com/open?id=deck"},
    {"presentation_id": "deck"},
    {"url": "deck"},
])
def test_natural_lookup_returns_a_canonical_account_bound_ref(operation, selector):
    calls = []
    async def execute(**kwargs):
        calls.append(kwargs)
        row = {"account_id": "chosen", "presentation_id": "deck", "title": "Exact deck", "snapshot": snapshot()}
        return {"ok": True, "ret": {**row, "items": [row]}}
    response = asyncio.run(provider(execute).dispatch(NamedServiceContext(), request(operation, filters={**selector, "account_id": "chosen"})))
    assert response.ok, response.to_dict()
    objects = response.ret["items"] if operation == "object.search" else [response.ret["object"]]
    assert objects[0]["ref"] == "slides:google:chosen:presentation:deck"
    call = calls[0]
    assert call["account_id"] == "chosen" and call["claim"] == ["slides:read"]
    assert call["payload"].get("presentation_id", call["payload"].get("presentation_ref")) == "deck"
    assert "query" not in call["payload"]


@pytest.mark.parametrize("operation", ["object.search", "object.get"])
@pytest.mark.parametrize("value", ["not a presentation URL", "https://example.org/presentation/d/deck/edit", "https://docs.google.com.evil/presentation/d/deck/edit", "https://[", "slides:google:a:presentation:deck", None])
def test_unrecognized_shapes_never_fall_back_to_search_or_call_google(operation, value):
    async def execute(**_):
        pytest.fail("An invalid selector must not resolve credentials or search titles")
    response = asyncio.run(provider(execute).dispatch(NamedServiceContext(), request(operation, query="a plausible title", filters={"url": value})))
    if operation == "object.search":
        assert response.ok and response.ret["items"] == []
    else:
        assert not response.ok and response.error["code"] == "slides_presentation_not_found"


@pytest.mark.parametrize("operation", ["object.search", "object.get"])
def test_two_natural_selectors_are_refused(operation):
    async def execute(**_):
        pytest.fail("Conflicting selector shapes must not execute")
    response = asyncio.run(provider(execute).dispatch(NamedServiceContext(), request(operation, filters={"url": "deck", "presentation_id": "deck"})))
    assert not response.ok and response.error["code"] == "slides_invalid_selector"


def test_a_natural_filter_cannot_retarget_a_bound_ref():
    async def execute(**_):
        pytest.fail("A conflicting bound identity must not execute")
    response = asyncio.run(provider(execute).dispatch(NamedServiceContext(), request("object.get", object_ref="slides:google:bound:presentation:deck", filters={"url": "other"})))
    assert not response.ok and response.error["code"] == "slides_source_mismatch"


@pytest.mark.parametrize("operation", ["object.search", "object.get"])
def test_natural_lookup_preserves_account_required_and_labeled_candidates(operation):
    candidates = [{"account_id": "personal", "label": "Personal"}, {"account_id": "work", "label": "Work"}]
    async def execute(**kwargs):
        assert kwargs["account_id"] == ""
        return {"ok": False, "error": {"code": "needs_connected_account_consent", "message": "Choose an account"}, "consent": {"reason": "account_required", "candidates": candidates}}
    response = asyncio.run(provider(execute).dispatch(NamedServiceContext(), request(operation, filters={"presentation_id": "deck"})))
    assert not response.ok
    assert response.error["details"]["reason"] == "account_required"
    assert response.error["details"]["candidates"] == candidates
