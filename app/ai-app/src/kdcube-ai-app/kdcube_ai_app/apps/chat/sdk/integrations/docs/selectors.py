# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Natural, provider-neutral selectors for document sub-resources."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


SELECTOR_CANDIDATE_LIMIT = 20


class DocsSelectorError(ValueError):
    """A selector cannot be resolved without guessing."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.details = dict(details or {})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized(value: Any) -> str:
    return " ".join(_text(value).casefold().split())


def _positive_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _hierarchy_parts(value: Any) -> list[str]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_text(part) for part in value if _text(part)]
    text = _text(value)
    if not text:
        return []
    return [part.strip() for part in re.split(r"\s*(?:/|>)\s*", text) if part.strip()]


def tab_candidates(tabs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return stable, human-readable tab candidates in provider order."""

    rows = [dict(tab) for tab in tabs if isinstance(tab, Mapping)]
    by_id = {_text(row.get("tab_id")): row for row in rows if _text(row.get("tab_id"))}

    def _path(row: Mapping[str, Any]) -> list[str]:
        path: list[str] = []
        seen: set[str] = set()
        current: Mapping[str, Any] | None = row
        while current is not None:
            title = _text(current.get("title")) or "<untitled>"
            path.append(title)
            parent_id = _text(current.get("parent_tab_id"))
            if not parent_id or parent_id in seen:
                break
            seen.add(parent_id)
            current = by_id.get(parent_id)
        return list(reversed(path))

    return [
        {
            "tab_id": _text(row.get("tab_id")),
            "title": _text(row.get("title")) or "<untitled>",
            "position": position,
            "hierarchy": _path(row),
            "parent_tab_id": _text(row.get("parent_tab_id")),
            "nesting_level": row.get("nesting_level"),
            "end_index": row.get("end_index"),
        }
        for position, row in enumerate(rows, start=1)
    ]


def resolve_tab_selector(
    tabs: Sequence[Mapping[str, Any]],
    selector: Mapping[str, Any] | str,
) -> dict[str, Any]:
    """Resolve one tab by exact lexical predicates over returned tab metadata."""

    if isinstance(selector, str):
        raw = {"title": selector}
    elif isinstance(selector, Mapping):
        raw = dict(selector)
    else:
        raise DocsSelectorError(
            "docs_tab_selector_invalid",
            "tab_selector must be a string or an object.",
            status=400,
            details={"selector_type": type(selector).__name__},
        )
    title = _normalized(raw.get("title"))
    title_contains = _normalized(raw.get("title_contains"))
    position = _positive_int(raw.get("position"))
    hierarchy = [_normalized(part) for part in _hierarchy_parts(raw.get("hierarchy"))]
    if not any((title, title_contains, position, hierarchy)):
        raise DocsSelectorError(
            "docs_tab_selector_invalid",
            "tab_selector must provide title, title_contains, position, or hierarchy.",
            status=400,
            details={"selector": raw},
        )
    if raw.get("position") not in (None, "") and position is None:
        raise DocsSelectorError(
            "docs_tab_selector_invalid",
            "tab_selector.position must be a positive 1-based integer.",
            status=400,
            details={"selector": raw},
        )

    candidates = tab_candidates(tabs)
    matches: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_title = _normalized(candidate.get("title"))
        candidate_hierarchy = [
            _normalized(part) for part in candidate.get("hierarchy") or []
        ]
        if title and candidate_title != title:
            continue
        if title_contains and title_contains not in candidate_title:
            continue
        if position is not None and candidate.get("position") != position:
            continue
        if hierarchy and candidate_hierarchy != hierarchy:
            continue
        matches.append(candidate)

    if not matches:
        raise DocsSelectorError(
            "docs_tab_not_found",
            "No tab matches the supplied tab_selector.",
            status=404,
            details={
                "selector": raw,
                "candidate_count": len(candidates),
                "candidates": candidates[:SELECTOR_CANDIDATE_LIMIT],
                "candidates_truncated": len(candidates) > SELECTOR_CANDIDATE_LIMIT,
                "next_action": "Choose one returned tab by title, position, or hierarchy.",
            },
        )
    if len(matches) > 1:
        raise DocsSelectorError(
            "docs_tab_selector_ambiguous",
            "The tab_selector matches more than one tab.",
            status=409,
            details={
                "selector": raw,
                "match_count": len(matches),
                "candidates": matches[:SELECTOR_CANDIDATE_LIMIT],
                "candidates_truncated": len(matches) > SELECTOR_CANDIDATE_LIMIT,
                "next_action": "Add position or full hierarchy to identify one tab.",
            },
        )
    return matches[0]


COMMENT_SCOPES = ("text", "cell", "document")


def comment_candidates(
    comments: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return bounded comment details suitable for disambiguation."""

    candidates: list[dict[str, Any]] = []
    for position, comment in enumerate(comments, start=1):
        if not isinstance(comment, Mapping):
            continue
        content = _text(comment.get("content"))
        quoted_text = _text(comment.get("quoted_text"))
        candidates.append(
            {
                "comment_id": _text(comment.get("comment_id")),
                "position": position,
                "content": content[:240],
                "quoted_text": quoted_text[:240],
                "author": _text(comment.get("author")),
                "author_is_me": bool(comment.get("author_is_me")),
                "resolved": bool(comment.get("resolved")),
                "created_time": _text(comment.get("created_time")),
                **{
                    key: comment[key]
                    for key in ("scope", "tab_title", "where", "anchor_state")
                    if comment.get(key) not in (None, "")
                },
                **(
                    {"current_text": _text(comment.get("current_text"))[:240]}
                    if comment.get("current_text")
                    else {}
                ),
            }
        )
    return candidates


def matching_comments(
    comments: Sequence[Mapping[str, Any]],
    selector: Mapping[str, Any] | str,
) -> list[dict[str, Any]]:
    """Filter comments using explicit lexical predicates only."""

    if isinstance(selector, str):
        raw = {"text_contains": selector}
    elif isinstance(selector, Mapping):
        raw = dict(selector)
    else:
        raise DocsSelectorError(
            "docs_comment_selector_invalid",
            "comment_selector must be a string or an object.",
            status=400,
            details={"selector_type": type(selector).__name__},
        )
    text_contains = _normalized(raw.get("text_contains"))
    quoted_contains = _normalized(raw.get("quoted_text_contains"))
    author = _normalized(raw.get("author"))
    author_contains = _normalized(raw.get("author_contains"))
    position = _positive_int(raw.get("position"))
    resolved_supplied = isinstance(raw.get("resolved"), bool)
    resolved = bool(raw.get("resolved"))
    scope = _normalized(raw.get("scope"))
    if scope and scope not in COMMENT_SCOPES:
        raise DocsSelectorError(
            "docs_comment_selector_invalid",
            f"comment_selector.scope must be one of: {', '.join(COMMENT_SCOPES)}.",
            status=400,
            details={"selector": raw},
        )
    if not any(
        (
            text_contains,
            quoted_contains,
            author,
            author_contains,
            position,
            resolved_supplied,
            scope,
        )
    ):
        raise DocsSelectorError(
            "docs_comment_selector_invalid",
            "comment_selector must provide text_contains, quoted_text_contains, "
            "author, author_contains, position, resolved, or scope.",
            status=400,
            details={"selector": raw},
        )
    if raw.get("position") not in (None, "") and position is None:
        raise DocsSelectorError(
            "docs_comment_selector_invalid",
            "comment_selector.position must be a positive 1-based integer.",
            status=400,
            details={"selector": raw},
        )

    matches: list[dict[str, Any]] = []
    for position_index, raw_comment in enumerate(comments, start=1):
        if not isinstance(raw_comment, Mapping):
            continue
        candidate = comment_candidates([raw_comment])[0]
        candidate["position"] = position_index
        content = _normalized(raw_comment.get("content"))
        quoted_text = _normalized(raw_comment.get("quoted_text"))
        current_text = _normalized(raw_comment.get("current_text"))
        author_name = _normalized(candidate.get("author"))
        if (
            text_contains
            and text_contains not in content
            and text_contains not in quoted_text
            and text_contains not in current_text
        ):
            continue
        if (
            quoted_contains
            and quoted_contains not in quoted_text
            and quoted_contains not in current_text
        ):
            continue
        if scope and _normalized(raw_comment.get("scope")) != scope:
            continue
        if author == "me":
            if not candidate.get("author_is_me"):
                continue
        elif author and author_name != author:
            continue
        if author_contains and author_contains not in author_name:
            continue
        if position is not None and candidate.get("position") != position:
            continue
        if resolved_supplied and candidate.get("resolved") is not resolved:
            continue
        matches.append(candidate)
    return matches


def resolve_comment_selector(
    comments: Sequence[Mapping[str, Any]],
    selector: Mapping[str, Any] | str,
) -> dict[str, Any]:
    """Resolve exactly one document-level comment without exposing ID lookup."""

    if isinstance(selector, str):
        raw = {"text_contains": selector}
    elif isinstance(selector, Mapping):
        raw = dict(selector)
    else:
        raise DocsSelectorError(
            "docs_comment_selector_invalid",
            "comment_selector must be a string or an object.",
            status=400,
            details={"selector_type": type(selector).__name__},
        )
    matches = matching_comments(comments, raw)
    if not matches:
        raise DocsSelectorError(
            "docs_comment_not_found",
            "No document comment matches the supplied comment_selector.",
            status=404,
            details={
                "selector": raw,
                "candidate_count": len(comments),
                "candidates": comment_candidates(comments)[:SELECTOR_CANDIDATE_LIMIT],
                "candidates_truncated": len(comments) > SELECTOR_CANDIDATE_LIMIT,
                "next_action": "Choose one returned comment or narrow the selector.",
            },
        )
    if len(matches) > 1:
        raise DocsSelectorError(
            "docs_comment_selector_ambiguous",
            "The comment_selector matches more than one document comment.",
            status=409,
            details={
                "selector": raw,
                "match_count": len(matches),
                "candidates": matches[:SELECTOR_CANDIDATE_LIMIT],
                "candidates_truncated": len(matches) > SELECTOR_CANDIDATE_LIMIT,
                "next_action": (
                    "Add author, resolved state, position, or a more specific text fragment."
                ),
            },
        )
    return matches[0]


__all__ = [
    "DocsSelectorError",
    "SELECTOR_CANDIDATE_LIMIT",
    "comment_candidates",
    "matching_comments",
    "resolve_comment_selector",
    "resolve_tab_selector",
    "tab_candidates",
]
