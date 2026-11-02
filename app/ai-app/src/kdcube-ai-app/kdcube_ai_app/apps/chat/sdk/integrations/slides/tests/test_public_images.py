"""Every owned-file image route diagnoses the public origin before execution."""
import asyncio
import json

import httpx
import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.file_staging import load_staged
from kdcube_ai_app.apps.chat.sdk.integrations.slides.named_service import make_slides_named_service_provider
from kdcube_ai_app.apps.chat.sdk.integrations.slides.tests.test_media_preparation import OWNER, action_payload, staged_png
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import NamedServiceRequest


def payload_for(action, source):
    if action == "fill_template":
        return {"source": "deck", "title": "Filled", "images": {"{{IMAGE}}": {"file_ref": source}}}
    if action == "replace_shapes_with_image":
        return {"tag": "{{IMAGE}}", "file_ref": source}
    return action_payload(action, source)


@pytest.mark.parametrize("action", ["add_image", "compose", "fill_template", "replace_shapes_with_image", "duplicate_slide_like"])
@pytest.mark.parametrize("answer", ["warning", "unreachable", "image"])
def test_owned_image_origin_is_checked_before_provider_write(tmp_path, monkeypatch, action, answer):
    source = staged_png(tmp_path, (4, 2))
    image = load_staged(tmp_path, source, owner=OWNER)[1]
    owned, fetches, writes = [], [], []
    real_client = httpx.AsyncClient

    def origin(request):
        fetches.append(request)
        assert "authorization" not in request.headers and "cookie" not in request.headers
        if answer == "unreachable":
            raise httpx.ConnectError("cannot reach " + str(request.url), request=request)
        if not request.headers["user-agent"].startswith("Mozilla/"):
            return httpx.Response(403, headers={"content-type": "application/json"})
        return httpx.Response(200, headers={"content-type": "image/png" if answer == "image" else "text/html"}, content=image if answer == "image" else b"tunnel warning")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(origin), **kw))

    def mint(ctx, info):
        owned.append(info["staged_ref"])
        return {"url": "https://origin.example/file?capability=private-test-capability"}

    async def execute(**kwargs):
        writes.append(kwargs)
        assert answer == "image", "origin refusal must happen before provider execution"
        return {"ok": True, "ret": {"presentation_id": "deck", "account_id": "account"}}

    provider = make_slides_named_service_provider(execute_operation=execute, staging_root_factory=lambda: tmp_path, provider_fetch_url_factory=mint)
    response = asyncio.run(provider.dispatch(OWNER, NamedServiceRequest(operation="object.action", namespace="slides", action=action, object_ref="slides:google:account:presentation:deck", payload=payload_for(action, source))))
    assert len(fetches) == 1
    assert fetches[0].headers["user-agent"].startswith("Mozilla/")
    assert "private-test-capability" not in json.dumps(response.to_dict())
    if answer == "image":
        assert response.ok and len(writes) == 1
    else:
        assert not response.ok and not writes
        assert response.status == 502
        assert response.error.code == ("deployment_origin_unreachable" if answer == "unreachable" else "deployment_origin_not_public")
        assert response.error.details["where"] == "deployment_config"
        assert response.error.details["purpose"] == f"slides.{action}"
        if answer == "warning":
            assert response.error.details["origin_media_type"] == "text/html"
        else:
            assert response.error.details["error"] == "ConnectError"
    assert load_staged(tmp_path, source, owner=OWNER)[1] == image
    assert owned
    for ref in owned:
        with pytest.raises(FileNotFoundError):
            load_staged(tmp_path, ref, owner=OWNER)
