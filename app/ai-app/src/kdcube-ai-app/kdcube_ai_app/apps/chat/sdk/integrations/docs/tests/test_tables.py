# SPDX-License-Identifier: MIT
"""Provider-neutral table, row, and column selectors."""
from __future__ import annotations

from typing import Any

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.docs.selectors import DocsSelectorError
from kdcube_ai_app.apps.chat.sdk.integrations.docs.tables import (
    effective_header_rows,
    spread_table_selector,
    parse_row_range,
    resolve_column,
    resolve_row,
    resolve_table,
)


def _table(
    rows: list[list[str]],
    *,
    position: int = 1,
    heading: str | None = None,
    header_rows: int = 0,
) -> dict[str, Any]:
    return {
        "position": position,
        "after_heading": {"text": heading, "level": 2} if heading else None,
        "rows": len(rows),
        "columns": max(len(row) for row in rows),
        "header_rows": header_rows,
        "cells": [[{"text": text} for text in row] for row in rows],
    }


TASKS = _table(
    [["Task", "Status"], ["Fix login", "Open"], ["Fix logout", "Done"]],
    heading="Tasks",
    header_rows=1,
)
PLAIN = _table([["Office", "120"], ["Travel", "300"]], position=2, heading="Budget")


def _code(call) -> str:
    with pytest.raises(DocsSelectorError) as info:
        call()
    return info.value.code


def test_table_by_position_heading_and_header_fragment() -> None:
    assert resolve_table([TASKS, PLAIN], 2) is PLAIN
    assert resolve_table([TASKS, PLAIN], {"after_heading": "  tasks "}) is TASKS
    assert resolve_table([TASKS, PLAIN], {"header_contains": "stat"}) is TASKS
    # Without a header row, header_contains looks at the first row.
    assert resolve_table([TASKS, PLAIN], {"header_contains": "office"}) is PLAIN


def test_table_selector_errors_carry_candidates() -> None:
    with pytest.raises(DocsSelectorError) as info:
        resolve_table([TASKS, PLAIN], {"after_heading": "Missing"})
    assert info.value.code == "docs_table_not_found"
    assert [row["position"] for row in info.value.details["candidates"]] == [1, 2]
    assert info.value.details["candidates"][0]["header"] == ["Task", "Status"]

    assert _code(lambda: resolve_table([TASKS], {})) == "docs_table_selector_invalid"
    assert _code(lambda: resolve_table([TASKS], {"position": 0})) == "docs_table_selector_invalid"
    twin = {**TASKS, "position": 2}
    assert _code(lambda: resolve_table([TASKS, twin], {"after_heading": "Tasks"})) == (
        "docs_table_ambiguous"
    )
    assert resolve_table([TASKS, twin], {"after_heading": "Tasks", "position": 2}) is twin


def test_row_by_number_equals_and_contains() -> None:
    assert resolve_row(TASKS, 1, header_rows=1) == 1
    assert resolve_row(TASKS, {"number": 3}, header_rows=1) == 3
    where = {"where": {"column": "Task", "equals": "fix LOGIN"}}
    assert resolve_row(TASKS, where, header_rows=1) == 2
    assert resolve_row(TASKS, {"where": {"column": 2, "contains": "don"}}, header_rows=1) == 3


def test_row_errors() -> None:
    assert _code(lambda: resolve_row(TASKS, 9, header_rows=1)) == "docs_table_row_not_found"
    assert _code(
        lambda: resolve_row(TASKS, {"where": {"column": "Task", "contains": "fix"}}, header_rows=1)
    ) == "docs_table_row_ambiguous"
    assert _code(
        lambda: resolve_row(
            TASKS, {"where": {"column": "Task", "equals": "a", "contains": "b"}}, header_rows=1
        )
    ) == "docs_table_selector_invalid"
    assert _code(lambda: resolve_row(TASKS, "two", header_rows=1)) == "docs_table_selector_invalid"


def test_columns_by_number_or_header_name() -> None:
    assert resolve_column(TASKS, 2, header_rows=1) == 2
    assert resolve_column(TASKS, "status", header_rows=1) == 2
    assert _code(lambda: resolve_column(TASKS, 5, header_rows=1)) == "docs_table_column_not_found"
    assert _code(lambda: resolve_column(TASKS, "Owner", header_rows=1)) == (
        "docs_table_column_not_found"
    )
    assert _code(lambda: resolve_column(PLAIN, "Office", header_rows=0)) == "docs_table_no_header"
    assert resolve_column(PLAIN, "Office", header_rows=1) == 1

    twins = _table([["Name", "Name"], ["a", "b"]], header_rows=1)
    assert _code(lambda: resolve_column(twins, "name", header_rows=1)) == (
        "docs_table_column_ambiguous"
    )


def test_explicit_header_rows_and_row_ranges() -> None:
    assert effective_header_rows(PLAIN) == 0
    assert effective_header_rows(PLAIN, 1) == 1
    assert _code(lambda: effective_header_rows(PLAIN, 3)) == "docs_table_selector_invalid"
    assert _code(lambda: effective_header_rows(PLAIN, True)) == "docs_table_selector_invalid"

    assert parse_row_range(None, rows=120) == (1, 50)
    assert parse_row_range("51-100", rows=60) == (51, 60)
    assert parse_row_range("7", rows=60) == (7, 7)
    assert _code(lambda: parse_row_range("10-2", rows=60)) == "docs_table_selector_invalid"


def test_digit_column_key_is_a_header_name_before_a_number() -> None:
    priced = _table([["Office", "120"], ["Travel", "300"]], header_rows=1)
    # The header literally reads "120": the key names it, not column 120.
    assert resolve_column(priced, "120", header_rows=1) == 2
    assert _code(lambda: resolve_column(priced, "3", header_rows=1)) == (
        "docs_table_column_not_found"
    )
    # Without that header name a digit key stays a 1-based number.
    assert resolve_column(TASKS, "2", header_rows=1) == 2
    assert resolve_column(TASKS, 2, header_rows=1) == 2


def test_spread_table_selector_accepts_a_read_selector() -> None:
    selector = {"tab_selector": {"title": "Main"}, "table": {"position": 1}}

    assert spread_table_selector({"selector": selector, "row": 2}) == {
        "tab_selector": {"title": "Main"},
        "table": {"position": 1},
        "row": 2,
    }
    # The same selector nested under table, as an agent may send it.
    assert spread_table_selector({"table": selector}) == {
        "tab_selector": {"title": "Main"},
        "table": {"position": 1},
    }
    # Explicit fields win, and a payload without a selector is untouched.
    assert spread_table_selector(
        {"selector": selector, "tab_selector": {"title": "Archive"}}
    )["tab_selector"] == {"title": "Archive"}
    assert spread_table_selector({"table": {"position": 2}}) == {"table": {"position": 2}}
