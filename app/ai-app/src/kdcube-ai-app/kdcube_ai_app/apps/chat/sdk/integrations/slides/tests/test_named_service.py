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


def test_nondefault_thumbnail_size_explicitly_streams_instead_of_wrong_url():
    async def execute(**kwargs):return {"ok":True,"ret":{"presentation_id":"deck","account_id":"account","snapshot":snapshot()}}
    response=asyncio.run(provider(execute,file_url_factory=lambda *args:{"url":"https://files.example/default-size"}).dispatch(NamedServiceContext(),request("object.action",action="thumbnail",object_ref="slides:google:account:presentation:deck",payload={"slide":{"position":1},"size":"MEDIUM"})))
    assert response.ok
    assert "download" not in response.ret["object"]
    assert response.ret["object"]["delivery"]["filters"] == {"size":"MEDIUM"}


def test_account_override_is_refused_before_provider_call():
    async def execute(**kwargs):raise AssertionError("must not call Google")
    response=asyncio.run(provider(execute).dispatch(NamedServiceContext(),request("object.get",object_ref="slides:google:account:presentation:deck",filters={"account_id":"different"})))
    assert not response.ok and response.error.code == "slides_account_mismatch"


def test_unknown_actions_are_refused_before_provider_call():
    async def execute(**kwargs):raise AssertionError("must not call Google")
    response=asyncio.run(provider(execute).dispatch(NamedServiceContext(),request("object.action",action="raw_batch_update",object_ref="slides:google:account:presentation:deck")))
    assert not response.ok and response.error.code == "slides_unsupported_action"


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
    save_staged(tmp_path,source,buffer.getvalue())
    minted=[]
    def mint(ctx,info):
        minted.append(info["staged_ref"])
        return {"url":"https://fetch.example/private-capability"}
    async def execute(**kwargs):
        assert kwargs["payload"]["image_uri"] == "https://fetch.example/private-capability"
        assert load_staged(tmp_path,minted[0])[1] == buffer.getvalue()
        return {"ok":False,"error":{"code":"google_slides_rate_limited","message":"Rate limited"},"ret":{"retryable":True}}
    response=asyncio.run(provider(execute,staging_root_factory=lambda:tmp_path,provider_fetch_url_factory=mint).dispatch(NamedServiceContext(user_id="user"),request("object.action",action="replace_shapes_with_image",object_ref="slides:google:account:presentation:deck",payload={"tag":"{{IMAGE}}","file_ref":source})))
    assert not response.ok and "private-capability" not in json.dumps(response.to_dict())
    assert load_staged(tmp_path,source)[1] == buffer.getvalue()
    with pytest.raises((FileNotFoundError,ValueError)):load_staged(tmp_path,minted[0])
