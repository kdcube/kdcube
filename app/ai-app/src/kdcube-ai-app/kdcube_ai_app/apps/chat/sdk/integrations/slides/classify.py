"""Deterministic reference-slide vocabulary from layout and visible contents."""
from __future__ import annotations

import re


def classify(elements, layout=None, title=""):
    if layout:
        return layout
    kinds = [e.get("kind") for e in elements]
    bodies = [e for e in elements if str(e.get("role", "")).startswith("body")]
    if "table" in kinds:
        return "table"
    if "chart" in kinds:
        return "chart"
    if "image" in kinds and any(e.get("text", "").strip() for e in elements):
        return "image_with_text"
    if len(bodies) == 2:
        return "title_and_two_columns"
    if bodies:
        return "title_and_body"
    if title.strip().casefold() in ("thank you", "questions", "спасибо", "дякую"):
        return "closing"
    text = [e.get("text", "").strip() for e in elements if e.get("text", "").strip()]
    if len(text) == 1 and re.fullmatch(r"[\d\s.,%+−-]+", text[0]):
        return "big_number"
    if any(e.get("alt_text", "").casefold() == "quote" for e in elements):
        return "quote"
    return "title_only" if title and len(text) == 1 else "other"
