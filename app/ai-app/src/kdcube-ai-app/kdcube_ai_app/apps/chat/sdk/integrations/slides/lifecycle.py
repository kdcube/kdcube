"""Bounded Drive conversion and app-created presentation trash/restore."""
from __future__ import annotations

import base64
import io
import json
import re
import uuid
import zipfile
from urllib.parse import parse_qs, urlparse

from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError, presentation_id

DRIVE_API = "https://www.googleapis.com/drive/v3"
SLIDES_MIME_TYPE = "application/vnd.google-apps.presentation"
CREATED_PROPERTY = "kdcube_slides_created"
CREATED_PROPERTIES = {CREATED_PROPERTY: "1"}
MAX_IMPORT_BYTES = 5*1024*1024
IMPORT_TYPES = {"pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation", "odp": "application/vnd.oasis.opendocument.presentation"}


def validate_title(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 300:
        raise SlidesValidationError("slides_invalid_title", "Pass a nonempty title of at most 300 characters.")
    return value.strip()


def drive_file_id(value):
    text = str(value or "")
    if text.startswith("https://"):
        parsed = urlparse(text)
        if parsed.hostname == "drive.google.com":
            match = re.fullmatch(r"/file/d/([A-Za-z0-9_-]+)(?:/.*)?", parsed.path)
            identity = match.group(1) if match else parse_qs(parsed.query).get("id", [""])[0] if parsed.path == "/open" else ""
            if re.fullmatch(r"[A-Za-z0-9_-]+", identity):
                return identity
    return presentation_id(value)


def import_bytes(payload):
    encoded = payload.get("content_base64")
    if not isinstance(encoded, str) or len(encoded) > (MAX_IMPORT_BYTES+2)//3*4:
        raise SlidesValidationError("slides_import_too_large", "Import PPTX/ODP files up to 5 MB through this bounded multipart route.")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise SlidesValidationError("slides_invalid_import", "The import file bytes are invalid.") from exc
    if not data or len(data) > MAX_IMPORT_BYTES:
        raise SlidesValidationError("slides_import_too_large", "Import a nonempty PPTX/ODP file of at most 5 MB.")
    kind = payload.get("source_format")
    if not isinstance(kind, str) or kind not in IMPORT_TYPES:
        raise SlidesValidationError("slides_import_format_unsupported", "Import source_format pptx or odp.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            if len(names) > 10000:
                raise ValueError("too many entries")
            if kind == "pptx":
                valid = "[Content_Types].xml" in names and "ppt/presentation.xml" in names
            else:
                info = archive.getinfo("mimetype")
                valid = info.file_size < 256 and archive.read(info) == IMPORT_TYPES["odp"].encode()
            if not valid:
                raise ValueError("format mismatch")
    except (ValueError, KeyError, zipfile.BadZipFile, RuntimeError) as exc:
        raise SlidesValidationError("slides_import_format_unsupported", "The file content does not match the selected PPTX/ODP format.") from exc
    return data, IMPORT_TYPES[kind]


async def mark_created(api, deck_id):
    await api.request("PATCH", f"{DRIVE_API}/files/{deck_id}", operation="create", mutating=True, json={"appProperties": CREATED_PROPERTIES}, params={"fields": "id"})


async def import_presentation(api, payload):
    source = payload.get("source_file", payload.get("source"))
    has_bytes = "content_base64" in payload
    if bool(source) == has_bytes:
        raise SlidesValidationError("slides_import_source_ambiguous", "Pass one Drive source_file or one deployment file_ref.")
    metadata = {"mimeType": SLIDES_MIME_TYPE, "appProperties": CREATED_PROPERTIES}
    if payload.get("parent_id"):
        metadata["parents"] = [drive_file_id(payload["parent_id"])]
    if has_bytes:
        data, mime = import_bytes(payload)
        metadata["name"] = validate_title(payload.get("title"))
        if payload.get("preview"):
            return {"preview": True, "title": metadata["name"], "source_format": payload["source_format"], "byte_size": len(data), "fit": [], "overlaps": [], "off_slide": []}
        boundary = "slides_"+uuid.uuid4().hex
        content = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()+json.dumps(metadata).encode()+f"\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n".encode()+data+f"\r\n--{boundary}--\r\n".encode())
        result = await api.request("POST", "https://www.googleapis.com/upload/drive/v3/files", operation="import", mutating=True, params={"uploadType": "multipart", "fields": "id,name,mimeType"}, headers={"Content-Type": f"multipart/related; boundary={boundary}"}, content=content)
    else:
        source_id = drive_file_id(source)
        original = await api.request("GET", f"{DRIVE_API}/files/{source_id}", operation="import", params={"fields": "id,name,mimeType"})
        if original.get("mimeType") not in (*IMPORT_TYPES.values(), SLIDES_MIME_TYPE):
            raise SlidesValidationError("slides_import_format_unsupported", "Choose a PPTX, ODP or native Slides file in Drive.")
        metadata["name"] = validate_title(payload.get("title", original.get("name")))
        if payload.get("preview"):
            return {"preview": True, "source_file": source_id, "title": metadata["name"], "fit": [], "overlaps": [], "off_slide": []}
        result = await api.request("POST", f"{DRIVE_API}/files/{source_id}/copy", operation="import", mutating=True, params={"fields": "id,name,mimeType"}, json=metadata)
    if not result.get("id"):
        raise SlidesValidationError("google_slides_invalid_response", "Google accepted import but returned no identity. Inspect recent files before importing again.", details={"outcome_unknown": True})
    api.created_id = result["id"]
    if result.get("mimeType") != SLIDES_MIME_TYPE:
        raise SlidesValidationError("slides_import_not_converted", "The created file is not native Slides. Inspect it before importing again.")
    return {"presentation_id": result["id"], "title": result.get("name", metadata["name"]), "web_url": f"https://docs.google.com/presentation/d/{result['id']}/edit"}


async def trash_or_restore(api, operation, payload, *, diagnostics):
    deck_id = presentation_id(payload.get("presentation_ref") or payload.get("source"))
    metadata = await api.request("GET", f"{DRIVE_API}/files/{deck_id}", operation=operation, params={"fields": "id,name,mimeType,trashed,appProperties"})
    if metadata.get("mimeType") != SLIDES_MIME_TYPE or metadata.get("appProperties", {}).get(CREATED_PROPERTY) != "1":
        raise SlidesValidationError("slides_app_created_required", "Trash and restore require a deck created, copied or imported by this service with its creation marker. Older unmarked decks are kept.")
    current = await api.get(deck_id) if not metadata.get("trashed") or payload.get("revision_id") else None
    if payload.get("revision_id"):
        if current.get("revisionId") != payload["revision_id"]:
            raise SlidesValidationError("slides_revision_changed", "The presentation changed after the supplied revision_id.")
    trashed = operation == "trash"
    if not payload.get("preview"):
        await api.request("PATCH", f"{DRIVE_API}/files/{deck_id}", operation=operation, mutating=True, json={"trashed": trashed}, params={"fields": "id,trashed"})
        if not trashed:
            current = await api.get(deck_id)
    flags = await diagnostics(api, current, predicted=bool(payload.get("preview"))) if current else {"fit": [], "overlaps": [], "off_slide": [], "geometry_unavailable": "trashed_presentation"}
    return {"presentation_id": deck_id, "title": metadata.get("name", ""), "trashed": trashed, "before_trashed": bool(metadata.get("trashed")), "preview": bool(payload.get("preview")), **flags}
