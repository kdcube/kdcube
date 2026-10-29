import copy
import io
import json

import httpx
import pytest
from PIL import Image

from kdcube_ai_app.apps.chat.sdk.integrations.google import slides_proxy as proxy
from kdcube_ai_app.apps.chat.sdk.integrations.google.tests.test_slides_proxy import run
from kdcube_ai_app.apps.chat.sdk.integrations.slides.tests.test_composition import deck

@pytest.fixture
def png_bytes():
    stream = io.BytesIO()
    Image.new("RGB", (1, 1), (12, 34, 56)).save(stream, format="PNG")
    return stream.getvalue()


def test_compose_preview_resolves_targets_without_writing_or_returning_coordinates(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json=deck())
    result = run(monkeypatch, "compose", {"presentation_ref": "deck1", "slide": {"position": 1}, "preview": True, "cols": [1, 1], "cells": [{"at": [0, 0], "element": {"role": "body"}}, {"at": [0, 1], "element": {"text": "Caption"}}]}, handler)
    assert result["ok"] and calls == ["GET"]
    assert result["ret"]["changes"]["elements"][0]["ref"] == "body1"
    assert result["ret"]["fit_predicted"]
    assert "translateX" not in json.dumps(result)
    assert "off_slide" in result["ret"] and "overlaps" in result["ret"]


def test_public_image_is_checked_anonymously_before_google_write(monkeypatch, png_bytes):
    calls = []
    async def download(client, url, **kwargs):
        assert url == "https://example.test/image.png"
        calls.append("image-read")
        return png_bytes, "image/png"
    monkeypatch.setattr(proxy, "_download", download)
    def handler(request):
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json=deck())
        body = json.loads(request.content)
        assert body["writeControl"] == {"requiredRevisionId": "r1"}
        image = next(r["createImage"] for r in body["requests"] if "createImage" in r)
        assert image["url"] == "https://example.test/image.png"
        assert image["elementProperties"]["size"]["width"] == image["elementProperties"]["size"]["height"]
        return httpx.Response(200, json={"writeControl": {"requiredRevisionId": "r2"}, "replies": [{}]})
    result = run(monkeypatch, "add_image", {"presentation_ref": "deck1", "slide": {"position": 1}, "image_uri": "https://example.test/image.png"}, handler)
    assert result["ok"] and calls == ["GET", "image-read", "POST", "GET"]


def test_unsupported_public_image_is_refused_before_google_batch(monkeypatch, png_bytes):
    calls = []
    async def download(*args, **kwargs):
        return png_bytes, "image/webp"
    monkeypatch.setattr(proxy, "_download", download)
    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json=deck())
    result = run(monkeypatch, "add_image", {"presentation_ref": "deck1", "slide": {"position": 1}, "image_uri": "https://example.test/image.webp"}, handler)
    assert result["error"]["code"] == "slides_image_unexpected_type"
    assert calls == ["GET"]


def test_duplicate_notes_followup_failure_reports_new_slide_and_never_recreates(monkeypatch):
    state = deck()
    posts = []
    copied_id = None
    def handler(request):
        nonlocal copied_id
        if request.method == "GET":
            return httpx.Response(200, json=state)
        body = json.loads(request.content)
        posts.append(body)
        if len(posts) == 1:
            spec = body["requests"][0]["duplicateObject"]
            copied_id = spec["objectIds"]["slide1"]
            copied = copy.deepcopy(state["slides"][0])
            copied["objectId"] = copied_id
            copied["slideProperties"] = {"notesPage": {"notesProperties": {"speakerNotesObjectId": "real_notes"}, "pageElements": [{"objectId": "real_notes", "shape": {"text": {"textElements": [{"textRun": {"content": "Speaker\n"}}]}}}]}}
            state["slides"].append(copied)
            state["revisionId"] = "r2"
            return httpx.Response(200, json={"writeControl": {"requiredRevisionId": "r2"}, "replies": [{}]})
        assert body["writeControl"] == {"requiredRevisionId": "r2"}
        assert body["requests"][0]["deleteText"]["objectId"] == "real_notes"
        return httpx.Response(403, json={"error": {"code": 403, "message": "Denied"}})
    result = run(monkeypatch, "duplicate_slide_like", {"presentation_ref": "deck1", "like": {"position": 1}, "replacements": {"notes": "New notes"}}, handler)
    assert not result["ok"] and result["ret"]["partial_result"]
    assert result["ret"]["created_slide_id"] == copied_id
    assert len(posts) == 2
