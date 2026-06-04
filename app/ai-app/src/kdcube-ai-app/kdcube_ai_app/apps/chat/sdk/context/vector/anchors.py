# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
"""
Parse Retrieval-anchors blocks emitted by the React decision agent into a
flat anchors_text string suitable for BM25F-style indexing.

Contract (from v3/v2 decision protocols):

    <channel:summary>Goal: ...
    Outcome: ...
    Key facts: ...
    Refs: ...
    Retrieval-anchors:
      phrases: ["verbatim string", ...]
      entities: ["HighIDFProperNoun", ...]</channel:summary>

`phrases` are verbatim multi-word strings; they get quoted in the output so
Postgres `to_tsvector('simple', ...)` keeps them as distinct tokens. `entities`
are single proper nouns; they go in bare. Order is phrases-first so the more
discriminating tokens land earlier when the tsvector is built.

The parser is intentionally lenient: missing block, malformed JSON, missing
keys, and YAML-list-shaped values all produce an empty string rather than an
error. The persistence path treats empty as "no anchors" and the generated
search_tsv falls back to body-only weighting.
"""

from __future__ import annotations

import json
import re
from typing import List, Tuple

_ANCHORS_HEADER = re.compile(r"(?im)^\s*retrieval[\s_-]*anchors\s*:\s*$")
_FIELD_LINE = re.compile(r"(?i)^\s*(phrases|entities)\s*:\s*(.*)$")
_YAML_ITEM = re.compile(r"^\s*-\s*(.+?)\s*$")


def _parse_inline_list(raw: str) -> List[str]:
    raw = (raw or "").strip()
    if not raw:
        return []
    # JSON array first — covers ["a", "b"] and ['a', 'b'] after a quick repair.
    candidate = raw
    if candidate.startswith("[") and candidate.endswith("]"):
        try:
            arr = json.loads(candidate)
            if isinstance(arr, list):
                return [str(x).strip() for x in arr if str(x).strip()]
        except Exception:
            try:
                arr = json.loads(candidate.replace("'", '"'))
                if isinstance(arr, list):
                    return [str(x).strip() for x in arr if str(x).strip()]
            except Exception:
                pass
        # Best-effort: strip brackets and split on commas, honoring quotes.
        inner = candidate[1:-1]
        parts = re.findall(r'"([^"]*)"|\'([^\']*)\'|([^,]+)', inner)
        items: List[str] = []
        for a, b, c in parts:
            tok = (a or b or c).strip().strip(",").strip()
            if tok:
                items.append(tok)
        return items
    return []


def _collect_block(lines: List[str], start_idx: int) -> Tuple[List[str], List[str]]:
    phrases: List[str] = []
    entities: List[str] = []
    i = start_idx
    pending_key: str = ""
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        # End the block on a blank line or an unindented non-field line.
        if not stripped:
            break
        if not line.startswith((" ", "\t")) and not _FIELD_LINE.match(line):
            break
        m = _FIELD_LINE.match(line)
        if m:
            key = m.group(1).lower()
            value = m.group(2).strip()
            if value:
                items = _parse_inline_list(value)
                if key == "phrases":
                    phrases.extend(items)
                else:
                    entities.extend(items)
                pending_key = ""
            else:
                pending_key = key
        elif pending_key:
            yi = _YAML_ITEM.match(line)
            if yi:
                tok = yi.group(1).strip().strip('"').strip("'")
                if tok:
                    if pending_key == "phrases":
                        phrases.append(tok)
                    else:
                        entities.append(tok)
            else:
                pending_key = ""
        i += 1
    return phrases, entities


def parse_retrieval_anchors(summary_text: str) -> str:
    """
    Return a space-separated anchors_text string for indexing.

    Phrases (verbatim multi-word strings) are double-quoted so the
    `'simple'` tsvector configuration keeps them grouped logically when
    callers later query with `phraseto_tsquery` / `websearch_to_tsquery`.
    Entities are bare single tokens (or short proper noun groups).

    Empty input or a missing/malformed `Retrieval-anchors:` block returns "".
    """
    text = (summary_text or "").strip()
    if not text:
        return ""
    lines = text.splitlines()
    header_idx = -1
    for idx, line in enumerate(lines):
        if _ANCHORS_HEADER.match(line):
            header_idx = idx
            break
    if header_idx < 0:
        return ""
    phrases, entities = _collect_block(lines, header_idx + 1)
    tokens: List[str] = []
    for p in phrases:
        cleaned = p.strip().replace('"', '').replace("\n", " ").strip()
        if not cleaned:
            continue
        tokens.append(f'"{cleaned}"')
    for e in entities:
        cleaned = e.strip().replace('"', '').replace("\n", " ").strip()
        if cleaned:
            tokens.append(cleaned)
    return " ".join(tokens)
