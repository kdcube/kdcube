import base64
import io
import json
import zipfile

import httpx
import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.google.tests.test_slides_proxy import deck, run
from kdcube_ai_app.apps.chat.sdk.integrations.slides.lifecycle import CREATED_PROPERTIES, IMPORT_TYPES, import_bytes
from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError


def source_bytes(kind="pptx"):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        if kind == "pptx":
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("ppt/presentation.xml", "<presentation/>")
        else:
            archive.writestr("mimetype", IMPORT_TYPES["odp"])
    return stream.getvalue()


@pytest.mark.parametrize("kind", ["pptx", "odp"])
def test_deployment_file_import_uses_related_multipart_and_native_target(monkeypatch, kind):
    seen = []
    def handler(request):
        seen.append(request)
        if request.method == "POST":
            assert request.url.host == "www.googleapis.com"
            assert request.url.params["uploadType"] == "multipart"
            assert request.headers["content-type"].startswith("multipart/related; boundary=")
            assert b'application/vnd.google-apps.presentation' in request.content
            assert b'kdcube_slides_created' in request.content
            assert source_bytes(kind) in request.content
            return httpx.Response(200, json={"id": "deck", "name": "Imported", "mimeType": "application/vnd.google-apps.presentation"})
        return httpx.Response(200, json=deck())
    result = run(monkeypatch, "import", {"title": "Imported", "source_format": kind, "content_base64": base64.b64encode(source_bytes(kind)).decode()}, handler)
    assert result["ok"] and result["ret"]["presentation_id"] == "deck"
    assert [r.method for r in seen] == ["POST", "GET"]
    assert "content_base64" not in str(result)


def test_drive_import_copies_with_slides_mime_and_app_marker(monkeypatch):
    seen = []
    def handler(request):
        seen.append(request)
        if request.url.host == "slides.googleapis.com":
            return httpx.Response(200, json=deck())
        if request.method == "GET":
            return httpx.Response(200, json={"id": "source", "name": "Source.pptx", "mimeType": IMPORT_TYPES["pptx"]})
        body = json.loads(request.content)
        assert body["mimeType"] == "application/vnd.google-apps.presentation"
        assert body["appProperties"] == CREATED_PROPERTIES
        assert request.url.path.endswith("/source/copy")
        return httpx.Response(200, json={"id": "deck", "name": "Imported", "mimeType": "application/vnd.google-apps.presentation"})
    result = run(monkeypatch, "import", {"source_file": "https://drive.google.com/file/d/source/view", "title": "Imported"}, handler)
    assert result["ok"]
    assert [r.method for r in seen] == ["GET", "POST", "GET"]


def test_import_preview_validates_source_without_writing(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json={"id": "source", "name": "Neutral.pptx", "mimeType": IMPORT_TYPES["pptx"]})
    result = run(monkeypatch, "import", {"source_file": "source", "preview": True}, handler)
    assert result["ok"] and result["ret"]["preview"]
    assert calls == ["GET"]
    result = run(monkeypatch, "import", {"title": "Neutral", "preview": True, "source_format": "pptx", "content_base64": base64.b64encode(source_bytes()).decode()}, handler)
    assert result["ok"] and calls == ["GET"]


def test_wrong_import_format_is_refused_before_any_provider_call(monkeypatch):
    def handler(request):
        pytest.fail("invalid bytes must not reach Google")
    result = run(monkeypatch, "import", {"title": "Neutral", "source_format": "pptx", "content_base64": base64.b64encode(b"not a presentation").decode()}, handler)
    assert result["error"]["code"] == "slides_import_format_unsupported"
    with pytest.raises(SlidesValidationError):
        import_bytes({"source_format": "odp", "content_base64": base64.b64encode(source_bytes("pptx")).decode()})


@pytest.mark.parametrize("operation", ["trash", "restore"])
def test_unmarked_decks_are_never_trashed_or_restored(monkeypatch, operation):
    calls = []
    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json={"id": "deck", "mimeType": "application/vnd.google-apps.presentation", "isAppAuthorized": True, "appProperties": {}})
    result = run(monkeypatch, operation, {"presentation_ref": "deck"}, handler)
    assert result["error"]["code"] == "slides_app_created_required"
    assert calls == ["GET"]


@pytest.mark.parametrize("operation,was_trashed", [("trash", False), ("restore", True)])
@pytest.mark.parametrize("preview", [False, True])
def test_lifecycle_marks_only_trashed_metadata_and_reports_geometry_when_available(monkeypatch, operation, was_trashed, preview):
    calls = []
    def handler(request):
        calls.append(request.method)
        if request.url.host == "slides.googleapis.com":
            return httpx.Response(200, json=deck())
        if request.method == "PATCH":
            assert json.loads(request.content) == {"trashed": operation == "trash"}
            return httpx.Response(200, json={"id": "deck", "trashed": operation == "trash"})
        return httpx.Response(200, json={"id": "deck", "name": "Neutral", "mimeType": "application/vnd.google-apps.presentation", "appProperties": CREATED_PROPERTIES, "trashed": was_trashed})
    result = run(monkeypatch, operation, {"presentation_ref": "deck", "preview": preview}, handler)
    assert result["ok"] and result["ret"]["trashed"] == (operation == "trash")
    assert calls.count("PATCH") == (0 if preview else 1)
    if not was_trashed or not preview:
        assert result["ret"]["fit"]
    else:
        assert result["ret"]["geometry_unavailable"] == "trashed_presentation"


def test_failed_marker_write_reports_the_created_deck_without_retry(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request.method)
        if request.method == "POST":
            return httpx.Response(200, json={"presentationId": "created"})
        return httpx.Response(403, json={"error": {"code": 403, "message": "Denied"}})
    result = run(monkeypatch, "create", {"title": "Neutral"}, handler)
    assert not result["ok"] and result["ret"]["partial_result"]
    assert result["ret"]["created_presentation_id"] == "created"
    assert calls == ["POST", "PATCH"]
