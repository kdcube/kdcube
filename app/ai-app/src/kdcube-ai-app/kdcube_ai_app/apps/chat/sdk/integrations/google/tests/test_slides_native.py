"""Raw typed-door boundaries exercised through the public adapter."""
import copy

import httpx
import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.google.tests.test_slides_s3_proxy import Provider, call


@pytest.mark.parametrize("payload,code", [
    ({"requests": [{"insertText": {"objectId": "shape_fixture", "text": "X"}}]}, "slides_revision_required"),
    ({"requiredRevisionId": " ", "requests": []}, "slides_revision_required"),
    ({"requiredRevisionId": "r1", "requests": []}, "slides_batch_invalid"),
    ({"requiredRevisionId": "r1", "requests": [{}] * 51}, "slides_batch_invalid"),
    ({"requiredRevisionId": "r1", "requests": [{"insertText": {}, "deleteText": {}}]}, "slides_batch_invalid"),
    ({"requiredRevisionId": "r1", "requests": [{"insertText": []}]}, "slides_batch_invalid"),
    ({"requiredRevisionId": "r1", "requests": [{"deletePresentation": {}}]}, "slides_batch_request_forbidden"),
    ({"requiredRevisionId": "r1", "requests": [{"insertComment": {"content": "Bypass"}}]}, "slides_batch_request_forbidden"),
    ({"requiredRevisionId": "r1", "requests": [{"deleteComment": {"commentId": "thread"}}]}, "slides_batch_request_forbidden"),
    ({"requiredRevisionId": "r1", "requests": [{"createTable": {"rows": 50, "columns": 50}}]}, "slides_batch_invalid"),
    ({"requiredRevisionId": "r1", "requests": [{"createTable": {"rows": True, "columns": 2}}]}, "slides_batch_invalid"),
    ({"requiredRevisionId": "r1", "requests": [{"insertTableRows": {"number": 51}}]}, "slides_batch_invalid"),
    ({"requiredRevisionId": "r1", "requests": [{"insertText": {"text": "x" * 100001}}]}, "slides_batch_too_large"),
    ({"requiredRevisionId": "r1", "requests": [{"replaceAllText": {"replaceText": "x" * 100001}}]}, "slides_batch_too_large"),
    ({"requiredRevisionId": "r1", "requests": [{"updateShapeProperties": {"unexpected": "x" * 1000001}}]}, "slides_batch_too_large"),
])
def test_invalid_raw_batches_are_refused_before_any_google_call(monkeypatch, payload, code):
    result, provider = call(monkeypatch, "batch_update", payload)
    assert result["error"]["code"] == code, result
    assert not provider.calls and not provider.writes


def test_overly_nested_raw_json_refuses_before_google(monkeypatch):
    body = {}
    for _ in range(32):
        body = {"nested": body}
    result, provider = call(monkeypatch, "batch_update", {"requiredRevisionId": "r1", "requests": [{"updateShapeProperties": body}]})
    assert result["error"]["code"] == "slides_batch_invalid"
    assert not provider.calls


@pytest.mark.parametrize("identity", ["page_fixture", "deck_fixture", "unseen_element"])
def test_delete_object_cannot_delete_a_slide_or_unknown_object(monkeypatch, identity):
    result, provider = call(monkeypatch, "batch_update", {"requiredRevisionId": "r1", "requests": [{"insertText": {"objectId": "shape_fixture", "text": "X"}}, {"deleteObject": {"objectId": identity}}]})
    assert result["error"]["code"] == "slides_batch_delete_target_forbidden"
    assert len(provider.calls) == 1 and not provider.writes


def test_stale_required_revision_refuses_before_post(monkeypatch):
    result, provider = call(monkeypatch, "batch_update", {"requiredRevisionId": "old", "requests": [{"deleteObject": {"objectId": "shape_fixture"}}]})
    assert result["error"]["code"] == "slides_revision_changed"
    assert len(provider.calls) == 1 and not provider.writes


@pytest.mark.parametrize("preview", [False, True])
def test_element_delete_is_one_guarded_batch_and_preview_writes_nothing(monkeypatch, preview):
    result, provider = call(monkeypatch, "batch_update", {"requiredRevisionId": "r1", "requests": [{"deleteObject": {"objectId": "shape_fixture"}}], "preview": preview})
    assert result["ok"], result
    if preview:
        assert result["ret"]["preview"] and not provider.writes
    else:
        assert len(provider.writes) == 1
        assert provider.writes[0][1]["writeControl"] == {"requiredRevisionId": "r1"}
        assert result["ret"]["revision_id"] == "r2"
        assert result["ret"]["observed_revision_id"] == "r2"
        assert all(e["objectId"] != "shape_fixture" for e in result["ret"]["structure"]["slides"][0]["pageElements"])


def test_structure_keeps_ids_converts_geometry_and_removes_capability_urls(monkeypatch):
    provider = Provider()
    provider.state["slides"][0]["pageElements"][0]["image"] = {"contentUrl": "https://provider.invalid/private-capability", "sourceUrl": "https://provider.invalid/source-capability"}
    before = copy.deepcopy(provider.state)
    result, _ = call(monkeypatch, "get_structure", {}, provider)
    assert result["ok"] and result["ret"]["geometry_unit"] == "EMU"
    raw = result["ret"]["structure"]
    assert raw["presentationId"] == "deck_fixture"
    assert raw["pageSize"]["width"] == {"magnitude": 720 * 12700, "unit": "EMU"}
    element = raw["slides"][0]["pageElements"][0]
    assert element["objectId"] == "shape_fixture"
    assert element["transform"]["translateX"] == 30 * 12700
    assert element["size"]["height"]["magnitude"] == 50 * 12700
    assert "comments" not in raw and "contentUrl" not in str(raw) and "sourceUrl" not in str(raw)
    assert provider.state == before
    assert "contentUrl" not in provider.calls[0].url.params["fields"]


def test_raw_write_timeout_is_uncertain_and_never_retried(monkeypatch):
    provider = Provider()
    writes = []
    def timeout(request):
        if request.method == "POST":
            writes.append(request)
            raise httpx.ReadTimeout("fixture timeout", request=request)
        return provider(request)
    result, _ = call(monkeypatch, "batch_update", {"requiredRevisionId": "r1", "requests": [{"deleteObject": {"objectId": "shape_fixture"}}]}, timeout)
    assert result["ret"]["outcome_unknown"]
    assert len(writes) == 1


def test_revision_race_is_not_retried_even_without_named_revision_id(monkeypatch):
    provider = Provider()
    writes = []
    def raced(request):
        if request.method == "POST":
            writes.append(request)
            return httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT", "message": "Required revision does not match current revision."}})
        return provider(request)
    result, _ = call(monkeypatch, "batch_update", {"requiredRevisionId": "r1", "requests": [{"deleteObject": {"objectId": "shape_fixture"}}]}, raced)
    assert result["error"]["code"] == "slides_revision_changed"
    assert len(writes) == 1
