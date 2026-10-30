import asyncio
import base64
import copy
import io
import json

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.slides.named_service import make_slides_named_service_provider
from kdcube_ai_app.apps.chat.sdk.integrations.slides.schema import SLIDES_ACTIONS, SLIDES_SCHEMA
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import NamedServiceContext, NamedServiceRequest, NamedServiceStreamResult


def snapshot():
    return {"schema": "kdcube.slides.snapshot.v1", "presentation": {"title":"Deck","revision_id":"r1","slides":1},"layouts":[],"slides":[{"position":1,"ref":"slide_a","title":"Roadmap","kind":"title_and_body","elements":[{"ref":"title_a","kind":"shape","role":"title","text":"Hello","geometry":{"x":1,"y":2}}]}],"range":{"next_slide":None}}


def provider(executor, **kwargs):
    return make_slides_named_service_provider(execute_operation=executor,bundle_id="services",**kwargs)


def request(operation, **kwargs):
    return NamedServiceRequest(operation=operation,namespace="slides",**kwargs)


def test_every_operation_and_action_has_human_and_agent_descriptions():
    for row in SLIDES_SCHEMA["operations"].values():
        assert row["label"] and row["description"] and row["instruction"]
    assert set(SLIDES_SCHEMA["actions"]) == set(SLIDES_ACTIONS)
    for row in SLIDES_SCHEMA["actions"].values():assert row["label"] and row["description"]


@pytest.mark.parametrize("action", ["trash", "restore"])
def test_lifecycle_uses_delete_claim_and_presentation_scope(action):
    calls = []
    async def execute(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "ret": {"account_id": "account", "presentation_id": "deck", "trashed": action == "trash"}}
    response = asyncio.run(provider(execute).dispatch(NamedServiceContext(), request("object.action", action=action, object_ref="slides:google:account:presentation:deck", payload={"preview": True})))
    assert response.ok and calls[0]["claim"] == ["slides:delete"]
    assert calls[0]["account_id"] == "account"
    calls.clear()
    response = asyncio.run(provider(execute).dispatch(NamedServiceContext(), request("object.action", action=action, object_ref="slides:google:account:slide:slide_a:deck")))
    assert not response.ok and not calls


def test_import_accepts_drive_source_without_target_and_preserves_account_binding():
    calls = []
    async def execute(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "ret": {"account_id": "account", "presentation_id": "imported", "title": "Imported"}}
    response = asyncio.run(provider(execute).dispatch(NamedServiceContext(), request("object.action", action="import", payload={"account_id": "account", "source_file": "https://drive.google.com/file/d/source/view"})))
    assert response.ok and calls[0]["operation"] == "import"
    assert calls[0]["account_id"] == "account"
    assert calls[0]["claim"] == ["slides:read", "slides:write"]
    calls.clear()
    response = asyncio.run(provider(execute).dispatch(NamedServiceContext(), request("object.action", action="import", payload={"account_id": "other", "source_file": "slides:google:account:presentation:source"})))
    assert not response.ok and not calls


def test_public_snapshot_hides_frame_content_routing_ids():
    view = snapshot()
    view["slides"][0]["elements"][0]["_content_ref"] = "private_inner"
    async def execute(**kwargs):
        return {"ok": True, "ret": {"account_id": "account", "presentation_id": "deck", "snapshot": view}}
    response = asyncio.run(provider(execute).dispatch(NamedServiceContext(), request("object.get", object_ref="slides:google:account:presentation:deck")))
    assert response.ok
    assert "private_inner" not in str(response.to_dict())


def test_discovery_does_not_resolve_credentials_or_call_google():
    async def execute(**kwargs):raise AssertionError("Discovery must stay local")
    async def exercise():
        p=provider(execute)
        for op in ("provider.about","provider.capabilities","object.schema"):
            response=await p.dispatch(NamedServiceContext(),request(op))
            assert response.ok
    asyncio.run(exercise())


def test_object_get_binds_account_and_projects_geometry_to_vocabulary():
    calls=[]
    async def execute(**kwargs):
        calls.append(kwargs)
        return {"ok":True,"ret":{"account_id":"account","presentation_id":"deck","title":"Deck","revision_id":"r1","snapshot":snapshot()}}
    response=asyncio.run(provider(execute).dispatch(NamedServiceContext(),request("object.get",object_ref="slides:google:account:presentation:deck")))
    assert response.ok
    assert calls[0]["account_id"] == "account" and calls[0]["claim"] == ["slides:read"]
    assert response.ret["object"]["snapshot"]["slides"][0]["ref"] == "slides:google:account:slide:slide_a:deck"
    assert "geometry" not in json.dumps(response.ret)


def test_export_returns_ref_and_signed_url_without_base64_in_response():
    async def execute(**kwargs):
        assert kwargs["operation"] == "get"
        return {"ok":True,"ret":{"presentation_id":"deck","account_id":"account","snapshot":snapshot()}}
    response=asyncio.run(provider(execute,file_url_factory=lambda ctx,info:{"url":"https://files.example/signed"}).dispatch(NamedServiceContext(),request("object.action",action="export",object_ref="slides:google:account:presentation:deck",payload={"format":"pptx"})))
    obj=response.ret["object"]
    assert obj["ref"] == "slides:google:account:export:pptx:deck"
    assert obj["download"]["url"] == "https://files.example/signed"
    assert "content_base64" not in json.dumps(response.ret)


def test_artifact_stream_delivers_bytes_out_of_band():
    async def execute(**kwargs):
        assert kwargs["operation"] == "export"
        return {"ok":True,"ret":{"content_base64":base64.b64encode(b"pdf-bytes").decode()}}
    async def exercise():
        response=await provider(execute).dispatch(NamedServiceContext(),request("object.get",object_ref="slides:google:account:export:pdf:deck",response_mode="stream"))
        assert isinstance(response,NamedServiceStreamResult)
        data=b"".join([chunk async for chunk in response.chunks])
        assert data == b"pdf-bytes"
        assert "content_base64" not in json.dumps(response.response.ret)
    asyncio.run(exercise())


def test_react_pull_materializes_export_instead_of_returning_another_reference():
    async def execute(**kwargs):return {"ok":True,"ret":{"content_base64":base64.b64encode(b"pptx-bytes").decode()}}
    async def exercise():
        result=await provider(execute).dispatch(NamedServiceContext(),request("object.get",object_ref="slides:google:account:export:pptx:deck",context={"source":"react.pull"}))
        assert isinstance(result,NamedServiceStreamResult)
        assert b"".join([chunk async for chunk in result.chunks]) == b"pptx-bytes"
    asyncio.run(exercise())


@pytest.mark.parametrize("size", ["SMALL", "MEDIUM", "LARGE"])
def test_all_thumbnail_sizes_get_a_bound_signed_ref_and_materialize_the_same_size(size):
    calls, signed = [], []
    async def execute(**kwargs):
        calls.append(kwargs)
        if kwargs["operation"] == "get":
            return {"ok": True, "ret": {"presentation_id": "deck", "account_id": "account", "snapshot": snapshot()}}
        assert kwargs["operation"] == "thumbnail"
        return {"ok": True, "ret": {"content_base64": base64.b64encode(size.encode()).decode()}}
    def sign(ctx, info):
        signed.append(info["ref"])
        return {"url": "https://files.example/signed"}
    async def exercise():
        p = provider(execute, file_url_factory=sign)
        response = await p.dispatch(NamedServiceContext(), request("object.action", action="thumbnail", object_ref="slides:google:account:presentation:deck", payload={"slide": {"position": 1}, "size": size}))
        assert response.ok
        obj = response.ret["object"]
        assert obj["ref"] == f"slides:google:account:thumbnail:{size}:slide_a:deck"
        assert signed == [obj["ref"]]
        assert obj["download"]["url"] == "https://files.example/signed"
        # The signed download reconstructs size from the ref with no filters.
        materialized = await p.dispatch(NamedServiceContext(), request("object.get", object_ref=obj["ref"], response_mode="stream"))
        assert isinstance(materialized, NamedServiceStreamResult)
        assert b"".join([chunk async for chunk in materialized.chunks]) == size.encode()
        assert calls[-1]["payload"]["size"] == size
    asyncio.run(exercise())


@pytest.mark.parametrize("filters,payload", [({"size": "SMALL"}, {}), ({}, {"size": "MEDIUM"})])
def test_thumbnail_ref_size_cannot_be_overridden(filters, payload):
    async def execute(**kwargs):
        raise AssertionError("A bound artifact must be rejected before provider access")
    response = asyncio.run(provider(execute).dispatch(NamedServiceContext(), request("object.get", object_ref="slides:google:account:thumbnail:LARGE:slide_a:deck", filters=filters, payload=payload, response_mode="stream")))
    assert not response.ok and response.error.code == "slides_thumbnail_size_mismatch"


def test_legacy_thumbnail_ref_still_materializes_small():
    async def execute(**kwargs):
        assert kwargs["payload"]["size"] == "SMALL"
        return {"ok": True, "ret": {"content_base64": base64.b64encode(b"small").decode()}}
    async def exercise():
        response = await provider(execute).dispatch(NamedServiceContext(), request("object.get", object_ref="slides:google:account:thumbnail:slide_a:deck", response_mode="stream"))
        assert isinstance(response, NamedServiceStreamResult)
        assert b"".join([chunk async for chunk in response.chunks]) == b"small"
    asyncio.run(exercise())


def test_account_override_is_refused_before_provider_call():
    async def execute(**kwargs):raise AssertionError("must not call Google")
    response=asyncio.run(provider(execute).dispatch(NamedServiceContext(),request("object.get",object_ref="slides:google:account:presentation:deck",filters={"account_id":"different"})))
    assert not response.ok and response.error.code == "slides_account_mismatch"


def test_unknown_actions_are_refused_before_provider_call():
    async def execute(**kwargs):raise AssertionError("must not call Google")
    response=asyncio.run(provider(execute).dispatch(NamedServiceContext(),request("object.action",action="raw_batch_update",object_ref="slides:google:account:presentation:deck")))
    assert not response.ok and response.error.code == "slides_unsupported_action"


@pytest.mark.parametrize("action", ["set_text", "style_text", "set_notes", "move_slide", "delete_slide", "skip_slide"])
def test_edit_actions_keep_a_slide_ref_scope_and_require_write_claims(action):
    seen = []
    async def execute(**kwargs):
        seen.append(kwargs)
        return {"ok": True, "ret": {"presentation_id": "deck", "account_id": "account"}}
    result = asyncio.run(provider(execute).dispatch(NamedServiceContext(), request("object.action", action=action, object_ref="slides:google:account:slide:slide_a:deck", payload={"preview": True})))
    assert result.ok
    assert seen[0]["payload"]["slide"] == {"ref": "slide_a"}
    assert seen[0]["claim"] == ["slides:read", "slides:write"]


def test_add_slide_requires_a_presentation_ref_before_provider_call():
    async def execute(**kwargs):
        raise AssertionError("A slide ref must not expand its scope")
    result = asyncio.run(provider(execute).dispatch(NamedServiceContext(), request("object.action", action="add_slide", object_ref="slides:google:account:slide:slide_a:deck", payload={"layout": "blank"})))
    assert not result.ok and result.error.code == "slides_presentation_ref_required"


def test_slide_ref_constrains_replace_to_that_slide():
    seen=[]
    async def execute(**kwargs):
        seen.append(kwargs)
        return {"ok":True,"ret":{"presentation_id":"deck","account_id":"account"}}
    result=asyncio.run(provider(execute).dispatch(NamedServiceContext(),request("object.action",action="replace_text",object_ref="slides:google:account:slide:slide_a:deck",payload={"replacements":{"Hello":"Hi"}})))
    assert result.ok and seen[0]["payload"]["slide"] == {"ref":"slide_a"}


@pytest.mark.parametrize("operation,kwargs",[("object.upsert",{"object_ref":"slides:google:account:slide:slide_a:deck","payload":{"title":"Rename"}}),("object.action",{"action":"replace_text","object_ref":"slides:google:account:export:pdf:deck","payload":{"replacements":{"Hello":"Hi"}}}),("object.action",{"action":"copy","object_ref":"slides:google:account:slide:slide_a:deck","payload":{"title":"Copy"}})])
def test_artifact_or_slide_ref_never_expands_into_a_whole_deck_write(operation,kwargs):
    async def execute(**kwargs):raise AssertionError("must not call provider")
    result=asyncio.run(provider(execute).dispatch(NamedServiceContext(),request(operation,**kwargs)))
    assert not result.ok and result.error.code == "slides_presentation_ref_required"


def test_conflicting_source_cannot_change_the_addressed_deck():
    async def execute(**kwargs):raise AssertionError("must not call provider")
    result=asyncio.run(provider(execute).dispatch(NamedServiceContext(),request("object.action",action="copy",object_ref="slides:google:account:presentation:deck",payload={"source":"slides:google:account:presentation:other_deck","title":"Copy"})))
    assert not result.ok and result.error.code == "slides_source_mismatch"


def test_file_ref_uses_owned_copy_and_cleans_it_after_provider_failure(tmp_path):
    from PIL import Image
    from kdcube_ai_app.apps.chat.sdk.integrations.file_staging import new_staged_ref,save_staged,load_staged
    buffer=io.BytesIO()
    Image.new("RGB",(4,4)).save(buffer,"PNG")
    source=new_staged_ref("source.png")
    ctx = NamedServiceContext(user_id="user", tenant="tenant", project="project")
    save_staged(tmp_path,source,buffer.getvalue(), owner=ctx)
    minted=[]
    def mint(ctx,info):
        minted.append(info["staged_ref"])
        return {"url":"https://fetch.example/private-capability"}
    async def execute(**kwargs):
        assert kwargs["payload"]["image_uri"] == "https://fetch.example/private-capability"
        assert load_staged(tmp_path,minted[0], owner=ctx)[1] == buffer.getvalue()
        return {"ok":False,"error":{"code":"google_slides_rate_limited","message":"Rate limited"},"ret":{"retryable":True}}
    response=asyncio.run(provider(execute,staging_root_factory=lambda:tmp_path,provider_fetch_url_factory=mint).dispatch(ctx,request("object.action",action="replace_shapes_with_image",object_ref="slides:google:account:presentation:deck",payload={"tag":"{{IMAGE}}","file_ref":source})))
    assert not response.ok and "private-capability" not in json.dumps(response.to_dict())
    assert load_staged(tmp_path,source, owner=ctx)[1] == buffer.getvalue()
    with pytest.raises((FileNotFoundError,ValueError)):load_staged(tmp_path,minted[0], owner=ctx)
