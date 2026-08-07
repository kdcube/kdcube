# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The linkedin namespace contract: refs, honest capabilities, bounded actions."""

from __future__ import annotations

import base64
from typing import Any

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.linkedin import named_service as ns
from kdcube_ai_app.apps.chat.sdk.protocol import ExternalEventPayload, ExternalEventRouting
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import bind_current_request_context
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.models import (
    ConnectedAccount,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceContext,
    NamedServiceRequest,
)

PNG_BASE64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 32).decode()

ACCOUNT = ConnectedAccount(
    account_id="acc_1",
    provider_id="linkedin",
    connector_app_id="demo",
    external_subject="dE5aOhH-ap",
    display_name="Jane Smith",
    email="jane@example.com",
    claims=("linkedin:post",),
    credential_id="cred_1",
)


@pytest.fixture()
def provider(monkeypatch):
    async def _accounts(**_kwargs):
        return [ACCOUNT]

    monkeypatch.setattr(ns, "connected_linkedin_accounts", _accounts)
    return ns.LinkedInNamedServiceProvider(bundle_id="kdcube-services@1-0")


@pytest.fixture()
def ctx():
    return NamedServiceContext(tenant="t", project="p", user_id="u1")


def _request(operation: str, **kwargs: Any) -> NamedServiceRequest:
    return NamedServiceRequest(operation=operation, namespace=ns.LINKEDIN_NAMESPACE, **kwargs)


def _attrs(response) -> dict[str, Any]:
    return dict((response.ret or {}).get("attrs") or {})


def _extra(response) -> dict[str, Any]:
    return dict((response.ret or {}).get("extra") or {})


def test_post_ref_round_trips_through_the_urn_colons():
    ref = ns.post_ref("acc_1", "urn:li:share:7123456789")
    assert ref == "linkedin:acc_1:post:urn:li:share:7123456789"
    assert ns.parse_linkedin_ref(ref) == {
        "account_id": "acc_1",
        "kind": "post",
        "post_urn": "urn:li:share:7123456789",
    }


def test_account_ref_round_trips():
    assert ns.parse_linkedin_ref(ns.account_ref("acc_1")) == {
        "account_id": "acc_1",
        "kind": "account",
    }


def test_foreign_refs_are_rejected():
    assert ns.parse_linkedin_ref("slack:acc:channel:C1") == {}
    assert ns.parse_linkedin_ref("") == {}


@pytest.mark.asyncio
async def test_capabilities_do_not_advertise_search(provider, ctx):
    response = await provider.provider_capabilities(ctx, _request("provider.capabilities"))
    capabilities = _attrs(response)["capabilities"]
    assert capabilities["search"] is False
    assert capabilities["list"] is True and capabilities["get"] is True
    assert sorted(capabilities["actions"]) == [
        "add_comment",
        "discard_upload",
        "publish_image_post",
        "publish_post",
        "request_upload",
    ]
    assert "object.search" in capabilities["not_supported"]


@pytest.mark.asyncio
async def test_read_operations_declare_no_provider_claim(provider, ctx):
    response = await provider.provider_capabilities(ctx, _request("provider.capabilities"))
    hints = _attrs(response)["capabilities"]["grant_hints"]
    assert hints["object.list"] == []
    assert hints["object.get"] == []
    assert hints["object.action.publish_post"] == ["linkedin:post"]
    assert hints["object.action.add_comment"] == ["linkedin:post"]


@pytest.mark.asyncio
async def test_object_list_returns_accounts_with_author_urns(provider, ctx):
    response = await provider.object_list(ctx, _request("object.list"))
    items = (response.ret or {})["items"]
    assert [item["object_ref"] for item in items] == ["linkedin:acc_1"]
    assert items[0]["author_urn"] == "urn:li:person:dE5aOhH-ap"
    assert items[0]["object_kind"] == ns.LINKEDIN_ACCOUNT_KIND


@pytest.mark.asyncio
async def test_object_get_on_a_post_ref_reports_content_as_unavailable(provider, ctx):
    response = await provider.object_get(
        ctx, _request("object.get", object_ref="linkedin:acc_1:post:urn:li:share:7123")
    )
    obj = (response.ret or {})["object"]
    assert obj["permalink"] == "https://www.linkedin.com/feed/update/urn:li:share:7123"
    assert obj["content_available"] is False


@pytest.mark.asyncio
async def test_object_get_rejects_a_malformed_ref(provider, ctx):
    response = await provider.object_get(ctx, _request("object.get", object_ref="linkedin"))
    assert response.ok is False
    assert response.error.code == "linkedin_invalid_ref"


@pytest.mark.asyncio
async def test_object_get_reports_an_unknown_account(provider, ctx):
    response = await provider.object_get(ctx, _request("object.get", object_ref="linkedin:nope"))
    assert response.ok is False
    assert response.error.code == "linkedin_account_not_found"


@pytest.mark.asyncio
async def test_object_get_on_a_post_ref_checks_the_account_too(provider, ctx):
    """A post ref under an unconnected account must not answer with a permalink.

    The permalink is derived from the ref, so without this check any invented
    account id returns ok and an agent could report a post that never existed.
    """
    response = await provider.object_get(
        ctx, _request("object.get", object_ref="linkedin:nope:post:urn:li:share:7123")
    )
    assert response.ok is False
    assert response.error.code == "linkedin_account_not_found"


@pytest.mark.asyncio
async def test_object_get_marks_a_post_urn_as_unverified(provider, ctx):
    """The account is checked; the urn cannot be — LinkedIn exposes no read."""
    response = await provider.object_get(
        ctx, _request("object.get", object_ref="linkedin:acc_1:post:urn:li:share:7123")
    )
    assert (response.ret or {})["object"]["urn_verified"] is False


@pytest.mark.asyncio
async def test_publish_image_post_returns_a_ref_that_feeds_add_comment(provider, ctx, monkeypatch):
    captured: dict[str, Any] = {}

    async def _publish(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "error": None,
            "ret": {
                "post_urn": "urn:li:share:7123456789",
                "account_id": "acc_1",
                "author": "urn:li:person:dE5aOhH-ap",
                "image_count": len(kwargs.get("files") or []),
            },
        }

    monkeypatch.setattr(provider._linkedin, "publish", _publish)
    # Inline files need a bound request identity: the workspace helper mints a
    # disposable outdir + turn id from it on turn-less transports.
    request_payload = ExternalEventPayload(
        routing=ExternalEventRouting(bundle_id="kdcube-services@1-0", session_id="sess-1")
    )
    with bind_current_request_context(request_payload):
        response = await provider.object_action(
            ctx,
            _request(
                "object.action",
                action=ns.ACTION_PUBLISH_IMAGE_POST,
                payload={
                    "text": "hello",
                    "alt_texts": ["chart"],
                    "files": [
                        {"filename": "c.png", "content_base64": PNG_BASE64, "mime": "image/png"}
                    ],
                },
            ),
        )
    assert response.ok is True
    ref = _attrs(response)["object_ref"]
    assert ref == "linkedin:acc_1:post:urn:li:share:7123456789"
    assert ns.parse_linkedin_ref(ref)["kind"] == "post"
    assert [item["filename"] for item in captured["files"]] == ["c.png"]
    # The bytes must arrive decoded: resolve_payload_file_entries leaves an
    # inline entry as content_base64, so a missing materialize step would send
    # an empty image and LinkedIn would accept it.
    assert captured["files"][0]["data"] == base64.b64decode(PNG_BASE64)
    assert captured["files"][0]["mime_type"] == "image/png"


@pytest.mark.asyncio
async def test_add_comment_accepts_the_post_ref(provider, ctx, monkeypatch):
    captured: dict[str, Any] = {}

    async def _comment(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "error": None, "ret": {"comment_id": "99", "account_id": "acc_1"}}

    monkeypatch.setattr(provider._linkedin, "comment_on_linkedin_post", _comment)
    response = await provider.object_action(
        ctx,
        _request(
            "object.action",
            action=ns.ACTION_ADD_COMMENT,
            object_ref="linkedin:acc_1:post:urn:li:share:7123456789",
            payload={"text": "nice"},
        ),
    )
    assert response.ok is True
    assert captured["post_urn"] == "urn:li:share:7123456789"
    assert captured["account_id"] == "acc_1"


@pytest.mark.asyncio
async def test_add_comment_without_a_target_is_rejected(provider, ctx):
    response = await provider.object_action(
        ctx, _request("object.action", action=ns.ACTION_ADD_COMMENT, payload={"text": "x"})
    )
    assert response.ok is False
    assert response.error.code == "linkedin_post_ref_required"


@pytest.mark.asyncio
async def test_unknown_actions_fail_closed(provider, ctx):
    response = await provider.object_action(
        ctx, _request("object.action", action="delete_account")
    )
    assert response.ok is False
    assert response.error.code == "linkedin_unknown_action"


@pytest.mark.asyncio
async def test_schema_states_what_is_not_supported(provider, ctx):
    response = await provider.object_schema(ctx, _request("object.schema"))
    schema = _extra(response)["schema"]
    assert set(schema["refs"]) == {"account", "post"}
    assert "r_member_social" in schema["not_supported"]["post_content_read"]
    assert schema["limits"]["images_per_post"] == 20


@pytest.mark.asyncio
async def test_publish_failure_is_translated_not_raised(provider, ctx, monkeypatch):
    async def _publish(**_kwargs):
        return {
            "ok": False,
            "error": {"code": "linkedin_access_denied", "message": "Not enough permissions."},
            "ret": {"provider_status": 403},
        }

    monkeypatch.setattr(provider._linkedin, "publish", _publish)
    response = await provider.object_action(
        ctx,
        _request("object.action", action=ns.ACTION_PUBLISH_POST, payload={"text": "hi"}),
    )
    assert response.ok is False
    assert response.error.code == "linkedin_access_denied"
    assert response.error.message == "Not enough permissions."


@pytest.mark.asyncio
async def test_comment_failure_is_translated_not_raised(provider, ctx, monkeypatch):
    async def _comment(**_kwargs):
        return {"ok": False, "error": {"code": "linkedin_not_found"}, "ret": {}}

    monkeypatch.setattr(provider._linkedin, "comment_on_linkedin_post", _comment)
    response = await provider.object_action(
        ctx,
        _request(
            "object.action",
            action=ns.ACTION_ADD_COMMENT,
            object_ref="linkedin:acc_1:post:urn:li:share:7",
            payload={"text": "x"},
        ),
    )
    assert response.ok is False
    assert response.error.code == "linkedin_not_found"
    # No message on the envelope -> the fallback carries it.
    assert response.error.message == "LinkedIn comment could not be added."


@pytest.mark.asyncio
async def test_consent_envelope_from_the_tool_becomes_a_403(provider, ctx, monkeypatch):
    async def _publish(**_kwargs):
        return {
            "ok": False,
            "error": {
                "code": "needs_connected_account_consent",
                "message": "Connect LinkedIn.",
                "consent": {"provider_id": "linkedin", "reason": "connect_required"},
            },
            "ret": {},
        }

    monkeypatch.setattr(provider._linkedin, "publish", _publish)
    response = await provider.object_action(
        ctx,
        _request("object.action", action=ns.ACTION_PUBLISH_POST, payload={"text": "hi"}),
    )
    assert response.ok is False
    assert response.status == 403
    assert response.error.code == "needs_connected_account_consent"


@pytest.mark.asyncio
async def test_request_upload_returns_a_signed_slot(ctx, monkeypatch):
    async def _accounts(**_kwargs):
        return [ACCOUNT]

    monkeypatch.setattr(ns, "connected_linkedin_accounts", _accounts)

    async def _slot(_ctx, info):
        return {"upload_url": "https://host/upload?token=t",
                "staged_ref": f"staged:abc/{info['filename']}",
                "expires_at": 123}

    provider = ns.LinkedInNamedServiceProvider(upload_slot_factory=_slot)
    response = await provider.object_action(
        ctx,
        _request("object.action", action=ns.ACTION_REQUEST_UPLOAD,
                 payload={"filename": "chart.png", "mime": "image/png"}),
    )
    assert response.ok is True
    extra = _extra(response)
    assert extra["upload_url"] == "https://host/upload?token=t"
    assert extra["staged_ref"] == "staged:abc/chart.png"
    # The agent must learn the byte path without guessing it.
    assert "upload_url" in extra["how"] and "staged_ref" in extra["how"]


@pytest.mark.asyncio
async def test_request_upload_needs_a_filename(provider, ctx):
    response = await provider.object_action(
        ctx, _request("object.action", action=ns.ACTION_REQUEST_UPLOAD, payload={})
    )
    assert response.ok is False
    assert response.error.code == "filename_required"


@pytest.mark.asyncio
async def test_request_upload_reports_a_deployment_without_staging(provider, ctx):
    # provider fixture has no upload_slot_factory.
    response = await provider.object_action(
        ctx,
        _request("object.action", action=ns.ACTION_REQUEST_UPLOAD,
                 payload={"filename": "chart.png"}),
    )
    assert response.ok is False
    assert response.error.code == "upload_not_configured"
    assert response.status == 503


@pytest.mark.asyncio
async def test_discard_upload_needs_a_staged_ref(provider, ctx):
    response = await provider.object_action(
        ctx, _request("object.action", action=ns.ACTION_DISCARD_UPLOAD, payload={})
    )
    assert response.ok is False
    assert response.error.code == "staged_ref_required"


@pytest.mark.asyncio
async def test_staged_bytes_reach_publish_and_are_then_released(provider, ctx, monkeypatch, tmp_path):
    from kdcube_ai_app.apps.chat.sdk.integrations import file_staging

    root = tmp_path / "staging"
    root.mkdir()
    staged_ref = file_staging.new_staged_ref("chart.png")
    file_staging.save_staged(root, staged_ref, b"\x89PNG-staged-bytes")

    monkeypatch.setattr(provider, "_staging_root", lambda: root)
    captured: dict[str, Any] = {}

    async def _publish(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "error": None,
                "ret": {"post_urn": "urn:li:share:7", "account_id": "acc_1"}}

    monkeypatch.setattr(provider._linkedin, "publish", _publish)
    request_payload = ExternalEventPayload(
        routing=ExternalEventRouting(bundle_id="kdcube-services@1-0", session_id="sess-1")
    )
    with bind_current_request_context(request_payload):
        response = await provider.object_action(
            ctx,
            _request("object.action", action=ns.ACTION_PUBLISH_IMAGE_POST,
                     payload={"text": "hi", "files": [{"staged_ref": staged_ref}]}),
        )
    assert response.ok is True
    assert captured["files"][0]["data"] == b"\x89PNG-staged-bytes"
    # Single-use: the post owns the bytes now.
    with pytest.raises((FileNotFoundError, ValueError)):
        file_staging.load_staged(root, staged_ref)


# --------------------------------------------------------------------------- #
# Text/image action split
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_publish_post_rejects_file_shaped_payload_keys(provider, ctx, monkeypatch):
    """A text post that silently dropped its images would look like a success."""
    called = False

    async def _publish(**_kwargs):
        nonlocal called
        called = True
        return {"ok": True, "error": None, "ret": {}}

    monkeypatch.setattr(provider._linkedin, "publish", _publish)
    response = await provider.object_action(
        ctx,
        _request(
            "object.action",
            action=ns.ACTION_PUBLISH_POST,
            payload={"text": "hi", "files": [{"staged_ref": "staged:1:a.png"}]},
        ),
    )
    assert response.ok is False
    assert response.error.code == "linkedin_post_carries_no_images"
    assert response.error.details["action"] == ns.ACTION_PUBLISH_IMAGE_POST
    assert response.error.details["rejected_keys"] == ["files"]
    assert called is False


@pytest.mark.asyncio
async def test_publish_image_post_requires_files(provider, ctx):
    response = await provider.object_action(
        ctx,
        _request("object.action", action=ns.ACTION_PUBLISH_IMAGE_POST, payload={"text": "hi"}),
    )
    assert response.ok is False
    assert response.error.code == "linkedin_images_required"
    assert response.error.details["action"] == ns.ACTION_PUBLISH_POST


@pytest.mark.asyncio
async def test_publish_image_post_reads_a_workspace_path(provider, ctx, monkeypatch, tmp_path):
    """In-chat lane: the agent passes a path, the service reads the bytes."""
    image = tmp_path / "chart.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"y" * 32)
    captured: dict[str, Any] = {}

    def _load(path: str):
        assert path == "conv:fi:chart.png"
        return {"filename": "chart.png", "mime_type": "image/png", "data": image.read_bytes()}, None

    async def _publish(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "error": None, "ret": {"post_urn": "urn:li:share:9", "account_id": "acc_1"}}

    monkeypatch.setattr(ns, "load_image_artifact", _load)
    monkeypatch.setattr(provider._linkedin, "publish", _publish)
    response = await provider.object_action(
        ctx,
        _request(
            "object.action",
            action=ns.ACTION_PUBLISH_IMAGE_POST,
            payload={"text": "hi", "files": [{"file_path": "conv:fi:chart.png"}]},
        ),
    )
    assert response.ok is True
    assert captured["files"][0]["data"] == image.read_bytes()
    assert captured["files"][0]["mime_type"] == "image/png"


@pytest.mark.asyncio
async def test_publish_image_post_keeps_mixed_lane_order(provider, ctx, monkeypatch):
    """alt_texts are positional, so resolved files must keep the payload order."""
    captured: dict[str, Any] = {}

    def _load(path: str):
        return {"filename": "ws.png", "mime_type": "image/png", "data": b"\x89PNG-workspace"}, None

    async def _publish(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "error": None, "ret": {"post_urn": "urn:li:share:9", "account_id": "acc_1"}}

    monkeypatch.setattr(ns, "load_image_artifact", _load)
    monkeypatch.setattr(provider._linkedin, "publish", _publish)
    request_payload = ExternalEventPayload(
        routing=ExternalEventRouting(bundle_id="kdcube-services@1-0", session_id="sess-1")
    )
    with bind_current_request_context(request_payload):
        response = await provider.object_action(
            ctx,
            _request(
                "object.action",
                action=ns.ACTION_PUBLISH_IMAGE_POST,
                payload={
                    "text": "hi",
                    "alt_texts": ["from workspace", "inline"],
                    "files": [
                        {"file_path": "conv:fi:ws.png"},
                        {"filename": "inline.png", "content_base64": PNG_BASE64, "mime": "image/png"},
                    ],
                },
            ),
        )
    assert response.ok is True
    assert [item["filename"] for item in captured["files"]] == ["ws.png", "inline.png"]


@pytest.mark.asyncio
async def test_workspace_surface_teaches_the_path_form(provider, ctx, monkeypatch):
    monkeypatch.setattr(ns, "linkedin_schema_for_surface", ns.linkedin_schema_for_surface)
    base = ns.LINKEDIN_SCHEMA["actions"][ns.ACTION_PUBLISH_IMAGE_POST]["payload"]["files"]["description"]
    assert "staged_ref" in base and "file_path" not in base
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.integrations.inline_files.has_turn_workspace",
        lambda: True,
    )
    in_chat = ns.linkedin_schema_for_surface()
    files = in_chat["actions"][ns.ACTION_PUBLISH_IMAGE_POST]["payload"]["files"]["description"]
    assert "file_path" in files
    # The base schema must not be mutated by the surface variant.
    assert ns.LINKEDIN_SCHEMA["actions"][ns.ACTION_PUBLISH_IMAGE_POST]["payload"]["files"]["description"] == base


@pytest.mark.asyncio
async def test_request_upload_points_at_the_image_action(provider, ctx):
    """The slot instruction must not send the caller into the text-post guard."""
    provider._upload_slot_factory = lambda _ctx, _spec: {
        "upload_url": "https://host/upload?t=1",
        "staged_ref": "staged:1:a.jpg",
        "expires_at": 0,
    }
    response = await provider.object_action(
        ctx,
        _request(
            "object.action",
            action=ns.ACTION_REQUEST_UPLOAD,
            payload={"filename": "a.jpg", "mime": "image/jpeg"},
        ),
    )
    assert response.ok is True
    how = _extra(response)["how"]
    assert ns.ACTION_PUBLISH_IMAGE_POST in how
    # publish_post rejects files, so naming it here would be a wrong instruction.
    assert f"in {ns.ACTION_PUBLISH_POST} files" not in how
