"""Regressions for slow Drive copies, short title frames and response metadata."""
import asyncio
import copy
import json

import httpx
import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.google import slides_proxy as proxy
from kdcube_ai_app.apps.chat.sdk.integrations.google.slides_structure import fit
from kdcube_ai_app.apps.chat.sdk.integrations.slides.named_service import make_slides_named_service_provider
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import NamedServiceContext, NamedServiceRequest


def presentation(deck_id="deck"):
    return {
        "presentationId": deck_id, "title": "Source deck", "revisionId": "r1",
        "pageSize": {"width": {"magnitude": 720, "unit": "PT"}, "height": {"magnitude": 405, "unit": "PT"}},
        "slides": [{"objectId": "slide_a", "pageElements": [{
            "objectId": "title_a",
            "size": {"width": {"magnitude": 600, "unit": "PT"}, "height": {"magnitude": 45, "unit": "PT"}},
            "shape": {"placeholder": {"type": "TITLE"}, "text": {"textElements": [{"textRun": {
                "content": "Slide1! {{NAME}}\n", "style": {"fontSize": {"magnitude": 28}},
            }}]}},
        }]}],
    }


def transport(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(proxy.httpx, "AsyncClient", lambda *a, **kw: real(*a, **{**kw, "transport": httpx.MockTransport(handler)}))


@pytest.mark.parametrize("operation,payload", [
    ("copy", {"title": "Copied deck"}),
    ("fill_template", {"title": "Copied deck", "values": {"NAME": "Ada"}}),
    ("export", {"format": "pdf"}),
])
def test_slow_drive_file_operation_can_finish_after_thirty_seconds(monkeypatch, operation, payload):
    slow_calls = []

    def handler(request):
        slow = request.url.path.endswith(("/copy", "/export"))
        if slow:
            slow_calls.append(request.url.path)
            # Model a 45-second provider operation without sleeping. The old
            # 30-second budget raises the same ReadTimeout observed live.
            if request.extensions["timeout"]["read"] < 45:
                raise httpx.ReadTimeout("provider still processing", request=request)
            assert request.extensions["timeout"] == {"connect": 10, "read": 120, "write": 120, "pool": 120}
            if request.url.path.endswith("/export"):
                return httpx.Response(200, content=b"pdf", headers={"Content-Type": "application/pdf"})
            return httpx.Response(200, json={"id": "copied_deck", "name": "Copied deck"})
        assert request.extensions["timeout"]["read"] == 30
        if request.method == "POST":
            return httpx.Response(200, json={"writeControl": {"requiredRevisionId": "r2"}})
        return httpx.Response(200, json=presentation("copied_deck" if request.url.path.endswith("/copied_deck") else "deck"))

    transport(monkeypatch, handler)
    out = asyncio.run(proxy.execute_google_slides_operation(operation=operation, access_token="fixture-token", payload={"presentation_ref": "deck", **payload}))
    assert out["ok"], out
    assert len(slow_calls) == 1


def test_copy_timeout_after_extended_deadline_remains_unknown_and_is_not_retried(monkeypatch):
    copies = []

    def handler(request):
        if request.url.path.endswith("/copy"):
            copies.append(request)
            raise httpx.ReadTimeout("provider outcome unknown", request=request)
        return httpx.Response(200, json=presentation())

    transport(monkeypatch, handler)
    out = asyncio.run(proxy.execute_google_slides_operation(operation="copy", access_token="fixture-token", payload={"presentation_ref": "deck", "title": "Copied deck"}))
    assert not out["ok"] and out["ret"]["outcome_unknown"]
    assert len(copies) == 1
    assert "created_presentation_id" not in out["ret"]


@pytest.mark.parametrize("role", ["TITLE", "CENTERED_TITLE"])
def test_short_single_line_title_keeps_vertical_capacity(role):
    shape = copy.deepcopy(presentation()["slides"][0]["pageElements"][0]["shape"])
    shape["placeholder"]["type"] = role
    box = {"width": 600 * 12700, "height": 45 * 12700}
    title = fit(shape, box, {})
    assert title["lines_needed"] == 1 and title["lines_available"] > 1
    assert title["status"] in {"ok", "tight"}
    shape["placeholder"]["type"] = "BODY"
    assert fit(shape, box, {})["status"] == "overflow_risk"
    shape["placeholder"]["type"] = role
    shape["text"]["textElements"][0]["textRun"]["content"] = "Long title " * 50
    assert fit(shape, box, {})["status"] == "overflow_risk"


def test_slide_read_has_slide_kind_title_and_schema():
    async def execute(**kwargs):
        return {"ok": True, "ret": {
            "presentation_id": "deck", "title": "Source deck", "revision_id": "r1",
            "snapshot": {"slides": [{"ref": "slide_a", "title": "Slide1!", "elements": []}], "range": {"next_slide": None}},
        }}

    provider = make_slides_named_service_provider(execute_operation=execute)
    ref = "slides:google:account:slide:slide_a:deck"
    response = asyncio.run(provider.dispatch(NamedServiceContext(), NamedServiceRequest(namespace="slides", operation="object.get", object_ref=ref)))
    assert response.ok
    obj = response.ret["object"]
    assert (obj["ref"], obj["kind"], obj["title"]) == (ref, "slides.slide", "Slide1!")
    assert provider.schema_object_kind_from_ref(ref) == "slides.slide"
    assert "slides.slide" in provider.spec.object_kinds


def test_edit_preview_keeps_deck_title_through_named_service(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json=presentation())

    transport(monkeypatch, handler)

    async def execute(**kwargs):
        return await proxy.execute_google_slides_operation(operation=kwargs["operation"], access_token="fixture-token", payload=kwargs["payload"])

    provider = make_slides_named_service_provider(execute_operation=execute)
    response = asyncio.run(provider.dispatch(NamedServiceContext(), NamedServiceRequest(
        namespace="slides", operation="object.action", action="replace_text",
        object_ref="slides:google:account:presentation:deck",
        payload={"preview": True, "replacements": {"Slide1!": "Updated title"}},
    )))
    assert response.ok
    assert response.ret["object"]["title"] == "Source deck"
    assert response.ret["result"]["preview"]
    assert calls == ["GET"]
    assert "Updated title" in json.dumps(response.ret["result"]["changes"])
