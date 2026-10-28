"""Opaque presentation handles and deterministic, bounded slide selectors."""
from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import urlparse


class SlidesValidationError(ValueError):
    def __init__(self, code, message, *, details=None):
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


def presentation_id(value):
    text = str(value or "").strip()
    if text.startswith("slides:"):
        return parse_slides_ref(text)["presentation_id"]
    if text.startswith("https://"):
        url = urlparse(text)
        if url.hostname == "docs.google.com":
            match = re.fullmatch(r"/presentation/(?:u/\d+/)?d/([\w-]+)(?:/.*)?", url.path)
            if match:
                return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]+", text):
        return text
    raise SlidesValidationError("slides_invalid_ref", "Pass a presentation ref or a Google Slides link.")


def slides_ref(account_id, deck_id, *, kind="presentation", detail=""):
    parts = ["slides", "google", str(account_id), kind]
    if detail:
        parts.append(str(detail))
    parts.append(str(deck_id))
    return ":".join(parts)


def parse_slides_ref(value):
    parts = str(value or "").strip().split(":")
    if len(parts) not in (5, 6) or parts[:2] != ["slides", "google"] or not parts[2]:
        raise SlidesValidationError("slides_invalid_ref", "Use a slides:google account-bound ref returned by this service.")
    kind = parts[3]
    if kind == "presentation" and len(parts) == 5:
        detail = ""
    elif kind in ("slide", "thumbnail", "export") and len(parts) == 6:
        detail = parts[4]
        if kind == "export" and detail not in ("pdf", "pptx"):
            raise SlidesValidationError("slides_invalid_ref", "Export format must be pdf or pptx.")
    else:
        raise SlidesValidationError("slides_invalid_ref", "Unsupported presentation object ref.")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", parts[-1]) or (detail and not re.fullmatch(r"[A-Za-z0-9_-]+", detail)):
        raise SlidesValidationError("slides_invalid_ref", "The presentation handle is invalid.")
    return {"account_id": parts[2], "presentation_id": parts[-1], "kind": kind, "detail": detail}


def _one(matches, target, selector):
    if len(matches) == 1:
        return matches[0]
    code = f"slides_{target}_" + ("ambiguous" if matches else "not_found")
    candidates = [{k: row[k] for k in ("ref", "position", "title", "role", "kind") if k in row} for row in matches[:5]]
    raise SlidesValidationError(code, f"Choose one {target} with a snapshot ref or a more specific selector.", details={"selector": selector, "candidates": candidates})


def resolve_slide(slides, selector):
    if isinstance(selector, str):
        selector = {"ref": selector}
    if not isinstance(selector, Mapping) or not selector:
        raise SlidesValidationError("slides_slide_not_found", "Pass a slide selector: position, title, title_contains, tag, kind, ref or last.")
    allowed = {"ref", "position", "title", "title_contains", "tag", "kind", "last"}
    if set(selector) - allowed:
        raise SlidesValidationError("slides_invalid_selector", "Unknown slide selector field.")
    matches = list(slides)
    for key, value in selector.items():
        if key == "last":
            matches = [s for s in matches if value is True and s.get("position") == len(slides)]
        elif key == "tag":
            matches = [s for s in matches if str(value) in "\n".join(e.get("text", "") for e in s.get("elements", []))]
        elif key == "title_contains":
            matches = [s for s in matches if str(value).casefold() in s.get("title", "").casefold()]
        elif key == "ref":
            handle = str(value).split(":")[-2] if str(value).startswith("slides:") else value
            matches = [s for s in matches if s.get("ref") in (value, handle) or s.get("object_id") == handle]
        else:
            matches = [s for s in matches if s.get(key) == value]
    return _one(matches, "slide", selector)


def resolve_element(slide, selector):
    if isinstance(selector, str):
        selector = {"ref": selector}
    if not isinstance(selector, Mapping) or not selector:
        raise SlidesValidationError("slides_element_not_found", "Pass an element ref, role, text_contains, alt_text or kind and 1-based index.")
    allowed = {"ref", "role", "text_contains", "alt_text", "kind", "index"}
    if set(selector) - allowed:
        raise SlidesValidationError("slides_invalid_selector", "Unknown element selector field.")
    rows = list(slide.get("elements", []))
    for key, value in selector.items():
        if key == "index":
            continue
        if key == "text_contains":
            rows = [r for r in rows if str(value).casefold() in r.get("text", "").casefold()]
        else:
            rows = [r for r in rows if r.get(key) == value]
    if "index" in selector:
        index = selector["index"]
        rows = rows[index - 1:index] if type(index) is int and index > 0 else []
    return _one(rows, "element", selector)
