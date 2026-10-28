"""The Slides vocabulary, with governed execution supplied by the host app."""
from __future__ import annotations

import base64
import json
import re
from contextlib import AsyncExitStack

from kdcube_ai_app.apps.chat.sdk.integrations.named_service_consent import tool_error_response
from kdcube_ai_app.apps.chat.sdk.integrations.slides.images import image_payload
from kdcube_ai_app.apps.chat.sdk.integrations.slides.schema import (
    SLIDES_NAMESPACE, SLIDES_SCHEMA, SLIDES_INTRO, SLIDES_PRESENTATION,
    SLIDES_ACTIONS, SLIDES_CONNECTED_ACCOUNT_REQUIREMENTS, CLAIMS_BY_OPERATION,
    action_claims,
)
from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError, parse_slides_ref, presentation_id, slides_ref, resolve_slide
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceProvider, NamedServiceProviderSpec, NamedServiceResponse,
    NamedServiceSearchScope, NamedServiceStreamResult,
)

EXPORT_FORMATS = {"pdf": "application/pdf", "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}


def slides_named_service_spec(*, bundle_id=None):
    return NamedServiceProviderSpec(
        provider_id="sdk.integrations.slides", namespace=SLIDES_NAMESPACE,
        bundle_id=bundle_id, refs=("slides:*",),
        object_kinds=("slides.presentation", "slides.export", "slides.thumbnail"),
        search_scopes=(NamedServiceSearchScope(namespace="slides", label="presentations", description="Find presentations by title or list recent decks."),),
        operations={name: {"transports": ("local", "api")} for name in SLIDES_SCHEMA["operations"]},
        label="Presentations", description="Read, copy, fill and edit connected presentations.", intro=SLIDES_INTRO,
        metadata={"presentation": {"about": SLIDES_INTRO, "third_party": "Google Slides", "operations": {k:v for k,v in SLIDES_PRESENTATION.items() if "." in k}, "actions": {k:v for k,v in SLIDES_PRESENTATION.items() if k in SLIDES_ACTIONS}}, "connected_accounts": SLIDES_CONNECTED_ACCOUNT_REQUIREMENTS, "grant_hints": CLAIMS_BY_OPERATION, "canonical_refs": SLIDES_SCHEMA["refs"], "actions": {name: meta["description"] for name,meta in SLIDES_SCHEMA["actions"].items()}, "object_kinds": {name: meta["description"] for name,meta in SLIDES_SCHEMA["object_kinds"].items()}, "provider_catalog": {"google": {"provider_id": "google", "label": "Google Slides", "claims": {"read": "slides:read", "write": "slides:write"}}}},
    )


def presentation_object(result, account_id):
    deck_id = result.get("presentation_id", "")
    return {"ref": slides_ref(account_id, deck_id), "kind": "slides.presentation", "title": result.get("title", ""), "revision_id": result.get("revision_id"), "web_url": result.get("web_url") or f"https://docs.google.com/presentation/d/{deck_id}/edit"}


def _input(request, payload):
    source = payload.get("source") or request.object_ref or payload.get("presentation_ref")
    account = str(payload.get("account_id") or request.filters.get("account_id") or "")
    parsed = None
    if str(source or "").startswith("slides:"):
        parsed = parse_slides_ref(source)
        if account and account != parsed["account_id"]:
            raise SlidesValidationError("slides_account_mismatch", "Use the account bound to the presentation ref.")
        account = parsed["account_id"]
    return account, presentation_id(source) if source else "", parsed


async def _chunks(data):
    for start in range(0, len(data), 65536):
        yield data[start:start+65536]


class SlidesNamedServiceProvider(NamedServiceProvider):
    def __init__(self, *, execute_operation, bundle_id=None, file_url_factory=None, staging_root_factory=None, provider_fetch_url_factory=None):
        super().__init__(slides_named_service_spec(bundle_id=bundle_id))
        self._execute_operation = execute_operation
        self._file_url_factory = file_url_factory
        self._staging_root_factory = staging_root_factory
        self._provider_fetch_url_factory = provider_fetch_url_factory

    async def dispatch(self, ctx, request):
        try:
            return await super().dispatch(ctx, request)
        except SlidesValidationError as exc:
            return NamedServiceResponse.error_response(code=exc.code, message=str(exc), details=exc.details, namespace="slides", object_ref=request.object_ref)

    def schema_object_kind_from_ref(self, object_ref):
        try:
            kind = parse_slides_ref(object_ref)["kind"]
            return "slides." + ("presentation" if kind == "slide" else kind)
        except SlidesValidationError:
            return None

    async def _execute(self, request, operation, payload, account_id):
        out = await self._execute_operation(operation=operation, claim=action_claims(operation, payload), payload=payload, account_id=account_id, tool_name=f"named_services.slides.{request.operation}" + (f".{request.action}" if request.action else ""))
        if not isinstance(out, dict) or not out.get("ok"):
            return None, tool_error_response(out if isinstance(out, dict) else {}, request=request, namespace="slides", provider_identity=self.provider_identity(), default_code="slides_operation_failed", fallback_message="The presentation operation failed.")
        return out.get("ret") or {}, None

    async def provider_about(self, ctx, request):
        return NamedServiceResponse.ok_response(namespace="slides", extra={"title": "Presentations", "description": SLIDES_INTRO, "schema": SLIDES_SCHEMA})

    async def provider_capabilities(self, ctx, request):
        return NamedServiceResponse.ok_response(namespace="slides", capabilities={"list": True, "search": True, "get": True, "upsert": True, "actions": list(SLIDES_ACTIONS), "connected_accounts": SLIDES_CONNECTED_ACCOUNT_REQUIREMENTS})

    async def object_schema(self, ctx, request):
        return NamedServiceResponse.ok_response(namespace="slides", extra={"schema": SLIDES_SCHEMA})

    async def object_list(self, ctx, request):
        return await self._search(request, "")

    async def object_search(self, ctx, request):
        return await self._search(request, request.query or "")

    async def _search(self, request, query):
        account = str(request.filters.get("account_id") or "")
        result, error = await self._execute(request, "search", {"query": query, "limit": request.limit or 20, "cursor": request.cursor or ""}, account)
        if error is not None:
            return error
        account = result.get("account_id") or account
        return NamedServiceResponse.ok_response(namespace="slides", items=[presentation_object(row, account) for row in result.get("items", [])], next_cursor=result.get("next_cursor"), extra={"incomplete_search": result.get("incomplete_search", False)})

    async def object_get(self, ctx, request):
        payload = dict(request.filters or {})
        account, deck_id, parsed = _input(request, payload)
        if parsed and parsed["kind"] in ("export", "thumbnail"):
            return await self._artifact(ctx, request, parsed, materialize=request.response_mode == "stream" or bool(request.context.get("materialize")))
        result, error = await self._execute(request, "get", {**payload, "presentation_ref": deck_id}, account)
        if error is not None:
            return error
        account = result.get("account_id") or account
        view = result["snapshot"]
        for slide in view["slides"]:
            slide["ref"] = slides_ref(account, deck_id, kind="slide", detail=slide["ref"])
        if parsed and parsed["kind"] == "slide":
            # A slide handle must not silently become the first deck page.
            target = {"ref": slides_ref(account, deck_id, kind="slide", detail=parsed["detail"])}
            while not any(s["ref"] == target["ref"] for s in view["slides"]) and view["range"]["next_slide"]:
                result, error = await self._execute(request, "get", {"presentation_ref": deck_id, "start_slide": view["range"]["next_slide"], "slide_limit": 50}, account)
                if error is not None:
                    return error
                view = result["snapshot"]
                for slide in view["slides"]:
                    slide["ref"] = slides_ref(account, deck_id, kind="slide", detail=slide["ref"])
            view["slides"] = [resolve_slide(view["slides"], target)]
        # Geometry is computed in the SDK. The named-service vocabulary reports
        # regions/relations and fit rather than asking agents to work in EMU.
        for slide in view["slides"]:
            for element in slide["elements"]:
                element.pop("geometry", None)
        obj = {**presentation_object(result, account), "snapshot": view}
        response = NamedServiceResponse.ok_response(namespace="slides", object_ref=obj["ref"], object=obj, revision=result.get("revision_id"))
        if request.response_mode == "stream":
            data = json.dumps(view, ensure_ascii=False).encode()
            return NamedServiceStreamResult(response=response, chunks=_chunks(data), filename=f"{deck_id}.slides.json", media_type="application/vnd.kdcube.slides.snapshot+json")
        return response

    async def object_upsert(self, ctx, request):
        payload = {**dict(request.payload or {}), **dict(request.object or {})}
        account, deck_id, _ = _input(request, payload)
        result, error = await self._execute(request, "rename" if deck_id else "create", {**payload, "presentation_ref": deck_id}, account)
        return error if error is not None else self._mutation(request, result, account)

    def _mutation(self, request, result, account):
        account = result.get("account_id") or account
        obj = presentation_object(result, account) if result.get("presentation_id") else None
        return NamedServiceResponse.ok_response(namespace="slides", object_ref=obj["ref"] if obj else request.object_ref, object=obj, extra={"action": request.action or request.operation, "result": result})

    async def object_action(self, ctx, request):
        action = request.action
        if action not in SLIDES_ACTIONS:
            raise SlidesValidationError("slides_unsupported_action", "Choose a supported action from object.schema.")
        payload = dict(request.payload or {})
        account, deck_id, parsed = _input(request, payload)
        if action in ("export", "thumbnail"):
            detail = str(payload.get("format", "pdf")).lower() if action == "export" else ""
            if action == "export" and detail not in EXPORT_FORMATS:
                raise SlidesValidationError("slides_invalid_export_format", "Export format must be pdf or pptx.")
            if action == "thumbnail":
                result, error = await self._execute(request, "get", {"presentation_ref": deck_id, "start_slide": payload.get("start_slide", 1), "slide_limit": 50}, account)
                if error is not None:
                    return error
                # Resolve against all pages, not just the first fifty slides.
                rows = result["snapshot"]["slides"]
                next_slide = result["snapshot"]["range"]["next_slide"]
                while next_slide:
                    page, error = await self._execute(request, "get", {"presentation_ref": deck_id, "start_slide": next_slide, "slide_limit": 50}, result.get("account_id") or account)
                    if error is not None:
                        return error
                    rows.extend(page["snapshot"]["slides"])
                    next_slide = page["snapshot"]["range"]["next_slide"]
                detail = resolve_slide(rows, payload.get("slide") or ({"ref": parsed["detail"]} if parsed and parsed["kind"] == "slide" else None))["ref"]
                account = result.get("account_id") or account
            else:
                result, error = await self._execute(request, "get", {"presentation_ref": deck_id}, account)
                if error is not None:
                    return error
                account = result.get("account_id") or account
            return await self._artifact(ctx, request, {"account_id": account, "presentation_id": deck_id, "kind": action, "detail": detail}, materialize=request.response_mode == "stream")
        async with AsyncExitStack() as stack:
            if action == "replace_shapes_with_image":
                payload = await stack.enter_async_context(image_payload(ctx, payload, staging_root_factory=self._staging_root_factory, provider_fetch_url_factory=self._provider_fetch_url_factory))
            elif action == "fill_template":
                images = payload.get("images", {})
                if not isinstance(images, dict):
                    raise SlidesValidationError("slides_invalid_template", "Pass images as a tag-to-image map.")
                mapped = {}
                for tag, image in images.items():
                    if not isinstance(image, (str, dict)):
                        raise SlidesValidationError("slides_invalid_template", "Each image must be a URL or a map with file_ref or image_uri.")
                    image = {"image_uri": image} if isinstance(image, str) else dict(image)
                    mapped[tag] = await stack.enter_async_context(image_payload(ctx, {**image, "preview": bool(payload.get("preview"))}, staging_root_factory=self._staging_root_factory, provider_fetch_url_factory=self._provider_fetch_url_factory))
                payload["images"] = mapped
            result, error = await self._execute(request, action, {**payload, "presentation_ref": deck_id}, account)
        return error if error is not None else self._mutation(request, result, account)

    async def _artifact(self, ctx, request, parsed, *, materialize):
        kind = parsed["kind"]
        ref = slides_ref(parsed["account_id"], parsed["presentation_id"], kind=kind, detail=parsed["detail"])
        payload = {"presentation_ref": parsed["presentation_id"]}
        if kind == "export":
            payload["format"] = parsed["detail"]
            extension, mime = parsed["detail"], EXPORT_FORMATS[parsed["detail"]]
        else:
            payload.update(slide={"ref": parsed["detail"]}, size=str(request.payload.get("size") or request.filters.get("size") or "SMALL").upper())
            if payload["size"] not in ("SMALL", "MEDIUM", "LARGE"):
                raise SlidesValidationError("slides_invalid_thumbnail_size", "Use SMALL, MEDIUM or LARGE.")
            extension, mime = "png", "image/png"
        filename = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{parsed['presentation_id']}-{parsed['detail']}.{extension}")
        obj = {"ref": ref, "kind": f"slides.{kind}", "filename": filename, "mime_type": mime}
        if materialize:
            result, error = await self._execute(request, kind, payload, parsed["account_id"])
            if error is not None:
                return error
            try:
                data = base64.b64decode(result["content_base64"], validate=True)
            except (KeyError, ValueError, TypeError) as exc:
                raise SlidesValidationError("slides_artifact_invalid", "The presentation provider returned invalid file bytes.") from exc
            obj.update({k: result[k] for k in ("width", "height", "byte_size") if k in result})
            response = NamedServiceResponse.ok_response(namespace="slides", object_ref=ref, object=obj, attrs={"materialization": {"media_type": mime, "filename": filename, "size_bytes": len(data), "complete": True}})
            return NamedServiceStreamResult(response=response, chunks=_chunks(data), filename=filename, media_type=mime)
        # The canonical thumbnail ref names a slide, with SMALL as its default.
        # Other sizes are explicitly streamed using filters.size, so a signed
        # URL cannot silently serve a different size from its metadata.
        if self._file_url_factory and (kind == "export" or payload["size"] == "SMALL"):
            download = self._file_url_factory(ctx, {"ref": ref})
            if hasattr(download, "__await__"):
                download = await download
            if download and download.get("url"):
                obj["download"] = {"encoding": "url", **download}
        if "download" not in obj:
            obj["delivery"] = {"response_mode": "stream", "filters": {"size": payload["size"]} if kind == "thumbnail" else {}, "note": "Read this ref with streaming object.get to receive the file bytes."}
        return NamedServiceResponse.ok_response(namespace="slides", object_ref=ref, object=obj)


def make_slides_named_service_provider(**kwargs):
    return SlidesNamedServiceProvider(**kwargs)
