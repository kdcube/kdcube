"""Google Slides/Drive transport. Credentials stay in the trusted caller.

One bounded call, explicit revision guards, no quota/transport retries. A
revision race may be resolved once only when the caller supplied no revision.
"""
from __future__ import annotations

import base64
import copy
import io
import json
import re
from collections.abc import Mapping
from urllib.parse import urljoin, urlparse

import httpx

from kdcube_ai_app.apps.chat.sdk.integrations.google.slides_structure import snapshot
from kdcube_ai_app.apps.chat.sdk.integrations.provider_errors import provider_failure_from_response, provider_failure_from_exception
from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError, presentation_id, resolve_slide
from kdcube_ai_app.apps.chat.sdk.integrations.slides.templates import replace_text_requests, image_requests, template_requests

SLIDES_API = "https://slides.googleapis.com/v1"
DRIVE_API = "https://www.googleapis.com/drive/v3"
SLIDES_MIME_TYPE = "application/vnd.google-apps.presentation"
EXPORT_FORMATS = {"pdf": "application/pdf", "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}
MAX_EXPORT_BYTES = 10 * 1024 * 1024
MAX_IMAGE_BYTES = 50 * 1024 * 1024
MAX_PRESENTATION_BYTES = 16 * 1024 * 1024
MAX_BATCH_REQUESTS = 100
MUTATING_OPERATIONS = frozenset({"create", "rename", "copy", "fill_template", "replace_text", "replace_shapes_with_image"})
# URLs from image/chart objects are deliberately excluded. Group children
# remain internal and are projected by slides_structure, never returned raw.
_ELEMENT = "objectId,title,description,size,transform,shape,table,elementGroup(children),image(imageProperties(cropProperties)),sheetsChart(spreadsheetId,chartId),video(id,source)"
PRESENTATION_FIELDS = f"presentationId,title,revisionId,pageSize,slides(objectId,slideProperties(isSkipped,layoutObjectId,notesPage(notesProperties,pageElements({_ELEMENT}))),pageElements({_ELEMENT})),layouts(objectId,layoutProperties,pageElements({_ELEMENT})),masters(objectId,pageElements({_ELEMENT}))"


class _ProviderError(Exception):
    def __init__(self, failure):
        self.failure = failure


class SlidesClient:
    def __init__(self, client, token):
        self.client, self.headers = client, {"Authorization": f"Bearer {token}"}
        self.created_id = ""
        self.writes_applied = False

    async def request(self, method, url, *, operation, mutating=False, **kwargs):
        response = await self.client.request(method, url, headers=self.headers, **kwargs)
        if response.status_code >= 400:
            failure = provider_failure_from_response(response, provider="google", service="google_slides", operation=operation, fallback="Google Slides could not complete this operation.", mutating=mutating)
            message = failure.message.lower()
            if response.status_code == 400 and "required revision" in message and ("does not match" in message or "mismatch" in message):
                raise SlidesValidationError("slides_revision_changed", "The presentation changed. Read it again before retrying with a new revision_id.")
            if response.status_code == 400 and "office file" in message:
                raise SlidesValidationError("slides_not_native", "This is an Office file in Drive. Save a native Google Slides copy, or use import when that action is available, then edit the native copy.")
            if response.status_code == 400 and operation == "replace_shapes_with_image":
                raise SlidesValidationError("slides_image_fetch_failed", "Google could not fetch the image. Check the public URL, format, size and image dimensions.")
            raise _ProviderError(failure)
        if mutating:
            self.writes_applied = True
        if len(response.content) > MAX_PRESENTATION_BYTES:
            raise SlidesValidationError("slides_response_too_large", "This presentation exceeds the bounded read size. Export it or use a smaller deck.")
        try:
            body=response.json() if response.content else {}
        except ValueError as exc:
            raise SlidesValidationError("google_slides_invalid_response", "Google returned an unreadable response. Read the presentation before retrying a write.", details={"outcome_unknown":mutating}) from exc
        if not isinstance(body,Mapping):
            raise SlidesValidationError("google_slides_invalid_response", "Google returned an invalid response object. Read the presentation before retrying a write.", details={"outcome_unknown":mutating})
        return dict(body)

    async def get(self, deck_id):
        result=await self.request("GET", f"{SLIDES_API}/presentations/{deck_id}", operation="get", params={"fields": PRESENTATION_FIELDS})
        if not result.get("presentationId"):
            raise SlidesValidationError("google_slides_invalid_response", "Google returned no presentation identity. Read the presentation again.")
        return result

    async def all_snapshot(self, presentation):
        # The REST API has no slide pagination. Project bounded windows locally;
        # edit resolution must still inspect every slide, never only page one.
        result = snapshot(presentation, start=1, limit=50)
        result["slides"] = []
        for start in range(1, len(presentation.get("slides", []))+1, 50):
            part = snapshot(presentation, start=start, limit=50)
            result["slides"].extend(part["slides"])
        # Internal edit resolution sees complete text, independently of the
        # bounded text projected into a model-facing snapshot.
        def texts(elements):
            for element in elements:
                yield element
                yield from texts(element.get("elementGroup", {}).get("children", []))
        from kdcube_ai_app.apps.chat.sdk.integrations.google.slides_structure import shape_text
        raw = {e.get("objectId"): e for slide in presentation.get("slides", []) for e in texts(slide.get("pageElements", []))}
        for slide in result["slides"]:
            for element in slide["elements"]:
                if element["kind"] == "shape":
                    element["text"] = shape_text(raw[element["ref"]].get("shape", {}))
                    element["text_truncated"] = False
        return result

    async def write_batches(self, deck_id, requests, revision, *, operation):
        if not revision:
            raise SlidesValidationError("slides_revision_unavailable", "Google returned no revision. Read the presentation again before editing.")
        replies = []
        for start in range(0, len(requests), MAX_BATCH_REQUESTS):
            result = await self.request("POST", f"{SLIDES_API}/presentations/{deck_id}:batchUpdate", operation=operation, mutating=True, json={"requests": requests[start:start+MAX_BATCH_REQUESTS], "writeControl": {"requiredRevisionId": revision}})
            replies.extend(result.get("replies", []))
            next_revision = result.get("writeControl", {}).get("requiredRevisionId")
            if not next_revision:
                raise SlidesValidationError("slides_revision_unavailable", "The write completed but Google returned no next revision. Read the presentation before continuing.")
            revision = next_revision
        return {"revision_id": revision, "replies": replies}

    async def copy(self, deck_id, payload):
        title = _title(payload.get("title"))
        body = {"name": title}
        if payload.get("parent_id"):
            body["parents"] = [presentation_id(payload["parent_id"])]
        result = await self.request("POST", f"{DRIVE_API}/files/{deck_id}/copy", operation="copy", mutating=True, params={"fields": "id,name,mimeType,webViewLink"}, json=body)
        if not result.get("id"):
            raise SlidesValidationError("google_slides_invalid_response", "Google accepted the copy but returned no identity. Inspect recent presentations before creating another copy.", details={"outcome_unknown":True})
        self.created_id = result.get("id", "")
        return {"presentation_id": result["id"], "title": result.get("name", title), "web_url": f"https://docs.google.com/presentation/d/{result['id']}/edit"}


def _title(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 300:
        raise SlidesValidationError("slides_invalid_title", "Pass a nonempty title of at most 300 characters.")
    return value.strip()


async def _download(client, url, *, max_bytes, google_thumbnail=False):
    # Public capability URLs are fetched without the Google bearer. Each
    # redirect is checked too, and body reads stop at the explicit byte limit.
    from kdcube_ai_app.apps.chat.sdk.tools.backends.web import ssrf_guard
    for _ in range(4):
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.username or parsed.password or not parsed.hostname or len(url) > (8192 if google_thumbnail else 2048):
            raise SlidesValidationError("slides_image_uri_blocked", "Use a public HTTPS image URL without embedded credentials.")
        if google_thumbnail and not (parsed.hostname == "googleusercontent.com" or parsed.hostname.endswith(".googleusercontent.com")):
            raise SlidesValidationError("slides_image_uri_blocked", "Google returned an unexpected thumbnail host.")
        verdict = await ssrf_guard.check_url(url)
        if not verdict.allowed:
            raise SlidesValidationError("slides_image_uri_blocked", "The image host is not reachable through the public image policy.")
        async with client.stream("GET", url, follow_redirects=False) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers.get("location", ""))
                continue
            if response.status_code != 200:
                raise SlidesValidationError("slides_image_fetch_failed", "The image server did not return a successful image response.")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) >= max_bytes:
                    raise SlidesValidationError("slides_image_too_large", "The image exceeds the supported byte limit.")
            return bytes(data), response.headers.get("content-type", "").split(";")[0]
    raise SlidesValidationError("slides_image_uri_blocked", "The image URL redirects too many times.")


async def validate_image_uri(client, uri):
    from PIL import Image
    from kdcube_ai_app.infra.service_hub.multimodality import validate_image_bytes
    data, mime = await _download(client, str(uri), max_bytes=MAX_IMAGE_BYTES)
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width*image.height > 25_000_000:
                raise SlidesValidationError("slides_image_too_large", "Images must contain at most 25 megapixels.")
    except SlidesValidationError:
        raise
    except Exception as exc:
        raise SlidesValidationError("slides_image_unexpected_type", "Use a valid PNG, JPEG or GIF image.") from exc
    measured = validate_image_bytes(data, media_type=mime)
    if not measured.get("valid") or measured.get("media_type", mime) not in {"image/png", "image/jpeg", "image/gif"}:
        raise SlidesValidationError("slides_image_unexpected_type", "Use a PNG, JPEG or GIF image.")
    if not measured.get("width") or not measured.get("height") or measured["width"] * measured["height"] > 25_000_000:
        raise SlidesValidationError("slides_image_too_large", "Images must contain at most 25 megapixels.")


def _predict(presentation, requests):
    """Estimate text fit over a private copy; never mutate the provider input."""
    from kdcube_ai_app.apps.chat.sdk.integrations.google.slides_structure import shape_text
    result=copy.deepcopy(presentation)
    def shapes(elements):
        for e in elements:
            if "shape" in e:yield e
            yield from shapes(e.get("elementGroup", {}).get("children", []))
    for request in requests:
        replacement=request.get("replaceAllText")
        image=request.get("replaceAllShapesWithImage")
        for slide in result.get("slides", []):
            target_pages=(replacement or image or {}).get("pageObjectIds", [])
            if target_pages and slide["objectId"] not in target_pages:continue
            for element in shapes(slide.get("pageElements", [])):
                text=shape_text(element["shape"])
                changed=None
                if replacement:
                    old=replacement["containsText"]["text"]
                    changed=text.replace(old,replacement["replaceText"]) if replacement["containsText"].get("matchCase") else re.sub(re.escape(old),lambda m:replacement["replaceText"],text,flags=re.I)
                elif image and image["containsText"]["text"] in text:
                    changed=""
                elif request.get("deleteText", {}).get("objectId")==element["objectId"]:changed=""
                elif request.get("insertText", {}).get("objectId")==element["objectId"]:changed=request["insertText"]["text"]+text
                if changed is not None:
                    runs=element["shape"].get("text", {}).get("textElements", [])
                    style=next((r["textRun"].get("style", {}) for r in runs if "textRun" in r), {})
                    element["shape"]["text"]={"textElements":[{"textRun":{"content":changed,"style":style}}]}
    return result


async def _fit_flags(api, presentation, *, predicted=False):
    view=await api.all_snapshot(presentation)
    flags=[{"slide":s["ref"],"element":e["ref"],**e["fit"]} for s in view["slides"] for e in s["elements"] if e.get("fit")]
    return {"fit":flags[:1000],"fit_truncated":len(flags)>1000,"fit_predicted":predicted}


async def _run(api, operation, payload):
    if operation not in {"get", "search", "export", "thumbnail", *MUTATING_OPERATIONS}:
        raise SlidesValidationError("slides_unsupported_operation", "Choose an operation declared by the Slides schema.")
    if operation == "search":
        query = str(payload.get("query") or "")
        if len(query) > 300:
            raise SlidesValidationError("slides_invalid_query", "Use a title query of at most 300 characters.")
        escaped = query.replace("\\", "\\\\").replace("'", "\\'")
        q = f"trashed = false and mimeType = '{SLIDES_MIME_TYPE}'"
        if query:
            q += f" and name contains '{escaped}'"
        limit = payload.get("limit", 20)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise SlidesValidationError("slides_invalid_limit", "Use a result limit from 1 to 50.")
        result = await api.request("GET", f"{DRIVE_API}/files", operation="search", params={"q": q, "pageSize": limit, "pageToken": str(payload.get("cursor") or ""), "fields": "nextPageToken,incompleteSearch,files(id,name,mimeType,webViewLink,modifiedTime)", "orderBy": "modifiedTime desc"})
        rows = result.get("files", [])
        rows.sort(key=lambda row: row.get("name", "").casefold() != query.casefold())
        return {"items": [{"presentation_id": r["id"], "title": r.get("name", ""), "web_url": r.get("webViewLink", ""), "modified_time": r.get("modifiedTime")} for r in rows], "next_cursor": result.get("nextPageToken"), "incomplete_search": result.get("incompleteSearch", False)}
    if operation == "create":
        title = _title(payload.get("title"))
        if payload.get("preview"):
            return {"preview": True, "title": title, "fit": []}
        result = await api.request("POST", f"{SLIDES_API}/presentations", operation="create", mutating=True, json={"title": title})
        if not result.get("presentationId"):
            raise SlidesValidationError("google_slides_invalid_response", "Google accepted creation but returned no identity. Inspect recent presentations before creating another deck.", details={"outcome_unknown":True})
        api.created_id=result["presentationId"]
        return {"presentation_id": result["presentationId"], "title": title, "fit": []}
    deck_id = presentation_id(payload.get("presentation_ref") or payload.get("source"))
    if operation == "export":
        await api.get(deck_id)  # Native-only, including direct adapter callers.
        fmt = str(payload.get("format", "pdf")).lower()
        if fmt not in EXPORT_FORMATS:
            raise SlidesValidationError("slides_invalid_export_format", "Export format must be pdf or pptx.")
        response = await api.client.get(f"{DRIVE_API}/files/{deck_id}/export", headers=api.headers, params={"mimeType": EXPORT_FORMATS[fmt]})
        if response.status_code >= 400:
            raise _ProviderError(provider_failure_from_response(response, provider="google", service="google_slides", operation="export", fallback="Google could not export this presentation."))
        if len(response.content) > MAX_EXPORT_BYTES:
            raise SlidesValidationError("slides_export_too_large", "The export exceeds Drive's 10 MB export ceiling.")
        return {"presentation_id": deck_id, "format": fmt, "mime_type": EXPORT_FORMATS[fmt], "byte_size": len(response.content), "content_base64": base64.b64encode(response.content).decode("ascii")}
    presentation = await api.get(deck_id)
    if operation == "get":
        if payload.get("slide_selector") is not None:
            selected=resolve_slide((await api.all_snapshot(presentation))["slides"],payload["slide_selector"])
            result=snapshot(presentation,start=selected["position"],limit=1)
            result["range"]["next_slide"]=None
            result["range"]["selection"]="one_slide"
        else:
            result = snapshot(presentation, start=payload.get("start_slide", 1), limit=payload.get("slide_limit", 20))
        return {"presentation_id": deck_id, "title": presentation.get("title", ""), "snapshot": result, "revision_id": presentation.get("revisionId")}
    expected = payload.get("revision_id")
    if expected and expected != presentation.get("revisionId"):
        raise SlidesValidationError("slides_revision_changed", "The presentation changed after the supplied revision_id. Read it again before editing.")
    if operation == "thumbnail":
        view = await api.all_snapshot(presentation)
        slide = resolve_slide(view["slides"], payload.get("slide"))
        size = str(payload.get("size", "SMALL")).upper()
        if size not in ("SMALL", "MEDIUM", "LARGE"):
            raise SlidesValidationError("slides_invalid_thumbnail_size", "Use SMALL, MEDIUM or LARGE.")
        result = await api.request("GET", f"{SLIDES_API}/presentations/{deck_id}/pages/{slide['ref']}/thumbnail", operation="thumbnail", params={"thumbnailProperties.mimeType": "PNG", "thumbnailProperties.thumbnailSize": size})
        data, mime = await _download(api.client, result.get("contentUrl", ""), max_bytes=MAX_EXPORT_BYTES, google_thumbnail=True)
        if mime != "image/png" or not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise SlidesValidationError("slides_image_unexpected_type", "Google returned an invalid PNG thumbnail.")
        return {"presentation_id": deck_id, "slide_id": slide["ref"], "width": result.get("width"), "height": result.get("height"), "mime_type": "image/png", "byte_size": len(data), "content_base64": base64.b64encode(data).decode("ascii")}
    if operation == "rename":
        title = _title(payload.get("title"))
        if payload.get("preview"):
            return {"preview": True, "presentation_id": deck_id, "before": presentation.get("title"), "title": title, **await _fit_flags(api,presentation,predicted=True)}
        # Drive metadata has no Slides revision guard. Do not promise atomic
        # compare-and-swap for a rename: the supplied revision was checked above.
        await api.request("PATCH", f"{DRIVE_API}/files/{deck_id}", operation="rename", mutating=True, json={"name": title}, params={"fields": "id,name"})
        return {"presentation_id": deck_id, "title": title, **await _fit_flags(api,presentation)}
    if operation == "copy":
        _title(payload.get("title"))
        if payload.get("preview"):
            return {"preview": True, "source_presentation_id": deck_id, "title": payload["title"], **await _fit_flags(api,presentation,predicted=True)}
        copied=await api.copy(deck_id,payload)
        copied_presentation=await api.get(copied["presentation_id"])
        return {**copied,"revision_id":copied_presentation.get("revisionId"), **await _fit_flags(api,copied_presentation)}
    if operation not in ("fill_template", "replace_text", "replace_shapes_with_image"):
        raise SlidesValidationError("slides_unsupported_operation", "Choose an operation declared by the Slides schema.")
    for attempt in range(2):
        view = await api.all_snapshot(presentation)
        if operation == "fill_template":
            _title(payload.get("title"))
            requests, plan = template_requests(view, payload)
        elif operation == "replace_text":
            requests, plan = replace_text_requests(view, payload)
        else:
            requests, plan = image_requests(view, payload), {"tag": payload.get("tag", payload.get("text"))}
        if payload.get("preview"):
            return {"preview": True, "presentation_id": deck_id, "changes": plan, "request_count": len(requests), "revision_id": presentation.get("revisionId"), **await _fit_flags(api,_predict(presentation,requests),predicted=True)}
        for request in requests:
            if "replaceAllShapesWithImage" in request:
                await validate_image_uri(api.client, request["replaceAllShapesWithImage"]["imageUrl"])
        if operation == "fill_template":
            copied = await api.copy(deck_id, payload)
            deck_id = copied["presentation_id"]
            presentation = await api.get(deck_id)
            # A copied deck may assign different element/page ids. Compile
            # against its own fresh structure and chain its own revision.
            requests, plan = template_requests(await api.all_snapshot(presentation), payload)
        try:
            written = await api.write_batches(deck_id, requests, presentation.get("revisionId"), operation=operation)
        except SlidesValidationError as exc:
            if exc.code == "slides_revision_changed" and not expected and not api.writes_applied and attempt == 0:
                presentation = await api.get(deck_id)
                continue
            raise
        after = await api.get(deck_id) if requests else presentation
        return {"presentation_id": deck_id, "title": after.get("title", ""), "revision_id": written["revision_id"], "observed_revision_id":after.get("revisionId"), "changes": plan, **await _fit_flags(api,after), "replies": written["replies"]}


async def execute_google_slides_operation(*, operation, access_token, payload=None):
    body = dict(payload or {})
    where = f"google_slides.{operation}"
    if not access_token:
        return {"ok": False, "error": {"code": "credential_missing_access_token", "message": "The Google connection has no access token.", "managed": True, "where": where}, "ret": {"outcome_unknown": False}}
    api = None
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10)) as client:
            api = SlidesClient(client, access_token)
            ret = await _run(api, operation, body)
        return {"ok": True, "error": None, "ret": ret}
    except SlidesValidationError as exc:
        result = {"ok": False, "error": {"code": exc.code, "message": str(exc), "details": exc.details, "managed": True, "where": where}, "ret": {"outcome_unknown": False, **exc.details}}
    except _ProviderError as exc:
        result = exc.failure.error_result(where=where)
        if operation == "fill_template" and not (api and api.created_id) and exc.failure.category in ("access_denied", "not_found"):
            result["error"].update(code="slides_template_unreadable", message="The connected account cannot read or copy this template. Share it with that account and retry.")
    except httpx.HTTPError as exc:
        failure = provider_failure_from_exception(exc, provider="google", service="google_slides", operation=operation, fallback="Google Slides did not return a response. Read the presentation before retrying a write.", mutating=operation in MUTATING_OPERATIONS and not body.get("preview"))
        result = failure.error_result(where=where)
        result["error"]["message"] = "Google Slides did not return a response. Read the presentation before retrying a write."
    if api and api.writes_applied:
        result["ret"]["partial_result"] = True
        if api.created_id:
            result["ret"]["created_presentation_id"] = api.created_id
    return result
