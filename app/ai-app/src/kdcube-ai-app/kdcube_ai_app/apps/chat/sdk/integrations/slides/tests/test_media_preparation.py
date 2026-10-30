import asyncio
import base64
import contextlib
import io
import json
import zipfile

import httpx
import pytest
from PIL import Image

from kdcube_ai_app.apps.chat.sdk.integrations.file_staging import load_staged, new_staged_ref, save_staged
from kdcube_ai_app.apps.chat.sdk.integrations.google import slides_proxy
from kdcube_ai_app.apps.chat.sdk.integrations.slides.named_service import make_slides_named_service_provider
from kdcube_ai_app.apps.chat.sdk.integrations.slides.import_files import import_file_payload
from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError
from kdcube_ai_app.apps.chat.sdk.integrations.slides.tests.test_composition import deck
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import NamedServiceContext, NamedServiceRequest


OWNER = NamedServiceContext(user_id="user", tenant="tenant", project="project")


def staged_png(root, size):
    stream = io.BytesIO()
    Image.new("RGB", size, (12, 34, 56)).save(stream, format="PNG")
    ref = new_staged_ref("source.png")
    save_staged(root, ref, stream.getvalue(), owner=OWNER)
    return ref


def action_payload(action, source):
    if action == "add_image":
        return {"slide": {"position": 1}, "file_ref": source}
    if action == "compose":
        return {"slide": {"position": 1}, "cells": [{"at": [0, 0], "element": {"image": {"file_ref": source}}}]}
    return {"like": {"position": 1}, "replacements": {"images": [{"element": {"kind": "image", "index": 1}, "file_ref": source}]}}


@pytest.mark.parametrize("action", ["add_image", "compose", "duplicate_slide_like"])
def test_new_image_actions_preserve_source_and_clean_owned_copies_after_failure(tmp_path, action):
    source = staged_png(tmp_path, (4, 2))
    owned = []
    def mint(ctx, info):
        owned.append(info["staged_ref"])
        assert load_staged(tmp_path, info["staged_ref"], owner=OWNER)[1] == load_staged(tmp_path, source, owner=OWNER)[1]
        return {"url": "https://fetch.example/private-image-capability"}
    async def execute(**kwargs):
        assert owned
        return {"ok": False, "error": {"code": "google_slides_rate_limited", "message": "Rate limited"}, "ret": {"retryable": True}}
    provider = make_slides_named_service_provider(execute_operation=execute, staging_root_factory=lambda: tmp_path, provider_fetch_url_factory=mint)
    response = asyncio.run(provider.dispatch(OWNER, NamedServiceRequest(operation="object.action", namespace="slides", action=action, object_ref="slides:google:account:presentation:deck1", payload=action_payload(action, source))))
    assert not response.ok and "private-image-capability" not in json.dumps(response.to_dict())
    assert load_staged(tmp_path, source, owner=OWNER)[1]
    with pytest.raises((FileNotFoundError, ValueError)):
        load_staged(tmp_path, owned[0], owner=OWNER)


def test_multi_image_file_preview_preserves_each_aspect_without_fetch_or_mint(tmp_path, monkeypatch):
    sources = [staged_png(tmp_path, (4, 2)), staged_png(tmp_path, (2, 4))]
    real = httpx.AsyncClient
    def handler(request):
        assert request.method == "GET" and request.url.host == "slides.googleapis.com"
        return httpx.Response(200, json=deck())
    monkeypatch.setattr(slides_proxy.httpx, "AsyncClient", lambda *args, **kwargs: real(*args, **{**kwargs, "transport": httpx.MockTransport(handler)}))
    def mint(*args):
        pytest.fail("preview must not mint a public image capability")
    async def execute(**kwargs):
        images = [cell["element"]["image"] for cell in kwargs["payload"]["cells"]]
        assert images[0]["image_uri"] != images[1]["image_uri"]
        assert [i["_preview_image_size"] for i in images] == [{"width": 4, "height": 2}, {"width": 2, "height": 4}]
        return await slides_proxy.execute_google_slides_operation(operation=kwargs["operation"], access_token="fixture", payload=kwargs["payload"])
    provider = make_slides_named_service_provider(execute_operation=execute, staging_root_factory=lambda: tmp_path, provider_fetch_url_factory=mint)
    payload = {"preview": True, "slide": {"position": 1}, "cols": [1, 1], "cells": [{"at": [0, index], "element": {"image": {"file_ref": source}}} for index, source in enumerate(sources)]}
    response = asyncio.run(provider.dispatch(OWNER, NamedServiceRequest(operation="object.action", namespace="slides", action="compose", object_ref="slides:google:account:presentation:deck1", payload=payload)))
    assert response.ok
    assert "preview.invalid" not in json.dumps(response.to_dict())
    assert len(list(tmp_path.iterdir())) == 2  # only the caller's original sources


def test_invalid_file_image_is_refused_before_credentials_or_google(tmp_path):
    source = new_staged_ref("not-an-image.bin")
    save_staged(tmp_path, source, b"not an image", owner=OWNER)
    async def execute(**kwargs):
        pytest.fail("unsupported file bytes must not reach provider execution")
    provider = make_slides_named_service_provider(execute_operation=execute, staging_root_factory=lambda: tmp_path, provider_fetch_url_factory=lambda *args: pytest.fail("must not mint"))
    response = asyncio.run(provider.dispatch(OWNER, NamedServiceRequest(operation="object.action", namespace="slides", action="add_image", object_ref="slides:google:account:presentation:deck1", payload=action_payload("add_image", source))))
    assert not response.ok and response.error.code == "slides_image_unexpected_type"


def test_oversized_staged_image_is_unavailable_before_provider_execution(tmp_path, monkeypatch):
    from kdcube_ai_app.apps.chat.sdk.integrations import file_staging
    source = staged_png(tmp_path, (4, 2))
    monkeypatch.setattr(file_staging, "MAX_STAGED_FILE_BYTES", 1)
    async def execute(**kwargs):
        pytest.fail("an oversized file must not reach provider execution")
    provider = make_slides_named_service_provider(execute_operation=execute, staging_root_factory=lambda: tmp_path, provider_fetch_url_factory=lambda *args: pytest.fail("must not mint"))
    response = asyncio.run(provider.dispatch(OWNER, NamedServiceRequest(operation="object.action", namespace="slides", action="add_image", object_ref="slides:google:account:presentation:deck1", payload=action_payload("add_image", source))))
    assert not response.ok and response.error.code == "slides_image_file_missing"


def test_conversation_image_honors_shared_staging_bound_before_provider_execution(tmp_path, monkeypatch):
    from kdcube_ai_app.apps.chat.sdk.integrations import file_staging, inline_files
    stream = io.BytesIO()
    Image.new("RGB", (4, 2)).save(stream, format="PNG")
    path = tmp_path / "conversation.png"
    path.write_bytes(stream.getvalue())
    source = "conv:fi:conversation-image"
    ctx = NamedServiceContext(user_id="user", tenant="tenant", project="project", conversation_id="conversation")
    @contextlib.asynccontextmanager
    async def materialize(ref, **kwargs):
        assert ref == source
        assert kwargs == {"user_id": "user", "tenant": "tenant", "project": "project", "conversation_id": "conversation"}
        yield {"path": str(path)}
    monkeypatch.setattr(inline_files, "materialized_conversation_file_ref", materialize)
    monkeypatch.setattr(file_staging, "MAX_STAGED_FILE_BYTES", 1)
    async def execute(**kwargs):
        pytest.fail("an oversized conversation file must not reach provider execution")
    provider = make_slides_named_service_provider(execute_operation=execute, staging_root_factory=lambda: tmp_path / "staging", provider_fetch_url_factory=lambda *args: pytest.fail("must not mint"))
    response = asyncio.run(provider.dispatch(ctx, NamedServiceRequest(operation="object.action", namespace="slides", action="add_image", object_ref="slides:google:account:presentation:deck1", payload=action_payload("add_image", source))))
    assert not response.ok and response.error.code == "slides_image_too_large"
    assert not (tmp_path / "staging").exists()


@pytest.mark.parametrize("field", ["user_id", "tenant", "project"])
@pytest.mark.parametrize("action", ["add_image", "compose", "duplicate_slide_like", "replace_shapes_with_image"])
def test_file_image_refuses_another_owner_before_provider_or_mint(tmp_path, field, action):
    source = staged_png(tmp_path, (4, 2))
    identity = {"user_id": "user", "tenant": "tenant", "project": "project"}
    identity[field] = "other"
    async def execute(**kwargs):
        pytest.fail("an unavailable source must not reach provider execution")
    def mint(*args):
        pytest.fail("an unavailable source must not mint a capability")
    provider = make_slides_named_service_provider(execute_operation=execute, staging_root_factory=lambda: tmp_path, provider_fetch_url_factory=mint)
    payload = {"tag": "{{IMAGE}}", "file_ref": source} if action == "replace_shapes_with_image" else action_payload(action, source)
    response = asyncio.run(provider.dispatch(NamedServiceContext(**identity), NamedServiceRequest(operation="object.action", namespace="slides", action=action, object_ref="slides:google:account:presentation:deck1", payload=payload)))
    assert not response.ok and response.error.code == "slides_image_file_missing"
    assert load_staged(tmp_path, source, owner=OWNER)[1]
    assert len(list(tmp_path.iterdir())) == 1


def test_staged_import_reads_only_the_callers_source(tmp_path):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("ppt/presentation.xml", "<presentation/>")
    source = new_staged_ref("source.pptx")
    save_staged(tmp_path, source, stream.getvalue(), owner=OWNER)
    result = asyncio.run(import_file_payload(OWNER, {"file_ref": source}, lambda: tmp_path))
    assert result["source_format"] == "pptx" and base64.b64decode(result["content_base64"]) == stream.getvalue()
    assert load_staged(tmp_path, source, owner=OWNER)[1] == stream.getvalue()
    for field in ("user_id", "tenant", "project"):
        identity = {"user_id": "user", "tenant": "tenant", "project": "project"}
        identity[field] = "other"
        with pytest.raises(SlidesValidationError) as exc:
            asyncio.run(import_file_payload(NamedServiceContext(**identity), {"file_ref": source}, lambda: tmp_path))
        assert exc.value.code == "slides_import_file_missing"
