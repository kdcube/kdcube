"""YouTube/Drive video sources and placement-independent construction."""
from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import parse_qs, urlparse

from kdcube_ai_app.apps.chat.sdk.integrations.slides.geometry import element_properties
from kdcube_ai_app.apps.chat.sdk.integrations.slides.layouts import new_id
from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError


def video_source(value):
    if isinstance(value, str):
        value = {"url": value}
    if not isinstance(value, Mapping):
        raise SlidesValidationError("slides_invalid_video", "Pass video as a YouTube/Drive HTTPS URL or {source: youtube|drive, id}.")
    source, identity = str(value.get("source", "")).upper(), value.get("id")
    if value.get("url"):
        if identity:
            raise SlidesValidationError("slides_invalid_video", "Pass a video URL or source/id, with one source per video.")
        try:
            url = urlparse(str(value["url"]))
            valid_port = url.port in (None, 443)
        except ValueError as exc:
            raise SlidesValidationError("slides_invalid_video", "Use a valid YouTube or Drive HTTPS URL.") from exc
        if url.scheme != "https" or url.username or url.password or not valid_port:
            raise SlidesValidationError("slides_invalid_video", "Use a YouTube or Drive HTTPS URL without credentials.")
        host = url.hostname
        if host in ("youtube.com", "www.youtube.com", "m.youtube.com"):
            inferred = "YOUTUBE"
            identity = (parse_qs(url.query).get("v") or [""])[0] if url.path == "/watch" else url.path.split("/")[-1] if url.path.startswith(("/shorts/", "/embed/")) else ""
        elif host == "youtu.be":
            inferred, identity = "YOUTUBE", url.path.strip("/")
        elif host == "drive.google.com":
            inferred = "DRIVE"
            match = re.fullmatch(r"/file/d/([\w-]+)(?:/.*)?", url.path)
            identity = match.group(1) if match else (parse_qs(url.query).get("id") or [""])[0]
        else:
            raise SlidesValidationError("slides_invalid_video", "Use a youtube.com, youtu.be or drive.google.com video URL.")
        if source and source != inferred:
            raise SlidesValidationError("slides_invalid_video", "The video URL and source disagree.")
        source = inferred
    pattern = r"[A-Za-z0-9_-]{11}" if source == "YOUTUBE" else r"[A-Za-z0-9_-]+"
    if source not in ("YOUTUBE", "DRIVE") or not isinstance(identity, str) or not re.fullmatch(pattern, identity) or len(identity) > 256:
        raise SlidesValidationError("slides_invalid_video", "Choose youtube or drive and a valid video ID, or pass its HTTPS URL.")
    return {"source": source, "id": identity}


def new_video(raw_slide, value, box):
    source = video_source(value)
    ref = new_id()
    return [{"createVideo": {"objectId": ref, **source, "elementProperties": element_properties(raw_slide["objectId"], box)}}], {"ref": ref, "kind": "video", **source}, box
