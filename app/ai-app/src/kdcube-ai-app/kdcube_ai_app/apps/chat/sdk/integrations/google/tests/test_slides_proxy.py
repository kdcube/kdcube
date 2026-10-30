"""Slides and Drive request contracts over an in-memory HTTP transport."""
import asyncio
import base64
import copy
import json

import httpx
import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.google import slides_proxy as proxy


def deck():
    return {"presentationId": "deck", "title": "Template", "revisionId": "r1", "pageSize": {"width": {"magnitude": 9144000}, "height": {"magnitude": 5143500}}, "slides": [{"objectId": "slide_a", "pageElements": [{"objectId": "title_a", "size": {"width": {"magnitude": 3000000}, "height": {"magnitude": 1000000}}, "shape": {"placeholder": {"type": "TITLE"}, "text": {"textElements": [{"textRun": {"content": "Hello {{NAME}}"}}]}}}]}]}


def editable_deck():
    value = deck()
    title = copy.deepcopy(value["slides"][0]["pageElements"][0])
    title["objectId"] = "layout_title"
    body = copy.deepcopy(title)
    body["objectId"] = "layout_body"
    body["shape"]["placeholder"]["type"] = "BODY"
    value["layouts"] = [{"objectId": "layout_a", "layoutProperties": {"name": "TITLE_AND_BODY", "displayName": "Title and body", "masterObjectId": "master_a"}, "pageElements": [title, body]}]
    value["masters"] = [{"objectId": "master_a", "pageElements": []}]
    value["slides"][0]["slideProperties"] = {"layoutObjectId": "layout_a", "masterObjectId": "master_a"}
    return value


def run(monkeypatch, operation, payload, handler, *, token="fixture-token"):
    real = httpx.AsyncClient
    def factory(*args, **kwargs):
        return real(*args, **{**kwargs, "transport": httpx.MockTransport(handler)})
    monkeypatch.setattr(proxy.httpx, "AsyncClient", factory)
    return asyncio.run(proxy.execute_google_slides_operation(operation=operation, access_token=token, payload=payload))


def test_read_is_masked_bounded_and_token_free(monkeypatch):
    def handler(request):
        assert request.headers["authorization"] == "Bearer fixture-token"
        assert "fields" in request.url.params
        assert "contentUrl" not in request.url.params["fields"]
        assert "revisionId" in request.url.params["fields"]
        return httpx.Response(200, json=deck())
    out = run(monkeypatch, "get", {"presentation_ref": "https://docs.google.com/presentation/d/deck/edit"}, handler)
    assert out["ok"]
    assert out["ret"]["snapshot"]["slides"][0]["title"] == "Hello {{NAME}}"
    assert "fixture-token" not in json.dumps(out)


def test_slide_handle_selection_uses_one_fresh_read_beyond_page_one(monkeypatch):
    calls=[]
    def handler(request):
        calls.append(request.method)
        value=deck()
        value["slides"]=[{**copy.deepcopy(value["slides"][0]),"objectId":f"slide_{i}"} for i in range(1,61)]
        return httpx.Response(200,json=value)
    result=run(monkeypatch,"get",{"presentation_ref":"deck","slide_selector":{"position":55}},handler)
    assert result["ok"] and result["ret"]["snapshot"]["slides"][0]["ref"] == "slide_55"
    assert len(result["ret"]["snapshot"]["slides"]) == 1
    assert calls == ["GET"]


def test_search_quotes_drive_query_and_preserves_next_cursor(monkeypatch):
    def handler(request):
        assert "name contains 'O\\'Brien'" in request.url.params["q"]
        assert request.url.params["pageToken"] == "next-page"
        return httpx.Response(200, json={"files": [{"id": "deck", "name": "O'Brien"}], "nextPageToken": "page-3"})
    result = run(monkeypatch, "search", {"query": "O'Brien", "cursor": "next-page"}, handler)
    assert result["ret"]["next_cursor"] == "page-3"


def test_supplied_stale_revision_writes_nothing(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json=deck())
    result = run(monkeypatch, "replace_text", {"presentation_ref": "deck", "revision_id": "old", "replacements": {"Hello": "Hi"}}, handler)
    assert result["error"]["code"] == "slides_revision_changed"
    assert calls == ["GET"]


def test_replace_all_text_always_carries_revision_guard(monkeypatch):
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=deck())
        body = json.loads(request.content)
        assert body["writeControl"] == {"requiredRevisionId": "r1"}
        assert body["requests"][0]["replaceAllText"]["pageObjectIds"] == ["slide_a"]
        return httpx.Response(200, json={"writeControl": {"requiredRevisionId": "r2"}, "replies": [{}]})
    result = run(monkeypatch, "replace_text", {"presentation_ref": "deck", "replacements": {"Hello": "Hi"}}, handler)
    assert result["ok"] and result["ret"]["revision_id"] == "r2"
    assert result["ret"]["fit"]


def test_race_re_resolves_once_without_caller_revision(monkeypatch):
    reads, writes = [], []
    def handler(request):
        if request.method == "GET":
            reads.append(1)
            body = deck()
            body["revisionId"] = "r1" if len(reads) == 1 else "r2"
            return httpx.Response(200, json=body)
        writes.append(json.loads(request.content)["writeControl"]["requiredRevisionId"])
        if len(writes) == 1:
            return httpx.Response(400, json={"error": {"message": "The required revision ID 'r1' does not match the latest revision."}})
        return httpx.Response(200, json={"writeControl": {"requiredRevisionId": "r3"}})
    result = run(monkeypatch, "replace_text", {"presentation_ref": "deck", "replacements": {"Hello": "Hi"}}, handler)
    assert result["ok"]
    assert writes == ["r1", "r2"]


def test_caller_guard_race_is_never_retried(monkeypatch):
    writes = []
    def handler(request):
        if request.method == "GET":return httpx.Response(200, json=deck())
        writes.append(1)
        return httpx.Response(400, json={"error": {"message": "The required revision ID 'r1' does not match the latest revision."}})
    result = run(monkeypatch, "replace_text", {"presentation_ref": "deck", "revision_id": "r1", "replacements": {"Hello": "Hi"}}, handler)
    assert result["error"]["code"] == "slides_revision_changed" and len(writes) == 1


def test_multi_batch_chains_returned_revisions(monkeypatch):
    revisions = []
    def handler(request):
        body = json.loads(request.content)
        revisions.append(body["writeControl"]["requiredRevisionId"])
        return httpx.Response(200, json={"writeControl": {"requiredRevisionId": f"r{len(revisions)+1}"}, "replies": [{}]})
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            api = proxy.SlidesClient(client, "fixture-token")
            return await api.write_batches("deck", [{"replaceAllText": {}}]*201, "r1", operation="replace_text")
    result = asyncio.run(exercise())
    assert revisions == ["r1", "r2", "r3"]
    assert result["revision_id"] == "r4"


def test_template_missing_tag_and_preview_do_not_copy(monkeypatch):
    writes = []
    def handler(request):
        if request.method != "GET":writes.append(request.method)
        return httpx.Response(200, json=deck())
    missing = run(monkeypatch, "fill_template", {"source": "deck", "title": "Copy", "values": {"MISSING": "value"}}, handler)
    assert missing["error"]["code"] == "slides_template_tag_missing" and not writes
    # Reset the patched factory between invocations (each run captures it).


def test_template_preview_is_read_only(monkeypatch):
    def handler(request):
        assert request.method == "GET"
        return httpx.Response(200, json=deck())
    result = run(monkeypatch, "fill_template", {"source": "deck", "title": "Copy", "values": {"NAME": "Ada"}, "preview": True}, handler)
    assert result["ok"] and result["ret"]["preview"]
    assert result["ret"]["fit_predicted"] and result["ret"]["fit"]


def test_unreadable_template_is_actionable_and_does_not_create(monkeypatch):
    def handler(request):
        assert request.method == "GET"
        return httpx.Response(404,json={"error":{"message":"File not found"}})
    result=run(monkeypatch,"fill_template",{"source":"deck","title":"Copy","values":{"NAME":"Ada"}},handler)
    assert result["error"]["code"] == "slides_template_unreadable"


def test_preview_predicts_overflow_and_never_mutates_the_read(monkeypatch):
    original=deck()
    def handler(request):
        assert request.method == "GET"
        return httpx.Response(200,json=original)
    result=run(monkeypatch,"replace_text",{"presentation_ref":"deck","replacements":{"Hello {{NAME}}":"A"*5000},"preview":True},handler)
    assert result["ret"]["fit_predicted"]
    assert result["ret"]["fit"][0]["status"] == "overflow_risk"
    assert original == deck()


def test_targeted_replace_preserves_other_runs_and_uses_utf16_ranges():
    from kdcube_ai_app.apps.chat.sdk.integrations.google.slides_structure import shape_text

    original = deck()
    shape = original["slides"][0]["pageElements"][0]["shape"]
    shape["text"]["textElements"] = [
        {"textRun": {"content": "Keep ", "style": {"bold": True, "fontSize": {"magnitude": 18}}}},
        {"textRun": {"content": "😀 old middle old\n", "style": {"bold": False, "fontSize": {"magnitude": 18}}}},
    ]
    before = copy.deepcopy(original)
    requests, plan = proxy.replace_text_requests(proxy.snapshot(original), {
        "slide": {"ref": "slide_a"}, "element": {"ref": "title_a"},
        "replacements": {"old": "new phrase"},
    })
    assert requests == [
        {"deleteText": {"objectId": "title_a", "textRange": {"type": "FIXED_RANGE", "startIndex": 19, "endIndex": 22}}},
        {"insertText": {"objectId": "title_a", "text": "new phrase", "insertionIndex": 19}},
        {"deleteText": {"objectId": "title_a", "textRange": {"type": "FIXED_RANGE", "startIndex": 8, "endIndex": 11}}},
        {"insertText": {"objectId": "title_a", "text": "new phrase", "insertionIndex": 8}},
    ]
    predicted = proxy._predict(original, requests)["slides"][0]["pageElements"][0]["shape"]
    assert shape_text(predicted) == plan[0]["after"] == "Keep 😀 new phrase middle new phrase\n"
    assert predicted["text"]["textElements"][0] == shape["text"]["textElements"][0]
    assert original == before


@pytest.mark.parametrize("replacements,match_case,expected", [
    ({"Hello": ""}, True, " {{NAME}}"),
    ({"hello": "😀", "{{NAME}}": "value"}, False, "😀 value"),
    ({"Hello": "new", "new": "final"}, True, "final {{NAME}}"),
    ({"missing": "unused"}, True, "Hello {{NAME}}"),
])
def test_targeted_prediction_tracks_deletions_case_and_sequential_maps(replacements, match_case, expected):
    from kdcube_ai_app.apps.chat.sdk.integrations.google.slides_structure import shape_text

    original = deck()
    requests, plan = proxy.replace_text_requests(proxy.snapshot(original), {
        "slide": {"ref": "slide_a"}, "element": {"ref": "title_a"},
        "replacements": replacements, "match_case": match_case,
    })
    predicted = proxy._predict(original, requests)
    assert shape_text(predicted["slides"][0]["pageElements"][0]["shape"]) == plan[0]["after"] == expected


def test_add_slide_preview_writes_nothing_and_predicts_fit_without_coordinates(monkeypatch):
    def handler(request):
        assert request.method == "GET"
        return httpx.Response(200, json=editable_deck())
    result = run(monkeypatch, "add_slide", {"presentation_ref": "deck", "layout": "title_and_body", "body": "x" * 1000, "preview": True}, handler)
    assert result["ok"] and result["ret"]["preview"]
    changes = result["ret"]["changes"]
    body_ref = changes["elements"][0]["ref"]
    assert any(r["element"] == body_ref and r["status"] == "overflow_risk" for r in result["ret"]["fit"])
    assert changes["position"] == 2
    def assert_no_coordinates(value):
        if isinstance(value, dict):
            assert not {"geometry", "transform", "scaleX", "scaleY", "translateX", "translateY"} & value.keys()
            for child in value.values():
                assert_no_coordinates(child)
        elif isinstance(value, list):
            for child in value:
                assert_no_coordinates(child)
    assert_no_coordinates(result)
    assert result["ret"]["geometry_diagnostics_truncated"] is False


def test_compose_preview_creates_semantic_content_without_a_provider_write(monkeypatch):
    def handler(request):
        assert request.method == "GET"
        return httpx.Response(200, json=editable_deck())
    result = run(monkeypatch, "compose", {"presentation_ref": "deck", "slide": {"position": 1}, "preview": True, "cells": [{"at": [0, 0], "element": {"text": "A composed caption"}}]}, handler)
    assert result["ok"] and result["ret"]["preview"]
    assert result["ret"]["changes"]["elements"][0]["after"] == "A composed caption\n"
    assert result["ret"]["fit_predicted"]
    assert "translateX" not in json.dumps(result)


@pytest.mark.parametrize("notes_status", [200, 400, 403])
def test_add_slide_reads_new_notes_id_and_chains_revision_without_recreating(monkeypatch, notes_status):
    state, writes, created = editable_deck(), [], []
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=state)
        body = json.loads(request.content)
        writes.append(body)
        if len(writes) == 1:
            assert body["writeControl"] == {"requiredRevisionId": "r1"}
            spec = body["requests"][0]["createSlide"]
            assert {m["layoutPlaceholderObjectId"] for m in spec["placeholderIdMappings"]} == {"layout_title", "layout_body"}
            created.append(spec["objectId"])
            state["revisionId"] = "r2"
            state["slides"].append({"objectId": created[0], "pageElements": [], "slideProperties": {"notesPage": {"notesProperties": {"speakerNotesObjectId": "provider_notes_1"}, "pageElements": [{"objectId": "provider_notes_1", "shape": {"text": {"textElements": [{"textRun": {"content": "\n"}}]}}}]}}})
            return httpx.Response(200, json={"writeControl": {"requiredRevisionId": "r2"}})
        assert len(writes) == 2
        assert body["writeControl"] == {"requiredRevisionId": "r2"}
        assert not any("deleteText" in item for item in body["requests"])
        assert body["requests"][0]["insertText"] == {"objectId": "provider_notes_1", "text": "Speaker notes", "insertionIndex": 0}
        assert all("createSlide" not in r for r in body["requests"])
        if notes_status != 200:
            message = "The required revision ID does not match the latest revision." if notes_status == 400 else "Permission denied"
            return httpx.Response(notes_status, json={"error": {"message": message}})
        state["revisionId"] = "r3"
        return httpx.Response(200, json={"writeControl": {"requiredRevisionId": "r3"}})
    result = run(monkeypatch, "add_slide", {"presentation_ref": "deck", "layout": "title_and_body", "title": "Created", "notes": "Speaker notes"}, handler)
    assert len(writes) == 2 and len(created) == 1
    if notes_status == 200:
        assert result["ok"] and result["ret"]["revision_id"] == "r3"
    else:
        assert not result["ok"] and result["ret"]["partial_result"]
        assert result["ret"]["created_slide_id"] == created[0]


def test_s2a_caller_revision_is_required_on_the_content_batch(monkeypatch):
    writes = []
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=deck())
        body = json.loads(request.content)
        writes.append(body)
        assert body["writeControl"] == {"requiredRevisionId": "r1"}
        assert body["requests"][0]["updateTextStyle"]["textRange"] == {"type": "FIXED_RANGE", "startIndex": 0, "endIndex": 5}
        return httpx.Response(200, json={"writeControl": {"requiredRevisionId": "r2"}})
    result = run(monkeypatch, "style_text", {"presentation_ref": "deck", "revision_id": "r1", "slide": {"ref": "slide_a"}, "element": {"ref": "title_a"}, "quoted_text": "Hello", "style": {"italic": True}}, handler)
    assert result["ok"] and len(writes) == 1


def test_template_copy_compiles_against_copy_ids(monkeypatch):
    writes = []
    def handler(request):
        if request.method == "GET":
            value = deck()
            if request.url.path.endswith("copy_deck"):
                value["presentationId"] = "copy_deck"
                value["revisionId"] = "copy-r1"
                value["slides"][0]["objectId"] = "copy_slide"
            return httpx.Response(200, json=value)
        if request.url.path.endswith("/copy"):
            return httpx.Response(200, json={"id": "copy_deck", "name": "Copy"})
        body = json.loads(request.content)
        writes.append(body)
        assert body["writeControl"]["requiredRevisionId"] == "copy-r1"
        assert body["requests"][0]["replaceAllText"]["pageObjectIds"] == ["copy_slide"]
        return httpx.Response(200, json={"writeControl": {"requiredRevisionId": "copy-r2"}})
    result = run(monkeypatch, "fill_template", {"source": "deck", "title": "Copy", "values": {"NAME": "Ada"}}, handler)
    assert result["ok"] and result["ret"]["presentation_id"] == "copy_deck" and len(writes) == 1


@pytest.mark.parametrize("status,code", [(429,"google_slides_rate_limited"),(503,"google_slides_provider_unavailable"),(401,"google_slides_authorization_failed")])
def test_provider_failures_are_normalized_without_hidden_retry(monkeypatch,status,code):
    calls = []
    def handler(request):
        calls.append(1)
        return httpx.Response(status, json={"error": {"message": "Provider unavailable"}}, headers={"Retry-After": "7"})
    result = run(monkeypatch, "get", {"presentation_ref": "deck"}, handler)
    assert not result["ok"]
    assert result["error"]["code"] == code
    assert result["ret"]["provider_status"] == status and len(calls) == 1
    if status == 429:assert result["ret"]["retry_after"] == "7"


def test_office_files_are_refused_with_import_guidance(monkeypatch):
    result = run(monkeypatch, "get", {"presentation_ref": "deck"}, lambda req:httpx.Response(400,json={"error":{"message":"The document must not be an Office file."}}))
    assert result["error"]["code"] == "slides_not_native"
    assert "import" in result["error"]["message"]


@pytest.mark.parametrize("fmt",["pdf","pptx"])
def test_export_uses_drive_and_keeps_bytes_out_of_model_contract(monkeypatch,fmt):
    def handler(request):
        if request.url.path.endswith("/export"):
            assert request.url.params["mimeType"] == proxy.EXPORT_FORMATS[fmt]
            return httpx.Response(200,content=b"fixture-export",headers={"Content-Type":proxy.EXPORT_FORMATS[fmt]})
        return httpx.Response(200,json=deck())
    result = run(monkeypatch,"export",{"presentation_ref":"deck","format":fmt},handler)
    assert base64.b64decode(result["ret"]["content_base64"]) == b"fixture-export"


def test_thumbnail_download_is_anonymous_and_capability_stays_private(monkeypatch):
    from kdcube_ai_app.apps.chat.sdk.tools.backends.web import ssrf_guard
    class Allowed:
        allowed = True
    async def allow(url):return Allowed()
    monkeypatch.setattr(ssrf_guard,"check_url",allow)
    def handler(request):
        if request.url.host.endswith("googleusercontent.com"):
            assert "authorization" not in request.headers
            return httpx.Response(200,content=b"\x89PNG\r\n\x1a\nfixture",headers={"Content-Type":"image/png"})
        if request.url.path.endswith("/thumbnail"):
            return httpx.Response(200,json={"contentUrl":"https://lh7-us.googleusercontent.com/private-capability", "width":200,"height":113})
        return httpx.Response(200,json=deck())
    result=run(monkeypatch,"thumbnail",{"presentation_ref":"deck","slide":{"position":1}},handler)
    assert result["ok"] and result["ret"]["width"] == 200
    assert "contentUrl" not in json.dumps(result) and "private-capability" not in json.dumps(result)


def test_partial_template_failure_identifies_created_copy(monkeypatch):
    def handler(request):
        if request.url.path.endswith("/copy"):return httpx.Response(200,json={"id":"copy_deck","name":"Copy"})
        if request.method == "POST":return httpx.Response(503,json={"error":{"message":"unavailable"}})
        return httpx.Response(200,json=deck())
    result=run(monkeypatch,"fill_template",{"source":"deck","title":"Copy","values":{"NAME":"Ada"}},handler)
    assert result["ret"]["created_presentation_id"] == "copy_deck"
    assert result["ret"]["partial_result"]


def test_bad_provider_response_is_not_echoed_and_write_is_uncertain(monkeypatch):
    result=run(monkeypatch,"create",{"title":"New"},lambda req:httpx.Response(200,content=b"malformed-provider-body"))
    assert result["error"]["code"] == "google_slides_invalid_response"
    assert result["ret"]["outcome_unknown"] and result["ret"]["partial_result"]
    assert "malformed-provider-body" not in json.dumps(result)


def test_create_posts_only_the_requested_title(monkeypatch):
    def handler(request):
        if request.url.path == "/v1/presentations":
            assert json.loads(request.content) == {"title":"New"}
            return httpx.Response(200,json={"presentationId":"new_deck"})
        return httpx.Response(200,json=deck())
    result=run(monkeypatch,"create",{"title":"New"},handler)
    assert result["ok"] and result["ret"]["presentation_id"] == "new_deck"
