"""Bounded occurrence diagnostics shared by literal edits and comment anchors."""
from __future__ import annotations

import re

from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError

PREVIEW_WINDOW_CHARS = 80  # On each side of the complete literal match, as in Docs.
MAX_CANDIDATES = 20


def text_matches(text, quote, where, *, match_case=True, **target):
    if not isinstance(quote, str) or not quote or len(quote) > 100_000:
        raise SlidesValidationError("slides_invalid_text_span", "Pass nonempty literal quoted text of at most 100000 characters.")
    for match in re.finditer(re.escape(quote), text, 0 if match_case else re.IGNORECASE):
        yield {"text": text, "match": match, "where": where, **target}


def select_text_match(matches, occurrence=None):
    if occurrence is not None and (type(occurrence) is not int or occurrence < 1):
        raise SlidesValidationError("slides_invalid_occurrence", "occurrence must be a positive 1-based integer.")
    chosen, candidates, total = None, [], 0
    for total, item in enumerate(matches, 1):
        if total == (occurrence or 1):
            chosen = {**item, "occurrence": total}
        if total <= MAX_CANDIDATES:
            text, match = item["text"], item["match"]
            context = text[max(0, match.start() - PREVIEW_WINDOW_CHARS):match.end() + PREVIEW_WINDOW_CHARS]
            candidates.append({"occurrence": total, "where": item["where"], "context": context})
    details = {"occurrences": total, "candidates": candidates, "candidates_truncated": total > MAX_CANDIDATES}
    if not total:
        raise SlidesValidationError("slides_text_not_found", "The quoted text does not occur in the selected target.", details=details)
    if occurrence is None and total > 1:
        raise SlidesValidationError("slides_text_ambiguous", "Choose a 1-based occurrence from the candidates, or narrow the slide, element or cell selector.", details=details)
    if chosen is None:
        raise SlidesValidationError("slides_invalid_occurrence", f"occurrence must be between 1 and {total}.", details=details)
    return chosen


def match_range(item):
    text, match = item["text"], item["match"]
    return {"type": "FIXED_RANGE", "startIndex": len(text[:match.start()].encode("utf-16-le")) // 2, "endIndex": len(text[:match.end()].encode("utf-16-le")) // 2}
