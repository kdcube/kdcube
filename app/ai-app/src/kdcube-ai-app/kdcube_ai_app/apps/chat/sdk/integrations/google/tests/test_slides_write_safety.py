"""Successful writes keep provider IDs and refuse uncertain post-write failures."""
import copy
import json

import httpx
import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.google.tests.test_slides_s3_proxy import Provider, call


class CreationTransport:
    """Google generates optional IDs; bypass the compiler-oriented predictor."""
    def __init__(self, failure=None):
        self.provider = Provider()
        self.posts = []
        self.failure = failure

    def __call__(self, request):
        if request.method == "POST":
            body = json.loads(request.content)
            self.posts.append(body)
            assert body["writeControl"] == {"requiredRevisionId": "r1"}
            kind, spec = next(iter(body["requests"][0].items()))
            created = spec.get("objectId") if kind == "createSlide" else spec.get("objectIds", {}).get(spec["objectId"])
            created = created or "generated_slide"
            self.provider.state["slides"].append({"objectId": created, "pageElements": []})
            self.provider.state["revisionId"] = "r2"
            response = {"replies": [{kind: {"objectId": created}}], "writeControl": {"requiredRevisionId": "r2"}}
            if self.failure == "malformed_replies":
                response["replies"] = 7
            elif self.failure == "malformed_revision":
                response["writeControl"] = []
            elif self.failure == "missing_revision":
                response.pop("writeControl")
            return httpx.Response(200, json=response)
        if self.posts:
            if self.failure == "unexpected_readback":
                raise RuntimeError("fixture private diagnostic must not escape")
            if self.failure == "invalid_readback":
                return httpx.Response(200, json={})
        return self.provider(request)


GENERATED = [{"createSlide": {}}, {"duplicateObject": {"objectId": "page_fixture"}}]


@pytest.mark.parametrize("request", GENERATED)
def test_google_generated_creation_ids_succeed_through_public_adapter(monkeypatch, request):
    transport = CreationTransport()
    original = copy.deepcopy(request)
    result, _ = call(monkeypatch, "batch_update", {"requiredRevisionId": "r1", "requests": [request]}, transport)
    assert result["ok"], result
    assert len(transport.posts) == 1
    assert transport.posts[0]["requests"] == [original]
    assert result["ret"]["replies"] == [{next(iter(request)): {"objectId": "generated_slide"}}]
    assert result["ret"]["structure"]["slides"][-1]["objectId"] == "generated_slide"
    assert result["ret"]["observed_revision_id"] == "r2"
    assert request == original


@pytest.mark.parametrize("failure", ["malformed_replies", "malformed_revision", "missing_revision", "unexpected_readback", "invalid_readback"])
def test_any_post_write_failure_is_managed_and_never_retries(monkeypatch, failure):
    transport = CreationTransport(failure)
    result, _ = call(monkeypatch, "batch_update", {"requiredRevisionId": "r1", "requests": [{"createSlide": {"objectId": "client_slide"}}]}, transport)
    assert not result["ok"] and result["error"]["managed"], result
    assert result["ret"]["outcome_unknown"] is True
    assert result["ret"]["partial_result"] is True
    assert "Read the presentation" in result["ret"]["next_step"]
    assert "fixture private diagnostic" not in str(result)
    assert len(transport.posts) == 1
    assert transport.provider.state["slides"][-1]["objectId"] == "client_slide"


@pytest.mark.parametrize("request", GENERATED)
def test_uncertain_readback_retains_the_generated_id_from_the_reply(monkeypatch, request):
    transport = CreationTransport("unexpected_readback")
    result, _ = call(monkeypatch, "batch_update", {"requiredRevisionId": "r1", "requests": [request]}, transport)
    assert not result["ok"] and result["error"]["managed"], result
    assert result["ret"]["outcome_unknown"] is True
    assert result["ret"]["created_slide_id"] == "generated_slide"
    assert len(transport.posts) == 1
