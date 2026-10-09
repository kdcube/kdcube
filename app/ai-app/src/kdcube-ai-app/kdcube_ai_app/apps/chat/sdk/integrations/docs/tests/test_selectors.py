from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.docs.selectors import (
    DocsSelectorError,
    SELECTOR_CANDIDATE_LIMIT,
    matching_comments,
    resolve_comment_selector,
    resolve_tab_selector,
)


def test_tab_selector_supports_position_and_hierarchy() -> None:
    tabs = [
        {"tab_id": "root", "title": "Invoices", "parent_tab_id": ""},
        {"tab_id": "july", "title": "July", "parent_tab_id": "root"},
        {"tab_id": "notes", "title": "Notes", "parent_tab_id": "root"},
    ]

    by_position = resolve_tab_selector(tabs, {"position": 2})
    by_hierarchy = resolve_tab_selector(tabs, {"hierarchy": "Invoices / July"})

    assert by_position["tab_id"] == "july"
    assert by_hierarchy["tab_id"] == "july"
    assert by_hierarchy["hierarchy"] == ["Invoices", "July"]


def test_tab_selector_never_guesses_between_overlapping_title_fragments() -> None:
    tabs = [
        {"tab_id": "one", "title": "Internal Notes", "parent_tab_id": ""},
        {"tab_id": "two", "title": "Invoice Notes", "parent_tab_id": ""},
    ]

    with pytest.raises(DocsSelectorError) as exc_info:
        resolve_tab_selector(tabs, {"title_contains": "notes"})

    assert exc_info.value.code == "docs_tab_selector_ambiguous"
    assert len(exc_info.value.details["candidates"]) == 2


def test_tab_selector_not_found_returns_the_available_tabs() -> None:
    tabs = [{"tab_id": "main", "title": "Overview", "parent_tab_id": ""}]

    with pytest.raises(DocsSelectorError) as exc_info:
        resolve_tab_selector(tabs, {"title": "Invoices"})

    assert exc_info.value.code == "docs_tab_not_found"
    assert exc_info.value.details["candidates"][0]["title"] == "Overview"


def test_tab_selector_rejects_non_object_values() -> None:
    with pytest.raises(DocsSelectorError) as exc_info:
        resolve_tab_selector([], 7)  # type: ignore[arg-type]

    assert exc_info.value.code == "docs_tab_selector_invalid"
    assert exc_info.value.details["selector_type"] == "int"


def test_comment_selector_can_identify_the_connected_users_comment() -> None:
    comments = [
        {
            "comment_id": "other",
            "content": "Invoice total needs review.",
            "author": "Reviewer",
            "author_is_me": False,
            "resolved": False,
        },
        {
            "comment_id": "mine",
            "content": "Invoice total has been corrected.",
            "author": "Elena Viter",
            "author_is_me": True,
            "resolved": True,
        },
    ]

    match = resolve_comment_selector(
        comments,
        {"text_contains": "invoice total", "author": "me", "resolved": True},
    )

    assert match["comment_id"] == "mine"
    assert match["position"] == 2


def test_comment_selector_checks_full_text_but_bounds_candidate_preview() -> None:
    comments = [
        {
            "comment_id": "long",
            "content": "x" * 300 + " unique tail",
            "author": "Reviewer",
            "resolved": False,
        }
    ]

    match = resolve_comment_selector(comments, {"text_contains": "unique tail"})

    assert match["comment_id"] == "long"
    assert len(match["content"]) == 240


def test_comment_selector_reports_no_match() -> None:
    comments = [
        {
            "comment_id": "one",
            "content": "Review the heading.",
            "author": "Reviewer",
            "resolved": False,
        }
    ]

    with pytest.raises(DocsSelectorError) as exc_info:
        resolve_comment_selector(comments, {"text_contains": "invoice"})

    assert exc_info.value.code == "docs_comment_not_found"
    assert exc_info.value.details["candidates"][0]["comment_id"] == "one"


def test_comment_selector_rejects_non_object_values() -> None:
    with pytest.raises(DocsSelectorError) as exc_info:
        resolve_comment_selector([], 7)  # type: ignore[arg-type]

    assert exc_info.value.code == "docs_comment_selector_invalid"
    assert exc_info.value.details["selector_type"] == "int"


def test_comment_selector_bounds_ambiguity_candidates() -> None:
    comments = [
        {
            "comment_id": f"comment-{index}",
            "content": "Review the invoice total.",
            "author": f"Reviewer {index}",
            "resolved": False,
        }
        for index in range(SELECTOR_CANDIDATE_LIMIT + 5)
    ]

    with pytest.raises(DocsSelectorError) as exc_info:
        resolve_comment_selector(comments, {"text_contains": "invoice total"})

    details = exc_info.value.details
    assert details["match_count"] == SELECTOR_CANDIDATE_LIMIT + 5
    assert len(details["candidates"]) == SELECTOR_CANDIDATE_LIMIT
    assert details["candidates_truncated"] is True


def test_comment_selector_reads_the_text_under_a_comment_now() -> None:
    comments = [
        {"comment_id": "c1", "content": "check", "quoted_text": "June", "current_text": "July", "scope": "text"},
        {"comment_id": "c2", "content": "general", "scope": "document"},
    ]
    assert [m["comment_id"] for m in matching_comments(comments, {"quoted_text_contains": "july"})] == ["c1"]
    assert [m["comment_id"] for m in matching_comments(comments, {"text_contains": "june"})] == ["c1"]
    assert [m["comment_id"] for m in matching_comments(comments, {"scope": "document"})] == ["c2"]
    assert matching_comments(comments, {"scope": "text"})[0]["current_text"] == "July"


def test_comment_selector_refuses_an_unknown_scope() -> None:
    with pytest.raises(DocsSelectorError) as error:
        matching_comments([], {"scope": "paragraph"})
    assert error.value.code == "docs_comment_selector_invalid"
